"""Human-approved exact edits; compare-and-swap immutable workspace versions.
Never writes the original uploaded directory, never lets model execute a shell.
"""
from __future__ import annotations
import ast
import difflib
import json
import tomllib
from collections import Counter
from .models import ChangeSet, Edit
from .security import sha, fingerprint, path_ok, redact
from .db import Store, now, uid
from .workspaces import Workspaces


def unified_patch(before: bytes | None, after: bytes | None, path: str) -> str:
    """Git-applicable unified diff, including empty files and missing final newline."""
    def quoted(value):
        return '"'+value.replace('\\','\\\\').replace('"','\\"')+'"'
    a='a/'+path; b='b/'+path
    result=[f'diff --git {quoted(a)} {quoted(b)}\n']
    if before is None:
        result.append('new file mode 100644\n')
        if after==b'': return ''.join(result)+'index 0000000..e69de29\n'
    if after is None:
        result.append('deleted file mode 100644\n')
        if before==b'': return ''.join(result)+'index e69de29..0000000\n'
    for line in difflib.unified_diff((before or b'').decode('utf-8').splitlines(keepends=True),
              (after or b'').decode('utf-8').splitlines(keepends=True),
              fromfile=quoted(a) if before is not None else '/dev/null',
              tofile=quoted(b) if after is not None else '/dev/null'):
        if line.endswith('\n'): result.append(line)
        else: result.append(line+'\n\\ No newline at end of file\n')
    return ''.join(result)


def static_check(files: dict[str,bytes]) -> dict:
    checked=0; problems=[]; skipped=Counter()
    for path,raw in sorted(files.items()):
        try:
            text=raw.decode('utf-8-sig')
            if path.endswith('.py'): ast.parse(text,filename=path)
            elif path.endswith('.json'): json.loads(text)
            elif path.endswith('.toml'): tomllib.loads(text)
            else: skipped['no_parser']+=1; continue
            checked+=1
        except (SyntaxError,ValueError,UnicodeError,RecursionError) as e:
            checked+=1; problems.append({'path':path,'line':getattr(e,'lineno',None),'message':str(e)[:1000]})
    return {'status':('not_applicable' if not checked else ('passed' if not problems else 'failed')),'kind':'static','executed_code':False,
            'checked':checked,'skipped':dict(skipped),'problems':problems,
            'notice':'只做 Python/JSON/TOML 语法检查；不证明测试通过或行为正确。'}


class Changes:
    def __init__(self,workspaces: Workspaces,db: Store):
        self.ws=workspaces; self.db=db

    def materialize(self,wid: str,snapshot: str,spec: ChangeSet) -> tuple[dict,list[dict],str]:
        files=self.ws.content_map(wid,snapshot); seen=set(); changes=[]; diff=[]
        for edit in spec.edits:
            path=path_ok(edit.path)
            if path.casefold() in seen: raise ValueError('同一提案每个文件只允许一个编辑，先在 Coder 合并')
            seen.add(path.casefold()); before=files.get(path)
            if '[REDACTED' in edit.new_text: raise ValueError('不能把已脱敏占位符写入源码')
            if edit.operation=='create':
                if before is not None or path.casefold() in {x.casefold() for x in files}: raise ValueError('待创建文件已存在')
                after=edit.new_text.encode('utf-8')
            else:
                if before is None: raise ValueError(f'{path} 不存在')
                if edit.expected_hash and sha(before)!=edit.expected_hash: raise ValueError('文件哈希不匹配')
                text=before.decode('utf-8')
                if edit.operation=='delete': after=None
                elif edit.operation=='replace_file': after=edit.new_text.encode('utf-8')
                else:
                    if not edit.old_text or text.count(edit.old_text)!=1:
                        raise ValueError(f'{path}: old_text 必须精确匹配且只出现一次')
                    after=text.replace(edit.old_text,edit.new_text,1).encode('utf-8')
            if after is not None and len(after)>self.ws.config.max_file_bytes: raise ValueError('修改后文件超出大小限制')
            if after==before: continue
            if after is None: files.pop(path,None)
            else: files[path]=after
            changes.append({'path':path,'operation':edit.operation,'before_hash':sha(before) if before is not None else None,
                            'after_hash':sha(after) if after is not None else None})
            # All lines, including newline changes, are retained in the edit payload.
            diff.append(unified_patch(before,after,path))
        if not changes: raise ValueError('提案没有实际文件变化')
        if len(files)>self.ws.config.max_files or sum(len(x) for x in files.values())>self.ws.config.max_repo_bytes:
            raise ValueError('修改后工作区超出容量上限')
        return files,changes,''.join(diff)

    def create(self,wid: str,snapshot: str,spec: ChangeSet,run_id: str|None=None,model_generated=False) -> dict:
        self.ws.read(wid,snapshot)
        if model_generated:
            for e in spec.edits:
                if e.operation=='replace_file':
                    raw=self.ws.source(wid,e.path,snapshot)['content']
                    if raw!=redact(raw): raise ValueError('包含脱敏内容的文件不允许由模型整体替换，请用精确局部编辑')
        files,changed,diff=self.materialize(wid,snapshot,spec)
        payload={'workspace_id':wid,'snapshot_id':snapshot,'spec':spec.model_dump()}
        item={'id':uid(),**payload,'digest':fingerprint(payload),'files':changed,'diff':diff,
              'static_check':static_check(files),'status':'pending','created_at':now(),'run_id':run_id,
              'applied_snapshot':None,'published':None,
              'risk_notice':'批准会修改工作区副本；配置、依赖、测试、CI 文件变化需特别核对。不会执行源码或自动提交。'}
        return self.db.put('proposal',item)

    def decide(self,pid: str,digest: str,approved: bool) -> dict:
        with self.ws.lock:
            p=self.db.get('proposal',pid)
            if p['digest']!=digest: raise ValueError('审批摘要不匹配，请重新查看提案')
            if p['status']=='applied' and approved: return p
            if p['status']!='pending': raise ValueError('此提案已经处理')
            if not approved: return self.db.update('proposal',pid,{'status':'rejected','decided_at':now()})
            w,s,_=self.ws.read(p['workspace_id'])
            if w['snapshot_id']!=p['snapshot_id']: raise ValueError('源码基线已改变，请基于新版本重新生成提案')
            count=sum(x['workspace_id']==w['id'] for x in self.db.list('snapshot'))
            if count>=self.ws.config.max_versions: raise ValueError('工作区版本达到上限')
            files,_,_=self.materialize(w['id'],s['id'],ChangeSet.model_validate(p['spec']))
            snap=self.ws.write_snapshot(w['id'],files,parent=s['id'],modes={x['path']:x['mode'] for x in s['files']})
            self.db.update('workspace',w['id'],{'snapshot_id':snap['id']})
            return self.db.update('proposal',pid,{'status':'applied','applied_snapshot':snap['id'],'decided_at':now()})

    def inverse(self,pid: str) -> dict:
        p=self.db.get('proposal',pid)
        if p['status']!='applied': raise ValueError('只能撤销已应用的提案')
        w,_,_=self.ws.read(p['workspace_id'])
        if w['snapshot_id']!=p['applied_snapshot']: raise ValueError('后续版本已经改变，请手动生成新的回滚提案')
        edits=[]
        for change in p['files']:
            path=change['path']
            try: old=self.ws.source(w['id'],path,p['snapshot_id'])['content']
            except KeyError: old=None
            try: current=self.ws.source(w['id'],path,p['applied_snapshot'])
            except KeyError: current=None
            if old is None: edits.append(Edit(path=path,operation='delete',expected_hash=current['hash']))
            elif current is None: edits.append(Edit(path=path,operation='create',new_text=old))
            else: edits.append(Edit(path=path,operation='replace_file',new_text=old,expected_hash=current['hash']))
        return self.create(w['id'],w['snapshot_id'],ChangeSet(title='撤销：'+p['spec']['title'][:170],edits=edits))

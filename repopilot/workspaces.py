"""Immutable raw UTF-8 workspaces. Source is never imported or executed on host."""
from __future__ import annotations
import io
import json
import os
import shutil
import stat
import threading
import zipfile
from pathlib import Path, PurePosixPath
from collections import Counter
from pathspec import GitIgnoreSpec
from .config import Configuration
from .db import Store, uid, now
from .security import sha, fingerprint, path_ok


class Workspaces:
    def __init__(self, config: Configuration, db: Store):
        self.config=config; self.db=db; self.root=config.data_dir/'workspaces'
        self.root.mkdir(exist_ok=True)
        self.lock=threading.RLock()

    def read(self, wid: str, snapshot: str | None=None) -> tuple[dict,dict,Path]:
        w=self.db.get('workspace',wid)
        s=self.db.get('snapshot',snapshot or w['snapshot_id'])
        if s['workspace_id']!=wid: raise ValueError('工作区与版本不匹配')
        root=self.root/wid/s['id']
        if not root.is_dir(): raise ValueError('工作区文件不可用')
        return w,s,root

    def files(self,wid: str,snapshot: str|None=None) -> list[dict]:
        return self.read(wid,snapshot)[1]['files']

    def source(self,wid: str,path: str,snapshot: str|None=None) -> dict:
        path=path_ok(path); _,s,root=self.read(wid,snapshot)
        record=next((x for x in s['files'] if x['path']==path),None)
        if not record: raise KeyError('文件不在此版本内')
        file=root/path
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()): raise ValueError('不允许符号链接')
        raw=file.read_bytes()
        if sha(raw)!=record['hash']: raise ValueError('工作区被外部修改，请重新导入；原有证据不再可靠')
        return record | {'content':raw.decode('utf-8')}

    def content_map(self,wid: str,snapshot: str|None=None) -> dict[str,bytes]:
        _,s,root=self.read(wid,snapshot)
        result={}
        for item in s['files']:
            result[item['path']]=self.source(wid,item['path'],s['id'])['content'].encode('utf-8')
        return result

    def ingest(self,name: str,entries: dict[str,bytes],origin: dict|None=None,modes: dict|None=None) -> dict:
        with self.lock:
            if len(self.db.list('workspace'))>=self.config.max_workspace_count:
                raise ValueError('工作区数量已达上限，请删除不再使用的工作区')
            accepted,skipped=self.filter(entries)
            if not accepted: raise ValueError('没有可导入的 UTF-8 文本源码；请检查过滤规则和文件大小')
            wid=uid()
            snapshot=self.write_snapshot(wid,accepted,modes=modes)
            item={'id':wid,'name':name[:100],'snapshot_id':snapshot['id'],'initial_snapshot':snapshot['id'],
                  'origin':origin or {'type':'upload'},'created_at':now(),'skipped':dict(skipped)}
            return self.db.put('workspace',item)

    def filter(self,entries: dict[str,bytes]) -> tuple[dict,Counter]:
        specs=[]
        for name,data in entries.items():
            if PurePosixPath(name).name=='.gitignore' and len(data)<65536:
                try:
                    p=path_ok(name); anchor=PurePosixPath(p).parent
                    specs.append((anchor,GitIgnoreSpec.from_lines(data.decode('utf-8').splitlines())))
                except (ValueError,UnicodeError): pass
        specs.sort(key=lambda x:len(x[0].parts))
        accepted={}; skipped=Counter(); total=0; cases=set()
        for raw,data in sorted(entries.items()):
            try: name=path_ok(raw)
            except ValueError:
                skipped['unsupported_or_unsafe']+=1; continue
            p=PurePosixPath(name); ignored=False
            for anchor,spec in specs:
                if p.is_relative_to(anchor):
                    match=spec.check_file(p.relative_to(anchor).as_posix())
                    if match.include is not None: ignored=match.include
            if ignored and p.name!='.gitignore': skipped['gitignore']+=1; continue
            if len(data)>self.config.max_file_bytes or b'\0' in data:
                skipped['large_or_binary']+=1; continue
            try: data.decode('utf-8')
            except UnicodeError: skipped['non_utf8']+=1; continue
            if name.casefold() in cases: raise ValueError('文件路径存在大小写冲突，无法安全跨平台导入')
            if len(accepted)>=self.config.max_files or total+len(data)>self.config.max_repo_bytes:
                raise ValueError('仓库超出文件数或总字节限制；请导入较小的子项目')
            cases.add(name.casefold()); accepted[name]=data; total+=len(data)
        return accepted,skipped

    def write_snapshot(self,wid: str,files: dict[str,bytes],parent: str|None=None,modes: dict|None=None) -> dict:
        for path in files: path_ok(path)
        manifest=[{'path':path,'hash':sha(data),'size':len(data),'lines':len(data.decode('utf-8').splitlines()),
                   'mode':(modes or {}).get(path,'100644')} for path,data in sorted(files.items())]
        sid=fingerprint({'workspace':wid,'files':manifest})
        dest=self.root/wid/sid
        if not dest.exists():
            temp=self.root/wid/('tmp-'+uid()); temp.mkdir(parents=True)
            try:
                for path,raw in files.items():
                    p=temp/path; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(raw)
                    p.chmod(0o644)
                os.replace(temp,dest)
            except BaseException:
                shutil.rmtree(temp,ignore_errors=True); raise
        snap={'id':sid,'workspace_id':wid,'parent':parent,'created_at':now(),'files':manifest,
              'bytes':sum(x['size'] for x in manifest),'file_count':len(manifest)}
        try: return self.db.get('snapshot',sid)
        except KeyError: return self.db.put('snapshot',snap)

    def zip_entries(self,raw: bytes) -> dict[str,bytes]:
        if len(raw)>self.config.max_upload_bytes: raise ValueError('ZIP 太大')
        out={}; total=0
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos=[i for i in z.infolist() if not i.is_dir()]
            if len(infos)>10000: raise ValueError('ZIP 文件数量过多')
            # Reject path traversal before optionally removing one common root directory.
            for i in infos:
                p=PurePosixPath(i.filename)
                if p.is_absolute() or '\\' in i.filename or ':' in i.filename or '..' in p.parts:
                    raise ValueError('ZIP 包含不安全路径')
                mode=i.external_attr>>16
                if stat.S_ISLNK(mode): raise ValueError('ZIP 不允许符号链接')
                if i.flag_bits&1: raise ValueError('不支持加密 ZIP')
                total+=i.file_size
                if total>self.config.max_repo_bytes*2: raise ValueError('ZIP 解压体积过大')
            roots={i.filename.split('/')[0] for i in infos}
            strip=len(roots)==1 and all('/' in i.filename for i in infos)
            for i in infos:
                name=i.filename.split('/',1)[1] if strip else i.filename
                try: path_ok(name)
                except ValueError: continue
                if i.file_size>self.config.max_file_bytes: continue
                if name in out: raise ValueError('ZIP 存在重复路径')
                out[name]=z.read(i)
        return out

    def export(self,wid: str,snapshot: str|None=None) -> bytes:
        files=self.content_map(wid,snapshot); data=io.BytesIO()
        with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as z:
            for name,body in files.items(): z.writestr(name,body)
        return data.getvalue()

    def remove(self,wid: str):
        with self.lock:
            self.db.get('workspace',wid)
            owned_ids=[]
            for kind in ('snapshot','run','proposal','test','session','index_job'):
                for item in self.db.list(kind):
                    if item.get('workspace_id')==wid:
                        if kind in ('run','test','index_job'): owned_ids.append(item['id'])
                        self.db.delete(kind,item['id'])
            with self.db.connect() as con:
                con.executemany('DELETE FROM events WHERE run_id=?',[(id,) for id in owned_ids])
            self.db.delete('workspace',wid)
            shutil.rmtree(self.root/wid,ignore_errors=True)

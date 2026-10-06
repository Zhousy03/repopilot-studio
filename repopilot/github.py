"""GitHub-only import and two-phase draft PR publishing. No shell git/credentials."""
from __future__ import annotations
import asyncio
import base64
import json
import re
from urllib.parse import quote,urlsplit
from .config import Configuration
from .db import Store,now
from .security import github_repo,fingerprint,sha,path_ok
from .workspaces import Workspaces
import httpx


class GitHub:
    def __init__(self,config: Configuration,workspaces: Workspaces,db: Store,transport=None):
        self.config=config; self.ws=workspaces; self.db=db; self.transport=transport
        self.publish_locks={}

    def client(self):
        headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'RepoPilot-Studio/1.1'}
        token=self.config.value.github_token
        if token: headers['Authorization']='Bearer '+token
        return httpx.AsyncClient(base_url='https://api.github.com',headers=headers,timeout=40,follow_redirects=False,trust_env=True,transport=self.transport)

    async def request(self,method: str,path: str,body=None):
        # Only GET is retried. A failed POST may already have had a side effect.
        if not path.startswith('/repos/'): raise ValueError('非法 GitHub API 路径')
        attempts=3 if method=='GET' else 1
        for i in range(attempts):
            try:
                async with self.client() as client:
                    r=await client.request(method,path,json=body)
                    r.raise_for_status(); return r.json()
            except httpx.HTTPStatusError as exc:
                code=exc.response.status_code
                if method=='GET' and (code==429 or code>=500) and i+1<attempts:
                    await asyncio.sleep(.5*2**i); continue
                raise ValueError(f'GitHub API 返回 {code}；请检查权限、分支、速率限制。写操作状态不明时请先核对远端。') from exc
            except httpx.TransportError as exc:
                if i+1<attempts: await asyncio.sleep(.5*2**i); continue
                raise ValueError('GitHub 网络请求失败。写操作可能已经到达远端，请核对后重试。') from exc

    async def import_repo(self,url: str,ref: str) -> dict:
        owner,repo=github_repo(url); prefix=f'/repos/{owner}/{repo}'
        meta=await self.request('GET',prefix)
        branch=ref.strip() or meta['default_branch']
        if not re.fullmatch(r'[A-Za-z0-9_./-]{1,200}',branch) or '..' in branch: raise ValueError('分支/ref 不合法')
        commit=await self.request('GET',prefix+'/commits/'+quote(branch,safe=''))
        commit_sha=commit['sha']; tree_sha=commit['commit']['tree']['sha']
        if not re.fullmatch('[a-f0-9]{40,64}',commit_sha): raise ValueError('GitHub 返回无效 commit')
        tree=await self.request('GET',prefix+f'/git/trees/{tree_sha}?recursive=1')
        if tree.get('truncated'): raise ValueError('仓库树太大，GitHub 返回不完整列表；请改为上传所需子目录')
        modes={x['path']:x.get('mode','100644') for x in tree.get('tree',[]) if x.get('type')=='blob' and x.get('mode')!='120000'}
        # Download archive, accepting GitHub's documented codeload redirect only.
        target='https://api.github.com'+prefix+'/zipball/'+commit_sha; headers={}
        token=self.config.value.github_token
        if token: headers['Authorization']='Bearer '+token
        raw=bytearray()
        async with httpx.AsyncClient(timeout=60,transport=self.transport,follow_redirects=False,trust_env=True) as client:
            for step in range(3):
                async with client.stream('GET',target,headers=headers) as response:
                    if response.status_code in {301,302,303,307,308}:
                        target=response.headers.get('location',''); u=urlsplit(target)
                        if u.scheme!='https' or u.netloc!='codeload.github.com' or u.username: raise ValueError('拒绝非 GitHub 归档重定向')
                        headers={}; continue
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>self.config.max_upload_bytes: raise ValueError('GitHub 仓库归档过大，请上传子目录')
                    break
            else: raise ValueError('GitHub 归档重定向过多')
        entries=self.ws.zip_entries(bytes(raw))
        # Symlinks are rejected by zip_entries, git submodules are not materialized.
        return self.ws.ingest(repo,entries,{'type':'github','url':f'https://github.com/{owner}/{repo}',
            'owner':owner,'repo':repo,'ref':branch,'default_branch':meta['default_branch'],
            'commit':commit_sha,'tree':tree_sha,'submodules_included':False},modes)

    async def issue(self,url: str) -> dict:
        u=urlsplit(url)
        m=re.fullmatch(r'/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/(\d+)',u.path.rstrip('/'))
        if u.scheme!='https' or u.netloc!='github.com' or not m or u.query or u.fragment:
            raise ValueError('Issue 地址必须为 https://github.com/OWNER/REPO/issues/NUMBER')
        owner,repo,n=m.groups(); p=f'/repos/{owner}/{repo}/issues/{n}'
        issue=await self.request('GET',p)
        return {'url':url,'title':issue.get('title',''),'content':(issue.get('body') or '')[:16000],
                'updated_at':issue.get('updated_at'),'source':'github_issue'}

    async def preview(self,pid: str) -> dict:
        p=self.db.get('proposal',pid)
        if p['status']!='applied': raise ValueError('请先批准并应用提案到工作区')
        w,_,_=self.ws.read(p['workspace_id'])
        origin=w['origin']
        if origin.get('type')!='github': raise ValueError('只有从 GitHub 导入的工作区可以创建 PR；本地工作区请导出 patch')
        if w['snapshot_id']!=p['applied_snapshot']: raise ValueError('工作区已发生后续修改，请选择最新提案')
        if not self.config.value.github_token: raise ValueError('请先配置具有仓库写入权限的 GitHub Token')
        base=origin['ref']; prefix=f'/repos/{origin["owner"]}/{origin["repo"]}'
        # Require an actual branch, not a tag or a detached SHA.
        latest=await self.request('GET',prefix+'/git/ref/heads/'+quote(base,safe=''))
        if latest['object']['sha']!=origin['commit']: raise ValueError('远端基线已经变化，请重新导入并重新生成提案')
        original=self.ws.content_map(w['id'],w['initial_snapshot'])
        current=self.ws.content_map(w['id'],p['applied_snapshot'])
        paths=sorted(x for x in set(original)|set(current) if original.get(x)!=current.get(x))
        if not paths or len(paths)>50: raise ValueError('发布变化为空或超过 50 个文件')
        details={'proposal_id':pid,'workspace_id':w['id'],'repository':origin['owner']+'/'+origin['repo'],
                 'base':base,'base_commit':origin['commit'],'snapshot_id':p['applied_snapshot'],
                 'branch':'repopilot/'+pid[:16],'files':paths,'draft':True,
                 'notice':'将创建远端分支与草稿 PR，可能触发目标仓库 CI。不会修改默认分支或自动合并。'}
        details['fingerprint']=fingerprint(details)
        return details

    async def publish(self,pid: str,expected: str) -> dict:
        lock=self.publish_locks.setdefault(pid,asyncio.Lock())
        async with lock:
            p=self.db.get('proposal',pid)
            if p.get('published'): return p['published']
            info=await self.preview(pid)
            if info['fingerprint']!=expected: raise ValueError('发布预览已改变，请重新确认')
            w,s,_=self.ws.read(p['workspace_id'],p['applied_snapshot']); o=w['origin']; prefix='/repos/'+info['repository']
            files=self.ws.content_map(w['id'],s['id']); modes={x['path']:x['mode'] for x in s['files']}
            entries=[]
            for path in info['files']:
                path_ok(path)
                if path not in files: entries.append({'path':path,'mode':'100644','type':'blob','sha':None})
                else: entries.append({'path':path,'mode':modes.get(path,'100644'),'type':'blob','content':files[path].decode('utf-8')})
            journal=p.get('publication_journal') or {}
            # Write each remote identity before continuing; retry queries deterministic branch/PR.
            if not journal.get('tree'):
                tree=await self.request('POST',prefix+'/git/trees',{'base_tree':o['tree'],'tree':entries})
                journal['tree']=tree['sha']; self.db.update('proposal',pid,{'publication_journal':journal})
            head=quote(o['owner']+':'+info['branch'],safe='')
            pulls=await self.request('GET',prefix+'/pulls?state=all&head='+head)
            if pulls:
                match=next((x for x in pulls if x['head']['ref']==info['branch'] and x['base']['ref']==o['ref']),None)
                if match:
                    result={'url':match['html_url'],'number':match['number'],'branch':info['branch'],'reused':True}
                    self.db.update('proposal',pid,{'published':result}); return result
            # Check existing ref with raw response so 404 has unambiguous meaning.
            async with self.client() as client:
                r=await client.get(prefix+'/git/ref/heads/'+quote(info['branch'],safe=''))
            if r.status_code==200:
                existing=await self.request('GET',prefix+'/git/commits/'+r.json()['object']['sha'])
                if existing['tree']['sha']!=journal['tree']: raise ValueError('目标分支已经存在且内容不同，请人工处理')
                journal['commit']=existing['sha']
            elif r.status_code==404:
                if not journal.get('commit'):
                    commit=await self.request('POST',prefix+'/git/commits',{'message':p['spec']['title'],
                        'tree':journal['tree'],'parents':[o['commit']]})
                    journal['commit']=commit['sha']; self.db.update('proposal',pid,{'publication_journal':journal})
                await self.request('POST',prefix+'/git/refs',{'ref':'refs/heads/'+info['branch'],'sha':journal['commit']})
            else: raise ValueError(f'查询发布分支失败：{r.status_code}')
            self.db.update('proposal',pid,{'publication_journal':journal})
            pr=await self.request('POST',prefix+'/pulls',{'title':p['spec']['title'],'head':info['branch'],'base':o['ref'],
              'body':p['spec']['summary']+'\n\nGenerated with RepoPilot Studio; human-reviewed workspace proposal '+pid+
                     '\n\nTests are recorded locally. This description does not claim CI has passed.', 'draft':True})
            result={'url':pr['html_url'],'number':pr['number'],'branch':info['branch'],'reused':False}
            self.db.update('proposal',pid,{'published':result}); return result

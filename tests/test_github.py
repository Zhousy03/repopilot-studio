import io,json,zipfile
import httpx
import pytest
from repopilot.github import GitHub
from repopilot.models import ChangeSet,Edit
from repopilot.security import sha

async def test_github_import_token_not_forwarded_to_codeload(components):
    config,db,ws,ch=components;config.update({'github_token':'private-test-token'})
    body=io.BytesIO()
    with zipfile.ZipFile(body,'w') as z:z.writestr('repo/src/file.py','x=1\n')
    seen=[]
    def handle(req):
        seen.append(req)
        path=req.url.path
        if req.url.host=='codeload.github.com':return httpx.Response(200,content=body.getvalue())
        if path.endswith('/zipball/'+'a'*40):return httpx.Response(302,headers={'location':'https://codeload.github.com/owner/repo/legacy.zip/'+'a'*40})
        if '/git/trees/' in path:return httpx.Response(200,json={'tree':[{'path':'src/file.py','mode':'100644','type':'blob'}]})
        if '/commits/' in path:return httpx.Response(200,json={'sha':'a'*40,'commit':{'tree':{'sha':'b'*40}}})
        return httpx.Response(200,json={'default_branch':'main'})
    gh=GitHub(config,ws,db,httpx.MockTransport(handle))
    w=await gh.import_repo('https://github.com/owner/repo','')
    assert ws.source(w['id'],'src/file.py')['content']=='x=1\n'
    assert all('authorization' not in r.headers for r in seen if r.url.host=='codeload.github.com')
    assert w['origin']['commit']=='a'*40

async def test_publish_changes_only_draft_and_idempotent(components):
    config,db,ws,ch=components;config.update({'github_token':'private-test-token'})
    origin={'type':'github','owner':'owner','repo':'repo','ref':'main','commit':'a'*40,'tree':'b'*40,'url':'https://github.com/owner/repo'}
    w=ws.ingest('repo',{'app.py':b'x=1\n'},origin)
    p=ch.create(w['id'],w['snapshot_id'],ChangeSet(title='Change x',edits=[Edit(path='app.py',old_text='x=1',new_text='x=2')]))
    ch.decide(p['id'],p['digest'],True)
    calls=[]; branch=[]; published=[]
    def handler(req):
        calls.append((req.method,req.url.path,json.loads(req.content) if req.content else None))
        path=req.url.path; data=json.loads(req.content) if req.content else {}
        if req.method=='GET' and path.endswith('/git/ref/heads/main'): return httpx.Response(200,json={'object':{'sha':'a'*40}})
        if req.method=='GET' and '/git/ref/heads/repopilot/' in path:return httpx.Response(404)
        if req.method=='GET' and path.endswith('/pulls'):return httpx.Response(200,json=[])
        if req.method=='POST' and path.endswith('/git/trees'):
            assert data['tree'][0]['content']=='x=2\n';assert data['base_tree']=='b'*40
            return httpx.Response(201,json={'sha':'c'*40})
        if req.method=='POST' and path.endswith('/git/commits'):return httpx.Response(201,json={'sha':'d'*40})
        if req.method=='POST' and path.endswith('/git/refs'):
            assert data['ref'].startswith('refs/heads/repopilot/');branch.append(data);return httpx.Response(201,json=data)
        if req.method=='POST' and path.endswith('/pulls'):
            assert data['draft'] is True and data['base']=='main';published.append(data)
            return httpx.Response(201,json={'number':1,'html_url':'https://github.com/owner/repo/pull/1'})
        raise AssertionError((req.method,path))
    gh=GitHub(config,ws,db,httpx.MockTransport(handler));preview=await gh.preview(p['id'])
    with pytest.raises(ValueError):await gh.publish(p['id'],'f'*64)
    result=await gh.publish(p['id'],preview['fingerprint'])
    again=await gh.publish(p['id'],preview['fingerprint'])
    assert result==again and len(published)==1 and len(branch)==1
    assert not any(method in {'PATCH','DELETE'} for method,_,_ in calls)

async def test_remote_baseline_changed_is_rejected(components):
    config,db,ws,ch=components;config.update({'github_token':'unit'})
    w=ws.ingest('repo',{'a.py':b'x=1'}, {'type':'github','owner':'o','repo':'r','ref':'main','commit':'a'*40,'tree':'b'*40})
    p=ch.create(w['id'],w['snapshot_id'],ChangeSet(title='x',edits=[Edit(path='a.py',old_text='x=1',new_text='x=2')]))
    ch.decide(p['id'],p['digest'],True)
    gh=GitHub(config,ws,db,httpx.MockTransport(lambda r:httpx.Response(200,json={'object':{'sha':'f'*40}})))
    with pytest.raises(ValueError,match='基线'):await gh.preview(p['id'])

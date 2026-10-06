import io
import json
import time
import zipfile
from fastapi.testclient import TestClient
from repopilot.api import create_app


def import_workspace(client):
    response=client.post('/api/workspaces/text',json={'name':'api-test','files':[{'path':'calc.py','content':'def add(a, b):\n    return a - b\n'}]})
    assert response.status_code==200,response.text
    return response.json()


def poll(client,path,statuses=('done','awaiting_approval','error')):
    for _ in range(300):
        r=client.get(path).json()
        if r['status'] in statuses: return r
        time.sleep(.01)
    raise AssertionError('Task did not reach a terminal status')

def test_empty_boot_no_demo(client):
    assert client.get('/api/workspaces').json()==[]
    assert client.get('/api/health').json()['sample_repository'] is False
    page=client.get('/').text
    assert 'mini-shop' not in page
    assert 'YOUR CODE.' in page

def test_csrf_and_host(client):
    assert client.post('/api/runs',json={},headers={'X-RepoPilot-Client':''}).status_code==403
    assert client.get('/api/workspaces',headers={'Origin':'https://evil.invalid'}).status_code==403
    assert client.get('/api/health',headers={'Host':'evil.invalid'}).status_code==400

def test_settings_no_key_echo(client):
    secret='unit-test-not-a-real-key-234567'
    r=client.patch('/api/settings',json={'api_key':secret})
    assert r.status_code==200 and secret not in r.text
    assert r.json()['api_key_configured'] is True
    assert secret not in client.get('/api/settings').text
    client.patch('/api/settings',json={'api_key':''})
    assert not client.get('/api/settings').json()['api_key_configured']

def test_frontend_folder_import(client):
    r=client.post('/api/workspaces/folder',data={'name':'folder'},files=[('files',('src/core.py',b'x=1\n','text/plain'))])
    assert r.status_code==200,r.text
    assert client.get('/api/workspaces/'+r.json()['id']).json()['snapshot']['file_count']==1

def test_zip_import_and_file_endpoint(client):
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z: z.writestr('root/src/core.py','x=1\n')
    r=client.post('/api/workspaces/zip',data={'name':'archive'},files={'archive':('a.zip',raw.getvalue(),'application/zip')})
    assert r.status_code==200,r.text
    wid=r.json()['id']; f=client.get(f'/api/workspaces/{wid}/file',params={'path':'src/core.py'})
    assert f.json()['content']=='x=1\n'
    assert client.get(f'/api/workspaces/{wid}/file',params={'path':'../core.py'}).status_code==400

def test_offline_run_and_events_persist(client):
    w=import_workspace(client)
    r=client.post('/api/runs',json={'workspace_id':w['id'],'question':'add'}).json()
    result=poll(client,'/api/runs/'+r['id'])
    assert result['status']=='done',result
    assert result['citation_check']=='offline_extract'
    assert result['evidence'][0]['path']=='calc.py'
    events=client.get('/api/events/'+r['id']).text
    assert 'event: evidence' in events and 'event: done' in events
    assert 'answer_delta' not in events
    trace=client.get('/api/runs/'+r['id']+'/export?format=json').json()
    assert trace['events']
    assert client.get('/api/runs?q=add').json()['total']==1

def test_manual_edit_complete_api_flow(client):
    w=import_workspace(client)
    p=client.post('/api/proposals',json={'workspace_id':w['id'],'snapshot_id':w['snapshot_id'],
       'title':'Fix add','edits':[{'path':'calc.py','old_text':'return a - b','new_text':'return a + b'}]})
    assert p.status_code==200,p.text
    proposal=p.json()
    assert client.post('/api/proposals/'+proposal['id']+'/decision',json={'approved':True,'digest':'f'*64}).status_code==400
    applied=client.post('/api/proposals/'+proposal['id']+'/decision',json={'approved':True,'digest':proposal['digest']})
    assert applied.status_code==200,applied.text
    assert 'a + b' in client.get('/api/workspaces/'+w['id']+'/file?path=calc.py').json()['content']
    snapshot=applied.json()['applied_snapshot']
    test=client.post('/api/tests',json={'workspace_id':w['id'],'snapshot_id':snapshot,'profile':'static'}).json()
    tr=poll(client,'/api/tests/'+test['id'])
    assert tr['result']['status']=='passed'
    assert tr['result']['executed_code'] is False
    assert client.get('/api/proposals/'+proposal['id']+'/patch').text

def test_dynamic_tests_require_confirmation(client):
    w=import_workspace(client)
    r=client.post('/api/tests',json={'workspace_id':w['id'],'snapshot_id':w['snapshot_id'],'profile':'pytest'})
    assert r.status_code==400

def test_static_snapshot_cross_workspace_rejected(client):
    w=import_workspace(client); other=import_workspace(client)
    r=client.post('/api/tests',json={'workspace_id':w['id'],'snapshot_id':other['snapshot_id']})
    assert r.status_code==400

def test_markdown_no_html_execution(client):
    html=client.post('/api/markdown',json={'text':'<script>alert(1)</script>\n\n| A | B |\n|---|---|\n| x | y |\n\n```python\nx=1\n```\n\n[evil](javascript:alert(1))'}).json()['html']
    assert '<script>' not in html
    assert '<table>' in html
    assert 'language-python' in html
    assert 'href="javascript:' not in html

def test_data_survives_restart(tmp_path):
    data=tmp_path/'persistent'
    with TestClient(create_app(data,force_portable=True),headers={'X-RepoPilot-Client':'test'}) as c:
        w=import_workspace(c)
        r=c.post('/api/runs',json={'workspace_id':w['id'],'question':'add'}).json()
        poll(c,'/api/runs/'+r['id'])
    with TestClient(create_app(data,force_portable=True)) as c:
        assert len(c.get('/api/workspaces').json())==1
        assert c.get('/api/runs/'+r['id']).json()['status']=='done'

def test_invalid_settings_never_echo_stored_secret(client):
    secret='a-very-private-unrecognized-token'
    client.patch('/api/settings',json={'api_key':secret})
    r=client.patch('/api/settings',json={'base_url':'not a url'})
    assert r.status_code==400
    assert secret not in r.text

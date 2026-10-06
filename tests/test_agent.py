import asyncio
import json
from fastapi.testclient import TestClient
from repopilot.api import create_app
from repopilot.ai import Gateway
from repopilot.workflow import Workflow
from repopilot.retrieval import Index
from test_api import import_workspace,poll


class ScriptedGateway(Gateway):
    async def complete(self,messages,role='planner',tools=None):
        await self.reserve(role)
        if role=='planner':
            data=json.loads(messages[-1]['content'])
            obj={'actions':[],'sufficient':True,'reason':'enough evidence'}
            if not data['previous_tools']:
                obj={'actions':[{'tool':'read_file','path':'calc.py','start':1,'end':10}],'sufficient':False,'reason':'inspect exact function'}
            return {'content':json.dumps(obj)}
        if role=='coder':return {'content':json.dumps({'title':'Fix addition','summary':'Use addition instead of subtraction',
            'edits':[{'path':'calc.py','old_text':'return a - b','new_text':'return a + b'}]})}
        await asyncio.sleep(.015)
        return {'content':f'{role} analysis [E1]'}
    async def stream(self,messages,role='answer'):
        await self.reserve(role)
        await self.emit('answer_delta',{'text':'## Checked\n\n','provisional':True})
        await self.emit('answer_delta',{'text':'Uses subtraction [E1].','provisional':True})
        return '## Checked\n\nUses subtraction [E1].'


def test_agent_creates_proposal_approval_and_finalizes(tmp_path):
    app=create_app(tmp_path/'data',gateway_factory=ScriptedGateway,force_portable=True)
    with TestClient(app,headers={'X-RepoPilot-Client':'test'}) as c:
        c.patch('/api/settings',json={'mode':'openai','base_url':'https://model.invalid/v1','model':'model',
                                      'allow_source_upload':True,'multi_agent':True})
        w=import_workspace(c)
        r=c.post('/api/runs',json={'workspace_id':w['id'],'question':'Fix add','intent':'change'}).json()
        result=poll(c,'/api/runs/'+r['id'])
        assert result['status']=='awaiting_approval',result
        pid=result['proposal_id']; p=c.get('/api/proposals/'+pid).json()
        assert p['status']=='pending'
        assert 'a - b' in c.get('/api/workspaces/'+w['id']+'/file?path=calc.py').json()['content']
        c.post('/api/proposals/'+pid+'/decision',json={'digest':p['digest'],'approved':True})
        result=poll(c,'/api/runs/'+r['id'],statuses=('done','error'))
        assert result['status']=='done',result
        assert 'a + b' in c.get('/api/workspaces/'+w['id']+'/file?path=calc.py').json()['content']
        events=c.get('/api/events/'+r['id']).text
        assert 'event: agent_started' in events
        assert 'event: answer_delta' in events
        assert 'event: approval_required' in events
        assert result['metrics']['model_requests']<=12

class InvalidCitation(ScriptedGateway):
    async def stream(self,messages,role='answer'):
        await self.emit('answer_delta',{'text':'Wrong reference [E999]','provisional':True})
        return 'Wrong reference [E999]'


def test_reject_invalid_citation_not_fake_groundedness(tmp_path):
    with TestClient(create_app(tmp_path/'data',gateway_factory=InvalidCitation,force_portable=True),headers={'X-RepoPilot-Client':'test'}) as c:
        c.patch('/api/settings',json={'mode':'openai','base_url':'https://model.invalid/v1','model':'x','allow_source_upload':True})
        w=import_workspace(c); r=c.post('/api/runs',json={'workspace_id':w['id'],'question':'add'}).json()
        result=poll(c,'/api/runs/'+r['id'])
        assert result['citation_check']=='rejected'
        assert 'Wrong reference' not in result['answer']
        assert 'answer_reset' in c.get('/api/events/'+r['id']).text


def test_cross_workspace_session_denied(client):
    w=import_workspace(client); another=import_workspace(client)
    r=client.post('/api/runs',json={'workspace_id':w['id'],'question':'add'}).json()
    poll(client,'/api/runs/'+r['id'])
    bad=client.post('/api/runs',json={'workspace_id':another['id'],'question':'hi','session_id':r['session_id']})
    assert bad.status_code==400

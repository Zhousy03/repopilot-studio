import asyncio
import json
import httpx
import pytest
from repopilot.ai import Gateway,ModelError,parse_json
from repopilot.config import UserSettings


def settings(**kw):
    return UserSettings(mode='openai',base_url='https://model.invalid/v1',model='test-model',allow_source_upload=True,**kw)

class Parts(httpx.AsyncByteStream):
    def __init__(self,complete=True):self.closed=False;self.complete=complete
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"first "}}]}\n\n'
        await asyncio.sleep(.02)
        yield b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n'
        if self.complete: yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    async def aclose(self):self.closed=True

async def test_real_transport_stream_order():
    parts=Parts(); seen=[]
    async def handler(req): return httpx.Response(200,stream=parts,headers={'content-type':'text/event-stream'})
    async def emit(event,data): seen.append((event,data))
    g=Gateway(settings(),emit,httpx.MockTransport(handler))
    text=await g.stream([{'role':'user','content':'hi'}])
    assert text=='first second'
    assert [x[1]['text'] for x in seen if x[0]=='answer_delta']==['first ','second']
    assert parts.closed
    assert g.metrics()['input_tokens'] is None

async def test_incomplete_stream_rejected_and_closed():
    parts=Parts(complete=False);seen=[]
    async def emit(e,d):seen.append(e)
    g=Gateway(settings(),emit,httpx.MockTransport(lambda req:httpx.Response(200,stream=parts)))
    with pytest.raises(ModelError): await g.stream([{'role':'user','content':'hi'}])
    assert 'answer_reset' in seen and parts.closed

async def test_auth_error_not_retried():
    requests=[]
    def handle(req):requests.append(req);return httpx.Response(401,json={'error':'hidden-secret'})
    g=Gateway(settings(),transport=httpx.MockTransport(handle))
    with pytest.raises(ModelError) as exc: await g.complete([{'role':'user','content':'hi'}])
    assert len(requests)==1
    assert 'hidden-secret' not in str(exc.value)

async def test_transient_retry(monkeypatch):
    calls=[]
    async def no_sleep(_):pass
    monkeypatch.setattr('repopilot.ai.asyncio.sleep',no_sleep)
    def handle(req):
        calls.append(req)
        return httpx.Response(503) if len(calls)==1 else httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}],
                       'usage':{'prompt_tokens':7,'completion_tokens':2}})
    g=Gateway(settings(),transport=httpx.MockTransport(handle))
    assert (await g.complete([{'role':'user','content':'hi'}]))['content']=='ok'
    assert g.calls==2 and g.input_tokens==7 and not g.metrics()['usage_complete']

async def test_model_budget():
    g=Gateway(settings(max_model_calls=2),transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})))
    for _ in range(2):await g.complete([{'role':'user','content':'x'}])
    with pytest.raises(ModelError,match='预算'):await g.complete([{'role':'user','content':'x'}])

async def test_embeddings_order_and_values():
    g=Gateway(settings(embedding_model='embed'),transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'data':[
        {'index':1,'embedding':[0,1]},{'index':0,'embedding':[1,0]}]})))
    assert await g.embed(['a','b'])==[[1,0],[0,1]]

@pytest.mark.parametrize('text',['not json','[]','null','{"x": bad}'])
def test_bad_plan_json(text):
    with pytest.raises(ValueError):parse_json(text)

def test_json_fence(): assert parse_json('```json\n{"x":1}\n```')=={'x':1}

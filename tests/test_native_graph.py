import pytest

async def test_native_langgraph_checkpointer(tmp_path):
    pytest.importorskip('langgraph')
    pytest.importorskip('langgraph.checkpoint.sqlite.aio')
    from langgraph.graph import StateGraph,START,END
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from typing_extensions import TypedDict
    class State(TypedDict):value:int
    async def inc(state):return {'value':state['value']+1}
    b=StateGraph(State);b.add_node('inc',inc);b.add_edge(START,'inc');b.add_edge('inc',END)
    config={'configurable':{'thread_id':'native-checkpoint-test'}}
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'checkpoints.db')) as saver:
        graph=b.compile(checkpointer=saver)
        result=await graph.ainvoke({'value':3},config)
        assert result['value']==4
        assert (await graph.aget_state(config)).values['value']==4

async def test_native_application_graph(tmp_path):
    pytest.importorskip('langgraph')
    pytest.importorskip('langgraph.checkpoint.sqlite.aio')
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from repopilot.config import Configuration
    from repopilot.db import Store
    from repopilot.workspaces import Workspaces
    from repopilot.service import Engine
    from repopilot.models import Ask
    c=Configuration(tmp_path/'data');d=Store(c.data_dir);ws=Workspaces(c,d)
    w=ws.ingest('temporary-test',{'code.py':b'def hello():\n    return "hi"\n'})
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.db')) as saver:
        engine=Engine(c,d,ws,saver)
        r=engine.launch(Ask(workspace_id=w['id'],question='hello'))
        await engine.tasks[r['id']]
        result=d.get('run',r['id'])
        assert result['status']=='done',result
        assert result['runtime']=='langgraph'
        await engine.close()

"""Native LangGraph orchestration, loaded only when installed.
Side effects (applying code) are in the service's approval transaction, not before
interrupt(), so replay cannot reapply code.
"""
from __future__ import annotations
from importlib.util import find_spec
from .workflow import State


def available() -> bool:
    try: return find_spec('langgraph.graph') is not None
    except (ModuleNotFoundError,ValueError): return False


def build_graph(flow,checkpointer):
    from langgraph.graph import StateGraph,START,END
    from langgraph.types import interrupt
    b=StateGraph(State)
    for name in ('retrieve','plan','tools','specialists','coder','generate','verify','proposal'):
        async def node(state,name=name): return await flow.invoke_node(name,state)
        b.add_node(name,node)
    async def approval(state):
        if state.get('proposal_id'):
            p=flow.changes.db.get('proposal',state['proposal_id'])
            decision=interrupt({'proposal_id':p['id'],'digest':p['digest'],'files':p['files']})
            return {'approval':decision}
        return {'approval':{}}
    b.add_node('approval',approval)
    b.add_edge(START,'retrieve'); b.add_edge('retrieve','plan')
    b.add_conditional_edges('plan',lambda s:'specialists' if s.get('sufficient') else 'tools',
                            {'specialists':'specialists','tools':'tools'})
    b.add_edge('tools','plan')
    for a,z in zip(('specialists','coder','generate','verify','proposal'),('coder','generate','verify','proposal','approval')):
        b.add_edge(a,z)
    b.add_edge('approval',END)
    return b.compile(checkpointer=checkpointer)

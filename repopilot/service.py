"""Application-level durable runs, explicit cancellation and approval boundaries."""
from __future__ import annotations
import asyncio
import contextlib
import json
import time
from importlib.util import find_spec
from .ai import Gateway,ModelError,make_gateway
from .config import Configuration,UserSettings
from .db import Store,uid,now
from .workspaces import Workspaces
from .retrieval import Index
from .changes import Changes,static_check
from .github import GitHub
from .workflow import Workflow,State
from .graph import build_graph
from .models import Ask,TestRequest
from .sandbox import Sandbox
from .markdown import render_markdown
from .security import redact

TERMINAL={'done','error','cancelled','interrupted','awaiting_approval'}
NODES=['retrieve','plan','tools','specialists','coder','generate','verify','proposal']


class Engine:
    def __init__(self,config: Configuration,db: Store,ws: Workspaces,checkpointer=None,gateway_factory=make_gateway):
        self.config=config; self.db=db; self.ws=ws; self.changes=Changes(ws,db)
        self.github=GitHub(config,ws,db); self.checkpointer=checkpointer; self.gateway_factory=gateway_factory
        self.tasks={}; self.semaphore=asyncio.Semaphore(2); self.cache={}
        self.runtime='langgraph' if checkpointer is not None else 'portable-state-machine'
        # A process cannot resume a lost socket or arbitrary running Python stack.
        for kind in ('run','test','index_job'):
            for record in db.list(kind):
                if record.get('status') in {'running','queued'}:
                    db.update(kind,record['id'],{'status':'interrupted','error':'服务重启，需显式恢复或重新运行'})

    def index(self,wid,sid):
        key=(wid,sid)
        if key not in self.cache:
            if len(self.cache)>=8: self.cache.pop(next(iter(self.cache)))
            self.cache[key]=Index(self.ws,wid,sid)
        return self.cache[key]

    async def emit(self,rid,event,data):
        # Bounded individual event payload; full reports live in state.
        self.db.event(rid,event,data)
        if event=='model_call': self.db.update('run',rid,{'model_requests':data['count']})

    def ensure_capacity(self):
        if sum(not x.done() for x in self.tasks.values())>=6:
            raise ValueError('任务队列已满，请等待或停止现有任务')

    def launch(self,request: Ask):
        self.ensure_capacity()
        w,s,_=self.ws.read(request.workspace_id)
        if not request.question.strip(): raise ValueError('请输入问题')
        if self.config.value.mode=='offline' and request.intent in {'change','review'}:
            raise ValueError('代码审查和 AI 修改提案需要启用模型；离线模式可检索源码或手动编辑提案')
        if self.config.value.mode=='openai':
            make_gateway(self.config.value).ensure()
        session_id=request.session_id or uid()
        try:
            session=self.db.get('session',session_id)
            if session['workspace_id']!=w['id']: raise ValueError('会话不属于此工作区')
        except KeyError: self.db.put('session',{'id':session_id,'workspace_id':w['id'],'created_at':now()})
        for r in self.db.list('run'):
            if r.get('session_id')==session_id and r['status'] in {'queued','running','awaiting_approval'}:
                raise ValueError('这个会话有运行中或待审批的任务，请先处理或新建会话')
        history=[r for r in self.db.list('run') if r.get('session_id')==session_id and r['status']=='done' and r['snapshot_id']==s['id']][:3]
        history=[{'question':r['question'],'answer':r.get('answer','')[:2500]} for r in reversed(history)]
        rid=uid(); state:State={'run_id':rid,'workspace_id':w['id'],'snapshot_id':s['id'],'question':request.question,
             'intent':request.intent,'urls':request.urls,'history':history,'evidence':[],'tool_calls':0,'rounds':0,'warnings':[]}
        record={'id':rid,'workspace_id':w['id'],'snapshot_id':s['id'],'session_id':session_id,
                'question':request.question,'intent':request.intent,'status':'queued','created_at':now(),
                'state':state,'next_node':'retrieve','runtime':self.runtime,'answer':'','model_requests':0,'error':None}
        self.db.put('run',record); self.db.event(rid,'started',{'run_id':rid,'session_id':session_id,'runtime':self.runtime})
        self.tasks[rid]=asyncio.create_task(self.execute(rid,settings=self.config.value.model_copy(deep=True)))
        return self.public_run(record)

    async def execute(self,rid: str,resume=False,approval=None,settings:UserSettings|None=None):
        settings=settings or self.config.value.model_copy(deep=True)
        async with self.semaphore:
            r=self.db.get('run',rid); state=r['state']; begin=time.monotonic()
            self.db.update('run',rid,{'status':'running','error':None})
            async def emit(event,data): await self.emit(rid,event,data)
            try:
                gateway=self.gateway_factory(settings,emit)
            except Exception as exc:
                self.db.update('run',rid,{'status':'error','error':'模型适配器初始化失败：'+type(exc).__name__})
                await emit('error',{'message':'模型适配器不可用，请检查设置和依赖'})
                self.tasks.pop(rid,None)
                return
            gateway.calls=r.get('model_requests',0)
            metrics=state.get('metrics',{})
            gateway.input_tokens=metrics.get('input_tokens') or 0; gateway.output_tokens=metrics.get('output_tokens') or 0
            gateway.usage_reports=metrics.get('usage_reports') or 0
            try:
                index=await asyncio.to_thread(self.index,r['workspace_id'],r['snapshot_id'])
                flow=Workflow(index,gateway,self.github,emit,self.changes)
                async with asyncio.timeout(gateway.s.timeout_seconds):
                    if self.checkpointer is not None and r['runtime']=='langgraph':
                        from langgraph.types import Command
                        graph=build_graph(flow,self.checkpointer)
                        config={'configurable':{'thread_id':rid},'recursion_limit':60}
                        arg=Command(resume=approval) if approval is not None else (None if resume else state)
                        result=await graph.ainvoke(arg,config)
                        snap=await graph.aget_state(config)
                        state=dict(snap.values)
                        self.db.update('run',rid,{'state':state})
                        if snap.next:
                            p=self.db.get('proposal',state['proposal_id'])
                            self.db.update('run',rid,{'status':'awaiting_approval','answer':state.get('answer',''),'proposal_id':p['id']})
                            await emit('approval_required',{'proposal_id':p['id'],'digest':p['digest'],'files':p['files']})
                            return
                    else:
                        node=r.get('next_node','retrieve') if resume else 'retrieve'
                        if approval is not None:
                            state['approval']=approval; node='finished'
                        while node!='finished':
                            update=await flow.invoke_node(node,state); state=state|update
                            if node=='plan': node='specialists' if state.get('sufficient') else 'tools'
                            elif node=='tools': node='plan'
                            elif node=='proposal': node='finished'
                            else: node={'retrieve':'plan','specialists':'coder','coder':'generate','generate':'verify','verify':'proposal'}[node]
                            self.db.update('run',rid,{'state':state,'next_node':node})
                        if state.get('proposal_id') and approval is None:
                            p=self.db.get('proposal',state['proposal_id'])
                            self.db.update('run',rid,{'status':'awaiting_approval','answer':state.get('answer',''),'proposal_id':p['id']})
                            await emit('approval_required',{'proposal_id':p['id'],'digest':p['digest'],'files':p['files']})
                            return
                record=self.db.update('run',rid,{'status':'done','state':state,'answer':state.get('answer',''),
                    'finished_at':now(),'duration_ms':round((time.monotonic()-begin)*1000),
                    'proposal_id':state.get('proposal_id'),'metrics':gateway.metrics()})
                await emit('done',self.public_run(record))
            except asyncio.CancelledError:
                self.db.update('run',rid,{'status':'cancelled','state':state,'error':'已停止；未完成草稿不作为最终结果'})
                await emit('answer_reset',{'reason':'任务已停止'})
                await emit('cancelled',{'run_id':rid})
                raise
            except Exception as exc:
                error='任务超过本次运行时长预算' if isinstance(exc,TimeoutError) else (str(exc) if isinstance(exc,(ValueError,ModelError,KeyError)) else f'任务失败：{type(exc).__name__}')
                error=redact(error)[:1500]
                self.db.update('run',rid,{'status':'error','state':state,'error':error,'metrics':gateway.metrics()})
                await emit('answer_reset',{'reason':'未完成草稿已撤下'})
                await emit('error',{'message':error,'run_id':rid})
            finally:
                # Removes strong references without retaining completed tasks forever.
                self.tasks.pop(rid,None)

    def resume(self,rid: str):
        self.ensure_capacity(); r=self.db.get('run',rid)
        if r['status'] not in {'interrupted','error'}: raise ValueError('只有中断或失败的任务可以恢复')
        if rid in self.tasks: raise ValueError('任务仍在运行')
        if r['runtime']=='langgraph' and self.checkpointer is None: raise ValueError('该任务需要原生 LangGraph Checkpointer，请安装原有依赖后恢复')
        self.db.update('run',rid,{'status':'queued'})
        self.tasks[rid]=asyncio.create_task(self.execute(rid,resume=True))
        return {'id':rid,'status':'queued'}

    async def cancel(self,id: str):
        task=self.tasks.get(id)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await task
            self.tasks.pop(id,None)
            # A task cancelled before acquiring the queue semaphore has not
            # entered execute()'s handler yet. Still finish its durable record.
            for kind in ('run','test','index_job'):
                try:
                    record=self.db.get(kind,id)
                except KeyError:
                    continue
                if record['status'] in {'queued','running'}:
                    self.db.update(kind,id,{'status':'cancelled','error':'任务已停止'})
                    await self.emit(id,'cancelled',{'id':id})
                break
        else:
            r=self.db.get('run',id)
            if r['status'] not in {'awaiting_approval','interrupted','error'}: raise ValueError('任务已结束')
            if r.get('proposal_id'):
                p=self.db.get('proposal',r['proposal_id'])
                if p['status']=='pending': self.changes.decide(p['id'],p['digest'],False)
            self.db.update('run',id,{'status':'cancelled'})
            await self.emit(id,'cancelled',{'run_id':id})
        return {'id':id,'status':'cancelled'}

    def decide(self,pid: str,digest: str,approved: bool):
        p=self.changes.decide(pid,digest,approved)
        rid=p.get('run_id')
        if rid:
            r=self.db.get('run',rid)
            if r['status']=='awaiting_approval' and rid not in self.tasks:
                self.tasks[rid]=asyncio.create_task(self.execute(rid,resume=True,approval={
                    'approved':approved,'applied_snapshot':p.get('applied_snapshot'),'proposal_id':pid}))
        return p

    def public_run(self,r: dict) -> dict:
        state=r.get('state',{})
        return {k:v for k,v in r.items() if k not in {'state'}}|{
            'answer_html':render_markdown(r.get('answer','')),'evidence':state.get('evidence',[]),
            'warnings':state.get('warnings',[]),'citation_check':state.get('citation_check'),
            'reports':{'researcher':state.get('research_report',''),'reviewer':state.get('review_report','')},
            'metrics':r.get('metrics') or state.get('metrics',{}),
            'notice':'引用编号检查不是事实验证；请核对源码。'}

    def launch_test(self,request: TestRequest):
        self.ensure_capacity(); self.ws.read(request.workspace_id,request.snapshot_id)
        if request.profile!='static' and not request.approved: raise ValueError('动态测试需要本次明确确认')
        record={'id':uid(),'workspace_id':request.workspace_id,'snapshot_id':request.snapshot_id,
                'profile':request.profile,'status':'queued','created_at':now()}
        self.db.put('test',record); self.tasks[record['id']]=asyncio.create_task(self.test_task(record))
        return record

    async def test_task(self,record):
        id=record['id']; self.db.update('test',id,{'status':'running'})
        async def emit(event,data): self.db.event(id,event,data)
        try:
            # Verify immutable bytes before exposing the snapshot to the container.
            files=await asyncio.to_thread(self.ws.content_map,record['workspace_id'],record['snapshot_id'])
            if record['profile']=='static': result=await asyncio.to_thread(static_check,files)
            else:
                _,_,root=self.ws.read(record['workspace_id'],record['snapshot_id'])
                result=await Sandbox(self.config.value).run(root,record['profile'],emit)
            saved=self.db.update('test',id,{'status':'done','finished_at':now(),'result':result})
            await emit('done',saved)
        except asyncio.CancelledError:
            self.db.update('test',id,{'status':'cancelled'}); await emit('cancelled',{'id':id}); raise
        except Exception as exc:
            msg=str(exc)[:1500] if isinstance(exc,ValueError) else type(exc).__name__
            self.db.update('test',id,{'status':'error','error':msg}); await emit('error',{'message':msg})
        finally: self.tasks.pop(id,None)

    def launch_index(self,wid):
        self.ensure_capacity(); w,s,_=self.ws.read(wid)
        record={'id':uid(),'workspace_id':wid,'snapshot_id':s['id'],'status':'queued','created_at':now()}
        self.db.put('index_job',record)
        async def job():
            self.db.update('index_job',record['id'],{'status':'running'})
            async def emit(event,data): self.db.event(record['id'],event,data)
            try:
                index=await asyncio.to_thread(self.index,wid,s['id'])
                result=await index.build_vectors(Gateway(self.config.value),emit)
                self.db.update('index_job',record['id'],{'status':'done','result':result}); await emit('done',result)
            except asyncio.CancelledError:
                self.db.update('index_job',record['id'],{'status':'cancelled'}); raise
            except Exception as exc:
                self.db.update('index_job',record['id'],{'status':'error','error':type(exc).__name__+': '+redact(str(exc))[:500]})
                await emit('error',{'message':'语义索引失败，请检查 Embedding 配置和额度'})
            finally: self.tasks.pop(record['id'],None)
        self.tasks[record['id']]=asyncio.create_task(job()); return record

    async def close(self):
        ids=list(self.tasks); tasks=list(self.tasks.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        for id in ids:
            for kind in ('run','test','index_job'):
                try:
                    r=self.db.get(kind,id)
                    if r['status']=='cancelled': self.db.update(kind,id,{'status':'interrupted','error':'应用关闭，任务被中断'})
                except KeyError: pass

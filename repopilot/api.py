"""Single-operator, loopback-only service. Not a public multi-tenant SaaS."""
from __future__ import annotations
import asyncio
import io
import json
import os
import time
import zipfile
import importlib.util
from contextlib import asynccontextmanager,AsyncExitStack
from pathlib import Path
from typing import Any
from fastapi import FastAPI,Request,UploadFile,File,Form,HTTPException,Query
from fastapi.responses import FileResponse,JSONResponse,Response,StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import ValidationError
from . import __version__
from .config import Configuration
from .db import Store
from .workspaces import Workspaces
from .models import GithubImport,FolderImport,Ask,ManualProposal,ChangeSet,Decision,TestRequest,PublishRequest
from .service import Engine,TERMINAL
from .sandbox import Sandbox
from .markdown import render_markdown
from .security import redact
from .ai import ModelError
from .documents import HOSTS

STATIC=Path(__file__).parent/'static'


def create_app(data_dir: Path|None=None,gateway_factory=None,force_portable=False):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config=Configuration(data_dir); db=Store(config.data_dir); ws=Workspaces(config,db)
        async with AsyncExitStack() as stack:
            cp=None; checkpoint_notice=None
            if not force_portable:
                try:
                    if config.checkpoint_url:
                        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                        cp=await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(config.checkpoint_url))
                        await cp.setup()
                    else:
                        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
                        cp=await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(config.data_dir/'checkpoints.sqlite3')))
                        await cp.setup()
                except ImportError:
                    if config.checkpoint_url: raise RuntimeError('CHECKPOINT_DATABASE_URL 已设置，但缺少 postgres 扩展')
                    checkpoint_notice='未安装 ai 扩展；使用明确标识的应用状态机。安装 .[ai] 可启用原生 LangGraph。'
            engine=Engine(config,db,ws,cp,**({'gateway_factory':gateway_factory} if gateway_factory else {}))
            app.state.config=config; app.state.db=db; app.state.ws=ws; app.state.engine=engine
            app.state.checkpoint_notice=checkpoint_notice
            yield
            await engine.close()

    app=FastAPI(title='RepoPilot Studio',version=__version__,lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])

    @app.middleware('http')
    async def boundary(request: Request,call_next):
        expected=f'{request.url.scheme}://{request.headers.get("host","")}'
        origin=request.headers.get('origin')
        if origin and origin!=expected: return JSONResponse({'detail':'仅允许同源访问'},403)
        if request.method in {'POST','PUT','PATCH','DELETE'} and request.headers.get('x-repopilot-client') not in {'browser','cli','test'}:
            return JSONResponse({'detail':'缺少 X-RepoPilot-Client 请求头'},403)
        size=request.headers.get('content-length')
        if size:
            try:
                if int(size)>70_000_000: return JSONResponse({'detail':'上传内容过大'},413)
            except ValueError: return JSONResponse({'detail':'非法 Content-Length'},400)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ModelError)
    async def model_error(request,exc): return JSONResponse({'detail':redact(str(exc))[:1000]},502)

    @app.exception_handler(zipfile.BadZipFile)
    async def bad_zip(request,exc): return JSONResponse({'detail':'ZIP 内容无效或损坏'},400)

    @app.exception_handler(KeyError)
    async def missing(request,exc): return JSONResponse({'detail':str(exc)},404)
    @app.exception_handler(ValueError)
    async def invalid(request,exc): return JSONResponse({'detail':redact(str(exc))[:1800]},400)
    @app.exception_handler(ValidationError)
    async def validation(request,exc):
        errors=exc.errors(include_input=False,include_context=False,include_url=False)
        return JSONResponse({'detail':'参数验证失败','errors':errors},400)

    @app.get('/api/health')
    async def health():
        return {'status':'ok','version':__version__,'runtime':app.state.engine.runtime,
                'mode':app.state.config.value.mode,'checkpoint_notice':app.state.checkpoint_notice,
                'profile':'single-operator-local','sample_repository':False}

    @app.get('/api/settings')
    async def settings(): return app.state.config.public()|{'documentation_hosts':sorted(HOSTS)}
    @app.patch('/api/settings')
    async def save_settings(body:dict): return app.state.config.update(body)
    @app.get('/api/capabilities')
    async def capabilities():
        return {'runtime':app.state.engine.runtime,'sandbox':await Sandbox(app.state.config.value).availability(),
                'native_langgraph':app.state.engine.checkpointer is not None,'github_configured':bool(app.state.config.value.github_token)}
    @app.post('/api/settings/check-model')
    async def check_model(body:dict | None = None):
        from .ai import make_gateway,text_content
        # This check sends only a fixed greeting, never repository source.
        settings=app.state.config.preview(body)
        settings=settings.model_copy(update={'mode':'openai','allow_source_upload':True,
                                             'backup_url':'','backup_model':'','backup_key':''})
        gateway=make_gateway(settings)
        started=time.monotonic()
        try:
            response=await gateway.complete([{'role':'user','content':'Reply with OK.'}],role='connectivity_check')
            return {'ok':True,'model':settings.model,'adapter':type(gateway).__name__,
                    'latency_ms':round((time.monotonic()-started)*1000),
                    'response':redact(text_content(response.get('content','')))[:200]}
        except Exception as exc: raise ValueError(f'连接验证失败：{redact(str(exc))[:500]}') from exc

    @app.get('/api/workspaces')
    async def workspaces():
        out=[]
        for w in app.state.db.list('workspace'):
            try: s=app.state.db.get('snapshot',w['snapshot_id']); out.append(w|{'file_count':s['file_count'],'bytes':s['bytes']})
            except KeyError: continue
        return out

    @app.post('/api/workspaces/folder')
    async def folder(name:str=Form(...,max_length=100),files:list[UploadFile]=File(...)):
        if len(files)>app.state.config.max_files: raise ValueError('上传文件数量超过限制')
        entries={}; total=0
        for file in files:
            raw=await file.read(app.state.config.max_file_bytes+1); await file.close()
            if len(raw)>app.state.config.max_file_bytes: continue
            total+=len(raw)
            if total>app.state.config.max_repo_bytes: raise ValueError('目录总大小超出限制')
            if file.filename in entries: raise ValueError('上传包含重复文件路径')
            entries[file.filename]=raw
        return await asyncio.to_thread(app.state.ws.ingest,name,entries)

    @app.post('/api/workspaces/text')
    async def folder_json(body:FolderImport):
        if len({f.path for f in body.files})!=len(body.files): raise ValueError('上传包含重复文件路径')
        return await asyncio.to_thread(app.state.ws.ingest,body.name,{f.path:f.content.encode('utf-8') for f in body.files})

    @app.post('/api/workspaces/zip')
    async def zip_import(name:str=Form(...,max_length=100),archive:UploadFile=File(...)):
        raw=await archive.read(app.state.config.max_upload_bytes+1); await archive.close()
        entries=await asyncio.to_thread(app.state.ws.zip_entries,raw)
        return await asyncio.to_thread(app.state.ws.ingest,name,entries)

    @app.post('/api/workspaces/github')
    async def github_import(body:GithubImport):
        try: return await app.state.engine.github.import_repo(body.url,body.ref)
        except Exception as exc:
            if isinstance(exc,ValueError): raise
            raise ValueError('GitHub 导入失败，请检查网络、访问权限和归档大小') from exc

    @app.get('/api/workspaces/{wid}')
    async def workspace(wid:str):
        w,s,_=app.state.ws.read(wid); return w|{'snapshot':s}
    @app.delete('/api/workspaces/{wid}')
    async def delete_workspace(wid:str):
        for kind in ('run','test','index_job'):
            if any(x.get('workspace_id')==wid and x['status'] in {'running','queued','awaiting_approval'} for x in app.state.db.list(kind)):
                raise ValueError('工作区仍有运行中或待审批任务，请先停止或处理')
        await asyncio.to_thread(app.state.ws.remove,wid)
        app.state.engine.cache={k:v for k,v in app.state.engine.cache.items() if k[0]!=wid}
        return {'deleted':wid}
    @app.get('/api/workspaces/{wid}/versions')
    async def versions(wid:str):
        app.state.db.get('workspace',wid)
        return [{k:v for k,v in s.items() if k!='files'} for s in app.state.db.list('snapshot') if s['workspace_id']==wid]
    @app.get('/api/workspaces/{wid}/file')
    async def file(wid:str,path:str=Query(max_length=500),snapshot:str|None=None):
        return await asyncio.to_thread(app.state.ws.source,wid,path,snapshot)
    @app.get('/api/workspaces/{wid}/export')
    async def export_workspace(wid:str,snapshot:str|None=None):
        data=await asyncio.to_thread(app.state.ws.export,wid,snapshot)
        return Response(data,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="workspace.zip"'})
    @app.post('/api/workspaces/{wid}/index')
    async def index_workspace(wid:str): return app.state.engine.launch_index(wid)
    @app.get('/api/index-jobs/{id}')
    async def index_job(id:str): return app.state.db.get('index_job',id)
    @app.get('/api/workspaces/{wid}/symbols')
    async def symbols(wid:str,q:str=Query(default='',max_length=200)):
        w,s,_=app.state.ws.read(wid)
        idx=await asyncio.to_thread(app.state.engine.index,wid,s['id'])
        return idx.find_symbols(q)

    @app.post('/api/runs',status_code=202)
    async def start_run(body:Ask): return app.state.engine.launch(body)
    @app.get('/api/runs')
    async def history(workspace_id:str='',q:str=Query(default='',max_length=300),offset:int=0,limit:int=30):
        limit=max(1,min(limit,100)); offset=max(0,offset)
        rows=[x for x in app.state.db.list('run') if (not workspace_id or x['workspace_id']==workspace_id)
              and q.lower() in x['question'].lower()]
        return {'total':len(rows),'items':[{k:v for k,v in r.items() if k not in {'state','answer'}} for r in rows[offset:offset+limit]]}
    @app.get('/api/runs/{rid}')
    async def run(rid:str): return app.state.engine.public_run(app.state.db.get('run',rid))
    @app.post('/api/runs/{rid}/cancel')
    async def cancel_run(rid:str): return await app.state.engine.cancel(rid)
    @app.post('/api/runs/{rid}/resume')
    async def resume_run(rid:str): return app.state.engine.resume(rid)
    @app.get('/api/runs/{rid}/checkpoints')
    async def checkpoints(rid:str):
        r=app.state.db.get('run',rid)
        if app.state.engine.checkpointer is None or r['runtime']!='langgraph':
            return {'runtime':r['runtime'],'state':r['state'],'next_node':r.get('next_node'),
                    'notice':'应用节点检查点，不冒充原生 LangGraph StateSnapshot。'}
        config={'configurable':{'thread_id':rid}}
        cp=app.state.engine.checkpointer; rows=[]
        async for item in cp.alist(config,limit=100):
            rows.append({'config':item.config,'metadata':item.metadata,'at':item.checkpoint.get('ts')})
        return {'runtime':'langgraph','checkpoints':rows}
    @app.get('/api/runs/{rid}/export')
    async def export_run(rid:str,format:str='markdown'):
        r=app.state.engine.public_run(app.state.db.get('run',rid))
        if format=='json':
            content=json.dumps(r|{'events':app.state.db.all_events(rid)},ensure_ascii=False,indent=2)
            return Response(content,media_type='application/json',headers={'Content-Disposition':'attachment; filename="analysis.json"'})
        content='# '+r['question']+'\n\n'+r.get('answer','')+'\n\n## 证据来源\n\n'
        content+='\n'.join(f"- [{e['label']}] {e['path']} L{e.get('start_line',1)}–L{e.get('end_line',1)} (snapshot {e.get('snapshot_id','external')})" for e in r['evidence'])
        content+='\n\n引用编号有效不等于事实验证；未附测试记录不代表测试通过。\n'
        return Response(content,media_type='text/markdown',headers={'Content-Disposition':'attachment; filename="analysis.md"'})

    @app.get('/api/events/{id}')
    async def events(id:str,request:Request,after:int=0):
        kind=None
        for candidate in ('run','test','index_job'):
            try: app.state.db.get(candidate,id); kind=candidate; break
            except KeyError: pass
        if kind is None: raise KeyError('任务不存在')
        if request.headers.get('Last-Event-ID','').isdigit(): after=max(after,int(request.headers['Last-Event-ID']))
        async def stream():
            cursor=after; heartbeat=0
            while True:
                if await request.is_disconnected(): return
                rows=await asyncio.to_thread(app.state.db.events,id,cursor)
                for row in rows:
                    cursor=row['seq']
                    yield f"id: {cursor}\nevent: {row['event']}\ndata: {json.dumps(row['data'],ensure_ascii=False)}\n\n"
                record=app.state.db.get(kind,id)
                if not rows and record['status'] in TERMINAL: return
                heartbeat+=1
                if heartbeat>=30:
                    yield ': keep-alive\n\n'; heartbeat=0
                await asyncio.sleep(.2)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})

    @app.post('/api/markdown')
    async def markdown(body:dict):
        return {'html':render_markdown(str(body.get('text',''))[:60000])}

    @app.get('/api/proposals')
    async def proposals(workspace_id:str=''):
        return [{k:v for k,v in p.items() if k not in {'diff','spec'}}|{'title':p['spec']['title']} for p in app.state.db.list('proposal')
                if not workspace_id or p['workspace_id']==workspace_id]
    @app.post('/api/proposals')
    async def manual_proposal(body:ManualProposal):
        spec=ChangeSet.model_validate(body.model_dump(exclude={'workspace_id','snapshot_id'}))
        return await asyncio.to_thread(app.state.engine.changes.create,body.workspace_id,body.snapshot_id,spec)
    @app.get('/api/proposals/{pid}')
    async def proposal(pid:str): return app.state.db.get('proposal',pid)
    @app.post('/api/proposals/{pid}/decision')
    async def decide(pid:str,body:Decision): return app.state.engine.decide(pid,body.digest,body.approved)
    @app.post('/api/proposals/{pid}/revert')
    async def revert(pid:str): return await asyncio.to_thread(app.state.engine.changes.inverse,pid)
    @app.get('/api/proposals/{pid}/patch')
    async def patch(pid:str):
        p=app.state.db.get('proposal',pid)
        return Response(p['diff'],media_type='text/x-diff',headers={'Content-Disposition':'attachment; filename="changes.patch"'})
    @app.get('/api/proposals/{pid}/publish-preview')
    async def preview_publish(pid:str): return await app.state.engine.github.preview(pid)
    @app.post('/api/proposals/{pid}/publish')
    async def publish(pid:str,body:PublishRequest): return await app.state.engine.github.publish(pid,body.fingerprint)

    @app.get('/api/tests')
    async def tests(workspace_id:str=''):
        return [t for t in app.state.db.list('test') if not workspace_id or t['workspace_id']==workspace_id]
    @app.post('/api/tests',status_code=202)
    async def start_test(body:TestRequest): return app.state.engine.launch_test(body)
    @app.get('/api/tests/{id}')
    async def test(id:str): return app.state.db.get('test',id)
    @app.post('/api/tests/{id}/cancel')
    async def cancel_test(id:str):
        task=app.state.engine.tasks.get(id)
        if task: task.cancel()
        return {'id':id,'cancel_requested':bool(task)}
    @app.get('/api/evaluations')
    async def eval_summary():
        rows=app.state.db.list('run'); done=[x for x in rows if x['status']=='done']
        return {'recorded_runs':len(rows),'completed':len(done),'errors':sum(x['status']=='error' for x in rows),
                'mean_duration_ms':round(sum(x.get('duration_ms',0) for x in done)/len(done)) if done else None,
                'notice':'只汇总运行记录；没有标注数据时不计算答案准确率。'}

    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    @app.get('/',include_in_schema=False)
    async def index(): return FileResponse(STATIC/'index.html',headers={'Cache-Control':'no-cache'})
    return app


app=create_app()

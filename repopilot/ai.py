"""Bounded OpenAI-compatible chat/embedding transport with honest usage telemetry.
No key or full provider error body is returned to the browser.
"""
from __future__ import annotations
import asyncio
import json
import math
import random
import re
import time
from collections.abc import Callable, Awaitable
from typing import Any
from urllib.parse import urlsplit
import httpx
from .config import UserSettings
from .security import fingerprint

Emit=Callable[[str,dict],Awaitable[None]]


async def noop(event: str,data: dict): pass


def parse_json(text: str) -> dict:
    text=text.strip()
    if text.startswith('```'):
        text=re.sub(r'^```(?:json)?\s*','',text,flags=re.I)
        text=re.sub(r'\s*```$','',text)
    try: value=json.loads(text)
    except json.JSONDecodeError as exc: raise ValueError('模型没有返回可解析的 JSON 对象') from exc
    if not isinstance(value,dict): raise ValueError('模型输出必须是 JSON 对象')
    return value


def text_content(value: Any) -> str:
    if isinstance(value,str): return value
    if isinstance(value,list):
        return ''.join(x.get('text','') for x in value if isinstance(x,dict) and x.get('type') in {'text','output_text'})
    return ''


class ModelError(RuntimeError): pass


class Gateway:
    def __init__(self,settings: UserSettings,emit: Emit=noop,transport=None):
        self.s=settings.model_copy(deep=True); self.emit=emit; self.transport=transport
        self.calls=0; self.input_tokens=0; self.output_tokens=0; self.usage_reports=0; self.lock=asyncio.Lock()
        self.providers=[]
        if self.s.base_url and self.s.model: self.providers.append((self.s.base_url,self.s.model,self.s.api_key))
        if self.s.backup_url and self.s.backup_model: self.providers.append((self.s.backup_url,self.s.backup_model,self.s.backup_key))

    def ensure(self):
        if self.s.mode!='openai' or not self.providers: raise ModelError('请在设置中配置并启用聊天模型')
        if not self.s.allow_source_upload: raise ModelError('尚未确认向配置的模型服务发送问题和选中的源码')

    async def reserve(self,role: str):
        async with self.lock:
            if self.calls>=self.s.max_model_calls: raise ModelError('达到单次分析的模型请求预算')
            self.calls+=1
            await self.emit('model_call',{'role':role,'count':self.calls,'limit':self.s.max_model_calls})

    def usage(self,data):
        u=data.get('usage') or {}
        if u:
            self.usage_reports+=1
            self.input_tokens+=int(u.get('prompt_tokens',0) or 0)
            self.output_tokens+=int(u.get('completion_tokens',0) or 0)

    def metrics(self):
        return {'model_requests':self.calls,'input_tokens':self.input_tokens if self.usage_reports else None,
                'output_tokens':self.output_tokens if self.usage_reports else None,
                'usage_reports':self.usage_reports,'usage_complete':self.usage_reports==self.calls,
                'estimated_cost':round((self.input_tokens*self.s.input_price_per_million+
                       self.output_tokens*self.s.output_price_per_million)/1_000_000,6) if self.usage_reports and
                       (self.s.input_price_per_million or self.s.output_price_per_million) else None,
                'cost_notice':'按已配置单价及服务实际返回的 usage 估算；缺失用量或失败请求不在其中。'}

    def client(self,url: str):
        # Honour the operator's network configuration for remote services, while
        # keeping local model traffic off environment-configured proxies.
        remote=urlsplit(url).hostname not in {'localhost','127.0.0.1','::1'}
        return httpx.AsyncClient(timeout=self.s.request_timeout,transport=self.transport,follow_redirects=False,trust_env=remote)

    @staticmethod
    def headers(key):
        return {'Authorization':f'Bearer {key}','Content-Type':'application/json'} if key else {'Content-Type':'application/json'}

    @staticmethod
    def transient(exc) -> bool:
        return isinstance(exc,httpx.TransportError) or isinstance(exc,httpx.HTTPStatusError) and (exc.response.status_code==429 or exc.response.status_code>=500)

    async def complete(self,messages: list[dict],role='planner',tools: list[dict]|None=None) -> dict:
        self.ensure(); last=None
        for index,(url,model,key) in enumerate(self.providers):
            for attempt in range(2):
                await self.reserve(role)
                try:
                    async with self.client(url) as client:
                        payload={'model':model,'messages':messages,'max_tokens':self.s.max_output_tokens,'stream':False}
                        if tools: payload['tools']=tools; payload['tool_choice']='auto'
                        response=await client.post(url+'/chat/completions',headers=self.headers(key),json=payload)
                        response.raise_for_status(); data=response.json()
                    self.usage(data)
                    choice=data.get('choices',[{}])[0].get('message',{})
                    if not choice.get('content') and not choice.get('tool_calls'): raise ModelError('模型没有返回可用回答')
                    return choice
                except (httpx.HTTPError,ValueError,IndexError) as exc:
                    last=exc
                    if not self.transient(exc):
                        raise ModelError(f'模型请求失败（{getattr(getattr(exc,"response",None),"status_code",type(exc).__name__)}）；检查配置。') from exc
                    if attempt==0:
                        await self.emit('retry',{'role':role,'attempt':2,'provider':index+1})
                        await asyncio.sleep(.4+random.random()*.3)
            if index+1<len(self.providers): await self.emit('fallback',{'role':role,'provider':index+2})
        raise ModelError('模型服务暂时不可用，重试和备用服务均未成功') from last

    async def stream(self,messages: list[dict],role='answer') -> str:
        self.ensure()
        for index,(url,model,key) in enumerate(self.providers):
            parts=[]; chars=0; chunks=0; started=time.monotonic(); done=False
            await self.reserve(role)
            try:
                async with self.client(url) as client:
                    async with client.stream('POST',url+'/chat/completions',headers=self.headers(key),json={
                        'model':model,'messages':messages,'stream':True,'max_tokens':self.s.max_output_tokens,
                    }) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith('data:'): continue
                            raw=line[5:].strip()
                            if raw=='[DONE]': done=True; break
                            if not raw: continue
                            data=json.loads(raw)
                            if 'error' in data: raise ModelError('模型流返回错误')
                            self.usage(data)
                            for c in data.get('choices',[]):
                                if c.get('finish_reason')=='length': raise ModelError('模型达到输出长度上限，草稿不完整，请缩小任务')
                                if c.get('finish_reason') in {'stop','tool_calls'}: done=True
                                text=text_content(c.get('delta',{}).get('content',''))
                                if not text: continue
                                chars+=len(text); chunks+=1
                                if chars>60000: raise ModelError('模型输出超过应用文本上限')
                                parts.append(text)
                                await self.emit('answer_delta',{'text':text,'provisional':True})
                                if chunks==1: await self.emit('first_token',{'ms':round((time.monotonic()-started)*1000)})
                if not done: raise ModelError('模型连接提前关闭，未收到正常结束标记')
                if not ''.join(parts).strip(): raise ModelError('模型没有返回文本')
                return ''.join(parts)
            except asyncio.CancelledError: raise
            except (httpx.HTTPError,ValueError,ModelError) as exc:
                await self.emit('answer_reset',{'reason':'当前草稿未完成，已撤下'})
                if index+1<len(self.providers) and (self.transient(exc) or isinstance(exc,ModelError)):
                    await self.emit('fallback',{'role':role,'provider':index+2}); continue
                raise ModelError('模型文本流未完成；未保存为成功答案。请检查服务的 SSE 兼容性或稍后重试。') from exc
        raise ModelError('没有可用模型')

    def embedding_identity(self):
        return fingerprint({'url':self.s.embedding_url or self.s.base_url,'model':self.s.embedding_model})

    async def embed(self,texts: list[str]) -> list[list[float]]:
        if not self.s.allow_source_upload: raise ModelError('请先确认源码发送范围')
        url=self.s.embedding_url or self.s.base_url; key=self.s.embedding_key or self.s.api_key
        if not url or not self.s.embedding_model: raise ModelError('尚未配置 Embedding 服务')
        async with self.client(url) as client:
            r=await client.post(url+'/embeddings',headers=self.headers(key),json={'model':self.s.embedding_model,'input':texts})
            r.raise_for_status(); rows=sorted(r.json().get('data',[]),key=lambda x:x['index'])
        if len(rows)!=len(texts): raise ModelError('Embedding 返回数量不匹配')
        vectors=[]
        for row in rows:
            v=row.get('embedding')
            if not isinstance(v,list) or not 1<=len(v)<=20000 or not all(isinstance(x,(float,int)) and math.isfinite(x) for x in v):
                raise ModelError('Embedding 返回无效向量')
            vectors.append(v)
        return vectors


class LangChainGateway(Gateway):
    """Same contract through real ChatOpenAI; SDK retries disabled to avoid nesting."""
    def make_model(self,provider):
        from langchain_openai import ChatOpenAI
        url,model,key=provider
        return ChatOpenAI(base_url=url,model=model,api_key=key or 'local-not-required',
            max_retries=0,timeout=self.s.request_timeout,max_tokens=self.s.max_output_tokens,
            stream_usage=False)

    def lc_usage(self,message):
        u=getattr(message,'usage_metadata',None)
        if u:
            self.usage({'usage':{'prompt_tokens':u.get('input_tokens',0),'completion_tokens':u.get('output_tokens',0)}})

    @staticmethod
    def transient(exc):
        code=getattr(exc,'status_code',0) or 0
        return Gateway.transient(exc) or code==429 or code>=500 or type(exc).__name__ in {'APIConnectionError','APITimeoutError'}

    async def complete(self,messages,role='planner',tools=None):
        self.ensure()
        for index,provider in enumerate(self.providers):
            for attempt in range(2):
                await self.reserve(role)
                try:
                    model=self.make_model(provider)
                    if tools:model=model.bind_tools(tools)
                    message=await model.ainvoke(messages)
                    self.lc_usage(message)
                    calls=[{'id':c['id'],'type':'function','function':{'name':c['name'],'arguments':json.dumps(c['args'],ensure_ascii=False)}} for c in getattr(message,'tool_calls',[])]
                    text=text_content(message.content)
                    if not text and not calls:raise ModelError('模型没有返回内容')
                    return {'content':text,'tool_calls':calls}
                except asyncio.CancelledError:raise
                except Exception as exc:
                    if not self.transient(exc):raise ModelError('LangChain 模型请求失败：'+type(exc).__name__) from exc
                    if attempt==0:
                        await self.emit('retry',{'role':role,'attempt':2,'provider':index+1})
                        await asyncio.sleep(.4+random.random()*.3)
            if index+1<len(self.providers):await self.emit('fallback',{'role':role,'provider':index+2})
        raise ModelError('模型重试和备用服务均未成功')

    async def stream(self,messages,role='answer'):
        from contextlib import aclosing
        self.ensure()
        for index,provider in enumerate(self.providers):
            await self.reserve(role); parts=[]; size=0; finished=False; begin=time.monotonic()
            try:
                model=self.make_model(provider)
                async with aclosing(model.astream(messages)) as stream:
                    async for message in stream:
                        self.lc_usage(message)
                        reason=(getattr(message,'response_metadata',{}) or {}).get('finish_reason')
                        if reason=='length':raise ModelError('达到输出上限，草稿不完整')
                        if reason in {'stop','tool_calls'}:finished=True
                        text=text_content(message.content)
                        if text:
                            size+=len(text)
                            if size>60000:raise ModelError('超出输出上限')
                            if not parts:await self.emit('first_token',{'ms':round((time.monotonic()-begin)*1000)})
                            parts.append(text);await self.emit('answer_delta',{'text':text,'provisional':True})
                if not finished or not parts:raise ModelError('没有收到正常结束的文本流')
                return ''.join(parts)
            except asyncio.CancelledError:raise
            except Exception as exc:
                await self.emit('answer_reset',{'reason':'未完成的模型草稿已撤下'})
                if index+1<len(self.providers) and (self.transient(exc) or isinstance(exc,ModelError)):
                    await self.emit('fallback',{'role':role,'provider':index+2});continue
                raise ModelError('LangChain 模型流未完成：'+type(exc).__name__) from exc
        raise ModelError('没有可用模型')


def make_gateway(settings,emit=noop):
    from importlib.util import find_spec
    use=settings.model_adapter
    if use=='langchain':
        if find_spec('langchain_openai') is None:raise ModelError('请安装 .[ai] 扩展以使用 LangChain')
        return LangChainGateway(settings,emit)
    if use=='auto' and find_spec('langchain_openai') is not None:
        return LangChainGateway(settings,emit)
    return Gateway(settings,emit)

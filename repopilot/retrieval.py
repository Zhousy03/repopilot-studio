"""Lexical, symbol and optional vector retrieval over immutable snapshots."""
from __future__ import annotations
import ast
import math
import re
import json
from collections import Counter
from pathlib import Path
from .security import redact, sha, fingerprint
from .workspaces import Workspaces

ALIASES={'库存':'stock inventory quantity reserve','订单':'order checkout','重试':'retry duplicate idempotency',
         '重复':'duplicate idempotency','鉴权':'auth authenticate authorize','登录':'login session',
         '测试':'test pytest unittest','超时':'timeout','缓存':'cache','错误':'error exception',
         '数据库':'database query sql','路由':'route router endpoint','配置':'config settings',
         '支付':'payment price total','函数':'def function','异步':'async await','文件':'file path'}


def tokens(text: str,expand=False) -> list[str]:
    if expand: text+=' '+' '.join(v for k,v in ALIASES.items() if k in text)
    text=re.sub(r'([a-z])([A-Z])',r'\1 \2',text)
    out=[]
    for part in re.findall(r'[A-Za-z_][A-Za-z_0-9]*|[\u4e00-\u9fff]+',text):
        if '\u4e00'<=part[0]<='\u9fff': out.extend(part[i:i+2] for i in range(max(len(part)-1,1)))
        else:
            out.append(part.lower()); out.extend(p for p in part.lower().split('_') if p!=part.lower())
    return [x for x in out if len(x)>1 and x not in {'the','and','for','with','that','this','from','return'}]


class Index:
    def __init__(self,workspaces: Workspaces,wid: str,snapshot: str):
        _,self.snapshot,self.root=workspaces.read(wid,snapshot)
        self.files={}; self.chunks=[]; self.symbols=[]
        for item in self.snapshot['files']:
            content=redact(workspaces.source(wid,item['path'],snapshot)['content'])
            lines=content.splitlines(); self.files[item['path']]=item|{'content':content,'line_list':lines}
            if item['path'].endswith('.py'):
                try:
                    tree=ast.parse(content,filename=item['path'])
                    for node in ast.walk(tree):
                        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                            self.symbols.append({'name':node.name,'path':item['path'],'start':node.lineno,
                                                 'end':getattr(node,'end_lineno',node.lineno),'kind':type(node).__name__})
                except (SyntaxError,ValueError,RecursionError): pass
            else:
                for lineno,line in enumerate(lines,1):
                    m=re.search(r'\b(?:function|class|interface|struct|fn|func)\s+([A-Za-z_][A-Za-z_0-9]*)',line)
                    if m: self.symbols.append({'name':m[1],'path':item['path'],'start':lineno,'end':min(lineno+40,len(lines)),'kind':'lexical-symbol'})
            for start in range(0,len(lines),45):
                end=min(start+60,len(lines)); body='\n'.join(lines[start:end])
                if body.strip() and len(body)<=12000:
                    terms=Counter(tokens(item['path']+'\n'+body))
                    self.chunks.append({'chunk_id':sha(f"{item['path']}:{start}:{end}:{item['hash']}")[:24],
                     'path':item['path'],'start_line':start+1,'end_line':end,'content':body,
                     'file_hash':item['hash'],'snapshot_id':snapshot,'terms':terms,'length':sum(terms.values())})
                if end==len(lines): break
        self.df=Counter(t for c in self.chunks for t in c['terms'])
        self.avg=sum(c['length'] for c in self.chunks)/max(len(self.chunks),1)

    @staticmethod
    def public(chunk: dict) -> dict:
        return {k:v for k,v in chunk.items() if k not in {'terms','length'}}

    def search(self,query: str,k=6) -> list[dict]:
        q=set(tokens(query,expand=True)); ranked=[]; n=len(self.chunks)
        for c in self.chunks:
            score=0.0
            for t in q:
                tf=c['terms'][t]
                if tf:
                    idf=math.log(1+(n-self.df[t]+.5)/(self.df[t]+.5))
                    score+=idf*tf*2.5/(tf+1.5*(.25+.75*c['length']/max(1,self.avg)))
            if query.lower() in c['content'].lower() and query.strip(): score+=3
            if score>0: ranked.append((score,c))
        ranked.sort(key=lambda x:(-x[0],x[1]['path'],x[1]['start_line']))
        out=[]
        for score,c in ranked:
            if sum(x['path']==c['path'] for x in out)>=3: continue
            out.append(self.public(c)|{'score':round(score,5)})
            if len(out)>=k: break
        return out

    def read(self,path: str,start=1,end=80) -> dict:
        if path not in self.files: raise ValueError('文件不在此工作区版本中')
        f=self.files[path]; lines=f['line_list']
        if start>len(lines) or end<start: raise ValueError('行号范围不合法')
        end=min(end,start+119,len(lines)); body='\n'.join(lines[start-1:end])
        if len(body)>16000: raise ValueError('源码片段过长，请缩小行范围')
        return {'chunk_id':sha(f'{path}:{start}:{end}:{f["hash"]}')[:24],'path':path,'start_line':start,'end_line':end,
                'content':body,'file_hash':f['hash'],'snapshot_id':self.snapshot['id'],'score':0}

    def find_symbols(self,query: str) -> list[dict]:
        q=query.lower().strip()
        return sorted((s for s in self.symbols if q in s['name'].lower()),key=lambda x:(x['name'],x['path']))[:30]

    async def hybrid(self,query: str,gateway,k=6) -> list[dict]:
        lexical=self.search(query,max(k*3,12))
        path=self.root.parent/(self.snapshot['id']+'.vectors.json')
        if not path.exists(): return lexical[:k]
        data=json.loads(path.read_text('utf-8'))
        if data['config']!=gateway.embedding_identity():
            raise ValueError('Embedding 配置已变更，请重新建立语义索引')
        vector=(await gateway.embed([query]))[0]
        if not vector: return lexical[:k]
        norm=math.sqrt(sum(x*x for x in vector)) or 1
        scored=[]
        byid={c['chunk_id']:c for c in self.chunks}
        for row in data['vectors']:
            v=row['vector']
            if len(v)!=len(vector): raise ValueError('向量维数改变，请重新索引')
            score=sum(a*b for a,b in zip(v,vector))/(norm*(math.sqrt(sum(x*x for x in v)) or 1))
            if row['id'] in byid: scored.append((score,self.public(byid[row['id']])))
        scored.sort(key=lambda x:-x[0]); rrf={}; entries={}
        for ranking in (lexical,[c for _,c in scored[:max(k*3,12)]]):
            for rank,c in enumerate(ranking):
                key=c['chunk_id']; entries[key]=c; rrf[key]=rrf.get(key,0)+1/(60+rank+1)
        return [entries[key]|{'score':round(rrf[key],6),'retrieval':'hybrid-rrf'} for key in sorted(rrf,key=lambda x:-rrf[x])[:k]]

    async def build_vectors(self,gateway,emit):
        if len(self.chunks)>1200: raise ValueError('此版本最多索引 1200 个片段，请拆分更大的项目')
        rows=[]
        for start in range(0,len(self.chunks),24):
            batch=self.chunks[start:start+24]
            vectors=await gateway.embed([c['path']+'\n'+c['content'] for c in batch])
            rows.extend({'id':c['chunk_id'],'vector':v} for c,v in zip(batch,vectors))
            await emit('progress',{'current':len(rows),'total':len(self.chunks),'stage':'embedding'})
        path=self.root.parent/(self.snapshot['id']+'.vectors.json'); temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps({'config':gateway.embedding_identity(),'vectors':rows}),encoding='utf-8')
        temp.replace(path)
        return {'chunks':len(rows),'snapshot_id':self.snapshot['id']}

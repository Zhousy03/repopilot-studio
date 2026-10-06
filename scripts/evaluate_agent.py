"""Run user-labelled read-only cases against the local service. Real AI mode costs tokens.
JSONL keys: question; optionally required_tools, forbidden_tools, max_tool_calls,
required_substrings. Each case gets a fresh session. No auto-approval or publishing.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx


def evaluate(run:dict, events:list[dict], case:dict) -> dict:
    calls=[e['data']['action'] for e in events if e['event']=='tool_finished']
    names=[c['tool'] for c in calls]
    out={'status':run['status'],'model_requests':run.get('metrics',{}).get('model_requests'),
         'tool_calls':len(calls),'actual_tools':names,'metrics':run.get('metrics',{})}
    if 'required_tools' in case:out['required_tools_present']=set(case['required_tools']).issubset(names)
    if 'forbidden_tools' in case:out['forbidden_tool_absent']=not bool(set(case['forbidden_tools']) & set(names))
    if 'max_tool_calls' in case:out['tool_budget_passed']=len(calls)<=int(case['max_tool_calls'])
    if case.get('required_substrings'):
        out['substring_smoke_passed']=all(x.casefold() in run.get('answer','').casefold() for x in case['required_substrings'])
    return out


async def main(args):
    u=urlsplit(args.base_url)
    if u.scheme!='http' or u.hostname not in {'127.0.0.1','localhost','::1'}:raise ValueError('Evaluator only connects to your local service')
    cases=[json.loads(line) for line in args.dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not cases:raise ValueError('Dataset is empty')
    rows=[]
    async with httpx.AsyncClient(base_url=args.base_url,timeout=20,trust_env=False,headers={'X-RepoPilot-Client':'cli'}) as c:
        mode=(await c.get('/api/health')).json().get('mode')
        if mode=='openai' and not args.allow_model_cost:raise ValueError('AI mode requires --allow-model-cost')
        for repetition in range(args.repeat):
            for number,case in enumerate(cases):
                if not isinstance(case.get('question'),str):raise ValueError('Every case requires a question')
                started=time.perf_counter()
                response=await c.post('/api/runs',json={'workspace_id':args.workspace,'question':case['question'],'intent':'investigate'})
                response.raise_for_status();rid=response.json()['id']
                deadline=time.monotonic()+args.timeout
                while True:
                    response=await c.get('/api/runs/'+rid);response.raise_for_status();run=response.json()
                    if run['status'] in {'done','error','cancelled','interrupted','awaiting_approval'}:break
                    if time.monotonic()>deadline:
                        await c.post('/api/runs/'+rid+'/cancel');raise TimeoutError('Evaluation case exceeded client budget')
                    await asyncio.sleep(.25)
                response=await c.get('/api/runs/'+rid+'/export?format=json');response.raise_for_status();export=response.json()
                result=evaluate(run,export['events'],case)
                result.update(case=number,repetition=repetition,run_id=rid,elapsed_seconds=round(time.perf_counter()-started,3))
                rows.append(result);print(json.dumps(result,ensure_ascii=False))
    summary={'cases_executed':len(rows),'completed':sum(x['status']=='done' for x in rows),
             'mean_latency_seconds':sum(x['elapsed_seconds'] for x in rows)/len(rows)}
    for key in ('required_tools_present','forbidden_tool_absent','tool_budget_passed','substring_smoke_passed'):
        values=[r[key] for r in rows if key in r]
        if values:summary[key+'_rate']=sum(values)/len(values)
    output={'summary':summary,'rows':rows,'notice':'子串检测不等于答案正确率；失败案例没有从分母中隐藏。未提供金标的项目不评分。'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url',default='http://127.0.0.1:8000');p.add_argument('--workspace',required=True)
    p.add_argument('--dataset',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--repeat',type=int,default=1);p.add_argument('--timeout',type=float,default=650)
    p.add_argument('--allow-model-cost',action='store_true');args=p.parse_args()
    if not 1<=args.repeat<=10:p.error('repeat must be 1..10')
    asyncio.run(main(args))

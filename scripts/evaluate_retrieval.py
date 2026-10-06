"""Evaluate YOUR JSONL labels on an imported workspace; no bundled fake benchmark.
Required JSONL keys: question (str), relevant_paths (nonempty list[str]).
No LLM calls. Report is file-level Recall@K and Hit@K; not answer correctness.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from repopilot.config import Configuration
from repopilot.db import Store
from repopilot.workspaces import Workspaces
from repopilot.retrieval import Index


def score(paths: list[str], gold: list[str], k: int) -> dict:
    expected=set(gold)
    if not expected: raise ValueError('relevant_paths cannot be empty')
    # K denotes chunk retrieval depth; deduplicate paths before file-level scoring.
    found=set(paths[:k]) & expected
    return {'file_recall_at_k':len(found)/len(expected),'file_hit_at_k':float(bool(found))}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',required=True);p.add_argument('--snapshot',default='')
    p.add_argument('--dataset',type=Path,required=True);p.add_argument('--k',type=int,default=5)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if not 1<=args.k<=20:p.error('k must be between 1 and 20')
    c=Configuration();db=Store(c.data_dir);ws=Workspaces(c,db);w,s,_=ws.read(args.workspace,args.snapshot or None)
    index=Index(ws,w['id'],s['id']);rows=[]
    for n,line in enumerate(args.dataset.read_text(encoding='utf-8').splitlines(),1):
        if not line.strip():continue
        case=json.loads(line)
        if not isinstance(case.get('question'),str) or not isinstance(case.get('relevant_paths'),list):
            raise ValueError(f'line {n}: question and relevant_paths are required')
        unknown=set(case['relevant_paths'])-set(index.files)
        if unknown: raise ValueError(f'line {n}: gold paths missing from selected snapshot: {sorted(unknown)}')
        docs=index.search(case['question'],k=args.k);paths=[d['path'] for d in docs]
        rows.append({'line':n,'question':case['question'],'retrieved_paths':paths,
                     **score(paths,case['relevant_paths'],args.k)})
    if not rows:raise ValueError('Dataset is empty')
    output={'workspace_id':w['id'],'snapshot_id':s['id'],'retrieval':'lexical','k':args.k,'cases':len(rows),
      'file_recall_at_k':sum(r['file_recall_at_k'] for r in rows)/len(rows),
      'file_hit_at_k':sum(r['file_hit_at_k'] for r in rows)/len(rows),'details':rows,
      'limitations':'只评给定版本与自备标注，不是跨仓库准确率，也不衡量答案正确性或语义索引效果。'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k!='details'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()

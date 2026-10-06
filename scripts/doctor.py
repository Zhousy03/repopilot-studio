"""Inspect local capabilities without calling a model, GitHub or external registry."""
from __future__ import annotations
import argparse
import asyncio
import importlib.metadata as metadata
import importlib.util
import json
import platform
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repopilot.config import Configuration
from repopilot.db import Store
from repopilot.sandbox import Sandbox


def installed(name: str) -> str | None:
    try: return metadata.version(name)
    except metadata.PackageNotFoundError: return None


async def report() -> dict:
    c=Configuration(); db=Store(c.data_dir)
    return {
        'python':platform.python_version(), 'platform':platform.system(),
        'minimum_python_satisfied':sys.version_info >= (3,11), 'data_dir':str(c.data_dir),
        'packages':{x:installed(x) for x in ('fastapi','httpx','langgraph','langchain-openai',
                    'langgraph-checkpoint-sqlite','langgraph-checkpoint-postgres','pytest')},
        'mode':c.value.mode,'model_adapter':c.value.model_adapter,
        'model_configured':bool(c.value.base_url and c.value.model),
        'source_upload_consent':c.value.allow_source_upload,
        'github_token_configured':bool(c.value.github_token),
        'embedding_configured':bool(c.value.embedding_model),
        'postgres_checkpoint_configured':bool(c.checkpoint_url),
        'workspaces':len(db.list('workspace')), 'docker':await Sandbox(c.value).availability(),
        'notice':'此检查不访问模型或 GitHub。配置存在不代表远端授权、模型协议或镜像运行已验证。',
    }


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.parse_args()
    print(json.dumps(asyncio.run(report()),ensure_ascii=False,indent=2))

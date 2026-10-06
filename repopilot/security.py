from __future__ import annotations
import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

EXTENSIONS={'.py','.pyi','.md','.mdx','.rst','.txt','.js','.jsx','.ts','.tsx','.mjs','.cjs',
'.json','.jsonc','.toml','.yaml','.yml','.go','.mod','.sum','.rs','.java','.sql','.css','.scss',
'.html','.vue','.rb','.php','.c','.h','.hpp','.cpp','.sh','.ps1','.bat','.xml','.gradle','.ini','.cfg','.lock','.svelte'}
SKIP={'.git','.hg','.svn','.venv','venv','node_modules','__pycache__','.repopilot','.data',
      '.next','dist','build','coverage','.idea','.vscode','.pytest_cache','.mypy_cache','.ruff_cache','vendor'}
SECRET=re.compile(r'(^\.env($|\.)|\.pem$|\.key$|\.p12$|\.pfx$|^id_rsa|^id_ed25519|credentials|^secrets?([._-]|$))',re.I)
TOKEN=re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b')
LITERAL=re.compile(r'(?im)((?:api[_-]?key|password|access[_-]?token|client[_-]?secret)\s*[:=]\s*)([\'"])([^\'"\n]{4,})([\'"])')


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode('utf-8') if isinstance(value,str) else value).hexdigest()


def fingerprint(data) -> str:
    return sha(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False))


def path_ok(raw: str) -> str:
    if not raw or len(raw)>500 or '\\' in raw or any(c in raw for c in '<>"|?*') or ':' in raw or any(ord(c)<32 or ord(c)==127 for c in raw):
        raise ValueError('不安全的相对路径')
    path=PurePosixPath(raw)
    if path.is_absolute() or any(x in {'','..','.'} for x in raw.split('/')):
        raise ValueError('路径必须在工作区内，不能含 . 或 ..')
    if any(x in SKIP for x in path.parts):
        raise ValueError('路径位于忽略目录')
    if SECRET.search(path.name): raise ValueError('不允许读取或修改敏感文件')
    if path.suffix.lower() not in EXTENSIONS and path.name not in {'Dockerfile','Makefile','.gitignore','.dockerignore','.editorconfig','.gitattributes'} and path.name.upper() not in {'README','LICENSE','NOTICE','COPYING','AUTHORS','CHANGELOG'}:
        raise ValueError('文件类型不在允许的文本类型中')
    # Windows device names are unsafe even when imported on Linux.
    if any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',x) or x.endswith((' ','.')) for x in path.parts):
        raise ValueError('不兼容的文件名')
    return path.as_posix()


def redact(text: str) -> str:
    text=TOKEN.sub('[REDACTED_TOKEN]',text)
    return LITERAL.sub(lambda m:m[1]+m[2]+'[REDACTED_LITERAL]'+m[4],text)


def github_repo(url: str) -> tuple[str,str]:
    u=urlsplit(url.strip())
    if u.scheme!='https' or u.netloc!='github.com' or u.query or u.fragment or u.username:
        raise ValueError('请填写 https://github.com/OWNER/REPO；分支单独填写')
    parts=u.path.strip('/').split('/')
    if len(parts)!=2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',p) for p in parts):
        raise ValueError('GitHub 仓库 URL 格式错误')
    owner,repo=parts
    if repo.endswith('.git'): repo=repo[:-4]
    if repo in {'','.','..'} or owner in {'.','..'}: raise ValueError('仓库名称无效')
    return owner,repo

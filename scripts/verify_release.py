"""Verify or package the public source tree, excluding local application data.

Usage: python scripts/verify_release.py [--update] [--archive PATH]
Hashes normalize text line endings so a Windows Git checkout also verifies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {'repopilot', 'sandbox', 'scripts', 'tests', 'docs', 'evals', '.github'}
ROOT_FILES = {'.gitignore', '.dockerignore', '.gitattributes', 'pyproject.toml',
              'MANIFEST.in', 'README.md', 'LICENSE', 'SECURITY.md', 'CONTRIBUTING.md',
              'CHANGELOG.md', 'Dockerfile', 'compose.yaml', 'start.sh', 'start.bat'}
IGNORED = {'.git', '.venv', '.repopilot', '.data', '.ui-results', '__pycache__',
           '.pytest_cache', '.ruff_cache', '.mypy_cache', 'node_modules', 'build', 'dist'}
TEXT_SUFFIXES = {'.py', '.js', '.css', '.html', '.md', '.json', '.yaml', '.yml', '.toml', '.sh', '.bat', '.txt', '.in'}
TOKEN = re.compile(rb'\b(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})\b')


def collect_source(root: Path) -> dict[str, bytes]:
    root = root.resolve()
    source = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if any(part in IGNORED or part.endswith('.egg-info') for part in rel.parts):
            continue
        if rel.parts[:2] == ('docs', 'validation'):
            continue
        if rel.as_posix() == 'RELEASE-MANIFEST.json':
            continue
        if path.is_dir():
            continue
        if len(rel.parts) == 1 and path.name not in ROOT_FILES:
            continue
        if len(rel.parts) > 1 and rel.parts[0] not in SOURCE_DIRS:
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f'发布源文件不能是符号链接：{rel.as_posix()}')
        if path.suffix.lower() not in TEXT_SUFFIXES | {'.png'} and not path.name.startswith('Dockerfile') and path.name not in ROOT_FILES:
            continue
        raw = path.read_bytes()
        if path.suffix.lower() != '.png':
            if TOKEN.search(raw):
                raise ValueError(f'检测到疑似凭据，请检查文件：{rel.as_posix()}')
            raw = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')
        source[rel.as_posix()] = raw
    return source


def manifest_for(source: dict[str, bytes], version: str) -> dict:
    return {'project': 'RepoPilot Studio', 'version': version,
            'hash_format': 'SHA-256; UTF-8 text normalized to LF; binary unchanged',
            'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in source.items()}}


def verify(source: dict[str, bytes], manifest: dict) -> list[str]:
    expected = manifest['files']
    errors = [f'{name}: missing' for name in expected.keys() - source.keys()]
    errors += [f'{name}: unlisted' for name in source.keys() - expected.keys()]
    errors += [f'{name}: modified' for name in expected.keys() & source.keys()
               if hashlib.sha256(source[name]).hexdigest() != expected[name]]
    return sorted(errors)


def write_archive(destination: Path, source: dict[str, bytes], manifest_raw: bytes):
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, raw in sorted((source | {'RELEASE-MANIFEST.json': manifest_raw}).items()):
            info = zipfile.ZipInfo('repopilot-studio/' + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            z.writestr(info, raw)
    with zipfile.ZipFile(destination) as z:
        if z.testzip():
            raise ValueError('发布 ZIP 校验失败')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update', action='store_true', help='Rebuild the manifest after reviewing source changes')
    parser.add_argument('--archive', type=Path, help='Write a clean source ZIP after successful verification')
    args = parser.parse_args()
    source = collect_source(ROOT)
    path = ROOT / 'RELEASE-MANIFEST.json'
    if args.update:
        version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
        path.write_text(json.dumps(manifest_for(source, version), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not path.is_file():
        parser.error('缺少发布清单；审查源码后运行 --update')
    errors = verify(source, json.loads(path.read_text(encoding='utf-8')))
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'已校验 {len(source)} 个源码文件，无疑似凭据。')
    if args.archive:
        write_archive(args.archive, source, path.read_bytes())
        print(f'发布包：{args.archive.resolve()}')


if __name__ == '__main__':
    main()

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


def release_module():
    path = Path(__file__).parents[1] / 'scripts' / 'verify_release.py'
    spec = importlib.util.spec_from_file_location('release_packaging', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_excludes_runtime_and_normalizes_text(tmp_path):
    release = release_module()
    files = {'README.md': b'hello\r\n', 'repopilot/core.py': b'x=1\r\n',
             '.repopilot/settings.json': b'private', '.env': b'private',
             '.venv/private.py': b'private', 'repopilot/__pycache__/core.pyc': b'private',
             'repopilot_studio.egg-info/PKG-INFO': b'private', 'docs/validation/old.txt': b'private'}
    for name, raw in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    source = release.collect_source(tmp_path)
    assert set(source) == {'README.md', 'repopilot/core.py'}
    assert source['README.md'] == b'hello\n'
    manifest = release.manifest_for(source, '1.1.0')
    assert release.verify(source, manifest) == []
    assert release.verify(source | {'repopilot/new.py': b'x=2\n'}, manifest) == ['repopilot/new.py: unlisted']
    archive = tmp_path / 'source.zip'
    release.write_archive(archive, source, json.dumps(manifest).encode())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == {'repopilot-studio/README.md', 'repopilot-studio/repopilot/core.py',
                                    'repopilot-studio/RELEASE-MANIFEST.json'}


def test_release_rejects_credentials_without_echo(tmp_path):
    release = release_module()
    secret = 'sk-' + 'a' * 32
    (tmp_path / 'README.md').write_text(secret)
    with pytest.raises(ValueError) as exc:
        release.collect_source(tmp_path)
    assert 'README.md' in str(exc.value) and secret not in str(exc.value)

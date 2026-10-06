"""Fixtures are generated in pytest temporary directories, never shipped as workspaces."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from repopilot.api import create_app
from repopilot.config import Configuration
from repopilot.db import Store
from repopilot.workspaces import Workspaces
from repopilot.changes import Changes

@pytest.fixture
def components(tmp_path):
    config=Configuration(tmp_path/'data'); db=Store(config.data_dir); ws=Workspaces(config,db)
    return config,db,ws,Changes(ws,db)

@pytest.fixture
def workspace(components):
    c,d,w,ch=components
    return w.ingest('test-fixture',{
        'calc.py':b'def add(a, b):\n    """Add two numbers."""\n    return a - b\n',
        'tests/test_calc.py':b'from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n',
        'pyproject.toml':b'[project]\nname="fixture"\nversion="0.0.0"\n',
    })

@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path/'appdata',force_portable=True)
    with TestClient(app,headers={'X-RepoPilot-Client':'test'}) as client: yield client

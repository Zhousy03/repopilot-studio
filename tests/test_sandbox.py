import shutil
import pytest
from repopilot.config import UserSettings
from repopilot.sandbox import Sandbox


def test_fixed_sandbox_command(tmp_path):
    box=Sandbox(UserSettings())
    cmd=box.command(tmp_path,'pytest','unit-name')
    assert '--network=none' in cmd
    assert '--read-only' in cmd and '--cap-drop=ALL' in cmd
    assert '--pull=never' in cmd and '--user=65534:65534' in cmd
    assert 'docker.sock' not in ' '.join(cmd)
    assert cmd[-1]=='pytest'
    with pytest.raises(ValueError):box.command(tmp_path,'pytest; rm -rf /','unit-name')

async def test_tests_disabled_fails_closed(tmp_path):
    box=Sandbox(UserSettings(tests_enabled=False))
    with pytest.raises(ValueError,match='开启'):await box.run(tmp_path,'pytest')

async def test_missing_docker_never_host_fallback(tmp_path,monkeypatch):
    box=Sandbox(UserSettings(tests_enabled=True));monkeypatch.setattr(shutil,'which',lambda x:None)
    with pytest.raises(ValueError,match='Docker'):await box.run(tmp_path,'pytest')

async def test_real_docker_if_prepared(tmp_path):
    box=Sandbox(UserSettings(tests_enabled=True))
    capabilities=await box.availability()
    if not capabilities['docker'] or not capabilities['image']:pytest.skip('Docker daemon and prepared test image are required')
    (tmp_path/'test_real.py').write_text('def test_addition():\n    assert 2 + 3 == 5\n')
    tmp_path.chmod(0o755)
    result=await box.run(tmp_path,'pytest')
    assert result['executed_code'] and result['status']=='passed'

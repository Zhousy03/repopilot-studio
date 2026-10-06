import asyncio

from repopilot.models import Ask
from repopilot.service import Engine


def test_check_model_uses_unsaved_form_without_persisting(client, monkeypatch):
    seen = []
    class Gateway:
        def __init__(self, settings):
            seen.append(settings)
        async def complete(self, messages, role):
            assert messages == [{'role': 'user', 'content': 'Reply with OK.'}]
            return {'content': 'OK'}
    monkeypatch.setattr('repopilot.ai.make_gateway', Gateway)
    client.patch('/api/settings', json={'model': 'saved', 'api_key': 'saved-test-key'})
    response = client.post('/api/settings/check-model', json={
        'base_url': 'https://model.invalid/v1', 'model': 'unsaved', 'api_key': 'unsaved-test-key',
        'backup_url': 'https://backup.invalid/v1', 'backup_model': 'backup'})
    assert response.status_code == 200, response.text
    assert response.json()['model'] == 'unsaved'
    assert 'unsaved-test-key' not in response.text
    assert seen[0].api_key == 'unsaved-test-key'
    assert seen[0].allow_source_upload and not seen[0].backup_url
    saved = client.app.state.config.value
    assert saved.mode == 'offline' and saved.model == 'saved' and saved.api_key == 'saved-test-key'


def test_check_model_reuses_saved_key_when_omitted(client, monkeypatch):
    seen = []
    class Gateway:
        def __init__(self, settings): seen.append(settings)
        async def complete(self, messages, role): return {'content': 'OK'}
    monkeypatch.setattr('repopilot.ai.make_gateway', Gateway)
    client.patch('/api/settings', json={'base_url': 'https://model.invalid/v1', 'model': 'saved', 'api_key': 'saved-key'})
    assert client.post('/api/settings/check-model', json={'model': 'new'}).status_code == 200
    assert seen[0].api_key == 'saved-key'
    assert client.post('/api/settings/check-model').status_code == 200
    assert seen[-1].model == 'saved'


def test_invalid_preview_does_not_echo_key_or_change_settings(client):
    response = client.post('/api/settings/check-model', json={'base_url': 'invalid', 'api_key': 'private-test-key'})
    assert response.status_code == 400
    assert 'private-test-key' not in response.text
    assert not client.app.state.config.value.api_key


def test_offline_review_and_change_fail_with_actionable_error(client):
    w = client.post('/api/workspaces/text', json={'name': 'test', 'files': [{'path': 'a.py', 'content': 'x=1'}]}).json()
    for intent in ('review', 'change'):
        r = client.post('/api/runs', json={'workspace_id': w['id'], 'question': 'x', 'intent': intent})
        assert r.status_code == 400 and '启用模型' in r.json()['detail']
    assert client.get('/api/runs').json()['total'] == 0


async def test_queued_run_keeps_settings_from_submission(components, workspace):
    config, db, ws, _ = components
    engine = Engine(config, db, ws)
    engine.semaphore = asyncio.Semaphore(0)
    run = engine.launch(Ask(workspace_id=workspace['id'], question='add'))
    task = engine.tasks[run['id']]
    # Changing settings while queued must not change this run into a cloud request.
    config.update({'mode': 'openai', 'model': 'new', 'base_url': 'https://model.invalid/v1'})
    engine.semaphore.release()
    await task
    saved = db.get('run', run['id'])
    assert saved['status'] == 'done', saved.get('error')
    assert saved['state']['citation_check'] == 'offline_extract'
    assert 'api_key' not in saved
    await engine.close()


async def test_cancel_queued_run_finishes_record_and_releases_capacity(components, workspace):
    config, db, ws, _ = components
    engine = Engine(config, db, ws)
    engine.semaphore = asyncio.Semaphore(0)
    run = engine.launch(Ask(workspace_id=workspace['id'], question='add'))
    await engine.cancel(run['id'])
    assert db.get('run', run['id'])['status'] == 'cancelled'
    assert run['id'] not in engine.tasks
    assert any(e['event'] == 'cancelled' for e in db.all_events(run['id']))
    await engine.close()

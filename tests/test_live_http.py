"""Loopback TCP integration: controlled upstream, not a real cloud model."""
import asyncio
import json
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from repopilot.api import create_app


@contextmanager
def serve(app):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError('local test server did not start')
        time.sleep(.01)
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(8)
        sock.close()
        assert not thread.is_alive(), 'test server failed to stop'


def model_stub():
    app = FastAPI()
    gate = threading.Event()
    opened = threading.Event()
    closed = threading.Event()

    @app.post('/v1/chat/completions')
    async def complete(request: Request):
        body = await request.json()
        if not body.get('stream'):
            return {'choices': [{'message': {'content': json.dumps({
                'actions': [], 'sufficient': True, 'reason': 'test fixture evidence is sufficient'
            })}}]}

        async def tokens():
            try:
                opened.set()
                yield 'data: ' + json.dumps({'choices': [{'delta': {'content': 'First live chunk [E1]. '}}]}) + '\n\n'
                while not gate.is_set():
                    await asyncio.sleep(.01)
                yield 'data: ' + json.dumps({'choices': [{'delta': {'content': 'Second live chunk.'}}]}) + '\n\n'
                yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            finally:
                closed.set()
        return StreamingResponse(tokens(), media_type='text/event-stream')
    return app, gate, opened, closed


async def start_run(c, model_url):
    r = await c.patch('/api/settings', json={
        'mode': 'openai', 'base_url': model_url + '/v1', 'model': 'controlled-upstream',
        'model_adapter': 'http', 'allow_source_upload': True, 'multi_agent': False,
    })
    assert r.status_code == 200, r.text
    w = (await c.post('/api/workspaces/text', json={
        'name': 'temporary-test', 'files': [{'path': 'calc.py', 'content': 'def add(a, b):\n    return a + b\n'}]
    })).json()
    r = await c.post('/api/runs', json={'workspace_id': w['id'], 'question': 'add'})
    assert r.status_code == 202, r.text
    return r.json()['id']


async def wait_status(c, rid, expected):
    for _ in range(150):
        r = (await c.get('/api/runs/' + rid)).json()
        if r['status'] in expected:
            return r
        await asyncio.sleep(.02)
    raise AssertionError(r)


async def first_delta(c, rid):
    last_id = 0
    async with c.stream('GET', '/api/events/' + rid) as response:
        assert response.status_code == 200
        kind = None
        async for line in response.aiter_lines():
            if line.startswith('id: '): last_id = int(line[4:])
            if line.startswith('event: '): kind = line[7:]
            if line.startswith('data: ') and kind == 'answer_delta':
                return json.loads(line[6:]), last_id
    raise AssertionError('stream closed without a live delta')


async def test_tcp_stream_before_completion_and_reconnect(tmp_path):
    upstream, gate, opened, closed = model_stub()
    with serve(upstream) as model_url, serve(create_app(tmp_path/'data', force_portable=True)) as app_url:
        async with httpx.AsyncClient(base_url=app_url, trust_env=False, timeout=5,
                    headers={'X-RepoPilot-Client': 'test'}) as c:
            try:
                rid = await start_run(c, model_url)
                data, last_id = await first_delta(c, rid)
                assert data['text'] == 'First live chunk [E1]. '
                assert not gate.is_set() and opened.is_set() and not closed.is_set()
                # Closing a viewing connection does not cancel the durable run.
                assert (await c.get('/api/runs/' + rid)).json()['status'] == 'running'
                gate.set()
                final = await wait_status(c, rid, {'done', 'error'})
                assert final['status'] == 'done', final
                assert final['answer'].endswith('Second live chunk.')
                replay = await c.get('/api/events/' + rid, headers={'Last-Event-ID': str(last_id)})
                assert 'Second live chunk.' in replay.text and 'event: done' in replay.text
                assert 'First live chunk [E1]. ' not in [line[6:] for line in replay.text.splitlines() if line.startswith('data: ')][0]
                assert closed.is_set()
            finally:
                gate.set()


async def test_tcp_explicit_stop_closes_upstream_and_discards_draft(tmp_path):
    upstream, gate, opened, closed = model_stub()
    with serve(upstream) as model_url, serve(create_app(tmp_path/'data', force_portable=True)) as app_url:
        async with httpx.AsyncClient(base_url=app_url, trust_env=False, timeout=5,
                    headers={'X-RepoPilot-Client': 'test'}) as c:
            try:
                rid = await start_run(c, model_url)
                await first_delta(c, rid)
                response = await c.post('/api/runs/' + rid + '/cancel')
                assert response.status_code == 200, response.text
                final = await wait_status(c, rid, {'cancelled', 'error'})
                assert final['status'] == 'cancelled'
                assert not final.get('answer')
                for _ in range(100):
                    if closed.is_set(): break
                    await asyncio.sleep(.01)
                assert closed.is_set() and not gate.is_set()
            finally:
                gate.set()

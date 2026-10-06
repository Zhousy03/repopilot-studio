"""Browser checks over a real local HTTP server with temporary application data.

Requires the ui-test extra and a Chromium browser. Model connectivity uses a
controlled local endpoint; this script never requires real provider credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, Request
from playwright.async_api import async_playwright, expect
from repopilot.api import create_app


def local_browser():
    for path in (shutil.which('chromium'), shutil.which('google-chrome'),
                 str(Path(os.getenv('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe'),
                 str(Path(os.getenv('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe')):
        if path and Path(path).is_file():
            return path
    return None


@contextmanager
def serve(app):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError('Local HTTP server failed to start')
        time.sleep(.02)
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()
        if thread.is_alive():
            raise RuntimeError('Local HTTP server failed to stop')


def archive():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('temporary-project/calc.py', 'def add(a, b):\n    """Compute addition."""\n    return a - b\n')
        z.writestr('temporary-project/tests/test_calc.py', 'from calc import add\n\ndef test_add():\n    assert add(2,3) == 5\n')
    return {'name': 'temporary-project.zip', 'mimeType': 'application/zip', 'buffer': buf.getvalue()}


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    errors = []
    def check(name, ok=True):
        assert ok, name
        results.append(name)
    with tempfile.TemporaryDirectory(prefix='studio-ui-') as temp:
        app = create_app(Path(temp) / 'data', force_portable=True)
        upstream = FastAPI()
        calls = []
        @upstream.post('/v1/chat/completions')
        async def model(request: Request):
            body = await request.json()
            calls.append(body)
            return {'choices': [{'message': {'content': 'OK'}}]}
        with serve(app) as url, serve(upstream) as model_url:
            async with async_playwright() as p:
                browser = await p.chromium.launch(executable_path=args.browser, headless=True)
                page = await browser.new_page(viewport={'width': 1512, 'height': 1050}, device_scale_factor=1)
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('dialog', lambda d: asyncio.create_task(d.accept()))
                await page.goto(url, wait_until='networkidle')
                check('empty installation', await page.locator('#empty-state').is_visible())
                check('import required before submitting', not await page.locator('#send-question').is_enabled())
                check('workspace actions disabled without workspace', not await page.locator('#export-workspace').is_enabled())
                check('offline AI intents disabled', await page.locator('#intent option[value=change]').is_disabled())
                check('desktop has no horizontal overflow', await page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                await page.screenshot(path=str(args.output / 'empty.png'), full_page=True)
                await page.locator('#top-settings').click()
                await page.wait_for_selector('#settings-dialog[open]')
                check('settings dialog opens')
                await page.locator('input[name=model]').fill('unsaved-ui-model')
                await page.locator('input[name=base_url]').fill(model_url + '/v1')
                await page.locator('#advanced-model summary').click()
                await page.locator('select[name=model_adapter]').select_option('http')
                await page.locator('#check-model').click()
                await expect(page.locator('#check-model')).to_be_enabled()
                status = await page.locator('#settings-status').inner_text()
                check('connectivity check succeeds: ' + status, '连接成功' in status)
                check('connection check uses current unsaved form', calls[-1]['model'] == 'unsaved-ui-model')
                saved = await (await page.request.get(url + '/api/settings')).json()
                check('connection check does not save settings', not saved['model'] and saved['mode'] == 'offline')
                await page.locator('#settings-dialog .close-dialog').click()
                await page.locator('[data-import=zip]').first.click()
                await page.locator('#zip-input').set_input_files(archive())
                await page.wait_for_selector('#import-dialog', state='hidden')
                await page.wait_for_selector('.file-row')
                check('ZIP upload over real HTTP creates workspace')
                check('ready state has useful starters', await page.locator('#task-starters').is_visible())
                await page.screenshot(path=str(args.output / 'workspace.png'), full_page=True)
                await page.locator('.file-row').filter(has_text='calc.py').first.click()
                await page.wait_for_selector('#source-panel:not(.hidden)')
                check('source shows original bytes', 'return a - b' in await page.locator('#source-code').inner_text())
                await page.locator('#edit-source').click()
                await page.locator('#editor-text').fill('def add(a, b):\n    """Compute addition."""\n    return a + b\n')
                await page.locator('#editor-title').fill('修复加法运算')
                await page.locator('#editor-form button[type=submit]').click()
                await page.wait_for_selector('#proposal-detail:not(.hidden)')
                check('edit creates pending diff', 'return a + b' in await page.locator('#proposal-detail').inner_text())
                approve = page.locator('#proposal-detail button').filter(has_text='批准并应用')
                check('approval requires consent', not await approve.is_enabled())
                await page.locator('#proposal-detail input[type=checkbox]').check()
                await approve.click()
                await page.get_by_role('button', name='测试这个版本', exact=True).wait_for()
                check('approved edit creates new version')
                w = (await (await page.request.get(url + '/api/workspaces')).json())[0]
                content = await (await page.request.get(url + f'/api/workspaces/{w["id"]}/file?path=calc.py')).json()
                check('applied bytes verified independently', 'a + b' in content['content'])
                await page.screenshot(path=str(args.output / 'changes.png'), full_page=True)
                await page.get_by_role('button', name='测试这个版本', exact=True).click()
                check('static check hides Docker consent', not await page.locator('#test-consent-label').is_visible())
                await page.locator('#start-test').click()
                await page.locator('#test-summary').filter(has_text='语法检查 · 通过').wait_for()
                check('test result has readable summary', '未运行测试代码' in await page.locator('#test-summary').inner_text())
                await page.locator('#test-profile').select_option('pytest')
                check('Docker consent appears only for execution', await page.locator('#test-consent-label').is_visible())
                check('unprepared Docker cannot be submitted', not await page.locator('#start-test').is_enabled())
                await page.locator('#test-profile').select_option('static')
                await page.screenshot(path=str(args.output / 'tests.png'), full_page=True)
                await page.locator('[data-tab=investigate]').click()
                await page.locator('#question-input').fill('add 函数如何计算？')
                await page.locator('#send-question').click()
                await page.wait_for_selector('#answer-actions:not(.hidden)', timeout=15000)
                check('investigation and SSE finish over real HTTP', await page.locator('#answer h2').count() > 0 and await page.locator('#evidence-list button').count() > 0)
                check('workflow timeline reflects actual stages', await page.locator('#timeline').locator(':scope > *').count() >= 3)
                await page.screenshot(path=str(args.output / 'answer.png'), full_page=True)
                await page.locator('#top-history').click()
                await page.locator('.history-item').first.wait_for()
                check('history lists completed task', await page.locator('.history-item').count() == 1)
                await page.locator('#history-query').fill('does-not-exist-anywhere')
                await page.locator('#history-results .empty-hint').wait_for()
                check('history search empty state', await page.locator('.history-item').count() == 0)
                await page.locator('#history-dialog .close-dialog').click()
                for width in (768, 390, 320):
                    await page.set_viewport_size({'width': width, 'height': 844})
                    check(f'no overflow at {width}px', await page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                await page.set_viewport_size({'width': 390, 'height': 844})
                check('mobile import and settings reachable', await page.locator('#mobile-import').is_visible() and await page.locator('#top-settings').is_visible())
                await page.screenshot(path=str(args.output / 'mobile.png'), full_page=True)
                await page.locator('#mobile-files').click()
                check('mobile file drawer opens', await page.locator('#workspace-sidebar').is_visible())
                check('mobile workspace switching available', await page.locator('#workspace-picker').is_visible())
                await page.locator('.file-row').filter(has_text='calc.py').first.click()
                await page.locator('#source-panel').wait_for(state='visible')
                check('mobile file opens source panel', await page.locator('#source-panel').is_visible())
                await page.screenshot(path=str(args.output / 'mobile-source.png'), full_page=True)
                await page.keyboard.press('Escape')
                check('Escape closes mobile panel', not await page.locator('.evidence-panel').is_visible())
                await page.locator('#view-evidence').click()
                check('mobile evidence opens from answer', await page.locator('#evidence-list').is_visible())
                await page.keyboard.press('Escape')
                await page.locator('#mobile-files').click()
                await page.locator('#remove-workspace').click()
                await page.locator('#empty-state').wait_for()
                await page.locator('#close-sidebar').click()
                check('remove last workspace clears source and actions', not await page.locator('#export-workspace').is_enabled() and not await page.locator('#source-panel').is_visible())
                check('no uncaught JavaScript errors', not errors)
                await browser.close()
    report = {'checks': results, 'passed': len(results), 'errors': errors, 'transport': 'native browser HTTP + SSE; controlled local connectivity endpoint'}
    (args.output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.ui-results')
    parser.add_argument('--browser', default=local_browser(), help='Chromium executable; default uses an installed browser or Playwright Chromium')
    asyncio.run(main(parser.parse_args()))

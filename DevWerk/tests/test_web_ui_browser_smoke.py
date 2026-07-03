import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request_json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_backend(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 30
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"backend exited early with code {process.returncode}")
        try:
            with urllib.request.urlopen(f"{base_url}/dashboard", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise AssertionError(f"backend did not start: {last_error}")


@pytest.mark.skipif(
    os.getenv("DEVWERK_RUN_BROWSER_SMOKE") != "1",
    reason="set DEVWERK_RUN_BROWSER_SMOKE=1 to run the real browser UI smoke",
)
def test_backend_web_ui_tabs_and_project_context_with_real_browser(tmp_path):
    if shutil.which("node") is None:
        pytest.fail("node is required for the browser smoke")

    backend_root = Path(__file__).resolve().parents[1]
    repo_root = backend_root.parent
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["DEVWERK_DB_PATH"] = str(tmp_path / "browser-smoke.db")
    env["LOG_DIR"] = str(tmp_path / "logs")

    stdout_path = tmp_path / "uvicorn.out.log"
    stderr_path = tmp_path / "uvicorn.err.log"
    process: subprocess.Popen[str] | None = None
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=backend_root,
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
        )
        try:
            _wait_for_backend(base_url, process)
            _request_json(
                "POST",
                f"{base_url}/v1/kanban/projects",
                {
                    "project_id": "web-smoke-alpha",
                    "name": "Web Smoke Alpha",
                    "description": "Browser smoke project alpha",
                },
            )
            _request_json(
                "POST",
                f"{base_url}/v1/kanban/projects",
                {
                    "project_id": "web-smoke-beta",
                    "name": "Web Smoke Beta",
                    "description": "Browser smoke project beta",
                },
            )
            smoke_workflow = {
                "name": "browser-smoke-flow",
                "columns": [
                    {"status_key": "intake", "title": "Intake", "position": 10, "transition_to": ["working", "blocked"]},
                    {"status_key": "working", "title": "Working", "position": 20, "transition_to": ["complete", "blocked"]},
                    {"status_key": "complete", "title": "Complete", "position": 90, "transition_to": []},
                    {"status_key": "blocked", "title": "Blocked", "position": 99, "transition_to": ["intake"]},
                ],
                "actions": {
                    "begin": {"to": "working"},
                    "workflow_done": {"to": "complete"},
                    "fail": {"to": "blocked"},
                    "abandon": {"to": "blocked"},
                    "retry": {"to": "intake"},
                },
            }
            for project_id in ("web-smoke-alpha", "web-smoke-beta"):
                _request_json(
                    "PUT",
                    f"{base_url}/v1/kanban/projects/{project_id}/workflow",
                    {"workflow": smoke_workflow},
                )
            _request_json(
                "POST",
                f"{base_url}/v1/kanban/tasks",
                {"project_id": "web-smoke-alpha", "title": "Alpha workflow task", "status_key": "working"},
            )
            _request_json(
                "POST",
                f"{base_url}/v1/kanban/tasks",
                {"project_id": "web-smoke-beta", "title": "Beta blocked task", "status_key": "blocked"},
            )

            playwright_core = repo_root / ".devwerk" / "node_modules" / "playwright-core"
            script = f"""
const fs = require('fs');
const path = require('path');
const modulePath = process.env.PLAYWRIGHT_CORE_PATH || {json.dumps(str(playwright_core))};
const playwright = require(fs.existsSync(modulePath) ? modulePath : 'playwright-core');
const chrome = process.env.DEVWERK_BROWSER_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
(async () => {{
  if (!fs.existsSync(chrome)) throw new Error(`Chrome executable not found: ${{chrome}}`);
  const browser = await playwright.chromium.launch({{ executablePath: chrome, headless: true }});
  const page = await browser.newPage({{ viewport: {{ width: 1440, height: 900 }} }});
  const errors = [];
  page.on('console', msg => {{ if (msg.type() === 'error') errors.push(msg.text()); }});
  page.on('pageerror', err => errors.push(err.message));
  async function one(selector, label) {{
    const count = await page.locator(selector).count();
    if (count !== 1) throw new Error(`${{label}} expected 1 element, got ${{count}}`);
    return page.locator(selector);
  }}
  async function info() {{
    return await page.evaluate(() => ({{
      url: location.href,
      hash: location.hash,
      h1: document.querySelector('h1')?.textContent || '',
      activeTab: document.querySelector('.tab.active')?.textContent || '',
      projectActiveTab: document.querySelector('button[data-project-tab].active')?.textContent || '',
      chatActiveTab: document.querySelector('button[data-chat-tab].active')?.textContent || '',
      taskActiveTab: document.querySelector('button[data-task-tab].active')?.textContent || ''
    }}));
  }}
  await page.goto('{base_url}/dashboard?project_id=web-smoke-alpha', {{ waitUntil: 'domcontentloaded' }});
  await page.waitForSelector('button[data-project-tab="configuration"]');
  await page.waitForSelector('.live-log-card');
  await page.waitForSelector('#prompt');
  const chatLayout = await page.evaluate(() => {{
    const grid = document.querySelector('.project-workbench-grid');
    const card = document.querySelector('.chat-card');
    const body = document.querySelector('#chatBody');
    if (!grid || !card || !body) return {{ missing: true }};
    const before = card.getBoundingClientRect();
    for (let i = 0; i < 80; i += 1) {{
      const bubble = document.createElement('div');
      bubble.className = i % 2 ? 'user-bubble' : 'message-content';
      bubble.textContent = `Synthetic long conversation row ${{i}} `.repeat(8);
      body.appendChild(bubble);
    }}
    const after = card.getBoundingClientRect();
    const bodyStyle = getComputedStyle(body);
    return {{
      missing: false,
      viewportHeight: window.innerHeight,
      gridHeight: Math.round(grid.getBoundingClientRect().height),
      cardBeforeHeight: Math.round(before.height),
      cardAfterHeight: Math.round(after.height),
      bodyClientHeight: body.clientHeight,
      bodyScrollHeight: body.scrollHeight,
      bodyOverflowY: bodyStyle.overflowY,
      pageScrollHeight: document.querySelector('.page')?.scrollHeight || 0,
      pageClientHeight: document.querySelector('.page')?.clientHeight || 0
    }};
  }});
  if (chatLayout.missing) throw new Error('chat layout elements are missing');
  if (chatLayout.cardBeforeHeight < 640 || chatLayout.cardBeforeHeight > 900) throw new Error(`chat card height outside designed range: ${{JSON.stringify(chatLayout)}}`);
  if (Math.abs(chatLayout.cardAfterHeight - chatLayout.cardBeforeHeight) > 2) throw new Error(`chat card was expanded by long conversation content: ${{JSON.stringify(chatLayout)}}`);
  if (!['auto', 'scroll'].includes(chatLayout.bodyOverflowY)) throw new Error(`chat body is not independently scrollable: ${{JSON.stringify(chatLayout)}}`);
  if (chatLayout.bodyScrollHeight <= chatLayout.bodyClientHeight) throw new Error(`long conversation did not create internal chat scroll: ${{JSON.stringify(chatLayout)}}`);
  await (await one('#newProjectMain', 'new project button')).click();
  await page.waitForSelector('.modal');
  if (!await page.locator('.modal h2:has-text("New Project")').count()) throw new Error('new project modal did not open');
  await page.locator('[data-dialog-close="true"]').click();
  for (const key of ['configuration','settings','workflow','routing','integrations','history','activity']) {{
    await (await one(`button[data-project-tab="${{key}}"]`, `project tab ${{key}}`)).click();
    await page.waitForTimeout(80);
    const current = await info();
    const expected = key === 'configuration' ? 'configuration' : key;
    if (!current.projectActiveTab.toLowerCase().includes(expected)) throw new Error(`project tab ${{key}} did not activate: ${{JSON.stringify(current)}}`);
  }}
  await (await one('button[data-project-tab="configuration"]', 'configuration tab')).click();
  await page.waitForSelector('.editor-card[data-editor-title="Project.MD"]');
  await page.locator('.editor-card[data-editor-title="Project.MD"] .code-editor').fill('# Project.MD: web-smoke-alpha\\n\\n## Project Intent\\nBrowser smoke.\\n');
  await page.locator('.editor-card[data-editor-title="Project.MD"] [data-action="editor-save"]').first().click();
  await page.waitForSelector('text=Project.MD saved.');
  await page.locator('.editor-card[data-editor-title="Agents"] [data-action="editor-format"]').first().click();
  await page.waitForSelector('.toast');
  await page.locator('.editor-card[data-editor-title="Parameters"] [data-action="editor-save"]').first().click();
  await page.waitForSelector('text=Parameters saved.');
  for (const key of ['events','memory','analytics','settings']) {{
    await (await one(`a[data-nav="${{key}}"]`, `nav ${{key}}`)).click();
    await page.waitForTimeout(160);
    const current = await info();
    if (current.hash !== `#${{key}}`) throw new Error(`nav ${{key}} hash mismatch: ${{JSON.stringify(current)}}`);
    if (!current.url.includes('project_id=web-smoke-alpha')) throw new Error(`nav ${{key}} lost project id: ${{JSON.stringify(current)}}`);
    if (!current.h1.toLowerCase().includes(key)) throw new Error(`nav ${{key}} h1 mismatch: ${{JSON.stringify(current)}}`);
    if (key === 'analytics') {{
      await page.waitForSelector('text=Project Token Breakdown');
      await page.waitForSelector('text=Task Token Breakdown');
    }}
    if (key === 'settings') {{
      await page.waitForSelector('text=Global Settings');
      await page.waitForSelector('text=Global Route Map');
    }}
  }}
  for (const key of ['overview','projects','kanban','tasks']) {{
    await (await one(`a[data-nav="${{key}}"]`, `nav ${{key}}`)).click();
    await page.waitForTimeout(180);
    const current = await info();
    const expectedPath = key === 'overview' ? '/workbench' : key === 'projects' ? '/dashboard' : `/${{key}}`;
    if (!current.url.includes(expectedPath)) throw new Error(`nav ${{key}} did not navigate: ${{JSON.stringify(current)}}`);
    if (!current.url.includes('project_id=web-smoke-alpha')) throw new Error(`nav ${{key}} lost project id: ${{JSON.stringify(current)}}`);
  }}
  await page.goto('{base_url}/kanban?project_id=web-smoke-alpha', {{ waitUntil: 'domcontentloaded' }});
  await page.waitForSelector('#createTask');
  await (await one('#createTask', 'create task button')).click();
  await page.waitForSelector('.modal');
  if (!await page.locator('.modal h2:has-text("New Task")').count()) throw new Error('new task modal did not open');
  await page.locator('[data-dialog-close="true"]').click();
  await page.goto('{base_url}/workbench?project_id=web-smoke-alpha', {{ waitUntil: 'domcontentloaded' }});
  await page.waitForSelector('text=DevWerk Overview');
  if (await page.locator('#prompt').count()) throw new Error('overview should not render project conversation composer');
  await page.goto('{base_url}/dashboard?project_id=web-smoke-alpha', {{ waitUntil: 'domcontentloaded' }});
  await page.waitForSelector('button[data-chat-tab="conversation"]');
  await page.locator('#prompt').fill('Discuss project workflow');
  if (!await page.locator('#prompt').inputValue().then(value => value.includes('Discuss project workflow'))) throw new Error('projects conversation composer is not editable');
  for (const key of ['conversation','workflow_log','artifacts']) {{
    await (await one(`button[data-chat-tab="${{key}}"]`, `chat tab ${{key}}`)).click();
    await page.waitForTimeout(80);
    const current = await info();
    const expected = key === 'workflow_log' ? 'workflow log' : key;
    if (!current.chatActiveTab.toLowerCase().includes(expected)) throw new Error(`chat tab ${{key}} did not activate: ${{JSON.stringify(current)}}`);
  }}
  await page.goto('{base_url}/tasks?project_id=web-smoke-alpha', {{ waitUntil: 'domcontentloaded' }});
  await page.waitForSelector('text=Task List');
  await page.waitForSelector('text=Task Token Usage');
  await page.waitForSelector('button[data-task-tab="summary"]');
  for (const key of ['summary','plan','diff','events','memory']) {{
    await (await one(`button[data-task-tab="${{key}}"]`, `task tab ${{key}}`)).click();
    await page.waitForTimeout(80);
    const current = await info();
    const expected = key === 'memory' ? 'memory' : key;
    if (!current.taskActiveTab.toLowerCase().includes(expected)) throw new Error(`task tab ${{key}} did not activate: ${{JSON.stringify(current)}}`);
  }}
  await page.goto('{base_url}/dashboard?project_id=web-smoke-alpha#analytics', {{ waitUntil: 'domcontentloaded' }});
  await page.waitForSelector('button[data-project="web-smoke-beta"]');
  await (await one('button[data-project="web-smoke-beta"]', 'project rail beta')).click();
  await page.waitForTimeout(400);
  const current = await info();
  if (!current.url.includes('project_id=web-smoke-beta')) throw new Error(`project switch did not update project id: ${{JSON.stringify(current)}}`);
  if (current.hash !== '#analytics') throw new Error(`project switch lost analytics hash: ${{JSON.stringify(current)}}`);
  if (!current.h1.toLowerCase().includes('analytics')) throw new Error(`project switch left analytics page: ${{JSON.stringify(current)}}`);
  if (errors.length) throw new Error(`browser console errors: ${{JSON.stringify(errors)}}`);
  await browser.close();
}})().catch(err => {{ console.error(err.stack || err.message || String(err)); process.exit(1); }});
"""
            completed = subprocess.run(
                ["node", "-e", script],
                cwd=repo_root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=90,
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()

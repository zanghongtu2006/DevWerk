from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


def _enabled() -> bool:
    return os.environ.get("DEVWERK_RUN_REAL_PROJECT_SCAFFOLD_SMOKE") == "1"


def _repo_backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _llm_config_path() -> Path:
    return _repo_backend_root() / "config" / "llm.json"


def _smoke_llm_config_path(tmp_path: Path) -> Path:
    config = json.loads(_llm_config_path().read_text(encoding="utf-8-sig"))
    for provider in (config.get("llms") or {}).values():
        if not isinstance(provider, dict):
            continue
        provider["timeout"] = max(int(provider.get("timeout") or 180), 240)
        for model_settings in (provider.get("models") or {}).values():
            if not isinstance(model_settings, dict):
                continue
            model_settings["max_tokens"] = min(int(model_settings.get("max_tokens") or 4096), 4096)
            if model_settings.get("thinking_mode") == "max":
                model_settings["thinking_mode"] = "balanced"
            if model_settings.get("effort_level") == "max":
                model_settings["effort_level"] = "medium"
    target = tmp_path / "llm-project-scaffold-smoke.json"
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(method: str, url: str, payload: dict | None = None, *, timeout: int = 30) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"HTTP {exc.code} from {url}: {text}") from exc
    return json.loads(text) if text else {}


def _wait_for_server(base_url: str, process: subprocess.Popen, *, timeout_seconds: int = 45) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"DevWerk server exited early: {process.returncode}")
        try:
            _request("GET", f"{base_url}/v1/kanban/projects", timeout=3)
            return
        except Exception:
            time.sleep(0.5)
    raise AssertionError("DevWerk server did not become ready")


def _wait_for_workflow(base_url: str, task_id: str, *, timeout_seconds: int = 420) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_state: dict = {}
    while time.monotonic() < deadline:
        last_state = _request("GET", f"{base_url}/v1/workflows/{task_id}", timeout=15)
        result = last_state.get("result")
        status = str(last_state.get("status_key") or "")
        if isinstance(result, dict) or status == "failed":
            return last_state
        time.sleep(2)
    raise AssertionError(f"workflow did not finish before timeout: {last_state}")


@pytest.mark.skipif(not _enabled(), reason="set DEVWERK_RUN_REAL_PROJECT_SCAFFOLD_SMOKE=1 to run live HTTP+LLM scaffold smoke")
def test_real_http_project_conversation_creates_scaffold_on_disk(tmp_path):
    if not _llm_config_path().is_file():
        pytest.skip("local LLM config is missing")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    project_id = f"live-scaffold-{uuid.uuid4().hex[:8]}"
    target_root = tmp_path / "mini-program-points-mall"
    env = {
        **os.environ,
        "APP_ENV": "test",
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "RELOAD": "false",
        "DEVWERK_LLM_CONFIG_PATH": str(_smoke_llm_config_path(tmp_path)),
        "DEVWERK_DB_PATH": str(tmp_path / "devwerk-live-scaffold.db"),
        "DEVWERK_SESSION_DIR": str(tmp_path / "sessions"),
        "DEVWERK_USAGE_TRACKING": "true",
        "WORKFLOW_SUPERVISOR_INTERVAL_SECONDS": "1",
        "WORKFLOW_QUEUED_RECOVERY_SECONDS": "120",
        "WORKFLOW_EXECUTION_TIMEOUT_SECONDS": "900",
        "WORKFLOW_CLIENT_TIMEOUT_SECONDS": "900",
        "DEVWERK_WORKFLOW_DESIGN_LLM_ATTEMPTS": "2",
    }
    server_log_path = tmp_path / "devwerk-server.log"
    server_log = server_log_path.open("w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(_repo_backend_root()),
        env=env,
        text=True,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_server(base_url, process)

        created = _request(
            "POST",
            f"{base_url}/v1/kanban/projects",
            {"project_id": project_id, "name": "Live Scaffold Smoke", "description": "Real LLM scaffold smoke"},
        )
        assert created["ok"] is True

        design = _request(
            "POST",
            f"{base_url}/v1/kanban/projects/{project_id}/conversation",
            {
                "action": "save_design",
                "save": True,
                "message": (
                    "Create and save a coding workflow for a mini-program points mall scaffold. "
                    "The workflow must have at least one code-producing implementation column and "
                    "must end through ready_to_apply, backend apply, verification, and done/failed."
                ),
            },
            timeout=620,
        )
        assert design["ok"] is True, design
        assert design["kind"] == "workflow_design", design

        workflow = _request("GET", f"{base_url}/v1/kanban/projects/{project_id}/workflow")
        success_status = (workflow["workflow"].get("actions") or {}).get("workflow_done", {}).get("to") or "done"
        columns = workflow["workflow"].get("columns") or []
        assert any(column.get("job_template") for column in columns)
        assert workflow["workflow"].get("requires_apply") is True or workflow["workflow"].get("workflow_type") == "coding"

        task_request = (
            f"Create a minimal scaffold on disk at target_root={target_root.as_posix()}. "
            "Build a small mini-program points mall skeleton with two folders: "
            "miniapp-uniapp and backend-java. Return concrete outputs.code_patch.files with target_root. "
            "Keep it small but real: include README.md, miniapp-uniapp/package.json, "
            "miniapp-uniapp/src/pages/index/index.vue, backend-java/pom.xml, "
            "backend-java/src/main/java/com/devwerk/points/PointsMallApplication.java, and "
            "backend-java/src/main/java/com/devwerk/points/ActivityController.java."
        )
        started = _request(
            "POST",
            f"{base_url}/v1/kanban/projects/{project_id}/conversation",
            {"action": "start_task", "message": task_request},
            timeout=60,
        )
        assert started["ok"] is True, started
        task_id = started["task_id"]
        final_state = _wait_for_workflow(base_url, task_id)
        assert final_state["status_key"] == success_status, final_state
        assert final_state["result"]["ok"] is True

        expected_files = [
            "README.md",
            "miniapp-uniapp/package.json",
            "miniapp-uniapp/src/pages/index/index.vue",
            "backend-java/pom.xml",
            "backend-java/src/main/java/com/devwerk/points/PointsMallApplication.java",
            "backend-java/src/main/java/com/devwerk/points/ActivityController.java",
        ]
        missing = [path for path in expected_files if not (target_root / path).is_file()]
        assert not missing, f"missing generated files: {missing}; existing={list(target_root.rglob('*'))}"

        task = _request("GET", f"{base_url}/v1/kanban/tasks/{task_id}")
        artifacts = task["task"].get("artifacts") or []
        assert any(item.get("artifact_type") == "backend_local_apply_result" for item in artifacts)
        assert any(item.get("artifact_type") == "workflow_result" for item in artifacts)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        server_log.close()

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings


def append_task_event(
    *,
    project_id: str,
    task_id: str,
    event_type: str,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """
    Persist the durable task event log outside SQLite.

    Rules:
    - Every kanban event appends to task/events.jsonl.
    - If payload.session_id exists, the same event is also appended to that
      session's events.jsonl.
    - Payload is stored as JSON, so no Python object pickles or process memory
      are required to reconstruct the workflow.
    """
    data = {
        "created_at": _now(),
        "project_id": project_id,
        "task_id": task_id,
        "event_type": event_type,
        "from_status": from_status,
        "to_status": to_status,
        "payload": payload or {},
    }
    _append_jsonl(_task_dir(project_id, task_id) / "events.jsonl", data)

    session_id = _session_id_from_payload(payload or {})
    if session_id:
        _append_jsonl(_session_dir(project_id, task_id, session_id) / "events.jsonl", data)


def record_phase_memory(
    *,
    project_id: str,
    task_id: str,
    phase_output: dict[str, Any],
) -> None:
    """
    Persist the current session memory snapshot.

    This is intentionally small and deterministic: phase inputs/outputs,
    summary, warnings, status, and next action. It is not a vector memory or a
    long-term framework memory yet; it is durable task/session memory for the
    current kanban loop.
    """
    session_id = str(phase_output.get("session_id") or "").strip()
    if not session_id:
        return

    task_dir = _task_dir(project_id, task_id)
    session_dir = _session_dir(project_id, task_id, session_id)
    payload = {
        "updated_at": _now(),
        "project_id": project_id,
        "task_id": task_id,
        "session_id": session_id,
        "phase": phase_output.get("phase"),
        "agent": phase_output.get("agent"),
        "status_key": phase_output.get("status_key"),
        "summary": phase_output.get("summary") or "",
        "inputs": phase_output.get("inputs") or {},
        "outputs": phase_output.get("outputs") or {},
        "warnings": phase_output.get("warnings") or [],
        "next_action": phase_output.get("next_action"),
    }

    _write_json(session_dir / "memory.json", payload)
    _append_jsonl(session_dir / "phase_outputs.jsonl", payload)
    _append_jsonl(task_dir / "memory.jsonl", payload)
    _write_json(task_dir / "latest_memory.json", payload)


def session_root() -> Path:
    configured = os.environ.get("DEVWERK_SESSION_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return _data_root() / "sessions"


def _data_root() -> Path:
    db_path = Path(str(settings().devwerk_db_path))
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parents[2] / db_path
    return db_path.parent


def _task_dir(project_id: str, task_id: str) -> Path:
    return session_root() / _safe_segment(project_id) / _safe_segment(task_id)


def _session_dir(project_id: str, task_id: str, session_id: str) -> Path:
    return _task_dir(project_id, task_id) / "sessions" / _safe_segment(session_id)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        out.write("\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _session_id_from_payload(payload: dict[str, Any]) -> str | None:
    session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    nested = payload.get("phase_output")
    if isinstance(nested, dict):
        nested_session_id = nested.get("session_id")
        if isinstance(nested_session_id, str) and nested_session_id.strip():
            return nested_session_id.strip()
    return None


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text[:160] or "default"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

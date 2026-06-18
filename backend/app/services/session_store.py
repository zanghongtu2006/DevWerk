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
    Append a project-level audit copy. SQLite is the source of truth for
    conversation, column-run, revision, artifact, and event state.
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
    _append_jsonl(session_root() / _safe_segment(project_id) / "audit_events.jsonl", data)


def record_phase_memory(
    *,
    project_id: str,
    task_id: str,
    phase_output: dict[str, Any],
) -> None:
    """
    Update compact project memory from a phase output.

    Runtime session state is stored in kb_conversations/kb_messages and
    kb_column_runs. This function deliberately does not create per-task or
    per-agent filesystem session trees.
    """
    session_id = str(phase_output.get("session_id") or "").strip()
    if not session_id:
        return

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
        "decision": phase_output.get("decision"),
        "next_action": phase_output.get("next_action"),
    }

    record_project_memory(project_id=project_id, task_id=task_id, phase_output=payload)


def read_project_memory(project_id: str) -> dict[str, Any]:
    path = project_memory_path(project_id)
    if not path.is_file():
        return _default_project_memory(project_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_project_memory(project_id)
    if not isinstance(data, dict):
        return _default_project_memory(project_id)
    return _normalize_project_memory(project_id, data)


def project_memory_path(project_id: str) -> Path:
    return session_root() / _safe_segment(project_id) / "project_memory.json"


def record_project_memory(
    *,
    project_id: str,
    task_id: str,
    phase_output: dict[str, Any],
) -> dict[str, Any]:
    """
    Persist compact project-level memory derived from workflow phase outputs.

    Project memory is deliberately narrower than full session logs. It keeps
    reusable engineering facts such as task summaries, touched paths, commands,
    framework signals, and rules without storing raw prompts or long transcripts.
    """
    now = _now()
    memory = read_project_memory(project_id)
    inputs = phase_output.get("inputs") if isinstance(phase_output.get("inputs"), dict) else {}
    outputs = phase_output.get("outputs") if isinstance(phase_output.get("outputs"), dict) else {}
    warnings = phase_output.get("warnings") if isinstance(phase_output.get("warnings"), list) else []

    summary = str(phase_output.get("summary") or "").strip()
    phase_summary = {
        "updated_at": now,
        "task_id": task_id,
        "session_id": str(phase_output.get("session_id") or ""),
        "phase": phase_output.get("phase"),
        "agent": phase_output.get("agent"),
        "status_key": phase_output.get("status_key"),
        "summary": summary,
        "paths": _extract_paths(inputs, outputs),
        "commands": _extract_commands(outputs),
        "warnings": [str(item) for item in warnings[:10]],
        "next_action": phase_output.get("next_action"),
        "decision": phase_output.get("decision"),
    }

    memory["project_id"] = project_id
    memory["updated_at"] = now
    memory["tasks_seen"] = _dedupe([*memory.get("tasks_seen", []), task_id], limit=1000)
    memory["frameworks"] = _dedupe([*memory.get("frameworks", []), *_extract_frameworks(inputs, outputs)], limit=100)
    memory["paths"] = _dedupe([*memory.get("paths", []), *phase_summary["paths"]], limit=500)
    memory["commands"] = _dedupe([*memory.get("commands", []), *phase_summary["commands"]], limit=200)
    memory["rules"] = _dedupe([*memory.get("rules", []), *_extract_rules(inputs, outputs)], limit=200)

    summaries = [item for item in memory.get("phase_summaries", []) if isinstance(item, dict)]
    summaries.append(phase_summary)
    memory["phase_summaries"] = summaries[-500:]

    project_dir = session_root() / _safe_segment(project_id)
    _write_json(project_dir / "project_memory.json", memory)
    _append_jsonl(
        project_dir / "project_memory.jsonl",
        {
            "updated_at": now,
            "project_id": project_id,
            "task_id": task_id,
            "phase": phase_summary["phase"],
            "agent": phase_summary["agent"],
            "status_key": phase_summary["status_key"],
            "summary": summary,
            "paths": phase_summary["paths"],
            "commands": phase_summary["commands"],
            "next_action": phase_summary["next_action"],
            "decision": phase_summary["decision"],
        },
    )
    return memory


def session_root() -> Path:
    configured = os.environ.get("DEVWERK_SESSION_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return _data_root() / "sessions"


def _default_project_memory(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "version": 1,
        "updated_at": None,
        "tasks_seen": [],
        "frameworks": [],
        "paths": [],
        "commands": [],
        "rules": [],
        "phase_summaries": [],
    }


def _normalize_project_memory(project_id: str, data: dict[str, Any]) -> dict[str, Any]:
    memory = _default_project_memory(project_id)
    memory.update(data)
    memory["project_id"] = project_id
    for key in ("tasks_seen", "frameworks", "paths", "commands", "rules", "phase_summaries"):
        if not isinstance(memory.get(key), list):
            memory[key] = []
    return memory


def _extract_paths(inputs: dict[str, Any], outputs: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for item in _walk_values({"inputs": inputs, "outputs": outputs}):
        if not isinstance(item, dict):
            continue
        for key in ("path", "file", "filepath"):
            value = item.get(key)
            if isinstance(value, str) and _looks_like_path(value):
                paths.append(value)
        for key in ("paths", "files", "changed_paths", "approved_paths"):
            value = item.get(key)
            if isinstance(value, list):
                for candidate in value:
                    if isinstance(candidate, str) and _looks_like_path(candidate):
                        paths.append(candidate)
                    elif isinstance(candidate, dict):
                        nested = candidate.get("path") or candidate.get("file")
                        if isinstance(nested, str) and _looks_like_path(nested):
                            paths.append(nested)
    return _dedupe(paths, limit=120)


def _extract_commands(outputs: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for item in _walk_values(outputs):
        if not isinstance(item, dict):
            continue
        args = item.get("args")
        if item.get("tool") == "run_command" and isinstance(args, dict):
            command = args.get("command")
            if isinstance(command, list):
                commands.append(" ".join(str(part) for part in command))
            elif isinstance(command, str):
                commands.append(command)
    return _dedupe(commands, limit=50)


def _extract_frameworks(inputs: dict[str, Any], outputs: dict[str, Any]) -> list[str]:
    frameworks: list[str] = []
    for item in _walk_values({"inputs": inputs, "outputs": outputs}):
        if not isinstance(item, dict):
            continue
        for key in ("framework", "frameworks", "detected_framework", "detected_frameworks"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                frameworks.append(value.strip())
            elif isinstance(value, list):
                frameworks.extend(str(entry).strip() for entry in value if str(entry).strip())
        kind = item.get("kind")
        if isinstance(kind, str) and kind.strip():
            frameworks.append(kind.strip())
    return _dedupe(frameworks, limit=20)


def _extract_rules(inputs: dict[str, Any], outputs: dict[str, Any]) -> list[str]:
    rules: list[str] = []
    for item in _walk_values({"inputs": inputs, "outputs": outputs}):
        if not isinstance(item, dict):
            continue
        for key in ("rules", "writing_rules", "constraints", "acceptance_criteria"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                rules.append(value.strip())
            elif isinstance(value, list):
                rules.extend(str(entry).strip() for entry in value if str(entry).strip())
    return _dedupe(rules, limit=50)


def _walk_values(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_values(nested)


def _looks_like_path(value: str) -> bool:
    text = value.strip()
    return bool(text) and ("\\" in text or "/" in text or "." in Path(text).name)


def _dedupe(values: list[Any], *, limit: int) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        item = value if isinstance(value, dict) else str(value).strip()
        if item == "":
            continue
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[-limit:]


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

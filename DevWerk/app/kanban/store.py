from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.session_store import append_task_event
from app.kanban.definition import (
    empty_workflow_definition,
    validate_managed_workflow_definition,
    workflow_from_dict,
)
from app.services.workflow_designer import normalize_workflow_payload

_log = logging.getLogger("devwerk.kanban")
_initialized = False

DEFAULT_PROJECT_ID = "default"
TABLE_NAME_PREFIX = "kb_"
T_PROJECTS = f"{TABLE_NAME_PREFIX}projects"
T_PROJECT_SETTINGS = f"{TABLE_NAME_PREFIX}project_settings"
T_COLUMNS = f"{TABLE_NAME_PREFIX}columns"
T_TASKS = f"{TABLE_NAME_PREFIX}tasks"
T_EVENTS = f"{TABLE_NAME_PREFIX}events"
T_ARTIFACTS = f"{TABLE_NAME_PREFIX}artifacts"
T_CONVERSATIONS = f"{TABLE_NAME_PREFIX}conversations"
T_MESSAGES = f"{TABLE_NAME_PREFIX}messages"
T_COLUMN_RUNS = f"{TABLE_NAME_PREFIX}column_runs"
T_REVISIONS = f"{TABLE_NAME_PREFIX}revisions"


def init_kanban_db() -> None:
    global _initialized
    if _initialized:
        return

    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        _migrate_legacy_tables(conn)
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS kb_projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kb_project_settings (
                project_id TEXT PRIMARY KEY,
                agents_json TEXT NOT NULL DEFAULT '{}',
                models_json TEXT NOT NULL DEFAULT '{}',
                parameters_json TEXT NOT NULL DEFAULT '{}',
                workflow_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kb_columns (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                status_key TEXT NOT NULL,
                title TEXT NOT NULL,
                position INTEGER NOT NULL,
                wip_limit INTEGER,
                transition_to TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, status_key)
            );

            CREATE INDEX IF NOT EXISTS idx_kb_columns_project_position
                ON kb_columns(project_id, position);

            CREATE INDEX IF NOT EXISTS idx_kb_projects_updated_name
                ON kb_projects(updated_at DESC, name ASC);

            CREATE TABLE IF NOT EXISTS kb_tasks (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                status_key TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_kb_tasks_project_status
                ON kb_tasks(project_id, status_key, archived);

            CREATE INDEX IF NOT EXISTS idx_kb_tasks_project_archived_updated
                ON kb_tasks(project_id, archived, updated_at DESC);

            CREATE TABLE IF NOT EXISTS kb_events (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                payload_summary_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_kb_events_task_time
                ON kb_events(task_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_kb_events_project_time
                ON kb_events(project_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_kb_events_project_type_time
                ON kb_events(project_id, event_type, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_kb_events_task_type_time
                ON kb_events(task_id, event_type, created_at DESC);

            CREATE TABLE IF NOT EXISTS kb_artifacts (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                path TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_kb_artifacts_task
                ON kb_artifacts(task_id);

            CREATE INDEX IF NOT EXISTS idx_kb_artifacts_task_type_time
                ON kb_artifacts(task_id, artifact_type, created_at DESC);

            CREATE TABLE IF NOT EXISTS kb_conversations (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'active',
                active_column TEXT,
                waiting_for TEXT,
                summary TEXT NOT NULL DEFAULT '',
                summary_version INTEGER NOT NULL DEFAULT 0,
                token_estimate INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_kb_conversations_project_time
                ON kb_conversations(project_id, updated_at);

            CREATE TABLE IF NOT EXISTS kb_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL,
                message_type TEXT NOT NULL DEFAULT 'message',
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                compressed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(conversation_id, sequence)
            );

            CREATE INDEX IF NOT EXISTS idx_kb_messages_conversation_sequence
                ON kb_messages(conversation_id, sequence);

            CREATE TABLE IF NOT EXISTS kb_column_runs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status_key TEXT NOT NULL,
                agent TEXT NOT NULL,
                run_no INTEGER NOT NULL,
                state TEXT NOT NULL,
                checkpoint_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(task_id, status_key, run_no)
            );

            CREATE INDEX IF NOT EXISTS idx_kb_column_runs_task
                ON kb_column_runs(task_id, created_at);

            CREATE TABLE IF NOT EXISTS kb_revisions (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                parent_revision_id TEXT,
                state TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                ops_json TEXT NOT NULL DEFAULT '[]',
                patch_ops_json TEXT NOT NULL DEFAULT '[]',
                changed_paths_json TEXT NOT NULL DEFAULT '[]',
                verification_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(task_id, sequence)
            );

            CREATE INDEX IF NOT EXISTS idx_kb_revisions_task_sequence
                ON kb_revisions(task_id, sequence);
            """
        )
        _ensure_column(conn, T_PROJECT_SETTINGS, "workflow_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, T_EVENTS, "payload_summary_json", "TEXT NOT NULL DEFAULT '{}'")
    _initialized = True
    _log.debug("kanban db initialized path=%s", path)


def get_board(project_id: str | None = None) -> dict[str, Any]:
    pid = _project_id(project_id)
    ensure_project(pid)
    with _conn() as conn:
        columns = _columns(conn, pid)
        tasks = conn.execute(
            """
            SELECT *
              FROM kb_tasks
             WHERE project_id = ?
               AND archived = 0
             ORDER BY priority DESC, updated_at DESC
            """,
            (pid,),
        ).fetchall()
    grouped = {c["status_key"]: [] for c in columns}
    for row in tasks:
        task = _task_dict(row)
        grouped.setdefault(task["status_key"], []).append(task)
    return {
        "ok": True,
        "project_id": pid,
        "columns": [
            {
                **col,
                "tasks": grouped.get(col["status_key"], []),
            }
            for col in columns
        ],
    }


def list_projects() -> list[dict[str, Any]]:
    init_kanban_db()
    ensure_project(DEFAULT_PROJECT_ID)
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT *
              FROM kb_projects
             ORDER BY updated_at DESC, name ASC
            """
        ).fetchall()
        projects = [_project_dict(row) for row in rows]
        stats_by_project = _project_stats_many(conn, [project["id"] for project in projects])
        for project in projects:
            project["stats"] = stats_by_project.get(project["id"], _empty_project_stats())
        return projects


def get_project(project_id: str | None) -> dict[str, Any]:
    pid = _project_id(project_id)
    ensure_project(pid)
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_projects WHERE id = ?", (pid,)).fetchone()
        settings_row = conn.execute("SELECT * FROM kb_project_settings WHERE project_id = ?", (pid,)).fetchone()
        project = _project_dict(row)
        project["settings"] = _settings_dict(settings_row)
        project["stats"] = _project_stats(conn, pid)
    return {"ok": True, "project": project}


def delete_project(project_id: str | None) -> dict[str, Any]:
    pid = _project_id(project_id)
    if pid == DEFAULT_PROJECT_ID:
        raise ValueError("default project cannot be deleted")
    init_kanban_db()
    with _conn() as conn:
        row = conn.execute("SELECT id FROM kb_projects WHERE id = ?", (pid,)).fetchone()
        if row is None:
            raise KeyError(f"project not found: {pid}")
        conn.execute("DELETE FROM kb_revisions WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM kb_column_runs WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM kb_messages WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM kb_conversations WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM kb_artifacts WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM kb_events WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM kb_tasks WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM kb_columns WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM kb_project_settings WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM kb_projects WHERE id = ?", (pid,))
    _log.debug("kanban project deleted project_id=%s", pid)
    return {"ok": True, "project_id": pid, "deleted": True}


def upsert_project(
    *,
    project_id: str | None,
    name: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    pid = _project_id(project_id)
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO kb_projects (id, name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                updated_at = excluded.updated_at
            """,
            (pid, (name or pid).strip() or pid, description or "", now, now),
        )
    ensure_project_settings(pid)
    return get_project(pid)


def get_project_settings(project_id: str | None) -> dict[str, Any]:
    pid = _project_id(project_id)
    ensure_project(pid)
    ensure_project_settings(pid)
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_project_settings WHERE project_id = ?", (pid,)).fetchone()
    return {"ok": True, "project_id": pid, "settings": _settings_dict(row)}


def update_project_settings(
    project_id: str | None,
    *,
    agents: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pid = _project_id(project_id)
    ensure_project(pid)
    ensure_project_settings(pid)
    workflow_definition = None
    normalized_workflow: dict[str, Any] | None = None
    if workflow is not None:
        debug: dict[str, Any] = {}
        current_workflow = get_project_workflow(pid).get("workflow") or {}
        normalized_workflow = normalize_workflow_payload(workflow, base_workflow=current_workflow, debug=debug)
        workflow_definition = workflow_from_dict(normalized_workflow)
        validate_managed_workflow_definition(workflow_definition)
        if debug.get("normalization_notes"):
            _log.debug(
                "kanban workflow normalized project_id=%s notes=%s",
                pid,
                debug.get("normalization_notes"),
            )
    now = _now()
    updates = []
    params: list[Any] = []
    if agents is not None:
        updates.append("agents_json = ?")
        params.append(_json(agents))
    if parameters is not None:
        updates.append("parameters_json = ?")
        params.append(_json(parameters))
    if workflow is not None:
        updates.append("workflow_json = ?")
        params.append(_json(normalized_workflow or workflow))
    if updates:
        updates.append("updated_at = ?")
        params.append(now)
        params.append(pid)
        with _conn() as conn:
            conn.execute(f"UPDATE kb_project_settings SET {', '.join(updates)} WHERE project_id = ?", params)
    if workflow_definition is not None:
        replace_columns(pid, workflow_definition.columns_for_kanban())
    return get_project_settings(pid)


def get_project_workflow(project_id: str | None = None) -> dict[str, Any]:
    pid = _project_id(project_id)
    ensure_project(pid)
    ensure_project_settings(pid)
    with _conn() as conn:
        row = conn.execute("SELECT workflow_json FROM kb_project_settings WHERE project_id = ?", (pid,)).fetchone()
    raw = _loads(row["workflow_json"], {}) if row is not None else {}
    definition = workflow_from_dict(raw) if isinstance(raw, dict) and raw else empty_workflow_definition()
    return {"ok": True, "project_id": pid, "workflow": definition_to_dict(definition)}


def update_project_workflow(project_id: str | None, workflow: dict[str, Any]) -> dict[str, Any]:
    update_project_settings(project_id, workflow=workflow)
    return get_project_workflow(project_id)


def list_columns(project_id: str | None = None) -> list[dict[str, Any]]:
    pid = _project_id(project_id)
    ensure_project(pid)
    with _conn() as conn:
        return _columns(conn, pid)


def replace_columns(project_id: str | None, columns: list[dict[str, Any]]) -> dict[str, Any]:
    pid = _project_id(project_id)
    if not columns:
        raise ValueError("columns must not be empty")
    normalized = [_normalize_column(c, i) for i, c in enumerate(columns)]
    keys = [c["status_key"] for c in normalized]
    if len(set(keys)) != len(keys):
        raise ValueError("column status_key values must be unique")
    with _conn() as conn:
        task_statuses = {
            row["status_key"]
            for row in conn.execute(
                "SELECT DISTINCT status_key FROM kb_tasks WHERE project_id = ? AND archived = 0",
                (pid,),
            ).fetchall()
        }
        missing = sorted(task_statuses - set(keys))
        if missing:
            raise ValueError(f"cannot remove columns with active tasks: {', '.join(missing)}")

        now = _now()
        conn.execute("DELETE FROM kb_columns WHERE project_id = ?", (pid,))
        for col in normalized:
            conn.execute(
                """
                INSERT INTO kb_columns (
                    id, project_id, status_key, title, position, wip_limit,
                    transition_to, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    pid,
                    col["status_key"],
                    col["title"],
                    col["position"],
                    col.get("wip_limit"),
                    json.dumps(col["transition_to"], ensure_ascii=False),
                    now,
                    now,
                ),
            )
    _log.debug("kanban columns replaced project_id=%s keys=%s", pid, keys)
    return get_board(pid)


def create_task(
    *,
    project_id: str | None,
    title: str,
    description: str = "",
    status_key: str | None = None,
    priority: int = 0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pid = _project_id(project_id)
    ensure_project(pid)
    with _conn() as conn:
        columns = _columns(conn, pid)
        if not columns:
            raise ValueError("project workflow is not configured; define columns through the project conversation first")
        status = status_key or columns[0]["status_key"]
        _require_status(columns, status)
        now = _now()
        task_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO kb_tasks (
                id, project_id, title, description, status_key, priority,
                metadata_json, archived, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                task_id,
                pid,
                title.strip(),
                description or "",
                status,
                int(priority or 0),
                _json(metadata or {}),
                now,
                now,
            ),
        )
        _insert_event(
            conn,
            task_id=task_id,
            project_id=pid,
            event_type="task_created",
            from_status=None,
            to_status=status,
            payload={"title": title, "status_key": status},
        )
    _log.debug("kanban task created project_id=%s task_id=%s status=%s title=%s", pid, task_id, status, title)
    return get_task(task_id)


def get_task(task_id: str) -> dict[str, Any]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        events = conn.execute(
            "SELECT * FROM kb_events WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        artifacts = conn.execute(
            "SELECT * FROM kb_artifacts WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        conversation_row = conn.execute("SELECT * FROM kb_conversations WHERE task_id = ?", (task_id,)).fetchone()
        messages = []
        if conversation_row is not None:
            messages = conn.execute(
                "SELECT * FROM kb_messages WHERE conversation_id = ? ORDER BY sequence ASC",
                (conversation_row["id"],),
            ).fetchall()
        column_runs = conn.execute(
            "SELECT * FROM kb_column_runs WHERE task_id = ? ORDER BY created_at ASC", (task_id,)
        ).fetchall()
        revisions = conn.execute(
            "SELECT * FROM kb_revisions WHERE task_id = ? ORDER BY sequence ASC", (task_id,)
        ).fetchall()
    task = _task_dict(row)
    task["events"] = [_event_dict(e) for e in events]
    task["artifacts"] = [_artifact_dict(a) for a in artifacts]
    task["conversation"] = _conversation_dict(conversation_row) if conversation_row is not None else None
    if task["conversation"] is not None:
        task["conversation"]["messages"] = [_message_dict(item) for item in messages]
    task["column_runs"] = [_column_run_dict(item) for item in column_runs]
    task["revisions"] = [_revision_dict(item) for item in revisions]
    return {"ok": True, "task": task}


def get_workflow_runtime_state(task_id: str, *, result_after: str | None = None) -> dict[str, Any]:
    """Read the hot workflow poll path without loading full events, artifacts, messages, or revisions."""
    with _conn() as conn:
        task = conn.execute(
            "SELECT id, project_id, status_key, created_at, updated_at FROM kb_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        conversation = conn.execute(
            "SELECT state, active_column, waiting_for, updated_at FROM kb_conversations WHERE task_id = ?", (task_id,)
        ).fetchone()
        sql = "SELECT payload_json, created_at FROM kb_artifacts WHERE task_id = ? AND artifact_type = 'workflow_result'"
        params: list[Any] = [task_id]
        if result_after:
            sql += " AND created_at > ?"
            params.append(result_after)
        sql += " ORDER BY created_at DESC LIMIT 1"
        result_row = conn.execute(sql, params).fetchone()
    return {
        "task": {
            "id": task["id"],
            "project_id": task["project_id"],
            "status_key": task["status_key"],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        },
        "conversation": {
            "state": conversation["state"],
            "active_column": conversation["active_column"],
            "waiting_for": conversation["waiting_for"],
            "updated_at": conversation["updated_at"],
        } if conversation is not None else None,
        "result": _loads(result_row["payload_json"], {}) if result_row is not None else None,
        "result_created_at": result_row["created_at"] if result_row is not None else None,
    }


def list_managed_workflow_states() -> list[dict[str, Any]]:
    """Return persistent non-terminal tasks for the workflow supervisor."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT t.id AS task_id, t.project_id, t.status_key, t.updated_at AS task_updated_at,
                   c.state, c.active_column, c.waiting_for, c.updated_at AS conversation_updated_at
              FROM kb_tasks t
              JOIN kb_conversations c ON c.task_id = t.id
             WHERE t.archived = 0
             ORDER BY c.updated_at ASC
            """
        ).fetchall()
        result = []
        for row in rows:
            success, failure = _workflow_terminal_statuses_from_db(conn, row["project_id"])
            if row["status_key"] in (success | failure):
                continue
            result.append(dict(row))
    return result


def get_latest_artifact_payload(task_id: str, artifact_type: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT payload_json
              FROM kb_artifacts
             WHERE task_id = ? AND artifact_type = ?
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (task_id, artifact_type),
        ).fetchone()
    if row is None:
        return None
    payload = _loads(row["payload_json"], {})
    return payload if isinstance(payload, dict) else None


def ensure_conversation(task_id: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    with _conn() as conn:
        task = conn.execute("SELECT project_id, status_key FROM kb_tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        row = conn.execute("SELECT * FROM kb_conversations WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            now = _now()
            conversation_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO kb_conversations (
                    id, task_id, project_id, state, active_column, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (conversation_id, task_id, task["project_id"], task["status_key"], _json(metadata or {}), now, now),
            )
            row = conn.execute("SELECT * FROM kb_conversations WHERE id = ?", (conversation_id,)).fetchone()
    return _conversation_dict(row)


def get_conversation(task_id: str, *, include_messages: bool = True) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_conversations WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        result = _conversation_dict(row)
        if include_messages:
            messages = conn.execute(
                "SELECT * FROM kb_messages WHERE conversation_id = ? ORDER BY sequence ASC", (row["id"],)
            ).fetchall()
            result["messages"] = [_message_dict(item) for item in messages]
    return result


def update_conversation(task_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {"state", "active_column", "waiting_for", "summary", "summary_version", "token_estimate", "metadata"}
    values = {key: value for key, value in fields.items() if key in allowed}
    ensure_conversation(task_id)
    if values:
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in values.items():
            column = "metadata_json" if key == "metadata" else key
            assignments.append(f"{column} = ?")
            params.append(_json(value or {}) if key == "metadata" else value)
        assignments.append("updated_at = ?")
        params.extend([_now(), task_id])
        with _conn() as conn:
            conn.execute(f"UPDATE kb_conversations SET {', '.join(assignments)} WHERE task_id = ?", params)
    return get_conversation(task_id) or {}


def append_conversation_message(
    task_id: str,
    *,
    role: str,
    content: str,
    message_type: str = "message",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversation = ensure_conversation(task_id)
    with _conn() as conn:
        next_sequence = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS value FROM kb_messages WHERE conversation_id = ?",
            (conversation["id"],),
        ).fetchone()["value"]
        message_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO kb_messages (
                id, conversation_id, task_id, project_id, sequence, role, message_type,
                content, metadata_json, compressed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                message_id, conversation["id"], task_id, conversation["project_id"], next_sequence,
                str(role or "user"), str(message_type or "message"), str(content or ""),
                _json(metadata or {}), _now(),
            ),
        )
        row = conn.execute("SELECT * FROM kb_messages WHERE id = ?", (message_id,)).fetchone()
    add_event(task_id, "conversation_message_recorded", {"message_id": message_id, "role": role, "message_type": message_type})
    return _message_dict(row)


def compress_conversation_messages(task_id: str, *, through_sequence: int, summary: str, token_estimate: int) -> dict[str, Any]:
    conversation = ensure_conversation(task_id)
    with _conn() as conn:
        conn.execute(
            "UPDATE kb_messages SET compressed = 1 WHERE conversation_id = ? AND sequence <= ?",
            (conversation["id"], int(through_sequence)),
        )
        conn.execute(
            """
            UPDATE kb_conversations
               SET summary = ?, summary_version = summary_version + 1, token_estimate = ?, updated_at = ?
             WHERE task_id = ?
            """,
            (summary, int(token_estimate), _now(), task_id),
        )
    add_event(task_id, "context_compressed", {"through_sequence": through_sequence, "token_estimate": token_estimate})
    return get_conversation(task_id) or {}


def start_column_run(task_id: str, *, status_key: str, agent: str, checkpoint: dict[str, Any] | None = None) -> dict[str, Any]:
    conversation = ensure_conversation(task_id)
    with _conn() as conn:
        run_no = conn.execute(
            "SELECT COALESCE(MAX(run_no), 0) + 1 AS value FROM kb_column_runs WHERE task_id = ? AND status_key = ?",
            (task_id, status_key),
        ).fetchone()["value"]
        run_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            """
            INSERT INTO kb_column_runs (id, task_id, project_id, status_key, agent, run_no, state, checkpoint_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (run_id, task_id, conversation["project_id"], status_key, agent, run_no, _json(checkpoint or {}), now, now),
        )
        row = conn.execute("SELECT * FROM kb_column_runs WHERE id = ?", (run_id,)).fetchone()
    update_conversation(task_id, state="running", active_column=status_key, waiting_for=None)
    return _column_run_dict(row)


def finish_column_run(run_id: str, *, state: str, checkpoint: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _now()
    completed_at = now if state in {"completed", "failed", "cancelled"} else None
    with _conn() as conn:
        conn.execute(
            "UPDATE kb_column_runs SET state = ?, checkpoint_json = ?, updated_at = ?, completed_at = ? WHERE id = ?",
            (state, _json(checkpoint or {}), now, completed_at, run_id),
        )
        row = conn.execute("SELECT * FROM kb_column_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"column run not found: {run_id}")
    return _column_run_dict(row)


def create_revision(task_id: str, *, summary: str, ops: list[Any], patch_ops: list[Any], changed_paths: list[str]) -> dict[str, Any]:
    conversation = ensure_conversation(task_id)
    with _conn() as conn:
        previous = conn.execute(
            "SELECT id, sequence FROM kb_revisions WHERE task_id = ? ORDER BY sequence DESC LIMIT 1", (task_id,)
        ).fetchone()
        sequence = int(previous["sequence"]) + 1 if previous else 1
        revision_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            """
            INSERT INTO kb_revisions (
                id, task_id, project_id, sequence, parent_revision_id, state, summary,
                ops_json, patch_ops_json, changed_paths_json, verification_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                revision_id, task_id, conversation["project_id"], sequence,
                previous["id"] if previous else None, summary, _json(ops), _json(patch_ops),
                _json(changed_paths), now, now,
            ),
        )
        row = conn.execute("SELECT * FROM kb_revisions WHERE id = ?", (revision_id,)).fetchone()
    add_event(task_id, "revision_created", {"revision_id": revision_id, "sequence": sequence, "changed_paths": changed_paths})
    return _revision_dict(row)


def update_task(task_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    allowed = {"title", "description", "priority", "metadata", "archived"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_task(task_id)

    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        set_parts = []
        params: list[Any] = []
        for key, value in updates.items():
            if key == "metadata":
                set_parts.append("metadata_json = ?")
                params.append(_json(value or {}))
            elif key == "archived":
                set_parts.append("archived = ?")
                params.append(1 if value else 0)
            else:
                column = "status_key" if key == "status" else key
                set_parts.append(f"{column} = ?")
                params.append(value)
        set_parts.append("updated_at = ?")
        params.append(_now())
        params.append(task_id)
        conn.execute(f"UPDATE kb_tasks SET {', '.join(set_parts)} WHERE id = ?", params)
        _insert_event(
            conn,
            task_id=task_id,
            project_id=row["project_id"],
            event_type="task_updated",
            from_status=row["status_key"],
            to_status=row["status_key"],
            payload=updates,
        )
    return get_task(task_id)


def move_task(task_id: str, to_status: str, *, force: bool = False, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        columns = _columns(conn, row["project_id"])
        by_key = {c["status_key"]: c for c in columns}
        if to_status not in by_key:
            raise ValueError(f"unknown status: {to_status}")
        from_status = row["status_key"]
        allowed = by_key.get(from_status, {}).get("transition_to", [])
        if not force and to_status != from_status and to_status not in allowed:
            raise ValueError(f"transition not allowed: {from_status} -> {to_status}")
        now = _now()
        conn.execute(
            "UPDATE kb_tasks SET status_key = ?, updated_at = ? WHERE id = ?",
            (to_status, now, task_id),
        )
        _insert_event(
            conn,
            task_id=task_id,
            project_id=row["project_id"],
            event_type="task_moved",
            from_status=from_status,
            to_status=to_status,
            payload=payload or {},
        )
    _log.debug("kanban task moved task_id=%s from=%s to=%s force=%s", task_id, from_status, to_status, force)
    return _task_record_response(task_id)


def add_event(task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        _insert_event(
            conn,
            task_id=task_id,
            project_id=row["project_id"],
            event_type=event_type,
            from_status=row["status_key"],
            to_status=row["status_key"],
            payload=payload or {},
        )
    return _task_record_response(task_id)


def add_project_event(
    project_id: str | None,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pid = _project_id(project_id)
    ensure_project(pid)
    event_payload = payload or {}
    with _conn() as conn:
        _insert_event(
            conn,
            task_id="__project__",
            project_id=pid,
            event_type=event_type,
            from_status=None,
            to_status=None,
            payload=event_payload,
        )
    return {"ok": True, "project_id": pid, "event_type": event_type, "payload": event_payload}


def list_events(
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    limit: int = 200,
    payload_mode: str = "full",
) -> dict[str, Any]:
    pid = _project_id(project_id)
    max_rows = max(1, min(int(limit or 200), 1000))
    where = ["e.project_id = ?"]
    params: list[Any] = [pid]
    if task_id:
        where.append("e.task_id = ?")
        params.append(task_id)
    params.append(max_rows)
    payload_select = "e.payload_summary_json AS payload_json" if payload_mode == "summary" else "e.payload_json AS payload_json"
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT
                e.id, e.task_id, e.project_id, e.event_type, e.from_status, e.to_status,
                {payload_select},
                e.created_at,
                t.title AS task_title,
                t.status_key AS task_status_key
              FROM kb_events e
              LEFT JOIN kb_tasks t ON t.id = e.task_id
             WHERE {' AND '.join(where)}
             ORDER BY e.created_at DESC
             LIMIT ?
            """,
            params,
        ).fetchall()
    return {
        "ok": True,
        "project_id": pid,
        "task_id": task_id,
        "events": [_event_dict(row, payload_mode=payload_mode) for row in rows],
    }


def list_project_conversation_messages(project_id: str | None = None, *, limit: int = 80) -> dict[str, Any]:
    """Return project conversation messages without parsing unrelated event payloads."""
    pid = _project_id(project_id)
    max_rows = max(1, min(int(limit or 80), 500))
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, task_id, project_id, event_type, payload_json, created_at
              FROM kb_events
             WHERE project_id = ?
               AND event_type = 'project_conversation_message'
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (pid, max_rows),
        ).fetchall()
    messages = []
    for row in reversed(rows):
        payload = _loads(row["payload_json"], {})
        if not isinstance(payload, dict):
            payload = {}
        messages.append(
            {
                "role": payload.get("role") or "assistant",
                "content": payload.get("content") or "",
                "kind": payload.get("kind") or "message",
                "created_at": row["created_at"],
                "task_id": payload.get("task_id"),
            }
        )
    return {"ok": True, "project_id": pid, "messages": messages}


def add_artifact(
    task_id: str,
    *,
    artifact_type: str,
    path: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        conn.execute(
            """
            INSERT INTO kb_artifacts (
                id, task_id, project_id, artifact_type, path, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                task_id,
                row["project_id"],
                artifact_type,
                path,
                _json(payload or {}),
                _now(),
            ),
        )
        _insert_event(
            conn,
            task_id=task_id,
            project_id=row["project_id"],
            event_type="artifact_added",
            from_status=row["status_key"],
            to_status=row["status_key"],
            payload={"artifact_type": artifact_type, "path": path},
        )
    return _task_record_response(task_id)


def ensure_project(project_id: str | None = None) -> None:
    pid = _project_id(project_id)
    init_kanban_db()
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO kb_projects (id, name, description, created_at, updated_at)
            VALUES (?, ?, '', ?, ?)
            """,
            (pid, pid, now, now),
        )
    _ensure_project_settings_no_init(pid)


def ensure_project_settings(project_id: str | None = None) -> None:
    pid = _project_id(project_id)
    init_kanban_db()
    _ensure_project_settings_no_init(pid)


def _ensure_project_settings_no_init(pid: str) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO kb_project_settings (
                project_id, agents_json, models_json, parameters_json, workflow_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                _json(_default_agents()),
                "{}",
                _json(_default_parameters()),
                "{}",
                now,
                now,
            ),
        )


def _normalize_column(col: dict[str, Any], index: int) -> dict[str, Any]:
    status_key = str(col.get("status_key") or col.get("key") or "").strip()
    if not status_key:
        raise ValueError("column status_key is required")
    transition_to = col.get("transition_to", [])
    if not isinstance(transition_to, list):
        raise ValueError(f"transition_to must be a list for column {status_key}")
    return {
        "status_key": status_key,
        "title": str(col.get("title") or status_key).strip(),
        "position": int(col.get("position", (index + 1) * 10)),
        "wip_limit": col.get("wip_limit"),
        "transition_to": [str(x).strip() for x in transition_to if str(x).strip()],
    }


def _columns(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
          FROM kb_columns
         WHERE project_id = ?
         ORDER BY position ASC, title ASC
        """,
        (project_id,),
    ).fetchall()
    return [_column_dict(row) for row in rows]


def _require_status(columns: list[dict[str, Any]], status_key: str) -> None:
    if status_key not in {c["status_key"] for c in columns}:
        raise ValueError(f"unknown status: {status_key}")


def _insert_event(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    project_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    payload: dict[str, Any],
) -> None:
    event_payload = payload or {}
    conn.execute(
        """
        INSERT INTO kb_events (
            id, task_id, project_id, event_type, from_status, to_status,
                payload_json, payload_summary_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            task_id,
            project_id,
            event_type,
            from_status,
            to_status,
            _json(event_payload),
            _json(_payload_summary(event_payload)),
            _now(),
        ),
    )
    append_task_event(
        project_id=project_id,
        task_id=task_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        payload=event_payload,
    )


def _column_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "status_key": row["status_key"],
        "title": row["title"],
        "position": row["position"],
        "wip_limit": row["wip_limit"],
        "transition_to": _loads(row["transition_to"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _project_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _settings_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "agents": _default_agents(),
            "parameters": _default_parameters(),
        }
    agents = _normalize_agents(_loads(row["agents_json"], _default_agents()))
    parameters = {**_default_parameters(), **_loads(row["parameters_json"], {})}
    return {
        "agents": agents,
        "parameters": parameters,
        "workflow": _loads(row["workflow_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _project_stats(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    task_rows = conn.execute(
        "SELECT status_key, archived FROM kb_tasks WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    success_statuses, failure_statuses = _workflow_terminal_statuses_from_db(conn, project_id)
    terminal_statuses = success_statuses | failure_statuses
    tasks_count = len(task_rows)
    archived_tasks = sum(1 for row in task_rows if row["archived"])
    active_tasks = sum(1 for row in task_rows if not row["archived"] and row["status_key"] not in terminal_statuses)
    done_tasks = sum(1 for row in task_rows if not row["archived"] and row["status_key"] in success_statuses)
    failed_tasks = sum(1 for row in task_rows if not row["archived"] and row["status_key"] in failure_statuses)

    request_count = 0
    token_row = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "duration_ms": 0,
    }
    if _table_exists(conn, "api_requests"):
        request_count = conn.execute(
            "SELECT COUNT(*) AS count FROM api_requests WHERE project_id = ?",
            (project_id,),
        ).fetchone()["count"]
    if _table_exists(conn, "llm_usage"):
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS calls,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                SUM(COALESCE(duration_ms, 0)) AS duration_ms
              FROM llm_usage
             WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        token_row = dict(row)
    return {
        "tasks": tasks_count,
        "archived_tasks": archived_tasks,
        "active_tasks": active_tasks,
        "done_tasks": done_tasks,
        "failed_tasks": failed_tasks,
        "request_count": request_count or 0,
        "llm_calls": token_row.get("calls") or 0,
        "input_tokens": token_row.get("input_tokens") or 0,
        "output_tokens": token_row.get("output_tokens") or 0,
        "total_tokens": token_row.get("total_tokens") or 0,
        "cached_input_tokens": token_row.get("cached_input_tokens") or 0,
        "duration_ms": token_row.get("duration_ms") or 0,
    }


def _empty_project_stats() -> dict[str, Any]:
    return {
        "tasks": 0,
        "archived_tasks": 0,
        "active_tasks": 0,
        "done_tasks": 0,
        "failed_tasks": 0,
        "request_count": 0,
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "duration_ms": 0,
    }


def _project_stats_many(conn: sqlite3.Connection, project_ids: list[str]) -> dict[str, dict[str, Any]]:
    stats = {project_id: _empty_project_stats() for project_id in project_ids}
    if not project_ids:
        return stats

    terminal_by_project = {
        project_id: _workflow_terminal_statuses_from_db(conn, project_id)
        for project_id in project_ids
    }
    placeholders = ",".join("?" for _ in project_ids)
    task_rows = conn.execute(
        f"""
        SELECT project_id, status_key, archived, COUNT(*) AS count
          FROM kb_tasks
         WHERE project_id IN ({placeholders})
         GROUP BY project_id, status_key, archived
        """,
        project_ids,
    ).fetchall()
    for row in task_rows:
        project_id = row["project_id"]
        count = int(row["count"] or 0)
        archived = bool(row["archived"])
        status_key = str(row["status_key"] or "")
        success_statuses, failure_statuses = terminal_by_project.get(project_id, (set(), set()))
        terminal_statuses = success_statuses | failure_statuses
        item = stats.setdefault(project_id, _empty_project_stats())
        item["tasks"] += count
        if archived:
            item["archived_tasks"] += count
            continue
        if status_key not in terminal_statuses:
            item["active_tasks"] += count
        if status_key in success_statuses:
            item["done_tasks"] += count
        if status_key in failure_statuses:
            item["failed_tasks"] += count

    if _table_exists(conn, "api_requests"):
        for row in conn.execute(
            f"""
            SELECT project_id, COUNT(*) AS count
              FROM api_requests
             WHERE project_id IN ({placeholders})
             GROUP BY project_id
            """,
            project_ids,
        ).fetchall():
            stats.setdefault(row["project_id"], _empty_project_stats())["request_count"] = int(row["count"] or 0)

    if _table_exists(conn, "llm_usage"):
        for row in conn.execute(
            f"""
            SELECT
                project_id,
                COUNT(*) AS calls,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                SUM(COALESCE(duration_ms, 0)) AS duration_ms
              FROM llm_usage
             WHERE project_id IN ({placeholders})
             GROUP BY project_id
            """,
            project_ids,
        ).fetchall():
            item = stats.setdefault(row["project_id"], _empty_project_stats())
            item["llm_calls"] = row["calls"] or 0
            item["input_tokens"] = row["input_tokens"] or 0
            item["output_tokens"] = row["output_tokens"] or 0
            item["total_tokens"] = row["total_tokens"] or 0
            item["cached_input_tokens"] = row["cached_input_tokens"] or 0
            item["duration_ms"] = row["duration_ms"] or 0
    return stats


def _workflow_terminal_statuses_from_db(conn: sqlite3.Connection, project_id: str) -> tuple[set[str], set[str]]:
    row = conn.execute("SELECT workflow_json FROM kb_project_settings WHERE project_id = ?", (project_id,)).fetchone()
    workflow = _loads(row["workflow_json"], {}) if row is not None else {}
    try:
        definition = workflow_from_dict(workflow if isinstance(workflow, dict) else {})
        return definition.terminal_statuses("success"), definition.terminal_statuses("failure")
    except Exception as exc:  # noqa: BLE001
        _log.debug("project terminal status lookup failed project_id=%s error=%s", project_id, exc)
        return set(), set()


def _default_agents() -> dict[str, Any]:
    return {
        "context-indexer": {"enabled": True, "model_route": "default"},
        "project-agent": {"enabled": True, "model_route": "default"},
    }


def _normalize_agents(value: Any) -> dict[str, Any]:
    agents = {**_default_agents()}
    if isinstance(value, dict):
        for name, raw in value.items():
            if isinstance(raw, dict):
                agents[name] = {**agents.get(name, {}), **raw}
    return agents


def _default_parameters() -> dict[str, Any]:
    return {
        "thinking_mode": "balanced",
        "effort_level": "max",
        "temperature": 0.2,
        "max_tokens": 4096,
        "workflow_max_total_runs": 512,
        "workflow_max_rework_runs": 128,
        "agent_tool_max_rounds": 128,
    }


def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "title": row["title"],
        "description": row["description"],
        "status_key": row["status_key"],
        "priority": row["priority"],
        "metadata": _loads(row["metadata_json"], {}),
        "archived": bool(row["archived"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _task_record_response(task_id: str) -> dict[str, Any]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(f"task not found: {task_id}")
    return {"ok": True, "task": _task_dict(row)}


def _event_dict(row: sqlite3.Row, *, payload_mode: str = "full") -> dict[str, Any]:
    keys = set(row.keys())
    event = {
        "id": row["id"],
        "task_id": row["task_id"],
        "project_id": row["project_id"],
        "event_type": row["event_type"],
        "from_status": row["from_status"],
        "to_status": row["to_status"],
        "payload": _event_payload(
            row["payload_json"],
            payload_mode=payload_mode,
            payload_size=row["payload_size"] if "payload_size" in keys else None,
        ),
        "created_at": row["created_at"],
    }
    if "task_title" in keys:
        event["task_title"] = row["task_title"]
    if "task_status_key" in keys:
        event["task_status_key"] = row["task_status_key"]
    return event


def _event_payload(payload_json: str | None, *, payload_mode: str = "full", payload_size: int | None = None) -> Any:
    text = payload_json or ""
    if payload_mode != "summary":
        return _loads(text, {})
    size = payload_size if payload_size is not None else len(text)
    if size > len(text):
        return {
            "_summary": True,
            "_truncated": True,
            "_bytes": size,
            "preview": text,
        }
    if len(text) > 8000:
        return {"_summary": True, "_truncated": True, "_bytes": len(text), "preview": text[:2000]}
    return _loads(text, {})


def _payload_summary(value: Any, *, max_string: int = 500, max_items: int = 12, depth: int = 0) -> Any:
    if depth > 3:
        return _short_scalar(value, max_string=max_string)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                out["_truncated_keys"] = max(0, len(value) - max_items)
                break
            out[str(key)] = _payload_summary(item, max_string=max_string, max_items=max_items, depth=depth + 1)
        return out
    if isinstance(value, list):
        out = [_payload_summary(item, max_string=max_string, max_items=max_items, depth=depth + 1) for item in value[:max_items]]
        if len(value) > max_items:
            out.append({"_truncated_items": len(value) - max_items})
        return out
    return _short_scalar(value, max_string=max_string)


def _short_scalar(value: Any, *, max_string: int) -> Any:
    if isinstance(value, str) and len(value) > max_string:
        return {"_summary": True, "_truncated": True, "_chars": len(value), "preview": value[:max_string]}
    return value


def _artifact_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "project_id": row["project_id"],
        "artifact_type": row["artifact_type"],
        "path": row["path"],
        "payload": _loads(row["payload_json"], {}),
        "created_at": row["created_at"],
    }


def _conversation_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "project_id": row["project_id"],
        "state": row["state"],
        "active_column": row["active_column"],
        "waiting_for": row["waiting_for"],
        "summary": row["summary"],
        "summary_version": row["summary_version"],
        "token_estimate": row["token_estimate"],
        "metadata": _loads(row["metadata_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "task_id": row["task_id"],
        "project_id": row["project_id"],
        "sequence": row["sequence"],
        "role": row["role"],
        "message_type": row["message_type"],
        "content": row["content"],
        "metadata": _loads(row["metadata_json"], {}),
        "compressed": bool(row["compressed"]),
        "created_at": row["created_at"],
    }


def _column_run_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "project_id": row["project_id"],
        "status_key": row["status_key"],
        "agent": row["agent"],
        "run_no": row["run_no"],
        "state": row["state"],
        "checkpoint": _loads(row["checkpoint_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _revision_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "project_id": row["project_id"],
        "sequence": row["sequence"],
        "parent_revision_id": row["parent_revision_id"],
        "state": row["state"],
        "summary": row["summary"],
        "ops": _loads(row["ops_json"], []),
        "patch_ops": _loads(row["patch_ops_json"], []),
        "changed_paths": _loads(row["changed_paths_json"], []),
        "verification": _loads(row["verification_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _project_id(project_id: str | None) -> str:
    return (project_id or "").strip() or DEFAULT_PROJECT_ID


def _migrate_legacy_tables(conn: sqlite3.Connection) -> None:
    mappings = {
        "kanban_columns": T_COLUMNS,
        "kanban_tasks": T_TASKS,
        "kanban_events": T_EVENTS,
        "kanban_artifacts": T_ARTIFACTS,
    }
    for old, new in mappings.items():
        if _table_exists(conn, old) and not _table_exists(conn, new):
            conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
            _log.debug("renamed legacy table %s -> %s", old, new)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")


def definition_to_dict(definition: Any) -> dict[str, Any]:
    return {
        "name": definition.name,
        "version": definition.version,
        "workflow_type": getattr(definition, "workflow_type", ""),
        "requires_apply": bool(getattr(definition, "requires_apply", False)),
        "parameters": getattr(definition, "parameters", {}) or {},
        "columns": [
            {
                "status_key": col.status_key,
                "title": col.title,
                "position": col.position,
                "transition_to": col.transition_to,
                "kind": col.kind,
                "agent": col.agent,
                "runtime": col.runtime,
                "job_template": col.job_template,
                "input_artifacts": col.input_artifacts or [],
                "output_artifact": col.output_artifact,
                "output_contract": col.output_contract,
                "success_action": col.success_action,
                "failure_actions": col.failure_actions or [],
                "context_policy": col.context_policy or {},
                "retry_policy": col.retry_policy or {},
                "terminal": col.terminal,
                "terminal_kind": col.terminal_kind,
            }
            for col in definition.columns
        ],
        "actions": definition.actions,
    }


def _conn() -> sqlite3.Connection:
    init_kanban_db()
    conn = _connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _db_path() -> Path:
    configured = Path(str(settings().devwerk_db_path))
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[2] / configured


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=30)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default

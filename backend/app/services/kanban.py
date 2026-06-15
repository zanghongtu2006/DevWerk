from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

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
DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {
        "status_key": "draft",
        "title": "Draft",
        "position": 10,
        "transition_to": ["context_indexed", "failed"],
    },
    {
        "status_key": "context_indexed",
        "title": "Context Indexed",
        "position": 20,
        "transition_to": ["planned", "failed"],
    },
    {
        "status_key": "planned",
        "title": "Planned",
        "position": 30,
        "transition_to": ["coding", "draft", "failed"],
    },
    {
        "status_key": "coding",
        "title": "Coding",
        "position": 40,
        "transition_to": ["ready_to_apply", "planned", "failed"],
    },
    {
        "status_key": "ready_to_apply",
        "title": "Ready To Apply",
        "position": 50,
        "transition_to": ["applied", "coding", "failed"],
    },
    {
        "status_key": "applied",
        "title": "Applied",
        "position": 60,
        "transition_to": ["verified", "coding", "planned", "failed"],
    },
    {
        "status_key": "verified",
        "title": "Verified",
        "position": 70,
        "transition_to": ["done", "applied", "failed"],
    },
    {
        "status_key": "done",
        "title": "Done",
        "position": 80,
        "transition_to": [],
    },
    {
        "status_key": "failed",
        "title": "Failed",
        "position": 90,
        "transition_to": ["draft"],
    },
]


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

            CREATE TABLE IF NOT EXISTS kb_events (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_kb_events_task_time
                ON kb_events(task_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_kb_events_project_time
                ON kb_events(project_id, created_at);

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
            """
        )
    _initialized = True
    _log.debug("kanban db initialized path=%s", path)


def get_board(project_id: str | None = None) -> dict[str, Any]:
    pid = _project_id(project_id)
    ensure_default_columns(pid)
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
        for project in projects:
            project["stats"] = _project_stats(conn, project["id"])
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
    ensure_default_columns(pid)
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
) -> dict[str, Any]:
    pid = _project_id(project_id)
    ensure_project(pid)
    ensure_project_settings(pid)
    now = _now()
    updates = []
    params: list[Any] = []
    if agents is not None:
        updates.append("agents_json = ?")
        params.append(_json(agents))
    if parameters is not None:
        updates.append("parameters_json = ?")
        params.append(_json(parameters))
    if updates:
        updates.append("updated_at = ?")
        params.append(now)
        params.append(pid)
        with _conn() as conn:
            conn.execute(f"UPDATE kb_project_settings SET {', '.join(updates)} WHERE project_id = ?", params)
    return get_project_settings(pid)


def list_columns(project_id: str | None = None) -> list[dict[str, Any]]:
    pid = _project_id(project_id)
    ensure_default_columns(pid)
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
    ensure_default_columns(pid)
    with _conn() as conn:
        columns = _columns(conn, pid)
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
    task = _task_dict(row)
    task["events"] = [_event_dict(e) for e in events]
    task["artifacts"] = [_artifact_dict(a) for a in artifacts]
    return {"ok": True, "task": task}


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
    return get_task(task_id)


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
    return get_task(task_id)


def list_events(
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    pid = _project_id(project_id)
    max_rows = max(1, min(int(limit or 200), 1000))
    where = ["e.project_id = ?"]
    params: list[Any] = [pid]
    if task_id:
        where.append("e.task_id = ?")
        params.append(task_id)
    params.append(max_rows)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT
                e.*,
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
        "events": [_event_dict(row) for row in rows],
    }


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
    return get_task(task_id)


def ensure_default_columns(project_id: str | None = None) -> None:
    pid = _project_id(project_id)
    init_kanban_db()
    ensure_project(pid)
    with _conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM kb_columns WHERE project_id = ?",
            (pid,),
        ).fetchone()["count"]
        if count:
            _ensure_workflow_columns(conn, pid)
            return
        now = _now()
        for col in DEFAULT_COLUMNS:
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
                    _json(col["transition_to"]),
                    now,
                    now,
                ),
            )
    _log.debug("kanban default columns created project_id=%s", pid)


def _ensure_workflow_columns(conn: sqlite3.Connection, pid: str) -> None:
    now = _now()
    for col in DEFAULT_COLUMNS:
        conn.execute(
            """
            INSERT INTO kb_columns (
                id, project_id, status_key, title, position, wip_limit,
                transition_to, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, status_key) DO UPDATE SET
                title = excluded.title,
                position = excluded.position,
                transition_to = excluded.transition_to,
                updated_at = excluded.updated_at
            """,
            (
                str(uuid.uuid4()),
                pid,
                col["status_key"],
                col["title"],
                col["position"],
                col.get("wip_limit"),
                _json(col["transition_to"]),
                now,
                now,
            ),
        )


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
                project_id, agents_json, models_json, parameters_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                _json(_default_agents()),
                "{}",
                _json(_default_parameters()),
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
    conn.execute(
        """
        INSERT INTO kb_events (
            id, task_id, project_id, event_type, from_status, to_status,
            payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            task_id,
            project_id,
            event_type,
            from_status,
            to_status,
            _json(payload),
            _now(),
        ),
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
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _project_stats(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    task_row = conn.execute(
        """
        SELECT
            COUNT(*) AS tasks,
            SUM(CASE WHEN archived = 1 THEN 1 ELSE 0 END) AS archived_tasks
          FROM kb_tasks
         WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()

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
        "tasks": task_row["tasks"] or 0,
        "archived_tasks": task_row["archived_tasks"] or 0,
        "request_count": request_count or 0,
        "llm_calls": token_row.get("calls") or 0,
        "input_tokens": token_row.get("input_tokens") or 0,
        "output_tokens": token_row.get("output_tokens") or 0,
        "total_tokens": token_row.get("total_tokens") or 0,
        "cached_input_tokens": token_row.get("cached_input_tokens") or 0,
        "duration_ms": token_row.get("duration_ms") or 0,
    }


def _default_agents() -> dict[str, Any]:
    return {
        "coder": {"enabled": True, "model_ref": "minimax/m3"},
        "planner": {"enabled": True, "model_ref": "deepseek/deepseek-chat"},
        "executor": {"enabled": True, "model_ref": "minimax/m3"},
    }


def _normalize_agents(value: Any) -> dict[str, Any]:
    agents = {**_default_agents()}
    if isinstance(value, dict):
        for name, raw in value.items():
            if isinstance(raw, dict):
                item = {**agents.get(name, {}), **raw}
                legacy_profile = item.pop("model_profile", None)
                if item.get("model_ref") in {None, "", "default", "DEVWERK_DEFAULT_API"}:
                    item["model_ref"] = "minimax/m3" if legacy_profile != "deepseek" else "deepseek/deepseek-chat"
                agents[name] = item
    return agents


def _default_parameters() -> dict[str, Any]:
    return {
        "thinking_mode": "balanced",
        "effort_level": "max",
        "temperature": 0.2,
        "max_tokens": 4096,
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


def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
    event = {
        "id": row["id"],
        "task_id": row["task_id"],
        "project_id": row["project_id"],
        "event_type": row["event_type"],
        "from_status": row["from_status"],
        "to_status": row["to_status"],
        "payload": _loads(row["payload_json"], {}),
        "created_at": row["created_at"],
    }
    keys = set(row.keys())
    if "task_title" in keys:
        event["task_title"] = row["task_title"]
    if "task_status_key" in keys:
        event["task_status_key"] = row["task_status_key"]
    return event


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

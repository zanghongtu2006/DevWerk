from __future__ import annotations

import contextvars
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

_log = logging.getLogger("devwerk.usage")
_current_request: contextvars.ContextVar["UsageRequestContext | None"] = contextvars.ContextVar(
    "devwerk_usage_request",
    default=None,
)
_initialized = False


@dataclass(frozen=True)
class UsageRequestContext:
    request_id: str
    project_id: str
    task_id: str | None
    route: str
    action: str
    started_at: str
    started_monotonic: float


def init_usage_db() -> None:
    global _initialized
    if not _enabled():
        _log.debug("usage tracking disabled")
        return
    if _initialized:
        return

    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS api_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                task_id TEXT,
                route TEXT NOT NULL,
                action TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                duration_ms INTEGER,
                status_code INTEGER,
                success INTEGER,
                error_type TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_api_requests_project_time
                ON api_requests(project_id, started_at);

            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                project_id TEXT NOT NULL,
                task_id TEXT,
                agent_name TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                cached_input_tokens INTEGER,
                cache_creation_input_tokens INTEGER,
                cached_output_tokens INTEGER,
                input_cache_hit_rate REAL,
                output_cache_hit_rate REAL,
                duration_ms INTEGER,
                success INTEGER NOT NULL,
                error_type TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_llm_usage_project_time
                ON llm_usage(project_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_llm_usage_request
                ON llm_usage(request_id);
            """
        )
        _ensure_column(conn, "api_requests", "task_id", "TEXT")
        _ensure_column(conn, "llm_usage", "task_id", "TEXT")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_api_requests_task_time
                ON api_requests(task_id, started_at);
            CREATE INDEX IF NOT EXISTS idx_llm_usage_task_time
                ON llm_usage(task_id, created_at);
            """
        )
    _initialized = True
    _log.debug("usage db initialized path=%s", path)


def start_request(
    project_id: str | None,
    route: str,
    action: str,
    task_id: str | None = None,
) -> UsageRequestContext:
    normalized_project_id = (project_id or "").strip() or str(uuid.uuid4())
    normalized_task_id = (task_id or "").strip() or None
    now = _now()
    ctx = UsageRequestContext(
        request_id=str(uuid.uuid4()),
        project_id=normalized_project_id,
        task_id=normalized_task_id,
        route=route,
        action=action,
        started_at=now,
        started_monotonic=time.monotonic(),
    )
    _current_request.set(ctx)

    if not _enabled():
        return ctx

    try:
        init_usage_db()
        with _connect(_db_path()) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO api_requests (
                    request_id, project_id, task_id, route, action, started_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ctx.request_id, ctx.project_id, ctx.task_id, ctx.route, ctx.action, ctx.started_at, now),
            )
        _log.debug(
            "usage request started request_id=%s project_id=%s task_id=%s route=%s action=%s",
            ctx.request_id,
            ctx.project_id,
            ctx.task_id,
            ctx.route,
            ctx.action,
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("failed to start usage request tracking: %s", exc)
    return ctx


def finish_request(
    ctx: UsageRequestContext | None,
    *,
    status_code: int | None,
    success: bool,
    error_type: str | None = None,
) -> None:
    if ctx is None:
        return
    if not _enabled():
        return

    completed_at = _now()
    duration_ms = int((time.monotonic() - ctx.started_monotonic) * 1000)
    try:
        with _connect(_db_path()) as conn:
            conn.execute(
                """
                UPDATE api_requests
                   SET completed_at = ?,
                       duration_ms = ?,
                       status_code = ?,
                       success = ?,
                       error_type = ?
                 WHERE request_id = ?
                """,
                (
                    completed_at,
                    duration_ms,
                    status_code,
                    1 if success else 0,
                    error_type,
                    ctx.request_id,
                ),
            )
        _log.debug(
            "usage request finished request_id=%s project_id=%s status=%s success=%s duration_ms=%s",
            ctx.request_id,
            ctx.project_id,
            status_code,
            success,
            duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("failed to finish usage request tracking: %s", exc)


def clear_request() -> None:
    _current_request.set(None)


def current_request() -> UsageRequestContext | None:
    return _current_request.get()


def record_llm_usage(
    *,
    agent_name: str,
    provider: str,
    model: str,
    usage: dict[str, Any] | None,
    duration_ms: int,
    success: bool,
    error_type: str | None = None,
) -> None:
    if not _enabled():
        return

    ctx = current_request()
    project_id = ctx.project_id if ctx else str(uuid.uuid4())
    task_id = ctx.task_id if ctx else None
    request_id = ctx.request_id if ctx else None
    normalized = _normalize_usage(usage or {})

    try:
        init_usage_db()
        with _connect(_db_path()) as conn:
            conn.execute(
                """
                INSERT INTO llm_usage (
                    request_id, project_id, task_id, agent_name, provider, model,
                    input_tokens, output_tokens, total_tokens,
                    cached_input_tokens, cache_creation_input_tokens, cached_output_tokens,
                    input_cache_hit_rate, output_cache_hit_rate,
                    duration_ms, success, error_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    project_id,
                    task_id,
                    agent_name,
                    provider,
                    model,
                    normalized.get("input_tokens"),
                    normalized.get("output_tokens"),
                    normalized.get("total_tokens"),
                    normalized.get("cached_input_tokens"),
                    normalized.get("cache_creation_input_tokens"),
                    normalized.get("cached_output_tokens"),
                    normalized.get("input_cache_hit_rate"),
                    normalized.get("output_cache_hit_rate"),
                    duration_ms,
                    1 if success else 0,
                    error_type,
                    _now(),
                ),
            )
        _log.debug(
            "llm usage recorded request_id=%s project_id=%s task_id=%s agent=%s provider=%s model=%s usage=%s duration_ms=%s success=%s error=%s",
            request_id,
            project_id,
            task_id,
            agent_name,
            provider,
            model,
            normalized,
            duration_ms,
            success,
            error_type,
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("failed to record llm usage: %s", exc)


def usage_summary(
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    if not _enabled():
        return {"ok": True, "enabled": False, "projects": [], "request_count": 0}

    init_usage_db()
    filters = []
    params: list[Any] = []
    if project_id:
        filters.append("project_id = ?")
        params.append(project_id)
    if task_id:
        filters.append("task_id = ?")
        params.append(task_id)
    if start:
        filters.append("created_at >= ?")
        params.append(start)
    if end:
        filters.append("created_at <= ?")
        params.append(end)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    with _connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT
                project_id,
                task_id,
                agent_name,
                provider,
                model,
                COUNT(*) AS calls,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                SUM(COALESCE(cache_creation_input_tokens, 0)) AS cache_creation_input_tokens,
                AVG(input_cache_hit_rate) AS avg_input_cache_hit_rate,
                SUM(COALESCE(duration_ms, 0)) AS duration_ms,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successful_calls
            FROM llm_usage
            {where}
            GROUP BY project_id, task_id, agent_name, provider, model
            ORDER BY project_id, task_id, agent_name, provider, model
            """,
            params,
        ).fetchall()
        project_rows = conn.execute(
            f"""
            SELECT
                project_id,
                COUNT(*) AS calls,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                SUM(COALESCE(duration_ms, 0)) AS duration_ms,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successful_calls
            FROM llm_usage
            {where}
            GROUP BY project_id
            ORDER BY total_tokens DESC, project_id
            """,
            params,
        ).fetchall()
        task_rows = conn.execute(
            f"""
            SELECT
                project_id,
                task_id,
                COUNT(*) AS calls,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                SUM(COALESCE(duration_ms, 0)) AS duration_ms,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successful_calls
            FROM llm_usage
            {where}
            GROUP BY project_id, task_id
            ORDER BY total_tokens DESC, project_id, task_id
            """,
            params,
        ).fetchall()
        agent_rows = conn.execute(
            f"""
            SELECT
                agent_name,
                provider,
                model,
                COUNT(*) AS calls,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                SUM(COALESCE(total_tokens, 0)) AS total_tokens,
                SUM(COALESCE(duration_ms, 0)) AS duration_ms,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successful_calls
            FROM llm_usage
            {where}
            GROUP BY agent_name, provider, model
            ORDER BY total_tokens DESC, agent_name, provider, model
            """,
            params,
        ).fetchall()

        request_filters = []
        request_params: list[Any] = []
        if project_id:
            request_filters.append("project_id = ?")
            request_params.append(project_id)
        if task_id:
            request_filters.append("task_id = ?")
            request_params.append(task_id)
        if start:
            request_filters.append("started_at >= ?")
            request_params.append(start)
        if end:
            request_filters.append("started_at <= ?")
            request_params.append(end)
        request_where = f"WHERE {' AND '.join(request_filters)}" if request_filters else ""
        request_count = conn.execute(
            f"SELECT COUNT(*) AS count FROM api_requests {request_where}",
            request_params,
        ).fetchone()["count"]

    row_dicts = [dict(row) for row in rows]
    totals = _sum_usage_rows(row_dicts)
    totals["request_count"] = request_count

    return {
        "ok": True,
        "enabled": True,
        "scope": {
            "project_id": project_id,
            "task_id": task_id,
            "start": start,
            "end": end,
        },
        "totals": totals,
        "request_count": request_count,
        "projects": row_dicts,
        "by_project": [dict(row) for row in project_rows],
        "by_task": [dict(row) for row in task_rows],
        "by_agent": [dict(row) for row in agent_rows],
    }


def _sum_usage_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "duration_ms": 0,
        "successful_calls": 0,
    }
    for row in rows:
        for key in totals:
            totals[key] += int(row.get(key) or 0)
    return totals


def _normalize_usage(usage: dict[str, Any]) -> dict[str, Any]:
    input_tokens = _int_or_none(usage.get("input_tokens"))
    output_tokens = _int_or_none(usage.get("output_tokens"))
    total_tokens = _int_or_none(usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    cached_input_tokens = _int_or_none(usage.get("cached_input_tokens"))
    cache_creation_input_tokens = _int_or_none(usage.get("cache_creation_input_tokens"))
    cached_output_tokens = _int_or_none(usage.get("cached_output_tokens"))

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cached_output_tokens": cached_output_tokens,
        "input_cache_hit_rate": _rate(cached_input_tokens, input_tokens),
        "output_cache_hit_rate": _rate(cached_output_tokens, output_tokens),
    }


def _enabled() -> bool:
    return bool(getattr(settings(), "devwerk_usage_tracking", True))


def _db_path() -> Path:
    configured = Path(str(settings().devwerk_db_path))
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[2] / configured


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=30)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rate(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    value = float(numerator) / float(denominator)
    return max(0.0, min(1.0, value))

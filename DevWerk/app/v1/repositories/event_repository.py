from __future__ import annotations

from typing import Any

from app.v1.repositories.base import StoreHost
from app.v1.storage_support import utcnow

class EventRepository:
    def __init__(self, store: StoreHost):
        self.store = store

    def events(self, project_id: str | None = None, task_id: str | None = None, after: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.store.policy.service_limits.detail_page_size
        where, values = ["id>?"], [after]
        if project_id:
            where.append("project_id=?")
            values.append(project_id)
        if task_id:
            where.append("task_id=?")
            values.append(task_id)
        values.append(min(max(limit, 1), self.store.policy.service_limits.max_page_size))
        with self.store.connect() as db:
            rows = db.execute(f"SELECT * FROM v1_events WHERE {' AND '.join(where)} ORDER BY id LIMIT ?", values).fetchall()
        return [self.store._decode(dict(row), "data_json") for row in rows]  # type: ignore[misc]

    def recent_events(self, project_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.store.policy.service_limits.default_page_size
        bounded_limit = min(
            max(limit, 1),
            self.store.policy.service_limits.max_page_size,
        )
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM (SELECT * FROM v1_events WHERE project_id=? "
                "ORDER BY id DESC LIMIT ?) ORDER BY id",
                (project_id, bounded_limit),
            ).fetchall()
        return [self.store._decode(dict(row), "data_json") for row in rows]  # type: ignore[misc]


    def record_external_event(self, project_id: str, event_type: str, correlation_key: str, output: dict[str, Any]) -> dict[str, Any]:
        self.store.get_project(project_id)
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            self.store._event(db, project_id, None, None, event_type, {"correlation_key": correlation_key, "output": output, "source": "automation_api"})
            event_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
        return {"id": event_id, "project_id": project_id, "type": event_type, "correlation_key": correlation_key, "output": output, "created_at": now}


    def correlated_event(self, project_id: str, event_type: str, correlation_key: str, after: str) -> dict[str, Any] | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM v1_events WHERE project_id=? AND type=? AND created_at>=? AND json_extract(data_json,'$.correlation_key')=? ORDER BY id LIMIT 1",
                (project_id, event_type, after, correlation_key),
            ).fetchone()
        return self.store._decode(dict(row), "data_json") if row else None

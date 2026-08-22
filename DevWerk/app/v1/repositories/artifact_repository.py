from __future__ import annotations

import json
from typing import Any

from app.v1.repositories.base import StoreHost
from app.v1.storage_support import new_id, utcnow

class ArtifactRepository:
    def __init__(self, store: StoreHost):
        self.store = store

    def register_artifact(self, project_id: str, task_id: str | None, run_id: str | None, kind: str, path: str, sha256: str, size: int, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        artifact_id, now = new_id("art"), utcnow()
        with self.store.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_artifacts(id,project_id,task_id,run_id,kind,path,sha256,size,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET id=excluded.id,task_id=excluded.task_id,run_id=excluded.run_id,kind=excluded.kind,sha256=excluded.sha256,size=excluded.size,meta_json=excluded.meta_json,created_at=excluded.created_at",
                (artifact_id, project_id, task_id, run_id, kind, path, sha256, size, json.dumps(meta or {}, ensure_ascii=False), now),
            )
            self.store._event(db, project_id, task_id, run_id, "artifact.written", {"path": path, "kind": kind, "size": size})
        return {"id": artifact_id, "path": path, "kind": kind, "size": size, "sha256": sha256}


    def artifacts(self, project_id: str, task_id: str, limit: int | None = None, after: str = "") -> list[dict[str, Any]]:
        limit = limit or self.store.policy.service_limits.detail_page_size
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM v1_artifacts WHERE project_id=? AND task_id=? AND created_at>? ORDER BY created_at LIMIT ?", (project_id, task_id, after, min(max(limit, 1), self.store.policy.service_limits.max_page_size))).fetchall()
        return [self.store._decode(dict(row), "meta_json") for row in rows]  # type: ignore[misc]

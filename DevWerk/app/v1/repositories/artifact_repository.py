from __future__ import annotations

import json
import hashlib
from app.v1.files import ProjectFiles
from typing import Any

from app.v1.repositories.base import StoreHost
from app.v1.storage_support import new_id, utcnow
from app.v1.services.dependency_resolver import dependency_tasks

class ArtifactRepository:
    def __init__(self, store: StoreHost):
        self.store = store

    def register_artifact(self, project_id: str, task_id: str | None, run_id: str | None, kind: str, path: str, sha256: str, size: int, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        artifact_id, now = new_id("art"), utcnow()
        files = ProjectFiles(self.store.get_project(project_id)["base_dir"], self.store.policy)
        target = files.resolve(path)
        content = target.read_bytes() if target.is_file() else None
        if content is not None and hashlib.sha256(content).hexdigest() != sha256:
            raise ValueError("Artifact changed before its immutable snapshot was registered")
        with self.store.tx(immediate=True) as db:
            if content is not None:
                db.execute("INSERT OR IGNORE INTO v1_artifact_contents(sha256,content) VALUES(?,?)", (sha256, content))
            db.execute(
                "INSERT INTO v1_artifacts(id,project_id,task_id,run_id,kind,path,sha256,size,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET id=excluded.id,task_id=excluded.task_id,run_id=excluded.run_id,kind=excluded.kind,sha256=excluded.sha256,size=excluded.size,meta_json=excluded.meta_json,created_at=excluded.created_at",
                (artifact_id, project_id, task_id, run_id, kind, path, sha256, size, json.dumps(meta or {}, ensure_ascii=False), now),
            )
            self.store._event(db, project_id, task_id, run_id, "artifact.written", {"path": path, "kind": kind, "size": size})
        return {"id": artifact_id, "path": path, "kind": kind, "size": size, "sha256": sha256}

    def snapshot_text(self, artifact):
        with self.store.connect() as db:
            row = db.execute("SELECT content FROM v1_artifact_contents WHERE sha256=?", (artifact.get("sha256"),)).fetchone()
        return bytes(row[0]).decode("utf-8") if row else None


    def artifacts(self, project_id: str, task_id: str, limit: int | None = None, after: str = "") -> list[dict[str, Any]]:
        limit = limit or self.store.policy.service_limits.detail_page_size
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM v1_artifact_versions WHERE project_id=? AND task_id=? AND created_at>? ORDER BY created_at LIMIT ?", (project_id, task_id, after, min(max(limit, 1), self.store.policy.service_limits.max_page_size))).fetchall()
        return [self.store._decode(dict(row), "meta_json") for row in rows]  # type: ignore[misc]

    def accepted_dependency_artifacts(
        self,
        project_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        """Return immutable versions owned by canonical transitive done dependencies."""
        with self.store.connect() as db:
            rows = []
            for task in dependency_tasks(db, project_id, task_id, transitive=True):
                if task["status"] == "done":
                    rows.extend(db.execute(
                        "SELECT * FROM v1_artifact_versions WHERE project_id=? AND task_id=? ORDER BY created_at DESC,rowid DESC",
                        (project_id, task["id"]),
                    ).fetchall())
        return [self.store._decode(dict(row), "meta_json") for row in rows]

    def current_task_artifacts(
        self,
        project_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_artifact_versions WHERE project_id=? AND task_id=? ORDER BY created_at DESC,rowid DESC",
                (project_id, task_id),
            ).fetchall()
        return [
            self.store._decode(dict(row), "meta_json")
            for row in rows
        ]  # type: ignore[misc]

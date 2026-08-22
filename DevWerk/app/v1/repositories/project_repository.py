from __future__ import annotations

from pathlib import Path
from typing import Any

from app.v1.repositories.base import StoreHost
from app.v1.storage_support import new_id, utcnow

class ProjectRepository:
    def __init__(self, store: StoreHost):
        self.store = store

    def create_project(self, name: str, description: str, base_dir: str, agent_instruction: str = "") -> dict[str, Any]:
        project_id, now = new_id("prj"), utcnow()
        canonical_base_dir = str(Path(base_dir).expanduser().resolve())
        Path(canonical_base_dir).mkdir(parents=True, exist_ok=True)
        with self.store.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_projects(id,name,description,base_dir,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (project_id, name, description, canonical_base_dir, now, now),
            )
            db.execute(
                "INSERT INTO v1_conversation_agents(project_id,logical_id,state,instruction,instruction_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (project_id, new_id("ca"), "idle", agent_instruction, 1, now, now),
            )
            self.store._event(db, project_id, None, None, "project.created", {"name": name})
            self.store._refresh_projection(db, project_id)
        return self.get_project(project_id)


    def conversation_agent(self, project_id: str) -> dict[str, Any]:
        with self.store.connect() as db:
            row = self.store._dict(db.execute(
                "SELECT * FROM v1_conversation_agents WHERE project_id=?", (project_id,)
            ).fetchone())
        if not row:
            raise KeyError(project_id)
        return row


    def update_conversation_instruction(self, project_id: str, instruction: str) -> dict[str, Any]:
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            changed = db.execute(
                "UPDATE v1_conversation_agents SET instruction=?,instruction_revision=instruction_revision+1,updated_at=? WHERE project_id=?",
                (instruction, now, project_id),
            ).rowcount
            if changed != 1:
                raise KeyError(project_id)
            self.store._event(db, project_id, None, None, "agent.instruction_updated", {})
        return self.conversation_agent(project_id)


    def list_projects(self) -> list[dict[str, Any]]:
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM v1_projects ORDER BY created_at DESC")]


    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.store.connect() as db:
            row = self.store._dict(db.execute("SELECT * FROM v1_projects WHERE id=?", (project_id,)).fetchone())
        if not row:
            raise KeyError(project_id)
        return row

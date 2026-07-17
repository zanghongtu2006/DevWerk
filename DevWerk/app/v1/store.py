from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.v1.domain import WorkflowDefinition
from app.v1.contracts import check_schema
from app.v1.files import ProjectFiles


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class V1Store:
    def __init__(self, db_path: str):
        self.path = Path(db_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.payload_root = self.path.parent / "payloads"
        self.payload_root.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def tx(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init_schema(self) -> None:
        with self._schema_lock, self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS v1_projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    base_dir TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_v1_projects_base_dir_nocase
                    ON v1_projects(base_dir COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS v1_conversation_agents (
                    project_id TEXT PRIMARY KEY, logical_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL, instruction TEXT NOT NULL DEFAULT '',
                    instruction_revision INTEGER NOT NULL DEFAULT 1,
                    last_observed_event_id INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS v1_conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_conversation_project_id
                    ON v1_conversations(project_id, id DESC);
                CREATE TABLE IF NOT EXISTS v1_conversation_jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    user_message_id INTEGER NOT NULL, message TEXT NOT NULL,
                    start_task INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
                    task_id TEXT, agent_run_id TEXT, error TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_message_id) REFERENCES v1_conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_conversation_jobs_dispatch
                    ON v1_conversation_jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_v1_conversation_jobs_project
                    ON v1_conversation_jobs(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS v1_workflow_revisions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    definition_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, revision),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_v1_workflow_active
                    ON v1_workflow_revisions(project_id) WHERE active=1;
                CREATE TABLE IF NOT EXISTS v1_tasks (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workflow_revision_id TEXT NOT NULL,
                    title TEXT NOT NULL, brief TEXT NOT NULL, input_json TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL,
                    control_state TEXT NOT NULL DEFAULT 'active', pending_deadline_at TEXT,
                    pause_deadline_at TEXT, rerun_of_task_id TEXT,
                    current_column TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT, lease_until TEXT, not_before TEXT, error TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(workflow_revision_id) REFERENCES v1_workflow_revisions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_v1_tasks_dispatch
                    ON v1_tasks(status, not_before, updated_at);
                CREATE INDEX IF NOT EXISTS idx_v1_tasks_project
                    ON v1_tasks(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS v1_column_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL, column_key TEXT NOT NULL,
                    sequence INTEGER NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL,
                    input_json TEXT NOT NULL DEFAULT '{}', output_json TEXT NOT NULL DEFAULT '{}',
                    agent_run_id TEXT, error TEXT, failure_fingerprint TEXT,
                    heartbeat_at TEXT, last_progress_at TEXT,
                    started_at TEXT, finished_at TEXT, created_at TEXT NOT NULL, claim_deadline_at TEXT,
                    UNIQUE(task_id, sequence),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES v1_tasks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_runs_task ON v1_column_runs(task_id, sequence);
                CREATE TABLE IF NOT EXISTS v1_agent_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, column_run_id TEXT,
                    kind TEXT NOT NULL, status TEXT NOT NULL,
                    instruction_revision INTEGER NOT NULL, instruction_snapshot TEXT NOT NULL,
                    context_json TEXT NOT NULL, capabilities_json TEXT NOT NULL,
                    iterations INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0,
                    final_text TEXT NOT NULL DEFAULT '', error TEXT,
                    created_at TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_agent_runs_project
                    ON v1_agent_runs(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_v1_agent_runs_task
                    ON v1_agent_runs(task_id, created_at);
                CREATE TABLE IF NOT EXISTS v1_agent_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, agent_run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    tool_calls_json TEXT NOT NULL DEFAULT '[]', tool_call_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(agent_run_id, sequence),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES v1_agent_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_agent_messages_run
                    ON v1_agent_messages(agent_run_id, sequence);
                CREATE TABLE IF NOT EXISTS v1_tool_invocations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, agent_run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, tool_call_id TEXT NOT NULL,
                    capability TEXT NOT NULL, arguments_json TEXT NOT NULL,
                    result_json TEXT NOT NULL, ok INTEGER NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(agent_run_id, tool_call_id),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_run_id) REFERENCES v1_agent_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_tool_invocations_run
                    ON v1_tool_invocations(agent_run_id, sequence);
                CREATE TABLE IF NOT EXISTS v1_await_handles (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    provider TEXT NOT NULL, token TEXT, status TEXT NOT NULL,
                    next_check_at TEXT NOT NULL, stale_at TEXT NOT NULL, hard_deadline_at TEXT NOT NULL,
                    progress_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES v1_tasks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_await_due ON v1_await_handles(status, next_check_at);
                CREATE TABLE IF NOT EXISTS v1_artifacts (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT,
                    run_id TEXT, kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT,
                    size INTEGER NOT NULL DEFAULT 0, meta_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                    UNIQUE(project_id, path),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_artifacts_task ON v1_artifacts(task_id, created_at);
                CREATE TABLE IF NOT EXISTS v1_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, task_id TEXT,
                    run_id TEXT, type TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v1_events_project ON v1_events(project_id, id);
                CREATE INDEX IF NOT EXISTS idx_v1_events_task ON v1_events(task_id, id);
                CREATE TABLE IF NOT EXISTS v1_project_mailbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, task_id TEXT, run_id TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, observed_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_mailbox_pending
                    ON v1_project_mailbox(project_id, state, id);
                CREATE TABLE IF NOT EXISTS v1_governance_decisions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
                    subject_id TEXT, decision TEXT NOT NULL, data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_governance_project
                    ON v1_governance_decisions(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS v1_scheduled_reviews (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, reason TEXT NOT NULL,
                    due_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, observed_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_scheduled_reviews_due
                    ON v1_scheduled_reviews(state, due_at);
                CREATE TABLE IF NOT EXISTS v1_kanban_projection (
                    project_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
                    projection_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS v1_backlog_items (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, brief TEXT NOT NULL,
                    readiness_json TEXT NOT NULL, state TEXT NOT NULL, task_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_backlog_project ON v1_backlog_items(project_id,state,updated_at);
                CREATE TABLE IF NOT EXISTS v1_scheduling_entries (
                    task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, state TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
                    wip_group TEXT NOT NULL DEFAULT 'default', wip_limit INTEGER NOT NULL DEFAULT 4,
                    dependencies_json TEXT NOT NULL DEFAULT '[]', resources_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES v1_tasks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS v1_execution_receipts (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, execution_key TEXT NOT NULL, capability TEXT NOT NULL,
                    status TEXT NOT NULL, arguments_json TEXT NOT NULL, result_json TEXT, error TEXT, started_at TEXT NOT NULL, finished_at TEXT,
                    UNIQUE(project_id,execution_key), FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS v1_direct_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, agent_run_id TEXT, capability TEXT NOT NULL,
                    decision TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS v1_intervention_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, decision TEXT NOT NULL,
                    data_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                """
            )
            self._ensure_column(db, "v1_conversation_agents", "instruction", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(db, "v1_conversation_agents", "instruction_revision", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(db, "v1_conversation_agents", "last_observed_event_id", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db, "v1_conversation_jobs", "agent_run_id", "TEXT")
            self._ensure_column(db, "v1_column_runs", "agent_run_id", "TEXT")
            self._ensure_column(db, "v1_column_runs", "failure_fingerprint", "TEXT")
            self._ensure_column(db, "v1_column_runs", "heartbeat_at", "TEXT")
            self._ensure_column(db, "v1_column_runs", "last_progress_at", "TEXT")
            self._ensure_column(db, "v1_tasks", "not_before", "TEXT")
            self._ensure_column(db, "v1_projects", "state_version", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db, "v1_conversation_agents", "lease_owner", "TEXT")
            self._ensure_column(db, "v1_conversation_agents", "lease_until", "TEXT")
            self._ensure_column(db, "v1_conversation_jobs", "trigger_kind", "TEXT NOT NULL DEFAULT 'user'")
            self._ensure_column(db, "v1_conversation_jobs", "trigger_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_conversation_jobs", "mailbox_ids_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(db, "v1_conversation_jobs", "scheduled_review_id", "TEXT")
            self._ensure_column(db, "v1_conversation_jobs", "worker_id", "TEXT")
            self._ensure_column(db, "v1_conversation_jobs", "recovery_attempt", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db, "v1_conversation_jobs", "not_before", "TEXT")
            self._ensure_column(db, "v1_conversation_jobs", "result_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_tasks", "readiness_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_tasks", "state_version", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(db, "v1_tasks", "terminal_artifact_id", "TEXT")
            self._ensure_column(db, "v1_tasks", "terminal_event_id", "INTEGER")
            self._ensure_column(db, "v1_tasks", "notified_at", "TEXT")
            self._ensure_column(db, "v1_tasks", "observed_at", "TEXT")
            self._ensure_column(db, "v1_tasks", "supervision_action", "TEXT")
            self._ensure_column(db, "v1_tasks", "control_state", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(db, "v1_tasks", "pending_deadline_at", "TEXT")
            self._ensure_column(db, "v1_tasks", "pause_deadline_at", "TEXT")
            self._ensure_column(db, "v1_tasks", "rerun_of_task_id", "TEXT")
            self._ensure_column(db, "v1_column_runs", "error_category", "TEXT")
            self._ensure_column(db, "v1_column_runs", "claim_deadline_at", "TEXT")
            self._ensure_column(db, "v1_await_handles", "column_key", "TEXT")
            self._ensure_column(db, "v1_await_handles", "poll_capability", "TEXT")
            self._ensure_column(db, "v1_await_handles", "poll_arguments_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_await_handles", "success_outcome", "TEXT NOT NULL DEFAULT 'success'")
            self._ensure_column(db, "v1_await_handles", "timeout_outcome", "TEXT NOT NULL DEFAULT 'failure'")
            self._ensure_column(db, "v1_await_handles", "result_json", "TEXT NOT NULL DEFAULT '{}'")
            for table in ("v1_column_runs", "v1_agent_messages", "v1_tool_invocations", "v1_await_handles"):
                self._ensure_column(db, table, "project_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(db, "v1_await_handles", "waiting_kind", "TEXT NOT NULL DEFAULT 'external'")
            self._ensure_column(db, "v1_await_handles", "soft_deadline_at", "TEXT")
            self._ensure_column(db, "v1_await_handles", "health", "TEXT NOT NULL DEFAULT 'healthy'")
            self._ensure_column(db, "v1_await_handles", "resume_condition_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_await_handles", "cancel_capability", "TEXT")
            self._ensure_column(db, "v1_await_handles", "cancel_arguments_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_await_handles", "cleanup_capability", "TEXT")
            self._ensure_column(db, "v1_await_handles", "cleanup_arguments_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_await_handles", "idempotency_key", "TEXT")
            db.execute("UPDATE v1_column_runs SET project_id=(SELECT project_id FROM v1_tasks WHERE id=v1_column_runs.task_id) WHERE project_id='' ")
            db.execute("UPDATE v1_agent_messages SET project_id=(SELECT project_id FROM v1_agent_runs WHERE id=v1_agent_messages.agent_run_id) WHERE project_id='' ")
            db.execute("UPDATE v1_tool_invocations SET project_id=(SELECT project_id FROM v1_agent_runs WHERE id=v1_tool_invocations.agent_run_id) WHERE project_id='' ")
            db.execute("UPDATE v1_await_handles SET project_id=(SELECT project_id FROM v1_tasks WHERE id=v1_await_handles.task_id) WHERE project_id='' ")

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    @staticmethod
    def _decode(row: dict[str, Any] | None, *fields: str) -> dict[str, Any] | None:
        if row is None:
            return None
        for field in fields:
            if field in row:
                row[field.removesuffix("_json")] = json.loads(row.pop(field) or "{}")
        return row

    def _pack_text(self, value: str, *, max_bytes: int = 128_000) -> str:
        data = value.encode("utf-8")
        if len(data) <= max_bytes:
            return value
        return json.dumps(self._write_payload(data, "text/plain; charset=utf-8"), ensure_ascii=False)

    def _pack_json(self, value: Any, *, max_bytes: int = 256_000) -> str:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        if len(data) <= max_bytes:
            return data.decode("utf-8")
        return json.dumps(self._write_payload(data, "application/json"), ensure_ascii=False)

    def _write_payload(self, data: bytes, media_type: str) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        target = self.payload_root / digest[:2] / f"{digest}.blob"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fd, temporary = tempfile.mkstemp(prefix=".payload-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {"$artifact_ref": target.relative_to(self.path.parent).as_posix(), "sha256": digest, "size": len(data), "media_type": media_type}

    @staticmethod
    def _ensure_json_budget(value: Any, max_bytes: int, label: str) -> None:
        size = len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
        if size > max_bytes:
            raise ValueError(f"{label} exceeds the {max_bytes}-byte SQLite budget; write large content as a Project artifact and store only its reference")

    def create_project(self, name: str, description: str, base_dir: str, agent_instruction: str = "") -> dict[str, Any]:
        project_id, now = new_id("prj"), utcnow()
        canonical_base_dir = str(Path(base_dir).expanduser().resolve())
        Path(canonical_base_dir).mkdir(parents=True, exist_ok=True)
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_projects(id,name,description,base_dir,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (project_id, name, description, canonical_base_dir, now, now),
            )
            db.execute(
                "INSERT INTO v1_conversation_agents(project_id,logical_id,state,instruction,instruction_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (project_id, new_id("ca"), "idle", agent_instruction, 1, now, now),
            )
            self._event(db, project_id, None, None, "project.created", {"name": name})
            self._refresh_projection(db, project_id)
        return self.get_project(project_id)

    def conversation_agent(self, project_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute(
                "SELECT * FROM v1_conversation_agents WHERE project_id=?", (project_id,)
            ).fetchone())
        if not row:
            raise KeyError(project_id)
        return row

    def update_conversation_instruction(self, project_id: str, instruction: str) -> dict[str, Any]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            changed = db.execute(
                "UPDATE v1_conversation_agents SET instruction=?,instruction_revision=instruction_revision+1,updated_at=? WHERE project_id=?",
                (instruction, now, project_id),
            ).rowcount
            if changed != 1:
                raise KeyError(project_id)
            self._event(db, project_id, None, None, "agent.instruction_updated", {})
        return self.conversation_agent(project_id)

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM v1_projects ORDER BY created_at DESC")]

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute("SELECT * FROM v1_projects WHERE id=?", (project_id,)).fetchone())
        if not row:
            raise KeyError(project_id)
        return row

    def add_message(self, project_id: str, role: str, content: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        now = utcnow()
        packed_content, packed_meta = self._pack_text(content), self._pack_json(meta or {})
        with self.tx(immediate=True) as db:
            cursor = db.execute(
                "INSERT INTO v1_conversations(project_id,role,content,meta_json,created_at) VALUES(?,?,?,?,?)",
                (project_id, role, packed_content, packed_meta, now),
            )
            message_id = cursor.lastrowid
            self._event(db, project_id, None, None, "conversation.message", {"message_id": message_id, "role": role})
        return {"id": message_id, "project_id": project_id, "role": role, "content": content, "meta": meta or {}, "created_at": now}

    def messages(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM (SELECT * FROM v1_conversations WHERE project_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (project_id, min(max(limit, 1), 500)),
            ).fetchall()
        return [self._decode(dict(row), "meta_json") for row in rows]  # type: ignore[misc]

    def create_conversation_job(self, project_id: str, message: str, start_task: bool) -> dict[str, Any]:
        self.get_project(project_id)
        job_id, now = new_id("cjob"), utcnow()
        with self.tx(immediate=True) as db:
            cursor = db.execute(
                "INSERT INTO v1_conversations(project_id,role,content,meta_json,created_at) VALUES(?,?,?,?,?)",
                (
                    project_id,
                    "user",
                    message,
                    json.dumps({"status": "queued", "job_id": job_id}, ensure_ascii=False),
                    now,
                ),
            )
            message_id = int(cursor.lastrowid)
            db.execute(
                "INSERT INTO v1_conversation_jobs "
                "(id,project_id,user_message_id,message,start_task,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'queued',?,?)",
                (job_id, project_id, message_id, message, int(start_task), now, now),
            )
            db.execute(
                "UPDATE v1_conversation_agents SET state='planning',updated_at=? WHERE project_id=?",
                (now, project_id),
            )
            self._event(
                db,
                project_id,
                None,
                None,
                "conversation.queued",
                {"job_id": job_id, "message_id": message_id, "start_task": bool(start_task)},
            )
        return self.get_conversation_job(job_id)

    def enqueue_governance_jobs(self) -> list[str]:
        """Create one durable supervision turn per Project when facts require it."""
        now = utcnow()
        created: list[str] = []
        with self.tx(immediate=True) as db:
            projects = db.execute(
                "SELECT DISTINCT p.id FROM v1_projects p JOIN v1_conversation_agents a ON a.project_id=p.id "
                "LEFT JOIN v1_project_mailbox m ON m.project_id=p.id AND m.state='pending' "
                "LEFT JOIN v1_scheduled_reviews s ON s.project_id=p.id AND s.state='pending' AND s.due_at<=? "
                "WHERE a.state!='attention' AND (m.id IS NOT NULL OR s.id IS NOT NULL)",
                (now,),
            ).fetchall()
            for project in projects:
                project_id = project[0]
                busy = db.execute(
                    "SELECT 1 FROM v1_conversation_jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",
                    (project_id,),
                ).fetchone()
                if busy:
                    continue
                delayed = db.execute(
                    "SELECT 1 FROM v1_conversation_jobs WHERE project_id=? AND status='failed' AND not_before>? ORDER BY updated_at DESC LIMIT 1",
                    (project_id, now),
                ).fetchone()
                if delayed:
                    continue
                mailbox_ids = [row[0] for row in db.execute(
                    "SELECT id FROM v1_project_mailbox WHERE project_id=? AND state='pending' ORDER BY id LIMIT 100",
                    (project_id,),
                ).fetchall()]
                review = db.execute(
                    "SELECT id,reason FROM v1_scheduled_reviews WHERE project_id=? AND state='pending' AND due_at<=? ORDER BY due_at LIMIT 1",
                    (project_id, now),
                ).fetchone()
                trigger_kind = "mailbox" if mailbox_ids else "scheduled_review"
                trigger = {"mailbox_ids": mailbox_ids, "review_reason": review[1] if review else None}
                prior_row = db.execute("SELECT status,recovery_attempt FROM v1_conversation_jobs WHERE project_id=? AND trigger_kind!='user' ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
                prior = int(prior_row[1]) if prior_row and prior_row[0] == "failed" else 0
                job_id = new_id("cjob")
                cursor = db.execute(
                    "INSERT INTO v1_conversations(project_id,role,content,meta_json,created_at) VALUES(?,?,?,?,?)",
                    (project_id, "system", "", json.dumps({"trigger": trigger_kind}, ensure_ascii=False), now),
                )
                db.execute(
                    "INSERT INTO v1_conversation_jobs(id,project_id,user_message_id,message,start_task,status,trigger_kind,trigger_json,mailbox_ids_json,scheduled_review_id,recovery_attempt,created_at,updated_at) "
                    "VALUES(?,?,?,?,1,'queued',?,?,?,?,?,?,?)",
                    (job_id, project_id, int(cursor.lastrowid), "", trigger_kind, json.dumps(trigger, ensure_ascii=False), json.dumps(mailbox_ids), review[0] if review else None, int(prior) + 1, now, now),
                )
                db.execute("UPDATE v1_conversation_agents SET state='planning',updated_at=? WHERE project_id=?", (now, project_id))
                self._event(db, project_id, None, None, "conversation.governance_queued", {"job_id": job_id, "trigger": trigger_kind})
                created.append(job_id)
        return created

    def get_conversation_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute("SELECT * FROM v1_conversation_jobs WHERE id=?", (job_id,)).fetchone())
        if not row:
            raise KeyError(job_id)
        row["start_task"] = bool(row["start_task"])
        row = self._decode(row, "trigger_json", "mailbox_ids_json", "result_json") or row
        return row

    def claim_conversation_job(self, job_id: str, worker_id: str) -> dict[str, Any] | None:
        now = utcnow()
        with self.tx(immediate=True) as db:
            changed = db.execute(
                "UPDATE v1_conversation_jobs SET status='running',worker_id=?,updated_at=?,error=NULL "
                "WHERE id=? AND status='queued' AND NOT EXISTS (SELECT 1 FROM v1_conversation_agents a JOIN v1_conversation_jobs j ON j.project_id=a.project_id WHERE j.id=? AND a.lease_until IS NOT NULL AND a.lease_until>?)",
                (worker_id, now, job_id, job_id, now),
            ).rowcount
            if not changed:
                return None
            row = db.execute("SELECT * FROM v1_conversation_jobs WHERE id=?", (job_id,)).fetchone()
            assert row is not None
            captured = json.loads(row["mailbox_ids_json"] or "[]")
            if not captured:
                captured = [item[0] for item in db.execute(
                    "SELECT id FROM v1_project_mailbox WHERE project_id=? AND state='pending' ORDER BY id LIMIT 100",
                    (row["project_id"],),
                ).fetchall()]
                db.execute("UPDATE v1_conversation_jobs SET mailbox_ids_json=? WHERE id=?", (json.dumps(captured), job_id))
            lease_until = (datetime.now(timezone.utc) + timedelta(seconds=180)).isoformat(timespec="milliseconds")
            db.execute(
                "UPDATE v1_conversation_agents SET lease_owner=?,lease_until=?,state='planning',updated_at=? WHERE project_id=?",
                (worker_id, lease_until, now, row["project_id"]),
            )
            agent = db.execute(
                "SELECT logical_id FROM v1_conversation_agents WHERE project_id=?", (row["project_id"],)
            ).fetchone()
            self._event(
                db,
                row["project_id"],
                None,
                None,
                "conversation.planning_started",
                {"job_id": job_id, "worker_id": worker_id, "agent_id": agent[0] if agent else None, "llm_used": True},
            )
        return self.get_conversation_job(job_id)

    def finish_conversation_job(
        self,
        job_id: str,
        task_id: str | None,
        agent_run_id: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            row = db.execute("SELECT project_id,mailbox_ids_json,scheduled_review_id,worker_id FROM v1_conversation_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            project_id = row[0]
            owner = db.execute("SELECT lease_owner FROM v1_conversation_agents WHERE project_id=?", (project_id,)).fetchone()
            if not owner or owner[0] != row[3]:
                raise RuntimeError("Conversation governance lease ownership was lost")
            db.execute(
                "UPDATE v1_conversation_jobs SET status='succeeded',task_id=?,agent_run_id=?,result_json=?,error=NULL,updated_at=?,finished_at=? "
                "WHERE id=?",
                (task_id, agent_run_id, json.dumps(result or {}, ensure_ascii=False), now, now, job_id),
            )
            self._settle_conversation_agent(db, project_id, now)
            mailbox_ids = json.loads(row[1] or "[]")
            if mailbox_ids:
                placeholders = ",".join("?" for _ in mailbox_ids)
                db.execute(
                    f"UPDATE v1_project_mailbox SET state='observed',observed_at=? WHERE project_id=? AND state='pending' AND id IN ({placeholders})",
                    [now, project_id, *mailbox_ids],
                )
                db.execute(
                    f"UPDATE v1_tasks SET observed_at=?,supervision_action=COALESCE(supervision_action,'observed_no_intervention'),state_version=state_version+1 WHERE project_id=? AND id IN (SELECT task_id FROM v1_project_mailbox WHERE id IN ({placeholders}) AND task_id IS NOT NULL)",
                    [now, project_id, *mailbox_ids],
                )
            if row[2]:
                db.execute("UPDATE v1_scheduled_reviews SET state='observed',observed_at=? WHERE id=?", (now, row[2]))
            db.execute("UPDATE v1_conversation_agents SET lease_owner=NULL,lease_until=NULL WHERE project_id=?", (project_id,))
            self._event(db, project_id, task_id, None, "conversation.planning_succeeded", {"job_id": job_id})
        return self.get_conversation_job(job_id)

    def fail_conversation_job(self, job_id: str, error: str) -> dict[str, Any]:
        now = utcnow()
        safe_error = error[:2000]
        with self.tx(immediate=True) as db:
            row = db.execute(
                "SELECT project_id,trigger_kind,recovery_attempt FROM v1_conversation_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                raise KeyError(job_id)
            project_id = row[0]
            delayed_until = (datetime.now(timezone.utc) + timedelta(seconds=min(300, 5 * (2 ** int(row[2] or 0))))).isoformat(timespec="milliseconds")
            db.execute(
                "UPDATE v1_conversation_jobs SET status='failed',error=?,not_before=?,updated_at=?,finished_at=? WHERE id=?",
                (safe_error, delayed_until, now, now, job_id),
            )
            db.execute(
                "INSERT INTO v1_conversations(project_id,role,content,meta_json,created_at) VALUES(?,?,?,?,?)",
                (
                    project_id,
                    "assistant",
                    "任务规划失败，已记录错误并通知项目管理 Agent。请稍后重试或查看项目事件。",
                    json.dumps({"status": "failed", "job_id": job_id, "error": safe_error}, ensure_ascii=False),
                    now,
                ),
            )
            if row[1] != "user" and int(row[2] or 0) >= 3:
                db.execute("UPDATE v1_conversation_agents SET state='attention',lease_owner=NULL,lease_until=NULL,updated_at=? WHERE project_id=?", (now, project_id))
                self._event(db, project_id, None, None, "conversation.governance_attention_required", {"job_id": job_id, "attempt": int(row[2] or 0), "error": safe_error})
            else:
                self._settle_conversation_agent(db, project_id, now)
            db.execute("UPDATE v1_conversation_agents SET lease_owner=NULL,lease_until=NULL WHERE project_id=?", (project_id,))
            self._event(db, project_id, None, None, "conversation.planning_failed", {"job_id": job_id, "error": safe_error})
            self._mailbox(db, project_id, "conversation_planning_failed", None, None, {"job_id": job_id, "error": safe_error})
        return self.get_conversation_job(job_id)

    def recover_conversation_jobs(self) -> list[dict[str, Any]]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_conversation_jobs SET status='queued',updated_at=? WHERE status='running'",
                (now,),
            )
            db.execute("UPDATE v1_conversation_agents SET lease_owner=NULL,lease_until=NULL")
            rows = db.execute(
                "SELECT * FROM v1_conversation_jobs WHERE status='queued' ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def renew_conversation_lease(self, project_id: str, worker_id: str, lease_seconds: int = 180) -> bool:
        now = utcnow(); lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        with self.tx(immediate=True) as db:
            changed = db.execute("UPDATE v1_conversation_agents SET lease_until=?,updated_at=? WHERE project_id=? AND lease_owner=? AND state='planning'", (lease, now, project_id, worker_id)).rowcount
        return changed == 1

    @staticmethod
    def _settle_conversation_agent(db: sqlite3.Connection, project_id: str, now: str) -> None:
        pending = db.execute(
            "SELECT COUNT(*) FROM v1_conversation_jobs WHERE project_id=? AND status IN ('queued','running')",
            (project_id,),
        ).fetchone()[0]
        db.execute(
            "UPDATE v1_conversation_agents SET state=?,updated_at=? WHERE project_id=?",
            ("planning" if pending else "idle", now, project_id),
        )

    def publish_workflow(self, project_id: str, workflow: WorkflowDefinition) -> dict[str, Any]:
        self.get_project(project_id)
        for column in workflow.columns:
            check_schema(column.input_contract, label=f"Column {column.key} input_contract")
            check_schema(column.output_contract, label=f"Column {column.key} output_contract")
        workflow_id, now = new_id("wf"), utcnow()
        payload = workflow.model_dump_json()
        if len(payload.encode("utf-8")) > 2_000_000:
            raise ValueError("Workflow definition exceeds the 2000000-byte SQLite budget")
        with self.tx(immediate=True) as db:
            current = db.execute(
                "SELECT COALESCE(MAX(revision),0) FROM v1_workflow_revisions WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            db.execute("UPDATE v1_workflow_revisions SET active=0 WHERE project_id=?", (project_id,))
            db.execute(
                "INSERT INTO v1_workflow_revisions VALUES(?,?,?,?,?,?)",
                (workflow_id, project_id, current + 1, payload, 1, now),
            )
            self._event(db, project_id, None, None, "workflow.published", {"workflow_id": workflow_id, "revision": current + 1})
            self._refresh_projection(db, project_id)
        return self.get_workflow(project_id)

    def get_workflow(self, project_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute(
                "SELECT * FROM v1_workflow_revisions WHERE project_id=? AND active=1", (project_id,)
            ).fetchone())
        if not row:
            raise KeyError(f"no active workflow for {project_id}")
        row["definition"] = json.loads(row.pop("definition_json"))
        return row

    def workflow_by_id(self, project_id: str, workflow_id: str) -> WorkflowDefinition:
        with self.connect() as db:
            row = db.execute("SELECT definition_json FROM v1_workflow_revisions WHERE id=? AND project_id=?", (workflow_id, project_id)).fetchone()
        if not row:
            raise KeyError(workflow_id)
        return WorkflowDefinition.model_validate_json(row[0])

    def get_workflow_revision(self, project_id: str, workflow_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute("SELECT * FROM v1_workflow_revisions WHERE id=? AND project_id=?", (workflow_id, project_id)).fetchone())
        if not row:
            raise KeyError(workflow_id)
        row["definition"] = json.loads(row.pop("definition_json"))
        row["active"] = bool(row["active"])
        return row

    def create_task(
        self,
        project_id: str,
        title: str,
        brief: str,
        input_data: dict[str, Any],
        readiness: dict[str, Any],
        *,
        pending_timeout_seconds: int = 86_400,
        rerun_of_task_id: str | None = None,
    ) -> dict[str, Any]:
        if readiness.get("decision") != "dispatch":
            raise ValueError("Task creation requires an explicit dispatch readiness decision")
        self._ensure_json_budget(input_data, 256_000, "Task input")
        self._ensure_json_budget(readiness, 64_000, "Task readiness")
        workflow = self.get_workflow(project_id)
        definition = WorkflowDefinition.model_validate(workflow["definition"])
        task_id, now = new_id("tsk"), utcnow()
        pending_deadline = (datetime.now(timezone.utc) + timedelta(seconds=max(60, min(pending_timeout_seconds, 2_592_000)))).isoformat(timespec="milliseconds")
        claim_deadline = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="milliseconds")
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_tasks(id,project_id,workflow_revision_id,title,brief,input_json,context_json,readiness_json,status,control_state,pending_deadline_at,rerun_of_task_id,current_column,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'active',?,?,?,?,?)",
                (task_id, project_id, workflow["id"], title, brief, json.dumps(input_data, ensure_ascii=False), "{}", json.dumps(readiness, ensure_ascii=False), "pending", pending_deadline, rerun_of_task_id, definition.entry, now, now),
            )
            run_id = new_id("run")
            db.execute(
                "INSERT INTO v1_column_runs(id,project_id,task_id,column_key,sequence,status,attempt,input_json,created_at,claim_deadline_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, task_id, definition.entry, 1, "pending", 0, json.dumps(input_data, ensure_ascii=False), now, claim_deadline),
            )
            backlog_id = new_id("backlog")
            db.execute("INSERT INTO v1_backlog_items(id,project_id,title,brief,readiness_json,state,task_id,created_at,updated_at) VALUES(?,?,?,?,?,'dispatched',?,?,?)", (backlog_id, project_id, title, brief, json.dumps(readiness, ensure_ascii=False), task_id, now, now))
            db.execute("INSERT INTO v1_scheduling_entries(task_id,project_id,state,priority,wip_group,wip_limit,dependencies_json,resources_json,created_at,updated_at) VALUES(?,?,'admitted',0,'default',4,'[]',?,?,?)", (task_id, project_id, json.dumps(readiness.get("resource_conflicts") or []), now, now))
            db.execute(
                "INSERT INTO v1_governance_decisions(id,project_id,kind,subject_id,decision,data_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (new_id("gdec"), project_id, "readiness", task_id, "dispatch", json.dumps(readiness, ensure_ascii=False), now),
            )
            self._event(db, project_id, task_id, None, "task.created", {"title": title, "entry": definition.entry})
            self._refresh_projection(db, project_id)
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute("SELECT * FROM v1_tasks WHERE id=?", (task_id,)).fetchone())
        if not row:
            raise KeyError(task_id)
        return self._decode(row, "input_json", "context_json", "readiness_json")  # type: ignore[return-value]

    def get_project_task(self, project_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["project_id"] != project_id:
            raise KeyError(task_id)
        return task

    def list_tasks(self, project_id: str, limit: int = 100, cursor: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            before = "9999-12-31T23:59:59+00:00"
            if cursor:
                row = db.execute("SELECT created_at FROM v1_tasks WHERE id=? AND project_id=?", (cursor, project_id)).fetchone()
                if not row:
                    raise KeyError(cursor)
                before = row[0]
            rows = db.execute("SELECT * FROM v1_tasks WHERE project_id=? AND created_at<? ORDER BY created_at DESC LIMIT ?", (project_id, before, min(max(limit, 1), 500))).fetchall()
        return [self._decode(dict(row), "input_json", "context_json", "readiness_json") for row in rows]  # type: ignore[misc]

    def task_summaries(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,title,status,current_column,attempt,error,state_version,terminal_artifact_id,notified_at,observed_at,supervision_action,updated_at FROM v1_tasks WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, min(max(limit, 1), 500)),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_backlog(self, project_id: str, title: str, brief: str, readiness: dict[str, Any]) -> dict[str, Any]:
        decision = str(readiness.get("decision") or "hold")
        if decision == "dispatch":
            raise ValueError("dispatch readiness must use task.create")
        backlog_id, now = new_id("backlog"), utcnow()
        with self.tx(immediate=True) as db:
            db.execute("INSERT INTO v1_backlog_items(id,project_id,title,brief,readiness_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (backlog_id, project_id, title, brief, json.dumps(readiness, ensure_ascii=False), decision, now, now))
            self._event(db, project_id, None, None, "backlog.recorded", {"backlog_id": backlog_id, "decision": decision})
        return {"id": backlog_id, "project_id": project_id, "title": title, "brief": brief, "readiness": readiness, "state": decision, "created_at": now, "updated_at": now}

    def schedule_task(self, project_id: str, task_id: str, state: str, priority: int, wip_group: str, wip_limit: int, dependencies: list[str], resources: list[str]) -> dict[str, Any]:
        self.get_project_task(project_id, task_id)
        if state not in {"admitted", "queued", "hold", "cancelled"}:
            raise ValueError("invalid scheduling state")
        now = utcnow()
        with self.tx(immediate=True) as db:
            db.execute("UPDATE v1_scheduling_entries SET state=?,priority=?,wip_group=?,wip_limit=?,dependencies_json=?,resources_json=?,updated_at=? WHERE task_id=? AND project_id=?", (state, priority, wip_group, wip_limit, json.dumps(dependencies), json.dumps(resources), now, task_id, project_id))
            self._event(db, project_id, task_id, None, "scheduling.decided", {"state": state, "priority": priority, "wip_group": wip_group, "wip_limit": wip_limit, "dependencies": dependencies, "resources": resources})
        return {"project_id": project_id, "task_id": task_id, "state": state, "priority": priority, "wip_group": wip_group, "wip_limit": wip_limit, "dependencies": dependencies, "resources": resources, "updated_at": now}

    def retry_task(self, task_id: str, column_key: str | None = None, *, clear_context: bool = False) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable; use task.rerun to create a successor")
        if task["status"] == "running":
            raise ValueError("a running Task cannot be retried until its active Attempt stops")
        workflow = self.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        target = column_key or workflow.entry
        column = workflow.column(target)
        if column.terminal:
            raise ValueError("retry target must be a non-terminal Column")
        now = utcnow()
        pending_deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="milliseconds")
        context = {} if clear_context else task["context"]
        with self.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_tasks SET status='pending',control_state='active',pending_deadline_at=?,pause_deadline_at=NULL,current_column=?,attempt=0,context_json=?,error=NULL,lease_owner=NULL,lease_until=NULL,not_before=NULL,finished_at=NULL,state_version=state_version+1,updated_at=? WHERE id=?",
                (pending_deadline, target, json.dumps(context, ensure_ascii=False), now, task_id),
            )
            data = {"target": target, "clear_context": clear_context}
            self._event(db, task["project_id"], task_id, None, "task.retry_requested", data)
            self._mailbox(db, task["project_id"], "task.retry_requested", task_id, None, data)
        return self.get_task(task_id)

    def route_task_to_failed(self, task_id: str, reason: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable")
        return self._fail_task_now(task, reason, "cancelled")

    def rerun_task(self, task_id: str, *, pending_timeout_seconds: int = 86_400) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] not in {"done", "failed"}:
            raise ValueError("task.rerun requires an immutable terminal Task")
        return self.create_task(
            task["project_id"],
            task["title"],
            task["brief"],
            task["input"],
            task["readiness"],
            pending_timeout_seconds=pending_timeout_seconds,
            rerun_of_task_id=task["id"],
        )

    def pause_task(self, task_id: str, pause_timeout_seconds: int) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable")
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=max(60, min(pause_timeout_seconds, 2_592_000)))).isoformat(timespec="milliseconds")
        now = utcnow()
        target_state = "pause_requested" if task["status"] == "running" else "paused"
        with self.tx(immediate=True) as db:
            changed = db.execute(
                "UPDATE v1_tasks SET control_state=?,pause_deadline_at=?,state_version=state_version+1,updated_at=? WHERE id=? AND status NOT IN ('done','failed')",
                (target_state, deadline, now, task_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task changed while pausing")
            self._event(db, task["project_id"], task_id, None, "task.pause_requested", {"pause_deadline_at": deadline})
            self._mailbox(db, task["project_id"], "task_pause_requested", task_id, None, {"pause_deadline_at": deadline})
            self._refresh_projection(db, task["project_id"])
        return self.get_task(task_id)

    def resume_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable")
        if task.get("control_state") not in {"paused", "pause_requested"}:
            raise ValueError("Task is not paused")
        now = utcnow()
        with self.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_tasks SET control_state='active',pause_deadline_at=NULL,state_version=state_version+1,updated_at=? WHERE id=? AND status NOT IN ('done','failed')",
                (now, task_id),
            )
            self._event(db, task["project_id"], task_id, None, "task.resumed", {})
            self._mailbox(db, task["project_id"], "task_resumed", task_id, None, {})
            self._refresh_projection(db, task["project_id"])
        return self.get_task(task_id)

    def fail_unrecoverable_task(self, task: dict[str, Any], run_id: str, error: str, terminal_artifact: dict[str, Any]) -> None:
        now = utcnow()
        with self.tx(immediate=True) as db:
            db.execute("UPDATE v1_column_runs SET status='failed',error=?,finished_at=? WHERE id=?", (error, now, run_id))
            db.execute(
                "UPDATE v1_tasks SET status='failed',error=?,lease_owner=NULL,lease_until=NULL,state_version=state_version+1,updated_at=?,finished_at=? WHERE id=?",
                (error, now, now, task["id"]),
            )
            artifact_id = new_id("art")
            db.execute("INSERT INTO v1_artifacts(id,project_id,task_id,run_id,kind,path,sha256,size,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET id=excluded.id,task_id=excluded.task_id,run_id=excluded.run_id,kind=excluded.kind,sha256=excluded.sha256,size=excluded.size,meta_json=excluded.meta_json,created_at=excluded.created_at", (artifact_id, task["project_id"], task["id"], run_id, terminal_artifact["kind"], terminal_artifact["path"], terminal_artifact["sha256"], terminal_artifact["size"], json.dumps(terminal_artifact["meta"]), now))
            data = {"error": error, "reason": "runtime_definition_unavailable", "artifact_id": artifact_id}
            self._event(db, task["project_id"], task["id"], run_id, "task.failed", data)
            terminal_event_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._mailbox(db, task["project_id"], "task.failed", task["id"], run_id, data)
            db.execute("UPDATE v1_tasks SET terminal_artifact_id=?,terminal_event_id=?,notified_at=?,state_version=state_version+1 WHERE id=?", (artifact_id, terminal_event_id, now, task["id"]))
            self._refresh_projection(db, task["project_id"])

    def _fail_task_now(self, task: dict[str, Any], reason: str, failure_code: str) -> dict[str, Any]:
        reason = reason[:4000]
        now = utcnow()
        workflow = self.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        failed_column = workflow.terminal_key("failed")
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM v1_column_runs WHERE task_id=? ORDER BY sequence DESC LIMIT 1",
                (task["id"],),
            ).fetchone()
        run_id = row[0] if row else None
        payload = {
            "schema": "devwerk.task-terminal.v1",
            "project_id": task["project_id"],
            "task_id": task["id"],
            "column_run_id": run_id,
            "terminal": "failed",
            "failure_code": failure_code,
            "error": reason,
            "recorded_at": now,
        }
        info = ProjectFiles(self.get_project(task["project_id"])["base_dir"]).write_text(
            f".devwerk/terminal/{task['id']}.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        )
        with self.tx(immediate=True) as db:
            current = db.execute("SELECT status FROM v1_tasks WHERE id=?", (task["id"],)).fetchone()
            if not current:
                raise KeyError(task["id"])
            if current[0] in {"done", "failed"}:
                return self.get_task(task["id"])
            db.execute(
                "UPDATE v1_column_runs SET status='failed',error=?,finished_at=? WHERE task_id=? AND status IN ('pending','running','waiting')",
                (reason, now, task["id"]),
            )
            db.execute("UPDATE v1_await_handles SET status='cancelled',updated_at=? WHERE task_id=? AND status='pending'", (now, task["id"]))
            changed = db.execute(
                "UPDATE v1_tasks SET status='failed',control_state='active',pending_deadline_at=NULL,pause_deadline_at=NULL,current_column=?,error=?,lease_owner=NULL,lease_until=NULL,not_before=NULL,state_version=state_version+1,updated_at=?,finished_at=? WHERE id=? AND status NOT IN ('done','failed')",
                (failed_column, reason, now, now, task["id"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task changed while applying terminal failure")
            artifact_id = new_id("art")
            db.execute(
                "INSERT INTO v1_artifacts(id,project_id,task_id,run_id,kind,path,sha256,size,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET id=excluded.id,task_id=excluded.task_id,run_id=excluded.run_id,kind=excluded.kind,sha256=excluded.sha256,size=excluded.size,meta_json=excluded.meta_json,created_at=excluded.created_at",
                (artifact_id, task["project_id"], task["id"], run_id, "task_terminal", info["path"], info["sha256"], info["size"], json.dumps({"terminal": "failed", "failure_code": failure_code}, ensure_ascii=False), now),
            )
            data = {"error": reason, "failure_code": failure_code, "artifact_id": artifact_id}
            self._event(db, task["project_id"], task["id"], run_id, "task.failed", data)
            terminal_event_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._mailbox(db, task["project_id"], "task.failed", task["id"], run_id, data)
            db.execute(
                "UPDATE v1_tasks SET terminal_artifact_id=?,terminal_event_id=?,notified_at=? WHERE id=?",
                (artifact_id, terminal_event_id, now, task["id"]),
            )
            self._refresh_projection(db, task["project_id"])
        return self.get_task(task["id"])

    def claim_task(self, task_id: str, owner: str, lease_seconds: int = 120) -> dict[str, Any] | None:
        now = utcnow()
        lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        with self.tx(immediate=True) as db:
            cursor = db.execute(
                "UPDATE v1_tasks SET status='running',lease_owner=?,lease_until=?,not_before=NULL,pending_deadline_at=NULL,state_version=state_version+1,updated_at=? WHERE id=? AND status IN ('pending','waiting','recovering') AND control_state='active' AND (not_before IS NULL OR not_before<=?)",
                (owner, lease, now, task_id, now),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_task(task_id)

    def renew_lease(self, task_id: str, owner: str, lease_seconds: int = 120) -> bool:
        now = utcnow()
        lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        with self.tx(immediate=True) as db:
            cursor = db.execute(
                "UPDATE v1_tasks SET lease_until=?,updated_at=? WHERE id=? AND status='running' AND lease_owner=?",
                (lease, now, task_id, owner),
            )
            if cursor.rowcount == 1:
                db.execute(
                    "UPDATE v1_column_runs SET heartbeat_at=? WHERE id=(SELECT id FROM v1_column_runs WHERE task_id=? AND status='running' ORDER BY sequence DESC LIMIT 1)",
                    (now, task_id),
                )
        return cursor.rowcount == 1

    def runnable_task_ids(self, limit: int = 20) -> list[str]:
        now = utcnow()
        with self.connect() as db:
            rows = db.execute(
                "SELECT t.id FROM v1_tasks t JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id "
                "WHERE t.control_state='active' AND ((((t.status IN ('pending','recovering')) AND (t.not_before IS NULL OR t.not_before<=?)) AND s.state='admitted' "
                "AND NOT EXISTS (SELECT 1 FROM json_each(s.dependencies_json) d JOIN v1_tasks dep ON dep.id=d.value WHERE dep.status!='done') "
                "AND (SELECT COUNT(*) FROM v1_tasks active JOIN v1_scheduling_entries sa ON sa.task_id=active.id WHERE active.project_id=t.project_id AND active.status='running' AND sa.wip_group=s.wip_group)<s.wip_limit) "
                "OR (t.status='running' AND t.lease_until<?)) ORDER BY s.priority DESC,t.updated_at LIMIT ?",
                (now, now, limit),
            ).fetchall()
        return [row[0] for row in rows]

    def recover_expired(self) -> int:
        now = utcnow()
        with self.tx(immediate=True) as db:
            rows = db.execute("SELECT id,project_id FROM v1_tasks WHERE status='running' AND lease_until<?", (now,)).fetchall()
            for row in rows:
                db.execute("UPDATE v1_tasks SET status='recovering',lease_owner=NULL,lease_until=NULL,state_version=state_version+1,updated_at=? WHERE id=?", (now, row[0]))
                db.execute("UPDATE v1_column_runs SET status='interrupted',error='lease expired',finished_at=? WHERE task_id=? AND status='running'", (now, row[0]))
                self._event(db, row[1], row[0], None, "task.recovering", {"reason": "lease_expired"})
                self._mailbox(db, row[1], "task_recovering", row[0], None, {"reason": "lease_expired"})
        return len(rows)

    def expire_nonterminal_deadlines(self) -> int:
        now = utcnow()
        with self.connect() as db:
            rows = db.execute(
                "SELECT DISTINCT t.id,CASE "
                "WHEN t.control_state='paused' AND t.pause_deadline_at<=? THEN 'pause_timeout' "
                "WHEN EXISTS (SELECT 1 FROM v1_column_runs r WHERE r.task_id=t.id AND r.status='pending' AND r.claim_deadline_at IS NOT NULL AND r.claim_deadline_at<=?) THEN 'claim_timeout' "
                "ELSE 'pending_timeout' END AS failure_code "
                "FROM v1_tasks t WHERE t.status NOT IN ('done','failed') AND ("
                "(t.control_state='paused' AND t.pause_deadline_at IS NOT NULL AND t.pause_deadline_at<=?) OR "
                "(t.status='pending' AND t.pending_deadline_at IS NOT NULL AND t.pending_deadline_at<=?) OR "
                "EXISTS (SELECT 1 FROM v1_column_runs r WHERE r.task_id=t.id AND r.status='pending' AND r.claim_deadline_at IS NOT NULL AND r.claim_deadline_at<=?))",
                (now, now, now, now, now),
            ).fetchall()
        failed = 0
        for row in rows:
            task = self.get_task(row[0])
            if task["status"] in {"done", "failed"}:
                continue
            self._fail_task_now(task, f"{row[1]}: nonterminal Task deadline expired", row[1])
            failed += 1
        return failed

    def begin_run(self, task: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            pending = db.execute(
                "SELECT id,sequence FROM v1_column_runs WHERE task_id=? AND column_key=? AND status='pending' ORDER BY sequence LIMIT 1",
                (task["id"], task["current_column"]),
            ).fetchone()
            if pending:
                run_id, sequence = pending[0], pending[1]
                db.execute(
                    "UPDATE v1_column_runs SET status='running',attempt=?,input_json=?,heartbeat_at=?,last_progress_at=?,started_at=?,claim_deadline_at=NULL WHERE id=?",
                    (task["attempt"] + 1, json.dumps(input_data, ensure_ascii=False), now, now, now, run_id),
                )
            else:
                run_id = new_id("run")
                sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM v1_column_runs WHERE task_id=?", (task["id"],)).fetchone()[0]
                db.execute(
                    "INSERT INTO v1_column_runs(id,project_id,task_id,column_key,sequence,status,attempt,input_json,heartbeat_at,last_progress_at,started_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, task["project_id"], task["id"], task["current_column"], sequence, "running", task["attempt"] + 1, json.dumps(input_data, ensure_ascii=False), now, now, now, now),
                )
            self._event(db, task["project_id"], task["id"], run_id, "column.started", {"column": task["current_column"], "sequence": sequence})
        return {"id": run_id, "sequence": sequence, "column_key": task["current_column"], "status": "running", "started_at": now}

    def prepare_terminal_evidence(self, task: dict[str, Any], run_id: str, terminal: str, output: dict[str, Any], error: str | None) -> dict[str, Any]:
        payload = {
            "schema": "devwerk.task-terminal.v1",
            "project_id": task["project_id"], "task_id": task["id"], "column_run_id": run_id,
            "terminal": terminal, "output": output, "error": error, "recorded_at": utcnow(),
        }
        files = ProjectFiles(self.get_project(task["project_id"])["base_dir"])
        info = files.write_text(
            f".devwerk/terminal/{task['id']}.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        )
        return {**info, "kind": "task_terminal", "meta": {"terminal": terminal, "schema": payload["schema"]}}

    def finish_run(self, task: dict[str, Any], run_id: str, output: dict[str, Any], outcome: str, next_column: str | None, terminal: str | None = None, error: str | None = None, terminal_artifact: dict[str, Any] | None = None) -> None:
        now = utcnow()
        run_status = "failed" if error else "succeeded"
        task_status = terminal or ("failed" if error else "pending")
        persisted_output = dict(output)
        task_context = persisted_output.pop("context", task["context"])
        self._ensure_json_budget(persisted_output, 512_000, "Column output")
        self._ensure_json_budget(task_context, 1_000_000, "Task context")
        with self.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_column_runs SET status=?,output_json=?,error=?,finished_at=? WHERE id=?",
                (run_status, json.dumps(persisted_output, ensure_ascii=False), error, now, run_id),
            )
            if terminal:
                terminal_error = error or (task.get("error") if terminal == "failed" else None)
                changed = db.execute(
                    "UPDATE v1_tasks SET status=?,control_state='active',pending_deadline_at=NULL,pause_deadline_at=NULL,context_json=?,error=?,lease_owner=NULL,lease_until=NULL,not_before=NULL,state_version=state_version+1,updated_at=?,finished_at=? WHERE id=? AND state_version=? AND status='running'",
                    (terminal, json.dumps(task_context, ensure_ascii=False), terminal_error, now, now, task["id"], task["state_version"]),
                ).rowcount
            else:
                sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM v1_column_runs WHERE task_id=?", (task["id"],)).fetchone()[0]
                claim_deadline = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="milliseconds")
                db.execute(
                    "INSERT INTO v1_column_runs(id,project_id,task_id,column_key,sequence,status,attempt,input_json,created_at,claim_deadline_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (new_id("run"), task["project_id"], task["id"], next_column, sequence, "pending", 0, "{}", now, claim_deadline),
                )
                changed = db.execute(
                    "UPDATE v1_tasks SET status=?,current_column=?,attempt=0,context_json=?,error=?,lease_owner=NULL,lease_until=NULL,not_before=NULL,control_state=CASE WHEN control_state='pause_requested' THEN 'paused' ELSE control_state END,state_version=state_version+1,updated_at=? WHERE id=? AND state_version=? AND status IN ('running','waiting')",
                    (task_status, next_column, json.dumps(task_context, ensure_ascii=False), error, now, task["id"], task["state_version"]),
                ).rowcount
            if changed != 1:
                raise RuntimeError("stale Task state_version while finishing Column Run")
            self._event(db, task["project_id"], task["id"], run_id, "column.finished", {"column": task["current_column"], "outcome": outcome, "status": run_status, "next": next_column})
            if terminal:
                terminal_error = error or (task.get("error") if terminal == "failed" else None)
                artifact_id = None
                if terminal_artifact:
                    artifact_id = new_id("art")
                    db.execute(
                        "INSERT INTO v1_artifacts(id,project_id,task_id,run_id,kind,path,sha256,size,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(project_id,path) DO UPDATE SET id=excluded.id,task_id=excluded.task_id,run_id=excluded.run_id,kind=excluded.kind,sha256=excluded.sha256,size=excluded.size,meta_json=excluded.meta_json,created_at=excluded.created_at",
                        (artifact_id, task["project_id"], task["id"], run_id, terminal_artifact["kind"], terminal_artifact["path"], terminal_artifact["sha256"], terminal_artifact["size"], json.dumps(terminal_artifact["meta"], ensure_ascii=False), now),
                    )
                self._event(db, task["project_id"], task["id"], run_id, f"task.{terminal}", {"error": terminal_error, "artifact_id": artifact_id})
                terminal_event_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                self._mailbox(db, task["project_id"], f"task.{terminal}", task["id"], run_id, {"error": terminal_error})
                db.execute(
                    "UPDATE v1_tasks SET terminal_artifact_id=?,terminal_event_id=?,notified_at=? WHERE id=?",
                    (artifact_id, terminal_event_id, now, task["id"]),
                )
            self._refresh_projection(db, task["project_id"])

    def fail_attempt(
        self,
        task: dict[str, Any],
        run_id: str,
        error: str,
        max_attempts: int,
        failure_column: str | None,
        failure_fingerprint: str | None = None,
        repeated_failure_limit: int = 2,
        backoff_seconds: float = 0,
        error_category: str = "runtime_permanent",
        retryable: bool = False,
    ) -> None:
        now, next_attempt = utcnow(), task["attempt"] + 1
        direct_terminal = False
        with self.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_column_runs SET status='failed',error=?,error_category=?,failure_fingerprint=?,last_progress_at=?,finished_at=? WHERE id=?",
                (error, error_category, failure_fingerprint, now, now, run_id),
            )
            repeated = 0
            if failure_fingerprint:
                recent = db.execute(
                    "SELECT failure_fingerprint FROM v1_column_runs WHERE task_id=? AND column_key=? AND status='failed' ORDER BY sequence DESC LIMIT ?",
                    (task["id"], task["current_column"], max(repeated_failure_limit, 1)),
                ).fetchall()
                for item in recent:
                    if item[0] != failure_fingerprint:
                        break
                    repeated += 1
            terminal = (not retryable) or next_attempt >= max_attempts or repeated >= repeated_failure_limit
            if terminal:
                if not failure_column:
                    raise ValueError("workflow has no explicit failed terminal column")
                target = self.workflow_by_id(task["project_id"], task["workflow_revision_id"]).column(failure_column)
                direct_terminal = target.terminal == "failed"
                if not direct_terminal:
                    pending_deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="milliseconds")
                    db.execute(
                        "UPDATE v1_tasks SET status='pending',pending_deadline_at=?,current_column=?,attempt=0,error=?,lease_owner=NULL,lease_until=NULL,state_version=state_version+1,updated_at=? WHERE id=?",
                        (pending_deadline, failure_column, error, now, task["id"]),
                    )
                data = {"error": error, "error_category": error_category, "retryable": retryable, "attempt": next_attempt, "next": failure_column, "failure_fingerprint": failure_fingerprint, "repeated": repeated}
                self._event(db, task["project_id"], task["id"], run_id, "retry.exhausted", data)
                self._mailbox(db, task["project_id"], "retry.exhausted", task["id"], run_id, data)
            else:
                not_before = (datetime.now(timezone.utc) + timedelta(seconds=max(0, backoff_seconds))).isoformat(timespec="milliseconds")
                db.execute("UPDATE v1_tasks SET status='recovering',attempt=?,error=?,lease_owner=NULL,lease_until=NULL,not_before=?,state_version=state_version+1,updated_at=? WHERE id=?", (next_attempt, error, not_before, now, task["id"]))
                self._event(db, task["project_id"], task["id"], run_id, "column.recovering", {"error": error, "error_category": error_category, "attempt": next_attempt, "not_before": not_before})
            self._refresh_projection(db, task["project_id"])
        if direct_terminal:
            self._fail_task_now(task, error, "retry_exhausted")

    def create_await_handle(
        self, task: dict[str, Any], run_id: str, *, provider: str, token: str | None,
        poll_capability: str, poll_arguments: dict[str, Any], next_check_seconds: int,
        stale_seconds: int, timeout_seconds: int, success_outcome: str, timeout_outcome: str,
        waiting_kind: str = "external", soft_deadline_seconds: int = 300,
        resume_condition: dict[str, Any] | None = None, cancel_capability: str | None = None,
        cancel_arguments: dict[str, Any] | None = None, cleanup_capability: str | None = None,
        cleanup_arguments: dict[str, Any] | None = None, idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        handle_id, now = new_id("await"), utcnow()
        instant = datetime.now(timezone.utc)
        next_check = (instant + timedelta(seconds=next_check_seconds)).isoformat(timespec="milliseconds")
        stale_at = (instant + timedelta(seconds=stale_seconds)).isoformat(timespec="milliseconds")
        deadline = (instant + timedelta(seconds=timeout_seconds)).isoformat(timespec="milliseconds")
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_await_handles(id,project_id,task_id,run_id,provider,token,status,next_check_at,stale_at,hard_deadline_at,progress_json,created_at,updated_at,column_key,poll_capability,poll_arguments_json,success_outcome,timeout_outcome,result_json,soft_deadline_at,waiting_kind,resume_condition_json,cancel_capability,cancel_arguments_json,cleanup_capability,cleanup_arguments_json,idempotency_key) "
                "VALUES(?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (handle_id, task["project_id"], task["id"], run_id, provider, token, next_check, stale_at, deadline, "{}", now, now, task["current_column"], poll_capability, json.dumps(poll_arguments, ensure_ascii=False), success_outcome, timeout_outcome, "{}", (instant + timedelta(seconds=min(timeout_seconds, soft_deadline_seconds))).isoformat(timespec="milliseconds"), waiting_kind, json.dumps(resume_condition or {}), cancel_capability, json.dumps(cancel_arguments or {}), cleanup_capability, json.dumps(cleanup_arguments or {}), idempotency_key),
            )
            db.execute("UPDATE v1_column_runs SET status='waiting',last_progress_at=? WHERE id=?", (now, run_id))
            db.execute("UPDATE v1_tasks SET status='waiting',lease_owner=NULL,lease_until=NULL,state_version=state_version+1,updated_at=? WHERE id=?", (now, task["id"]))
            self._event(db, task["project_id"], task["id"], run_id, "column.waiting", {"await_handle_id": handle_id, "next_check_at": next_check, "hard_deadline_at": deadline})
            self._mailbox(db, task["project_id"], "task_waiting", task["id"], run_id, {"await_handle_id": handle_id})
            self._refresh_projection(db, task["project_id"])
        return self.await_handle(handle_id)

    def await_handle(self, handle_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute("SELECT * FROM v1_await_handles WHERE id=?", (handle_id,)).fetchone())
        if not row:
            raise KeyError(handle_id)
        return self._decode(row, "progress_json", "poll_arguments_json", "result_json", "resume_condition_json", "cancel_arguments_json", "cleanup_arguments_json")  # type: ignore[return-value]

    def due_await_handles(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT h.* FROM v1_await_handles h JOIN v1_tasks t ON t.id=h.task_id "
                "WHERE h.status='pending' AND h.next_check_at<=? AND t.control_state='active' "
                "ORDER BY h.next_check_at LIMIT ?",
                (utcnow(), min(limit, 500)),
            ).fetchall()
        return [self._decode(dict(row), "progress_json", "poll_arguments_json", "result_json", "resume_condition_json", "cancel_arguments_json", "cleanup_arguments_json") for row in rows]  # type: ignore[misc]

    def mark_await_health(self, handle_id: str, health: str, event_type: str) -> None:
        with self.tx(immediate=True) as db:
            row = db.execute("SELECT project_id,task_id,run_id FROM v1_await_handles WHERE id=? AND status='pending' AND health!=?", (handle_id, health)).fetchone()
            if not row:
                return
            db.execute("UPDATE v1_await_handles SET health=?,updated_at=? WHERE id=?", (health, utcnow(), handle_id))
            self._event(db, row[0], row[1], row[2], event_type, {"await_handle_id": handle_id, "health": health})
            self._mailbox(db, row[0], event_type.replace('.', '_'), row[1], row[2], {"await_handle_id": handle_id, "health": health})

    def settle_await_handle(self, handle_id: str, status: str, result: dict[str, Any], *, next_check_seconds: int = 30) -> dict[str, Any]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            row = db.execute("SELECT h.*,t.project_id FROM v1_await_handles h JOIN v1_tasks t ON t.id=h.task_id WHERE h.id=?", (handle_id,)).fetchone()
            if not row:
                raise KeyError(handle_id)
            if status == "pending":
                next_check = (datetime.now(timezone.utc) + timedelta(seconds=next_check_seconds)).isoformat(timespec="milliseconds")
                changed = db.execute("UPDATE v1_await_handles SET next_check_at=?,progress_json=?,updated_at=? WHERE id=? AND status='pending'", (next_check, json.dumps(result, ensure_ascii=False), now, handle_id)).rowcount
            else:
                changed = db.execute("UPDATE v1_await_handles SET status=?,result_json=?,updated_at=? WHERE id=? AND status='pending'", (status, json.dumps(result, ensure_ascii=False), now, handle_id)).rowcount
                if not changed:
                    return self.await_handle(handle_id)
                self._event(db, row["project_id"], row["task_id"], row["run_id"], f"await.{status}", {"await_handle_id": handle_id, "result": result})
        return self.await_handle(handle_id)

    def runs(self, project_id: str, task_id: str, limit: int = 200, after_sequence: int = 0) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_column_runs WHERE project_id=? AND task_id=? AND sequence>? ORDER BY sequence LIMIT ?", (project_id, task_id, after_sequence, min(max(limit, 1), 500))).fetchall()
        return [self._decode(dict(row), "input_json", "output_json") for row in rows]  # type: ignore[misc]

    def begin_agent_run(
        self,
        *,
        project_id: str,
        kind: str,
        instruction_revision: int,
        instruction_snapshot: str,
        context_snapshot: dict[str, Any],
        capabilities: list[str],
        task_id: str | None = None,
        column_run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id, now = new_id("arun"), utcnow()
        packed_context, packed_capabilities = self._pack_json(context_snapshot), self._pack_json(capabilities)
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_agent_runs(id,project_id,task_id,column_run_id,kind,status,instruction_revision,instruction_snapshot,context_json,capabilities_json,created_at,started_at) "
                "VALUES(?,?,?,?,?,'running',?,?,?,?,?,?)",
                (
                    run_id,
                    project_id,
                    task_id,
                    column_run_id,
                    kind,
                    instruction_revision,
                    instruction_snapshot,
                    packed_context,
                    packed_capabilities,
                    now,
                    now,
                ),
            )
            if column_run_id:
                db.execute("UPDATE v1_column_runs SET agent_run_id=? WHERE id=?", (run_id, column_run_id))
            self._event(db, project_id, task_id, column_run_id, "agent.started", {"agent_run_id": run_id, "kind": kind})
        return self.get_agent_run(project_id, run_id)

    def finish_agent_run(
        self,
        agent_run_id: str,
        status: str,
        final_text: str,
        error: str | None,
        iterations: int,
        tool_calls: int,
    ) -> dict[str, Any]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            row = db.execute("SELECT project_id,task_id,column_run_id,kind FROM v1_agent_runs WHERE id=?", (agent_run_id,)).fetchone()
            if not row:
                raise KeyError(agent_run_id)
            db.execute(
                "UPDATE v1_agent_runs SET status=?,final_text=?,error=?,iterations=?,tool_calls=?,finished_at=? WHERE id=?",
                (status, final_text[:30_000], error, iterations, tool_calls, now, agent_run_id),
            )
            self._event(db, row[0], row[1], row[2], "agent.finished", {"agent_run_id": agent_run_id, "kind": row[3], "status": status, "error": error})
        return self.get_agent_run(row[0], agent_run_id)

    def get_agent_run(self, project_id: str, agent_run_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute("SELECT * FROM v1_agent_runs WHERE id=? AND project_id=?", (agent_run_id, project_id)).fetchone())
        if not row:
            raise KeyError(agent_run_id)
        return self._decode(row, "context_json", "capabilities_json")  # type: ignore[return-value]

    def agent_runs(self, *, project_id: str, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where: list[str] = ["project_id=?"]
        values: list[Any] = [project_id]
        if task_id:
            where.append("task_id=?")
            values.append(task_id)
        values.append(min(max(limit, 1), 500))
        clause = f"WHERE {' AND '.join(where)}"
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM v1_agent_runs {clause} ORDER BY created_at DESC LIMIT ?", values).fetchall()
        return [self._decode(dict(row), "context_json", "capabilities_json") for row in rows]  # type: ignore[misc]

    def add_agent_message(
        self,
        agent_run_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        packed_content, packed_calls = self._pack_text(content), self._pack_json(tool_calls or [])
        with self.tx(immediate=True) as db:
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM v1_agent_messages WHERE agent_run_id=?", (agent_run_id,)).fetchone()[0]
            db.execute(
                "INSERT INTO v1_agent_messages(project_id,agent_run_id,sequence,role,content,tool_calls_json,tool_call_id,created_at) VALUES((SELECT project_id FROM v1_agent_runs WHERE id=?),?,?,?,?,?,?,?)",
                (agent_run_id, agent_run_id, sequence, role, packed_content, packed_calls, tool_call_id, now),
            )
        return {"agent_run_id": agent_run_id, "sequence": sequence, "role": role, "content": content, "tool_calls": tool_calls or [], "tool_call_id": tool_call_id, "created_at": now}

    def agent_messages(self, project_id: str, agent_run_id: str, limit: int = 200, after_sequence: int = 0) -> list[dict[str, Any]]:
        self.get_agent_run(project_id, agent_run_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_agent_messages WHERE project_id=? AND agent_run_id=? AND sequence>? ORDER BY sequence LIMIT ?", (project_id, agent_run_id, after_sequence, min(max(limit, 1), 500))).fetchall()
        return [self._decode(dict(row), "tool_calls_json") for row in rows]  # type: ignore[misc]

    def record_tool_invocation(
        self,
        *,
        agent_run_id: str,
        tool_call_id: str,
        capability: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        ok: bool,
    ) -> dict[str, Any]:
        now = utcnow()
        packed_arguments, packed_result = self._pack_json(arguments), self._pack_json(result)
        with self.tx(immediate=True) as db:
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM v1_tool_invocations WHERE agent_run_id=?", (agent_run_id,)).fetchone()[0]
            cursor = db.execute(
                "INSERT INTO v1_tool_invocations(project_id,agent_run_id,sequence,tool_call_id,capability,arguments_json,result_json,ok,created_at) VALUES((SELECT project_id FROM v1_agent_runs WHERE id=?),?,?,?,?,?,?,?,?)",
                (agent_run_id, agent_run_id, sequence, tool_call_id, capability, packed_arguments, packed_result, int(ok), now),
            )
        return {"id": cursor.lastrowid, "agent_run_id": agent_run_id, "sequence": sequence, "tool_call_id": tool_call_id, "capability": capability, "arguments": arguments, "result": result, "ok": ok, "created_at": now}

    def tool_invocations(self, project_id: str, agent_run_id: str, limit: int = 200, after_sequence: int = 0) -> list[dict[str, Any]]:
        self.get_agent_run(project_id, agent_run_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_tool_invocations WHERE project_id=? AND agent_run_id=? AND sequence>? ORDER BY sequence LIMIT ?", (project_id, agent_run_id, after_sequence, min(max(limit, 1), 500))).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._decode(dict(row), "arguments_json", "result_json")
            item["ok"] = bool(item["ok"])
            result.append(item)
        return result

    def register_artifact(self, project_id: str, task_id: str | None, run_id: str | None, kind: str, path: str, sha256: str, size: int, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        artifact_id, now = new_id("art"), utcnow()
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_artifacts(id,project_id,task_id,run_id,kind,path,sha256,size,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET id=excluded.id,task_id=excluded.task_id,run_id=excluded.run_id,kind=excluded.kind,sha256=excluded.sha256,size=excluded.size,meta_json=excluded.meta_json,created_at=excluded.created_at",
                (artifact_id, project_id, task_id, run_id, kind, path, sha256, size, json.dumps(meta or {}, ensure_ascii=False), now),
            )
            self._event(db, project_id, task_id, run_id, "artifact.written", {"path": path, "kind": kind, "size": size})
        return {"id": artifact_id, "path": path, "kind": kind, "size": size, "sha256": sha256}

    def artifacts(self, project_id: str, task_id: str, limit: int = 200, after: str = "") -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_artifacts WHERE project_id=? AND task_id=? AND created_at>? ORDER BY created_at LIMIT ?", (project_id, task_id, after, min(max(limit, 1), 500))).fetchall()
        return [self._decode(dict(row), "meta_json") for row in rows]  # type: ignore[misc]

    def events(self, project_id: str | None = None, task_id: str | None = None, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        where, values = ["id>?"], [after]
        if project_id:
            where.append("project_id=?")
            values.append(project_id)
        if task_id:
            where.append("task_id=?")
            values.append(task_id)
        values.append(min(max(limit, 1), 500))
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM v1_events WHERE {' AND '.join(where)} ORDER BY id LIMIT ?", values).fetchall()
        return [self._decode(dict(row), "data_json") for row in rows]  # type: ignore[misc]

    def mailbox(self, project_id: str, *, state: str = "pending", limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_project_mailbox WHERE project_id=? AND state=? ORDER BY id LIMIT ?",
                (project_id, state, min(max(limit, 1), 500)),
            ).fetchall()
        return [self._decode(dict(row), "payload_json") for row in rows]  # type: ignore[misc]

    def schedule_review(self, project_id: str, reason: str, due_at: str) -> dict[str, Any]:
        self.get_project(project_id)
        review_id, now = new_id("review"), utcnow()
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_scheduled_reviews(id,project_id,reason,due_at,state,created_at) VALUES(?,?,?,?,'pending',?)",
                (review_id, project_id, reason[:4000], due_at, now),
            )
            self._event(db, project_id, None, None, "supervision.review_scheduled", {"review_id": review_id, "reason": reason[:4000], "due_at": due_at})
        return {"id": review_id, "project_id": project_id, "reason": reason[:4000], "due_at": due_at, "state": "pending", "created_at": now}

    def record_governance_decision(self, project_id: str, kind: str, subject_id: str | None, decision: str, data: dict[str, Any]) -> dict[str, Any]:
        decision_id, now = new_id("gdec"), utcnow()
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_governance_decisions(id,project_id,kind,subject_id,decision,data_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (decision_id, project_id, kind, subject_id, decision, json.dumps(data, ensure_ascii=False), now),
            )
            if subject_id and kind == "intervention":
                db.execute("UPDATE v1_tasks SET supervision_action=? WHERE id=? AND project_id=?", (decision, subject_id, project_id))
            if kind == "direct_execution":
                db.execute(
                    "INSERT INTO v1_direct_runs(id,project_id,agent_run_id,capability,decision,data_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (new_id("direct"), project_id, data.get("agent_run_id"), str(data.get("capability") or "unknown"), decision, json.dumps(data, ensure_ascii=False), now),
                )
            elif kind == "intervention":
                db.execute(
                    "INSERT INTO v1_intervention_runs(id,project_id,task_id,decision,data_json,created_at) VALUES(?,?,?,?,?,?)",
                    (new_id("intervention"), project_id, subject_id, decision, json.dumps(data, ensure_ascii=False), now),
                )
            self._event(db, project_id, subject_id if kind == "intervention" else None, None, f"governance.{kind}", {"decision_id": decision_id, "decision": decision})
        return {"id": decision_id, "project_id": project_id, "kind": kind, "subject_id": subject_id, "decision": decision, "data": data, "created_at": now}

    def start_execution_receipt(self, project_id: str, execution_key: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        packed = self._pack_json(arguments)
        with self.tx(immediate=True) as db:
            existing = db.execute("SELECT * FROM v1_execution_receipts WHERE project_id=? AND execution_key=?", (project_id, execution_key)).fetchone()
            if existing:
                item = self._decode(dict(existing), "arguments_json", "result_json")
                if item["status"] == "failed":
                    db.execute("UPDATE v1_execution_receipts SET status='started',arguments_json=?,result_json=NULL,error=NULL,started_at=?,finished_at=NULL WHERE id=?", (packed, now, item["id"]))
                    item.update({"status": "started", "arguments": arguments, "execution_key": execution_key, "claimed": True})
                    return item
                item["claimed"] = False
                return item
            receipt_id = new_id("receipt")
            db.execute("INSERT INTO v1_execution_receipts(id,project_id,execution_key,capability,status,arguments_json,started_at) VALUES(?,?,?,?, 'started',?,?)", (receipt_id, project_id, execution_key, capability, packed, now))
        return {"id": receipt_id, "project_id": project_id, "execution_key": execution_key, "capability": capability, "status": "started", "arguments": arguments, "claimed": True}

    def finish_execution_receipt(self, project_id: str, execution_key: str, ok: bool, result: Any, error: str | None) -> None:
        packed = self._pack_json(result) if result is not None else None
        with self.tx(immediate=True) as db:
            db.execute("UPDATE v1_execution_receipts SET status=?,result_json=?,error=?,finished_at=? WHERE project_id=? AND execution_key=? AND status='started'", ("completed" if ok else "failed", packed, error, utcnow(), project_id, execution_key))

    def governance_decisions(self, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_governance_decisions WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, min(max(limit, 1), 500))).fetchall()
        return [self._decode(dict(row), "data_json") for row in rows]  # type: ignore[misc]

    def project_projection(self, project_id: str) -> dict[str, Any]:
        self.get_project(project_id)
        with self.connect() as db:
            row = db.execute("SELECT version,projection_json,updated_at FROM v1_kanban_projection WHERE project_id=?", (project_id,)).fetchone()
            if not row:
                self._refresh_projection(db, project_id)
                row = db.execute("SELECT version,projection_json,updated_at FROM v1_kanban_projection WHERE project_id=?", (project_id,)).fetchone()
        assert row is not None
        return {"project_id": project_id, "version": row[0], "projection": json.loads(row[1]), "updated_at": row[2]}

    def observe_mailbox(self, project_id: str, message_id: int) -> bool:
        now = utcnow()
        with self.tx(immediate=True) as db:
            cursor = db.execute(
                "UPDATE v1_project_mailbox SET state='observed',observed_at=? "
                "WHERE id=? AND project_id=? AND state='pending'",
                (now, message_id, project_id),
            )
        return cursor.rowcount == 1

    def _event(self, db: sqlite3.Connection, project_id: str, task_id: str | None, run_id: str | None, event_type: str, data: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO v1_events(project_id,task_id,run_id,type,data_json,created_at) VALUES(?,?,?,?,?,?)",
            (project_id, task_id, run_id, event_type, self._compact_json(data, 64_000), utcnow()),
        )
        db.execute("UPDATE v1_projects SET state_version=state_version+1,updated_at=? WHERE id=?", (utcnow(), project_id))

    @staticmethod
    def _refresh_projection(db: sqlite3.Connection, project_id: str) -> None:
        version_row = db.execute("SELECT state_version FROM v1_projects WHERE id=?", (project_id,)).fetchone()
        if not version_row:
            return
        workflow_row = db.execute("SELECT id,revision,definition_json FROM v1_workflow_revisions WHERE project_id=? AND active=1", (project_id,)).fetchone()
        tasks = []
        for row in db.execute(
            "SELECT id,title,substr(brief,1,1000) AS brief,status,current_column,attempt,error,state_version,terminal_artifact_id,notified_at,observed_at,supervision_action,created_at,updated_at FROM v1_tasks WHERE project_id=? ORDER BY created_at DESC LIMIT 100",
            (project_id,),
        ):
            tasks.append(dict(row))
        projection = {
            "workflow": ({"id": workflow_row[0], "revision": workflow_row[1], "definition": json.loads(workflow_row[2])} if workflow_row else None),
            "tasks": tasks,
        }
        now = utcnow()
        db.execute(
            "INSERT INTO v1_kanban_projection(project_id,version,projection_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET version=excluded.version,projection_json=excluded.projection_json,updated_at=excluded.updated_at",
            (project_id, int(version_row[0]), json.dumps(projection, ensure_ascii=False), now),
        )

    def _mailbox(
        self,
        db: sqlite3.Connection,
        project_id: str,
        event_type: str,
        task_id: str | None,
        run_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        db.execute(
            "INSERT INTO v1_project_mailbox(project_id,event_type,task_id,run_id,payload_json,state,created_at) "
            "VALUES(?,?,?,?,?,'pending',?)",
            (project_id, event_type, task_id, run_id, self._compact_json(payload, 64_000), utcnow()),
        )

    @staticmethod
    def _compact_json(value: Any, max_bytes: int) -> str:
        data = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        if len(data) <= max_bytes:
            return data.decode("utf-8")
        return json.dumps({"truncated": True, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}, ensure_ascii=False)

from __future__ import annotations

import sqlite3
from app.v1.repositories.base import StoreHost

from app.v1.states import (
    AGENT_RUN_STATE_MACHINE,
    ATTEMPT_STATE_MACHINE,
    COLUMN_RUN_STATE_MACHINE,
    TASK_STATE_MACHINE,
)
class SchemaRepository:
    def __init__(self, store: StoreHost):
        self.store = store

    def init_schema(self) -> None:
        with self.store._schema_lock, self.store.connect() as db:
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
                CREATE TABLE IF NOT EXISTS v1_platform_policy_revisions (
                    revision INTEGER PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL, source_path TEXT NOT NULL, created_at TEXT NOT NULL
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
                    task_id TEXT, agent_run_id TEXT, error TEXT, resolved_by_job_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_message_id) REFERENCES v1_conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_conversation_jobs_dispatch
                    ON v1_conversation_jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_v1_conversation_jobs_project
                    ON v1_conversation_jobs(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS v1_workflows (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    active_revision_id TEXT, state_version INTEGER NOT NULL DEFAULT 0,
                    source_loop_key TEXT, source_loop_version TEXT, source_loop_digest TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS v1_project_loop_bindings (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    loop_key TEXT NOT NULL, loop_version TEXT NOT NULL, loop_digest TEXT NOT NULL,
                    bindings_json TEXT NOT NULL, workflow_plan_id TEXT NOT NULL,
                    workflow_revision_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(workflow_plan_id) REFERENCES v1_workflow_plans(id),
                    FOREIGN KEY(workflow_revision_id) REFERENCES v1_workflow_revisions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_v1_project_loop_bindings_project
                    ON v1_project_loop_bindings(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS v1_workflow_plans (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, schema_version TEXT NOT NULL,
                    plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(project_id, plan_hash),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_workflow_plans_project
                    ON v1_workflow_plans(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS v1_workflow_revisions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    definition_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    workflow_plan_id TEXT, created_at TEXT NOT NULL,
                    UNIQUE(project_id, revision),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(workflow_plan_id) REFERENCES v1_workflow_plans(id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_v1_workflow_active
                    ON v1_workflow_revisions(project_id) WHERE active=1;
                CREATE TABLE IF NOT EXISTS v1_task_plans (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    workflow_revision_id TEXT NOT NULL, schema_version TEXT NOT NULL,
                    objective TEXT NOT NULL, plan_json TEXT NOT NULL,
                    plan_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(project_id, plan_hash),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(workflow_revision_id) REFERENCES v1_workflow_revisions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_v1_task_plans_project
                    ON v1_task_plans(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS v1_tasks (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workflow_revision_id TEXT NOT NULL,
                    task_plan_id TEXT NOT NULL, proposed_task_ref TEXT NOT NULL,
                    title TEXT NOT NULL, brief TEXT NOT NULL, input_json TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL,
                    control_state TEXT NOT NULL DEFAULT 'active',
                    rerun_of_task_id TEXT, resolved_by_task_id TEXT,
                    current_column TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT, lease_until TEXT, error TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(workflow_revision_id) REFERENCES v1_workflow_revisions(id),
                    FOREIGN KEY(task_plan_id) REFERENCES v1_task_plans(id)
                );
                CREATE INDEX IF NOT EXISTS idx_v1_tasks_dispatch
                    ON v1_tasks(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_v1_tasks_project
                    ON v1_tasks(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS v1_column_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL, column_key TEXT NOT NULL,
                    sequence INTEGER NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL,
                    input_json TEXT NOT NULL DEFAULT '{}', output_json TEXT NOT NULL DEFAULT '{}',
                    agent_run_id TEXT, error TEXT, failure_fingerprint TEXT,
                    heartbeat_at TEXT, last_progress_at TEXT,
                    started_at TEXT, finished_at TEXT, created_at TEXT NOT NULL,
                    UNIQUE(task_id, sequence),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES v1_tasks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_runs_task ON v1_column_runs(task_id, sequence);
                CREATE TABLE IF NOT EXISTS v1_column_attempts (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
                    column_run_id TEXT NOT NULL, attempt_no INTEGER NOT NULL, status TEXT NOT NULL,
                    input_json TEXT NOT NULL DEFAULT '{}', output_json TEXT NOT NULL DEFAULT '{}',
                    checkpoint_json TEXT NOT NULL DEFAULT '{}', error TEXT, error_category TEXT,
                    failure_fingerprint TEXT, runtime_policy_revision INTEGER NOT NULL,
                    runtime_policy_hash TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT, created_at TEXT NOT NULL,
                    UNIQUE(column_run_id, attempt_no),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES v1_tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY(column_run_id) REFERENCES v1_column_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_attempts_run
                    ON v1_column_attempts(column_run_id, attempt_no);
                CREATE TABLE IF NOT EXISTS v1_agent_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, column_run_id TEXT,
                    conversation_job_id TEXT,
                    kind TEXT NOT NULL, status TEXT NOT NULL,
                    instruction_revision INTEGER NOT NULL, instruction_snapshot TEXT NOT NULL,
                    context_json TEXT NOT NULL, capabilities_json TEXT NOT NULL,
                    iterations INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0,
                    final_text TEXT NOT NULL DEFAULT '', error TEXT, error_category TEXT,
                    created_at TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS v1_agent_sessions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
                    session_key TEXT NOT NULL, state TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(task_id, session_key),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES v1_tasks(id) ON DELETE CASCADE
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
                    next_check_at TEXT NOT NULL,
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
                CREATE TABLE IF NOT EXISTS v1_task_dependencies (
                    task_id TEXT NOT NULL, depends_on_task_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, required_terminal TEXT NOT NULL DEFAULT 'done', created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, depends_on_task_id),
                    FOREIGN KEY(task_id) REFERENCES v1_tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY(depends_on_task_id) REFERENCES v1_tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_task_dependencies_project
                    ON v1_task_dependencies(project_id, task_id);
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
            self._ensure_column(db, "v1_projects", "state_version", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db, "v1_conversation_agents", "lease_owner", "TEXT")
            self._ensure_column(db, "v1_conversation_agents", "lease_until", "TEXT")
            self._ensure_column(db, "v1_conversation_jobs", "trigger_kind", "TEXT NOT NULL DEFAULT 'user'")
            self._ensure_column(db, "v1_conversation_jobs", "trigger_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_conversation_jobs", "mailbox_ids_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(db, "v1_conversation_jobs", "scheduled_review_id", "TEXT")
            self._ensure_column(db, "v1_conversation_jobs", "worker_id", "TEXT")
            self._ensure_column(db, "v1_conversation_jobs", "result_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_conversation_jobs", "resolved_by_job_id", "TEXT")
            self._ensure_column(db, "v1_tasks", "readiness_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_tasks", "state_version", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(db, "v1_tasks", "terminal_artifact_id", "TEXT")
            self._ensure_column(db, "v1_tasks", "terminal_event_id", "INTEGER")
            self._ensure_column(db, "v1_tasks", "notified_at", "TEXT")
            self._ensure_column(db, "v1_tasks", "observed_at", "TEXT")
            self._ensure_column(db, "v1_tasks", "supervision_action", "TEXT")
            self._ensure_column(db, "v1_tasks", "next_retry_at", "TEXT")
            self._ensure_column(db, "v1_tasks", "control_state", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(db, "v1_tasks", "rerun_of_task_id", "TEXT")
            self._ensure_column(db, "v1_tasks", "resolved_by_task_id", "TEXT")
            self._ensure_column(db, "v1_tasks", "task_plan_id", "TEXT")
            self._ensure_column(db, "v1_tasks", "proposed_task_ref", "TEXT")
            self._ensure_column(db, "v1_tasks", "conflict_domains_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(db, "v1_scheduling_entries", "auto_admit", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db, "v1_task_dependencies", "required_terminal", "TEXT NOT NULL DEFAULT 'done'")
            self._ensure_column(db, "v1_column_runs", "error_category", "TEXT")
            self._ensure_column(db, "v1_column_runs", "runtime_policy_revision", "INTEGER")
            self._ensure_column(db, "v1_column_runs", "runtime_policy_hash", "TEXT")
            self._ensure_column(db, "v1_column_attempts", "error_code", "TEXT")
            self._ensure_column(db, "v1_column_attempts", "agent_run_id", "TEXT")
            self._ensure_column(db, "v1_column_attempts", "partial_artifacts_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(db, "v1_await_handles", "column_key", "TEXT")
            self._ensure_column(db, "v1_await_handles", "column_attempt_id", "TEXT")
            self._ensure_column(db, "v1_await_handles", "poll_capability", "TEXT")
            self._ensure_column(db, "v1_await_handles", "poll_arguments_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_await_handles", "success_outcome", "TEXT NOT NULL DEFAULT 'success'")
            self._ensure_column(db, "v1_await_handles", "result_json", "TEXT NOT NULL DEFAULT '{}'")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_v1_tasks_unresolved_failure "
                "ON v1_tasks(project_id,status,resolved_by_task_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_v1_conversation_jobs_unresolved_failure "
                "ON v1_conversation_jobs(project_id,status,resolved_by_job_id)"
            )
            for table in ("v1_column_runs", "v1_agent_messages", "v1_tool_invocations", "v1_await_handles"):
                self._ensure_column(db, table, "project_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(db, "v1_await_handles", "waiting_kind", "TEXT NOT NULL DEFAULT 'external'")
            self._ensure_column(db, "v1_await_handles", "health", "TEXT NOT NULL DEFAULT 'healthy'")
            self._ensure_column(db, "v1_await_handles", "resume_condition_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_await_handles", "cancel_capability", "TEXT")
            self._ensure_column(db, "v1_await_handles", "cancel_arguments_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_await_handles", "cleanup_capability", "TEXT")
            self._ensure_column(db, "v1_await_handles", "cleanup_arguments_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_await_handles", "idempotency_key", "TEXT")
            self._ensure_column(db, "v1_await_handles", "checkpoint_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_await_handles", "event_type", "TEXT")
            self._ensure_column(db, "v1_await_handles", "correlation_key", "TEXT")
            self._ensure_column(db, "v1_await_handles", "resume_at", "TEXT")
            self._ensure_column(db, "v1_project_mailbox", "claim_owner", "TEXT")
            self._ensure_column(db, "v1_project_mailbox", "claim_expires_at", "TEXT")
            self._ensure_column(db, "v1_project_mailbox", "acknowledged_at", "TEXT")
            self._ensure_column(db, "v1_project_mailbox", "governance_decision_id", "TEXT")
            self._ensure_column(db, "v1_project_mailbox", "event_id", "INTEGER")
            self._ensure_column(db, "v1_project_mailbox", "reported_message_id", "INTEGER")
            self._ensure_column(db, "v1_project_mailbox", "reported_at", "TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_v1_mailbox_event ON v1_project_mailbox(event_id) WHERE event_id IS NOT NULL")
            self._ensure_column(db, "v1_workflows", "source_loop_key", "TEXT")
            self._ensure_column(db, "v1_workflows", "source_loop_version", "TEXT")
            self._ensure_column(db, "v1_workflows", "source_loop_digest", "TEXT")
            self._ensure_column(db, "v1_workflow_revisions", "workflow_id", "TEXT")
            self._ensure_column(db, "v1_workflow_revisions", "revision_no", "INTEGER")
            self._ensure_column(db, "v1_workflow_revisions", "schema_version", "TEXT NOT NULL DEFAULT 'devwerk.workflow.v1'")
            self._ensure_column(db, "v1_workflow_revisions", "definition_hash", "TEXT")
            self._ensure_column(db, "v1_workflow_revisions", "workflow_plan_id", "TEXT")
            self._ensure_column(db, "v1_agent_runs", "platform_policy_revision", "INTEGER")
            self._ensure_column(db, "v1_agent_runs", "platform_policy_hash", "TEXT")
            self._ensure_column(db, "v1_agent_runs", "runtime_policy_revision", "INTEGER")
            self._ensure_column(db, "v1_agent_runs", "runtime_policy_hash", "TEXT")
            self._ensure_column(db, "v1_agent_runs", "checkpoint_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "v1_agent_runs", "agent_session_id", "TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_v1_agent_runs_session "
                "ON v1_agent_runs(agent_session_id, created_at)"
            )
            self._ensure_column(db, "v1_agent_runs", "error_code", "TEXT")
            self._ensure_column(db, "v1_agent_runs", "error_category", "TEXT")
            self._ensure_column(db, "v1_agent_runs", "duration_seconds", "REAL")
            self._ensure_column(db, "v1_agent_runs", "column_attempt_id", "TEXT")
            self._ensure_column(db, "v1_agent_runs", "conversation_job_id", "TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_v1_agent_runs_conversation_job "
                "ON v1_agent_runs(conversation_job_id, created_at)"
            )
            unprovenanced = db.execute(
                "SELECT id,project_id FROM v1_workflows "
                "WHERE source_loop_key IS NULL OR source_loop_version IS NULL OR source_loop_digest IS NULL"
            ).fetchall()
            if unprovenanced:
                raise RuntimeError(
                    "database contains Workflow identities without filesystem Loop provenance; "
                    "remove the pre-Loop database before starting DevWerk"
                )
            db.execute("UPDATE v1_column_runs SET project_id=(SELECT project_id FROM v1_tasks WHERE id=v1_column_runs.task_id) WHERE project_id='' ")
            db.execute("UPDATE v1_agent_messages SET project_id=(SELECT project_id FROM v1_agent_runs WHERE id=v1_agent_messages.agent_run_id) WHERE project_id='' ")
            db.execute("UPDATE v1_tool_invocations SET project_id=(SELECT project_id FROM v1_agent_runs WHERE id=v1_tool_invocations.agent_run_id) WHERE project_id='' ")
            db.execute("UPDATE v1_await_handles SET project_id=(SELECT project_id FROM v1_tasks WHERE id=v1_await_handles.task_id) WHERE project_id='' ")
            # Preset definitions are filesystem Loops. Remove the obsolete SQLite copies
            # after the new schema is ready; active Project Workflow revisions remain intact.
            db.execute("DROP TABLE IF EXISTS v1_project_template_applications")
            db.execute("DROP TABLE IF EXISTS v1_workflow_templates")
            self._validate_persisted_runtime_statuses(db)

    @staticmethod
    def _validate_persisted_runtime_statuses(db: sqlite3.Connection) -> None:
        definitions = (
            ("v1_tasks", TASK_STATE_MACHINE),
            ("v1_column_runs", COLUMN_RUN_STATE_MACHINE),
            ("v1_column_attempts", ATTEMPT_STATE_MACHINE),
            ("v1_agent_runs", AGENT_RUN_STATE_MACHINE),
        )
        for table, machine in definitions:
            for row in db.execute(f"SELECT DISTINCT status FROM {table}").fetchall():
                try:
                    machine.parse(row[0])
                except ValueError as exc:
                    raise RuntimeError(
                        f"{table} contains unknown Runtime status {row[0]!r}"
                    ) from exc
        invalid_tools = db.execute(
            "SELECT DISTINCT ok FROM v1_tool_invocations WHERE ok NOT IN (0,1)"
        ).fetchall()
        if invalid_tools:
            raise RuntimeError(
                f"v1_tool_invocations contains invalid ok values: {[row[0] for row in invalid_tools]}"
            )

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.v1.domain import (
    CapabilitySequenceExecutor,
    OrchestrationPlan,
    ReadinessDecision,
    WorkflowDefinition,
)
from app.v1.contracts import canonicalize_contract_value, check_schema, validate_contract
from app.v1.capabilities import (
    CapabilityRegistry,
    task_binding_exact_strings,
    validate_task_capability_bindings,
    validate_workflow_capabilities,
)
from app.v1.files import ProjectFiles
from app.v1.loops import LoopCatalog
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, PlatformPolicySnapshot, V1RuntimePolicy
from app.v1.states import (
    AGENT_RUN_STATE_MACHINE,
    ATTEMPT_STATE_MACHINE,
    COLUMN_RUN_STATE_MACHINE,
    TASK_STATE_MACHINE,
    AttemptStatus,
    ColumnRunStatus,
    TaskStatus,
    ToolInvocationStatus,
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _resource_domains_overlap(left: list[Any], right: list[Any]) -> bool:
    """Conservative V1 overlap check for normalized scheduling conflict domains."""
    for first in left:
        for second in right:
            if not isinstance(first, str) or not isinstance(second, str) or ":" not in first or ":" not in second:
                return True
            first_kind, first_identity = first.split(":", 1)
            second_kind, second_identity = second.split(":", 1)
            if first_kind != second_kind:
                continue
            if not first_identity or not second_identity:
                return True
            if first_kind == "workspace_path":
                a, b = first_identity.rstrip("/"), second_identity.rstrip("/")
                if a == b or a.startswith(f"{b}/") or b.startswith(f"{a}/"):
                    return True
            elif first_identity == second_identity:
                return True
    return False


def _normalized_unit_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return re.sub(r"^(?:task|t)_", "", normalized)


def _numbered_unit_stem(value: str) -> str:
    return re.sub(r"(?:_\d+)+$", "", _normalized_unit_identifier(value))


def _resolve_loop_parameters(value: Any, parameters: dict[str, Any]) -> Any:
    """Resolve exact Loop parameters without evaluating code or free-form expressions."""
    if isinstance(value, dict):
        if set(value) == {"$param"}:
            key = str(value["$param"])
            if key not in parameters:
                raise ValueError(f"Loop parameter {key!r} is missing")
            return json.loads(json.dumps(parameters[key], ensure_ascii=False))
        return {key: _resolve_loop_parameters(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_loop_parameters(item, parameters) for item in value]
    return value


def _validate_process_stage_alignment(
    plan: OrchestrationPlan,
    workflow: WorkflowDefinition,
    policy: V1RuntimePolicy,
) -> None:
    """Reject the structural Task-as-Column shape without business semantics."""
    column_ids = {_normalized_unit_identifier(column.key) for column in workflow.columns}
    task_ids = {
        _normalized_unit_identifier(task.proposed_task_ref)
        for task in plan.task_portfolio
    }
    mirrored = sorted(column_ids & task_ids)
    if len(mirrored) >= 2:
        raise ValueError(
            "Workflow Columns mirror Task work-unit identifiers "
            f"{mirrored}; keep work units in Tasks and model reusable process stages as Columns"
        )

    column_stems: dict[str, int] = {}
    task_stems: dict[str, int] = {}
    for identifier in column_ids:
        stem = _numbered_unit_stem(identifier)
        column_stems[stem] = column_stems.get(stem, 0) + 1
    for identifier in task_ids:
        stem = _numbered_unit_stem(identifier)
        task_stems[stem] = task_stems.get(stem, 0) + 1
    repeated_mirrors = sorted(
        stem
        for stem in set(column_stems) & set(task_stems)
        if column_stems[stem] >= 2 and task_stems[stem] >= 2
    )
    if repeated_mirrors:
        raise ValueError(
            "Workflow contains numbered Column families that mirror Task work-unit families "
            f"{repeated_mirrors}; one reusable process Workflow must apply to every Task"
        )

    workflow_columns = {column.key: column for column in workflow.columns}
    planned_columns = {column.key: column for column in plan.columns}
    if set(workflow_columns) != set(planned_columns):
        raise ValueError("Workflow columns must exactly match the referenced orchestration plan")

    for key, planned in planned_columns.items():
        actual_mode = workflow_columns[key].executor.kind
        if planned.execution_mode != actual_mode:
            raise ValueError(
                f"Workflow Column {key!r} executor {actual_mode!r} does not match "
                f"the orchestration plan execution_mode {planned.execution_mode!r}"
            )

    # The Workflow is the executable declaration. Derive the representative
    # success route from that declaration instead of requiring the model to
    # author a second field-for-field copy of it in the plan.
    pending = [workflow.entry]
    visited: set[str] = set()
    reaches_done = False
    while pending:
        key = pending.pop()
        if key == workflow.terminals.success:
            reaches_done = True
            break
        if key == workflow.terminals.failure or key in visited:
            continue
        visited.add(key)
        pending.extend(item.target for item in workflow_columns[key].transitions)
    if not reaches_done:
        raise ValueError("Workflow entry has no declared path to done")

    current = workflow.entry
    terminal: str | None = None
    for index, step in enumerate(plan.representative_task_walkthrough):
        if step.column_key != current:
            raise ValueError(
                "Representative Task walkthrough does not follow the Workflow graph: "
                f"expected Column {current!r}, received {step.column_key!r} at step {index + 1}"
            )
        matches = [
            transition
            for transition in workflow_columns[current].transitions
            if transition.outcome == step.outcome
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Representative Task walkthrough outcome {step.outcome!r} is not a unique "
                f"transition from Column {current!r}"
            )
        target = matches[0].target
        if target in {workflow.terminals.success, workflow.terminals.failure}:
            terminal = target
            if index != len(plan.representative_task_walkthrough) - 1:
                raise ValueError("Representative Task walkthrough continues after a terminal transition")
        else:
            current = target
    if terminal != workflow.terminals.success:
        raise ValueError("Representative Task walkthrough must reach the Workflow success terminal")



def _workspace_deliverable_path(value: Any) -> str | None:
    """Return a normalized relative path only when a readiness deliverable is path-shaped."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().replace("\\", "/")
    if not candidate or any(character.isspace() for character in candidate):
        return None
    if candidate.startswith(("/", "./")) or ":" in candidate:
        return None
    parts = [part for part in candidate.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    if "/" not in candidate and "." not in parts[-1]:
        return None
    return "/".join(parts)


def _validate_deterministic_deliverable_coverage(
    workflow: WorkflowDefinition,
    readiness: dict[str, Any],
) -> None:
    """Prevent a static-only Workflow from accepting more file outputs than it declares."""
    if not all(isinstance(column.executor, CapabilitySequenceExecutor) for column in workflow.columns):
        return
    declared_paths = {
        normalized
        for column in workflow.columns
        for step in column.executor.steps
        if step.capability == "project.files.write"
        for normalized in [_workspace_deliverable_path(step.arguments.get("path"))]
        if normalized is not None
    }
    if not declared_paths:
        return
    expected_paths = {
        normalized
        for item in readiness.get("deliverables") or []
        for normalized in [_workspace_deliverable_path(item)]
        if normalized is not None
    }
    missing = sorted(expected_paths - declared_paths)
    if missing:
        raise ValueError(
            "Deterministic Workflow does not declare every path-shaped Task deliverable: "
            + ", ".join(missing)
        )


class V1Store:
    def __init__(
        self,
        db_path: str,
        policy: V1RuntimePolicy | None = None,
        *,
        registry: CapabilityRegistry,
    ):
        self.policy = policy or DEFAULT_V1_RUNTIME_POLICY
        self.registry = registry
        self.loops = LoopCatalog()
        self.path = Path(db_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        timeout_seconds = self.policy.service_limits.sqlite_busy_timeout_milliseconds / 1_000
        connection = sqlite3.connect(self.path, timeout=timeout_seconds, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.policy.service_limits.sqlite_busy_timeout_milliseconds}")
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
                CREATE TABLE IF NOT EXISTS v1_loop_applications (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    loop_key TEXT NOT NULL, loop_version TEXT NOT NULL, loop_digest TEXT NOT NULL,
                    bindings_json TEXT NOT NULL, orchestration_plan_id TEXT NOT NULL,
                    workflow_revision_id TEXT NOT NULL, task_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(orchestration_plan_id) REFERENCES v1_orchestration_plans(id),
                    FOREIGN KEY(workflow_revision_id) REFERENCES v1_workflow_revisions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_v1_loop_applications_project
                    ON v1_loop_applications(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS v1_orchestration_plans (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, schema_version TEXT NOT NULL,
                    plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(project_id, plan_hash),
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_v1_orchestration_plans_project
                    ON v1_orchestration_plans(project_id, created_at DESC);
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
                    control_state TEXT NOT NULL DEFAULT 'active',
                    rerun_of_task_id TEXT, resolved_by_task_id TEXT,
                    current_column TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT, lease_until TEXT, error TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, finished_at TEXT,
                    FOREIGN KEY(project_id) REFERENCES v1_projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(workflow_revision_id) REFERENCES v1_workflow_revisions(id)
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
            self._ensure_column(db, "v1_tasks", "orchestration_plan_id", "TEXT")
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
            self._ensure_column(db, "v1_workflow_revisions", "orchestration_plan_id", "TEXT")
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
            for row in db.execute(
                "SELECT id,project_id,revision,definition_json,active,created_at FROM v1_workflow_revisions"
            ).fetchall():
                definition_hash = hashlib.sha256(str(row[3]).encode("utf-8")).hexdigest()
                workflow_row = db.execute("SELECT id FROM v1_workflows WHERE project_id=?", (row[1],)).fetchone()
                workflow_id = workflow_row[0] if workflow_row else new_id("workflow")
                if not workflow_row:
                    name = str(json.loads(row[3])["name"])
                    db.execute(
                        "INSERT INTO v1_workflows(id,project_id,name,active_revision_id,state_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                        (workflow_id, row[1], name, row[0] if row[4] else None, 1, row[5], row[5]),
                    )
                elif row[4]:
                    db.execute(
                        "UPDATE v1_workflows SET active_revision_id=?,updated_at=? WHERE id=?",
                        (row[0], row[5], workflow_id),
                    )
                db.execute(
                    "UPDATE v1_workflow_revisions SET workflow_id=COALESCE(workflow_id,?),revision_no=COALESCE(revision_no,revision),definition_hash=COALESCE(definition_hash,?) WHERE id=?",
                    (workflow_id, definition_hash, row[0]),
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

    def list_loops(
        self,
        *,
        category: str | None = None,
        tag: str | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        page_limit = min(
            max(limit or self.policy.service_limits.default_page_size, 1),
            self.policy.service_limits.max_page_size,
        )
        return self.loops.list(category=category, tag=tag, query=query, limit=page_limit)

    def get_loop(self, loop_key: str) -> dict[str, Any]:
        loop = self.loops.get(loop_key)
        parameter_schema = dict(loop["parameter_schema"])
        bundle = dict(loop["bundle"])
        check_schema(parameter_schema, label=f"Loop {loop_key} parameters")
        defaults = dict(bundle.get("defaults") or {})
        if defaults:
            validate_contract(defaults, parameter_schema, label=f"Loop {loop_key} defaults")
            materialized = _resolve_loop_parameters(bundle, defaults)
            OrchestrationPlan.model_validate(materialized["orchestration_plan"])
            WorkflowDefinition.model_validate(materialized["workflow"])
        else:
            OrchestrationPlan.model_validate(bundle["orchestration_plan"])
            WorkflowDefinition.model_validate(bundle["workflow"])
        return loop

    def apply_loop(
        self,
        project_id: str,
        loop_key: str,
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        self.get_project(project_id)
        try:
            self.get_workflow(project_id)
        except KeyError:
            pass
        else:
            raise ValueError("a Loop can be applied only before the Project has a Workflow")
        loop = self.get_loop(loop_key)
        bundle = dict(loop["bundle"])
        parameters = canonicalize_contract_value(
            {**dict(bundle.get("defaults") or {}), **bindings},
            loop["parameter_schema"],
        )
        validate_contract(parameters, loop["parameter_schema"], label=f"Loop {loop_key} bindings")
        materialized = _resolve_loop_parameters(bundle, parameters)
        plan = OrchestrationPlan.model_validate(materialized["orchestration_plan"])
        workflow = WorkflowDefinition.model_validate(materialized["workflow"])
        task_specs = list(materialized.get("tasks") or [])
        if {item.proposed_task_ref for item in plan.task_portfolio} != {
            str(item.get("proposed_task_ref") or "") for item in task_specs
        }:
            raise ValueError("Loop tasks must exactly materialize the orchestration portfolio")
        plan_row = self.create_orchestration_plan(project_id, plan)
        workflow_row = self._publish_workflow_revision(
            project_id,
            workflow,
            plan_row["id"],
            initial_loop=loop,
        )
        tasks: list[dict[str, Any]] = []
        for spec in task_specs:
            tasks.append(self.create_task(
                project_id,
                str(spec["title"]),
                str(spec.get("brief") or ""),
                dict(spec.get("input") or {}),
                dict(spec["readiness"]),
                orchestration_plan_id=plan_row["id"],
                proposed_task_ref=str(spec["proposed_task_ref"]),
            ))
        application_id, now = new_id("loopapp"), utcnow()
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_loop_applications("
                "id,project_id,loop_key,loop_version,loop_digest,bindings_json,"
                "orchestration_plan_id,workflow_revision_id,task_ids_json,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    application_id,
                    project_id,
                    loop["loop_key"],
                    loop["version"],
                    loop["digest"],
                    json.dumps(parameters, ensure_ascii=False),
                    plan_row["id"],
                    workflow_row["id"],
                    json.dumps([item["id"] for item in tasks]),
                    now,
                ),
            )
            self._event(db, project_id, None, None, "workflow.loop.applied", {
                "application_id": application_id,
                "loop_key": loop["loop_key"],
                "loop_version": loop["version"],
                "loop_digest": loop["digest"],
                "workflow_revision_id": workflow_row["id"],
                "task_ids": [item["id"] for item in tasks],
            })
        return {
            "id": application_id,
            "project_id": project_id,
            "loop": {
                key: loop[key]
                for key in ("loop_key", "version", "digest", "name", "category", "directory")
            },
            "bindings": parameters,
            "orchestration_plan": plan_row,
            "workflow": workflow_row,
            "tasks": tasks,
            "created_at": now,
        }

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

    def _pack_text(self, value: str, *, max_bytes: int | None = None) -> str:
        return value

    def _pack_json(self, value: Any, *, max_bytes: int | None = None) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    def register_platform_policy(self, snapshot: PlatformPolicySnapshot) -> PlatformPolicySnapshot:
        with self.tx(immediate=True) as db:
            row = db.execute(
                "SELECT revision,content_hash,content,source_path,created_at FROM v1_platform_policy_revisions WHERE content_hash=?",
                (snapshot.content_hash,),
            ).fetchone()
            if row:
                return PlatformPolicySnapshot(row[3], row[2], row[1], int(row[0]))
            revision = int(db.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM v1_platform_policy_revisions"
            ).fetchone()[0])
            db.execute(
                "INSERT INTO v1_platform_policy_revisions(revision,content_hash,content,source_path,created_at) VALUES(?,?,?,?,?)",
                (revision, snapshot.content_hash, snapshot.content, snapshot.path, utcnow()),
            )
        return snapshot.with_revision(revision)

    def latest_platform_policy(self) -> PlatformPolicySnapshot:
        with self.connect() as db:
            row = db.execute(
                "SELECT revision,content_hash,content,source_path,created_at FROM v1_platform_policy_revisions ORDER BY revision DESC LIMIT 1"
            ).fetchone()
        if not row:
            raise KeyError("platform policy has not been registered")
        return PlatformPolicySnapshot(row[3], row[2], row[1], int(row[0]))

    def create_orchestration_plan(self, project_id: str, plan: OrchestrationPlan) -> dict[str, Any]:
        self.get_project(project_id)
        payload = plan.model_dump_json()
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        now = utcnow()
        with self.tx(immediate=True) as db:
            existing = db.execute(
                "SELECT id FROM v1_orchestration_plans WHERE project_id=? AND plan_hash=?",
                (project_id, digest),
            ).fetchone()
            plan_id = existing[0] if existing else new_id("oplan")
            if not existing:
                db.execute(
                    "INSERT INTO v1_orchestration_plans(id,project_id,schema_version,plan_json,plan_hash,created_at) VALUES(?,?,?,?,?,?)",
                    (plan_id, project_id, plan.schema_version, payload, digest, now),
                )
                self._event(db, project_id, None, None, "orchestration.plan_created", {"plan_id": plan_id, "plan_hash": digest})
        return self.get_orchestration_plan(project_id, plan_id)

    def get_orchestration_plan(self, project_id: str, plan_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute(
                "SELECT * FROM v1_orchestration_plans WHERE id=? AND project_id=?", (plan_id, project_id)
            ).fetchone())
        if not row:
            raise KeyError(plan_id)
        row["plan"] = json.loads(row.pop("plan_json"))
        return row

    def list_orchestration_plans(self, project_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_orchestration_plans WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, min(max(limit, 1), self.policy.service_limits.max_page_size)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for item in rows:
            row = dict(item)
            row["plan"] = json.loads(row.pop("plan_json"))
            result.append(row)
        return result

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
        message_meta = dict(meta or {})
        message_meta.setdefault("kind", "message" if role == "user" else "reply")
        with self.tx(immediate=True) as db:
            message_id = self._insert_message(db, project_id, role, content, message_meta, now)
        return {"id": message_id, "project_id": project_id, "role": role, "content": content, "meta": message_meta, "created_at": now}

    def _insert_message(
        self,
        db: sqlite3.Connection,
        project_id: str,
        role: str,
        content: str,
        meta: dict[str, Any],
        now: str,
    ) -> int:
        cursor = db.execute(
            "INSERT INTO v1_conversations(project_id,role,content,meta_json,created_at) VALUES(?,?,?,?,?)",
            (project_id, role, self._pack_text(content), self._pack_json(meta), now),
        )
        message_id = int(cursor.lastrowid)
        self._event(
            db,
            project_id,
            None,
            None,
            "conversation.message",
            {"message_id": message_id, "role": role},
        )
        return message_id

    def messages(
        self,
        project_id: str,
        limit: int | None = None,
        *,
        after_id: int | None = None,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        if after_id is not None and before_id is not None:
            raise ValueError("after_id and before_id are mutually exclusive")
        with self.connect() as db:
            if limit is None and after_id is None and before_id is None:
                rows = db.execute(
                    "SELECT * FROM v1_conversations WHERE project_id=? ORDER BY id",
                    (project_id,),
                ).fetchall()
                return [self._decode(dict(row), "meta_json") for row in rows]  # type: ignore[misc]
            bounded_limit = min(max(limit or self.policy.service_limits.default_page_size, 1), self.policy.service_limits.max_page_size)
            if after_id is not None:
                rows = db.execute(
                    "SELECT * FROM v1_conversations WHERE project_id=? AND id>? ORDER BY id LIMIT ?",
                    (project_id, after_id, bounded_limit),
                ).fetchall()
            elif before_id is not None:
                rows = db.execute(
                    "SELECT * FROM (SELECT * FROM v1_conversations WHERE project_id=? AND id<? "
                    "ORDER BY id DESC LIMIT ?) ORDER BY id",
                    (project_id, before_id, bounded_limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM (SELECT * FROM v1_conversations WHERE project_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                    (project_id, bounded_limit),
                ).fetchall()
        return [self._decode(dict(row), "meta_json") for row in rows]  # type: ignore[misc]

    def conversation_state(self, project_id: str) -> dict[str, Any]:
        agent = self.conversation_agent(project_id)
        with self.connect() as db:
            row = db.execute(
                "SELECT id,status,trigger_kind,created_at,updated_at,finished_at,error "
                "FROM v1_conversation_jobs WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        job = None
        if row:
            job = {
                "id": row[0],
                "status": row[1],
                "trigger_kind": row[2],
                "created_at": row[3],
                "updated_at": row[4],
                "finished_at": row[5],
                "has_error": bool(row[6]),
            }
        return {
            "project_id": project_id,
            "agent_state": agent["state"],
            "job": job,
        }

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
                    json.dumps({"status": "queued", "job_id": job_id, "kind": "message"}, ensure_ascii=False),
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
                "WHERE m.id IS NOT NULL OR s.id IS NOT NULL",
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
                mailbox_ids = [row[0] for row in db.execute(
                    "SELECT id FROM v1_project_mailbox WHERE project_id=? AND state='pending' ORDER BY id LIMIT ?",
                    (project_id, self.policy.context.mailbox_limit),
                ).fetchall()]
                review = db.execute(
                    "SELECT id,reason FROM v1_scheduled_reviews WHERE project_id=? AND state='pending' AND due_at<=? ORDER BY due_at LIMIT 1",
                    (project_id, now),
                ).fetchone()
                trigger_kind = "mailbox" if mailbox_ids else "scheduled_review"
                trigger = {"mailbox_ids": mailbox_ids, "review_reason": review[1] if review else None}
                job_id = new_id("cjob")
                review_reason = review[1] if review else None
                cursor = db.execute(
                    "INSERT INTO v1_conversations(project_id,role,content,meta_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        project_id,
                        "system",
                        "",
                        json.dumps(
                            {"trigger": trigger_kind, "job_id": job_id, "reason": review_reason},
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                db.execute(
                    "INSERT INTO v1_conversation_jobs(id,project_id,user_message_id,message,start_task,status,trigger_kind,trigger_json,mailbox_ids_json,scheduled_review_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,1,'queued',?,?,?,?,?,?)",
                    (job_id, project_id, int(cursor.lastrowid), "", trigger_kind, json.dumps(trigger, ensure_ascii=False), json.dumps(mailbox_ids), review[0] if review else None, now, now),
                )
                db.execute("UPDATE v1_conversation_agents SET state='planning',updated_at=? WHERE project_id=?", (now, project_id))
                self._event(
                    db,
                    project_id,
                    None,
                    None,
                    "conversation.governance_queued",
                    {"job_id": job_id, "trigger": trigger_kind, "reason": review_reason},
                )
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
                "WHERE id=? AND status='queued' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM v1_conversation_jobs earlier "
                "JOIN v1_conversation_jobs current ON current.id=? "
                "WHERE earlier.project_id=current.project_id AND earlier.status='queued' "
                "AND (earlier.created_at<current.created_at OR (earlier.created_at=current.created_at AND earlier.user_message_id<current.user_message_id))"
                ") "
                "AND NOT EXISTS (SELECT 1 FROM v1_conversation_agents a JOIN v1_conversation_jobs j ON j.project_id=a.project_id WHERE j.id=? AND a.lease_until IS NOT NULL AND a.lease_until>?)",
                (worker_id, now, job_id, job_id, job_id, now),
            ).rowcount
            if not changed:
                return None
            row = db.execute("SELECT * FROM v1_conversation_jobs WHERE id=?", (job_id,)).fetchone()
            assert row is not None
            captured = json.loads(row["mailbox_ids_json"] or "[]")
            if not captured:
                captured = [item[0] for item in db.execute(
                    "SELECT id FROM v1_project_mailbox WHERE project_id=? AND state='pending' ORDER BY id LIMIT ?",
                    (row["project_id"], self.policy.context.mailbox_limit),
                ).fetchall()]
                db.execute("UPDATE v1_conversation_jobs SET mailbox_ids_json=? WHERE id=?", (json.dumps(captured), job_id))
            lease_until = (datetime.now(timezone.utc) + timedelta(seconds=self.policy.scheduling.conversation_lease_seconds)).isoformat(timespec="milliseconds")
            if captured:
                placeholders = ",".join("?" for _ in captured)
                db.execute(
                    f"UPDATE v1_project_mailbox SET state='claimed',claim_owner=?,claim_expires_at=? WHERE project_id=? AND state='pending' AND id IN ({placeholders})",
                    [worker_id, lease_until, row["project_id"], *captured],
                )
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
        notification: dict[str, Any] | None = None,
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
            persisted_result = dict(result or {})
            notification_message_id = None
            if notification:
                notification_meta = dict(notification.get("meta") or {})
                notification_meta.setdefault("kind", "notification")
                notification_message_id = self._insert_message(
                    db,
                    project_id,
                    "assistant",
                    str(notification.get("content") or ""),
                    notification_meta,
                    now,
                )
                persisted_result["conversation_message_id"] = notification_message_id
            db.execute(
                "UPDATE v1_conversation_jobs SET status='succeeded',task_id=?,agent_run_id=?,result_json=?,error=NULL,updated_at=?,finished_at=? "
                "WHERE id=?",
                (task_id, agent_run_id, json.dumps(persisted_result, ensure_ascii=False), now, now, job_id),
            )
            self._settle_conversation_agent(db, project_id, now)
            mailbox_ids = json.loads(row[1] or "[]")
            resolved_failure_job_ids: list[str] = []
            if mailbox_ids:
                placeholders = ",".join("?" for _ in mailbox_ids)
                failed_job_ids: list[str] = []
                failure_facts = db.execute(
                    f"SELECT event_type,payload_json FROM v1_project_mailbox "
                    f"WHERE project_id=? AND id IN ({placeholders})",
                    [project_id, *mailbox_ids],
                ).fetchall()
                for event_type, payload_json in failure_facts:
                    if event_type != "conversation.planning_failed":
                        continue
                    payload = json.loads(payload_json or "{}")
                    failed_job_id = str(payload.get("job_id") or "")
                    if failed_job_id and failed_job_id != job_id:
                        failed_job_ids.append(failed_job_id)
                if failed_job_ids:
                    failure_placeholders = ",".join("?" for _ in failed_job_ids)
                    resolved_failure_job_ids = [
                        str(item[0])
                        for item in db.execute(
                            f"SELECT id FROM v1_conversation_jobs "
                            f"WHERE project_id=? AND status='failed' AND resolved_by_job_id IS NULL "
                            f"AND id IN ({failure_placeholders})",
                            [project_id, *failed_job_ids],
                        ).fetchall()
                    ]
                    db.execute(
                        f"UPDATE v1_conversation_jobs SET resolved_by_job_id=? "
                        f"WHERE project_id=? AND status='failed' AND resolved_by_job_id IS NULL "
                        f"AND id IN ({failure_placeholders})",
                        [job_id, project_id, *failed_job_ids],
                    )
                decision_id = new_id("gdec")
                decision = "governance_turn_completed" if task_id else "observed_no_intervention"
                db.execute(
                    "INSERT INTO v1_governance_decisions(id,project_id,kind,subject_id,decision,data_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (decision_id, project_id, "mailbox_ack", job_id, decision, json.dumps(result or {}, ensure_ascii=False), now),
                )
                db.execute(
                    f"UPDATE v1_project_mailbox SET state='acknowledged',observed_at=?,acknowledged_at=?,governance_decision_id=?,claim_owner=NULL,claim_expires_at=NULL WHERE project_id=? AND state='claimed' AND claim_owner=? AND id IN ({placeholders})",
                    [now, now, decision_id, project_id, row[3], *mailbox_ids],
                )
                if notification_message_id is not None:
                    db.execute(
                        f"UPDATE v1_project_mailbox SET reported_message_id=COALESCE(reported_message_id,?),reported_at=COALESCE(reported_at,?) WHERE project_id=? AND id IN ({placeholders})",
                        [notification_message_id, now, project_id, *mailbox_ids],
                    )
                db.execute(
                    f"UPDATE v1_tasks SET observed_at=?,supervision_action=COALESCE(supervision_action,'observed_no_intervention') WHERE project_id=? AND id IN (SELECT task_id FROM v1_project_mailbox WHERE id IN ({placeholders}) AND task_id IS NOT NULL)",
                    [now, project_id, *mailbox_ids],
                )
            if row[2]:
                db.execute("UPDATE v1_scheduled_reviews SET state='observed',observed_at=? WHERE id=?", (now, row[2]))
            db.execute("UPDATE v1_conversation_agents SET lease_owner=NULL,lease_until=NULL WHERE project_id=?", (project_id,))
            self._event(db, project_id, task_id, None, "conversation.planning_succeeded", {"job_id": job_id})
            if resolved_failure_job_ids:
                self._event(
                    db,
                    project_id,
                    task_id,
                    None,
                    "conversation.failure_lineage_resolved",
                    {
                        "successful_job_id": job_id,
                        "resolved_job_ids": resolved_failure_job_ids,
                    },
                )
        return self.get_conversation_job(job_id)

    def fail_conversation_job(
        self,
        job_id: str,
        error: str,
        *,
        result: dict[str, Any] | None = None,
        notification: dict[str, Any] | None = None,
        attention: bool = False,
    ) -> dict[str, Any]:
        now = utcnow()
        safe_error = error
        with self.tx(immediate=True) as db:
            row = db.execute(
                "SELECT project_id,trigger_kind,mailbox_ids_json,worker_id,scheduled_review_id "
                "FROM v1_conversation_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if not row:
                raise KeyError(job_id)
            project_id = row[0]
            persisted_result = dict(result or {})
            notification_message_id = None
            if notification:
                notification_meta = dict(notification.get("meta") or {})
                notification_meta.setdefault("kind", "notification")
                notification_message_id = self._insert_message(
                    db,
                    project_id,
                    "assistant",
                    str(notification.get("content") or ""),
                    notification_meta,
                    now,
                )
                persisted_result["conversation_message_id"] = notification_message_id
            db.execute(
                "UPDATE v1_conversation_jobs SET status='failed',error=?,result_json=?,updated_at=?,finished_at=? WHERE id=?",
                (
                    safe_error,
                    self._pack_json(persisted_result),
                    now,
                    now,
                    job_id,
                ),
            )
            mailbox_ids = json.loads(row[2] or "[]")
            if mailbox_ids:
                placeholders = ",".join("?" for _ in mailbox_ids)
                mailbox_state = "attention" if attention else "pending"
                db.execute(
                    f"UPDATE v1_project_mailbox SET state=?,claim_owner=NULL,claim_expires_at=NULL "
                    f"WHERE project_id=? AND state='claimed' AND claim_owner=? AND id IN ({placeholders})",
                    [mailbox_state, project_id, row[3], *mailbox_ids],
                )
            if attention:
                if row[4]:
                    db.execute(
                        "UPDATE v1_scheduled_reviews SET state='attention' WHERE id=? AND state='pending'",
                        (row[4],),
                    )
                db.execute(
                    "UPDATE v1_conversation_agents SET state='attention',updated_at=? WHERE project_id=?",
                    (now, project_id),
                )
            else:
                self._settle_conversation_agent(db, project_id, now)
            db.execute("UPDATE v1_conversation_agents SET lease_owner=NULL,lease_until=NULL WHERE project_id=?", (project_id,))
            self._event(db, project_id, None, None, "conversation.planning_failed", {"job_id": job_id, "error": safe_error})
        return self.get_conversation_job(job_id)

    def startup_conversation_jobs(self) -> list[dict[str, Any]]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_conversation_jobs SET status='failed',error='process interrupted',"
                "updated_at=?,finished_at=? WHERE status='running'",
                (now, now),
            )
            db.execute(
                "UPDATE v1_agent_runs SET status='failed',error='process interrupted',"
                "error_code='interrupted',error_category='runtime_interrupted',"
                "finished_at=? WHERE status='running'",
                (now,),
            )
            db.execute(
                "UPDATE v1_project_mailbox SET state='pending',claim_owner=NULL,claim_expires_at=NULL "
                "WHERE state='claimed'"
            )
            db.execute("UPDATE v1_conversation_agents SET lease_owner=NULL,lease_until=NULL")
            rows = db.execute(
                "SELECT * FROM v1_conversation_jobs WHERE status='queued' ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def renew_conversation_lease(self, project_id: str, worker_id: str, lease_seconds: int | None = None) -> bool:
        lease_seconds = lease_seconds or self.policy.scheduling.conversation_lease_seconds
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

    def publish_workflow(self, project_id: str, workflow: WorkflowDefinition, orchestration_plan_id: str) -> dict[str, Any]:
        try:
            self.get_workflow(project_id)
        except KeyError as exc:
            raise ValueError("initial Workflow creation requires loop.apply") from exc
        return self._publish_workflow_revision(project_id, workflow, orchestration_plan_id)

    def _publish_workflow_revision(
        self,
        project_id: str,
        workflow: WorkflowDefinition,
        orchestration_plan_id: str,
        *,
        initial_loop: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.get_project(project_id)
        validate_workflow_capabilities(workflow, self.registry)
        plan_row = self.get_orchestration_plan(project_id, orchestration_plan_id)
        plan = OrchestrationPlan.model_validate(plan_row["plan"])
        _validate_process_stage_alignment(plan, workflow, self.policy)
        for proposed_task in plan.task_portfolio:
            proposed_task.validate_agent_execution_workflow(workflow)
            proposed_task.validate_exact_input_workflow(workflow)
        planned_columns = {item.key: item for item in plan.columns}
        if set(planned_columns) != {item.key for item in workflow.columns}:
            raise ValueError("Workflow columns must exactly match the referenced orchestration plan")
        for column in workflow.columns:
            check_schema(column.input_contract, label=f"Column {column.key} input_contract")
            check_schema(column.output_contract, label=f"Column {column.key} output_contract")
        revision_id, now = new_id("wfrev"), utcnow()
        payload = workflow.model_dump_json()
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self.tx(immediate=True) as db:
            identity = db.execute("SELECT id FROM v1_workflows WHERE project_id=?", (project_id,)).fetchone()
            workflow_id = identity[0] if identity else new_id("workflow")
            if not identity:
                if initial_loop is None:
                    raise ValueError("initial Workflow creation requires a filesystem Loop")
                db.execute(
                    "INSERT INTO v1_workflows("
                    "id,project_id,name,state_version,source_loop_key,source_loop_version,"
                    "source_loop_digest,created_at,updated_at) VALUES(?,?,?,0,?,?,?,?,?)",
                    (
                        workflow_id,
                        project_id,
                        workflow.name,
                        str(initial_loop["loop_key"]),
                        str(initial_loop["version"]),
                        str(initial_loop["digest"]),
                        now,
                        now,
                    ),
                )
            elif initial_loop is not None:
                raise ValueError("a Loop cannot replace an existing Project Workflow")
            current = int(db.execute(
                "SELECT COALESCE(MAX(revision_no),0) FROM v1_workflow_revisions WHERE workflow_id=?", (workflow_id,)
            ).fetchone()[0])
            db.execute("UPDATE v1_workflow_revisions SET active=0 WHERE project_id=?", (project_id,))
            db.execute(
                "INSERT INTO v1_workflow_revisions(id,project_id,revision,definition_json,active,created_at,workflow_id,revision_no,schema_version,definition_hash,orchestration_plan_id) "
                "VALUES(?,?,?,?,1,?,?,?,?,?,?)",
                (revision_id, project_id, current + 1, payload, now, workflow_id, current + 1, workflow.schema_version, digest, orchestration_plan_id),
            )
            db.execute(
                "UPDATE v1_workflows SET name=?,active_revision_id=?,state_version=state_version+1,updated_at=? WHERE id=?",
                (workflow.name, revision_id, now, workflow_id),
            )
            self._event(db, project_id, None, None, "workflow.published", {
                "workflow_id": workflow_id,
                "revision_id": revision_id,
                "revision": current + 1,
                "orchestration_plan_id": orchestration_plan_id,
                "source_loop_key": initial_loop["loop_key"] if initial_loop else None,
            })
            self._refresh_projection(db, project_id)
        return self.get_workflow(project_id)

    def get_workflow(self, project_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute(
                "SELECT r.*,w.id AS workflow_identity_id,w.state_version AS workflow_state_version,"
                "w.source_loop_key,w.source_loop_version,w.source_loop_digest "
                "FROM v1_workflows w JOIN v1_workflow_revisions r ON r.id=w.active_revision_id WHERE w.project_id=?", (project_id,)
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
        orchestration_plan_id: str,
        proposed_task_ref: str,
        rerun_of_task_id: str | None = None,
    ) -> dict[str, Any]:
        if readiness.get("decision") not in {"dispatch", "queue"}:
            raise ValueError("Task creation requires an explicit dispatch or queue readiness decision")
        workflow = self.get_workflow(project_id)
        if workflow.get("orchestration_plan_id") != orchestration_plan_id:
            raise ValueError("Task orchestration plan must match the active workflow revision")
        plan = OrchestrationPlan.model_validate(self.get_orchestration_plan(project_id, orchestration_plan_id)["plan"])
        proposed = next((item for item in plan.task_portfolio if item.proposed_task_ref == proposed_task_ref), None)
        if proposed is None:
            raise ValueError("Task must reference an entry in the orchestration plan portfolio")
        definition = WorkflowDefinition.model_validate(workflow["definition"])
        input_data = validate_task_capability_bindings(
            definition,
            self.registry,
            input_data,
            exact_strings=task_binding_exact_strings(
                self,
                project_id,
                orchestration_plan_id,
                proposed_task_ref,
            ),
        )
        proposed.validate_agent_execution_workflow(definition)
        readiness = ReadinessDecision.model_validate({
            **readiness,
            "objective": proposed.objective,
            "dependencies": list(proposed.dependencies),
            "conflict_domains": [
                item.model_dump(mode="json")
                for item in proposed.conflict_domains
            ],
        }).model_dump(mode="json")
        _validate_deterministic_deliverable_coverage(definition, readiness)
        if rerun_of_task_id:
            predecessor = self.get_project_task(project_id, rerun_of_task_id)
            if predecessor["status"] not in {"done", "failed"}:
                raise ValueError("a Task successor requires an immutable terminal predecessor")
            if (
                predecessor.get("proposed_task_ref") != proposed_task_ref
            ):
                raise ValueError(
                    "a Task successor must preserve proposed task identity"
                )
        task_id, now = new_id("tsk"), utcnow()
        conflict_domains = [item.canonical_key for item in proposed.conflict_domains]
        resolved_dependencies: list[str] = []
        unresolved_dependencies: list[str] = []
        with self.connect() as db:
            for ref in proposed.dependencies:
                dependency = db.execute(
                    "SELECT id FROM v1_tasks WHERE project_id=? AND orchestration_plan_id=? AND proposed_task_ref=? AND status='done' "
                    "ORDER BY finished_at DESC,created_at DESC LIMIT 1",
                    (project_id, orchestration_plan_id, ref),
                ).fetchone()
                if dependency:
                    resolved_dependencies.append(dependency[0])
                else:
                    unresolved_dependencies.append(ref)
        schedule_state = "admitted" if readiness.get("decision") == "dispatch" and not unresolved_dependencies else "queued"
        pending_deadline = None
        initial_context = {
            "orchestration": {
                "plan_id": orchestration_plan_id,
                "task_ref": proposed_task_ref,
                "workflow_fit": proposed.workflow_fit,
                "review_scope": proposed.review_scope,
                "retry_scope": proposed.retry_scope,
            }
        }
        with self.tx(immediate=True) as db:
            if rerun_of_task_id:
                existing_successor = db.execute(
                    "SELECT id FROM v1_tasks WHERE project_id=? AND rerun_of_task_id=? LIMIT 1",
                    (project_id, rerun_of_task_id),
                ).fetchone()
                if existing_successor:
                    raise ValueError(
                        "The terminal predecessor already has a materialized successor"
                    )
            else:
                latest = db.execute(
                    "SELECT id,status,resolved_by_task_id FROM v1_tasks "
                    "WHERE project_id=? AND orchestration_plan_id=? AND proposed_task_ref=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (project_id, orchestration_plan_id, proposed_task_ref),
                ).fetchone()
                if latest and latest[1] == "failed" and not latest[2]:
                    rerun_of_task_id = str(latest[0])
                elif latest:
                    raise ValueError(
                        "The referenced orchestration plan Task is already materialized; "
                        "use task.rerun for an explicit terminal successor"
                    )
            db.execute(
                "INSERT INTO v1_tasks(id,project_id,workflow_revision_id,orchestration_plan_id,proposed_task_ref,title,brief,input_json,context_json,readiness_json,conflict_domains_json,status,control_state,rerun_of_task_id,current_column,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending','active',?,?,?,?)",
                (task_id, project_id, workflow["id"], orchestration_plan_id, proposed_task_ref, title, brief, json.dumps(input_data, ensure_ascii=False), json.dumps(initial_context, ensure_ascii=False), json.dumps(readiness, ensure_ascii=False), json.dumps(conflict_domains, ensure_ascii=False), rerun_of_task_id, definition.entry, now, now),
            )
            run_id = new_id("run")
            db.execute(
                "INSERT INTO v1_column_runs(id,project_id,task_id,column_key,sequence,status,attempt,input_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, task_id, definition.entry, 1, "pending", 0, json.dumps(input_data, ensure_ascii=False), now),
            )
            backlog_id = new_id("backlog")
            db.execute("INSERT INTO v1_backlog_items(id,project_id,title,brief,readiness_json,state,task_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (backlog_id, project_id, title, brief, json.dumps(readiness, ensure_ascii=False), "dispatched" if schedule_state == "admitted" else "queued", task_id, now, now))
            dependency_tokens = resolved_dependencies + [f"plan:{orchestration_plan_id}:{ref}" for ref in unresolved_dependencies]
            db.execute("INSERT INTO v1_scheduling_entries(task_id,project_id,state,priority,wip_group,wip_limit,dependencies_json,resources_json,created_at,updated_at,auto_admit) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (task_id, project_id, schedule_state, 0, plan.wip_group, plan.wip_limit, json.dumps(dependency_tokens), json.dumps(conflict_domains), now, now, int(readiness.get("decision") == "dispatch" and bool(unresolved_dependencies))))
            for dependency_id in resolved_dependencies:
                db.execute(
                    "INSERT INTO v1_task_dependencies(task_id,depends_on_task_id,project_id,required_terminal,created_at) VALUES(?,?,?,'done',?)",
                    (task_id, dependency_id, project_id, now),
                )
            db.execute(
                "INSERT INTO v1_governance_decisions(id,project_id,kind,subject_id,decision,data_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (new_id("gdec"), project_id, "readiness", task_id, schedule_state, json.dumps({**readiness, "unresolved_dependencies": unresolved_dependencies}, ensure_ascii=False), now),
            )
            self._event(
                db,
                project_id,
                task_id,
                None,
                "task.created",
                {
                    "title": title,
                    "entry": definition.entry,
                    "schedule_state": schedule_state,
                    "orchestration_plan_id": orchestration_plan_id,
                    "proposed_task_ref": proposed_task_ref,
                    "rerun_of_task_id": rerun_of_task_id,
                },
            )
            if unresolved_dependencies:
                self._event(
                    db,
                    project_id,
                    task_id,
                    None,
                    "task.dependency_waiting",
                    {
                        "dependencies": unresolved_dependencies,
                        "pending_reason": "waiting_dependency",
                    },
                )
            self._refresh_projection(db, project_id)
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute("SELECT * FROM v1_tasks WHERE id=?", (task_id,)).fetchone())
        if not row:
            raise KeyError(task_id)
        TASK_STATE_MACHINE.parse(row["status"])
        return self._decode(row, "input_json", "context_json", "readiness_json", "conflict_domains_json")  # type: ignore[return-value]

    def get_project_task(self, project_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["project_id"] != project_id:
            raise KeyError(task_id)
        return task

    def list_tasks(self, project_id: str, limit: int | None = None, cursor: str | None = None) -> list[dict[str, Any]]:
        limit = limit or self.policy.service_limits.default_page_size
        with self.connect() as db:
            before = "9999-12-31T23:59:59+00:00"
            if cursor:
                row = db.execute("SELECT created_at FROM v1_tasks WHERE id=? AND project_id=?", (cursor, project_id)).fetchone()
                if not row:
                    raise KeyError(cursor)
                before = row[0]
            rows = db.execute("SELECT * FROM v1_tasks WHERE project_id=? AND created_at<? ORDER BY created_at DESC LIMIT ?", (project_id, before, min(max(limit, 1), self.policy.service_limits.max_page_size))).fetchall()
        result = [self._decode(dict(row), "input_json", "context_json", "readiness_json", "conflict_domains_json") for row in rows]
        for item in result:
            TASK_STATE_MACHINE.parse(item["status"])
        return result  # type: ignore[return-value]

    def task_summaries(self, project_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.policy.context.task_summary_limit
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,title,status,current_column,attempt,error,state_version,"
                "terminal_artifact_id,notified_at,observed_at,supervision_action,"
                "rerun_of_task_id,resolved_by_task_id,updated_at "
                "FROM v1_tasks WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, min(max(limit, 1), self.policy.service_limits.max_page_size)),
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

    def schedule_task(
        self,
        project_id: str,
        task_id: str,
        state: str,
        priority: int,
        wip_group: str | None,
        wip_limit: int | None,
        dependencies: list[str] | None,
        resources: list[str] | None,
    ) -> dict[str, Any]:
        task = self.get_project_task(project_id, task_id)
        if state not in {"admitted", "queued", "hold", "cancelled"}:
            raise ValueError("invalid scheduling state")
        now = utcnow()
        with self.tx(immediate=True) as db:
            existing = db.execute(
                "SELECT dependencies_json,resources_json,wip_group,wip_limit,auto_admit FROM v1_scheduling_entries WHERE task_id=? AND project_id=?",
                (task_id, project_id),
            ).fetchone()
            if not existing:
                raise ValueError("Task has no scheduling entry")
            if wip_group is None:
                wip_group = str(existing[2])
            if wip_limit is None:
                wip_limit = int(existing[3])
            original_dependencies = list(json.loads(existing[0] or "[]"))
            canonical_dependencies: list[str] = []
            dependencies_changed = False
            for dependency in original_dependencies:
                if not isinstance(dependency, str) or not dependency.startswith("plan:"):
                    canonical_dependencies.append(dependency)
                    continue
                _, plan_id, task_ref = dependency.split(":", 2)
                match = db.execute(
                    "SELECT id FROM v1_tasks WHERE project_id=? AND orchestration_plan_id=? "
                    "AND proposed_task_ref=? AND status='done' "
                    "ORDER BY finished_at DESC,created_at DESC LIMIT 1",
                    (project_id, plan_id, task_ref),
                ).fetchone()
                if not match:
                    match = db.execute(
                        "SELECT successor.id FROM v1_tasks predecessor "
                        "JOIN v1_tasks successor ON successor.id=predecessor.resolved_by_task_id "
                        "WHERE predecessor.project_id=? AND predecessor.orchestration_plan_id=? "
                        "AND predecessor.proposed_task_ref=? AND predecessor.status='failed' "
                        "AND successor.project_id=predecessor.project_id AND successor.status='done' "
                        "ORDER BY successor.finished_at DESC,successor.created_at DESC LIMIT 1",
                        (project_id, plan_id, task_ref),
                    ).fetchone()
                if not match:
                    canonical_dependencies.append(dependency)
                    continue
                dependency_id = str(match[0])
                canonical_dependencies.append(dependency_id)
                db.execute(
                    "INSERT OR IGNORE INTO v1_task_dependencies(task_id,depends_on_task_id,project_id,required_terminal,created_at) VALUES(?,?,?,'done',?)",
                    (task_id, dependency_id, project_id, now),
                )
                dependencies_changed = True
            if dependencies_changed:
                db.execute(
                    "UPDATE v1_scheduling_entries SET dependencies_json=?,updated_at=? WHERE task_id=? AND project_id=?",
                    (json.dumps(canonical_dependencies), now, task_id, project_id),
                )
        canonical_resources = list(task["conflict_domains"])
        if (
            dependencies is not None
            and set(dependencies) != set(original_dependencies)
            and set(dependencies) != set(canonical_dependencies)
        ):
            raise ValueError("scheduling dependencies must preserve the Task's orchestration-plan dependencies")
        if resources is not None and set(resources) != set(canonical_resources):
            raise ValueError("scheduling resources must preserve the Task's orchestration-plan conflict domains")
        dependencies = canonical_dependencies
        resources = canonical_resources
        auto_admit = int(bool(existing[4]) and state == "queued")
        if state == "admitted":
            with self.connect() as db:
                unsatisfied = [
                    dependency
                    for dependency in dependencies
                    if not isinstance(dependency, str)
                    or dependency.startswith("plan:")
                    or not (
                        (row := db.execute(
                            "SELECT status FROM v1_tasks WHERE id=? AND project_id=?",
                            (dependency, project_id),
                        ).fetchone())
                        and row[0] == "done"
                    )
                ]
            if unsatisfied:
                raise ValueError("dependency_unsatisfied: Task cannot be admitted until all orchestration-plan dependencies are done")
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_scheduling_entries(task_id,project_id,state,priority,wip_group,wip_limit,dependencies_json,resources_json,created_at,updated_at,auto_admit) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET state=excluded.state,priority=excluded.priority,wip_group=excluded.wip_group,wip_limit=excluded.wip_limit,dependencies_json=excluded.dependencies_json,resources_json=excluded.resources_json,auto_admit=excluded.auto_admit,updated_at=excluded.updated_at",
                (task_id, project_id, state, priority, wip_group, wip_limit, json.dumps(dependencies), json.dumps(resources), now, now, auto_admit),
            )
            db.execute("UPDATE v1_tasks SET updated_at=? WHERE id=?", (now, task_id))
            self._event(db, project_id, task_id, None, "scheduling.decided", {"state": state, "priority": priority, "wip_group": wip_group, "wip_limit": wip_limit, "dependencies": dependencies, "resources": resources})
        return {"project_id": project_id, "task_id": task_id, "state": state, "priority": priority, "wip_group": wip_group, "wip_limit": wip_limit, "dependencies": dependencies, "resources": resources, "updated_at": now}

    def task_scheduling(self, project_id: str, task_id: str) -> dict[str, Any]:
        task = self.get_project_task(project_id, task_id)
        now = utcnow()
        with self.connect() as db:
            row = self._dict(db.execute(
                "SELECT task_id,project_id,state,priority,wip_group,wip_limit,dependencies_json,resources_json,"
                "auto_admit,created_at,updated_at FROM v1_scheduling_entries WHERE task_id=? AND project_id=?",
                (task_id, project_id),
            ).fetchone())
            if not row:
                raise KeyError(f"no scheduling entry for {task_id}")
            dependencies = json.loads(row.pop("dependencies_json") or "[]")
            dependency_facts: list[dict[str, Any]] = []
            for dependency in dependencies:
                if isinstance(dependency, str) and dependency.startswith("plan:"):
                    _, plan_id, task_ref = dependency.split(":", 2)
                    latest = db.execute(
                        "SELECT id,status,resolved_by_task_id FROM v1_tasks "
                        "WHERE project_id=? AND orchestration_plan_id=? AND proposed_task_ref=? "
                        "ORDER BY created_at DESC LIMIT 1",
                        (project_id, plan_id, task_ref),
                    ).fetchone()
                    dependency_facts.append({
                        "reference": dependency,
                        "plan_id": plan_id,
                        "task_ref": task_ref,
                        "resolved_task_id": None,
                        "required_terminal": "done",
                        "latest_task_id": latest[0] if latest else None,
                        "status": latest[1] if latest else "unmaterialized",
                        "resolved_by_task_id": latest[2] if latest else None,
                        "satisfied": False,
                    })
                    continue
                dependency_row = db.execute(
                    "SELECT id,status,proposed_task_ref FROM v1_tasks WHERE id=? AND project_id=?",
                    (dependency, project_id),
                ).fetchone()
                dependency_facts.append({
                    "reference": dependency,
                    "task_ref": dependency_row[2] if dependency_row else None,
                    "resolved_task_id": dependency_row[0] if dependency_row else None,
                    "required_terminal": "done",
                    "status": dependency_row[1] if dependency_row else "missing",
                    "satisfied": bool(dependency_row and dependency_row[1] == "done"),
                })
            eligible = self._dispatch_eligible(db, task_id, now)
        row["dependencies"] = dependency_facts
        row["resources"] = json.loads(row.pop("resources_json") or "[]")
        row["auto_admit"] = bool(row["auto_admit"])
        row["dispatch_eligible"] = eligible
        if task["status"] in {"done", "failed"}:
            pending_reason = "terminal"
        elif task["status"] == "running":
            pending_reason = "running"
        elif task["status"] == "waiting":
            pending_reason = "external_wait"
        elif any(not item["satisfied"] for item in dependency_facts):
            pending_reason = "waiting_dependency"
        elif row["state"] == "queued":
            pending_reason = "explicit_queue"
        elif row["state"] == "hold":
            pending_reason = "hold"
        elif eligible:
            pending_reason = "ready"
        else:
            pending_reason = "waiting_wip_or_resource"
        row["pending_reason"] = pending_reason
        row["blocked_by"] = [
            item for item in dependency_facts if not item["satisfied"]
        ]
        return row

    def task_dependency_context(self, project_id: str, task_id: str) -> list[dict[str, Any]]:
        self.get_project_task(project_id, task_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT dep.id,dep.proposed_task_ref,dep.title,dep.status,dep.finished_at,"
                "dep.terminal_artifact_id,a.kind,a.path,a.sha256,a.size "
                "FROM v1_task_dependencies d "
                "JOIN v1_tasks dep ON dep.id=d.depends_on_task_id AND dep.project_id=d.project_id "
                "LEFT JOIN v1_artifacts a ON a.id=dep.terminal_artifact_id AND a.project_id=dep.project_id "
                "WHERE d.task_id=? AND d.project_id=? ORDER BY d.created_at,dep.id",
                (task_id, project_id),
            ).fetchall()
        return [
            {
                "task_id": row[0],
                "task_ref": row[1],
                "title": row[2],
                "status": row[3],
                "finished_at": row[4],
                "terminal_artifact": (
                    {
                        "id": row[5],
                        "kind": row[6],
                        "path": row[7],
                        "sha256": row[8],
                        "size": row[9],
                    }
                    if row[5]
                    else None
                ),
            }
            for row in rows
        ]

    def retry_task(self, task_id: str, column_key: str | None = None, *, clear_context: bool = False) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable; use task.rerun to create a successor")
        if task["status"] == "running":
            raise ValueError("a running Task cannot be retried until its active Attempt stops")
        TASK_STATE_MACHINE.require(task["status"], TaskStatus.PENDING)
        workflow = self.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        target = column_key or workflow.entry
        if workflow.terminal_kind(target):
            raise ValueError("retry target must be a non-terminal Column")
        workflow.column(target)
        now = utcnow()
        context = {} if clear_context else task["context"]
        with self.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_tasks SET status='pending',control_state='active',current_column=?,attempt=0,context_json=?,error=NULL,lease_owner=NULL,lease_until=NULL,finished_at=NULL,state_version=state_version+1,updated_at=? WHERE id=?",
                (target, json.dumps(context, ensure_ascii=False), now, task_id),
            )
            data = {"target": target, "clear_context": clear_context}
            self._event(db, task["project_id"], task_id, None, "task.retry_requested", data)
            self._mailbox(db, task["project_id"], "task.retry_requested", task_id, None, data)
        return self.get_task(task_id)

    def reopen_task(self, task_id: str, column_key: str | None = None, *, clear_context: bool = False) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] != "failed":
            raise ValueError("task.reopen requires a failed Task; review rejection follows Workflow transitions")
        TASK_STATE_MACHINE.require(task["status"], TaskStatus.PENDING)
        workflow = self.workflow_by_id(task["project_id"], task["workflow_revision_id"])
        target = column_key or workflow.entry
        if workflow.terminal_kind(target):
            raise ValueError("task.reopen target must be a non-terminal Column")
        workflow.column(target)
        now = utcnow()
        context = {} if clear_context else task["context"]
        with self.tx(immediate=True) as db:
            sequence = int(db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM v1_column_runs WHERE task_id=?",
                (task_id,),
            ).fetchone()[0])
            run_id = new_id("run")
            db.execute(
                "INSERT INTO v1_column_runs(id,project_id,task_id,column_key,sequence,status,attempt,input_json,created_at) "
                "VALUES(?,?,?,?,?,'pending',0,'{}',?)",
                (run_id, task["project_id"], task_id, target, sequence, now),
            )
            changed = db.execute(
                "UPDATE v1_tasks SET status='pending',control_state='active',current_column=?,attempt=0,"
                "context_json=?,error=NULL,lease_owner=NULL,lease_until=NULL,finished_at=NULL,"
                "terminal_artifact_id=NULL,terminal_event_id=NULL,notified_at=NULL,observed_at=NULL,"
                "supervision_action='reopened',state_version=state_version+1,updated_at=? "
                "WHERE id=? AND status='failed'",
                (target, json.dumps(context, ensure_ascii=False), now, task_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task changed while reopening")
            dependencies = json.loads(db.execute(
                "SELECT dependencies_json FROM v1_scheduling_entries WHERE task_id=?",
                (task_id,),
            ).fetchone()[0] or "[]")
            eligible = all(
                isinstance(dependency, str)
                and not dependency.startswith("plan:")
                and (
                    (row := db.execute(
                        "SELECT status FROM v1_tasks WHERE id=? AND project_id=?",
                        (dependency, task["project_id"]),
                    ).fetchone())
                    and row[0] == "done"
                )
                for dependency in dependencies
            )
            schedule_state = "admitted" if eligible else "queued"
            db.execute(
                "UPDATE v1_scheduling_entries SET state=?,auto_admit=?,updated_at=? WHERE task_id=?",
                (schedule_state, int(not eligible), now, task_id),
            )
            data = {
                "target": target,
                "clear_context": clear_context,
                "column_run_id": run_id,
                "schedule_state": schedule_state,
                "previous_error": task.get("error"),
            }
            self._event(db, task["project_id"], task_id, run_id, "task.reopened", data)
            self._mailbox(db, task["project_id"], "task.reopened", task_id, run_id, data)
            self._refresh_projection(db, task["project_id"])
        return self.get_task(task_id)

    def route_task_to_failed(self, task_id: str, reason: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable")
        return self._fail_task_now(task, reason, "cancelled")

    def rerun_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] not in {"done", "failed"}:
            raise ValueError("task.rerun requires an immutable terminal Task")
        workflow = self.get_workflow(task["project_id"])
        orchestration_plan_id = str(
            workflow.get("orchestration_plan_id") or task["orchestration_plan_id"]
        )
        return self.create_task(
            task["project_id"],
            task["title"],
            task["brief"],
            task["input"],
            task["readiness"],
            orchestration_plan_id=orchestration_plan_id,
            proposed_task_ref=task["proposed_task_ref"],
            rerun_of_task_id=task["id"],
        )

    def pause_task(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] in {"done", "failed"}:
            raise ValueError("terminal Tasks are immutable")
        now = utcnow()
        target_state = "pause_requested" if task["status"] == "running" else "paused"
        with self.tx(immediate=True) as db:
            changed = db.execute(
                "UPDATE v1_tasks SET control_state=?,state_version=state_version+1,updated_at=? WHERE id=? AND status NOT IN ('done','failed')",
                (target_state, now, task_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Task changed while pausing")
            self._event(db, task["project_id"], task_id, None, "task.pause_requested", {})
            self._mailbox(db, task["project_id"], "task.pause_requested", task_id, None, {})
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
                "UPDATE v1_tasks SET control_state='active',state_version=state_version+1,updated_at=? WHERE id=? AND status NOT IN ('done','failed')",
                (now, task_id),
            )
            self._event(db, task["project_id"], task_id, None, "task.resumed", {})
            self._mailbox(db, task["project_id"], "task.resumed", task_id, None, {})
            self._refresh_projection(db, task["project_id"])
        return self.get_task(task_id)

    def fail_task_from_exception(self, task: dict[str, Any], run_id: str, error: str, terminal_artifact: dict[str, Any], *, checkpoint: dict[str, Any] | None = None) -> None:
        TASK_STATE_MACHINE.require(task["status"], TaskStatus.FAILED)
        now = utcnow()
        with self.tx(immediate=True) as db:
            db.execute("UPDATE v1_column_runs SET status='failed',error=?,finished_at=? WHERE id=?", (error, now, run_id))
            db.execute("UPDATE v1_column_attempts SET status='failed',error=?,checkpoint_json=?,finished_at=? WHERE column_run_id=? AND status IN ('running','waiting')", (error, self._pack_json(checkpoint or {}), now, run_id))
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

    def recover_task_from_exception(
        self,
        task: dict[str, Any],
        run_id: str,
        error: str,
        *,
        error_code: str,
        error_category: str,
        checkpoint: dict[str, Any] | None = None,
        agent_run_id: str | None = None,
    ) -> None:
        TASK_STATE_MACHINE.require(task["status"], TaskStatus.RECOVERING)
        now = utcnow()
        next_retry_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=self.policy.scheduling.recovery_retry_delay_seconds)
        ).isoformat(timespec="milliseconds")
        with self.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_column_runs SET status='failed',error=?,error_category=?,finished_at=? WHERE id=?",
                (error, error_category, now, run_id),
            )
            db.execute(
                "UPDATE v1_column_attempts SET status='failed',error=?,error_code=?,error_category=?,"
                "checkpoint_json=?,agent_run_id=COALESCE(?,agent_run_id),finished_at=? "
                "WHERE column_run_id=? AND status IN ('running','waiting')",
                (error, error_code, error_category, self._pack_json(checkpoint or {}), agent_run_id, now, run_id),
            )
            changed = db.execute(
                "UPDATE v1_tasks SET status='recovering',error=?,lease_owner=NULL,lease_until=NULL,next_retry_at=?,"
                "finished_at=NULL,supervision_action='recovering',state_version=state_version+1,updated_at=? "
                "WHERE id=? AND state_version=? AND status='running'",
                (error, next_retry_at, now, task["id"], task["state_version"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("stale Task state_version while entering recovery")
            self._event(
                db,
                task["project_id"],
                task["id"],
                run_id,
                "task.recovering",
                {
                    "column": task["current_column"],
                    "failed_run_id": run_id,
                    "error": error,
                    "error_code": error_code,
                    "error_category": error_category,
                    "retry_after_seconds": self.policy.scheduling.recovery_retry_delay_seconds,
                    "next_retry_at": next_retry_at,
                },
            )
            self._refresh_projection(db, task["project_id"])

    def _fail_task_now(self, task: dict[str, Any], reason: str, failure_code: str) -> dict[str, Any]:
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
        info = ProjectFiles(self.get_project(task["project_id"])["base_dir"], self.policy).write_text(
            f".devwerk/terminal/{task['id']}.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        )
        with self.tx(immediate=True) as db:
            current = db.execute("SELECT status FROM v1_tasks WHERE id=?", (task["id"],)).fetchone()
            if not current:
                raise KeyError(task["id"])
            if current[0] in {"done", "failed"}:
                return self.get_task(task["id"])
            TASK_STATE_MACHINE.require(current[0], TaskStatus.FAILED)
            db.execute(
                "UPDATE v1_column_runs SET status='failed',error=?,finished_at=? WHERE task_id=? AND status IN ('pending','running','waiting')",
                (reason, now, task["id"]),
            )
            db.execute(
                "UPDATE v1_column_attempts SET status='failed',error=?,finished_at=? WHERE task_id=? AND status IN ('running','waiting')",
                (reason, now, task["id"]),
            )
            db.execute("UPDATE v1_await_handles SET status='cancelled',updated_at=? WHERE task_id=? AND status='pending'", (now, task["id"]))
            changed = db.execute(
                "UPDATE v1_tasks SET status='failed',control_state='active',current_column=?,error=?,lease_owner=NULL,lease_until=NULL,state_version=state_version+1,updated_at=?,finished_at=? WHERE id=? AND status NOT IN ('done','failed')",
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

    def claim_task(self, task_id: str, owner: str, lease_seconds: int | None = None) -> dict[str, Any] | None:
        now = utcnow()
        lease_seconds = lease_seconds or self.policy.scheduling.task_lease_seconds
        lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")
        with self.tx(immediate=True) as db:
            requested_row = db.execute(
                "SELECT t.project_id,s.resources_json,t.status,t.current_column,t.next_retry_at FROM v1_tasks t JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id WHERE t.id=?",
                (task_id,),
            ).fetchone()
            if not requested_row:
                return None
            TASK_STATE_MACHINE.parse(requested_row[2])
            if requested_row[2] in {TaskStatus.PENDING.value, TaskStatus.RECOVERING.value}:
                TASK_STATE_MACHINE.require(requested_row[2], TaskStatus.RUNNING)
            requested = json.loads(requested_row[1] or "[]")
            held_rows = db.execute(
                "SELECT s.resources_json FROM v1_tasks t JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id "
                "WHERE t.project_id=? AND t.id!=? AND t.status='running'",
                (requested_row[0], task_id),
            ).fetchall()
            if any(_resource_domains_overlap(requested, json.loads(row[0] or "[]")) for row in held_rows):
                return None
            cursor = db.execute(
                "UPDATE v1_tasks SET status='running',lease_owner=?,lease_until=?,next_retry_at=NULL,state_version=state_version+1,updated_at=? WHERE id=? AND status IN ('pending','recovering') AND control_state='active' "
                "AND (status!='recovering' OR next_retry_at IS NULL OR next_retry_at<=?) "
                "AND EXISTS (SELECT 1 FROM v1_scheduling_entries s WHERE s.task_id=v1_tasks.id AND s.project_id=v1_tasks.project_id AND s.state='admitted' "
                "AND NOT EXISTS (SELECT 1 FROM json_each(s.dependencies_json) d LEFT JOIN v1_tasks dep ON dep.id=d.value WHERE dep.id IS NULL OR dep.status!='done') "
                "AND (SELECT COUNT(*) FROM v1_tasks active JOIN v1_scheduling_entries sa ON sa.task_id=active.id WHERE active.project_id=v1_tasks.project_id AND active.status='running' AND sa.wip_group=s.wip_group)<s.wip_limit)",
                (owner, lease, now, task_id, now),
            )
            if cursor.rowcount != 1:
                return None
            if requested_row[2] == "recovering":
                self._event(
                    db,
                    requested_row[0],
                    task_id,
                    None,
                    "task.recovery_started",
                    {"column": requested_row[3], "scheduled_retry_at": requested_row[4]},
                )
        return self.get_task(task_id)

    def renew_lease(self, task_id: str, owner: str, lease_seconds: int | None = None) -> bool:
        now = utcnow()
        lease_seconds = lease_seconds or self.policy.scheduling.task_lease_seconds
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

    def runnable_task_ids(self, limit: int | None = None) -> list[str]:
        now = utcnow()
        limit = limit or self.policy.scheduling.runnable_batch_size
        self._resolve_planned_dependencies()
        runnable: list[str] = []
        with self.tx(immediate=True) as db:
            rows = db.execute(
                "SELECT t.id,t.status FROM v1_tasks t JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id "
                "WHERE t.control_state='active' AND t.status IN ('pending','recovering') "
                "AND (t.status!='recovering' OR t.next_retry_at IS NULL OR t.next_retry_at<=?) "
                "ORDER BY s.priority DESC,t.updated_at LIMIT ?",
                (now, max(limit * 10, limit)),
            ).fetchall()
            for row in rows:
                task_id, status = row[0], row[1]
                if self._dispatch_eligible(db, task_id, now):
                    runnable.append(task_id)
                if len(runnable) >= limit:
                    break
        return runnable

    def _dispatch_eligible(self, db: sqlite3.Connection, task_id: str, now: str) -> bool:
        row = db.execute(
            "SELECT t.project_id,t.status,t.control_state,s.state,s.wip_group,s.wip_limit,"
            "s.dependencies_json,s.resources_json,t.next_retry_at "
            "FROM v1_tasks t JOIN v1_scheduling_entries s ON s.task_id=t.id AND s.project_id=t.project_id "
            "WHERE t.id=?",
            (task_id,),
        ).fetchone()
        if not row:
            return False
        if row[1] not in {"pending", "recovering"} or row[2] != "active" or row[3] != "admitted":
            return False
        if row[1] == "recovering" and row[8] and row[8] > now:
            return False
        dependencies = json.loads(row[6] or "[]")
        for dependency in dependencies:
            if not isinstance(dependency, str) or dependency.startswith("plan:"):
                return False
            dependency_row = db.execute(
                "SELECT status FROM v1_tasks WHERE id=? AND project_id=?",
            (dependency, row[0]),
            ).fetchone()
            if not dependency_row or dependency_row[0] != "done":
                return False
        active_count = db.execute(
            "SELECT COUNT(*) FROM v1_tasks active JOIN v1_scheduling_entries sa ON sa.task_id=active.id "
            "WHERE active.project_id=? AND active.status='running' AND sa.wip_group=?",
            (row[0], row[4]),
        ).fetchone()[0]
        if active_count >= int(row[5]):
            return False
        requested = json.loads(row[7] or "[]")
        held_rows = db.execute(
            "SELECT sa.resources_json FROM v1_tasks active JOIN v1_scheduling_entries sa ON sa.task_id=active.id "
            "WHERE active.project_id=? AND active.status='running' AND active.id!=?",
            (row[0], task_id),
        ).fetchall()
        return not any(_resource_domains_overlap(requested, json.loads(held[0] or "[]")) for held in held_rows)

    def _resolve_planned_dependencies(self) -> None:
        now = utcnow()
        with self.tx(immediate=True) as db:
            rows = db.execute(
                "SELECT s.task_id,s.project_id,s.dependencies_json FROM v1_scheduling_entries s "
                "JOIN v1_tasks t ON t.id=s.task_id AND t.project_id=s.project_id "
                "WHERE s.state='queued' AND s.auto_admit=1 AND t.status='pending'"
            ).fetchall()
            for row in rows:
                dependencies = json.loads(row[2] or "[]")
                resolved: list[str] = []
                changed = False
                for value in dependencies:
                    if not isinstance(value, str) or not value.startswith("plan:"):
                        resolved.append(value)
                        continue
                    _, plan_id, task_ref = value.split(":", 2)
                    match = db.execute(
                        "SELECT id FROM v1_tasks WHERE project_id=? AND orchestration_plan_id=? AND proposed_task_ref=? AND status='done' "
                        "ORDER BY finished_at DESC,created_at DESC LIMIT 1",
                        (row[1], plan_id, task_ref),
                    ).fetchone()
                    if not match:
                        match = db.execute(
                            "SELECT successor.id FROM v1_tasks predecessor "
                            "JOIN v1_tasks successor ON successor.id=predecessor.resolved_by_task_id "
                            "WHERE predecessor.project_id=? AND predecessor.orchestration_plan_id=? "
                            "AND predecessor.proposed_task_ref=? AND predecessor.status='failed' "
                            "AND successor.project_id=predecessor.project_id AND successor.status='done' "
                            "ORDER BY successor.finished_at DESC,successor.created_at DESC LIMIT 1",
                            (row[1], plan_id, task_ref),
                        ).fetchone()
                    if not match:
                        resolved.append(value)
                        continue
                    dependency_id = match[0]
                    resolved.append(dependency_id)
                    db.execute(
                        "INSERT OR IGNORE INTO v1_task_dependencies(task_id,depends_on_task_id,project_id,required_terminal,created_at) VALUES(?,?,?,'done',?)",
                        (row[0], dependency_id, row[1], now),
                    )
                    changed = True
                if changed:
                    db.execute(
                        "UPDATE v1_scheduling_entries SET dependencies_json=?,updated_at=? WHERE task_id=?",
                        (json.dumps(resolved), now, row[0]),
                    )
                if resolved and all(not value.startswith("plan:") for value in resolved):
                    admitted = db.execute(
                        "UPDATE v1_scheduling_entries SET state='admitted',auto_admit=0,updated_at=? "
                        "WHERE task_id=? AND state='queued' AND auto_admit=1",
                        (now, row[0]),
                    ).rowcount
                    if admitted:
                        db.execute("UPDATE v1_tasks SET updated_at=? WHERE id=? AND status='pending'", (now, row[0]))
                        db.execute(
                            "UPDATE v1_backlog_items SET state='dispatched',updated_at=? WHERE task_id=?",
                            (now, row[0]),
                        )
                        self._event(
                            db,
                            row[1],
                            row[0],
                            None,
                            "task.dependencies_satisfied",
                            {
                                "dependencies": resolved,
                                "schedule_state": "admitted",
                            },
                        )

    def begin_run(self, task: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            pending = db.execute(
                "SELECT id,sequence FROM v1_column_runs WHERE task_id=? AND column_key=? AND status IN ('pending','interrupted') ORDER BY sequence DESC LIMIT 1",
                (task["id"], task["current_column"]),
            ).fetchone()
            if pending:
                run_id, sequence = pending[0], pending[1]
                current_run_status = db.execute(
                    "SELECT status FROM v1_column_runs WHERE id=?", (run_id,)
                ).fetchone()[0]
                COLUMN_RUN_STATE_MACHINE.require(current_run_status, ColumnRunStatus.RUNNING)
                db.execute(
                    "UPDATE v1_column_runs SET status='running',attempt=?,input_json=?,runtime_policy_revision=?,runtime_policy_hash=?,heartbeat_at=?,last_progress_at=?,started_at=COALESCE(started_at,?),finished_at=NULL WHERE id=?",
                    (task["attempt"] + 1, json.dumps(input_data, ensure_ascii=False), self.policy.revision, self.policy.policy_hash, now, now, now, run_id),
                )
            else:
                run_id = new_id("run")
                sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM v1_column_runs WHERE task_id=?", (task["id"],)).fetchone()[0]
                db.execute(
                    "INSERT INTO v1_column_runs(id,project_id,task_id,column_key,sequence,status,attempt,input_json,runtime_policy_revision,runtime_policy_hash,heartbeat_at,last_progress_at,started_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, task["project_id"], task["id"], task["current_column"], sequence, "running", task["attempt"] + 1, json.dumps(input_data, ensure_ascii=False), self.policy.revision, self.policy.policy_hash, now, now, now, now),
                )
            attempt_no = int(db.execute("SELECT COALESCE(MAX(attempt_no),0)+1 FROM v1_column_attempts WHERE column_run_id=?", (run_id,)).fetchone()[0])
            attempt_id = new_id("attempt")
            db.execute(
                "INSERT INTO v1_column_attempts(id,project_id,task_id,column_run_id,attempt_no,status,input_json,runtime_policy_revision,runtime_policy_hash,started_at,created_at) VALUES(?,?,?,?,?,'running',?,?,?,?,?)",
                (attempt_id, task["project_id"], task["id"], run_id, attempt_no, json.dumps(input_data, ensure_ascii=False), self.policy.revision, self.policy.policy_hash, now, now),
            )
            event_type = "column.started" if attempt_no == 1 else "column.retry_started"
            self._event(db, task["project_id"], task["id"], run_id, event_type, {"column": task["current_column"], "sequence": sequence, "attempt_id": attempt_id, "attempt_no": attempt_no})
        return {"id": run_id, "attempt_id": attempt_id, "attempt_no": attempt_no, "sequence": sequence, "column_key": task["current_column"], "status": "running", "started_at": now}

    def prepare_terminal_evidence(self, task: dict[str, Any], run_id: str, terminal: str, output: dict[str, Any], error: str | None) -> dict[str, Any]:
        payload = {
            "schema": "devwerk.task-terminal.v1",
            "project_id": task["project_id"], "task_id": task["id"], "column_run_id": run_id,
            "terminal": terminal, "output": output, "error": error, "recorded_at": utcnow(),
        }
        files = ProjectFiles(self.get_project(task["project_id"])["base_dir"], self.policy)
        info = files.write_text(
            f".devwerk/terminal/{task['id']}.json",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        )
        return {**info, "kind": "task_terminal", "meta": {"terminal": terminal, "schema": payload["schema"]}}

    @staticmethod
    def _resolve_failed_task_ancestors(
        db: sqlite3.Connection,
        successful_task_id: str,
        now: str,
    ) -> list[str]:
        resolved: list[str] = []
        seen = {successful_task_id}
        current = successful_task_id
        while True:
            row = db.execute(
                "SELECT rerun_of_task_id FROM v1_tasks WHERE id=?",
                (current,),
            ).fetchone()
            predecessor_id = str(row[0]) if row and row[0] else ""
            if not predecessor_id or predecessor_id in seen:
                break
            seen.add(predecessor_id)
            predecessor = db.execute(
                "SELECT status,rerun_of_task_id FROM v1_tasks WHERE id=?",
                (predecessor_id,),
            ).fetchone()
            if not predecessor:
                break
            if predecessor[0] == "failed":
                changed = db.execute(
                    "UPDATE v1_tasks SET resolved_by_task_id=?,state_version=state_version+1,"
                    "updated_at=? WHERE id=? AND status='failed' AND resolved_by_task_id IS NULL",
                    (successful_task_id, now, predecessor_id),
                ).rowcount
                if changed:
                    resolved.append(predecessor_id)
            current = predecessor_id
        return resolved

    def finish_run(self, task: dict[str, Any], run_id: str, output: dict[str, Any], outcome: str, next_column: str | None, terminal: str | None = None, error: str | None = None, terminal_artifact: dict[str, Any] | None = None) -> None:
        now = utcnow()
        run_status = "failed" if error else "succeeded"
        task_status = terminal or ("failed" if error else "pending")
        persisted_output = dict(output)
        task_context = persisted_output.pop("context", task["context"])
        with self.tx(immediate=True) as db:
            attempt = db.execute(
                "SELECT id FROM v1_column_attempts WHERE column_run_id=? AND status IN ('running','waiting') ORDER BY attempt_no DESC LIMIT 1", (run_id,)
            ).fetchone()
            if not attempt:
                raise RuntimeError("Column Run has no active Attempt")
            run_row = db.execute("SELECT status FROM v1_column_runs WHERE id=?", (run_id,)).fetchone()
            attempt_row = db.execute("SELECT status FROM v1_column_attempts WHERE id=?", (attempt[0],)).fetchone()
            if not run_row or not attempt_row:
                raise RuntimeError("Column Run state disappeared while finishing")
            COLUMN_RUN_STATE_MACHINE.require(run_row[0], run_status)
            ATTEMPT_STATE_MACHINE.require(attempt_row[0], run_status)
            TASK_STATE_MACHINE.require(task["status"], task_status)
            db.execute(
                "UPDATE v1_column_attempts SET status=?,output_json=?,error=?,finished_at=? WHERE id=?",
                (run_status, json.dumps(persisted_output, ensure_ascii=False), error, now, attempt[0]),
            )
            db.execute(
                "UPDATE v1_column_runs SET status=?,output_json=?,error=?,finished_at=? WHERE id=?",
                (run_status, json.dumps(persisted_output, ensure_ascii=False), error, now, run_id),
            )
            if terminal:
                if not next_column:
                    raise ValueError("terminal Column completion requires its terminal target key")
                terminal_error = error or (task.get("error") if terminal == "failed" else None)
                changed = db.execute(
                    "UPDATE v1_tasks SET status=?,current_column=?,control_state='active',context_json=?,error=?,lease_owner=NULL,lease_until=NULL,supervision_action=NULL,state_version=state_version+1,updated_at=?,finished_at=? WHERE id=? AND state_version=? AND status IN ('running','waiting')",
                    (terminal, next_column, json.dumps(task_context, ensure_ascii=False), terminal_error, now, now, task["id"], task["state_version"]),
                ).rowcount
            else:
                sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM v1_column_runs WHERE task_id=?", (task["id"],)).fetchone()[0]
                db.execute(
                    "INSERT INTO v1_column_runs(id,project_id,task_id,column_key,sequence,status,attempt,input_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (new_id("run"), task["project_id"], task["id"], next_column, sequence, "pending", 0, "{}", now),
                )
                changed = db.execute(
                    "UPDATE v1_tasks SET status=?,current_column=?,attempt=0,context_json=?,error=?,lease_owner=NULL,lease_until=NULL,supervision_action=NULL,control_state=CASE WHEN control_state='pause_requested' THEN 'paused' ELSE control_state END,state_version=state_version+1,updated_at=? WHERE id=? AND state_version=? AND status IN ('running','waiting')",
                    (task_status, next_column, json.dumps(task_context, ensure_ascii=False), error, now, task["id"], task["state_version"]),
                ).rowcount
            if changed != 1:
                raise RuntimeError("stale Task state_version while finishing Column Run")
            resolved_task_ids = (
                self._resolve_failed_task_ancestors(db, task["id"], now)
                if terminal == "done"
                else []
            )
            self._event(db, task["project_id"], task["id"], run_id, "column.finished", {"column": task["current_column"], "outcome": outcome, "status": run_status, "next": next_column})
            if task.get("supervision_action") == "recovering" and run_status == "succeeded":
                self._event(
                    db,
                    task["project_id"],
                    task["id"],
                    run_id,
                    "task.recovered",
                    {"column": task["current_column"], "next": next_column},
                )
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
                if resolved_task_ids:
                    self._event(
                        db,
                        task["project_id"],
                        task["id"],
                        run_id,
                        "task.failure_lineage_resolved",
                        {
                            "successful_task_id": task["id"],
                            "resolved_task_ids": resolved_task_ids,
                        },
                    )
            self._refresh_projection(db, task["project_id"])

    def create_await_handle(
        self, task: dict[str, Any], run_id: str, *, provider: str, token: str | None,
        poll_capability: str | None, poll_arguments: dict[str, Any], next_check_seconds: int,
        success_outcome: str, waiting_kind: str = "external",
        resume_condition: dict[str, Any] | None = None, cancel_capability: str | None = None,
        cancel_arguments: dict[str, Any] | None = None, cleanup_capability: str | None = None,
        cleanup_arguments: dict[str, Any] | None = None, idempotency_key: str | None = None,
        checkpoint: dict[str, Any] | None = None, event_type: str | None = None,
        correlation_key: str | None = None, resume_at: str | None = None,
    ) -> dict[str, Any]:
        TASK_STATE_MACHINE.require(task["status"], TaskStatus.WAITING)
        handle_id, now = new_id("await"), utcnow()
        instant = datetime.now(timezone.utc)
        next_check = resume_at or (instant + timedelta(seconds=next_check_seconds)).isoformat(timespec="milliseconds")
        with self.tx(immediate=True) as db:
            attempt = db.execute(
                "SELECT id FROM v1_column_attempts WHERE column_run_id=? AND status IN ('running','waiting') ORDER BY attempt_no DESC LIMIT 1", (run_id,)
            ).fetchone()
            if not attempt:
                raise RuntimeError("durable wait requires an active Column Attempt")
            run_status = db.execute("SELECT status FROM v1_column_runs WHERE id=?", (run_id,)).fetchone()
            attempt_status = db.execute("SELECT status FROM v1_column_attempts WHERE id=?", (attempt[0],)).fetchone()
            if not run_status or not attempt_status:
                raise RuntimeError("durable wait lost its active Runtime state")
            COLUMN_RUN_STATE_MACHINE.require(run_status[0], ColumnRunStatus.WAITING)
            ATTEMPT_STATE_MACHINE.require(attempt_status[0], AttemptStatus.WAITING)
            execution_key = str((checkpoint or {}).get("execution_key") or "")
            if execution_key:
                changed = db.execute(
                    "UPDATE v1_execution_receipts SET status='awaiting',result_json=?,error=NULL WHERE project_id=? AND execution_key=? AND status='started'",
                    (self._pack_json((checkpoint or {}).get("capability_result") or {"status": "awaiting", "checkpoint": checkpoint or {}}), task["project_id"], execution_key),
                ).rowcount
                if changed != 1:
                    existing = db.execute(
                        "SELECT status FROM v1_execution_receipts WHERE project_id=? AND execution_key=?",
                        (task["project_id"], execution_key),
                    ).fetchone()
                    if not existing or existing[0] != "awaiting":
                        raise RuntimeError("awaiting execution receipt is not claimable")
            db.execute(
                "INSERT INTO v1_await_handles(id,project_id,task_id,run_id,column_attempt_id,provider,token,status,next_check_at,progress_json,created_at,updated_at,column_key,poll_capability,poll_arguments_json,success_outcome,result_json,waiting_kind,resume_condition_json,cancel_capability,cancel_arguments_json,cleanup_capability,cleanup_arguments_json,idempotency_key,checkpoint_json,event_type,correlation_key,resume_at) "
                "VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (handle_id, task["project_id"], task["id"], run_id, attempt[0], provider, token, next_check, "{}", now, now, task["current_column"], poll_capability, json.dumps(poll_arguments, ensure_ascii=False), success_outcome, "{}", waiting_kind, json.dumps(resume_condition or {}), cancel_capability, json.dumps(cancel_arguments or {}), cleanup_capability, json.dumps(cleanup_arguments or {}), idempotency_key, self._pack_json(checkpoint or {}), event_type, correlation_key, resume_at),
            )
            db.execute("UPDATE v1_column_runs SET status='waiting',last_progress_at=? WHERE id=?", (now, run_id))
            db.execute("UPDATE v1_column_attempts SET status='waiting',checkpoint_json=? WHERE id=?", (self._pack_json({**(checkpoint or {}), "await_handle_id": handle_id}), attempt[0]))
            db.execute("UPDATE v1_tasks SET status='waiting',lease_owner=NULL,lease_until=NULL,state_version=state_version+1,updated_at=? WHERE id=?", (now, task["id"]))
            self._event(db, task["project_id"], task["id"], run_id, "column.waiting", {"await_handle_id": handle_id, "next_check_at": next_check})
            self._mailbox(db, task["project_id"], "column.waiting", task["id"], run_id, {"await_handle_id": handle_id})
            self._refresh_projection(db, task["project_id"])
        return self.await_handle(handle_id)

    def await_handle(self, handle_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute("SELECT * FROM v1_await_handles WHERE id=?", (handle_id,)).fetchone())
        if not row:
            raise KeyError(handle_id)
        return self._decode(row, "progress_json", "poll_arguments_json", "result_json", "resume_condition_json", "cancel_arguments_json", "cleanup_arguments_json", "checkpoint_json")  # type: ignore[return-value]

    def due_await_handles(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.policy.scheduling.await_batch_size
        with self.connect() as db:
            rows = db.execute(
                "SELECT h.* FROM v1_await_handles h JOIN v1_tasks t ON t.id=h.task_id "
                "WHERE h.status='pending' AND h.next_check_at<=? AND t.control_state='active' "
                "ORDER BY h.next_check_at LIMIT ?",
                (utcnow(), min(limit, self.policy.service_limits.max_page_size)),
            ).fetchall()
        return [self._decode(dict(row), "progress_json", "poll_arguments_json", "result_json", "resume_condition_json", "cancel_arguments_json", "cleanup_arguments_json", "checkpoint_json") for row in rows]  # type: ignore[misc]

    def mark_await_health(self, handle_id: str, health: str, event_type: str) -> None:
        with self.tx(immediate=True) as db:
            row = db.execute("SELECT project_id,task_id,run_id FROM v1_await_handles WHERE id=? AND status='pending' AND health!=?", (handle_id, health)).fetchone()
            if not row:
                return
            db.execute("UPDATE v1_await_handles SET health=?,updated_at=? WHERE id=?", (health, utcnow(), handle_id))
            self._event(db, row[0], row[1], row[2], event_type, {"await_handle_id": handle_id, "health": health})
            self._mailbox(db, row[0], event_type, row[1], row[2], {"await_handle_id": handle_id, "health": health})

    def settle_await_handle(self, handle_id: str, status: str, result: dict[str, Any], *, next_check_seconds: int | None = None) -> dict[str, Any]:
        now = utcnow()
        if status == "pending" and next_check_seconds is None:
            raise ValueError("pending await settlement requires an explicit next_check_seconds")
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

    def runs(self, project_id: str, task_id: str, limit: int | None = None, after_sequence: int = 0) -> list[dict[str, Any]]:
        limit = limit or self.policy.service_limits.detail_page_size
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_column_runs WHERE project_id=? AND task_id=? AND sequence>? ORDER BY sequence LIMIT ?", (project_id, task_id, after_sequence, min(max(limit, 1), self.policy.service_limits.max_page_size))).fetchall()
        result = [self._decode(dict(row), "input_json", "output_json") for row in rows]
        for item in result:
            COLUMN_RUN_STATE_MACHINE.parse(item["status"])
        return result  # type: ignore[return-value]

    def attempts(self, project_id: str, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_column_attempts WHERE project_id=? AND task_id=? ORDER BY created_at,attempt_no LIMIT ?",
                (project_id, task_id, min(max(limit, 1), self.policy.service_limits.max_page_size)),
            ).fetchall()
        result = [self._decode(dict(row), "input_json", "output_json", "checkpoint_json") for row in rows]
        for item in result:
            ATTEMPT_STATE_MACHINE.parse(item["status"])
        return result  # type: ignore[return-value]

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
        column_attempt_id: str | None = None,
        platform_policy: PlatformPolicySnapshot,
        runtime_policy: V1RuntimePolicy,
        conversation_job_id: str | None = None,
        agent_session_id: str | None = None,
    ) -> dict[str, Any]:
        run_id, now = new_id("arun"), utcnow()
        packed_context, packed_capabilities = self._pack_json(context_snapshot), self._pack_json(capabilities)
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_agent_runs(id,project_id,task_id,column_run_id,column_attempt_id,conversation_job_id,agent_session_id,kind,status,instruction_revision,instruction_snapshot,context_json,capabilities_json,platform_policy_revision,platform_policy_hash,runtime_policy_revision,runtime_policy_hash,created_at,started_at) "
                "VALUES(?,?,?,?,?,?,?,?,'running',?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    project_id,
                    task_id,
                    column_run_id,
                    column_attempt_id,
                    conversation_job_id,
                    agent_session_id,
                    kind,
                    instruction_revision,
                    instruction_snapshot,
                    packed_context,
                    packed_capabilities,
                    platform_policy.revision,
                    platform_policy.content_hash,
                    runtime_policy.revision,
                    runtime_policy.policy_hash,
                    now,
                    now,
                ),
            )
            if column_run_id:
                db.execute("UPDATE v1_column_runs SET agent_run_id=? WHERE id=?", (run_id, column_run_id))
            self._event(db, project_id, task_id, column_run_id, "agent.started", {"agent_run_id": run_id, "kind": kind})
        return self.get_agent_run(project_id, run_id)

    def get_or_create_agent_session(self, project_id: str, task_id: str, session_key: str) -> dict[str, Any]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            row = db.execute(
                "SELECT * FROM v1_agent_sessions WHERE project_id=? AND task_id=? AND session_key=?",
                (project_id, task_id, session_key),
            ).fetchone()
            if row is None:
                session_id = new_id("asess")
                db.execute(
                    "INSERT INTO v1_agent_sessions(id,project_id,task_id,session_key,state,created_at,updated_at) "
                    "VALUES(?,?,?,?,'active',?,?)",
                    (session_id, project_id, task_id, session_key, now, now),
                )
                self._event(db, project_id, task_id, None, "agent.session.created", {
                    "agent_session_id": session_id,
                    "session_key": session_key,
                })
                row = db.execute("SELECT * FROM v1_agent_sessions WHERE id=?", (session_id,)).fetchone()
            else:
                db.execute(
                    "UPDATE v1_agent_sessions SET state='active',updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
                self._event(db, project_id, task_id, None, "agent.session.resumed", {
                    "agent_session_id": row["id"],
                    "session_key": session_key,
                })
        return dict(row)

    def agent_session_messages(self, project_id: str, agent_session_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,final_text,error,status FROM v1_agent_runs "
                "WHERE project_id=? AND agent_session_id=? AND status IN ('succeeded','failed') "
                "ORDER BY created_at,id",
                (project_id, agent_session_id),
            ).fetchall()
        return [
            {
                "role": "prior_run",
                "content": json.dumps({
                    "prior_agent_run_id": row[0],
                    "status": row[3],
                    "final_text": row[1],
                    "error": row[2],
                    "instruction": "Continue the same logical assignment using current Task artifacts and review feedback.",
                }, ensure_ascii=False),
                "tool_calls": [],
                "tool_call_id": None,
            }
            for row in rows
        ]

    def suspend_agent_session(self, project_id: str, agent_session_id: str, task_id: str) -> None:
        now = utcnow()
        with self.tx(immediate=True) as db:
            changed = db.execute(
                "UPDATE v1_agent_sessions SET state='suspended',updated_at=? "
                "WHERE id=? AND project_id=? AND task_id=?",
                (now, agent_session_id, project_id, task_id),
            ).rowcount
            if changed:
                self._event(db, project_id, task_id, None, "agent.session.suspended", {
                    "agent_session_id": agent_session_id,
                })

    def finish_agent_run(
        self,
        agent_run_id: str,
        status: str,
        final_text: str,
        error: str | None,
        iterations: int,
        tool_calls: int,
        *,
        error_code: str | None = None,
        error_category: str | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        with self.tx(immediate=True) as db:
            row = db.execute("SELECT project_id,task_id,column_run_id,kind,started_at,status FROM v1_agent_runs WHERE id=?", (agent_run_id,)).fetchone()
            if not row:
                raise KeyError(agent_run_id)
            AGENT_RUN_STATE_MACHINE.require(row[5], status)
            db.execute(
                "UPDATE v1_agent_runs SET status=?,final_text=?,error=?,error_code=?,error_category=?,checkpoint_json=?,iterations=?,tool_calls=?,duration_seconds=?,finished_at=? WHERE id=?",
                (status, final_text, error, error_code, error_category, self._pack_json(checkpoint or {}), iterations, tool_calls, max(0.0, (datetime.fromisoformat(now) - datetime.fromisoformat(row[4])).total_seconds()), now, agent_run_id),
            )
            self._event(db, row[0], row[1], row[2], "agent.finished", {"agent_run_id": agent_run_id, "kind": row[3], "status": status, "error": error})
        return self.get_agent_run(row[0], agent_run_id)

    def get_agent_run(self, project_id: str, agent_run_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = self._dict(db.execute("SELECT * FROM v1_agent_runs WHERE id=? AND project_id=?", (agent_run_id, project_id)).fetchone())
        if not row:
            raise KeyError(agent_run_id)
        AGENT_RUN_STATE_MACHINE.parse(row["status"])
        return self._decode(row, "context_json", "capabilities_json", "checkpoint_json")  # type: ignore[return-value]

    def agent_runs(self, *, project_id: str, task_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.policy.service_limits.default_page_size
        where: list[str] = ["project_id=?"]
        values: list[Any] = [project_id]
        if task_id:
            where.append("task_id=?")
            values.append(task_id)
        values.append(min(max(limit, 1), self.policy.service_limits.max_page_size))
        clause = f"WHERE {' AND '.join(where)}"
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM v1_agent_runs {clause} ORDER BY created_at DESC LIMIT ?", values).fetchall()
        result = [self._decode(dict(row), "context_json", "capabilities_json", "checkpoint_json") for row in rows]
        for item in result:
            AGENT_RUN_STATE_MACHINE.parse(item["status"])
        return result  # type: ignore[return-value]

    def conversation_job_agent_runs(self, project_id: str, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_agent_runs "
                "WHERE project_id=? AND conversation_job_id=? AND kind='conversation' "
                "ORDER BY created_at,id",
                (project_id, job_id),
            ).fetchall()
        return [
            self._decode(dict(row), "context_json", "capabilities_json", "checkpoint_json")
            for row in rows
        ]  # type: ignore[misc]

    def add_agent_message(
        self,
        agent_run_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        progress_details: dict[str, Any] | None = None,
        emit_progress: bool = True,
    ) -> dict[str, Any]:
        now = utcnow()
        packed_content, packed_calls = self._pack_text(content), self._pack_json(tool_calls or [])
        conversation_progress: tuple[str, str, dict[str, Any]] | None = None
        with self.tx(immediate=True) as db:
            run = db.execute(
                "SELECT project_id,kind,conversation_job_id FROM v1_agent_runs WHERE id=?",
                (agent_run_id,),
            ).fetchone()
            if not run:
                raise KeyError(agent_run_id)
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM v1_agent_messages WHERE agent_run_id=?", (agent_run_id,)).fetchone()[0]
            db.execute(
                "INSERT INTO v1_agent_messages(project_id,agent_run_id,sequence,role,content,tool_calls_json,tool_call_id,created_at) VALUES((SELECT project_id FROM v1_agent_runs WHERE id=?),?,?,?,?,?,?,?)",
                (agent_run_id, agent_run_id, sequence, role, packed_content, packed_calls, tool_call_id, now),
            )
            if emit_progress and run["kind"] == "conversation" and role == "assistant" and (content.strip() or tool_calls):
                parts = [content] if content.strip() else []
                for call in tool_calls or []:
                    function = call.get("function") or {}
                    arguments = function.get("arguments") or {}
                    parts.append(
                        "调用工具："
                        + str(function.get("name") or call.get("name") or "unknown")
                        + "\n输入："
                        + json.dumps(arguments, ensure_ascii=False, default=str)
                    )
                conversation_progress = (
                    "model_output",
                    "\n\n".join(parts),
                    {"sequence": sequence, **(progress_details or {})},
                )
            elif emit_progress and run["kind"] == "conversation" and role == "tool":
                conversation_progress = (
                    "tool_result",
                    f"工具返回（{tool_call_id or 'unknown'}）\n{content}",
                    {"sequence": sequence, "tool_call_id": tool_call_id, **(progress_details or {})},
                )
        if conversation_progress:
            self.record_conversation_progress(
                agent_run_id,
                kind=conversation_progress[0],
                content=conversation_progress[1],
                details=conversation_progress[2],
            )
        return {"agent_run_id": agent_run_id, "sequence": sequence, "role": role, "content": content, "tool_calls": tool_calls or [], "tool_call_id": tool_call_id, "created_at": now}

    def record_conversation_progress(
        self,
        agent_run_id: str,
        *,
        kind: str,
        content: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Persist complete Conversation progress as ordered event chunks."""
        with self.connect() as db:
            run = db.execute(
                "SELECT project_id,kind,conversation_job_id FROM v1_agent_runs WHERE id=?",
                (agent_run_id,),
            ).fetchone()
        if not run or run["kind"] != "conversation":
            return
        chunks = [content]
        with self.tx(immediate=True) as db:
            for chunk_index, chunk in enumerate(chunks, start=1):
                self._event(
                    db,
                    str(run["project_id"]),
                    None,
                    None,
                    "conversation.progress",
                    {
                        "agent_run_id": agent_run_id,
                        "conversation_job_id": run["conversation_job_id"],
                        "kind": kind,
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                        "content": chunk,
                        **(details or {}),
                    },
                )

    def agent_messages(self, project_id: str, agent_run_id: str, limit: int | None = None, after_sequence: int = 0) -> list[dict[str, Any]]:
        limit = limit or self.policy.service_limits.detail_page_size
        self.get_agent_run(project_id, agent_run_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_agent_messages WHERE project_id=? AND agent_run_id=? AND sequence>? ORDER BY sequence LIMIT ?", (project_id, agent_run_id, after_sequence, min(max(limit, 1), self.policy.service_limits.max_page_size))).fetchall()
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
        return {"id": cursor.lastrowid, "agent_run_id": agent_run_id, "sequence": sequence, "tool_call_id": tool_call_id, "capability": capability, "arguments": arguments, "result": result, "ok": ok, "status": ToolInvocationStatus.SUCCEEDED.value if ok else ToolInvocationStatus.FAILED.value, "created_at": now}

    def tool_invocations(
        self,
        project_id: str,
        agent_run_id: str,
        limit: int | None = None,
        after_sequence: int = 0,
        *,
        hydrate_payloads: bool = False,
    ) -> list[dict[str, Any]]:
        limit = limit or self.policy.service_limits.detail_page_size
        self.get_agent_run(project_id, agent_run_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_tool_invocations WHERE project_id=? AND agent_run_id=? AND sequence>? ORDER BY sequence LIMIT ?", (project_id, agent_run_id, after_sequence, min(max(limit, 1), self.policy.service_limits.max_page_size))).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._decode(dict(row), "arguments_json", "result_json")
            item["ok"] = bool(item["ok"])
            item["status"] = ToolInvocationStatus.SUCCEEDED.value if item["ok"] else ToolInvocationStatus.FAILED.value
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

    def artifacts(self, project_id: str, task_id: str, limit: int | None = None, after: str = "") -> list[dict[str, Any]]:
        limit = limit or self.policy.service_limits.detail_page_size
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_artifacts WHERE project_id=? AND task_id=? AND created_at>? ORDER BY created_at LIMIT ?", (project_id, task_id, after, min(max(limit, 1), self.policy.service_limits.max_page_size))).fetchall()
        return [self._decode(dict(row), "meta_json") for row in rows]  # type: ignore[misc]

    def events(self, project_id: str | None = None, task_id: str | None = None, after: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.policy.service_limits.detail_page_size
        where, values = ["id>?"], [after]
        if project_id:
            where.append("project_id=?")
            values.append(project_id)
        if task_id:
            where.append("task_id=?")
            values.append(task_id)
        values.append(min(max(limit, 1), self.policy.service_limits.max_page_size))
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM v1_events WHERE {' AND '.join(where)} ORDER BY id LIMIT ?", values).fetchall()
        return [self._decode(dict(row), "data_json") for row in rows]  # type: ignore[misc]

    def record_external_event(self, project_id: str, event_type: str, correlation_key: str, output: dict[str, Any]) -> dict[str, Any]:
        self.get_project(project_id)
        now = utcnow()
        with self.tx(immediate=True) as db:
            self._event(db, project_id, None, None, event_type, {"correlation_key": correlation_key, "output": output, "source": "automation_api"})
            event_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
        return {"id": event_id, "project_id": project_id, "type": event_type, "correlation_key": correlation_key, "output": output, "created_at": now}

    def correlated_event(self, project_id: str, event_type: str, correlation_key: str, after: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM v1_events WHERE project_id=? AND type=? AND created_at>=? AND json_extract(data_json,'$.correlation_key')=? ORDER BY id LIMIT 1",
                (project_id, event_type, after, correlation_key),
            ).fetchone()
        return self._decode(dict(row), "data_json") if row else None

    def mailbox(self, project_id: str, *, state: str = "pending", limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.policy.context.mailbox_limit
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_project_mailbox WHERE project_id=? AND state=? ORDER BY id LIMIT ?",
                (project_id, state, min(max(limit, 1), self.policy.service_limits.max_page_size)),
            ).fetchall()
        return [self._decode(dict(row), "payload_json") for row in rows]  # type: ignore[misc]

    def schedule_review(self, project_id: str, reason: str, due_at: str) -> dict[str, Any]:
        self.get_project(project_id)
        review_id, now = new_id("review"), utcnow()
        with self.tx(immediate=True) as db:
            db.execute(
                "INSERT INTO v1_scheduled_reviews(id,project_id,reason,due_at,state,created_at) VALUES(?,?,?,?,'pending',?)",
                (review_id, project_id, reason, due_at, now),
            )
            self._event(db, project_id, None, None, "supervision.review_scheduled", {"review_id": review_id, "reason": reason, "due_at": due_at})
        return {"id": review_id, "project_id": project_id, "reason": reason, "due_at": due_at, "state": "pending", "created_at": now}

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

    def mark_execution_receipt_awaiting(self, project_id: str, execution_key: str, result: dict[str, Any]) -> None:
        with self.tx(immediate=True) as db:
            changed = db.execute(
                "UPDATE v1_execution_receipts SET status='awaiting',result_json=?,error=NULL WHERE project_id=? AND execution_key=? AND status='started'",
                (self._pack_json(result), project_id, execution_key),
            ).rowcount
            if changed != 1:
                raise RuntimeError("execution receipt changed before awaiting was persisted")

    def complete_awaiting_receipt(self, project_id: str, execution_key: str, result: Any) -> None:
        with self.tx(immediate=True) as db:
            db.execute(
                "UPDATE v1_execution_receipts SET status='completed',result_json=?,error=NULL,finished_at=? WHERE project_id=? AND execution_key=? AND status='awaiting'",
                (self._pack_json(result), utcnow(), project_id, execution_key),
            )

    def governance_decisions(self, project_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self.policy.service_limits.detail_page_size
        with self.connect() as db:
            rows = db.execute("SELECT * FROM v1_governance_decisions WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, min(max(limit, 1), self.policy.service_limits.max_page_size))).fetchall()
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

    def unresolved_failures(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        self.get_project(project_id)
        with self.connect() as db:
            task_rows = db.execute(
                "SELECT id,title,error,rerun_of_task_id,finished_at FROM v1_tasks "
                "WHERE project_id=? AND status='failed' AND resolved_by_task_id IS NULL "
                "ORDER BY created_at",
                (project_id,),
            ).fetchall()
            job_rows = db.execute(
                "SELECT id,trigger_kind,error,finished_at FROM v1_conversation_jobs "
                "WHERE project_id=? AND status='failed' AND resolved_by_job_id IS NULL "
                "ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return {
            "tasks": [
                {
                    "id": row[0],
                    "title": row[1],
                    "error": row[2],
                    "rerun_of_task_id": row[3],
                    "finished_at": row[4],
                }
                for row in task_rows
            ],
            "conversation_jobs": [
                {
                    "id": row[0],
                    "trigger_kind": row[1],
                    "error": row[2],
                    "finished_at": row[3],
                }
                for row in job_rows
            ],
        }

    def project_quiescence(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        now = datetime.now(timezone.utc)
        with self.connect() as db:
            conversation_jobs = int(db.execute(
                "SELECT COUNT(*) FROM v1_conversation_jobs WHERE project_id=? AND status IN ('queued','running')", (project_id,)
            ).fetchone()[0])
            active_tasks = int(db.execute(
                "SELECT COUNT(*) FROM v1_tasks WHERE project_id=? AND status IN ('pending','running','waiting','recovering')", (project_id,)
            ).fetchone()[0])
            due_reviews = int(db.execute(
                "SELECT COUNT(*) FROM v1_scheduled_reviews WHERE project_id=? AND state='pending' AND due_at<=?", (project_id, utcnow())
            ).fetchone()[0])
            pending_mailbox = int(
                db.execute(
                    "SELECT COUNT(*) FROM v1_project_mailbox "
                    "WHERE project_id=? AND state IN ('pending','claimed')",
                    (project_id,),
                ).fetchone()[0]
            )
            agent_state_row = db.execute(
                "SELECT state FROM v1_conversation_agents WHERE project_id=?",
                (project_id,),
            ).fetchone()
            unresolved_task_failures = int(
                db.execute(
                    "SELECT COUNT(*) FROM v1_tasks "
                    "WHERE project_id=? AND status='failed' AND resolved_by_task_id IS NULL",
                    (project_id,),
                ).fetchone()[0]
            )
            unresolved_conversation_failures = int(
                db.execute(
                    "SELECT COUNT(*) FROM v1_conversation_jobs "
                    "WHERE project_id=? AND status='failed' AND resolved_by_job_id IS NULL",
                    (project_id,),
                ).fetchone()[0]
            )
            activity_values = [project["updated_at"]]
            for table, field in (("v1_events", "created_at"), ("v1_artifacts", "created_at"), ("v1_agent_runs", "created_at"), ("v1_tasks", "created_at")):
                value = db.execute(f"SELECT MAX({field}) FROM {table} WHERE project_id=?", (project_id,)).fetchone()[0]
                if value:
                    activity_values.append(value)
        latest_activity_at = max(activity_values)
        stable_seconds = max(0.0, (now - datetime.fromisoformat(latest_activity_at)).total_seconds())
        remaining = max(0.0, self.policy.scheduling.quiescence_observation_seconds - stable_seconds)
        blockers = {
            "conversation_jobs": conversation_jobs,
            "nonterminal_tasks": active_tasks,
            "unacknowledged_mailbox": pending_mailbox,
            "due_scheduled_reviews": due_reviews,
            "stability_window_remaining_seconds": remaining,
        }
        physically_quiescent = (
            conversation_jobs == 0
            and active_tasks == 0
            and pending_mailbox == 0
            and due_reviews == 0
            and remaining == 0
        )
        attention = bool(agent_state_row and agent_state_row[0] == "attention")
        unresolved = unresolved_task_failures + unresolved_conversation_failures
        return {
            "project_id": project_id,
            "quiescent": physically_quiescent,
            "observed_at": now.isoformat(timespec="milliseconds"),
            "latest_activity_at": latest_activity_at,
            "required_stability_seconds": self.policy.scheduling.quiescence_observation_seconds,
            "governance_outcome": (
                "attention_required"
                if attention or (physically_quiescent and unresolved)
                else "settled"
                if physically_quiescent
                else "active"
            ),
            "blockers": blockers,
            "unresolved_failures": {
                "tasks": unresolved_task_failures,
                "conversation_jobs": unresolved_conversation_failures,
            },
        }

    def observe_mailbox(self, project_id: str, message_id: int) -> bool:
        now = utcnow()
        with self.tx(immediate=True) as db:
            decision_id = new_id("gdec")
            cursor = db.execute(
                "UPDATE v1_project_mailbox SET state='acknowledged',observed_at=?,acknowledged_at=?,governance_decision_id=? "
                "WHERE id=? AND project_id=? AND state='pending'",
                (now, now, decision_id, message_id, project_id),
            )
            if cursor.rowcount == 1:
                db.execute(
                    "INSERT INTO v1_governance_decisions(id,project_id,kind,subject_id,decision,data_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (decision_id, project_id, "mailbox_ack", str(message_id), "observed_no_intervention", "{}", now),
                )
        return cursor.rowcount == 1

    def _event(self, db: sqlite3.Connection, project_id: str, task_id: str | None, run_id: str | None, event_type: str, data: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO v1_events(project_id,task_id,run_id,type,data_json,created_at) VALUES(?,?,?,?,?,?)",
            (project_id, task_id, run_id, event_type, json.dumps(data, ensure_ascii=False, default=str), utcnow()),
        )
        db.execute("UPDATE v1_projects SET state_version=state_version+1,updated_at=? WHERE id=?", (utcnow(), project_id))

    def _refresh_projection(self, db: sqlite3.Connection, project_id: str) -> None:
        version_row = db.execute("SELECT state_version FROM v1_projects WHERE id=?", (project_id,)).fetchone()
        if not version_row:
            return
        workflow_row = db.execute("SELECT id,revision,definition_json FROM v1_workflow_revisions WHERE project_id=? AND active=1", (project_id,)).fetchone()
        tasks = []
        for row in db.execute(
            "SELECT id,title,substr(brief,1,1000) AS brief,status,current_column,attempt,"
            "error,state_version,terminal_artifact_id,notified_at,observed_at,"
            "supervision_action,rerun_of_task_id,resolved_by_task_id,created_at,updated_at "
            "FROM v1_tasks WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, self.policy.context.task_summary_limit),
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
        event = db.execute(
            "SELECT id FROM v1_events WHERE project_id=? AND type=? AND task_id IS ? AND run_id IS ? ORDER BY id DESC LIMIT 1",
            (project_id, event_type, task_id, run_id),
        ).fetchone()
        db.execute(
            "INSERT INTO v1_project_mailbox(project_id,event_id,event_type,task_id,run_id,payload_json,state,created_at) "
            "VALUES(?,?,?,?,?,?,'pending',?)",
            (project_id, event[0] if event else None, event_type, task_id, run_id, json.dumps(payload, ensure_ascii=False, default=str), utcnow()),
        )

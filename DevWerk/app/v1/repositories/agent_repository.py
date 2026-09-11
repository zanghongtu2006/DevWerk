"""Durable agent identity, work bindings and addressed input.

An agent is not a thread. A completed assignment releases its execution slot,
while its session remains available for later work in the same requirement.
"""
from __future__ import annotations

import json
import hashlib
from typing import Any

from app.v1.execution_control import ExecutionOwnershipLost, ExecutionBudgetExceeded
from app.v1.storage_support import new_id, utcnow


class AgentBusy(RuntimeError):
    error_code = "agent_busy"
    error_category = "infrastructure_transient"


class RequirementConflict(ValueError):
    error_code = 'requirement_scope_conflict'


class AgentRepository:
    def __init__(self, store):
        self.store = store

    def init_schema(self):
        with self.store.tx(immediate=True) as db:
            statements = [
                'CREATE TABLE IF NOT EXISTS v1_agent_worker_slots (requirement_id TEXT NOT NULL,worker_key TEXT NOT NULL,instance_id TEXT NOT NULL,PRIMARY KEY(requirement_id,worker_key))',
                "CREATE TABLE IF NOT EXISTS v1_requirement_planning_ops (operation_id TEXT PRIMARY KEY,requirement_id TEXT NOT NULL,created_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS v1_requirement_task_causes (requirement_id TEXT NOT NULL,cause_key TEXT NOT NULL,task_ref TEXT NOT NULL,plan_fingerprint TEXT NOT NULL,task_id TEXT NOT NULL,PRIMARY KEY(requirement_id,cause_key,task_ref))",
                "CREATE TABLE IF NOT EXISTS v1_assignment_waits (assignment_id TEXT NOT NULL,operation_id TEXT PRIMARY KEY,created_at TEXT NOT NULL)",
                """CREATE TABLE IF NOT EXISTS v1_agent_operations (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scope_id TEXT NOT NULL,
                    source_run_id TEXT NOT NULL, source_sequence INTEGER NOT NULL, call_index INTEGER NOT NULL,
                    tool_call_id TEXT NOT NULL, capability TEXT NOT NULL, arguments_json TEXT NOT NULL,
                    result_json TEXT, completion_json TEXT, wait_json TEXT,
                    delivered INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    UNIQUE(source_run_id,source_sequence,call_index))""",
                """CREATE TABLE IF NOT EXISTS v1_requirements (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES v1_projects(id),
                    scope_key TEXT NOT NULL, objective TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id,scope_key))""",
                """CREATE TABLE IF NOT EXISTS v1_agent_instances (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES v1_projects(id),
                    requirement_id TEXT REFERENCES v1_requirements(id), parent_id TEXT,
                    role TEXT NOT NULL, reuse_key TEXT NOT NULL, lifecycle TEXT NOT NULL DEFAULT 'available',
                    active_session_id TEXT, active_assignment_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id,requirement_id,reuse_key))""",
                """CREATE TABLE IF NOT EXISTS v1_agent_assignments (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES v1_projects(id),
                    requirement_id TEXT NOT NULL REFERENCES v1_requirements(id), requirement_revision INTEGER NOT NULL,
                    task_id TEXT NOT NULL REFERENCES v1_tasks(id), column_run_id TEXT NOT NULL UNIQUE REFERENCES v1_column_runs(id),
                    agent_instance_id TEXT NOT NULL REFERENCES v1_agent_instances(id), session_id TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1, task_generation INTEGER NOT NULL, lease_owner TEXT,
                    status TEXT NOT NULL DEFAULT 'running', input_json TEXT NOT NULL, contract_json TEXT NOT NULL,
                    result_json TEXT, model_calls INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0,
                    resumes INTEGER NOT NULL DEFAULT 0, no_progress_awaits INTEGER NOT NULL DEFAULT 0,
                    progress_marker TEXT NOT NULL DEFAULT '', budget_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
                "CREATE INDEX IF NOT EXISTS idx_agent_assignments_worker ON v1_agent_assignments(agent_instance_id,status)",
                """CREATE TABLE IF NOT EXISTS v1_agent_context_sessions (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES v1_projects(id),
                    instance_id TEXT NOT NULL REFERENCES v1_agent_instances(id),
                    session_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active',
                    legacy_session_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(instance_id,session_key))""",
                """CREATE TABLE IF NOT EXISTS v1_agent_context_snapshots (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    through_message_id INTEGER NOT NULL, summary_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(session_id,revision))""",
            ]
            for statement in statements:
                db.execute(statement)
            for table in ("v1_tasks", "v1_task_plans", "v1_workflow_plans", "v1_conversation_jobs"):
                self.store.schema_repository._ensure_column(db, table, "requirement_id", "TEXT")
            for name, declaration in {
                "recipient_agent_id": "TEXT", "assignment_id": "TEXT", "consumed_by_run_id": "TEXT",
                "message_kind": "TEXT NOT NULL DEFAULT 'event'", "dedupe_key": "TEXT",
            }.items():
                self.store.schema_repository._ensure_column(db, "v1_project_mailbox", name, declaration)
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_message_dedupe ON v1_project_mailbox(project_id,dedupe_key) WHERE dedupe_key IS NOT NULL")
            self.store.schema_repository._ensure_column(db, "v1_conversation_jobs", "claim_token", "TEXT")
            self.store.schema_repository._ensure_column(db, 'v1_conversation_jobs', 'requirement_revision', 'INTEGER')
            self.store.schema_repository._ensure_column(db, 'v1_agent_runs', 'operation_protocol', 'INTEGER NOT NULL DEFAULT 0')
            # Additive migration: legacy task-scoped sessions remain untouched.
            # New Worker sessions have explicit ownership; legacy_session_id can
            # link an audited old transcript without guessing cross-Task identity.
            for name in ("assignment_id", "agent_instance_id"):
                self.store.schema_repository._ensure_column(db, "v1_agent_runs", name, "TEXT")

    def requirement(self, project_id, *, objective="Project delivery", scope_key="default", strict=False):
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            row = db.execute("SELECT * FROM v1_requirements WHERE project_id=? AND scope_key=?", (project_id, scope_key)).fetchone()
            if row is not None and strict and (row['objective'] != objective or row['status'] != 'active'):
                raise RequirementConflict('This scope_key already identifies a Requirement with a different objective or closed status. Use project.scope.revise for an extension; use a distinct scope_key only for independent work.')
            if row is None:
                identity = new_id("req")
                db.execute("INSERT INTO v1_requirements(id,project_id,scope_key,objective,created_at,updated_at) VALUES(?,?,?,?,?,?)", (identity, project_id, scope_key, objective, now, now))
                row = db.execute("SELECT * FROM v1_requirements WHERE id=?", (identity,)).fetchone()
            return dict(row)

    def get_requirement(self, project_id, requirement_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM v1_requirements WHERE id=? AND project_id=?", (requirement_id, project_id)).fetchone()
        if not row:
            raise KeyError("requirement is outside this Project")
        return dict(row)

    def turn_requirement(self, job, mailbox):
        """Bind the Conversation turn to the revision of its durable cause."""
        if job.get('requirement_id'):
            return self.store.scopes.requirement_version(job['project_id'], job['requirement_id'], job.get('requirement_revision'))
        requirement = None
        if job.get('trigger_kind', 'user') == 'user' and job.get('start_task'):
            with self.store.connect() as db:
                recent = db.execute("SELECT r.* FROM v1_requirements r JOIN v1_conversation_jobs j ON j.requirement_id=r.id WHERE j.project_id=? AND j.id!=? AND j.trigger_kind='user' ORDER BY j.created_at DESC,j.rowid DESC LIMIT 1", (job['project_id'], job['id'])).fetchone()
            requirement = dict(recent) if recent else self.requirement(job['project_id'], objective=job.get('message') or 'Project delivery')
        else:
            ids = set()
            with self.store.connect() as db:
                for message in mailbox:
                    if message.get('task_id'):
                        row = db.execute('SELECT requirement_id,requirement_revision FROM v1_tasks WHERE id=? AND project_id=?', (message['task_id'], job['project_id'])).fetchone()
                        if row and row[0]:
                            ids.add((row[0], row[1]))
                if len(ids) == 1:
                    req_id, revision = ids.pop()
                    requirement = self.store.scopes.requirement_version(job['project_id'], req_id, revision)
                elif not ids:
                    rows = db.execute("SELECT * FROM v1_requirements WHERE project_id=? AND status='active'", (job['project_id'],)).fetchall()
                    if len(rows) == 1:
                        requirement = dict(rows[0])
        if requirement:
            with self.store.tx(immediate=True) as db:
                db.execute('UPDATE v1_conversation_jobs SET requirement_id=?,requirement_revision=? WHERE id=? AND requirement_id IS NULL', (requirement['id'], requirement['revision'], job['id']))
        return requirement

    def conversation_job(self, ctx):
        with self.store.connect() as db:
            row = db.execute('SELECT j.* FROM v1_conversation_jobs j JOIN v1_agent_runs r ON r.conversation_job_id=j.id WHERE r.id=? AND j.project_id=? AND r.project_id=? AND r.kind=\'conversation\' AND j.status=\'running\'', (ctx.agent_run_id, ctx.project_id, ctx.project_id)).fetchone()
        if not row:
            raise ExecutionOwnershipLost('This operation requires a running project Conversation Agent turn')
        return dict(row)

    def assert_conversation(self, ctx):
        if ctx.column_run_id or ctx.agent_instance_id != self.store.conversation_agent(ctx.project_id)['logical_id']:
            raise PermissionError('This operation belongs to the project Conversation Agent; a Column Worker cannot perform it')
        return self.conversation_job(ctx)

    def select_requirement(self, ctx, requirement_id):
        job = self.assert_conversation(ctx)
        requirement = self.get_requirement(ctx.project_id, requirement_id)
        if job['trigger_kind'] != 'user' and (job['requirement_id'], job['requirement_revision']) != (requirement_id, requirement['revision']):
            raise RequirementConflict('Event turns must retain the Requirement revision of their source work')
        if requirement['status'] != 'active':
            raise RequirementConflict('Requirement is '+requirement['status']+'; use project.scope.revise on a user-requested extension before planning')
        with self.store.tx(immediate=True) as db:
            if ctx.execution_control:
                ctx.execution_control.check()
            changed = db.execute('UPDATE v1_conversation_jobs SET requirement_id=?,requirement_revision=? WHERE id=(SELECT conversation_job_id FROM v1_agent_runs WHERE id=?) AND status=\'running\'', (requirement_id, requirement['revision'], ctx.agent_run_id)).rowcount
            if changed != 1:
                raise ExecutionOwnershipLost('Requirement selection requires a running Conversation turn')
        return requirement

    def charge_planning(self, ctx):
        if not ctx.execution_key or not ctx.requirement_id:
            return
        with self.store.tx(immediate=True) as db:
            if db.execute('SELECT 1 FROM v1_requirement_planning_ops WHERE operation_id=?', (ctx.execution_key,)).fetchone():
                return
            count = db.execute('SELECT COUNT(*) FROM v1_requirement_planning_ops WHERE requirement_id=?', (ctx.requirement_id,)).fetchone()[0]
            if count >= self.store.policy.execution.requirement_max_planning_actions:
                raise ExecutionBudgetExceeded('Requirement planning action limit reached; review the work graph')
            db.execute('INSERT INTO v1_requirement_planning_ops VALUES(?,?,?)', (ctx.execution_key, ctx.requirement_id, utcnow()))

    def attach_task(self, task_id, requirement_id):
        task = self.store.get_task(task_id)
        requirement = self.get_requirement(task["project_id"], requirement_id)
        if requirement["status"] != "active":
            raise ValueError("Requirement is not active")
        with self.store.tx(immediate=True) as db:
            if db.execute("SELECT 1 FROM v1_agent_assignments WHERE task_id=?", (task_id,)).fetchone():
                raise ValueError("An assigned Task cannot move between Requirements")
            db.execute("UPDATE v1_tasks SET requirement_id=?,requirement_revision=? WHERE id=?", (requirement_id, requirement['revision'], task_id))

    def main(self, project_id):
        # Legacy storage label; this is the Project's Conversation Agent itself.
        identity = self.store.conversation_agent(project_id)["logical_id"]
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            db.execute("INSERT OR IGNORE INTO v1_agent_instances(id,project_id,role,reuse_key,created_at,updated_at,active_session_id) VALUES(?,?,'main','main',?,?,?)", (identity, project_id, now, now, identity))
            return dict(db.execute("SELECT * FROM v1_agent_instances WHERE id=?", (identity,)).fetchone())

    def get_worker(self, project_id, worker_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM v1_agent_instances WHERE id=? AND project_id=?", (worker_id, project_id)).fetchone()
        if row is None:
            raise KeyError("Agent is outside this Project")
        return dict(row)

    def list_workers(self, project_id):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM v1_agent_instances WHERE project_id=? ORDER BY created_at,id", (project_id,))]

    def list_requirements(self, project_id):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM v1_requirements WHERE project_id=? ORDER BY created_at', (project_id,))]

    def _worker_for_key(self, db, project_id, requirement_id, key):
        row = db.execute('SELECT w.* FROM v1_agent_worker_slots s JOIN v1_agent_instances w ON w.id=s.instance_id WHERE s.requirement_id=? AND s.worker_key=? AND w.project_id=?', (requirement_id, key, project_id)).fetchone()
        return row or db.execute('SELECT * FROM v1_agent_instances WHERE project_id=? AND requirement_id=? AND reuse_key=?', (project_id, requirement_id, key)).fetchone()

    def replace_worker(self, ctx, worker_id, summary):
        self.assert_conversation(ctx)
        if not str(summary).strip():
            raise ValueError('A successor requires an explicit handoff summary')
        with self.store.tx(immediate=True) as db:
            old = self.get_worker(ctx.project_id, worker_id)
            requirement = self.get_requirement(ctx.project_id, old['requirement_id']) if old['requirement_id'] else None
            if old['role'] != 'leaf' or old['lifecycle'] != 'retired' or old['active_assignment_id']:
                raise ValueError('Only an idle retired Worker can have a successor')
            if not requirement or requirement['status'] != 'active' or (ctx.requirement_id, ctx.requirement_revision) != (requirement['id'], requirement['revision']):
                raise RequirementConflict('Select the active current Requirement before replacing a Worker')
            slot = db.execute('SELECT worker_key FROM v1_agent_worker_slots WHERE instance_id=?', (worker_id,)).fetchone()
            key = slot[0] if slot else old['reuse_key']
            current = self._worker_for_key(db, ctx.project_id, requirement['id'], key)
            if current['id'] != worker_id:
                raise ValueError('Worker already has a successor; inspect the current Worker')
            identity, session, now = new_id('worker'), new_id('asess'), utcnow()
            db.execute('INSERT INTO v1_agent_instances(id,project_id,requirement_id,parent_id,role,reuse_key,active_session_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                       (identity, ctx.project_id, requirement['id'], old['parent_id'], 'leaf', key+':successor:'+identity, session, now, now))
            db.execute('INSERT INTO v1_agent_context_sessions(id,project_id,instance_id,session_key,created_at,updated_at) VALUES(?,?,?,?,?,?)', (session, ctx.project_id, identity, key, now, now))
            db.execute('INSERT INTO v1_agent_context_snapshots VALUES(?,?,1,0,?,?)', (new_id('snapshot'), session, json.dumps({'summary': summary, 'predecessor_worker_id': worker_id}), now))
            db.execute('INSERT INTO v1_agent_worker_slots VALUES(?,?,?) ON CONFLICT(requirement_id,worker_key) DO UPDATE SET instance_id=excluded.instance_id', (requirement['id'], key, identity))
            self.store._event(db, ctx.project_id, None, None, 'agent.worker.replaced', {'predecessor_worker_id': worker_id, 'worker_id': identity, 'worker_key': key})
            return {'worker': self.get_worker(ctx.project_id, identity), 'predecessor_worker_id': worker_id, 'worker_key': key}

    def planning_cause(self, ctx):
        if not ctx.agent_run_id or not ctx.requirement_id:
            return None
        with self.store.connect() as db:
            job = db.execute('SELECT j.* FROM v1_conversation_jobs j JOIN v1_agent_runs r ON r.conversation_job_id=j.id WHERE r.id=?', (ctx.agent_run_id,)).fetchone()
            if not job:
                return None
            if job['trigger_kind'] == 'user':
                return f'job:{job["id"]}'
            ids = json.loads(job['mailbox_ids_json'] or '[]')
            causes = []
            for identity in ids:
                row = db.execute('SELECT event_id FROM v1_project_mailbox WHERE id=? AND project_id=?', (identity, ctx.project_id)).fetchone()
                if row:
                    causes.append(f'event:{row[0]}' if row[0] is not None else f'message:{identity}')
            return '|'.join(sorted(set(causes))) if causes else f'job:{job["id"]}'

    def caused_task(self, requirement_id, cause, task_ref, fingerprint):
        if not requirement_id or not cause:
            return None
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM v1_requirement_task_causes WHERE requirement_id=? AND cause_key=? AND task_ref=?', (requirement_id, cause, task_ref)).fetchone()
        if row and row['plan_fingerprint'] != fingerprint:
            raise ValueError('This feedback/task reference already has a different plan; declare distinct follow-up work explicitly')
        return row['task_id'] if row else None

    def record_caused_task(self, requirement_id, cause, task_ref, fingerprint, task_id):
        if requirement_id and cause:
            with self.store.tx(immediate=True) as db:
                db.execute('INSERT INTO v1_requirement_task_causes VALUES(?,?,?,?,?)', (requirement_id, cause, task_ref, fingerprint, task_id))

    def dispatch_available(self, db, task_id):
        task = self.store.get_task(task_id)
        workflow = self.store.workflow_by_id(task['project_id'], task['workflow_revision_id'])
        column = workflow.column(task['current_column'])
        if column.executor.kind != 'agent':
            return True
        key = getattr(column.executor, 'worker_key', None) or column.metadata.get('agent_session_key') or f"task:{task_id}:{column.key}"
        requirement_id = task.get('requirement_id')
        if not requirement_id:
            row = db.execute("SELECT id FROM v1_requirements WHERE project_id=? AND scope_key='default'", (task['project_id'],)).fetchone()
            requirement_id = row[0] if row else None
        if requirement_id:
            requirement = self.get_requirement(task['project_id'], requirement_id)
            if requirement['status'] != 'active':
                return False
            worker = self._worker_for_key(db, task['project_id'], requirement_id, key)
            if worker:
                assignment = db.execute('SELECT task_id FROM v1_agent_assignments WHERE id=?', (worker['active_assignment_id'],)).fetchone()
                if worker['lifecycle'] != 'available' or (assignment and assignment[0] != task_id):
                    return False
        # Claiming a Task reserves the future session writer too. This closes
        # the interval between Task claim and Assignment creation.
        for other in db.execute("SELECT * FROM v1_tasks WHERE project_id=? AND id!=? AND status='running'", (task['project_id'], task_id)).fetchall():
            if (other['requirement_id'] or requirement_id) != requirement_id:
                continue
            other_column = self.store.workflow_by_id(other['project_id'], other['workflow_revision_id']).column(other['current_column'])
            other_key = getattr(other_column.executor, 'worker_key', None) or other_column.metadata.get('agent_session_key') or f"task:{other['id']}:{other_column.key}"
            if other_column.executor.kind == 'agent' and other_key == key:
                return False
        return True

    def close_requirement(self, project_id, requirement_id, status):
        if status not in {'completed', 'cancelled'}:
            raise ValueError('Invalid Requirement closure')
        with self.store.tx(immediate=True) as db:
            self.get_requirement(project_id, requirement_id)
            if db.execute("SELECT 1 FROM v1_tasks WHERE project_id=? AND requirement_id=? AND status NOT IN ('done','failed')", (project_id, requirement_id)).fetchone():
                raise AgentBusy('Settle Requirement Tasks before closing it')
            if db.execute('SELECT 1 FROM v1_agent_instances WHERE requirement_id=? AND active_assignment_id IS NOT NULL', (requirement_id,)).fetchone():
                raise AgentBusy('Settle active Assignments before closing the Requirement')
            db.execute("UPDATE v1_requirements SET status=?,updated_at=? WHERE id=? AND status='active'", (status, utcnow(), requirement_id))
            # Closing delivery is not retirement of its reusable Workers.
            for worker in db.execute('SELECT id FROM v1_agent_instances WHERE requirement_id=?', (requirement_id,)).fetchall():
                self._fail_pending_messages(db, project_id, worker['id'], 'Requirement is '+status)
        return self.get_requirement(project_id, requirement_id)

    def inspect_worker(self, project_id, worker_id):
        worker = self.get_worker(project_id, worker_id)
        with self.store.connect() as db:
            assignments = [self.store._decode(dict(row), 'input_json', 'contract_json', 'result_json', 'budget_json') for row in db.execute('SELECT * FROM v1_agent_assignments WHERE agent_instance_id=? ORDER BY created_at', (worker_id,))]
        return {'worker': worker, 'assignments': assignments, 'messages': self.messages(project_id, worker_id)}

    def context_page(self, project_id, worker_id, *, after=0, limit=20):
        worker = self.get_worker(project_id, worker_id)
        with self.store.connect() as db:
            session = db.execute('SELECT legacy_session_id FROM v1_agent_context_sessions WHERE id=?', (worker['active_session_id'],)).fetchone()
            rows = db.execute("SELECT m.* FROM v1_agent_messages m JOIN v1_agent_runs r ON r.id=m.agent_run_id WHERE r.project_id=? AND r.agent_session_id IN (?,?) AND m.id>? AND m.role!='system' ORDER BY m.id LIMIT ?", (project_id, worker['active_session_id'], session[0] if session else None, after, min(max(limit, 1), 50))).fetchall()
        return [self.store._decode(dict(row), 'tool_calls_json') for row in rows]

    def assign(self, task, run, column, *, worker_key=None):
        now = utcnow()
        main = self.main(task["project_id"])
        requirement = (self.store.scopes.requirement_version(task["project_id"], task["requirement_id"], task.get('requirement_revision'))
                       if task.get("requirement_id") else self.requirement(task["project_id"]))
        if requirement["status"] != "active" or self.get_requirement(task['project_id'], requirement['id'])['status'] != 'active':
            raise ExecutionOwnershipLost("Requirement is no longer active")
        executor = column.executor
        key = worker_key or getattr(executor, "worker_key", None) or column.metadata.get("agent_session_key")
        # Explicit keys opt into requirement reuse. Default workers are Task-local.
        key = str(key or f"task:{task['id']}:{column.key}")
        with self.store.tx(immediate=True) as db:
            self.store.assert_execution_owner(task, db=db)
            scope = self.store.scopes.for_workflow(task['workflow_revision_id'])
            if scope and not scope['requirement_id']:
                self.store.scopes.bind_workflow(task['workflow_revision_id'], scope['binding_id'], requirement['id'], requirement['revision'])
            existing = db.execute("SELECT * FROM v1_agent_assignments WHERE column_run_id=?", (run["id"],)).fetchone()
            if existing:
                active = dict(existing)
                worker = db.execute("SELECT * FROM v1_agent_instances WHERE id=?", (active["agent_instance_id"],)).fetchone()
                if worker["lifecycle"] != "available":
                    raise AgentBusy("Worker is suspended or retired")
                if worker["active_assignment_id"] not in (None, active["id"]):
                    raise AgentBusy("Worker is running another Assignment")
                if active["status"] in {"completed", "cancelled", "failed"}:
                    raise ExecutionOwnershipLost("Assignment is already terminal")
                changed_owner = active["task_generation"] != task["state_version"] or active["lease_owner"] != task.get("lease_owner")
                db.execute("UPDATE v1_agent_assignments SET generation=generation+?,task_generation=?,lease_owner=?,status='running',updated_at=? WHERE id=?", (int(changed_owner), task["state_version"], task.get("lease_owner"), now, active["id"]))
                db.execute("UPDATE v1_agent_instances SET active_assignment_id=?,updated_at=? WHERE id=?", (active["id"], now, worker["id"]))
                return dict(db.execute("SELECT * FROM v1_agent_assignments WHERE id=?", (active["id"],)).fetchone())
            worker = self._worker_for_key(db, task['project_id'], requirement['id'], key)
            if worker is None:
                worker_id, session_id = new_id("worker"), new_id("asess")
                db.execute("INSERT INTO v1_agent_instances(id,project_id,requirement_id,parent_id,role,reuse_key,active_session_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (worker_id, task["project_id"], requirement["id"], main["id"], "leaf", key, session_id, now, now))
                db.execute("INSERT INTO v1_agent_context_sessions(id,project_id,session_key,created_at,updated_at,instance_id) VALUES(?,?,?,?,?,?)", (session_id, task["project_id"], key, now, now, worker_id))
                legacy_key = column.metadata.get('agent_session_key')
                if legacy_key:
                    legacy = db.execute('SELECT id FROM v1_agent_sessions WHERE project_id=? AND task_id=? AND session_key=?', (task['project_id'], task['id'], str(legacy_key))).fetchone()
                    if legacy:
                        db.execute('UPDATE v1_agent_context_sessions SET legacy_session_id=? WHERE id=?', (legacy[0], session_id))
                worker = db.execute("SELECT * FROM v1_agent_instances WHERE id=?", (worker_id,)).fetchone()
            if worker["lifecycle"] != "available" or worker["active_assignment_id"]:
                raise AgentBusy("Worker is not available; its context has a single writer")
            identity = new_id("assignment")
            db.execute("UPDATE v1_tasks SET requirement_id=?,requirement_revision=? WHERE id=?", (requirement["id"], requirement['revision'], task["id"]))
            db.execute("""INSERT INTO v1_agent_assignments(id,project_id,requirement_id,requirement_revision,task_id,column_run_id,
                       agent_instance_id,session_id,task_generation,lease_owner,input_json,contract_json,budget_json,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (identity, task["project_id"], requirement["id"], requirement["revision"], task["id"], run["id"], worker["id"], worker["active_session_id"], task["state_version"], task.get("lease_owner"), json.dumps(task.get("input") or {}), column.model_dump_json(), self.store.policy.execution.model_dump_json(), now, now))
            db.execute("UPDATE v1_agent_instances SET active_assignment_id=?,updated_at=? WHERE id=?", (identity, now, worker["id"]))
            prior = db.execute('SELECT COALESCE(SUM(iterations),0),COALESCE(SUM(tool_calls),0),COUNT(*) FROM v1_agent_runs WHERE column_run_id=?', (run['id'],)).fetchone()
            db.execute('UPDATE v1_agent_assignments SET model_calls=?,tool_calls=?,resumes=? WHERE id=?', (*prior, identity))
            self.store._event(db, task["project_id"], task["id"], run["id"], "agent.assigned", {"assignment_id": identity, "agent_instance_id": worker["id"], "session_id": worker["active_session_id"]})
            return dict(db.execute("SELECT * FROM v1_agent_assignments WHERE id=?", (identity,)).fetchone())

    def assert_owner(self, assignment, *, db=None):
        if db is None:
            with self.store.connect() as conn:
                return self.assert_owner(assignment, db=conn)
        row = db.execute("""SELECT a.*, w.lifecycle,w.active_assignment_id,r.status AS requirement_status,r.revision AS current_revision
                          FROM v1_agent_assignments a JOIN v1_agent_instances w ON w.id=a.agent_instance_id
                          JOIN v1_requirements r ON r.id=a.requirement_id WHERE a.id=?""", (assignment["id"],)).fetchone()
        if (not row or row["generation"] != assignment["generation"] or row["active_assignment_id"] != row["id"]
                or row["lifecycle"] != "available" or row["requirement_status"] != "active"
                or row["status"] not in {"running", "waiting"}):
            raise ExecutionOwnershipLost("Worker/Assignment generation no longer owns this turn")
        version = self.store.scopes.requirement_version(row['project_id'], row['requirement_id'], row['requirement_revision'])
        if version['status'] != 'active':
            raise ExecutionOwnershipLost('Assignment Requirement revision is closed')
        self.store.assert_execution_owner({"id": row["task_id"], "project_id": row["project_id"], "state_version": row["task_generation"], "lease_owner": row["lease_owner"]}, db=db)

    def finish(self, assignment, status, result=None):
        if status not in {"completed", "failed", "cancelled", "waiting", "blocked"}:
            raise ValueError("Invalid Assignment settlement")
        with self.store.tx(immediate=True) as db:
            self.assert_owner(assignment, db=db)
            db.execute("UPDATE v1_agent_assignments SET status=?,result_json=?,updated_at=? WHERE id=?", (status, json.dumps(result), utcnow(), assignment["id"]))
            if status != "waiting":
                db.execute("UPDATE v1_agent_instances SET active_assignment_id=NULL,updated_at=? WHERE id=? AND active_assignment_id=?", (utcnow(), assignment["agent_instance_id"], assignment["id"]))
            if status in {'completed', 'failed', 'cancelled'}:
                self._fail_pending_messages(db, assignment['project_id'], assignment['agent_instance_id'], 'Assignment is '+status, assignment_id=assignment['id'])
            if status in {"completed", "failed", "blocked"}:
                self.store._event(db, assignment['project_id'], assignment['task_id'], assignment['column_run_id'], 'agent.assignment.'+status, {'assignment_id': assignment['id'], 'agent_instance_id': assignment['agent_instance_id'], 'result': result})
                self.store._mailbox(db, assignment["project_id"], "agent.assignment." + status, assignment["task_id"], assignment["column_run_id"], {"assignment_id": assignment["id"], "agent_instance_id": assignment["agent_instance_id"], "result": result})

    def validate_message_target(self, db, project_id, worker_id, assignment_id=None):
        worker = self.get_worker(project_id, worker_id)
        if worker['role'] != 'leaf':
            raise ValueError('Worker messages require a leaf recipient')
        if worker["lifecycle"] == "retired":
            raise ValueError("Worker is retired")
        if self.get_requirement(project_id, worker['requirement_id'])['status'] != 'active':
            raise ValueError('Worker Requirement is closed')
        if assignment_id:
            assignment = db.execute('SELECT status FROM v1_agent_assignments WHERE id=? AND agent_instance_id=? AND project_id=?', (assignment_id, worker_id, project_id)).fetchone()
            if not assignment:
                raise ValueError('Message Assignment does not belong to this Worker')
            if assignment['status'] in {'completed', 'failed', 'cancelled'}:
                raise ValueError('Message Assignment has ended')

    def _fail_pending_messages(self, db, project_id, worker_id, reason, *, assignment_id=None):
        from app.v1.states import MAILBOX_STATE_MACHINE, MailboxStatus
        MAILBOX_STATE_MACHINE.require(MailboxStatus.PENDING, MailboxStatus.FAILED)
        db.execute("UPDATE v1_project_mailbox SET state='failed',failed_at=?,last_error=? "
                   "WHERE project_id=? AND recipient_agent_id=? AND state='pending' "
                   "AND (? IS NULL OR assignment_id=?)",
                   (utcnow(), reason+' before message consumption', project_id, worker_id, assignment_id, assignment_id))

    def send(self, project_id, worker_id, content, *, assignment_id=None, dedupe_key=None):
        if not str(content).strip():
            raise ValueError("Message must not be empty")
        with self.store.tx(immediate=True) as db:
            if dedupe_key:
                existing = db.execute("SELECT * FROM v1_project_mailbox WHERE project_id=? AND dedupe_key=?", (project_id, dedupe_key)).fetchone()
                if existing:
                    payload = json.loads(existing['payload_json'])
                    if existing['recipient_agent_id'] != worker_id or existing['assignment_id'] != assignment_id or payload.get('content') != str(content):
                        raise ValueError('Message dedupe_key cannot refer to different input')
                    return dict(existing)
            self.validate_message_target(db, project_id, worker_id, assignment_id)
            self.store._mailbox(db, project_id, "agent.message", None, None, {"content": str(content), "worker_id": worker_id})
            identity = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("UPDATE v1_project_mailbox SET recipient_agent_id=?,assignment_id=?,message_kind='steer',dedupe_key=? WHERE id=?", (worker_id, assignment_id, dedupe_key, identity))
            return dict(db.execute("SELECT * FROM v1_project_mailbox WHERE id=?", (identity,)).fetchone())

    def messages(self, project_id, worker_id):
        self.get_worker(project_id, worker_id)
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM v1_project_mailbox WHERE project_id=? AND recipient_agent_id=? ORDER BY id", (project_id, worker_id))]

    def consume_messages(self, assignment, agent_run_id):
        with self.store.tx(immediate=True) as db:
            self.assert_owner(assignment, db=db)
            rows = db.execute("SELECT * FROM v1_project_mailbox WHERE project_id=? AND recipient_agent_id=? AND state='pending' AND (assignment_id IS NULL OR assignment_id=?) ORDER BY id", (assignment["project_id"], assignment["agent_instance_id"], assignment["id"])).fetchall()
            if not rows:
                return []
            payload = [{"message_id": row["id"], "input": json.loads(row["payload_json"])} for row in rows]
            self.store.add_agent_message(agent_run_id, "user", json.dumps({"worker_messages": payload}), [], emit_progress=False)
            for row in rows:
                now = utcnow()
                db.execute("UPDATE v1_project_mailbox SET state='acknowledged',consumed_by_run_id=?,acknowledged_at=?,received_at=?,delivered_at=?,delivery_count=delivery_count+1 WHERE id=? AND state='pending'", (agent_run_id, now, now, now, row["id"]))
                db.execute("INSERT INTO v1_mailbox_deliveries(project_id,mailbox_id,attempt_no,conversation_job_id,consumer_kind,state,delivered_at,received_at,finished_at) VALUES(?,?,?,?,?,'acknowledged',?,?,?)", (assignment['project_id'], row['id'], row['delivery_count']+1, agent_run_id, 'worker_assignment', now, now, now))
            return payload

    def set_lifecycle(self, project_id, worker_id, state):
        if state not in {"available", "suspended", "retired"}:
            raise ValueError("Invalid Worker lifecycle")
        with self.store.tx(immediate=True) as db:
            worker = self.get_worker(project_id, worker_id)
            if worker["role"] == "main":
                raise ValueError("Main lifecycle is owned by its Project")
            if worker["lifecycle"] == "retired":
                raise ValueError("Retired Worker cannot be implicitly revived")
            if worker["active_assignment_id"]:
                raise ValueError("Pause/cancel the active Task before suspending or retiring its Worker")
            db.execute("UPDATE v1_agent_instances SET lifecycle=?,updated_at=? WHERE id=?", (state, utcnow(), worker_id))
            if state == 'retired':
                self._fail_pending_messages(db, project_id, worker_id, 'Worker is retired')
        return self.get_worker(project_id, worker_id)

    def for_column(self, run_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM v1_agent_assignments WHERE column_run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def settle_column(self, task, run_id, status, result=None):
        """Commit with the Task transition, before it releases its fencing token."""
        assignment = self.for_column(run_id)
        if not assignment or assignment['status'] in {'completed', 'failed', 'cancelled'}:
            return
        with self.store.tx(immediate=True) as db:
            self.store.assert_execution_owner(task, db=db)
            # Await polling owns a newer Task lease, without executing a Worker turn.
            db.execute('UPDATE v1_agent_assignments SET task_generation=?,lease_owner=? WHERE id=?',
                       (task['state_version'], task.get('lease_owner'), assignment['id']))
            self.finish(assignment, status, result)

    def invalidate_task(self, db, task_id, status='blocked'):
        """Administrative Task transition fences its Worker in the same commit."""
        assert status in {'blocked', 'cancelled', 'failed'}
        rows = db.execute("SELECT * FROM v1_agent_assignments WHERE task_id=? AND status IN ('running','waiting','blocked')", (task_id,)).fetchall()
        for row in rows:
            db.execute('UPDATE v1_agent_assignments SET status=?,generation=generation+1,updated_at=? WHERE id=?', (status, utcnow(), row['id']))
            db.execute('UPDATE v1_agent_instances SET active_assignment_id=NULL,updated_at=? WHERE id=? AND active_assignment_id=?', (utcnow(), row['agent_instance_id'], row['id']))
            if status in {'failed', 'cancelled'}:
                self._fail_pending_messages(db, row['project_id'], row['agent_instance_id'], 'Assignment is '+status, assignment_id=row['id'])
            self.store._event(db, row['project_id'], task_id, row['column_run_id'], 'agent.assignment.'+status, {'assignment_id': row['id'], 'agent_instance_id': row['agent_instance_id']})

    def note_wait(self, assignment, operation_id, ledger):
        from app.v1.execution_ledger import execution_progress
        marker = hashlib.sha256(json.dumps(execution_progress(ledger, excluded_capabilities={'column.await', 'column.complete'})).encode()).hexdigest()
        with self.store.tx(immediate=True) as db:
            self.assert_owner(assignment, db=db)
            if db.execute('SELECT 1 FROM v1_assignment_waits WHERE operation_id=?', (operation_id,)).fetchone():
                return
            row = db.execute('SELECT progress_marker,no_progress_awaits,budget_json FROM v1_agent_assignments WHERE id=?', (assignment['id'],)).fetchone()
            count = row['no_progress_awaits']+1 if marker == row['progress_marker'] else 1
            limit = json.loads(row['budget_json']).get('assignment_no_progress_awaits', 3)
            if count > limit:
                raise ExecutionBudgetExceeded('Assignment repeatedly awaited without execution progress')
            db.execute('INSERT INTO v1_assignment_waits VALUES(?,?,?)', (assignment['id'], operation_id, utcnow()))
            db.execute('UPDATE v1_agent_assignments SET progress_marker=?,no_progress_awaits=? WHERE id=?', (marker, count, assignment['id']))

    def charge(self, assignment, *, models=0, tools=0, resumes=0):
        """Reserve work before dispatch. Limits belong to the logical Assignment."""
        with self.store.tx(immediate=True) as db:
            self.assert_owner(assignment, db=db)
            row = db.execute('SELECT * FROM v1_agent_assignments WHERE id=?', (assignment['id'],)).fetchone()
            budget = json.loads(row['budget_json'])
            if (row['model_calls'] + models > budget['agent_max_iterations']
                    or row['tool_calls'] + tools > budget['agent_max_tool_calls']
                    or row['resumes'] + resumes > budget.get('assignment_max_resumes', 25)):
                raise ExecutionBudgetExceeded('Assignment cumulative model/tool/resume budget exhausted')
            db.execute('UPDATE v1_agent_assignments SET model_calls=model_calls+?,tool_calls=tool_calls+?,resumes=resumes+?,updated_at=? WHERE id=?',
                       (models, tools, resumes, utcnow(), assignment['id']))

    def prepare_operations(self, project_id, scope_id, run_id, sequence, calls):
        with self.store.tx(immediate=True) as db:
            ids = []
            for index, call in enumerate(calls):
                identity = f'op:{run_id}:{sequence}:{index}'
                db.execute('INSERT INTO v1_agent_operations(id,project_id,scope_id,source_run_id,source_sequence,call_index,tool_call_id,capability,arguments_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (identity, project_id, scope_id, run_id, sequence, index, call.id, call.name, json.dumps(call.arguments), utcnow()))
                ids.append(identity)
            return ids

    def pending_operations(self, project_id, scope_id):
        with self.store.connect() as db:
            rows = db.execute("""SELECT o.* FROM v1_agent_operations o WHERE project_id=? AND scope_id=?
                                  AND (delivered=0 OR completion_json IS NOT NULL OR
                                  (wait_json IS NOT NULL AND NOT EXISTS (SELECT 1 FROM v1_await_handles h
                                   WHERE h.run_id=o.scope_id AND json_extract(h.checkpoint_json,'$.operation_id')=o.id)))
                                  ORDER BY o.rowid""", (project_id, scope_id)).fetchall()
        return [dict(row) for row in rows]

    def operation(self, identity):
        with self.store.connect() as db:
            return dict(db.execute('SELECT * FROM v1_agent_operations WHERE id=?', (identity,)).fetchone())

    def answer_operation(self, identity, result, completion, wait):
        with self.store.tx(immediate=True) as db:
            db.execute('UPDATE v1_agent_operations SET result_json=?,completion_json=?,wait_json=? WHERE id=? AND result_json IS NULL',
                       (json.dumps(result), json.dumps(completion) if completion else None, json.dumps(wait) if wait else None, identity))

    def deliver_operation(self, identity):
        with self.store.tx(immediate=True) as db:
            db.execute('UPDATE v1_agent_operations SET delivered=1 WHERE id=? AND result_json IS NOT NULL', (identity,))

    def session_history(self, project_id, session_id, *, before_run_id=None):
        with self.store.connect() as db:
            session = db.execute("SELECT id,legacy_session_id FROM v1_agent_context_sessions WHERE id=? AND project_id=?", (session_id, project_id)).fetchone()
            if not session:
                return []
            rows = db.execute("""SELECT m.* FROM v1_agent_messages m JOIN v1_agent_runs r ON r.id=m.agent_run_id
                               WHERE r.project_id=? AND r.agent_session_id IN (?,?) AND r.id IS NOT ?
                               AND m.role!='system' ORDER BY m.id""", (project_id, session_id, session['legacy_session_id'], before_run_id)).fetchall()
            snapshot = db.execute("SELECT * FROM v1_agent_context_snapshots WHERE session_id=? ORDER BY revision DESC LIMIT 1", (session_id,)).fetchone()
        history = []
        if snapshot:
            history.append({"role": "user", "content": json.dumps({"context_checkpoint": json.loads(snapshot["summary_json"]), "covers_through_message_id": snapshot["through_message_id"]})})
            rows = [row for row in rows if row["id"] > snapshot["through_message_id"]]
        for row in rows:
            item = self.store._decode(dict(row), "tool_calls_json")
            history.append(item)
        checkpoint_prefix = history[:1] if snapshot else []
        budget = self.store.policy.context.worker_context_max_characters - len(json.dumps(checkpoint_prefix)) - 1024
        used, retained = 0, []
        for item in reversed(history[1:] if snapshot else history):
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > budget:
                break
            retained.append(item)
            used += size
        if len(retained) + len(checkpoint_prefix) < len(history):
            first_retained_id = retained[-1].get('id') if retained else None
            with self.store.connect() as db:
                prior_work = [dict(row) for row in db.execute('SELECT id,task_id,status FROM v1_agent_assignments WHERE session_id=? ORDER BY created_at DESC LIMIT 8', (session_id,))]
            checkpoint = {'context_window': {'older_messages_omitted': len(history)-len(retained), 'first_retained_message_id': first_retained_id,
                          'prior_assignments': prior_work,
                          'retrieve': 'Use agent.context.read to inspect older source messages; no source history has been deleted.'}}
            history = checkpoint_prefix + [{'role': 'user', 'content': json.dumps(checkpoint)}] + list(reversed(retained))
        return history

    def save_context_snapshot(self, project_id, worker_id, through_message_id, summary):
        if len(json.dumps(summary)) > self.store.policy.context.worker_context_max_characters // 2:
            raise ValueError('Context summary exceeds half of the Worker context window')
        worker = self.get_worker(project_id, worker_id)
        with self.store.tx(immediate=True) as db:
            if worker["active_assignment_id"]:
                raise AgentBusy("Context compaction requires an idle Worker")
            source = db.execute("SELECT m.id FROM v1_agent_messages m JOIN v1_agent_runs r ON r.id=m.agent_run_id WHERE m.id=? AND r.agent_session_id=? AND r.project_id=?", (through_message_id, worker["active_session_id"], project_id)).fetchone()
            if not source:
                raise ValueError("Snapshot boundary is outside the Worker session")
            previous = db.execute("SELECT COALESCE(MAX(revision),0),COALESCE(MAX(through_message_id),0) FROM v1_agent_context_snapshots WHERE session_id=?", (worker["active_session_id"],)).fetchone()
            if through_message_id < previous[1]:
                raise ValueError("Context coverage cannot move backwards")
            db.execute("INSERT INTO v1_agent_context_snapshots VALUES(?,?,?,?,?,?)", (new_id("ctx"), worker["active_session_id"], previous[0]+1, through_message_id, json.dumps(summary), utcnow()))

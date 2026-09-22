"""Task feedback and verification evidence; Mailbox is not a routing participant."""
import json
from collections import deque

from app.v1.storage_support import new_id, utcnow


class TaskFeedbackRepository:
    def __init__(self, store):
        self.store = store

    def init_schema(self):
        with self.store.tx(immediate=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS v1_task_feedback (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES v1_projects(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL REFERENCES v1_tasks(id) ON DELETE CASCADE,
                dedupe_key TEXT NOT NULL, responsible_column TEXT NOT NULL,
                description TEXT NOT NULL, checks_json TEXT NOT NULL, source_run_id TEXT,
                source_job_id TEXT, reported_sequence INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'open', repair_run_id TEXT,
                verification_json TEXT, created_at TEXT NOT NULL, resolved_at TEXT,
                UNIQUE(task_id,dedupe_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS v1_acceptance_results (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES v1_tasks(id) ON DELETE CASCADE,
                workflow_revision_id TEXT NOT NULL, column_run_id TEXT NOT NULL,
                column_attempt_id TEXT NOT NULL, agent_run_id TEXT, check_key TEXT NOT NULL,
                execution_key TEXT NOT NULL, ok INTEGER NOT NULL, result_json TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(task_id,execution_key))''')

    def list(self, project_id, task_id):
        self.store.get_project_task(project_id, task_id)
        with self.store.connect() as db:
            return [self.store._decode(dict(row), 'checks_json', 'verification_json') for row in db.execute(
                'SELECT * FROM v1_task_feedback WHERE task_id=? ORDER BY rowid', (task_id,))]

    @staticmethod
    def route(workflow, source, target):
        """Return a declared next edge, including a real cycle for same-column rework."""
        queue = deque((edge.target, edge.target) for edge in workflow.column(source).transitions
                      if not workflow.terminal_kind(edge.target))
        seen = set()
        while queue:
            node, first = queue.popleft()
            if node == target:
                return first
            if node in seen:
                continue
            seen.add(node)
            queue.extend((edge.target, first) for edge in workflow.column(node).transitions
                         if not workflow.terminal_kind(edge.target))
        raise ValueError(f'No declared feedback route from {source} to {target}; revise the workflow in an explicit successor')

    def record(self, ctx, args):
        task_id = args['task_id']
        if ctx.column_run_id and task_id != ctx.task_id:
            raise ValueError('Column feedback is scoped to its own Task')
        with self.store.tx(immediate=True) as db:
            task = self.store.get_project_task(ctx.project_id, task_id)
            if ctx.column_run_id:
                self.store.assert_execution_owner(self.store.get_task(task_id), db=db)
                run = db.execute('SELECT task_id,status FROM v1_column_runs WHERE id=?', (ctx.column_run_id,)).fetchone()
                if not run or run['task_id'] != task_id or run['status'] != 'running':
                    raise ValueError('Feedback source Column is no longer active')
            if task['status'] in {'done','failed'}:
                raise ValueError('Terminal Task is immutable; create task.successor then record feedback on that Task')
            workflow = self.store.workflow_by_id(ctx.project_id, task['workflow_revision_id'])
            responsible = args['responsible_column']
            workflow.column(responsible)
            if task['current_column'] != responsible or task['status'] != 'pending':
                self.route(workflow, task['current_column'], responsible)
            checks = args['checks']
            if not checks or len({(c['column'],c['check_key']) for c in checks}) != len(checks):
                raise ValueError('Feedback requires nonempty unique frozen verification checks')
            for check in checks:
                column = workflow.column(check['column'])
                if not any(c.key == check['check_key'] for c in column.acceptance_checks):
                    raise ValueError('Feedback check must reference a frozen acceptance check')
                if check['column'] != responsible:
                    self.route(workflow, responsible, check['column'])
            encoded = json.dumps(checks, sort_keys=True)
            prior = db.execute('SELECT * FROM v1_task_feedback WHERE task_id=? AND dedupe_key=?',
                               (task_id,args['dedupe_key'])).fetchone()
            if prior:
                if (prior['responsible_column'], prior['description'], prior['checks_json']) != (responsible,args['description'],encoded):
                    raise ValueError('Feedback dedupe_key refers to different content')
                return self.store._decode(dict(prior), 'checks_json', 'verification_json')
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM v1_column_runs WHERE task_id=? AND status!='pending'", (task_id,)).fetchone()[0]
            job = self.store.intents.job_for_run(ctx.agent_run_id, ctx.project_id)
            identity = new_id('feedback')
            db.execute('''INSERT INTO v1_task_feedback
                (id,project_id,task_id,dedupe_key,responsible_column,description,checks_json,
                 source_run_id,source_job_id,reported_sequence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (identity,ctx.project_id,task_id,args['dedupe_key'],responsible,args['description'],encoded,
                 ctx.column_run_id,job['id'] if job else None,sequence,utcnow()))
            self.store._event(db,ctx.project_id,task_id,ctx.column_run_id,'task.feedback_recorded',{'feedback_id':identity})
        return next(item for item in self.list(ctx.project_id, task_id) if item['id'] == identity)

    def inherit(self, db, predecessor_id, task_id, workflow):
        """Carry unresolved obligations forward without changing the predecessor."""
        rows = db.execute("SELECT * FROM v1_task_feedback WHERE task_id=? AND state!='resolved' ORDER BY rowid", (predecessor_id,)).fetchall()
        for row in rows:
            workflow.column(row['responsible_column'])
            for check in json.loads(row['checks_json']):
                if not any(c.key == check['check_key'] for c in workflow.column(check['column']).acceptance_checks):
                    raise ValueError('A successor cannot discard unresolved feedback verification checks')
            db.execute('''INSERT INTO v1_task_feedback
                (id,project_id,task_id,dedupe_key,responsible_column,description,checks_json,
                 source_run_id,source_job_id,reported_sequence,created_at) VALUES(?,?,?,?,?,?,?,?,?,0,?)''',
                (new_id('feedback'),row['project_id'],task_id,'inherited:'+row['id'],row['responsible_column'],
                 row['description'],row['checks_json'],row['source_run_id'],row['source_job_id'],utcnow()))

    def record_check(self, spec, key, execution_key, result, *, task_owner=None, agent_run_id=None):
        with self.store.tx(immediate=True) as db:
            if spec.assignment:
                self.store.agents.assert_owner(spec.assignment, db=db)
            else:
                if task_owner is None:
                    raise ValueError('Acceptance evidence requires an owned Column execution')
                self.store.assert_execution_owner(task_owner, db=db)
            task = self.store.get_project_task(spec.project['id'], spec.task_id)
            db.execute('''INSERT INTO v1_acceptance_results VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (new_id('verification'),spec.task_id,task['workflow_revision_id'],spec.column_run_id,
                 spec.column_attempt_id,agent_run_id,key,execution_key,int(result.ok),
                 result.model_dump_json(),utcnow()))

    def verified(self, db, task, column, key, *, after_sequence=0, current_run=None):
        # The latest visit invalidates all receipts from previous visits, even if
        # the old run succeeded. A pending/failed latest visit is not evidence.
        latest = db.execute('SELECT id,sequence,status FROM v1_column_runs WHERE task_id=? AND column_key=? ORDER BY sequence DESC LIMIT 1',
                            (task['id'],column)).fetchone()
        if not latest or latest['sequence'] < after_sequence or (latest['status'] != 'succeeded' and latest['id'] != current_run):
            return None
        row = db.execute('''SELECT * FROM v1_acceptance_results WHERE task_id=? AND workflow_revision_id=?
            AND column_run_id=? AND check_key=? ORDER BY rowid DESC LIMIT 1''',
            (task['id'],task['workflow_revision_id'],latest['id'],key)).fetchone()
        if not row or not row['ok']:
            return None
        attempt = db.execute('SELECT id FROM v1_column_attempts WHERE column_run_id=? ORDER BY attempt_no DESC LIMIT 1', (latest['id'],)).fetchone()
        return row['id'] if attempt and attempt['id'] == row['column_attempt_id'] else None

    def settle(self, db, task, run_id, outcome, next_column, terminal):
        workflow = self.store.workflow_by_id(task['project_id'], task['workflow_revision_id'])
        if terminal == 'failed':
            return next_column, terminal
        feedback = db.execute("SELECT * FROM v1_task_feedback WHERE task_id=? ORDER BY rowid", (task['id'],)).fetchall()
        if not feedback and not workflow.acceptance_obligations:
            return next_column, terminal
        run = db.execute('SELECT sequence FROM v1_column_runs WHERE id=?', (run_id,)).fetchone()
        transition = next(t for t in workflow.column(task['current_column']).transitions if t.outcome == outcome)
        for item in feedback:
            repair_run = item['repair_run_id']
            if item['responsible_column'] == task['current_column'] and run['sequence'] > item['reported_sequence'] and not transition.allows_unresolved_failures:
                repair_run = run_id
                db.execute("UPDATE v1_task_feedback SET state='verifying',repair_run_id=? WHERE id=?", (run_id,item['id']))
            if repair_run:
                repair_sequence = db.execute('SELECT sequence FROM v1_column_runs WHERE id=?', (repair_run,)).fetchone()[0]
                verified = [self.verified(db,task,c['column'],c['check_key'],after_sequence=repair_sequence,current_run=run_id)
                            for c in json.loads(item['checks_json'])]
                if all(verified):
                    db.execute("UPDATE v1_task_feedback SET state='resolved',verification_json=?,resolved_at=? WHERE id=?", (json.dumps(verified),utcnow(),item['id']))
                else:
                    db.execute("UPDATE v1_task_feedback SET state='verifying',verification_json=NULL,resolved_at=NULL WHERE id=?", (item['id'],))
        remaining = db.execute("SELECT * FROM v1_task_feedback WHERE task_id=? AND state!='resolved' ORDER BY rowid", (task['id'],)).fetchall()
        if remaining:
            item = remaining[0]
            target = item['responsible_column']
            if item['state'] == 'verifying':
                repair_sequence = db.execute('SELECT sequence FROM v1_column_runs WHERE id=?', (item['repair_run_id'],)).fetchone()[0]
                target = next(c['column'] for c in json.loads(item['checks_json']) if not self.verified(
                    db,task,c['column'],c['check_key'],after_sequence=repair_sequence,current_run=run_id))
            routed = self.route(workflow, task['current_column'], target)
            self.store._event(db,task['project_id'],task['id'],run_id,'task.feedback_routed',{'feedback_id':item['id'],'next':routed})
            return routed, None
        if terminal == 'done':
            for obligation in workflow.acceptance_obligations:
                barrier = 0
                for key in obligation.invalidated_by:
                    latest = db.execute('SELECT sequence FROM v1_column_runs WHERE task_id=? AND column_key=? ORDER BY sequence DESC LIMIT 1', (task['id'],key)).fetchone()
                    barrier = max(barrier,latest[0] if latest else 0)
                if not self.verified(db,task,obligation.column,obligation.check_key,after_sequence=barrier,current_run=run_id):
                    raise ValueError(f'Required acceptance evidence missing: {obligation.column}/{obligation.check_key}')
        return next_column, terminal

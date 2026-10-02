"""Task feedback and verification evidence; Mailbox is not a routing participant."""
import json
import hashlib
from collections import deque

from app.v1.storage_support import new_id, utcnow

RUNTIME_BEHAVIOR_CHECK = 'runtime.behavior'


def _behavior_affecting_write(arguments):
    path = str(arguments.get('path') or '').replace('\\', '/').rsplit('/', 1)[-1].lower()
    return not (path.endswith('.md') and 'report' in path)


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
            self.store.schema_repository._ensure_column(db,'v1_task_feedback','observed_check_rowid','INTEGER NOT NULL DEFAULT 0')

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
            behavior_columns = self._behavior_columns(db, task['workflow_revision_id'])
            responsible = args['responsible_column']
            workflow.column(responsible)
            if task['current_column'] != responsible:
                self.route(workflow, task['current_column'], responsible)
            checks = args['checks']
            if not checks or len({(c['column'],c['check_key']) for c in checks}) != len(checks):
                raise ValueError('Feedback requires nonempty unique frozen verification checks')
            for check in checks:
                column = workflow.column(check['column'])
                runtime_check = (check['check_key'] == RUNTIME_BEHAVIOR_CHECK
                                 and column.key in behavior_columns
                                 and not any(c.evidence_kind == 'behavior' for c in column.acceptance_checks))
                if not runtime_check and not any(c.key == check['check_key'] for c in column.acceptance_checks):
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
            db.execute('UPDATE v1_task_feedback SET observed_check_rowid=(SELECT COALESCE(MAX(rowid),0) FROM v1_acceptance_results) WHERE id=?',(identity,))
        return next(item for item in self.list(ctx.project_id, task_id) if item['id'] == identity)

    def inherit(self, db, predecessor_id, task_id, workflow):
        """Carry unresolved obligations forward without changing the predecessor."""
        rows = db.execute("SELECT * FROM v1_task_feedback WHERE task_id=? AND state!='resolved' ORDER BY rowid", (predecessor_id,)).fetchall()
        for row in rows:
            workflow.column(row['responsible_column'])
            revision = db.execute('SELECT workflow_revision_id FROM v1_tasks WHERE id=?', (task_id,)).fetchone()
            behavior_columns = self._behavior_columns(db, revision[0])
            for check in json.loads(row['checks_json']):
                column = workflow.column(check['column'])
                runtime_check = (check['check_key'] == RUNTIME_BEHAVIOR_CHECK
                                 and column.key in behavior_columns
                                 and not any(c.evidence_kind == 'behavior' for c in column.acceptance_checks))
                if not runtime_check and not any(c.key == check['check_key'] for c in column.acceptance_checks):
                    raise ValueError('A successor cannot discard unresolved feedback verification checks')
            db.execute('''INSERT INTO v1_task_feedback
                (id,project_id,task_id,dedupe_key,responsible_column,description,checks_json,
                 source_run_id,source_job_id,reported_sequence,created_at) VALUES(?,?,?,?,?,?,?,?,?,0,?)''',
                (new_id('feedback'),row['project_id'],task_id,'inherited:'+row['id'],row['responsible_column'],
                 row['description'],row['checks_json'],row['source_run_id'],row['source_job_id'],utcnow()))

    @staticmethod
    def _behavior_columns(db, revision_id):
        row = db.execute('''SELECT p.plan_json FROM v1_workflow_revisions r
            JOIN v1_workflow_plans p ON p.id=r.workflow_plan_id WHERE r.id=?''', (revision_id,)).fetchone()
        return set((json.loads(row[0]).get('task_contract') or {}).get('required_behavior_columns') or []) if row else set()

    @staticmethod
    def _latest_impact_run(db, task_id, column, current_run=None):
        runs = db.execute('''SELECT id,sequence,status FROM v1_column_runs
            WHERE task_id=? AND column_key=? ORDER BY sequence DESC''', (task_id, column)).fetchall()
        for run in runs:
            if run['status'] == 'succeeded' or run['id'] == current_run:
                return run
            effects = db.execute('''SELECT i.capability,i.arguments_json FROM v1_tool_invocations i
                JOIN v1_agent_runs r ON r.id=i.agent_run_id WHERE r.column_run_id=?
                AND i.ok=1 AND i.capability IN ('project.files.write','project.command.run')''',
                (run['id'],)).fetchall()
            if any(effect['capability'] == 'project.command.run' or _behavior_affecting_write(
                json.loads(effect['arguments_json'])) for effect in effects):
                return run
        return None

    def record_column_evidence(self, db, task, run_id, attempt_id):
        """Bind a successful Worker-selected behavior command to this Column visit."""
        run = db.execute('SELECT task_id,column_key FROM v1_column_runs WHERE id=?', (run_id,)).fetchone()
        if not run or run['task_id'] != task['id'] or run['column_key'] not in self._behavior_columns(db, task['workflow_revision_id']):
            return
        workflow = self.store.workflow_by_id(task['project_id'], task['workflow_revision_id'])
        if any(check.evidence_kind == 'behavior'
               for check in workflow.column(run['column_key']).acceptance_checks):
            return  # Fixed checks retain their original verification receipts.
        from app.v1.services.task_plan_compiler import known_nonbehavior_command
        writes = db.execute('''SELECT i.rowid AS invocation_rowid,i.arguments_json FROM v1_tool_invocations i
            JOIN v1_agent_runs r ON r.id=i.agent_run_id
            WHERE r.column_run_id=? AND i.capability='project.files.write' AND i.ok=1
            ORDER BY i.rowid DESC''', (run_id,)).fetchall()
        last_write = next((row['invocation_rowid'] for row in writes if _behavior_affecting_write(
            json.loads(row['arguments_json']))), 0)
        rows = db.execute('''SELECT i.agent_run_id,i.tool_call_id,i.arguments_json FROM v1_tool_invocations i
            JOIN v1_agent_runs r ON r.id=i.agent_run_id
            WHERE r.column_run_id=? AND i.capability='project.command.run' AND i.ok=1
              AND i.rowid>?
            ORDER BY i.rowid DESC''', (run_id, last_write)).fetchall()
        executed = next((row for row in rows if not known_nonbehavior_command(
            json.loads(row['arguments_json']).get('argv', []))), None)
        if not executed:
            return
        execution_key = f'{run_id}:{RUNTIME_BEHAVIOR_CHECK}'
        result = {'ok': True, 'capability': 'project.command.run',
                  'output': {'tool_call_id': executed['tool_call_id'], 'agent_run_id': executed['agent_run_id']}}
        db.execute('''INSERT OR IGNORE INTO v1_acceptance_results VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                   (new_id('verification'),task['id'],task['workflow_revision_id'],run_id,
                    attempt_id,executed['agent_run_id'],RUNTIME_BEHAVIOR_CHECK,execution_key,1,
                    json.dumps(result,ensure_ascii=False),utcnow()))

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

    def latest_failed_checks(self, task_id, column_run_id):
        """Return bounded, still-failing formal checks for this Column visit."""
        with self.store.connect() as db:
            rows = db.execute('''SELECT result.id,result.check_key,result.result_json
                FROM v1_acceptance_results AS result
                JOIN (SELECT check_key,MAX(rowid) AS latest_rowid
                    FROM v1_acceptance_results
                    WHERE task_id=? AND column_run_id=? AND workflow_revision_id=(
                        SELECT workflow_revision_id FROM v1_tasks WHERE id=?)
                    GROUP BY check_key) AS latest ON result.rowid=latest.latest_rowid
                WHERE result.ok=0 ORDER BY result.rowid DESC LIMIT 8''',
                (task_id,column_run_id,task_id)).fetchall()
        failures = []
        for row in rows:
            error = (json.loads(row['result_json']).get('error') or {})
            failures.append({'check_key':row['check_key'], 'acceptance_result_id':row['id'],
                'error_type':str(error.get('type') or 'CheckFailed')[:80],
                'error_message':str(error.get('message') or 'Formal acceptance check failed')[:300]})
        return failures

    def failure_detail(self, project_id, task_id):
        """Expose a bounded delivery failure, not a command log or model summary."""
        task = self.store.get_project_task(project_id, task_id)
        if task['status'] not in {'failed', 'recovering'}:
            return None
        with self.store.connect() as db:
            row = db.execute('''SELECT id,column_key,status,output_json,error FROM v1_column_runs
                WHERE project_id=? AND task_id=? ORDER BY sequence DESC LIMIT 1''',
                (project_id,task_id)).fetchone()
        output = json.loads(row['output_json'] or '{}') if row else {}
        if not isinstance(output, dict):
            output = {}
        reason_keys = ('failure_reason','environment_limitation') + (
            ('reason','error','summary') if row and row['status'] == 'failed' else ())
        reported = next((value for key in reason_keys
                         if isinstance(value := output.get(key), str) and value.strip()), None)
        runtime_error = task.get('error') or (row['error'] if row else None) or ''
        agent_failure = self.latest_agent_failure(task_id, row['id']) if row else None
        return {'column_key':row['column_key'] if row else task.get('current_column'),
                'column_run_id':row['id'] if row else None,
                'reported_reason':reported[:400] if reported else None,
                'runtime_error':str(runtime_error)[:300],
                'completion_rejection':agent_failure.get('completion_rejection') if agent_failure else None}

    def latest_agent_failure(self, task_id, column_run_id, *, exclude_agent_run_id=None):
        """Surface the last terminal attempt's bounded rejection on this Column visit."""
        with self.store.connect() as db:
            run = db.execute('''SELECT id,status,error FROM v1_agent_runs
                WHERE task_id=? AND column_run_id=? AND id!=?
                  AND status IN ('failed','succeeded','completed')
                ORDER BY rowid DESC LIMIT 1''',
                (task_id,column_run_id,exclude_agent_run_id or '')).fetchone()
            if not run or run['status'] != 'failed':
                return None
            rejection = db.execute('''SELECT result_json FROM v1_tool_invocations
                WHERE agent_run_id=? AND capability='column.complete' AND ok=0
                ORDER BY sequence DESC LIMIT 1''',(run['id'],)).fetchone()
        error = {}
        if rejection:
            try:
                error = (json.loads(rejection['result_json']).get('error') or {})
            except (TypeError, ValueError):
                pass
        message = str(error.get('message') or '').strip() if isinstance(error, dict) else ''
        return {'agent_run_id':run['id'],
                'runtime_error':str(run['error'] or 'Agent Run failed')[:300],
                'completion_rejection':message[:600] if message else None}

    def pending_for_column(self, task_id, column_key):
        """Current bounded repair obligations for one Worker, from durable feedback."""
        with self.store.connect() as db:
            rows = db.execute('''SELECT id,state,responsible_column,description,checks_json
                FROM v1_task_feedback AS feedback WHERE task_id=? AND state!='resolved'
                AND (responsible_column=? OR EXISTS (
                    SELECT 1 FROM json_each(feedback.checks_json) AS check_item
                    WHERE json_extract(check_item.value,'$.column')=?))
                ORDER BY CASE WHEN responsible_column=? THEN 0 ELSE 1 END,
                         feedback.rowid DESC LIMIT 8''',
                (task_id,column_key,column_key,column_key)).fetchall()
        return [{'feedback_id':row['id'], 'state':row['state'],
                 'responsible_column':row['responsible_column'],
                 'description':row['description'][:500],
                 'verification_checks':[
                     {'column':check.get('column'), 'check_key':check.get('check_key')}
                     for check in json.loads(row['checks_json'])[:8]]}
                for row in rows]

    def verified(self, db, task, column, key, *, after_sequence=0, current_run=None, after_check_rowid=0):
        # A later visit without a successful effect does not invalidate an
        # earlier verified result. A visit that changed files still does.
        latest = self._latest_impact_run(db, task['id'], column, current_run)
        if not latest or latest['sequence'] < after_sequence or (latest['status'] != 'succeeded' and latest['id'] != current_run):
            return None
        row = db.execute('''SELECT rowid AS check_rowid,* FROM v1_acceptance_results WHERE task_id=? AND workflow_revision_id=?
            AND column_run_id=? AND check_key=? ORDER BY rowid DESC LIMIT 1''',
            (task['id'],task['workflow_revision_id'],latest['id'],key)).fetchone()
        if not row or not row['ok'] or row['check_rowid'] <= after_check_rowid:
            return None
        attempt = db.execute('SELECT id FROM v1_column_attempts WHERE column_run_id=? ORDER BY attempt_no DESC LIMIT 1', (latest['id'],)).fetchone()
        if not attempt or attempt['id'] != row['column_attempt_id']:
            return None
        from app.v1.files import ProjectFiles
        evidence = (json.loads(row['result_json']).get('output') or {}).get('scenario_evidence') or {}
        if evidence:
            files = ProjectFiles(self.store.get_project(task['project_id'])['base_dir'], self.store.policy)
            for scenario in evidence['scenarios']:
                source = scenario['test_source']
                try:
                    if hashlib.sha256(files.resolve(source['path']).read_bytes()).hexdigest() != source['sha256']:
                        return None
                except (OSError, ValueError):
                    return None
        return row['id']

    def settle(self, db, task, run_id, outcome, next_column, terminal):
        workflow = self.store.workflow_by_id(task['project_id'], task['workflow_revision_id'])
        if terminal == 'failed':
            return next_column, terminal
        feedback = db.execute("SELECT * FROM v1_task_feedback WHERE task_id=? ORDER BY rowid", (task['id'],)).fetchall()
        behavior_columns = self._behavior_columns(db, task['workflow_revision_id'])
        if not feedback and not workflow.acceptance_obligations and not behavior_columns:
            return next_column, terminal
        run = db.execute('SELECT sequence FROM v1_column_runs WHERE id=?', (run_id,)).fetchone()
        transition = next(t for t in workflow.column(task['current_column']).transitions if t.outcome == outcome)
        for item in feedback:
            repair_run = item['repair_run_id']
            if item['responsible_column'] == task['current_column'] and (
                run['sequence'] > item['reported_sequence'] or item['source_run_id'] == run_id
            ) and not transition.allows_unresolved_failures:
                repair_run = run_id
                db.execute("UPDATE v1_task_feedback SET state='verifying',repair_run_id=? WHERE id=?", (run_id,item['id']))
            if repair_run:
                repair_sequence = db.execute('SELECT sequence FROM v1_column_runs WHERE id=?', (repair_run,)).fetchone()[0]
                verified = [self.verified(db,task,c['column'],c['check_key'],after_sequence=repair_sequence,current_run=run_id,
                                         after_check_rowid=item['observed_check_rowid'])
                            for c in json.loads(item['checks_json'])]
                if all(verified):
                    db.execute("UPDATE v1_task_feedback SET state='resolved',verification_json=?,resolved_at=? WHERE id=?", (json.dumps(verified),utcnow(),item['id']))
                else:
                    db.execute("UPDATE v1_task_feedback SET state='verifying',verification_json=NULL,resolved_at=NULL WHERE id=?", (item['id'],))
        # An explicit rework outcome is the Column's frozen control decision.
        # Older unrelated feedback must not turn a failed test into a success edge.
        if transition.allows_unresolved_failures:
            return next_column, terminal
        remaining = db.execute("SELECT * FROM v1_task_feedback WHERE task_id=? AND state!='resolved' ORDER BY rowid", (task['id'],)).fetchall()
        if remaining:
            item = remaining[0]
            target = item['responsible_column']
            if item['state'] == 'verifying':
                repair_sequence = db.execute('SELECT sequence FROM v1_column_runs WHERE id=?', (item['repair_run_id'],)).fetchone()[0]
                target = next(c['column'] for c in json.loads(item['checks_json']) if not self.verified(
                    db,task,c['column'],c['check_key'],after_sequence=repair_sequence,current_run=run_id,
                    after_check_rowid=item['observed_check_rowid']))
            routed = self.route(workflow, task['current_column'], target)
            self.store._event(db,task['project_id'],task['id'],run_id,'task.feedback_routed',{'feedback_id':item['id'],'next':routed})
            return routed, None
        if terminal == 'done':
            # Older interrupted Attempts can contain the passing command while
            # the final Attempt only submits completion. Rebuild a missing
            # visit receipt before enforcing terminal behavior obligations.
            for key in behavior_columns:
                latest = self._latest_impact_run(db, task['id'], key)
                if latest and latest['status'] == 'succeeded':
                    attempt = db.execute('''SELECT id FROM v1_column_attempts
                        WHERE column_run_id=? ORDER BY attempt_no DESC LIMIT 1''',
                        (latest['id'],)).fetchone()
                    if attempt:
                        self.record_column_evidence(db, task, latest['id'], attempt['id'])
            for obligation in workflow.acceptance_obligations:
                barrier = 0
                for key in obligation.invalidated_by:
                    latest = self._latest_impact_run(db, task['id'], key, run_id)
                    barrier = max(barrier,latest['sequence'] if latest else 0)
                if not self.verified(db,task,obligation.column,obligation.check_key,after_sequence=barrier,current_run=run_id):
                    raise ValueError(f'Required acceptance evidence missing: {obligation.column}/{obligation.check_key}')
            invalidated_by = self._invalidators(db, task['workflow_revision_id'])
            for key in behavior_columns:
                if any(check.evidence_kind == 'behavior' for check in workflow.column(key).acceptance_checks):
                    continue  # Frozen checks keep their original verification path.
                invalidators = {key} if key in invalidated_by else invalidated_by
                barrier = 0
                for changed in invalidators:
                    latest = self._latest_impact_run(db, task['id'], changed, run_id)
                    barrier = max(barrier,latest['sequence'] if latest else 0)
                if not self.verified(db,task,key,RUNTIME_BEHAVIOR_CHECK,after_sequence=barrier,current_run=run_id):
                    raise ValueError(f'Required behavior evidence missing: {key}')
        return next_column, terminal

    @staticmethod
    def _invalidators(db, revision_id):
        row = db.execute('''SELECT p.plan_json FROM v1_workflow_revisions r
            JOIN v1_workflow_plans p ON p.id=r.workflow_plan_id WHERE r.id=?''', (revision_id,)).fetchone()
        return set((json.loads(row[0]).get('task_contract') or {}).get('acceptance_invalidated_by') or []) if row else set()

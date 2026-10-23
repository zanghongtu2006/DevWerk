"""Task feedback and verification evidence; Mailbox is not a routing participant."""
import json
from app.v1.protocols.conversation.feedback_context_contracts import (AgentFailureFact, FailedAcceptanceCheck,
    PendingFeedback, TaskFailureDetail, TaskFeedbackRecord)
from app.v1.protocols.tools.planning.capability_task_contracts import FeedbackCheckReference

from app.v1.storage_support import new_id, utcnow
from app.v1.execution.states import FEEDBACK_STATE_MACHINE, FeedbackStatus
from app.v1.protocols.workflow.runtime_json_contracts import FeedbackVerificationPayload

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
            return [TaskFeedbackRecord.model_validate(
                self.store._decode(dict(row), 'checks_json', 'verification_json')).model_dump(mode='json')
                for row in db.execute('SELECT * FROM v1_task_feedback WHERE task_id=? ORDER BY rowid', (task_id,))]

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
            checks = args.get('checks') or []
            if len({(c['column'],c['check_key']) for c in checks}) != len(checks):
                raise ValueError('Feedback requires unique frozen verification checks')
            for check in checks:
                column = workflow.column(check['column'])
                if not any(c.key == check['check_key'] for c in column.acceptance_checks):
                    raise ValueError('Feedback check must reference a frozen acceptance check')
            encoded = json.dumps(checks, sort_keys=True)
            prior = db.execute('SELECT * FROM v1_task_feedback WHERE task_id=? AND dedupe_key=?',
                               (task_id,args['dedupe_key'])).fetchone()
            if prior:
                if (prior['responsible_column'], prior['description'], prior['checks_json']) != (responsible,args['description'],encoded):
                    raise ValueError('Feedback dedupe_key refers to different content')
                return TaskFeedbackRecord.model_validate(
                    self.store._decode(dict(prior), 'checks_json', 'verification_json')).model_dump(mode='json')
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM v1_column_runs WHERE task_id=? AND status!='pending'", (task_id,)).fetchone()[0]
            job = self.store.intents.job_for_run(ctx.agent_run_id, ctx.project_id)
            identity = new_id('feedback')
            db.execute('''INSERT INTO v1_task_feedback
                (id,project_id,task_id,dedupe_key,responsible_column,description,checks_json,
                 source_run_id,source_job_id,reported_sequence,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (identity,ctx.project_id,task_id,args['dedupe_key'],responsible,args['description'],encoded,
                 ctx.column_run_id,job['id'] if job else None,sequence,utcnow()))
            self.store._event(db,ctx.project_id,task_id,ctx.column_run_id,'task.feedback_recorded',{'feedback_id':identity})
            self.store._mailbox(db,ctx.project_id,'task.feedback_recorded',task_id,ctx.column_run_id,
                                {'feedback_id':identity})
            db.execute('UPDATE v1_task_feedback SET observed_check_rowid=(SELECT COALESCE(MAX(rowid),0) FROM v1_acceptance_results) WHERE id=?',(identity,))
        return next(item for item in self.list(ctx.project_id, task_id) if item['id'] == identity)

    def resolve(self, ctx, args):
        """A later review Column explicitly verifies feedback without fixed checks."""
        if not ctx.column_run_id or not ctx.task_id:
            raise ValueError('Manual feedback resolution requires an active Column')
        task_id = args['task_id']
        if task_id != ctx.task_id:
            raise ValueError('Column feedback is scoped to its own Task')
        with self.store.tx(immediate=True) as db:
            self.store.assert_execution_owner(self.store.get_task(task_id), db=db)
            current = db.execute('SELECT column_key,sequence,status FROM v1_column_runs WHERE id=? AND task_id=?',
                                 (ctx.column_run_id,task_id)).fetchone()
            if not current or current['status'] != 'running':
                raise ValueError('Feedback reviewer Column is no longer active')
            row = db.execute('SELECT * FROM v1_task_feedback WHERE id=? AND project_id=? AND task_id=?',
                             (args['feedback_id'],ctx.project_id,task_id)).fetchone()
            if not row or row['state'] != FeedbackStatus.VERIFYING.value or json.loads(row['checks_json']):
                raise ValueError('Only manual feedback awaiting review can be resolved')
            repair = db.execute('SELECT sequence FROM v1_column_runs WHERE id=? AND task_id=? AND status=?',
                                (row['repair_run_id'],task_id,'succeeded')).fetchone()
            if not repair or current['sequence'] <= repair['sequence'] or current['column_key'] == row['responsible_column']:
                raise ValueError('A later independent Column must review the completed repair')
            FEEDBACK_STATE_MACHINE.require(FeedbackStatus.VERIFYING, FeedbackStatus.RESOLVED)
            db.execute('UPDATE v1_task_feedback SET state=?,verification_json=?,resolved_at=? WHERE id=?',
                       (FeedbackStatus.RESOLVED.value,FeedbackVerificationPayload(
                           review_run_id=ctx.column_run_id, note=args['verification_note']
                       ).model_dump_json(),
                        utcnow(),row['id']))
            self.store._event(db,ctx.project_id,task_id,ctx.column_run_id,'task.feedback_resolved',
                              {'feedback_id':row['id']})
        return next(item for item in self.list(ctx.project_id,task_id) if item['id'] == args['feedback_id'])

    def inherit(self, db, predecessor_id, task_id, workflow):
        """Carry unresolved obligations forward without changing the predecessor."""
        rows = db.execute("SELECT * FROM v1_task_feedback WHERE task_id=? AND state!=? ORDER BY rowid",
                          (predecessor_id, FeedbackStatus.RESOLVED.value)).fetchall()
        for row in rows:
            workflow.column(row['responsible_column'])
            for check in json.loads(row['checks_json']):
                column = workflow.column(check['column'])
                if not any(c.key == check['check_key'] for c in column.acceptance_checks):
                    raise ValueError('A successor cannot discard unresolved feedback verification checks')
            db.execute('''INSERT INTO v1_task_feedback
                (id,project_id,task_id,dedupe_key,responsible_column,description,checks_json,
                 source_run_id,source_job_id,reported_sequence,created_at) VALUES(?,?,?,?,?,?,?,?,?,0,?)''',
                (new_id('feedback'),row['project_id'],task_id,'inherited:'+row['id'],row['responsible_column'],
                 row['description'],row['checks_json'],row['source_run_id'],row['source_job_id'],utcnow()))

    def _latest_impact_run(self, db, task_id, column, current_run=None):
        runs = db.execute('''SELECT id,sequence,status FROM v1_column_runs
            WHERE task_id=? AND column_key=? ORDER BY sequence DESC''', (task_id, column)).fetchall()
        for run in runs:
            if run['status'] == 'succeeded' or run['id'] == current_run:
                return run
            effects = db.execute('''SELECT i.capability FROM v1_tool_invocations i
                JOIN v1_agent_runs r ON r.id=i.agent_run_id WHERE r.column_run_id=?
                AND i.ok=1''',
                (run['id'],)).fetchall()
            if any(not self.store.registry.contains(item['capability']) or
                   self.store.registry.side_effect_kind(item['capability']) in {'write', 'process'}
                   for item in effects):
                return run
        return None

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
            failures.append(FailedAcceptanceCheck(check_key=row['check_key'],
                acceptance_result_id=row['id'],
                error_type=str(error.get('type') or 'CheckFailed')[:80],
                error_message=str(error.get('message') or 'Formal acceptance check failed')[:300]
                ).model_dump(mode='json'))
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
        return TaskFailureDetail(column_key=row['column_key'] if row else task.get('current_column'),
            column_run_id=row['id'] if row else None,
            reported_reason=reported[:400] if reported else None,
            runtime_error=str(runtime_error)[:300],
            completion_rejection=agent_failure.get('completion_rejection') if agent_failure else None
            ).model_dump(mode='json')

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
        return AgentFailureFact(agent_run_id=run['id'],
            runtime_error=str(run['error'] or 'Agent Run failed')[:300],
            completion_rejection=message[:600] if message else None).model_dump(mode='json')

    def pending_for_column(self, task_id, column_key):
        """Current bounded repair obligations for one Worker, from durable feedback."""
        with self.store.connect() as db:
            rows = db.execute('''SELECT id,state,responsible_column,description,checks_json
                FROM v1_task_feedback AS feedback WHERE task_id=? AND state!=?
                AND (responsible_column=? OR EXISTS (
                    SELECT 1 FROM json_each(feedback.checks_json) AS check_item
                    WHERE json_extract(check_item.value,'$.column')=?))
                ORDER BY CASE WHEN responsible_column=? THEN 0 ELSE 1 END,
                         feedback.rowid DESC LIMIT 8''',
                (task_id,FeedbackStatus.RESOLVED.value,column_key,column_key,column_key)).fetchall()
        return [PendingFeedback(feedback_id=row['id'], state=row['state'],
            responsible_column=row['responsible_column'], description=row['description'][:500],
            verification_mode='fixed_checks' if json.loads(row['checks_json']) else 'manual_review',
            verification_checks=[FeedbackCheckReference.model_validate(check)
                for check in json.loads(row['checks_json'])[:8]]).model_dump(mode='json')
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
        return row['id']

    def verify_transition_checks(self, db, task, run_id, transition):
        """Check only the evidence named by the selected Workflow edge."""
        for requirement in transition.required_checks:
            barrier = 0
            for key in requirement.invalidated_by:
                latest = self._latest_impact_run(db, task['id'], key, run_id)
                barrier = max(barrier, latest['sequence'] if latest else 0)
            if not self.verified(db, task, requirement.column, requirement.check_key,
                                 after_sequence=barrier, current_run=run_id):
                raise ValueError(
                    f"Workflow edge check missing: {requirement.column}/{requirement.check_key}"
                )

    def apply_completion_evidence(self, db, task, run_id, outcome, terminal):
        workflow = self.store.workflow_by_id(task['project_id'], task['workflow_revision_id'])
        if terminal == 'failed':
            return
        feedback = db.execute("SELECT * FROM v1_task_feedback WHERE task_id=? AND state!=? ORDER BY rowid",
                              (task['id'], FeedbackStatus.RESOLVED.value)).fetchall()
        if not feedback:
            return
        run = db.execute('SELECT sequence FROM v1_column_runs WHERE id=?', (run_id,)).fetchone()
        transition = next(t for t in workflow.column(task['current_column']).transitions if t.outcome == outcome)
        for item in feedback:
            state = FEEDBACK_STATE_MACHINE.parse(item['state'])
            repair_run = item['repair_run_id']
            if state == FeedbackStatus.OPEN and item['responsible_column'] == task['current_column'] and (
                run['sequence'] > item['reported_sequence'] or item['source_run_id'] == run_id
            ) and not transition.allows_unresolved_failures and (
                json.loads(item['checks_json']) or run['sequence'] > item['reported_sequence']
            ):
                FEEDBACK_STATE_MACHINE.require(state, FeedbackStatus.VERIFYING)
                state = FeedbackStatus.VERIFYING
                repair_run = run_id
                db.execute("UPDATE v1_task_feedback SET state=?,repair_run_id=? WHERE id=?",
                           (state.value,run_id,item['id']))
            if repair_run:
                if state != FeedbackStatus.VERIFYING:
                    raise ValueError('Feedback repair_run_id requires verifying state')
                repair_sequence = db.execute('SELECT sequence FROM v1_column_runs WHERE id=?', (repair_run,)).fetchone()[0]
                verified = [self.verified(db,task,c['column'],c['check_key'],after_sequence=repair_sequence,current_run=run_id,
                                         after_check_rowid=item['observed_check_rowid'])
                            for c in json.loads(item['checks_json'])]
                if json.loads(item['checks_json']) and all(verified):
                    FEEDBACK_STATE_MACHINE.require(state, FeedbackStatus.RESOLVED)
                    db.execute("UPDATE v1_task_feedback SET state=?,verification_json=?,resolved_at=? WHERE id=?",
                               (FeedbackStatus.RESOLVED.value,json.dumps(verified),utcnow(),item['id']))
                else:
                    FEEDBACK_STATE_MACHINE.require(state, FeedbackStatus.VERIFYING)
                    db.execute("UPDATE v1_task_feedback SET state=?,verification_json=NULL,resolved_at=NULL WHERE id=?",
                               (FeedbackStatus.VERIFYING.value,item['id']))

"""Durable user-turn boundaries, separate from already authorized Worker lifetimes."""
import hashlib
import json

from app.v1.conversations.conversation_intent import (TurnResolution, DiscussionDraftUpdate, IntentDecision,
                                        IntentQuestion, IntentProposal, WorkIntentState)
from app.v1.protocols.conversation.conversation_intent_contracts import (RequirementSnapshot, TurnResolutionReceipt,
                                                  UserTurnContract)
from app.v1.storage_support import new_id, utcnow


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def merge_decisions(state: WorkIntentState, updates: list[IntentDecision]) -> None:
    merged = {item.key: item for item in state.decisions}
    for update in updates:
        merged[update.key] = update
    state.decisions = list(merged.values())


def merge_questions(state: WorkIntentState, updates) -> None:
    merged = {item.key: item for item in state.open_questions}
    for update in updates:
        if update.key not in merged and update.question is None and update.state == 'resolved':
            decided = any(item.key == update.key and item.status in {'user_decided', 'delegated_choice'}
                          for item in state.decisions)
            if decided:
                continue
        prior = merged.get(update.key)
        if prior is None:
            if not update.question:
                raise ValueError(f'NewQuestionMissing: {update.key} needs question text; existing keys may update state only')
            question = IntentQuestion.model_validate(update)
        else:
            question = prior.model_copy(deep=True)
            for name in update.model_fields_set:
                replacement = getattr(update, name)
                if replacement is not None:
                    setattr(question, name, replacement)
            question = IntentQuestion.model_validate(question)
        if not question.question:
            raise ValueError(f'NewQuestionMissing: {update.key} needs question text; existing keys may update state only')
        merged[update.key] = question
    state.open_questions = list(merged.values())


class ConversationIntentRepository:
    def __init__(self, store):
        self.store = store

    def init_schema(self):
        with self.store.tx(immediate=True) as db:
            for name, spec in [('requested_mode', "TEXT NOT NULL DEFAULT 'auto'"),
                               ('user_action_json', 'TEXT'), ('turn_contract_json', "TEXT NOT NULL DEFAULT '{}'"),
                               ('execution_grant_id', 'TEXT')]:
                self.store.schema_repository._ensure_column(db, 'v1_conversation_jobs', name, spec)
            db.execute('''CREATE TABLE IF NOT EXISTS v1_work_intents (
                project_id TEXT NOT NULL, revision INTEGER NOT NULL, source_message_id INTEGER NOT NULL,
                job_id TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project_id,revision))''')
            # Early development builds made job_id unique, before a turn could
            # append draft revisions. Preserve their snapshots on initialization.
            indexes = db.execute("SELECT name FROM pragma_index_list('v1_work_intents') WHERE \"unique\"=1").fetchall()
            if any([r['name'] for r in db.execute('SELECT name FROM pragma_index_info(?)', (index['name'],))] == ['job_id'] for index in indexes):
                db.execute('ALTER TABLE v1_work_intents RENAME TO v1_work_intents_legacy')
                db.execute('''CREATE TABLE v1_work_intents (
                    project_id TEXT NOT NULL, revision INTEGER NOT NULL, source_message_id INTEGER NOT NULL,
                    job_id TEXT NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,revision))''')
                db.execute('INSERT INTO v1_work_intents SELECT project_id,revision,source_message_id,job_id,snapshot_json,created_at FROM v1_work_intents_legacy')
                db.execute('DROP TABLE v1_work_intents_legacy')
            db.execute('''CREATE TABLE IF NOT EXISTS v1_turn_resolutions (
                job_id TEXT PRIMARY KEY, resolution_json TEXT NOT NULL, result_json TEXT NOT NULL,
                created_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS v1_discussion_draft_updates (
                job_id TEXT PRIMARY KEY, update_json TEXT NOT NULL, result_json TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS v1_discussion_draft_revisions (
                job_id TEXT NOT NULL, based_on_revision INTEGER NOT NULL,
                update_json TEXT NOT NULL, result_json TEXT NOT NULL,
                PRIMARY KEY(job_id,based_on_revision))''')
            db.execute('''INSERT OR IGNORE INTO v1_discussion_draft_revisions
                SELECT job_id,json_extract(update_json,'$.based_on_intent_revision'),update_json,result_json
                FROM v1_discussion_draft_updates''')
            db.execute('''CREATE TABLE IF NOT EXISTS v1_execution_grants (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_job_id TEXT NOT NULL,
                requirement_id TEXT, requirement_revision INTEGER, kind TEXT NOT NULL, state TEXT NOT NULL,
                snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL, created_at TEXT NOT NULL)''')

    def _context_model(self, job) -> WorkIntentState:
        with self.store.connect() as db:
            row = db.execute('''SELECT snapshot_json FROM v1_work_intents WHERE project_id=?
                AND source_message_id<=? ORDER BY revision DESC LIMIT 1''',
                (job['project_id'], job['user_message_id'])).fetchone()
        return WorkIntentState.model_validate_json(row[0]) if row else WorkIntentState()

    def context(self, job):
        return self._context_model(job).model_dump(mode='json')

    def job_for_run(self, run_id, project_id):
        if not run_id:
            return None
        with self.store.connect() as db:
            row = db.execute('''SELECT j.* FROM v1_conversation_jobs j JOIN v1_agent_runs r
                ON r.conversation_job_id=j.id WHERE r.id=? AND r.project_id=? AND j.project_id=?''',
                (run_id, project_id, project_id)).fetchone()
        return dict(row) if row else None

    def contract(self, job):
        return UserTurnContract.model_validate_json(job.get('turn_contract_json') or '{}').model_dump(
            mode='json', exclude_none=True)

    def _evidence(self, db, job, resolution):
        if resolution.source_message_id != job['user_message_id']:
            raise ValueError('TurnEvidenceMismatch: resolve the current user message')
        ids = {e.message_id for e in resolution.user_evidence}
        for item in [*resolution.decision_updates, *resolution.open_question_updates]:
            ids.update(item.source_message_ids)
        for identity in ids:
            row = db.execute('SELECT role,content FROM v1_conversations WHERE id=? AND project_id=?',
                             (identity, job['project_id'])).fetchone()
            if not row or row['role'] != 'user' or identity > job['user_message_id']:
                raise ValueError('TurnEvidenceMismatch: evidence must reference actual prior/current user messages')
            for evidence in resolution.user_evidence:
                if evidence.message_id == identity and evidence.quote is not None and evidence.quote not in row['content']:
                    raise ValueError('TurnEvidenceMismatch: quote is absent from the referenced user message. '
                        'Do not paraphrase. Reference message_id alone or provide an exact excerpt.')
        if resolution.act in {'execute', 'control'} or resolution.constraint_change == 'release_hold':
            if not any(e.message_id == job['user_message_id'] for e in resolution.user_evidence):
                raise ValueError('TurnEvidenceMissing: this action needs the current user request, not an assistant promise')

    def resolve(self, ctx, arguments, *, final_only=False):
        resolution = TurnResolution.model_validate(arguments)
        if final_only and resolution.act not in {'discuss', 'status', 'clarify'}:
            raise ValueError('A final report cannot grant execution or control')
        job = self.store.agents.assert_conversation(ctx)
        if job['trigger_kind'] != 'user':
            raise ValueError('Event turns cannot create user execution authorization')
        payload = stable(resolution.model_dump(mode='json'))
        with ctx.effect_guard():
            with self.store.connect() as db:
                prior = db.execute('SELECT * FROM v1_turn_resolutions WHERE job_id=?', (job['id'],)).fetchone()
                if prior:
                    if prior['resolution_json'] != payload:
                        raise ValueError('TurnAlreadyResolved: a committed turn boundary cannot be upgraded')
                    return TurnResolutionReceipt.model_validate_json(prior['result_json']).model_dump(mode='json')
                self._evidence(db, job, resolution)
                state = self._context_model(job)
                latest = db.execute('SELECT MAX(revision) FROM v1_work_intents WHERE project_id=?', (ctx.project_id,)).fetchone()[0] or 0
                if state.revision != resolution.based_on_intent_revision or latest != state.revision:
                    raise ValueError(f'TurnRevisionConflict: expected based_on_intent_revision={state.revision}, '
                                     f'source_message_id={job["user_message_id"]}. Rejected calls do not increment the revision. '
                                     'Use conversation.turn.inspect if context is unclear.')
                if resolution.act in {'execute', 'control'} and (not job['start_task'] or job['requested_mode'] == 'discuss'):
                    raise ValueError('ConversationMutationDisabled: explicit discussion mode is this turn\'s upper bound')
                if resolution.focus == 'new_scope':
                    state.reset_scope(new_id('draft'))
                state.draft_id = state.draft_id or new_id('draft')
                if resolution.constraint_change == 'set_hold' or resolution.act in {'discuss', 'clarify'}:
                    source_id = state.hold_source_id if state.execution_hold and resolution.constraint_change != 'set_hold' else None
                    state.execution_hold = True
                    state.hold_source_id = source_id or job['user_message_id']
                if resolution.constraint_change == 'release_hold':
                    state.execution_hold = False
                    state.hold_source_id = None
                if resolution.act == 'execute' and state.execution_hold:
                    raise ValueError('DiscussionHoldActive: the prior discussion boundary still applies. Re-evaluate the current request: '
                        'technical choices, recommendations and a proposed plan are discussion, not permission to implement. '
                        'Resolve discuss and answer those questions. Only an actual current instruction to perform the work can release the hold.')
                merge_decisions(state, resolution.decision_updates)
                merge_questions(state, resolution.open_question_updates)
                if resolution.scope_summary:
                    state.scope_summary = resolution.scope_summary
                if resolution.accepted_proposal_id:
                    proposal = state.proposal
                    if not proposal or proposal.id != resolution.accepted_proposal_id:
                        raise ValueError('ProposalConflict: accepted proposal is not the current immutable proposal')
                    state.scope_summary = proposal.content
                action = json.loads(job.get('user_action_json') or 'null')
                if action and resolution.act == 'execute':
                    proposal = state.proposal
                    if not proposal or (action['proposal_id'], action['proposal_hash']) != (proposal.id, proposal.hash):
                        raise ValueError('ProposalConflict: the user action refers to a superseded proposal')
                    if resolution.accepted_proposal_id != proposal.id:
                        raise ValueError('ProposalConflict: bind the exact proposal selected by the user')
                if resolution.proposal is not None:
                    if resolution.act == 'execute':
                        raise ValueError('Accept the existing proposal or a direct user scope; do not invent an accepted proposal')
                    state.proposal = IntentProposal(id=new_id('proposal'), content=resolution.proposal,
                        hash=hashlib.sha256(resolution.proposal.encode()).hexdigest())
                requirement = None
                grant_id = None
                phase = 'discussion'
                target = resolution.requirement_id or state.requirement_id
                if resolution.act == 'execute':
                    if not state.scope_summary:
                        raise ValueError('ScopeSummaryMissing: record the actual requested delivery scope')
                    if resolution.execution_request in {'continue', 'extend'}:
                        if not target:
                            raise ValueError('RequirementMissing: identify the existing scope to continue/extend')
                        requirement = self.store.agents.get_requirement(ctx.project_id, target)
                    else:
                        requirement = self.store.agents.requirement(ctx.project_id, objective=state.scope_summary,
                                                                    scope_key=state.draft_id, strict=True)
                    if resolution.execution_request != 'extend' and requirement['status'] != 'active':
                        raise ValueError('RequirementClosed: use an explicit scope extension')
                    state.requirement_id = requirement['id']
                    existing_grant = None
                    if resolution.execution_request == 'continue':
                        state.scope_summary = requirement['objective']
                        existing_grant = db.execute("SELECT id FROM v1_execution_grants WHERE project_id=? AND requirement_id=? AND requirement_revision=? AND state='active' ORDER BY created_at DESC LIMIT 1",
                            (ctx.project_id, requirement['id'], requirement['revision'])).fetchone()
                    if existing_grant:
                        grant_id = existing_grant['id']
                    else:
                        grant_id = new_id('grant')
                        grant_state = 'pending_scope_revision' if resolution.execution_request == 'extend' else 'active'
                        snapshot = stable(state.model_dump(mode='json'))
                        db.execute('INSERT INTO v1_execution_grants VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (grant_id, ctx.project_id, job['id'], requirement['id'], requirement['revision'],
                             resolution.execution_request, grant_state, snapshot, hashlib.sha256(snapshot.encode()).hexdigest(), utcnow()))
                    db.execute('UPDATE v1_conversation_jobs SET requirement_id=?,requirement_revision=?,execution_grant_id=? WHERE id=?',
                               (requirement['id'], requirement['revision'], grant_id, job['id']))
                    self.store.scopes.remember(requirement, job_id=job['id'], reason='user execution request')
                    phase = 'execution'
                elif resolution.act == 'control':
                    controlled_task = self.store.get_project_task(ctx.project_id, resolution.control_task_id)
                    if resolution.control_capability == 'task.resume':
                        if controlled_task['status'] == 'recovering':
                            raise ValueError('RecoveringTaskRequiresRetry: call conversation.execution.authorize '
                                             'with request=continue, then task.retry for the same Task')
                        if controlled_task['status'] == 'failed':
                            raise ValueError('FailedTaskRequiresReopen: call conversation.execution.authorize '
                                             'with request=continue, then task.reopen for the same Task')
                    phase = 'targeted_control'
                state.revision = latest + 1
                contract = UserTurnContract(phase=phase, act=resolution.act,
                    source_message_id=job['user_message_id'], intent_revision=state.revision,
                    execution_grant_id=grant_id, control_capability=resolution.control_capability,
                    control_task_id=resolution.control_task_id)
                result = TurnResolutionReceipt(turn_contract=contract, work_intent=state,
                    requirement=RequirementSnapshot.model_validate(requirement) if requirement else None,
                    next_reply_contract='Boundary committed. Do not include turn_resolution in the final report. '
                    'For discussion answer naturally; save durable work notes with project.memory.write if needed. '
                    'For work_result use conversation.reply with actual tool/task IDs. '
                    'A Task traverses all Columns. Never describe lifecycle steps as separate Tasks of the same delivery.')
                db.execute('INSERT INTO v1_work_intents VALUES(?,?,?,?,?,?)',
                           (ctx.project_id, state.revision, job['user_message_id'], job['id'], stable(state.model_dump(mode='json')), utcnow()))
                db.execute('UPDATE v1_conversation_jobs SET turn_contract_json=? WHERE id=?', (stable(contract.model_dump(mode='json')), job['id']))
                db.execute('INSERT INTO v1_turn_resolutions VALUES(?,?,?,?)', (job['id'], payload, stable(result.model_dump(mode='json')), utcnow()))
                self.store._event(db, ctx.project_id, None, None, 'conversation.turn_resolved',
                                  {'job_id':job['id'], 'phase':phase, 'intent_revision':state.revision})
                return result.model_dump(mode='json')

    def update_draft(self, ctx, arguments):
        update = DiscussionDraftUpdate.model_validate(arguments)
        job = self.store.agents.assert_conversation(ctx)
        if self.contract(job)['phase'] != 'discussion' or job['trigger_kind'] != 'user':
            raise ValueError('Draft updates belong to discussion, not execution authority')
        payload = stable(update.model_dump(mode='json'))
        with ctx.effect_guard():
            with self.store.connect() as db:
                prior = db.execute('SELECT * FROM v1_discussion_draft_revisions WHERE job_id=? AND based_on_revision=?',
                                   (job['id'],update.based_on_intent_revision)).fetchone()
                if prior:
                    if prior['update_json'] != payload:
                        raise ValueError('DraftRevisionConflict: this draft revision already has a different committed update')
                    return json.loads(prior['result_json'])
                state = self._context_model(job)
                latest = db.execute('SELECT MAX(revision) FROM v1_work_intents WHERE project_id=?', (ctx.project_id,)).fetchone()[0]
                if update.based_on_intent_revision != state.revision or latest != state.revision:
                    raise ValueError('TurnRevisionConflict: use the latest discussion revision')
                evidence = TurnResolution(source_message_id=job['user_message_id'], based_on_intent_revision=state.revision,
                                          act='discuss', decision_updates=update.decision_updates, open_question_updates=update.open_question_updates)
                self._evidence(db, job, evidence)
                merge_decisions(state, update.decision_updates)
                merge_questions(state, update.open_question_updates)
                if update.proposal is not None:
                    state.proposal = IntentProposal(id=new_id('proposal'), content=update.proposal,
                        hash=hashlib.sha256(update.proposal.encode()).hexdigest())
                state.revision += 1
                saved = state.model_dump(mode='json')
                db.execute('INSERT INTO v1_work_intents VALUES(?,?,?,?,?,?)',
                           (ctx.project_id,state.revision,job['user_message_id'],job['id'],stable(saved),utcnow()))
                db.execute('INSERT INTO v1_discussion_draft_revisions VALUES(?,?,?,?)',
                           (job['id'],update.based_on_intent_revision,payload,stable(saved)))
                return saved

    def denial(self, ctx, capability, arguments, effect):
        if ctx.column_run_id:
            return None
        job = self.job_for_run(ctx.agent_run_id, ctx.project_id)
        if job is None:
            return 'ConversationMutationDisabled: this caller is discussion-only' if not ctx.start_task and effect in {'write','process','control'} else None
        if effect not in {'write', 'process', 'control', 'session_control'}:
            return None
        if job['status'] != 'running':
            return 'ConversationTurnInactive: no mutations after a turn has finished'
        if capability in {'conversation.turn.resolve', 'conversation.execution.authorize',
                          'conversation.task.control.authorize', 'conversation.reply'}:
            return None
        contract = self.contract(job)
        if capability == 'conversation.draft.update':
            return None if job['trigger_kind'] == 'user' and contract['phase'] == 'discussion' else 'DraftUpdateOutsideDiscussion'
        if job['trigger_kind'] != 'user':
            if contract.get('phase') == 'supervision':
                return self._supervision_denial(ctx, job, contract, capability, arguments)
            return 'EventMutationDisabled: notifications cannot authorize planning, execution or Task control; Runtime follows the frozen Workflow'
        if not job['start_task'] or job['requested_mode'] == 'discuss':
            return 'ConversationMutationDisabled: this user turn explicitly permits discussion only'
        if contract['phase'] == 'targeted_control':
            return None if (capability == contract['control_capability'] and arguments.get('task_id') == contract['control_task_id']) else 'TurnControlScopeMismatch: only the requested Task control is allowed'
        if contract['phase'] != 'execution':
            if contract['phase'] == 'unresolved' and capability in {'task.retry', 'task.reopen', 'agent.worker.send'}:
                return ('RecoveryRequiresExecutionAuthorization: call conversation.execution.authorize '
                        'with request=continue before Worker input, task.retry or task.reopen')
            return 'TurnUnresolved: call conversation.execution.authorize or conversation.task.control.authorize before business mutations' if contract['phase'] == 'unresolved' else 'ConversationMutationDisabled: continue discussing; no work was authorized'
        with self.store.connect() as db:
            grant = db.execute('SELECT * FROM v1_execution_grants WHERE id=? AND project_id=?',
                               (job['execution_grant_id'],ctx.project_id)).fetchone()
        if not grant:
            return 'ExecutionGrantMissing'
        if grant['state'] == 'pending_scope_revision':
            return None if capability == 'project.scope.revise' and arguments.get('requirement_id') == grant['requirement_id'] else 'ScopeRevisionPending: revise the requested scope before creating work'
        req = self.store.agents.get_requirement(ctx.project_id, grant['requirement_id'])
        if grant['state'] != 'active' or req['status'] != 'active' or req['revision'] != grant['requirement_revision']:
            return 'ExecutionScopeClosed: the current grant is closed or superseded'
        if (job['requirement_id'], job['requirement_revision']) != (grant['requirement_id'], grant['requirement_revision']):
            return 'ExecutionScopeMismatch: this Job cannot select a scope outside its grant'
        if capability == 'project.scope.revise' and grant['kind'] != 'extend':
            # A newly begun, independent Requirement may take over the current
            # Workflow in this same user turn. ScopeRepository.revise still
            # enforces terminal predecessors and publishes a new revision.
            current = self.store.scopes.inspect(ctx.project_id)
            new_scope_handover = (
                grant['kind'] == 'begin' and grant['source_job_id'] == job['id']
                and req['revision'] == 1
                and arguments.get('requirement_id') == req['id']
                and current.get('workflow_revision_id') == arguments.get('expected_workflow_revision_id')
                and current.get('requirement_id') not in (None, req['id'])
            )
            if not new_scope_handover:
                return ('ScopeExtensionRequestMissing: project.scope.revise changes Requirement scope and requires request=extend. '
                        'If only changing Columns or transitions within the current Requirement, keep request=continue, '
                        'call workflow.create instead. A new begin Requirement may take over '
                        'a previous completed Workflow only in its creation turn')
        if capability == 'requirement.create' and (arguments.get('scope_key'), arguments.get('objective')) != (req['scope_key'], req['objective']):
            return 'ExecutionScopeMismatch: resolve a new user scope instead of replacing this turn requirement'
        return None

    def _supervision_denial(self, ctx, job, contract, capability, arguments):
        task_id = contract.get('supervised_task_id')
        if not task_id or not job.get('execution_grant_id'):
            return 'SupervisionGrantMissing'
        with self.store.connect() as db:
            grant = db.execute("SELECT state,requirement_id,requirement_revision FROM v1_execution_grants WHERE id=? AND project_id=?",
                               (job['execution_grant_id'],ctx.project_id)).fetchone()
            task = db.execute('SELECT requirement_id,requirement_revision,status,current_column,proposed_task_ref FROM v1_tasks WHERE id=? AND project_id=?',
                              (task_id,ctx.project_id)).fetchone()
            requirement = db.execute('SELECT status,revision FROM v1_requirements WHERE id=? AND project_id=?',
                                     (job['requirement_id'],ctx.project_id)).fetchone()
            if (not grant or grant['state'] != 'active' or not task or not requirement or
                    requirement['status'] != 'active' or
                    (grant['requirement_id'],grant['requirement_revision']) !=
                    (job['requirement_id'],job['requirement_revision']) or
                    (task['requirement_id'],task['requirement_revision']) !=
                    (job['requirement_id'],job['requirement_revision']) or
                    requirement['revision'] != job['requirement_revision']):
                return 'SupervisionScopeExpired'
            if capability in {'workflow.plan.save', 'workflow.publish', 'workflow.create',
                              'workflow.acceptance.configure', 'task.plan.save', 'task.successor'}:
                if task['status'] != 'failed':
                    return 'SupervisionTaskNotFailed: Workflow repair requires a failed Task'
                successor = db.execute(
                    'SELECT 1 FROM v1_tasks WHERE project_id=? AND rerun_of_task_id=? LIMIT 1',
                    (ctx.project_id, task_id),
                ).fetchone()
                if successor:
                    return 'SupervisionSuccessorAlreadyQueued: inspect the existing successor Task'
                if capability == 'task.plan.save':
                    plan = arguments.get('plan')
                    if isinstance(plan, str):
                        try:
                            plan = json.loads(plan)
                        except ValueError:
                            plan = None
                    planned = plan.get('tasks') if isinstance(plan, dict) else None
                    if (not isinstance(planned, list) or len(planned) != 1 or
                            not isinstance(planned[0], dict) or
                            planned[0].get('proposed_task_ref') != task['proposed_task_ref'] or
                            plan.get('repair_of_task_id') != task_id):
                        return 'SupervisionRepairPlanMismatch: save one corrected TaskPlan for the supervised Task'
                if capability == 'task.successor':
                    if arguments.get('task_id') != task_id:
                        return 'SupervisionTaskMismatch: successor must target the supervised Task'
                    saved = db.execute('SELECT plan_json FROM v1_task_plans WHERE id=? AND project_id=?',
                                       (arguments.get('task_plan_id'),ctx.project_id)).fetchone()
                    if not saved or json.loads(saved['plan_json']).get('repair_of_task_id') != task_id:
                        return 'SupervisionRepairPlanMismatch: successor requires a saved repair TaskPlan'
                return None
            if capability in {'task.retry','task.reopen'}:
                if arguments.get('task_id') != task_id or arguments.get('clear_context'):
                    return 'SupervisionTaskMismatch: preserve the same Task and Worker context'
                prior_recovery = db.execute(
                    "SELECT 1 FROM v1_tool_invocations invocation "
                    "JOIN v1_agent_runs run ON run.id=invocation.agent_run_id "
                    "WHERE run.conversation_job_id=? AND invocation.ok=1 "
                    "AND invocation.capability IN ('task.retry','task.reopen','task.successor') LIMIT 1",
                    (job['id'],),
                ).fetchone()
                if prior_recovery:
                    return 'SupervisionRecoveryAlreadyRequested: inspect the Task and finish this notification turn'
                required_status = 'recovering' if capability == 'task.retry' else 'failed'
                if task['status'] != required_status:
                    return f'SupervisionTaskNotRecoverable: {capability} requires a {required_status} Task; current status is {task["status"]}'
                return None
            if capability == 'agent.worker.send':
                assignment_id = arguments.get('assignment_id')
                assignment = db.execute(
                    'SELECT a.task_id,a.agent_instance_id,a.status,r.column_key,r.sequence '
                    'FROM v1_agent_assignments a JOIN v1_column_runs r ON r.id=a.column_run_id '
                    'WHERE a.id=? AND a.project_id=?',
                    (assignment_id,ctx.project_id),
                ).fetchone() if assignment_id else None
                latest = db.execute(
                    'SELECT a.id FROM v1_agent_assignments a '
                    'JOIN v1_column_runs r ON r.id=a.column_run_id '
                    'WHERE a.task_id=? AND r.column_key=? ORDER BY r.sequence DESC LIMIT 1',
                    (task_id,task['current_column']),
                ).fetchone()
                if (not assignment or assignment['task_id'] != task_id or
                        assignment['agent_instance_id'] != arguments.get('worker_id') or
                        assignment['column_key'] != task['current_column'] or
                        not latest or latest['id'] != assignment_id or
                        not ((task['status'] == 'recovering' and assignment['status'] == 'blocked') or
                             (task['status'] == 'running' and assignment['status'] == 'running'))):
                    return 'SupervisionWorkerMismatch: target the current Assignment of this Task'
                return None
        return 'SupervisionMutationDisabled: this event only permits same-Task recovery or Worker input'

    def bind_revised_scope(self, ctx, requirement):
        job = self.job_for_run(ctx.agent_run_id, ctx.project_id)
        if job and job.get('execution_grant_id'):
            with self.store.tx(immediate=True) as db:
                db.execute("UPDATE v1_execution_grants SET requirement_revision=?,state='active' WHERE id=? AND requirement_id=?",
                           (requirement['revision'], job['execution_grant_id'], requirement['id']))

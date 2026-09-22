"""Durable user-turn boundaries, separate from already authorized Worker lifetimes."""
import hashlib
import json

from app.v1.conversation_intent import TurnResolution, DiscussionDraftUpdate, IntentQuestion
from app.v1.storage_support import new_id, utcnow


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def merge_intent_updates(state, field, updates):
    merged = {item['key']:item for item in state[field]}
    for update in updates:
        if field == 'open_questions':
            item = {**merged.get(update.key, {}), **update.model_dump(mode='json', exclude_unset=True, exclude_none=True)}
            if not item.get('question'):
                raise ValueError(f'NewQuestionMissing: {update.key} needs question text; existing keys may update state only')
            merged[update.key] = IntentQuestion.model_validate(item).model_dump(mode='json')
        else:
            merged[update.key] = update.model_dump(mode='json')
    state[field] = list(merged.values())


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

    def context(self, job):
        with self.store.connect() as db:
            row = db.execute('''SELECT snapshot_json FROM v1_work_intents WHERE project_id=?
                AND source_message_id<=? ORDER BY revision DESC LIMIT 1''',
                (job['project_id'], job['user_message_id'])).fetchone()
        return json.loads(row[0]) if row else {
            'revision': 0, 'draft_id': None, 'execution_hold': False, 'hold_source_id': None,
            'scope_summary': '', 'decisions': [], 'open_questions': [], 'proposal': None,
            'requirement_id': None,
        }

    def job_for_run(self, run_id, project_id):
        if not run_id:
            return None
        with self.store.connect() as db:
            row = db.execute('''SELECT j.* FROM v1_conversation_jobs j JOIN v1_agent_runs r
                ON r.conversation_job_id=j.id WHERE r.id=? AND r.project_id=? AND j.project_id=?''',
                (run_id, project_id, project_id)).fetchone()
        return dict(row) if row else None

    def contract(self, job):
        return json.loads(job.get('turn_contract_json') or '{}') or {'phase': 'unresolved'}

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
                    return json.loads(prior['result_json'])
                self._evidence(db, job, resolution)
                state = self.context(job)
                latest = db.execute('SELECT MAX(revision) FROM v1_work_intents WHERE project_id=?', (ctx.project_id,)).fetchone()[0] or 0
                if state['revision'] != resolution.based_on_intent_revision or latest != state['revision']:
                    raise ValueError(f'TurnRevisionConflict: expected based_on_intent_revision={state["revision"]}, '
                                     f'source_message_id={job["user_message_id"]}. Rejected calls do not increment the revision. '
                                     'Use conversation.turn.inspect if context is unclear.')
                if resolution.act in {'execute', 'control'} and (not job['start_task'] or job['requested_mode'] == 'discuss'):
                    raise ValueError('ConversationMutationDisabled: explicit discussion mode is this turn\'s upper bound')
                if resolution.focus == 'new_scope':
                    state = {**state, 'draft_id': new_id('draft'), 'scope_summary': '', 'decisions': [],
                             'open_questions': [], 'proposal': None, 'requirement_id': None}
                state['draft_id'] = state['draft_id'] or new_id('draft')
                if resolution.constraint_change == 'set_hold' or resolution.act in {'discuss', 'clarify'}:
                    source_id = state.get('hold_source_id') if state['execution_hold'] and resolution.constraint_change != 'set_hold' else None
                    state.update(execution_hold=True, hold_source_id=source_id or job['user_message_id'])
                if resolution.constraint_change == 'release_hold':
                    state.update(execution_hold=False, hold_source_id=None)
                if resolution.act == 'execute' and state['execution_hold']:
                    raise ValueError('DiscussionHoldActive: the prior discussion boundary still applies. Re-evaluate the current request: '
                        'technical choices, recommendations and a proposed plan are discussion, not permission to implement. '
                        'Resolve discuss and answer those questions. Only an actual current instruction to perform the work can release the hold.')
                for field, updates in [('decisions', resolution.decision_updates), ('open_questions', resolution.open_question_updates)]:
                    merge_intent_updates(state, field, updates)
                if resolution.scope_summary:
                    state['scope_summary'] = resolution.scope_summary
                if resolution.accepted_proposal_id:
                    proposal = state.get('proposal')
                    if not proposal or proposal['id'] != resolution.accepted_proposal_id:
                        raise ValueError('ProposalConflict: accepted proposal is not the current immutable proposal')
                    state['scope_summary'] = proposal['content']
                action = json.loads(job.get('user_action_json') or 'null')
                if action and resolution.act == 'execute':
                    proposal = state.get('proposal')
                    if not proposal or (action['proposal_id'], action['proposal_hash']) != (proposal['id'], proposal['hash']):
                        raise ValueError('ProposalConflict: the user action refers to a superseded proposal')
                    if resolution.accepted_proposal_id != proposal['id']:
                        raise ValueError('ProposalConflict: bind the exact proposal selected by the user')
                if resolution.proposal is not None:
                    if resolution.act == 'execute':
                        raise ValueError('Accept the existing proposal or a direct user scope; do not invent an accepted proposal')
                    state['proposal'] = {'id': new_id('proposal'), 'content': resolution.proposal,
                                         'hash': hashlib.sha256(resolution.proposal.encode()).hexdigest()}
                if resolution.act == 'execute' and any(q['state'] == 'open' and q['blocking_for_start'] for q in state['open_questions']):
                    raise ValueError('OpenScopeDecision: resolve/delegate the remaining blocking questions before execution')
                requirement = None
                grant_id = None
                phase = 'discussion'
                target = resolution.requirement_id or state.get('requirement_id')
                if resolution.act == 'execute':
                    if not state['scope_summary']:
                        raise ValueError('ScopeSummaryMissing: record the actual requested delivery scope')
                    if resolution.execution_request in {'continue', 'extend'}:
                        if not target:
                            raise ValueError('RequirementMissing: identify the existing scope to continue/extend')
                        requirement = self.store.agents.get_requirement(ctx.project_id, target)
                    else:
                        requirement = self.store.agents.requirement(ctx.project_id, objective=state['scope_summary'],
                                                                    scope_key=state['draft_id'], strict=True)
                    if resolution.execution_request != 'extend' and requirement['status'] != 'active':
                        raise ValueError('RequirementClosed: use an explicit scope extension')
                    state['requirement_id'] = requirement['id']
                    existing_grant = None
                    if resolution.execution_request == 'continue':
                        state['scope_summary'] = requirement['objective']
                        existing_grant = db.execute("SELECT id FROM v1_execution_grants WHERE project_id=? AND requirement_id=? AND requirement_revision=? AND state='active' ORDER BY created_at DESC LIMIT 1",
                            (ctx.project_id, requirement['id'], requirement['revision'])).fetchone()
                    if existing_grant:
                        grant_id = existing_grant['id']
                    else:
                        grant_id = new_id('grant')
                        grant_state = 'pending_scope_revision' if resolution.execution_request == 'extend' else 'active'
                        snapshot = stable(state)
                        db.execute('INSERT INTO v1_execution_grants VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (grant_id, ctx.project_id, job['id'], requirement['id'], requirement['revision'],
                             resolution.execution_request, grant_state, snapshot, hashlib.sha256(snapshot.encode()).hexdigest(), utcnow()))
                    db.execute('UPDATE v1_conversation_jobs SET requirement_id=?,requirement_revision=?,execution_grant_id=? WHERE id=?',
                               (requirement['id'], requirement['revision'], grant_id, job['id']))
                    self.store.scopes.remember(requirement, job_id=job['id'], reason='user execution request')
                    phase = 'execution'
                elif resolution.act == 'control':
                    self.store.get_project_task(ctx.project_id, resolution.control_task_id)
                    phase = 'targeted_control'
                state['revision'] = latest + 1
                contract = {'phase': phase, 'act': resolution.act, 'source_message_id': job['user_message_id'], 'intent_revision': state['revision'],
                            'execution_grant_id': grant_id, 'control_capability': resolution.control_capability,
                            'control_task_id': resolution.control_task_id}
                result = {'turn_contract': contract, 'work_intent': state, 'requirement': requirement,
                          'next_reply_contract': 'Boundary committed. Do not include turn_resolution in the final report. '
                          'For discussion answer naturally; save further decisions/questions/proposals with conversation.draft.update if needed. '
                          'For work_result use conversation.reply with actual tool/task IDs. '
                          'A Task traverses all Columns. Never describe lifecycle steps as separate Tasks of the same delivery.'}
                db.execute('INSERT INTO v1_work_intents VALUES(?,?,?,?,?,?)',
                           (ctx.project_id, state['revision'], job['user_message_id'], job['id'], stable(state), utcnow()))
                db.execute('UPDATE v1_conversation_jobs SET turn_contract_json=? WHERE id=?', (stable(contract), job['id']))
                db.execute('INSERT INTO v1_turn_resolutions VALUES(?,?,?,?)', (job['id'], payload, stable(result), utcnow()))
                self.store._event(db, ctx.project_id, None, None, 'conversation.turn_resolved',
                                  {'job_id':job['id'], 'phase':phase, 'intent_revision':state['revision']})
                return result

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
                state = self.context(job)
                latest = db.execute('SELECT MAX(revision) FROM v1_work_intents WHERE project_id=?', (ctx.project_id,)).fetchone()[0]
                if update.based_on_intent_revision != state['revision'] or latest != state['revision']:
                    raise ValueError('TurnRevisionConflict: use the latest discussion revision')
                evidence = TurnResolution(source_message_id=job['user_message_id'], based_on_intent_revision=state['revision'],
                                          act='discuss', decision_updates=update.decision_updates, open_question_updates=update.open_question_updates)
                self._evidence(db, job, evidence)
                for field, changes in [('decisions',update.decision_updates),('open_questions',update.open_question_updates)]:
                    merge_intent_updates(state, field, changes)
                if update.proposal is not None:
                    state['proposal'] = {'id':new_id('proposal'), 'content':update.proposal,
                                         'hash':hashlib.sha256(update.proposal.encode()).hexdigest()}
                state['revision'] += 1
                db.execute('INSERT INTO v1_work_intents VALUES(?,?,?,?,?,?)',
                           (ctx.project_id,state['revision'],job['user_message_id'],job['id'],stable(state),utcnow()))
                db.execute('INSERT INTO v1_discussion_draft_revisions VALUES(?,?,?,?)',
                           (job['id'],update.based_on_intent_revision,payload,stable(state)))
                return state

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
        if capability in {'conversation.turn.resolve', 'conversation.reply'}:
            return None
        contract = self.contract(job)
        if capability == 'conversation.draft.update':
            return None if job['trigger_kind'] == 'user' and contract['phase'] == 'discussion' else 'DraftUpdateOutsideDiscussion'
        if job['trigger_kind'] != 'user':
            return 'EventMutationDisabled: notifications cannot authorize planning, execution or Task control; Runtime follows the frozen Workflow'
        if not job['start_task'] or job['requested_mode'] == 'discuss':
            return 'ConversationMutationDisabled: this user turn explicitly permits discussion only'
        if contract['phase'] == 'targeted_control':
            return None if (capability == contract['control_capability'] and arguments.get('task_id') == contract['control_task_id']) else 'TurnControlScopeMismatch: only the requested Task control is allowed'
        if contract['phase'] != 'execution':
            return 'TurnUnresolved: submit conversation.turn.resolve before business mutations' if contract['phase'] == 'unresolved' else 'ConversationMutationDisabled: continue discussing; no work was authorized'
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
            return 'ScopeExtensionRequestMissing: a new user extension turn is required to expand the scope'
        if capability == 'requirement.create' and (arguments.get('scope_key'), arguments.get('objective')) != (req['scope_key'], req['objective']):
            return 'ExecutionScopeMismatch: resolve a new user scope instead of replacing this turn requirement'
        return None

    def bind_revised_scope(self, ctx, requirement):
        job = self.job_for_run(ctx.agent_run_id, ctx.project_id)
        if job and job.get('execution_grant_id'):
            with self.store.tx(immediate=True) as db:
                db.execute("UPDATE v1_execution_grants SET requirement_revision=?,state='active' WHERE id=? AND requirement_id=?",
                           (requirement['revision'], job['execution_grant_id'], requirement['id']))

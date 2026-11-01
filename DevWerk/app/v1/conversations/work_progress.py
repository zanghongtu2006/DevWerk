"""Job-owned work tracking; it records progress but never grants business effects."""
from __future__ import annotations

from app.v1.storage_support import utcnow
from app.v1.protocols.conversation.conversation_work_contracts import (
    ArtifactObservation, ConversationWorkSnapshot, ConversationWorkUpdateInput,
    WorkResource, WorkSnapshotInput,
)


class ConversationWorkProgress:
    def __init__(self, store):
        self.store = store

    def current_view(self, project_id, job_id):
        return self.store.conversation_work.latest(project_id, job_id)

    def historical_reference(self, project_id, job_id, revision):
        return self.store.conversation_work.get_revision(project_id, job_id, revision)

    def update(self, ctx, arguments):
        request = ConversationWorkUpdateInput.model_validate(arguments)
        if len(request.model_dump_json().encode('utf-8')) > 32 * 1024:
            raise ValueError('Work snapshot exceeds 32 KiB; use coarse-grained items')
        job = self.store.agents.assert_conversation(ctx)
        if request.snapshot:
            for item in request.snapshot.items:
                if item.deferral_quote and item.deferral_quote not in job['message']:
                    raise ValueError(f'Deferral for {item.item_id} must quote the current user request')
        contract = self.store.intents.contract(job)
        if (job['trigger_kind'] != 'user' or not job['start_task'] or
                job['requested_mode'] == 'discuss' or contract['phase'] == 'discussion'):
            raise PermissionError('Work continuation requires an active user execution turn')
        with ctx.effect_guard():
            with self.store.tx(immediate=True) as db:
                operation_key = ctx.execution_key or f'{ctx.agent_run_id}:{request.base_revision}'
                replay = self.store.conversation_work.by_operation(ctx.project_id, operation_key, db=db)
                if replay:
                    if replay.job_id != job['id'] or replay.revision != request.base_revision + 1:
                        raise ValueError('WorkOperationConflict: operation key belongs to another update')
                    if request.resume_from and request.resume_from != replay.resume_from:
                        raise ValueError('WorkOperationConflict: resume source changed on replay')
                    if request.snapshot:
                        expected = request.snapshot.model_dump(mode='json')
                        actual = replay.model_dump(mode='json', exclude={
                            'project_id': True, 'job_id': True, 'source_user_message_id': True,
                            'revision': True, 'resume_from': True, 'resources': {'__all__': {'observation'}}})
                        if actual != expected:
                            raise ValueError('WorkOperationConflict: update payload changed on replay')
                    return replay
                current = self.store.conversation_work.latest(ctx.project_id, job['id'], db=db)
                if request.resume_from:
                    if current or request.base_revision != 0:
                        raise ValueError('Resume is only allowed for the first work revision in this Job')
                    source = self.historical_reference(ctx.project_id,
                        request.resume_from.source_job_id, request.resume_from.source_revision)
                    if not source or source.job_id == job['id']:
                        raise ValueError('Resume source is absent or is the current Job')
                    wanted = set(request.resume_from.item_ids)
                    items = {item.item_id: item for item in source.items}
                    if wanted - items.keys():
                        raise ValueError('Resume refers to an absent work item')
                    # Keep the dependency closure; no dangling prerequisites.
                    while True:
                        closure = wanted | {dependency for key in wanted
                                            for dependency in items[key].depends_on}
                        if closure == wanted:
                            break
                        wanted = closure
                    selected = [item.model_copy(deep=True) for item in source.items
                                if item.item_id in wanted]
                    for item in selected:
                        if item.agent_status == 'deferred':
                            item.agent_status = 'pending'
                            item.deferral_quote = None
                    needed = {resource for item in selected for resource in item.resource_ids}
                    resources = [resource.model_copy(deep=True) for resource in source.resources
                                 if resource.resource_id in needed]
                    proposed = WorkSnapshotInput(objective=source.objective,
                        items=selected, resources=resources, next_action=source.next_action)
                else:
                    proposed = request.snapshot
                if proposed is None:
                    raise ValueError('Missing work snapshot')
                if current:
                    proposed_ids = {item.item_id for item in proposed.items}
                    missing_ids = [item.item_id for item in current.items if item.item_id not in proposed_ids]
                    if missing_ids:
                        raise ValueError('Work update cannot silently drop prior items: ' + ', '.join(missing_ids))
                resources = []
                changed_paths = set()
                for resource in proposed.resources:
                    observed = WorkResource.model_validate(resource.model_dump())
                    if resource.relative_path:
                        fact = ctx.files.inspect_file(resource.relative_path)
                        if observed.observation and observed.observation.sha256 != fact['sha256']:
                            changed_paths.add(resource.relative_path)
                        observed.observation = ArtifactObservation(**fact, observed_at=utcnow())
                    if resource.tool_call_id:
                        receipt = db.execute('''SELECT 1 FROM v1_tool_invocations
                            WHERE project_id=? AND tool_call_id=? LIMIT 1''',
                            (ctx.project_id, resource.tool_call_id)).fetchone()
                        if not receipt:
                            raise ValueError(f'Work resource {resource.resource_id} references an absent tool receipt: {resource.tool_call_id}')
                    resources.append(observed)
                items = [item.model_copy(deep=True) for item in proposed.items]
                observed_by_id = {resource.resource_id: resource.observation for resource in resources}
                for item in items:
                    if any(check.tool_call_id and not db.execute('''SELECT 1 FROM v1_tool_invocations
                        WHERE project_id=? AND tool_call_id=? LIMIT 1''',
                        (ctx.project_id, check.tool_call_id)).fetchone() for check in item.checks):
                        raise ValueError('Work check references an absent tool receipt')
                    if any(check.resource_id and check.resource_sha256 !=
                           (observed_by_id[check.resource_id].sha256 if observed_by_id[check.resource_id] else None)
                           for check in item.checks):
                        item.agent_status = 'blocked'
                        item.unresolved_problems.append('Check does not match current artifact version')
                    if request.resume_from and changed_paths.intersection(
                        resource.relative_path for resource in resources
                        if resource.resource_id in item.resource_ids and resource.relative_path):
                        item.agent_status = 'blocked'
                        item.unresolved_problems.append('Inherited artifact changed; recheck required')
                snapshot = ConversationWorkSnapshot(
                    **proposed.model_dump(exclude={'items', 'resources'}),
                    items=items, resources=resources, project_id=ctx.project_id,
                    job_id=job['id'], source_user_message_id=job['user_message_id'],
                    revision=request.base_revision + 1,
                    resume_from=request.resume_from or (current.resume_from if current else None),
                )
                return self.store.conversation_work.append(snapshot, request.base_revision,
                    operation_key, db=db)

    def validate_ready(self, snapshot: ConversationWorkSnapshot):
        return all(item.agent_status == 'ready' and (item.resource_ids or item.checks) and
                   not item.unresolved_problems and
                   all(not check.unresolved for check in item.checks) and
                   all(resource.observation and resource.observation.exists
                       for resource in snapshot.resources if resource.resource_id in item.resource_ids)
                   for item in snapshot.items if item.agent_status != "deferred")

    def current_readiness(self, snapshot: ConversationWorkSnapshot):
        from app.v1.workspace.files import ProjectFiles
        files = ProjectFiles(self.store.get_project(snapshot.project_id)['base_dir'])
        stale = set()
        current_hashes = {}
        receipt_ids = {resource.tool_call_id for resource in snapshot.resources if resource.tool_call_id}
        receipt_ids.update(check.tool_call_id for item in snapshot.items
                           for check in item.checks if check.tool_call_id)
        with self.store.connect() as db:
            successful_receipts = {receipt_id for receipt_id in receipt_ids
                if db.execute('''SELECT 1 FROM v1_tool_invocations
                    WHERE project_id=? AND tool_call_id=? AND ok=1 LIMIT 1''',
                    (snapshot.project_id, receipt_id)).fetchone()}
        for resource in snapshot.resources:
            if resource.relative_path:
                actual = files.inspect_file(resource.relative_path)
                current_hashes[resource.resource_id] = actual['sha256']
                if not actual['exists'] or not resource.observation or (resource.observation.exists,
                        resource.observation.sha256) != (actual['exists'], actual['sha256']):
                    stale.add(resource.resource_id)
            elif not resource.tool_call_id or resource.tool_call_id not in successful_receipts:
                stale.add(resource.resource_id)
        incomplete = [item.item_id for item in snapshot.items
                      if item.agent_status != 'deferred' and (item.agent_status != 'ready' or not (item.resource_ids or item.checks)
                      or item.unresolved_problems or any(check.unresolved or
                          not check.tool_call_id or check.tool_call_id not in successful_receipts or
                          (check.resource_id and check.resource_sha256 !=
                           current_hashes.get(check.resource_id)) for check in item.checks)
                      or stale.intersection(item.resource_ids))]
        return incomplete, stale

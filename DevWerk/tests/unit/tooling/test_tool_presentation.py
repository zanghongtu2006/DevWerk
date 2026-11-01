from __future__ import annotations

import json

from app.v1.agents.agent_models import AgentRunSpec
from app.v1.agents.agent_run_preparation import AgentRunPreparer
from app.v1.protocols.tools.core.capability_protocol import CapabilityToolset
from app.v1.tooling.capability_registry import CapabilityEntry
from app.v1.tooling.tool_presentation import ToolPresentation
from app.v1.tooling.tool_call_resolution import resolve_tool_call


def _prepared_conversation(store, project):
    job = store.create_conversation_job(project['id'], 'Find the right Tool', True)
    spec = AgentRunSpec(kind='conversation', project=project, instruction='',
                        instruction_revision=1, context={}, capability_ids=store.registry.all_ids(),
                        conversation_job_id=job['id'])
    prepared = AgentRunPreparer(store=store, registry=store.registry,
        runtime_policy=store.policy, platform_policy=store.latest_platform_policy()).prepare(spec)
    return prepared


def test_conversation_catalog_describes_on_demand_tools_without_changing_visibility(store, tmp_path):
    store.registry.register(CapabilityEntry(
        id='mcp__fixture__lookup', description='Look up external information',
        input_schema={'type': 'object', 'properties': {'query': {'type': 'string'}},
                      'required': ['query'], 'additionalProperties': False},
        output_schema={'type': 'object'}, handler=lambda _args, _ctx: {},
        toolset=CapabilityToolset.MCP,
    ))
    project = store.create_project('catalog', '', str(tmp_path / 'project'))
    prepared = _prepared_conversation(store, project)
    context = prepared.capability_context
    catalog = ToolPresentation(store.registry)
    direct = {tool['function']['name'] for tool in prepared.tools}

    assert len(direct) == 25
    assert {'tool.catalog.search', 'tool.catalog.describe', 'tool.catalog.call',
            'project.scope.revise', 'conversation.reply'} <= direct
    assert 'tool.catalog.select' not in direct
    assert 'conversation.task.control.authorize' not in direct
    assert catalog.search({'query': 'conversation.task.control.authorize'}, context)['items'] == []
    assert not {'workflow.create', 'task.plan.save', 'mcp__fixture__lookup'} & direct
    assert len(json.dumps(prepared.tools, ensure_ascii=False)) < 30_000

    search = catalog.search({'query': 'workflow.create'}, context)
    assert search['items'][0]['id'] == 'workflow.create'
    assert not search['items'][0]['presented']
    assert catalog.search({'query': 'create workflow'}, context)['items'][0]['id'] == 'workflow.create'
    external = catalog.search({'source': 'mcp'}, context)
    assert [item['id'] for item in external['items']] == ['mcp__fixture__lookup']

    described = catalog.describe({'tool_id': 'workflow.create'}, context)
    assert described['parameters']['type'] == 'object'
    assert described['schema_digest']
    from app.v1.contracts import provider_contract_schema
    assert described['parameters'] == provider_contract_schema(
        store.registry.input_schema('workflow.create'))
    bridged = resolve_tool_call('tool.catalog.call',
        {'tool_id': 'mcp__fixture__lookup', 'arguments': {'query': 'fact'}},
        presented_schemas={name: {} for name in direct},
        allowed=prepared.allowed, registry=store.registry, context=context)
    assert bridged.capability == 'mcp__fixture__lookup'
    assert bridged.rejection_type is None
    assert {tool['function']['name'] for tool in prepared.tools} == direct


def test_catalog_rejects_tools_outside_the_run_snapshot(store, tmp_path):
    project = store.create_project('catalog boundary', '', str(tmp_path / 'project'))
    prepared = _prepared_conversation(store, project)
    store.registry.register(CapabilityEntry(
        id='mcp__late__tool', description='Registered after this Run started',
        input_schema={'type': 'object'}, output_schema={'type': 'object'},
        handler=lambda _args, _ctx: {}, toolset=CapabilityToolset.MCP,
    ))
    catalog = ToolPresentation(store.registry)
    assert catalog.search({'query': 'mcp__late__tool'}, prepared.capability_context)['items'] == []
    try:
        catalog.describe({'tool_id': 'mcp__late__tool'}, prepared.capability_context)
    except ValueError as exc:
        assert 'unavailable' in str(exc)
    else:
        raise AssertionError('A Run may not describe a Tool outside its original capability snapshot')


def test_catalog_partial_terms_fallback_is_bounded_and_authorized(store, tmp_path):
    project = store.create_project('search fallback', '', str(tmp_path / 'project'))
    prepared = _prepared_conversation(store, project)
    catalog = ToolPresentation(store.registry)
    context = prepared.capability_context

    exact = catalog.search({'query': 'project.files.read'}, context)
    assert exact['match_mode'] == 'exact'
    assert exact['items'][0]['id'] == 'project.files.read'
    partial = catalog.search({'query': 'read impossibleword', 'limit': 2}, context)
    assert partial['match_mode'] == 'partial_terms'
    assert partial['total'] > 2
    second = catalog.search({'query': 'read impossibleword', 'offset': 2, 'limit': 2}, context)
    assert {item['id'] for item in partial['items']}.isdisjoint(
        item['id'] for item in second['items'])
    assert all(item['id'] != 'conversation.task.control.authorize'
               for item in partial['items'] + second['items'])
    assert catalog.search({'query': 'impossibleword'}, context)['match_mode'] == 'no_match'


def test_limited_conversation_without_catalog_keeps_its_explicit_tools(store, tmp_path):
    project = store.create_project('limited Agent', '', str(tmp_path / 'project'))
    job = store.create_conversation_job(project['id'], 'Read one file', True)
    prepared = AgentRunPreparer(store=store, registry=store.registry,
        runtime_policy=store.policy, platform_policy=store.latest_platform_policy()).prepare(
            AgentRunSpec(kind='conversation', project=project, instruction='',
                instruction_revision=1, context={}, capability_ids=['project.files.read'],
                conversation_job_id=job['id']))
    assert {tool['function']['name'] for tool in prepared.tools} == {
        'project.files.read', 'agent.result.read',
    }


def test_hidden_native_call_is_rejected_and_visible_revision_is_normalized(store, tmp_path):
    project = store.create_project('tool boundary', '', str(tmp_path / 'project'))
    prepared = _prepared_conversation(store, project)
    schemas = {tool['function']['name']: tool['function']['parameters']
               for tool in prepared.tools}
    hidden = resolve_tool_call('workflow.create', {'name': 'unseen'},
        presented_schemas=schemas, allowed=prepared.allowed,
        registry=store.registry, context=prepared.capability_context)
    assert hidden.rejection_type == 'ToolNotPresented'

    visible = resolve_tool_call('project.scope.revise',
        {'expected_revision': '2'}, presented_schemas=schemas,
        allowed=prepared.allowed, registry=store.registry,
        context=prepared.capability_context)
    assert visible.rejection_type is None
    assert visible.arguments['expected_revision'] == 2


def test_bridge_rejects_stale_schema_and_persists_target_identity(store, tmp_path):
    store.registry.register(CapabilityEntry(
        id='mcp__fixture__lookup', description='Look up a fact',
        input_schema={'type': 'object', 'properties': {'query': {'type': 'string'}},
                      'required': ['query'], 'additionalProperties': False},
        output_schema={'type': 'object'}, handler=lambda _args, _ctx: {},
        toolset=CapabilityToolset.MCP,
    ))
    project = store.create_project('replay mapping', '', str(tmp_path / 'project'))
    prepared = _prepared_conversation(store, project)
    schemas = {tool['function']['name']: tool['function']['parameters']
               for tool in prepared.tools}
    stale = resolve_tool_call('tool.catalog.call',
        {'tool_id': 'mcp__fixture__lookup', 'arguments': {'query': 'fact'},
         'schema_digest': 'stale'},
        presented_schemas=schemas, allowed=prepared.allowed,
        registry=store.registry, context=prepared.capability_context)
    assert stale.rejection_type == 'ToolSchemaChanged'
    operation = resolve_tool_call('tool.catalog.call',
        {'tool_id': 'mcp__fixture__lookup', 'arguments': {'query': 'fact'}},
        presented_schemas=schemas, allowed=prepared.allowed,
        registry=store.registry, context=prepared.capability_context)
    from app.v1.domain import AgentToolCall
    ids = store.agents.prepare_operations(project['id'], prepared.run['id'],
        prepared.run['id'], 1,
        [AgentToolCall(id='provider-call', name=operation.capability,
                       arguments=operation.arguments)], [operation])
    saved = store.agents.operation(ids[0])
    assert saved['capability'] == 'mcp__fixture__lookup'
    from app.v1.protocols.agent.resolved_tool_operation import ResolvedToolOperation
    replay = ResolvedToolOperation.model_validate_json(saved['resolution_json'])
    assert replay.provider_name == 'tool.catalog.call'
    assert replay.provider_arguments['tool_id'] == 'mcp__fixture__lookup'


def test_replayed_bridge_operation_reuses_target_receipt(store, tmp_path):
    executions = []
    store.registry.register(CapabilityEntry(
        id='mcp__fixture__count', description='Count one external read',
        input_schema={'type': 'object', 'properties': {'query': {'type': 'string'}},
                      'required': ['query'], 'additionalProperties': False},
        output_schema={'type': 'object', 'properties': {'answer': {'type': 'string'}},
                       'required': ['answer'], 'additionalProperties': False},
        handler=lambda args, _ctx: executions.append(args['query']) or {'answer': 'found'},
        toolset=CapabilityToolset.MCP, side_effect_kind='read',
    ))
    project = store.create_project('bridge replay', '', str(tmp_path / 'project'))
    prepared = _prepared_conversation(store, project)
    schemas = {tool['function']['name']: tool['function']['parameters']
               for tool in prepared.tools}
    resolution = resolve_tool_call('tool.catalog.call',
        {'tool_id': 'mcp__fixture__count', 'arguments': {'query': 'fact'}},
        presented_schemas=schemas, allowed=prepared.allowed,
        registry=store.registry, context=prepared.capability_context)
    from app.v1.domain import AgentToolCall
    from app.v1.agents.agent_tool_execution import AgentToolBatchExecutor
    call = AgentToolCall(id='provider-call', name=resolution.capability,
                         arguments=resolution.arguments)
    ids = store.agents.prepare_operations(project['id'], prepared.run['id'],
        prepared.run['id'], 1, [call], [resolution])
    spec = AgentRunSpec(kind='conversation', project=project, instruction='',
        instruction_revision=1, context={}, capability_ids=prepared.allowed,
        conversation_job_id=prepared.run['conversation_job_id'])
    executor = AgentToolBatchExecutor(store=store, registry=store.registry,
        spec=spec, run_id=prepared.run['id'], allowed=prepared.allowed,
        effect_kinds=prepared.effect_kinds,
        capability_context=prepared.capability_context, logical_ledger=[],
        completion_contract=None)
    first_messages = []
    first = executor.execute([call], first_messages, iteration=1,
        operation_ids=ids, resolutions=[resolution])
    assert first.ledger_entries[0]['capability'] == 'mcp__fixture__count'
    assert first_messages[0]['name'] == 'tool.catalog.call'
    replay_messages = []
    executor.execute([call], replay_messages, iteration=2,
        operation_ids=ids, resolutions=[resolution])
    assert executions == ['fact']
    assert replay_messages[0]['tool_call_id'] == 'provider-call'

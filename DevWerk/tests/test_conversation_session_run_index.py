import json

import pytest

from app.v1.agent_models import AgentRunSpec
from app.v1.agent_run_preparation import AgentRunPreparer
from app.v1.capabilities import build_core_registry
from app.v1.store import V1Store


def begin_conversation_run(store, project):
    return store.begin_agent_run(
        project_id=project['id'], kind='conversation', instruction_revision=1,
        instruction_snapshot='Project Agent', context_snapshot={},
        capabilities=['agent.result.read'], platform_policy=store.latest_platform_policy(),
        runtime_policy=store.policy,
        agent_session_id=store.conversation_agent(project['id'])['logical_id'],
    )


def test_project_session_resumes_with_bounded_prior_failure_evidence(store, tmp_path):
    project = store.create_project('resume failure', '', str(tmp_path / 'project'))
    old = begin_conversation_run(store, project)
    store.record_tool_invocation(
        agent_run_id=old['id'], tool_call_id='failed-plan', capability='workflow.plan.save',
        arguments={'plan':'large'},
        result={'ok':False, 'error':{'type':'PlanRejected', 'message':'missing acceptance check'},
                'output':{'private_large_output':'x' * 100_000}}, ok=False,
    )
    store.finish_agent_run(old['id'], 'failed', '', 'Planning stopped', 1, 1)

    spec = AgentRunSpec(kind='conversation', project=project, instruction='Continue',
                        instruction_revision=1, context={},
                        capability_ids=['agent.result.read'],
                        agent_session_id=store.conversation_agent(project['id'])['logical_id'])
    prepared = AgentRunPreparer(
        store=store, registry=store.registry, runtime_policy=store.policy,
        platform_policy=store.latest_platform_policy()).prepare(spec)
    frames = [json.loads(item['content']) for item in prepared.messages
              if item['role'] == 'user' and 'prior_run_evidence_index' in item['content']]
    assert len(frames) == 1
    prior = frames[0]['prior_run_evidence_index'][0]
    assert prior['agent_run_id'] == old['id']
    assert prior['status'] == 'failed'
    assert prior['recent_receipts'] == [{
        'tool_call_id':'failed-plan', 'capability':'workflow.plan.save', 'ok':False,
        'error_type':'PlanRejected', 'error_message':'missing acceptance check'}]
    assert 'private_large_output' not in json.dumps(frames)
    assert len(json.dumps(frames)) < 2000

    reopened = V1Store(str(store.path), registry=build_core_registry())
    again = reopened.conversation_session_run_index(
        project['id'], spec.agent_session_id, before_run_id=prepared.run['id'])
    assert again[0]['agent_run_id'] == old['id']


def test_project_session_run_index_is_ordered_bounded_and_isolated(store, tmp_path):
    project = store.create_project('one', '', str(tmp_path / 'one'))
    other = store.create_project('two', '', str(tmp_path / 'two'))
    old = []
    for index in range(6):
        run = begin_conversation_run(store, project)
        old.append(run)
        for receipt in range(12):
            store.record_tool_invocation(
                agent_run_id=run['id'], tool_call_id=f'call-{index}-{receipt}',
                capability='project.files.read', arguments={}, result={'ok':True}, ok=True)
    current = begin_conversation_run(store, project)
    index = store.conversation_session_run_index(
        project['id'], store.conversation_agent(project['id'])['logical_id'],
        before_run_id=current['id'])
    assert [entry['agent_run_id'] for entry in index] == [run['id'] for run in reversed(old[-3:])]
    assert all(len(entry['recent_receipts']) == 6 for entry in index)
    assert index[0]['recent_receipts'][0]['tool_call_id'] == 'call-5-6'
    with pytest.raises(ValueError, match='outside'):
        store.conversation_session_run_index(
            other['id'], store.conversation_agent(other['id'])['logical_id'],
            before_run_id=current['id'])

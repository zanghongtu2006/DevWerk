from app.v1.agent import AgentCore
from app.v1.capabilities import build_core_registry
from app.v1.conversations.conversation import ConversationGateway
from app.v1.domain import AgentModelResponse, AgentToolCall
from app.v1.policy import ExecutionLimits, V1RuntimePolicy
from tests.unit.conversation.test_conversation_contract import run_turn


def test_last_conversation_iteration_is_reserved_for_reply(store, tmp_path):
    project = store.create_project('budget close', '', str(tmp_path / 'project'))
    seen = []

    def model(_messages, _tools, **kwargs):
        seen.append(kwargs.get('required_tool_name'))
        if len(seen) == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='list-loops', name='loop.list', arguments={})])
        return AgentModelResponse(text='The installed methods were inspected; no work was created.')

    policy = V1RuntimePolicy(execution=ExecutionLimits(
        agent_max_iterations=2, agent_max_tool_calls=3))
    registry = build_core_registry()
    gateway = ConversationGateway(store, registry,
        agent_core=AgentCore(store, registry, model, policy=policy))
    accepted = run_turn(gateway, project['id'], 'What writing methods are installed?', True)
    job = store.get_conversation_job(accepted['job']['id'])
    assert job['status'] == 'succeeded'
    assert seen == [None, 'conversation.reply']


def test_reserved_final_iteration_rejects_new_business_tool_batch(store, tmp_path):
    project = store.create_project('budget guard', '', str(tmp_path / 'project'))
    seen = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal seen
        seen += 1
        if seen == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='list-loops', name='loop.list', arguments={})])
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id='second-list', name='loop.list', arguments={})])

    policy = V1RuntimePolicy(execution=ExecutionLimits(
        agent_max_iterations=2, agent_max_tool_calls=3))
    registry = build_core_registry()
    gateway = ConversationGateway(store, registry,
        agent_core=AgentCore(store, registry, model, policy=policy))
    accepted = run_turn(gateway, project['id'], 'Inspect the installed methods.', True)
    job = store.get_conversation_job(accepted['job']['id'])
    assert job['status'] == 'failed'
    receipts = store.tool_invocations(project['id'], job['agent_run_id'])
    assert [item['tool_call_id'] for item in receipts] == ['list-loops']

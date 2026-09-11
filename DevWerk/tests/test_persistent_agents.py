from __future__ import annotations

import pytest

from app.v1.execution_control import ExecutionOwnershipLost
from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow
from uuid import uuid4


def assignment(store, project, workflow, *, worker_key="developer", requirement=None):
    task = create_planned_task(store, project["id"], "work " + uuid4().hex)
    if requirement:
        store.agents.attach_task(task["id"], requirement["id"])
    task = store.claim_task(task["id"], "test-worker")
    run = store.begin_run(task, {})
    return task, run, store.agents.assign(task, run, workflow.column("work"), worker_key=worker_key)


def test_worker_idle_then_new_assignment_preserves_identity_and_context(store, tmp_path):
    project = store.create_project("delivery", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    task, run, first = assignment(store, project, workflow)
    store.agents.finish(first, "completed", {"summary": "first output"})
    evidence = store.prepare_terminal_evidence(task, run["id"], "done", {}, None)
    store.finish_run(task, run["id"], {}, "success", "done", terminal="done", terminal_artifact=evidence)
    _, _, second = assignment(store, project, workflow)
    assert first["id"] != second["id"]
    assert first["agent_instance_id"] == second["agent_instance_id"]
    assert first["session_id"] == second["session_id"]


def test_one_worker_has_only_one_active_assignment(store, tmp_path):
    project = store.create_project("delivery", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    _, _, first = assignment(store, project, workflow)
    worker = store.agents.get_worker(project["id"], first["agent_instance_id"])
    assert worker["active_assignment_id"] == first["id"]
    with pytest.raises(ExecutionOwnershipLost):
        store.agents.assert_owner({**first, "generation": first["generation"] + 1})


def test_durable_worker_message_is_not_claimed_as_consumed_on_send(store, tmp_path):
    project = store.create_project("delivery", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    _, _, active = assignment(store, project, workflow)
    message = store.agents.send(project["id"], active["agent_instance_id"], "Use the new label", assignment_id=active["id"])
    assert message["state"] == "pending"
    assert message["consumed_by_run_id"] is None
    assert store.agents.messages(project["id"], active["agent_instance_id"])[0]["id"] == message["id"]


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_late_assignment_input_settles_without_fabricating_consumption(store, tmp_path, status):
    project = store.create_project('late input', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    publish_planned_workflow(store, project['id'], workflow)
    _, _, active = assignment(store, project, workflow)
    worker_id = active['agent_instance_id']
    scoped = store.agents.send(project['id'], worker_id, 'arrived during final model call', assignment_id=active['id'])
    future = store.agents.send(project['id'], worker_id, 'input for the next assignment')
    store.agents.finish(active, status)
    messages = {m['id']: m for m in store.agents.messages(project['id'], worker_id)}
    assert messages[scoped['id']]['state'] == 'failed'
    assert messages[scoped['id']]['consumed_by_run_id'] is None
    assert 'before message consumption' in messages[scoped['id']]['last_error']
    assert messages[future['id']]['state'] == 'pending'
    with pytest.raises(ValueError, match='Assignment has ended'):
        store.agents.send(project['id'], worker_id, 'too late', assignment_id=active['id'])
    with pytest.raises(ValueError, match='Assignment has ended'):
        store.mailbox_service.redeliver(project['id'], scoped['id'], 'retry')


@pytest.mark.parametrize('close_requirement', [False, True])
def test_retirement_settles_unconsumed_worker_input(store, tmp_path, close_requirement):
    project = store.create_project('retirement', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    publish_planned_workflow(store, project['id'], workflow)
    task, run, active = assignment(store, project, workflow)
    worker_id = active['agent_instance_id']
    message = store.agents.send(project['id'], worker_id, 'future input')
    evidence = store.prepare_terminal_evidence(task, run['id'], 'done', {}, None)
    store.finish_run(task, run['id'], {}, 'success', 'done', terminal='done', terminal_artifact=evidence)
    if close_requirement:
        store.agents.close_requirement(project['id'], active['requirement_id'], 'completed')
    else:
        store.agents.set_lifecycle(project['id'], worker_id, 'retired')
    result = store.agents.messages(project['id'], worker_id)[0]
    assert result['state'] == 'failed' and result['consumed_by_run_id'] is None
    assert store.agents.get_worker(project['id'], worker_id)['lifecycle'] == ('available' if close_requirement else 'retired')
    with pytest.raises(ValueError, match='closed' if close_requirement else 'retired'):
        store.mailbox_service.redeliver(project['id'], message['id'], 'retry')

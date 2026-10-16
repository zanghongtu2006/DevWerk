from __future__ import annotations

import hashlib
import json
import sys

import httpx
import pytest

from app.v1.capabilities import (
    CapabilityContext,
    CapabilityEntry,
    _command_run,
    _system_command_run,
    build_core_registry,
    resolve_references,
    validate_task_capability_bindings,
    validate_workflow_capabilities,
)
from app.v1.contracts import ContractError
from app.v1.domain import AcceptanceCheck, CapabilityStep, ExactTaskInputString, Transition
from app.v1.files import ProjectFiles, SystemFiles
from app.v1.mcp_tools import (
    MCPServerConfig,
    MCPToolManager,
    _HTTPTransport,
    _StdioTransport,
    load_mcp_servers,
)
from tests.helpers import (
    agent_workflow,
    create_planned_task,
    publish_initial_workflow,
    publish_planned_workflow,
    sequence_workflow,
    task_plan,
    workflow_plan,
)

def test_acceptance_check_accepts_registered_read_tool_and_rejects_mutating_tool(store):
    store.registry.register(CapabilityEntry(
        id='external.evidence.read', description='Read external evidence',
        input_schema={'type': 'object', 'properties': {'key': {'type': 'string'}},
                      'required': ['key'], 'additionalProperties': False},
        output_schema={'type': 'object'}, handler=lambda args, ctx: {'key': args['key']},
        side_effect_kind='read'))
    workflow = agent_workflow()
    workflow.column('work').acceptance_checks = [AcceptanceCheck(
        key='evidence', capability='external.evidence.read', arguments={'key': 'result'})]
    validate_workflow_capabilities(workflow, store.registry)
    workflow.column('work').acceptance_checks[0].capability = 'project.files.write'
    with pytest.raises(ValueError, match='registered Column capability with none/read/process effect'):
        validate_workflow_capabilities(workflow, store.registry)
    workflow.column('work').acceptance_checks[0].capability = 'external.missing'
    with pytest.raises(ValueError, match='registered Column capability'):
        validate_workflow_capabilities(workflow, store.registry)
    store.registry.register(CapabilityEntry(
        id='external.private.read', description='Private read', input_schema={'type': 'object'},
        output_schema={'type': 'object'}, handler=lambda args, ctx: {},
        side_effect_kind='read', delegable_to_column=False))
    workflow.column('work').acceptance_checks[0].capability = 'external.private.read'
    with pytest.raises(ValueError, match='registered Column capability'):
        validate_workflow_capabilities(workflow, store.registry)


def test_command_tool_advertises_bounded_process_lifecycle(store, tmp_path):
    project = store.create_project('command contract', '', str(tmp_path / 'project'))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project['id'], project=project, store=store)
    description = registry.schemas(['project.command.run'], context)[0]['function']['description']
    assert 'Child processes are cleaned up when the call ends' in description
    assert 'Start-Process or start /b call can exit 0' in description
    assert 'one bounded command' in description


def test_feedback_tool_advertises_notification_without_routing(store, tmp_path):
    project = store.create_project('feedback contract', '', str(tmp_path / 'project'))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project['id'], project=project, store=store)
    description = registry.schemas(['task.feedback.record'], context)[0]['function']['description']
    assert 'Conversation Agent supervision' in description
    assert 'does not select a Workflow edge or block done' in description
    assert 'declared Column outcome' in description


def test_registry_dispatches_generic_file_capability_and_tracks_artifact(store, tmp_path):
    project = store.create_project("caps", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    result = registry.dispatch(
        "project.files.write",
        {"path": "any/domain.txt", "content": "content"},
        context,
    )

    assert result.ok
    assert result.output["file"]["path"] == "any/domain.txt"
    assert (tmp_path / "project" / "any" / "domain.txt").is_file()


def test_project_files_list_recurses_for_trailing_double_star(store, tmp_path):
    project = store.create_project('recursive-list', '', str(tmp_path / 'project'))
    root = tmp_path / 'project'
    (root / 'backend' / 'src').mkdir(parents=True)
    (root / 'backend' / 'pom.xml').write_text('pom', encoding='utf-8')
    (root / 'backend' / 'src' / 'App.java').write_text('class App {}', encoding='utf-8')
    (root / 'frontend').mkdir()
    (root / 'frontend' / 'package.json').write_text('{}', encoding='utf-8')
    result = store.registry.dispatch('project.files.list', {'pattern': 'backend/**'},
        CapabilityContext(project['id'], project, store))
    assert result.ok, result.error
    assert result.output['paths'] == ['backend/pom.xml', 'backend/src/App.java']


def test_column_declared_writable_paths_reject_cross_task_file_mutation(store, tmp_path):
    project = store.create_project("write-boundary", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(
        project_id=project["id"],
        project=project,
        store=store,
        agent_run_id="arun_test",
        writable_paths=("summaries/02.md",),
    )

    rejected = registry.dispatch(
        "project.files.write",
        {"path": "summaries/01.md", "content": "wrong task"},
        context,
    )
    accepted = registry.dispatch(
        "project.files.write",
        {"path": "summaries/02.md", "content": "current task"},
        context,
    )

    assert not rejected.ok
    assert "outside this Column's declared writable paths" in rejected.error["message"]
    assert accepted.ok
    assert not (tmp_path / "project" / "summaries" / "01.md").exists()
    assert (tmp_path / "project" / "summaries" / "02.md").is_file()


def test_registry_measures_text_with_deterministic_metrics(store, tmp_path):
    project = store.create_project("measure", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    registry.dispatch(
        "project.files.write",
        {"path": "sample.txt", "content": "甲 乙\n丙"},
        context,
    )

    result = registry.dispatch("project.files.measure", {"path": "sample.txt"}, context)

    assert result.ok
    assert result.output["utf8_characters"] == 5
    assert result.output["non_whitespace_characters"] == 3
    assert result.output["line_count"] == 2
    assert result.output["size_bytes"] == len("甲 乙\n丙".encode("utf-8"))


def test_registry_rejects_bad_arguments_and_path_escape(store, tmp_path):
    project = store.create_project("caps", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)

    with pytest.raises(ContractError, match="content"):
        registry.dispatch("project.files.write", {"path": "x"}, context)
    with pytest.raises(ValueError, match="escapes"):
        registry.dispatch("project.files.read", {"path": "../outside"}, context)


def test_command_capability_keeps_runtime_process_exit_semantics(store, tmp_path):
    project = store.create_project("command-result", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)

    failed = registry.dispatch(
        "project.command.run",
        {"argv": [sys.executable, "-c", "raise SystemExit(7)"]},
        context,
    )
    accepted = registry.dispatch(
        "project.command.run",
        {
            "argv": [sys.executable, "-c", "raise SystemExit(7)"],
            "success_exit_codes": [7],
        },
        context,
    )

    assert not failed.ok
    assert failed.error["type"] == "CommandFailed"
    assert failed.output["exit_code"] == 7
    assert not accepted.ok
    assert accepted.output["exit_code"] == 7


@pytest.mark.parametrize('command,files', [(_command_run, ProjectFiles),
                                          (_system_command_run, SystemFiles)])
@pytest.mark.parametrize('failure_flag,expected', [('timed_out', 'command timed out'),
                                                   ('output_truncated', 'command output exceeded limit')])
def test_command_failure_receipt_names_timeout_or_truncation(
        store, tmp_path, monkeypatch, command, files, failure_flag, expected):
    project = store.create_project('command-receipt', '', str(tmp_path / 'project'))
    context = CapabilityContext(project_id=project['id'], project=project, store=store)
    output = {'exit_code': 0, 'stdout': 'STARTUP_LOG_' * 1000, 'stderr': '',
              'timed_out': failure_flag == 'timed_out',
              'output_truncated': failure_flag == 'output_truncated'}
    monkeypatch.setattr(files, 'run', lambda self, *args, **kwargs: output)
    result = command({'argv': ['example']}, context)
    assert not result.ok
    assert result.error['type'] == 'CommandFailed'
    assert result.error['message'].startswith(expected)
    assert len(result.error['message']) < 200
    assert 'STARTUP_LOG_' not in result.error['message']
    assert result.output['stdout'].startswith('STARTUP_LOG_')
    assert result.error['exit_code'] == 0
    assert result.error[failure_flag] is True


def test_json_references_are_explicit_and_do_not_evaluate_templates():
    scope = {"input": {"task": {"value": "resolved"}}}
    value = resolve_references(
        {"copied": {"$ref": "/input/task/value"}, "literal": "${input.task.value}"},
        scope,
    )
    assert value == {"copied": "resolved", "literal": "${input.task.value}"}


def test_workflow_publish_capability_creates_initial_workflow_without_loop(store, tmp_path):
    project = store.create_project("workflow", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store, start_task=True)
    workflow = sequence_workflow(name="created during dialogue")
    planned = registry.dispatch("workflow.plan.save", {"plan": workflow_plan(workflow).model_dump(mode="json")}, context)
    assert planned.ok
    published = registry.dispatch(
        "workflow.publish",
        {"workflow_plan_id": planned.output["id"], "workflow": workflow.model_dump(mode="json")},
        context,
    )
    assert published.ok
    assert published.output["workflow_plan_id"] == planned.output["id"]
    assert published.output["source_loop_key"] is None
    assert store.get_workflow(project["id"])["id"] == published.output["id"]


def test_loop_apply_capability_creates_initial_workflow(store, tmp_path):
    project = store.create_project("loop", "", str(tmp_path / "loop-project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store, start_task=True)

    result = registry.dispatch(
        "loop.apply",
        {
            "loop_key": "software.gitlab_devops",
            "bindings": {
                "product_name": "Loop delivery",
                "gitlab_repository": "group/project",
            },
        },
        context,
    )

    assert result.ok
    assert result.output["loop"]["loop_key"] == "software.gitlab_devops"
    assert store.get_workflow(project["id"])["source_loop_key"] == "software.gitlab_devops"


def test_loop_template_can_be_replaced_by_a_smaller_workflow_in_same_project(store, tmp_path):
    project = store.create_project("editable loop", "", str(tmp_path / "editable-loop"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store, start_task=True)
    applied = registry.dispatch("loop.apply", {"loop_key": "software.gitlab_devops",
        "bindings": {"product_name": "demo", "gitlab_repository": "group/project"}}, context)
    assert applied.ok, applied.error
    smaller = sequence_workflow(name="small delivery")
    planned = registry.dispatch("workflow.plan.save",
        {"plan": workflow_plan(smaller).model_dump(mode="json")}, context)
    assert planned.ok, planned.error
    published = registry.dispatch("workflow.publish", {
        "workflow_plan_id": planned.output["id"], "workflow": smaller.model_dump(mode="json")}, context)
    assert published.ok, published.error
    assert [column["key"] for column in published.output["definition"]["columns"]] == ["execute"]
    assert published.output["source_loop_key"] == "software.gitlab_devops"


def test_mailbox_agent_cannot_apply_loop_or_expand_task_graph(store, tmp_path):
    project = store.create_project("mailbox authority", "", str(tmp_path / "mailbox-authority"))
    registry = build_core_registry()
    context = CapabilityContext(
        project_id=project["id"],
        project=project,
        store=store,
        agent_run_id="arun_mailbox",
        start_task=False,
        user_initiated=False,
    )

    result = registry.dispatch(
        "loop.apply",
        {
            "loop_key": "novel.production",
            "bindings": {
                "project_title": "mailbox must not create this",
                "premise": "none",
                "chapter_count": 1,
                "chapter_target_characters": 1000,
                "chapter_max_characters": 2000,
            },
        },
        context,
    )
    assert result.ok is False
    assert result.error['type'] == 'ConversationMutationDisabled'


def test_workflow_rejects_inline_control_character_sensitive_capability_strings():
    workflow = sequence_workflow(content="first\nsecond\n")

    with pytest.raises(ValueError, match="control-character-sensitive"):
        validate_workflow_capabilities(workflow, build_core_registry())


def test_workflow_requires_runtime_reference_for_exact_verification_strings():
    workflow = sequence_workflow()
    column = workflow.columns[0]
    column.executor.steps = [
        CapabilityStep(
            capability="project.files.verify",
            arguments={"path": "result.txt", "expected_content": "matched"},
            save_as="verification",
        )
    ]
    column.executor.completed_outcome = None
    column.executor.outcome_from = "/steps/verification/output/outcome"
    column.transitions = [
        Transition(outcome="matched", target="done"),
        Transition(outcome="mismatch", target="failed"),
        Transition(outcome="failure", target="failed"),
    ]

    with pytest.raises(ValueError, match="must be supplied through a Runtime \\$ref"):
        validate_workflow_capabilities(workflow, build_core_registry())


def test_file_verify_schema_explains_binary_business_routing(store, tmp_path):
    project = store.create_project("verify routing", "", str(tmp_path / "project"))
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    description = build_core_registry().schemas(
        ["project.files.verify"], context
    )[0]["function"]["description"]

    assert "mismatch is a routable business decision" in description
    assert "route matched forward" in description
    assert "route mismatch back" in description


def test_command_schema_explains_exact_output_encoding(store, tmp_path):
    project = store.create_project("command contract", "", str(tmp_path / "project"))
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    description = build_core_registry().schemas(
        ["project.command.run"], context
    )[0]["function"]["description"]

    assert "Preserve the contracted bytes" in description
    assert "PowerShell" not in description


def test_workflow_publish_schema_exposes_live_capability_catalog(store, tmp_path):
    project = store.create_project("catalog", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    schema = registry.schemas(["workflow.publish"], context)[0]["function"]["parameters"]
    description = registry.schemas(["workflow.publish"], context)[0]["function"]["description"]
    assert "<$ref>/input/task/input/...</$ref>" in description
    assert "must not restate Task-owned exact content" in description

    agent_capabilities = schema["$defs"]["AgentExecutor"]["properties"]["capabilities"]
    step_capability = schema["$defs"]["CapabilityStep"]["properties"]["capability"]
    column_schema = schema["$defs"]["ColumnDefinition"]
    assert "capabilities" in schema["$defs"]["AgentExecutor"]["required"]
    assert {"executor", "transitions"}.issubset(column_schema["required"])
    assert "runtime_outcomes" not in column_schema["properties"]
    assert "must not appear" in schema["properties"]["workflow"]["properties"]["columns"]["description"]
    assert agent_capabilities["minItems"] == 1
    assert "project.files.write" in agent_capabilities["items"]["enum"]
    assert "project.files.write" in step_capability["enum"]
    assert "task.create" not in agent_capabilities["items"]["enum"]
    assert "novel.writing" not in agent_capabilities["items"]["enum"]
    arguments_description = schema["$defs"]["CapabilityStep"]["properties"]["arguments"]["description"]
    assert "/input/task/input" in arguments_description
    assert "${input.contract.content}" in arguments_description
    assert "not references" in arguments_description
    assert "Generated helper programs" in arguments_description
    assert "Never inline" in arguments_description
    input_contract_description = column_schema["properties"]["input_contract"]["description"]
    assert "task.input.contract" in input_contract_description
    assert "root-level contract" in input_contract_description


def test_workflow_plan_schema_uses_the_compact_governance_self_check(store, tmp_path):
    project = store.create_project("compact plan", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    schema = registry.schemas(["workflow.plan.save"], context)[0]["function"]["parameters"]
    self_check = schema["$defs"]["WorkflowPlanSelfCheck"]

    expected = {
        "every_task_can_start_at_entry",
        "every_column_applies_to_every_task",
        "columns_are_process_stages_not_work_slices",
        "tasks_are_independently_reviewable",
        "context_handoffs_are_explicit",
        "concurrency_conflicts_are_declared",
        "terminal_and_rework_paths_are_explicit",
    }
    assert set(self_check["properties"]) == expected
    assert set(self_check["required"]) == expected
    assert all(value["const"] is True for value in self_check["properties"].values())
    assert "walkthrough" not in self_check["properties"]

    plan_schema = schema["properties"]["plan"]
    assert {
        "flow_unit",
        "task_contract",
        "lifecycle_walkthrough",
        "wip_group",
        "wip_limit",
    }.issubset(plan_schema["required"])
    assert "execution_mode" in schema["$defs"]["WorkflowColumnPlan"]["required"]


def test_project_column_catalog_exposes_only_delegable_contracts(store, tmp_path):
    project = store.create_project("catalog", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)

    catalog = registry.column_catalog(context)
    by_id = {item["id"]: item for item in catalog}
    assert "project.files.write" in by_id
    assert by_id["project.files.write"]["input_schema"]["required"] == ["path", "content"]
    assert by_id["project.files.write"]["side_effect_kind"] == "write"
    assert "task.create" not in by_id


def test_workflow_publish_rejects_conversation_control_capability_for_column(store, tmp_path):
    project = store.create_project("role-boundary", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    workflow = agent_workflow()
    planned = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    workflow.columns[0].executor.capabilities = ["task.create"]

    with pytest.raises(ContractError, match="input rejected"):
        registry.dispatch(
            "workflow.publish",
            {"workflow_plan_id": planned["id"], "workflow": workflow.model_dump(mode="json")},
            context,
        )


def test_workflow_publish_rejects_invalid_literal_capability_arguments(store, tmp_path):
    project = store.create_project("literal-contract", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    workflow = sequence_workflow()
    workflow.columns[0].executor.steps[0].arguments["append"] = True
    planned = store.create_workflow_plan(project["id"], workflow_plan(workflow))

    with pytest.raises(ContractError, match="Additional properties are not allowed"):
        registry.dispatch(
            "workflow.publish",
            {
                "workflow_plan_id": planned["id"],
                "workflow": workflow.model_dump(mode="json"),
            },
            context,
        )


def test_workflow_publish_rejects_invalid_structure_around_runtime_reference(store, tmp_path):
    project = store.create_project("referenced-contract", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    workflow.columns[0].executor.steps = [
        CapabilityStep(
            capability="project.command.run",
            arguments={
                "argv": ["powershell", "-NoProfile", "-Command", "Write-Output ok"],
                "path": {"$ref": "/input/task/input/contract/path"},
            },
        )
    ]
    planned = store.create_workflow_plan(project["id"], workflow_plan(workflow))

    with pytest.raises(ValueError, match="Additional properties are not allowed.*path"):
        publish_initial_workflow(store, project["id"], workflow, planned["id"])


def test_workflow_publish_requires_registry_decision_outcome_provenance(store, tmp_path):
    project = store.create_project("decision provenance", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    workflow = sequence_workflow()
    column = workflow.columns[0]
    column.executor.steps = [
        CapabilityStep(
            capability="project.files.verify",
            arguments={
                "path": "result.txt",
                "expected_content": {"$ref": "/input/task/input/contract/content"},
            },
            save_as="verification",
        )
    ]
    column.executor.completed_outcome = "matched"
    column.executor.outcome_from = None
    column.transitions = [
        Transition(outcome="matched", target="done"),
        Transition(outcome="failure", target="failed"),
    ]
    planned = store.create_workflow_plan(project["id"], workflow_plan(workflow))

    with pytest.raises(ValueError, match="must derive its sequence outcome"):
        registry.dispatch(
            "workflow.publish",
            {
                "workflow_plan_id": planned["id"],
                "workflow": workflow.model_dump(mode="json"),
            },
            context,
        )

    column.executor.completed_outcome = None
    column.executor.outcome_from = "/steps/verification/output/outcome"
    column.transitions.insert(1, Transition(outcome="mismatch", target="failed"))
    accepted = registry.dispatch(
        "workflow.publish",
        {
            "workflow_plan_id": planned["id"],
            "workflow": workflow.model_dump(mode="json"),
        },
        context,
    )

    assert accepted.ok


@pytest.mark.parametrize(
    ("capability", "save_as"),
    [
        ("system.noop", "noop"),
        ("project.files.write", "written"),
    ],
)
def test_workflow_publish_rejects_outcome_pointer_not_proven_by_output_schema(
    store,
    tmp_path,
    capability,
    save_as,
):
    project = store.create_project(
        f"invalid outcome source {capability}",
        "",
        str(tmp_path / capability.replace(".", "-")),
    )
    publish_planned_workflow(store, project["id"], sequence_workflow())
    registry = build_core_registry()
    context = CapabilityContext(
        project_id=project["id"],
        project=project,
        store=store,
    )
    workflow = sequence_workflow()
    column = workflow.columns[0]
    arguments = (
        {}
        if capability == "system.noop"
        else {"path": "result.txt", "content": "done"}
    )
    column.executor.steps = [
        CapabilityStep(
            capability=capability,
            arguments=arguments,
            save_as=save_as,
        )
    ]
    column.executor.completed_outcome = None
    column.executor.outcome_from = f"/steps/{save_as}/output/outcome"
    planned = store.create_workflow_plan(
        project["id"],
        workflow_plan(workflow),
    )

    with pytest.raises(ValueError, match="does not exist in the Capability output schema"):
        registry.dispatch(
            "workflow.publish",
            {
                "workflow_plan_id": planned["id"],
                "workflow": workflow.model_dump(mode="json"),
            },
            context,
        )


def test_workflow_publish_canonicalizes_exact_tagged_and_rejects_forward_runtime_references(store, tmp_path):
    registry = build_core_registry()

    tagged_project = store.create_project(
        "tagged reference",
        "",
        str(tmp_path / "tagged"),
    )
    publish_planned_workflow(store, tagged_project["id"], sequence_workflow())
    tagged_context = CapabilityContext(
        project_id=tagged_project["id"],
        project=tagged_project,
        store=store,
    )
    tagged = sequence_workflow()
    tagged.columns[0].executor.steps[0].arguments["path"] = (
        "<$ref>/input/task/input/contract/path</$ref>"
    )
    tagged_plan = store.create_workflow_plan(
        tagged_project["id"],
        workflow_plan(tagged),
    )
    tagged_result = registry.dispatch(
        "workflow.publish",
        {
            "workflow_plan_id": tagged_plan["id"],
            "workflow": tagged.model_dump(mode="json"),
        },
        tagged_context,
    )
    assert tagged_result.ok
    published_arguments = tagged_result.output["definition"]["columns"][0]["executor"]["steps"][0]["arguments"]
    assert published_arguments["path"] == {"$ref": "/input/task/input/contract/path"}

    forward_project = store.create_project(
        "forward reference",
        "",
        str(tmp_path / "forward"),
    )
    publish_planned_workflow(store, forward_project["id"], sequence_workflow())
    forward_context = CapabilityContext(
        project_id=forward_project["id"],
        project=forward_project,
        store=store,
    )
    forward = sequence_workflow()
    column = forward.columns[0]
    column.executor.steps = [
        CapabilityStep(
            capability="project.files.read",
            arguments={"path": {"$ref": "/steps/verification/output/outcome"}},
            save_as="read",
        ),
        CapabilityStep(
            capability="project.files.verify",
            arguments={
                "path": "result.txt",
                "expected_content": {"$ref": "/input/task/input/contract/content"},
            },
            save_as="verification",
        ),
    ]
    column.executor.completed_outcome = None
    column.executor.outcome_from = "/steps/verification/output/outcome"
    column.transitions = [
        Transition(outcome="matched", target="done"),
        Transition(outcome="mismatch", target="failed"),
        Transition(outcome="failure", target="failed"),
    ]
    forward_plan = store.create_workflow_plan(
        forward_project["id"],
        workflow_plan(forward),
    )
    with pytest.raises(ValueError, match="must select an earlier saved step"):
        registry.dispatch(
            "workflow.publish",
            {
                "workflow_plan_id": forward_plan["id"],
                "workflow": forward.model_dump(mode="json"),
            },
            forward_context,
        )


def test_task_binding_preflight_rejects_contradictory_or_wrong_typed_expectations():
    registry = build_core_registry()
    workflow = sequence_workflow()
    column = workflow.columns[0]
    column.executor.steps = [
        CapabilityStep(
            capability="project.files.verify",
            arguments={
                "path": {"$ref": "/input/task/input/contract/path"},
                "expected_content": {"$ref": "/input/task/input/contract/content"},
                "expected_sha256": {"$ref": "/input/task/input/contract/sha256"},
                "expected_size_bytes": {"$ref": "/input/task/input/contract/size"},
            },
            save_as="verification",
        )
    ]
    column.executor.completed_outcome = None
    column.executor.outcome_from = "/steps/verification/output/outcome"
    column.transitions = [
        Transition(outcome="matched", target="done"),
        Transition(outcome="mismatch", target="failed"),
        Transition(outcome="failure", target="failed"),
    ]

    with pytest.raises(ValueError, match="contradicts"):
        validate_task_capability_bindings(
            workflow,
            registry,
            {
                "contract": {
                    "path": "result.txt",
                    "content": "exact",
                    "sha256": hashlib.sha256(b"exact\n").hexdigest(),
                    "size": 6,
                }
            },
        )

    with pytest.raises(ValueError, match="not of type 'integer'"):
        validate_task_capability_bindings(
            workflow,
            registry,
            {
                "contract": {
                    "path": "result.txt",
                    "content": "exact\n",
                    "sha256": hashlib.sha256(b"exact\n").hexdigest(),
                    "size": "6",
                }
            },
        )

    validate_task_capability_bindings(
        workflow,
        registry,
        {
            "contract": {
                "path": "result.txt",
                "content": "exact\n",
                "sha256": hashlib.sha256(b"exact\n").hexdigest(),
                "size": 6,
            }
        },
    )


def test_task_binding_preflight_materializes_structured_exact_strings():
    registry = build_core_registry()
    workflow = sequence_workflow()
    column = workflow.columns[0]
    column.executor.steps = [
        CapabilityStep(
            capability="project.files.verify",
            arguments={
                "path": {"$ref": "/input/task/input/contract/path"},
                "expected_content": {"$ref": "/input/task/input/contract/content"},
                "expected_sha256": {"$ref": "/input/task/input/contract/sha256"},
                "expected_size_bytes": {"$ref": "/input/task/input/contract/size"},
            },
            save_as="verification",
        )
    ]
    column.executor.completed_outcome = None
    column.executor.outcome_from = "/steps/verification/output/outcome"
    column.transitions = [
        Transition(outcome="matched", target="done"),
        Transition(outcome="mismatch", target="failed"),
        Transition(outcome="failure", target="failed"),
    ]
    exact_content = "ROUND7_A_OK\n"
    exact_digest = hashlib.sha256(exact_content.encode("utf-8")).hexdigest()
    exact_strings = {
        "/contract/path": "acceptance.txt",
        "/contract/content": exact_content,
        "/contract/sha256": exact_digest,
    }

    normalized = validate_task_capability_bindings(
        workflow,
        registry,
        {
            "contract": {
                "path": "acceptance.txt",
                "content": "ROUND7_A_OK",
                "sha256": exact_digest,
                "size": 12,
            }
        },
        exact_strings=exact_strings,
    )
    assert normalized["contract"]["content"] == exact_content
    assert normalized["contract"]["content"].endswith("\n")

    with pytest.raises(ValueError, match="must be declared"):
        validate_task_capability_bindings(
            workflow,
            registry,
            {
                "contract": {
                    "path": "acceptance.txt",
                    "content": exact_content,
                    "sha256": exact_digest,
                    "size": 12,
                }
            },
            exact_strings={
                "/contract/path": "acceptance.txt",
                "/contract/sha256": exact_digest,
            },
        )


def test_task_create_persists_the_plan_materialized_exact_string(store, tmp_path):
    project = store.create_project("exact Task input", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(
        project_id=project["id"],
        project=project,
        store=store,
        start_task=True,
    )
    workflow = sequence_workflow()
    column = workflow.columns[0]
    column.executor.steps = [
        CapabilityStep(
            capability="project.files.write",
            arguments={
                "path": {"$ref": "/input/task/input/contract/path"},
                "content": {"$ref": "/input/task/input/contract/content"},
            },
            save_as="written",
        ),
        CapabilityStep(
            capability="project.files.verify",
            arguments={
                "path": {"$ref": "/input/task/input/contract/path"},
                "expected_content": {"$ref": "/input/task/input/contract/content"},
                "expected_size_bytes": 12,
                "expected_ends_with_newline": True,
            },
            save_as="verification",
        ),
    ]
    column.executor.completed_outcome = None
    column.executor.outcome_from = "/steps/verification/output/outcome"
    column.transitions = [
        Transition(outcome="matched", target="done"),
        Transition(outcome="mismatch", target="failed"),
        Transition(outcome="failure", target="failed"),
    ]
    stored_plan = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    revision = publish_initial_workflow(store, project["id"], workflow, stored_plan["id"])
    exact_strings = [
        ExactTaskInputString(
            pointer="/contract/path",
            escaped_value="acceptance.txt",
        ),
        ExactTaskInputString(
            pointer="/contract/content",
            escaped_value=r"ROUND9_A_OK\n",
        ),
    ]
    planned_tasks = store.create_task_plan(
        project["id"],
        task_plan(
            revision["id"],
            workflow,
            title="materialize exact string",
            input_data={"contract": {"path": "acceptance.txt", "content": "ROUND9_A_OK"}},
            exact_input_strings=exact_strings,
        ),
    )

    created = registry.dispatch(
        "task.create",
        {
            "task_plan_id": planned_tasks["id"],
            "proposed_task_ref": "primary",
        },
        context,
    )

    assert created.ok
    assert created.output["input"]["contract"]["content"] == "ROUND9_A_OK\n"
    assert store.get_task(created.output["id"])["input"]["contract"]["content"] == "ROUND9_A_OK\n"


def test_conversation_agent_control_tools_preserve_terminal_immutability_and_rerun(store, tmp_path):
    project = store.create_project("control", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "controlled")
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)

    failed_route = registry.dispatch("task.fail", {"task_id": task["id"], "reason": "operator intervention"}, context)
    assert failed_route.ok
    assert failed_route.output["current_column"] == "failed"
    with pytest.raises(ValueError, match="terminal Tasks are immutable"):
        registry.dispatch("task.retry", {"task_id": task["id"], "clear_context": True}, context)
    rerun = registry.dispatch("task.rerun", {"task_id": task["id"]}, context)
    assert rerun.ok
    assert rerun.output["id"] != task["id"]
    assert rerun.output["rerun_of_task_id"] == task["id"]
    assert rerun.output["status"] == "pending"
    scheduling = registry.dispatch(
        "scheduling.decide",
        {"task_id": rerun.output["id"], "state": "admitted"},
        context,
    )
    assert scheduling.ok
    inspected = registry.dispatch("task.inspect", {"task_id": rerun.output["id"]}, context)
    assert inspected.ok
    assert inspected.output["scheduling"]["dispatch_eligible"] is True
    assert inspected.output["scheduling"]["dependencies"] == []


def test_conversation_diagnostics_do_not_repeat_persisted_runtime_context(store, tmp_path):
    project = store.create_project("compact diagnostics", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "inspect compactly")
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)

    inspected = registry.dispatch("task.inspect", {"task_id": task["id"]}, context)

    assert inspected.ok
    assert inspected.output["task"]["id"] == task["id"]
    assert "input" not in inspected.output["task"]
    assert "context" not in inspected.output["task"]
    assert "readiness" not in inspected.output["task"]

    with store.tx(immediate=True) as db:
        store._event(
            db,
            project["id"],
            None,
            None,
            "conversation.progress",
            {
                "kind": "tool_result",
                "iteration": 3,
                "capability": "task.inspect",
                "content": "large persisted payload must not be replayed",
            },
        )
    events = registry.dispatch("event.list", {"after": 0, "limit": 100}, context)

    assert events.ok
    progress = next(item for item in events.output if item["type"] == "conversation.progress")
    assert progress["data"] == {
        "kind": "tool_result",
        "iteration": 3,
        "capability": "task.inspect",
    }


# ---------------------------------------------------------------------------
# MCP tool bridge
# ---------------------------------------------------------------------------

SERVER = r'''
import json,sys
for line in sys.stdin:
    message=json.loads(line)
    if 'id' not in message:
        continue
    method=message['method']
    if method=='initialize':
        result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
    elif method=='tools/list':
        result={'tools':[{'name':'echo','description':'Return the supplied value',
            'inputSchema':{'type':'object','required':['value'],
                'properties':{'value':{'type':'string'}},'additionalProperties':False},
            'annotations':{'readOnlyHint':True}}]}
    elif method=='tools/call':
        result={'content':[{'type':'text','text':'echo:'+message['params']['arguments']['value']}]}
    else:
        result={}
    print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
'''


def test_mcp_stdio_tool_discovered_and_dispatched_through_registry(store, tmp_path):
    script = tmp_path / 'mcp_fixture.py'
    script.write_text(SERVER, encoding='utf-8')
    config_path = tmp_path / 'mcp-servers.yaml'
    config_path.write_text('mcp_servers:\n  fixture:\n    transport: stdio\n'
                           f"    command: '{sys.executable}'\n"
                           f"    args: ['{script}']\n", encoding='utf-8')
    registry = build_core_registry()
    manager = MCPToolManager(load_mcp_servers(config_path), registry)
    try:
        assert manager.discover() == {'fixture':['mcp__fixture__echo']}
        project = store.create_project('mcp', '', str(tmp_path / 'project'))
        ctx = CapabilityContext(project['id'], project, store)
        entry = registry.resolve(['mcp__fixture__echo'], ctx)[0]
        assert entry.side_effect_kind == 'read'
        result = registry.dispatch('mcp__fixture__echo', {'value':'hello'}, ctx)
        assert result.ok, result.error
        assert result.output['content'] == [{'type':'text','text':'echo:hello'}]
    finally:
        manager.close()


def test_mcp_http_transport_accepts_streamable_sse_response():
    seen = []

    def respond(request):
        payload = json.loads(request.content)
        seen.append((payload['method'], request.headers.get('mcp-session-id')))
        if payload['method'] == 'notifications/initialized':
            return httpx.Response(202)
        if payload['method'] == 'initialize':
            return httpx.Response(200, json={'jsonrpc':'2.0','id':payload['id'],
                'result':{'protocolVersion':'2025-06-18'}}, headers={'mcp-session-id':'session-1'})
        event = json.dumps({'jsonrpc':'2.0','id':payload['id'],
            'result':{'tools':[{'name':'hello'}]}})
        return httpx.Response(200, text='event: message\ndata: '+event+'\n\n',
                              headers={'content-type':'text/event-stream'})

    connection = _HTTPTransport(MCPServerConfig(transport='http', url='http://mcp.test/mcp'))
    connection.client.close()
    connection.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert connection.request('tools/list', {})['tools'][0]['name'] == 'hello'
        assert seen == [('initialize', None), ('notifications/initialized', 'session-1'),
                        ('tools/list', 'session-1')]
    finally:
        connection.close()


def test_mcp_configuration_rejects_incomplete_server(tmp_path):
    path = tmp_path / 'mcp-servers.yaml'
    path.write_text('mcp_servers:\n  broken:\n    transport: stdio\n', encoding='utf-8')
    with pytest.raises(ValueError):
        load_mcp_servers(path)


def test_mcp_http_expired_session_reconnects_discovery_but_not_mutation():
    calls = []
    initialized = 0

    def respond(request):
        nonlocal initialized
        payload = json.loads(request.content)
        method = payload['method']
        calls.append((method, request.headers.get('mcp-session-id')))
        if method == 'initialize':
            initialized += 1
            return httpx.Response(200, json={'jsonrpc':'2.0','id':payload['id'],
                'result':{'protocolVersion':'2025-06-18'}},
                headers={'mcp-session-id':f'session-{initialized}'})
        if method == 'notifications/initialized':
            return httpx.Response(202)
        if len([call for call in calls if call[0] == method]) == 1:
            return httpx.Response(404)
        return httpx.Response(200, json={'jsonrpc':'2.0','id':payload['id'],
            'result':{'tools':[]}})

    connection = _HTTPTransport(MCPServerConfig(transport='http', url='http://mcp.test/mcp'))
    connection.client.close()
    connection.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert connection.request('tools/list', {}) == {'tools':[]}
        assert initialized == 2
        with pytest.raises(ConnectionError, match='expired'):
            connection.request('tools/call', {'name':'write', 'arguments':{}})
        assert len([call for call in calls if call[0] == 'tools/call']) == 1
        assert not connection.initialized
    finally:
        connection.close()


def test_mcp_stdio_timeout_discards_old_process_and_reconnects(tmp_path):
    script = tmp_path / 'slow_server.py'
    script.write_text('''import json, sys, time
for line in sys.stdin:
    message = json.loads(line)
    if message.get('method') == 'tools/call':
        time.sleep(5)
    if 'id' in message:
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'tools':[]}}), flush=True)
''', encoding='utf-8')
    connection = _StdioTransport(MCPServerConfig(
        transport='stdio', command=sys.executable, args=[str(script)], timeout_seconds=1))
    try:
        assert connection.request('tools/list', {}) == {'tools':[]}
        previous = connection.process
        with pytest.raises(TimeoutError):
            connection.request('tools/call', {'name':'slow','arguments':{}})
        assert connection.process is None
        assert connection.request('tools/list', {}) == {'tools':[]}
        assert connection.process is not previous
    finally:
        connection.close()

from __future__ import annotations

from app.v1.domain import (
    AgentExecutor,
    CapabilitySequenceExecutor,
    CapabilityStep,
    ColumnDefinition,
    OrchestrationColumnPlan,
    OrchestrationPlan,
    OrchestrationSelfCheck,
    OrchestrationTaskPlan,
    OrchestrationWalkthroughStep,
    Transition,
    WorkflowDefinition,
)


def readiness(**overrides):
    value = {
        "decision": "dispatch",
        "objective": "Deliver the requested result",
        "scope": ["requested work"],
        "non_scope": [],
        "deliverables": ["verifiable result"],
        "acceptance_criteria": ["workflow reaches an explicit terminal"],
        "dependencies_checked": True,
        "dependencies": [],
        "conflict_domains": [],
        "risks": [],
        "reason_summary": "The work benefits from tracked Workflow execution.",
        "next_review_at": None,
    }
    value.update(overrides)
    return value


def sequence_workflow(*, name: str = "deterministic", path: str = "result.txt", content: str = "done") -> WorkflowDefinition:
    return WorkflowDefinition(
        name=name,
        entry="execute",
        columns=[
            ColumnDefinition(
                key="execute",
                name="Execute",
                executor=CapabilitySequenceExecutor(
                    steps=[
                        CapabilityStep(
                            capability="project.files.write",
                            arguments={"path": path, "content": content},
                        )
                    ]
                ),
                transitions=[
                    Transition(outcome="success", target="done"),
                    Transition(outcome="failure", target="failed"),
                ],
            ),
        ],
    )


def agent_workflow(*, instruction: str = "Use the declared capabilities and submit a contract-valid completion.") -> WorkflowDefinition:
    return WorkflowDefinition(
        name="agent work",
        entry="work",
        columns=[
            ColumnDefinition(
                key="work",
                name="Work",
                instruction=instruction,
                executor=AgentExecutor(
                    capabilities=["project.files.write", "project.files.read"],
                ),
                output_contract={
                    "type": "object",
                    "required": ["delivered"],
                    "properties": {"delivered": {"type": "boolean"}},
                    "additionalProperties": True,
                },
                transitions=[
                    Transition(outcome="success", target="done"),
                    Transition(outcome="failure", target="failed"),
                ],
            ),
        ],
    )


def orchestration_plan(workflow: WorkflowDefinition, *, task_ref: str = "primary") -> OrchestrationPlan:
    columns = []
    for column in workflow.columns:
        executor = column.executor
        entry_evidence = ["task is ready at the workflow entry or prior transition"]
        exit_evidence = ["declared output contract and transition outcome"]
        columns.append(OrchestrationColumnPlan(
            key=column.key,
            responsibility=f"Complete the {column.name} process stage.",
            execution_mode=executor.kind,
            entry_evidence=entry_evidence,
            exit_evidence=exit_evidence,
            context_boundary="Use only selected project, task, upstream output, and artifact context.",
            review_or_rework_role="Produce independently reviewable evidence and follow declared rework transitions.",
        ))

    workflow_columns = {column.key: column for column in workflow.columns}

    def find_path(key: str, terminal: str, visited: set[str]):
        if key in visited:
            return None
        column = workflow_columns[key]
        for transition in column.transitions:
            if transition.target == terminal:
                return [(column, transition)]
        for transition in column.transitions:
            if transition.target in {workflow.terminals.success, workflow.terminals.failure}:
                continue
            tail = find_path(transition.target, terminal, visited | {key})
            if tail is not None:
                return [(column, transition), *tail]
        return None

    representative_path = (
        find_path(workflow.entry, workflow.terminals.success, set())
        or find_path(workflow.entry, workflow.terminals.failure, set())
    )
    assert representative_path is not None

    return OrchestrationPlan(
        intent_summary="Deliver one domain-neutral, independently reviewable result.",
        completion_definition="The workflow reaches done or failed with durable evidence.",
        flow_unit="One independently reviewable requested result.",
        lifecycle_summary="Each task traverses the same declared process columns.",
        entry_meaning="The task is ready to enter the first process stage.",
        terminal_meaning="Done is accepted delivery; failed is explicit unsuccessful closure.",
        columns=columns,
        task_portfolio=[OrchestrationTaskPlan(
            proposed_task_ref=task_ref,
            objective="Deliver the requested result",
            workflow_fit="The task can start at entry and every column applies as a process stage.",
            agent_execution=(
                "required"
                if any(isinstance(column.executor, AgentExecutor) for column in workflow.columns)
                else "forbidden"
            ),
            review_scope="Review the task deliverable and declared evidence.",
            retry_scope="Retry only the failed process stage or create an explicit rerun.",
        )],
        representative_task_ref=task_ref,
        representative_task_walkthrough=[
            OrchestrationWalkthroughStep(
                column_key=column.key,
                receives=["representative Task facts and declared upstream evidence"],
                action=f"Perform the {column.name} stage for the representative Task.",
                produces=["declared stage output and durable evidence"],
                completion_evidence=["output contract and selected transition are satisfied"],
                outcome=transition.outcome,
            )
            for column, transition in representative_path
        ],
        wip_group="default",
        wip_limit=1,
        wip_decision="Admit the task when dependencies and conflict domains permit.",
        progress_evidence=["column completion and artifact events"],
        intervention_conditions=["stalled, failed, or acceptance evidence missing"],
        self_check=OrchestrationSelfCheck(
            every_task_can_start_at_entry=True,
            every_column_applies_to_every_task=True,
            columns_are_process_stages_not_work_slices=True,
            tasks_are_independently_reviewable=True,
            context_handoffs_are_explicit=True,
            concurrency_conflicts_are_declared=True,
            terminal_and_rework_paths_are_explicit=True,
        ),
    )


def publish_initial_workflow(store, project_id: str, workflow: WorkflowDefinition, plan_id: str):
    """Test-only initial publication with explicit Loop provenance."""
    return store._publish_workflow_revision(
        project_id,
        workflow,
        plan_id,
        initial_loop={
            "loop_key": "tests.dynamic",
            "version": "1.0.0",
            "digest": "0" * 64,
        },
    )


def publish_planned_workflow(store, project_id: str, workflow: WorkflowDefinition):
    plan = store.create_orchestration_plan(project_id, orchestration_plan(workflow))
    try:
        store.get_workflow(project_id)
    except KeyError:
        revision = publish_initial_workflow(store, project_id, workflow, plan["id"])
    else:
        revision = store.publish_workflow(project_id, workflow, plan["id"])
    return plan, revision


def create_planned_task(store, project_id: str, title: str, brief: str = "", input_data=None, readiness_data=None, *, plan_id: str | None = None, task_ref: str = "primary", **kwargs):
    active = store.get_workflow(project_id)
    return store.create_task(
        project_id, title, brief, input_data or {}, readiness_data or readiness(),
        orchestration_plan_id=plan_id or active["orchestration_plan_id"],
        proposed_task_ref=task_ref,
        **kwargs,
    )

"""Typed output contracts for the core planning and task capabilities."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from app.v1.domain import TaskPlan, WorkflowAuthoringRequest, WorkflowDefinition, WorkflowPlan
from app.v1.execution.states import TaskStatus


class _ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowCreateInput(_ToolInput):
    workflow: WorkflowAuthoringRequest = Field(description="Complete directed Workflow authoring request")


class WorkflowPublishInput(_ToolInput):
    workflow_plan_id: str = Field(min_length=1, description="Saved Workflow Plan ID")
    workflow: WorkflowDefinition = Field(description="Complete directed Workflow definition")


class WorkflowPlanSaveInput(_ToolInput):
    plan: WorkflowPlan = Field(description="Reusable Workflow Plan")


class TaskPlanSaveInput(_ToolInput):
    plan: TaskPlan = Field(description="Concrete Task Plan")


class TaskPlanValidateInput(_ToolInput):
    plan: TaskPlan = Field(description="Concrete Task Plan; use the same value with task.plan.save after validation")


class SavedPlanReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, description="Persisted plan ID")
    project_id: str = Field(description="Owning Project ID")
    schema_version: str = Field(description="Plan schema version")
    plan_hash: str = Field(description="Frozen plan content hash")
    created_at: str = Field(description="Persistence timestamp")
    requirement_id: str | None = Field(default=None, description="Owning Requirement, when bound")


class WorkflowPlanRecord(SavedPlanReceipt):
    plan: WorkflowPlan = Field(description="Persisted reusable Workflow Plan")


class TaskPlanRecord(SavedPlanReceipt):
    workflow_revision_id: str = Field(min_length=1, description="Target Workflow revision")
    objective: str = Field(description="Concrete Task Plan objective")
    requirement_revision: int | None = Field(default=None, description="Bound Requirement revision")
    plan: TaskPlan = Field(description="Persisted concrete Task Plan")


class WorkflowPlanListReceipt(RootModel[list[WorkflowPlanRecord]]):
    pass


class TaskPlanListReceipt(RootModel[list[TaskPlanRecord]]):
    pass


class WorkflowPublishNextArguments(BaseModel):
    workflow_plan_id: str = Field(min_length=1, description="Saved Workflow Plan ID to publish")


class WorkflowPublishNextAction(BaseModel):
    capability: Literal["workflow.publish"] = Field(default="workflow.publish", description="Next tool")
    arguments: WorkflowPublishNextArguments = Field(description="Next tool arguments")
    required_input: str = Field(description="Missing authoring input")


class WorkflowPlanSaveReceipt(WorkflowPlanRecord):
    published: Literal[False]
    next_action: WorkflowPublishNextAction

    @classmethod
    def from_saved_plan(cls, value: dict[str, Any]) -> "WorkflowPlanSaveReceipt":
        saved = WorkflowPlanRecord.model_validate(value)
        return cls(
            id=saved.id,
            project_id=saved.project_id,
            schema_version=saved.schema_version,
            plan_hash=saved.plan_hash,
            created_at=saved.created_at,
            requirement_id=saved.requirement_id,
            plan=saved.plan,
            published=False,
            next_action=WorkflowPublishNextAction(
                arguments=WorkflowPublishNextArguments(workflow_plan_id=saved.id),
                required_input="Add the complete Workflow definition implementing this Plan.",
            ),
        )


class TaskCreateNextArguments(BaseModel):
    task_plan_id: str = Field(min_length=1, description="Saved Task Plan ID")
    proposed_task_ref: str = Field(min_length=1, description="Task ref within the saved Plan")


class TaskCreateNextAction(BaseModel):
    capability: Literal["task.create"] = Field(default="task.create", description="Next tool")
    arguments: TaskCreateNextArguments = Field(description="Next tool arguments")


class TaskPlanSaveReceipt(TaskPlanRecord):
    next_action: TaskCreateNextAction | None = None

    @classmethod
    def from_saved_plan(cls, value: dict[str, Any], proposed_task_ref: str) -> "TaskPlanSaveReceipt":
        saved = cls.model_validate(value)
        return saved.model_copy(update={
            "next_action": TaskCreateNextAction(arguments=TaskCreateNextArguments(
                task_plan_id=saved.id,
                proposed_task_ref=proposed_task_ref,
            )),
        })


class WorkflowReceipt(BaseModel):
    id: str = Field(min_length=1)
    workflow_plan_id: str = Field(min_length=1)
    definition_hash: str = Field(min_length=1)
    definition: WorkflowDefinition
    source_loop_key: str | None = None


class ValidationDiagnostic(BaseModel):
    path: str
    message: str
    expected: str | None = None


class WorkflowAcceptanceConfigureReceipt(BaseModel):
    id: str
    definition_hash: str
    workflow_plan_id: str
    changed_columns: list[str]
    details_capability: Literal["workflow.inspect"] = "workflow.inspect"
    ready_for_task_plan: bool
    remaining_diagnostic_count: int = Field(ge=0)
    remaining_diagnostics: list[ValidationDiagnostic]


class ValidationReceipt(BaseModel):
    valid: bool
    diagnostics: list[ValidationDiagnostic]
    legal_columns: list[str] | None = None
    deferred_command_checks: list["DeferredCommandCheck"] = Field(default_factory=list)


class EntryEvidenceCandidate(BaseModel):
    execution_key: str
    path: str | None = None
    execution_grant_id: str


class EntryEvidenceDirectory(BaseModel):
    kind: str
    candidate_ids: list[str] | None = None
    input_pointer: str | None = None
    candidates: list[EntryEvidenceCandidate] | None = None


class AuthorizedScopeRecord(BaseModel):
    grant_id: str
    user_request: str
    original_user_request: str | None = None


class TaskPlanContractReceipt(BaseModel):
    workflow_revision_id: str | None
    next_action: str
    flow_unit: str | None = None
    input_schema: dict[str, JsonValue] | None = None
    identity_pointer: str | None = None
    entry_evidence: dict[str, EntryEvidenceDirectory] | None = None
    authorized_scope: list[AuthorizedScopeRecord] | None = None


class DeferredCommandCheck(BaseModel):
    column: str
    check_key: str
    validation: str


class AcceptanceLaunchDiagnostics(BaseModel):
    diagnostics: list[ValidationDiagnostic]
    deferred_command_checks: list[DeferredCommandCheck]


class TaskMaterializationReceipt(BaseModel):
    created_task_ids: list[str]
    reused_task_ids: list[str]


class TaskDispatchReceipt(BaseModel):
    owner: Literal["runtime"] = "runtime"
    mode: Literal["asynchronous"] = "asynchronous"
    next_action: str = (
        "Report the actual Task IDs with conversation.reply. Runtime schedules Column Workers automatically; "
        "pending does not mean started. Do not start a Worker manually or implement the Task deliverables yourself."
    )


class MaterializedTaskRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    materialization: TaskMaterializationReceipt | None = None
    dispatch: TaskDispatchReceipt | None = None

    @classmethod
    def from_task(cls, task: dict[str, Any], materialization: TaskMaterializationReceipt) -> "MaterializedTaskRow":
        result = cls.model_validate(task)
        result.materialization = materialization
        return result


class TaskReceipt(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1)
    workflow_revision_id: str = Field(min_length=1)
    status: TaskStatus
    materialization: TaskMaterializationReceipt
    dispatch: TaskDispatchReceipt

    @classmethod
    def from_task(cls, task: dict[str, Any], materialization: TaskMaterializationReceipt) -> "TaskReceipt":
        record = MaterializedTaskRow.from_task(task, materialization)
        record.dispatch = TaskDispatchReceipt()
        return cls.model_validate(record.model_dump(mode="json", exclude_unset=True))

"""Project and active Workflow inspection output contracts."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from app.v1.domain import WorkflowDefinition


class ProjectSummaryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Project ID")
    name: str = Field(description="Project name")
    description: str = Field(description="Project description")
    base_dir: str = Field(description="Project workspace path")
    created_at: str = Field(description="Creation timestamp")
    updated_at: str = Field(description="Last update timestamp")


class ProjectRecord(ProjectSummaryReceipt):
    state_version: int = Field(ge=0, description="Projection version")


class ConversationAgentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    logical_id: str
    state: str
    instruction: str
    instruction_revision: int = Field(ge=1)
    last_observed_event_id: int = Field(ge=0)
    lease_owner: str | None
    lease_until: str | None
    created_at: str
    updated_at: str


class WorkflowRevisionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Active Workflow revision ID")
    project_id: str = Field(description="Owning Project ID")
    revision: int = Field(ge=1, description="Revision number")
    active: bool = Field(description="Whether this revision is active")
    workflow_plan_id: str | None = Field(description="Saved Workflow Plan ID")
    created_at: str = Field(description="Revision creation timestamp")
    workflow_id: str | None = Field(default=None, description="Logical Workflow identity, if recorded")
    revision_no: int | None = Field(default=None, description="Compatibility revision number")
    schema_version: str = Field(description="Workflow definition schema version")
    definition_hash: str | None = Field(default=None, description="Frozen definition hash")
    workflow_identity_id: str = Field(description="Logical Workflow ID")
    workflow_state_version: int = Field(ge=0, description="Workflow state version")
    source_loop_key: str | None = Field(description="Source Loop key, if applied")
    source_loop_version: str | None = Field(description="Source Loop version, if applied")
    source_loop_digest: str | None = Field(description="Source Loop digest, if applied")
    definition: WorkflowDefinition = Field(description="Active directed Workflow graph")
    loop_bindings: dict[str, JsonValue] = Field(description="Bound parameters from the source Loop")


class ProjectInspectReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: ProjectSummaryReceipt = Field(description="Project identity and workspace")
    workflow: WorkflowRevisionReceipt | None = Field(description="Active Workflow or null")


class OptionalWorkflowRevisionReceipt(RootModel[WorkflowRevisionReceipt | None]):
    pass

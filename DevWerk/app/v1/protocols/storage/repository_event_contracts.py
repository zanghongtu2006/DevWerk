"""Fixed Scope, external-event, and artifact repository receipts."""

from pydantic import BaseModel, ConfigDict, JsonValue

from app.v1.protocols.conversation.conversation_intent_contracts import RequirementSnapshot


class ScopeRevisionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement: RequirementSnapshot
    workflow_revision_id: str | None
    binding_id: str | None
    bindings: dict[str, JsonValue]
    previous_workflow_revision_id: str | None
    task_ids: list[str]
    workflow_definition_changed: bool


class ExternalEventData(BaseModel):
    correlation_key: str
    output: dict[str, JsonValue]
    source: str = "automation_api"


class ExternalEventReceipt(BaseModel):
    id: int
    project_id: str
    type: str
    correlation_key: str
    output: dict[str, JsonValue]
    created_at: str


class ArtifactWriteEvent(BaseModel):
    path: str
    kind: str
    size: int


class ArtifactRegisterReceipt(ArtifactWriteEvent):
    id: str
    sha256: str

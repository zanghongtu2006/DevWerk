"""Typed Column context records composed from dynamic artifact facts."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from app.v1.domain import ColumnDefinition


class SelectedArtifactView(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: str | None = None
    artifact_id: str | None = None
    source_task_id: str | None = None
    source_run_id: str | None = None
    provenance: str = ""

    @classmethod
    def from_facts(
        cls, facts: dict[str, Any], provenance: str, *, content: str | None = None,
        artifact_id: str | None = None, source_task_id: str | None = None,
        source_run_id: str | None = None,
    ) -> dict[str, JsonValue]:
        result = cls.model_validate(TypeAdapter(dict[str, JsonValue]).validate_python(facts))
        result.provenance = provenance
        if content is not None:
            result.content = content
        if artifact_id is not None:
            result.artifact_id = artifact_id
        if source_task_id is not None:
            result.source_task_id = source_task_id
        if source_run_id is not None:
            result.source_run_id = source_run_id
        return result.model_dump(mode="json", exclude_none=True)


class FeedbackCheckView(BaseModel):
    column: str
    check_key: str
    purpose: str


class FeedbackContractView(BaseModel):
    responsible_columns: list[str]
    checks: list[FeedbackCheckView] = Field(default_factory=list)
    instruction: str = (
        "Record concrete defects for Conversation Agent supervision. Recording feedback does not choose "
        "a route or block a terminal edge. Submit the outcome of this Column using a declared Workflow "
        "transition; choose a rework outcome when this Column cannot satisfy its selected edge. Runtime "
        "handles handoff; do not send instructions through Mailbox or run other Workers."
    )


class WorkflowContextView(BaseModel):
    id: str
    name: str
    description: str


class AgentColumnContext(BaseModel):
    workflow: WorkflowContextView
    column: ColumnDefinition
    input: JsonValue
    task_feedback: JsonValue
    feedback_contract: FeedbackContractView | None = None

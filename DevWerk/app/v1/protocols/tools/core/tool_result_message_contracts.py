"""Tool result envelope shown to the Agent with an optional evidence reference."""

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.v1.domain import ToolResult


class ToolEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="ignore")
    agent_run_id: str | None = None
    tool_call_id: str | None = None
    evidence_id: str | None = None
    capability: str | None = None
    entity_ids: list[str] | None = None
    entity_ids_truncated: bool | None = None
    entity_id_count: int | None = Field(default=None, ge=0)
    entity_ids_sha256: str | None = None


class ToolResultMessage(ToolResult):
    evidence: ToolEvidenceReference | None = None

    @field_serializer("evidence")
    def serialize_evidence(self, value: ToolEvidenceReference | None) -> dict | None:
        return value.model_dump(exclude_none=True) if value else None

    @classmethod
    def from_result(cls, result: ToolResult, reference: dict | None) -> "ToolResultMessage":
        message = cls.model_validate(result.model_dump(mode="json"))
        if reference:
            evidence = ToolEvidenceReference.model_validate(reference)
            if evidence.model_dump(exclude_none=True):
                message.evidence = evidence
        return message

"""Typed bounded summaries placed in Conversation and Agent context."""

from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue

from app.v1.protocols.conversation.conversation_intent_contracts import TurnResolutionReceipt


class TaskPlanContextSummary(BaseModel):
    id: str
    workflow_revision_id: str | None = None
    requirement_id: str | None = None
    created_at: str | None = None
    proposed_task_refs: list[str]

    @classmethod
    def from_plan(cls, plan: dict[str, Any]) -> "TaskPlanContextSummary":
        return cls(
            id=plan['id'], workflow_revision_id=plan.get('workflow_revision_id'),
            requirement_id=plan.get('requirement_id'), created_at=plan.get('created_at'),
            proposed_task_refs=[item.get('proposed_task_ref') for item in (plan.get('plan') or {}).get('tasks', [])],
        )


class TaskSummaryWithFailure(BaseModel):
    model_config = ConfigDict(extra="allow")
    failure_detail: JsonValue = None

    @classmethod
    def from_task(cls, task: dict[str, Any], failure_detail: Any) -> dict[str, JsonValue]:
        result = cls.model_validate(task)
        result.failure_detail = failure_detail
        return result.model_dump(mode="json")


class AuthorizationReplayReceipt(TurnResolutionReceipt):
    model_config = ConfigDict(extra="forbid")
    already_authorized: bool = True
    requested_change_ignored: bool = True
    next_action: str = (
        "Use the existing execution_grant_id and continue the already authorized work; "
        "this replay did not change scope or create a new grant."
    )

    @classmethod
    def from_saved(cls, data: str) -> dict[str, JsonValue]:
        result = cls.model_validate_json(data)
        result.already_authorized = True
        result.requested_change_ignored = True
        result.next_action = cls.model_fields['next_action'].default
        return result.model_dump(mode="json")


class CompactContextSummary(BaseModel):
    """Small terminal projection of a bounded context checkpoint."""

    source_hash: str | None = None
    previous_source_hash: str | None = None
    archived_messages: int | None = None
    method: str | None = None
    summary: str

    @classmethod
    def from_bounded(cls, value: dict[str, Any], summary: str) -> dict[str, JsonValue]:
        return cls(
            source_hash=value.get("source_hash"),
            previous_source_hash=value.get("previous_source_hash"),
            archived_messages=value.get("archived_messages"),
            method=value.get("method"), summary=summary,
        ).model_dump(mode="json", exclude_none=True)

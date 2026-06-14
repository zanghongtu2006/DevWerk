"""
Plan models — file-level change declarations returned by /v1/ide/plan.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class PlanFile(BaseModel):
    """
    One file in the planner's proposed change list.
    """

    path: str = Field(description="Relative path from project root, /-separated.")

    nature: Literal["new", "modified", "deleted"] = Field(
        description="Whether this file will be created, modified, or deleted."
    )

    description: str = Field(
        description="Human-readable one-line description of the change intent."
    )

    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="LLM confidence that this file actually needs to change.",
    )


class PlanResponse(BaseModel):
    """
    Response from POST /v1/ide/plan.
    """

    ok: bool = True
    task_id: Optional[str] = None
    status_key: Optional[str] = None
    session_id: Optional[str] = None
    phase_output: Optional[dict[str, Any]] = None
    next_action: Optional[str] = None

    files: list[PlanFile] = Field(
        default_factory=list,
        description=(
            "Files the LLM intends to modify. "
            "Empty list is valid only when the backend classified the request as pure Q&A."
        ),
    )

    summary: str = Field(
        default="",
        description="One-line summary shown to the user before they approve.",
    )

    warnings: list[str] = Field(
        default_factory=list,
        description="Any warnings the LLM surfaced (e.g. framework file, contains config).",
    )

    error_code: Optional[str] = None
    error_message: Optional[str] = None


class ExecuteRequest(BaseModel):
    """
    Body for POST /v1/ide/execute.

    Sent after the user has reviewed and approved the plan.
    """

    messages: list[dict] = Field(
        description="Full conversation history up to and including the original user message."
    )

    project_root: Optional[str] = Field(default=None)
    project_id: Optional[str] = Field(default=None)
    task_id: Optional[str] = Field(default=None)

    mode: Literal["agent", "scaffold"] = Field(default="agent")

    approved_paths: list[str] = Field(
        default_factory=list,
        description="Paths the user has explicitly approved for this execution round.",
    )

    approved_ops: list[dict] = Field(
        default_factory=list,
        description="Pre-built ops from frontend. Backend re-generates if empty.",
    )

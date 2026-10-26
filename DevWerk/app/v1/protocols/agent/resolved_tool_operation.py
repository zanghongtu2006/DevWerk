"""Durable mapping from a Provider call to the Tool authorized for execution."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ResolvedToolOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_name: str
    provider_arguments: dict[str, Any]
    capability: str
    arguments: dict[str, Any]
    schema_digest: str | None = None
    presentation_digest: str | None = None
    rejection_type: str | None = None
    rejection_message: str | None = None

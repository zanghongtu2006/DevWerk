"""Read-only evidence for replacing the active Workflow revision."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class WorkflowRevisionBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str
    current_column: str
    reason: Literal["active_effect", "started_nonterminal"]

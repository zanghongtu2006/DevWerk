"""Conversation Job-owned work progress, distinct from Workflow Task state."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArtifactObservation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    relative_path: str
    exists: bool
    sha256: str | None = None
    size_bytes: int | None = None
    observed_at: str


class WorkResourceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource_id: str = Field(min_length=1)
    purpose: str = ''
    relative_path: str | None = None
    tool_call_id: str | None = None

    @model_validator(mode='before')
    @classmethod
    def accept_legacy_empty_observation(cls, value):
        if isinstance(value, dict) and 'observation' in value and value['observation'] is None:
            return {key: field for key, field in value.items() if key != 'observation'}
        return value


class WorkResource(WorkResourceInput):
    observation: ArtifactObservation | None = None


class WorkCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    check_id: str = Field(min_length=1)
    target: str
    tool_call_id: str | None = Field(default=None, description='For a completed check, cite the exact successful tool call that performed this check.')
    resource_id: str | None = None
    resource_sha256: str | None = None
    assessment: str = ''
    unresolved: bool = False


class WorkItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_id: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    agent_status: Literal['pending', 'in_progress', 'ready', 'blocked', 'deferred'] = 'pending'
    deferral_quote: str | None = Field(default=None, min_length=1, max_length=500,
        description='Exact user words placing a deferred item in a later turn.')
    resource_ids: list[str] = Field(default_factory=list)
    checks: list[WorkCheck] = Field(default_factory=list)
    unresolved_problems: list[str] = Field(default_factory=list)


class WorkResumeFrom(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_job_id: str
    source_revision: int = Field(ge=1)
    item_ids: list[str] = Field(min_length=1)
    mode: Literal['continue', 'revise']
    reason: str = Field(min_length=1)


class WorkSnapshotInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    objective: str = Field(min_length=1)
    items: list[WorkItem] = Field(max_length=32)
    resources: list[WorkResourceInput] = Field(default_factory=list)
    next_action: str = ''

    @model_validator(mode='after')
    def valid_references(self):
        ids = {item.item_id for item in self.items}
        resources = {item.resource_id for item in self.resources}
        if len(ids) != len(self.items) or len(resources) != len(self.resources):
            raise ValueError('Duplicate work item or resource ID')
        if any(set(item.depends_on) - ids or set(item.resource_ids) - resources
               for item in self.items):
            raise ValueError('Work item has a missing dependency or resource')
        if any(check.resource_id and check.resource_id not in resources
               for item in self.items for check in item.checks):
            raise ValueError('Work check references a missing resource')
        deferred = {item.item_id for item in self.items if item.agent_status == 'deferred'}
        if any((item.agent_status == 'deferred') != bool(item.deferral_quote and item.deferral_quote.strip())
               for item in self.items):
            raise ValueError('A deferred item requires a user deferral quote; omit it for current work')
        if deferred and len(deferred) == len(self.items):
            raise ValueError('Keep at least one current deliverable; put future ideas in next_action')
        if any(deferred.intersection(item.depends_on) for item in self.items if item.item_id not in deferred):
            raise ValueError('Current work cannot depend on deferred work')
        return self


class ConversationWorkSnapshot(WorkSnapshotInput):
    resources: list[WorkResource] = Field(default_factory=list)
    project_id: str
    job_id: str
    source_user_message_id: int
    revision: int = Field(ge=1)
    resume_from: WorkResumeFrom | None = None


class ConversationWorkUpdateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_revision: int = Field(ge=0)
    snapshot: WorkSnapshotInput | None = None
    resume_from: WorkResumeFrom | None = None

    @model_validator(mode='after')
    def one_update_kind(self):
        if (self.snapshot is None) == (self.resume_from is None):
            raise ValueError('Provide exactly one of snapshot or resume_from')
        return self


class ConversationWorkReadInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str | None = None


class ConversationWorkReadReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    snapshot: ConversationWorkSnapshot | None
    reference_only: bool

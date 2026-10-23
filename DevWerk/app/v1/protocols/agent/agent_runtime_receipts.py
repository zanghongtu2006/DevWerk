"""Small fixed Agent runtime and provider-budget records."""

from pydantic import BaseModel, ConfigDict, Field


class RunCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iterations_completed: int = Field(ge=0)
    tool_calls_completed: int = Field(ge=0)
    direct_effect_calls: int = Field(ge=0)
    latest_text: str


class AgentTraceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_run_id: str
    conversation_job_id: str | None
    project_id: str
    task_id: str | None
    column_run_id: str | None
    column_attempt_id: str | None
    agent_kind: str
    iteration: int = Field(ge=0)


class LLMContextLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: int = Field(ge=1)
    output: int = Field(ge=1)
    protocol: str
    model: str


class SupervisorHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    running: bool
    last_successful_tick: str | None
    last_exception: str | None

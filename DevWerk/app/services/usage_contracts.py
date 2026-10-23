"""Fixed API and storage shapes for model usage summaries."""

from pydantic import BaseModel, ConfigDict, Field


class NormalizedUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cached_input_tokens: int | None
    cache_creation_input_tokens: int | None
    cached_output_tokens: int | None
    input_cache_hit_rate: float | None
    output_cache_hit_rate: float | None


class UsageScope(BaseModel):
    project_id: str | None
    task_id: str | None
    start: str | None
    end: str | None


class UsageAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str | None = None
    task_id: str | None = None
    agent_name: str | None = None
    provider: str | None = None
    model: str | None = None
    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    avg_input_cache_hit_rate: float | None = None
    duration_ms: int = Field(ge=0)
    successful_calls: int = Field(ge=0)
    request_count: int | None = Field(default=None, ge=0)


class UsageSummary(BaseModel):
    ok: bool = True
    enabled: bool
    projects: list[UsageAggregate]
    request_count: int = Field(ge=0)
    scope: UsageScope | None = None
    totals: UsageAggregate | None = None
    by_project: list[UsageAggregate] | None = None
    by_task: list[UsageAggregate] | None = None
    by_agent: list[UsageAggregate] | None = None

    def wire(self) -> dict:
        return self.model_dump(mode="json", exclude_unset=True)

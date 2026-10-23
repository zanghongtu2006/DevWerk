"""Validated transport configuration passed to a provider client."""

from pydantic import BaseModel, ConfigDict, Field


class ProviderClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str
    api_name: str
    protocol: str
    base_url: str
    api_key: str
    model: str
    effort_level: str | None
    trust_env_proxy: bool
    connect_max_attempts: int = Field(ge=1)
    request_timeout_seconds: float = Field(gt=0)
    thinking_mode: str
    temperature: float
    top_p: float | None
    max_tokens: int = Field(ge=1)
    context_window: int | None = Field(default=None, ge=1)

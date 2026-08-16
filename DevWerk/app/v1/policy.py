from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SchedulingPolicy(_PolicyModel):
    conversation_workers: int = Field(default=4, ge=1)
    runtime_workers: int = Field(default=4, ge=1)
    default_wip_limit: int = Field(default=4, ge=1)
    task_lease_seconds: int = Field(default=120, ge=1)
    task_lease_renew_seconds: int = Field(default=30, ge=1)
    conversation_lease_seconds: int = Field(default=180, ge=1)
    conversation_lease_renew_seconds: int = Field(default=60, ge=1)
    supervisor_interval_seconds: float = Field(default=0.5, gt=0)
    runnable_batch_size: int = Field(default=20, ge=1)
    await_batch_size: int = Field(default=100, ge=1)
    quiescence_observation_seconds: int = Field(default=2, ge=0)
    recovery_retry_delay_seconds: int = Field(default=30, ge=1)


class ContextPolicy(_PolicyModel):
    task_summary_limit: int = Field(default=100, ge=1)
    mailbox_limit: int = Field(default=100, ge=1)


class ServiceLimits(_PolicyModel):
    default_page_size: int = Field(default=100, ge=1)
    detail_page_size: int = Field(default=200, ge=1)
    max_page_size: int = Field(default=500, ge=1)
    default_file_list_size: int = Field(default=200, ge=1)
    max_file_list_size: int = Field(default=1_000, ge=1)
    default_search_results: int = Field(default=100, ge=1)
    max_search_results: int = Field(default=500, ge=1)
    event_poll_interval_seconds: float = Field(default=1.0, gt=0)
    sqlite_busy_timeout_milliseconds: int = Field(default=15_000, ge=1)


class V1RuntimePolicy(_PolicyModel):
    schema_version: str = "devwerk.runtime-policy.v1"
    revision: int = Field(default=1, ge=1)
    scheduling: SchedulingPolicy = Field(default_factory=SchedulingPolicy)
    context: ContextPolicy = Field(default_factory=ContextPolicy)
    service_limits: ServiceLimits = Field(default_factory=ServiceLimits)

    @property
    def policy_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

DEFAULT_V1_RUNTIME_POLICY = V1RuntimePolicy()


@dataclass(frozen=True)
class PlatformPolicySnapshot:
    path: str
    content: str
    content_hash: str
    revision: int = 0

    def with_revision(self, revision: int) -> "PlatformPolicySnapshot":
        return PlatformPolicySnapshot(self.path, self.content, self.content_hash, revision)


class PlatformPolicyLoader:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()

    def load(self) -> PlatformPolicySnapshot:
        if not self.path.is_file():
            raise RuntimeError(f"Conversation Platform Policy is missing: {self.path}")
        content = self.path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not content:
            raise RuntimeError(f"Conversation Platform Policy is empty: {self.path}")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return PlatformPolicySnapshot(str(self.path), content, content_hash)

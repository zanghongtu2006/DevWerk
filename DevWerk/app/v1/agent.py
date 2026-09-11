from __future__ import annotations

from typing import Any

from app.v1.agent_models import AgentRunResult, AgentRunSpec, ModelComplete
from app.v1.agent_runner import AgentExecutionRunner
from app.v1.capabilities import CapabilityRegistry
from app.v1.llm import complete as provider_complete
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, PlatformPolicySnapshot, V1RuntimePolicy


class AgentCore:
    """Stable facade for Provider-independent Agent execution."""

    def __init__(
        self,
        store: Any,
        registry: CapabilityRegistry,
        model_complete: ModelComplete | None = None,
        *,
        policy: V1RuntimePolicy | None = None,
        platform_policy: PlatformPolicySnapshot | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.model_complete = model_complete or provider_complete
        self.policy = policy or DEFAULT_V1_RUNTIME_POLICY
        self.platform_policy = platform_policy

    def run(self, spec: AgentRunSpec) -> AgentRunResult:
        return AgentExecutionRunner(
            store=self.store,
            registry=self.registry,
            model_complete=self.model_complete,
            runtime_policy=self.policy,
            platform_policy=self.platform_policy,
        ).run(spec)

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    id: str
    roles: tuple[str, ...]
    runtime: str
    model_route: str
    capabilities: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    memory_policy: str = "task_and_project"
    context_policy: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class JobTemplate:
    id: str
    role: str
    output_contract: str
    required_capabilities: tuple[str, ...] = ()
    preferred_agent: str | None = None


@dataclass(frozen=True)
class AgentCatalog:
    agents: tuple[AgentSpec, ...]
    jobs: tuple[JobTemplate, ...]

    def job(self, job_id: str) -> JobTemplate:
        match = next((job for job in self.jobs if job.id == job_id), None)
        if match is None:
            raise KeyError(f"unknown job template: {job_id}")
        return match

    def select(self, job: JobTemplate) -> AgentSpec:
        candidates = [
            agent
            for agent in self.agents
            if agent.enabled
            and job.role in agent.roles
            and set(job.required_capabilities).issubset(agent.capabilities)
        ]
        if not candidates and job.role != "general":
            candidates = [
                agent
                for agent in self.agents
                if agent.enabled
                and "general" in agent.roles
                and set(job.required_capabilities).issubset(agent.capabilities)
            ]
        if job.preferred_agent:
            preferred = next((agent for agent in candidates if agent.id == job.preferred_agent), None)
            if preferred is not None:
                return preferred
        if not candidates:
            raise LookupError(f"no enabled agent can execute job template {job.id!r}")
        return candidates[0]

    def with_project_overrides(self, overrides: object) -> "AgentCatalog":
        if not isinstance(overrides, dict):
            return self
        updated: list[AgentSpec] = []
        for agent in self.agents:
            raw = overrides.get(agent.id)
            if not isinstance(raw, dict):
                updated.append(agent)
                continue
            model_route = str(raw.get("model_route") or raw.get("model_ref") or agent.model_route).strip()
            updated.append(
                replace(
                    agent,
                    enabled=bool(raw.get("enabled", agent.enabled)),
                    model_route=model_route or agent.model_route,
                )
            )
        return AgentCatalog(tuple(updated), self.jobs)


def default_agent_catalog() -> AgentCatalog:
    path = Path(__file__).resolve().parents[2] / "config" / "agents" / "default.json"
    return agent_catalog_from_dict(json.loads(path.read_text(encoding="utf-8")))


def agent_catalog_from_dict(value: dict[str, Any]) -> AgentCatalog:
    agents: list[AgentSpec] = []
    for raw in value.get("agents") or []:
        if not isinstance(raw, dict):
            continue
        agent_id = str(raw.get("id") or "").strip()
        roles = _strings(raw.get("roles"))
        if not agent_id or not roles:
            continue
        agents.append(
            AgentSpec(
                id=agent_id,
                roles=roles,
                runtime=str(raw.get("runtime") or "tool_loop").strip(),
                model_route=str(raw.get("model_route") or "default").strip(),
                capabilities=_strings(raw.get("capabilities")),
                skills=_strings(raw.get("skills")),
                memory_policy=str(raw.get("memory_policy") or "task_and_project").strip(),
                context_policy=dict(raw.get("context_policy") or {}),
                enabled=bool(raw.get("enabled", True)),
            )
        )

    jobs: list[JobTemplate] = []
    for raw in value.get("job_templates") or []:
        if not isinstance(raw, dict):
            continue
        job_id = str(raw.get("id") or "").strip()
        role = str(raw.get("role") or "").strip()
        output_contract = str(raw.get("output_contract") or "").strip()
        if not job_id or not role or not output_contract:
            continue
        jobs.append(
            JobTemplate(
                id=job_id,
                role=role,
                output_contract=output_contract,
                required_capabilities=_strings(raw.get("required_capabilities")),
                preferred_agent=_optional_text(raw.get("preferred_agent")),
            )
        )
    if not agents or not jobs:
        raise ValueError("agent catalog must define at least one agent and one job template")
    return AgentCatalog(tuple(agents), tuple(jobs))


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None

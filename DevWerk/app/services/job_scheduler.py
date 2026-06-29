from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from app.services.agent_definition import AgentCatalog, AgentSpec, JobTemplate


@dataclass(frozen=True)
class ScheduledJob:
    id: str
    task_id: str
    column: str
    template: JobTemplate
    agent: AgentSpec


class JobScheduler:
    def __init__(self, catalog: AgentCatalog):
        self.catalog = catalog

    def schedule(self, *, task_id: str, column: str, job_template: str) -> ScheduledJob:
        dynamic_template = False
        try:
            template = self.catalog.job(job_template)
        except KeyError:
            dynamic_template = True
            template = JobTemplate(id=job_template, role="general", output_contract=job_template)
        agent = self.catalog.select(template)
        if dynamic_template:
            configured = next((candidate for candidate in self.catalog.agents if candidate.id == f"{column}-agent" and candidate.enabled), None)
            if configured is not None:
                agent = configured
            else:
                agent = replace(
                    agent,
                    id=f"{column}-agent",
                    roles=(f"column:{column}", "workflow_node"),
                    skills=(f"workflow-column:{column}", *agent.skills),
                )
        return ScheduledJob(
            id=f"job-{uuid.uuid4()}",
            task_id=task_id,
            column=column,
            template=template,
            agent=agent,
        )

from __future__ import annotations

import uuid
from dataclasses import dataclass

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
        try:
            template = self.catalog.job(job_template)
        except KeyError:
            template = JobTemplate(id=job_template, role="general", output_contract=job_template)
        agent = self.catalog.select(template)
        return ScheduledJob(
            id=f"job-{uuid.uuid4()}",
            task_id=task_id,
            column=column,
            template=template,
            agent=agent,
        )

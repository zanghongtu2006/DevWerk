"""Public Runtime state-machine catalog."""

from pydantic import BaseModel, ConfigDict


class StateCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: list[str]
    transitions: dict[str, list[str]]


class RuntimeStatusCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: StateCatalogItem
    column_run: StateCatalogItem
    attempt: StateCatalogItem
    agent_run: StateCatalogItem
    tool_invocation: StateCatalogItem
    mailbox: StateCatalogItem
    feedback: StateCatalogItem

"""Exact, paged reads of persisted context summaries."""
from pydantic import BaseModel, ConfigDict, Field


class CheckpointReadInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    checkpoint_id: str = Field(min_length=1)
    entry_id: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=10000)


class CheckpointReadReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    checkpoint_id: str
    entry_id: str | None = None
    legacy: bool
    content: str
    offset: int
    total_characters: int
    next_offset: int | None = None
    sha256: str

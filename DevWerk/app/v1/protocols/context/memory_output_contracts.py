"""Fixed File Memory receipts and bounded Agent-context projections."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class MemoryFileReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    reference: str
    metadata: dict[str, JsonValue]
    content: str
    sha256: str = Field(min_length=64, max_length=64)


class MemoryCoreMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str
    scope: str
    authority: str
    status: str
    revision: int = Field(ge=1)
    project_id: str
    updated_at: str


class MemoryStoredMetadata(BaseModel):
    """Front matter shared by semantic and Project core Memory files."""

    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str
    scope: str
    authority: str
    status: str
    revision: int = Field(ge=1)
    scope_id: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    source_hash: str | None = None
    superseded_by: str | None = None
    project_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryProjectInitReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    root: str
    created: list[str]


class MemoryIndexRebuildReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    rebuilt: bool
    project_id: str


class MemorySnapshotReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    reference: str
    sha256: str = Field(min_length=64, max_length=64)


class MemoryContextOmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str
    reference: str | None = None
    scope: str | None = None
    query: str | None = None


class MemoryContextCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record: MemoryFileReceipt
    reason: str
    reasons: list[str]


class MemoryContextRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str
    metadata: dict[str, JsonValue]
    sha256: str = Field(min_length=64, max_length=64)
    content: str
    content_complete: bool
    next_offset: int | None = Field(ge=0)


class MemoryContextSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str
    id: str | None
    scope: str | None
    scope_id: str | None
    kind: str | None
    sha256: str = Field(min_length=64, max_length=64)
    reason: str
    reasons: list[str]
    content_complete: bool


class MemoryContextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_provider: str
    index_provider: str
    selected: list[MemoryContextSelection]
    omitted: list[MemoryContextOmission]
    omitted_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    total_candidates: int | None = Field(ge=0)
    candidate_truncated: bool
    candidate_limit_per_selector: int = Field(ge=1)
    retrieve: str = "project.memory.read with reference, offset and expected_sha256"
    instruction: str = (
        "A partial Memory excerpt is a source pointer, not a complete constraint set. "
        "Read relevant missing pages before relying on it."
    )


class MemoryContextReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[MemoryContextRecord]
    manifest: MemoryContextManifest

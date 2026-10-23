"""Wire contracts for Project semantic Memory capabilities."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from app.v1.protocols.tools.core.capability_protocol import READ_LIMITS


MemoryScope = Literal["project", "conversation", "workflow", "task"]
MemoryStatus = Literal["active", "superseded", "stale", "tombstoned"]


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryReadInput(MemoryInput):
    reference: str = Field(min_length=1, description="Exact Memory record reference")
    offset: int = Field(default=0, ge=0, description="Character offset for paging")
    max_characters: int = Field(default=READ_LIMITS.memory_page_characters, ge=1, le=4000,
                                description="Maximum returned characters")
    expected_sha256: str = Field(default=None, min_length=64, max_length=64,
                                 description="Optional full-record hash assertion")


class MemorySearchInput(MemoryInput):
    query: str = Field(max_length=4000, description="Semantic search query")
    scope: MemoryScope = Field(default=None, description="Optional Memory scope selector")
    scope_id: str = Field(default=None, max_length=500, description="Existing ID in the selected scope")
    kinds: list[str] = Field(default_factory=list, max_length=100, description="Memory kind filters")
    limit: int = Field(default=READ_LIMITS.memory_search_results, ge=1, le=200,
                       description="Requested page size; Runtime may apply a lower preview limit")
    offset: int = Field(default=0, ge=0, le=5000, description="Search result offset")


class MemoryWriteInput(MemoryInput):
    kind: str = Field(min_length=1, max_length=200, description="Semantic Memory kind")
    scope: MemoryScope = Field(description="Visibility scope of this record")
    content: str = Field(min_length=1, description="Human-readable Memory content")
    id: str = Field(default=None, min_length=1, max_length=500, description="Optional caller-selected record ID")
    scope_id: str = Field(default=None, max_length=500, description="Existing scope ID; omit for current scope")
    authority: str = Field(default=None, min_length=1, max_length=200, description="Source authority label")
    source_type: str = Field(default=None, min_length=1, max_length=200, description="Source type; defaults to Agent Run")
    source_id: str | None = Field(default=None, max_length=500, description="Source record ID")
    source_hash: str | None = Field(default=None, max_length=128, description="Source content hash")
    revision: int = Field(default=None, ge=1, description="Explicit record revision")
    status: MemoryStatus = Field(default=None, description="Record lifecycle status")


class MemoryAppendInput(MemoryInput):
    reference: str = Field(min_length=1, description="Existing Memory record reference")
    content: str = Field(min_length=1, description="Content of the appended revision")
    source_type: str = Field(default=None, min_length=1, max_length=200, description="Revision source type")
    source_id: str | None = Field(default=None, max_length=500, description="Revision source ID")


class MemorySupersedeInput(MemoryInput):
    reference: str = Field(min_length=1, description="Record to supersede")
    replacement: MemoryWriteInput = Field(description="New versioned Memory record")


class MemorySearchMetadata(BaseModel):
    """Only these fields from a larger stored Memory record are exposed in search."""

    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    kind: str | None = None
    scope: MemoryScope | None = None
    scope_id: str | None = None
    revision: int | None = Field(default=None, ge=1)
    authority: str | None = None
    status: MemoryStatus | None = None

    def wire(self) -> dict:
        return self.model_dump(mode="json", exclude_unset=True)


class MemorySearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str
    sha256: str
    metadata: MemorySearchMetadata
    scope_resolution: Literal["resolved", "unbound_or_legacy"]
    content: str
    content_complete: bool
    next_offset: int | None = Field(ge=0)
    search_more_matches: bool
    search_limit_applied: int = Field(ge=1)
    search_offset: int = Field(ge=0)
    search_next_offset: int | None = Field(ge=0)


class MemorySearchReceipt(RootModel[list[MemorySearchHit]]):
    pass


class MemoryRecordReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    reference: str
    metadata: dict[str, JsonValue]
    content: str
    sha256: str = Field(min_length=64, max_length=64)
    scope_resolution: Literal["resolved", "unbound_or_legacy"] | None = None


class MemoryPageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    reference: str
    metadata: dict[str, JsonValue]
    sha256: str = Field(min_length=64, max_length=64)
    offset: int = Field(ge=0)
    content: str
    next_offset: int | None = Field(ge=0)
    total_characters: int = Field(ge=0)
    content_complete: bool
    scope_resolution: Literal["resolved", "unbound_or_legacy"] | None = None


class MemorySourceChangedReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    reference: str
    source_changed: Literal[True]
    sha256: str = Field(min_length=64, max_length=64)
    revision: int | None = Field(ge=1)
    expected_sha256: str = Field(min_length=64, max_length=64)


class MemoryReadReceipt(RootModel[MemoryPageReceipt | MemorySourceChangedReceipt]):
    pass

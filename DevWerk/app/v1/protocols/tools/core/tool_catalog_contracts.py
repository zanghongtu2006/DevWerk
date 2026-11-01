"""Typed contracts for per-Run Tool discovery and Provider presentation."""

from enum import Enum
from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TOOL_SUMMARY_DESCRIPTION_LIMIT = 400
MAX_SELECTED_OPTIONAL_TOOLS = 16
MAX_TOOL_DESCRIPTION_BYTES = 48_000


class ToolExposure(str, Enum):
    DIRECT = "direct"
    ON_DEMAND = "on_demand"
    INTERNAL = "internal"


class ToolSource(str, Enum):
    BUILTIN = "builtin"
    MCP = "mcp"


class ToolCatalogSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str | None = Field(default=None, max_length=200, description="Optional Tool ID or description search text")
    source: ToolSource | None = Field(default=None, description="Optional built-in or MCP source filter")
    offset: int = Field(default=0, ge=0, description="Zero-based result offset")
    limit: int = Field(default=10, ge=1, le=20, description="Maximum returned Tool summaries")


class ToolCatalogSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    description: str = Field(max_length=TOOL_SUMMARY_DESCRIPTION_LIMIT)
    source: ToolSource
    presented: bool


class ToolCatalogSourceCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    builtin: int = Field(ge=0)
    mcp: int = Field(ge=0)


class ToolCatalogSearchReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ToolCatalogSummary]
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    sources: ToolCatalogSourceCounts
    match_mode: Literal["exact", "all_terms", "partial_terms", "empty_query", "no_match"] = "all_terms"


class ToolCatalogSelectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_ids: list[str] = Field(min_length=1, max_length=MAX_SELECTED_OPTIONAL_TOOLS,
                                description="Exact Tool IDs to present on the next model request")

    @model_validator(mode="after")
    def unique_ids(self) -> "ToolCatalogSelectInput":
        if any(not identity.strip() for identity in self.tool_ids):
            raise ValueError("tool_ids must contain non-empty Tool IDs")
        if len(set(self.tool_ids)) != len(self.tool_ids):
            raise ValueError("tool_ids contains duplicate Tool IDs")
        return self


class ToolCatalogSelectReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_ids: list[str]
    applies_on_next_request: Literal[True] = True


class ToolCatalogDescribeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_id: str = Field(min_length=1, description="Exact available Tool ID")


class MCPConnectionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: str
    target_access: str | None = Field(default=None, max_length=600, description="Operator-provided public instructions for accessing the managed target")
    transport: Literal["stdio", "http"] = Field(description="Connection to the MCP server; downstream application endpoints are separate.")


class ToolCatalogDescribeReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_id: str
    description: str
    mcp_connection: MCPConnectionInfo | None = None
    parameters: dict[str, Any] = Field(description="Complete Provider-visible input schema")
    schema_digest: str = Field(description="SHA-256 of the Provider-visible input schema")


class ToolCatalogCallInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_id: str = Field(min_length=1, description="Exact Tool ID returned by catalog search or describe")
    arguments: dict[str, Any] = Field(description="Arguments validated against the target Tool schema")
    schema_digest: str | None = Field(default=None, description="Optional digest from tool.catalog.describe")


class ToolCatalogCallReceipt(BaseModel):
    """Declared boundary receipt; execution itself returns the target Tool result."""

    model_config = ConfigDict(extra="forbid")
    target_tool_id: str
    result: dict[str, Any]

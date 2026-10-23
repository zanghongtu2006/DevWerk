"""Generic MCP transport records; tool-specific schemas remain external data."""

from typing import Any, Literal

from pydantic import BaseModel, Field, JsonValue, model_validator


CLIENT_METHOD_UNAVAILABLE = -32601


class MCPClientInfo(BaseModel):
    name: str = "DevWerk"
    version: str = "1.0.0"


class MCPInitializeParams(BaseModel):
    protocol_version: str = Field(alias="protocolVersion")
    capabilities: dict[str, JsonValue] = Field(default_factory=dict)
    client_info: MCPClientInfo = Field(default_factory=MCPClientInfo, alias="clientInfo")

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", by_alias=True)


class MCPProtocolError(BaseModel):
    code: int
    message: str


class MCPJsonRpcMessage(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = None
    method: str | None = None
    params: dict[str, JsonValue] | None = None
    error: MCPProtocolError | None = None

    @model_validator(mode="after")
    def validate_message(self) -> "MCPJsonRpcMessage":
        if (self.method is None) == (self.error is None):
            raise ValueError("JSON-RPC message requires exactly one method or error")
        if self.error is not None and self.id is None:
            raise ValueError("JSON-RPC error response requires a request id")
        if self.params is not None and self.method is None:
            raise ValueError("JSON-RPC params require a method")
        return self

    def wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)

    @classmethod
    def request(cls, identity: int, method: str, params: dict[str, JsonValue]) -> "MCPJsonRpcMessage":
        return cls(id=identity, method=method, params=params)

    @classmethod
    def notification(cls, method: str) -> "MCPJsonRpcMessage":
        return cls(method=method)

    @classmethod
    def unavailable(cls, identity: int | str) -> "MCPJsonRpcMessage":
        return cls(id=identity, error=MCPProtocolError(
            code=CLIENT_METHOD_UNAVAILABLE, message="Client method unavailable",
        ))


class MCPToolCallParams(BaseModel):
    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]


class MCPResultView(BaseModel):
    content: list[JsonValue] = Field(default_factory=list)
    structured_content: JsonValue = None


class MCPTruncatedView(BaseModel):
    truncated: Literal[True] = True
    preview: str
    original_characters: int = Field(ge=0)

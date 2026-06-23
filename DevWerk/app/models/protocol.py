# app/models/ide.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str


class WorkspaceFile(BaseModel):
    path: str
    sha1: Optional[str] = None
    size: Optional[int] = None


class SourceMapSymbol(BaseModel):
    name: str
    kind: str
    signature: Optional[str] = None
    line: Optional[int] = None


class SourceMapFile(BaseModel):
    path: str
    kind: str
    language: Optional[str] = None
    package: Optional[str] = None
    imports: List[str] = []
    symbols: List[SourceMapSymbol] = []
    size: int = 0


class SourceMap(BaseModel):
    root: str
    generated_at: int
    total_files: int
    indexed_files: int
    skipped_files: int
    files: List[SourceMapFile] = []


class SyntaxDiagnostic(BaseModel):
    path: str
    line: Optional[int] = None
    column: Optional[int] = None
    severity: str = "error"
    message: str
    source: str = "client"


class WorkspaceSummary(BaseModel):
    # Capability providers send compact workspace facts, not full project contents.
    root_id: Optional[str] = None
    changed_files: List[WorkspaceFile] = []
    open_files: List[str] = []
    tree_preview: Optional[str] = None
    source_map: Optional[SourceMap] = None
    syntax_diagnostics: List[SyntaxDiagnostic] = []


class ToolRequest(BaseModel):
    id: str
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    id: str
    ok: bool
    content: Optional[str] = None
    error: Optional[str] = None


class PatchOp(BaseModel):
    op: str
    content: str


class FileOp(BaseModel):
    op: str
    path: str
    language: Optional[str] = None
    content: Optional[str] = None


class IdeChatRequest(BaseModel):
    mode: str
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    project_root: Optional[str] = None
    messages: List[Message]

    workspace: Optional[WorkspaceSummary] = None
    tool_results: List[ToolResult] = []


class IdeChatResponse(BaseModel):
    reply: str = ""
    task_id: Optional[str] = None
    status_key: Optional[str] = None
    planning: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    phase_output: Optional[Dict[str, Any]] = None
    next_action: Optional[str] = None
    interaction: Optional[Dict[str, Any]] = None
    waiting_for: Optional[str] = None

    code_tree: Optional[str] = None
    ops: List[FileOp] = []

    tool_requests: List[ToolRequest] = []
    patch_ops: List[PatchOp] = []

    done: bool = False

    ok: bool = True
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    retryable: bool = False

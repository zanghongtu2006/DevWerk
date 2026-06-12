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


class WorkspaceSummary(BaseModel):
    # 插件发“摘要 + 增量”，不要发全量文件内容
    root_id: Optional[str] = None
    changed_files: List[WorkspaceFile] = []
    open_files: List[str] = []
    tree_preview: Optional[str] = None
    source_map: Optional[SourceMap] = None


class ToolRequest(BaseModel):
    id: str
    tool: str  # list_dir | read_file | search
    args: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    id: str
    ok: bool
    content: Optional[str] = None
    error: Optional[str] = None


class PatchOp(BaseModel):
    op: str  # apply_patch
    content: str  # unified diff


# -------- scaffold 旧模式：文件 CRUD --------
class FileOp(BaseModel):
    op: str
    path: str
    language: Optional[str] = None
    content: Optional[str] = None


class IdeChatRequest(BaseModel):
    mode: str  # scaffold | agent
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    project_root: Optional[str] = None
    messages: List[Message]

    # agent 模式需要的摘要与工具回传
    workspace: Optional[WorkspaceSummary] = None
    tool_results: List[ToolResult] = []


class IdeChatResponse(BaseModel):
    reply: str = ""
    task_id: Optional[str] = None
    status_key: Optional[str] = None
    planning: Optional[Dict[str, Any]] = None

    # scaffold（可选）
    code_tree: Optional[str] = None
    ops: List[FileOp] = []

    # agent（可选）
    tool_requests: List[ToolRequest] = []
    patch_ops: List[PatchOp] = []

    done: bool = False

    #  新增：错误信息（可选）
    ok: bool = True
    error_code: Optional[str] = None  # e.g. "MODEL_TIMEOUT" / "MODEL_UNAVAILABLE"
    error_message: Optional[str] = None
    retryable: bool = False

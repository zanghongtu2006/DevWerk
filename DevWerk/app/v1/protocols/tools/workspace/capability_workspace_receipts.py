"""Successful output contracts for workspace capabilities."""

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileListReceipt(WorkspaceReceipt):
    paths: list[str] = Field(description="Returned file paths")
    count: int = Field(ge=0, description="Number of returned paths")


class FileReadReceipt(WorkspaceReceipt):
    path: str = Field(description="Read path")
    content: str = Field(description="Returned UTF-8 text")


class FilePageReceipt(FileReadReceipt):
    offset: int = Field(ge=0, description="Offset of this page")
    next_offset: int | None = Field(description="Next offset, if more text remains")
    total_characters: int = Field(ge=0, description="Characters in the complete file")
    content_complete: bool = Field(description="Whether this page reaches file end")


class FileWriteFacts(WorkspaceReceipt):
    path: str = Field(description="Written file path")
    size: int = Field(ge=0, description="UTF-8 byte count")
    utf8_characters: int = Field(ge=0, description="Character count")
    non_whitespace_characters: int = Field(ge=0, description="Non-whitespace character count")
    line_count: int = Field(ge=0, description="Line count")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="Content SHA-256")


class SystemFileWriteFacts(WorkspaceReceipt):
    path: str = Field(description="Written file path")
    size_bytes: int = Field(ge=0, description="UTF-8 byte count")
    utf8_characters: int = Field(ge=0, description="Character count")
    line_count: int = Field(ge=0, description="Line count")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="Content SHA-256")


class FileMeasureReceipt(WorkspaceReceipt):
    path: str = Field(description="Measured file path")
    size_bytes: int = Field(ge=0, description="UTF-8 byte count")
    utf8_characters: int = Field(ge=0, description="Character count")
    non_whitespace_characters: int = Field(ge=0, description="Non-whitespace character count")
    line_count: int = Field(ge=0, description="Line count")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="Content SHA-256")
    modified_at_ns: int = Field(ge=0, description="Filesystem modification timestamp")


class ArtifactReceipt(WorkspaceReceipt):
    id: str = Field(description="Registered artifact ID")
    path: str = Field(description="Artifact path")
    kind: str = Field(description="Artifact category")
    size: int = Field(ge=0, description="UTF-8 byte count")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="Content SHA-256")


class ProjectFileWriteReceipt(WorkspaceReceipt):
    file: FileWriteFacts = Field(description="Written file facts")
    artifact: ArtifactReceipt = Field(description="Registered artifact")
    execution_key: str | None = Field(default=None, description="Execution receipt key, when supplied")


class SystemFileWriteReceipt(WorkspaceReceipt):
    file: SystemFileWriteFacts = Field(description="Written file facts")


class FileSearchMatch(WorkspaceReceipt):
    path: str = Field(description="Matching file path")
    line: int = Field(ge=1, description="One-based line number")
    text: str = Field(description="Bounded matching line")


class FileSearchReceipt(WorkspaceReceipt):
    matches: list[FileSearchMatch] = Field(description="Matching lines")
    truncated: bool = Field(description="Whether a result limit stopped the search")


class CommandReceipt(WorkspaceReceipt):
    command: list[str] = Field(description="Requested argv")
    resolved_command: list[str] = Field(description="Resolved argv")
    cwd: str = Field(description="Command working directory")
    exit_code: int = Field(description="Process exit status")
    stdout: str = Field(description="Bounded standard output")
    stderr: str = Field(description="Bounded standard error")
    timed_out: bool = Field(description="Whether the command timed out")
    output_truncated: bool = Field(description="Whether the output exceeded its bound")

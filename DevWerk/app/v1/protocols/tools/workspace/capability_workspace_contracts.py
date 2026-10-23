"""System and Project workspace Tool input classes."""

from pydantic import BaseModel, ConfigDict, Field, create_model

MAX_COMMAND_ARGUMENTS = 100
MAX_LEGACY_EXIT_CODES = 32
SUCCESS_EXIT_CODE = 0
MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_SEARCH_LINE_CHARACTERS = 1_000


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SystemFileListInput(WorkspaceInput):
    path: str | None = Field(default=None, description="Directory; defaults to service working directory")
    pattern: str | None = Field(default=None, description="Relative file glob")
    limit: int | None = Field(default=None, ge=1, description="Maximum returned paths")


class SystemFileReadInput(WorkspaceInput):
    path: str = Field(min_length=1, description="Readable file path")
    max_chars: int | None = Field(default=None, ge=1, description="Maximum returned characters")


class SystemFileWriteInput(WorkspaceInput):
    path: str = Field(min_length=1, description="Writable file path")
    content: str = Field(description="Exact UTF-8 content")


class SystemFileSearchInput(WorkspaceInput):
    path: str | None = Field(default=None, description="Search root; defaults to service directory")
    pattern: str = Field(min_length=1, description="Regular expression to find")
    glob: str | None = Field(default=None, description="File glob within the root")
    limit: int | None = Field(default=None, ge=1, description="Maximum returned matches")


class ProjectFileListInput(WorkspaceInput):
    pattern: str | None = Field(default=None, description="Relative file glob")
    limit: int | None = Field(default=None, ge=1, description="Maximum returned paths")


class ProjectFileSearchInput(WorkspaceInput):
    pattern: str = Field(min_length=1, description="Regular expression to find")
    glob: str | None = Field(default=None, description="Relative file glob")
    limit: int | None = Field(default=None, ge=1, description="Maximum returned matches")


class CommandRunInput(WorkspaceInput):
    argv: list[str] = Field(min_length=1, max_length=MAX_COMMAND_ARGUMENTS, description="Executable and argument vector; no shell")
    cwd: str | None = Field(default=None, description="Working directory")
    success_exit_codes: set[int] | None = Field(default=None, min_length=1, max_length=MAX_LEGACY_EXIT_CODES,
                                         description="Compatibility field; Runtime accepts only exit code zero")


def with_maximum_limit(model: type[WorkspaceInput], maximum: int) -> type[WorkspaceInput]:
    """Bind policy limits through a subclass field, without JSON Schema mutation."""
    return create_model(
        f"Bound{model.__name__}", __base__=model,
        limit=(int | None, Field(default=None, ge=1, le=maximum, description="Maximum returned items")),
    )

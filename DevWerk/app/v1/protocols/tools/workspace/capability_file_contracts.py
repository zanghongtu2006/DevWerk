"""Wire contracts and deterministic expectations for Project file capabilities."""

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _FileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilePathInput(_FileInput):
    path: str = Field(min_length=1, description="Relative path inside the Project workspace")


class FileReadInput(FilePathInput):
    max_chars: int = Field(default=None, ge=1, description="Maximum returned characters")
    offset: int = Field(default=0, ge=0, description="Character offset for the next page")


class FileWriteInput(FilePathInput):
    content: str = Field(description="Exact UTF-8 file content")
    kind: str = Field(default=None, max_length=100, description="Artifact kind; defaults to file")


class FileVerifyInput(FilePathInput):
    expected_content: str = Field(default=None, description="Exact required file content")
    expected_sha256: str = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$", description="Required SHA-256")
    expected_size_bytes: int = Field(default=None, ge=0, description="Required UTF-8 byte count")
    expected_utf8_characters: int = Field(default=None, ge=0, description="Required Unicode character count")
    expected_non_whitespace_characters: int = Field(default=None, ge=0, description="Required non-whitespace count")
    minimum_non_whitespace_characters: int = Field(default=None, ge=0, description="Minimum non-whitespace count")
    maximum_non_whitespace_characters: int = Field(default=None, ge=0, description="Maximum non-whitespace count")
    expected_line_count: int = Field(default=None, ge=0, description="Required line count")
    expected_ends_with_newline: bool = Field(default=None, description="Required final-newline state")

    def expectations(self) -> dict[str, Any]:
        return self.model_dump(exclude={"path"}, exclude_unset=True)

    def verify_consistency(self) -> None:
        if self.expected_content is None:
            return
        content = self.expected_content
        derived = {
            "expected_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "expected_size_bytes": len(content.encode("utf-8")),
            "expected_utf8_characters": len(content),
            "expected_non_whitespace_characters": sum(not char.isspace() for char in content),
            "expected_line_count": len(content.splitlines()),
            "expected_ends_with_newline": content.endswith(("\n", "\r")),
        }
        supplied = self.expectations()
        contradictions = {key: {"supplied": supplied[key], "derived_from_expected_content": value}
                          for key, value in derived.items() if key in supplied and supplied[key] != value}
        count = derived["expected_non_whitespace_characters"]
        if self.minimum_non_whitespace_characters is not None and count < self.minimum_non_whitespace_characters:
            contradictions["minimum_non_whitespace_characters"] = {
                "supplied": self.minimum_non_whitespace_characters,
                "derived_from_expected_content": None,
            }
        if self.maximum_non_whitespace_characters is not None and count > self.maximum_non_whitespace_characters:
            contradictions["maximum_non_whitespace_characters"] = {
                "supplied": self.maximum_non_whitespace_characters,
                "derived_from_expected_content": None,
            }
        if contradictions:
            raise ValueError(
                "expected_content contradicts explicit verification expectations; "
                f"conflicting facts={contradictions}"
            )


class FileVerifyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["matched", "mismatch"] = Field(description="Comparison outcome")
    matched: bool = Field(description="Whether every declared expectation matched")
    checks: dict[str, bool] = Field(description="Boolean result by declared check")
    mismatches: list[str] = Field(description="Names of failed checks")
    actual: "FileVerificationPresent | FileVerificationMissing" = Field(description="Measured file facts")


class FileVerificationMissing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    exists: Literal[False] = False


class FileVerificationPresent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    exists: Literal[True] = True
    size_bytes: int = Field(ge=0)
    utf8_characters: int = Field(ge=0)
    non_whitespace_characters: int = Field(ge=0)
    line_count: int = Field(ge=0)
    ends_with_newline: bool
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    modified_at_ns: int = Field(ge=0)

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, V1RuntimePolicy


logger = logging.getLogger("devwerk.files")


class ProjectFiles:
    def __init__(self, base_dir: str, policy: V1RuntimePolicy | None = None):
        self.policy = policy or DEFAULT_V1_RUNTIME_POLICY
        self.root = Path(base_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        normalized = self._normalize_relative(relative_path, kind="path")
        if not normalized:
            raise ValueError(f"path must be relative to project base_dir: {relative_path!r}")
        candidate = (self.root / normalized).resolve()
        self._relative_resolved(candidate, source=relative_path)
        return candidate

    @staticmethod
    def _normalize_relative(value: str, *, kind: str) -> str:
        normalized = str(value or "").replace("\\", "/")
        parsed = Path(normalized)
        if parsed.is_absolute() or ".." in parsed.parts:
            raise ValueError(f"{kind} escapes project base_dir: {value!r}")
        return normalized

    def _relative_resolved(self, candidate: Path, *, source: str) -> Path:
        """Return a canonical project-relative path, including for glob matches.

        ``Path.glob`` may traverse a junction or symlink whose textual path is below
        the project while its concrete target is not. Every filesystem capability
        therefore validates the resolved object, not only the supplied pattern.
        """
        resolved = candidate.resolve()
        try:
            return resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path escapes project base_dir: {source!r}") from exc

    def _matched_files(self, pattern: str) -> list[tuple[Path, Path]]:
        normalized = self._normalize_relative(pattern or "**/*", kind="glob")
        matches: list[tuple[Path, Path]] = []
        for textual in sorted(self.root.glob(normalized)):
            if not textual.is_file():
                continue
            relative = self._relative_resolved(textual, source=str(textual))
            matches.append((textual.resolve(), relative))
        return matches

    def list_paths(self, pattern: str = "**/*", *, limit: int | None = None) -> list[str]:
        limit = limit or self.policy.service_limits.default_file_list_size
        paths: list[str] = []
        for _path, relative in self._matched_files(pattern):
            paths.append(relative.as_posix())
            if len(paths) >= max(1, min(limit, self.policy.service_limits.max_file_list_size)):
                break
        return paths

    def write_text(self, relative_path: str, content: str) -> dict[str, Any]:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {
            "path": target.relative_to(self.root).as_posix(),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def read_text(self, relative_path: str, max_chars: int | None = None) -> str:
        text = self.resolve(relative_path).read_text(encoding="utf-8")
        return text if max_chars is None else text[:max_chars]

    def measure_text(self, relative_path: str) -> dict[str, Any]:
        target = self.resolve(relative_path)
        data = target.read_bytes()
        text = data.decode("utf-8")
        return {
            "path": target.relative_to(self.root).as_posix(),
            "size_bytes": len(data),
            "utf8_characters": len(text),
            "non_whitespace_characters": sum(1 for character in text if not character.isspace()),
            "line_count": len(text.splitlines()),
            "sha256": hashlib.sha256(data).hexdigest(),
            "modified_at_ns": target.stat().st_mtime_ns,
        }

    def verify_text(self, relative_path: str, expectations: dict[str, Any]) -> dict[str, Any]:
        target = self.resolve(relative_path)
        expectation_keys = (
            "expected_content",
            "expected_sha256",
            "expected_size_bytes",
            "expected_utf8_characters",
            "expected_non_whitespace_characters",
            "expected_line_count",
            "expected_ends_with_newline",
            "minimum_non_whitespace_characters",
            "maximum_non_whitespace_characters",
        )
        declared = [key for key in expectation_keys if expectations.get(key) is not None]
        if not declared:
            raise ValueError("project.files.verify requires at least one explicit expectation")
        if not target.exists():
            checks = {key: False for key in declared}
            return {
                "outcome": "mismatch",
                "matched": False,
                "checks": checks,
                "mismatches": sorted(checks),
                "actual": {
                    "path": target.relative_to(self.root).as_posix(),
                    "exists": False,
                },
            }
        data = target.read_bytes()
        text = data.decode("utf-8")
        actual = {
            "path": target.relative_to(self.root).as_posix(),
            "exists": True,
            "size_bytes": len(data),
            "utf8_characters": len(text),
            "non_whitespace_characters": sum(
                1 for character in text if not character.isspace()
            ),
            "line_count": len(text.splitlines()),
            "ends_with_newline": text.endswith(("\n", "\r")),
            "sha256": hashlib.sha256(data).hexdigest(),
            "modified_at_ns": target.stat().st_mtime_ns,
        }
        checks: dict[str, bool] = {}
        expected_content = expectations.get("expected_content")
        if expected_content is not None:
            checks["expected_content"] = text == str(expected_content)
        scalar_fields = (
            "expected_sha256",
            "expected_size_bytes",
            "expected_utf8_characters",
            "expected_non_whitespace_characters",
            "expected_line_count",
            "expected_ends_with_newline",
        )
        actual_fields = (
            "sha256",
            "size_bytes",
            "utf8_characters",
            "non_whitespace_characters",
            "line_count",
            "ends_with_newline",
        )
        for expected_key, actual_key in zip(scalar_fields, actual_fields):
            if expectations.get(expected_key) is not None:
                checks[expected_key] = actual[actual_key] == expectations[expected_key]
        minimum = expectations.get("minimum_non_whitespace_characters")
        if minimum is not None:
            checks["minimum_non_whitespace_characters"] = (
                actual["non_whitespace_characters"] >= int(minimum)
            )
        maximum = expectations.get("maximum_non_whitespace_characters")
        if maximum is not None:
            checks["maximum_non_whitespace_characters"] = (
                actual["non_whitespace_characters"] <= int(maximum)
            )
        mismatches = sorted(name for name, matched in checks.items() if not matched)
        return {
            "outcome": "matched" if not mismatches else "mismatch",
            "matched": not mismatches,
            "checks": checks,
            "mismatches": mismatches,
            "actual": actual,
        }

    def existing_texts(
        self,
        pattern: str,
        max_total_chars: int | None = None,
        *,
        limit: int | None = None,
        exclude_paths: set[str] | None = None,
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        remaining = max_total_chars
        maximum_files = max(1, limit or self.policy.context.artifact_context_max_files)
        excluded = exclude_paths or set()
        for path, relative in self._matched_files(pattern):
            relative_path = relative.as_posix()
            if relative_path in excluded:
                continue
            if remaining is not None and remaining <= 0:
                break
            try:
                with path.open("r", encoding="utf-8") as handle:
                    text = handle.read(remaining + 1 if remaining is not None else -1)
            except UnicodeDecodeError as exc:
                logger.debug(
                    "context artifact skipped path=%s reason=non_utf8 error=%s",
                    relative_path,
                    exc,
                )
                continue
            if remaining is not None and len(text) > remaining:
                logger.debug(
                    "context artifact skipped path=%s reason=context_character_limit",
                    relative_path,
                )
                continue
            if remaining is not None:
                remaining -= len(text)
            result.append({"path": relative_path, "content": text})
            if len(result) >= maximum_files:
                break
        return result

    def run(self, argv: list[str], cwd: str = ".") -> dict[str, Any]:
        working_dir = self.resolve(cwd)
        working_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.run(
            argv,
            cwd=working_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=os.environ.copy(),
        )
        return {
            "command": argv,
            "cwd": str(working_dir.relative_to(self.root)),
            "exit_code": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
        }

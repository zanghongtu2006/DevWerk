from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class ProjectFiles:
    def __init__(self, base_dir: str):
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

    def list_paths(self, pattern: str = "**/*", *, limit: int = 200) -> list[str]:
        paths: list[str] = []
        for _path, relative in self._matched_files(pattern):
            paths.append(relative.as_posix())
            if len(paths) >= max(1, min(limit, 1000)):
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

    def read_text(self, relative_path: str, max_chars: int = 100_000) -> str:
        text = self.resolve(relative_path).read_text(encoding="utf-8")
        return text[:max_chars]

    def existing_texts(self, pattern: str, max_total_chars: int = 30_000) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        remaining = max_total_chars
        for path, relative in self._matched_files(pattern):
            if remaining <= 0:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if len(text) > remaining:
                continue
            remaining -= len(text)
            result.append({"path": relative.as_posix(), "content": text})
        return result

    def run(self, argv: list[str], cwd: str = ".", timeout: int = 600) -> dict[str, Any]:
        working_dir = self.resolve(cwd)
        working_dir.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.run(
                argv,
                cwd=working_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                env=os.environ.copy(),
            )
            return {
                "command": argv,
                "cwd": str(working_dir.relative_to(self.root)),
                "exit_code": process.returncode,
                "stdout": process.stdout[-20_000:],
                "stderr": process.stderr[-20_000:],
            }
        except FileNotFoundError as exc:
            return {"command": argv, "cwd": cwd, "exit_code": 127, "stdout": "", "stderr": str(exc)}
        except subprocess.TimeoutExpired as exc:
            return {
                "command": argv,
                "cwd": cwd,
                "exit_code": 124,
                "stdout": str(exc.stdout or "")[-20_000:],
                "stderr": f"command timed out after {timeout}s\n{exc.stderr or ''}"[-20_000:],
            }

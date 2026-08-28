from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.v1.domain import MemorySelector
from app.v1.storage_support import new_id


MemoryScope = Literal[
    "project",
    "conversation",
    "workflow",
    "task",
    "workcell",
    "participant",
]

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_CORE_FILES = (
    ("PROJECT.md", "Project Memory", "Durable Project identity and accepted facts."),
    ("CURRENT.md", "Current State", "Current objective, progress, and next meaningful action."),
    ("DECISIONS.md", "Decisions", "User-confirmed and accepted Project decisions."),
    ("CONSTRAINTS.md", "Constraints", "Active Project constraints and working agreements."),
    ("OPEN_ISSUES.md", "Open Issues", "Unresolved questions, risks, and blockers."),
)


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: new_id("mem"))
    kind: str = Field(min_length=1, max_length=200)
    scope: MemoryScope
    scope_id: str | None = Field(default=None, max_length=500)
    authority: str = Field(default="agent_derived", min_length=1, max_length=200)
    content: str = Field(min_length=1)
    source_type: str = Field(default="agent", min_length=1, max_length=200)
    source_id: str | None = Field(default=None, max_length=500)
    source_hash: str | None = Field(default=None, max_length=128)
    revision: int = Field(default=1, ge=1)
    status: Literal["active", "superseded", "stale", "tombstoned"] = "active"
    superseded_by: str | None = Field(default=None, max_length=500)
    created_at: str | None = None
    updated_at: str | None = None


class MemoryStore(ABC):
    """Semantic Memory source-of-truth boundary."""

    name: str

    @abstractmethod
    def initialize_project(self, project: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def write(self, project: dict[str, Any], record: MemoryRecord) -> dict[str, Any]: ...

    @abstractmethod
    def append(
        self,
        project: dict[str, Any],
        reference: str,
        content: str,
        *,
        source_type: str,
        source_id: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def read(self, project: dict[str, Any], reference: str) -> dict[str, Any]: ...

    @abstractmethod
    def list(
        self,
        project: dict[str, Any],
        *,
        scope: str | None = None,
        scope_id: str | None = None,
        kinds: Iterable[str] = (),
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def search(
        self,
        project: dict[str, Any],
        query: str,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
        kinds: Iterable[str] = (),
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def supersede(
        self,
        project: dict[str, Any],
        reference: str,
        replacement: MemoryRecord,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def snapshot(
        self,
        project: dict[str, Any],
        *,
        scope: str,
        scope_id: str,
        state: dict[str, Any],
    ) -> dict[str, Any]: ...

    @abstractmethod
    def restore(self, project: dict[str, Any], reference: str) -> dict[str, Any]: ...


class MemoryIndex(ABC):
    """Disposable search projection over a MemoryStore."""

    name: str

    def index(self, store: MemoryStore, project: dict[str, Any], reference: str) -> None:
        return None

    def remove(self, store: MemoryStore, project: dict[str, Any], reference: str) -> None:
        return None

    @abstractmethod
    def search(
        self,
        store: MemoryStore,
        project: dict[str, Any],
        query: str,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
        kinds: Iterable[str] = (),
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    def rebuild(self, store: MemoryStore, project: dict[str, Any]) -> dict[str, Any]:
        return {"provider": self.name, "rebuilt": True, "project_id": project["id"]}


class TextMemoryIndex(MemoryIndex):
    name = "text"

    def search(
        self,
        store: MemoryStore,
        project: dict[str, Any],
        query: str,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
        kinds: Iterable[str] = (),
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return store.search(
            project,
            query,
            scope=scope,
            scope_id=scope_id,
            kinds=kinds,
            limit=limit,
        )


class FileMemoryStore(MemoryStore):
    """Human-readable, atomic, Project-local semantic Memory."""

    name = "file"

    @staticmethod
    def root(project: dict[str, Any]) -> Path:
        return Path(str(project["base_dir"])).resolve() / ".devwerk" / "memory"

    def initialize_project(self, project: dict[str, Any]) -> dict[str, Any]:
        root = self.root(project)
        root.mkdir(parents=True, exist_ok=True)
        for relative in ("knowledge", "tasks", "workcells", "records"):
            (root / relative).mkdir(parents=True, exist_ok=True)
        created: list[str] = []
        for name, title, purpose in _CORE_FILES:
            path = root / name
            if path.exists():
                continue
            body = f"# {title}\n\n{purpose}\n"
            metadata = {
                "id": f"core_{name[:-3].lower()}",
                "kind": "project_core",
                "scope": "project",
                "authority": "project",
                "status": "active",
                "revision": 1,
                "project_id": project["id"],
                "updated_at": _now(),
            }
            if name == "PROJECT.md":
                body += f"\n- Name: {project.get('name', '')}\n- Description: {project.get('description', '')}\n"
            _atomic_write(path, _render(metadata, body))
            created.append(name)
        return {
            "provider": self.name,
            "root": str(root),
            "created": created,
        }

    def write(self, project: dict[str, Any], record: MemoryRecord) -> dict[str, Any]:
        self.initialize_project(project)
        now = _now()
        value = record.model_copy(update={
            "created_at": record.created_at or now,
            "updated_at": now,
        })
        path = self._record_path(project, value)
        if path.exists():
            previous = self._read_path(project, path)
            if int(previous["metadata"].get("revision") or 1) >= value.revision:
                raise ValueError("Memory revision must increase when replacing an existing record")
        metadata = value.model_dump(exclude={"content"}, exclude_none=True)
        _atomic_write(path, _render(metadata, value.content))
        return self._read_path(project, path)

    def append(
        self,
        project: dict[str, Any],
        reference: str,
        content: str,
        *,
        source_type: str,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        if not content:
            raise ValueError("Memory append content must not be empty")
        current = self.read(project, reference)
        metadata = dict(current["metadata"])
        metadata.update({
            "revision": int(metadata.get("revision") or 1) + 1,
            "source_type": source_type,
            "source_id": source_id,
            "status": "active",
            "updated_at": _now(),
        })
        path = (self.root(project) / current["reference"]).resolve()
        _require_within(self.root(project), path)
        merged = current["content"].rstrip() + "\n\n" + content.lstrip()
        _atomic_write(path, _render(metadata, merged))
        return self._read_path(project, path)

    def read(self, project: dict[str, Any], reference: str) -> dict[str, Any]:
        root = self.root(project)
        candidate = (root / reference).resolve()
        _require_within(root, candidate)
        if not candidate.is_file():
            matches = [item for item in root.rglob("*.md") if _front_matter(item).get("id") == reference]
            if len(matches) != 1:
                raise FileNotFoundError(reference)
            candidate = matches[0]
        return self._read_path(project, candidate)

    def list(
        self,
        project: dict[str, Any],
        *,
        scope: str | None = None,
        scope_id: str | None = None,
        kinds: Iterable[str] = (),
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        self.initialize_project(project)
        selected_kinds = {str(item) for item in kinds}
        records: list[dict[str, Any]] = []
        for path in sorted(self.root(project).rglob("*.md")):
            item = self._read_path(project, path)
            metadata = item["metadata"]
            if scope and metadata.get("scope") != scope:
                continue
            if scope_id is not None and metadata.get("scope_id") != scope_id:
                continue
            if selected_kinds and metadata.get("kind") not in selected_kinds:
                continue
            if not include_inactive and metadata.get("status", "active") != "active":
                continue
            records.append(item)
        return records

    def search(
        self,
        project: dict[str, Any],
        query: str,
        *,
        scope: str | None = None,
        scope_id: str | None = None,
        kinds: Iterable[str] = (),
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        terms = [item for item in re.split(r"\s+", query.casefold().strip()) if item]
        candidates = self.list(
            project,
            scope=scope,
            scope_id=scope_id,
            kinds=kinds,
        )
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for item in candidates:
            haystack = (
                json.dumps(item["metadata"], ensure_ascii=False, sort_keys=True)
                + "\n"
                + item["content"]
            ).casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            score = sum(haystack.count(term) for term in terms) if terms else 0
            scored.append((score, item["reference"], item))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored] if limit is None else [item[2] for item in scored[:limit]]

    def supersede(
        self,
        project: dict[str, Any],
        reference: str,
        replacement: MemoryRecord,
    ) -> dict[str, Any]:
        current = self.read(project, reference)
        metadata = dict(current["metadata"])
        metadata["status"] = "superseded"
        metadata["superseded_by"] = replacement.id
        metadata["updated_at"] = _now()
        current_path = (self.root(project) / current["reference"]).resolve()
        _atomic_write(current_path, _render(metadata, current["content"]))
        next_revision = max(int(metadata.get("revision") or 1) + 1, replacement.revision)
        return self.write(project, replacement.model_copy(update={"revision": next_revision}))

    def snapshot(
        self,
        project: dict[str, Any],
        *,
        scope: str,
        scope_id: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        self.initialize_project(project)
        _safe_segment(scope)
        _safe_segment(scope_id)
        root = self.root(project) / "snapshots" / scope / scope_id
        root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        path = root / f"{digest}.json"
        if not path.exists():
            _atomic_write(path, payload + "\n")
        return {
            "provider": self.name,
            "reference": path.relative_to(self.root(project)).as_posix(),
            "sha256": digest,
        }

    def restore(self, project: dict[str, Any], reference: str) -> dict[str, Any]:
        root = self.root(project)
        candidate = (root / reference).resolve()
        _require_within(root, candidate)
        if not candidate.is_file() or candidate.suffix.lower() != ".json":
            raise FileNotFoundError(reference)
        value = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Memory snapshot must contain an object")
        return value

    def _record_path(self, project: dict[str, Any], record: MemoryRecord) -> Path:
        _safe_segment(record.id)
        _safe_segment(record.scope)
        scope_id = record.scope_id or "_"
        _safe_segment(scope_id)
        return self.root(project) / "records" / record.scope / scope_id / f"{record.id}.md"

    def _read_path(self, project: dict[str, Any], path: Path) -> dict[str, Any]:
        root = self.root(project)
        resolved = path.resolve()
        _require_within(root, resolved)
        raw = resolved.read_text(encoding="utf-8")
        metadata, content = _parse(raw)
        return {
            "provider": self.name,
            "reference": resolved.relative_to(root).as_posix(),
            "metadata": metadata,
            "content": content,
            "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        }


class MemoryManager:
    def __init__(
        self,
        store: MemoryStore | None = None,
        index: MemoryIndex | None = None,
    ):
        self.store = store or FileMemoryStore()
        self.index = index or TextMemoryIndex()

    def initialize_project(self, project: dict[str, Any]) -> dict[str, Any]:
        return self.store.initialize_project(project)

    def build_context(
        self,
        project: dict[str, Any],
        *,
        selectors: Iterable[MemorySelector] = (),
        task_id: str | None = None,
        workcell_id: str | None = None,
        participant_key: str | None = None,
        include_core: bool = True,
    ) -> dict[str, Any]:
        self.store.initialize_project(project)
        references: list[dict[str, Any]] = []
        seen: set[str] = set()
        if include_core:
            for name, _title, _purpose in _CORE_FILES:
                item = self.store.read(project, name)
                if not item["content"].strip():
                    continue
                item["reason"] = "project_core"
                references.append(item)
                seen.add(item["reference"])
        omitted: list[dict[str, Any]] = []
        for selector in selectors:
            scope_id = _scope_id(selector.scope, task_id, workcell_id, participant_key)
            matches = self.index.search(
                self.store,
                project,
                selector.query,
                scope=selector.scope,
                scope_id=scope_id,
                kinds=selector.kinds,
                limit=selector.limit,
            )
            if selector.required and not matches:
                raise ValueError(
                    f"required Memory selector returned no records: scope={selector.scope!r} query={selector.query!r}"
                )
            for item in matches:
                if item["reference"] in seen:
                    continue
                item["reason"] = f"selector:{selector.scope}:{selector.query}"
                references.append(item)
                seen.add(item["reference"])
        manifest = {
            "store_provider": self.store.name,
            "index_provider": self.index.name,
            "selected": [
                {
                    "reference": item["reference"],
                    "id": item["metadata"].get("id"),
                    "scope": item["metadata"].get("scope"),
                    "kind": item["metadata"].get("kind"),
                    "sha256": item["sha256"],
                    "reason": item["reason"],
                }
                for item in references
            ],
            "omitted": omitted,
        }
        return {
            "records": [
                {
                    "reference": item["reference"],
                    "metadata": item["metadata"],
                    "content": item["content"],
                }
                for item in references
            ],
            "manifest": manifest,
        }


def _scope_id(
    scope: str,
    task_id: str | None,
    workcell_id: str | None,
    participant_key: str | None,
) -> str | None:
    if scope == "task":
        return task_id
    if scope == "workcell":
        return workcell_id
    if scope == "participant":
        if not workcell_id or not participant_key:
            return None
        return f"{workcell_id}.{participant_key}"
    return None


def _safe_segment(value: str) -> None:
    if not _SAFE_SEGMENT.fullmatch(value):
        raise ValueError(f"invalid Memory path segment: {value!r}")


def _require_within(root: Path, candidate: Path) -> None:
    resolved_root = root.resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Memory reference escapes the Project Memory root") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _render(metadata: dict[str, Any], content: str) -> str:
    header = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=True,
        default_flow_style=False,
    ).strip()
    return f"---\n{header}\n---\n\n{content.rstrip()}\n"


def _parse(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end < 0:
        raise ValueError("Memory Markdown has invalid YAML front matter")
    metadata = yaml.safe_load(raw[4:end]) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Memory front matter must be an object")
    return dict(metadata), raw[end + 5:].lstrip("\n")


def _front_matter(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    return _parse(raw)[0]


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)

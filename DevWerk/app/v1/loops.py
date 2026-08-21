"""Filesystem discovery for reusable DevWerk Loops."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


_HEADING = re.compile(r"^##\s+(.+?)(?::)?\s*(?:<br\s*/?>)?\s*$", re.IGNORECASE)
_TITLE = re.compile(r"^#\s+(.+?)\s*$")
_BREAK = re.compile(r"\s*<br\s*/?>\s*", re.IGNORECASE)
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")


def _field_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _clean_line(value: str) -> str:
    return _BREAK.sub("", value).strip()


def _parse_loop_meta(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    title = ""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        if not title:
            match = _TITLE.match(raw_line.strip())
            if match and not raw_line.lstrip().startswith("##"):
                title = match.group(1).strip()
                continue
        heading = _HEADING.match(raw_line.strip())
        if heading:
            current = _field_key(heading.group(1))
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(raw_line)

    aliases = {
        "loop_version_s": "loop_version",
        "loop_version": "loop_version",
        "loop_key": "loop_key",
        "use_case": "use_case",
        "selection_guide": "selection_guide",
        "loop_input": "loop_input",
        "loop_output": "loop_output",
    }
    normalized: dict[str, list[str]] = {}
    for key, value in sections.items():
        normalized[aliases.get(key, key)] = value

    def text_field(name: str) -> str:
        lines = [_clean_line(line) for line in normalized.get(name, [])]
        return "\n".join(line for line in lines if line).strip()

    tags = []
    for line in normalized.get("tags", []):
        value = _clean_line(line)
        if not value:
            continue
        value = re.sub(r"^[-*+]\s+", "", value).strip()
        tags.extend(item.strip() for item in value.split(",") if item.strip())

    record = {
        "loop_key": text_field("loop_key"),
        "version": text_field("loop_version"),
        "name": title,
        "description": text_field("description"),
        "publisher": text_field("publisher"),
        "category": text_field("category"),
        "tags": tags,
        "use_case": text_field("use_case"),
        "selection_guide": text_field("selection_guide"),
        "input": text_field("loop_input"),
        "output": text_field("loop_output"),
        "meta": text,
    }
    required = ("loop_key", "version", "name", "description", "publisher", "category", "use_case", "selection_guide")
    missing = [field for field in required if not record[field]]
    if missing:
        raise ValueError(f"Loop metadata {path} is missing: {', '.join(missing)}")
    if not _KEY.fullmatch(record["loop_key"]):
        raise ValueError(f"Loop metadata {path} has invalid Loop Key {record['loop_key']!r}")
    if not _VERSION.fullmatch(record["version"]):
        raise ValueError(f"Loop metadata {path} has invalid semantic version {record['version']!r}")
    if not tags:
        raise ValueError(f"Loop metadata {path} must declare at least one tag")
    return record


class LoopCatalog:
    """Discovers Loop cards and executable bundles directly from one directory."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or Path(__file__).resolve().parents[2] / "loops").resolve()

    def _records(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        records: list[dict[str, Any]] = []
        keys: set[str] = set()
        for directory in sorted(path for path in self.root.iterdir() if path.is_dir()):
            meta_path = directory / "loop.meta"
            if not meta_path.is_file():
                continue
            record = _parse_loop_meta(meta_path)
            if record["loop_key"] in keys:
                raise ValueError(f"duplicate Loop Key {record['loop_key']!r} under {self.root}")
            keys.add(record["loop_key"])
            record["directory"] = directory.name
            record["meta_path"] = meta_path
            record["bundle_path"] = directory / "loop.json"
            records.append(record)
        return records

    @staticmethod
    def _summary(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: record[key]
            for key in (
                "loop_key", "version", "name", "description", "publisher", "category",
                "tags", "use_case", "selection_guide", "input", "output", "directory",
            )
        }

    def list(
        self,
        *,
        category: str | None = None,
        tag: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        records = self._records()
        if category:
            records = [item for item in records if item["category"] == category]
        if tag:
            needle = tag.casefold()
            records = [item for item in records if any(value.casefold() == needle for value in item["tags"])]
        if query:
            needle = query.casefold()
            records = [
                item for item in records
                if needle in "\n".join(
                    [
                        item["loop_key"], item["name"], item["description"], item["category"],
                        item["use_case"], item["selection_guide"], *item["tags"],
                    ]
                ).casefold()
            ]
        return [self._summary(item) for item in records[:limit]]

    def get(self, loop_key: str) -> dict[str, Any]:
        record = next((item for item in self._records() if item["loop_key"] == loop_key), None)
        if record is None:
            raise KeyError(f"Loop {loop_key!r} was not found")
        bundle_path = Path(record["bundle_path"])
        if not bundle_path.is_file():
            raise ValueError(f"Loop {loop_key!r} is missing loop.json")
        bundle_bytes = bundle_path.read_bytes()
        executable = json.loads(bundle_bytes.decode("utf-8"))
        if executable.get("schema_version") != "devwerk.loop.bundle.v1":
            raise ValueError(f"Loop {loop_key!r} has an unsupported bundle schema")
        if not isinstance(executable.get("parameter_schema"), dict) or not isinstance(executable.get("bundle"), dict):
            raise ValueError(f"Loop {loop_key!r} must declare parameter_schema and bundle objects")
        digest = hashlib.sha256(record["meta"].encode("utf-8") + b"\0" + bundle_bytes).hexdigest()
        result = self._summary(record)
        result.update({
            "digest": digest,
            "parameter_schema": executable["parameter_schema"],
            "bundle": executable["bundle"],
            "meta": record["meta"],
        })
        return result

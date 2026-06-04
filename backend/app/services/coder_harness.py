"""
Coder harness agent.

This is the first backend-owned coding harness. It consumes the IDE-provided
source map, identifies the current project/framework shape, and produces a
per-request writing skill for the model. Future versions can replace the local
heuristics with a memory-backed framework recognition agent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger("devwerk.coder_harness")


@dataclass(frozen=True)
class FrameworkProfile:
    name: str
    confidence: float
    evidence: list[str]
    languages: list[str]
    build_systems: list[str]


class CoderHarness:
    """
    Build a local, zero-token coder skill from source_map.

    The skill is intentionally textual because it is injected into the same
    message stream as workspace_summary and can be consumed by any LLM adapter.
    """

    def build_skill(self, workspace: dict[str, Any] | None) -> str | None:
        _log.debug("CoderHarness.build_skill: start workspace_type=%s", type(workspace).__name__)
        source_map = _extract_source_map(workspace)
        if not source_map:
            _log.debug("CoderHarness.build_skill: no source_map found; skip coder skill")
            return None

        _log_source_map(source_map)
        files = _source_files(source_map)
        _log.debug("CoderHarness.build_skill: normalized_files_count=%s", len(files))
        profile = self._detect_framework(files)
        skill = self._render_skill(profile, files)
        _log.debug(
            "CoderHarness.build_skill: generated framework=%s confidence=%.2f evidence=%s skill_chars=%s",
            profile.name,
            profile.confidence,
            profile.evidence,
            len(skill),
        )
        return skill

    def _detect_framework(self, files: list[dict[str, Any]]) -> FrameworkProfile:
        _log.debug("CoderHarness.detect: start files=%s", len(files))
        paths = {str(f.get("path") or "").replace("\\", "/") for f in files}
        imports = {
            str(imp)
            for f in files
            for imp in (f.get("imports") or [])
            if imp
        }
        languages = sorted({
            str(f.get("language") or "").lower()
            for f in files
            if f.get("language")
        })

        _log.debug("CoderHarness.detect: languages=%s", languages)
        _log.debug("CoderHarness.detect: sample_paths=%s", sorted(p for p in paths if p)[:30])
        _log.debug("CoderHarness.detect: sample_imports=%s", sorted(imports)[:30])

        evidence: list[str] = []
        build_systems: list[str] = []

        if "pom.xml" in paths:
            build_systems.append("maven")
            evidence.append("pom.xml")
            _log.debug("CoderHarness.detect: build signal maven via pom.xml")
        if any(p.endswith("build.gradle") or p.endswith("build.gradle.kts") for p in paths):
            build_systems.append("gradle")
            evidence.append("Gradle build file")
            _log.debug("CoderHarness.detect: build signal gradle via build.gradle(.kts)")
        if "package.json" in paths:
            build_systems.append("npm")
            evidence.append("package.json")
            _log.debug("CoderHarness.detect: build signal npm via package.json")

        has_intellij_plugin = (
            "src/main/resources/META-INF/plugin.xml" in paths
            or "idea-plugin/src/main/resources/META-INF/plugin.xml" in paths
        )
        has_fastapi_like_backend = (
            any(p.endswith("app/main.py") for p in paths)
            and any("/routes/" in f"/{p}" or p.endswith("routes/ide.py") for p in paths)
        )
        _log.debug(
            "CoderHarness.detect: feature_flags intellij_plugin=%s fastapi_like_backend=%s",
            has_intellij_plugin,
            has_fastapi_like_backend,
        )

        if has_intellij_plugin and has_fastapi_like_backend:
            evidence.extend(["IntelliJ plugin.xml", "FastAPI-style backend layout"])
            _log.debug("CoderHarness.detect: matched devwerk-monorepo evidence=%s", evidence)
            return FrameworkProfile("devwerk-monorepo", 0.9, evidence, languages, build_systems)

        if any(i.startswith("org.springframework") for i in imports):
            evidence.append("Spring imports")
            _log.debug("CoderHarness.detect: matched spring-boot evidence=%s", evidence)
            return FrameworkProfile("spring-boot", 0.9, evidence, languages, build_systems)

        if has_intellij_plugin:
            evidence.append("IntelliJ plugin.xml")
            _log.debug("CoderHarness.detect: matched intellij-plugin evidence=%s", evidence)
            return FrameworkProfile("intellij-plugin", 0.9, evidence, languages, build_systems)

        if has_fastapi_like_backend or (
            any(p.endswith("app/main.py") for p in paths)
            and any("fastapi" in p.lower() for p in paths | imports)
        ):
            evidence.append("FastAPI-style app entrypoint")
            _log.debug("CoderHarness.detect: matched fastapi evidence=%s", evidence)
            return FrameworkProfile("fastapi", 0.85, evidence, languages, build_systems)

        if any(p.endswith(".tsx") or p.endswith(".jsx") for p in paths):
            evidence.append("React-style JSX/TSX files")
            _log.debug("CoderHarness.detect: matched react evidence=%s", evidence)
            return FrameworkProfile("react", 0.7, evidence, languages, build_systems)

        if any(p.endswith(".vue") for p in paths):
            evidence.append("Vue single-file components")
            _log.debug("CoderHarness.detect: matched vue evidence=%s", evidence)
            return FrameworkProfile("vue", 0.75, evidence, languages, build_systems)

        if "py" in languages:
            evidence.append("Python source files")
            _log.debug("CoderHarness.detect: matched python evidence=%s", evidence)
            return FrameworkProfile("python", 0.55, evidence, languages, build_systems)

        if "java" in languages or "kt" in languages or "kts" in languages:
            evidence.append("JVM source files")
            _log.debug("CoderHarness.detect: matched jvm evidence=%s", evidence)
            return FrameworkProfile("jvm", 0.55, evidence, languages, build_systems)

        evidence.append("No framework-specific signals")
        _log.debug("CoderHarness.detect: matched generic evidence=%s", evidence)
        return FrameworkProfile("generic", 0.35, evidence, languages, build_systems)

    def _render_skill(self, profile: FrameworkProfile, files: list[dict[str, Any]]) -> str:
        top_paths = _representative_paths(files)
        rules = _framework_rules(profile.name)
        _log.debug(
            "CoderHarness.render: framework=%s representative_paths=%s rules_count=%s",
            profile.name,
            top_paths,
            len(rules),
        )

        return "\n".join([
            "coder_harness_skill:",
            f"  role: coder",
            f"  framework: {profile.name}",
            f"  confidence: {profile.confidence:.2f}",
            f"  languages: {', '.join(profile.languages) if profile.languages else 'unknown'}",
            f"  build_systems: {', '.join(profile.build_systems) if profile.build_systems else 'unknown'}",
            "  evidence:",
            *[f"    - {item}" for item in profile.evidence[:8]],
            "  representative_paths:",
            *[f"    - {path}" for path in top_paths],
            "  writing_rules:",
            *[f"    - {rule}" for rule in rules],
            "  invariant_rules:",
            "    - Treat source_map as an index, not full source content.",
            "    - Use source_map first to locate likely files, then request read_file for exact content before editing.",
            "    - Prefer patch_ops.apply_patch for existing files; use file ops only for creates/deletes or whole-file generation.",
            "    - Do not write outside approved frontend paths during execute.",
            "    - Preserve project-local architecture, package names, naming style, and build boundaries.",
        ])


def build_coder_skill(workspace: dict[str, Any] | None) -> str | None:
    return CoderHarness().build_skill(workspace)


def _extract_source_map(workspace: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(workspace, dict):
        _log.debug("CoderHarness.extract_source_map: workspace is not dict")
        return None
    source_map = workspace.get("source_map")
    if not isinstance(source_map, dict):
        _log.debug("CoderHarness.extract_source_map: source_map missing or invalid type=%s", type(source_map).__name__)
        return None
    _log.debug("CoderHarness.extract_source_map: source_map found")
    return source_map if isinstance(source_map, dict) else None


def _source_files(source_map: dict[str, Any]) -> list[dict[str, Any]]:
    files = source_map.get("files") or []
    normalized = [f for f in files if isinstance(f, dict)]
    _log.debug(
        "CoderHarness.source_files: raw_count=%s normalized_count=%s invalid_count=%s",
        len(files) if isinstance(files, list) else "not-list",
        len(normalized),
        (len(files) - len(normalized)) if isinstance(files, list) else "unknown",
    )
    return normalized


def _log_source_map(source_map: dict[str, Any]) -> None:
    files = source_map.get("files") or []
    file_count = len(files) if isinstance(files, list) else 0
    _log.debug(
        "CoderHarness.source_map: root=%s generated_at=%s total_files=%s indexed_files=%s skipped_files=%s files_payload_count=%s",
        source_map.get("root"),
        source_map.get("generated_at"),
        source_map.get("total_files"),
        source_map.get("indexed_files"),
        source_map.get("skipped_files"),
        file_count,
    )
    if isinstance(files, list):
        samples = []
        for f in files[:20]:
            if not isinstance(f, dict):
                continue
            samples.append({
                "path": f.get("path"),
                "language": f.get("language"),
                "kind": f.get("kind"),
                "symbols": len(f.get("symbols") or []),
                "imports": len(f.get("imports") or []),
            })
        _log.debug("CoderHarness.source_map: sample_files=%s", samples)


def _representative_paths(files: list[dict[str, Any]]) -> list[str]:
    preferred = []
    for f in files:
        path = str(f.get("path") or "")
        if not path:
            continue
        if path.endswith((
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "package.json",
            "plugin.xml",
            "main.py",
            "settings.gradle.kts",
        )):
            preferred.append(path)
    if preferred:
        return sorted(dict.fromkeys(preferred))[:12]

    return sorted(
        str(f.get("path") or "")
        for f in files
        if f.get("path")
    )[:12]


def _framework_rules(name: str) -> list[str]:
    common = [
        "Keep changes small and aligned with the existing module boundaries.",
        "Do not introduce a new framework or dependency unless the user explicitly asks for it.",
    ]
    specific = {
        "spring-boot": [
            "Follow existing package structure under src/main/java and src/test/java.",
            "Keep controller/service/repository responsibilities separated.",
            "Prefer constructor injection and existing Spring annotations already used in the project.",
        ],
        "devwerk-monorepo": [
            "Keep IntelliJ plugin code under idea-plugin and backend harness/service code under backend/app.",
            "Plugin changes should only collect IDE context, source maps, attachments, user approvals, and apply guarded CodeOps results.",
            "Backend changes should own model orchestration, coder harness rules, prompt construction, and framework intelligence.",
            "Do not reintroduce direct model clients or prompt engineering into the plugin.",
            "Do not bypass frontend snapshot and path-guard execution flow.",
        ],
        "intellij-plugin": [
            "Use IntelliJ Platform APIs and keep write actions inside WriteCommandAction.",
            "Keep UI work on Swing components and preserve tool-window responsiveness.",
            "Do not bypass DevWerk snapshot and path-guard execution flow.",
        ],
        "fastapi": [
            "Keep FastAPI routes thin; put reusable behavior in services.",
            "Use Pydantic models for request/response shape changes.",
            "Avoid filesystem writes outside configured local storage roots.",
        ],
        "react": [
            "Follow existing component and state-management conventions.",
            "Keep UI state local unless the app already uses a shared store.",
        ],
        "vue": [
            "Follow existing component/script/style organization.",
            "Keep props/events compatible with surrounding components.",
        ],
        "python": [
            "Keep route, service, model, and utility boundaries explicit.",
            "Prefer typed helper functions and avoid global mutable state.",
        ],
        "jvm": [
            "Follow existing package and Gradle/Maven module structure.",
            "Prefer small classes/functions and existing JVM idioms in the project.",
        ],
        "generic": [
            "Infer structure from source_map, tree_preview, and read_file before editing.",
            "If framework is unclear, make the least invasive change possible.",
        ],
    }
    return common + specific.get(name, specific["generic"])

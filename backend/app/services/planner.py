"""
Planner service.

Receives a ChatRequest (same shape as the existing /v1/ide/chat endpoint) and:
  1. Runs the normal agent loop; LLM can call tool_requests to research the codebase
  2. Intercepts the final response and extracts a file-level PlanFile list
  3. Returns a PlanResponse without executing any file operations

No file is written during the plan phase.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from app.models.plan import PlanFile, PlanResponse
from app.services.llm_factory import get_llm_client

_log = logging.getLogger("devwerk.planner")


class Planner:
    """
    Stateless planner.

    Calls the LLM with a prompt that instructs it to:
      - research the codebase via tool_calls
      - output a JSON plan in the final response (no file writes)
    """

    PLAN_INSTRUCTION = (
        "You are DevWerk's PLANNER. Your job is to research the codebase "
        "and produce a FILE-LEVEL change plan - NOT to write any files.\n\n"
        "Rules:\n"
        "  1. You may call tools (list_dir, read_file, search) to understand the codebase.\n"
        "     If workspace_summary.source_map exists, use it first to identify files, packages, classes, methods, entrypoints, and dependencies.\n"
        "     If coder_harness_skill exists, use its writing rules when deciding which files belong in the plan.\n"
        "  2. When you have enough information, respond with a JSON object "
        "containing a 'plan' key with this shape:\n"
        "     { plan: { files: [{path, nature, description, confidence}], "
        "                summary, warnings } }\n"
        "  3. nature must be one of: new | modified | deleted\n"
        "  4. confidence is 0.0-1.0 how sure you are this file needs to change.\n"
        "  5. Do NOT output any ops, patch_ops, or tool_requests in your final response.\n"
        "  6. summary is one line; warnings[] lists any risky files.\n"
    )

    def __init__(self, agent_name: str = "planner", event_sink: Callable[[str, dict[str, Any]], None] | None = None):
        self.agent_name = agent_name
        self.event_sink = event_sink

    def plan(self, messages: list[dict], mode: str = "agent") -> PlanResponse:
        """
        Run the planner.

        Args:
            messages: Chat messages, same format as /v1/ide/chat.
                      The LAST message must be the user's request.
            mode: "agent" (default) or "scaffold".

        Returns:
            PlanResponse with files=[], summary, warnings.
        """
        injected_messages = _inject_plan_instruction(list(messages), mode)
        _log.debug(
            "Planner.plan: start mode=%s input_messages=%s injected_messages=%s",
            mode,
            len(messages),
            len(injected_messages),
        )

        max_rounds = 4
        backoff = 0.5

        for attempt in range(max_rounds):
            try:
                _log.debug("Planner.plan: attempt=%s/%s calling_llm", attempt + 1, max_rounds)
                self._emit_event(
                    "plan_llm_round_started",
                    {
                        "round": attempt + 1,
                        "max_rounds": max_rounds,
                        "mode": mode,
                        "agent": self.agent_name,
                        "input": {
                            "message_count": len(injected_messages),
                            "roles": [str(m.get("role") or "") for m in injected_messages],
                            "last_user_chars": len(_last_user_text(injected_messages)),
                        },
                    },
                )
                result = self._call_llm(injected_messages)
                self._emit_event(
                    "plan_llm_round_result",
                    {
                        "round": attempt + 1,
                        "agent": self.agent_name,
                        "output": _raw_result_summary(result),
                    },
                )
                _log.debug("Planner.plan: attempt=%s raw_result_keys=%s", attempt + 1, sorted(result.keys()))
                plan = self._extract_plan(result, messages)
                self._emit_event(
                    "plan_llm_round_extracted",
                    {
                        "round": attempt + 1,
                        "agent": self.agent_name,
                        "result": {
                            "ok": plan.ok,
                            "file_count": len(plan.files),
                            "files": [f.path for f in plan.files],
                            "warnings": plan.warnings,
                            "summary": plan.summary,
                            "error_code": plan.error_code,
                        },
                    },
                )
                _log.debug(
                    "Planner.plan: extracted ok=%s files=%s warnings=%s summary=%s",
                    plan.ok,
                    len(plan.files),
                    len(plan.warnings),
                    plan.summary,
                )
                return plan

            except Exception as exc:  # noqa: BLE001
                is_timeout = (
                    "ReadTimeout" in type(exc).__name__
                    or "timeout" in str(exc).lower()
                )
                if attempt < max_rounds - 1 and is_timeout:
                    time.sleep(backoff * (attempt + 1))
                    continue

                _log.warning("Planner failed: %s", exc)
                self._emit_event(
                    "plan_llm_round_failed",
                    {
                        "round": attempt + 1,
                        "agent": self.agent_name,
                        "error": f"{type(exc).__name__}: {exc}",
                        "retryable": attempt < max_rounds - 1 and is_timeout,
                    },
                )
                return PlanResponse(
                    ok=False,
                    files=[],
                    error_code="PLAN_ERROR",
                    error_message=f"{type(exc).__name__}: {exc}",
                )

        return PlanResponse(
            ok=False,
            files=[],
            error_code="PLAN_EXHAUSTED",
            error_message="Planner ran out of research rounds.",
        )

    def _call_llm(self, messages: list[dict]) -> dict:
        _log.debug("Planner.call_llm: agent=%s messages=%s", self.agent_name, len(messages))
        client = get_llm_client(self.agent_name)
        result = client.chat_json(messages)
        _log.debug("Planner.call_llm: result_keys=%s", sorted(result.keys()))
        return result

    def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(event_type, payload)
        except Exception as exc:  # noqa: BLE001
            _log.debug("Planner event sink failed event=%s error=%s", event_type, exc)

    @staticmethod
    def _extract_plan(raw: dict, messages: list[dict] | None = None) -> PlanResponse:
        """
        Pull the plan out of the LLM JSON output.

        The LLM is asked to wrap its plan in { plan: { files, summary, warnings } }.
        We are lenient: if the top-level already has those keys we use them directly.
        """
        plan_obj = raw.get("plan") or raw
        if not isinstance(plan_obj, dict):
            return _fallback_plan(raw, messages or [])

        files_raw: list[dict] = plan_obj.get("files") or []
        _log.debug(
            "Planner.extract_plan: raw_files=%s plan_keys=%s",
            len(files_raw) if isinstance(files_raw, list) else "not-list",
            sorted(plan_obj.keys()) if isinstance(plan_obj, dict) else type(plan_obj).__name__,
        )
        files = []
        for f in files_raw:
            if not isinstance(f, dict):
                continue
            path = (f.get("path") or "").strip()
            if not path:
                continue
            try:
                files.append(PlanFile(
                    path=path,
                    nature=f.get("nature") or "modified",
                    description=str(f.get("description") or "").strip(),
                    confidence=float(f.get("confidence") or 0.8),
                ))
            except Exception:
                continue

        summary = str(plan_obj.get("summary") or "").strip()
        warnings_raw = plan_obj.get("warnings") or []
        warnings = [str(w) for w in warnings_raw if w]

        if not files:
            fallback = _fallback_plan(raw, messages or [])
            if fallback.files:
                fallback.summary = summary or fallback.summary
                fallback.warnings = warnings or fallback.warnings
                return fallback

        if not summary and files:
            n = len(files)
            summary = f"{n} file{'s' if n != 1 else ''} to change - review before executing."

        return PlanResponse(
            ok=True,
            files=files,
            summary=summary,
            warnings=warnings,
        )


def _inject_plan_instruction(messages: list[dict], mode: str) -> list[dict]:
    """
    Prepend a system message with the planner instruction so the LLM
    understands it should research and output a plan, not execute.
    """
    system_content = Planner.PLAN_INSTRUCTION

    if messages and messages[0].get("role", "").lower() == "system":
        merged = messages[0]["content"] + "\n\n" + system_content
        return [{"role": "system", "content": merged}] + messages[1:]

    return [{"role": "system", "content": system_content}] + messages


def _raw_result_summary(raw: dict) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"type": type(raw).__name__}
    text = str(raw.get("raw_text") or raw.get("reply") or raw.get("content") or "")
    plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else raw
    files = plan.get("files") if isinstance(plan, dict) else []
    return {
        "keys": sorted(raw.keys()),
        "raw_text_chars": len(text),
        "raw_text_preview": text[:240],
        "file_count": len(files) if isinstance(files, list) else 0,
        "tool_request_count": len(raw.get("tool_requests") or []) if isinstance(raw.get("tool_requests") or [], list) else 0,
    }


def _fallback_plan(raw: dict, messages: list[dict]) -> PlanResponse:
    user_text = _last_user_text(messages)
    workspace = _last_workspace(messages)
    tree_preview = str(workspace.get("tree_preview") or "") if isinstance(workspace, dict) else ""
    is_empty_project = not tree_preview.strip() or tree_preview.strip() in {".", "<empty>", "(empty)"}
    text = f"{user_text}\n{tree_preview}".lower()
    raw_keys = sorted(raw.keys()) if isinstance(raw, dict) else []
    _log.debug(
        "Planner.fallback: raw_keys=%s user_chars=%s tree_chars=%s is_empty_project=%s",
        raw_keys,
        len(user_text),
        len(tree_preview),
        is_empty_project,
    )

    if _looks_like_spring_boot(text):
        files = [
            PlanFile(path="build.gradle", nature="new" if is_empty_project else "modified", description="Configure Spring Boot, Java 21, and Gradle."),
            PlanFile(path="settings.gradle", nature="new" if is_empty_project else "modified", description="Set the Gradle project name."),
            PlanFile(path="src/main/java/com/devwerk/demo/DemoApplication.java", nature="new", description="Add the Spring Boot application entrypoint."),
            PlanFile(path="src/main/java/com/devwerk/demo/HelloController.java", nature="new", description="Add a minimal REST hello endpoint."),
        ]
        return PlanResponse(
            ok=True,
            files=files,
            summary="Create a minimal Java 21 Spring Boot REST API scaffold.",
            warnings=["Planner LLM returned non-plan text; generated deterministic Spring Boot fallback plan."],
        )

    if _looks_like_user_management_request(text):
        files = _user_management_files(tree_preview)
        _log.debug("Planner.fallback: inferred user_management files=%s", [f.path for f in files])
        return PlanResponse(
            ok=True,
            files=files,
            summary="Add an extensible user management module with registration and user CRUD.",
            warnings=[
                "Planner LLM returned no file-level plan; generated deterministic fallback plan from user intent and workspace tree.",
                "Permission behavior needs an extensible policy/service boundary because no concrete auth design exists yet.",
            ],
        )

    target_paths = _mentioned_paths(user_text)
    files = [
        PlanFile(path=path, nature="new" if is_empty_project else "modified", description="Implement the requested change.")
        for path in target_paths
    ]
    if files:
        return PlanResponse(
            ok=True,
            files=files,
            summary="Implement the requested code change.",
            warnings=["Planner LLM returned non-plan text; generated fallback plan from requested paths."],
        )

    if _looks_like_code_change_request(text):
        files = _generic_code_change_files(user_text, tree_preview, is_empty_project)
        _log.debug("Planner.fallback: inferred generic_code_change files=%s", [f.path for f in files])
        return PlanResponse(
            ok=True,
            files=files,
            summary="Implement the requested code change.",
            warnings=[
                "Planner LLM returned no file-level plan; generated conservative fallback files from workspace tree.",
            ],
        )

    raw_text = str(raw.get("raw_text") or raw.get("reply") or "").strip()
    return PlanResponse(
        ok=False,
        files=[],
        summary=raw_text[:240] or "",
        warnings=["Planner LLM returned non-plan text and no deterministic file plan could be inferred."],
        error_code="PLAN_EMPTY",
        error_message="Planner produced no file-level plan for this coding request.",
    )


def _last_user_text(messages: list[dict]) -> str:
    for item in reversed(messages):
        if isinstance(item, dict) and str(item.get("role") or "").lower() == "user":
            content = str(item.get("content") or "")
            if not content.startswith(("workspace_summary:", "coder_harness_skill:")):
                return content
    return ""


def _last_workspace(messages: list[dict]) -> dict:
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if not content.startswith("workspace_summary:"):
            continue
        raw = content.split("workspace_summary:", 1)[1].strip()
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {}


def _looks_like_spring_boot(text: str) -> bool:
    return any(term in text for term in ("springboot", "spring boot", "spring-boot"))


def _looks_like_user_management_request(text: str) -> bool:
    user_terms = (
        "用户",
        "user",
        "account",
        "member",
    )
    feature_terms = (
        "注册",
        "register",
        "crud",
        "增删改查",
        "新增",
        "删除",
        "修改",
        "查询",
        "create",
        "delete",
        "update",
        "list",
    )
    return any(term in text for term in user_terms) and any(term in text for term in feature_terms)


def _looks_like_code_change_request(text: str) -> bool:
    terms = (
        "加一个",
        "添加",
        "新增",
        "实现",
        "修改",
        "删除",
        "重构",
        "修复",
        "生成",
        "创建",
        "build",
        "create",
        "add ",
        "implement",
        "modify",
        "update",
        "fix ",
        "refactor",
    )
    return any(term in text for term in terms)


def _user_management_files(tree_preview: str) -> list[PlanFile]:
    base_package_path = _detect_java_package_path(tree_preview) or "src/main/java/com/devwerk/demo"
    resource_root = _detect_resource_root(tree_preview) or "src/main/resources"
    existing_pom = _has_tree_file(tree_preview, "pom.xml")

    return [
        PlanFile(
            path="pom.xml",
            nature="modified" if existing_pom else "new",
            description="Ensure Spring Web and validation dependencies support the user-management API.",
            confidence=0.72,
        ),
        PlanFile(
            path=f"{base_package_path}/user/User.java",
            nature="new",
            description="Add the user domain model used by registration and CRUD operations.",
            confidence=0.88,
        ),
        PlanFile(
            path=f"{base_package_path}/user/UserCreateRequest.java",
            nature="new",
            description="Add request DTO for registration and create-user operations.",
            confidence=0.84,
        ),
        PlanFile(
            path=f"{base_package_path}/user/UserUpdateRequest.java",
            nature="new",
            description="Add request DTO for updating user profile data.",
            confidence=0.84,
        ),
        PlanFile(
            path=f"{base_package_path}/user/UserPermissionPolicy.java",
            nature="new",
            description="Add an extensible permission policy boundary for user CRUD operations.",
            confidence=0.86,
        ),
        PlanFile(
            path=f"{base_package_path}/user/UserService.java",
            nature="new",
            description="Implement registration and in-memory user CRUD business logic.",
            confidence=0.9,
        ),
        PlanFile(
            path=f"{base_package_path}/user/UserController.java",
            nature="new",
            description="Expose REST endpoints for registration and user CRUD.",
            confidence=0.9,
        ),
        PlanFile(
            path=f"{resource_root}/application.properties",
            nature="modified" if _has_tree_file(tree_preview, "application.properties") else "new",
            description="Keep minimal application configuration for the user-management API.",
            confidence=0.62,
        ),
    ]


def _generic_code_change_files(user_text: str, tree_preview: str, is_empty_project: bool) -> list[PlanFile]:
    if _looks_like_spring_project(tree_preview):
        base_package_path = _detect_java_package_path(tree_preview) or "src/main/java/com/devwerk/demo"
        return [
            PlanFile(
                path=f"{base_package_path}/DevWerkGeneratedFeature.java",
                nature="new",
                description=f"Implement requested feature: {user_text[:120]}",
                confidence=0.55,
            ),
            PlanFile(
                path="pom.xml",
                nature="modified" if _has_tree_file(tree_preview, "pom.xml") else "new",
                description="Adjust project dependencies or build metadata if the feature requires it.",
                confidence=0.45,
            ),
        ]

    return [
        PlanFile(
            path="README.md" if is_empty_project else "devwerk-generated-plan.md",
            nature="new" if is_empty_project else "modified",
            description=f"Conservative placeholder plan for requested code change: {user_text[:120]}",
            confidence=0.35,
        )
    ]


def _looks_like_spring_project(tree_preview: str) -> bool:
    lower = tree_preview.lower()
    return "pom.xml" in lower or "build.gradle" in lower or "src/main/java" in lower


def _detect_java_package_path(tree_preview: str) -> str:
    paths = _tree_paths(tree_preview)
    java_files = [
        path
        for path in paths
        if path.startswith("src/main/java/") and path.endswith(".java")
    ]
    app_files = [path for path in java_files if path.lower().endswith("application.java")]
    chosen = (app_files or java_files or [""])[0]
    if chosen:
        parent = chosen.rsplit("/", 1)[0]
        if parent and parent != "src/main/java":
            if parent.endswith("/controller"):
                return parent.rsplit("/", 1)[0]
            return parent
    package_dirs = [
        path
        for path in paths
        if path.startswith("src/main/java/") and not path.endswith(".java")
    ]
    if package_dirs:
        chosen_dir = max(package_dirs, key=lambda item: item.count("/"))
        if chosen_dir.endswith("/controller"):
            return chosen_dir.rsplit("/", 1)[0]
        return chosen_dir
    return ""


def _detect_resource_root(tree_preview: str) -> str:
    for path in _tree_paths(tree_preview):
        if path.startswith("src/main/resources"):
            return "src/main/resources"
    return ""


def _has_tree_file(tree_preview: str, filename: str) -> bool:
    wanted = filename.lower()
    return any(path.lower().endswith("/" + wanted) or path.lower() == wanted for path in _tree_paths(tree_preview))


def _tree_paths(tree_preview: str) -> list[str]:
    lines = [line.rstrip() for line in tree_preview.splitlines() if line.strip()]
    stack: list[tuple[int, str]] = []
    paths: list[str] = []

    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        name = line.strip().rstrip("/")
        if not name:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, name))
        parts = [item[1] for item in stack]
        normalized = _strip_project_root_from_tree_path("/".join(parts))
        if normalized:
            paths.append(normalized)

    return paths


def _strip_project_root_from_tree_path(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if not parts:
        return ""
    anchors = {
        "src",
        "app",
        "backend",
        "frontend",
        "idea-plugin",
        "pom.xml",
        "build.gradle",
        "settings.gradle",
        ".gitignore",
        "README.md",
    }
    if len(parts) > 1 and parts[0] not in anchors:
        parts = parts[1:]
    return "/".join(parts)


def _mentioned_paths(text: str) -> list[str]:
    candidates = re.findall(r"[A-Za-z0-9_./-]+\.(?:java|kt|py|js|ts|json|md|gradle|xml|yml|yaml)", text)
    out: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = path.strip().strip("`'\"").lstrip("./")
        if not normalized or normalized in seen or ".." in normalized.split("/"):
            continue
        seen.add(normalized)
        out.append(normalized)
    return out[:20]

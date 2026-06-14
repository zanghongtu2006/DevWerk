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

    def __init__(self, agent_name: str = "planner"):
        self.agent_name = agent_name

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
                result = self._call_llm(injected_messages)
                _log.debug("Planner.plan: attempt=%s raw_result_keys=%s", attempt + 1, sorted(result.keys()))
                plan = self._extract_plan(result, messages)
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
                fallback.warnings = warnings
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


def _fallback_plan(raw: dict, messages: list[dict]) -> PlanResponse:
    user_text = _last_user_text(messages)
    workspace = _last_workspace(messages)
    tree_preview = str(workspace.get("tree_preview") or "") if isinstance(workspace, dict) else ""
    is_empty_project = not tree_preview.strip() or tree_preview.strip() in {".", "<empty>", "(empty)"}
    text = f"{user_text}\n{tree_preview}".lower()

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

    raw_text = str(raw.get("raw_text") or raw.get("reply") or "").strip()
    return PlanResponse(
        ok=True,
        files=[],
        summary=raw_text[:240] or "No file-level plan could be inferred.",
        warnings=["Planner LLM returned non-plan text and no deterministic file plan could be inferred."],
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

"""
Planner service.

The planner is intentionally evidence-driven. It may ask the model to research
the codebase and return a file-level plan, but it must not infer business or
framework directory structures in backend code.
"""

from __future__ import annotations

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
      - research the codebase via tool calls when needed
      - output a JSON plan in the final response
      - avoid file writes during planning
    """

    PLAN_INSTRUCTION = (
        "You are DevWerk's PLANNER. Your job is to research the codebase "
        "and produce a FILE-LEVEL change plan - NOT to write any files.\n\n"
        "Rules:\n"
        "  1. Use code_context_summary/source_map first when available. They are IDE-provided facts, not full file contents.\n"
        "  2. Do not invent directories, packages, modules, or framework conventions. If the exact target path is unclear, request tools or return no plan.\n"
        "  3. You may call tools (list_dir, read_file, search) to understand the codebase.\n"
        "  4. When you have enough information, respond with a JSON object containing a 'plan' key:\n"
        "     { plan: { files: [{path, nature, description, confidence}], summary, warnings } }\n"
        "  5. nature must be one of: new | modified | deleted.\n"
        "  6. confidence is 0.0-1.0 how sure you are this file needs to change.\n"
        "  7. Do NOT output any ops, patch_ops, or tool_requests in your final response.\n"
        "  8. summary is one line; warnings[] lists any risky or missing context.\n"
    )

    def __init__(self, agent_name: str = "planner", event_sink: Callable[[str, dict[str, Any]], None] | None = None):
        self.agent_name = agent_name
        self.event_sink = event_sink

    def plan(self, messages: list[dict], mode: str = "agent") -> PlanResponse:
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
                    {"round": attempt + 1, "agent": self.agent_name, "output": _raw_result_summary(result)},
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
                is_timeout = "ReadTimeout" in type(exc).__name__ or "timeout" in str(exc).lower()
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
        plan_obj = raw.get("plan") or raw
        if not isinstance(plan_obj, dict):
            return _fallback_plan(raw, messages or [])

        files_raw: list[dict] = plan_obj.get("files") or []
        _log.debug(
            "Planner.extract_plan: raw_files=%s plan_keys=%s",
            len(files_raw) if isinstance(files_raw, list) else "not-list",
            sorted(plan_obj.keys()) if isinstance(plan_obj, dict) else type(plan_obj).__name__,
        )
        files: list[PlanFile] = []
        if isinstance(files_raw, list):
            for item in files_raw:
                if not isinstance(item, dict):
                    continue
                path = _safe_rel_path(item.get("path"))
                if not path:
                    continue
                try:
                    files.append(
                        PlanFile(
                            path=path,
                            nature=item.get("nature") or "modified",
                            description=str(item.get("description") or "").strip(),
                            confidence=float(item.get("confidence") or 0.8),
                        )
                    )
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

        if not summary:
            n = len(files)
            summary = f"{n} file{'s' if n != 1 else ''} to change - review before executing."

        return PlanResponse(ok=True, files=files, summary=summary, warnings=warnings)


def _inject_plan_instruction(messages: list[dict], mode: str) -> list[dict]:
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
    raw_keys = sorted(raw.keys()) if isinstance(raw, dict) else []
    raw_text = str(raw.get("raw_text") or raw.get("reply") or "").strip() if isinstance(raw, dict) else ""
    target_paths = _mentioned_paths(user_text)
    _log.debug(
        "Planner.fallback: raw_keys=%s user_chars=%s raw_text_chars=%s explicit_paths=%s",
        raw_keys,
        len(user_text),
        len(raw_text),
        target_paths,
    )

    if target_paths:
        return PlanResponse(
            ok=True,
            files=[
                PlanFile(
                    path=path,
                    nature="modified",
                    description="User explicitly referenced this project-relative path.",
                    confidence=0.55,
                )
                for path in target_paths
            ],
            summary="Plan limited to explicitly referenced project paths.",
            warnings=["Planner returned no structured file plan; DevWerk did not infer any additional paths."],
        )

    return PlanResponse(
        ok=False,
        files=[],
        summary=raw_text[:240],
        warnings=["Planner returned no structured file-level plan; backend refused to infer business or framework paths."],
        error_code="PLAN_EMPTY",
        error_message=(
            "Planner produced no file-level plan from source_map/tool evidence. "
            "Retry with more workspace context or let the planner request tools."
        ),
    )


def _last_user_text(messages: list[dict]) -> str:
    for item in reversed(messages):
        if isinstance(item, dict) and str(item.get("role") or "").lower() == "user":
            content = str(item.get("content") or "")
            if not content.startswith(("workspace_summary:", "code_context_summary:", "code_context_skill:")):
                return content
    return ""


def _mentioned_paths(text: str) -> list[str]:
    candidates = re.findall(r"(?<![\w.-])(?:\.\/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+(?![\w.-])", text)
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _safe_rel_path(candidate.strip("`'\""))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
        if len(out) >= 20:
            break
    return out


def _safe_rel_path(value: object) -> str:
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    parts = [part for part in path.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)

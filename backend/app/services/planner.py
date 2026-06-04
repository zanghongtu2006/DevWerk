"""
Planner service.

Receives a ChatRequest (same shape as the existing /v1/ide/chat endpoint) and:
  1. Runs the normal agent loop — LLM can call tool_requests to research the codebase
  2. Intercepts the final response and extracts a file-level PlanFile list
  3. Returns a PlanResponse without executing any file operations

No file is written during the plan phase.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.core.config import settings
from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.models.plan import PlanFile, PlanResponse
from app.services.validation import validate_model_response

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
        "and produce a FILE-LEVEL change plan — NOT to write any files.\n\n"
        "Rules:\n"
        "  1. You may call tools (list_dir, read_file, search) to understand the codebase.\n"
        "     If workspace_summary.source_map exists, use it first to identify files, packages, classes, methods, entrypoints, and dependencies.\n"
        "     If coder_harness_skill exists, use its writing rules when deciding which files belong in the plan.\n"
        "  2. When you have enough information, respond with a JSON object "
        "containing a 'plan' key with this shape:\n"
        "     { plan: { files: [{path, nature, description, confidence}], "
        "                summary, warnings } }\n"
        "  3. nature must be one of: new | modified | deleted\n"
        "  4. confidence is 0.0–1.0 how sure you are this file needs to change.\n"
        "  5. Do NOT output any ops, patch_ops, or tool_requests in your final response.\n"
        "  6. summary is one line; warnings[] lists any risky files.\n"
    )

    def __init__(self, config: dict | None = None):
        if config:
            self.base_url: str = config.get("base_url", "http://127.0.0.1:11434").rstrip("/")
            self.model: str = config.get("model", "deepseek-r1:32b")
            self.timeout: float = float(config.get("timeout", 180.0))
            self.enable_schema: bool = bool(config.get("enable_schema", True))
        else:
            cfg = settings()
            self.base_url = cfg.ollama_base_url.rstrip("/")
            self.model = cfg.ollama_model
            self.timeout = float(cfg.ollama_timeout)
            self.enable_schema = cfg.ollama_enable_schema

        import requests as http_requests
        self._http = http_requests

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
                plan = self._extract_plan(result)
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
        """Single LLM call — same protocol as OllamaClient.chat_structured."""
        import requests as http_requests

        _log.debug(
            "Planner.call_llm: base_url=%s model=%s enable_schema=%s messages=%s timeout=%s",
            self.base_url,
            self.model,
            self.enable_schema,
            len(messages),
            self.timeout,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": 0.3},
        }

        if self.enable_schema:
            payload["format"] = MODEL_RESPONSE_SCHEMA

        url = f"{self.base_url}/api/chat"
        resp = http_requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        _log.debug("Planner.call_llm: response_keys=%s", sorted(data.keys()))

        content = (data.get("message") or {}).get("content")
        if isinstance(content, dict):
            _log.debug("Planner.call_llm: content_type=dict keys=%s", sorted(content.keys()))
            return content
        if isinstance(content, str):
            _log.debug("Planner.call_llm: content_type=str chars=%s", len(content))
            return json.loads(content)

        raise ValueError(f"Ollama returned unexpected content type: {type(content).__name__}")

    @staticmethod
    def _extract_plan(raw: dict) -> PlanResponse:
        """
        Pull the plan out of the LLM JSON output.

        The LLM is asked to wrap its plan in { plan: { files, summary, warnings } }.
        We are lenient: if the top-level already has those keys we use them directly.
        """
        plan_obj = raw.get("plan") or raw

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

        if not summary and files:
            n = len(files)
            summary = f"{n} file{'s' if n != 1 else ''} to change — review before executing."

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

"""
Evidence-driven planning loop runtime.

The planner is intentionally evidence-driven. It may ask the model to research
the codebase and return a file-level plan, but it must not infer business or
framework directory structures in backend code.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.models.protocol import ToolRequest, ToolResult
from app.models.plan import PlanFile, PlanResponse
from app.services.llm_factory import get_llm_client
from app.services.provider_errors import is_retryable_llm_error, llm_error_code, llm_error_log_payload, llm_error_message
from app.services.capability_broker import CapabilityBroker
from app.services.tool_protocol import LOCAL_CAPABILITIES, REMOTE_CAPABILITIES, ToolProtocolError, normalize_tool_request

_log = logging.getLogger("devwerk.planner")


class EvidencePlanningLoop:
    """
    Stateless planning output adapter used by a config-derived agent job.

    Calls the LLM with a prompt that instructs it to:
      - research the codebase via tool calls when needed
      - output a JSON plan in the final response
      - avoid file writes during planning
    """

    PLAN_INSTRUCTION = (
        "You are DevWerk's PLANNER. Your job is to research the codebase "
        "and produce a FILE-LEVEL change plan - NOT to write any files.\n\n"
        "Rules:\n"
        "  1. Use code_context_summary/source_map first when available. They are client-provided facts, not full file contents.\n"
        "     If syntax_diagnostics are present, their paths/messages are direct client evidence for syntax-fix tasks.\n"
        "  2. Do not invent directories, packages, modules, or framework conventions. If the exact target path is unclear, request tools or return no plan.\n"
        "  3. You may call local capabilities (workspace.list, workspace.read, workspace.search) to understand the codebase.\n"
        "     Tool calls must be JSON only: {\"tool_requests\":[{\"id\":\"p1\",\"tool\":\"workspace.read\",\"args\":{\"path\":\"relative/path.ext\",\"start_line\":1,\"end_line\":200}}]}.\n"
        "     workspace.search uses args.query; args.pattern is tolerated as a query alias and path is optional.\n"
        "     Use project-relative paths from source_map/tree_preview. Never use absolute paths.\n"
        "  4. When you have enough information, respond with a JSON object containing a 'plan' key:\n"
        "     { plan: { files: [{path, nature, intent, required, description, confidence}], summary, warnings } }\n"
        "     files[] must include only writable targets that the coder should create, modify, or delete.\n"
        "     Do not put reference-only files, tool evidence, or optional examples in files[]; put them in warnings/summary instead.\n"
        "  5. nature must be one of: new | modified | deleted. intent must be create | modify | delete.\n"
        "     required=true only when the outcome cannot be correct unless that exact path changes. Candidate paths should use required=false.\n"
        "  6. confidence is 0.0-1.0 how sure you are this file needs to change.\n"
        "  7. Do NOT output any ops, patch_ops, or tool_requests in your final response.\n"
        "  8. summary is one line; warnings[] lists any risky or missing context.\n"
    )
    FINAL_SYNTHESIS_INSTRUCTION = (
        "The planner research budget is complete. Produce the final file-level plan now using the source_map and "
        "all tool_results already in this conversation. Do not request more tools. Include only writable files that "
        "the coder should change; record remaining uncertainty in warnings."
    )

    def __init__(
        self,
        model_route: str = "planner",
        agent_id: str = "planning-agent",
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        max_rounds: int = 128,
    ):
        self.model_route = model_route
        self.agent_id = agent_id
        self.event_sink = event_sink
        self.max_rounds = max(1, max_rounds)

    def plan(
        self,
        messages: list[dict],
        mode: str = "agent",
        project_root: str | None = None,
        client_capabilities: dict[str, Any] | None = None,
    ) -> PlanResponse:
        client_tools = _declared_client_tools(client_capabilities)
        conversation = _inject_plan_instruction(list(messages), mode, client_tools=client_tools)
        _log.debug(
            "Planner.plan: start mode=%s input_messages=%s injected_messages=%s",
            mode,
            len(messages),
            len(conversation),
        )

        max_rounds = self.max_rounds
        backoff = 0.5
        last_plan: PlanResponse | None = None
        used_tools = False

        for attempt in range(max_rounds):
            try:
                _log.debug("Planner.plan: attempt=%s/%s calling_llm", attempt + 1, max_rounds)
                self._emit_event(
                    "plan_llm_round_started",
                    {
                        "round": attempt + 1,
                        "max_rounds": max_rounds,
                        "mode": mode,
                        "agent": self.agent_id,
                        "input": {
                            "message_count": len(conversation),
                            "roles": [str(m.get("role") or "") for m in conversation],
                            "last_user_chars": len(_last_user_text(conversation)),
                        },
                    },
                )
                result = self._call_llm(conversation)
                self._emit_event(
                    "plan_llm_round_result",
                    {"round": attempt + 1, "agent": self.agent_id, "output": _raw_result_summary(result)},
                )
                _log.debug("Planner.plan: attempt=%s raw_result_keys=%s", attempt + 1, sorted(result.keys()))
                plan = self._extract_plan(result, conversation)
                last_plan = plan
                self._emit_event(
                    "plan_llm_round_extracted",
                    {
                        "round": attempt + 1,
                        "agent": self.agent_id,
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
                if plan.ok or plan.error_code == "PLAN_DIRECTORY_PATHS":
                    return plan

                tool_requests = _extract_tool_requests(
                    result,
                    conversation,
                    project_root=project_root,
                    allowed_tools=LOCAL_CAPABILITIES | client_tools,
                )
                if tool_requests:
                    client_requests = [request for request in tool_requests if request.tool in client_tools]
                    if client_requests:
                        self._emit_event(
                            "plan_client_tool_requested",
                            {
                                "round": attempt + 1,
                                "agent": self.agent_id,
                                "requests": [request.model_dump() for request in client_requests],
                            },
                        )
                        return PlanResponse(
                            ok=True,
                            files=[],
                            summary="Planner is waiting for client-provided project evidence.",
                            warnings=[],
                            tool_requests=client_requests,
                            next_action="need_client_tool",
                        )
                    used_tools = True
                    self._emit_event(
                        "plan_tool_requests",
                        {
                            "round": attempt + 1,
                            "agent": self.agent_id,
                            "count": len(tool_requests),
                            "requests": [
                                {"id": req.id, "tool": req.tool, "args": req.args}
                                for req in tool_requests
                            ],
                        },
                    )
                    tool_results = _execute_tool_requests(project_root, tool_requests)
                    self._emit_event(
                        "plan_tool_results",
                        {
                            "round": attempt + 1,
                            "agent": self.agent_id,
                            "results": [
                                {"id": res.id, "ok": res.ok, "content_chars": len(res.content or ""), "error": res.error}
                                for res in tool_results
                            ],
                        },
                    )
                    _log.debug(
                        "Planner.plan: round=%s executing_tool_requests=%s tool_results=%s",
                        attempt + 1,
                        [{"id": req.id, "tool": req.tool, "args": req.args} for req in tool_requests],
                        [{"id": res.id, "ok": res.ok, "content_chars": len(res.content or ""), "error": res.error} for res in tool_results],
                    )
                    conversation = conversation + [
                        {
                            "role": "assistant",
                            "content": "tool_requests:\n"
                            + json.dumps([req.model_dump(exclude_none=True) for req in tool_requests], ensure_ascii=False),
                        },
                        {
                            "role": "user",
                            "content": "tool_results:\n"
                            + json.dumps([res.model_dump(exclude_none=True) for res in tool_results], ensure_ascii=False),
                        },
                    ]
                    continue

                raw_text = str(result.get("raw_text") or result.get("reply") or result.get("content") or "").strip()
                conversation = conversation + [
                    {"role": "assistant", "content": raw_text or "No structured planner output was produced."},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was neither a valid tool request nor a file-level plan. "
                            "Continue this same planning session. Respond only with tool_requests JSON if more "
                            "evidence is required, or with the final plan JSON if the evidence is sufficient."
                        ),
                    },
                ]
                if attempt < max_rounds - 1:
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                retryable = is_retryable_llm_error(exc)
                if attempt < max_rounds - 1 and retryable:
                    time.sleep(backoff * (attempt + 1))
                    continue

                _log.warning("Planner failed: %s", exc)
                self._emit_event(
                    "plan_llm_round_failed",
                    {
                        "round": attempt + 1,
                        "agent": self.agent_id,
                        "error": llm_error_message(exc),
                        "error_code": llm_error_code(exc, "PLAN_ERROR"),
                        "retryable": attempt < max_rounds - 1 and retryable,
                        "provider_error": llm_error_log_payload(exc),
                    },
                )
                return PlanResponse(
                    ok=False,
                    files=[],
                    error_code=llm_error_code(exc, "PLAN_ERROR"),
                    error_message=llm_error_message(exc),
                )

        if last_plan is not None:
            final_round = max_rounds + 1
            final_conversation = conversation + [{"role": "user", "content": self.FINAL_SYNTHESIS_INSTRUCTION}]
            try:
                _log.debug("Planner.plan: final_synthesis_round=%s calling_llm", final_round)
                self._emit_event(
                    "plan_llm_round_started",
                    {
                        "round": final_round,
                        "max_rounds": final_round,
                        "mode": mode,
                        "agent": self.agent_id,
                        "final_synthesis": True,
                        "input": {"message_count": len(final_conversation)},
                    },
                )
                result = self._call_llm(final_conversation)
                self._emit_event(
                    "plan_llm_round_result",
                    {
                        "round": final_round,
                        "agent": self.agent_id,
                        "final_synthesis": True,
                        "output": _raw_result_summary(result),
                    },
                )
                final_plan = self._extract_plan(result, final_conversation)
                self._emit_event(
                    "plan_llm_round_extracted",
                    {
                        "round": final_round,
                        "agent": self.agent_id,
                        "final_synthesis": True,
                        "result": {
                            "ok": final_plan.ok,
                            "file_count": len(final_plan.files),
                            "files": [item.path for item in final_plan.files],
                            "warnings": final_plan.warnings,
                            "summary": final_plan.summary,
                            "error_code": final_plan.error_code,
                        },
                    },
                )
                if final_plan.ok or final_plan.error_code == "PLAN_DIRECTORY_PATHS":
                    return final_plan
                raw_text = str(result.get("raw_text") or result.get("reply") or result.get("content") or "").strip()
                if raw_text:
                    source_paths = sorted(_source_map_paths(final_conversation))
                    repair_messages = [
                        {
                            "role": "system",
                            "content": (
                                "Convert the supplied planner analysis into one JSON object with this exact shape: "
                                '{"plan":{"files":[{"path":"...","nature":"modified|new|deleted",'
                                '"intent":"modify|create|delete","required":true|false,"description":"...",'
                                '"confidence":0.0}],"summary":"...","warnings":[]}}. '
                                "Do not request tools. Do not add a path unless it appears in allowed_paths. "
                                "Reference-only files must not be included in files."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"allowed_paths": source_paths, "planner_analysis": raw_text},
                                ensure_ascii=False,
                            ),
                        },
                    ]
                    _log.debug(
                        "Planner.plan: final_format_repair calling_llm analysis_chars=%s allowed_paths=%s",
                        len(raw_text),
                        len(source_paths),
                    )
                    self._emit_event(
                        "plan_format_repair_started",
                        {"round": final_round + 1, "agent": self.agent_id, "analysis_chars": len(raw_text)},
                    )
                    repaired_result = self._call_llm(repair_messages)
                    repaired_plan = self._extract_plan(repaired_result, final_conversation)
                    self._emit_event(
                        "plan_format_repair_completed",
                        {
                            "round": final_round + 1,
                            "agent": self.agent_id,
                            "ok": repaired_plan.ok,
                            "files": [item.path for item in repaired_plan.files],
                            "error_code": repaired_plan.error_code,
                        },
                    )
                    if repaired_plan.ok or repaired_plan.error_code == "PLAN_DIRECTORY_PATHS":
                        return repaired_plan
                    final_plan = repaired_plan
                last_plan = final_plan
            except Exception as exc:  # noqa: BLE001
                _log.warning("Planner final synthesis failed: %s", exc)
                return PlanResponse(
                    ok=False,
                    files=[],
                    error_code=llm_error_code(exc, "PLAN_ERROR"),
                    error_message=llm_error_message(exc),
                )

            if not used_tools:
                return last_plan
            return PlanResponse(
                ok=False,
                files=[],
                summary=last_plan.summary,
                warnings=last_plan.warnings + ["Planner exhausted tool research rounds before producing a file-level plan."],
                error_code="PLAN_EXHAUSTED",
                error_message="Planner requested tools repeatedly without producing a file-level plan.",
            )

        return PlanResponse(
            ok=False,
            files=[],
            error_code="PLAN_EXHAUSTED",
            error_message="Planner ran out of research rounds.",
        )

    def _call_llm(self, messages: list[dict]) -> dict:
        _log.debug("Planner.call_llm: agent=%s messages=%s", self.model_route, len(messages))
        client = get_llm_client(self.model_route)
        result = _normalize_planner_response(client.chat_json(messages))
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
                            intent=str(item.get("intent") or _intent_from_nature(item.get("nature"))),
                            required=bool(item.get("required", False)),
                        )
                    )
                except Exception:
                    continue

        summary = str(plan_obj.get("summary") or "").strip()
        warnings_raw = plan_obj.get("warnings") or []
        warnings = [str(w) for w in warnings_raw if w]
        directory_paths = _workspace_directory_paths(messages or [])
        directory_plan_paths = sorted({item.path for item in files if item.path in directory_paths})
        if directory_plan_paths:
            _log.debug("Planner.extract_plan: rejected directory plan paths=%s", directory_plan_paths)
            return PlanResponse(
                ok=False,
                files=[],
                summary=summary,
                warnings=warnings + ["Planner returned directory paths where file-level plan paths are required."],
                error_code="PLAN_DIRECTORY_PATHS",
                error_message=(
                    "Planner produced directory-level paths instead of file-level paths: "
                    + ", ".join(directory_plan_paths[:12])
                ),
            )

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


def _inject_plan_instruction(messages: list[dict], mode: str, *, client_tools: set[str] | None = None) -> list[dict]:
    system_content = EvidencePlanningLoop.PLAN_INSTRUCTION
    available = sorted(client_tools or set())
    if available:
        system_content += (
            "\nClient evidence tools declared for this request: "
            + ", ".join(available)
            + ". Request them with the same tool_requests JSON contract when direct provider evidence is required. "
            "For compilation/build-error investigation, prefer project.compile over static guessing when it is available. "
            "Client tools pause this planner session; their tool_results will be returned before planning resumes.\n"
        )
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


def _normalize_planner_response(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        objects = [item for item in value if isinstance(item, dict)]
        if len(objects) == len(value) and objects:
            if all(item.get("tool") or item.get("name") for item in objects):
                return {"tool_requests": objects}
            if all(item.get("path") for item in objects):
                return {
                    "plan": {
                        "files": objects,
                        "summary": "Planner returned a file-level plan array.",
                        "warnings": [],
                    }
                }
        return {
            "raw_text": json.dumps(value, ensure_ascii=False),
            "raw_value_type": "list",
        }
    return {
        "raw_text": str(value or ""),
        "raw_value_type": type(value).__name__,
    }


def _extract_tool_requests(
    raw: dict,
    messages: list[dict],
    project_root: str | None = None,
    *,
    allowed_tools: set[str] | None = None,
) -> list[ToolRequest]:
    source_paths = _source_map_paths(messages)
    accepted_tools = allowed_tools or LOCAL_CAPABILITIES
    raw_requests: list[dict[str, Any]] = []
    if isinstance(raw, dict) and (raw.get("tool") or raw.get("name")):
        raw_requests.append(raw)
    if isinstance(raw, dict) and isinstance(raw.get("tool_requests"), list):
        raw_requests.extend(item for item in raw["tool_requests"] if isinstance(item, dict))

    text = str(raw.get("raw_text") or raw.get("reply") or raw.get("content") or "") if isinstance(raw, dict) else ""
    raw_requests.extend(_json_tool_calls_from_text(text))
    raw_requests.extend(_xml_tool_calls_from_text(text))
    if not raw_requests:
        raw_requests.extend(_search_tool_calls_from_text(text, messages))

    requests: list[ToolRequest] = []
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(raw_requests, start=1):
        tool = str(item.get("tool") or item.get("name") or "").strip()
        if tool not in accepted_tools:
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else item.get("arguments")
        args = dict(args) if isinstance(args, dict) else {}
        if "path" not in args and "file_path" in args:
            args["path"] = args.get("file_path")
        try:
            normalized_item = normalize_tool_request(
                {"id": item.get("id") or f"p{len(requests) + 1}", "tool": tool, "args": args},
                index - 1,
            )
        except ToolProtocolError:
            continue
        tool = normalized_item["tool"]
        args = dict(normalized_item["args"])
        if "paths" in args and isinstance(args["paths"], list):
            args["paths"] = [
                normalized
                for value in args["paths"]
                if (normalized := _normalize_tool_path(value, source_paths=source_paths, project_root=project_root))
            ]
        if tool in {"workspace.list", "workspace.read"}:
            args["path"] = _normalize_tool_path(args.get("path"), source_paths=source_paths, project_root=project_root)
            if tool == "workspace.read" and not args["path"]:
                continue
        if tool == "workspace.read":
            args.setdefault("start_line", 1)
            args.setdefault("end_line", 220)
        elif tool == "workspace.list":
            args.setdefault("max_depth", 3)
        elif tool == "workspace.search":
            args["query"] = str(args.get("query") or "").strip()
            args.setdefault("max_results", 50)
            if not args["query"]:
                continue
        req_id = str(item.get("id") or f"p{len(requests) + 1}")
        key = (tool, str(args.get("path") or ""), str(args.get("query") or ""))
        if key in seen:
            continue
        seen.add(key)
        requests.append(ToolRequest(id=req_id, tool=tool, args=args))
        if len(requests) >= 12:
            break

    if requests:
        _log.debug(
            "Planner.extract_tool_requests: count=%s requests=%s source_paths=%s",
            len(requests),
            [{"id": req.id, "tool": req.tool, "args": req.args} for req in requests],
            len(source_paths),
        )
    return requests


def _declared_client_tools(capabilities: object) -> set[str]:
    return CapabilityBroker().available(capabilities, REMOTE_CAPABILITIES)


def _json_tool_calls_from_text(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    decoder = json.JSONDecoder()
    out: list[dict[str, Any]] = []
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        idx = start + max(end, 1)
        if isinstance(obj, dict) and (obj.get("tool") or obj.get("name")) and (obj.get("args") or obj.get("arguments")):
            out.append(obj)
            if len(out) >= 30:
                break
    return out


def _xml_tool_calls_from_text(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    out: list[dict[str, Any]] = []
    for match in re.finditer(r"<invoke\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</invoke>", text, flags=re.DOTALL):
        args: dict[str, Any] = {}
        body = match.group(2)
        for param in re.finditer(r"<parameter\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</parameter>", body, flags=re.DOTALL):
            args[param.group(1).strip()] = _strip_tool_text(param.group(2))
        out.append({"name": match.group(1).strip(), "arguments": args})
        if len(out) >= 30:
            break
    return out


def _search_tool_calls_from_text(text: str, messages: list[dict]) -> list[dict[str, Any]]:
    if not text or not _looks_like_search_intent(text):
        return []
    terms = _candidate_search_terms(_last_user_text(messages), text)
    return [
        {"id": f"p{i + 1}", "tool": "workspace.search", "args": {"query": term, "max_results": 80}}
        for i, term in enumerate(terms[:6])
    ]


def _looks_like_search_intent(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in ("search", "inspect", "find", "look for", "scan"))


def _candidate_search_terms(user_text: str, model_text: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: object) -> None:
        value = str(term or "").strip().strip("`'\".,;:()[]{}")
        if len(value) < 3 or len(value) > 80:
            return
        if value.lower() in _SEARCH_STOP_WORDS:
            return
        if value not in seen:
            seen.add(value)
            terms.append(value)

    for snippet in re.findall(r"`([^`]{3,80})`", model_text + "\n" + user_text):
        add(snippet)
        if "." in snippet:
            add(snippet.split(".", 1)[0])

    for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_.]{2,}\b", model_text):
        if any(ch.isupper() for ch in token) or "." in token or "_" in token:
            add(token)
            if "." in token:
                add(token.split(".", 1)[0])

    quoted_errors = re.findall(r"\"([^\"]{3,80})\"", user_text)
    for error in quoted_errors:
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_.]{2,}\b", error):
            add(token)

    return terms


_SEARCH_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "these",
    "those",
    "user",
    "errors",
    "error",
    "source",
    "files",
    "string",
    "literal",
    "character",
    "class",
    "unclosed",
    "illegal",
    "escape",
}


def _strip_tool_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).strip()


def _source_map_paths(messages: list[dict]) -> set[str]:
    workspace = _last_workspace_summary(messages)
    source_map = workspace.get("source_map") if isinstance(workspace, dict) else None
    files = source_map.get("files") if isinstance(source_map, dict) else None
    out: set[str] = set()
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            normalized = _safe_rel_path(item.get("path"))
            if normalized:
                out.add(normalized)
    return out


def _diagnostic_paths(messages: list[dict]) -> list[str]:
    workspace = _last_workspace_summary(messages)
    diagnostics = workspace.get("syntax_diagnostics") if isinstance(workspace, dict) else None
    if not isinstance(diagnostics, list):
        return []
    source_paths = _source_map_paths(messages)
    out: list[str] = []
    seen: set[str] = set()
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or "").replace("\\", "/").strip()
        if _has_hidden_dir_segment(raw_path):
            continue
        path = _normalize_tool_path(raw_path, source_paths=source_paths)
        if not path or _has_hidden_dir_segment(path) or path in seen:
            continue
        seen.add(path)
        out.append(path)
        if len(out) >= 20:
            break
    if out:
        _log.debug("Planner.diagnostic_paths: paths=%s source_paths=%s", out, len(source_paths))
    return out


def _normalize_tool_path(value: object, *, source_paths: set[str], project_root: str | None = None) -> str:
    original = str(value or "").strip().replace("\\", "/")
    if not original:
        return ""
    text = original
    root = str(project_root or "").strip().replace("\\", "/").rstrip("/")
    if root and text.lower().startswith(root.lower() + "/"):
        text = text[len(root) + 1 :]
    elif root:
        root_name = root.rsplit("/", 1)[-1]
        if root_name and text.lower().startswith(root_name.lower() + "/"):
            text = text[len(root_name) + 1 :]

    source_match = _source_path_suffix_match(text, source_paths)
    if source_match:
        return source_match

    if re.match(r"^[A-Za-z]:/", text) or text.startswith("/"):
        _log.debug("Planner.normalize_tool_path: rejected foreign absolute path=%s", original)
        return ""

    return _safe_rel_path(text)


def _source_path_suffix_match(path: str, source_paths: set[str]) -> str:
    text = str(path or "").replace("\\", "/").strip().lstrip("/")
    for source_path in sorted(source_paths, key=len, reverse=True):
        if text == source_path or text.endswith("/" + source_path):
            return source_path
    return ""


def _execute_tool_requests(project_root: str | None, reqs: list[ToolRequest]) -> list[ToolResult]:
    if not project_root:
        return [ToolResult(id=req.id, ok=False, error="project_root is null") for req in reqs]
    root = Path(project_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return [ToolResult(id=req.id, ok=False, error=f"project_root is not a directory: {project_root}") for req in reqs]

    results: list[ToolResult] = []
    for req in reqs:
        try:
            if req.tool == "workspace.list":
                rel = _safe_rel_path(req.args.get("path"))
                if rel and _contains_hidden_segment(rel):
                    results.append(ToolResult(id=req.id, ok=False, error=f"blocked hidden directory path: {rel}"))
                    continue
                max_depth = _int_arg(req.args.get("max_depth"), 3, 1, 8)
                results.append(ToolResult(id=req.id, ok=True, content=_tool_list_dir(root, rel, max_depth)))
            elif req.tool == "workspace.read":
                rel = _safe_rel_path(req.args.get("path"))
                if not rel:
                    results.append(ToolResult(id=req.id, ok=False, error="path is required"))
                    continue
                if _has_hidden_dir_segment(rel):
                    results.append(ToolResult(id=req.id, ok=False, error=f"blocked hidden directory path: {rel}"))
                    continue
                start_line = _int_arg(req.args.get("start_line"), 1, 1, 1_000_000)
                end_line = _int_arg(req.args.get("end_line"), start_line + 220, start_line, 1_000_000)
                results.append(ToolResult(id=req.id, ok=True, content=_tool_read_file(root, rel, start_line, end_line)))
            elif req.tool == "workspace.search":
                query = str(req.args.get("query") or "")
                raw_paths = req.args.get("paths")
                paths = raw_paths if isinstance(raw_paths, list) else []
                safe_paths = [_safe_rel_path(item) for item in paths]
                safe_paths = [path for path in safe_paths if not _contains_hidden_segment(path)]
                max_results = _int_arg(req.args.get("max_results"), 50, 1, 500)
                results.append(ToolResult(id=req.id, ok=True, content=_tool_search(root, query, safe_paths, max_results)))
            else:
                results.append(ToolResult(id=req.id, ok=False, error=f"unknown tool: {req.tool}"))
        except Exception as exc:  # noqa: BLE001
            results.append(ToolResult(id=req.id, ok=False, error=f"{type(exc).__name__}: {exc}"))
    return results


def _tool_list_dir(root: Path, rel: str, max_depth: int) -> str:
    target = _safe_project_path(root, rel)
    if not target.exists():
        return f"[workspace.list] not found: {rel}"
    if not target.is_dir():
        return f"[workspace.list] not a directory: {rel}"
    label = "." if not rel or rel == "." else (target.name or ".")
    lines = [f"{label}/"]

    def walk(path: Path, depth: int, indent: str) -> None:
        if depth >= max_depth:
            return
        children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for child in children:
            if child.is_dir() and child.name.startswith("."):
                continue
            if child.is_dir():
                lines.append(f"{indent}  {child.name}/")
                walk(child, depth + 1, indent + "  ")
            else:
                lines.append(f"{indent}  {child.name}")

    walk(target, 0, "")
    return "\n".join(lines).rstrip()


def _tool_read_file(root: Path, rel: str, start_line: int, end_line: int) -> str:
    target = _safe_project_path(root, rel)
    if not target.exists():
        return f"[workspace.read] not found: {rel}"
    if target.is_dir():
        return f"[workspace.read] is a directory: {rel}"
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(start_line, 1) - 1
    end = max(end_line, start_line)
    sliced = lines[start:end]
    return f"FILE: {rel} (lines {start_line}-{end_line})\n" + "\n".join(sliced)


def _tool_search(root: Path, query: str, paths: list[str], max_results: int) -> str:
    needle = query.strip()
    if not needle:
        return "[search] empty query"
    roots = paths or [""]
    filename_mode = _looks_like_filename_query(needle)
    results: list[str] = []
    for rel in roots:
        base = _safe_project_path(root, rel)
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else base.rglob("*")
        for path in candidates:
            if len(results) >= max_results:
                break
            if not path.is_file():
                continue
            rel_path = path.relative_to(root).as_posix()
            if _has_hidden_dir_segment(rel_path):
                continue
            if any(part.lower() in {"build", "out", "node_modules"} for part in path.relative_to(root).parts[:-1]):
                continue
            if filename_mode:
                matched = path.name.lower() == needle.lower()
            else:
                if path.stat().st_size > 1_000_000:
                    continue
                matched = needle.lower() in path.read_text(encoding="utf-8", errors="ignore").lower()
            if matched:
                results.append(rel_path)
        if len(results) >= max_results:
            break
    return "\n".join(results) if results else "[search] no hits"


def _safe_project_path(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path escapes project_root: {rel}")
    return target


def _contains_hidden_segment(rel: str) -> bool:
    return any(part.startswith(".") for part in rel.replace("\\", "/").split("/") if part)


def _has_hidden_dir_segment(rel: str) -> bool:
    parts = [part for part in rel.replace("\\", "/").split("/") if part]
    return len(parts) > 1 and any(part.startswith(".") for part in parts[:-1])


def _looks_like_filename_query(query: str) -> bool:
    if any(ch in query for ch in ("/", "\\", "\n", "\t")) or "." not in query:
        return False
    return bool(re.fullmatch(r"[^./\\\s][^/\\\s]*\.[^./\\\s][^/\\\s]*", query.strip()))


def _int_arg(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _intent_from_nature(value: Any) -> str:
    return {"new": "create", "deleted": "delete"}.get(str(value or "modified").strip().lower(), "modify")


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

    diagnostic_paths = _diagnostic_paths(messages)
    if diagnostic_paths:
        return PlanResponse(
            ok=True,
            files=[
                PlanFile(
                    path=path,
                    nature="modified",
                    description="Client syntax diagnostics identify this file as a required investigation/fix target.",
                    confidence=0.75,
                )
                for path in diagnostic_paths
            ],
            summary="Plan based on client syntax diagnostic file evidence.",
            warnings=[
                "Planner returned no structured file plan; DevWerk used client-provided diagnostic paths only and did not infer framework paths."
            ],
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
    internal_prefixes = (
        "workspace_summary:",
        "code_context_summary:",
        "code_context_skill:",
        "tool_results:",
        "client_tool_results:",
        "request_meta:",
        "workflow_replan_feedback:",
        "workflow_phase_context:",
    )
    for item in reversed(messages):
        if isinstance(item, dict) and str(item.get("role") or "").lower() == "user":
            content = str(item.get("content") or "")
            if not content.startswith(internal_prefixes):
                return content
    return ""


def _workspace_directory_paths(messages: list[dict]) -> set[str]:
    workspace = _last_workspace_summary(messages)
    tree_preview = str(workspace.get("tree_preview") or "") if isinstance(workspace, dict) else ""
    return _tree_directory_paths(tree_preview)


def _last_workspace_summary(messages: list[dict]) -> dict[str, Any]:
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        if not content.startswith("workspace_summary:"):
            continue
        raw = content.split("workspace_summary:", 1)[1].strip()
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _tree_directory_paths(tree_preview: str) -> set[str]:
    lines = [line.rstrip() for line in str(tree_preview or "").splitlines() if line.strip()]
    stack: list[tuple[int, str]] = []
    dirs: set[str] = set()
    for line in lines:
        stripped = line.strip()
        is_dir = stripped.endswith("/")
        name = stripped.rstrip("/")
        if not is_dir or not name or name == ".":
            continue
        indent = len(line) - len(line.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, name))
        rel = "/".join(part for _, part in stack if part and part != ".")
        normalized = _safe_rel_path(rel)
        if normalized:
            dirs.add(normalized)
    return dirs


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


def _has_hidden_dir_segment(path: str) -> bool:
    parts = [part for part in str(path or "").replace("\\", "/").split("/") if part]
    return len(parts) > 1 and any(part.startswith(".") for part in parts[:-1])

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from app.v1.domain import ToolResult


ACTION_EFFECT_KINDS = {"write", "process", "control"}


def evidence_id(agent_run_id: str, tool_call_id: str) -> str:
    return f"{agent_run_id}:{tool_call_id}"


def operation_sha256(capability: str, arguments: dict[str, Any]) -> str:
    operation_json = json.dumps(
        {"capability": capability, "arguments": normalized_operation_arguments(capability, arguments)},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(operation_json.encode("utf-8")).hexdigest()


def normalized_operation_arguments(capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    if capability in {"project.command.run", "system.command.run"}:
        normalized.pop("success_exit_codes", None)
        normalized["cwd"] = str(normalized.get("cwd") or ".")
    return normalized


def ledger_entry(
    agent_run_id: str,
    tool_call_id: str,
    capability: str,
    effect_kind: str,
    result: ToolResult,
    *,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = result.model_dump(mode="json")
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    entity_ids = sorted(_entity_ids(payload))
    entity_digest = hashlib.sha256(
        json.dumps(entity_ids, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    entry: dict[str, Any] = {
        "agent_run_id": agent_run_id,
        "tool_call_id": tool_call_id,
        "evidence_id": evidence_id(agent_run_id, tool_call_id),
        "capability": capability,
        "effect_kind": effect_kind,
        "ok": result.ok,
        "status": result.status,
        "operation_sha256": operation_sha256(capability, arguments or {}),
        "entity_ids": entity_ids,
        "result_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
    }
    entry["entity_id_count"] = len(entity_ids)
    entry["entity_ids_sha256"] = entity_digest
    entry["facts"] = {"arguments": arguments or {}, "result": payload}
    return entry


def action_entries(
    ledger: Iterable[dict[str, Any]],
    *,
    excluded_capabilities: Iterable[str] = (),
) -> list[dict[str, Any]]:
    excluded = set(excluded_capabilities)
    return [
        item
        for item in ledger
        if item.get("effect_kind") in ACTION_EFFECT_KINDS
        and str(item.get("capability") or "") not in excluded
    ]


def evidence_entries(
    ledger: Iterable[dict[str, Any]],
    *,
    excluded_capabilities: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return executable capability receipts, including read-only/no-op evidence."""
    excluded = set(excluded_capabilities)
    return [
        item
        for item in ledger
        if str(item.get("capability") or "") not in excluded
    ]


def successful_action_entries(
    ledger: Iterable[dict[str, Any]],
    *,
    excluded_capabilities: Iterable[str] = (),
) -> list[dict[str, Any]]:
    return [
        item
        for item in action_entries(ledger, excluded_capabilities=excluded_capabilities)
        if item.get("ok") and item.get("status") == "completed"
    ]


def unresolved_execution_failures(
    ledger: Iterable[dict[str, Any]],
    *,
    excluded_capabilities: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """Return executed failures not repaired by a later identical operation.

    The operation hash is used only for exact retry correlation. Alternative
    repair paths require an explicit completion resolution; a hash is never
    treated as proof of semantic equivalence.
    """
    unresolved: dict[str, dict[str, Any]] = {}
    for item in action_entries(ledger, excluded_capabilities=excluded_capabilities):
        if _rejected_before_effect(item):
            continue
        if item.get('status') == 'awaiting':
            continue
        operation = str(item.get("operation_sha256") or "")
        if not operation:
            continue
        if item.get("ok") and item.get("status") == "completed":
            unresolved.pop(operation, None)
        else:
            unresolved[operation] = item
    return unresolved


def repeats_failed_operation(
    capability: str,
    arguments: dict[str, Any],
    ledger: Iterable[dict[str, Any]],
) -> bool:
    operation_hash = operation_sha256(capability, arguments)
    entries = list(ledger)
    latest_failure = next(
        (
            index
            for index in range(len(entries) - 1, -1, -1)
            if str(entries[index].get("capability") or "") == capability
            and str(entries[index].get("operation_sha256") or "") == operation_hash
            and (
                not entries[index].get("ok")
                or entries[index].get("status") != "completed"
            )
        ),
        None,
    )
    if latest_failure is None:
        return False
    return not any(
        item.get("effect_kind") in ACTION_EFFECT_KINDS
        and item.get("ok")
        and item.get("status") == "completed"
        for item in entries[latest_failure + 1 :]
    )


def execution_progress(
    ledger: Iterable[dict[str, Any]],
    *,
    excluded_capabilities: Iterable[str] = (),
) -> tuple[str, ...]:
    progress: set[str] = set()
    for item in action_entries(ledger, excluded_capabilities=excluded_capabilities):
        if _rejected_before_effect(item):
            continue
        if item.get("ok") and item.get("status") == "completed":
            progress.add("ok:" + str(item.get("operation_sha256") or "") + ":" + str(item.get("result_sha256") or ""))
            continue
        progress.add(
            "failed:"
            + str(item.get("capability") or "")
            + ":"
            + str(item.get("operation_sha256") or "")
            + ":"
            + str(item.get("result_sha256") or "")
        )
    return tuple(sorted(progress))


def _rejected_before_effect(item: dict[str, Any]) -> bool:
    return (
        (((item.get("facts") or {}).get("result") or {}).get("checkpoint") or {}).get(
            "failure_disposition"
        )
        == "rejected_before_effect"
    )


def _entity_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key == "id" or key.endswith("_id")) and item:
                found.add(item)
            found.update(_entity_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_entity_ids(item))
    return found

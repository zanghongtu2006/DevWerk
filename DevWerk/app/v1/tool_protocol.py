from __future__ import annotations

from typing import Any

from app.v1.contracts import canonicalize_contract_value, validate_contract


def column_tool_batch_error(
    tool_calls: list[Any],
    completion_tool_name: str,
) -> str | None:
    complete_indices = [
        index for index, call in enumerate(tool_calls) if call.name == completion_tool_name
    ]
    await_indices = [
        index for index, call in enumerate(tool_calls) if call.name == "column.await"
    ]
    if len(complete_indices) > 1:
        return (
            f"A model response may contain only one {completion_tool_name} call. "
            f"Combine outcome, output, summary, evidence, and resolutions into one final "
            f"{completion_tool_name} call. No tool call from this response was executed."
        )
    if complete_indices and await_indices:
        return (
            f"{completion_tool_name} and column.await are mutually exclusive in one model response. "
            f"Return either one final {completion_tool_name} call or one final column.await call. "
            "No tool call from this response was executed."
        )
    if complete_indices and complete_indices[0] != len(tool_calls) - 1:
        return (
            f"{completion_tool_name} must be the final tool call in its model response. "
            f"Move all required tool calls before one final {completion_tool_name} call. "
            "No tool call from this response was executed."
        )
    if len(await_indices) > 1:
        return (
            "A model response may contain only one column.await call, and it must be final. "
            "No tool call from this response was executed."
        )
    if await_indices and await_indices[0] != len(tool_calls) - 1:
        return (
            "column.await must be the final tool call in its model response. "
            "No tool call from this response was executed."
        )
    return None


def await_tool_schema(allowed: list[str], wait_config: dict[str, Any]) -> dict[str, Any]:
    kind = str(wait_config.get("kind") or "poll")
    required = ["provider"]
    properties: dict[str, Any] = {
        "provider": {"type": "string", "minLength": 1, "maxLength": 200},
        "token": {"type": ["string", "null"], "maxLength": 4000},
        "checkpoint": {"type": "object"},
    }
    if kind == "poll":
        required.extend(["poll_capability", "poll_arguments"])
        properties.update({
            "poll_capability": {"type": "string", "enum": sorted(allowed)},
            "poll_arguments": {"type": "object"},
            "next_check_seconds": {"type": "integer", "minimum": 1},
        })
    return {
        "type": "function",
        "function": {
            "name": "column.await",
            "description": (
                f"Suspend this Column Run using its declared {kind} wait policy instead of "
                "keeping an Agent alive."
            ),
            "parameters": {
                "type": "object",
                "required": required,
                "properties": properties,
                "additionalProperties": False,
            },
        },
    }


def parse_await_submission(
    arguments: dict[str, Any],
    allowed: list[str],
    wait_config: dict[str, Any],
) -> dict[str, Any]:
    schema = await_tool_schema(allowed, wait_config)["function"]["parameters"]
    normalized = canonicalize_contract_value(arguments, schema)
    validate_contract(normalized, schema, label="column.await")
    return dict(normalized)

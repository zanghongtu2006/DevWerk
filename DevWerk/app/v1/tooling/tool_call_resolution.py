"""Resolve one Provider response against the exact Tool presentation for its request."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import ValidationError

from app.v1.contracts import canonicalize_contract_value, provider_contract_schema
from app.v1.protocols.agent.resolved_tool_operation import ResolvedToolOperation
from app.v1.protocols.tools.core.tool_catalog_contracts import ToolCatalogCallInput, ToolExposure
from app.v1.tooling.builtin_tool_catalog import BuiltinTool
from app.v1.tooling.capability_registry import CapabilityContext, CapabilityEntry, CapabilityRegistry


CATALOG_CALL_ID = BuiltinTool.TOOL_CATALOG_CALL.value.id


def schema_digest(schema: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def target_provider_schema(entry: CapabilityEntry) -> dict[str, Any]:
    """The shape an Anthropic-compatible Provider actually receives."""
    return provider_contract_schema(entry.provider_input_schema or entry.input_schema)


def resolve_tool_call(
    name: str, arguments: dict[str, Any], *,
    presented_schemas: dict[str, dict[str, Any]],
    allowed: list[str], registry: CapabilityRegistry,
    context: CapabilityContext,
) -> ResolvedToolOperation:
    if name not in presented_schemas:
        return ResolvedToolOperation(provider_name=name, provider_arguments=arguments,
            capability=name, arguments=arguments, rejection_type="ToolNotPresented",
            rejection_message=f"{name} was not presented on this model request. Use tool.catalog.search, tool.catalog.describe and tool.catalog.call for an on-demand Tool.")
    if name != CATALOG_CALL_ID:
        schema = (registry.input_schema(name) if registry.contains(name)
                  else presented_schemas[name])
        return ResolvedToolOperation(provider_name=name, provider_arguments=arguments,
            capability=name, arguments=canonicalize_contract_value(arguments, schema),
            schema_digest=schema_digest(presented_schemas[name]))
    try:
        request = ToolCatalogCallInput.model_validate(arguments)
    except ValidationError as exc:
        return ResolvedToolOperation(provider_name=name, provider_arguments=arguments,
            capability=name, arguments=arguments, rejection_type="ToolCallContractError",
            rejection_message=str(exc))
    target = request.tool_id
    if target not in allowed or not registry.contains(target):
        return ResolvedToolOperation(provider_name=name, provider_arguments=arguments,
            capability=name, arguments=arguments, rejection_type="CapabilityUnavailable",
            rejection_message=f"Tool is unavailable in this Agent Run: {target}")
    entries = registry.resolve([target], context)
    if not entries or entries[0].exposure != ToolExposure.ON_DEMAND:
        return ResolvedToolOperation(provider_name=name, provider_arguments=arguments,
            capability=name, arguments=arguments, rejection_type="CapabilityUnavailable",
            rejection_message=f"Tool is not available through the on-demand bridge: {target}")
    entry = entries[0]
    provider_schema = target_provider_schema(entry)
    digest = schema_digest(provider_schema)
    if request.schema_digest is not None and request.schema_digest != digest:
        return ResolvedToolOperation(provider_name=name, provider_arguments=arguments,
            capability=name, arguments=arguments, rejection_type="ToolSchemaChanged",
            rejection_message=f"Schema for {target} changed; call tool.catalog.describe again before retrying.")
    normalized = canonicalize_contract_value(request.arguments, entry.input_schema)
    return ResolvedToolOperation(provider_name=name, provider_arguments=arguments,
        capability=target, arguments=normalized, schema_digest=digest)

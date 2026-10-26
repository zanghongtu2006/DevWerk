"""Discover authorized Tools and present selected schemas to a Conversation Run."""

from __future__ import annotations

from typing import Any
import json

from app.v1.protocols.tools.core.capability_protocol import CapabilityToolset
from app.v1.protocols.tools.core.tool_catalog_contracts import (
    TOOL_SUMMARY_DESCRIPTION_LIMIT,
    ToolCatalogSearchInput,
    ToolCatalogSearchReceipt,
    ToolCatalogDescribeInput,
    ToolCatalogDescribeReceipt,
    MAX_TOOL_DESCRIPTION_BYTES,
    ToolCatalogSelectInput,
    ToolCatalogSelectReceipt,
    ToolCatalogSourceCounts,
    ToolCatalogSummary,
    ToolExposure,
    ToolSource,
)
from app.v1.tooling.capability_registry import CapabilityContext, CapabilityEntry, CapabilityRegistry
from app.v1.tooling.builtin_tool_catalog import BuiltinTool
from app.v1.tooling.tool_call_resolution import schema_digest, target_provider_schema


class ToolSchemaTooLarge(ValueError):
    error_code = "TOOL_SCHEMA_TOO_LARGE"


class ToolPresentation:
    """Provider visibility only; the Agent Run and Registry still authorize effects."""

    SELECT_TOOL_ID = BuiltinTool.TOOL_CATALOG_SELECT.value.id

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def _available_entries(self, context: CapabilityContext) -> list[CapabilityEntry]:
        if not context.agent_run_id:
            raise ValueError("Tool discovery requires an Agent Run")
        run = context.store.get_agent_run(context.project_id, context.agent_run_id)
        if run["kind"] != "conversation":
            raise ValueError("Tool discovery belongs to the Conversation Agent")
        allowed = [identity for identity in run["capabilities"] if self.registry.contains(identity)]
        return [entry for entry in self.registry.resolve(allowed, context)
                if entry.exposure != ToolExposure.INTERNAL]

    @staticmethod
    def _source(entry: CapabilityEntry) -> ToolSource:
        return ToolSource.MCP if entry.toolset == CapabilityToolset.MCP else ToolSource.BUILTIN

    def selected_ids(self, context: CapabilityContext) -> list[str]:
        if not context.agent_run_id:
            return []
        invocation = context.store.latest_successful_tool_invocation(
            context.project_id, context.agent_run_id, self.SELECT_TOOL_ID,
        )
        if not invocation:
            return []
        result = invocation["result"]
        receipt = ToolCatalogSelectReceipt.model_validate(result["output"])
        return receipt.selected_ids

    def search(self, args: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
        request = ToolCatalogSearchInput.model_validate(args)
        entries = self._available_entries(context)
        source_counts = ToolCatalogSourceCounts(
            builtin=sum(self._source(entry) == ToolSource.BUILTIN for entry in entries),
            mcp=sum(self._source(entry) == ToolSource.MCP for entry in entries),
        )
        query = (request.query or "").strip().casefold()
        terms = query.split()
        matches = [
            entry for entry in entries
            if (request.source is None or self._source(entry) == request.source)
            and all(term in f"{entry.id}\n{entry.description}".casefold() for term in terms)
        ]
        matches.sort(key=lambda entry: (entry.id.casefold() != query,
                                        not entry.id.casefold().startswith(query),
                                        not all(term in entry.id.casefold() for term in terms), entry.id))
        page = matches[request.offset:request.offset + request.limit]
        next_offset = request.offset + len(page)
        receipt = ToolCatalogSearchReceipt(
            items=[ToolCatalogSummary(
                id=entry.id,
                description=entry.description[:TOOL_SUMMARY_DESCRIPTION_LIMIT],
                source=self._source(entry),
                presented=entry.exposure == ToolExposure.DIRECT,
            ) for entry in page],
            total=len(matches),
            next_offset=next_offset if next_offset < len(matches) else None,
            sources=source_counts,
        )
        return receipt.model_dump(mode="json")

    def describe(self, args: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
        request = ToolCatalogDescribeInput.model_validate(args)
        entry = next((item for item in self._available_entries(context)
                      if item.id == request.tool_id), None)
        if entry is None:
            raise ValueError(f"Tool is unavailable in this Agent Run: {request.tool_id}")
        parameters = target_provider_schema(entry)
        if len(json.dumps(parameters, ensure_ascii=False).encode("utf-8")) > MAX_TOOL_DESCRIPTION_BYTES:
            raise ToolSchemaTooLarge(f"{entry.id} schema exceeds the catalog description limit")
        digest = schema_digest(parameters)
        return ToolCatalogDescribeReceipt(
            tool_id=entry.id, description=entry.description,
            parameters=parameters, schema_digest=digest,
        ).model_dump(mode="json")

    def select(self, args: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
        request = ToolCatalogSelectInput.model_validate(args)
        available = {entry.id: entry for entry in self._available_entries(context)}
        optional: list[str] = []
        for index, identity in enumerate(request.tool_ids):
            entry = available.get(identity)
            if entry is None:
                raise ValueError(f"tool_ids[{index}]: Tool is unavailable in this Agent Run: {identity}")
            if entry.exposure != ToolExposure.DIRECT:
                optional.append(identity)
        return ToolCatalogSelectReceipt(selected_ids=optional).model_dump(mode="json")

    def selected_schemas(
        self, context: CapabilityContext, direct_tool_names: set[str],
    ) -> list[dict[str, Any]]:
        selected = set(self.selected_ids(context))
        if not selected:
            return []
        return [entry.tool_schema() for entry in self._available_entries(context)
                if entry.id in selected and entry.id not in direct_tool_names]

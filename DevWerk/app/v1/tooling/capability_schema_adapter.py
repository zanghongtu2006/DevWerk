"""Read-only interpretation of legacy Capability output schemas."""

from typing import Any
from app.v1.protocols.tools.core.provider_schema_contracts import ProviderSchemaNode


def declared_outcomes(output_schema: dict[str, Any]) -> tuple[str, ...]:
    outcome = ProviderSchemaNode.model_validate(output_schema).child("outcome")
    return tuple(str(value) for value in outcome.enum) if isinstance(outcome, ProviderSchemaNode) and outcome.enum else ()

"""Public descriptor for a Column-delegable internal capability."""

from pydantic import BaseModel, ConfigDict, JsonValue


class CapabilityCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    description: str
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    side_effect_kind: str
    parallel_safe: bool

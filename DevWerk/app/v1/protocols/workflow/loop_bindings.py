"""Validated dynamic JSON values for externally authored Loop parameters."""

from typing import Any

from pydantic import BaseModel, Field, JsonValue, RootModel, TypeAdapter


_json_object = TypeAdapter(dict[str, JsonValue])


class LoopBindings(BaseModel):
    values: dict[str, JsonValue] = Field(default_factory=dict, description="Effective Loop parameter values")

    @classmethod
    def combine(cls, *layers: dict[str, Any]) -> "LoopBindings":
        result = cls()
        for layer in layers:
            result.values.update(_json_object.validate_python(layer))
        return result

    def json_text(self) -> str:
        return _json_object.dump_json(self.values).decode("utf-8")


class LoopPackageSnapshot(RootModel[dict[str, JsonValue]]):
    def json_text(self) -> str:
        return self.model_dump_json()

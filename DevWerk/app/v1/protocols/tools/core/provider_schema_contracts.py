"""Typed JSON Schema view used at the external Provider transport boundary.

The strict tool contract remains the source of truth. This model owns the
Provider compatibility projection; JSON object construction happens only in
Pydantic serialization, never in the projection algorithm.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SchemaDiscriminator(BaseModel):
    model_config = ConfigDict(extra="allow")
    property_name: str | None = Field(default=None, alias="propertyName")


class ProviderSchemaNode(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_type: str | list[str] | None = Field(default=None, alias="type")
    reference: str | None = Field(default=None, alias="$ref")
    definitions: dict[str, ProviderSchemaNode | bool] | None = Field(default=None, alias="$defs")
    legacy_definitions: dict[str, ProviderSchemaNode | bool] | None = Field(default=None, alias="definitions")
    properties: dict[str, ProviderSchemaNode | bool] | None = None
    pattern_properties: dict[str, ProviderSchemaNode | bool] | None = Field(default=None, alias="patternProperties")
    dependent_schemas: dict[str, ProviderSchemaNode | bool] | None = Field(default=None, alias="dependentSchemas")
    property_names: ProviderSchemaNode | bool | None = Field(default=None, alias="propertyNames")
    items: ProviderSchemaNode | bool | None = None
    prefix_items: list[ProviderSchemaNode | bool] | None = Field(default=None, alias="prefixItems")
    contains: ProviderSchemaNode | bool | None = None
    one_of: list[ProviderSchemaNode | bool] | None = Field(default=None, alias="oneOf")
    any_of: list[ProviderSchemaNode | bool] | None = Field(default=None, alias="anyOf")
    all_of: list[ProviderSchemaNode | bool] | None = Field(default=None, alias="allOf")
    not_schema: ProviderSchemaNode | bool | None = Field(default=None, alias="not")
    if_schema: ProviderSchemaNode | bool | None = Field(default=None, alias="if")
    then_schema: ProviderSchemaNode | bool | None = Field(default=None, alias="then")
    else_schema: ProviderSchemaNode | bool | None = Field(default=None, alias="else")
    required: list[str] | None = None
    enum: list[Any] | None = None
    const: Any = None
    additional_properties: bool | ProviderSchemaNode | None = Field(default=None, alias="additionalProperties")
    unevaluated_properties: bool | ProviderSchemaNode | None = Field(default=None, alias="unevaluatedProperties")
    unevaluated_items: bool | ProviderSchemaNode | None = Field(default=None, alias="unevaluatedItems")
    discriminator: SchemaDiscriminator | None = None
    description: str | None = None
    title: str | None = None
    default: Any = None

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def clone(self) -> ProviderSchemaNode:
        return self.model_copy(deep=True)

    def child(self, token: str) -> ProviderSchemaNode | bool | None:
        if self.schema_type == "object" and self.properties is not None:
            return self.properties.get(token)
        if self.schema_type == "array" and token.isdigit():
            return self.items
        return None

    def overlay(self, other: ProviderSchemaNode) -> ProviderSchemaNode:
        result = self.clone()
        for field_name in other.model_fields_set:
            if field_name != "reference":
                setattr(result, field_name, getattr(other, field_name))
        for name, value in (other.model_extra or {}).items():
            setattr(result, name, value)
        return result


ProviderSchemaNode.model_rebuild()


def provider_view(schema: dict[str, Any]) -> dict[str, Any]:
    root = ProviderSchemaNode.model_validate(schema)
    return _project(root, root, ()).wire()


def _project(node: ProviderSchemaNode | bool, root: ProviderSchemaNode, seen: tuple[str, ...]) -> ProviderSchemaNode | bool:
    if isinstance(node, bool):
        return node
    reference = node.reference
    if reference and reference.startswith("#/"):
        if reference in seen:
            return ProviderSchemaNode(schema_type="object")
        resolved = _reference(root, reference)
        if isinstance(resolved, bool):
            if not resolved:
                return False
            sibling = node.clone()
            sibling.reference = None
            return _project(sibling, root, (*seen, reference))
        return _project(resolved.overlay(node), root, (*seen, reference))

    choices = node.one_of if node.one_of is not None else node.any_of
    if choices is not None:
        projected = [_project(choice, root, seen) for choice in choices]
        non_null = [choice for choice in projected if isinstance(choice, ProviderSchemaNode) and choice.schema_type != "null"]
        null_count = sum(
            isinstance(choice, ProviderSchemaNode) and choice.schema_type == "null"
            for choice in projected
        )
        if len(non_null) == 1 and null_count > 0 and len(non_null) + null_count == len(projected):
            result = non_null[0]
            if node.description and not result.description:
                result.description = node.description
            return result
        discriminator = node.discriminator.property_name if node.discriminator else None
        if discriminator and non_null and all(choice.schema_type == "object" for choice in non_null):
            return _flatten_discriminated(non_null, discriminator)

    result = node.clone()
    result.reference = None
    result.definitions = None
    result.legacy_definitions = None
    result.title = None
    result.default = None
    result.discriminator = None
    if result.properties is not None:
        result.properties = {name: _project(child, root, seen) for name, child in result.properties.items()}
    if result.pattern_properties is not None:
        result.pattern_properties = {name: _project(child, root, seen) for name, child in result.pattern_properties.items()}
    if result.dependent_schemas is not None:
        result.dependent_schemas = {name: _project(child, root, seen) for name, child in result.dependent_schemas.items()}
    if result.property_names is not None:
        result.property_names = _project(result.property_names, root, seen)
    if result.items is not None:
        result.items = _project(result.items, root, seen)
    if result.prefix_items is not None:
        result.prefix_items = [_project(child, root, seen) for child in result.prefix_items]
    if result.contains is not None:
        result.contains = _project(result.contains, root, seen)
    if result.one_of is not None:
        result.one_of = [_project(child, root, seen) for child in result.one_of]
    if result.any_of is not None:
        result.any_of = [_project(child, root, seen) for child in result.any_of]
    if result.all_of is not None:
        result.all_of = [_project(child, root, seen) for child in result.all_of]
    for field_name in ("not_schema", "if_schema", "then_schema", "else_schema"):
        child = getattr(result, field_name)
        if child is not None:
            setattr(result, field_name, _project(child, root, seen))
    if isinstance(result.additional_properties, ProviderSchemaNode):
        result.additional_properties = _project(result.additional_properties, root, seen)
    if isinstance(result.unevaluated_properties, ProviderSchemaNode):
        result.unevaluated_properties = _project(result.unevaluated_properties, root, seen)
    if isinstance(result.unevaluated_items, ProviderSchemaNode):
        result.unevaluated_items = _project(result.unevaluated_items, root, seen)
    if result.const is not None:
        result.enum = [result.const]
        result.schema_type = result.schema_type or _json_type(result.const)
        result.const = None
    return result


def _reference(root: ProviderSchemaNode, reference: str) -> ProviderSchemaNode | bool:
    current: Any = root
    for encoded in reference[2:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, ProviderSchemaNode):
            field_name = next(
                (name for name, field in type(current).model_fields.items()
                 if name == token or field.alias == token),
                None,
            )
            current = getattr(current, field_name, None) if field_name else None
        elif isinstance(current, dict):
            current = current.get(token)
        else:
            return ProviderSchemaNode()
    return current if isinstance(current, (ProviderSchemaNode, bool)) else ProviderSchemaNode()


def _flatten_discriminated(choices: list[ProviderSchemaNode], discriminator: str) -> ProviderSchemaNode:
    properties: dict[str, ProviderSchemaNode | bool] = {}
    differing_properties: dict[str, list[ProviderSchemaNode | bool]] = {}
    shared_required: set[str] | None = None
    variant_requirements: list[str] = []
    discriminator_values: list[Any] = []
    for choice in choices:
        fields = choice.properties or {}
        required = set(choice.required or [])
        if discriminator in fields:
            required.add(discriminator)
        shared_required = required if shared_required is None else shared_required & required
        discriminator_field = fields.get(discriminator)
        values = discriminator_field.enum or [] if isinstance(discriminator_field, ProviderSchemaNode) else []
        discriminator_values.extend(value for value in values if value not in discriminator_values)
        label = repr(values[0]) if len(values) == 1 else repr(values)
        variant_fields = sorted(required - {discriminator})
        variant_requirements.append(
            f"When {discriminator}={label}, required fields are "
            f"{', '.join(variant_fields) if variant_fields else '(none beyond the discriminator)'}."
        )
        for name, field in fields.items():
            existing = properties.get(name)
            if existing is None:
                properties[name] = field.clone() if isinstance(field, ProviderSchemaNode) else field
            elif existing != field:
                if (isinstance(existing, ProviderSchemaNode)
                        and isinstance(field, ProviderSchemaNode)
                        and existing.enum is not None and field.enum is not None):
                    existing.enum.extend(value for value in field.enum if value not in existing.enum)
                else:
                    variants = differing_properties.setdefault(name, [existing])
                    candidate = field.clone() if isinstance(field, ProviderSchemaNode) else field
                    if candidate not in variants:
                        variants.append(candidate)
    for name, variants in differing_properties.items():
        properties[name] = ProviderSchemaNode(any_of=variants)
    if discriminator_values:
        properties[discriminator] = ProviderSchemaNode(
            schema_type="string", enum=discriminator_values,
            description="Selects the strict object variant validated by DevWerk.",
        )
    return ProviderSchemaNode(
        schema_type="object", properties=properties,
        required=sorted((shared_required or set()) | {discriminator}),
        additional_properties=False,
        description=" ".join([
            f"Select one object shape using discriminator property {discriminator!r}.",
            *variant_requirements, "Do not mix fields from different variants.",
        ]),
    )


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"

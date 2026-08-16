from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


class ContractError(ValueError):
    pass


def canonicalize_contract_value(value: Any, schema: dict[str, Any]) -> Any:
    """Normalize provider-native structural wrappers into the declared JSON shape.

    Some Anthropic-compatible providers serialize arrays as an ``item`` wrapper
    and JSON primitives as strings. The adapter may remove only these syntactic
    wrappers; semantic validation remains the contract validator's job.
    """
    return _canonicalize(value, schema, schema)


def _canonicalize(value: Any, schema: Any, root: dict[str, Any]) -> Any:
    if not isinstance(schema, dict):
        return value
    provider_reference = _provider_reference(value)
    if provider_reference is not None:
        return provider_reference
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        resolved: Any = root
        for token in reference[2:].split("/"):
            if not isinstance(resolved, dict):
                return value
            resolved = resolved.get(token.replace("~1", "/").replace("~0", "~"))
        return _canonicalize(value, resolved, root)

    for keyword in ("oneOf", "anyOf"):
        choices = schema.get(keyword)
        if isinstance(choices, list):
            if isinstance(value, str) and not value.strip() and any(
                isinstance(choice, dict) and choice.get("type") == "null"
                for choice in choices
            ):
                return None
            for choice in choices:
                candidate = _canonicalize(value, choice, root)
                try:
                    Draft202012Validator({**root, **choice}).validate(candidate)
                    return candidate
                except (SchemaError, ValidationError):
                    continue

    declared_type = schema.get("type")
    constant = schema.get("const")
    if isinstance(value, str) and isinstance(constant, bool):
        lowered = value.strip().casefold()
        if lowered in {"true", "false"}:
            return lowered == "true"
    if isinstance(value, str) and isinstance(constant, int) and not isinstance(constant, bool):
        stripped = value.strip()
        if stripped and stripped.lstrip("-").isdigit():
            return int(stripped)
    if declared_type == "array":
        if isinstance(value, dict) and set(value) == {"$text"} and isinstance(value["$text"], str):
            decoded = _decode_provider_json_text(value["$text"], list)
            if decoded is not None:
                value = decoded
        if isinstance(value, dict) and set(value) == {"item"}:
            value = value["item"]
            if value is None or value == "":
                value = []
            elif not isinstance(value, list):
                value = [value]
        elif isinstance(value, str):
            value = [] if not value.strip() else [value]
        if isinstance(value, list):
            return [_canonicalize(item, schema.get("items", {}), root) for item in value]
        return value
    if declared_type == "object" and isinstance(value, dict):
        if set(value) == {"$text"} and isinstance(value["$text"], str):
            decoded = _decode_provider_json_text(value["$text"], dict)
            if decoded is not None:
                value = decoded
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        return {
            key: _canonicalize(item, properties.get(key, {}), root)
            for key, item in value.items()
        }
    if declared_type == "boolean" and isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "false"}:
            return lowered == "true"
    if declared_type == "integer" and isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.lstrip("-").isdigit():
            return int(stripped)
    if declared_type == "number" and isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return value
    return value


def _decode_provider_json_text(value: str, expected_type: type[Any]) -> Any | None:
    """Decode one Provider JSON-text wrapper when its declared shape agrees."""
    try:
        decoded = json.loads(value.strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, expected_type) else None


def _provider_reference(value: Any) -> dict[str, str] | None:
    """Decode one exact Provider transport wrapper for DevWerk's reserved $ref."""
    if not isinstance(value, str):
        return None
    prefix = "<$ref>"
    suffix = "</$ref>"
    if not value.startswith(prefix) or not value.endswith(suffix):
        return None
    pointer = value[len(prefix):-len(suffix)]
    if not pointer.startswith("/") or any(
        character in pointer
        for character in {"<", ">", "\r", "\n", "\t"}
    ):
        return None
    index = 0
    while index < len(pointer):
        if pointer[index] != "~":
            index += 1
            continue
        if index + 1 >= len(pointer) or pointer[index + 1] not in {"0", "1"}:
            return None
        index += 2
    return {"$ref": pointer}


def provider_contract_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Materialize a compact provider-facing schema without weakening validation.

    Provider tool protocols do not consistently resolve local JSON Schema
    references or discriminated nullable unions. This view inlines references,
    removes presentation-only metadata, and presents discriminated object unions
    as one object with an explicit discriminator. Tool results are still
    canonicalized and validated against the original schema.
    """
    return _provider_schema(schema, schema, ())


def _provider_schema(schema: Any, root: dict[str, Any], seen: tuple[str, ...]) -> Any:
    if not isinstance(schema, dict):
        return schema
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        if reference in seen:
            return {"type": "object"}
        resolved = _resolve_reference(root, reference)
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        materialized = _provider_schema({**resolved, **siblings}, root, (*seen, reference))
        return materialized

    choices_keyword = "oneOf" if isinstance(schema.get("oneOf"), list) else "anyOf" if isinstance(schema.get("anyOf"), list) else None
    if choices_keyword:
        choices = [
            _provider_schema(choice, root, seen)
            for choice in schema[choices_keyword]
            if isinstance(choice, dict)
        ]
        non_null = [choice for choice in choices if choice.get("type") != "null"]
        if len(non_null) == 1 and len(non_null) != len(choices):
            return _merge_provider_annotations(non_null[0], schema)
        discriminator = schema.get("discriminator")
        if non_null and all(choice.get("type") == "object" for choice in non_null):
            property_name = discriminator.get("propertyName") if isinstance(discriminator, dict) else None
            if property_name:
                prepared = [
                    _require_provider_discriminator(choice, str(property_name))
                    for choice in non_null
                ]
                return _merge_provider_discriminated_objects(prepared, str(property_name))

    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {"$defs", "definitions", "title", "default", "discriminator"}:
            continue
        if key in {"oneOf", "anyOf"} and isinstance(value, list):
            result[key] = [_provider_schema(choice, root, seen) for choice in value]
        elif key == "properties" and isinstance(value, dict):
            result[key] = {
                name: _provider_schema(property_schema, root, seen)
                for name, property_schema in value.items()
            }
        elif key == "items":
            result[key] = _provider_schema(value, root, seen)
        else:
            result[key] = value
    constant = result.pop("const", None)
    if constant is not None:
        result["enum"] = [constant]
        result.setdefault("type", _json_type(constant))
    return result


def _resolve_reference(root: dict[str, Any], reference: str) -> dict[str, Any]:
    resolved: Any = root
    for token in reference[2:].split("/"):
        if not isinstance(resolved, dict):
            return {}
        resolved = resolved.get(token.replace("~1", "/").replace("~0", "~"))
    return resolved if isinstance(resolved, dict) else {}


def _merge_provider_annotations(base: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    if isinstance(source.get("description"), str):
        result.setdefault("description", source["description"])
    return result


def _require_provider_discriminator(choice: dict[str, Any], discriminator: str) -> dict[str, Any]:
    result = dict(choice)
    required = set(result.get("required") or [])
    if discriminator in (result.get("properties") or {}):
        required.add(discriminator)
    if required:
        result["required"] = sorted(required)
    return result


def _merge_provider_discriminated_objects(
    choices: list[dict[str, Any]],
    discriminator: str,
) -> dict[str, Any]:
    """Expose one shallow Provider object while preserving strict validation.

    Some Anthropic-compatible Providers translate nested ``oneOf`` object
    unions through an XML-shaped intermediate representation and emit an empty
    object for the selected branch. A merged authoring view keeps the
    discriminator and all variant properties visible without asking that
    transport to preserve nested union structure. Returned values are still
    validated against the original discriminated schema.
    """

    properties: dict[str, Any] = {}
    shared_required: set[str] | None = None
    variant_requirements: list[str] = []
    discriminator_values: list[Any] = []

    for choice in choices:
        choice_properties = choice.get("properties")
        if not isinstance(choice_properties, dict):
            continue
        required = set(choice.get("required") or [])
        shared_required = required if shared_required is None else shared_required & required
        discriminator_schema = choice_properties.get(discriminator)
        values = (
            discriminator_schema.get("enum")
            if isinstance(discriminator_schema, dict)
            and isinstance(discriminator_schema.get("enum"), list)
            else []
        )
        discriminator_values.extend(value for value in values if value not in discriminator_values)
        label = repr(values[0]) if len(values) == 1 else repr(values)
        variant_fields = sorted(required - {discriminator})
        variant_requirements.append(
            f"When {discriminator}={label}, required fields are "
            f"{', '.join(variant_fields) if variant_fields else '(none beyond the discriminator)'}."
        )
        for name, property_schema in choice_properties.items():
            existing = properties.get(name)
            if existing is None:
                properties[name] = property_schema
            elif existing != property_schema:
                if (
                    isinstance(existing, dict)
                    and isinstance(property_schema, dict)
                    and isinstance(existing.get("enum"), list)
                    and isinstance(property_schema.get("enum"), list)
                ):
                    merged_values = list(existing["enum"])
                    merged_values.extend(
                        value for value in property_schema["enum"] if value not in merged_values
                    )
                    properties[name] = {**existing, "enum": merged_values}
                else:
                    properties[name] = {"anyOf": [existing, property_schema]}

    if discriminator_values:
        properties[discriminator] = {
            "type": "string",
            "enum": discriminator_values,
            "description": "Selects the strict object variant validated by DevWerk.",
        }
    required = set(shared_required or set())
    required.add(discriminator)
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(required),
        "additionalProperties": False,
        "description": " ".join(
            [
                f"Select one object shape using discriminator property {discriminator!r}.",
                *variant_requirements,
                "Do not mix fields from different variants.",
            ]
        ),
    }


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def check_schema(schema: dict[str, Any], *, label: str) -> None:
    if not schema:
        return
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ContractError(f"{label} is not a valid JSON Schema: {exc.message}") from exc


def validate_contract(value: Any, schema: dict[str, Any], *, label: str) -> None:
    if not schema:
        return
    check_schema(schema, label=label)
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "$"
        detail = _nested_validation_detail(exc)
        suffix = f"; branch details: {detail}" if detail else ""
        raise ContractError(
            f"{label} rejected value at {location}: {exc.message}{suffix}"
        ) from exc


def validate_contract_template(value: Any, schema: dict[str, Any], *, label: str) -> None:
    """Validate a declarative value while deferring only unresolved $ref leaves.

    Runtime references do not excuse invalid surrounding structure. In particular,
    unknown object properties, missing required properties, and invalid container
    shapes remain publication-time contract errors.
    """
    if not schema:
        return
    check_schema(schema, label=label)
    for error in Draft202012Validator(schema).iter_errors(value):
        if _template_error_is_deferred(error, value):
            continue
        location = "/".join(str(part) for part in error.absolute_path) or "$"
        detail = _nested_validation_detail(error)
        suffix = f"; branch details: {detail}" if detail else ""
        raise ContractError(
            f"{label} rejected value at {location}: {error.message}{suffix}"
        ) from error


def _template_error_is_deferred(error: ValidationError, value: Any) -> bool:
    if error.context:
        return all(_template_error_is_deferred(child, value) for child in error.context)
    if error.validator in {
        "additionalProperties",
        "required",
        "minProperties",
        "maxProperties",
        "minItems",
        "maxItems",
        "uniqueItems",
    }:
        return False
    current = value
    if _is_runtime_reference(current):
        return True
    for token in error.absolute_path:
        try:
            current = current[token] if isinstance(current, (dict, list)) else None
        except (KeyError, IndexError, TypeError):
            return False
        if _is_runtime_reference(current):
            return True
    return False


def _is_runtime_reference(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"$ref"}
        and isinstance(value.get("$ref"), str)
        and str(value["$ref"]).startswith("/")
    )


def _nested_validation_detail(error: ValidationError, *, limit: int = 8) -> str:
    leaves: list[ValidationError] = []

    def collect(item: ValidationError) -> None:
        if item.context:
            for child in item.context:
                collect(child)
            return
        leaves.append(item)

    collect(error)
    keyword = str(error.validator)
    mismatched_branches = {
        branch
        for item in leaves
        if item.validator == "const"
        and list(item.absolute_path)
        and str(list(item.absolute_path)[-1]) in {"kind", "type"}
        and (branch := _validation_branch_index(item, keyword)) is not None
    }
    if mismatched_branches:
        leaves = [
            item
            for item in leaves
            if _validation_branch_index(item, keyword) not in mismatched_branches
        ]
    matching = [item for item in leaves if _matches_discriminator_branch(item)]
    if matching:
        leaves = matching
    if not leaves or not error.context:
        return ""
    priority = {
        "additionalProperties": 0,
        "required": 1,
        "const": 2,
        "enum": 3,
        "type": 4,
    }
    rendered: list[str] = []
    for item in sorted(
        leaves,
        key=lambda candidate: (
            priority.get(str(candidate.validator), 10),
            len(candidate.absolute_path),
        ),
    ):
        location = "/".join(str(part) for part in item.absolute_path) or "$"
        message = f"{location}: {item.message}"
        if message not in rendered:
            rendered.append(message)
        if len(rendered) >= limit:
            break
    return " | ".join(rendered)


def _validation_branch_index(error: ValidationError, keyword: str) -> int | None:
    path = list(error.absolute_schema_path)
    for index in range(len(path) - 2, -1, -1):
        if path[index] == keyword and isinstance(path[index + 1], int):
            return int(path[index + 1])
    return None


def _matches_discriminator_branch(error: ValidationError) -> bool:
    current: ValidationError | None = error
    while current is not None:
        schema = current.schema
        instance = current.instance
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if isinstance(properties, dict) and isinstance(instance, dict):
            for discriminator in ("kind", "type"):
                declared = properties.get(discriminator)
                if not isinstance(declared, dict) or "const" not in declared:
                    continue
                if discriminator in instance:
                    return instance[discriminator] == declared["const"]
        current = current.parent
    return True

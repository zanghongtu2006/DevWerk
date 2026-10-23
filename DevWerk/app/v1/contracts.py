from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


class ContractError(ValueError):
    def __init__(self, message: str, *, path: str | None = None,
                 expected: str | None = None, actual: str | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.expected = expected
        self.actual = actual


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
                    Draft202012Validator(root).evolve(schema=choice).validate(candidate)
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
        value = _recover_misplaced_named_properties(value, properties)
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


_MISPLACED_PARAMETER = re.compile(
    r',\s*"?parameter\s+name\s*=\s*"(?P<name>[^"<>]+)"\s*>\s*',
    re.IGNORECASE,
)


def _recover_misplaced_named_properties(
    value: dict[str, Any],
    properties: dict[str, Any],
) -> dict[str, Any]:
    """Recover exact fields misplaced by an Anthropic-compatible transport.

    Some compatible Providers occasionally append a following named parameter to
    a JSON-text parameter, for example ``{\"a\":1},\"parameter
    name=\"summary\">done``. Recovery is deliberately narrow: the source prefix
    must decode to the source property's declared JSON container type, and every
    recovered name must be declared by the surrounding object schema and absent
    from the Provider object. No semantic value is invented.
    """
    for source_name, source_value in value.items():
        if not isinstance(source_value, str):
            continue
        matches = list(_MISPLACED_PARAMETER.finditer(source_value))
        if not matches:
            continue
        recovered_names = [match.group("name") for match in matches]
        if any(name not in properties or name in value for name in recovered_names):
            continue
        source_schema = properties.get(source_name)
        source_type = source_schema.get("type") if isinstance(source_schema, dict) else None
        expected_type = dict if source_type == "object" else list if source_type == "array" else None
        if expected_type is None:
            continue
        decoded = _decode_provider_json_text(source_value[: matches[0].start()], expected_type)
        if decoded is None:
            continue
        recovered = dict(value)
        recovered[source_name] = decoded
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(source_value)
            recovered[match.group("name")] = source_value[match.end() : end]
        return recovered
    return value


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
    from app.v1.protocols.tools.core.provider_schema_contracts import ProviderSchemaNode
    return ProviderSchemaNode(reference=pointer).wire()


def provider_contract_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Build a typed compatibility view while retaining the strict source contract."""
    from app.v1.protocols.tools.core.provider_schema_contracts import provider_view

    return provider_view(schema)


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
        expected = str(exc.validator_value) if exc.validator == "type" else None
        actual = _json_value_type(exc.instance) if expected else None
        reason = f"expected {expected}, received {actual}" if expected else exc.message
        raise ContractError(
            f"{label} rejected value at {location}: {reason}{suffix}",
            path=location, expected=expected, actual=actual,
        ) from exc


def _json_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def validate_optional_defaults(value: dict[str, Any], schema: dict[str, Any], *, label: str) -> None:
    """Validate supplied defaults without mutating the declared parameter schema.

    Only missing top-level required parameters are deferred until callers supply
    bindings. Nested required fields in an explicitly supplied value remain errors.
    """
    check_schema(schema, label=label)
    for error in Draft202012Validator(schema).iter_errors(value):
        if error.validator == "required" and not error.absolute_path:
            continue
        location = "/".join(str(part) for part in error.absolute_path) or "$"
        raise ContractError(f"{label} rejected value at {location}: {error.message}") from error


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
        expected = str(error.validator_value) if error.validator == "type" else None
        actual = _json_value_type(error.instance) if expected else None
        reason = f"expected {expected}, received {actual}" if expected else error.message
        raise ContractError(
            f"{label} rejected value at {location}: {reason}{suffix}",
            path=location, expected=expected, actual=actual,
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

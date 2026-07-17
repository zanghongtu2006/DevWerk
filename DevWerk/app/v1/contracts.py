from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


class ContractError(ValueError):
    pass


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
        raise ContractError(f"{label} rejected value at {location}: {exc.message}") from exc

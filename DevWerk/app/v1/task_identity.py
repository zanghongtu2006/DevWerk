from __future__ import annotations

import json
from typing import Any

from app.v1.domain import TaskContract


def resolve_input_pointer(value: dict[str, Any], pointer: str) -> Any:
    current: Any = value
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise ValueError(f"Task input cannot resolve identity pointer {pointer!r}")
        current = current[token]
    return current


def task_identity_pointer(contract: TaskContract) -> str | None:
    if contract.identity_pointer:
        return contract.identity_pointer
    if contract.dependency_contract is not None:
        return contract.dependency_contract.order_pointer
    return None


def logical_task_key(contract: TaskContract, input_data: dict[str, Any]) -> str | None:
    pointer = task_identity_pointer(contract)
    if pointer is None:
        return None
    return logical_task_key_for_value(pointer, resolve_input_pointer(input_data, pointer))


def logical_task_key_for_value(pointer: str, value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{pointer}={encoded}"

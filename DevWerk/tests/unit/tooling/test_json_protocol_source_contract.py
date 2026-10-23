from tests.support.paths import REPO_ROOT
"""Prevent raw JSON object stitching from returning to application code."""

import ast
from pathlib import Path


APP = REPO_ROOT / "app"


def test_application_does_not_unpack_dicts_into_json_objects():
    offenders = []
    for path in APP.rglob("*.py"):
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.relative_to(APP)}:{node.lineno}"
            for node in ast.walk(syntax)
            if isinstance(node, ast.Dict) and any(key is None for key in node.keys)
        )
    assert offenders == []


def test_application_does_not_serialize_literal_json_objects():
    offenders = []
    for path in APP.rglob("*.py"):
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.relative_to(APP)}:{node.lineno}"
            for node in ast.walk(syntax)
            if isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Dict)
            and ((isinstance(node.func, ast.Attribute) and node.func.attr in {"dumps", "_pack_json"})
                 or (isinstance(node.func, ast.Name) and node.func.id in {"stable_json", "packed", "stable"}))
        )
    assert offenders == []


def test_application_does_not_serialize_locally_assembled_json_dicts():
    offenders = []
    for path in APP.rglob("*.py"):
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(syntax):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assembled = {}
            for node in ast.walk(function):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            assembled[target.id] = node.lineno
            for node in ast.walk(function):
                if (isinstance(node, ast.Call) and node.args
                        and isinstance(node.args[0], ast.Name)
                        and ((isinstance(node.func, ast.Attribute) and node.func.attr in {"dumps", "_pack_json"})
                             or (isinstance(node.func, ast.Name) and node.func.id in {"stable_json", "packed", "stable"}))
                        and node.args[0].id in assembled
                        and assembled[node.args[0].id] < node.lineno):
                    offenders.append(f"{path.relative_to(APP)}:{assembled[node.args[0].id]}->{node.lineno}")
    assert offenders == []


def test_application_does_not_define_schema_types_as_dict_literals():
    schema_types = {"object", "array", "string", "integer", "boolean", "number", "null"}
    offenders = []
    for path in APP.rglob("*.py"):
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(syntax):
            if not isinstance(node, ast.Dict):
                continue
            if any(
                isinstance(key, ast.Constant) and key.value == "type"
                and isinstance(value, ast.Constant) and value.value in schema_types
                for key, value in zip(node.keys, node.values)
            ):
                offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert offenders == []


def test_application_does_not_merge_json_objects_with_dict_union():
    offenders = []
    for path in APP.rglob("*.py"):
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.relative_to(APP)}:{node.lineno}"
            for node in ast.walk(syntax)
            if isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.BitOr)
            and (isinstance(node.left, ast.Dict) or isinstance(node.right, ast.Dict))
        )
    assert offenders == []


def test_capability_handlers_do_not_return_unnamed_dict_literals():
    path = APP / "v1" / "tooling" / "capability_handlers.py"
    syntax = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        node.lineno for node in ast.walk(syntax)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    ]
    assert offenders == []


def test_capability_handlers_do_not_append_or_update_unnamed_json_objects():
    path = APP / "v1" / "tooling" / "capability_handlers.py"
    syntax = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        node.lineno for node in ast.walk(syntax)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"append", "update"}
        and node.args and isinstance(node.args[0], ast.Dict)
    ]
    assert offenders == []


def test_all_builtin_capabilities_declare_a_success_output_contract():
    from app.v1.capabilities import build_core_registry

    registry = build_core_registry()
    assert [identity for identity in registry.all_ids() if not registry.output_schema(identity)] == []

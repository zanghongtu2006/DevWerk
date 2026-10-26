"""Frozen pre-guide Workflow packages used only to test legacy Project behavior.

Production Loop discovery never reads these fixtures and cannot publish them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.v1.contracts import canonicalize_contract_value, validate_contract
from app.v1.domain import WorkflowDefinition, WorkflowPlan
from app.v1.storage_support import new_id, utcnow

_FIXTURE_ROOT = Path(__file__).resolve().parent / "legacy_loop_fixtures"
_DIRECTORIES = {
    "novel.production": "novel-production",
    "software.ddd_delivery": "ddd-software-delivery",
    "software.gitlab_devops": "gitlab-devops",
}
_VERSIONS = {
    "novel.production": "1.5.2",
    "software.ddd_delivery": "1.4.10",
    "software.gitlab_devops": "1.2.0",
}


def _resolve_fixture_parameters(value: Any, parameters: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$param"}:
            return parameters[value["$param"]]
        return {key: _resolve_fixture_parameters(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_fixture_parameters(item, parameters) for item in value]
    return value


def legacy_package(loop_key: str) -> dict[str, Any]:
    path = _FIXTURE_ROOT / (_DIRECTORIES[loop_key] + ".json")
    package = json.loads(path.read_text(encoding="utf-8"))
    assets_root = Path(__file__).resolve().parents[2] / "loops" / _DIRECTORIES[loop_key] / "assets"
    assets = [
        {"path": item.relative_to(assets_root).as_posix(), "content": item.read_text(encoding="utf-8")}
        for item in sorted(assets_root.rglob("*.md"))
    ] if assets_root.is_dir() else []
    package.update({
        "loop_key": loop_key,
        "version": _VERSIONS[loop_key],
        "digest": hashlib.sha256(path.read_bytes()).hexdigest(),
        "assets": assets,
    })
    return package


def apply_legacy_fixture(store, project_id: str, loop_key: str, bindings: dict[str, Any]) -> dict[str, Any]:
    """Seed a frozen historical Project without exposing a Loop publish API."""
    package = legacy_package(loop_key)
    values = canonicalize_contract_value(
        {**package["bundle"].get("defaults", {}), **bindings}, package["parameter_schema"]
    )
    validate_contract(values, package["parameter_schema"], label="Historical bindings")
    materialized = _resolve_fixture_parameters(package["bundle"], values)
    plan = store.create_workflow_plan(project_id, WorkflowPlan.model_validate(materialized["workflow_plan"]))
    workflow = store.publish_workflow(
        project_id, WorkflowDefinition.model_validate(materialized["workflow"]), plan["id"]
    )
    application_id, now = new_id("loopapp"), utcnow()
    with store.tx(immediate=True) as db:
        db.execute(
            "INSERT INTO v1_project_loop_bindings("
            "id,project_id,loop_key,loop_version,loop_digest,bindings_json,"
            "workflow_plan_id,workflow_revision_id,created_at,package_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (application_id, project_id, loop_key, package["version"], package["digest"],
             json.dumps(values, ensure_ascii=False), plan["id"], workflow["id"], now,
             json.dumps(package, ensure_ascii=False)),
        )
        db.execute(
            "UPDATE v1_workflows SET source_loop_key=?,source_loop_version=?,source_loop_digest=? "
            "WHERE project_id=?",
            (loop_key, package["version"], package["digest"], project_id),
        )
    store.scopes.bind_workflow(workflow["id"], application_id)
    return {
        "id": application_id,
        "loop": package,
        "bindings": values,
        "workflow_plan": plan,
        "workflow": store.get_workflow(project_id),
        "created_at": now,
    }

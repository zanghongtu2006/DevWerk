from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.kanban.store import add_artifact, add_event, get_conversation, get_task


TABLE_MEMORY_ITEMS = "kb_memory_items"
TABLE_CONTEXT_PACKS = "kb_context_packs"
TABLE_AGENT_RUNS = "kb_agent_runs"
TABLE_PROMOTION_CANDIDATES = "kb_promotion_candidates"

SCOPES = {"workspace", "project", "workflow", "task", "session", "run"}
PROJECT_MEMORY_TYPES = {
    "project_profile",
    "project_rules",
    "project_rule",
    "architecture_summary",
    "source_map",
    "code_summary",
    "dependency_map",
    "test_strategy",
    "known_issues",
    "known_issue",
    "historical_decisions",
    "api_contract",
}
TASK_MEMORY_TYPES = {
    "task_brief",
    "task_constraints",
    "task_plan",
    "task_progress",
    "task_analysis_summary",
    "task_code_context",
    "task_decisions",
    "task_handoff_summary",
    "task_test_state",
    "task_final_summary",
    "promotion_candidates",
    "patch_summary",
    "test_state",
}
WORKFLOW_MEMORY_TYPES = {
    "workflow_definition",
    "workflow_state",
    "current_stage",
    "stage_outputs",
    "agent_assignments",
    "blocking_issues",
    "transition_conditions",
}
SESSION_MEMORY_TYPES = {"task_session_summary", "recent_key_messages", "explicit_user_constraints"}
RUN_MEMORY_TYPES = {
    "run_state",
    "local_plan",
    "tool_results",
    "observations",
    "output",
    "writeback_payload",
}
PROMOTION_TARGET_TYPES = {
    "project_rule",
    "architecture_summary",
    "source_map",
    "code_summary",
    "test_strategy",
    "known_issue",
    "api_contract",
    "dependency_map",
}

_initialized = False


def init_memory_db() -> None:
    global _initialized
    if _initialized:
        return
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS kb_memory_items (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                key TEXT NOT NULL,
                content_json TEXT NOT NULL DEFAULT '{}',
                source_type TEXT NOT NULL DEFAULT '',
                source_ref_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, task_id, scope, memory_type, key)
            );

            CREATE INDEX IF NOT EXISTS idx_kb_memory_items_project_scope
                ON kb_memory_items(project_id, scope, memory_type);

            CREATE INDEX IF NOT EXISTS idx_kb_memory_items_task
                ON kb_memory_items(task_id, scope, memory_type);

            CREATE TABLE IF NOT EXISTS kb_context_packs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL DEFAULT '',
                run_id TEXT,
                agent_role TEXT NOT NULL,
                stage TEXT NOT NULL,
                token_budget INTEGER NOT NULL DEFAULT 0,
                content_json TEXT NOT NULL DEFAULT '{}',
                included_memory_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_kb_context_packs_task
                ON kb_context_packs(task_id, created_at);

            CREATE TABLE IF NOT EXISTS kb_agent_runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL DEFAULT '',
                agent_role TEXT NOT NULL,
                stage TEXT NOT NULL,
                input_context_pack_id TEXT,
                status TEXT NOT NULL,
                local_plan_json TEXT NOT NULL DEFAULT '{}',
                tool_results_json TEXT NOT NULL DEFAULT '[]',
                observations_json TEXT NOT NULL DEFAULT '[]',
                output_json TEXT NOT NULL DEFAULT '{}',
                writeback_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_kb_agent_runs_task
                ON kb_agent_runs(task_id, created_at);

            CREATE TABLE IF NOT EXISTS kb_promotion_candidates (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                run_id TEXT,
                target_memory_type TEXT NOT NULL,
                content_json TEXT NOT NULL DEFAULT '{}',
                reason TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reviewed_at TEXT,
                review_note TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_kb_promotion_candidates_project
                ON kb_promotion_candidates(project_id, status, created_at);
            """
        )
    _initialized = True


def upsert_memory_item(
    *,
    project_id: str,
    task_id: str | None = None,
    scope: str,
    memory_type: str,
    key: str,
    content: dict[str, Any],
    source_type: str,
    source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_memory_db()
    scope_key = _validate_scope(scope)
    type_key = _validate_memory_type(scope_key, memory_type)
    project_key = _safe_id(project_id)
    task_key = str(task_id or "")
    item_key = str(key or "latest").strip() or "latest"
    item_id = f"mem_{uuid.uuid4()}"
    now = _now()
    with _conn() as conn:
        existing = conn.execute(
            """
            SELECT id, created_at
              FROM kb_memory_items
             WHERE project_id = ? AND task_id = ? AND scope = ? AND memory_type = ? AND key = ?
            """,
            (project_key, task_key, scope_key, type_key, item_key),
        ).fetchone()
        if existing is not None:
            item_id = existing["id"]
            created_at = existing["created_at"]
            conn.execute(
                """
                UPDATE kb_memory_items
                   SET content_json = ?, source_type = ?, source_ref_json = ?, updated_at = ?
                 WHERE id = ?
                """,
                (_json(content), str(source_type or ""), _json(source_ref or {}), now, item_id),
            )
            event_type = "memory_item_updated"
        else:
            created_at = now
            conn.execute(
                """
                INSERT INTO kb_memory_items (
                    id, project_id, task_id, scope, memory_type, key, content_json,
                    source_type, source_ref_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    project_key,
                    task_key,
                    scope_key,
                    type_key,
                    item_key,
                    _json(content),
                    str(source_type or ""),
                    _json(source_ref or {}),
                    now,
                    now,
                ),
            )
            event_type = "memory_item_created"
    if task_key:
        _safe_event(task_key, event_type, {"memory_id": item_id, "scope": scope_key, "memory_type": type_key, "key": item_key})
    return {
        "id": item_id,
        "project_id": project_key,
        "task_id": task_key,
        "scope": scope_key,
        "memory_type": type_key,
        "key": item_key,
        "content": content,
        "source_type": str(source_type or ""),
        "source_ref": source_ref or {},
        "created_at": created_at,
        "updated_at": now,
    }


def read_task_memory(task_id: str) -> dict[str, Any]:
    init_memory_db()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT *
              FROM kb_memory_items
             WHERE task_id = ? AND scope = 'task'
             ORDER BY created_at ASC
            """,
            (task_id,),
        ).fetchall()
    memory: dict[str, Any] = {}
    append_types = {"task_decisions", "task_handoff_summary"}
    for row in rows:
        memory_type = row["memory_type"]
        content = _loads(row["content_json"], {})
        if memory_type in append_types:
            bucket = memory.setdefault(memory_type, {"items": []})
            bucket["items"].append({**content, "key": row["key"], "memory_id": row["id"], "created_at": row["created_at"]})
        else:
            bucket = memory.setdefault(memory_type, {})
            bucket[row["key"]] = content
    return memory


def read_project_memory_items(project_id: str, memory_type: str | None = None) -> dict[str, Any]:
    init_memory_db()
    params: list[Any] = [_safe_id(project_id)]
    where = "project_id = ? AND scope = 'project'"
    if memory_type:
        aliases = _project_memory_type_aliases(str(memory_type).strip())
        where += f" AND memory_type IN ({','.join('?' for _ in aliases)})"
        params.extend(aliases)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM kb_memory_items WHERE {where} ORDER BY updated_at DESC",
            params,
        ).fetchall()
    return {"ok": True, "project_id": project_id, "items": [_memory_item(row) for row in rows]}


def _project_memory_type_aliases(memory_type: str) -> list[str]:
    if memory_type in {"project_rule", "project_rules"}:
        return ["project_rule", "project_rules"]
    if memory_type in {"known_issue", "known_issues"}:
        return ["known_issue", "known_issues"]
    return [memory_type]


def create_agent_run(
    *,
    project_id: str,
    task_id: str,
    workflow_id: str,
    agent_role: str,
    stage: str,
    token_budget: int = 0,
) -> dict[str, Any]:
    init_memory_db()
    run_id = f"run_{uuid.uuid4()}"
    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO kb_agent_runs (
                id, project_id, task_id, workflow_id, agent_role, stage, status,
                local_plan_json, tool_results_json, observations_json, output_json,
                writeback_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', '{}', '[]', '[]', '{}', '{}', ?, ?)
            """,
            (run_id, _safe_id(project_id), task_id, str(workflow_id or ""), str(agent_role or ""), str(stage or ""), now, now),
        )
    _safe_event(task_id, "agent_run_started", {"run_id": run_id, "agent_role": agent_role, "stage": stage, "token_budget": token_budget})
    return {"ok": True, "run_id": run_id, "project_id": project_id, "task_id": task_id, "status": "running"}


def build_context_pack(
    *,
    project_id: str,
    task_id: str,
    workflow_id: str,
    agent_role: str,
    stage: str,
    token_budget: int = 4096,
    run_id: str | None = None,
    workspace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_memory_db()
    project_key = _safe_id(project_id)
    task_memory = read_task_memory(task_id)
    project_items = _project_items_by_type(project_key)
    included_ids: list[str] = []
    project_rules = _keyed_project_items(project_items, {"project_rules", "project_rule"}, included_ids)
    project_profile = _keyed_project_items(project_items, {"project_profile"}, included_ids)
    architecture_summary = _keyed_project_items(project_items, {"architecture_summary"}, included_ids)
    test_strategy = _keyed_project_items(project_items, {"test_strategy"}, included_ids)

    task_brief = _latest_or_empty(task_memory, "task_brief")
    task_constraints = _latest_or_empty(task_memory, "task_constraints")
    task_plan = _latest_or_empty(task_memory, "task_plan")
    task_analysis_summary = _latest_or_empty(task_memory, "task_analysis_summary")
    task_code_context = _latest_or_empty(task_memory, "task_code_context")
    if not task_code_context:
        task_code_context = _build_task_code_context_from_workspace(workspace)
    task_decisions = (task_memory.get("task_decisions") or {}).get("items") or []
    task_handoff = (task_memory.get("task_handoff_summary") or {}).get("items") or []
    task_final_summary = _latest_or_empty(task_memory, "task_final_summary")
    latest_failure_bundle = _latest_task_artifact_payload(task_id, "failure_bundle")
    conversation = get_conversation(task_id) or {}
    recent_messages = [
        {"role": msg.get("role"), "content": str(msg.get("content") or "")[:600]}
        for msg in (conversation.get("messages") or [])[-4:]
        if isinstance(msg, dict)
    ]
    pack = {
        "context_pack_id": f"ctx_{uuid.uuid4()}",
        "project": {
            "profile": project_profile,
            "rules": project_rules,
            "architecture_summary": architecture_summary,
            "test_strategy": test_strategy,
        },
        "task": {
            "brief": task_brief,
            "constraints": task_constraints,
            "plan": task_plan,
            "progress": _latest_or_empty(task_memory, "task_progress"),
            "analysis_summary": task_analysis_summary,
            "decisions": task_decisions[-8:],
            "code_context": task_code_context,
            "handoff_history": task_handoff[-6:],
            "test_state": _latest_or_empty(task_memory, "task_test_state") or _latest_or_empty(task_memory, "test_state"),
            "final_summary": task_final_summary,
            "latest_failure_bundle": latest_failure_bundle or {},
        },
        "workflow": {
            "workflow_id": str(workflow_id or ""),
            "current_stage": stage,
            "agent_role": agent_role,
        },
        "session": {
            "summary": conversation.get("summary") or "",
            "recent_key_messages": recent_messages,
        },
        "knowledge": {
            "source_map": _source_map_summary(workspace),
            "retrieved_code_summaries": _project_memory_contents(project_items, {"code_summary"}, included_ids),
        },
        "agent_instruction": {
            "role": agent_role,
            "stage": stage,
            "output_contract": "Return phase output plus optional writeback payload.",
        },
        "on_demand": ["full_session", "full_source_files", "full_run_trace", "full_test_logs", "uploaded_documents"],
    }
    pack = _rank_and_trim(pack, token_budget)
    pack["debug"] = {
        "included_memory_ids": _dedupe(included_ids),
        "token_budget": int(token_budget or 0),
        "estimated_chars": len(json.dumps(pack, ensure_ascii=False)),
        "load_strategy": {
            "always": ["project_profile", "project_rules", "task_brief", "task_constraints", "workflow_current_stage"],
            "retrieve": ["source_map", "code_summary", "task_decisions", "handoff_summary", "task_code_context"],
            "on_demand": pack["on_demand"],
        },
    }
    context_pack_id = pack["context_pack_id"]
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO kb_context_packs (
                id, project_id, task_id, workflow_id, run_id, agent_role, stage,
                token_budget, content_json, included_memory_ids_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context_pack_id,
                project_key,
                task_id,
                str(workflow_id or ""),
                run_id,
                str(agent_role or ""),
                str(stage or ""),
                int(token_budget or 0),
                _json(pack),
                _json(pack["debug"]["included_memory_ids"]),
                _now(),
            ),
        )
        if run_id:
            conn.execute(
                "UPDATE kb_agent_runs SET input_context_pack_id = ?, updated_at = ? WHERE id = ?",
                (context_pack_id, _now(), run_id),
            )
    _safe_event(
        task_id,
        "context_pack_created",
        {
            "context_pack_id": context_pack_id,
            "run_id": run_id,
            "agent_role": agent_role,
            "stage": stage,
            "memory_ids": pack["debug"]["included_memory_ids"],
            "token_budget": token_budget,
            "estimated_chars": pack["debug"]["estimated_chars"],
        },
    )
    return {"ok": True, "context_pack_id": context_pack_id, "context_pack": pack}


def handle_agent_writeback(run_id: str, writeback: dict[str, Any]) -> dict[str, Any]:
    init_memory_db()
    run = _get_run(run_id)
    project_id = run["project_id"]
    task_id = run["task_id"]
    payload = writeback if isinstance(writeback, dict) else {}
    task_updates = payload.get("task_updates") if isinstance(payload.get("task_updates"), dict) else {}
    workflow_updates = payload.get("workflow_updates") if isinstance(payload.get("workflow_updates"), dict) else {}
    run_updates = payload.get("run_updates") if isinstance(payload.get("run_updates"), dict) else {}
    written: list[dict[str, Any]] = []

    mapping = {
        "progress": ("task_progress", "latest"),
        "analysis_summary": ("task_analysis_summary", "latest"),
        "code_context": ("task_code_context", "latest"),
        "patch_summary": ("patch_summary", "latest"),
        "test_state": ("task_test_state", "latest"),
        "final_summary": ("task_final_summary", "latest"),
    }
    for source_key, (memory_type, key) in mapping.items():
        value = task_updates.get(source_key)
        content = _memory_content(value)
        if content:
            written.append(
                upsert_memory_item(
                    project_id=project_id,
                    task_id=task_id,
                    scope="task",
                    memory_type=memory_type,
                    key=key,
                    content=content,
                    source_type="agent_writeback",
                    source_ref={"run_id": run_id, "stage": run["stage"], "agent_role": run["agent_role"]},
                )
            )
    decisions = task_updates.get("decisions")
    if isinstance(decisions, list):
        for decision in decisions:
            content = _memory_content(decision, scalar_key="decision")
            if content:
                written.append(
                    upsert_memory_item(
                        project_id=project_id,
                        task_id=task_id,
                        scope="task",
                        memory_type="task_decisions",
                        key=f"decision-{uuid.uuid4()}",
                        content=content,
                        source_type="agent_writeback",
                        source_ref={"run_id": run_id},
                    )
                )
    handoff = task_updates.get("handoff_summary")
    handoff_content = _memory_content(handoff, scalar_key="summary")
    if handoff_content:
        written.append(
            upsert_memory_item(
                project_id=project_id,
                task_id=task_id,
                scope="task",
                memory_type="task_handoff_summary",
                key=f"handoff-{uuid.uuid4()}",
                content=handoff_content,
                source_type="agent_writeback",
                source_ref={"run_id": run_id, "stage": run["stage"], "agent_role": run["agent_role"]},
            )
        )
    if workflow_updates:
        written.append(
            upsert_memory_item(
                project_id=project_id,
                task_id=task_id,
                scope="workflow",
                memory_type="workflow_state",
                key="latest",
                content=workflow_updates,
                source_type="agent_writeback",
                source_ref={"run_id": run_id},
            )
        )

    candidates = []
    for item in payload.get("project_memory_candidates") or []:
        if isinstance(item, dict):
            candidates.append(_create_promotion_candidate(project_id, task_id, run_id, item))

    now = _now()
    with _conn() as conn:
        conn.execute(
            """
            UPDATE kb_agent_runs
               SET status = 'completed',
                   observations_json = ?,
                   tool_results_json = ?,
                   output_json = ?,
                   writeback_json = ?,
                   updated_at = ?,
                   completed_at = ?
             WHERE id = ?
            """,
            (
                _json(run_updates.get("observations") or []),
                _json(run_updates.get("tool_results") or []),
                _json(payload.get("output") or {}),
                _json(payload),
                now,
                now,
                run_id,
            ),
        )
    _safe_event(
        task_id,
        "writeback_received",
        {
            "run_id": run_id,
            "memory_ids": [item["id"] for item in written],
            "promotion_candidates": [item["candidate_id"] for item in candidates],
        },
    )
    _safe_event(task_id, "agent_run_finished", {"run_id": run_id, "status": "completed"})
    return {"ok": True, "run_id": run_id, "memory_items": written, "promotion_candidates": candidates}


def list_promotion_candidates(
    *,
    project_id: str,
    task_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    init_memory_db()
    params: list[Any] = [_safe_id(project_id)]
    where = ["project_id = ?"]
    if task_id:
        where.append("task_id = ?")
        params.append(task_id)
    if status:
        where.append("status = ?")
        params.append(status)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM kb_promotion_candidates WHERE {' AND '.join(where)} ORDER BY created_at DESC",
            params,
        ).fetchall()
    return {"ok": True, "project_id": project_id, "candidates": [_candidate(row) for row in rows]}


def approve_promotion_candidate(project_id: str, candidate_id: str, note: str = "") -> dict[str, Any]:
    return _review_candidate(project_id, candidate_id, "approved", note)


def reject_promotion_candidate(project_id: str, candidate_id: str, note: str = "") -> dict[str, Any]:
    return _review_candidate(project_id, candidate_id, "rejected", note)


def get_context_pack(context_pack_id: str) -> dict[str, Any]:
    init_memory_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_context_packs WHERE id = ?", (context_pack_id,)).fetchone()
    if row is None:
        raise KeyError(f"context pack not found: {context_pack_id}")
    return {"ok": True, "context_pack": _loads(row["content_json"], {})}


def _review_candidate(project_id: str, candidate_id: str, status: str, note: str) -> dict[str, Any]:
    init_memory_db()
    project_key = _safe_id(project_id)
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM kb_promotion_candidates WHERE id = ? AND project_id = ?",
            (candidate_id, project_key),
        ).fetchone()
        if row is None:
            raise KeyError(f"promotion candidate not found: {candidate_id}")
        conn.execute(
            """
            UPDATE kb_promotion_candidates
               SET status = ?, updated_at = ?, reviewed_at = ?, review_note = ?
             WHERE id = ?
            """,
            (status, now, now, str(note or ""), candidate_id),
        )
    candidate = _candidate(row)
    candidate["status"] = status
    candidate["reviewed_at"] = now
    candidate["review_note"] = str(note or "")
    if status == "approved":
        upsert_memory_item(
            project_id=project_key,
            task_id="",
            scope="project",
            memory_type=candidate["target_memory_type"],
            key=candidate_id,
            content=candidate["content"],
            source_type="promotion_candidate",
            source_ref={"candidate_id": candidate_id, "task_id": candidate["task_id"], "run_id": candidate.get("run_id")},
        )
    _safe_event(
        candidate["task_id"],
        f"promotion_candidate_{status}",
        {"candidate_id": candidate_id, "target_memory_type": candidate["target_memory_type"], "review_note": note},
    )
    return {"ok": True, "project_id": project_key, "candidate": candidate}


def _create_promotion_candidate(project_id: str, task_id: str, run_id: str, item: dict[str, Any]) -> dict[str, Any]:
    target = str(item.get("target_memory_type") or "").strip()
    if target not in PROMOTION_TARGET_TYPES:
        target = "known_issue"
    candidate_id = f"cand_{uuid.uuid4()}"
    now = _now()
    confidence = _confidence_score(item.get("confidence"))
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO kb_promotion_candidates (
                id, project_id, task_id, run_id, target_memory_type, content_json,
                reason, confidence, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)
            """,
            (
                candidate_id,
                _safe_id(project_id),
                task_id,
                run_id,
                target,
                _json(item.get("content") if isinstance(item.get("content"), dict) else {"value": item.get("content")}),
                str(item.get("reason") or ""),
                confidence,
                now,
                now,
            ),
        )
    _safe_event(task_id, "promotion_candidate_created", {"candidate_id": candidate_id, "target_memory_type": target})
    upsert_memory_item(
        project_id=project_id,
        task_id=task_id,
        scope="task",
        memory_type="promotion_candidates",
        key=candidate_id,
        content={"candidate_id": candidate_id, "target_memory_type": target, "status": "candidate"},
        source_type="agent_writeback",
        source_ref={"run_id": run_id},
    )
    return {
        "candidate_id": candidate_id,
        "project_id": project_id,
        "task_id": task_id,
        "run_id": run_id,
        "target_memory_type": target,
        "content": item.get("content") if isinstance(item.get("content"), dict) else {"value": item.get("content")},
        "reason": str(item.get("reason") or ""),
        "confidence": confidence,
        "status": "candidate",
        "created_at": now,
    }


def _confidence_score(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    text = str(value or "").strip().lower()
    if not text:
        return 0.0
    labels = {
        "none": 0.0,
        "unknown": 0.0,
        "low": 0.25,
        "weak": 0.25,
        "medium": 0.5,
        "moderate": 0.5,
        "mid": 0.5,
        "high": 0.75,
        "strong": 0.75,
        "certain": 0.95,
        "very high": 0.95,
    }
    if text in labels:
        return labels[text]
    if text.endswith("%"):
        try:
            return max(0.0, min(1.0, float(text[:-1].strip()) / 100.0))
        except ValueError:
            return 0.0
    try:
        return max(0.0, min(1.0, float(text)))
    except ValueError:
        return 0.0


def _get_run(run_id: str) -> dict[str, Any]:
    init_memory_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM kb_agent_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"agent run not found: {run_id}")
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "workflow_id": row["workflow_id"],
        "agent_role": row["agent_role"],
        "stage": row["stage"],
        "input_context_pack_id": row["input_context_pack_id"],
        "status": row["status"],
    }


def _project_items_by_type(project_id: str) -> dict[str, list[sqlite3.Row]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM kb_memory_items WHERE project_id = ? AND scope = 'project' ORDER BY updated_at DESC",
            (project_id,),
        ).fetchall()
    out: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        out.setdefault(row["memory_type"], []).append(row)
    return out


def _keyed_project_items(project_items: dict[str, list[sqlite3.Row]], memory_types: set[str], included_ids: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for memory_type in memory_types:
        for row in project_items.get(memory_type, []):
            included_ids.append(row["id"])
            out[row["key"]] = _loads(row["content_json"], {})
    return out


def _project_memory_contents(project_items: dict[str, list[sqlite3.Row]], memory_types: set[str], included_ids: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for memory_type in memory_types:
        for row in project_items.get(memory_type, [])[:8]:
            included_ids.append(row["id"])
            out.append({"key": row["key"], "content": _loads(row["content_json"], {})})
    return out


def _latest_or_empty(task_memory: dict[str, Any], memory_type: str) -> dict[str, Any]:
    bucket = task_memory.get(memory_type)
    if not isinstance(bucket, dict):
        return {}
    latest = bucket.get("latest")
    return latest if isinstance(latest, dict) else {}


def _latest_task_artifact_payload(task_id: str, artifact_type: str) -> dict[str, Any] | None:
    try:
        task = get_task(task_id).get("task") or {}
    except Exception:  # noqa: BLE001
        return None
    artifacts = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
    for artifact in reversed(artifacts):
        if isinstance(artifact, dict) and artifact.get("artifact_type") == artifact_type:
            payload = artifact.get("payload")
            return payload if isinstance(payload, dict) else None
    return None


def _memory_content(value: Any, *, scalar_key: str = "value") -> dict[str, Any]:
    if isinstance(value, dict):
        return value if value else {}
    if isinstance(value, list):
        return {"items": value} if value else {}
    if isinstance(value, str):
        text = value.strip()
        return {scalar_key: text} if text else {}
    if value is None:
        return {}
    return {scalar_key: value}


def _build_task_code_context_from_workspace(workspace: dict[str, Any] | None) -> dict[str, Any]:
    source_map = workspace.get("source_map") if isinstance(workspace, dict) and isinstance(workspace.get("source_map"), dict) else {}
    files = source_map.get("files") if isinstance(source_map, dict) else []
    paths: list[str] = []
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.append(item["path"])
            elif isinstance(item, str):
                paths.append(item)
    paths = _dedupe([path for path in paths if path])[:20]
    return {
        "related_modules": _module_guesses(paths),
        "related_files": paths,
        "files_to_change": paths[:8],
        "files_to_avoid": [],
        "current_behavior": "",
        "possible_change": "",
        "risk_notes": [],
    }


def _source_map_summary(workspace: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(workspace, dict) or not isinstance(workspace.get("source_map"), dict):
        return {"available": False}
    source_map = workspace["source_map"]
    files = source_map.get("files") if isinstance(source_map.get("files"), list) else []
    return {
        "available": True,
        "total_files": source_map.get("total_files"),
        "indexed_files": source_map.get("indexed_files"),
        "sample_paths": [
            item.get("path") if isinstance(item, dict) else str(item)
            for item in files[:20]
        ],
    }


def _rank_and_trim(pack: dict[str, Any], token_budget: int) -> dict[str, Any]:
    budget = int(token_budget or 0)
    if budget <= 0:
        return pack
    max_chars = max(1200, budget * 4)
    encoded = json.dumps(pack, ensure_ascii=False)
    if len(encoded) <= max_chars:
        return pack
    pack = dict(pack)
    pack["session"] = {
        "summary": str((pack.get("session") or {}).get("summary") or "")[:600],
        "recent_key_messages": (pack.get("session") or {}).get("recent_key_messages", [])[-2:],
    }
    pack.setdefault("debug", {})["trimmed"] = True
    return pack


def _module_guesses(paths: list[str]) -> list[str]:
    modules = []
    for path in paths:
        parts = [part for part in re.split(r"[\\/]+", path) if part]
        if len(parts) >= 2:
            modules.append("/".join(parts[:-1]))
    return _dedupe(modules)[:12]


def _memory_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "scope": row["scope"],
        "memory_type": row["memory_type"],
        "key": row["key"],
        "content": _loads(row["content_json"], {}),
        "source_type": row["source_type"],
        "source_ref": _loads(row["source_ref_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _candidate(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "candidate_id": row["id"],
        "project_id": row["project_id"],
        "task_id": row["task_id"],
        "run_id": row["run_id"],
        "target_memory_type": row["target_memory_type"],
        "content": _loads(row["content_json"], {}),
        "reason": row["reason"],
        "confidence": row["confidence"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "reviewed_at": row["reviewed_at"],
        "review_note": row["review_note"],
    }


def _validate_scope(scope: str) -> str:
    value = str(scope or "").strip().lower()
    if value not in SCOPES:
        raise ValueError(f"invalid memory scope: {scope}")
    return value


def _validate_memory_type(scope: str, memory_type: str) -> str:
    value = str(memory_type or "").strip()
    allowed = {
        "project": PROJECT_MEMORY_TYPES,
        "task": TASK_MEMORY_TYPES,
        "workflow": WORKFLOW_MEMORY_TYPES,
        "session": SESSION_MEMORY_TYPES,
        "run": RUN_MEMORY_TYPES,
        "workspace": {"source_map", "code_summary", "workspace_profile"},
    }.get(scope, set())
    if value not in allowed:
        raise ValueError(f"invalid memory type {value!r} for scope {scope!r}")
    return value


def _safe_event(task_id: str, event_type: str, payload: dict[str, Any]) -> None:
    try:
        add_event(task_id, event_type, payload)
    except Exception:
        return


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str, default: Any) -> Any:
    try:
        data = json.loads(value or "")
    except json.JSONDecodeError:
        return default
    return data if data is not None else default


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _safe_id(value: str) -> str:
    text = str(value or "").strip()
    return text or "default"


def _db_path() -> Path:
    db_path = Path(str(settings().devwerk_db_path))
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parents[2] / db_path
    return db_path


def _conn() -> sqlite3.Connection:
    return _connect(_db_path())


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

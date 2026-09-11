"""One project-scoped resolver for symbolic dependencies and successor lineage."""
from __future__ import annotations


def canonical_task(db, project_id: str, reference: str):
    if not isinstance(reference, str):
        return None
    if reference.startswith("task-plan:"):
        parts = reference.split(":", 2)
        if len(parts) != 3:
            return None
        row = db.execute("SELECT id FROM v1_tasks WHERE project_id=? AND task_plan_id=? AND proposed_task_ref=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                         (project_id, parts[1], parts[2])).fetchone()
        if not row:
            return None
        reference = row[0]
    seen = set()
    while reference:
        if reference in seen:
            raise ValueError("dependency successor lineage contains a cycle")
        seen.add(reference)
        row = db.execute("SELECT * FROM v1_tasks WHERE id=? AND project_id=?", (reference, project_id)).fetchone()
        if not row:
            return None
        task = dict(row)
        if not task.get("resolved_by_task_id"):
            return task
        reference = task["resolved_by_task_id"]
    return None


def dependency_tasks(db, project_id: str, task_id: str, *, transitive=False):
    pending = [task_id]
    seen = {task_id}
    result = []
    while pending:
        parent = pending.pop()
        references = db.execute("SELECT depends_on_task_id FROM v1_task_dependencies WHERE project_id=? AND task_id=?", (project_id, parent)).fetchall()
        for reference in references:
            task = canonical_task(db, project_id, reference[0])
            if task is None or task["id"] in seen:
                continue
            seen.add(task["id"])
            result.append(task)
            if transitive:
                pending.append(task["id"])
    return result

"""Versioned project goals and Loop bindings; revisions never rewrite old work."""
from __future__ import annotations

import json
from copy import deepcopy

from app.v1.contracts import canonicalize_contract_value, validate_contract
from app.v1.domain import WorkflowDefinition, WorkflowPlan
from app.v1.storage_support import new_id, utcnow


class ScopeConflict(ValueError):
    error_code = 'scope_revision_conflict'


class ScopeRepository:
    def __init__(self, store):
        self.store = store

    def init_schema(self):
        with self.store.tx(immediate=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS v1_requirement_revisions (
                requirement_id TEXT NOT NULL, revision INTEGER NOT NULL, objective TEXT NOT NULL,
                status TEXT NOT NULL, source_job_id TEXT, reason TEXT, created_at TEXT NOT NULL,
                PRIMARY KEY(requirement_id,revision))''')
            db.execute('''CREATE TABLE IF NOT EXISTS v1_workflow_scopes (
                workflow_revision_id TEXT PRIMARY KEY, binding_id TEXT,
                requirement_id TEXT, requirement_revision INTEGER)''')
            for table in ('v1_tasks', 'v1_task_plans'):
                self.store.schema_repository._ensure_column(db, table, 'requirement_revision', 'INTEGER')
            db.execute('''INSERT OR IGNORE INTO v1_requirement_revisions
                SELECT id,revision,objective,status,NULL,'legacy snapshot',created_at FROM v1_requirements''')
            # An exact application association is authoritative. Later workflow
            # revisions inherit the uniquely known preceding binding, never a
            # binding from the future of that revision.
            for rev in db.execute('SELECT * FROM v1_workflow_revisions ORDER BY created_at,rowid').fetchall():
                if db.execute('SELECT 1 FROM v1_workflow_scopes WHERE workflow_revision_id=?', (rev['id'],)).fetchone():
                    continue
                binding = db.execute('''SELECT id FROM v1_project_loop_bindings WHERE project_id=?
                    AND (workflow_revision_id=? OR created_at<=?)
                    ORDER BY (workflow_revision_id=?) DESC,created_at DESC,rowid DESC LIMIT 1''',
                    (rev['project_id'], rev['id'], rev['created_at'], rev['id'])).fetchone()
                req = db.execute('SELECT requirement_id FROM v1_workflow_plans WHERE id=?', (rev['workflow_plan_id'],)).fetchone()
                req_id = req[0] if req else None
                if not req_id:
                    owners = db.execute('SELECT DISTINCT requirement_id FROM v1_tasks WHERE workflow_revision_id=? AND requirement_id IS NOT NULL', (rev['id'],)).fetchall()
                    req_id = owners[0][0] if len(owners) == 1 else None
                version = db.execute('SELECT revision FROM v1_requirements WHERE id=?', (req_id,)).fetchone() if req_id else None
                db.execute('INSERT INTO v1_workflow_scopes VALUES(?,?,?,?)', (rev['id'], binding[0] if binding else None, req_id, version[0] if version else None))
            db.execute('''UPDATE v1_tasks SET requirement_revision=COALESCE(
                (SELECT requirement_revision FROM v1_agent_assignments a WHERE a.task_id=v1_tasks.id ORDER BY created_at LIMIT 1),
                (SELECT requirement_revision FROM v1_workflow_scopes s WHERE s.workflow_revision_id=v1_tasks.workflow_revision_id))
                WHERE requirement_revision IS NULL AND requirement_id IS NOT NULL''')

    def remember(self, requirement, *, job_id=None, reason=None):
        with self.store.tx(immediate=True) as db:
            db.execute('INSERT OR IGNORE INTO v1_requirement_revisions VALUES(?,?,?,?,?,?,?)',
                       (requirement['id'], requirement['revision'], requirement['objective'], requirement['status'], job_id, reason, utcnow()))

    def requirement_version(self, project_id, requirement_id, revision=None):
        current = self.store.agents.get_requirement(project_id, requirement_id)
        self.remember(current)
        if revision is None or revision == current['revision']:
            return current
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM v1_requirement_revisions WHERE requirement_id=? AND revision=?', (requirement_id, revision)).fetchone()
        if not row:
            raise ScopeConflict('The requested Requirement revision has no snapshot')
        return {**current, 'revision': row['revision'], 'objective': row['objective'], 'status': row['status']}

    def for_workflow(self, revision_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM v1_workflow_scopes WHERE workflow_revision_id=?', (revision_id,)).fetchone()
        return dict(row) if row else None

    def bind_workflow(self, revision_id, binding_id, requirement_id=None, requirement_revision=None):
        with self.store.tx(immediate=True) as db:
            db.execute('''INSERT INTO v1_workflow_scopes VALUES(?,?,?,?)
                ON CONFLICT(workflow_revision_id) DO UPDATE SET binding_id=excluded.binding_id,
                requirement_id=excluded.requirement_id,requirement_revision=excluded.requirement_revision''',
                (revision_id, binding_id, requirement_id, requirement_revision))

    def inherit(self, revision_id, previous_revision_id):
        prior = self.for_workflow(previous_revision_id) if previous_revision_id else None
        self.bind_workflow(revision_id, prior['binding_id'] if prior else None,
                           prior['requirement_id'] if prior else None, prior['requirement_revision'] if prior else None)

    def inspect(self, project_id):
        try:
            workflow = self.store.get_workflow(project_id)
        except KeyError:
            return {'workflow': None, 'requirements': self.store.agents.list_requirements(project_id)}
        scope = self.for_workflow(workflow['id']) or {}
        return {'workflow_revision_id': workflow['id'], **scope,
                'bindings': workflow['loop_bindings'], 'requirements': self.store.agents.list_requirements(project_id)}

    def revise(self, ctx, *, requirement_id, expected_revision, expected_workflow_revision_id,
               objective, binding_patch, reason, loop_digest=None):
        from app.v1.store import _resolve_loop_parameters
        self.store.agents.assert_conversation(ctx)
        if not ctx.user_initiated or not ctx.start_task:
            raise ValueError('scope_revision_requires_user_request: continue the existing scope for event turns')
        with self.store.tx(immediate=True) as db:
            if ctx.execution_control:
                ctx.execution_control.check()
            current = self.store.agents.get_requirement(ctx.project_id, requirement_id)
            active = self.store.get_workflow(ctx.project_id)
            owner = self.for_workflow(active['id'])
            if owner and owner['requirement_id'] not in (None, requirement_id):
                raise ScopeConflict('The active Workflow belongs to a different Requirement')
            if db.execute("SELECT 1 FROM v1_tasks WHERE project_id=? AND failure_code='effect_outcome_unknown'", (ctx.project_id,)).fetchone():
                raise ScopeConflict('Reconcile the unknown Task effect before revising project scope')
            if current['revision'] != expected_revision or active['id'] != expected_workflow_revision_id:
                raise ScopeConflict('Scope changed; inspect the current Requirement and Workflow revisions before revising')
            self.remember(current)
            binding = self.store.get_project_loop_binding(ctx.project_id, active['id'])
            if not binding or not binding.get('package_json'):
                raise ScopeConflict('Scope revision requires the frozen Loop package for this Workflow')
            old_package = json.loads(binding['package_json'])
            package = old_package
            if loop_digest and loop_digest != binding['loop_digest']:
                package = self.store.get_loop(binding['loop_key'])
                if package['digest'] != loop_digest:
                    raise ScopeConflict('The selected Loop digest is no longer available')
            parameters = canonicalize_contract_value({**package['bundle'].get('defaults', {}), **binding['bindings'], **binding_patch}, package['parameter_schema'])
            validate_contract(parameters, package['parameter_schema'], label='Revised scope bindings')
            old_material = _resolve_loop_parameters(old_package['bundle'], binding['bindings'])
            new_material = _resolve_loop_parameters(package['bundle'], parameters)
            old_method = WorkflowPlan.model_validate(old_material['workflow_plan']).model_dump(mode='json')
            old_workflow = WorkflowDefinition.model_validate(old_material['workflow']).model_dump(mode='json')
            new_method = WorkflowPlan.model_validate(new_material['workflow_plan']).model_dump(mode='json')
            new_workflow = WorkflowDefinition.model_validate(new_material['workflow']).model_dump(mode='json')
            current_method = self.store.get_workflow_plan(ctx.project_id, active['workflow_plan_id'])['plan']
            method = WorkflowPlan.model_validate(_apply_delta(old_method, new_method, current_method))
            workflow = WorkflowDefinition.model_validate(_apply_delta(old_workflow, new_workflow, active['definition']))
            plan = self.store.create_workflow_plan(ctx.project_id, method)
            published = self.store.publish_workflow(ctx.project_id, workflow, plan['id'])
            binding_id = new_id('loopapp')
            db.execute('''INSERT INTO v1_project_loop_bindings
                (id,project_id,loop_key,loop_version,loop_digest,bindings_json,workflow_plan_id,workflow_revision_id,created_at,package_json)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (binding_id, ctx.project_id, package['loop_key'], package['version'], package['digest'], json.dumps(parameters, ensure_ascii=False),
                 plan['id'], published['id'], utcnow(), json.dumps(package, ensure_ascii=False)))
            changed = db.execute('UPDATE v1_requirements SET revision=revision+1,objective=?,status=\'active\',updated_at=? WHERE id=? AND revision=?',
                                 (objective, utcnow(), requirement_id, expected_revision)).rowcount
            if changed != 1:
                raise ScopeConflict('Requirement revision changed during scope commit')
            revised = self.store.agents.get_requirement(ctx.project_id, requirement_id)
            self.remember(revised, job_id=self.store.agents.conversation_job(ctx)['id'], reason=reason)
            self.bind_workflow(published['id'], binding_id, requirement_id, revised['revision'])
            self.store.agents.select_requirement(ctx, requirement_id)
            result = {'requirement': revised, 'workflow_revision_id': published['id'], 'binding_id': binding_id,
                      'bindings': parameters, 'previous_workflow_revision_id': active['id'], 'task_ids': []}
            self.store._event(db, ctx.project_id, None, None, 'project.scope.revised', result)
            return result


def _apply_delta(old, new, current):
    """Preserve project customizations; never silently replace conflicting edits."""
    if old == new:
        return deepcopy(current)
    if current == old:
        return deepcopy(new)
    if isinstance(old, dict) and isinstance(new, dict) and isinstance(current, dict):
        result = deepcopy(current)
        for key in old.keys() | new.keys():
            if old.get(key) == new.get(key) and (key in old) == (key in new):
                continue
            if key not in old:
                if key in current and current[key] != new[key]:
                    raise ScopeConflict('Loop update conflicts with customized field: '+key)
                result[key] = deepcopy(new[key])
            elif key not in new:
                if current.get(key) != old[key]:
                    raise ScopeConflict('Loop update removes customized field: '+key)
                result.pop(key, None)
            else:
                result[key] = _apply_delta(old[key], new[key], current.get(key))
        return result
    if isinstance(old, list) and isinstance(new, list) and isinstance(current, list) and len(old) == len(new) == len(current):
        return [_apply_delta(a, b, c) for a, b, c in zip(old, new, current)]
    raise ScopeConflict('Loop update conflicts with a customized Workflow; revise the method explicitly first')

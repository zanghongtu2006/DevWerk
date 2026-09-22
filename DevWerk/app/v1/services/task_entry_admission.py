"""Verify declared entry evidence against server records, never model booleans."""
import hashlib
import json

from app.v1.files import ProjectFiles
from app.v1.task_identity import resolve_input_pointer


def validate_entry_evidence(store, project_id, plan, method):
    requirements = method.task_contract.entry_requirements
    if not requirements:
        return
    scope = store.scopes.for_workflow(plan.workflow_revision_id) or {}
    files = ProjectFiles(store.get_project(project_id)['base_dir'], store.policy)
    with store.connect() as db:
        for task in plan.tasks:
            grants = {}
            for rule in requirements:
                ref = task.entry_evidence.get(rule.key)
                if not ref:
                    raise ValueError(f'EntryEvidenceMissing: Task {task.proposed_task_ref} needs {rule.key}; requirements_confirmed alone is not evidence')
                if rule.kind != 'accepted_scope_snapshot':
                    continue
                grant = db.execute('SELECT * FROM v1_execution_grants WHERE id=? AND project_id=?', (ref, project_id)).fetchone()
                if not grant or grant['state'] != 'active' or (grant['requirement_id'], grant['requirement_revision']) != (scope.get('requirement_id'), scope.get('requirement_revision')):
                    raise ValueError(f'EntryEvidenceScopeMismatch: {rule.key} must reference this Workflow Requirement revision')
                grants[rule.key] = dict(grant)
            for rule in requirements:
                if rule.kind != 'input_file_snapshot':
                    continue
                grant = grants.get(rule.scope_evidence_key)
                if not grant:
                    raise ValueError('EntryEvidenceScopeMissing: file evidence requires an accepted scope snapshot')
                receipt = db.execute('SELECT * FROM v1_execution_receipts WHERE project_id=? AND execution_key=?',
                                     (project_id, task.entry_evidence[rule.key])).fetchone()
                if not receipt or receipt['status'] != 'completed' or receipt['capability'] != 'project.files.write':
                    candidates = []
                    for row in db.execute("SELECT * FROM v1_execution_receipts WHERE project_id=? AND capability='project.files.write' AND status='completed' ORDER BY started_at DESC LIMIT 20", (project_id,)):
                        decoded = store._decode(dict(row), 'arguments_json', 'result_json')
                        if decoded['arguments'].get('path') == resolve_input_pointer(task.input, rule.input_pointer):
                            candidates.append(decoded['execution_key'])
                    raise ValueError(f'EntryEvidenceFileMissing: {rule.key} requires the write execution_key, not an artifact ID. '
                        f'Available write receipts at this input path: {candidates}. Use the matching accepted-scope receipt; other checks still apply.')
                receipt = store._decode(dict(receipt), 'arguments_json', 'result_json')
                operation = db.execute('SELECT source_run_id FROM v1_agent_operations WHERE id=? AND project_id=?',
                    (receipt['execution_key'], project_id)).fetchone()
                source_run = operation['source_run_id'] if operation else receipt['execution_key'].split(':', 1)[0]
                source_job = store.intents.job_for_run(source_run, project_id)
                if not source_job or source_job.get('execution_grant_id') != grant['id']:
                    raise ValueError('EntryEvidenceFileScopeMismatch: baseline receipt must belong to this accepted scope grant')
                path = resolve_input_pointer(task.input, rule.input_pointer)
                args = receipt['arguments']
                if args.get('path') != path or receipt['started_at'] < grant['created_at']:
                    raise ValueError('EntryEvidenceFileMismatch: baseline must be written for the accepted scope at the Task input path')
                expected = hashlib.sha256(args['content'].encode('utf-8')).hexdigest()
                target = files.resolve(path)
                if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                    raise ValueError('EntryEvidenceFileChanged: requirement baseline no longer matches its immutable write receipt')

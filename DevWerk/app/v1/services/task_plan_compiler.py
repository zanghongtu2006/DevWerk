"""Shared, side-effect-free preparation for plan admission and materialization."""
from app.v1.contracts import canonicalize_contract_value, validate_contract
from app.v1.domain import ReadinessDecision, TaskPlan
from app.v1.task_identity import logical_task_key

COMPILER_VERSION = 1


class PlanAdmissionError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__('; '.join(f"{d['path']}: {d['message']}" for d in diagnostics))


def acceptance_diagnostics(definition, method, data=None):
    """Collect independent contract failures without executing future checks."""
    errors = []
    column_paths = {c.key:f'/workflow/columns/{i}' for i,c in enumerate(definition.columns)}
    def issue(path, message):
        errors.append({'path': path, 'message': message})
    for key in method.task_contract.feedback_columns:
        executor = definition.column(key).executor
        if executor.kind != 'agent' or 'task.feedback.record' not in executor.capabilities:
            issue(column_paths[key]+'/executor/capabilities', f'FeedbackContractMissing: {key} must expose task.feedback.record')
    return errors


def prepare_task_input(proposed, definition, method, registry):
    from app.v1.capabilities import validate_task_capability_bindings
    from app.v1.store import _validate_deterministic_deliverable_coverage

    data = canonicalize_contract_value(proposed.input, method.task_contract.input_schema)
    data = validate_task_capability_bindings(
        definition, registry, data,
        exact_strings={item.pointer: item.value for item in proposed.exact_input_strings},
    )
    validate_contract(data, method.task_contract.input_schema, label=f'Task {proposed.proposed_task_ref} input')
    proposed.validate_agent_execution_workflow(definition)
    errors = acceptance_diagnostics(definition, method, data)
    if errors:
        raise PlanAdmissionError(errors)
    proposed.validate_exact_input_workflow(definition)
    readiness = ReadinessDecision.model_validate({
        **proposed.readiness.model_dump(mode='json'), 'objective': proposed.objective,
        'dependencies': list(proposed.dependencies),
        'conflict_domains': [item.model_dump(mode='json') for item in proposed.conflict_domains],
    }).model_dump(mode='json')
    _validate_deterministic_deliverable_coverage(definition, readiness)
    return data, readiness, logical_task_key(method.task_contract, data)


def compile_task_plan(plan, definition, method, registry):
    normalized = TaskPlan.model_validate(plan.model_dump(mode='json'))
    identities = {}
    for proposed in normalized.tasks:
        data, _, identity = prepare_task_input(proposed, definition, method, registry)
        if identity is not None and identity in identities:
            raise ValueError(
                f'DuplicateTaskIdentity: Tasks {identities[identity]!r} and {proposed.proposed_task_ref!r} '
                f'share {identity!r}. A Task traverses every Column; combine stages of the same delivery.'
            )
        if identity is not None:
            identities[identity] = proposed.proposed_task_ref
        proposed.input = data
    return normalized


def diagnose_task_plan(plan, definition, method, registry):
    errors = []
    for index, proposed in enumerate(plan.tasks):
        try:
            prepare_task_input(proposed, definition, method, registry)
        except PlanAdmissionError as exc:
            errors.extend({**d, 'path': d['path'] if d['path'].startswith('/workflow/') else f'/tasks/{index}'+d['path']} for d in exc.diagnostics)
        except ValueError as exc:
            errors.append({'path': f'/tasks/{index}', 'message': str(exc)})
    if not errors:
        try:
            compile_task_plan(plan, definition, method, registry)
        except ValueError as exc:
            errors.append({'path': '/tasks', 'message': str(exc)})
    return {'valid': not errors, 'diagnostics': errors,
            'legal_columns': [c.key for c in definition.columns]}


def diagnose_acceptance_launches(store, project_id, definition):
    from app.v1.command_resolution import resolve_command
    from app.v1.files import ProjectFiles
    files = ProjectFiles(store.get_project(project_id)['base_dir'], store.policy)
    errors, deferred = [], []
    for index, column in enumerate(definition.columns):
        for number, check in enumerate(column.acceptance_checks):
            if check.capability != 'project.command.run':
                continue
            path = f'/workflow/columns/{index}/acceptance_checks/{number}/arguments'
            if check.launch_validation == 'deferred':
                deferred.append({'column':column.key,'check_key':check.key,'validation':'before Runtime execution'})
                continue
            try:
                cwd = files.resolve(check.arguments.get('cwd') or '.')
                if not cwd.is_dir():
                    raise ValueError('Preflight cwd does not exist; declare deferred for a future generated directory')
                resolve_command(check.arguments['argv'], cwd)
            except (KeyError,ValueError,OSError) as exc:
                errors.append({'path':path,'message':str(exc)})
    return {'diagnostics':errors,'deferred_command_checks':deferred}

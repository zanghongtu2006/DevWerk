"""Shared, side-effect-free preparation for plan admission and materialization."""
from app.v1.contracts import canonicalize_contract_value, validate_contract
from app.v1.domain import ReadinessDecision, TaskPlan
from app.v1.workflows.task_identity import logical_task_key
from app.v1.protocols.tools.planning.capability_contracts import (AcceptanceLaunchDiagnostics, DeferredCommandCheck,
                                         ValidationDiagnostic, ValidationReceipt)

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
        errors.append(ValidationDiagnostic(path=path, message=message).model_dump(mode='json'))
    for key in method.task_contract.feedback_columns:
        if key not in column_paths:
            issue('/workflow/columns', f'FeedbackContractUnknownColumn: {key} is not a Workflow Column')
            continue
        executor = definition.column(key).executor
        if executor.kind != 'agent' or 'task.feedback.record' not in executor.capabilities:
            issue(column_paths[key]+'/executor/capabilities',
                  f'FeedbackContractMissing: {key} must expose task.feedback.record. '
                  'Revise its agent executor and publish the corrected Workflow; '
                  'workflow.acceptance.configure cannot change executor capabilities.')
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
    facts = proposed.readiness
    readiness = ReadinessDecision(
        decision=facts.decision,
        objective=proposed.objective,
        scope=facts.scope,
        non_scope=facts.non_scope,
        deliverables=facts.deliverables,
        acceptance_criteria=facts.acceptance_criteria,
        dependencies_checked=facts.dependencies_checked,
        dependencies=list(proposed.dependencies),
        conflict_domains=list(proposed.conflict_domains),
        risks=facts.risks,
        reason_summary=facts.reason_summary,
        next_review_at=facts.next_review_at,
    ).model_dump(mode='json')
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
            for item in exc.diagnostics:
                diagnostic = ValidationDiagnostic.model_validate(item)
                if not diagnostic.path.startswith('/workflow/'):
                    diagnostic.path = f'/tasks/{index}{diagnostic.path}'
                errors.append(diagnostic.model_dump(mode='json'))
        except ValueError as exc:
            errors.append(ValidationDiagnostic(path=f'/tasks/{index}', message=str(exc)).model_dump(mode='json'))
    if not errors:
        try:
            compile_task_plan(plan, definition, method, registry)
        except ValueError as exc:
            errors.append(ValidationDiagnostic(path='/tasks', message=str(exc)).model_dump(mode='json'))
    return ValidationReceipt(valid=not errors, diagnostics=errors,
        legal_columns=[c.key for c in definition.columns]).model_dump(mode='json', exclude_unset=True)


def diagnose_acceptance_launches(store, project_id, definition):
    from app.v1.workspace.command_resolution import resolve_command
    from app.v1.workspace.files import ProjectFiles
    files = ProjectFiles(store.get_project(project_id)['base_dir'], store.policy)
    errors, deferred = [], []
    for index, column in enumerate(definition.columns):
        for number, check in enumerate(column.acceptance_checks):
            if check.capability != 'project.command.run':
                continue
            path = f'/workflow/columns/{index}/acceptance_checks/{number}/arguments'
            if check.launch_validation == 'deferred':
                deferred.append(DeferredCommandCheck(column=column.key, check_key=check.key,
                    validation='before Runtime execution'))
                continue
            try:
                cwd = files.resolve(check.arguments.get('cwd') or '.')
                if not cwd.is_dir():
                    raise ValueError('Preflight cwd does not exist; declare deferred for a future generated directory')
                resolve_command(check.arguments['argv'], cwd)
            except (KeyError,ValueError,OSError) as exc:
                errors.append(ValidationDiagnostic(path=path, message=str(exc)))
    return AcceptanceLaunchDiagnostics(diagnostics=errors,
        deferred_command_checks=deferred).model_dump(mode='json')

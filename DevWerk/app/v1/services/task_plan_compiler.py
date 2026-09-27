"""Shared, side-effect-free preparation for plan admission and materialization."""
from app.v1.contracts import canonicalize_contract_value, validate_contract
from app.v1.domain import ReadinessDecision, TaskPlan
from app.v1.task_identity import logical_task_key, resolve_input_pointer

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
    for column in definition.columns:
        for index, check in enumerate(column.acceptance_checks):
            if check.evidence_kind != 'behavior':
                continue
            reason = known_nonbehavior_command(check.arguments.get('argv', []))
            if reason:
                issue(column_paths[column.key]+f'/acceptance_checks/{index}/arguments', reason)
    for key in method.task_contract.feedback_columns:
        executor = definition.column(key).executor
        if executor.kind != 'agent' or 'task.feedback.record' not in executor.capabilities:
            issue(column_paths[key]+'/executor/capabilities', f'FeedbackContractMissing: {key} must expose task.feedback.record')
    covered = set()
    required = set()
    pointer = method.task_contract.scenario_input_pointer
    if pointer and data is not None:
        values = resolve_input_pointer(data, pointer)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values) or len(set(values)) != len(values):
            issue('/input'+pointer, 'ScenarioContractInvalid: expected unique nonempty scenario IDs from the accepted requirement')
        else:
            required = set(values)
    for key in method.task_contract.required_behavior_columns:
        invalidators = ({key} if key in method.task_contract.acceptance_invalidated_by
                        else set(method.task_contract.acceptance_invalidated_by))
        obligations = {(item.column,item.check_key) for item in definition.acceptance_obligations
                       if invalidators.issubset(item.invalidated_by)}
        column = definition.column(key)
        checks = [c for c in column.acceptance_checks if c.evidence_kind == 'behavior' and (key,c.key) in obligations]
        if not checks:
            issue(column_paths[key]+'/acceptance_checks', f'AcceptanceContractMissing: {key} needs a frozen behavioral command and a workflow.acceptance_obligations reference with invalidated_by={sorted(invalidators)}')
        if pointer:
            ids = set()
            for check in checks:
                if not check.report_path or not check.scenario_ids:
                    issue(column_paths[key]+'/acceptance_checks/'+str(column.acceptance_checks.index(check)), 'ScenarioContractMissing: behavior requires scenario_ids and a fresh structured report_path')
                ids.update(check.scenario_ids)
            covered.update(ids)
            if required and any(t.target == 'done' for t in column.transitions) and not required.issubset(ids):
                issue(column_paths[key]+'/acceptance_checks', 'ScenarioCoverageMissing: terminal acceptance lacks '+', '.join(sorted(required-ids)))
    if required-covered:
        issue('/workflow/acceptance_obligations', 'ScenarioCoverageMissing: '+', '.join(sorted(required-covered)))
    return errors


def known_nonbehavior_command(argv):
    """Reject a few provably non-testing commands, not arbitrary script semantics.

    Runtime still requires fresh assertions and independent review for everything
    else. This admission check never reinterprets previously frozen definitions.
    """
    if not argv or not all(isinstance(a,str) for a in argv):
        return None
    import re
    import shlex

    lowered = [a.lower() for a in argv]
    if any(a in {'-dskiptests','-dskiptests=true','-dmaven.test.skip=true','--list'} for a in lowered):
        return 'NonBehaviorCheck: tests are disabled or only listed; configure an assertion runner and a fresh report'
    command = lowered
    for _ in range(8):
        executable = command[0].strip('"').replace('\\','/').rsplit('/',1)[-1] if command else ''
        if executable not in {'cmd','cmd.exe'} or '/c' not in command:
            break
        command = command[command.index('/c')+1:]
        if len(command) == 1:
            payload = command[0].strip()
            if payload.startswith('"') and payload.endswith('"'):
                payload = payload[1:-1]
            try:
                command = shlex.split(payload, posix=False)
            except ValueError:
                return None  # Malformed shell syntax is diagnosed by execution, not guessed here.
    if command and command[0] in {'dir','ls','get-childitem','test-path'} and not any(any(c in token for c in '&|;><') for token in command):
        return 'NonBehaviorCheck: listing or checking file existence is artifact evidence, not a behavior assertion/report producer'
    if re.fullmatch(r'if\s+(?:not\s+)?exist\s+(?:"[^"]+"|[^\s&|;<>]+)\s+\(?\s*echo\s+[^&|;<>]+?\)?', ' '.join(command)):
        return 'NonBehaviorCheck: if exist/echo only checks an artifact; configure an entry script that executes assertions and writes a fresh scenario report'
    # Only built-in Maven lifecycle prefixes before test, with simple logging flags.
    # Custom plugin goals/profiles and npm scripts can run tests; do not guess their semantics.
    if command and command[0].strip('"').replace('\\','/').rsplit('/',1)[-1] in {'mvn','mvn.cmd','mvnw','mvnw.cmd'}:
        goals = [a for a in command[1:] if a not in {'-q','--quiet','-b','--batch-mode','-ntp','--no-transfer-progress'}]
        if goals and all(a in {'clean','validate','initialize','generate-sources','process-sources','generate-resources','process-resources','compile'} for a in goals):
            return 'NonBehaviorCheck: Maven stops before the test phase; configure an assertion runner that also writes the fresh scenario report'
    return None


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
            'legal_columns': [c.key for c in definition.columns],
            'required_behavior_columns': method.task_contract.required_behavior_columns}


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

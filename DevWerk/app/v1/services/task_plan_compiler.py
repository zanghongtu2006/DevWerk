"""Shared, side-effect-free preparation for plan admission and materialization."""
from app.v1.contracts import canonicalize_contract_value, validate_contract
from app.v1.domain import ReadinessDecision, TaskPlan
from app.v1.task_identity import logical_task_key

COMPILER_VERSION = 1


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
    for key in method.task_contract.feedback_columns:
        executor = definition.column(key).executor
        if executor.kind != 'agent' or 'task.feedback.record' not in executor.capabilities:
            raise ValueError(f'FeedbackContractMissing: {key} must expose task.feedback.record')
    for key in method.task_contract.required_behavior_columns:
        invalidators = ({key} if key in method.task_contract.acceptance_invalidated_by
                        else set(method.task_contract.acceptance_invalidated_by))
        obligations = {(item.column,item.check_key) for item in definition.acceptance_obligations
                       if invalidators.issubset(item.invalidated_by)}
        column = definition.column(key)
        if not any(check.evidence_kind == 'behavior' and (key,check.key) in obligations for check in column.acceptance_checks):
            raise ValueError(f'AcceptanceContractMissing: {key} needs a frozen behavioral command and a workflow.acceptance_obligations reference; publish the instantiated Workflow before saving a TaskPlan')
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

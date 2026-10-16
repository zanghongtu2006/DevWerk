"""Execute a Workflow-declared acceptance check using its registered capability."""


def run_acceptance_check(registry, context, check):
    return registry.dispatch(check['capability'], check['arguments'], context)

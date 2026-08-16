# Agent Tool Rejection Recovery V1

## Purpose

An Agent must be able to observe a tool rejection, change its plan and still complete the current Column when the rejected call produced no side effect.

## Runtime distinction

Tool results distinguish two failure meanings:

- `rejected_before_effect`: the requested operation was not executed, for example invalid arguments, a missing input file or a path outside the Column's declared writable paths. The result remains visible to the Agent and audit log, but it does not become an unresolved side effect.
- execution failure: the operation started and failed, for example a command returned a non-zero exit code. It remains unresolved until the same operation succeeds or the Workflow chooses a failure outcome.

The distinction is made by the capability boundary, not by prompts or business-specific branches.

## Completion rule

A successful Column completion must still reference all successful write, process and control evidence. Failed execution effects still block an unsupported success. A rejected-before-effect call may be abandoned after the Agent has observed it and selected a valid path forward.

## Acceptance

- An invalid file write is returned to the Agent as a visible tool result.
- The Agent can continue with a valid operation and complete the Column.
- A real failed command cannot be hidden by running a different successful command.

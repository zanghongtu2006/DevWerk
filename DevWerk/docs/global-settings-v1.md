# DevWerk V1 Global Settings

## Purpose

DevWerk reads one repository-local YAML file as the source of truth for global runtime settings:

`config/global-settings.yaml`

The file is validated at startup. Unknown fields are rejected so a setting cannot appear configured while being ignored.

## Schema

```yaml
schema_version: devwerk.global-settings.v1

workflow:
  auto_resume_previous_tasks: false
```

`workflow.auto_resume_previous_tasks` controls whether unfinished Tasks from a previous process are eligible to continue immediately after DevWerk starts.

## Web editing and persistence

The Web workbench exposes a `Settings` page backed by the same validated schema:

- `GET /v1/settings` returns the current values and the editable-field metadata.
- `POST /v1/settings` accepts the complete `devwerk.global-settings.v1` document.
- The server validates the request before atomically replacing `config/global-settings.yaml`.
- Arbitrary YAML keys are not accepted and are not rendered as editable settings.

Settings that affect startup behavior are marked `restart_required`. When DevWerk is launched through `startup.bat`, saving such a change writes one restart request, stops the current Uvicorn process, and lets the same script start it again with `venv\\Scripts\\python.exe`. The Web page waits for `/v1/health` and reloads after the service is available. Saving an unchanged value does not restart the service.

If DevWerk was launched by another process manager, the value is still saved, but the API reports that no managed restart was scheduled. That process manager remains responsible for restarting DevWerk.

## Default startup behavior

When `auto_resume_previous_tasks` is `false`:

1. Tasks that were executing, waiting, recovering, or already admitted to the execution frontier become `pending` and enter a startup pause.
2. Dependency-queued Tasks that have not reached the execution frontier remain active. They are governed by the Task Plan dependency graph and must not receive an independent startup hold.
3. Active Column Runs and Attempts become `interrupted`; their history remains queryable.
4. Pending external wait handles become `interrupted` so they cannot resume independently of the Task.
5. Workflow Revision, current Column, Task input, context, dependencies, WIP group, and scheduling decisions remain unchanged.
6. `done` and `failed` Tasks remain immutable terminal history.
7. Resuming or reopening one Task releases every startup hold in the same Task Plan. This is one Workflow-execution authorization, not a per-Task authorization.
8. After that authorization, successful dependency completion automatically admits downstream Tasks. The Workflow and Scheduler continue driving the full dependency graph without Conversation Agent or user intervention.

When `auto_resume_previous_tasks` is `true`, DevWerk retains the existing automatic Runtime startup behavior.

## V1 boundary

Global Settings configure Runtime behavior. They do not modify Loop assets or generate a different Workflow definition.

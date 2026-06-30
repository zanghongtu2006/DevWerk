# Workflow Operation

Use this skill when an agent is executing a workflow column.

## Rules

- Treat the workflow definition as the source of truth for available actions and terminal states.
- Do not move Kanban columns directly. Return a semantic `next_action` that the workflow engine can validate.
- If evidence is missing, request a capability tool instead of guessing.
- Record a compact output artifact that downstream columns can use.

# DevWerk Engineering Lessons

## 2026-06-26: Component semantics must be explicit

### Incident

The Dashboard `New Project` button was implemented as a renamed legacy
`Save Project` form action. It still required `projectId` input and silently
returned when the field was empty:

```js
if (!projectId) return;
```

This contradicted the intended product meaning of `New Project`.

### Intended Semantics

`New Project` is not a CRUD save button. It is an entrypoint into the project
conversation workbench.

Clicking it must always produce a visible action:

1. Open the Workbench conversation page.
2. Create or preserve a draft project identity if the user has not provided one.
3. Let the project conversation define and maintain the project workflow,
   Kanban columns, state machine, agents, and task dispatch behavior.
4. Support coding and non-coding projects through the same conversation model.

### Root Cause

The implementation preserved the old UI model while changing the label:

- Old model: `projectId + projectName + Save Project`.
- Required model: `New Project -> Project Conversation`.

The button label changed, but the component semantics did not. This created a
silent no-op and made the UI appear broken.

### Rule

Never rename a component without re-validating its behavior against the new
product semantics.

For every UI action:

- The label must match the action.
- The action must have visible feedback.
- Empty input must not silently cancel a primary action.
- Guard clauses must either show feedback or route the user to the correct next
  step.
- Tests must cover the semantic behavior, not only the presence of text in HTML.

### Checklist For Future Changes

Before changing or adding a UI control:

1. Define whether it is a navigation action, command action, save action, or
   state transition.
2. Identify the authoritative owner of the behavior: UI, backend route,
   workflow engine, or plugin capability.
3. Confirm whether the action can be valid with empty input.
4. If input is missing, decide explicitly between:
   - generate a draft/default value,
   - open a guided flow,
   - show a blocking validation error.
5. Add a test for the user-visible behavior.
6. Add a compatibility test if the action touches workflow/plugin protocols.

### DevWerk-Specific Principle

DevWerk is moving toward a conversation-first workflow system. Dashboard buttons
should route users into explicit workflows; they should not preserve ambiguous
CRUD behavior when the product intent is workflow orchestration.

# DevWerk Loop guides

A Loop is an optional, descriptive method guide for the Conversation Agent. It is comparable to a Skill: the Agent reads it, adapts its ideas to the current user request, then calls the ordinary Workflow and Task tools to create the actual graph and work items. Reading a Loop never creates or changes a Workflow.

## Package

Each direct child directory contains one Markdown `loop.meta` card and may contain additional UTF-8 Markdown guides under `assets/`. The card includes a title and the sections `Description`, `Publisher`, `Category`, `Tags`, `Use Case`, `Selection Guide`, `Loop Input`, `Loop Output`, `Loop Version(s)`, and `Loop Key`. Add natural-language sections for suggested stages, decisions, evidence, and handoffs as needed.

Do not include an executable Workflow, JSON Schema, Tool invocation, script, fixed artifact path, or output filename in a Loop. The guide may suggest roles and the order in which to consider them; it must leave actual Columns, transitions, context selection, Task inputs, file organization, and completion checks to the Conversation Agent and the published Workflow. Method resources explain how to do work and are not automatically injected into Column sessions.

## Use

1. Discover a relevant card with `loop.list` and read it with `loop.inspect`.
2. Discuss the current goal and adapt or discard the guide.
3. Use `workflow.plan.save` and `workflow.publish` (or `workflow.create`) to publish a Project-specific graph, then use the Task planning tools for concrete work.
4. Judge publication and completion from committed Tool receipts and Task state, not from the guide or an Agent's promise.

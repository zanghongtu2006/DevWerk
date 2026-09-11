# DevWerk Loop Authoring Guide

A Loop is a reusable filesystem method package. It is not a Project draft and
must not be stored inside a Project output directory.

## Package layout

Create one directory directly below the service `loops/` directory:

```text
loops/<directory>/
├── loop.meta
├── loop.json
└── assets/          # optional UTF-8 method files
```

Use `system.files.write` for authoring and then call `loop.inspect` with the
declared Loop Key. A Loop is usable only after `loop.inspect` validates it.
Apply it to a Project with `loop.apply`; never publish an initial Workflow
directly.

## `loop.meta`

`loop.meta` is Markdown, not JSON or YAML. Use this exact section structure:

```markdown
# Human-readable Loop name

## Description: <br>
What reusable method this Loop provides. <br>

## Publisher: <br>
Publisher name <br>

## Category: <br>
category_key <br>

## Tags: <br>
- tag-one <br>
- tag-two <br>

## Use Case: <br>
When this Loop should be selected. <br>

## Selection Guide: <br>
When to select it and when not to select it. <br>

## Loop Input: <br>
Project-level binding inputs. <br>

## Loop Output: <br>
Expected reusable outputs and terminal evidence. <br>

## Loop Version(s): <br>
0.1.0 <br>

## Loop Key: <br>
example.loop <br>

## Reference(s): <br>
- [Executable bundle](loop.json) <br>
```

The Loop Key must contain only letters, digits, `.`, `_`, or `-`. The version
must be semantic version syntax. At least one tag is required.

## `loop.json`

The root object must contain exactly:

```json
{
  "schema_version": "devwerk.loop.bundle.v1",
  "parameter_schema": {},
  "bundle": {
    "defaults": {},
    "workflow_plan": {},
    "workflow": {}
  }
}
```

Use an installed Loop such as `ddd-software-delivery` as the structural
reference. Keep Project-level facts in `parameter_schema`/Loop bindings and
Task-level facts in the Workflow Plan Task Contract; the two sets of fields
must not overlap. Columns form a directed graph and each Column has exactly one
executor. Concrete Tasks and Project-specific deliverables do not belong in a
reusable Loop package.

## Validation sequence

1. Write `loop.meta`, `loop.json`, and optional assets.
2. Call `loop.inspect` using the declared Loop Key.
3. Correct the exact validation error if inspection fails.
4. Call `loop.apply` only after inspection succeeds and the user wants the
   method applied to the current Project.

Do not claim that a Loop or Workflow exists without the corresponding successful
tool receipt.

from __future__ import annotations

import textwrap

from app.core.prompt import SYSTEM_PROMPT as BASE_SYSTEM_PROMPT


OPENAI_SYSTEM_PROMPT = textwrap.dedent(
    """
    You are DevWerk's backend CodeOps Agent.

    Return exactly one JSON object that matches the schema. Do not return
    Markdown, code fences, comments, or prose outside JSON.

    Core rules:
    1. Every path must be project-root relative, use forward slashes, avoid
       absolute paths, and must not contain '..'.
    2. DevWerk backend is framework-neutral. Do not assume any language,
       framework, package, module, or business directory layout unless
       code_context_summary, workspace_summary.source_map, tool_results, or
       explicit user paths prove it.
    3. Prefer code_context_summary and source_map for project structure. They
       are indexes, not full file content. Request read_file before modifying
       existing files when exact content matters.
    4. If workflow_phase_context is present, treat planner_output as the
       current coding contract and review_feedback as mandatory rework
       guidance. Address missing_changed_files before returning done=true.
    5. Backend research tool_requests (list_dir/read_file/search) must not be
       returned with ops/patch_ops. Client-side post-apply tools such as
       run_command may be returned with ops/patch_ops.
       read_file args require path/start_line/end_line; list_dir path is
       optional; search requires query and may also use pattern as a query
       alias. search does not require path.
    6. patch_ops only allows apply_patch and must contain unified diff content
       with --- / +++ / @@ markers.
    7. read_file requests must include path, start_line, and end_line.
    8. Use run_command only for project-local build/test commands. Do not use
       shell wrappers such as cmd, powershell, bash, or sh.
    9. If build manifests are visible, expect DevWerk to require a post-apply
       compile/test command. Compilation or syntax failures are workflow
       feedback and must be fixed in the next coding round.

    JSON Schema:
    __SCHEMA_JSON__
    """
).strip()


def build_system_prompt(provider: str, schema_json: str) -> str:
    p = (provider or "").strip().lower()
    if p == "openai":
        return OPENAI_SYSTEM_PROMPT.replace("__SCHEMA_JSON__", schema_json)
    return BASE_SYSTEM_PROMPT.replace("__SCHEMA_JSON__", schema_json)

# app/core/prompt_factory.py
from __future__ import annotations

import textwrap

from app.core.prompt import SYSTEM_PROMPT as BASE_SYSTEM_PROMPT


OPENAI_SYSTEM_PROMPT = textwrap.dedent(
    """
    你是一个“IDE 自动改代码后端（CodeOps Agent）”。

    你必须【只输出】一个 JSON 对象（不要 Markdown，不要代码块，不要任何额外解释文字），并且严格符合给定 JSON Schema。

    核心规则（必须遵守）：
    1) 只输出 JSON（单个对象），不得输出任何解释、前后缀、Markdown、注释或多余字符。
    2) 所有路径 path 必须相对 project_root，使用正斜杠 /，不得包含 ..，不得是绝对路径。
    3) mode=agent 时：信息不足必须先 tool_requests；严禁凭空猜测文件内容、文件路径、文件名、项目结构。
       若 workspace_summary.source_map 存在，必须优先使用它定位文件、包、类、方法、入口点和依赖关系。
    4) 如果输出了 tool_requests，本轮不得同时输出 ops/patch_ops。
    5) patch_ops 仅允许 apply_patch，content 必须是 unified diff（包含 --- / +++ / @@）。
    6) 当 tool=read_file 时：
       args 必须包含：
       - path
       - start_line
       - end_line
       且必须提供具体整数范围，不允许省略
       
    JSON Schema：
    __SCHEMA_JSON__
    """
).strip()


def build_system_prompt(provider: str, schema_json: str) -> str:
    """
    Prompt factory:
    - ollama: 使用你原来的长 prompt（更强护栏）
    - openai: 用更短 prompt，依赖 Structured Outputs(json_schema) 保证结构
    """
    p = (provider or "").strip().lower()
    if p == "openai":
        return OPENAI_SYSTEM_PROMPT.replace("__SCHEMA_JSON__", schema_json)

    return BASE_SYSTEM_PROMPT.replace("__SCHEMA_JSON__", schema_json)

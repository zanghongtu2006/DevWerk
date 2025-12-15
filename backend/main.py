from __future__ import annotations

import json
import os
import textwrap
from typing import List, Optional, Any, Dict

import requests as http_requests
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

app = FastAPI()

# ----- 数据结构，与插件端约定保持一致 -----

class Message(BaseModel):
    role: str
    content: str


class IdeChatRequest(BaseModel):
    mode: str
    project_root: Optional[str] = None
    messages: List[Message]


# 文件操作定义：后面插件用这个来真正改代码
class FileOp(BaseModel):
    op: str  # "create_dir" | "create_file" | "update_file" | "delete_path" ...
    path: str  # 相对项目根目录，例如 "my-app/pom.xml"
    language: Optional[str] = None
    content: Optional[str] = None  # create/update 时的完整文件内容


class IdeChatResponse(BaseModel):
    reply: str
    code_tree: Optional[str] = None  # 给人看的目录树
    ops: List[FileOp] = []  # 给插件执行的操作列表


# ----- 结构化输出 Schema（让模型只输出 JSON） -----

MODEL_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply", "code_tree", "ops"],
    "properties": {
        "reply": {"type": "string"},
        "code_tree": {"type": "string"},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["op", "path", "language", "content"],
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["create_dir", "create_file", "update_file", "delete_path"],
                    },
                    "path": {"type": "string"},
                    "language": {"type": ["string", "null"]},
                    "content": {"type": ["string", "null"]},
                },
            },
        },
    },
}


SYSTEM_PROMPT = textwrap.dedent(
    """
    你是一个“IDE 代码生成后端（CodeOps Agent）”。

    你必须【只输出】一个 JSON 对象（不要 Markdown，不要代码块，不要任何额外解释文字），并且严格符合给定 JSON Schema。
    输出对象必须包含字段：reply, code_tree, ops。

    强制规则（必须遵守）：
    1) 只输出 JSON（单个对象），不得输出任何解释、前后缀、Markdown、注释或多余字符。
    2) ops 只能包含以下操作：
       - create_dir: 创建目录（language/content 必须为 null）
       - create_file: 创建文件（如存在可覆盖）（language 为文件语言，content 为文件完整内容）
       - update_file: 更新文件（覆盖写入）（language 为文件语言，content 为文件完整内容）
       - delete_path: 删除文件或目录（language/content 必须为 null）
    3) 所有 path 必须是相对 project_root（或工作区根）的相对路径，必须使用正斜杠 /，不得包含 ..，不得是绝对路径。
    4) code_tree 必须反映最终文件树（仅文本树），使用 \\n 换行，缩进用两个空格。
    5) reply 只能是一句很短的状态说明（不解释实现细节）。

    JSON Schema：
    {schema_json}

    你只需要生成“可执行的代码与文件内容”，不需要解释。
    """
).strip()


# ----- Ollama 配置 -----
# 你本地已拉 deepseek:32b，此处默认使用它
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://82.157.232.122:12434").rstrip("/")
OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "deepseek-r1:32b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "180"))


def _to_model_messages(req: IdeChatRequest) -> List[Dict[str, str]]:
    """
    把插件的历史 messages 原样喂给模型，但加上系统 prompt（强制 JSON 输出）。
    """
    schema_json = json.dumps(MODEL_RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    sys_prompt = SYSTEM_PROMPT.format(schema_json=schema_json)

    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]

    # 兼容各种 role（统一小写）
    for m in req.messages:
        role = (m.role or "").strip().lower()
        if role not in ("system", "user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": m.content or ""})

    # 给模型额外上下文：project_root（如果有）
    if req.project_root:
        messages.append(
            {
                "role": "user",
                "content": (
                    "workspace_hint:\n"
                    f"project_root={req.project_root}\n"
                    "\n"
                    "注意：所有 ops.path 必须相对 project_root（或工作区根），使用 /，不得包含 .. 或绝对路径。\n"
                    "只输出 JSON。\n"
                ),
            }
        )

    return messages


def _call_ollama_structured(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    调用 Ollama /api/chat，要求模型输出严格符合 schema 的 JSON（通过 format）。
    """
    payload: Dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": messages,
        "format": MODEL_RESPONSE_SCHEMA,  # 结构化输出：强制 JSON schema
        "options": {
            "temperature": 0.2,
        },
    }
    resp = http_requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    content = (data.get("message") or {}).get("content")

    if isinstance(content, dict):
        obj = content
    elif isinstance(content, str):
        obj = json.loads(content)
    else:
        raise ValueError("Ollama returned invalid content type")

    _validate_model_response(obj)
    return obj


def _validate_model_response(obj: Dict[str, Any]) -> None:
    if not isinstance(obj, dict):
        raise ValueError("Response must be a JSON object")
    for k in ("reply", "code_tree", "ops"):
        if k not in obj:
            raise ValueError(f"Missing field: {k}")
    if not isinstance(obj["reply"], str):
        raise ValueError("reply must be string")
    if not isinstance(obj["code_tree"], str):
        raise ValueError("code_tree must be string")
    if not isinstance(obj["ops"], list):
        raise ValueError("ops must be array")
    for i, op in enumerate(obj["ops"]):
        if not isinstance(op, dict):
            raise ValueError(f"ops[{i}] must be object")
        for k in ("op", "path", "language", "content"):
            if k not in op:
                raise ValueError(f"ops[{i}] missing {k}")
        if op["op"] not in ("create_dir", "create_file", "update_file", "delete_path"):
            raise ValueError(f"ops[{i}].op invalid: {op['op']}")
        if not isinstance(op["path"], str) or not op["path"]:
            raise ValueError(f"ops[{i}].path invalid")
        # 简单安全约束：不允许绝对、不允许 ..
        p = op["path"]
        if p.startswith("/") or p.startswith("\\") or "://" in p:
            raise ValueError(f"ops[{i}].path must be relative: {p}")
        if ".." in p.split("/"):
            raise ValueError(f"ops[{i}].path must not contain '..': {p}")


def _coerce_to_fileops(obj_ops: List[Dict[str, Any]]) -> List[FileOp]:
    return [
        FileOp(
            op=o.get("op"),
            path=o.get("path"),
            language=o.get("language", None),
            content=o.get("content", None),
        )
        for o in obj_ops
    ]


# ----- 主接口：/v1/ide/chat（保持 API 不变） -----

@app.post("/debug/raw")
async def debug_raw(request: Request):
    body = await request.body()
    print("RAW BODY:", body)
    return {"ok": True}


@app.post("/v1/ide/chat", response_model=IdeChatResponse)
def ide_chat(req: IdeChatRequest):
    """
    新版本：
    - 保持 /v1/ide/chat 请求/响应结构不变
    - 使用 deepseek:32b（Ollama）生成结构化 JSON（reply/code_tree/ops）
    - 返回给 IDEA 插件执行 ops
    """
    # 如果 mode 不是你期望的，也可以在这里做分流；当前直接统一走大模型
    messages = _to_model_messages(req)

    try:
        obj = _call_ollama_structured(messages)
    except Exception as ex:
        # 容错：大模型不可用时，返回一个“安全”的空操作，避免插件崩
        return IdeChatResponse(
            reply=f"模型调用失败：{type(ex).__name__}",
            code_tree="",
            ops=[],
        )

    return IdeChatResponse(
        reply=obj["reply"],
        code_tree=obj.get("code_tree") or "",
        ops=_coerce_to_fileops(obj.get("ops") or []),
    )

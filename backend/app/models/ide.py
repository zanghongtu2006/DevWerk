# app/models/ide.py
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

class Message(BaseModel):
    role: str
    content: str

class IdeChatRequest(BaseModel):
    mode: str
    project_root: Optional[str] = None
    messages: List[Message]

class FileOp(BaseModel):
    op: str
    path: str
    language: Optional[str] = None
    content: Optional[str] = None

class IdeChatResponse(BaseModel):
    reply: str
    code_tree: Optional[str] = None
    ops: List[FileOp] = []

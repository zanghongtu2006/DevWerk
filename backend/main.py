from urllib.request import Request

from fastapi import FastAPI, requests
from fastapi import Request
from pydantic import BaseModel
from typing import List, Optional


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
    op: str                    # "create_dir" | "create_file" | "modify_file" | "delete_file" ...
    path: str                  # 相对项目根目录，例如 "my-app/pom.xml"
    language: Optional[str] = None
    content: Optional[str] = None     # create/modify 时的完整文件内容


class IdeChatResponse(BaseModel):
    reply: str
    code_tree: Optional[str] = None   # 给人看的目录树
    ops: List[FileOp] = []            # 给插件执行的操作列表


# ----- 一个 mock 的 Maven-Java 代码树（展示用） -----

MAVEN_JAVA_TREE = """my-app/
  pom.xml
  src/
    main/
      java/
        com/
          example/
            app/
              App.java
      resources/
    test/
      java/
        com/
          example/
            app/
              AppTest.java
"""

# ----- Ollama 调用工具函数（暂时不用，但先放着，方便以后打开） -----

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.1"   # 你可以换成自己拉的模型名

def call_ollama(messages: list[dict]) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": messages,
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    # 根据官方文档，chat 返回 message.content 字段为模型输出
    # https://docs.ollama.com/api/chat
    return data.get("message", {}).get("content", "")


# ----- 主接口：/v1/ide/chat -----

@app.post("/debug/raw")
async def debug_raw(request: Request):
    body = await request.body()
    print("RAW BODY:", body)
    return {"ok": True}

@app.post("/v1/ide/chat", response_model=IdeChatResponse)
def ide_chat(req: IdeChatRequest):
    """
    当前版本：
    - 不真正调用大模型
    - 固定返回一个 Maven-Java 项目结构的代码树
    - 同时返回一组 FileOp，描述如何在项目根目录创建这个骨架
    """
    # 这里你以后可以根据 req.messages 最后一条内容做不同分支
    # 比如：如果用户输入里包含 "maven" 就返回这个骨架，否则返回别的

    reply_text = "已为你准备一个简单的 Maven Java 项目骨架（my-app）。"

    ops: List[FileOp] = [
        # 目录结构
        FileOp(op="create_dir", path="my-app"),
        FileOp(op="create_dir", path="my-app/src"),
        FileOp(op="create_dir", path="my-app/src/main"),
        FileOp(op="create_dir", path="my-app/src/main/java"),
        FileOp(op="create_dir", path="my-app/src/main/java/com"),
        FileOp(op="create_dir", path="my-app/src/main/java/com/example"),
        FileOp(op="create_dir", path="my-app/src/main/java/com/example/app"),
        FileOp(op="create_dir", path="my-app/src/main/resources"),
        FileOp(op="create_dir", path="my-app/src/test"),
        FileOp(op="create_dir", path="my-app/src/test/java"),
        FileOp(op="create_dir", path="my-app/src/test/java/com"),
        FileOp(op="create_dir", path="my-app/src/test/java/com/example"),
        FileOp(op="create_dir", path="my-app/src/test/java/com/example/app"),

        # pom.xml
        FileOp(
            op="create_file",
            path="my-app/pom.xml",
            language="xml",
            content="""<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/maven-v4_0_0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.example</groupId>
    <artifactId>my-app</artifactId>
    <packaging>jar</packaging>
    <version>1.0-SNAPSHOT</version>
    <name>my-app</name>

    <dependencies>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
    </dependencies>
</project>
"""
        ),

        # App.java
        FileOp(
            op="create_file",
            path="my-app/src/main/java/com/example/app/App.java",
            language="java",
            content="""package com.example.app;

public class App {
    public static void main(String[] args) {
        System.out.println("Hello from my-app!");
    }
}
"""
        ),

        # AppTest.java（简单占位）
        FileOp(
            op="create_file",
            path="my-app/src/test/java/com/example/app/AppTest.java",
            language="java",
            content="""package com.example.app;

import org.junit.Test;
import static org.junit.Assert.*;

public class AppTest {

    @Test
    public void testApp() {
        assertTrue(true);
    }
}
"""
        ),
    ]

    return IdeChatResponse(
        reply=reply_text,
        code_tree=MAVEN_JAVA_TREE,
        ops=ops,
    )

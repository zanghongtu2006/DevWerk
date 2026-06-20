from __future__ import annotations

from fastapi.testclient import TestClient


MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_mcp_streamable_http_lists_and_calls_tools(monkeypatch):
    import app.main as main_module
    import app.mcp_server as mcp_server

    monkeypatch.setattr(
        mcp_server,
        "workflow_state_payload",
        lambda task_id, **_: {
            "ok": True,
            "task_id": task_id,
            "status_key": "coding",
            "ready": False,
            "done": False,
        },
    )
    started = []
    monkeypatch.setattr(
        mcp_server,
        "start_workflow_payload",
        lambda body: started.append(body)
        or {"ok": True, "task_id": "mcp-started-task", "status_key": "draft"},
    )

    app = main_module.create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        initialize = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "devwerk-test", "version": "1"},
                },
            },
        )
        tools = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        state = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "devwerk_get_workflow",
                    "arguments": {"task_id": "mcp-smoke-task"},
                },
            },
        )
        start = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "devwerk_start_workflow",
                    "arguments": {
                        "project_id": "mcp-project",
                        "request": "Fix the project",
                        "project_root": "C:/work/project",
                        "workspace": {"tree_preview": "src/main.py"},
                    },
                },
            },
        )

    assert initialize.status_code == 200
    assert initialize.history == []
    assert initialize.json()["result"]["serverInfo"]["name"] == "DevWerk"

    tool_names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert tool_names == {
        "devwerk_start_workflow",
        "devwerk_get_workflow",
        "devwerk_get_workflow_result",
        "devwerk_continue_workflow",
        "devwerk_cancel_workflow",
        "devwerk_report_apply_result",
        "devwerk_get_events",
        "devwerk_get_task",
        "devwerk_list_projects",
    }
    result = state.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["task_id"] == "mcp-smoke-task"
    assert result["structuredContent"]["status_key"] == "coding"
    assert start.json()["result"]["structuredContent"]["task_id"] == "mcp-started-task"
    assert started[0]["project_id"] == "mcp-project"
    assert started[0]["project_root"] == "C:/work/project"
    assert started[0]["messages"] == [{"role": "user", "content": "Fix the project"}]
    assert started[0]["workspace"] == {
        "root_id": "mcp-project",
        "tree_preview": "src/main.py",
    }

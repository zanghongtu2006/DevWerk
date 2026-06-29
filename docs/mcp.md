# DevWerk MCP

DevWerk exposes its service-owned workflow engine as a Streamable HTTP MCP
server. The endpoint is served by the normal DevWerk service process:

```text
http://127.0.0.1:8000/mcp
```

The MCP transport is stateless. Project, task, conversation, Kanban, artifact,
and event state remains persisted by DevWerk rather than being tied to an MCP
connection.

## Codex and VS Code configuration

Start the DevWerk service first:

```powershell
cd D:\workspace\codex\devwerk\DevWerk\DevWerk
.\startup.bat
```

Add this server in the Codex MCP Servers UI, or place the following in the
shared `~/.codex/config.toml` configuration:

```toml
[mcp_servers.devwerk]
url = "http://127.0.0.1:8000/mcp"
enabled = true
required = true
tool_timeout_sec = 1800
default_tools_approval_mode = "auto"
```

No MCP bearer token is required for the current localhost deployment. Restart
or reload the Codex client after changing the configuration. The connection
should expose nine `devwerk_*` tools.

## Workflow usage

1. Make sure the project already has a saved workflow definition.
2. Call `devwerk_start_workflow` with a stable project UUID and task request.
3. Call `devwerk_get_workflow` until the task pauses or returns a result.
4. Use `devwerk_continue_workflow` for revisions, messages,
   or client tool results.
5. Apply returned `ops` or `patch_ops` with the capability client's own file tools.
6. Call `devwerk_report_apply_result` only after the write has actually
   succeeded. Include verification output when available.
7. Use `devwerk_get_events` or `devwerk_get_task` for detailed agent and Kanban
   history.

The MCP client does not need the IDEA plugin. In this mode Codex or another
MCP-capable client owns local file access, while DevWerk owns workflow state,
dynamic column-agent execution, persistence, and observability.

## Available tools

- `devwerk_start_workflow`
- `devwerk_get_workflow`
- `devwerk_get_workflow_result`
- `devwerk_continue_workflow`
- `devwerk_cancel_workflow`
- `devwerk_report_apply_result`
- `devwerk_get_events`
- `devwerk_get_task`
- `devwerk_list_projects`

The current endpoint is intended for localhost use. Add authenticated MCP
transport before exposing it to a network.

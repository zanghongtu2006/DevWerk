# Network Access

Use this skill when a workflow node needs external documentation, current package information, examples from official sources, or remote endpoint checks.

## Capabilities

- Prefer provider capabilities such as `network.http`, `network.web`, or equivalent MCP/web tools.
- Use primary sources when technical correctness matters.
- Capture URLs, response status, and concise findings in the phase output.

## Rules

- Do not invent current facts when a network capability is available.
- Keep fetched context compact and relevant to the task.
- If network access is unavailable but required, return `decision: "need_client_tool"` with the requested network capability.

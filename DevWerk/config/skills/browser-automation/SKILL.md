# Browser Automation

Use this skill when a workflow node needs to observe or operate a browser, inspect a web UI, verify a rendered page, collect console/network evidence, or run end-to-end checks.

## Capabilities

- Prefer capability tools exposed by the connected client or runtime: `browser.cdp`, `browser.playwright`, or compatible browser automation capabilities.
- CDP is suitable for browser inspection, console logs, DOM state, network traces, screenshots, and debugging an already-open browser target.
- Playwright is suitable for deterministic page navigation, interaction, assertions, screenshots, and repeatable smoke tests.
- If the capability provider advertises both CDP and Playwright, choose the least invasive tool that provides the evidence needed by the current workflow column.

## Rules

- Do not assume the provider is IntelliJ, VS Code, CI, or a local browser. Request capabilities by contract name and let the provider implement them.
- Ask for browser evidence before making UI claims.
- Return `decision: "need_client_tool"` with concrete `tool_requests` when browser access is required.
- Summarize observed evidence in the column output so downstream agents can reuse it without replaying every browser action.

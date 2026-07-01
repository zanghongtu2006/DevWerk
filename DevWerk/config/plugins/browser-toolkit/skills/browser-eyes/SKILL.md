# Browser Eyes

Use this skill when a workflow task needs direct browser evidence.

Available capabilities:

- `browser.cdp`: inspect console logs, network events, DOM state, storage, and browser runtime evidence.
- `browser.playwright`: open pages, click, type, wait, screenshot, inspect locators, and verify UI behavior.

Rules:

- Ask for browser evidence before making visual or interaction claims.
- Prefer screenshots and console/network diagnostics when debugging UI regressions.
- Return tool requests instead of pretending browser state is known.
- Record the observed evidence in the phase output summary.

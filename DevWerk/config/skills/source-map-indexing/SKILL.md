# Source Map Indexing

Use this skill when a context node needs to summarize project structure from a source map, file tree, diagnostics, or capability-provided workspace evidence.

## Rules

- Source maps describe the workspace; they do not replace file reads or diagnostics when exact code is needed.
- Preserve language-agnostic behavior. Do not assume Java, IntelliJ, Maven, or a fixed source layout.
- Emit a compact context bundle with representative paths, framework signals, diagnostics counts, and unresolved evidence gaps.

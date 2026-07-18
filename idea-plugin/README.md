# DevWerk IntelliJ Plugin

The DevWerk IntelliJ plugin is a capability provider and project-side safety
boundary for the DevWerk service. It contributes workspace evidence and
semantic tools, sends workflow requests to the service, and applies approved
file operations inside an IntelliJ project.

It is not the owner of Kanban state or workflow design. Those responsibilities
belong to the FastAPI service in `../DevWerk/`.

## Current Status

- Plugin version: `0.0.1`
- IntelliJ Platform baseline: `2024.1` (`sinceBuild=241`, `untilBuild=243.*`)
- JVM target: Java 17
- Primary UI: right-side **DevWerk** tool window
- Development state: paused while the standalone DevWerk Version 1 service is
  completed; plugin work resumes after the service can complete tasks independently

The service no longer documents legacy `/v1/plan` and `/v1/execute` endpoints.
New integration work should use `/v1/workflows`, workflow events/messages, and
semantic actions.

## Responsibilities

- collect project tree, source map, open files, changed files, and diagnostics
- provide workspace list/read/search operations
- provide compile, process, and IDE evidence where supported
- apply structured file operations inside the guarded project root
- capture before/after snapshots around source mutations
- display DevWerk workflow interaction in an IntelliJ tool window

## Safety Model

`SnapshotGuard` captures mutation targets before an operation and verifies the
snapshot set before apply. After successful execution, the plugin captures the
resulting state. Path guards prevent operations from escaping the project root.

Generated changes must still be reviewed. A successful HTTP response does not
replace checking the applied paths, IDE diagnostics, and workflow artifacts.

## Build

```powershell
cd idea-plugin
.\gradlew.bat compileKotlin
```

Useful development tasks:

```powershell
.\gradlew.bat test
.\gradlew.bat runIde
.\gradlew.bat buildPlugin
```

Build output is written under `build/`.

## Project Layout

```text
src/main/kotlin/com/zanghongtu/devwerk/
  DevWerkFsToolWindowPanel.kt   tool-window UI
  DevwerkOperationRunner.kt    guarded operation execution
  SnapshotGuard.kt             before/after snapshot protection
  codeEditor/HttpAiClient.kt   service client and workflow polling
  codeEditor/SourceMapBuilder.kt
  codeEditor/WorkspaceTools.kt
  settings/                    local provider settings
src/main/resources/META-INF/plugin.xml
```

## Configuration and Privacy

Provider URLs, models, and tokens are stored in local IDE settings. Do not
commit credentials or include them in bug reports. Workspace contents leave the
IDE only through the provider/service configuration selected by the user.

## License

GNU LGPL 2.1, matching the repository-level license.

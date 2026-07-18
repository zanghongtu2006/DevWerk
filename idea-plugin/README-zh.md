# DevWerk IntelliJ 插件

DevWerk IntelliJ 插件是 DevWerk 服务的能力提供者，也是项目侧的安全边界。它负责收集工作区证据、提供语义工具、向服务发起工作流请求，并在 IntelliJ 项目中应用已经批准的文件操作。

Kanban 状态、工作流设计和任务运行由 `../DevWerk/` 下的 FastAPI 服务负责，不由插件维护。

## 当前状态

- 插件版本：`0.0.1`
- IntelliJ Platform 基线：`2024.1`（`sinceBuild=241`，`untilBuild=243.*`）
- JVM：Java 17
- 主要入口：IDE 右侧 **DevWerk** 工具窗口
- 开发阶段：暂时挂起；DevWerk Version 1 服务能够独立完成任务后再恢复插件开发

服务端已不再把旧 `/v1/plan`、`/v1/execute` 作为正式接口。后续集成应使用 `/v1/workflows`、工作流事件/消息和语义 action。

## 主要职责

- 收集项目树、source map、打开文件、变更文件和诊断信息
- 提供 workspace list/read/search 能力
- 在支持时提供编译、进程和 IDE 诊断能力
- 在受保护的项目根目录内执行结构化文件操作
- 对源码修改保存 before/after 快照
- 在 IntelliJ 工具窗口中展示 DevWerk 交互

## 安全模型

`SnapshotGuard` 在修改前保存目标文件快照，并在 apply 前检查快照完整性；执行成功后再保存修改后的状态。路径守卫用于阻止操作逃逸出项目根目录。

生成结果仍需要人工检查。HTTP 请求成功不等于代码交付成功，还应检查实际修改路径、IDE 诊断和工作流工件。

## 构建

```powershell
cd idea-plugin
.\gradlew.bat compileKotlin
```

常用开发任务：

```powershell
.\gradlew.bat test
.\gradlew.bat runIde
.\gradlew.bat buildPlugin
```

构建产物位于 `build/`。

## 目录结构

```text
src/main/kotlin/com/zanghongtu/devwerk/
  DevWerkFsToolWindowPanel.kt   工具窗口 UI
  DevwerkOperationRunner.kt    受保护的操作执行
  SnapshotGuard.kt             修改前后快照
  codeEditor/HttpAiClient.kt   服务客户端与工作流轮询
  codeEditor/SourceMapBuilder.kt
  codeEditor/WorkspaceTools.kt
  settings/                    本地 provider 设置
src/main/resources/META-INF/plugin.xml
```

## 配置与隐私

Provider URL、模型和 token 保存在本地 IDE 设置中。不要提交凭据，也不要在问题报告中粘贴密钥。工作区内容是否离开 IDE，取决于用户选择的服务与 provider 配置。

## 许可证

GNU LGPL 2.1，与仓库根目录许可证一致。

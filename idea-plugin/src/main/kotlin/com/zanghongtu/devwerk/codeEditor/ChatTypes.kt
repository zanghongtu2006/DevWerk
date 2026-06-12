// =============================================================================
// ChatTypes.kt — shared types for DevWerk frontend and backend communication
// =============================================================================

package com.zanghongtu.devwerk.codeEditor

import com.intellij.openapi.project.Project

/**
 * 单条对话消息
 */
data class ChatMessage(
    val role: String,   // "user" / "assistant" / "system"
    val content: String
)

/**
 * 发送给 AI 的上下文（projectRoot + 历史对话）
 */
data class ChatContext(
    val projectRoot: String?,
    val history: List<ChatMessage>,
    val projectId: String? = null,
    // IntelliJ Project, used for zero-token local workspace/source-map indexing.
    val project: Project? = null,
    // DevWerk 上下文（记录请求/响应/执行）
    val devCtx: DevwerkContext? = null
)

/**
 * agent 模式：工作区摘要（可选，但建议带）
 */
data class WorkspaceFile(
    val path: String,
    val sha1: String? = null,
    val size: Int? = null
)

data class WorkspaceSummary(
    val rootId: String? = null,
    val changedFiles: List<WorkspaceFile> = emptyList(),
    val openFiles: List<String> = emptyList(),
    val treePreview: String? = null,
    val sourceMap: SourceMap? = null
)

data class SourceMap(
    val root: String,
    val generatedAt: Long,
    val totalFiles: Int,
    val indexedFiles: Int,
    val skippedFiles: Int,
    val files: List<SourceMapFile>
)

data class SourceMapFile(
    val path: String,
    val kind: String,
    val language: String? = null,
    val packageName: String? = null,
    val imports: List<String> = emptyList(),
    val symbols: List<SourceMapSymbol> = emptyList(),
    val size: Long = 0L
)

data class SourceMapSymbol(
    val name: String,
    val kind: String,
    val signature: String? = null,
    val line: Int? = null
)

/**
 * agent 模式：工具请求/工具结果
 */
data class ToolRequest(
    val id: String,
    val tool: String, // list_dir | read_file | search
    val args: Map<String, Any?> = emptyMap()
)

data class ToolResult(
    val id: String,
    val ok: Boolean,
    val content: String? = null,
    val error: String? = null
)

data class UploadedAttachment(
    val id: String,
    val filename: String,
    val contentType: String? = null,
    val size: Long = 0L,
    val localPath: String
)

/**
 * agent 模式：patch 操作
 */
data class PatchOp(
    val op: String,     // apply_patch
    val content: String  // unified diff
)

/**
 * scaffold 旧模式 / 兼容模式：文件 CRUD
 */
data class FileOp(
    val op: String,
    val path: String,
    val language: String? = null,
    val content: String? = null,
)

/**
 * 后端返回的整体响应（兼容 scaffold + agent）
 */
data class IdeChatResponse(
    val reply: String,
    val codeTree: String? = null,
    val ops: List<FileOp> = emptyList(),
    val toolRequests: List<ToolRequest> = emptyList(),
    val patchOps: List<PatchOp> = emptyList(),
    val done: Boolean = false,
    val ok: Boolean = true,
    val errorCode: String? = null,
    val errorMessage: String? = null,
    val retryable: Boolean = false,
    val rawResponses: List<String> = emptyList()
)

data class DevwerkContext(
    val projectRoot: java.nio.file.Path,
    val devwerkDir: java.nio.file.Path,
    val opDir: java.nio.file.Path,
    val opLog: java.nio.file.Path
)

// =============================================================================
// Plan types — returned by POST /v1/ide/plan
// =============================================================================

/**
 * One file in the planner's proposed change list.
 */
data class PlanFile(
    val path: String,
    val nature: String,   // "new" | "modified" | "deleted"
    val description: String,
    val confidence: Double = 0.8
)

/**
 * Response from POST /v1/ide/plan.
 */
data class PlanResponse(
    val ok: Boolean = true,
    val files: List<PlanFile> = emptyList(),
    val summary: String = "",
    val warnings: List<String> = emptyList(),
    val errorCode: String? = null,
    val errorMessage: String? = null
)

/**
 * Request body for POST /v1/ide/execute.
 */
data class ExecuteRequest(
    val messages: List<ChatMessage>,
    val projectRoot: String?,
    val mode: String = "agent",
    val approvedPaths: List<String> = emptyList(),
    val approvedOps: List<FileOp> = emptyList()
)

// =============================================================================
// AI client interface
// =============================================================================

interface AiClient {
    fun sendChat(
        context: ChatContext,
        userMessage: String
    ): IdeChatResponse
}

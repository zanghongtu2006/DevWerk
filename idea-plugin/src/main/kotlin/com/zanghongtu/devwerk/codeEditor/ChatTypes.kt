package com.zanghongtu.devwerk.codeEditor

import com.intellij.openapi.project.Project

data class ChatMessage(
    val role: String,
    val content: String
)

data class ChatContext(
    val projectRoot: String?,
    val history: List<ChatMessage>,
    val projectId: String? = null,
    val taskId: String? = null,
    val workflowAction: String? = null,
    val project: Project? = null,
    val devCtx: DevwerkContext? = null
)

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
    val sourceMap: SourceMap? = null,
    val syntaxDiagnostics: List<SyntaxDiagnostic> = emptyList()
)

data class SyntaxDiagnostic(
    val path: String,
    val line: Int? = null,
    val column: Int? = null,
    val severity: String = "error",
    val message: String,
    val source: String = "ide"
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

data class ToolRequest(
    val id: String,
    val tool: String,
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

data class PatchOp(
    val op: String,
    val content: String
)

data class FileOp(
    val op: String,
    val path: String,
    val language: String? = null,
    val content: String? = null
)

data class IdeChatResponse(
    val reply: String,
    val taskId: String? = null,
    val statusKey: String? = null,
    val codeTree: String? = null,
    val ops: List<FileOp> = emptyList(),
    val toolRequests: List<ToolRequest> = emptyList(),
    val patchOps: List<PatchOp> = emptyList(),
    val done: Boolean = false,
    val ok: Boolean = true,
    val errorCode: String? = null,
    val errorMessage: String? = null,
    val retryable: Boolean = false,
    val waitingFor: String? = null,
    val interaction: Map<String, Any?> = emptyMap(),
    val rawResponses: List<String> = emptyList()
)

data class DevwerkContext(
    val projectRoot: java.nio.file.Path,
    val devwerkDir: java.nio.file.Path,
    val opDir: java.nio.file.Path,
    val opLog: java.nio.file.Path
)

interface AiClient {
    fun sendWorkflow(
        context: ChatContext,
        userMessage: String
    ): IdeChatResponse
}

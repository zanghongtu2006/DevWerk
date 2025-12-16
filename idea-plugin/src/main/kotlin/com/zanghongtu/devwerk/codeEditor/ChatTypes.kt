package com.zanghongtu.devwerk.codeEditor

/**
 * 单条对话消息
 */
data class ChatMessage(
    val role: String,   // "user" / "assistant" / "system"
    val content: String
)

/**
 * 发送给 AI 的上下文（当前只用到 projectRoot + 历史对话）
 */
data class ChatContext(
    val projectRoot: String?,
    val history: List<ChatMessage>
)

/**
 * Web server 返回的单条文件操作
 *
 * op:
 *   - create_dir
 *   - delete_dir
 *   - create_file
 *   - modify_file
 *   - delete_file
 *
 * path: 相对项目根目录的路径，比如 "my-app/src/main/java/com/example/app/App.java"
 */
data class FileOp(
    val op: String,
    val path: String,
    val language: String? = null,
    val content: String? = null,
)

/**
 * Web server 返回的整体响应
 */
data class IdeChatResponse(
    val reply: String,
    val codeTree: String?,
    val ops: List<FileOp>
)

/**
 * AI 客户端接口（目前只有一个 Http 实现）
 */
interface AiClient {
    fun sendChat(
        context: ChatContext,
        userMessage: String
    ): IdeChatResponse
}

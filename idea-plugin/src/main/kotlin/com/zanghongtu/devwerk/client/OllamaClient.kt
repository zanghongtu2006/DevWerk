package com.zanghongtu.devwerk.client

import com.zanghongtu.devwerk.codeEditor.AiClient
import com.zanghongtu.devwerk.codeEditor.ChatContext
import com.zanghongtu.devwerk.codeEditor.IdeChatResponse
import com.zanghongtu.devwerk.prompt.CodeOpsParser
import com.zanghongtu.devwerk.prompt.PromptTemplates
import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

class OllamaClient(
    private val baseUrl: String,
    private val model: String
) : AiClient {

    private val http = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(300))
        .build()

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        val url = baseUrl.trimEnd('/') + "/api/chat"

        val msgs = JSONArray()

        // system prompt（只加一次：每次请求都放在最前面）
        msgs.put(JSONObject().put("role", "system").put("content", PromptTemplates.codeOpsSystemPrompt()))

        // history
        for (m in context.history) {
            msgs.put(JSONObject().put("role", m.role).put("content", m.content))
        }
        // current user
        msgs.put(JSONObject().put("role", "user").put("content", userMessage))

        val body = JSONObject()
            .put("model", model)
            .put("stream", false)
            .put("messages", msgs)

        val req = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(Duration.ofSeconds(300))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(body.toString()))
            .build()

        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        if (resp.statusCode() !in 200..299) {
            throw RuntimeException("Ollama chat failed: HTTP ${resp.statusCode()} - ${resp.body()}")
        }

        val json = JSONObject(resp.body())
        val raw = json.optJSONObject("message")?.optString("content").orEmpty()

        // 解析为 CodeOps JSON：reply/code_tree/ops
        return CodeOpsParser.parseToIdeChatResponse(raw)
    }
}

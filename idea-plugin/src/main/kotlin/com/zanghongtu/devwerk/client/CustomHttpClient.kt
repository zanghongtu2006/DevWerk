package com.zanghongtu.devwerk.client

import com.zanghongtu.devwerk.codeEditor.AiClient
import com.zanghongtu.devwerk.codeEditor.ChatContext
import com.zanghongtu.devwerk.codeEditor.IdeChatResponse
import org.json.JSONArray
import org.json.JSONObject
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

class CustomHttpClient(
    private val endpoint: String,
    private val token: String,
    private val model: String
) : AiClient {

    private val http = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(10))
        .build()

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        if (endpoint.isBlank()) {
            throw RuntimeException("Custom endpoint is empty. Please set URL in Settings.")
        }

        val msgs = JSONArray()
        for (m in context.history) {
            msgs.put(JSONObject().put("role", m.role).put("content", m.content))
        }
        msgs.put(JSONObject().put("role", "user").put("content", userMessage))

        val body = JSONObject()
            .put("mode", "ide-chat")
            .put("project_root", context.projectRoot ?: "")
            .put("model", model)
            .put("messages", msgs)

        val builder = HttpRequest.newBuilder()
            .uri(URI.create(endpoint))
            .timeout(Duration.ofSeconds(120))
            .header("Content-Type", "application/json")

        if (token.isNotBlank()) {
            builder.header("Authorization", "Bearer $token")
        }

        val req = builder.POST(HttpRequest.BodyPublishers.ofString(body.toString())).build()

        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        if (resp.statusCode() !in 200..299) {
            throw RuntimeException("Custom endpoint failed: HTTP ${resp.statusCode()} - ${resp.body()}")
        }

        val json = JSONObject(resp.body())
        val reply = json.optString("reply", "")

        return IdeChatResponse(
            reply = reply,
            codeTree = json.optString("code_tree", null),
            ops = emptyList() // 后续可从 json 解析 ops
        )
    }
}

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

class OllamaClient(
    private val baseUrl: String,
    private val model: String
) : AiClient {

    private val http = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(10))
        .build()

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        val url = baseUrl.trimEnd('/') + "/api/chat"

        val msgs = JSONArray()
        for (m in context.history) {
            val o = JSONObject()
            o.put("role", m.role)
            o.put("content", m.content)
            msgs.put(o)
        }
        // 加上本次 userMessage（确保一致）
        msgs.put(JSONObject().put("role", "user").put("content", userMessage))

        val body = JSONObject()
            .put("model", model)
            .put("stream", false)
            .put("messages", msgs)

        val req = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(Duration.ofSeconds(120))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(body.toString()))
            .build()

        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        if (resp.statusCode() !in 200..299) {
            throw RuntimeException("Ollama chat failed: HTTP ${resp.statusCode()} - ${resp.body()}")
        }

        val json = JSONObject(resp.body())
        val reply = json.optJSONObject("message")?.optString("content").orEmpty()

        // 直连先不做 ops/codeTree
        return IdeChatResponse(
            reply = reply,
            codeTree = null,
            ops = emptyList()
        )
    }
}

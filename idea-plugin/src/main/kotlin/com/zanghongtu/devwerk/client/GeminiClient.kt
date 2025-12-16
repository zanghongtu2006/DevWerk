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

class GeminiClient(
    private val apiKey: String,
    private val model: String
) : AiClient {

    private val http = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(10))
        .build()

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        if (apiKey.isBlank()) {
            throw RuntimeException("Gemini API key is empty. Please set Token in Settings.")
        }

        // Google AI Studio: v1beta/models/{model}:generateContent?key=...
        val url = "https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent?key=$apiKey"

        // Gemini 的内容结构：contents: [{role, parts:[{text}]}]
        val contents = JSONArray()

        // 简化：把历史都塞成 user/assistant 的 text（能跑通即可）
        for (m in context.history) {
            val role = if (m.role == "assistant") "model" else "user"
            val parts = JSONArray().put(JSONObject().put("text", m.content))
            contents.put(JSONObject().put("role", role).put("parts", parts))
        }
        contents.put(
            JSONObject()
                .put("role", "user")
                .put("parts", JSONArray().put(JSONObject().put("text", userMessage)))
        )

        val body = JSONObject().put("contents", contents)

        val req = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(Duration.ofSeconds(120))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(body.toString()))
            .build()

        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        if (resp.statusCode() !in 200..299) {
            throw RuntimeException("Gemini generateContent failed: HTTP ${resp.statusCode()} - ${resp.body()}")
        }

        val json = JSONObject(resp.body())
        val reply = json
            .optJSONArray("candidates")
            ?.optJSONObject(0)
            ?.optJSONObject("content")
            ?.optJSONArray("parts")
            ?.optJSONObject(0)
            ?.optString("text")
            .orEmpty()

        return IdeChatResponse(
            reply = reply,
            codeTree = null,
            ops = emptyList()
        )
    }
}

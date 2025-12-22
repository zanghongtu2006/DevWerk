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

class DeepseekClient(
    private val apiKey: String,
    private val model: String
) : AiClient {

    private val http = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(300))
        .build()

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        if (apiKey.isBlank()) {
            throw RuntimeException("Deepseek API key is empty. Please set Token in Settings.")
        }

        val url = "https://api.deepseek.com/v1/chat/completions"

        val messages = JSONArray()

        // system
        messages.put(JSONObject().put("role", "system").put("content", PromptTemplates.codeOpsSystemPrompt()))

        // history
        for (m in context.history) {
            messages.put(JSONObject().put("role", m.role).put("content", m.content))
        }
        messages.put(JSONObject().put("role", "user").put("content", userMessage))

        val body = JSONObject()
            .put("model", model)
            .put("messages", messages)

        val req = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(Duration.ofSeconds(300))
            .header("Authorization", "Bearer $apiKey")
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(body.toString()))
            .build()

        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        if (resp.statusCode() !in 200..299) {
            throw RuntimeException("Deepseek chat failed: HTTP ${resp.statusCode()} - ${resp.body()}")
        }

        val json = JSONObject(resp.body())
        val raw = json
            .optJSONArray("choices")
            ?.optJSONObject(0)
            ?.optJSONObject("message")
            ?.optString("content")
            .orEmpty()

        return CodeOpsParser.parseToIdeChatResponse(raw)
    }
}

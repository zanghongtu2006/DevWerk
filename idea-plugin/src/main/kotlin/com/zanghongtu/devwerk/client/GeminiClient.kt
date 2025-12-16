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

class GeminiClient(
    private val apiKey: String,
    private val model: String
) : AiClient {

    private val http = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(300))
        .build()

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        if (apiKey.isBlank()) {
            throw RuntimeException("Gemini API key is empty. Please set Token in Settings.")
        }

        val url = "https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent?key=$apiKey"

        val contents = JSONArray()

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

        val sysText = PromptTemplates.codeOpsSystemPrompt()
        val sysContent = JSONObject()
            .put("role", "user") // Content 结构需要 parts，这里按文档 Content 结构组织
            .put("parts", JSONArray().put(JSONObject().put("text", sysText)))

        val body = JSONObject()
            .put("contents", contents)
            // ✅ 两种字段都带，提升兼容性
            .put("systemInstruction", sysContent)
            .put("system_instruction", JSONObject().put("parts", JSONObject().put("text", sysText)))

        val req = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(Duration.ofSeconds(300))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(body.toString()))
            .build()

        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        if (resp.statusCode() !in 200..299) {
            throw RuntimeException("Gemini generateContent failed: HTTP ${resp.statusCode()} - ${resp.body()}")
        }

        val json = JSONObject(resp.body())
        val raw = json
            .optJSONArray("candidates")
            ?.optJSONObject(0)
            ?.optJSONObject("content")
            ?.optJSONArray("parts")
            ?.optJSONObject(0)
            ?.optString("text")
            .orEmpty()

        return CodeOpsParser.parseToIdeChatResponse(raw)
    }
}

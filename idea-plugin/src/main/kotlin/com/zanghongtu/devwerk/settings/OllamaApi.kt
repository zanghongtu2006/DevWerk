package com.zanghongtu.devwerk.settings

import org.json.JSONObject
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse

object OllamaApi {

    /**
     * 从 Ollama 拉取本地模型列表：
     * GET {baseUrl}/api/tags
     * baseUrl 通常是 http://localhost:11434
     */
    fun listModels(baseUrl: String): List<String> {
        val url = baseUrl.trimEnd('/') + "/api/tags"
        val client = HttpClient.newBuilder().build()
        val req = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .GET()
            .build()

        val resp = client.send(req, HttpResponse.BodyHandlers.ofString())
        if (resp.statusCode() !in 200..299) {
            throw RuntimeException("Ollama /api/tags failed: HTTP ${resp.statusCode()} - ${resp.body()}")
        }

        val json = JSONObject(resp.body())
        val models = json.optJSONArray("models") ?: return emptyList()
        val out = mutableListOf<String>()
        for (i in 0 until models.length()) {
            val m = models.optJSONObject(i) ?: continue
            val name = m.optString("name", "").trim()
            if (name.isNotEmpty()) out += name
        }
        return out
    }
}

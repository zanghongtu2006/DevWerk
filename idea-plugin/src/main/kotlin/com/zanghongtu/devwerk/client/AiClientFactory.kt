package com.zanghongtu.devwerk

import com.intellij.openapi.project.Project
import com.zanghongtu.devwerk.client.*
import com.zanghongtu.devwerk.codeEditor.AiClient
import com.zanghongtu.devwerk.codeEditor.HttpAiClient

object AiClientFactory {

    private const val DEFAULT_BASE = "http://127.0.0.1:8000"

    fun create(project: Project?): AiClient {
        val profile = AiSettingsService.instance().getActiveProfile()
        val provider = AiProvider.valueOf(profile.provider)

        return when (provider) {
            AiProvider.TECH_ZUKUNFT -> {
                // DevWerk backend — full client with plan/execute support
                val base = profile.baseUrl.ifBlank { DEFAULT_BASE }.trimEnd('/')
                HttpAiClient(
                    chatEndpoint = "$base/v1/ide/chat",
                    planEndpoint = "$base/v1/ide/plan",
                    executeEndpoint = "$base/v1/ide/execute",
                    authToken = profile.token.ifBlank { null }
                )
            }
            AiProvider.GPT -> OpenAiClient(apiKey = profile.token, model = profile.model.ifBlank { "gpt-4o-mini" })
            AiProvider.GEMINI -> GeminiClient(apiKey = profile.token, model = profile.model.ifBlank { "gemini-1.5-pro" })
            AiProvider.OLLAMA -> OllamaClient(baseUrl = profile.baseUrl.ifBlank { "http://localhost:11434" }, model = profile.model.ifBlank { "llama3.1" })
            AiProvider.CUSTOM -> CustomHttpClient(endpoint = profile.baseUrl, token = profile.token, model = profile.model)
            AiProvider.DEEPSEEK -> DeepseekClient(apiKey = profile.token, model = profile.model.ifBlank { "deepseek-v3.2" })
            AiProvider.QWEN -> QWenClient(apiKey = profile.token, model = profile.model.ifBlank { "qwen3-coder" })
        }
    }
}

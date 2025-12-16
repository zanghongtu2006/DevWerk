package com.zanghongtu.devwerk

import com.intellij.openapi.project.Project
import com.zanghongtu.devwerk.client.CustomHttpClient
import com.zanghongtu.devwerk.client.OpenAiClient
import com.zanghongtu.devwerk.client.OllamaClient
import com.zanghongtu.devwerk.client.GeminiClient
import com.zanghongtu.devwerk.client.TechZukunftClient
import com.zanghongtu.devwerk.codeEditor.AiClient

object AiClientFactory {

    fun create(project: Project?): AiClient {
        val profile = AiSettingsService.instance().getActiveProfile()
        val provider = AiProvider.valueOf(profile.provider)

        return when (provider) {
            AiProvider.TECH_ZUKUNFT -> {
                // 只有这个走你的 server
                TechZukunftClient(
                    endpoint = profile.baseUrl.ifBlank { "https://api.techzukunft.ai/v1/ide/chat" },
                    authToken = profile.token.ifBlank { null }
                )
            }
            AiProvider.GPT -> OpenAiClient(apiKey = profile.token, model = profile.model.ifBlank { "gpt-4o-mini" })
            AiProvider.GEMINI -> GeminiClient(apiKey = profile.token, model = profile.model.ifBlank { "gemini-1.5-pro" })
            AiProvider.OLLAMA -> OllamaClient(baseUrl = profile.baseUrl.ifBlank { "http://localhost:11434" }, model = profile.model.ifBlank { "llama3.1" })
            AiProvider.CUSTOM -> CustomHttpClient(endpoint = profile.baseUrl, token = profile.token, model = profile.model)
        }
    }
}

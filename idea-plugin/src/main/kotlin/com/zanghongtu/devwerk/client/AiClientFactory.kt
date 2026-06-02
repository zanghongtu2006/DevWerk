package com.zanghongtu.devwerk

import com.intellij.openapi.project.Project
import com.zanghongtu.devwerk.codeEditor.AiClient
import com.zanghongtu.devwerk.codeEditor.HttpAiClient

object AiClientFactory {

    private const val DEFAULT_BASE = "http://127.0.0.1:8000"

    fun create(project: Project?): AiClient {
        val profile = AiSettingsService.instance().getActiveProfile()
        val base = normalizeBackendBase(profile.baseUrl.ifBlank { DEFAULT_BASE })

        return HttpAiClient(
            chatEndpoint = "$base/v1/ide/chat",
            planEndpoint = "$base/v1/ide/plan",
            executeEndpoint = "$base/v1/ide/execute",
            attachmentEndpoint = "$base/v1/ide/attachments",
            authToken = profile.token.ifBlank { null }
        )
    }

    private fun normalizeBackendBase(input: String): String {
        var s = input.trim().trimEnd('/')
        val suffixes = listOf("/v1/ide/chat", "/v1/ide/plan", "/v1/ide/execute", "/v1/ide/attachments", "/v1/ide", "/v1")
        for (suffix in suffixes) {
            if (s.endsWith(suffix, ignoreCase = true)) {
                s = s.removeSuffix(suffix)
                break
            }
        }
        return s.ifBlank { DEFAULT_BASE }
    }
}

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
            workflowsEndpoint = "$base/v1/workflows",
            attachmentEndpoint = "$base/v1/ide/attachments",
            kanbanTasksEndpoint = "$base/v1/kanban/tasks",
            authToken = profile.token.ifBlank { null }
        )
    }

    private fun normalizeBackendBase(input: String): String {
        var s = input.trim().trimEnd('/')
        val suffixes = listOf(
            "/v1/ide/attachments",
            "/v1/workflows",
            "/v1"
        )
        for (suffix in suffixes) {
            if (s.endsWith(suffix, ignoreCase = true)) {
                s = s.removeSuffix(suffix)
                break
            }
        }
        return s.ifBlank { DEFAULT_BASE }
    }
}

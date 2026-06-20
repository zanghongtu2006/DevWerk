package com.zanghongtu.devwerk

import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.zanghongtu.devwerk.codeEditor.ChatContext
import com.zanghongtu.devwerk.codeEditor.HttpAiClient
import com.zanghongtu.devwerk.codeEditor.ToolRequest
import java.nio.file.Files

class IdeaSdkCompilerIntegrationTest : BasePlatformTestCase() {
    override fun runInDispatchThread(): Boolean = false

    fun testCompilerManagerProducesAuditableClientToolOutcome() {
        val client = HttpAiClient(
            workflowsEndpoint = "http://127.0.0.1:1/v1/workflows",
            attachmentEndpoint = "http://127.0.0.1:1/v1/attachments",
            kanbanTasksEndpoint = "http://127.0.0.1:1/v1/kanban/tasks",
        )
        val context = ChatContext(
            projectRoot = project.basePath ?: Files.createTempDirectory("devwerk-sdk-test").toString(),
            history = emptyList(),
            project = project,
        )

        val result = client.executeClientTools(
            context,
            listOf(
                ToolRequest(
                    id = "sdk-compile-test",
                    tool = "ide_compile",
                    args = mapOf("timeout_seconds" to 1, "max_errors" to 20),
                )
            ),
        ).single()

        val evidence = result.content ?: result.error.orEmpty()
        assertTrue(evidence, evidence.startsWith("[ide_compile]"))
        assertTrue(
            evidence,
            evidence.contains("completed") ||
                (evidence.contains("timed out") && evidence.contains("waiting for IntelliJ CompilerManager")),
        )
        assertFalse(evidence, evidence.contains("project is unavailable"))
    }
}

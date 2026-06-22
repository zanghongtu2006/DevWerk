package com.zanghongtu.devwerk

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.roots.CompilerModuleExtension
import com.intellij.openapi.roots.ModuleRootModificationUtil
import com.intellij.openapi.projectRoots.ProjectJdkTable
import com.intellij.openapi.vfs.VfsUtil
import com.intellij.testFramework.IdeaTestUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.zanghongtu.devwerk.codeEditor.ChatContext
import com.zanghongtu.devwerk.codeEditor.HttpAiClient
import com.zanghongtu.devwerk.codeEditor.ToolRequest
import java.nio.file.Files

class IdeaSdkCompilerIntegrationTest : BasePlatformTestCase() {
    override fun runInDispatchThread(): Boolean = false

    override fun setUp() {
        super.setUp()
        val sdk = IdeaTestUtil.getMockJdk17()
        val compilerOutputUrl = VfsUtil.pathToUrl(Files.createTempDirectory("devwerk-compiler-output").toString())
        val application = ApplicationManager.getApplication()
        application.invokeAndWait {
            application.runWriteAction {
                ProjectJdkTable.getInstance().addJdk(sdk, testRootDisposable)
            }
        }
        ModuleRootModificationUtil.setModuleSdk(module, sdk)
        ModuleRootModificationUtil.updateModel(module) { model ->
            model.getModuleExtension(CompilerModuleExtension::class.java).apply {
                inheritCompilerOutputPath(false)
                setCompilerOutputPath(compilerOutputUrl)
                setCompilerOutputPathForTests("$compilerOutputUrl/test")
            }
        }
    }

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
                    tool = "project.compile",
                    args = mapOf("timeout_seconds" to 30, "max_errors" to 20),
                )
            ),
        ).single()

        val evidence = result.content ?: result.error.orEmpty()
        assertTrue(evidence, evidence.startsWith("[project.compile]"))
        assertTrue(evidence, evidence.contains("[project.compile] completed"))
        assertFalse(evidence, evidence.contains("project is unavailable"))
        assertFalse(evidence, evidence.contains("NoClassDefFoundError"))
        assertFalse(evidence, evidence.contains("ClassNotFoundException"))
    }

    fun testPluginDeclaresJavaRuntimeDependency() {
        val descriptor = javaClass.classLoader.getResource("META-INF/plugin.xml")
        assertNotNull("META-INF/plugin.xml must be available to the plugin classloader", descriptor)
        val xml = descriptor!!.readText()
        assertTrue(
            "CompilerManager requires the Java plugin to be declared as a runtime dependency",
            xml.contains("<depends>com.intellij.java</depends>"),
        )
    }
}

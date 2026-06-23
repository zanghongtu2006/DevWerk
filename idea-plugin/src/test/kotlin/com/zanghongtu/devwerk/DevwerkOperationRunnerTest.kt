package com.zanghongtu.devwerk

import com.zanghongtu.devwerk.codeEditor.DevwerkContext
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files

class DevwerkOperationRunnerTest {
    @Test
    fun `snapshot id keeps ISO date prefix and uuid`() {
        val projectRoot = Files.createTempDirectory("devwerk-project")
        val devwerkDir = projectRoot.resolve(".devwerk")
        val taskDir = devwerkDir.resolve("tasks/task-1")
        Files.createDirectories(taskDir)
        val opLog = taskDir.resolve("operation.log")
        Files.createFile(opLog)
        val ctx = DevwerkContext(
            projectRoot = projectRoot,
            devwerkDir = devwerkDir,
            opDir = taskDir,
            opLog = opLog,
        )

        val snapshotCtx = DevwerkOperationRunner().beginSnapshot(ctx)
        val snapshotId = snapshotCtx.opDir.fileName.toString()

        assertTrue(
            "snapshot id should be yyyy-MM-dd-UUID, got $snapshotId",
            snapshotId.matches(Regex("""\d{4}-\d{2}-\d{2}-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}""")),
        )
        assertTrue(Files.isDirectory(snapshotCtx.opDir.resolve("before")))
        assertTrue(Files.isDirectory(snapshotCtx.opDir.resolve("after")))
    }
}

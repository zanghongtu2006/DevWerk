package com.zanghongtu.devwerk

import com.zanghongtu.devwerk.codeEditor.DevwerkContext
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files

class DevwerkOperationRunnerTest {
    @Test
    fun `snapshot id keeps timestamp prefix and uuid`() {
        val projectRoot = Files.createTempDirectory("devwerk-project")
        val devwerkDir = projectRoot.resolve(".devwerk")
        val taskDir = devwerkDir.resolve("tasks/20260624-101112-123-task-1")
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
            "snapshot id should be yyyyMMdd-HHmmss-SSS-UUID, got $snapshotId",
            snapshotId.matches(Regex("""\d{8}-\d{6}-\d{3}-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}""")),
        )
        assertTrue(Files.isDirectory(snapshotCtx.opDir.resolve("before")))
        assertTrue(Files.isDirectory(snapshotCtx.opDir.resolve("after")))
    }

    @Test
    fun `task binding prefixes task directory with timestamp and reuses it`() {
        val projectRoot = Files.createTempDirectory("devwerk-project")
        val devwerkDir = projectRoot.resolve(".devwerk")
        val pendingDir = devwerkDir.resolve("pending/pending-1")
        Files.createDirectories(pendingDir)
        val pendingLog = pendingDir.resolve("operation.log")
        Files.writeString(pendingLog, "pending log\n")
        val ctx = DevwerkContext(
            projectRoot = projectRoot,
            devwerkDir = devwerkDir,
            opDir = pendingDir,
            opLog = pendingLog,
        )

        val first = DevwerkOperationRunner().bindTask(ctx, "abc-123")
        val taskDirName = first.opDir.fileName.toString()
        val second = DevwerkOperationRunner().bindTask(first, "abc-123")

        assertTrue(
            "task dir should be yyyyMMdd-HHmmss-SSS-taskId, got $taskDirName",
            taskDirName.matches(Regex("""\d{8}-\d{6}-\d{3}-abc-123""")),
        )
        assertEquals(first.opDir.normalize(), second.opDir.normalize())
        assertTrue(Files.isRegularFile(first.opLog))
        assertTrue(Files.readString(first.opLog).contains("workflowTaskId=abc-123"))
    }
}

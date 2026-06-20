package com.zanghongtu.devwerk

import com.zanghongtu.devwerk.codeEditor.FileOp
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files

class SnapshotGuardTest {
    @Test
    fun `create_file targeting an existing file is backed up`() {
        val project = Files.createTempDirectory("devwerk-project")
        val source = project.resolve("src/existing.txt")
        Files.createDirectories(source.parent)
        Files.writeString(source, "original")
        val before = Files.createTempDirectory("devwerk-before")
        val targets = SnapshotGuard.mutationTargets(
            listOf(FileOp(op = "create_file", path = "src/existing.txt", content = "replacement")), emptySet(),
        )

        val records = SnapshotGuard.captureBefore(project, before, targets)

        assertEquals("original", Files.readString(before.resolve("src/existing.txt")))
        assertTrue(records.single().existed)
        SnapshotGuard.assertComplete(before)
    }

    @Test
    fun `new file records absence in the before manifest`() {
        val project = Files.createTempDirectory("devwerk-project")
        val before = Files.createTempDirectory("devwerk-before")
        val targets = SnapshotGuard.mutationTargets(
            listOf(FileOp(op = "create_file", path = "src/new.txt", content = "new")), emptySet(),
        )

        val records = SnapshotGuard.captureBefore(project, before, targets)
        val manifest = JSONObject(Files.readString(before.resolve(SnapshotGuard.MANIFEST_FILE)))

        assertFalse(records.single().existed)
        assertFalse(manifest.getJSONArray("entries").getJSONObject(0).getBoolean("existed"))
        assertTrue(Files.isRegularFile(before.resolve(SnapshotGuard.MANIFEST_FILE)))
    }

    @Test(expected = IllegalStateException::class)
    fun `missing required backup blocks apply`() {
        val project = Files.createTempDirectory("devwerk-project")
        val source = project.resolve("src/existing.txt")
        Files.createDirectories(source.parent)
        Files.writeString(source, "original")
        val before = Files.createTempDirectory("devwerk-before")
        val targets = SnapshotGuard.mutationTargets(
            listOf(FileOp(op = "update_file", path = "src/existing.txt", content = "replacement")), emptySet(),
        )
        SnapshotGuard.captureBefore(project, before, targets)
        Files.delete(before.resolve("src/existing.txt"))

        SnapshotGuard.assertComplete(before)
    }
}

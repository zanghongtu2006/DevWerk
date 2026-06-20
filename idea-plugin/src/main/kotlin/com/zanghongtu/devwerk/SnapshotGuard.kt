package com.zanghongtu.devwerk

import com.zanghongtu.devwerk.codeEditor.FileOp
import org.json.JSONArray
import org.json.JSONObject
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.StandardOpenOption
import java.time.Instant

data class SnapshotTarget(val path: String, val operation: String)

data class SnapshotRecord(
    val path: String,
    val operation: String,
    val existed: Boolean,
    val directory: Boolean,
)

object SnapshotGuard {
    const val MANIFEST_FILE = "manifest.json"

    fun mutationTargets(ops: List<FileOp>, patchPaths: Set<String>): List<SnapshotTarget> {
        val targets = mutableListOf<SnapshotTarget>()
        ops.filter { it.op in MUTATING_FILE_OPS }.forEach { targets += SnapshotTarget(it.path, it.op) }
        patchPaths.forEach { targets += SnapshotTarget(it, "apply_patch") }
        return targets
            .map { SnapshotTarget(normalizeRelPath(it.path), it.operation) }
            .filter { it.path.isNotBlank() }
            .distinctBy { it.path }
    }

    fun captureBefore(projectRoot: Path, beforeRoot: Path, targets: List<SnapshotTarget>): List<SnapshotRecord> {
        val normalizedProjectRoot = projectRoot.toAbsolutePath().normalize()
        Files.createDirectories(beforeRoot)
        val records = targets.map { target ->
            val safePath = normalizeRelPath(target.path)
            require(safePath.isNotBlank()) { "Snapshot target path is invalid: ${target.path}" }
            val source = normalizedProjectRoot.resolve(safePath).normalize()
            require(source.startsWith(normalizedProjectRoot)) { "Snapshot target escapes project root: ${target.path}" }
            val existed = Files.exists(source)
            val directory = existed && Files.isDirectory(source)
            if (existed) {
                val destination = beforeRoot.resolve(safePath).normalize()
                require(destination.startsWith(beforeRoot.normalize())) { "Snapshot destination escapes before root: $safePath" }
                if (directory) copyDirectory(source, destination) else copyFile(source, destination)
                verifyCopy(source, destination)
            }
            SnapshotRecord(safePath, target.operation, existed, directory)
        }

        val manifest = JSONObject()
            .put("version", 1)
            .put("created_at", Instant.now().toString())
            .put("entries", JSONArray(records.map { record ->
                JSONObject()
                    .put("path", record.path)
                    .put("operation", record.operation)
                    .put("existed", record.existed)
                    .put("directory", record.directory)
            }))
        Files.writeString(
            beforeRoot.resolve(MANIFEST_FILE),
            manifest.toString(2) + "\n",
            StandardCharsets.UTF_8,
            StandardOpenOption.CREATE,
            StandardOpenOption.TRUNCATE_EXISTING,
        )
        assertComplete(beforeRoot)
        return records
    }

    fun assertComplete(beforeRoot: Path) {
        val manifestPath = beforeRoot.resolve(MANIFEST_FILE)
        check(Files.isRegularFile(manifestPath)) { "Before snapshot manifest is missing: $manifestPath" }
        val entries = JSONObject(Files.readString(manifestPath, StandardCharsets.UTF_8)).getJSONArray("entries")
        for (index in 0 until entries.length()) {
            val entry = entries.getJSONObject(index)
            if (!entry.getBoolean("existed")) continue
            val path = normalizeRelPath(entry.getString("path"))
            check(path.isNotBlank()) { "Before snapshot manifest contains an invalid path" }
            val backup = beforeRoot.resolve(path).normalize()
            check(backup.startsWith(beforeRoot.normalize()) && Files.exists(backup)) {
                "Required before snapshot is missing: $path"
            }
        }
    }

    private fun copyFile(source: Path, destination: Path) {
        Files.createDirectories(destination.parent)
        Files.copy(source, destination, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.COPY_ATTRIBUTES)
    }

    private fun copyDirectory(source: Path, destination: Path) {
        Files.walk(source).use { paths ->
            paths.forEach { current ->
                val target = destination.resolve(source.relativize(current))
                if (Files.isDirectory(current)) Files.createDirectories(target) else copyFile(current, target)
            }
        }
    }

    private fun verifyCopy(source: Path, destination: Path) {
        check(Files.exists(destination)) { "Snapshot copy was not created: $destination" }
        if (Files.isDirectory(source)) {
            Files.walk(source).use { paths ->
                paths.filter { Files.isRegularFile(it) }.forEach { current ->
                    val copied = destination.resolve(source.relativize(current))
                    check(Files.isRegularFile(copied) && Files.mismatch(current, copied) == -1L) {
                        "Snapshot verification failed: $current"
                    }
                }
            }
        } else {
            check(Files.isRegularFile(destination) && Files.mismatch(source, destination) == -1L) {
                "Snapshot verification failed: $source"
            }
        }
    }

    private fun normalizeRelPath(path: String): String {
        var value = path.trim().replace("\\", "/")
        while (value.startsWith("/")) value = value.substring(1)
        val parts = value.split("/").filter { it.isNotBlank() }
        if (parts.any { it == ".." }) return ""
        return parts.joinToString("/")
    }

    private val MUTATING_FILE_OPS = setOf(
        "create_file", "update_file", "modify_file", "delete_path", "delete_file", "delete_dir",
    )
}

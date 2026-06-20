package com.zanghongtu.devwerk

import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.zanghongtu.devwerk.codeEditor.DevwerkContext
import com.zanghongtu.devwerk.codeEditor.FileOp
import com.zanghongtu.devwerk.codeEditor.IdeChatResponse
import com.zanghongtu.devwerk.codeEditor.FsScaffolder
import com.zanghongtu.devwerk.codeEditor.PatchApplier
import java.nio.charset.StandardCharsets
import java.nio.file.*
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.UUID

class DevwerkOperationRunner {

    fun beginInteraction(project: Project, projectRootPath: Path, taskId: String? = null): DevwerkContext {
        val ctx = ensureDevwerkAndCreateTaskDir(projectRootPath, taskId)
        appendLog(ctx.opLog, "=== DevWerk Workflow Interaction Started ===\n")
        appendLog(ctx.opLog, "[INFO] projectRoot=${ctx.projectRoot}\n")

        refreshVfs(projectRootPath)
        return ctx
    }

    fun bindTask(ctx: DevwerkContext, taskId: String): DevwerkContext {
        val safeTaskId = taskId.trim().takeIf { it.matches(Regex("[A-Za-z0-9._-]+")) } ?: return ctx
        val taskDir = ctx.devwerkDir.resolve("tasks").resolve(safeTaskId)
        val taskLog = taskDir.resolve("operation.log")
        if (ctx.opLog.normalize() == taskLog.normalize()) return ctx

        Files.createDirectories(taskDir)
        if (Files.exists(ctx.opLog)) {
            Files.writeString(
                taskLog,
                Files.readString(ctx.opLog, StandardCharsets.UTF_8),
                StandardCharsets.UTF_8,
                StandardOpenOption.CREATE,
                StandardOpenOption.APPEND
            )
            Files.deleteIfExists(ctx.opLog)
            runCatching { Files.deleteIfExists(ctx.opDir) }
        }
        val bound = ctx.copy(opDir = taskDir, opLog = taskLog)
        appendLog(bound.opLog, "[INFO] workflowTaskId=$safeTaskId\n")
        return bound
    }

    fun beginSnapshot(ctx: DevwerkContext): DevwerkContext {
        val snapshotId = LocalDateTime.now().format(SNAPSHOT_TIME_FORMAT) + "-" + UUID.randomUUID()
        val snapshotDir = ctx.opDir.resolve("snapshots").resolve(snapshotId)
        Files.createDirectories(snapshotDir.resolve("before"))
        Files.createDirectories(snapshotDir.resolve("after"))
        appendLog(ctx.opLog, "[INFO] Snapshot started: $snapshotId\n")
        return ctx.copy(opDir = snapshotDir)
    }

    fun recordFinalSummaryAndBackup(project: Project, ctx: DevwerkContext, response: IdeChatResponse) {
        appendLog(ctx.opLog, "\n===== FINAL SUMMARY BEGIN =====\n")
        appendLog(ctx.opLog, "[INFO] reply=${response.reply}\n")
        appendLog(ctx.opLog, "[INFO] done=${response.done}\n")
        appendLog(ctx.opLog, "[INFO] ops_count=${response.ops.size}\n")
        appendLog(ctx.opLog, "[INFO] patch_ops_count=${response.patchOps.size}\n")
        appendLog(ctx.opLog, "[INFO] tool_requests_count=${response.toolRequests.size}\n")
        appendLog(ctx.opLog, "===== FINAL SUMMARY END =====\n")

        if (response.ops.isNotEmpty()) {
            appendLog(ctx.opLog, "\n===== OPS LIST BEGIN =====\n")
            response.ops.forEachIndexed { idx, op ->
                appendLog(ctx.opLog, "[OP ${idx + 1}] ${op.op} ${op.path}\n")
            }
            appendLog(ctx.opLog, "===== OPS LIST END =====\n")
        }

        val patchPathsRaw =
            if (response.patchOps.isNotEmpty()) PatchApplier.collectAffectedPaths(response.patchOps) else emptySet()

        val patchPaths = patchPathsRaw
            .map { normalizeRelPath(it) }
            .filter { it.isNotBlank() && !hasHiddenDirSegment(it) }
            .toSet()

        if (patchPaths.isNotEmpty()) {
            appendLog(ctx.opLog, "\n===== PATCH PATHS BEGIN =====\n")
            patchPaths.sorted().forEach { p -> appendLog(ctx.opLog, "[PATCH] $p\n") }
            appendLog(ctx.opLog, "===== PATCH PATHS END =====\n")
        } else if (patchPathsRaw.isNotEmpty()) {
            // 如果全部被过滤掉，记一下
            val blocked = patchPathsRaw.map { normalizeRelPath(it) }.filter { it.isNotBlank() && hasHiddenDirSegment(it) }
            if (blocked.isNotEmpty()) {
                appendLog(ctx.opLog, "\n[WARN] Patch targets contain hidden-dir paths and were blocked:\n")
                blocked.distinct().sorted().forEach { appendLog(ctx.opLog, "[WARN] blocked patch path: $it\n") }
            }
        }

        val beforeTargets = collectBeforeTargets(response.ops, patchPaths)
        snapshotTo(ctx, beforeTargets, slot = "before", reason = "before")

        refreshVfs(ctx.projectRoot)
    }

    fun recordInteractionPaused(ctx: DevwerkContext, response: IdeChatResponse) {
        val reason = response.interaction["reason"]?.toString()?.takeIf { it.isNotBlank() }
            ?: response.waitingFor
            ?: "unspecified"
        appendLog(ctx.opLog, "\n===== INTERACTION PAUSED =====\n")
        appendLog(ctx.opLog, "[INFO] task_id=${response.taskId}\n")
        appendLog(ctx.opLog, "[INFO] status_key=${response.statusKey}\n")
        appendLog(ctx.opLog, "[INFO] waiting_for=${response.waitingFor}\n")
        appendLog(ctx.opLog, "[INFO] reason=$reason\n")
        appendLog(ctx.opLog, "===== INTERACTION PAUSED END =====\n")
        appendLog(ctx.opLog, "=== DevWerk Workflow Interaction Ended: PAUSED ===\n")
    }

    fun recordInteractionEnded(ctx: DevwerkContext, response: IdeChatResponse) {
        val outcome = when {
            !response.ok -> "FAILED"
            response.statusKey.equals("failed", ignoreCase = true) -> "FAILED"
            else -> "DONE"
        }
        appendLog(ctx.opLog, "\n=== DevWerk Workflow Interaction Ended: $outcome ===\n")
    }

    fun applyResponse(project: Project, ctx: DevwerkContext, response: IdeChatResponse) {
        // 1) patch_ops（兜底：如果 patch 目标涉及隐藏目录，直接拒绝）
        if (response.patchOps.isNotEmpty()) {
            val raw = PatchApplier.collectAffectedPaths(response.patchOps).map { normalizeRelPath(it) }.filter { it.isNotBlank() }
            val blocked = raw.filter { hasHiddenDirSegment(it) }.distinct()

            if (blocked.isNotEmpty()) {
                appendLog(ctx.opLog, "[WARN] Refuse to apply patchOps because it targets hidden-dir paths:\n")
                blocked.sorted().forEach { appendLog(ctx.opLog, "[WARN] blocked patch path: $it\n") }
                appendLog(ctx.opLog, "[INFO] patchOps skipped.\n")
            } else {
                appendLog(ctx.opLog, "[INFO] Applying patchOps: ${response.patchOps.size}\n")
                PatchApplier.applyPatchOps(project, response.patchOps)
                appendLog(ctx.opLog, "[OK] patchOps applied.\n")
            }
        } else if (response.ops.isNotEmpty()) {
            // 2) file ops（兜底：屏蔽隐藏目录内部操作）
            val filteredOps = response.ops.filter { op ->
                val p = normalizeRelPath(op.path)
                val blocked = p.isNotBlank() && hasHiddenDirSegment(p)
                if (blocked) {
                    appendLog(ctx.opLog, "[WARN] blocked file op on hidden-dir path: ${op.op} ${op.path}\n")
                }
                !blocked
            }

            if (filteredOps.isEmpty()) {
                appendLog(ctx.opLog, "[INFO] No safe file ops to apply (all blocked or empty).\n")
            } else {
                appendLog(ctx.opLog, "[INFO] Applying file ops: ${filteredOps.size}\n")
                FsScaffolder.applyFileOps(project, filteredOps)
                appendLog(ctx.opLog, "[OK] file ops applied.\n")
            }
        } else {
            appendLog(ctx.opLog, "[INFO] No ops/patchOps to apply.\n")
        }

        val patchPathsRaw =
            if (response.patchOps.isNotEmpty()) PatchApplier.collectAffectedPaths(response.patchOps) else emptySet()

        val patchPaths = patchPathsRaw
            .map { normalizeRelPath(it) }
            .filter { it.isNotBlank() && !hasHiddenDirSegment(it) }
            .toSet()

        val afterTargets = collectAfterTargets(response.ops, patchPaths)
        snapshotTo(ctx, afterTargets, slot = "after", reason = "after")

        refreshVfs(ctx.projectRoot)
    }

    private fun collectBeforeTargets(ops: List<FileOp>, patchPaths: Set<String>): List<String> {
        val fromOps = ops.filter {
            it.op == "update_file" ||
                    it.op == "modify_file" ||
                    it.op == "delete_path" ||
                    it.op == "delete_file" ||
                    it.op == "delete_dir"
        }.map { it.path }

        return (fromOps + patchPaths)
            .map { normalizeRelPath(it) }
            .filter { it.isNotBlank() && !hasHiddenDirSegment(it) }
            .distinct()
    }

    private fun collectAfterTargets(ops: List<FileOp>, patchPaths: Set<String>): List<String> {
        val fromOps = ops.filter {
            it.op == "create_file" ||
                    it.op == "update_file" ||
                    it.op == "modify_file"
        }.map { it.path }

        return (fromOps + patchPaths)
            .map { normalizeRelPath(it) }
            .filter { it.isNotBlank() && !hasHiddenDirSegment(it) }
            .distinct()
    }

    private fun snapshotTo(ctx: DevwerkContext, relPaths: List<String>, slot: String, reason: String) {
        if (relPaths.isEmpty()) {
            appendLog(ctx.opLog, "[INFO] No snapshot targets for $reason.\n")
            return
        }

        val root = ctx.opDir.resolve(slot)
        Files.createDirectories(root)

        for (rel0 in relPaths) {
            val safeRel = normalizeRelPath(rel0)
            if (safeRel.isBlank()) {
                appendLog(ctx.opLog, "[WARN] Snapshot($reason) skip invalid path: $rel0\n")
                continue
            }

            //  关键：永远不快照隐藏目录内部文件
            if (hasHiddenDirSegment(safeRel)) {
                appendLog(ctx.opLog, "[WARN] Snapshot($reason) skip hidden-dir path: $safeRel\n")
                continue
            }

            val src = ctx.projectRoot.resolve(safeRel).normalize()
            if (!src.startsWith(ctx.projectRoot)) {
                appendLog(ctx.opLog, "[WARN] Snapshot($reason) skip (escapes root): $safeRel\n")
                continue
            }

            if (!Files.exists(src)) {
                appendLog(ctx.opLog, "[WARN] Snapshot($reason) not found: $safeRel\n")
                continue
            }

            val dst = root.resolve(safeRel).normalize()
            try {
                if (Files.isDirectory(src)) {
                    copyDirectoryRecursively(src, dst)
                    appendLog(ctx.opLog, "[OK] Snapshot($reason) dir: $safeRel -> $dst\n")
                } else {
                    Files.createDirectories(dst.parent)
                    Files.copy(src, dst, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.COPY_ATTRIBUTES)
                    appendLog(ctx.opLog, "[OK] Snapshot($reason) file: $safeRel -> $dst\n")
                }
            } catch (e: Exception) {
                appendLog(ctx.opLog, "[ERROR] Snapshot($reason) failed: $safeRel, ${e::class.java.simpleName}: ${e.message}\n")
            }
        }
    }

    private fun copyDirectoryRecursively(srcDir: Path, dstDir: Path) {
        Files.walk(srcDir).use { stream ->
            stream.forEach { src ->
                val rel = srcDir.relativize(src)
                val dst = dstDir.resolve(rel)
                if (Files.isDirectory(src)) {
                    Files.createDirectories(dst)
                } else {
                    Files.createDirectories(dst.parent)
                    Files.copy(src, dst, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.COPY_ATTRIBUTES)
                }
            }
        }
    }

    private fun ensureDevwerkAndCreateTaskDir(projectRoot: Path, taskId: String?): DevwerkContext {
        val devwerkDir = projectRoot.resolve(".devwerk")
        Files.createDirectories(devwerkDir)

        ensureGitignoreContainsDevwerk(projectRoot)

        val safeTaskId = taskId?.trim()?.takeIf { it.matches(Regex("[A-Za-z0-9._-]+")) }
        val opDir = if (safeTaskId != null) {
            devwerkDir.resolve("tasks").resolve(safeTaskId)
        } else {
            devwerkDir.resolve("pending").resolve(UUID.randomUUID().toString())
        }
        Files.createDirectories(opDir)

        val opLog = opDir.resolve("operation.log")
        if (!Files.exists(opLog)) Files.createFile(opLog)

        return DevwerkContext(
            projectRoot = projectRoot,
            devwerkDir = devwerkDir,
            opDir = opDir,
            opLog = opLog
        )
    }

    private fun ensureGitignoreContainsDevwerk(projectRoot: Path) {
        val gitignore = projectRoot.resolve(".gitignore")
        if (!Files.exists(gitignore)) {
            Files.writeString(
                gitignore,
                ".devwerk\n",
                StandardCharsets.UTF_8,
                StandardOpenOption.CREATE,
                StandardOpenOption.TRUNCATE_EXISTING
            )
            return
        }

        val content = Files.readString(gitignore, StandardCharsets.UTF_8)
        val hasLine = content.lineSequence().any { line ->
            val t = line.trim()
            t == ".devwerk" || t == "/.devwerk"
        }
        if (!hasLine) {
            val suffix = if (content.endsWith("\n") || content.isEmpty()) "" else "\n"
            Files.writeString(
                gitignore,
                suffix + ".devwerk\n",
                StandardCharsets.UTF_8,
                StandardOpenOption.APPEND
            )
        }
    }

    private fun appendLog(logFile: Path, text: String) {
        Files.writeString(
            logFile,
            timestampLogText(text),
            StandardCharsets.UTF_8,
            StandardOpenOption.CREATE,
            StandardOpenOption.APPEND
        )
    }

    private fun timestampLogText(text: String): String {
        val suffix = if (text.endsWith("\n")) "\n" else ""
        return text.trimEnd('\n').split('\n').joinToString("\n") { line ->
            if (line.isBlank()) line else "${LocalDateTime.now().format(LOG_TIME_FORMAT)} $line"
        } + suffix
    }

    private fun normalizeRelPath(p: String): String {
        var s = p.trim().replace("\\", "/")
        while (s.startsWith("/")) s = s.substring(1)
        val parts = s.split("/").filter { it.isNotBlank() }
        if (parts.any { it == ".." }) return ""
        return parts.joinToString("/")
    }

    /**
     * 只判断“目录段”是否包含隐藏目录：
     * - 允许 ".gitignore" 这种顶层隐藏文件
     * - 但拦截 ".devwerk/xxx"、".idea/xxx"、".git/xxx"
     */
    private fun hasHiddenDirSegment(rel: String): Boolean {
        val parts = rel.trim().replace("\\", "/").split("/").filter { it.isNotBlank() }
        if (parts.size <= 1) return false
        return parts.dropLast(1).any { it.startsWith(".") }
    }

    private fun refreshVfs(projectRoot: Path) {
        val lfs = LocalFileSystem.getInstance()
        val rootVf = lfs.refreshAndFindFileByPath(projectRoot.toString().replace('\\', '/'))
        rootVf?.refresh(true, true)
    }

    companion object {
        private val LOG_TIME_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS")
        private val SNAPSHOT_TIME_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss-SSS")
    }
}

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
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.UUID
import kotlin.streams.asSequence

class DevwerkOperationRunner {

    /**
     * 发送前调用：只做目录准备（1、2），并写入 header
     */
    fun beginOperation(project: Project, projectRootPath: Path): DevwerkContext {
        val ctx = ensureDevwerkAndCreateOpDir(projectRootPath)
        appendLog(ctx.opLog, "=== DevWerk Operation Started: ${ctx.opDir.fileName} ===\n")
        appendLog(ctx.opLog, "[INFO] projectRoot=${ctx.projectRoot}\n")
        refreshVfs(projectRootPath)
        return ctx
    }

    /**
     * 拿到最终 response 后调用：做（4）备份 + 记录摘要（可选）
     * 注意：请求/响应原样日志已经由 HttpAiClient 在发送/接收时写入了。
     */
    fun recordFinalSummaryAndBackup(project: Project, ctx: DevwerkContext, response: IdeChatResponse) {
        appendLog(ctx.opLog, "\n===== FINAL SUMMARY BEGIN =====\n")
        appendLog(ctx.opLog, "[INFO] reply=${response.reply}\n")
        appendLog(ctx.opLog, "[INFO] done=${response.done}\n")
        appendLog(ctx.opLog, "[INFO] ops_count=${response.ops.size}\n")
        appendLog(ctx.opLog, "[INFO] patch_ops_count=${response.patchOps.size}\n")
        appendLog(ctx.opLog, "[INFO] tool_requests_count=${response.toolRequests.size}\n")
        appendLog(ctx.opLog, "===== FINAL SUMMARY END =====\n")

        // 4) 备份：ops + patch_ops 触及的文件
        backupFilesForOps(ctx, response.ops)
        backupFilesForPatch(ctx, response)

        refreshVfs(ctx.projectRoot)
    }

    /**
     * 5) 执行原来的增删改逻辑（不调用 AI）。
     */
    fun applyResponse(project: Project, ctx: DevwerkContext, response: IdeChatResponse) {
        if (response.patchOps.isNotEmpty()) {
            appendLog(ctx.opLog, "[INFO] Applying patchOps: ${response.patchOps.size}\n")
            PatchApplier.applyPatchOps(project, response.patchOps)
            appendLog(ctx.opLog, "[OK] patchOps applied.\n")
        } else if (response.ops.isNotEmpty()) {
            appendLog(ctx.opLog, "[INFO] Applying file ops: ${response.ops.size}\n")
            FsScaffolder.applyFileOps(project, response.ops)
            appendLog(ctx.opLog, "[OK] file ops applied.\n")
        } else {
            appendLog(ctx.opLog, "[INFO] No ops/patchOps to apply.\n")
        }
        refreshVfs(ctx.projectRoot)
    }

    private fun ensureDevwerkAndCreateOpDir(projectRoot: Path): DevwerkContext {
        val devwerkDir = projectRoot.resolve(".devwerk")
        Files.createDirectories(devwerkDir)

        ensureGitignoreContainsDevwerk(projectRoot)

        val opDir = createNextOperationDir(devwerkDir)
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

    private fun createNextOperationDir(devwerkDir: Path): Path {
        val dateStr = LocalDate.now().format(DateTimeFormatter.BASIC_ISO_DATE) // YYYYMMDD

        val maxIndex = Files.list(devwerkDir).use { stream ->
            stream.asSequence()
                .filter { Files.isDirectory(it) }
                .map { it.fileName.toString() }
                .mapNotNull { name ->
                    if (!name.startsWith("$dateStr-")) return@mapNotNull null
                    val parts = name.split("-", limit = 3)
                    if (parts.size < 3) return@mapNotNull null
                    parts[1].toIntOrNull()
                }
                .maxOrNull() ?: 0
        }

        val nextIndex = maxIndex + 1
        val indexStr = "%04d".format(nextIndex)
        val uuid = UUID.randomUUID().toString()
        return devwerkDir.resolve("$dateStr-$indexStr-$uuid")
    }

    private fun appendLog(logFile: Path, text: String) {
        Files.writeString(
            logFile,
            text,
            StandardCharsets.UTF_8,
            StandardOpenOption.CREATE,
            StandardOpenOption.APPEND
        )
    }

    private fun backupFilesForOps(ctx: DevwerkContext, ops: List<FileOp>) {
        val targets = ops
            .filter {
                it.op == "update_file" ||
                        it.op == "modify_file" ||
                        it.op == "delete_path" ||
                        it.op == "delete_file"
            }
            .map { it.path }
            .distinct()

        backupFilesByRelPaths(ctx, targets, reason = "ops")
    }

    private fun backupFilesForPatch(ctx: DevwerkContext, response: IdeChatResponse) {
        if (response.patchOps.isEmpty()) return
        val targets = PatchApplier.collectAffectedPaths(response.patchOps).toList()
        backupFilesByRelPaths(ctx, targets, reason = "patch_ops")
    }

    private fun backupFilesByRelPaths(ctx: DevwerkContext, relPaths: List<String>, reason: String) {
        if (relPaths.isEmpty()) {
            appendLog(ctx.opLog, "[INFO] No files to backup for $reason.\n")
            return
        }

        for (relPath in relPaths) {
            val safeRel = normalizeRelPath(relPath)
            if (safeRel.isBlank()) {
                appendLog(ctx.opLog, "[WARN] Skip backup (invalid relPath): $relPath\n")
                continue
            }

            val src = ctx.projectRoot.resolve(safeRel).normalize()
            val dst = ctx.opDir.resolve(safeRel).normalize()

            if (!src.startsWith(ctx.projectRoot)) {
                appendLog(ctx.opLog, "[WARN] Skip backup (path escapes root): $safeRel\n")
                continue
            }

            try {
                if (Files.exists(src) && Files.isRegularFile(src)) {
                    Files.createDirectories(dst.parent)
                    Files.copy(src, dst, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.COPY_ATTRIBUTES)
                    appendLog(ctx.opLog, "[OK] Backup($reason): $safeRel -> $dst\n")
                } else {
                    appendLog(ctx.opLog, "[WARN] Backup($reason) skipped (not found or not file): $safeRel\n")
                }
            } catch (e: Exception) {
                appendLog(ctx.opLog, "[ERROR] Backup($reason) failed: $safeRel, ${e::class.java.simpleName}: ${e.message}\n")
            }
        }
    }

    private fun normalizeRelPath(p: String): String {
        var s = p.trim().replace("\\", "/")
        while (s.startsWith("/")) s = s.substring(1)
        val parts = s.split("/").filter { it.isNotBlank() }
        if (parts.any { it == ".." }) return ""
        return parts.joinToString("/")
    }

    private fun refreshVfs(projectRoot: Path) {
        val lfs = LocalFileSystem.getInstance()
        val rootVf = lfs.refreshAndFindFileByPath(projectRoot.toString().replace('\\', '/'))
        rootVf?.refresh(true, true)
    }
}

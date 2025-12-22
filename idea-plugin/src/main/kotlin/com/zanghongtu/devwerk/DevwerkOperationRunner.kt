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
     * 发送前调用：做目录准备（1、2），并创建 before/after 目录
     */
    fun beginOperation(project: Project, projectRootPath: Path): DevwerkContext {
        val ctx = ensureDevwerkAndCreateOpDir(projectRootPath)

        // 创建 before/after 根目录
        Files.createDirectories(ctx.opDir.resolve("before"))
        Files.createDirectories(ctx.opDir.resolve("after"))

        appendLog(ctx.opLog, "=== DevWerk Operation Started: ${ctx.opDir.fileName} ===\n")
        appendLog(ctx.opLog, "[INFO] projectRoot=${ctx.projectRoot}\n")

        refreshVfs(projectRootPath)
        return ctx
    }

    /**
     * 拿到最终 response 后调用：
     * - 记录结构化的“本次会触及哪些文件/操作”
     * - 做 BEFORE 备份（修改/删除 + patch 涉及的已存在文件）
     *
     * 注意：请求/响应原样日志已由 HttpAiClient 在发送/接收时写入。
     */
    fun recordFinalSummaryAndBackup(project: Project, ctx: DevwerkContext, response: IdeChatResponse) {
        appendLog(ctx.opLog, "\n===== FINAL SUMMARY BEGIN =====\n")
        appendLog(ctx.opLog, "[INFO] reply=${response.reply}\n")
        appendLog(ctx.opLog, "[INFO] done=${response.done}\n")
        appendLog(ctx.opLog, "[INFO] ops_count=${response.ops.size}\n")
        appendLog(ctx.opLog, "[INFO] patch_ops_count=${response.patchOps.size}\n")
        appendLog(ctx.opLog, "[INFO] tool_requests_count=${response.toolRequests.size}\n")
        appendLog(ctx.opLog, "===== FINAL SUMMARY END =====\n")

        // 记录每条 op（让你一眼知道增删改哪些文件）
        if (response.ops.isNotEmpty()) {
            appendLog(ctx.opLog, "\n===== OPS LIST BEGIN =====\n")
            response.ops.forEachIndexed { idx, op ->
                appendLog(ctx.opLog, "[OP ${idx + 1}] ${op.op} ${op.path}\n")
            }
            appendLog(ctx.opLog, "===== OPS LIST END =====\n")
        }

        // patch 涉及的文件清单
        val patchPaths = if (response.patchOps.isNotEmpty()) PatchApplier.collectAffectedPaths(response.patchOps) else emptySet()
        if (patchPaths.isNotEmpty()) {
            appendLog(ctx.opLog, "\n===== PATCH PATHS BEGIN =====\n")
            patchPaths.sorted().forEach { p -> appendLog(ctx.opLog, "[PATCH] $p\n") }
            appendLog(ctx.opLog, "===== PATCH PATHS END =====\n")
        }

        // BEFORE：备份“将被修改或删除”的文件 + patch 涉及的已存在文件
        val beforeTargets = collectBeforeTargets(response.ops, patchPaths)
        snapshotTo(ctx, beforeTargets, slot = "before", reason = "before")

        refreshVfs(ctx.projectRoot)
    }

    /**
     * 执行文件变更（5），并在执行后做 AFTER 快照（新增/修改 + patch）
     */
    fun applyResponse(project: Project, ctx: DevwerkContext, response: IdeChatResponse) {
        // 应用变更
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

        // AFTER：备份“新增/修改”的文件 + patch 涉及的文件（存在的）
        val patchPaths = if (response.patchOps.isNotEmpty()) PatchApplier.collectAffectedPaths(response.patchOps) else emptySet()
        val afterTargets = collectAfterTargets(response.ops, patchPaths)
        snapshotTo(ctx, afterTargets, slot = "after", reason = "after")

        refreshVfs(ctx.projectRoot)
    }

    // -------------------------------
    // target collection
    // -------------------------------

    /**
     * BEFORE 需要备份：
     * - update/modify：备份旧文件
     * - delete：备份被删前内容（文件或目录）
     * - patch：备份 patch 涉及且当前存在的文件
     */
    private fun collectBeforeTargets(ops: List<FileOp>, patchPaths: Set<String>): List<String> {
        val fromOps = ops.filter {
            it.op == "update_file" ||
                    it.op == "modify_file" ||
                    it.op == "delete_path" ||
                    it.op == "delete_file" ||
                    it.op == "delete_dir"
        }.map { it.path }

        return (fromOps + patchPaths).distinct()
    }

    /**
     * AFTER 需要备份：
     * - create/update/modify：备份最终文件内容
     * - patch：备份最终文件内容
     */
    private fun collectAfterTargets(ops: List<FileOp>, patchPaths: Set<String>): List<String> {
        val fromOps = ops.filter {
            it.op == "create_file" ||
                    it.op == "update_file" ||
                    it.op == "modify_file"
        }.map { it.path }

        return (fromOps + patchPaths).distinct()
    }

    // -------------------------------
    // snapshot core
    // -------------------------------

    /**
     * 将给定相对路径列表快照到 opDir/{slot}/ 下，保持目录结构。
     * - 文件：直接 copy
     * - 目录（常见于 delete_path/delete_dir）：递归 copy（慎用，但满足你“保持目录结构”的要求）
     */
    private fun snapshotTo(ctx: DevwerkContext, relPaths: List<String>, slot: String, reason: String) {
        if (relPaths.isEmpty()) {
            appendLog(ctx.opLog, "[INFO] No snapshot targets for $reason.\n")
            return
        }

        val root = ctx.opDir.resolve(slot)
        Files.createDirectories(root)

        for (rel in relPaths) {
            val safeRel = normalizeRelPath(rel)
            if (safeRel.isBlank()) {
                appendLog(ctx.opLog, "[WARN] Snapshot($reason) skip invalid path: $rel\n")
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
                    // 目录递归 copy
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

    // -------------------------------
    // filesystem helpers
    // -------------------------------

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

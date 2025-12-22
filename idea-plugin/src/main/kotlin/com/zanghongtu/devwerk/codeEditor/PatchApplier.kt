package com.zanghongtu.devwerk.codeEditor

import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VfsUtil
import com.intellij.openapi.vfs.VirtualFile

object PatchApplier {

    fun applyPatchOps(project: Project, patchOps: List<PatchOp>) {
        if (patchOps.isEmpty()) return
        val basePath = project.basePath ?: return
        val baseDir = LocalFileSystem.getInstance().findFileByPath(basePath) ?: return

        WriteCommandAction.runWriteCommandAction(project) {
            for (po in patchOps) {
                if (po.op != "apply_patch") continue
                applyUnifiedDiff(baseDir, po.content)
            }
        }
    }

    /**
     * 用于 DevWerk 备份：从 patchOps 中提取会被创建/修改/删除的目标文件路径（相对路径）。
     */
    fun collectAffectedPaths(patchOps: List<PatchOp>): Set<String> {
        val out = mutableSetOf<String>()
        for (po in patchOps) {
            if (po.op != "apply_patch") continue
            val patches = parseUnifiedDiff(po.content)
            for (fp in patches) {
                val raw = (fp.newPath ?: fp.oldPath) ?: continue
                val norm = normalizePatchPath(raw)
                if (norm.isNotBlank()) out += norm
            }
        }
        return out
    }

    private data class Hunk(val oldStart: Int, val oldCount: Int, val newStart: Int, val newCount: Int, val lines: List<String>)
    private data class FilePatch(
        val oldPath: String?,
        val newPath: String?,
        val isCreate: Boolean,
        val isDelete: Boolean,
        val hunks: List<Hunk>
    )

    private fun applyUnifiedDiff(baseDir: VirtualFile, diff: String) {
        val patches = parseUnifiedDiff(diff)
        for (fp in patches) {
            val targetPath = (fp.newPath ?: fp.oldPath)?.let { normalizePatchPath(it) } ?: continue
            if (targetPath.isBlank()) continue

            if (fp.isDelete) {
                val vf = findRelative(baseDir, targetPath)
                vf?.delete(this)
                continue
            }

            val oldText = if (!fp.isCreate) {
                val vf = findRelative(baseDir, targetPath)
                if (vf != null && !vf.isDirectory) VfsUtil.loadText(vf) else ""
            } else {
                ""
            }

            val newText = applyHunks(oldText, fp.hunks)

            val file = ensureFile(baseDir, targetPath)
            VfsUtil.saveText(file, newText)
        }
    }

    private fun parseUnifiedDiff(text: String): List<FilePatch> {
        val lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        val out = mutableListOf<FilePatch>()

        var i = 0
        while (i < lines.size) {
            if (!lines[i].startsWith("--- ")) { i++; continue }

            val oldLine = lines[i]; i++
            if (i >= lines.size) break
            val newLine = lines[i]
            if (!newLine.startsWith("+++ ")) continue
            i++

            val oldPathRaw = oldLine.removePrefix("--- ").trim()
            val newPathRaw = newLine.removePrefix("+++ ").trim()

            val isCreate = oldPathRaw == "/dev/null"
            val isDelete = newPathRaw == "/dev/null"

            val hunks = mutableListOf<Hunk>()
            while (i < lines.size) {
                val line = lines[i]
                if (line.startsWith("--- ")) break
                if (!line.startsWith("@@")) { i++; continue }

                val header = line
                val m = Regex("""@@\s*-(\d+)(?:,(\d+))?\s*\+(\d+)(?:,(\d+))?\s*@@""").find(header)
                val oldStart = m?.groupValues?.get(1)?.toIntOrNull() ?: 1
                val oldCount = m?.groupValues?.get(2)?.toIntOrNull() ?: 1
                val newStart = m?.groupValues?.get(3)?.toIntOrNull() ?: 1
                val newCount = m?.groupValues?.get(4)?.toIntOrNull() ?: 1
                i++

                val hunkLines = mutableListOf<String>()
                while (i < lines.size) {
                    val l = lines[i]
                    if (l.startsWith("@@") || l.startsWith("--- ")) break
                    hunkLines += l
                    i++
                }

                hunks += Hunk(oldStart, oldCount, newStart, newCount, hunkLines)
            }

            out += FilePatch(
                oldPath = if (isCreate) null else oldPathRaw,
                newPath = if (isDelete) null else newPathRaw,
                isCreate = isCreate,
                isDelete = isDelete,
                hunks = hunks
            )
        }

        return out
    }

    private fun applyHunks(oldText: String, hunks: List<Hunk>): String {
        val src = oldText.replace("\r\n", "\n").replace("\r", "\n").split("\n").toMutableList()
        val dst = mutableListOf<String>()

        var srcIndex = 0
        for (h in hunks) {
            val hunkStartIndex = (h.oldStart.coerceAtLeast(1) - 1).coerceAtMost(src.size)

            while (srcIndex < hunkStartIndex) {
                dst += src[srcIndex]
                srcIndex++
            }

            for (dl in h.lines) {
                if (dl.startsWith("\\ No newline at end of file")) {
                    continue
                }
                if (dl.isEmpty()) {
                    // 空行也可能是上下文行（以 ' ' 开头），这里必须按首字符判断
                }
                val tag = dl.firstOrNull()
                val payload = if (dl.isNotEmpty()) dl.substring(1) else ""

                when (tag) {
                    ' ' -> {
                        if (srcIndex < src.size) {
                            dst += src[srcIndex]
                            srcIndex++
                        } else {
                            dst += payload
                        }
                    }
                    '-' -> {
                        if (srcIndex < src.size) srcIndex++
                    }
                    '+' -> {
                        dst += payload
                    }
                    else -> {
                        // 未知行，按原样加入，避免丢信息
                        dst += dl
                    }
                }
            }
        }

        while (srcIndex < src.size) {
            dst += src[srcIndex]
            srcIndex++
        }

        // 统一以 \n 结尾（更符合补丁预期）
        return dst.joinToString("\n").let { if (it.endsWith("\n")) it else it + "\n" }
    }

    private fun normalizePatchPath(p: String): String {
        var s = p.trim()

        // 常见 unified diff 路径：a/xxx、b/xxx
        if (s.startsWith("a/")) s = s.substring(2)
        if (s.startsWith("b/")) s = s.substring(2)

        s = s.replace("\\", "/")
        while (s.startsWith("/")) s = s.substring(1)

        val parts = s.split("/").filter { it.isNotBlank() }
        if (parts.any { it == ".." }) return ""
        return parts.joinToString("/")
    }

    private fun findRelative(base: VirtualFile, rel: String): VirtualFile? {
        val parts = rel.split("/").filter { it.isNotBlank() }
        return VfsUtil.findRelativeFile(base, *parts.toTypedArray())
    }

    private fun ensureFile(base: VirtualFile, rel: String): VirtualFile {
        val parts = rel.split("/").filter { it.isNotBlank() }
        val dirParts = parts.dropLast(1)
        val name = parts.last()

        var cur = base
        for (p in dirParts) {
            val existing = cur.findChild(p)
            cur = if (existing != null && existing.isDirectory) {
                existing
            } else {
                cur.createChildDirectory(this, p)
            }
        }

        val existingFile = cur.findChild(name)
        if (existingFile != null && !existingFile.isDirectory) return existingFile
        return cur.createChildData(this, name)
    }
}

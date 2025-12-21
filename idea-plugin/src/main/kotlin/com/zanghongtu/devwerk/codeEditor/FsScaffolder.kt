package com.zanghongtu.devwerk.codeEditor

import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VfsUtil
import com.intellij.openapi.vfs.VirtualFile
import java.io.IOException

object FsScaffolder {

    fun applyFileOps(project: Project, ops: List<FileOp>) {
        if (ops.isEmpty()) return

        val basePath = project.basePath ?: return
        val baseDir = LocalFileSystem.getInstance().findFileByPath(basePath) ?: return

        WriteCommandAction.runWriteCommandAction(project) {
            for (fileOp in ops) {
                applySingleOp(baseDir, fileOp)
            }
        }
    }

    private fun applySingleOp(baseDir: VirtualFile, op: FileOp) {
        val normalizedPath = normalizeRelPath(op.path)
        if (normalizedPath.isEmpty()) return

        when (op.op) {
            // ---- 后端标准 ----
            "create_dir" -> ensureDir(baseDir, normalizedPath)

            "create_file", "update_file" -> {
                val file = ensureFile(baseDir, normalizedPath)
                val text = op.content ?: ""
                VfsUtil.saveText(file, text)
            }

            "delete_path" -> {
                val vf = findRelative(baseDir, normalizedPath)
                if (vf != null) {
                    vf.delete(this)
                    return
                }
                // 找不到就忽略
            }

            // ---- 旧兼容：你插件原来的 op ----
            "delete_dir" -> {
                val vf = findRelative(baseDir, normalizedPath)
                if (vf != null && vf.isDirectory && vf != baseDir) vf.delete(this)
            }

            "modify_file" -> {
                val file = ensureFile(baseDir, normalizedPath)
                val text = op.content ?: ""
                VfsUtil.saveText(file, text)
            }

            "delete_file" -> {
                val vf = findRelative(baseDir, normalizedPath)
                if (vf != null && !vf.isDirectory) vf.delete(this)
            }

            else -> {
                // unknown op -> ignore
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

    private fun findRelative(base: VirtualFile, rel: String): VirtualFile? {
        val parts = rel.split("/").filter { it.isNotBlank() }
        return VfsUtil.findRelativeFile(base, *parts.toTypedArray())
    }

    private fun ensureDir(base: VirtualFile, relDir: String): VirtualFile {
        val parts = relDir.split("/").filter { it.isNotBlank() }
        var cur = base
        for (p in parts) {
            val existing = cur.findChild(p)
            cur = if (existing != null && existing.isDirectory) {
                existing
            } else {
                cur.createChildDirectory(this, p)
            }
        }
        return cur
    }

    private fun ensureFile(base: VirtualFile, relFile: String): VirtualFile {
        val parts = relFile.split("/").filter { it.isNotBlank() }
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

    private fun createFileWithContent(dir: VirtualFile, fileName: String, content: String): VirtualFile {
        val existing = dir.findChild(fileName)
        val file = existing ?: dir.createChildData(this, fileName)
        try {
            VfsUtil.saveText(file, content)
        } catch (e: IOException) {
            throw RuntimeException("Failed to write file ${file.path}: ${e.message}", e)
        }
        return file
    }
}

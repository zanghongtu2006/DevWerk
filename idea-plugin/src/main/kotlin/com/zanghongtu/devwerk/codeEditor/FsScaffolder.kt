package com.zanghongtu.devwerk.codeEditor

import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VfsUtil
import com.intellij.openapi.vfs.VirtualFile
import java.io.IOException

object FsScaffolder {

    private data class CodeNode(
        val name: String,
        val isDir: Boolean,
        val children: MutableList<CodeNode> = mutableListOf()
    )

    private fun parseCodeTree(text: String): List<CodeNode> {
        val roots = mutableListOf<CodeNode>()
        val stack = mutableListOf<Pair<Int, CodeNode>>() // level -> node

        for (rawLine in text.lines()) {
            if (rawLine.isBlank()) continue
            val line = rawLine.rstrip()
            val trimmed = line.trimStart()
            if (trimmed.isBlank()) continue
            if (trimmed.startsWith("===")) continue

            var firstNonWs = line.indexOfFirst { !it.isWhitespace() }
            if (firstNonWs < 0) firstNonWs = 0
            val level = firstNonWs / 2

            var token = trimmed
            if (token.startsWith("📁 ") || token.startsWith("📄 ")) {
                token = token.substring(2).trimStart()
            }

            val isDir = token.endsWith("/")
            val name = if (isDir) token.removeSuffix("/") else token
            if (name.isBlank()) continue

            val node = CodeNode(name = name, isDir = isDir)

            while (stack.isNotEmpty() && stack.last().first >= level) {
                stack.removeAt(stack.size - 1)
            }

            if (stack.isEmpty()) {
                roots += node
            } else {
                stack.last().second.children += node
            }

            if (isDir) {
                stack += level to node
            }
        }

        return roots
    }

    private fun String.rstrip(): String = replace(Regex("\\s+$"), "")

    private fun createFromNode(parentDir: VirtualFile, node: CodeNode) {
        if (node.isDir) {
            val dir = createChildDirectory(parentDir, node.name)
            for (child in node.children) {
                createFromNode(dir, child)
            }
        } else {
            createFileWithContent(parentDir, node.name, "")
        }
    }

    // ========== 新增：应用结构化 FileOp 指令 ==========

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
        val normalizedPath = op.path.trim().trimStart('/', '\\')
        if (normalizedPath.isEmpty()) return

        val parts = normalizedPath.split("/", "\\").filter { it.isNotBlank() }
        if (parts.isEmpty()) return

        val isDirOp = op.op == "create_dir" || op.op == "delete_dir"

        val dirPathParts: List<String>
        val fileName: String?

        if (isDirOp) {
            dirPathParts = parts
            fileName = null
        } else {
            dirPathParts = parts.dropLast(1)
            fileName = parts.last()
        }

        var current: VirtualFile = baseDir
        for (part in dirPathParts) {
            var child = current.findChild(part)
            if (child == null && op.op != "delete_dir" && op.op != "delete_file") {
                child = current.createChildDirectory(this, part)
            }
            if (child == null) {
                // 删除时找不到就当作成功跳过
                return
            }
            current = child
        }

        when (op.op) {
            "create_dir" -> {
                // 目录已经通过上面的循环创建/获取，无需额外操作
            }
            "delete_dir" -> {
                if (current != baseDir) {
                    current.delete(this)
                }
            }
            "create_file", "modify_file" -> {
                val name = fileName ?: return
                val file = current.findChild(name) ?: current.createChildData(this, name)
                val text = op.content ?: ""
                VfsUtil.saveText(file, text)
            }
            "delete_file" -> {
                val name = fileName ?: return
                val file = current.findChild(name) ?: return
                file.delete(this)
            }
            else -> {
                // 未知 op：先忽略
            }
        }
    }

    // ========== 基础工具方法 ==========

    private fun createChildDirectory(parent: VirtualFile, name: String): VirtualFile {
        val existing = parent.findChild(name)
        if (existing != null && existing.isDirectory) return existing
        return parent.createChildDirectory(this, name)
    }

    private fun createNestedDirs(parent: VirtualFile, path: String): VirtualFile {
        var current = parent
        val parts = path.split("/", "\\").filter { it.isNotBlank() }
        for (part in parts) {
            var child = current.findChild(part)
            if (child == null) {
                child = current.createChildDirectory(this, part)
            }
            current = child
        }
        return current
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

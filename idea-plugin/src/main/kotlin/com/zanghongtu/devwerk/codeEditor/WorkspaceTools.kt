package com.zanghongtu.devwerk.codeEditor

import java.io.File
import java.nio.charset.Charset

object WorkspaceTools {

    fun listDir(basePath: String, relativePath: String, maxDepth: Int = 2): String {
        val target = File(basePath, normalizeRel(relativePath))
        if (!target.exists()) return "[list_dir] not found: $relativePath"
        if (!target.isDirectory) return "[list_dir] not a directory: $relativePath"

        val sb = StringBuilder()
        sb.append(target.name.ifBlank { "." }).append("/\n")
        walkDir(target, sb, "", 0, maxDepth)
        return sb.toString().trimEnd()
    }

    private fun walkDir(dir: File, sb: StringBuilder, indent: String, depth: Int, maxDepth: Int) {
        if (depth >= maxDepth) return
        val children = dir.listFiles()?.sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() })) ?: return
        for (c in children) {
            val name = c.name
            if (c.isDirectory) {
                sb.append(indent).append("  ").append(name).append("/\n")
                walkDir(c, sb, indent + "  ", depth + 1, maxDepth)
            } else {
                sb.append(indent).append("  ").append(name).append("\n")
            }
        }
    }

    fun readFile(basePath: String, relativePath: String, startLine: Int, endLine: Int): String {
        val file = File(basePath, normalizeRel(relativePath))
        if (!file.exists()) return "[read_file] not found: $relativePath"
        if (file.isDirectory) return "[read_file] is a directory: $relativePath"

        val lines = file.readLinesSafe()
        val s = (startLine.coerceAtLeast(1) - 1).coerceAtMost(lines.size)
        val e = endLine.coerceAtLeast(startLine).coerceAtMost(lines.size)
        val slice = lines.subList(s, e)

        val header = "FILE: $relativePath (lines ${startLine}-${endLine})"
        return buildString {
            append(header).append("\n")
            append(slice.joinToString("\n"))
        }
    }

    fun search(basePath: String, query: String, paths: List<String>, maxResults: Int = 50): String {
        val q = query.trim()
        if (q.isBlank()) return "[search] empty query"

        val roots = if (paths.isEmpty()) listOf("src/", "app/") else paths
        val results = mutableListOf<String>()

        // ✅ 如果 query 看起来像“文件名”，就走文件名精确匹配
        val filenameMode = looksLikeFileNameQuery(q)

        for (p in roots) {
            val root = File(basePath, normalizeRel(p))
            if (!root.exists()) continue

            scanFiles(root) { f ->
                if (results.size >= maxResults) return@scanFiles false
                if (!f.isFile) return@scanFiles true

                // 跳过超大文件
                if (!filenameMode && f.length() > 1_000_000) return@scanFiles true

                val hit = if (filenameMode) {
                    // ✅ 文件名精确匹配（Windows 下不区分大小写更符合直觉）
                    f.name.equals(q, ignoreCase = true)
                } else {
                    // ✅ 内容匹配（你原来的行为）
                    val text = runCatching { f.readText(Charset.forName("UTF-8")) }.getOrNull() ?: return@scanFiles true
                    text.contains(q, ignoreCase = true)
                }

                if (hit) {
                    val rel = f.absolutePath.replace("\\", "/")
                    results += rel.substringAfter(basePath.replace("\\", "/") + "/")
                }
                true
            }

            if (results.size >= maxResults) break
        }

        if (results.isEmpty()) return "[search] no hits"
        return results.joinToString("\n")
    }

    private fun looksLikeFileNameQuery(q: String): Boolean {
        // 不含路径分隔符，且像一个文件名（带扩展名）
        if (q.contains("/") || q.contains("\\") || q.contains("\n") || q.contains("\t")) return false
        if (!q.contains(".")) return false
        // 常见源码/配置文件扩展名：你也可以按需加
        val lower = q.lowercase()
        return lower.endsWith(".java") ||
                lower.endsWith(".kt") ||
                lower.endsWith(".xml") ||
                lower.endsWith(".yml") ||
                lower.endsWith(".yaml") ||
                lower.endsWith(".gradle") ||
                lower.endsWith(".properties") ||
                lower.endsWith(".json")
    }

    private fun scanFiles(root: File, onFile: (File) -> Boolean) {
        val stack = ArrayDeque<File>()
        stack.add(root)
        while (stack.isNotEmpty()) {
            val cur = stack.removeLast()
            val ok = onFile(cur)
            if (!ok) return
            if (cur.isDirectory) {
                val children = cur.listFiles() ?: continue
                for (c in children) {
                    // 简单过滤一些常见目录
                    val n = c.name.lowercase()
                    if (c.isDirectory && (n == ".git" || n == ".idea" || n == "build" || n == "out" || n == "node_modules")) {
                        continue
                    }
                    stack.add(c)
                }
            }
        }
    }

    private fun normalizeRel(p: String): String {
        var s = p.trim().replace("\\", "/")
        while (s.startsWith("/")) s = s.substring(1)
        // 不允许 ..
        val parts = s.split("/").filter { it.isNotBlank() }
        if (parts.any { it == ".." }) {
            return ""
        }
        return parts.joinToString("/")
    }

    private fun File.readLinesSafe(): List<String> {
        return runCatching { this.readLines(Charset.forName("UTF-8")) }
            .getOrElse { emptyList() }
    }
}

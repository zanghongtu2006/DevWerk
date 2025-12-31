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
        val children = dir.listFiles()
            ?.sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
            ?: return

        for (c in children) {
            val name = c.name

            //  关键：隐藏目录（.devwerk/.idea/.git 等）不写入 tree_preview
            if (c.isDirectory && name.startsWith(".")) {
                continue
            }

            if (c.isDirectory) {
                sb.append(indent).append("  ").append(name).append("/\n")
                walkDir(c, sb, indent + "  ", depth + 1, maxDepth)
            } else {
                sb.append(indent).append("  ").append(name).append("\n")
            }
        }
    }

    fun readFile(basePath: String, relativePath: String, startLine: Int, endLine: Int): String {
        val rel = normalizeRel(relativePath)

        //  关键：禁止 read_file 读取隐藏目录内部文件（允许 ".gitignore" 这种顶层隐藏文件）
        if (hasHiddenDirSegment(rel)) {
            return "[read_file] blocked hidden directory path: $relativePath"
        }

        val file = File(basePath, rel)
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

        //  如果 caller 传 [""]，我们就把它当成“从项目根开始搜”
        val roots = if (paths.isEmpty()) listOf("src/", "app/") else paths

        //  关键：过滤掉隐藏目录入口（允许 "" / "." 表示根）
        val safeRoots = roots
            .map { normalizeRel(it) }
            .filter { it.isBlank() || it == "." || !containsHiddenSegment(it) }

        val results = mutableListOf<String>()

        // 如果 query 看起来像“文件名”，就走文件名精确匹配
        val filenameMode = looksLikeFileNameQuery(q)

        for (p in safeRoots) {
            val root = if (p.isBlank() || p == ".") File(basePath) else File(basePath, p)
            if (!root.exists()) continue

            scanFiles(root) { f ->
                if (results.size >= maxResults) return@scanFiles false
                if (!f.isFile) return@scanFiles true

                // 生成相对路径
                val rel = f.absolutePath.replace("\\", "/")
                    .substringAfter(basePath.replace("\\", "/") + "/")

                //  关键：屏蔽隐藏目录内部文件
                if (hasHiddenDirSegment(rel)) return@scanFiles true

                // 跳过超大文件
                if (!filenameMode && f.length() > 1_000_000) return@scanFiles true

                val hit = if (filenameMode) {
                    f.name.equals(q, ignoreCase = true)
                } else {
                    val text = runCatching { f.readText(Charset.forName("UTF-8")) }
                        .getOrNull() ?: return@scanFiles true
                    text.contains(q, ignoreCase = true)
                }

                if (hit) {
                    results += rel
                }
                true
            }

            if (results.size >= maxResults) break
        }

        if (results.isEmpty()) return "[search] no hits"
        return results.joinToString("\n")
    }

    private fun looksLikeFileNameQuery(q: String): Boolean {
        if (q.contains("/") || q.contains("\\") || q.contains("\n") || q.contains("\t")) return false
        if (!q.contains(".")) return false
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
                    val name = c.name
                    val n = name.lowercase()

                    //  关键：屏蔽所有 "." 开头的目录（包含 .devwerk）
                    if (c.isDirectory && name.startsWith(".")) {
                        continue
                    }

                    // 额外过滤一些常见大目录
                    if (c.isDirectory && (n == "build" || n == "out" || n == "node_modules")) {
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
        val parts = s.split("/").filter { it.isNotBlank() }
        if (parts.any { it == ".." }) return ""
        return parts.joinToString("/")
    }

    /**
     * 任意 segment 以 "." 开头（用于 list_dir/search roots 这类“入口路径”）
     */
    private fun containsHiddenSegment(rel: String): Boolean {
        val parts = rel.trim().replace("\\", "/").split("/").filter { it.isNotBlank() }
        return parts.any { it.startsWith(".") }
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

    private fun File.readLinesSafe(): List<String> {
        return runCatching { this.readLines(Charset.forName("UTF-8")) }
            .getOrElse { emptyList() }
    }
}

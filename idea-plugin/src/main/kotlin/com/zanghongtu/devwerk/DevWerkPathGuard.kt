// src/main/kotlin/com/zanghongtu/devwerk/codeEditor/DevWerkPathGuard.kt
package com.zanghongtu.devwerk.codeEditor

object DevWerkPathGuard {

    /** 统一成 / 分隔，去掉首尾空白 */
    fun norm(rel: String): String =
        rel.trim().replace('\\', '/').removePrefix("/")

    /** 任意 segment 以 "." 开头（用于 list_dir 这种“目录本身”也要拦截的场景） */
    fun containsHiddenSegment(rel: String): Boolean {
        val parts = norm(rel).split('/').filter { it.isNotBlank() }
        return parts.any { it.startsWith(".") }
    }

    /**
     * 只拦截“目录段”是隐藏目录的情况：
     * - 允许 ".gitignore" 这种顶层隐藏文件
     * - 但拦截 ".devwerk/xxx", ".idea/xxx", ".git/xxx"
     */
    fun hasHiddenDirSegment(rel: String): Boolean {
        val parts = norm(rel).split('/').filter { it.isNotBlank() }
        if (parts.size <= 1) return false // 没有父目录段
        return parts.dropLast(1).any { it.startsWith(".") }
    }
}

package com.zanghongtu.devwerk.codeEditor

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.roots.ProjectFileIndex
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.psi.PsiManager
import com.intellij.psi.PsiNamedElement
import com.intellij.psi.PsiRecursiveElementWalkingVisitor

object SourceMapBuilder {
    private const val MAX_INDEXED_FILES = 2_000
    private const val MAX_SYMBOLS_PER_FILE = 120
    private const val MAX_FILE_SIZE = 1_000_000L

    private val indexedExtensions = setOf(
        "java", "kt", "kts", "xml", "gradle", "properties", "yml", "yaml",
        "json", "py", "js", "jsx", "ts", "tsx", "go", "rs", "sql"
    )

    fun build(project: Project, projectRoot: String?): SourceMap? {
        if (projectRoot.isNullOrBlank()) return null

        return ApplicationManager.getApplication().runReadAction<SourceMap?> {
            val basePath = project.basePath ?: return@runReadAction null
            val base = LocalFileSystem.getInstance().findFileByPath(basePath) ?: return@runReadAction null
            val fileIndex = ProjectFileIndex.getInstance(project)
            val psiManager = PsiManager.getInstance(project)
            val files = mutableListOf<SourceMapFile>()
            var totalFiles = 0
            var skippedFiles = 0

            fileIndex.iterateContent { vf ->
                if (vf.isDirectory) return@iterateContent true
                totalFiles++

                val rel = relativePath(base, vf)
                if (rel.isBlank() || hasHiddenDirSegment(rel) || shouldSkip(vf)) {
                    skippedFiles++
                    return@iterateContent true
                }

                if (files.size >= MAX_INDEXED_FILES) {
                    skippedFiles++
                    return@iterateContent true
                }

                val psiFile = psiManager.findFile(vf)
                files += buildGenericEntry(fileIndex, vf, rel, psiFile)
                true
            }

            SourceMap(
                root = project.name,
                generatedAt = System.currentTimeMillis(),
                totalFiles = totalFiles,
                indexedFiles = files.size,
                skippedFiles = skippedFiles,
                files = files.sortedBy { it.path }
            )
        }
    }

    private fun buildGenericEntry(
        fileIndex: ProjectFileIndex,
        vf: VirtualFile,
        rel: String,
        psiFile: com.intellij.psi.PsiFile?
    ): SourceMapFile {
        val symbols = mutableListOf<SourceMapSymbol>()
        if (psiFile != null) {
            psiFile.accept(object : PsiRecursiveElementWalkingVisitor() {
                override fun visitElement(element: com.intellij.psi.PsiElement) {
                    if (symbols.size >= MAX_SYMBOLS_PER_FILE) {
                        stopWalking()
                        return
                    }
                    if (element !== psiFile && element is PsiNamedElement) {
                        val name = element.name
                        if (!name.isNullOrBlank()) {
                            symbols += SourceMapSymbol(
                                name = name,
                                kind = symbolKind(element),
                                signature = name,
                                line = lineOf(vf, element.textOffset)
                            )
                        }
                    }
                    super.visitElement(element)
                }
            })
        }

        return SourceMapFile(
            path = rel,
            kind = contentKind(fileIndex, vf),
            language = languageFor(vf),
            packageName = null,
            imports = emptyList(),
            symbols = symbols,
            size = vf.length
        )
    }

    private fun symbolKind(element: PsiNamedElement): String =
        element.javaClass.simpleName
            .removePrefix("Psi")
            .removeSuffix("Impl")
            .ifBlank { "symbol" }
            .lowercase()

    private fun lineOf(vf: VirtualFile, offset: Int): Int? {
        if (offset < 0) return null
        val doc = FileDocumentManager.getInstance().getDocument(vf) ?: return null
        return doc.getLineNumber(offset) + 1
    }

    private fun contentKind(fileIndex: ProjectFileIndex, vf: VirtualFile): String = when {
        fileIndex.isInTestSourceContent(vf) -> "test"
        fileIndex.isInSourceContent(vf) -> "source"
        else -> "content"
    }

    private fun languageFor(vf: VirtualFile): String? = vf.extension?.lowercase()

    private fun shouldSkip(vf: VirtualFile): Boolean {
        val ext = vf.extension?.lowercase() ?: return true
        if (ext !in indexedExtensions) return true
        if (vf.length > MAX_FILE_SIZE) return true
        return false
    }

    private fun relativePath(base: VirtualFile, vf: VirtualFile): String {
        val basePath = base.path.trimEnd('/')
        val path = vf.path
        if (!path.startsWith("$basePath/")) return ""
        return path.removePrefix("$basePath/").replace("\\", "/")
    }

    private fun hasHiddenDirSegment(rel: String): Boolean {
        val parts = rel.split("/").filter { it.isNotBlank() }
        return parts.size > 1 && parts.dropLast(1).any { it.startsWith(".") }
    }
}

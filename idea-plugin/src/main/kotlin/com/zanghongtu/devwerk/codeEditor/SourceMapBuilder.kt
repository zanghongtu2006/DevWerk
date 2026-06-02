package com.zanghongtu.devwerk.codeEditor

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.roots.ProjectFileIndex
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.psi.PsiClass
import com.intellij.psi.PsiField
import com.intellij.psi.PsiJavaFile
import com.intellij.psi.PsiManager
import com.intellij.psi.PsiMethod
import com.intellij.psi.util.PsiTreeUtil

object SourceMapBuilder {
    private const val MAX_INDEXED_FILES = 1_500
    private const val MAX_IMPORTS_PER_FILE = 80
    private const val MAX_SYMBOLS_PER_FILE = 120
    private const val MAX_FILE_SIZE = 1_000_000L

    private val indexedExtensions = setOf(
        "java", "kt", "kts", "xml", "gradle", "properties", "yml", "yaml",
        "json", "py", "js", "jsx", "ts", "tsx", "go", "rs", "sql"
    )

    fun build(project: Project, projectRoot: String?): SourceMap? {
        if (projectRoot.isNullOrBlank()) return null

        return ApplicationManager.getApplication().runReadAction<SourceMap?> {
            val base = project.baseDir ?: return@runReadAction null
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
                files += if (psiFile is PsiJavaFile) {
                    buildJavaEntry(fileIndex, vf, rel, psiFile)
                } else {
                    SourceMapFile(
                        path = rel,
                        kind = contentKind(fileIndex, vf),
                        language = languageFor(vf),
                        size = vf.length
                    )
                }
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

    private fun buildJavaEntry(
        fileIndex: ProjectFileIndex,
        vf: VirtualFile,
        rel: String,
        psiFile: PsiJavaFile
    ): SourceMapFile {
        val imports = psiFile.importList?.allImportStatements
            ?.mapNotNull { it.importReference?.qualifiedName }
            ?.distinct()
            ?.take(MAX_IMPORTS_PER_FILE)
            ?: emptyList()

        val symbols = mutableListOf<SourceMapSymbol>()
        val classes = PsiTreeUtil.findChildrenOfType(psiFile, PsiClass::class.java)
        for (cls in classes) {
            if (symbols.size >= MAX_SYMBOLS_PER_FILE) break
            symbols += SourceMapSymbol(
                name = cls.qualifiedName ?: cls.name ?: continue,
                kind = classKind(cls),
                signature = cls.name,
                line = lineOf(vf, cls.textOffset)
            )
        }

        val methods = PsiTreeUtil.findChildrenOfType(psiFile, PsiMethod::class.java)
        for (method in methods) {
            if (symbols.size >= MAX_SYMBOLS_PER_FILE) break
            symbols += SourceMapSymbol(
                name = method.containingClass?.qualifiedName?.let { "$it.${method.name}" } ?: method.name,
                kind = if (method.isConstructor) "constructor" else "method",
                signature = methodSignature(method),
                line = lineOf(vf, method.textOffset)
            )
        }

        val fields = PsiTreeUtil.findChildrenOfType(psiFile, PsiField::class.java)
        for (field in fields) {
            if (symbols.size >= MAX_SYMBOLS_PER_FILE) break
            symbols += SourceMapSymbol(
                name = field.containingClass?.qualifiedName?.let { "$it.${field.name}" } ?: field.name,
                kind = "field",
                signature = "${field.type.presentableText} ${field.name}",
                line = lineOf(vf, field.textOffset)
            )
        }

        return SourceMapFile(
            path = rel,
            kind = contentKind(fileIndex, vf),
            language = "java",
            packageName = psiFile.packageName.ifBlank { null },
            imports = imports,
            symbols = symbols,
            size = vf.length
        )
    }

    private fun methodSignature(method: PsiMethod): String {
        val params = method.parameterList.parameters.joinToString(", ") { p ->
            "${p.type.presentableText} ${p.name}"
        }
        val prefix = if (method.isConstructor) method.name else "${method.returnType?.presentableText ?: "void"} ${method.name}"
        return "$prefix($params)"
    }

    private fun classKind(cls: PsiClass): String = when {
        cls.isAnnotationType -> "annotation"
        cls.isEnum -> "enum"
        cls.isInterface -> "interface"
        else -> "class"
    }

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

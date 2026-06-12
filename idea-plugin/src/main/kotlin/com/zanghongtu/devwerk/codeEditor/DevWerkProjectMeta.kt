package com.zanghongtu.devwerk.codeEditor

import com.intellij.openapi.project.Project
import org.json.JSONObject
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.time.Instant
import java.util.UUID

object DevWerkProjectMeta {
    private const val DIR_NAME = ".devwerk"
    private const val META_FILE = "meta"

    @Synchronized
    fun getOrCreateProjectId(project: Project): String {
        val basePath = project.basePath
        if (basePath.isNullOrBlank()) {
            return UUID.randomUUID().toString()
        }

        val metaPath = Paths.get(basePath).resolve(DIR_NAME).resolve(META_FILE)
        readProjectId(metaPath)?.let { return it }

        val projectId = UUID.randomUUID().toString()
        Files.createDirectories(metaPath.parent)
        val payload = JSONObject()
            .put("project_id", projectId)
            .put("created_at", Instant.now().toString())
            .put("schema_version", 1)
            .toString(2)
        Files.writeString(metaPath, payload, StandardCharsets.UTF_8)
        return projectId
    }

    private fun readProjectId(metaPath: Path): String? {
        if (!Files.isRegularFile(metaPath)) return null
        return runCatching {
            val obj = JSONObject(Files.readString(metaPath, StandardCharsets.UTF_8))
            obj.optString("project_id", "").trim().takeIf { it.isNotBlank() }
        }.getOrNull()
    }
}

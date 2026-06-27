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

    data class ActiveTask(
        val taskId: String,
        val statusKey: String? = null,
        val waitingFor: String? = null,
        val updatedAt: String? = null
    )

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

    @Synchronized
    fun getActiveTask(project: Project): ActiveTask? {
        val metaPath = metaPath(project) ?: return null
        if (!Files.isRegularFile(metaPath)) return null
        return runCatching {
            val obj = readMeta(metaPath)
            val active = obj.optJSONObject("active_task") ?: return@runCatching null
            val taskId = active.optString("task_id", "").trim().takeIf { isSafeTaskId(it) } ?: return@runCatching null
            ActiveTask(
                taskId = taskId,
                statusKey = active.optString("status_key", "").trim().takeIf { it.isNotBlank() },
                waitingFor = active.optString("waiting_for", "").trim().takeIf { it.isNotBlank() },
                updatedAt = active.optString("updated_at", "").trim().takeIf { it.isNotBlank() }
            )
        }.getOrNull()
    }

    @Synchronized
    fun saveActiveTask(project: Project, taskId: String, statusKey: String?, waitingFor: String?) {
        if (!isSafeTaskId(taskId)) return
        val metaPath = metaPath(project) ?: return
        Files.createDirectories(metaPath.parent)
        val obj = readMeta(metaPath)
        if (obj.optString("project_id", "").isBlank()) {
            obj.put("project_id", getOrCreateProjectId(project))
        }
        obj.put("schema_version", obj.optInt("schema_version", 1))
        obj.put(
            "active_task",
            JSONObject()
                .put("task_id", taskId)
                .put("status_key", statusKey ?: JSONObject.NULL)
                .put("waiting_for", waitingFor ?: JSONObject.NULL)
                .put("updated_at", Instant.now().toString())
        )
        Files.writeString(metaPath, obj.toString(2), StandardCharsets.UTF_8)
    }

    @Synchronized
    fun clearActiveTask(project: Project, taskId: String? = null) {
        val metaPath = metaPath(project) ?: return
        if (!Files.isRegularFile(metaPath)) return
        val obj = readMeta(metaPath)
        val active = obj.optJSONObject("active_task")
        if (taskId != null && active != null && active.optString("task_id", "") != taskId) return
        obj.remove("active_task")
        obj.put("active_task_cleared_at", Instant.now().toString())
        Files.writeString(metaPath, obj.toString(2), StandardCharsets.UTF_8)
    }

    private fun metaPath(project: Project): Path? {
        val basePath = project.basePath
        if (basePath.isNullOrBlank()) return null
        return Paths.get(basePath).resolve(DIR_NAME).resolve(META_FILE)
    }

    private fun readMeta(metaPath: Path): JSONObject {
        if (!Files.isRegularFile(metaPath)) return JSONObject()
        return runCatching { JSONObject(Files.readString(metaPath, StandardCharsets.UTF_8)) }
            .getOrDefault(JSONObject())
    }

    private fun readProjectId(metaPath: Path): String? {
        if (!Files.isRegularFile(metaPath)) return null
        return runCatching {
            val obj = JSONObject(Files.readString(metaPath, StandardCharsets.UTF_8))
            obj.optString("project_id", "").trim().takeIf { it.isNotBlank() }
        }.getOrNull()
    }

    private fun isSafeTaskId(value: String): Boolean =
        value.trim().matches(Regex("[A-Za-z0-9._-]+"))
}

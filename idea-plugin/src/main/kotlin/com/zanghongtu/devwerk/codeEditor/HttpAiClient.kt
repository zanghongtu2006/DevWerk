package com.zanghongtu.devwerk.codeEditor

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.StandardOpenOption
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit

class HttpAiClient(
    private val chatEndpoint: String,
    private val planEndpoint: String,
    private val executeEndpoint: String,
    private val attachmentEndpoint: String,
    private val authToken: String? = null
) : AiClient {

    private val client = OkHttpClient().newBuilder()
        .proxy(java.net.Proxy.NO_PROXY)
        .connectTimeout(300, TimeUnit.SECONDS)
        .writeTimeout(300, TimeUnit.SECONDS)
        .readTimeout(300, TimeUnit.SECONDS)
        .callTimeout(300, TimeUnit.SECONDS)
        .build()

    // -------------------------------------------------------------------------
    // Public API
    // -------------------------------------------------------------------------

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        return sendChatInternal(context, userMessage, chatEndpoint)
    }

    fun sendPlan(context: ChatContext, userMessage: String): PlanResponse {
        val (mode, cleanMsg) = parseMode(userMessage)
        val messages = buildMessages(context, cleanMsg)
        val workspace = buildWorkspaceSummary(context)

        val respBody = postToServer(
            endpoint = planEndpoint,
            chatContext = context,
            round = 1,
            mode = mode,
            projectRoot = context.projectRoot,
            messages = messages,
            workspace = workspace,
            toolResults = emptyList()
        )

        return parsePlanResponse(respBody)
    }

    fun sendExecute(
        context: ChatContext,
        userMessage: String,
        approvedPaths: List<String>,
        approvedOps: List<FileOp> = emptyList()
    ): IdeChatResponse {
        val (mode, cleanMsg) = parseMode(userMessage)
        val messages = buildMessages(context, cleanMsg)

        val bodyJson = buildExecuteBody(context, messages, mode, approvedPaths, approvedOps)

        appendDevLog(context, "\n===== EXECUTE REQUEST =====\n$bodyJson\n")

        val mediaType = "application/json; charset=utf-8".toMediaType()
        val requestBody = bodyJson.toRequestBody(mediaType)

        val requestBuilder = Request.Builder()
            .url(executeEndpoint)
            .post(requestBody)
            .header("Content-Type", "application/json; charset=utf-8")

        context.projectId?.takeIf { it.isNotBlank() }?.let {
            requestBuilder.header("X-DevWerk-Project-Id", it)
        }
        if (!authToken.isNullOrBlank()) {
            requestBuilder.header("Authorization", "Bearer $authToken")
        }

        val request = requestBuilder.build()

        client.newCall(request).execute().use { response: Response ->
            val respBody = response.body?.string() ?: ""
            appendDevLog(context, "\n===== EXECUTE RESPONSE =====\n$respBody\n")

            if (!response.isSuccessful) {
                throw RuntimeException("HTTP ${response.code} from AI server: $respBody")
            }

            return parseIdeChatResponse(respBody)
        }
    }

    fun uploadAttachment(file: File, projectId: String? = null): UploadedAttachment {
        if (!file.exists() || !file.isFile) {
            throw IllegalArgumentException("Attachment is not a file: ${file.absolutePath}")
        }

        val contentType = runCatching { Files.probeContentType(file.toPath()) }.getOrNull()
            ?: "application/octet-stream"
        val requestBody = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("project_id", projectId ?: "")
            .addFormDataPart(
                name = "file",
                filename = file.name,
                body = file.asRequestBody(contentType.toMediaType())
            )
            .build()

        val requestBuilder = Request.Builder()
            .url(attachmentEndpoint)
            .post(requestBody)

        if (!projectId.isNullOrBlank()) {
            requestBuilder.header("X-DevWerk-Project-Id", projectId)
        }
        if (!authToken.isNullOrBlank()) {
            requestBuilder.header("Authorization", "Bearer $authToken")
        }

        client.newCall(requestBuilder.build()).execute().use { response ->
            val body = response.body?.string() ?: ""
            if (!response.isSuccessful) {
                throw RuntimeException("HTTP ${response.code} from attachment server: $body")
            }

            val obj = JSONObject(body)
            if (!obj.optBoolean("ok", false)) {
                throw RuntimeException(obj.optString("error_message", "Attachment upload failed"))
            }

            return UploadedAttachment(
                id = obj.getString("id"),
                filename = obj.getString("filename"),
                contentType = obj.optString("content_type", contentType),
                size = obj.optLong("size", file.length()),
                localPath = obj.getString("local_path")
            )
        }
    }

    // -------------------------------------------------------------------------
    // Internal helpers
    // -------------------------------------------------------------------------

    private fun sendChatInternal(context: ChatContext, userMessage: String, endpoint: String): IdeChatResponse {
        val (mode, cleanUserMsg) = parseMode(userMessage)
        val messages = buildMessages(context, cleanUserMsg)
        val workspace = buildWorkspaceSummary(context)

        val maxRounds = 6
        var round = 0
        var lastResp: IdeChatResponse? = null
        var pendingToolResults: List<ToolResult> = emptyList()
        val rawResponses = mutableListOf<String>()
        val accOps = mutableListOf<FileOp>()
        val accPatchOps = mutableListOf<PatchOp>()

        while (round < maxRounds) {
            round++

            val respBody = postToServer(
                endpoint = endpoint,
                chatContext = context,
                round = round,
                mode = mode,
                projectRoot = context.projectRoot,
                messages = messages,
                workspace = workspace,
                toolResults = pendingToolResults
            )

            rawResponses += respBody
            val respParsed = parseIdeChatResponse(respBody)
            var resp = respParsed.copy(rawResponses = rawResponses.toList())
            lastResp = resp

            if (resp.toolRequests.isNotEmpty() && mode == "agent") {
                if (resp.ops.isNotEmpty() || resp.patchOps.isNotEmpty()) {
                    accOps += resp.ops
                    accPatchOps += resp.patchOps
                    appendDevLog(context, "\n[WARN] Model returned tool_requests with ops/patch_ops. Accumulating and continuing.\n")
                    resp = resp.copy(ops = emptyList(), patchOps = emptyList())
                }

                messages += ChatMessage("assistant", "tool_requests:\n" + toolRequestsToJson(resp.toolRequests))
                pendingToolResults = executeTools(context.projectRoot, resp.toolRequests)
                continue
            }

            val merged = resp.copy(
                ops = (accOps + resp.ops),
                patchOps = (accPatchOps + resp.patchOps),
                rawResponses = rawResponses.toList()
            )
            return merged
        }

        val fallback = (lastResp ?: IdeChatResponse(reply = "No response", done = true)).copy(rawResponses = rawResponses.toList())
        return fallback.copy(ops = (accOps + fallback.ops), patchOps = (accPatchOps + fallback.patchOps))
    }

    private fun buildMessages(context: ChatContext, newUserMsg: String): MutableList<ChatMessage> {
        val messages = mutableListOf<ChatMessage>()
        messages += context.history
        val last = context.history.lastOrNull()
        val shouldAppend = !(last != null && last.role.equals("user", ignoreCase = true) && last.content == newUserMsg)
        if (shouldAppend) {
            messages += ChatMessage("user", newUserMsg)
        }
        return messages
    }

    private fun parseMode(userMessage: String): Pair<String, String> {
        val t = userMessage.trim()
        return if (t.startsWith("/scaffold", ignoreCase = true)) {
            "scaffold" to t.removePrefix("/scaffold").trim().ifBlank { "scaffold" }
        } else {
            "agent" to t
        }
    }

    private fun buildWorkspaceSummary(context: ChatContext): WorkspaceSummary? {
        val projectRoot = context.projectRoot
        if (projectRoot.isNullOrBlank()) return null
        val preview = runCatching { WorkspaceTools.listDir(projectRoot, "", 6) }.getOrNull()
        val sourceMap = context.project?.let { project ->
            runCatching { SourceMapBuilder.build(project, projectRoot) }.getOrNull()
        }
        return WorkspaceSummary(
            rootId = context.projectId,
            changedFiles = emptyList(),
            openFiles = emptyList(),
            treePreview = preview,
            sourceMap = sourceMap
        )
    }

    private fun appendDevLog(context: ChatContext, text: String) {
        val log = context.devCtx?.opLog ?: return
        runCatching {
            Files.writeString(log, timestampLogText(text), StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND)
        }
    }

    private fun timestampLogText(text: String): String {
        val suffix = if (text.endsWith("\n")) "\n" else ""
        return text.trimEnd('\n').split('\n').joinToString("\n") { line ->
            if (line.isBlank()) line else "${LocalDateTime.now().format(LOG_TIME_FORMAT)} $line"
        } + suffix
    }

    private fun postToServer(
        endpoint: String,
        chatContext: ChatContext,
        round: Int,
        mode: String,
        projectRoot: String?,
        messages: List<ChatMessage>,
        workspace: WorkspaceSummary?,
        toolResults: List<ToolResult>
    ): String {
        val messagesJson = JSONArray()
        for (m in messages) {
            val obj = JSONObject()
            obj.put("role", m.role.lowercase())
            obj.put("content", m.content)
            messagesJson.put(obj)
        }

        val root = JSONObject()
        root.put("project_id", chatContext.projectId ?: JSONObject.NULL)
        root.put("task_id", chatContext.taskId ?: JSONObject.NULL)
        root.put("mode", mode)
        root.put("project_root", projectRoot ?: JSONObject.NULL)
        root.put("messages", messagesJson)

        if (workspace != null) {
            val w = JSONObject()
            w.put("root_id", workspace.rootId ?: chatContext.projectId ?: JSONObject.NULL)
            val changed = JSONArray()
            for (f in workspace.changedFiles) {
                val fo = JSONObject()
                fo.put("path", f.path)
                fo.put("sha1", f.sha1 ?: JSONObject.NULL)
                fo.put("size", f.size ?: JSONObject.NULL)
                changed.put(fo)
            }
            w.put("changed_files", changed)
            val open = JSONArray()
            for (p in workspace.openFiles) open.put(p)
            w.put("open_files", open)
            w.put("tree_preview", workspace.treePreview ?: JSONObject.NULL)
            w.put("source_map", sourceMapToJson(workspace.sourceMap))
            root.put("workspace", w)
        } else {
            root.put("workspace", JSONObject.NULL)
        }

        val trArr = JSONArray()
        for (tr in toolResults) {
            val o = JSONObject()
            o.put("id", tr.id); o.put("ok", tr.ok)
            o.put("content", tr.content ?: JSONObject.NULL)
            o.put("error", tr.error ?: JSONObject.NULL)
            trArr.put(o)
        }
        root.put("tool_results", trArr)

        val bodyJson = root.toString()
        appendDevLog(chatContext, "\n===== ROUND $round REQUEST ($endpoint) =====\n$bodyJson\n")

        val mediaType = "application/json; charset=utf-8".toMediaType()
        val requestBody = bodyJson.toRequestBody(mediaType)

        val requestBuilder = Request.Builder()
            .url(endpoint)
            .post(requestBody)
            .header("Content-Type", "application/json; charset=utf-8")

        chatContext.projectId?.takeIf { it.isNotBlank() }?.let {
            requestBuilder.header("X-DevWerk-Project-Id", it)
        }
        if (!authToken.isNullOrBlank()) {
            requestBuilder.header("Authorization", "Bearer $authToken")
        }

        val request = requestBuilder.build()

        client.newCall(request).execute().use { response: Response ->
            val respBody = response.body?.string() ?: ""
            appendDevLog(chatContext, "\n===== ROUND $round RESPONSE =====\n$respBody\n")

            if (!response.isSuccessful) {
                throw RuntimeException("HTTP ${response.code} from AI server: $respBody")
            }
            return respBody
        }
    }

    private fun buildExecuteBody(
        context: ChatContext,
        messages: List<ChatMessage>,
        mode: String,
        approvedPaths: List<String>,
        approvedOps: List<FileOp>
    ): String {
        val messagesJson = JSONArray()
        for (m in messages) {
            val obj = JSONObject()
            obj.put("role", m.role.lowercase())
            obj.put("content", m.content)
            messagesJson.put(obj)
        }

        val opsJson = JSONArray()
        for (op in approvedOps) {
            val o = JSONObject()
            o.put("op", op.op); o.put("path", op.path)
            op.language?.let { o.put("language", it) }
            op.content?.let { o.put("content", it) }
            opsJson.put(o)
        }

        val root = JSONObject()
        root.put("project_id", context.projectId ?: JSONObject.NULL)
        root.put("task_id", context.taskId ?: JSONObject.NULL)
        root.put("messages", messagesJson)
        root.put("mode", mode)
        root.put("project_root", context.projectRoot ?: JSONObject.NULL)
        root.put("approved_paths", JSONArray(approvedPaths))
        root.put("approved_ops", opsJson)
        buildWorkspaceSummary(context)?.let { workspace ->
            val w = JSONObject()
            w.put("root_id", workspace.rootId ?: context.projectId ?: JSONObject.NULL)
            w.put("changed_files", JSONArray())
            w.put("open_files", JSONArray(workspace.openFiles))
            w.put("tree_preview", workspace.treePreview ?: JSONObject.NULL)
            w.put("source_map", sourceMapToJson(workspace.sourceMap))
            root.put("workspace", w)
        } ?: root.put("workspace", JSONObject.NULL)

        return root.toString()
    }

    private fun sourceMapToJson(sourceMap: SourceMap?): Any {
        if (sourceMap == null) return JSONObject.NULL

        val root = JSONObject()
        root.put("root", sourceMap.root)
        root.put("generated_at", sourceMap.generatedAt)
        root.put("total_files", sourceMap.totalFiles)
        root.put("indexed_files", sourceMap.indexedFiles)
        root.put("skipped_files", sourceMap.skippedFiles)

        val files = JSONArray()
        for (f in sourceMap.files) {
            val fo = JSONObject()
            fo.put("path", f.path)
            fo.put("kind", f.kind)
            fo.put("language", f.language ?: JSONObject.NULL)
            fo.put("package", f.packageName ?: JSONObject.NULL)
            fo.put("size", f.size)

            val imports = JSONArray()
            for (imp in f.imports) imports.put(imp)
            fo.put("imports", imports)

            val symbols = JSONArray()
            for (s in f.symbols) {
                val so = JSONObject()
                so.put("name", s.name)
                so.put("kind", s.kind)
                so.put("signature", s.signature ?: JSONObject.NULL)
                so.put("line", s.line ?: JSONObject.NULL)
                symbols.put(so)
            }
            fo.put("symbols", symbols)
            files.put(fo)
        }
        root.put("files", files)
        return root
    }

    private fun toolRequestsToJson(reqs: List<ToolRequest>): String {
        val arr = JSONArray()
        for (r in reqs) {
            val o = JSONObject()
            o.put("id", r.id); o.put("tool", r.tool)
            val args = JSONObject()
            for ((k, v) in r.args) {
                args.put(k, v ?: JSONObject.NULL)
            }
            o.put("args", args)
            arr.put(o)
        }
        return arr.toString()
    }

    // -------------------------------------------------------------------------
    // Hidden-dir guard helpers
    // -------------------------------------------------------------------------

    private fun normRel(p: String): String {
        var s = (p ?: "").trim().replace("\\", "/")
        while (s.startsWith("/")) s = s.substring(1)
        val parts = s.split("/").filter { it.isNotBlank() }
        return if (parts.any { it == ".." }) "" else parts.joinToString("/")
    }

    private fun containsHiddenSegment(rel: String): Boolean =
        normRel(rel).split("/").filter { it.isNotBlank() }.any { it.startsWith(".") }

    private fun hasHiddenDirSegment(rel: String): Boolean {
        val parts = normRel(rel).split("/").filter { it.isNotBlank() }
        return parts.size > 1 && parts.dropLast(1).any { it.startsWith(".") }
    }

    private fun executeTools(projectRoot: String?, reqs: List<ToolRequest>): List<ToolResult> {
        val base = projectRoot
        if (base.isNullOrBlank()) {
            return reqs.map { ToolResult(id = it.id, ok = false, error = "project_root is null") }
        }

        val results = mutableListOf<ToolResult>()
        for (r in reqs) {
            val id = r.id
            try {
                when (r.tool) {
                    "list_dir" -> {
                        val path = (r.args["path"] as? String) ?: ""
                        val rel = normRel(path)
                        if (rel.isNotBlank() && containsHiddenSegment(rel)) {
                            results += ToolResult(id = id, ok = false, error = "blocked hidden directory path: $rel")
                            continue
                        }
                        val maxDepth = (r.args["max_depth"] as? Number)?.toInt() ?: 2
                        val content = WorkspaceTools.listDir(base, rel, maxDepth)
                        results += ToolResult(id = id, ok = true, content = content)
                    }
                    "read_file" -> {
                        val path = (r.args["path"] as? String) ?: ""
                        val rel = normRel(path)
                        if (hasHiddenDirSegment(rel)) {
                            results += ToolResult(id = id, ok = false, error = "blocked hidden directory path: $rel")
                            continue
                        }
                        val start = (r.args["start_line"] as? Number)?.toInt() ?: 1
                        val end = (r.args["end_line"] as? Number)?.toInt() ?: (start + 200)
                        val content = WorkspaceTools.readFile(base, rel, start, end)
                        results += ToolResult(id = id, ok = true, content = content)
                    }
                    "search" -> {
                        val query = (r.args["query"] as? String) ?: ""
                        val maxResults = (r.args["max_results"] as? Number)?.toInt() ?: 50
                        val pathsAny = r.args["paths"]
                        val paths: List<String> = when (pathsAny) {
                            is List<*> -> pathsAny.filterIsInstance<String>()
                            is Array<*> -> pathsAny.filterIsInstance<String>()
                            else -> emptyList()
                        }
                        val safePaths = paths.map { normRel(it) }.filter { it.isBlank() || !containsHiddenSegment(it) }
                        val content = WorkspaceTools.search(base, query, safePaths, maxResults)
                        results += ToolResult(id = id, ok = true, content = content)
                    }
                    else -> results += ToolResult(id = id, ok = false, error = "unknown tool: ${r.tool}")
                }
            } catch (t: Throwable) {
                results += ToolResult(id = id, ok = false, error = "${typeName(t)}: ${t.message}")
            }
        }
        return results
    }

    private fun typeName(t: Throwable): String = t::class.java.simpleName.ifBlank { "Throwable" }

    companion object {
        private val LOG_TIME_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS")
    }

    // -------------------------------------------------------------------------
    // Response parsers
    // -------------------------------------------------------------------------

    private fun parsePlanResponse(body: String): PlanResponse {
        return try {
            val obj = JSONObject(body)
            val ok = obj.optBoolean("ok", true)
            val taskId = if (obj.has("task_id") && !obj.isNull("task_id")) obj.getString("task_id") else null
            val statusKey = if (obj.has("status_key") && !obj.isNull("status_key")) obj.getString("status_key") else null
            val errorCode = if (obj.has("error_code") && !obj.isNull("error_code")) obj.getString("error_code") else null
            val errorMessage = if (obj.has("error_message") && !obj.isNull("error_message")) obj.getString("error_message") else null

            if (!ok) {
                return PlanResponse(
                    ok = false,
                    taskId = taskId,
                    statusKey = statusKey,
                    errorCode = errorCode,
                    errorMessage = errorMessage
                )
            }

            val filesArr = obj.optJSONArray("files") ?: JSONArray()
            val files = (0 until filesArr.length()).mapNotNull { i ->
                val item = filesArr.optJSONObject(i) ?: return@mapNotNull null
                val path = item.optString("path", "").trim()
                if (path.isBlank()) return@mapNotNull null
                PlanFile(
                    path = path,
                    nature = item.optString("nature", "modified").trim(),
                    description = item.optString("description", "").trim(),
                    confidence = item.optDouble("confidence", 0.8)
                )
            }

            val summary = obj.optString("summary", "").trim()
            val warningsArr = obj.optJSONArray("warnings") ?: JSONArray()
            val warnings = (0 until warningsArr.length()).mapNotNull {
                warningsArr.optString(it, "").trim().takeIf { w -> w.isNotBlank() }
            }

            PlanResponse(ok = true, taskId = taskId, statusKey = statusKey, files = files, summary = summary, warnings = warnings)
        } catch (t: Throwable) {
            PlanResponse(ok = false, errorCode = "PARSE_ERROR", errorMessage = "${typeName(t)}: ${t.message}")
        }
    }

    private fun parseIdeChatResponse(body: String): IdeChatResponse {
        val obj = JSONObject(body)
        val ok = obj.optBoolean("ok", true)
        val errorCode = if (obj.has("error_code") && !obj.isNull("error_code")) obj.getString("error_code") else null
        val errorMessage = if (obj.has("error_message") && !obj.isNull("error_message")) obj.getString("error_message") else null
        val retryable = obj.optBoolean("retryable", false)

        val reply = obj.optString("reply", "")
        val taskId = if (obj.has("task_id") && !obj.isNull("task_id")) obj.getString("task_id") else null
        val statusKey = if (obj.has("status_key") && !obj.isNull("status_key")) obj.getString("status_key") else null
        val codeTree = if (obj.has("code_tree") && !obj.isNull("code_tree")) obj.getString("code_tree") else null
        val done = obj.optBoolean("done", false)

        val opsArray: JSONArray = obj.optJSONArray("ops") ?: JSONArray()
        val ops = (0 until opsArray.length()).mapNotNull { i ->
            val item = opsArray.optJSONObject(i) ?: return@mapNotNull null
            val opType = item.optString("op", "").trim()
            val path = item.optString("path", "").trim()
            if (opType.isEmpty() || path.isEmpty()) return@mapNotNull null
            FileOp(
                op = opType,
                path = path,
                language = if (item.has("language") && !item.isNull("language")) item.getString("language") else null,
                content = if (item.has("content") && !item.isNull("content")) item.getString("content") else null
            )
        }

        val toolReqArr: JSONArray = obj.optJSONArray("tool_requests") ?: JSONArray()
        val toolReqs = (0 until toolReqArr.length()).mapNotNull { i ->
            val item = toolReqArr.optJSONObject(i) ?: return@mapNotNull null
            val id = item.optString("id", "").trim()
            val tool = item.optString("tool", "").trim()
            if (id.isBlank() || tool.isBlank()) return@mapNotNull null
            val argsObj = item.optJSONObject("args") ?: JSONObject()
            val args = mutableMapOf<String, Any?>()
            for (k in argsObj.keys()) {
                args[k] = argsObj.get(k).let { v -> if (v == JSONObject.NULL) null else v }
            }
            ToolRequest(id = id, tool = tool, args = args)
        }

        val patchArr: JSONArray = obj.optJSONArray("patch_ops") ?: JSONArray()
        val patchOps = (0 until patchArr.length()).mapNotNull { i ->
            val item = patchArr.optJSONObject(i) ?: return@mapNotNull null
            val op = item.optString("op", "").trim()
            val content = item.optString("content", "")
            if (op.isBlank() || content.isBlank()) return@mapNotNull null
            PatchOp(op = op, content = content)
        }

        return IdeChatResponse(
            reply = reply, taskId = taskId, statusKey = statusKey,
            codeTree = codeTree, ops = ops,
            toolRequests = toolReqs, patchOps = patchOps, done = done,
            ok = ok, errorCode = errorCode, errorMessage = errorMessage, retryable = retryable
        )
    }
}

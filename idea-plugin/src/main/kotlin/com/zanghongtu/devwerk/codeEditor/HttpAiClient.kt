package com.zanghongtu.devwerk.codeEditor

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.StandardOpenOption
import java.util.concurrent.TimeUnit

class HttpAiClient(
    private val endpoint: String,      // http://localhost:8000/v1/ide/chat
    private val authToken: String? = null
) : AiClient {

    private val client = OkHttpClient().newBuilder()
        .proxy(java.net.Proxy.NO_PROXY)
        .connectTimeout(300, TimeUnit.SECONDS)
        .writeTimeout(300, TimeUnit.SECONDS)
        .readTimeout(300, TimeUnit.SECONDS)
        .callTimeout(300, TimeUnit.SECONDS)
        .build()

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        val (mode, cleanUserMsg) = parseMode(userMessage)

        val messages = mutableListOf<ChatMessage>()
        messages += context.history

        // 防重复：避免 history 已经包含本次 user 又追加一次
        val last = context.history.lastOrNull()
        val shouldAppendUser =
            !(last != null && last.role.equals("user", ignoreCase = true) && last.content == cleanUserMsg)
        if (shouldAppendUser) {
            messages += ChatMessage("user", cleanUserMsg)
        }

        val workspace = buildWorkspaceSummary(context.projectRoot)

        val maxRounds = 6
        var round = 0
        var lastResp: IdeChatResponse? = null
        var pendingToolResults: List<ToolResult> = emptyList()

        val rawResponses = mutableListOf<String>()

        // 新增：跨轮累积 ops / patchOps，避免被 tool_requests 吞掉
        val accOps = mutableListOf<FileOp>()
        val accPatchOps = mutableListOf<PatchOp>()

        while (round < maxRounds) {
            round++

            val respBody = postToServer(
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

            // 如果模型违规（同一轮给了 tool_requests + ops/patch），先把 ops/patch 记下来
            if (resp.toolRequests.isNotEmpty() && mode == "agent") {
                if (resp.ops.isNotEmpty() || resp.patchOps.isNotEmpty()) {
                    accOps += resp.ops
                    accPatchOps += resp.patchOps

                    appendDevLog(
                        context,
                        "\n[WARN] Model returned tool_requests with ops/patch_ops in the same round. " +
                                "Accumulating ops/patch_ops and continuing tool loop.\n"
                    )

                    // 清空本轮 ops/patch，避免“边问边改”的语义污染后续
                    resp = resp.copy(ops = emptyList(), patchOps = emptyList())
                }

                messages += ChatMessage(
                    "assistant",
                    "tool_requests:\n" + toolRequestsToJson(resp.toolRequests)
                )

                pendingToolResults = executeTools(context.projectRoot, resp.toolRequests)
                continue
            }

            // 最终返回前，把累积的 ops/patch 合并进去
            val merged = resp.copy(
                ops = (accOps + resp.ops),
                patchOps = (accPatchOps + resp.patchOps),
                rawResponses = rawResponses.toList()
            )
            return merged
        }

        val fallback = (lastResp ?: IdeChatResponse(reply = "No response", done = true))
            .copy(rawResponses = rawResponses.toList())

        return fallback.copy(
            ops = (accOps + fallback.ops),
            patchOps = (accPatchOps + fallback.patchOps)
        )
    }

    private fun parseMode(userMessage: String): Pair<String, String> {
        val t = userMessage.trim()
        return if (t.startsWith("/scaffold", ignoreCase = true)) {
            val msg = t.removePrefix("/scaffold").trim().ifBlank { "scaffold" }
            "scaffold" to msg
        } else {
            "agent" to t
        }
    }

    private fun buildWorkspaceSummary(projectRoot: String?): WorkspaceSummary? {
        if (projectRoot.isNullOrBlank()) return null
        //  listDir 已经会屏蔽所有 "." 开头目录，所以 tree_preview 不会泄漏 .devwerk
        val preview = runCatching { WorkspaceTools.listDir(projectRoot, "", 6) }.getOrNull()
        return WorkspaceSummary(
            rootId = null,
            changedFiles = emptyList(),
            openFiles = emptyList(),
            treePreview = preview
        )
    }

    private fun appendDevLog(context: ChatContext, text: String) {
        val log = context.devCtx?.opLog ?: return
        runCatching {
            Files.writeString(
                log,
                text,
                StandardCharsets.UTF_8,
                StandardOpenOption.CREATE,
                StandardOpenOption.APPEND
            )
        }
    }

    private fun postToServer(
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
        root.put("mode", mode)
        root.put("project_root", projectRoot ?: JSONObject.NULL)
        root.put("messages", messagesJson)

        if (workspace != null) {
            val w = JSONObject()
            w.put("root_id", workspace.rootId ?: JSONObject.NULL)

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
            root.put("workspace", w)
        } else {
            root.put("workspace", JSONObject.NULL)
        }

        val trArr = JSONArray()
        for (tr in toolResults) {
            val o = JSONObject()
            o.put("id", tr.id)
            o.put("ok", tr.ok)
            o.put("content", tr.content ?: JSONObject.NULL)
            o.put("error", tr.error ?: JSONObject.NULL)
            trArr.put(o)
        }
        root.put("tool_results", trArr)

        val bodyJson = root.toString()

        appendDevLog(chatContext, "\n===== ROUND $round REQUEST BEGIN =====\n")
        appendDevLog(chatContext, bodyJson)
        appendDevLog(chatContext, "\n===== ROUND $round REQUEST END =====\n")

        val mediaType = "application/json; charset=utf-8".toMediaType()
        val requestBody = bodyJson.toRequestBody(mediaType)

        val requestBuilder = Request.Builder()
            .url(endpoint)
            .post(requestBody)
            .header("Content-Type", "application/json; charset=utf-8")

        if (!authToken.isNullOrBlank()) {
            requestBuilder.header("Authorization", "Bearer $authToken")
        }

        val request = requestBuilder.build()

        client.newCall(request).execute().use { response: Response ->
            val respBody = response.body?.string() ?: ""

            appendDevLog(chatContext, "\n===== ROUND $round RESPONSE BEGIN =====\n")
            appendDevLog(chatContext, respBody)
            appendDevLog(chatContext, "\n===== ROUND $round RESPONSE END =====\n")

            if (!response.isSuccessful) {
                throw RuntimeException("HTTP ${response.code} from AI server: $respBody")
            }
            return respBody
        }
    }

    private fun toolRequestsToJson(reqs: List<ToolRequest>): String {
        val arr = JSONArray()
        for (r in reqs) {
            val o = JSONObject()
            o.put("id", r.id)
            o.put("tool", r.tool)
            val args = JSONObject()
            for ((k, v) in r.args) {
                args.put(k, v ?: JSONObject.NULL)
            }
            o.put("args", args)
            arr.put(o)
        }
        return arr.toString()
    }

    // -------------------------
    // Hidden-dir guard helpers
    // -------------------------

    private fun normRel(p: String): String {
        var s = (p ?: "").trim().replace("\\", "/")
        while (s.startsWith("/")) s = s.substring(1)
        val parts = s.split("/").filter { it.isNotBlank() }
        if (parts.any { it == ".." }) return ""
        return parts.joinToString("/")
    }

    private fun containsHiddenSegment(rel: String): Boolean {
        val parts = normRel(rel).split("/").filter { it.isNotBlank() }
        return parts.any { it.startsWith(".") }
    }

    private fun hasHiddenDirSegment(rel: String): Boolean {
        val parts = normRel(rel).split("/").filter { it.isNotBlank() }
        if (parts.size <= 1) return false
        return parts.dropLast(1).any { it.startsWith(".") }
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

                        //  拒绝进入隐藏目录（入口只要包含隐藏 segment 就拒绝）
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

                        //  拒绝读取隐藏目录内部文件（允许 ".gitignore" 顶层文件）
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

                        //  过滤隐藏目录入口；允许 "" 表示根
                        val safePaths = paths
                            .map { normRel(it) }
                            .filter { it.isBlank() || !containsHiddenSegment(it) }

                        val content = WorkspaceTools.search(base, query, safePaths, maxResults)
                        results += ToolResult(id = id, ok = true, content = content)
                    }

                    else -> {
                        results += ToolResult(id = id, ok = false, error = "unknown tool: ${r.tool}")
                    }
                }
            } catch (t: Throwable) {
                results += ToolResult(id = id, ok = false, error = "${typeName(t)}: ${t.message}")
            }
        }

        return results
    }

    private fun typeName(t: Throwable): String = t::class.java.simpleName.ifBlank { "Throwable" }

    private fun parseIdeChatResponse(body: String): IdeChatResponse {
        val obj = JSONObject(body)
        val ok = obj.optBoolean("ok", true)
        val errorCode = if (obj.has("error_code") && !obj.isNull("error_code")) obj.getString("error_code") else null
        val errorMessage = if (obj.has("error_message") && !obj.isNull("error_message")) obj.getString("error_message") else null
        val retryable = obj.optBoolean("retryable", false)

        val reply = obj.optString("reply", "")
        val codeTree =
            if (obj.has("code_tree") && !obj.isNull("code_tree")) obj.getString("code_tree") else null

        val done = obj.optBoolean("done", false)

        val opsArray: JSONArray = obj.optJSONArray("ops") ?: JSONArray()
        val ops = mutableListOf<FileOp>()
        for (i in 0 until opsArray.length()) {
            val item = opsArray.optJSONObject(i) ?: continue
            val opType = item.optString("op", "").trim()
            val path = item.optString("path", "").trim()
            if (opType.isEmpty() || path.isEmpty()) continue

            val language =
                if (item.has("language") && !item.isNull("language")) item.getString("language") else null
            val content =
                if (item.has("content") && !item.isNull("content")) item.getString("content") else null

            ops += FileOp(op = opType, path = path, language = language, content = content)
        }

        val toolReqArr: JSONArray = obj.optJSONArray("tool_requests") ?: JSONArray()
        val toolReqs = mutableListOf<ToolRequest>()
        for (i in 0 until toolReqArr.length()) {
            val item = toolReqArr.optJSONObject(i) ?: continue
            val id = item.optString("id", "").trim()
            val tool = item.optString("tool", "").trim()
            val argsObj = item.optJSONObject("args") ?: JSONObject()

            val args = mutableMapOf<String, Any?>()
            for (k in argsObj.keys()) {
                args[k] = argsObj.get(k).let { v -> if (v == JSONObject.NULL) null else v }
            }

            if (id.isNotBlank() && tool.isNotBlank()) {
                toolReqs += ToolRequest(id = id, tool = tool, args = args)
            }
        }

        val patchArr: JSONArray = obj.optJSONArray("patch_ops") ?: JSONArray()
        val patchOps = mutableListOf<PatchOp>()
        for (i in 0 until patchArr.length()) {
            val item = patchArr.optJSONObject(i) ?: continue
            val op = item.optString("op", "").trim()
            val content = item.optString("content", "")
            if (op.isNotBlank() && content.isNotBlank()) {
                patchOps += PatchOp(op = op, content = content)
            }
        }

        return IdeChatResponse(
            reply = reply,
            codeTree = codeTree,
            ops = ops,
            toolRequests = toolReqs,
            patchOps = patchOps,
            done = done,
            ok = ok,
            errorCode = errorCode,
            errorMessage = errorMessage,
            retryable = retryable
        )
    }
}

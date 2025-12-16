package com.zanghongtu.devwerk.codeEditor

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class HttpAiClient(
    private val endpoint: String,      // 比如 http://localhost:8000/v1/ide/chat
    private val authToken: String? = null
) : AiClient {

    private val client = OkHttpClient().newBuilder()
        .proxy(java.net.Proxy.NO_PROXY)
        .connectTimeout(300, TimeUnit.SECONDS)   // 建连超时
        .writeTimeout(300, TimeUnit.SECONDS)     // 发送请求体超时
        .readTimeout(300, TimeUnit.SECONDS)     // 读取响应超时（大模型最常卡这里）
        .callTimeout(300, TimeUnit.SECONDS)     // 整个调用总超时（可选但建议）
        .build()

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        println(">>> okhttp timeouts: connect=${client.connectTimeoutMillis}ms, " +
                "read=${client.readTimeoutMillis}ms, write=${client.writeTimeoutMillis}ms, " +
                "call=${client.callTimeoutMillis}ms")

        // 1. 组装 messages：历史 + 当前
        val allMessages = context.history + ChatMessage("user", userMessage)

        val messagesJson = JSONArray()
        for (msg in allMessages) {
            val obj = JSONObject()
            obj.put("role", msg.role.lowercase())
            obj.put("content", msg.content)
            messagesJson.put(obj)
        }

        // 2. 根对象
        val root = JSONObject()
        root.put("mode", "fs-chat")
        if (context.projectRoot != null) {
            root.put("project_root", context.projectRoot)
        } else {
            root.put("project_root", JSONObject.NULL)
        }
        root.put("messages", messagesJson)

        val bodyJson = root.toString()

        println(">>> endpoint = $endpoint")
        println(">>> request body string length = ${bodyJson.length}")
        println(">>> request body = $bodyJson")

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

            println(">>> response status = ${response.code}")
            println(">>> response body   = $respBody")

            if (!response.isSuccessful) {
                throw RuntimeException("HTTP ${response.code} from AI server: $respBody")
            }

            return parseIdeChatResponse(respBody)
        }

    }
    // ---- JSON 帮助方法 ----

    private fun jsonString(s: String): String {
        val escaped = s
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        return "\"$escaped\""
    }

    private fun parseIdeChatResponse(body: String): IdeChatResponse {
        val obj = JSONObject(body)

        val reply = obj.optString("reply", "")
        val codeTree =
            if (obj.has("code_tree") && !obj.isNull("code_tree")) obj.getString("code_tree")
            else null

        val opsArray: JSONArray = obj.optJSONArray("ops") ?: JSONArray()
        val ops = mutableListOf<FileOp>()
        for (i in 0 until opsArray.length()) {
            val item = opsArray.optJSONObject(i) ?: continue
            val opType = item.optString("op", "").trim()
            val path = item.optString("path", "").trim()
            if (opType.isEmpty() || path.isEmpty()) continue

            val language =
                if (item.has("language") && !item.isNull("language")) item.getString("language")
                else null
            val content =
                if (item.has("content") && !item.isNull("content")) item.getString("content")
                else null

            ops += FileOp(
                op = opType,
                path = path,
                language = language,
                content = content
            )
        }

        return IdeChatResponse(
            reply = reply,
            codeTree = codeTree,
            ops = ops
        )
    }
}

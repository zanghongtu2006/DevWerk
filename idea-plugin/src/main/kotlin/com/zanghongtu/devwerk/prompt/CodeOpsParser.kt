package com.zanghongtu.devwerk.prompt

import com.zanghongtu.devwerk.codeEditor.FileOp
import com.zanghongtu.devwerk.codeEditor.IdeChatResponse
import org.json.JSONArray
import org.json.JSONObject

object CodeOpsParser {

    fun parseToIdeChatResponse(rawModelText: String): IdeChatResponse {
        val jsonText = extractJsonObject(rawModelText)
        val obj = JSONObject(jsonText)

        val reply = obj.optString("reply", "")
        val codeTree = obj.optString("code_tree", "")
        val opsArr = obj.optJSONArray("ops") ?: JSONArray()

        val ops = mutableListOf<FileOp>()
        for (i in 0 until opsArr.length()) {
            val it = opsArr.getJSONObject(i)
            ops += FileOp(
                op = it.getString("op"),
                path = it.getString("path"),
                language = it.opt("language").takeIf { v -> v != JSONObject.NULL } as? String,
                content = it.opt("content").takeIf { v -> v != JSONObject.NULL } as? String
            )
        }

        return IdeChatResponse(
            reply = reply,
            codeTree = codeTree.ifBlank { null },
            ops = ops
        )
    }

    /**
     * 兼容模型偶尔输出 ```json ... ``` 或者前后夹杂文字的情况：
     * 取第一个 '{' 到最后一个 '}'。
     */
    private fun extractJsonObject(text: String): String {
        val s = text.trim()
        val start = s.indexOf('{')
        val end = s.lastIndexOf('}')
        if (start < 0 || end <= start) {
            throw IllegalArgumentException("Model output is not a JSON object: $s")
        }
        return s.substring(start, end + 1)
    }
}

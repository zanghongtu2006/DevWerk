package com.zanghongtu.devwerk.prompt

import java.nio.charset.StandardCharsets

object PromptRepository {

    private fun readResource(path: String): String {
        val stream = PromptRepository::class.java.classLoader.getResourceAsStream(path)
            ?: error("Resource not found: $path")
        return stream.readBytes().toString(StandardCharsets.UTF_8)
    }

    /** 读取 schema JSON 原文（字符串） */
    fun loadSchemaJsonV1(): String {
        return readResource("devwerk/schema/model_response_schema.json").trim()
    }

    /** 读取 system prompt 模板（包含 {schema_json} 占位符） */
    fun loadSystemPromptTemplateZh(): String {
        return readResource("devwerk/prompt/system_prompt.txt").trim()
    }

    /** 生成最终 system prompt（把 schema 注入进去） */
    fun buildSystemPromptV1Zh(): String {
        val tpl = loadSystemPromptTemplateZh()
        val schemaJson = loadSchemaJsonV1()
        return tpl.replace("{schema_json}", schemaJson)
    }
}

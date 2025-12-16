package com.zanghongtu.devwerk.prompt

import java.nio.charset.StandardCharsets

object PromptTemplates {

    private const val PROMPT_PATH = "/devwerk/prompt/system_prompt.txt"
    private const val SCHEMA_PATH = "/devwerk/schema/model_response_schema.json"

    fun codeOpsSystemPrompt(): String {
        val schemaJson = readResourceText(SCHEMA_PATH).trim()
        val promptTpl = readResourceText(PROMPT_PATH)
        return promptTpl.replace("{schema_json}", schemaJson)
    }

    private fun readResourceText(path: String): String {
        val ins = PromptTemplates::class.java.getResourceAsStream(path)
            ?: error("Resource not found: $path")
        return ins.use { String(it.readBytes(), StandardCharsets.UTF_8) }
    }
}

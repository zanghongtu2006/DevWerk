package com.zanghongtu.devwerk

enum class AiProvider(
    val display: String,

    // UI & logic flags
    val needsUrl: Boolean,
    val needsToken: Boolean,
    val supportsModelList: Boolean,

    // defaults
    val defaultUrl: String = "",
    val defaultModel: String = "",
    val tokenPlaceholder: String = ""
) {

    TECH_ZUKUNFT(
        display = "techZukunft",
        needsUrl = false,
        needsToken = false,
        supportsModelList = false,
        defaultUrl = "https://api.techzukunft.ai/v1/ide/chat",
        defaultModel = "tz-devwerk"
    ),

    GPT(
        display = "gpt",
        needsUrl = false,
        needsToken = true,
        supportsModelList = true,
        defaultModel = "gpt-4o-mini",
        tokenPlaceholder = "PLEASE_INPUT_OPENAI_API_KEY"
    ),

    GEMINI(
        display = "gemini",
        needsUrl = false,
        needsToken = true,
        supportsModelList = true,
        defaultModel = "gemini-1.5-pro",
        tokenPlaceholder = "PLEASE_INPUT_GEMINI_API_KEY"
    ),

    OLLAMA(
        display = "ollama",
        needsUrl = true,
        needsToken = false,
        supportsModelList = true,
        defaultUrl = "http://localhost:11434",
        defaultModel = "llama3.1"
    ),

    CUSTOM(
        display = "custom",
        needsUrl = true,
        needsToken = false,   // 你现在设计为 custom 默认不强制 token
        supportsModelList = true,
        defaultUrl = "http://localhost:8000/v1/chat",
        defaultModel = "default"
    );

    companion object {

        /** display -> enum（UI 用） */
        fun fromDisplay(display: String): AiProvider =
            entries.firstOrNull { it.display.equals(display, ignoreCase = true) }
                ?: TECH_ZUKUNFT

        /** name -> enum（存储用） */
        fun fromName(name: String): AiProvider =
            runCatching { valueOf(name) }.getOrElse { TECH_ZUKUNFT }
    }
}

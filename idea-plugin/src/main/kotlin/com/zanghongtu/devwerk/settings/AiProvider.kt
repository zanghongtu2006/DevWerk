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
        display = "TechZukunft",
        needsUrl = false,
        needsToken = true,
        supportsModelList = false,
        defaultUrl = "http://127.0.0.1:8001/v1/ide/chat",
        defaultModel = "tz-devwerk"
    ),

    GPT(
        display = "ChatGPT",
        needsUrl = false,
        needsToken = true,
        supportsModelList = true,
        defaultModel = "gpt-4o-mini",
        tokenPlaceholder = "PLEASE_INPUT_OPENAI_API_KEY"
    ),

    DEEPSEEK(
        display = "DeepSeek",
        needsUrl = false,
        needsToken = true,
        supportsModelList = true,
        defaultUrl = "https://api.deepseek.com/v1",
        defaultModel = "deepseek-chat"
    ),

    QWEN(
        display = "QWEN",
        needsUrl = true,
        needsToken = true,
        supportsModelList = true,
        defaultUrl = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        defaultModel = "qwen-plus"
    ),

    GEMINI(
        display = "GEMINI",
        needsUrl = false,
        needsToken = true,
        supportsModelList = true,
        defaultModel = "gemini-1.5-pro",
        tokenPlaceholder = "PLEASE_INPUT_GEMINI_API_KEY"
    ),

    OLLAMA(
        display = "OLLAMA",
        needsUrl = true,
        needsToken = false,
        supportsModelList = true,
        defaultUrl = "http://localhost:11434",
        defaultModel = "llama3.1"
    ),

    CUSTOM(
        display = "CUSTOM",
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

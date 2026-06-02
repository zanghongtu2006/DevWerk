package com.zanghongtu.devwerk

enum class AiProvider(
    val display: String,

    // defaults
    val defaultUrl: String = "",
    val tokenPlaceholder: String = ""
) {

    TECH_ZUKUNFT(
        display = "DevWerk Backend",
        defaultUrl = "http://127.0.0.1:8000",
        tokenPlaceholder = "API_KEY_OPTIONAL"
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

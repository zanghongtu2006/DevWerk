package com.zanghongtu.devwerk

data class AiProfile(
    var name: String = "TechZukunft",          // ✅ profile 名称（用来做 active key）
    var provider: String = AiProvider.TECH_ZUKUNFT.name,
    var baseUrl: String = "",
    var token: String = "",
    var model: String = ""
)

data class AiSettingsState(
    var active: String = "TechZukunft",        // ✅ 当前激活 profile 的 name
    var profiles: MutableList<AiProfile> = mutableListOf()
)

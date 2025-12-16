package com.zanghongtu.devwerk

import com.intellij.openapi.components.*
import com.intellij.util.xmlb.XmlSerializerUtil

@Service(Service.Level.APP)
@State(
    name = "DevWerkAiSettings",
    storages = [Storage("devwerk-ai-settings.xml")]
)
class AiSettingsService : PersistentStateComponent<AiSettingsState> {

    private var state = AiSettingsState()

    override fun getState(): AiSettingsState = state

    override fun loadState(loaded: AiSettingsState) {
        XmlSerializerUtil.copyBean(loaded, this.state)
        ensureDefaults()
    }

    /** 确保 profiles 非空且 active 指向有效项 */
    private fun ensureDefaults() {
        if (state.profiles.isEmpty()) {
            state.profiles = mutableListOf(
                AiProfile(
                    name = "TechZukunft",
                    provider = AiProvider.TECH_ZUKUNFT.name,
                    baseUrl = "https://api.techzukunft.ai/v1/ide/chat",
                    token = "",
                    model = "tz-devwerk"
                ),
                AiProfile(
                    name = "Ollama",
                    provider = AiProvider.OLLAMA.name,
                    baseUrl = "http://localhost:11434",
                    token = "",
                    model = "llama3.1"
                ),
                AiProfile(
                    name = "GPT",
                    provider = AiProvider.GPT.name,
                    baseUrl = "",
                    token = "",
                    model = "gpt-4o-mini"
                ),
                AiProfile(
                    name = "Gemini",
                    provider = AiProvider.GEMINI.name,
                    baseUrl = "",
                    token = "",
                    model = "gemini-1.5-pro"
                )
            )
        }

        if (state.active.isBlank()) {
            state.active = state.profiles.first().name
        }

        // active 指向不存在的 profile：回退到第一个
        val exists = state.profiles.any { it.name.equals(state.active, ignoreCase = true) }
        if (!exists) {
            state.active = state.profiles.first().name
        }
    }

    fun listProfiles(): List<AiProfile> {
        ensureDefaults()
        return state.profiles
    }

    fun getActiveProfile(): AiProfile {
        ensureDefaults()
        return state.profiles.first { it.name.equals(state.active, ignoreCase = true) }
    }

    fun setActiveProfile(profileName: String) {
        ensureDefaults()
        if (state.profiles.any { it.name.equals(profileName, ignoreCase = true) }) {
            state.active = profileName
        }
    }

    /** 按 name upsert profile，并可选择是否设为 active */
    fun upsertProfile(profile: AiProfile, setActive: Boolean = false) {
        ensureDefaults()
        val idx = state.profiles.indexOfFirst { it.name.equals(profile.name, ignoreCase = true) }
        if (idx >= 0) {
            state.profiles[idx] = profile
        } else {
            state.profiles.add(profile)
        }
        if (setActive) state.active = profile.name
    }

    companion object {
        fun instance(): AiSettingsService =
            com.intellij.openapi.application.ApplicationManager.getApplication()
                .getService(AiSettingsService::class.java)
    }
}

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
        val candidate = state.profiles.firstOrNull {
            it.provider == AiProvider.TECH_ZUKUNFT.name
        } ?: state.profiles.firstOrNull {
            val url = it.baseUrl.lowercase()
            "devwerk" in url || "/v1/ide" in url || url.contains(":8000") || url.contains(":8001")
        }

        val backendProfile = AiProfile(
            name = AiProvider.TECH_ZUKUNFT.display,
            provider = AiProvider.TECH_ZUKUNFT.name,
            baseUrl = candidate?.baseUrl?.takeIf { it.isNotBlank() } ?: AiProvider.TECH_ZUKUNFT.defaultUrl,
            token = candidate?.token.orEmpty(),
            model = ""
        )

        state.profiles = mutableListOf(backendProfile)
        state.active = backendProfile.name
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

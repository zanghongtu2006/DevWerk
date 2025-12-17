package com.zanghongtu.devwerk.settings

import com.intellij.openapi.ui.ComboBox
import com.intellij.openapi.ui.DialogWrapper
import com.intellij.ui.components.JBTextField
import com.intellij.util.ui.JBUI
import com.zanghongtu.devwerk.*
import java.awt.BorderLayout
import java.awt.GridBagConstraints
import java.awt.GridBagLayout
import javax.swing.*

class AiSettingsDialog : DialogWrapper(true) {

    companion object {
        private const val TOKEN_PLACEHOLDER = "PLEASE_INPUT_YOUR_TOKEN"
    }

    // ✅ Provider 是唯一切换入口
    private val providerCombo = ComboBox<String>().apply {
        AiProvider.entries.forEach { addItem(it.display) }
    }

    private val urlField = JBTextField()
    private val tokenField = JBTextField()
    private val modelCombo = ComboBox<String>()
    private val refreshModelsBtn = JButton("Refresh models")

    private lateinit var urlRow: JComponent
    private lateinit var tokenRow: JComponent
    private lateinit var modelRow: JComponent

    // 当前正在编辑的配置（对应 provider）
    private var currentProfile: AiProfile? = null

    init {
        title = "DevWerk AI Settings"

        val svc = AiSettingsService.instance()
        val activeProfile = svc.getActiveProfile()
        currentProfile = activeProfile

        // ✅ 用 activeProfile.provider 选中 providerCombo
        val activeProvider = AiProvider.fromName(activeProfile.provider)
        providerCombo.selectedItem = activeProvider.display

        // 回填 UI
        applyProfileToUi(activeProfile)

        init()
        updateUiByProvider(activeProvider)

        providerCombo.addActionListener { onProviderChanged() }
        refreshModelsBtn.addActionListener { refreshModels() }
    }

    override fun createCenterPanel(): JComponent {
        val root = JPanel(BorderLayout())
        val form = JPanel(GridBagLayout()).apply { border = JBUI.Borders.empty(10) }

        val gc = GridBagConstraints().apply {
            fill = GridBagConstraints.HORIZONTAL
            weightx = 1.0
            gridx = 0
            gridy = 0
        }

        fun row(label: String, comp: JComponent): JComponent {
            val p = JPanel(BorderLayout(10, 0))
            p.add(JLabel(label), BorderLayout.WEST)
            p.add(comp, BorderLayout.CENTER)
            form.add(p, gc)
            gc.gridy++
            return p
        }

        row("Provider:", providerCombo)

        urlRow = row("URL:", urlField)
        tokenRow = row("Token:", tokenField)

        val modelInner = JPanel(BorderLayout(8, 0)).apply {
            add(modelCombo, BorderLayout.CENTER)
            add(refreshModelsBtn, BorderLayout.EAST)
        }
        modelRow = row("Model:", modelInner)

        root.add(form, BorderLayout.CENTER)
        return root
    }

    override fun doOKAction() {
        val svc = AiSettingsService.instance()

        val provider = displayToProvider(providerCombo.selectedItem?.toString().orEmpty())

        // token：占位符不存储
        val tokenInput = tokenField.text.trim()
        val realToken = if (tokenInput == TOKEN_PLACEHOLDER) "" else tokenInput

        // ✅ 取当前 provider 对应的 profile（存在则更新，不存在则创建）
        val p = currentProfile ?: AiProfile(name = provider.display).also { currentProfile = it }

        // 用 provider.display 作为 profile.name（稳定、好读、不会重复）
        p.name = provider.display
        p.provider = provider.name

        when (provider) {
            AiProvider.TECH_ZUKUNFT -> {
                // 你要求：默认无需配置（这里仍写入默认值，方便以后开放）
                p.baseUrl = "https://api.techzukunft.ai/v1/ide/chat"
                p.token = realToken
                p.model = "tz-devwerk"
            }

            AiProvider.GPT -> {
                p.baseUrl = ""              // 云服务不需要 URL
                p.token = realToken
                p.model = modelCombo.selectedItem?.toString().orEmpty().ifBlank { "gpt-4o-mini" }
            }

            AiProvider.GEMINI -> {
                p.baseUrl = ""
                p.token = realToken
                p.model = modelCombo.selectedItem?.toString().orEmpty().ifBlank { "gemini-1.5-pro" }
            }

            AiProvider.OLLAMA -> {
                p.baseUrl = urlField.text.trim().ifBlank { "http://localhost:11434" }
                p.token = ""               // ollama 不需要 token
                p.model = modelCombo.selectedItem?.toString().orEmpty().ifBlank { "llama3.1" }
            }

            AiProvider.CUSTOM -> {
                p.baseUrl = urlField.text.trim()
                p.token = ""               // 你目前设计 custom 不需要 token（以后要开放改这里）
                p.model = modelCombo.selectedItem?.toString().orEmpty()
            }

            AiProvider.DEEPSEEK -> {
                p.baseUrl = ""              // 云服务不需要 URL
                p.token = realToken
                p.model = modelCombo.selectedItem?.toString().orEmpty().ifBlank { "deepseek-v3.2" }
            }

            AiProvider.QWEN -> {
                p.baseUrl = ""              // 云服务不需要 URL
                p.token = realToken
                p.model = modelCombo.selectedItem?.toString().orEmpty().ifBlank { "qwen3-coder" }
            }
        }

        // ✅ 保存并设为 active（全局生效）
        svc.upsertProfile(p, setActive = true)

        super.doOKAction()
    }

    // ---------------- events ----------------

    private fun onProviderChanged() {
        val provider = displayToProvider(providerCombo.selectedItem?.toString().orEmpty())
        val svc = AiSettingsService.instance()

        // 从 service 里找对应 provider 的 profile；如果没有就用默认值生成一个
        val existing = svc.listProfiles().firstOrNull { AiProvider.fromName(it.provider) == provider }
        val p = existing ?: defaultProfileForProvider(provider)

        currentProfile = p
        applyProfileToUi(p)
        updateUiByProvider(provider)
    }

    // ---------------- ui helpers ----------------

    private fun applyProfileToUi(p: AiProfile) {
        val provider = AiProvider.fromName(p.provider)

        urlField.text = p.baseUrl
        tokenField.text = p.token.ifBlank { TOKEN_PLACEHOLDER }

        modelCombo.removeAllItems()
        val modelValue = p.model.ifBlank { provider.defaultModel }
        if (modelValue.isNotBlank()) modelCombo.addItem(modelValue)
        modelCombo.selectedItem = modelValue

        // 默认 URL 补全
        if (provider.needsUrl && urlField.text.isNullOrBlank()) {
            urlField.text = provider.defaultUrl
        }
    }

    private fun updateUiByProvider(provider: AiProvider) {
        urlRow.isVisible = provider.needsUrl
        tokenRow.isVisible = provider.needsToken
        modelRow.isVisible = provider.supportsModelList
        refreshModelsBtn.isVisible = provider.supportsModelList

        tokenField.isEnabled = provider.needsToken

        (rootPane?.contentPane as? JComponent)?.revalidate()
        (rootPane?.contentPane as? JComponent)?.repaint()
    }

    private fun refreshModels() {
        val provider = displayToProvider(providerCombo.selectedItem?.toString().orEmpty())

        val models = when (provider) {
            AiProvider.OLLAMA -> {
                val url = urlField.text.trim().ifBlank { provider.defaultUrl }
                if (url.isBlank()) {
                    JOptionPane.showMessageDialog(null, "Please input Ollama URL (e.g. http://localhost:11434).")
                    return
                }
                OllamaApi.listModels(url)
            }

            AiProvider.GPT -> listOf("gpt-4o-mini", "gpt-4.1-mini", "gpt-4o")
            AiProvider.GEMINI -> listOf("gemini-1.5-flash", "gemini-1.5-pro")
            AiProvider.CUSTOM -> emptyList()
            AiProvider.TECH_ZUKUNFT -> emptyList()
            AiProvider.DEEPSEEK -> listOf("deepseek-v3.2")
            AiProvider.QWEN -> listOf("qwen3-coder")
        }

        if (models.isNotEmpty()) {
            modelCombo.removeAllItems()
            models.forEach { modelCombo.addItem(it) }
            modelCombo.selectedItem = models.first()
        }
    }

    // ---------------- defaults & mapping ----------------

    private fun defaultProfileForProvider(provider: AiProvider): AiProfile {
        return AiProfile(
            name = provider.display,
            provider = provider.name,
            baseUrl = provider.defaultUrl,
            token = "",
            model = provider.defaultModel
        )
    }

    private fun displayToProvider(display: String): AiProvider =
        AiProvider.entries.firstOrNull { it.display.equals(display, ignoreCase = true) }
            ?: AiProvider.TECH_ZUKUNFT
}

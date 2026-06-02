package com.zanghongtu.devwerk.settings

import com.intellij.openapi.ui.DialogWrapper
import com.intellij.ui.components.JBPasswordField
import com.intellij.ui.components.JBTextField
import com.intellij.util.ui.JBUI
import com.zanghongtu.devwerk.AiProfile
import com.zanghongtu.devwerk.AiProvider
import com.zanghongtu.devwerk.AiSettingsService
import java.awt.BorderLayout
import java.awt.GridBagConstraints
import java.awt.GridBagLayout
import javax.swing.JComponent
import javax.swing.JLabel
import javax.swing.JPanel

class AiSettingsDialog : DialogWrapper(true) {

    private val urlField = JBTextField()
    private val apiKeyField = JBPasswordField()

    init {
        title = "DevWerk Backend"

        val profile = AiSettingsService.instance().getActiveProfile()
        urlField.text = profile.baseUrl.ifBlank { AiProvider.TECH_ZUKUNFT.defaultUrl }
        apiKeyField.emptyText.text = AiProvider.TECH_ZUKUNFT.tokenPlaceholder
        if (profile.token.isNotBlank()) {
            apiKeyField.text = profile.token
        }

        init()
    }

    override fun createCenterPanel(): JComponent {
        val root = JPanel(BorderLayout())
        val form = JPanel(GridBagLayout()).apply {
            border = JBUI.Borders.empty(10)
        }

        val gc = GridBagConstraints().apply {
            fill = GridBagConstraints.HORIZONTAL
            weightx = 1.0
            gridx = 0
            gridy = 0
        }

        fun row(label: String, comp: JComponent) {
            val p = JPanel(BorderLayout(10, 0))
            p.add(JLabel(label), BorderLayout.WEST)
            p.add(comp, BorderLayout.CENTER)
            form.add(p, gc)
            gc.gridy++
        }

        row("Backend URL:", urlField)
        row("API Key:", apiKeyField)

        root.add(form, BorderLayout.CENTER)
        return root
    }

    override fun doOKAction() {
        val provider = AiProvider.TECH_ZUKUNFT
        val profile = AiProfile(
            name = provider.display,
            provider = provider.name,
            baseUrl = urlField.text.trim().ifBlank { provider.defaultUrl },
            token = String(apiKeyField.password).trim(),
            model = ""
        )

        AiSettingsService.instance().upsertProfile(profile, setActive = true)
        super.doOKAction()
    }
}

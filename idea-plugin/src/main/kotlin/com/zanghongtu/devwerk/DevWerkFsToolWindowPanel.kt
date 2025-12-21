package com.zanghongtu.devwerk

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.zanghongtu.devwerk.codeEditor.ChatContext
import com.zanghongtu.devwerk.codeEditor.ChatMessage
import com.zanghongtu.devwerk.codeEditor.FsScaffolder
import com.zanghongtu.devwerk.codeEditor.PatchApplier
import com.zanghongtu.devwerk.settings.AiSettingsDialog
import java.awt.BorderLayout
import java.awt.Dimension
import javax.swing.*
import javax.swing.SwingUtilities

class DevWerkFsToolWindowPanel(private val project: Project) : JPanel(BorderLayout()) {

    private val chatArea = JTextArea()
    private val inputField = JTextField()
    private val sendButton = JButton("Send")
    private val settingsButton = JButton("⚙")

    private val history = mutableListOf<ChatMessage>()

    init {
        initUi()
    }

    private fun initUi() {
        chatArea.isEditable = false
        chatArea.lineWrap = true
        chatArea.wrapStyleWord = true
        val chatScroll = JScrollPane(chatArea)
        chatScroll.preferredSize = Dimension(0, 200)

        val topPanel = JPanel(BorderLayout())
        topPanel.add(JLabel("DevWerk"), BorderLayout.WEST)
        topPanel.add(settingsButton, BorderLayout.EAST)

        val bottomPanel = JPanel(BorderLayout(4, 0))
        bottomPanel.add(inputField, BorderLayout.CENTER)
        bottomPanel.add(sendButton, BorderLayout.EAST)

        add(topPanel, BorderLayout.NORTH)
        add(chatScroll, BorderLayout.CENTER)
        add(bottomPanel, BorderLayout.SOUTH)

        sendButton.addActionListener { onSendClicked() }
        inputField.addActionListener { onSendClicked() }

        settingsButton.addActionListener {
            try {
                AiSettingsDialog().show()
            } catch (t: Throwable) {
                t.printStackTrace()
                appendChatLine("[Error] Failed to open settings: ${t.message}")
            }
        }
    }

    private fun onSendClicked() {
        val text = inputField.text.trim()
        if (text.isEmpty()) return

        appendChatLine("You: $text")
        history += ChatMessage("user", text)
        inputField.text = ""

        val ctx = ChatContext(
            projectRoot = project.basePath,
            history = history.toList()
        )

        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val aiClient = AiClientFactory.create(project)
                val response = aiClient.sendChat(ctx, text)

                history += ChatMessage("assistant", response.reply)

                // 1) 优先 patch_ops
                if (response.patchOps.isNotEmpty()) {
                    PatchApplier.applyPatchOps(project, response.patchOps)
                } else if (response.ops.isNotEmpty()) {
                    FsScaffolder.applyFileOps(project, response.ops)
                }

                SwingUtilities.invokeLater {
                    appendChatLine("Bot: ${response.reply}")

                    if (!response.codeTree.isNullOrBlank()) {
                        appendChatLine("=== Code Tree ===")
                        appendChatLine(response.codeTree)
                    }

                    if (response.patchOps.isNotEmpty()) {
                        appendChatLine("[System] ${response.patchOps.size} patch operation(s) applied to project.")
                    } else if (response.ops.isNotEmpty()) {
                        appendChatLine("[System] ${response.ops.size} file operation(s) applied to project.")
                    } else if (response.done) {
                        appendChatLine("[System] done=true")
                    }
                }
            } catch (t: Throwable) {
                t.printStackTrace()
                SwingUtilities.invokeLater {
                    appendChatLine("[Error] Failed to call AI server: ${t.message}")
                }
            }
        }
    }

    private fun appendChatLine(line: String) {
        if (chatArea.text.isEmpty()) {
            chatArea.text = line
        } else {
            chatArea.append("\n$line")
        }
        chatArea.caretPosition = chatArea.document.length
    }
}

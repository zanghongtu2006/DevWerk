package com.zanghongtu.devwerk

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.zanghongtu.devwerk.codeEditor.ChatContext
import com.zanghongtu.devwerk.codeEditor.ChatMessage
import com.zanghongtu.devwerk.settings.AiSettingsDialog
import java.awt.BorderLayout
import java.awt.Dimension
import java.nio.file.Paths
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

        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val basePath = project.basePath
                val runner = DevwerkOperationRunner()

                // 发送前就创建 .devwerk / .gitignore / opDir（方案A核心）
                val devCtx = if (!basePath.isNullOrBlank()) {
                    runner.beginOperation(project, Paths.get(basePath))
                } else null

                val ctx = ChatContext(
                    projectRoot = project.basePath,
                    history = history.toList(),
                    devCtx = devCtx
                )

                val aiClient = AiClientFactory.create(project)
                val response = aiClient.sendChat(ctx, text)

                history += ChatMessage("assistant", response.reply)

                // 拿到最终 response 后：写摘要 + 备份 + 应用（不再依赖 ops 是否为空）
                if (devCtx != null) {
                    runner.recordFinalSummaryAndBackup(project, devCtx, response)
                    runner.applyResponse(project, devCtx, response)
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

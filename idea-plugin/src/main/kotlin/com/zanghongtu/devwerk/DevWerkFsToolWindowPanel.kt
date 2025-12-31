package com.zanghongtu.devwerk

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.zanghongtu.devwerk.codeEditor.ChatContext
import com.zanghongtu.devwerk.codeEditor.ChatMessage
import com.zanghongtu.devwerk.codeEditor.DevwerkContext
import com.zanghongtu.devwerk.settings.AiSettingsDialog
import java.awt.BorderLayout
import java.awt.Dimension
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Paths
import java.nio.file.StandardOpenOption
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

        // UI 先显示
        appendChatLine("You: $text")
        inputField.text = ""

        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val basePath = project.basePath
                val runner = DevwerkOperationRunner()

                // 发送前就创建 .devwerk / .gitignore / opDir（方案A核心）
                val devCtx = if (!basePath.isNullOrBlank()) {
                    runner.beginOperation(project, Paths.get(basePath))
                } else null

                // ⚠️ 注意：这里不要把“当前 text”预先写入 history
                val chatCtx = ChatContext(
                    projectRoot = project.basePath,
                    history = history.toList(),
                    devCtx = devCtx
                )

                val aiClient = AiClientFactory.create(project)

                // ---- 第一次请求 ----
                var response = aiClient.sendChat(chatCtx, text)

                // ---- 自动重试一次：仅当 retryable=true 且本次不 ok ----
                if (!response.ok && response.retryable) {
                    appendOpLog(devCtx, "\n[INFO] retry once due to ${response.errorCode ?: "UNKNOWN"}: ${response.errorMessage ?: ""}\n")

                    // 重试时：不要把失败写入 history，不要改变 messages 上下文（仍然用同一个 chatCtx）
                    response = aiClient.sendChat(chatCtx, text)
                }

                //  现在再把本轮 user 写入 history（只写一次）
                history += ChatMessage("user", text)

                //  只有最终成功才写入 assistant history；失败只显示 system，不污染模型上下文
                if (response.ok) {
                    history += ChatMessage("assistant", response.reply)
                } else {
                    SwingUtilities.invokeLater {
                        appendChatLine("[System] AI error: ${response.errorCode ?: "UNKNOWN"} ${response.errorMessage ?: ""}")
                    }
                }

                // 写摘要/备份：建议无论成功失败都记录（便于排查）
                if (devCtx != null) {
                    runner.recordFinalSummaryAndBackup(project, devCtx, response)

                    // ⚠️ 只有 ok 才应用文件变更（避免错误响应触发 apply）
                    if (response.ok) {
                        runner.applyResponse(project, devCtx, response)
                    } else {
                        appendOpLog(devCtx, "[INFO] skip applyResponse because response.ok=false\n")
                    }
                }
                val reply = response.reply
                val codeTree = response.codeTree
                val patchOpsCount = response.patchOps.size
                val opsCount = response.ops.size
                val done = response.done
                val ok = response.ok
                SwingUtilities.invokeLater {
                    if (response.ok) {
                        appendChatLine("Bot: ${response.reply}")
                    }

                    if (!codeTree.isNullOrBlank()) {
                        appendChatLine("=== Code Tree ===")
                        appendChatLine(codeTree)
                    }

                    if (response.ok && response.patchOps.isNotEmpty()) {
                        appendChatLine("[System] ${response.patchOps.size} patch operation(s) applied to project.")
                    } else if (response.ok && response.ops.isNotEmpty()) {
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

    private fun appendOpLog(devCtx: DevwerkContext?, text: String) {
        val logPath = devCtx?.opLog ?: return
        runCatching {
            Files.writeString(
                logPath,
                text,
                StandardCharsets.UTF_8,
                StandardOpenOption.CREATE,
                StandardOpenOption.APPEND
            )
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

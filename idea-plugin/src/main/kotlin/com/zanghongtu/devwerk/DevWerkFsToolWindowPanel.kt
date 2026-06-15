package com.zanghongtu.devwerk

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.util.ui.JBUI
import com.intellij.util.ui.UIUtil
import com.zanghongtu.devwerk.codeEditor.*
import com.zanghongtu.devwerk.settings.AiSettingsDialog
import java.awt.BorderLayout
import java.awt.Color
import java.awt.Component
import java.awt.Dimension
import java.awt.FlowLayout
import java.awt.Graphics
import java.awt.Graphics2D
import java.awt.Insets
import java.awt.RenderingHints
import java.awt.event.InputEvent
import java.awt.event.KeyEvent
import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Paths
import java.nio.file.StandardOpenOption
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import javax.swing.*
import javax.swing.border.AbstractBorder
import org.json.JSONArray
import org.json.JSONObject

/**
 * DevWerk tool window panel:
 *
 *   1. USER_TYPING → user enters a request
 *   2. WORKFLOW_PENDING → backend workflow plans/codes while plugin polls
 *   3. READY_TO_APPLY → ops are applied with snapshot
 *   4. DONE / FAILED → back to idle
 */
class DevWerkFsToolWindowPanel(private val project: Project) : JPanel(BorderLayout()) {

    // State
    private enum class State { IDLE, WORKFLOW_PENDING }
    @Volatile private var state = State.IDLE

    private val history = mutableListOf<ChatMessage>()

    // UI components
    private val chatArea       = JTextArea()
    private val inputArea       = PromptTextArea("Message DevWerk...  Ctrl+Enter to send", 4, 20)
    private val sendButton      = JButton("Send")
    private val attachBtn       = JButton("+")
    private val clearAttachBtn  = JButton("Clear")
    private val settingsBtn     = JButton("⚙")
    private val pendingAttachments = mutableListOf<File>()
    private val attachmentPanel = JPanel(FlowLayout(FlowLayout.LEFT, 4, 0))

    init {
        initUi()
    }

    private fun initUi() {
        chatArea.isEditable = false
        chatArea.lineWrap = true
        chatArea.wrapStyleWord = true
        chatArea.border = JBUI.Borders.empty(8, 10)
        chatArea.background = UIUtil.getPanelBackground()
        val chatScroll = JScrollPane(chatArea)
        chatScroll.preferredSize = Dimension(0, 200)
        chatScroll.border = JBUI.Borders.empty()

        val topPanel = JPanel(BorderLayout())
        topPanel.border = JBUI.Borders.empty(4, 8)
        topPanel.add(JLabel("DevWerk"), BorderLayout.WEST)
        topPanel.add(settingsBtn, BorderLayout.EAST)

        val northPanel = JPanel(BorderLayout())

        inputArea.lineWrap = true
        inputArea.wrapStyleWord = true
        inputArea.border = JBUI.Borders.empty(6, 8)
        inputArea.background = UIUtil.getTextFieldBackground()
        inputArea.inputMap.put(
            KeyStroke.getKeyStroke(KeyEvent.VK_ENTER, InputEvent.CTRL_DOWN_MASK),
            "devwerk-send"
        )
        inputArea.actionMap.put("devwerk-send", object : AbstractAction() {
            override fun actionPerformed(e: java.awt.event.ActionEvent?) {
                onSendClicked()
            }
        })

        attachmentPanel.isOpaque = false
        attachmentPanel.border = JBUI.Borders.empty(0, 6, 4, 6)
        attachmentPanel.isVisible = false

        val inputScroll = JScrollPane(inputArea)
        inputScroll.border = JBUI.Borders.empty()
        inputScroll.isOpaque = false
        inputScroll.viewport.isOpaque = false
        inputScroll.preferredSize = Dimension(0, 92)

        val actionPanel = JPanel(BorderLayout())
        actionPanel.isOpaque = false
        actionPanel.border = JBUI.Borders.empty(4, 6, 0, 6)
        val leftActions = JPanel(FlowLayout(FlowLayout.LEFT, 4, 0))
        leftActions.isOpaque = false
        leftActions.add(attachBtn)
        leftActions.add(clearAttachBtn)
        val rightActions = JPanel(FlowLayout(FlowLayout.RIGHT, 0, 0))
        rightActions.isOpaque = false
        rightActions.add(sendButton)
        actionPanel.add(leftActions, BorderLayout.WEST)
        actionPanel.add(rightActions, BorderLayout.EAST)

        val composer = RoundedPanel()
        composer.layout = BorderLayout()
        composer.border = JBUI.Borders.empty(8)
        composer.add(attachmentPanel, BorderLayout.NORTH)
        composer.add(inputScroll, BorderLayout.CENTER)
        composer.add(actionPanel, BorderLayout.SOUTH)

        val bottomPanel = JPanel(BorderLayout())
        bottomPanel.border = JBUI.Borders.empty(8, 10, 10, 10)
        bottomPanel.add(composer, BorderLayout.CENTER)

        attachBtn.toolTipText = "Attach file"
        listOf(attachBtn, clearAttachBtn, sendButton, settingsBtn).forEach {
            it.isFocusable = false
        }

        northPanel.add(topPanel, BorderLayout.NORTH)
        add(northPanel, BorderLayout.NORTH)
        add(chatScroll, BorderLayout.CENTER)
        add(bottomPanel, BorderLayout.SOUTH)

        sendButton.addActionListener { onSendClicked() }
        attachBtn.addActionListener { chooseAttachments() }
        clearAttachBtn.addActionListener { clearPendingAttachments() }

        settingsBtn.addActionListener {
            try {
                AiSettingsDialog().show()
            } catch (t: Throwable) {
                t.printStackTrace()
                appendChatLine("[Error] Failed to open settings: ${t.message}")
            }
        }
    }

    private fun setState(newState: State) {
        state = newState
        SwingUtilities.invokeLater {
            when (newState) {
                State.IDLE -> {
                    sendButton.isEnabled = true
                    inputArea.isEnabled = true
                    attachBtn.isEnabled = true
                    clearAttachBtn.isEnabled = true
                }
                State.WORKFLOW_PENDING -> {
                    sendButton.isEnabled = false
                    inputArea.isEnabled = false
                    attachBtn.isEnabled = false
                    clearAttachBtn.isEnabled = false
                }
            }
        }
    }

    // Workflow submission

    private fun onSendClicked() {
        val text = inputArea.text.trim()
        if (text.isEmpty() && pendingAttachments.isEmpty()) return
        if (state != State.IDLE) return

        val attachments = pendingAttachments.toList()
        appendChatLine("You: ${text.ifBlank { "(attachments)" }}${if (attachments.isNotEmpty()) "\n[Attachments] ${attachments.joinToString { it.name }}" else ""}")
        inputArea.text = ""
        clearPendingAttachments()
        setState(State.WORKFLOW_PENDING)

        ApplicationManager.getApplication().executeOnPooledThread {
            runUploadThenWorkflow(text, attachments)
        }
    }

    private fun runUploadThenWorkflow(userText: String, files: List<File>) {
        try {
            val aiClient = AiClientFactory.create(project) as? HttpAiClient
            if (files.isNotEmpty() && aiClient == null) {
                SwingUtilities.invokeLater {
                    appendChatLine("[Error] Attachments require the DevWerk backend provider.")
                    setState(State.IDLE)
                }
                return
            }

            val projectId = DevWerkProjectMeta.getOrCreateProjectId(project)
            val uploaded = files.map { file -> aiClient!!.uploadAttachment(file, projectId) }
            val message = buildUserMessage(userText, uploaded)
            val chatCtx = ChatContext(
                projectRoot = project.basePath,
                history = history.toList(),
                projectId = projectId,
                taskId = null,
                project = project
            )
            runLegacyChat(chatCtx, message)
        } catch (t: Throwable) {
            t.printStackTrace()
            SwingUtilities.invokeLater {
                appendChatLine("[Error] Workflow failed: ${t.message}")
                setState(State.IDLE)
            }
        }
    }

    private fun runLegacyChat(chatCtx: ChatContext, userMessage: String) {
        try {
            val basePath = project.basePath
            val runner = DevwerkOperationRunner()
            val devCtx = if (!basePath.isNullOrBlank()) runner.beginOperation(project, Paths.get(basePath)) else null

            val updatedCtx = chatCtx.copy(devCtx = devCtx)
            val aiClient = AiClientFactory.create(project)

            var response = aiClient.sendWorkflow(updatedCtx, userMessage)

            if (!response.ok && response.retryable) {
                appendOpLog(devCtx, "\n[INFO] retry once\n")
                response = aiClient.sendWorkflow(updatedCtx.copy(history = history.toList()), userMessage)
            }

            history += ChatMessage("user", userMessage)
            if (response.ok) {
                history += ChatMessage("assistant", response.reply)
            } else {
                SwingUtilities.invokeLater {
                    appendChatLine("[System] AI error: ${response.errorCode ?: "UNKNOWN"} ${response.errorMessage ?: ""}")
                }
            }

            if (devCtx != null) {
                runner.recordFinalSummaryAndBackup(project, devCtx, response)
                if (response.ok) {
                    runCatching {
                        runner.applyResponse(project, devCtx, response)
                    }.onSuccess {
                        val verification = runPostApplyTools(aiClient as? HttpAiClient, updatedCtx, response, devCtx)
                        reportApplyResult(
                            aiClient,
                            response,
                            devCtx,
                            ok = true,
                            changedPaths = collectChangedPaths(response),
                            verification = verification
                        )
                    }.onFailure { applyError ->
                        reportApplyResult(
                            aiClient,
                            response,
                            devCtx,
                            ok = false,
                            changedPaths = collectChangedPaths(response),
                            errorMessage = "${applyError::class.java.simpleName}: ${applyError.message}"
                        )
                        throw applyError
                    }
                }
            } else if (response.ok) {
                (aiClient as? HttpAiClient)?.let {
                    reportApplyResult(it, response, null, ok = false, changedPaths = collectChangedPaths(response), errorMessage = "DevWerk local operation context is unavailable.")
                }
            }

            val resp = response
            SwingUtilities.invokeLater {
                if (resp.ok) appendChatLine("Bot: ${resp.reply}")
                resp.codeTree?.takeIf { it.isNotBlank() }?.let {
                    appendChatLine("=== Code Tree ==="); appendChatLine(it)
                }
                if (resp.ok && resp.patchOps.isNotEmpty()) {
                    appendChatLine("[System] ${resp.patchOps.size} patch op(s) applied.")
                } else if (resp.ok && resp.ops.isNotEmpty()) {
                    appendChatLine("[System] ${resp.ops.size} file op(s) applied.")
                }
                setState(State.IDLE)
            }

        } catch (t: Throwable) {
            t.printStackTrace()
            SwingUtilities.invokeLater {
                appendChatLine("[Error] AI call failed: ${t.message}")
                setState(State.IDLE)
            }
        }
    }

    // ── Utilities ───────────────────────────────────────────────────────────────

    private fun appendOpLog(devCtx: DevwerkContext?, text: String) {
        val logPath = devCtx?.opLog ?: return
        runCatching {
            Files.writeString(logPath, timestampLogText(text), StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND)
        }
    }

    private fun timestampLogText(text: String): String {
        val suffix = if (text.endsWith("\n")) "\n" else ""
        return text.trimEnd('\n').split('\n').joinToString("\n") { line ->
            if (line.isBlank()) line else "${LocalDateTime.now().format(LOG_TIME_FORMAT)} $line"
        } + suffix
    }

    private fun reportApplyResult(
        aiClient: AiClient,
        response: IdeChatResponse,
        devCtx: DevwerkContext?,
        ok: Boolean,
        changedPaths: List<String>,
        errorMessage: String? = null,
        verification: JSONObject = JSONObject()
    ) {
        val taskId = response.taskId ?: return
        val http = aiClient as? HttpAiClient ?: return
        runCatching {
            http.reportApplyResult(
                taskId = taskId,
                ok = ok,
                snapshotId = devCtx?.opDir?.fileName?.toString(),
                changedPaths = changedPaths,
                errorMessage = errorMessage,
                projectId = DevWerkProjectMeta.getOrCreateProjectId(project),
                verification = verification
            )
            appendOpLog(devCtx, "[INFO] Kanban apply_result action reported: ok=$ok taskId=$taskId\n")
        }.onFailure { syncError ->
            syncError.printStackTrace()
            appendOpLog(devCtx, "[WARN] Kanban apply_result action failed: ${syncError::class.java.simpleName}: ${syncError.message}\n")
            SwingUtilities.invokeLater {
                appendChatLine("[Warn] Kanban sync failed: ${syncError.message}")
            }
        }
    }

    private fun runPostApplyTools(
        http: HttpAiClient?,
        context: ChatContext,
        response: IdeChatResponse,
        devCtx: DevwerkContext?
    ): JSONObject {
        val requests = response.toolRequests
        if (http == null || requests.isEmpty()) return JSONObject()

        appendOpLog(devCtx, "[INFO] Executing ${requests.size} post-apply tool request(s).\n")
        val results = http.executeClientTools(context, requests)
        val byId = results.associateBy { it.id }
        val required = JSONArray()
        val resultMap = JSONObject()
        val details = JSONArray()

        for (request in requests) {
            val result = byId[request.id]
            val ok = result?.ok == true
            required.put(request.id)
            resultMap.put(request.id, if (ok) "passed" else "failed")
            details.put(
                JSONObject()
                    .put("id", request.id)
                    .put("tool", request.tool)
                    .put("ok", ok)
                    .put("content", result?.content ?: JSONObject.NULL)
                    .put("error", result?.error ?: JSONObject.NULL)
            )
        }

        return JSONObject()
            .put("required", required)
            .put("results", resultMap)
            .put("tool_results", details)
    }

    private fun collectChangedPaths(response: IdeChatResponse): List<String> {
        val fromOps = response.ops.mapNotNull { normalizeRelPath(it.path).takeIf { p -> p.isNotBlank() } }
        val fromPatchOps = PatchApplier.collectAffectedPaths(response.patchOps)
            .mapNotNull { normalizeRelPath(it).takeIf { p -> p.isNotBlank() } }
        return (fromOps + fromPatchOps).distinct()
    }

    private fun normalizeRelPath(path: String): String {
        var s = path.trim().replace("\\", "/")
        while (s.startsWith("/")) s = s.substring(1)
        val parts = s.split("/").filter { it.isNotBlank() }
        if (parts.any { it == ".." }) return ""
        return parts.joinToString("/")
    }

    private fun chooseAttachments() {
        val chooser = JFileChooser().apply {
            isMultiSelectionEnabled = true
            fileSelectionMode = JFileChooser.FILES_ONLY
        }
        if (chooser.showOpenDialog(this) != JFileChooser.APPROVE_OPTION) return
        for (file in chooser.selectedFiles.orEmpty()) {
            if (file.exists() && file.isFile && pendingAttachments.none { it.absolutePath == file.absolutePath }) {
                pendingAttachments += file
            }
        }
        refreshAttachmentList()
    }

    private fun clearPendingAttachments() {
        pendingAttachments.clear()
        refreshAttachmentList()
    }

    private fun refreshAttachmentList() {
        attachmentPanel.removeAll()
        pendingAttachments.forEach { file ->
            attachmentPanel.add(AttachmentChip(file.name, file.length()))
        }
        attachmentPanel.isVisible = pendingAttachments.isNotEmpty()
        attachmentPanel.revalidate()
        attachmentPanel.repaint()
    }

    private fun buildUserMessage(text: String, attachments: List<UploadedAttachment>): String {
        if (attachments.isEmpty()) return text
        return buildString {
            if (text.isNotBlank()) append(text.trim())
            append("\n\nattachments:\n")
            attachments.forEach { a ->
                append("- id: ").append(a.id).append("\n")
                append("  filename: ").append(a.filename).append("\n")
                append("  content_type: ").append(a.contentType ?: "application/octet-stream").append("\n")
                append("  size: ").append(a.size).append("\n")
                append("  local_path: ").append(a.localPath).append("\n")
            }
        }
    }

    private fun appendChatLine(line: String) {
        SwingUtilities.invokeLater {
            if (chatArea.text.isEmpty()) {
                chatArea.text = line
            } else {
                chatArea.append("\n$line")
            }
            chatArea.caretPosition = chatArea.document.length
        }
    }

    companion object {
        private val LOG_TIME_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS")
    }
}

private class PromptTextArea(
    private val prompt: String,
    rows: Int,
    columns: Int
) : JTextArea(rows, columns) {
    override fun paintComponent(g: Graphics) {
        super.paintComponent(g)
        if (text.isNotEmpty()) return

        val g2 = g.create() as Graphics2D
        try {
            g2.color = UIUtil.getInactiveTextColor()
            g2.font = font
            val insets = insets
            g2.drawString(prompt, insets.left + 2, insets.top + g2.fontMetrics.ascent + 1)
        } finally {
            g2.dispose()
        }
    }
}

private class RoundedPanel : JPanel() {
    init {
        isOpaque = false
        border = RoundedBorder()
    }

    override fun paintComponent(g: Graphics) {
        val g2 = g.create() as Graphics2D
        try {
            g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
            g2.color = UIUtil.getTextFieldBackground()
            g2.fillRoundRect(0, 0, width - 1, height - 1, 14, 14)
        } finally {
            g2.dispose()
        }
        super.paintComponent(g)
    }
}

private class RoundedBorder : AbstractBorder() {
    override fun paintBorder(c: Component, g: Graphics, x: Int, y: Int, width: Int, height: Int) {
        val g2 = g.create() as Graphics2D
        try {
            g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
            g2.color = UIUtil.getBoundsColor()
            g2.drawRoundRect(x, y, width - 1, height - 1, 14, 14)
        } finally {
            g2.dispose()
        }
    }

    override fun getBorderInsets(c: Component): Insets = JBUI.insets(1)
    override fun getBorderInsets(c: Component, insets: Insets): Insets {
        val next = getBorderInsets(c)
        insets.set(next.top, next.left, next.bottom, next.right)
        return insets
    }
}

private class AttachmentChip(name: String, size: Long) : JLabel("$name (${formatSize(size)})") {
    init {
        isOpaque = true
        background = UIUtil.getTextFieldBackground()
        border = JBUI.Borders.compound(
            RoundedBorder(),
            JBUI.Borders.empty(3, 8)
        )
    }
}

private fun formatSize(size: Long): String {
    if (size < 1024) return "$size B"
    val kb = size / 1024.0
    if (kb < 1024) return "%.1f KB".format(kb)
    return "%.1f MB".format(kb / 1024.0)
}

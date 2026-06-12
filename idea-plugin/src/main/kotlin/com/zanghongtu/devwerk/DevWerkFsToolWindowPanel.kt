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
import javax.swing.*
import javax.swing.border.AbstractBorder

/**
 * DevWerk tool window panel — two-phase workflow:
 *
 *   1. USER_TYPING → user enters a request
 *   2. PLAN_PENDING → plan is fetched from backend (read-only)
 *   3. PLAN_READY   → plan shown to user with approve/cancel buttons
 *   4. EXECUTE_PENDING → user approved; ops are applied with snapshot
 *   5. DONE / CANCELLED → back to idle
 */
class DevWerkFsToolWindowPanel(private val project: Project) : JPanel(BorderLayout()) {

    // State
    private enum class State { IDLE, PLAN_PENDING, PLAN_READY, EXECUTE_PENDING }
    @Volatile private var state = State.IDLE

    private val history = mutableListOf<ChatMessage>()
    @Volatile private var currentPlan: PlanResponse? = null
    private var currentPlanUserMessage: String = ""

    // UI components
    private val chatArea       = JTextArea()
    private val inputArea       = PromptTextArea("Message DevWerk...  Ctrl+Enter to send", 4, 20)
    private val sendButton      = JButton("Send")
    private val attachBtn       = JButton("+")
    private val clearAttachBtn  = JButton("Clear")
    private val settingsBtn     = JButton("⚙")
    private val pendingAttachments = mutableListOf<File>()
    private val attachmentPanel = JPanel(FlowLayout(FlowLayout.LEFT, 4, 0))

    // Plan panel
    private val planPanel       = JPanel(BorderLayout())
    private val planArea        = JTextArea(8, 0)
    private val planBtnsPanel   = JPanel()
    private val executeBtn      = JButton("Execute (已确认)")
    private val cancelPlanBtn   = JButton("Cancel")
    private val planCheckboxes  = mutableMapOf<String, JCheckBox>()

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

        planArea.isEditable = false
        planArea.lineWrap = true
        planArea.wrapStyleWord = true
        planArea.border = JBUI.Borders.empty(6)
        planPanel.add(JScrollPane(planArea), BorderLayout.CENTER)
        planBtnsPanel.add(executeBtn)
        planBtnsPanel.add(cancelPlanBtn)
        planPanel.add(planBtnsPanel, BorderLayout.SOUTH)
        planPanel.isVisible = false

        northPanel.add(topPanel, BorderLayout.NORTH)
        northPanel.add(planPanel, BorderLayout.CENTER)
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

        executeBtn.addActionListener { onExecuteClicked() }
        cancelPlanBtn.addActionListener { onCancelPlanClicked() }
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
                    planPanel.isVisible = false
                }
                State.PLAN_PENDING -> {
                    sendButton.isEnabled = false
                    inputArea.isEnabled = false
                    attachBtn.isEnabled = false
                    clearAttachBtn.isEnabled = false
                    planPanel.isVisible = false
                }
                State.PLAN_READY -> {
                    sendButton.isEnabled = false
                    inputArea.isEnabled = false
                    attachBtn.isEnabled = false
                    clearAttachBtn.isEnabled = false
                    planPanel.isVisible = true
                }
                State.EXECUTE_PENDING -> {
                    sendButton.isEnabled = false
                    inputArea.isEnabled = false
                    attachBtn.isEnabled = false
                    clearAttachBtn.isEnabled = false
                    planPanel.isVisible = false
                }
            }
        }
    }

    // ── Phase 1: Send → Plan ──────────────────────────────────────────────────

    private fun onSendClicked() {
        val text = inputArea.text.trim()
        if (text.isEmpty() && pendingAttachments.isEmpty()) return
        if (state != State.IDLE) return

        val attachments = pendingAttachments.toList()
        appendChatLine("You: ${text.ifBlank { "(attachments)" }}${if (attachments.isNotEmpty()) "\n[Attachments] ${attachments.joinToString { it.name }}" else ""}")
        inputArea.text = ""
        clearPendingAttachments()
        setState(State.PLAN_PENDING)

        ApplicationManager.getApplication().executeOnPooledThread {
            runUploadThenPlan(text, attachments)
        }
    }

    private fun runUploadThenPlan(userText: String, files: List<File>) {
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
            currentPlanUserMessage = message
            runPlanPhase(message)
        } catch (t: Throwable) {
            t.printStackTrace()
            SwingUtilities.invokeLater {
                appendChatLine("[Error] Attachment upload failed: ${t.message}")
                setState(State.IDLE)
            }
        }
    }

    private fun runPlanPhase(userMessage: String) {
        try {
            val basePath = project.basePath
            val runner = DevwerkOperationRunner()

            // Plan phase is READ-ONLY — no beginOperation here.
            val devCtx = if (!basePath.isNullOrBlank()) runner.beginOperation(project, Paths.get(basePath)) else null

            val chatCtx = ChatContext(
                projectRoot = project.basePath,
                history = history.toList(),
                projectId = DevWerkProjectMeta.getOrCreateProjectId(project),
                project = project,
                devCtx = devCtx
            )

            val aiClient = AiClientFactory.create(project) as? HttpAiClient

            if (aiClient == null) {
                SwingUtilities.invokeLater {
                    appendChatLine("[Info] Provider does not support plan mode; using direct execution.")
                }
                runLegacyChat(chatCtx, userMessage)
                return
            }

            val planResp = aiClient.sendPlan(chatCtx, userMessage)

            if (!planResp.ok) {
                SwingUtilities.invokeLater {
                    appendChatLine("[Plan Error] ${planResp.errorCode ?: "UNKNOWN"}: ${planResp.errorMessage ?: "No details"}")
                    setState(State.IDLE)
                }
                return
            }

            currentPlan = planResp

            SwingUtilities.invokeLater {
                showPlan(planResp)
                setState(State.PLAN_READY)
            }

        } catch (t: Throwable) {
            t.printStackTrace()
            SwingUtilities.invokeLater {
                appendChatLine("[Error] Plan failed: ${t.message}")
                setState(State.IDLE)
            }
        }
    }

    private fun showPlan(planResp: PlanResponse) {
        planArea.text = ""

        if (planResp.files.isEmpty()) {
            planArea.text = "(无文件变更 — 这是纯问答，直接执行)"
            return
        }

        val sb = StringBuilder()
        sb.append("📋 Plan — ${planResp.files.size} 个文件待确认\n")
        sb.append("━".repeat(50)).append("\n")

        if (planResp.summary.isNotBlank()) {
            sb.append("摘要: ${planResp.summary}\n\n")
        }

        planCheckboxes.clear()

        for (file in planResp.files) {
            val icon = when (file.nature) {
                "new"      -> "🟢 新增"
                "modified" -> "🟡 修改"
                "deleted"  -> "🔴 删除"
                else       -> "⚪ ${file.nature}"
            }

            sb.append("$icon  ${file.path}\n")
            sb.append("       ${file.description}\n")
            if (file.confidence < 0.7) {
                sb.append("       ⚠️ 置信度 ${(file.confidence * 100).toInt()}%\n")
            }
            sb.append("\n")
            planCheckboxes[file.path] = JCheckBox(file.path, true)
        }

        if (planResp.warnings.isNotEmpty()) {
            sb.append("⚠️  Warnings:\n")
            for (w in planResp.warnings) {
                sb.append("  • $w\n")
            }
        }

        planArea.text = sb.toString()
    }

    // ── Phase 2: Approve / Cancel ──────────────────────────────────────────────

    private fun onExecuteClicked() {
        if (state != State.PLAN_READY) return
        setState(State.EXECUTE_PENDING)

        ApplicationManager.getApplication().executeOnPooledThread {
            runExecutePhase()
        }
    }

    private fun onCancelPlanClicked() {
        if (state != State.PLAN_READY) return
        appendChatLine("Plan cancelled by user.")
        currentPlan = null
        setState(State.IDLE)
    }

    private fun runExecutePhase() {
        if (currentPlan == null) return
        val userMessage = currentPlanUserMessage

        try {
            val basePath = project.basePath
            val runner = DevwerkOperationRunner()
            val devCtx = if (!basePath.isNullOrBlank()) runner.beginOperation(project, Paths.get(basePath)) else null

            val chatCtx = ChatContext(
                projectRoot = project.basePath,
                history = history.toList(),
                projectId = DevWerkProjectMeta.getOrCreateProjectId(project),
                project = project,
                devCtx = devCtx
            )

            val aiClient = AiClientFactory.create(project) as? HttpAiClient
            if (aiClient == null) {
                SwingUtilities.invokeLater {
                    appendChatLine("[Error] No DevWerk backend client available.")
                    setState(State.IDLE)
                }
                currentPlan = null
                return
            }

            val approved = planCheckboxes.filter { it.value.isSelected }.keys.toList()

            if (approved.isEmpty()) {
                SwingUtilities.invokeLater {
                    appendChatLine("[Info] No files selected — nothing to execute.")
                    setState(State.IDLE)
                }
                currentPlan = null
                return
            }

            val execResp = aiClient.sendExecute(
                context = chatCtx,
                userMessage = userMessage,
                approvedPaths = approved,
                approvedOps = emptyList()
            )

            history += ChatMessage("user", userMessage)
            if (execResp.ok) history += ChatMessage("assistant", execResp.reply)

            if (devCtx != null) {
                runner.recordFinalSummaryAndBackup(project, devCtx, execResp)
                if (execResp.ok) runner.applyResponse(project, devCtx, execResp)
            }

            val resp = execResp
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
                currentPlan = null
                setState(State.IDLE)
            }

        } catch (t: Throwable) {
            t.printStackTrace()
            SwingUtilities.invokeLater {
                appendChatLine("[Error] Execute failed: ${t.message}")
                currentPlan = null
                setState(State.IDLE)
            }
        }
    }

    // ── Fallback: legacy single-shot chat ──────────────────────────────────────

    private fun runLegacyChat(chatCtx: ChatContext, userMessage: String) {
        try {
            val basePath = project.basePath
            val runner = DevwerkOperationRunner()
            val devCtx = if (!basePath.isNullOrBlank()) runner.beginOperation(project, Paths.get(basePath)) else null

            val updatedCtx = chatCtx.copy(devCtx = devCtx)
            val aiClient = AiClientFactory.create(project)

            var response = aiClient.sendChat(updatedCtx, userMessage)

            if (!response.ok && response.retryable) {
                appendOpLog(devCtx, "\n[INFO] retry once\n")
                response = aiClient.sendChat(updatedCtx.copy(history = history.toList()), userMessage)
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
                if (response.ok) runner.applyResponse(project, devCtx, response)
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
            Files.writeString(logPath, text, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND)
        }
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

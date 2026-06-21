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
 *   1. USER_TYPING -> user enters a request
 *   2. WORKFLOW_PENDING -> backend workflow plans/codes and streams events
 *   3. READY_TO_APPLY -> ops are applied with snapshot
 *   4. DONE / FAILED -> back to idle
 */
class DevWerkFsToolWindowPanel(private val project: Project) : JPanel(BorderLayout()) {

    // State
    private enum class State { IDLE, WORKFLOW_PENDING, PLAN_CONFIRMATION, USER_GUIDANCE }
    @Volatile private var state = State.IDLE

    private val history = mutableListOf<ChatMessage>()

    // UI components
    private val chatArea       = JTextArea()
    private val inputArea       = PromptTextArea("Message DevWerk...  Ctrl+Enter to send", 4, 20)
    private val sendButton      = JButton("Send")
    private val confirmPlanButton = JButton("Confirm & Code")
    private val attachBtn       = JButton("+")
    private val clearAttachBtn  = JButton("Clear")
    private val settingsBtn     = JButton("\u2699")
    private val pendingAttachments = mutableListOf<File>()
    private val attachmentPanel = JPanel(FlowLayout(FlowLayout.LEFT, 4, 0))
    @Volatile private var activeTaskId: String? = null
    @Volatile private var waitingFor: String? = null
    @Volatile private var activeDevCtx: DevwerkContext? = null

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
        rightActions.add(confirmPlanButton)
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
        confirmPlanButton.isVisible = false
        listOf(attachBtn, clearAttachBtn, sendButton, confirmPlanButton, settingsBtn).forEach {
            it.isFocusable = false
        }

        northPanel.add(topPanel, BorderLayout.NORTH)
        add(northPanel, BorderLayout.NORTH)
        add(chatScroll, BorderLayout.CENTER)
        add(bottomPanel, BorderLayout.SOUTH)

        sendButton.addActionListener { onSendClicked() }
        attachBtn.addActionListener { chooseAttachments() }
        clearAttachBtn.addActionListener { clearPendingAttachments() }
        confirmPlanButton.addActionListener {
            if (state == State.PLAN_CONFIRMATION && waitingFor == "plan_confirmation" && activeTaskId != null) {
                setState(State.WORKFLOW_PENDING)
                ApplicationManager.getApplication().executeOnPooledThread {
                    runUploadThenWorkflow("Confirm the proposed plan and continue.", emptyList(), "confirm_plan")
                }
            }
        }

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
                    confirmPlanButton.isEnabled = true
                }
                State.WORKFLOW_PENDING -> {
                    sendButton.isEnabled = false
                    inputArea.isEnabled = false
                    attachBtn.isEnabled = false
                    clearAttachBtn.isEnabled = false
                    confirmPlanButton.isEnabled = false
                }
                State.PLAN_CONFIRMATION -> {
                    sendButton.isEnabled = false
                    inputArea.isEnabled = false
                    attachBtn.isEnabled = false
                    clearAttachBtn.isEnabled = false
                    confirmPlanButton.isEnabled = true
                }
                State.USER_GUIDANCE -> {
                    sendButton.isEnabled = true
                    inputArea.isEnabled = true
                    attachBtn.isEnabled = true
                    clearAttachBtn.isEnabled = true
                    confirmPlanButton.isEnabled = false
                }
            }
        }
    }

    // Workflow submission

    private fun onSendClicked() {
        val text = inputArea.text.trim()
        if (text.isEmpty() && pendingAttachments.isEmpty()) return
        if (state !in setOf(State.IDLE, State.USER_GUIDANCE)) return

        val continuingWithGuidance = state == State.USER_GUIDANCE

        val attachments = pendingAttachments.toList()
        appendChatLine("You: ${text.ifBlank { "(attachments)" }}${if (attachments.isNotEmpty()) "\n[Attachments] ${attachments.joinToString { it.name }}" else ""}")
        inputArea.text = ""
        clearPendingAttachments()
        setState(State.WORKFLOW_PENDING)

        ApplicationManager.getApplication().executeOnPooledThread {
            runUploadThenWorkflow(
                text,
                attachments,
                when {
                    continuingWithGuidance -> "message"
                    activeTaskId != null -> "revise_plan"
                    else -> null
                }
            )
        }
    }

    private fun runUploadThenWorkflow(userText: String, files: List<File>, workflowAction: String?) {
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
                taskId = activeTaskId,
                workflowAction = workflowAction,
                project = project
            )
            runWorkflow(chatCtx, message)
        } catch (t: Throwable) {
            t.printStackTrace()
            SwingUtilities.invokeLater {
                appendChatLine("[Error] Workflow failed: ${t.message}")
                setState(State.IDLE)
            }
        }
    }

    private fun runWorkflow(chatCtx: ChatContext, userMessage: String) {
        try {
            val basePath = project.basePath
            val runner = DevwerkOperationRunner()
            var devCtx = activeDevCtx ?: if (!basePath.isNullOrBlank()) {
                runner.beginInteraction(project, Paths.get(basePath), chatCtx.taskId)
            } else null

            var updatedCtx = chatCtx.copy(devCtx = devCtx)
            val aiClient = AiClientFactory.create(project)

            var response = aiClient.sendWorkflow(updatedCtx, userMessage)
            val responseTaskId = response.taskId
            if (devCtx != null && !responseTaskId.isNullOrBlank()) {
                devCtx = runner.bindTask(devCtx, responseTaskId)
                activeDevCtx = devCtx
                updatedCtx = updatedCtx.copy(devCtx = devCtx, taskId = responseTaskId)
            }

            response = resolveClientToolPauses(aiClient, updatedCtx, response)

            history += ChatMessage("user", userMessage)
            if (response.ok) {
                if (response.waitingFor != "user_guidance") {
                    history += ChatMessage("assistant", response.reply)
                }
            } else {
                SwingUtilities.invokeLater {
                    appendChatLine("[System] AI error: ${response.errorCode ?: "UNKNOWN"} ${response.errorMessage ?: ""}")
                }
            }

            if (response.ok && response.waitingFor != null) {
                activeTaskId = response.taskId
                waitingFor = response.waitingFor
                devCtx?.let { runner.recordInteractionPaused(it, response) }
            } else if (devCtx != null) {
                if (isReadyToApply(response)) {
                    response = applyAndVerifyWithResume(aiClient, runner, project, updatedCtx, devCtx, response)
                } else if (!response.ok) {
                    runner.recordFinalSummaryAndBackup(project, devCtx, response)
                }
                val terminal = !response.ok || response.done || response.statusKey in setOf("done", "failed")
                if (terminal) runner.recordInteractionEnded(devCtx, response)
            } else if (isReadyToApply(response)) {
                (aiClient as? HttpAiClient)?.let {
                    reportApplyResult(it, response, null, ok = false, changedPaths = collectChangedPaths(response), errorMessage = "DevWerk local operation context is unavailable.")
                }
            }

            val resp = response
            SwingUtilities.invokeLater {
                if (resp.ok && resp.waitingFor == "user_guidance") {
                    appendChatLine("[Review] ${resp.reply}")
                    appendChatLine("[System] Workflow paused because the agents need additional guidance.")
                } else if (resp.ok) {
                    appendChatLine("Bot: ${resp.reply}")
                }
                if (resp.ok && resp.waitingFor == "plan_confirmation") {
                    appendChatLine("[System] Workflow paused at Planned and is waiting for confirmation.")
                }
                resp.codeTree?.takeIf { it.isNotBlank() }?.let {
                    appendChatLine("=== Code Tree ==="); appendChatLine(it)
                }
                if (resp.ok && resp.patchOps.isNotEmpty()) {
                    appendChatLine("[System] ${resp.patchOps.size} patch op(s) applied.")
                } else if (resp.ok && resp.ops.isNotEmpty()) {
                    appendChatLine("[System] ${resp.ops.size} file op(s) applied.")
                }
                confirmPlanButton.isVisible = resp.ok && resp.waitingFor == "plan_confirmation"
                val terminal = !resp.ok || resp.done || resp.statusKey in setOf("done", "failed")
                if (terminal) {
                    activeTaskId = null
                    waitingFor = null
                    activeDevCtx = null
                }
                setState(
                    when {
                        terminal -> State.IDLE
                        resp.waitingFor == "plan_confirmation" -> State.PLAN_CONFIRMATION
                        resp.waitingFor == "user_guidance" -> State.USER_GUIDANCE
                        else -> State.WORKFLOW_PENDING
                    }
                )
            }

        } catch (t: Throwable) {
            t.printStackTrace()
            SwingUtilities.invokeLater {
                appendChatLine("[Error] AI call failed: ${t.message}")
                setState(State.IDLE)
            }
        }
    }

    private fun resolveClientToolPauses(
        aiClient: AiClient,
        context: ChatContext,
        initial: IdeChatResponse
    ): IdeChatResponse {
        val http = aiClient as? HttpAiClient ?: return initial
        var response = initial
        repeat(128) { round ->
            if (!response.ok || response.waitingFor != "client_tool") return response
            val taskId = response.taskId?.takeIf { it.isNotBlank() }
                ?: return response.copy(
                    ok = false,
                    done = true,
                    errorCode = "CLIENT_TOOL_PROTOCOL_ERROR",
                    errorMessage = "Client-tool pause did not include task_id."
                )
            if (response.toolRequests.isEmpty()) {
                return response.copy(
                    ok = false,
                    done = true,
                    errorCode = "CLIENT_TOOL_PROTOCOL_ERROR",
                    errorMessage = "Client-tool pause did not include tool_requests."
                )
            }

            appendOpLog(
                context.devCtx,
                "[INFO] Client-tool workflow round=${round + 1} task=$taskId requests=${response.toolRequests.size}\n"
            )
            val results = http.executeClientTools(context.copy(taskId = taskId), response.toolRequests)
            response = http.continueWorkflowWithToolResults(context.copy(taskId = taskId), taskId, results)
        }
        return response.copy(
            ok = false,
            done = true,
            errorCode = "CLIENT_TOOL_ROUND_LIMIT",
            errorMessage = "Workflow exceeded 128 consecutive client-tool rounds."
        )
    }

    // ── Utilities ───────────────────────────────────────────────────────────────

    private fun applyAndVerifyWithResume(
        aiClient: AiClient,
        runner: DevwerkOperationRunner,
        project: Project,
        context: ChatContext,
        devCtx: DevwerkContext,
        initialResponse: IdeChatResponse
    ): IdeChatResponse {
        var current = initialResponse
        var resumeRounds = 0

        while (true) {
            if (!isReadyToApply(current)) return current
            val snapshotCtx = runner.beginSnapshot(devCtx)
            val actionResponse = runCatching {
                runner.recordFinalSummaryAndBackup(project, snapshotCtx, current)
                ApplicationManager.getApplication().invokeAndWait {
                    runner.applyResponse(project, snapshotCtx, current)
                }
                val verification = runPostApplyTools(aiClient as? HttpAiClient, context, current, snapshotCtx)
                reportApplyResult(
                    aiClient,
                    current,
                    snapshotCtx,
                    ok = true,
                    changedPaths = collectChangedPaths(current),
                    verification = verification
                )
            }.getOrElse { applyError ->
                appendOpLog(devCtx, "[WARN] Apply failed; reporting structured feedback to backend: ${applyError::class.java.simpleName}: ${applyError.message}\n")
                reportApplyResult(
                    aiClient,
                    current,
                    snapshotCtx,
                    ok = false,
                    changedPaths = collectChangedPaths(current),
                    errorMessage = "${applyError::class.java.simpleName}: ${applyError.message}"
                )
            }

            val resume = actionResponse.optJSONObject("workflow_resume")
            if (resume == null) {
                val backendStatus = actionResponse.optJSONObject("task")
                    ?.optString("status_key", "")
                    ?.takeIf { it.isNotBlank() }
                return if (backendStatus != null) {
                    current.copy(statusKey = backendStatus, done = backendStatus in setOf("done", "failed"))
                } else {
                    current
                }
            }

            val http = aiClient as? HttpAiClient ?: return current
            val taskId = current.taskId ?: return current
            val pollUrl = resume.optString("poll_url", "").takeIf { it.isNotBlank() } ?: return current
            val eventsUrl = resume.optString("events_url", "").takeIf { it.isNotBlank() } ?: return current
            appendOpLog(devCtx, "[INFO] Verification failed; waiting for backend recoding round ${resumeRounds + 1}.\n")
            current = http.awaitWorkflowContinuation(context, taskId, pollUrl, eventsUrl)
            if (!current.ok) return current
            resumeRounds += 1
        }
    }

    private fun isReadyToApply(response: IdeChatResponse): Boolean =
        response.ok &&
            response.waitingFor == null &&
            response.statusKey == "ready_to_apply" &&
            response.nextAction == "apply_result" &&
            collectChangedPaths(response).isNotEmpty()

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
    ): JSONObject {
        val taskId = response.taskId ?: return JSONObject()
        val http = aiClient as? HttpAiClient ?: return JSONObject()
        return runCatching {
            val actionResponse = http.reportApplyResult(
                taskId = taskId,
                ok = ok,
                snapshotId = devCtx?.opDir?.fileName?.toString(),
                changedPaths = changedPaths,
                errorMessage = errorMessage,
                projectId = DevWerkProjectMeta.getOrCreateProjectId(project),
                verification = verification
            )
            appendOpLog(devCtx, "[INFO] Kanban apply_result action reported: ok=$ok taskId=$taskId\n")
            actionResponse
        }.onFailure { syncError ->
            syncError.printStackTrace()
            appendOpLog(devCtx, "[WARN] Kanban apply_result action failed: ${syncError::class.java.simpleName}: ${syncError.message}\n")
            SwingUtilities.invokeLater {
                appendChatLine("[Warn] Kanban sync failed: ${syncError.message}")
            }
        }.getOrDefault(JSONObject())
    }

    private fun runPostApplyTools(
        http: HttpAiClient?,
        context: ChatContext,
        response: IdeChatResponse,
        devCtx: DevwerkContext?
    ): JSONObject {
        val requests = postApplyRequests(response)
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

    private fun postApplyRequests(response: IdeChatResponse): List<ToolRequest> {
        val changedPaths = collectChangedPaths(response)
        if (changedPaths.isEmpty()) return response.toolRequests

        val requests = response.toolRequests.toMutableList()
        val hasIdeCompile = requests.any { it.tool == "ide_compile" }
        if (!hasIdeCompile) {
            requests += ToolRequest(
                id = "ide_compile",
                tool = "ide_compile",
                args = mapOf(
                    "timeout_seconds" to 300,
                    "max_errors" to 200,
                    "reason" to "Default post-apply IntelliJ CompilerManager verification."
                )
            )
        }
        val hasIdeSyntaxCheck = requests.any { it.tool == "ide_syntax_check" }
        if (!hasIdeSyntaxCheck) {
            requests += ToolRequest(
                id = "ide_syntax_check",
                tool = "ide_syntax_check",
                args = mapOf(
                    "paths" to emptyList<String>(),
                    "max_errors" to 200,
                    "reason" to "Default post-apply IDE diagnostics across the project."
                )
            )
        }
        return requests
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

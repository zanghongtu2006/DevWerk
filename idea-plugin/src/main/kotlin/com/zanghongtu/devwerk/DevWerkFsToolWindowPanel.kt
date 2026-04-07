package com.zanghongtu.devwerk

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.zanghongtu.devwerk.codeEditor.*
import com.zanghongtu.devwerk.settings.AiSettingsDialog
import java.awt.BorderLayout
import java.awt.Dimension
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Paths
import java.nio.file.StandardOpenOption
import javax.swing.*

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
    private val inputField      = JTextField()
    private val sendButton      = JButton("Send")
    private val settingsBtn     = JButton("⚙")

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
        val chatScroll = JScrollPane(chatArea)
        chatScroll.preferredSize = Dimension(0, 200)

        val topPanel = JPanel(BorderLayout())
        topPanel.add(JLabel("DevWerk"), BorderLayout.WEST)
        topPanel.add(settingsBtn, BorderLayout.EAST)

        val bottomPanel = JPanel(BorderLayout(4, 0))
        bottomPanel.add(inputField, BorderLayout.CENTER)
        bottomPanel.add(sendButton, BorderLayout.EAST)

        planArea.isEditable = false
        planArea.lineWrap = true
        planArea.wrapStyleWord = true
        planPanel.add(JScrollPane(planArea), BorderLayout.CENTER)
        planBtnsPanel.add(executeBtn)
        planBtnsPanel.add(cancelPlanBtn)
        planPanel.add(planBtnsPanel, BorderLayout.SOUTH)
        planPanel.isVisible = false

        add(topPanel, BorderLayout.NORTH)
        add(chatScroll, BorderLayout.CENTER)
        add(planPanel, BorderLayout.NORTH)
        add(bottomPanel, BorderLayout.SOUTH)

        sendButton.addActionListener { onSendClicked() }
        inputField.addActionListener { onSendClicked() }

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
                    inputField.isEnabled = true
                    planPanel.isVisible = false
                }
                State.PLAN_PENDING -> {
                    sendButton.isEnabled = false
                    inputField.isEnabled = false
                    planPanel.isVisible = false
                }
                State.PLAN_READY -> {
                    sendButton.isEnabled = false
                    inputField.isEnabled = false
                    planPanel.isVisible = true
                }
                State.EXECUTE_PENDING -> {
                    sendButton.isEnabled = false
                    inputField.isEnabled = false
                    planPanel.isVisible = false
                }
            }
        }
    }

    // ── Phase 1: Send → Plan ──────────────────────────────────────────────────

    private fun onSendClicked() {
        val text = inputField.text.trim()
        if (text.isEmpty()) return
        if (state != State.IDLE) return

        appendChatLine("You: $text")
        inputField.text = ""
        currentPlanUserMessage = text
        setState(State.PLAN_PENDING)

        ApplicationManager.getApplication().executeOnPooledThread {
            runPlanPhase(text)
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
        val plan = currentPlan ?: return
        val userMessage = currentPlanUserMessage

        try {
            val basePath = project.basePath
            val runner = DevwerkOperationRunner()
            val devCtx = if (!basePath.isNullOrBlank()) runner.beginOperation(project, Paths.get(basePath)) else null

            val chatCtx = ChatContext(
                projectRoot = project.basePath,
                history = history.toList(),
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
                if (!resp.codeTree.isNullOrBlank()) {
                    appendChatLine("=== Code Tree ==="); appendChatLine(resp.codeTree!!)
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
                if (!resp.codeTree.isNullOrBlank()) {
                    appendChatLine("=== Code Tree ==="); appendChatLine(resp.codeTree!!)
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

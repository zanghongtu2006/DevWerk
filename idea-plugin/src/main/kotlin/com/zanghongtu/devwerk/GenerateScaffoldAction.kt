package com.zanghongtu.devwerk

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.wm.ToolWindowManager

class GenerateScaffoldAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return

        // 找到我们刚才注册的 ToolWindow（id 必须和 plugin.xml 里的一致）
        ToolWindowManager.getInstance(project).getToolWindow("DevWerk")?.activate(null)
    }
}

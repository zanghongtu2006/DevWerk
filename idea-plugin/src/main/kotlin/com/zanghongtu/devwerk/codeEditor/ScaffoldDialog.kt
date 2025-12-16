//package com.zanghongtu.devwerk.codeEditor
//
//import com.intellij.openapi.project.Project
//import com.intellij.openapi.ui.DialogWrapper
//import com.intellij.openapi.vfs.LocalFileSystem
//import com.intellij.openapi.vfs.VirtualFile
//import java.awt.BorderLayout
//import javax.swing.*
//
//class ScaffoldDialog(private val project: Project) : DialogWrapper(project, true) {
//
//    private val basePathField = JTextField()
//    private val templateCombo = JComboBox<ScaffoldTemplate>()
//
//    init {
//        title = "DevWerk"
//
//        initTemplates()
//        init()  // DialogWrapper 的初始化，必须在最后调用
//    }
//
//    /**
//     * 初始化模板下拉框
//     */
//    private fun initTemplates() {
//        val templates = listOf(
//            ScaffoldTemplate.SimpleBackend,
//            ScaffoldTemplate.SimpleFrontend,
//            ScaffoldTemplate.EmptyModule
//        )
//        val model = DefaultComboBoxModel<ScaffoldTemplate>()
//        templates.forEach { model.addElement(it) }
//        templateCombo.model = model
//    }
//
//    /**
//     * DialogWrapper 要求实现的核心方法：
//     * 返回中间区域的主面板
//     */
//    override fun createCenterPanel(): JComponent {
//        val panel = JPanel(BorderLayout(0, 8))
//
//        val formPanel = JPanel()
//        formPanel.layout = BoxLayout(formPanel, BoxLayout.Y_AXIS)
//
//        // Base Path 行
//        val basePanel = JPanel(BorderLayout(8, 0))
//        basePanel.add(JLabel("Base Path:"), BorderLayout.WEST)
//        basePathField.text = project.basePath ?: ""
//        basePanel.add(basePathField, BorderLayout.CENTER)
//
//        // Template 行
//        val templatePanel = JPanel(BorderLayout(8, 0))
//        templatePanel.add(JLabel("Template:"), BorderLayout.WEST)
//        templatePanel.add(templateCombo, BorderLayout.CENTER)
//
//        formPanel.add(basePanel)
//        formPanel.add(Box.createVerticalStrut(8))
//        formPanel.add(templatePanel)
//
//        panel.add(formPanel, BorderLayout.NORTH)
//
//        return panel
//    }
//
//    /**
//     * 指定默认聚焦的组件
//     */
//    override fun getPreferredFocusedComponent(): JComponent? {
//        return basePathField
//    }
//
//    /**
//     * 返回选择的 Base 目录
//     */
//    fun getBaseDir(): VirtualFile? {
//        val basePath = basePathField.text.trim()
//        if (basePath.isEmpty()) return null
//        return LocalFileSystem.getInstance().findFileByPath(basePath)
//    }
//
//    /**
//     * 返回选择的模板
//     */
//    fun getSelectedTemplate(): ScaffoldTemplate {
//        return templateCombo.selectedItem as ScaffoldTemplate
//    }
//}

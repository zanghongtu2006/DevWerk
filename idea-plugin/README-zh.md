# DevWerk

DevWerk 是一款面向 IntelliJ 平台（IDEA / PyCharm / WebStorm 等）的 AI 辅助开发插件，
专注于 “可执行代码生成（CodeOps）”，而不仅仅是对话式补全。

与传统 AI 插件不同，DevWerk 要求模型输出结构化 JSON（包含文件树与文件操作），
由插件在本地 安全、可控地执行文件创建 / 修改 / 删除。

## 核心特性

🧠 CodeOps 协议
AI 必须输出标准化 JSON（reply / code_tree / ops），而不是自由文本。

📂 真实文件系统操作
支持：

创建目录

创建文件

覆盖更新文件

删除文件或目录

🔌 多模型支持（无需统一后端）

Ollama（本地）

OpenAI（GPT）

Google Gemini

TechZukunft（专用后端）

Custom HTTP（预留）

🧩 Prompt 内置于插件

通用模型（Ollama / GPT / Gemini）使用插件内置 Prompt + Schema

不依赖中心化服务器

便于开源与二次扩展

⚙️ 本地配置，不上传密钥

Token / URL / Model 均存储在本地 IDE 配置

插件不会上传你的密钥或代码

## 🖥 支持的 IDE

DevWerk 基于 IntelliJ Platform，理论上支持：

IntelliJ IDEA（Community / Ultimate）

PyCharm

WebStorm

GoLand

CLion

Rider（部分功能）

## 使用方式（简要）

打开右侧 DevWerk 工具窗口

点击 ⚙ 配置 AI Provider

在对话框中输入你的需求（例如“生成一个 Maven Java 项目”）

AI 返回 CodeOps JSON

插件自动在项目中创建 / 修改文件

## 开源说明

本项目将逐步开源 插件核心逻辑与基础 Prompt

高级 Prompt / 商业后端可能以独立方式提供

欢迎 Issue / PR / 讨论

## License

TBD（建议 Apache-2.0 或 MIT）
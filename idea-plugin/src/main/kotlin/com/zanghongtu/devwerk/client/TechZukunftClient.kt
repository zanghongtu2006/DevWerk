package com.zanghongtu.devwerk.client

import com.zanghongtu.devwerk.codeEditor.AiClient
import com.zanghongtu.devwerk.codeEditor.ChatContext
import com.zanghongtu.devwerk.codeEditor.HttpAiClient
import com.zanghongtu.devwerk.codeEditor.IdeChatResponse

class TechZukunftClient(
    private val endpoint: String,
    private val authToken: String?
) : AiClient {

    private val delegate = HttpAiClient(endpoint = endpoint, authToken = authToken)

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        return delegate.sendChat(context, userMessage)
    }
}

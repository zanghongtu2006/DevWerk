package com.zanghongtu.devwerk.client

import com.zanghongtu.devwerk.codeEditor.AiClient
import com.zanghongtu.devwerk.codeEditor.ChatContext
import com.zanghongtu.devwerk.codeEditor.HttpAiClient
import com.zanghongtu.devwerk.codeEditor.IdeChatResponse

class TechZukunftClient(
    private val endpoint: String,
    private val authToken: String?
) : AiClient {

    private val delegate = HttpAiClient(
        chatEndpoint = endpoint,
        authToken = authToken,
        planEndpoint = TODO(),
        executeEndpoint = TODO()
    )

    override fun sendChat(context: ChatContext, userMessage: String): IdeChatResponse {
        return delegate.sendChat(context, userMessage)
    }
}

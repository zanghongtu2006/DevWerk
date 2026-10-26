"""Anthropic-compatible native message and tool-call adapter."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests as http_requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.debug_trace import trace_json
from app.services.provider_errors import provider_timeout_error, raise_for_provider_payload, raise_for_provider_response
from app.services.provider_request_contracts import (
    AnthropicCompletionRequest, AnthropicToolChoice, AnthropicToolDefinition,
)
from app.services.provider_message_contracts import (AnthropicMessage, AnthropicTextBlock,
    AnthropicToolResultBlock, AnthropicToolUseBlock)
from app.v1.protocols.agent.provider_content import (
    AnthropicAssistantContent, AnthropicRedactedThinkingBlock, AnthropicThinkingBlock,
)
from app.services.provider_response_contracts import ProviderCompletionResponse, ProviderToolCall, ProviderUsage
from app.services.provider_stream_contracts import AnthropicStreamAssembler, SSEFrameDecoder
from app.v1.contracts import provider_contract_schema
from app.v1.protocols.tools.core.provider_schema_contracts import ProviderSchemaNode


_trace_log = logging.getLogger("devwerk.llm.provider.anthropic")


class AnthropicClient:
    def __init__(self, config: dict[str, Any]):
        self.last_usage: dict[str, Any] | None = None
        self.api_name = str(config["api_name"])
        self.base_url = str(config["base_url"]).rstrip("/")
        self.api_key = config.get("api_key")
        self.model = str(config["model"])
        self.temperature = float(config["temperature"])
        self.top_p = config.get("top_p")
        self.max_tokens = int(config["max_tokens"])
        self.request_timeout_seconds = float(config.get("request_timeout_seconds", 600.0))
        self.stream_response = bool(config.get("stream_response", False))
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.max_tokens <= 0:
            raise ValueError("Anthropic max_tokens must be positive")
        self.url = f"{self.base_url}/messages" if self.base_url.endswith("/v1") else f"{self.base_url}/v1/messages"
        self.session = http_requests.Session()
        self.session.trust_env = bool(config.get("trust_env_proxy", False))
        connect_max_attempts = int(config.get("connect_max_attempts", 3))
        if connect_max_attempts < 1:
            raise ValueError("connect_max_attempts must be positive")
        transport = HTTPAdapter(max_retries=Retry(
            total=connect_max_attempts - 1,
            connect=connect_max_attempts - 1,
            read=0,
            redirect=0,
            status=0,
            other=0,
            backoff_factor=0.5,
        ))
        self.session.mount("http://", transport)
        self.session.mount("https://", transport)
        if not self.api_key:
            raise ValueError(f"api_key is not set for LLM provider {self.api_name!r}.")

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *, trace_id: str | None = None, require_tool: bool = False, required_tool_name: str | None = None) -> dict[str, Any]:
        system, provider_messages = self._to_provider_messages(messages)
        request = AnthropicCompletionRequest(
            model=self.model, max_tokens=self.max_tokens, temperature=self.temperature,
            system=system, messages=provider_messages,
            top_p=float(self.top_p) if self.top_p is not None else None,
            stream=True if self.stream_response else None,
        )
        if tools:
            request.tools = [
                AnthropicToolDefinition(
                    name=item["function"]["name"],
                    description=item["function"].get("description", ""),
                    input_schema=provider_contract_schema(
                        item["function"].get("parameters") or ProviderSchemaNode(schema_type="object").wire()
                    ),
                )
                for item in tools
            ]
            if required_tool_name:
                if required_tool_name not in {item.name for item in request.tools}:
                    raise ValueError(f"required tool is not exposed: {required_tool_name}")
                request.tool_choice = AnthropicToolChoice(type="tool", name=required_tool_name)
            elif require_tool:
                request.tool_choice = AnthropicToolChoice(type="any")
        payload = request.wire()
        headers = {
            "x-api-key": str(self.api_key),
            "authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        trace_json(
            _trace_log,
            "llm.provider_request",
            trace_id=trace_id,
            provider="anthropic",
            api_name=self.api_name,
            model=self.model,
            url=self.url,
            payload=payload,
        )
        try:
            if self.stream_response:
                data, status_code, response_headers = self._stream_complete(payload, headers)
                response_body = data
            else:
                response = self.session.post(
                    self.url, json=payload, headers=headers,
                    timeout=self.request_timeout_seconds,
                )
                raise_for_provider_response(response, provider="anthropic", api_name=self.api_name)
                data, status_code, response_headers = response.json(), response.status_code, dict(response.headers)
                response_body = response.text
        except http_requests.Timeout as exc:
            raise provider_timeout_error(
                exc, provider="anthropic", api_name=self.api_name,
                timeout_seconds=self.request_timeout_seconds,
            ) from exc
        trace_json(
            _trace_log, "llm.provider_response", trace_id=trace_id,
            provider="anthropic", api_name=self.api_name, model=self.model,
            status_code=status_code, response_headers=response_headers, body=response_body,
        )
        raise_for_provider_payload(
            data,
            provider="anthropic",
            api_name=self.api_name,
            status_code=status_code,
            request_id=response_headers.get("request-id") or response_headers.get("x-request-id"),
        )
        self.last_usage = _usage(data.get("usage"))
        text: list[str] = []
        calls: list[ProviderToolCall] = []
        assistant_blocks = []
        for item in data.get("content") or []:
            if not isinstance(item, dict):
                raise ValueError("Anthropic assistant content block must be an object")
            if item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str):
                text.append(item["text"])
                assistant_blocks.append(AnthropicTextBlock(text=item["text"]))
            elif item.get("type") == "thinking":
                assistant_blocks.append(AnthropicThinkingBlock.model_validate(item))
            elif item.get("type") == "redacted_thinking":
                assistant_blocks.append(AnthropicRedactedThinkingBlock.model_validate(item))
            elif item.get("type") == "tool_use":
                arguments = item.get("input") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("tool_use input must be a JSON object")
                name = str(item.get("name") or "")
                calls.append(ProviderToolCall(id=str(item.get("id") or ""),
                    name=name, arguments=arguments))
                assistant_blocks.append(AnthropicToolUseBlock(
                    id=str(item.get("id") or ""), name=name, input=item.get("input") or {},
                ))
            else:
                raise ValueError(f"Unsupported Anthropic assistant block: {item.get('type')!r}")
        return ProviderCompletionResponse(text="\n".join(text).strip(), tool_calls=calls,
            usage=ProviderUsage.model_validate(self.last_usage),
            provider_content=AnthropicAssistantContent(blocks=assistant_blocks) if assistant_blocks else None).wire()

    def _stream_complete(self, payload: dict[str, Any], headers: dict[str, str]) -> tuple[dict[str, Any], int, dict[str, str]]:
        started = time.monotonic()
        decoder = SSEFrameDecoder()
        assembler = AnthropicStreamAssembler()
        stream_headers = dict(headers)
        stream_headers["accept"] = "text/event-stream"
        with self.session.post(
            self.url, json=payload, headers=stream_headers,
            timeout=self.request_timeout_seconds, stream=True,
        ) as response:
            raise_for_provider_response(response, provider="anthropic", api_name=self.api_name)
            for line in response.iter_lines(chunk_size=1):
                if time.monotonic() - started > self.request_timeout_seconds:
                    raise http_requests.Timeout("Anthropic stream exceeded the configured total request timeout")
                frame = decoder.feed(line)
                if frame is None:
                    continue
                if frame.event == "error":
                    raise_for_provider_payload(
                        json.loads(frame.data), provider="anthropic", api_name=self.api_name,
                        status_code=response.status_code,
                        request_id=response.headers.get("request-id") or response.headers.get("x-request-id"),
                    )
                    raise http_requests.ConnectionError("Anthropic stream sent an unclassified error event")
                if assembler.accept(frame):
                    result = assembler.result()
                    return result.model_dump(mode="json"), response.status_code, dict(response.headers)
        raise http_requests.ConnectionError("Anthropic stream ended before message_stop")

    @staticmethod
    def _to_provider_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        systems: list[str] = []
        converted: list[AnthropicMessage] = []
        for message in messages:
            role = str(message.get("role") or "user")
            if role == "system":
                systems.append(str(message.get("content") or ""))
                continue
            if role == "assistant":
                if message.get("provider_content") is not None:
                    blocks = AnthropicAssistantContent.model_validate(message["provider_content"]).blocks
                else:
                    blocks = []
                    if message.get("content"):
                        blocks.append(AnthropicTextBlock(text=str(message["content"])))
                    for call in message.get("tool_calls") or []:
                        function = call.get("function") or {}
                        arguments = function.get("arguments") or {}
                        if isinstance(arguments, str):
                            arguments = json.loads(arguments)
                        blocks.append(AnthropicToolUseBlock(
                            id=str(call.get("id") or ""),
                            name=str(function.get("name") or ""), input=arguments))
                if blocks:
                    converted.append(AnthropicMessage(role="assistant", content=blocks))
                continue
            if role == "tool":
                block = AnthropicToolResultBlock(
                    tool_use_id=str(message.get("tool_call_id") or ""),
                    content=str(message.get("content") or ""))
                if converted and converted[-1].role == "user" and isinstance(converted[-1].content, list):
                    converted[-1].content.append(block)
                else:
                    converted.append(AnthropicMessage(role="user", content=[block]))
                continue
            content = str(message.get("content") or "")
            if converted and converted[-1].role == "user" and isinstance(converted[-1].content, str):
                converted[-1].content += "\n\n" + content
            else:
                converted.append(AnthropicMessage(role="user", content=content))
        if not converted:
            converted.append(AnthropicMessage(role="user", content=""))
        return "\n\n".join(item for item in systems if item), [item.wire() for item in converted]


def _usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    input_tokens = value.get("input_tokens")
    output_tokens = value.get("output_tokens")
    total = input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else value.get("total_tokens")
    return ProviderUsage(input_tokens=input_tokens, output_tokens=output_tokens,
        total_tokens=total, cached_input_tokens=value.get("cache_read_input_tokens"),
        cache_creation_input_tokens=value.get("cache_creation_input_tokens")).wire()

"""MCP tools as ordinary DevWerk capabilities.

The transport stays behind the CapabilityRegistry boundary: MCP responses are
tool data, never conversation authority or a second agent channel.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.v1.capabilities import CapabilityEntry, CapabilityRegistry
from app.v1.domain import ToolResult
from app.v1.contracts import check_schema
from app.v1.protocols.tools.core.capability_protocol import CapabilityToolset
from app.v1.protocols.tools.core.provider_schema_contracts import ProviderSchemaNode
from app.v1.protocols.transport.mcp_contracts import (
    MCPInitializeParams, MCPJsonRpcMessage, MCPToolCallParams, MCPResultView, MCPTruncatedView,
)


log = logging.getLogger('devwerk.mcp')
_PROTOCOL_VERSION = '2025-06-18'
_MAX_RESULT_CHARS = 50_000


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    transport: str = Field(pattern='^(stdio|http)$')
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    timeout_seconds: float = Field(default=30, ge=1, le=300)

    @model_validator(mode='after')
    def complete(self):
        if self.transport == 'stdio' and not self.command:
            raise ValueError('stdio MCP server needs command')
        if self.transport == 'http' and not self.url:
            raise ValueError('HTTP MCP server needs url')
        return self


def load_mcp_servers(path: Path) -> dict[str, MCPServerConfig]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    if not isinstance(data, dict) or not isinstance(data.get('mcp_servers', {}), dict):
        raise ValueError('MCP configuration must contain an mcp_servers mapping')
    servers = {}
    for name, raw in data.get('mcp_servers', {}).items():
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', name):
            raise ValueError(f'Invalid MCP server name: {name!r}')
        servers[name] = MCPServerConfig.model_validate(raw)
    return servers


@dataclass
class _StdioTransport:
    config: MCPServerConfig
    process: subprocess.Popen | None = None
    lines: queue.Queue[Any] = field(default_factory=queue.Queue)
    lock: threading.RLock = field(default_factory=threading.RLock)
    serial: int = 0
    initialized: bool = False

    def _start(self):
        if self.process and self.process.poll() is None and self.initialized:
            return
        self.close()
        env = os.environ.copy()
        env.update(self.config.env)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.process = subprocess.Popen(
            [self.config.command, *self.config.args], cwd=self.config.cwd or None,
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            bufsize=1, creationflags=flags,
        )
        self.lines = queue.Queue()
        process, output = self.process, self.lines

        def read_lines():
            try:
                for line in process.stdout:
                    try:
                        output.put(json.loads(line))
                    except ValueError:
                        log.warning('MCP server emitted non-JSON stdout')
            finally:
                output.put(None)

        threading.Thread(target=read_lines, daemon=True, name='devwerk-mcp-reader').start()
        try:
            self._request('initialize', MCPInitializeParams(protocolVersion=_PROTOCOL_VERSION).wire())
            self._send(MCPJsonRpcMessage.notification('notifications/initialized'))
            self.initialized = True
        except Exception:
            self.close()
            raise

    def _send(self, message: MCPJsonRpcMessage):
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            raise ConnectionError('MCP stdio server is unavailable')
        self.process.stdin.write(json.dumps(message.wire(), ensure_ascii=False, separators=(',', ':')) + '\n')
        self.process.stdin.flush()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.serial += 1
        identity = self.serial
        self._send(MCPJsonRpcMessage.request(identity, method, params))
        while True:
            try:
                message = self.lines.get(timeout=self.config.timeout_seconds)
            except queue.Empty as exc:
                raise TimeoutError(f'MCP {method} timed out') from exc
            if message is None:
                raise ConnectionError('MCP stdio server closed its output')
            if not isinstance(message, dict):
                continue
            if 'method' in message and 'id' in message:
                self._send(MCPJsonRpcMessage.unavailable(message['id']))
                continue
            if message.get('id') != identity:
                continue
            if message.get('error'):
                raise RuntimeError(f'MCP {method}: {str(message["error"])[:1000]}')
            return message.get('result') or {}

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self._start()
            try:
                return self._request(method, params)
            except (ConnectionError, TimeoutError, BrokenPipeError):
                # A late response from this process must never satisfy a later
                # request. A mutating call is not retried: its outcome is unknown.
                self.close()
                raise

    def close(self):
        self.initialized = False
        process, self.process = self.process, None
        if process:
            if process.stdin:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if process.stdout:
                process.stdout.close()


@dataclass
class _HTTPTransport:
    config: MCPServerConfig
    client: httpx.Client = field(init=False)
    session_id: str | None = None
    serial: int = 0
    initialized: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self):
        self.client = httpx.Client(timeout=self.config.timeout_seconds, follow_redirects=False)

    def _send(self, message: MCPJsonRpcMessage) -> dict[str, Any]:
        headers = {'Accept':'application/json, text/event-stream',
                   'Content-Type':'application/json', 'MCP-Protocol-Version':_PROTOCOL_VERSION}
        if self.session_id:
            headers['Mcp-Session-Id'] = self.session_id
        response = self.client.post(self.config.url, json=message.wire(), headers=headers)
        if response.status_code in (404, 410) and self.session_id:
            self.session_id = None
            self.initialized = False
            raise ConnectionError('MCP HTTP session expired')
        response.raise_for_status()
        self.session_id = response.headers.get('mcp-session-id') or self.session_id
        if response.status_code == 202 or not response.content:
            return {}
        if 'text/event-stream' in response.headers.get('content-type', ''):
            events = [line[5:].strip() for line in response.text.splitlines() if line.startswith('data:')]
            for event in events:
                if event and event != '[DONE]':
                    result = json.loads(event)
                    if result.get('id') == message.id:
                        return result
            raise RuntimeError('MCP HTTP response contained no matching JSON-RPC result')
        payload = response.json()
        if message.id is not None and payload.get('id') != message.id:
            raise ValueError('MCP HTTP response has an unexpected request id')
        return payload

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            return self._request(method, params, allow_reconnect=True)

    def _request(self, method: str, params: dict[str, Any], *, allow_reconnect: bool) -> dict[str, Any]:
        try:
            if not self.initialized:
                self.serial += 1
                init = self._send(MCPJsonRpcMessage.request(
                    self.serial, 'initialize', MCPInitializeParams(protocolVersion=_PROTOCOL_VERSION).wire(),
                ))
                if init.get('error'):
                    raise RuntimeError(f'MCP initialize: {str(init["error"])[:1000]}')
                self._send(MCPJsonRpcMessage.notification('notifications/initialized'))
                self.initialized = True
            self.serial += 1
            response = self._send(MCPJsonRpcMessage.request(self.serial, method, params))
            if response.get('error'):
                raise RuntimeError(f'MCP {method}: {str(response["error"])[:1000]}')
            return response.get('result') or {}
        except ConnectionError:
            if allow_reconnect and method == 'tools/list':
                return self._request(method, params, allow_reconnect=False)
            # tools/call may have executed remotely. The registry's durable
            # execution receipt must keep this outcome uncertain.
            raise

    def close(self):
        self.client.close()


class MCPToolManager:
    def __init__(self, configs: dict[str, MCPServerConfig], registry: CapabilityRegistry):
        self.registry = registry
        self._aliases: dict[str, tuple[str, str]] = {}
        self.servers = {name: (_StdioTransport(config) if config.transport == 'stdio'
                               else _HTTPTransport(config)) for name, config in configs.items()}

    def discover(self) -> dict[str, list[str]]:
        discovered = {}
        for server_name, connection in self.servers.items():
            try:
                tools, cursor, seen_cursors = [], None, set()
                while True:
                    response = connection.request('tools/list', {'cursor':cursor} if cursor else {})
                    tools.extend(response.get('tools') or [])
                    cursor = response.get('nextCursor')
                    if not cursor:
                        break
                    if cursor in seen_cursors:
                        raise ValueError('MCP tools/list returned a repeated cursor')
                    seen_cursors.add(cursor)
                names = []
                for tool in tools:
                    tool_name = str(tool['name'])
                    alias = 'mcp__' + re.sub(r'[^A-Za-z0-9_]', '_', server_name) + '__' + re.sub(r'[^A-Za-z0-9_]', '_', tool_name)
                    owner = (server_name, tool_name)
                    if alias in self._aliases and self._aliases[alias] != owner:
                        log.warning('MCP tool alias collision alias=%s server=%s tool=%s', alias, server_name, tool_name)
                        continue
                    if self.registry.contains(alias) and alias not in self._aliases:
                        log.warning('MCP tool alias conflicts with an existing capability alias=%s', alias)
                        continue
                    schema = tool.get('inputSchema') or ProviderSchemaNode(schema_type='object').wire()
                    check_schema(schema, label=f'MCP {server_name}.{tool_name} input')
                    annotations = tool.get('annotations') or {}
                    read_only = bool(annotations.get('readOnlyHint'))

                    def handler(arguments, _ctx, *, server=server_name, name=tool_name, capability=alias):
                        response = self.servers[server].request(
                            'tools/call', MCPToolCallParams(name=name, arguments=arguments).model_dump(mode='json')
                        )
                        output = _bounded_result(response)
                        if response.get('isError'):
                            return ToolResult(ok=False, capability=capability, output=output,
                                              error={'type':'MCPToolError', 'message':str(output)[:1000]})
                        return output

                    if not self.registry.contains(alias):
                        self.registry.register(CapabilityEntry(
                            id=alias, description=str(tool.get('description') or tool_name)[:4000],
                            input_schema=schema, output_schema={}, handler=handler,
                            toolset=CapabilityToolset.MCP, side_effect_kind='read' if read_only else 'process',
                            delegable_to_column=True,
                        ))
                        self._aliases[alias] = owner
                    names.append(alias)
                discovered[server_name] = names
            except Exception as exc:
                log.warning('MCP discovery failed server=%s error=%s', server_name, str(exc)[:500])
                discovered[server_name] = []
        return discovered

    def close(self):
        for connection in self.servers.values():
            connection.close()


def _bounded_result(response: dict[str, Any]) -> dict[str, Any]:
    result = MCPResultView(content=response.get('content') or [])
    if response.get('structuredContent') is not None:
        result.structured_content = response['structuredContent']
    raw = json.dumps(result.model_dump(mode='json', exclude_none=True), ensure_ascii=False)
    if len(raw) > _MAX_RESULT_CHARS:
        return MCPTruncatedView(
            preview=raw[:_MAX_RESULT_CHARS], original_characters=len(raw),
        ).model_dump(mode='json')
    return result.model_dump(mode='json', exclude_none=True)

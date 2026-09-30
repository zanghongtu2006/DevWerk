import json
import sys

import httpx
import pytest

from app.v1.capabilities import CapabilityContext, build_core_registry
from app.v1.mcp_tools import MCPServerConfig, MCPToolManager, _HTTPTransport, _StdioTransport, load_mcp_servers


SERVER = r'''
import json,sys
for line in sys.stdin:
    message=json.loads(line)
    if 'id' not in message:
        continue
    method=message['method']
    if method=='initialize':
        result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
    elif method=='tools/list':
        result={'tools':[{'name':'echo','description':'Return the supplied value',
            'inputSchema':{'type':'object','required':['value'],
                'properties':{'value':{'type':'string'}},'additionalProperties':False},
            'annotations':{'readOnlyHint':True}}]}
    elif method=='tools/call':
        result={'content':[{'type':'text','text':'echo:'+message['params']['arguments']['value']}]}
    else:
        result={}
    print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
'''


def test_mcp_stdio_tool_discovered_and_dispatched_through_registry(store, tmp_path):
    script = tmp_path / 'mcp_fixture.py'
    script.write_text(SERVER, encoding='utf-8')
    config_path = tmp_path / 'mcp-servers.yaml'
    config_path.write_text('mcp_servers:\n  fixture:\n    transport: stdio\n'
                           f"    command: '{sys.executable}'\n"
                           f"    args: ['{script}']\n", encoding='utf-8')
    registry = build_core_registry()
    manager = MCPToolManager(load_mcp_servers(config_path), registry)
    try:
        assert manager.discover() == {'fixture':['mcp__fixture__echo']}
        project = store.create_project('mcp', '', str(tmp_path / 'project'))
        ctx = CapabilityContext(project['id'], project, store)
        entry = registry.resolve(['mcp__fixture__echo'], ctx)[0]
        assert entry.side_effect_kind == 'read'
        result = registry.dispatch('mcp__fixture__echo', {'value':'hello'}, ctx)
        assert result.ok, result.error
        assert result.output['content'] == [{'type':'text','text':'echo:hello'}]
    finally:
        manager.close()


def test_mcp_http_transport_accepts_streamable_sse_response():
    seen = []

    def respond(request):
        payload = json.loads(request.content)
        seen.append((payload['method'], request.headers.get('mcp-session-id')))
        if payload['method'] == 'notifications/initialized':
            return httpx.Response(202)
        if payload['method'] == 'initialize':
            return httpx.Response(200, json={'jsonrpc':'2.0','id':payload['id'],
                'result':{'protocolVersion':'2025-06-18'}}, headers={'mcp-session-id':'session-1'})
        event = json.dumps({'jsonrpc':'2.0','id':payload['id'],
            'result':{'tools':[{'name':'hello'}]}})
        return httpx.Response(200, text='event: message\ndata: '+event+'\n\n',
                              headers={'content-type':'text/event-stream'})

    connection = _HTTPTransport(MCPServerConfig(transport='http', url='http://mcp.test/mcp'))
    connection.client.close()
    connection.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert connection.request('tools/list', {})['tools'][0]['name'] == 'hello'
        assert seen == [('initialize', None), ('notifications/initialized', 'session-1'),
                        ('tools/list', 'session-1')]
    finally:
        connection.close()


def test_mcp_configuration_rejects_incomplete_server(tmp_path):
    path = tmp_path / 'mcp-servers.yaml'
    path.write_text('mcp_servers:\n  broken:\n    transport: stdio\n', encoding='utf-8')
    with pytest.raises(ValueError):
        load_mcp_servers(path)


def test_mcp_http_expired_session_reconnects_discovery_but_not_mutation():
    calls = []
    initialized = 0

    def respond(request):
        nonlocal initialized
        payload = json.loads(request.content)
        method = payload['method']
        calls.append((method, request.headers.get('mcp-session-id')))
        if method == 'initialize':
            initialized += 1
            return httpx.Response(200, json={'jsonrpc':'2.0','id':payload['id'],
                'result':{'protocolVersion':'2025-06-18'}},
                headers={'mcp-session-id':f'session-{initialized}'})
        if method == 'notifications/initialized':
            return httpx.Response(202)
        if len([call for call in calls if call[0] == method]) == 1:
            return httpx.Response(404)
        return httpx.Response(200, json={'jsonrpc':'2.0','id':payload['id'],
            'result':{'tools':[]}})

    connection = _HTTPTransport(MCPServerConfig(transport='http', url='http://mcp.test/mcp'))
    connection.client.close()
    connection.client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert connection.request('tools/list', {}) == {'tools':[]}
        assert initialized == 2
        with pytest.raises(ConnectionError, match='expired'):
            connection.request('tools/call', {'name':'write', 'arguments':{}})
        assert len([call for call in calls if call[0] == 'tools/call']) == 1
        assert not connection.initialized
    finally:
        connection.close()


def test_mcp_stdio_timeout_discards_old_process_and_reconnects(tmp_path):
    script = tmp_path / 'slow_server.py'
    script.write_text('''import json, sys, time
for line in sys.stdin:
    message = json.loads(line)
    if message.get('method') == 'tools/call':
        time.sleep(5)
    if 'id' in message:
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'tools':[]}}), flush=True)
''', encoding='utf-8')
    connection = _StdioTransport(MCPServerConfig(
        transport='stdio', command=sys.executable, args=[str(script)], timeout_seconds=1))
    try:
        assert connection.request('tools/list', {}) == {'tools':[]}
        previous = connection.process
        with pytest.raises(TimeoutError):
            connection.request('tools/call', {'name':'slow','arguments':{}})
        assert connection.process is None
        assert connection.request('tools/list', {}) == {'tools':[]}
        assert connection.process is not previous
    finally:
        connection.close()

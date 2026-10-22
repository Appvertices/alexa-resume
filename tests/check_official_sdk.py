"""Interoperability check against the official MCP Python SDK. Never downloads a dependency.

Run with a Python environment that has the official `mcp` package installed:
    python tests/check_official_sdk.py
Exit 77 means BLOCKED (SDK not installed), not passed.
"""
import asyncio
import importlib.metadata
import json
from pathlib import Path
import sys
import threading
from http.server import HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sdk_version = importlib.metadata.version('mcp')
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    try:
        from mcp.shared.exceptions import McpError
    except ImportError:  # SDK 2.x renamed it.
        from mcp.shared.exceptions import MCPError as McpError
except (importlib.metadata.PackageNotFoundError, ImportError) as exc:
    print(json.dumps({'status': 'BLOCKED', 'official_sdk_test_executed': False,
                      'reason': 'Compatible official Python MCP SDK is not installed locally.',
                      'detail': str(exc), 'network_download_attempted': False}, indent=2))
    sys.exit(77)

from server import make_handler


def field(obj, snake, camel):
    """SDK 2.x uses snake_case result fields; 1.x uses camelCase."""
    return getattr(obj, snake) if hasattr(obj, snake) else getattr(obj, camel)


async def check(url):
    # SDK 1.x yields (read, write, get_session_id); 2.x yields (read, write).
    async with streamable_http_client(url) as streams_a:
        async with ClientSession(*streams_a[:2]) as a:
            result = await a.initialize()
            assert field(result, 'protocol_version', 'protocolVersion') == '2025-11-25'
            tools = await a.list_tools()
            assert {tool.name for tool in tools.tools} == {
                'routine_state', 'routine_start', 'routine_turn', 'routine_restore'}
            await a.send_ping()
            done = await a.call_tool('routine_turn', {'text': 'done', 'version': 0})
            assert not field(done, 'is_error', 'isError')
            checkpoint = json.loads(done.content[0].text)['checkpoint']
            stale = await a.call_tool('routine_turn', {'text': 'done', 'version': 0})
            assert field(stale, 'is_error', 'isError')
            try:
                await a.call_tool('routine_turn', {'text': 'done', 'version': False})
            except McpError as exc:
                assert exc.error.code == -32602
            else:
                raise AssertionError('Boolean version was accepted')
            async with streamable_http_client(url) as streams_b:
                async with ClientSession(*streams_b[:2]) as b:
                    await b.initialize()
                    state = await b.call_tool('routine_state', {})
                    assert json.loads(state.content[0].text)['completed'] == 0
                    restored = await b.call_tool('routine_restore', {
                        'checkpoint': checkpoint, 'version': 0, 'confirmed': True})
                    assert not field(restored, 'is_error', 'isError')
                    assert json.loads(restored.content[0].text)['completed'] == 1


server = HTTPServer(('127.0.0.1', 0), make_handler())
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    asyncio.run(asyncio.wait_for(check(f'http://127.0.0.1:{server.server_port}/mcp'), timeout=30))
    print(json.dumps({'status': 'PASS', 'official_sdk_test_executed': True,
                      'sdk': 'mcp', 'sdk_version': sdk_version,
                      'scope': 'Local initialize, ping, tools, errors, isolation, checkpoint restore.'}, indent=2))
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

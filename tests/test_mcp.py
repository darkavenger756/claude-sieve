"""Tests for MCP server protocol."""


from claude_sieve.mcp_server import _handle_message


def test_initialize():
    msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
    responses = _handle_message(msg)
    assert len(responses) == 1
    result = responses[0].get('result', {})
    assert result['protocolVersion'] == '2024-11-05'
    assert 'tools' in result['capabilities']
    assert result['serverInfo']['name'] == 'claude-sieve'


def test_tools_list():
    msg = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}
    responses = _handle_message(msg)
    assert len(responses) == 1
    tools = responses[0]['result']['tools']
    assert len(tools) >= 4
    names = [t['name'] for t in tools]
    assert 'compress' in names
    assert 'framework_detect' in names
    assert 'diff_impact' in names
    assert 'stats' in names


def test_compress_tool():
    msg = {
        'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
        'params': {
            'name': 'compress',
            'arguments': {
                'output': '=== test session starts ===\nFAILED test\nE   assert 1 == 2\n',
                'framework': 'pytest',
            },
        },
    }
    responses = _handle_message(msg)
    assert len(responses) == 1
    result = responses[0].get('result', {})
    assert 'compressed' in result
    assert 'FAILED' in result['compressed']
    assert 'cache_key' in result


def test_stats_tool():
    msg = {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'stats', 'arguments': {}}}
    responses = _handle_message(msg)
    assert len(responses) == 1
    result = responses[0].get('result', {})
    assert 'total_compress_calls' in result
    assert result['total_compress_calls'] >= 1


def test_resources_list():
    msg = {'jsonrpc': '2.0', 'id': 5, 'method': 'resources/list', 'params': {}}
    responses = _handle_message(msg)
    assert len(responses) == 1
    resources = responses[0]['result']['resources']
    assert len(resources) >= 2
    uris = [r['uri'] for r in resources]
    assert 'sieve://stats/session' in uris
    assert 'sieve://tools/list' in uris


def test_resources_read():
    msg = {
        'jsonrpc': '2.0', 'id': 6, 'method': 'resources/read',
        'params': {'uri': 'sieve://stats/session'},
    }
    responses = _handle_message(msg)
    assert len(responses) == 1
    result = responses[0].get('result', {})
    assert 'text' in result


def test_handles_notifications():
    msg = {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}}
    responses = _handle_message(msg)
    assert responses == []


def test_handles_cancelled():
    msg = {'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {}}
    responses = _handle_message(msg)
    assert responses == []


def test_unknown_method():
    msg = {'jsonrpc': '2.0', 'id': 7, 'method': 'unknown', 'params': {}}
    responses = _handle_message(msg)
    assert len(responses) == 1
    assert 'error' in responses[0]

"""MCP (Model Context Protocol) server for Claude-Sieve.

Implements a JSON-RPC 2.0 server over stdin/stdout following the MCP
specification.  Exposes tools that LLM agents can invoke to compress
test output and analyse diffs.

Start with::

    clavesieve mcp

The server reads JSON-RPC messages from stdin and writes responses to
stdout.  Compatible with Claude Code, Cursor, Windsurf, Cline, and any
other MCP-compatible client.
"""

import json
import sys
from typing import Any, Callable

from .compressors import LogSieve
from .ast_parser import ASTAnalyzer
from . import __version__


# Session-level cumulative stats
_session_stats: dict[str, int] = {'total_compress_calls': 0, 'total_bytes_in': 0, 'total_bytes_out': 0}
_session_cache: dict[str, str] = {}


# ------------------------------------------------------------------
# Tool definitions
# ------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        'name': 'compress',
        'description': (
            'Compress raw test output text, retaining only semantically '
            'relevant error information.  Returns compressed text and '
            'compression statistics.  Use this when you have test failure '
            'output that needs to fit within a token budget.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'output': {
                    'type': 'string',
                    'description': 'Raw test output text to compress.',
                },
                'framework': {
                    'type': 'string',
                    'description': (
                        'Test framework (auto-detect if omitted).'
                    ),
                    'enum': ['auto', 'pytest', 'jest', 'mocha', 'go',
                             'unittest'],
                },
            },
            'required': ['output'],
        },
        'annotations': {
            'readOnlyHint': True,
            'idempotentHint': True,
        },
    },
    {
        'name': 'framework_detect',
        'description': (
            'Auto-detect the test framework used to produce a given '
            'output fragment.  Returns the framework name and confidence. '
            'Call this on the first 20 lines of test output to determine '
            'which framework flag to pass to compress.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'output': {
                    'type': 'string',
                    'description': 'Sample of test output (first ~20 lines).',
                },
            },
            'required': ['output'],
        },
        'annotations': {
            'readOnlyHint': True,
            'idempotentHint': True,
        },
    },
    {
        'name': 'diff_impact',
        'description': (
            'Analyse a git diff and return the set of modified Python '
            'symbols (classes, functions, methods) that could affect '
            'test output relevance.  Pass the result as context_nodes '
            'to compress for AST-aware filtering.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'diff': {
                    'type': 'string',
                    'description': 'Full git diff output (unified format).',
                },
                'repo_root': {
                    'type': 'string',
                    'description': (
                        'Repository root path (default: current directory).'
                    ),
                },
            },
            'required': ['diff'],
        },
        'annotations': {
            'readOnlyHint': True,
            'idempotentHint': True,
        },
    },
    {
        'name': 'stats',
        'description': (
            'Return cumulative compression statistics for the current '
            'MCP session.  Includes total calls, bytes in/out across all '
            'compress invocations since the server started.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {},
        },
        'annotations': {
            'readOnlyHint': True,
            'idempotentHint': True,
        },
    },
]

_RESOURCES: list[dict[str, Any]] = [
    {
        'uri': 'sieve://stats/session',
        'name': 'Session Statistics',
        'description': 'Cumulative compression statistics for the current session.',
        'mimeType': 'application/json',
    },
    {
        'uri': 'sieve://tools/list',
        'name': 'Available Tools',
        'description': 'List of all available tools and their schemas.',
        'mimeType': 'application/json',
    },
]


def _handle_compress(params: dict[str, Any]) -> dict[str, Any]:
    output = params.get('output', '')
    framework = params.get('framework', 'auto')
    forced = None if framework == 'auto' else framework
    sieve = LogSieve(forced_framework=forced)
    compressed = sieve.sieve(output)
    stats = dict(sieve.stats)
    global _session_stats
    _session_stats['total_compress_calls'] += 1
    _session_stats['total_bytes_in'] += int(stats.get('bytes_in', 0) or 0)
    _session_stats['total_bytes_out'] += int(stats.get('bytes_out', 0) or 0)
    cache_key = str(hash(output))
    _session_cache[cache_key] = compressed
    return {
        'compressed': compressed,
        'stats': {
            'bytes_in': stats.get('bytes_in', 0),
            'bytes_out': stats.get('bytes_out', 0),
            'lines_in': stats.get('lines_in', 0),
            'lines_out': stats.get('lines_out', 0),
            'framework': stats.get('framework') or 'auto-detected',
        },
        'cache_key': cache_key,
    }


def _handle_framework_detect(params: dict[str, Any]) -> dict[str, Any]:
    output = params.get('output', '')
    sieve = LogSieve()
    for line in output.splitlines():
        detected = sieve.detect_framework(line)
        if detected is not None:
            return {'framework': detected, 'confidence': 'high'}
    return {'framework': None, 'confidence': 'low'}


def _handle_diff_impact(params: dict[str, Any]) -> dict[str, Any]:
    diff = params.get('diff', '')
    repo_root = params.get('repo_root', '.')
    analyzer = ASTAnalyzer()
    nodes = analyzer.bulk_analyze(diff, repo_root=repo_root)
    return {
        'modified_symbols': nodes,
        'count': len(nodes),
    }


def _handle_stats(params: dict[str, Any]) -> dict[str, Any]:
    global _session_stats
    calls = _session_stats['total_compress_calls']
    b_in = _session_stats['total_bytes_in']
    b_out = _session_stats['total_bytes_out']
    reduction = 0.0
    if b_in > 0:
        reduction = (1.0 - b_out / b_in) * 100.0
    return {
        'total_compress_calls': calls,
        'total_bytes_in': b_in,
        'total_bytes_out': b_out,
        'total_reduction_percent': round(reduction, 1),
        'cache_entries': len(_session_cache),
    }


def _handle_resources_list() -> list[dict[str, Any]]:
    return _RESOURCES


def _handle_resources_read(uri: str) -> dict[str, Any]:
    global _session_stats, _session_cache
    if uri == 'sieve://stats/session':
        return {
            'uri': uri,
            'mimeType': 'application/json',
            'text': json.dumps(_handle_stats({}), indent=2),
        }
    if uri == 'sieve://tools/list':
        return {
            'uri': uri,
            'mimeType': 'application/json',
            'text': json.dumps(_TOOLS, indent=2),
        }
    if uri.startswith('sieve://cache/'):
        key = uri[len('sieve://cache/'):]
        val = _session_cache.get(key)
        if val is not None:
            return {
                'uri': uri,
                'mimeType': 'text/plain',
                'text': val,
            }
    return {
        'uri': uri,
        'text': f'Resource not found: {uri}',
        'isError': True,
    }


_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    'compress': _handle_compress,
    'framework_detect': _handle_framework_detect,
    'diff_impact': _handle_diff_impact,
    'stats': _handle_stats,
}


# ------------------------------------------------------------------
# JSON-RPC 2.0 message handling
# ------------------------------------------------------------------


def _make_error(
    code: int, message: str, data: Any = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {'code': code, 'message': message}
    if data is not None:
        err['data'] = data
    return err


def _make_response(
    req_id: Any, result: Any = None, error: Any = None,
) -> dict[str, Any]:
    resp: dict[str, Any] = {'jsonrpc': '2.0'}
    if error is not None:
        resp['error'] = error
    else:
        resp['result'] = result
    if req_id is not None:
        resp['id'] = req_id
    return resp


def _handle_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Process a single JSON-RPC message and return response(s)."""
    req_id = msg.get('id')
    method = msg.get('method', '')
    params = msg.get('params', {}) or {}

    if not isinstance(params, dict):
        params = {}

    # --- lifecycle ----------------------------------------------------
    if method == 'initialize':
        return [_make_response(req_id, {
            'protocolVersion': '2024-11-05',
            'capabilities': {
                'tools': {},
                'resources': {},
            },
            'serverInfo': {
                'name': 'claude-sieve',
                'version': __version__,
            },
        })]

    if method == 'notifications/initialized':
        return []

    if method == 'notifications/cancelled':
        return []

    # --- resources ----------------------------------------------------
    if method == 'resources/list':
        return [_make_response(req_id, {'resources': _RESOURCES})]

    if method == 'resources/read':
        uri = params.get('uri', '')
        if isinstance(uri, str) and uri:
            result = _handle_resources_read(uri)
            return [_make_response(req_id, result)]
        return [_make_response(
            req_id,
            error=_make_error(-32602, 'Missing or invalid uri parameter'),
        )]

    # --- tools --------------------------------------------------------
    if method == 'tools/list':
        return [_make_response(req_id, {'tools': _TOOLS})]

    if method == 'tools/call':
        tool_name = params.get('name', '')
        tool_args = params.get('arguments', {}) or {}
        handler = _TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return [_make_response(
                req_id,
                error=_make_error(-32601, f'Unknown tool: {tool_name}'),
            )]
        try:
            result = handler(tool_args)
            return [_make_response(req_id, result)]
        except Exception as exc:
            return [_make_response(
                req_id,
                error=_make_error(-32603, str(exc)),
            )]

    # --- fallback -----------------------------------------------------
    return [_make_response(
        req_id,
        error=_make_error(-32601, f'Unknown method: {method}'),
    )]


# ------------------------------------------------------------------
# Server entry point
# ------------------------------------------------------------------


def run_server() -> int:
    """Read JSON-RPC messages from stdin and write responses to stdout.

    Returns 0 on clean exit.
    """
    for raw_line in sys.stdin:
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            msg = json.loads(stripped)
        except json.JSONDecodeError:
            resp = _make_response(
                None, error=_make_error(-32700, 'Parse error'),
            )
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()
            continue

        if not isinstance(msg, dict):
            resp = _make_response(
                None, error=_make_error(-32600, 'Invalid Request'),
            )
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()
            continue

        try:
            responses = _handle_message(msg)
        except Exception as exc:
            resp = _make_response(
                msg.get('id'),
                error=_make_error(-32603, f'Internal error: {exc}'),
            )
            responses = [resp]

        for r in responses:
            sys.stdout.write(json.dumps(r) + '\n')
        sys.stdout.flush()

    return 0

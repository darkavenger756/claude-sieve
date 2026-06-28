"""CLI entry point, subshell lifecycle orchestration, and stream routing.

Usage::

    clavesieve pytest tests/ -x
    clavesieve --diff HEAD~1 -- pytest tests/
    git diff HEAD~1 | clavesieve --diff - -- npm test
    clavesieve --output json -- jest --verbose
    clavesieve mcp                          # MCP server mode
"""

import argparse
import os
import signal
import subprocess
import sys
from typing import Sequence

from . import __version__
from .ast_parser import ASTAnalyzer
from .compressors import LogSieve
from .config import load_config, merge_cli_overrides


_COLOR_RED = '\033[31m'
_COLOR_GREEN = '\033[32m'
_COLOR_YELLOW = '\033[33m'
_COLOR_CYAN = '\033[36m'
_COLOR_BOLD = '\033[1m'
_COLOR_RESET = '\033[0m'


def _color(text: str, code: str, use_color: bool) -> str:
    if use_color:
        return f'{code}{text}{_COLOR_RESET}'
    return text


# ======================================================================
# Helpers
# ======================================================================


def _resolve_diff_source(diff_arg: str) -> str:
    if diff_arg == '-':
        return sys.stdin.read()
    if os.path.isfile(diff_arg):
        with open(diff_arg, encoding='utf-8', errors='replace') as f:
            return f.read()
    try:
        result = subprocess.run(
            ['git', 'diff', diff_arg],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(
            f'[Claude-Sieve] Error running git diff: {exc}',
            file=sys.stderr,
        )
        return ''
    if result.returncode != 0:
        print(
            f'[Claude-Sieve] git diff exited with code {result.returncode}',
            file=sys.stderr,
        )
        return ''
    return result.stdout


def _truncate_output(text: str, max_bytes: int, strategy: str = 'head-tail') -> str:
    """Truncate *text* to at most *max_bytes* using the given *strategy*.

    Strategies:
      ``head-tail`` — keep the first 20% and last 20%.
      ``head``      — keep only the first *max_bytes*.
      ``tail``      — keep only the last *max_bytes*.
    """
    if max_bytes <= 0:
        return text
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text

    if strategy == 'head':
        return encoded[:max_bytes].decode('utf-8', errors='replace')

    if strategy == 'tail':
        return encoded[-max_bytes:].decode('utf-8', errors='replace')

    head_pct = 0.2
    tail_pct = 0.2
    head_n = int(max_bytes * head_pct)
    tail_n = int(max_bytes * tail_pct)

    head = encoded[:head_n]
    tail = encoded[-tail_n:]
    dropped = len(encoded) - head_n - tail_n
    middle = f'\n[... truncated {dropped:,} bytes ...]\n'.encode('utf-8')
    return (head + middle + tail).decode('utf-8', errors='replace')


def _build_report(
    compressed: str,
    stats: dict[str, int | str | None],
    output_format: str,
    use_color: bool = False,
) -> str:
    bytes_in = stats.get('bytes_in', 0)
    bytes_out = stats.get('bytes_out', 0)
    assert isinstance(bytes_in, int) and isinstance(bytes_out, int)

    reduction = 0.0
    if bytes_in > 0:
        reduction = (1.0 - bytes_out / bytes_in) * 100.0

    framework = stats.get('framework') or 'auto-detected'

    if output_format == 'json':
        import json
        return json.dumps(
            {
                'compressed_output': compressed,
                'stats': {
                    'bytes_in': bytes_in,
                    'bytes_out': bytes_out,
                    'lines_in': stats.get('lines_in', 0),
                    'lines_out': stats.get('lines_out', 0),
                    'framework': framework,
                },
                'reduction_percent': round(reduction, 1),
            },
            indent=2,
        )

    if output_format == 'compact':
        parts: list[str] = []
        stripped = compressed.rstrip()
        if stripped:
            parts.append(stripped)
        metric = (
            f'[Claude-Sieve] {bytes_in:,}B \u2192 {bytes_out:,}B '
            f'({reduction:.1f}% reduction)'
        )
        if use_color:
            metric = _color(metric, _COLOR_CYAN, True)
        parts.append(metric)
        return '\n'.join(parts)

    lines_in = stats.get('lines_in', 0)
    lines_out = stats.get('lines_out', 0)
    reduction_str = f'{reduction:.1f}%'
    if use_color:
        reduction_str = _color(reduction_str, _COLOR_GREEN if reduction > 50 else _COLOR_YELLOW, True)

    report = [
        _color('## Claude-Sieve Diagnostic Report', _COLOR_BOLD, use_color),
        '',
        '| Metric | Value |',
        '|---|---|',
        f'| Framework | {framework} |',
        f'| Bytes Processed | {bytes_in:,} |',
        f'| Bytes Emitted | {bytes_out:,} |',
        f'| Lines Processed | {lines_in:,} |',
        f'| Lines Emitted | {lines_out:,} |',
        f'| Reduction | {reduction_str} |',
        '',
    ]
    if compressed.strip():
        report.append('```')
        report.append(compressed.rstrip())
        report.append('```')
    else:
        report.append('_No filtered output._')

    return '\n'.join(report)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='clavesieve',
        description='Claude-Sieve: AST-aware test output compressor for LLM agents',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  clavesieve pytest tests/ -x\n'
            '  clavesieve --diff HEAD~1 -- pytest tests/\n'
            '  git diff HEAD~1 | clavesieve --diff - -- npm test\n'
            '  clavesieve --output json -- jest --verbose\n'
            '  clavesieve mcp                        # MCP server mode\n'
            '  clavesieve bench                      # Run benchmarks\n'
            '  clavesieve bench pytest 5             # 5 iterations on pytest\n'
        ),
    )
    parser.add_argument(
        '--diff', '-d',
        type=str,
        default=None,
        metavar='SOURCE',
        help=(
            'Git diff source: "-" for stdin (pipe from git diff), '
            'a file path to a saved patch, or a git revision range '
            '(e.g. HEAD~1 or main..feature).'
        ),
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        choices=['markdown', 'json', 'compact'],
        help='Output format for the diagnostic report (default: markdown).',
    )
    parser.add_argument(
        '--framework', '-f',
        type=str,
        default=None,
        choices=['auto', 'pytest', 'jest', 'mocha', 'go', 'unittest'],
        help='Force a test framework (default: auto-detect).',
    )
    parser.add_argument(
        '--max-output', '-m',
        type=int,
        default=None,
        metavar='BYTES',
        help='Maximum output bytes (truncates to head+tail if exceeded).',
    )
    parser.add_argument(
        '--config', '-c',
        type=str,
        default=None,
        metavar='PATH',
        help='Path to a JSON config file (auto-discovered if omitted).',
    )
    parser.add_argument(
        '--color',
        action='store_true',
        default=None,
        help='Force ANSI color output.',
    )
    parser.add_argument(
        '--no-color',
        action='store_true',
        default=None,
        dest='no_color',
        help='Disable ANSI color output.',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Emit diagnostic information to stderr.',
    )
    parser.add_argument(
        '--version', '-V',
        action='store_true',
        help='Show version and exit.',
    )
    parser.add_argument(
        'command',
        nargs=argparse.REMAINDER,
        help='Command to execute and filter.',
    )
    return parser


# ======================================================================
# Entry point
# ======================================================================


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f'claude-sieve v{__version__}')
        return 0

    # ---- 0. special subcommands: bench / mcp server -------------------
    if args.command and args.command[0] == 'bench':
        from .bench import run_bench, print_bench
        fw = None
        iterations = 3
        rest = args.command[1:]
        for token in rest:
            if token in ('pytest', 'jest', 'mocha', 'go', 'unittest'):
                fw = token
            elif token.lstrip('-').isdigit():
                iterations = int(token.lstrip('-'))
        results = run_bench(framework=fw, iterations=iterations)
        print_bench(results)
        return 0

    if args.command and args.command[0] == 'mcp':
        from .mcp_server import run_server
        return run_server()

    if not args.command:
        parser.print_help()
        return 1

    command: list[str] = list(args.command)
    if command and command[0] == '--':
        command = command[1:]

    # ---- config file --------------------------------------------------
    cfg = load_config(args.config)
    cfg = merge_cli_overrides(cfg, args)

    use_color = args.color if args.color else (not args.no_color if args.no_color else sys.stdout.isatty())
    output_format: str = cfg.get('default_output', 'markdown') or 'markdown'
    forced_framework: str | None = cfg.get('framework', 'auto')
    if forced_framework == 'auto':
        forced_framework = None
    max_output: int = cfg.get('max_output_bytes', 0) or 0
    truncate_strategy: str = cfg.get('truncate_strategy', 'head-tail') or 'head-tail'
    exclude_loggers: list[str] = cfg.get('exclude_loggers', []) or []

    # ---- 1. resolve AST context from diff --------------------------------
    context_nodes: list[dict] = []
    if args.diff is not None:
        diff_stream = _resolve_diff_source(args.diff)
        if diff_stream.strip():
            analyzer = ASTAnalyzer()
            context_nodes = analyzer.bulk_analyze(diff_stream)
            if cfg.get('verbose'):
                print(
                    f'[Claude-Sieve] AST context: {len(context_nodes)} '
                    f'modified symbol(s) detected',
                    file=sys.stderr,
                )

    # ---- 2. build sieve --------------------------------------------------
    sieve = LogSieve(context_nodes=context_nodes, forced_framework=forced_framework, exclude_loggers=exclude_loggers)

    # ---- 3. launch subprocess --------------------------------------------
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        print(
            f'[Claude-Sieve] Command not found: {" ".join(command)}',
            file=sys.stderr,
        )
        return 127
    except PermissionError:
        print(
            f'[Claude-Sieve] Permission denied: {" ".join(command)}',
            file=sys.stderr,
        )
        return 126

    # ---- 4. signal forwarding --------------------------------------------
    _install_signal_forwarders(process)

    # ---- 5. tee loop: capture + real-time passthrough --------------------
    output_lines: list[str] = []
    assert process.stdout is not None
    try:
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            output_lines.append(line)
    except BrokenPipeError:
        pass

    returncode = process.wait()
    _restore_signal_handlers()

    # ---- 6. non-zero exit → compress & report ----------------------------
    if returncode != 0:
        full_output = ''.join(output_lines)
        try:
            if full_output:
                cache_hit = False
                cache_enabled = cfg.get('cache_enabled', False)
                if cache_enabled:
                    from .cache import SieveCache
                    _cache = SieveCache(ttl=cfg.get('cache_ttl_seconds', 3600))
                    cached = _cache.get(full_output, forced_framework or 'auto')
                    if cached is not None:
                        compressed, stats = cached
                        cache_hit = True
                if not cache_hit:
                    compressed = sieve.sieve(full_output)
                    stats = sieve.stats
                    if cache_enabled:
                        _cache.set(full_output, forced_framework or 'auto', compressed, dict(stats))
                if max_output > 0:
                    compressed = _truncate_output(compressed, max_output, strategy=truncate_strategy)
            else:
                compressed = ''
                stats = sieve.stats
            report = _build_report(compressed, stats, output_format, use_color)
            print()
            print(report)
        except Exception as exc:
            print(
                f'[Claude-Sieve] Compression error: {exc}',
                file=sys.stderr,
            )

    return returncode


# ======================================================================
# Signal handling
# ======================================================================

_saved_handlers: dict[int, object] = {}


def _install_signal_forwarders(process: subprocess.Popen) -> None:
    _saved_handlers.clear()

    def _forward(signum: int, frame: object) -> None:
        if process.poll() is None:
            process.send_signal(signum)
        handler = _saved_handlers.get(signum)
        if callable(handler):
            handler(signum, frame)

    for sig in (signal.SIGINT, signal.SIGTERM):
        _saved_handlers[sig] = signal.signal(sig, _forward)


def _restore_signal_handlers() -> None:
    for sig, handler in _saved_handlers.items():
        signal.signal(sig, handler)  # type: ignore[arg-type]
    _saved_handlers.clear()


if __name__ == '__main__':
    sys.exit(main())

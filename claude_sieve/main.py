"""CLI entry point, subshell lifecycle orchestration, and stream routing.

Usage::

    clavesieve pytest tests/ -x
    clavesieve --diff HEAD~1 -- pytest tests/
    git diff HEAD~1 | clavesieve --diff - -- npm test
    clavesieve --output json -- jest --verbose
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


# ======================================================================
# Helpers
# ======================================================================


def _resolve_diff_source(diff_arg: str) -> str:
    """Return the raw text of a git diff from *diff_arg*.

    Accepts ``'-'`` (stdin), a path to an existing file, or a git
    revision range (e.g. ``'HEAD~1'``, ``'main..feature'``).
    """
    if diff_arg == '-':
        return sys.stdin.read()
    if os.path.isfile(diff_arg):
        with open(diff_arg, encoding='utf-8', errors='replace') as f:
            return f.read()
    # Treat as git revision range
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


def _build_report(
    compressed: str,
    stats: dict[str, int | str | None],
    output_format: str,
) -> str:
    """Format the sieve output and metrics into one of the supported
    report formats."""
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
        parts.append(
            f'[Claude-Sieve] {bytes_in:,}B \u2192 {bytes_out:,}B '
            f'({reduction:.1f}% reduction)'
        )
        return '\n'.join(parts)

    # Markdown (default)
    lines_in = stats.get('lines_in', 0)
    lines_out = stats.get('lines_out', 0)
    report = [
        '## Claude-Sieve Diagnostic Report',
        '',
        '| Metric | Value |',
        '|---|---|',
        f'| Framework | {framework} |',
        f'| Bytes Processed | {bytes_in:,} |',
        f'| Bytes Emitted | {bytes_out:,} |',
        f'| Lines Processed | {lines_in:,} |',
        f'| Lines Emitted | {lines_out:,} |',
        f'| Reduction | {reduction:.1f}% |',
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
        default='markdown',
        choices=['markdown', 'json', 'compact'],
        help='Output format for the diagnostic report (default: markdown).',
    )
    parser.add_argument(
        '--framework', '-f',
        type=str,
        default='auto',
        choices=['auto', 'pytest', 'jest', 'mocha', 'go', 'unittest'],
        help='Force a test framework (default: auto-detect).',
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
    """CLI entry point.  Returns the exit code of the child process."""
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f'claude-sieve v{__version__}')
        return 0

    if not args.command:
        parser.print_help()
        return 1

    # argparse.REMAINDER captures a leading '--' separator as the first
    # element when users write:  clavesieve -- pytest tests/
    command: list[str] = list(args.command)
    if command and command[0] == '--':
        command = command[1:]

    # ---- 1. resolve AST context from diff --------------------------------
    context_nodes: list[dict] = []
    if args.diff is not None:
        diff_stream = _resolve_diff_source(args.diff)
        if diff_stream.strip():
            analyzer = ASTAnalyzer()
            context_nodes = analyzer.bulk_analyze(diff_stream)
            if args.verbose:
                print(
                    f'[Claude-Sieve] AST context: {len(context_nodes)} '
                    f'modified symbol(s) detected',
                    file=sys.stderr,
                )

    # ---- 2. build sieve --------------------------------------------------
    forced_fw: str | None = (
        None if args.framework == 'auto' else args.framework
    )
    sieve = LogSieve(context_nodes=context_nodes, forced_framework=forced_fw)

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
        # stdout closed (e.g. piped to head)
        pass

    returncode = process.wait()
    _restore_signal_handlers()

    # ---- 6. non-zero exit → compress & report ----------------------------
    if returncode != 0:
        full_output = ''.join(output_lines)
        try:
            if full_output:
                compressed = sieve.sieve(full_output)
            else:
                compressed = ''
            stats = sieve.stats
            report = _build_report(compressed, stats, args.output)
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
        signal.signal(sig, handler)
    _saved_handlers.clear()


if __name__ == '__main__':
    sys.exit(main())

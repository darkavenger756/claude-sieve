"""Regex-driven stream transformation and telemetry-truncation engines.

The :class:`LogSieve` class provides a thread-safe, framework-aware text
stream filter that strips non-actionable stack telemetry (e.g. internal
pytest frames, ``node_modules`` frames) while retaining error signatures,
file paths, line numbers, and any lines that reference recently modified
code symbols.
"""

import os
import re
import threading
from typing import Iterator, Optional


# ======================================================================
# Framework filter tables
# Each table defines:
#   'keep'  — lines matching any of these patterns are *candidates* for output
#   'drop'  — lines matching any of these patterns are *excluded* (even if
#             they also match a keep pattern), unless force-retained via
#             AST context nodes.
# ======================================================================

_PYTEST: dict[str, list[re.Pattern[str]]] = {
    'keep': [
        re.compile(r'^FAILED\s+'),
        re.compile(r'^E\s+'),
        re.compile(r'^\S+\.py:\d+:\s+'),
        re.compile(r'^(\s{4,})?assert\b'),
        re.compile(r'^AssertionError'),
        re.compile(r'^={3,}\s+FAILURES\s+={3,}'),
        re.compile(r'^_{3,}\s+'),
        re.compile(r'^={3,}\s+short test summary\s+={3,}'),
        re.compile(r'^={3,}\s+\d+ passed'),
        re.compile(r'^={3,}\s+\d+ failed'),
        re.compile(r'^={3,}\s+\d+ (passed|failed|warnings?)'),
        re.compile(r'^\d+ passed'),
        re.compile(r'^\d+ failed'),
        re.compile(r'^tests collected'),
        re.compile(r'^\d+ warnings?'),
        re.compile(r'^=+ .+ =+$'),
        re.compile(r'^\-+ .+ \-+$'),
        re.compile(r'^-.+Captured stdout'),
        re.compile(r'^-.+Captured stderr'),
        re.compile(r'^-.+Captured log'),
        re.compile(r'^>'),
        re.compile(r'^\s+[-+].*\S'),
        re.compile(r'^\s+~+\s*$'),
        re.compile(r'^(PASSED|FAILED|ERROR)\s'),
        re.compile(r'^\.+$'),
        re.compile(r'^s+$'),
    ],
    'drop': [
        re.compile(r'/site-packages/'),
        re.compile(r'(?:lib|site-packages)/python3\.\d+/'),
        re.compile(r'/<frozen importlib'),
        re.compile(r'_pytest/'),
        re.compile(r'pluggy/'),
        re.compile(r'py\.py'),
        re.compile(r'/conftest\.py'),
        re.compile(r'/virtualenv/'),
        re.compile(r'/venv/'),
        re.compile(r'/\.tox/'),
        re.compile(r'/\.direnv/'),
        re.compile(r'lib64/python3\.\d+/'),
    ],
}

_JEST: dict[str, list[re.Pattern[str]]] = {
    'keep': [
        re.compile(r'^(PASS|FAIL)\s'),
        re.compile(r'^(Test Suites:|Tests:)'),
        re.compile(r'^\s+●\s'),
        re.compile(r'^\s+at\s+.+\.\w+:\d+:\d+'),
        re.compile(r'^\s+expect\('),
        re.compile(r'^\s+Received:'),
        re.compile(r'^\s+Expected:'),
        re.compile(r'^\s+Difference:'),
        re.compile(r'^\s+>\s+\d+'),
        re.compile(r'^\s+\d+\|'),
        re.compile(r'^\s+\^+'),
        re.compile(r'^\s{10,}\S'),
    ],
    'drop': [
        re.compile(r'/node_modules/'),
        re.compile(r'\(node:internal/'),
        re.compile(r'jest-runtime'),
        re.compile(r'jest-jasmine'),
        re.compile(r'jest-runner'),
        re.compile(r'jest-config'),
        re.compile(r'jest-each'),
        re.compile(r'jest-matcher-utils'),
        re.compile(r'jest-message-util'),
        re.compile(r'jest-util'),
        re.compile(r'jest-worker'),
        re.compile(r'jest-cli'),
        re.compile(r'jest-haste-map'),
        re.compile(r'jest-resolve'),
        re.compile(r'jest-regex-util'),
        re.compile(r'jest-snapshot'),
        re.compile(r'^\s+at\s+processTicksAndRejections'),
        re.compile(r'^\s+at\s+async\s+'),
        re.compile(r'^\s+at\s+new Promise'),
        re.compile(r'^\s+at\s+<anonymous>'),
        re.compile(r'^\s+at\s+Object\.<anonymous>'),
    ],
}

_MOCHA: dict[str, list[re.Pattern[str]]] = {
    'keep': [
        re.compile(r'^\d+ (passing|failing|pending)'),
        re.compile(r'^\s+\d+\)'),
        re.compile(r'^\s+at\s+.+\.\w+:\d+:\d+'),
        re.compile(r'^\s+Error:'),
        re.compile(r'^\s+assert\b'),
        re.compile(r'^\s+expected\b'),
        re.compile(r'^\s+actual\b'),
        re.compile(r'^\s+-\s+'),
        re.compile(r'^\s\+\s+'),
        re.compile(r'^  \S'),
    ],
    'drop': [
        re.compile(r'/node_modules/'),
        re.compile(r'mocha/lib/'),
        re.compile(r'mocha/node_modules/'),
        re.compile(r'\(node:internal/'),
        re.compile(r'^\s+at\s+processTicksAndRejections'),
        re.compile(r'^\s+at\s+new Promise'),
        re.compile(r'^\s+at\s+Context\.<anonymous>'),
    ],
}

_GO: dict[str, list[re.Pattern[str]]] = {
    'keep': [
        re.compile(r'^=== RUN\s'),
        re.compile(r'^--- FAIL:\s'),
        re.compile(r'^(ok\s+|FAIL\s+)'),
        re.compile(r'^\s+\S+\.go:\d+'),
        re.compile(r'^\s+Error'),
        re.compile(r'^\s+Error Trace'),
        re.compile(r'^\tError'),
        re.compile(r'^\tMessage'),
        re.compile(r'^\tExpected'),
        re.compile(r'^\tActual'),
    ],
    'drop': [
        re.compile(r'/usr/local/go/src/'),
        re.compile(r'\(0x[0-9a-f]+\)'),
        re.compile(r'/go/pkg/mod/'),
        re.compile(r'\.\.\.'),
    ],
}

_UNITTEST: dict[str, list[re.Pattern[str]]] = {
    'keep': [
        re.compile(r'^(OK|FAILED)\s*$'),
        re.compile(r'^Ran \d+ test'),
        re.compile(r'^Traceback'),
        re.compile(r'^\s+File\s+\".+\.py\"'),
        re.compile(r'^AssertionError'),
        re.compile(r'^\w+Error:'),
        re.compile(r'^\s+assert\b'),
    ],
    'drop': [
        re.compile(r'/site-packages/'),
        re.compile(r'(?:lib|site-packages)/python3\.\d+/'),
        re.compile(r'/unittest/'),
    ],
}

_FRAMEWORK_TABLES: dict[str, dict[str, list[re.Pattern[str]]]] = {
    'pytest': _PYTEST,
    'jest': _JEST,
    'mocha': _MOCHA,
    'go': _GO,
    'unittest': _UNITTEST,
    'auto': _PYTEST,  # fallback
}


# ======================================================================
# Heuristic framework probes (ordered by specificity)
# ======================================================================

_FRAMEWORK_PROBES: list[tuple[str, list[re.Pattern[str]]]] = [
    ('pytest', [re.compile(r'^={3,}\s+test session starts\s+={3,}')]),
    ('pytest', [re.compile(r'FAILURES')]),
    ('pytest', [re.compile(r'^={3,}\s+FAILURES\s+={3,}')]),
    ('jest', [re.compile(r'^(PASS|FAIL)\s')]),
    ('jest', [re.compile(r'^Test Suites:')]),
    ('mocha', [re.compile(r'passing'), re.compile(r'failing')]),
    ('go', [re.compile(r'^=== RUN\s')]),
    ('go', [re.compile(r'^--- FAIL:')]),
    ('unittest', [re.compile(r'^Ran \d+ test')]),
    ('unittest', [re.compile(r'^FAILED \(')]),
]


class LogSieve:
    """Thread-safe, framework-aware test-output filter.

    Parameters
    ----------
    context_nodes:
        Optional list of modified-symbol dicts (as produced by
        :class:`~claude_sieve.ast_parser.ASTAnalyzer`).  Lines
        containing any ``file_path`` from these nodes are always
        retained regardless of drop-pattern matches.
    forced_framework:
        If given, skip auto-detection and use this framework's
        filter table.  One of ``'pytest'``, ``'jest'``, ``'mocha'``,
        ``'go'``, or ``None`` for auto-detect.
    """

    def __init__(
        self,
        context_nodes: Optional[list[dict]] = None,
        forced_framework: Optional[str] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._context_nodes = context_nodes or []
        self._forced_framework = forced_framework
        self._stats: dict[str, int | str | None] = {
            'bytes_in': 0,
            'bytes_out': 0,
            'lines_in': 0,
            'lines_out': 0,
            'framework': forced_framework,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, int | str | None]:
        with self._lock:
            return dict(self._stats)

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = {
                'bytes_in': 0,
                'bytes_out': 0,
                'lines_in': 0,
                'lines_out': 0,
                'framework': self._forced_framework,
            }

    def detect_framework(self, line: str) -> Optional[str]:
        """Probe a single output line and return the detected framework
        name, or ``None`` if no probe matches."""
        stripped = line.rstrip('\n\r')
        for fw, patterns in _FRAMEWORK_PROBES:
            for pat in patterns:
                if pat.search(stripped):
                    return fw
        return None

    def sieve(self, stream: str) -> str:
        """Filter a complete text stream and return only the lines deemed
        relevant after applying the active framework's keep/drop rules.

        Metrics are accumulated in :attr:`stats`.
        """
        lines = stream.splitlines(keepends=True)
        output_lines: list[str] = []
        framework: Optional[str] = self._forced_framework
        framework_locked = framework is not None

        with self._lock:
            raw = stream.encode('utf-8')
            self._stats['bytes_in'] += len(raw)
            self._stats['lines_in'] += len(lines)

        for line in lines:
            stripped = line.rstrip('\n\r')

            if not framework_locked:
                detected = self.detect_framework(stripped)
                if detected is not None:
                    framework = detected
                    framework_locked = True
                    with self._lock:
                        self._stats['framework'] = framework

            fw = framework or 'pytest'
            table = _FRAMEWORK_TABLES.get(fw, _PYTEST)

            if self._passes_filter(stripped, table):
                output_lines.append(line)

        result = ''.join(output_lines)

        with self._lock:
            out_bytes = result.encode('utf-8')
            self._stats['bytes_out'] += len(out_bytes)
            self._stats['lines_out'] += len(output_lines)

        return result

    def sieve_stream(self, stream: Iterator[str]) -> Iterator[str]:
        """Streaming variant of :meth:`sieve`.

        Yields lines as they arrive, buffering only the detection
        window for framework auto-detect.  Useful for very large
        outputs that would be expensive to hold in memory.
        """
        buffer: list[str] = []
        framework: Optional[str] = self._forced_framework
        framework_locked = framework is not None

        with self._lock:
            self._stats['lines_in'] = 0
            self._stats['bytes_in'] = 0
            self._stats['lines_out'] = 0
            self._stats['bytes_out'] = 0

        first_pass = True
        for line in stream:
            stripped = line.rstrip('\n\r')

            if not framework_locked:
                buffer.append(line)
                detected = self.detect_framework(stripped)
                if detected is not None:
                    framework = detected
                    framework_locked = True
                    # Re-process buffered lines with known framework
                    fw = framework or 'pytest'
                    table = _FRAMEWORK_TABLES.get(fw, _PYTEST)
                    for buf_line in buffer:
                        if self._passes_filter(
                            buf_line.rstrip('\n\r'), table
                        ):
                            with self._lock:
                                raw = buf_line.encode('utf-8')
                                self._stats['bytes_out'] += len(raw)
                                self._stats['lines_out'] += 1
                            yield buf_line
                    buffer.clear()
                if first_pass and len(buffer) >= 100:
                    # detect failed — default to pytest
                    framework = 'pytest'
                    framework_locked = True
                    fw = 'pytest'
                    table = _FRAMEWORK_TABLES.get(fw, _PYTEST)
                    for buf_line in buffer:
                        if self._passes_filter(
                            buf_line.rstrip('\n\r'), table
                        ):
                            with self._lock:
                                raw = buf_line.encode('utf-8')
                                self._stats['bytes_out'] += len(raw)
                                self._stats['lines_out'] += 1
                            yield buf_line
                    buffer.clear()
                    with self._lock:
                        self._stats['framework'] = 'pytest'
                first_pass = False
                continue

            fw = framework or 'pytest'
            table = _FRAMEWORK_TABLES.get(fw, _PYTEST)

            if self._passes_filter(stripped, table):
                with self._lock:
                    raw = line.encode('utf-8')
                    self._stats['bytes_out'] += len(raw)
                    self._stats['lines_out'] += 1
                yield line

            with self._lock:
                raw = line.encode('utf-8')
                self._stats['bytes_in'] += len(raw)
                self._stats['lines_in'] += 1

    def extend_patterns(
        self,
        fw: str,
        keep: list[str] | None = None,
        drop: list[str] | None = None,
    ) -> None:
        """Extend the keep/drop patterns for a given framework with
        custom regex strings (loaded from config file)."""
        table = _FRAMEWORK_TABLES.get(fw, _PYTEST)
        if keep:
            table['keep'].extend(re.compile(p) for p in keep)
        if drop:
            table['drop'].extend(re.compile(p) for p in drop)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _passes_filter(self, line: str, table: dict[str, list[re.Pattern[str]]]) -> bool:
        """Return ``True`` if *line* should be part of the compressed output."""
        # Short-circuit: empty lines are never kept
        if not line:
            return False

        # Drop rules (short-circuit if matched, unless context-retained)
        for pattern in table['drop']:
            if pattern.search(line):
                if self._is_context_relevant(line):
                    return True
                return False

        # Keep rules
        for pattern in table['keep']:
            if pattern.search(line):
                return True

        return False

    def _is_context_relevant(self, line: str) -> bool:
        if not self._context_nodes:
            return False
        for node in self._context_nodes:
            fp = node.get('file_path', '')
            if not fp:
                continue
            if fp in line:
                return True
            tail = os.path.basename(fp)
            if tail and tail in line:
                return True
        return False

"""AST-aware differential analyzer for Python source files.

Maps modified line ranges from a git diff stream to the exact semantic
symbols (classes, functions, methods) they affect, enabling downstream
filter engines to prioritise error telemetry from modified namespaces.
"""

import ast
import os
import re
from functools import lru_cache
from typing import Iterator


_HUNK_HEADER = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')
_PLUS_FILE = re.compile(r'^\+\+\+\s+(?:b/)?(.+)$')


class ASTAnalyzer:
    """Computes the set of semantic symbols affected by a git diff stream.

    Usage::

        analyzer = ASTAnalyzer()
        nodes = analyzer.bulk_analyze(diff_stream, repo_root='/path/to/repo')
        # nodes -> [{'type': 'function', 'name': 'validate', ...}, ...]
    """

    def __init__(self) -> None:
        self._file_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_modified_nodes(self, file_path: str, git_diff_stream: str) -> list[dict]:
        """Return all class / function / method symbols whose line ranges
        overlap with the line-level changes described in *git_diff_stream*
        for the given *file_path*.

        Parameters
        ----------
        file_path:
            Absolute or relative path to an existing ``.py`` file.
        git_diff_stream:
            Complete output of ``git diff`` (unified format) that may or
            may not contain hunks for *file_path*.

        Returns
        -------
        list[dict]:
            Each dict has keys ``type``, ``name``, ``parent``,
            ``start_line``, ``end_line``, ``file_path``, ``status``.
            Returns an empty list when the file is unchanged or absent.
        """
        if not os.path.isfile(file_path):
            return []

        source = self._read_file(file_path)
        per_file = self._parse_diff_hunks(git_diff_stream)

        hunk_text = self._resolve_hunk(per_file, file_path)
        if hunk_text is None:
            return []

        modified_lines = _collect_target_lines(hunk_text)
        if not modified_lines:
            return []

        tree = self._safe_parse(source, file_path)
        if tree is None:
            return []

        self._annotate_parents(tree)
        symbols = _extract_symbols(tree)

        result: list[dict] = []
        for sym in symbols:
            sym_lines = set(range(sym['start_line'], sym['end_line'] + 1))
            if sym_lines & modified_lines:
                result.append({
                    'type': sym['type'],
                    'name': sym['name'],
                    'parent': sym['parent'],
                    'start_line': sym['start_line'],
                    'end_line': sym['end_line'],
                    'file_path': os.path.abspath(file_path),
                    'status': 'modified',
                })
        return result

    def bulk_analyze(self, git_diff_stream: str, repo_root: str = '.') -> list[dict]:
        """Analyse a multi-file ``git diff`` stream and return the union
        of all modified symbols across every changed ``.py`` file.

        Parameters
        ----------
        git_diff_stream:
            Complete ``git diff`` output in unified format.
        repo_root:
            Directory the diff is relative to (default: CWD).

        Returns
        -------
        list[dict]:
            Aggregated list of modified-node dicts (see
            :meth:`get_modified_nodes`).
        """
        all_nodes: list[dict] = []
        per_file = self._parse_diff_hunks(git_diff_stream)

        for file_path in per_file:
            if not file_path.endswith('.py'):
                continue

            resolved = _resolve_path(file_path, repo_root)
            if resolved is None:
                continue

            try:
                nodes = self.get_modified_nodes(resolved, git_diff_stream)
                all_nodes.extend(nodes)
            except (OSError, SyntaxError, ValueError):
                continue

        return all_nodes

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_file(self, path: str) -> str:
        if path not in self._file_cache:
            with open(path, encoding='utf-8', errors='replace') as f:
                self._file_cache[path] = f.read()
        return self._file_cache[path]

    def _safe_parse(self, source: str, filename: str) -> ast.AST | None:
        try:
            return ast.parse(source, filename=filename)
        except SyntaxError:
            return None

    @lru_cache(maxsize=256)
    def _parse_diff_hunks(self, diff_stream: str) -> dict[str, str]:
        files: dict[str, str] = {}
        current_file: str | None = None
        current_hunk: list[str] = []

        for line in diff_stream.splitlines(keepends=True):
            if line.startswith('diff --git '):
                self._flush_hunk(files, current_file, current_hunk)
                current_file = None
                current_hunk = []
                continue

            match = _PLUS_FILE.match(line)
            if match:
                self._flush_hunk(files, current_file, current_hunk)
                current_file = match.group(1)
                current_hunk = []
                continue

            if current_file is not None:
                current_hunk.append(line)

        self._flush_hunk(files, current_file, current_hunk)
        return files

    @staticmethod
    def _flush_hunk(
        store: dict[str, str],
        path: str | None,
        lines: list[str],
    ) -> None:
        if path is not None and lines:
            store[path] = ''.join(lines)

    def _resolve_hunk(
        self, per_file: dict[str, str], file_path: str
    ) -> str | None:
        hunk = per_file.get(file_path)
        if hunk is not None:
            return hunk
        abs_target = os.path.abspath(file_path)
        for key, val in per_file.items():
            if os.path.abspath(key) == abs_target:
                return val
            if key.endswith(file_path) or file_path.endswith(key):
                return val
        return None

    @staticmethod
    def _annotate_parents(node: ast.AST, parent: ast.AST | None = None) -> None:
        node.parent = parent  # type: ignore[attr-defined]
        for child in ast.iter_child_nodes(node):
            ASTAnalyzer._annotate_parents(child, node)


# ======================================================================
# Module-level helpers
# ======================================================================


def _collect_target_lines(hunk_text: str) -> set[int]:
    """Extract the set of *new*-file line numbers touched by a hunk."""
    lines: set[int] = set()
    for line in hunk_text.splitlines():
        m = _HUNK_HEADER.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) else 1
            lines.update(range(start, start + count))
    return lines


def _extract_symbols(tree: ast.AST) -> list[dict]:
    """Walk an AST and return all class, function, and method definitions."""
    symbols: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = getattr(node, 'parent', None)
            parent_name: str = ''
            if isinstance(parent, ast.ClassDef):
                parent_name = parent.name
            sym_type: str
            if isinstance(node, ast.ClassDef):
                sym_type = 'class'
            elif parent_name:
                sym_type = 'method'
            else:
                sym_type = 'function'
            symbols.append({
                'type': sym_type,
                'name': node.name,
                'parent': parent_name,
                'start_line': node.lineno,
                'end_line': getattr(node, 'end_lineno', node.lineno),
            })
    return symbols


def _resolve_path(file_path: str, repo_root: str) -> str | None:
    """Try to locate *file_path* relative to *repo_root* or as-is."""
    candidates = [
        file_path if os.path.isabs(file_path) else os.path.join(repo_root, file_path),
        file_path,
    ]
    for p in candidates:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None

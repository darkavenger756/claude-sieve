"""Tree-sitter based AST analysis for deep code understanding.

Requires the ``[treesitter]`` extra (``pip install claude-sieve[treesitter]``).
Falls back to the stdlib ``ast`` parser when tree-sitter is unavailable.
"""


from .ast_parser import ASTAnalyzer


_TS_AVAILABLE = False
try:
    import tree_sitter  # type: ignore[import-not-found]  # noqa: F401
    _TS_AVAILABLE = True
except ImportError:
    pass


class DeepASTAnalyzer(ASTAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self._ts_available = _TS_AVAILABLE
        self._language = None
        if self._ts_available:
            self._init_language()

    def _init_language(self) -> None:
        try:
            from tree_sitter import Language, Parser  # type: ignore[import-not-found]
            self._parser = Parser()
            try:
                PYTHON_LANGUAGE = Language('build/my-languages.so', 'python')
                self._parser.set_language(PYTHON_LANGUAGE)
            except Exception:
                try:
                    import tree_sitter_python as tspython  # type: ignore[import-not-found]
                    self._parser.set_language(tspython.language())
                except Exception:
                    self._ts_available = False
        except Exception:
            self._ts_available = False

    def get_modified_nodes(self, file_path: str, git_diff_stream: str) -> list[dict]:
        if not self._ts_available:
            return super().get_modified_nodes(file_path, git_diff_stream)
        return self._ts_get_modified_nodes(file_path, git_diff_stream)

    def _ts_get_modified_nodes(self, file_path: str, git_diff_stream: str) -> list[dict]:
        import os
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
        try:
            tree = self._parser.parse(bytes(source, 'utf-8'))
        except Exception:
            return super().get_modified_nodes(file_path, git_diff_stream)
        symbols = _ts_extract_symbols(tree.root_node, source)
        result = []
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


def _collect_target_lines(hunk_text: str) -> set[int]:
    import re
    lines: set[int] = set()
    header = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')
    for line in hunk_text.splitlines():
        m = header.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) else 1
            lines.update(range(start, start + count))
    return lines


def _ts_extract_symbols(node, source: str) -> list[dict]:
    symbols = []
    if node.type in ('class_definition', 'function_definition', 'method_definition'):
        name_node = node.child_by_field_name('name')
        name = source[name_node.start_byte:name_node.end_byte] if name_node else '<anon>'
        parent_name = ''
        parent = node.parent
        while parent is not None:
            if parent.type == 'class_definition':
                pname = parent.child_by_field_name('name')
                if pname:
                    parent_name = source[pname.start_byte:pname.end_byte]
                break
            parent = parent.parent
        sym_type = 'class' if node.type == 'class_definition' else 'method' if parent_name else 'function'
        symbols.append({
            'type': sym_type,
            'name': name,
            'parent': parent_name,
            'start_line': node.start_point[0] + 1,
            'end_line': node.end_point[0] + 1,
        })
    for child in node.children:
        symbols.extend(_ts_extract_symbols(child, source))
    return symbols

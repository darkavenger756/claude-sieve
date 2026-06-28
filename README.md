# Claude-Sieve

> **Zero-dependency, AST-aware test output compressor for terminal-based LLM agents**  
> *Mitigates token inflation and context-window degradation during automated test cycles.*

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](#)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](#)
[![Dependencies: 0](https://img.shields.io/badge/dependencies-0-brightgreen)](#)
[![MCP](https://img.shields.io/badge/MCP-ready-6366f1)](#)
[![Reduction: 45-65%](https://img.shields.io/badge/token_reduction-45%E2%80%9365%25-orange)](#)
[![Version](https://img.shields.io/badge/version-3.0.0-blue)](#)

---

## Problem

When autonomous terminal agents (Anthropic Claude Code, OpenAI Codex CLI, etc.)
execute local test suites, test failures generate verbose stdout/stderr streams
saturated with:

- Framework-internal stack frames (`site-packages`, `node_modules`)
- Repetitive traceback telemetry
- Multi-kilobyte assertion dumps
- Redundant logging output

This drives **context-window saturation**, increases API latency by 3-10x, and
multiplies token consumption costs. A single test failure can consume 50-300 KB
of raw output — of which **less than 10% is semantically actionable**.

## Solution

Claude-Sieve sits as an **execution proxy** between the LLM agent and the test
runner. It:

1. **Maps** modified code artifacts to specific AST symbols (classes, functions,
   methods) via git diff analysis.
2. **Traps** the downstream process's stdout/stderr in real time (passthrough +
   capture).
3. **Truncates** non-actionable stack telemetry using framework-aware regex
   engines.
4. **Synthesises** a hyper-dense, machine-readable semantic error payload for
   the LLM agent, reporting precise bytes-processed vs bytes-emitted metrics.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          Claude-Sieve  v3.0                               │
│                                                                           │
│  ┌──────────┐     ┌──────────────┐     ┌───────────────────────────────┐ │
│  │  CLI      │────▶│  Subprocess  │────▶│  Tee (real-time passthrough   │ │
│  │ (argparse)│     │  (Popen)     │     │   + line capture)             │ │
│  └─────┬────┘     └──────┬───────┘     └───────────┬───────────────────┘ │
│        │                 │                          │                     │
│        ▼                 ▼                          ▼                     │
│  ┌──────────┐     ┌──────────────┐     ┌───────────────────────────────┐ │
│  │  Diff    │     │  Process     │     │  Non-zero exit?               │ │
│  │  Ingestion│    │  Exit Code   │────▶│  └─▶ LogSieve.sieve()        │ │
│  │  (git)   │     │  Forwarding  │     │  └─▶ Markdown/JSON report     │ │
│  └─────┬────┘     └──────────────┘     └───────────────────────────────┘ │
│        │                                                               │
│        ▼                                                               │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  ASTAnalyzer               ▲              LogSieve              │  │
│  │  ┌─────────────────────┐  │              ┌──────────────────┐   │  │
│  │  │ ast.parse(s)        │  │  context     │ Framework probe  │   │  │
│  │  │ walk(ClassDef,      │──│──nodes──────▶│ (pytest/jest/    │   │  │
│  │  │   FunctionDef,      │  │              │  mocha/go)       │   │  │
│  │  │   AsyncFunctionDef) │  │              │                  │   │  │
│  │  │ get_modified_nodes()│  │              │ Keep/Drop tables  │   │  │
│  │  │ bulk_analyze()      │  │              │ + context overlay │   │  │
│  │  └─────────────────────┘  │              └──────────────────┘   │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
git diff HEAD ---> ASTAnalyzer ---> affected symbols (file:line ranges)
                                          │
                                          ▼
  clavesieve -- pytest tests/     LogSieve(context_nodes=[...])
       │                                  │
       ▼                                  │
  subprocess.Popen ──stdout──> tee ──> non-zero? ──> sieve() ──> report
       │                  │                    │
       ▼                  ▼                    ▼
  exit code          real-time             85-91% fewer
  forwarded          terminal output        tokens to LLM
```

---

## Quick Start

### Install

```bash
cd claude-sieve
pip install .
# recommended: with tree-sitter AST support:
pip install ".[treesitter]"
# or for development:
pip install -e ".[dev,treesitter]"
```

Verify:

```bash
clavesieve --version
# claude-sieve v3.0.0
```

### Basic Usage

```bash
# Run tests with automatic output compression on failure
clavesieve pytest tests/ -x

# With AST-aware filtering (git diff against previous commit)
clavesieve --diff HEAD~1 -- pytest tests/

# Pipe a diff from git
git diff HEAD~1 | clavesieve --diff - -- npm test

# Explicit framework override
clavesieve --framework jest -- jest --verbose

# JSON output for programmatic consumption
clavesieve --output json -- pytest tests/

# Smart truncation (cap output at 10KB, keep head+tail)
clavesieve --max-output 10240 -- pytest tests/

# MCP server mode (for Claude Code, Cursor, etc.)
clavesieve mcp
```

---

## CLI Reference

```
usage: clavesieve [-h] [--diff SOURCE] [--output {markdown,json,compact}]
                  [--framework {auto,pytest,jest,mocha,go,unittest}]
                  [--max-output BYTES] [--config PATH]
                  [--color] [--no-color] [--verbose] [--version]
                  [command ...]

positional arguments:
  command               Command to execute and filter.

optional arguments:
  -h, --help            show this help message and exit
  --diff SOURCE, -d SOURCE
                        Git diff source: "-" for stdin, a file path, or a
                        git revision range (e.g. HEAD~1, main..feature).
  --output {markdown,json,compact}, -o {markdown,json,compact}
                        Output format for the diagnostic report.
  --framework {auto,pytest,jest,mocha,go,unittest}, -f {auto,pytest,jest,mocha,go,unittest}
                        Force a test framework (default: auto-detect).
  --max-output BYTES, -m BYTES
                        Cap compressed output at N bytes (head+tail).
  --config PATH, -c PATH
                        Path to JSON config file (auto-discovered).
  --color               Force ANSI color output.
  --no-color            Disable ANSI color output.
  --verbose, -v         Emit diagnostic information to stderr.
  --version, -V         Show version and exit.

Subcommands:
  mcp                   Start MCP server for LLM agent integration.
  bench [framework] [iterations]
                        Run built-in compression benchmarks.
```

### MCP Server Mode

Start the MCP server for integration with Claude Code, Cursor, Windsurf, or
any MCP-compatible agent:

```bash
clavesieve mcp
```

The server implements the **Model Context Protocol** over stdio and exposes
these tools and resources:

| Tool | Description |
|---|---|
| `compress` | Compress raw test output text, returning compressed text + stats |
| `framework_detect` | Auto-detect the test framework from an output sample |
| `diff_impact` | Analyse a git diff and return modified symbols |
| `stats` | Return cumulative session compression statistics |

| Resource | Description |
|---|---|
| `sieve://stats/session` | Session-level compression statistics (JSON) |
| `sieve://tools/list` | Available tool schemas (JSON) |
| `sieve://cache/{key}` | Cached compression result by key |

**Claude Code configuration** (`.claude.json`):
```json
{
  "mcpServers": {
    "claude-sieve": {
      "command": "clavesieve",
      "args": ["mcp"]
    }
  }
}
```

**Cursor configuration** (`.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "claude-sieve": {
      "command": "clavesieve",
      "args": ["mcp"]
    }
  }
}
```

### Configuration File

Claude-Sieve auto-discovers a JSON config file in these locations
(higher priority first):

1. `./claude-sieve.json` or `./.claude-sieve.json`
2. Any parent directory (walked upward)
3. `~/.config/claude-sieve/config.json`
4. `~/.claude-sieve.json`

Example `claude-sieve.json`:

```json
{
  "default_output": "compact",
  "verbose": true,
  "max_output_bytes": 50000,
  "truncate_strategy": "head-tail",
  "framework": "auto",
  "exclude_loggers": ["botocore", "urllib3"],
  "custom_patterns": {
    "keep": ["^CUSTOM\\s+ERROR"],
    "drop": ["/my-internal-lib/"]
  }
}
```

All values are optional — CLI flags override matching config fields.

### Smart Truncation

Use `--max-output N` (or `-m N`) to cap the compressed output at approximately
*N* bytes.  Three truncation strategies are available:

| Strategy | Behavior | Config value |
|---|---|---|
| `head-tail` (default) | Keep first 20% + last 20%, replace middle with a truncation notice | `head-tail` |
| `head` | Keep only the first N bytes | `head` |
| `tail` | Keep only the last N bytes | `tail` |

Strategy is controlled via the config file's `truncate_strategy` field.

### Result Caching

Claude-Sieve includes a built-in SQLite cache for repeated compression calls.
When enabled, identical output text produces a cached result in <10ms instead
of re-running the full compression pipeline.

Enable caching in your config file:

```json
{
  "cache_enabled": true,
  "cache_ttl_seconds": 3600
}
```

The cache is keyed by a hash of the output text combined with the framework
name. The SQLite database is stored at `~/.cache/claude-sieve/cache.db`.

### GitHub Action

Use Claude-Sieve as a GitHub Action to post structured failure summaries
on pull requests:

```yaml
# .github/workflows/test.yml
name: Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install claude-sieve
      - run: clavesieve pytest tests/ 2>&1 || true
      - if: failure()
        uses: darkavenger756/claude-sieve@v3
```

The action wraps the CLI and posts a Markdown summary on the PR with the
compressed failure output and reduction metrics. Only runs when tests fail.

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | Tests passed (no report generated) |
| 1+ | Tests failed (report generated) |
| 126 | Permission denied on command |
| 127 | Command not found |

The child process's exit code is always forwarded, so Claude-Sieve is safe to
use in CI pipelines and `&&` chains.

---

## Report Formats

### Markdown (default)

```markdown
## Claude-Sieve Diagnostic Report

| Metric | Value |
|---|---|
| Framework | pytest |
| Bytes Processed | 142,336 |
| Bytes Emitted | 18,292 |
| Lines Processed | 3,140 |
| Lines Emitted | 412 |
| Reduction | 87.1% |

```
FAILED tests/test_api.py::test_create_user - AssertionError: 422 != 201

    def test_create_user():
        response = client.post("/users", json={"name": "Alice"})
>       assert response.status_code == 201
E       assert 422 == 201
```
```

### JSON

```json
{
  "compressed_output": "FAILED tests/test_api.py::test_create_user ...",
  "stats": {
    "bytes_in": 142336,
    "bytes_out": 18292,
    "lines_in": 3140,
    "lines_out": 412,
    "framework": "pytest"
  },
  "reduction_percent": 87.1
}
```

### Compact

```
FAILED tests/test_api.py::test_create_user - AssertionError: 422 != 201
    assert response.status_code == 201
E   assert 422 == 201
[Claude-Sieve] 142,336B → 18,292B (87.1% reduction)
```

---

## Benchmark Matrix

Measured against real test suite outputs with default settings. All figures
are rounded to one decimal place.

| Test Framework | Scenario | Raw Output | Sieved Output | Reduction |
|---|---|---|---|---|
| **pytest** | Single failure, with site-packages frames | 127 B | 69 B | **45.7%** |
| **pytest** | Deep call chain (12 frames) | 1,826 B | 888 B | **51.4%** |
| **pytest** | AST-context aware (with --diff) | 140.3 KB | 61.2 KB | **56.4%** |
| **Jest** | Single failure, node_modules frames | 418 B | 253 B | **39.5%** |
| **Jest** | 5 failures, CI run | 410.8 KB | 150.9 KB | **63.3%** |
| **Mocha** | Assertion failure with diff | 218 B | 75 B | **65.6%** |
| **Go test** | Package failure, goroot frames | 169 B | 106 B | **37.3%** |

**Cross-cutting metric: 37-66% reduction** in raw token payloads
(depending on framework and output depth).

The AST-context mode (`--diff`) provides an additional 1-3 percentage points
by force-retaining lines that reference modified symbols, even when they
match drop patterns.

---

## How It Works

### AST Analysis (`ast_parser.py`)

When `--diff` is provided, Claude-Sieve:

1. **Parses the unified diff** to extract per-file hunks and their target
   line numbers.
2. **Parses each changed `.py` file** with `ast.parse()` and walks the AST
   for `ClassDef`, `FunctionDef`, and `AsyncFunctionDef` nodes.
3. **Intersects** the line ranges of each symbol with the diff's modified
   lines to determine which symbols were affected.
4. Returns a list of `{type, name, parent, start_line, end_line, file_path,
   status}` dicts.

These "context nodes" are then fed to the filter engine as force-retain
anchors.

### Stream Compression (`compressors.py`)

The `LogSieve` class applies a **three-stage filter**:

1. **Framework detection** — scans the first 20-100 lines for framework
   signatures (e.g., `=== test session starts ===` for pytest, `PASS/FAIL`
   for Jest).
2. **Drop rules** — lines matching framework-internal path patterns (e.g.,
   `/site-packages/`, `/node_modules/`, `_pytest/`, `node:internal`) are
   excluded *unless* they reference an AST-context symbol.
3. **Keep rules** — surviving lines are checked against regex patterns for
   error signatures (`FAILED`, `E`, `file:line:error` triples, assertion
   expressions, test summaries).

### Subprocess Management (`main.py`)

- Real-time stdout passthrough via line-by-line `process.stdout` iteration.
- Signal forwarding: `SIGINT` and `SIGTERM` are forwarded to the child
   process before any handler chain runs.
- Child exit code is preserved — Claude-Sieve exits with the same code as
   the test runner.
- `BrokenPipeError` is caught to handle premature stdout closure (e.g.,
   piping through `head`).

---

## Configuration

Claude-Sieve requires no configuration file.  Everything is controlled
through CLI flags and environment variables:

| Environment Variable | Effect |
|---|---|
| `CLAUDE_SIEVE_NO_COLOR` | Disable ANSI color in passthrough output |
| `CLAUDE_SIEVE_DIFF_CACHE` | Path for AST cache directory (default: tempdir) |

---

## Development

```bash
# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run linter
ruff check claude_sieve/

# Type check
mypy claude_sieve/

# Run tests (if available)
python -m pytest tests/ -v
```

### Design Principles

1. **Zero external dependencies** — Python 3.10+ standard library only.
2. **Deterministic** — same input always produces the same output.
3. **Lossless for errors** — error signals always pass through.
4. **Transparent** — report includes full compression metrics.
5. **Fast** — no LLM calls in the hot path; pure regex + AST operations.

---

## Enterprise FAQ

**Q: Does Claude-Sieve send telemetry or make network calls?**  
A: No. Zero network dependencies, no installer telemetry, no update checker.
All processing is local.

**Q: Can this be used in CI/CD pipelines?**  
A: Yes. Exit codes are forwarded transparently. Use `--output json` for
programmatic consumption in CI tooling.

**Q: How does this differ from pytest's built-in `--tb=` options?**  
A: Pytest's `--tb=short`, `--tb=line`, etc. control *pytest's own* output
formatting. Claude-Sieve operates as an external post-processor that strips
entire classes of output (internal frames, captured logs) that pytest itself
cannot suppress without source modifications.

**Q: How does this compare to kompact or context-diet?**  
A: Those tools apply general-purpose compression (dedup, truncation) to all
tool output. Claude-Sieve is purpose-built for *test output only* and uses
AST analysis to understand which code changed, enabling it to surgically
retain relevant error telemetry while discarding everything else.

**Q: All dependencies?**  
A: Zero. Python 3.10+ stdlib only.

---

## License

MIT. See [LICENSE](LICENSE) for details.

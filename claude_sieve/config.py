"""Configuration discovery and loading for Claude-Sieve.

Config files are JSON-formatted and searched in order:
  1. ``./claude-sieve.json``
  2. ``./.claude-sieve.json`` (hidden variant)
  3. Parent directories (up to filesystem root)
  4. ``~/.config/claude-sieve/config.json``
  5. ``~/.claude-sieve.json`` (legacy home config)

CLI flags override config-file values where both are set.
"""

import json
import os
from pathlib import Path
from typing import Any


_CONFIG_FILENAMES = ('claude-sieve.json', '.claude-sieve.json')

_DEFAULT_CONFIG: dict[str, Any] = {
    'default_output': 'markdown',
    'verbose': False,
    'max_output_bytes': 0,
    'truncate_strategy': 'head-tail',
    'truncate_head_pct': 0.2,
    'truncate_tail_pct': 0.2,
    'framework': 'auto',
    'cache_enabled': False,
    'cache_ttl_seconds': 3600,
    'exclude_loggers': [],
    'custom_patterns': {
        'keep': [],
        'drop': [],
    },
}


def _discover_config_paths() -> list[Path]:
    """Return all candidate config file paths in priority order."""
    paths: list[Path] = []

    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        for name in _CONFIG_FILENAMES:
            p = parent / name
            if p.is_file():
                paths.append(p.resolve())

    home = Path.home()
    home_config = home / '.config' / 'claude-sieve' / 'config.json'
    if home_config.is_file():
        paths.append(home_config.resolve())

    legacy = home / '.claude-sieve.json'
    if legacy.is_file() and legacy.resolve() not in {p.resolve() for p in paths}:
        paths.append(legacy.resolve())

    return paths


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load and merge config from file(s) with defaults.

    If *path* is provided, only that file is loaded.  Otherwise
    :func:`_discover_config_paths` is used (project → user).
    """
    merged = dict(_DEFAULT_CONFIG)

    if path is not None:
        p = Path(path)
        if p.is_file():
            _merge_file(merged, p)
    else:
        for candidate in _discover_config_paths():
            _merge_file(merged, candidate)

    return merged


def _merge_file(config: dict[str, Any], path: Path) -> None:
    """In-place merge of a JSON config file into *config*."""
    try:
        raw = path.read_text(encoding='utf-8')
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return

    if not isinstance(data, dict):
        return

    for key, value in data.items():
        if key in ('custom_patterns',) and isinstance(value, dict):
            existing = config.setdefault(key, {})
            for sub_key, sub_val in value.items():
                if sub_key in ('keep', 'drop') and isinstance(sub_val, list):
                    existing.setdefault(sub_key, []).extend(sub_val)
        elif key in ('exclude_loggers',) and isinstance(value, list):
            config.setdefault(key, []).extend(value)
        else:
            config[key] = value


def merge_cli_overrides(config: dict[str, Any], args: Any) -> dict[str, Any]:
    """Produce a new config dict with CLI-level overrides applied."""
    overridden = dict(config)

    cli_overrides = {
        'default_output': getattr(args, 'output', None),
        'verbose': getattr(args, 'verbose', None),
        'framework': getattr(args, 'framework', None),
        'max_output_bytes': getattr(args, 'max_output', None),
    }

    for key, cli_val in cli_overrides.items():
        if cli_val is not None and cli_val is not False:
            overridden[key] = cli_val

    return overridden

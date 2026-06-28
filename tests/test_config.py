"""Tests for config discovery and loading."""

import json
import tempfile

from claude_sieve.config import load_config, merge_cli_overrides


class Args:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_load_defaults():
    cfg = load_config('/nonexistent/path/config.json')
    assert cfg['default_output'] == 'markdown'
    assert cfg['cache_enabled'] is False
    assert cfg['truncate_strategy'] == 'head-tail'


def test_load_from_file():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({'default_output': 'compact', 'framework': 'jest'}, f)
        path = f.name
    cfg = load_config(path)
    assert cfg['default_output'] == 'compact'
    assert cfg['framework'] == 'jest'


def test_merge_cli_overrides():
    cfg = {'default_output': 'markdown', 'framework': 'auto', 'verbose': False, 'max_output_bytes': 0}
    args = Args(output='json', framework='pytest', verbose=True, max_output=5000)
    merged = merge_cli_overrides(cfg, args)
    assert merged['default_output'] == 'json'
    assert merged['framework'] == 'pytest'
    assert merged['verbose'] is True
    assert merged['max_output_bytes'] == 5000


def test_custom_patterns_merged():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({'custom_patterns': {'keep': ['^MY\\s+ERR'], 'drop': ['/internal/']}}, f)
        path = f.name
    cfg = load_config(path)
    assert '^MY\\s+ERR' in cfg['custom_patterns']['keep']
    assert '/internal/' in cfg['custom_patterns']['drop']


def test_exclude_loggers_merged():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump({'exclude_loggers': ['botocore', 'urllib3']}, f)
        path = f.name
    cfg = load_config(path)
    assert 'botocore' in cfg['exclude_loggers']
    assert 'urllib3' in cfg['exclude_loggers']

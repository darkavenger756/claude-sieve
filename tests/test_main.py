"""Tests for CLI entry point."""


from claude_sieve.main import main, _truncate_output


def test_truncate_head_tail():
    text = 'line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n'
    result = _truncate_output(text, max_bytes=20, strategy='head-tail')
    assert 'truncated' in result


def test_truncate_head():
    text = 'line1\nline2\nline3\n'
    result = _truncate_output(text, max_bytes=5, strategy='head')
    assert len(result) <= 10


def test_truncate_tail():
    text = 'line1\nline2\nline3\n'
    result = _truncate_output(text, max_bytes=5, strategy='tail')
    assert len(result) <= 10


def test_truncate_noop():
    text = 'short'
    result = _truncate_output(text, max_bytes=100, strategy='head-tail')
    assert result == text


def test_version():
    code = main(['--version'])
    assert code == 0


def test_no_args():
    code = main([])
    assert code == 1


def test_bad_command():
    code = main(['nonexistent_cmd'])
    assert code == 127


def test_bench():
    code = main(['bench'])
    assert code == 0

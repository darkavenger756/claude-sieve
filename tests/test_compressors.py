"""Tests for LogSieve compressor."""

from claude_sieve.compressors import LogSieve


def test_pytest_site_packages_dropped():
    output = (
        '=== test session starts ===\n'
        'FAILED test_demo.py::test_foo - AssertionError\n'
        'E   assert 1 == 2\n'
        '  /venv/lib/python3.12/site-packages/pkg/foo.py:42: err\n'
        '  tests/test_demo.py:5: test_foo\n'
        '=== 1 failed ===\n'
    )
    sieve = LogSieve(forced_framework='pytest')
    compressed = sieve.sieve(output)
    assert '/site-packages/' not in compressed
    assert 'FAILED test_demo.py' in compressed
    assert 'E   assert 1 == 2' in compressed
    assert '=== 1 failed ===\n' in compressed


def test_empty_lines_preserved():
    output = 'FAILED test\n\nE   detail\n=== 1 failed ===\n'
    sieve = LogSieve(forced_framework='pytest')
    compressed = sieve.sieve(output)
    assert '\n\n' in compressed


def test_auto_detect_pytest():
    output = '=== test session starts ===\nFAILED test\n'
    sieve = LogSieve()
    compressed = sieve.sieve(output)
    assert sieve.stats.get('framework') == 'pytest'
    assert 'FAILED test' in compressed


def test_jest_node_modules_dropped():
    output = (
        'FAIL tests/demo.test.js\n'
        '  \u25cf test_foo\n'
        '    expect(received).toBe(expected)\n'
        '    Expected: 2\n'
        '    Received: 1\n'
        '      at Object.<anonymous> (tests/demo.test.js:5:3)\n'
        '      at node_modules/jest-runner/build/index.js:100:20\n'
        'Test Suites: 1 failed, 1 total\n'
    )
    sieve = LogSieve(forced_framework='jest')
    compressed = sieve.sieve(output)
    assert 'node_modules' not in compressed
    assert 'expect(received)' in compressed


def test_go_goroot_dropped():
    output = (
        '=== RUN   TestFoo\n'
        '--- FAIL: TestFoo\n'
        '  \t/usr/local/go/src/testing/testing.go:1234: err\n'
        '  \ttests/demo_test.go:5: test_foo\n'
        'FAIL\n'
    )
    sieve = LogSieve(forced_framework='go')
    compressed = sieve.sieve(output)
    assert '/usr/local/go/' not in compressed
    assert '=== RUN' in compressed


def test_mocha_node_modules_dropped():
    output = (
        '  1 failing\n'
        '  1) test_foo it should work:\n'
        '     AssertionError: expected 1 to equal 2\n'
        '      at Context.<anonymous> (tests/demo.test.js:5:3)\n'
        '      at node_modules/mocha/lib/runner.js:100:20\n'
    )
    sieve = LogSieve(forced_framework='mocha')
    compressed = sieve.sieve(output)
    assert 'node_modules' not in compressed
    assert '1 failing' in compressed


def test_unittest_output():
    output = (
        'Traceback (most recent call last):\n'
        '  File "/usr/lib/python3.12/unittest/case.py", line 5, in test\n'
        '    raise AssertionError\n'
        '  File "tests/test_demo.py", line 10, in test_foo\n'
        '    self.assertTrue(False)\n'
        'AssertionError\n'
        'Ran 1 test in 0.001s\n'
        'FAILED\n'
    )
    sieve = LogSieve(forced_framework='unittest')
    compressed = sieve.sieve(output)
    assert '/unittest/' not in compressed
    assert 'FAILED' in compressed
    assert 'Ran 1 test' in compressed


def test_context_nodes_override_drop():
    output = (
        '  /venv/lib/python3.12/site-packages/pkg/foo.py:42: err\n'
        '  tests/test_demo.py:5: test_foo\n'
    )
    context = [{'file_path': '/abs/path/pkg/foo.py'}]
    sieve = LogSieve(context_nodes=context, forced_framework='pytest')
    compressed = sieve.sieve(output)
    assert '/site-packages/' in compressed


def test_extend_patterns():
    sieve = LogSieve(forced_framework='pytest')
    sieve.extend_patterns('pytest', keep=['^CUSTOM\\s+ERROR'], drop=['/my-lib/'])
    output = 'CUSTOM ERROR test\n  /my-lib/file.py:1: err\nE   assert\n'
    compressed = sieve.sieve(output)
    assert '/my-lib/' not in compressed


def test_stats_tracking():
    sieve = LogSieve(forced_framework='pytest')
    output = 'FAILED test\nE   assert\n=== 1 failed ===\n'
    sieve.sieve(output)
    stats = sieve.stats
    assert stats['bytes_in'] > 0
    assert stats['bytes_out'] > 0
    assert stats['lines_in'] > 0
    assert stats['lines_out'] > 0


def test_exclude_loggers():
    sieve = LogSieve(forced_framework='pytest', exclude_loggers=['botocore'])
    output = 'FAILED test\n2024-01-01 botocore.parser: DEBUG msg\nE   assert\n=== 1 failed ===\n'
    compressed = sieve.sieve(output)
    assert 'botocore' not in compressed
    assert 'FAILED test' in compressed

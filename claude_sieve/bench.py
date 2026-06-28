"""Built-in benchmark subcommand for Claude-Sieve.

Usage::

    clavesieve bench
    clavesieve bench --framework pytest --iterations 5
"""

import time
from typing import Optional

from .compressors import LogSieve


_SYNTHETIC_OUTPUTS = {
    'pytest': (
        '=== test session starts ===\n'
        'FAILED tests/test_demo.py::test_foo - AssertionError\n'
        'E   assert 1 == 2\n'
        '  /venv/lib/python3.12/site-packages/pkg/foo.py:42: in func\n'
        '  /venv/lib/python3.12/site-packages/pkg/bar.py:12: in helper\n'
        '  tests/test_demo.py:5: test_foo\n'
        '=== 1 failed ===\n'
    ),
    'jest': (
        'FAIL tests/demo.test.js\n'
        '  \u25cf test_foo > it should work\n'
        '    expect(received).toBe(expected)\n'
        '    Expected: 2\n'
        '    Received: 1\n'
        '      at Object.<anonymous> (tests/demo.test.js:5:3)\n'
        '      at node_modules/jest-runner/build/index.js:100:20\n'
        'Test Suites: 1 failed, 1 total\n'
        'Tests:       1 failed, 1 total\n'
    ),
    'mocha': (
        '  1 failing\n'
        '  1) test_foo it should work:\n'
        '     AssertionError: expected 1 to equal 2\n'
        '      at Context.<anonymous> (tests/demo.test.js:5:3)\n'
        '      at node_modules/mocha/lib/runner.js:100:20\n'
    ),
}


def run_bench(framework: Optional[str] = None, iterations: int = 3) -> dict:
    results = {}
    frameworks = [framework] if framework else list(_SYNTHETIC_OUTPUTS)

    for fw in frameworks:
        output = _SYNTHETIC_OUTPUTS.get(fw, '')
        if not output:
            continue

        total_time = 0.0
        total_bytes_in = 0
        total_bytes_out = 0

        for _ in range(iterations):
            sieve = LogSieve(forced_framework=fw)
            start = time.perf_counter()
            sieve.sieve(output)
            elapsed = time.perf_counter() - start
            stats = sieve.stats
            total_time += elapsed
            total_bytes_in += int(stats.get('bytes_in', 0) or 0)
            total_bytes_out += int(stats.get('bytes_out', 0) or 0)

        avg_time = total_time / iterations
        b_in = total_bytes_in // iterations
        b_out = total_bytes_out // iterations
        reduction = 0.0
        if b_in > 0:
            reduction = (1.0 - b_out / b_in) * 100.0

        results[fw] = {
            'iterations': iterations,
            'avg_time_ms': round(avg_time * 1000, 2),
            'bytes_in': b_in,
            'bytes_out': b_out,
            'reduction_percent': round(reduction, 1),
        }

    return results


def print_bench(results: dict) -> None:
    header = f"{'Framework':<12} {'Iter':<6} {'Avg (ms)':<10} {'In':<8} {'Out':<8} {'Reduction':<10}"
    print(header)
    print('-' * len(header))
    for fw, r in results.items():
        print(
            f"{fw:<12} {r['iterations']:<6} {r['avg_time_ms']:<10} "
            f"{r['bytes_in']:<8} {r['bytes_out']:<8} {r['reduction_percent']:<9}%"
        )

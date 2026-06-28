"""Parser for structured test report formats.

Supports:
- pytest ``--junitxml`` output (XML / JUnit format)
- jest ``--json`` output
- mocha ``--reporter json`` output
"""

import json
import xml.etree.ElementTree as ET
from typing import Any


def parse_report(raw: str, fmt: str = 'auto') -> dict[str, Any]:
    if fmt == 'auto':
        fmt = _detect_format(raw)
    if fmt == 'junit':
        return _parse_junit(raw)
    if fmt == 'jest':
        return _parse_jest_json(raw)
    if fmt == 'mocha':
        return _parse_mocha_json(raw)
    return {'error': f'Unknown format: {fmt}'}


def _detect_format(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith('<?xml') or stripped.startswith('<testsuite') or stripped.startswith('<testcase'):
        return 'junit'
    if stripped.startswith('{'):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                if 'numFailedTests' in data or 'numPassedTests' in data:
                    return 'jest'
                if 'stats' in data and isinstance(data.get('stats'), dict):
                    return 'mocha'
                if 'failures' in data and data.get('testsuites') is None:
                    if isinstance(data.get('failures'), list):
                        return 'jest'
                if 'testsuites' in data:
                    return 'junit'
        except (json.JSONDecodeError, ValueError):
            pass
    return 'unknown'


def _parse_junit(raw: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return {'error': f'XML parse error: {e}'}

    total = 0
    failures = 0
    errors = 0
    skipped = 0
    test_cases = []

    for ts in root.iter('testsuite'):
        total += int(ts.get('tests', 0))
        failures += int(ts.get('failures', 0))
        errors += int(ts.get('errors', 0))
        skipped += int(ts.get('skipped', 0))
        for tc in ts.iter('testcase'):
            name = tc.get('name', '')
            classname = tc.get('classname', '')
            file = tc.get('file', '')
            line = tc.get('line', '')
            failure = tc.find('failure')
            error = tc.find('error')
            if failure is not None:
                test_cases.append({
                    'name': f'{classname}::{name}' if classname else name,
                    'status': 'failed',
                    'message': failure.get('message', ''),
                    'type': failure.get('type', ''),
                    'file': file or '',
                    'line': line or '',
                })
            elif error is not None:
                test_cases.append({
                    'name': f'{classname}::{name}' if classname else name,
                    'status': 'error',
                    'message': error.get('message', ''),
                    'type': error.get('type', ''),
                    'file': file or '',
                    'line': line or '',
                })

    return {
        'format': 'junit',
        'total': total,
        'failures': failures,
        'errors': errors,
        'skipped': skipped,
        'passed': total - failures - errors - skipped,
        'failed_tests': test_cases,
    }


def _parse_jest_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {'error': f'JSON parse error: {e}'}

    total = data.get('numTotalTests', 0)
    failures = data.get('numFailedTests', 0)
    passed = data.get('numPassedTests', 0)
    test_cases = []

    if 'testResults' in data and isinstance(data['testResults'], list):
        for result in data['testResults']:
            assertion_results = result.get('assertionResults', [])
            for assertion in assertion_results:
                if assertion.get('status') in ('failed', 'broken'):
                    failure_msgs = assertion.get('failureMessages', [])
                    location = assertion.get('location', '') or ''
                    test_cases.append({
                        'name': assertion.get('fullName', assertion.get('title', '')),
                        'status': 'failed',
                        'message': (failure_msgs[0] if failure_msgs else '')[:500],
                        'file': result.get('name', ''),
                        'line': location,
                    })

    return {
        'format': 'jest',
        'total': total,
        'failures': failures,
        'passed': passed,
        'skipped': total - failures - passed,
        'failed_tests': test_cases,
    }


def _parse_mocha_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {'error': f'JSON parse error: {e}'}

    stats = data.get('stats', {})
    total = stats.get('tests', 0)
    failures = stats.get('failures', 0)
    passed = stats.get('passes', 0)
    test_cases = []

    failures_list = data.get('failures', [])
    for failure in failures_list:
        title = failure.get('fullTitle', failure.get('title', ''))
        err = failure.get('err', {})
        test_cases.append({
            'name': title,
            'status': 'failed',
            'message': (err.get('message', '') or '')[:500],
            'type': err.get('name', ''),
            'file': '',
            'line': '',
        })

    return {
        'format': 'mocha',
        'total': total,
        'failures': failures,
        'passed': passed,
        'skipped': total - failures - passed,
        'failed_tests': test_cases,
    }

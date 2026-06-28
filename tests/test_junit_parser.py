"""Tests for structured report parser."""

import json

from claude_sieve.junit_parser import parse_report, _detect_format


def test_detect_junit_xml():
    assert _detect_format('<?xml version="1.0"?><testsuite></testsuite>') == 'junit'


def test_detect_jest_json():
    assert _detect_format('{"numFailedTests": 1, "numTotalTests": 5}') == 'jest'


def test_detect_mocha_json():
    assert _detect_format('{"stats": {"tests": 1, "failures": 0}}') == 'mocha'


def test_parse_junit_empty():
    xml = '<?xml version="1.0"?><testsuites><testsuite tests="0" failures="0" errors="0"></testsuite></testsuites>'
    result = parse_report(xml, 'junit')
    assert result['total'] == 0
    assert result['failed_tests'] == []


def test_parse_junit_with_failure():
    xml = '''<?xml version="1.0"?>
    <testsuites>
      <testsuite tests="2" failures="1" errors="0">
        <testcase name="test_foo" classname="test_demo">
          <failure message="assert 1 == 2" type="AssertionError"/>
        </testcase>
        <testcase name="test_bar" classname="test_demo"/>
      </testsuite>
    </testsuites>'''
    result = parse_report(xml, 'junit')
    assert result['total'] == 2
    assert result['failures'] == 1
    assert len(result['failed_tests']) == 1
    assert result['failed_tests'][0]['name'] == 'test_demo::test_foo'


def test_parse_jest_json():
    raw = json.dumps({
        'numTotalTests': 3,
        'numFailedTests': 1,
        'numPassedTests': 2,
        'testResults': [
            {
                'name': 'tests/demo.test.js',
                'assertionResults': [
                    {
                        'title': 'should work',
                        'fullName': 'test_foo should work',
                        'status': 'failed',
                        'failureMessages': ['AssertionError: expected 1 to equal 2'],
                    },
                ],
            },
        ],
    })
    result = parse_report(raw, 'jest')
    assert result['total'] == 3
    assert result['failures'] == 1
    assert len(result['failed_tests']) == 1


def test_parse_mocha_json():
    raw = json.dumps({
        'stats': {'tests': 2, 'failures': 1, 'passes': 1},
        'failures': [
            {
                'title': 'should work',
                'fullTitle': 'test_foo should work',
                'err': {'message': 'expected 1 to equal 2', 'name': 'AssertionError'},
            },
        ],
    })
    result = parse_report(raw, 'mocha')
    assert result['total'] == 2
    assert result['failures'] == 1
    assert len(result['failed_tests']) == 1
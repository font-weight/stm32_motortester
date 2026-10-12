"""Fail-closed unittest runner: zero discovery, skip and expected failure fail."""
import argparse
import json
from pathlib import Path
import sys
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('patterns', nargs='+')
args = parser.parse_args()
root = Path(__file__).resolve().parents[4]
loader = unittest.TestLoader()
suite = unittest.TestSuite()
for pattern in args.patterns:
    found = loader.discover(str(root / 'host/tests'), pattern=pattern)
    if found.countTestCases() == 0:
        raise SystemExit('Empty discovery: ' + pattern)
    suite.addTests(found)
result = unittest.TextTestRunner(verbosity=2).run(suite)
summary = {'tests': result.testsRun, 'failures': len(result.failures),
           'errors': len(result.errors), 'skips': len(result.skipped),
           'expected_failures': len(result.expectedFailures),
           'unexpected_successes': len(result.unexpectedSuccesses),
           'optimized': not __debug__}
print('HOST008_RESULT=' + json.dumps(summary, sort_keys=True))
sys.exit(0 if (result.wasSuccessful() and result.testsRun > 0
               and not result.skipped and not result.expectedFailures) else 1)

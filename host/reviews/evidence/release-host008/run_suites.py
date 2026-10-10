"""Critic-owned fail-closed runner. No writes to the reviewed checkout."""
import argparse
import json
from pathlib import Path
import sys
import unittest

p = argparse.ArgumentParser()
p.add_argument('test_directory')
p.add_argument('patterns', nargs='+')
a = p.parse_args()
suite = unittest.TestSuite()
for pattern in a.patterns:
    found = unittest.TestLoader().discover(a.test_directory, pattern=pattern)
    if found.countTestCases() == 0:
        raise SystemExit('EMPTY DISCOVERY: ' + pattern)
    suite.addTests(found)
r = unittest.TextTestRunner(verbosity=2).run(suite)
summary = dict(tests=r.testsRun, failures=len(r.failures), errors=len(r.errors),
               skipped=len(r.skipped), expected_failures=len(r.expectedFailures),
               unexpected_successes=len(r.unexpectedSuccesses), optimized=not __debug__)
print('CRITIC_RESULT=' + json.dumps(summary, sort_keys=True))
raise SystemExit(0 if r.wasSuccessful() and r.testsRun > 0 and not r.skipped
                 and not r.expectedFailures and not r.unexpectedSuccesses else 1)

"""Запуск unittest с обязательной ненулевой инвентаризацией и нулём skip."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pattern', action='append', required=True)
    parser.add_argument('--expected-count', type=int, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    args = parser.parse_args()
    tests = Path(__file__).resolve().parents[2]
    combined, ids = unittest.TestSuite(), []
    for pattern in args.pattern:
        suite = unittest.defaultTestLoader.discover(str(tests), pattern=pattern)
        names = [test.id() for test in flatten(suite)]
        if not names:
            print(f'REJECTED_EMPTY_PATTERN: {pattern}', file=sys.stderr)
            return 2
        if any('_FailedTest' in name for name in names):
            print(f'REJECTED_IMPORT_ERROR: {pattern}', file=sys.stderr)
            unittest.TextTestRunner(verbosity=2).run(suite)
            return 2
        combined.addTests(suite)
        ids.extend(names)
    if len(ids) != args.expected_count or len(set(ids)) != len(ids):
        print(f'REJECTED_INVENTORY: count={len(ids)}, expected={args.expected_count}, unique={len(set(ids))}', file=sys.stderr)
        return 2
    record = {'started_utc': datetime.now(timezone.utc).isoformat(),
              'argv': sys.argv, 'python': sys.version, 'optimize': sys.flags.optimize,
              'patterns': args.pattern, 'ids': sorted(ids), 'discovered': len(ids)}
    args.inventory.write_text(json.dumps(record, indent=2) + '\n')
    print(f'EXACT_DISCOVERY={len(ids)} OPTIMIZE={sys.flags.optimize}', flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(combined)
    record.update(finished_utc=datetime.now(timezone.utc).isoformat(), tests_run=result.testsRun,
                  failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped),
                  unexpected_successes=len(result.unexpectedSuccesses), expected_failures=len(result.expectedFailures))
    args.inventory.write_text(json.dumps(record, indent=2) + '\n')
    print('RESULT=' + json.dumps({k: record[k] for k in ('discovered', 'tests_run', 'failures', 'errors', 'skips', 'expected_failures', 'unexpected_successes')}))
    return 0 if result.wasSuccessful() and not result.skipped and not result.expectedFailures and result.testsRun == len(ids) else 1


if __name__ == '__main__':
    sys.exit(main())

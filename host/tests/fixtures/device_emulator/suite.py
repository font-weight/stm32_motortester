"""Zero-skip unittest entry point. Setup uses statements, never bare asserts."""
import argparse
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "host" / "tests"))


def flattened(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flattened(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scope", choices=("focused", "provisional", "new", "full"))
    args = parser.parse_args()
    pattern = "test_*.py" if args.scope == "full" else "test_device_emulator*.py" if args.scope == "new" else "test_device_emulator_conformance.py"
    loaded = unittest.defaultTestLoader.discover(str(ROOT / "host" / "tests"), pattern=pattern)
    tests = list(flattened(loaded))
    if args.scope == "focused":
        tests = [test for test in tests if ".ProvisionalHoldTimeout." not in test.id()]
    elif args.scope == "provisional":
        tests = [test for test in tests if ".ProvisionalHoldTimeout." in test.id()]
    if not tests:
        raise RuntimeError("No tests discovered; this is a runner failure, not success")
    print(f"SCOPE={args.scope}; optimized={sys.flags.optimize}; expected_tests={len(tests)}", flush=True)
    if args.scope == "provisional":
        print("PROVISIONAL / UNACCEPTED hold-timeout interpretation only; not shared-contract acceptance", flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(tests))
    print(f"RESULT tests={result.testsRun} failures={len(result.failures)} errors={len(result.errors)} skips={len(result.skipped)}", flush=True)
    if result.skipped:
        print("RUNNER FAILURE: zero skips required", file=sys.stderr)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())

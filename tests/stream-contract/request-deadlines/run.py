#!/usr/bin/env python3
"""Run the pure-state suite and emit a hash-bound, machine-readable receipt."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import unittest


class ReceiptResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_ids = []
        self.subtests = 0

    def startTest(self, test):
        self.test_ids.append(test.id())
        super().startTest(test)

    def addSubTest(self, test, subtest, err):
        self.subtests += 1
        super().addSubTest(test, subtest, err)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", default="receipt.json")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(here), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2, resultclass=ReceiptResult).run(suite)
    inputs = ("deadline_model.py", "test_request_deadlines.py", "run.py",
              "README.md", "source-provenance.json")
    receipt = {
        "scope": "synthetic_pure_state_proposed_host_request_deadline",
        "endpoint_conformance": False,
        "acceptance_status": "BLOCKED_EVIDENCE_pending_independent_shared_review",
        "observed_runtime_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "command": "python3 run.py --receipt receipt.json",
        "source_sha256": {name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                          for name in inputs},
        "tests_run": result.testsRun,
        "subtests_run": result.subtests,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "successful": result.wasSuccessful(),
        "test_ids": result.test_ids,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: receipt[key] for key in
                      ("tests_run", "subtests_run", "failures", "errors", "successful")},
                     sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

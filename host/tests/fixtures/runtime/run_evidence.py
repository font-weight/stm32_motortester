"""Re-run frozen HOST-010 conformance and full suites without changing sources.

Run from any directory. --deps names an already installed Qt dependency folder;
no package is downloaded or installed. Every unittest run must succeed without
skips. Logs are replaced only inside this script's evidence subdirectory.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
EXPECTED = {
    "host/motortester/runtime.py": "902e26df36523113aa1e1e5bc9aa9ca92513945424ec65f58b16fe956686abb7",
    "host/tests/test_runtime.py": "a9b282a62d94ec29bdea64e464fbb3f8b28ec9bc4b91c27d394cebc3fbafa2d9",
    "host/docs/runtime.md": "ef2c07517b2766940ffa2924d54bc1c25b54c364e8f0ac3733eaa98beb1099dd",
    "host/motortester/transport.py": "34d2428e2505321cb894181e8e19bc8492ea2093eee0544d3e935e31960b2a86",
    "host/tests/test_transport.py": "a70c8aa54720dda420547eeeeb68c91864f3c8615bd2727b63f8e97228119527",
    "host/docs/transport.md": "0f418e79063408a61995ba5ddbaf77a9cb5b46461b92dd73781fcd87b94f9cb7",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = set((ROOT / "host/motortester").glob("*.py"))
    paths.update((ROOT / "host/tests").glob("test*.py"))
    paths.update((ROOT / "host/docs").glob("*.md"))
    for path in (ROOT / "host/tests/fixtures").rglob("*"):
        if path.is_file() and HERE not in path.parents and "__pycache__" not in path.parts:
            paths.add(path)
    paths.add(Path(__file__).resolve())
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(paths)}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deps", default="/tmp/motortester-deps")
    args = parser.parse_args()
    before = source_hashes()
    for name, expected in EXPECTED.items():
        if before.get(name) != expected:
            raise SystemExit(f"Frozen source mismatch: {name}; do not reuse old evidence")
    output = HERE / "evidence-20261012"
    output.mkdir(exist_ok=True)
    write_json(output / "source-pre.json", before)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=os.pathsep.join((str(ROOT / "host"), str(Path(args.deps).resolve()))))
    reports = []
    for label, optimized, pattern in (
        ("conformance-normal", False, "test_runtime_conformance.py"),
        ("conformance-optimized", True, "test_runtime_conformance.py"),
        ("full-normal", False, None),
        ("full-optimized", True, None),
    ):
        command = [sys.executable]
        if optimized:
            command.append("-O")
        command += ["-m", "unittest", "discover", "-s", "host/tests"]
        if pattern:
            command += ["-p", pattern]
        command.append("-v")
        began = datetime.now(timezone.utc).isoformat()
        process = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, timeout=600)
        ended = datetime.now(timezone.utc).isoformat()
        transcript = process.stdout
        match = re.search(r"Ran (\d+) tests? in ([0-9.]+)s", transcript)
        skips = bool(re.search(r"(?:skipped=|\.\.\. skipped )", transcript))
        succeeded = process.returncode == 0 and match is not None and not skips
        header = (f"UTC started: {began}\nUTC ended: {ended}\n"
                  f"Command: {' '.join(command)}\n"
                  f"PYTHONPATH={env['PYTHONPATH']}\n"
                  "PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen\n\n")
        path = output / f"{label}.log"
        path.write_text(header + transcript, encoding="utf-8")
        report = {"name": label, "command": command, "started_utc": began,
                  "ended_utc": ended, "returncode": process.returncode,
                  "tests": int(match[1]) if match else None,
                  "duration_seconds": float(match[2]) if match else None,
                  "skips_found": skips, "pass_without_skips": succeeded,
                  "log_sha256": digest(path)}
        reports.append(report)
        print(f"{label}: {report['tests']} tests; pass_without_skips={succeeded}", flush=True)
    after = source_hashes()
    write_json(output / "source-post.json", after)
    result = {"base_sha": "45ef38792b8e824e2109e7bacc1c9dc605917d06",
              "transport_source_sha": "ec11d01a42e0cbdc06039ddf274121a4db7202ed",
              "scope": "Linux synthetic software only; no Windows, USB or physical validation",
              "python": sys.version, "platform": platform.platform(),
              "frozen_files": EXPECTED, "source_unchanged": before == after,
              "runs": reports}
    write_json(output / "summary.json", result)
    if before != after or not all(item["pass_without_skips"] for item in reports):
        raise SystemExit("Evidence failed: inspect source manifests and logs")


if __name__ == "__main__":
    main()

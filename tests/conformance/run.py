#!/usr/bin/env python3
"""Reproducible native conformance runner; logs failure without granting a gate."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import tempfile

PINNED = {
    "firmware/protocol/mt_wire.c": "3d76b7f6725615f3fad4305575312997e72152f9",
    "firmware/protocol/mt_wire.h": "dcd14d8507f2812d488e4ad9264c3875b4f368d6",
    "firmware/protocol/mt_stream.c": "419e78a74548387db990296357c4fd16d03556a4",
    "firmware/protocol/mt_stream.h": "6e54d061b3185e5d68b54ec07e7f1b923c4e5f76",
    "firmware/protocol/mt_command.c": "ab15501f0afe68a01a3b371338619230cbd90d91",
    "firmware/protocol/mt_command.h": "14cb3f737d6d333cf0d78a8bcd0e1c5050cb9340",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument("--allow-source-drift", action="store_true",
                        help="Record a changed candidate rather than claim pinned-source results")
    parser.add_argument("--no-sanitizers", action="store_true",
                        help="Explicitly leave sanitizer evidence unexecuted")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    root = (args.source_root or here.parent.parent).resolve()
    out = (args.output_dir or Path(tempfile.mkdtemp(prefix="mt-conformance-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = dict(started_utc=datetime.now(timezone.utc).isoformat(),
                  environment=dict(platform=platform.platform(), python=sys.version),
                  source_root=str(root), output_dir=str(out), stages=[], source_files={},
                  implementation_commits=dict(wire="124b3f32e1e7f7282bce283baa4c9406d4d72950",
                                              stream_command="0f285510c1b9b6bd21bb7b60ae6098f7584ae71a"),
                  leak_detection="disabled: address/UB checks only; LSan not claimed",
                  scope="native software conformance only; no hardware, dispatcher, or review gate")
    log = (out / "run.log").open("w", buffering=1)

    def say(text):
        print(text, flush=True)
        log.write(text + "\n")

    def stage(name, command, env=None):
        say("$ " + shlex.join(map(str, command)))
        result = subprocess.run(command, cwd=root, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        say(result.stdout.rstrip())
        report["stages"].append(dict(name=name, command=list(map(str, command)),
                                     exit_code=result.returncode, output=result.stdout))
        if result.returncode != 0:
            raise RuntimeError(f"{name} failed with exit code {result.returncode}")

    try:
        say("Independent conformance; pinned Git blob checks run before compilation.")
        mismatches = []
        for name, expected in PINNED.items():
            data = (root / name).read_bytes()
            blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            report["source_files"][name] = dict(git_blob_sha=blob, expected_blob_sha=expected,
                                                sha256=hashlib.sha256(data).hexdigest(), size=len(data))
            if blob != expected:
                mismatches.append(name)
        report["exact_pinned_sources"] = not mismatches
        if mismatches and not args.allow_source_drift:
            raise RuntimeError("source fingerprint differs: " + ", ".join(mismatches))
        if mismatches:
            say("SOURCE DRIFT: these results do not certify the pinned commits: " + ", ".join(mismatches))
        report["test_files"] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(here.iterdir()) if p.suffix in (".c", ".h", ".py", ".json")
        }
        stage("fixture-reproducibility", [sys.executable, here / "generate_vectors.py", "--check"])
        cc = shlex.split(args.cc)
        stage("compiler-version", cc + ["--version"])
        common = cc + ["-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                       "-I", str(root / "firmware/protocol"),
                       str(root / "firmware/protocol/mt_wire.c"),
                       str(root / "firmware/protocol/mt_stream.c"),
                       str(root / "firmware/protocol/mt_command.c"), str(here / "test_conformance.c")]
        stage("strict-build", common + ["-O2", "-o", str(out / "conformance-strict")])
        stage("strict-run", [out / "conformance-strict"])
        if args.no_sanitizers:
            report["sanitizers"] = "NOT RUN: explicitly disabled"
        else:
            sanitizer_env = os.environ.copy()
            sanitizer_env["ASAN_OPTIONS"] = "detect_leaks=0:halt_on_error=1:abort_on_error=1"
            sanitizer_env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
            report["sanitizers"] = {k: sanitizer_env[k] for k in ("ASAN_OPTIONS", "UBSAN_OPTIONS")}
            stage("sanitizer-build", common + ["-O1", "-g", "-fsanitize=address,undefined",
                                               "-fno-sanitize-recover=all", "-fno-omit-frame-pointer",
                                               "-o", str(out / "conformance-sanitized")])
            stage("sanitizer-run", [out / "conformance-sanitized"], sanitizer_env)
        report["status"] = "PASS_NATIVE_CONFORMANCE"
    except (OSError, RuntimeError) as error:
        report["status"] = "FAILED_OR_BLOCKED"
        report["error"] = str(error)
        say(str(error))
    finally:
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        say("Evidence: " + str(out))
        log.close()
    return 0 if report["status"] == "PASS_NATIVE_CONFORMANCE" else 1


if __name__ == "__main__":
    sys.exit(main())

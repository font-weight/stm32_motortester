"""Run the frozen HOST007 matrix, fail explicitly on source drift or skipped tests."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
OUT = HERE / "evidence"
BASE = "477057aadc9bc8e757bc8ef817125377e816d212"
SHARED = "c54bd955aaf3cb863ef3df043ea237ea62e74ad4"
FROZEN_AUTHOR = {
    "host/motortester/device_emulator.py": "efb3761e5ad0693419df0abfe5498429a28ad7f4b7184c7d5cf8e48e1a97d0a2",
    "host/tests/test_device_emulator.py": "49986195412b66c902fef6428ce08c554570ef83d8c382cbb0e99f4455284b2c",
    "host/docs/device-emulator.md": "c40218af0a115a5333a8e12a4a3ef0ebb1266ecc2c28177d70917df5a8c94773",
    "host/motortester/session.py": "4049e5fda8499010bcdcad781af5a5ebfefde5de2b583e04b606b59bf1f61f61",
    "host/motortester/wire.py": "a8fa8edf58c22e87994674ad23fc654e4da93e229cfe21d482dbec3116bc8230",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sources():
    values = {}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path == OUT or OUT in path.parents:
            continue
        values[str(path.relative_to(ROOT))] = digest(path.read_bytes())
    return values


def main():
    OUT.mkdir(exist_ok=True)
    before = sources()
    current_base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", BASE, current_base], cwd=ROOT, check=False)
    if ancestor.returncode != 0:
        raise RuntimeError(f"RUNNER FAILURE: declared base is not an ancestor of {current_base}")
    for filename, expected in FROZEN_AUTHOR.items():
        if before.get(filename) != expected:
            raise RuntimeError(f"RUNNER FAILURE: frozen author/dependency source changed: {filename}")
    shared_sources = {}
    for source, local in (("protocol/wire-v0.1.md", "wire-v0.1.md"), ("docs/session-guard.md", "session-guard.md")):
        data = subprocess.check_output(["git", "show", f"{SHARED}:{source}"], cwd=ROOT)
        if data != (HERE / "source" / local).read_bytes():
            raise RuntimeError(f"RUNNER FAILURE: frozen shared source mismatch: {source}")
        shared_sources[source] = digest(data)
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="host:/tmp/motortester-deps", QT_QPA_PLATFORM="offscreen")
    suite = "host/tests/fixtures/device_emulator/suite.py"
    commands = [
        ("final-imports.log", [sys.executable, "-c", "import sys, PySide6, pyqtgraph, numpy; import motortester.device_emulator, motortester.session, motortester.wire; print(sys.version); print('IMPORTS OK', PySide6.__version__, numpy.__version__) "]),
        ("final-focused.log", [sys.executable, suite, "focused"]),
        ("final-provisional.log", [sys.executable, suite, "provisional"]),
        ("final-new-normal.log", [sys.executable, suite, "new"]),
        ("final-new-optimized.log", [sys.executable, "-O", suite, "new"]),
        ("final-full-host.log", [sys.executable, suite, "full"]),
    ]
    records = []
    for filename, command in commands:
        print(f"RUN {filename}", flush=True)
        with (OUT / filename).open("w", encoding="utf-8") as log:
            log.write("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=host:/tmp/motortester-deps QT_QPA_PLATFORM=offscreen " + shlex.join(command) + "\n")
            log.flush()
            process = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
        records.append({"command": command, "log": str((OUT / filename).relative_to(ROOT)), "exit_code": process.returncode})
        if sources() != before:
            records.append({"runner_failure": "Sources changed during evidence run; all results invalid for acceptance"})
            break
    after = sources()
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    evidence = {str(path.relative_to(ROOT)): digest(path.read_bytes())
                for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "manifest.json"}
    manifest = {
        "base_sha": BASE, "observed_head_sha": current_base, "shared_draft_sha": SHARED, "shared_source_sha256": shared_sources,
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python": sys.version,
        "environment": {name: env[name] for name in ("PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "QT_QPA_PLATFORM")},
        "commands": records, "sources_before_sha256": before, "sources_after_sha256": after,
        "changed_sources": changed, "evidence_sha256": evidence,
        "provisional_cases": 7, "provisional_status": "UNACCEPTED interpretation; final END byte anchor, >=5000ms",
        "limitations": ["synthetic Python byte peer only", "not firmware, USB, Windows or hardware validation", "no independent Critic-B score or acceptance claim"],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if changed or any("runner_failure" in row or row.get("exit_code", 1) for row in records):
        print("RUNNER FAILURE: failed command or changed sources; inspect manifest.json", file=sys.stderr)
        return 1
    print(f"STABLE: {len(before)} source files; {len(records)} commands; evidence manifest written", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Воспроизводимые evidence/log/hash; запускать после заморозки исходников."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
HOST = HERE.parents[2]
ROOT = HOST.parent
PIN = "b6ae8025baf1a47e8173997029d2d8b858ada74c"
SOURCE_PATHS = [
    "host/motortester/session.py", "host/motortester/wire.py", "host/tests/test_session.py",
    "host/tests/test_session_conformance.py", "host/docs/session-controller.md",
    "host/tests/fixtures/session/reference.py", "host/tests/fixtures/session/transcript.json",
    "host/tests/fixtures/session/run_checks.py", "host/tests/fixtures/session/README.md",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes():
    return {name: digest(ROOT / name) for name in SOURCE_PATHS}


def main():
    before = hashes()
    commands = [
        ("focused-final.log", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_session_conformance.py", "-v"]),
        ("session-all.log", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_session*.py", "-v"]),
        ("session-optimized.log", [sys.executable, "-O", "-m", "unittest", "discover", "-s", "tests", "-p", "test_session*.py", "-v"]),
        ("host-full.log", [sys.executable, "-m", "unittest", "discover", "-s", "host/tests", "-v"]),
    ]
    results = []
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(HOST) + ":/tmp/motortester-deps"}
    probe = subprocess.run([sys.executable, "-c", "import PySide6, pyqtgraph; print('PySide6', PySide6.__version__, PySide6.__file__); print('pyqtgraph', pyqtgraph.__version__, pyqtgraph.__file__)"],
                           cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    (HERE / "dependencies.log").write_text(probe.stdout + probe.stderr, encoding="utf-8")
    if probe.returncode:
        raise SystemExit("Недоступны известные GUI зависимости; см. dependencies.log")
    for name, command in commands:
        cwd = ROOT if name == "host-full.log" else HOST
        completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
        (HERE / name).write_text("$ " + " ".join(command) + "\n" + completed.stdout + completed.stderr, encoding="utf-8")
        results.append({"command": command, "cwd": str(cwd), "log": name, "returncode": completed.returncode})
        print(name, "exit", completed.returncode)
    after = hashes()
    sources = {}
    for path in ("protocol/wire-v0.1.md", "docs/session-guard.md"):
        result = subprocess.run(["git", "show", PIN + ":" + path], cwd=ROOT, capture_output=True, check=True)
        sources[path] = hashlib.sha256(result.stdout).hexdigest()
    manifest = {
        "base_sha": "c89c12df984390b34785eba81665abd6418658e2",
        "provisional_contract_sha": PIN, "python": sys.version, "platform": platform.platform(),
        "environment": {key: env[key] for key in ("QT_QPA_PLATFORM", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH")},
        "contract_source_sha256": sources, "files_before": before, "files_after": after,
        "unchanged_during_checks": before == after, "runs": results,
        "evidence_sha256": {p.name: digest(p) for p in sorted(HERE.glob("*.log"))},
        "scope": "draft_normal_analog_control, synthetic Linux only; no independent critic score",
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if before != after:
        raise SystemExit("Исходники изменились во время проверок; результат не финальный")
    if any(row["returncode"] for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Build a traceable Windows x64 portable candidate. This is not release approval."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import time

from launch_gui import receipt_path, validate_smoke_result
from serial_import_smoke import validate_report

HOST = Path(__file__).resolve().parent.parent
REPOSITORY = HOST.parent
EXPECTED_PYTHON = (3, 13, 16)
FROZEN_SMOKE_TIMEOUT = 60
CHILD_CLEANUP_TIMEOUT = 5


def run_logged(command, *, cwd, env, log, timeout=None):
    """Record launch before waiting; stream output and bound the direct child."""
    command = [str(item) for item in command]
    started = time.monotonic()
    with log.open("ab", buffering=0) as stream:
        def marker(text):
            stream.write((datetime.now(timezone.utc).isoformat() + " " + text + "\n").encode("utf-8"))
            stream.flush()
        marker("START " + subprocess.list2cmdline(command)
               + f" [timeout={timeout!r}; QT_QPA_PLATFORM={env.get('QT_QPA_PLATFORM', '<default>')}]")
        output_start = stream.tell()
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
        except OSError:
            marker("FAILED launch")
            raise
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                marker("TIMEOUT: terminating direct child")
            except OSError:
                pass  # A full/broken log must not prevent child cleanup.
            try:
                process.kill()
                process.wait(timeout=CHILD_CLEANUP_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as error:
                marker("FAILED: direct-child termination/reaping unconfirmed")
                raise RuntimeError("Timed-out child cleanup could not be confirmed") from error
            marker(f"FAILED timeout; direct child reaped; exit_code={process.returncode}")
            raise subprocess.TimeoutExpired(command, timeout)
        output_end = stream.tell()
        marker(f"END exit_code={process.returncode}; elapsed_seconds={time.monotonic()-started:.3f}")
    with log.open("rb") as stream:
        stream.seek(output_start)
        output = stream.read(output_end - output_start).decode("utf-8", errors="replace")
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command, output=output)
    return output


def run_frozen_serial_smoke(executable: Path, output: Path, run) -> dict:
    """Require both a successful child and its strict, fresh import-only report."""
    # lexists also catches a dangling link; the child still uses exclusive create
    # to prevent a file appearing between this check and the actual write.
    if os.path.lexists(output):
        raise RuntimeError("Serial smoke output already exists")
    run(executable, "--serial-import-smoke", output, timeout=30)
    return validate_report(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HOST / "build" / "windows-x64-candidate",
                        help="New build directory; an existing directory is never removed")
    args = parser.parse_args()
    if (sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64")
            or sys.maxsize <= 2**32 or sys.version_info[:3] != EXPECTED_PYTHON
            or sysconfig.get_config_var("Py_GIL_DISABLED")):
        parser.error("Use regular CPython 3.13.16 x64 on Windows; cross-compilation is unsupported")
    # Git is read-only here; a clean revision is necessary for traceable artifacts.
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=REPOSITORY, text=True)
    if dirty.strip():
        parser.error("Commit/review source changes first; the candidate build needs a clean checkout")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    log = output / "build.log"
    env = os.environ.copy()
    for variable in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM"):
        env.pop(variable, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYQTGRAPH_QT_LIB"] = "PySide6"
    env["PYTHONHASHSEED"] = "0"

    def run(*command, extra_env=None, timeout=None):
        output_text = run_logged(command, cwd=HOST, env=env | (extra_env or {}), log=log, timeout=timeout)
        print(output_text, end="")
        return output_text

    run(sys.executable, "-m", "venv", output / "venv")
    python = output / "venv" / "Scripts" / "python.exe"
    lock = HOST / "packaging" / "requirements-win-x64.lock"
    wheels = output / "wheelhouse"
    run(python, "-m", "pip", "--isolated", "download", "--index-url", "https://pypi.org/simple", "--require-hashes",
        "--only-binary=:all:", "--dest", wheels, "-r", lock)
    run(python, "-m", "pip", "--isolated", "install", "--no-index", "--find-links", wheels,
        "--require-hashes", "--only-binary=:all:", "-r", lock)
    run(python, "-m", "pip", "check")
    packages = json.loads(run(python, "-m", "pip", "list", "--format=json"))
    run(python, "-m", "unittest", "discover", "-s", "tests", "-v",
        extra_env={"QT_QPA_PLATFORM": "offscreen"})
    # The evidence script is invoked as a module so the source package is importable.
    run(python, "-m", "scripts.check_first_path", output / "synthetic-evidence")
    run(python, "-m", "PyInstaller", "--clean", "--noconfirm",
        "--distpath", output / "dist", "--workpath", output / "pyinstaller",
        HOST / "packaging" / "motortester.spec")
    bundle = output / "dist" / "Motortester"
    if not (bundle / "Motortester.exe").is_file():
        raise RuntimeError("PyInstaller did not produce Motortester.exe")
    serial_report_path = output / "frozen-serial-import.json"
    serial_report = run_frozen_serial_smoke(bundle / "Motortester.exe", serial_report_path, run)
    run(python, HOST / "packaging" / "collect_licenses.py", bundle / "licenses")
    shutil.copy2(HOST / "packaging" / "LICENSE-REVIEW.md", bundle / "LICENSE-REVIEW.md")
    shutil.copy2(lock, bundle / lock.name)
    (bundle / "CANDIDATE.txt").write_text(
        "HOST-001: кандидат для проверки, не принятый Windows-релиз.\n"
        "Запуск: Motortester.exe. Для переноски нужна вся папка, включая _internal.\n"
        "Импорт serial проверен без открытия порта. Физический USB не проверен. Демо синтетическое.\n"
        "Нужны ручная проверка Windows 10, лицензий и независимое ревью.\n",
        encoding="utf-8",
    )
    # This exercises the frozen Qt application, not a screenshot of a web mockup.
    screenshot_path = output / "frozen-window.png"
    if os.path.lexists(screenshot_path) or os.path.lexists(receipt_path(screenshot_path)):
        raise RuntimeError("Frozen screenshot output/receipt already exists")
    run(bundle / "Motortester.exe", "--screenshot", screenshot_path, timeout=FROZEN_SMOKE_TIMEOUT)
    try:
        screenshot_report = validate_smoke_result(screenshot_path)
    except (OSError, ValueError) as error:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(datetime.now(timezone.utc).isoformat()
                         + f" FAILED frozen screenshot validation: {type(error).__name__}\n")
        raise
    manifest = {
        "status": "candidate-not-release-approved",
        "source_commit": revision,
        "build_time_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "windows_10_manual_acceptance": "not performed by this script",
        "usb_verified": False,
        "frozen_gui_smoke": screenshot_report,
        "serial_import_smoke": {
            "report": serial_report_path.name,
            "sha256": hashlib.sha256(serial_report_path.read_bytes()).hexdigest(),
            "result": serial_report,
        },
        "license_review": "required; see LICENSE-REVIEW.md",
        "files": {p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(bundle.rglob("*")) if p.is_file()},
    }
    (bundle / "build-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = Path(shutil.make_archive(str(output / "Motortester-windows-x64-candidate"),
                                      "zip", root_dir=bundle.parent, base_dir=bundle.name))
    run("git", "archive", "--format=zip", f"--output={output / 'source.zip'}", revision, "host")
    artifacts = (archive, output / "source.zip", screenshot_path, receipt_path(screenshot_path), serial_report_path)
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in artifacts),
        encoding="ascii",
    )
    print(f"Candidate built: {archive}\nManual Windows 10 and license acceptance remain open.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        raise SystemExit(1)

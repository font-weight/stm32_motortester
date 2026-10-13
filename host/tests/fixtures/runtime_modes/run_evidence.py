"""Фиксировать focused normal/-O и отрицательный контроль без изменения production."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]
MODULES = ("__init__", "runtime", "session", "window", "wire", "device_emulator",
           "transport", "model", "recording")
PRODUCTION = [ROOT / "host" / "motortester" / (name + ".py") for name in MODULES]
FIXTURES = Path(__file__).resolve().parent
SOURCE = [ROOT / "host/tests/test_runtime_modes_integration.py", Path(__file__),
          FIXTURES / "auto-timeout-100.json", FIXTURES / "auto-timeout-100-host.bin",
          FIXTURES / "auto-timeout-100-device.bin", FIXTURES / "README.md"]
OLD_RUNTIME_SHA = "902e26df36523113aa1e1e5bc9aa9ca92513945424ec65f58b16fe956686abb7"


def hashes(paths):
    return {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in paths}


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    before = hashes(PRODUCTION + SOURCE)
    temporary = tempfile.TemporaryDirectory(prefix="runtime-modes-baseline-")
    try:
        if args.baseline:
            package = Path(temporary.name) / "motortester"
            package.mkdir()
            for source in PRODUCTION:
                shutil.copy2(source, package / source.name)
            runtime = package / "runtime.py"
            data = runtime.read_text()
            current = ('trigger_kind="auto_timeout" if b.trigger_kind == 2 else "signal",\n'
                       '                trigger_index=None if b.trigger_kind == 2 else b.trigger_index,')
            previous = 'trigger_kind="signal", trigger_index=b.trigger_index,'
            if data.count(current) != 1:
                raise RuntimeError("Текущее преобразование Auto не найдено ровно один раз")
            runtime.write_text(data.replace(current, previous))
            baseline_sha = sha256(runtime.read_bytes()).hexdigest()
            if baseline_sha != OLD_RUNTIME_SHA:
                raise RuntimeError("Восстановленная старая версия отличается от исходной 902e26df")
            sys.path.insert(0, temporary.name)
        else:
            baseline_sha = None
            sys.path.insert(0, str(ROOT / "host"))
        sys.path.insert(0, str(ROOT / "host/tests"))
        module = importlib.import_module("test_runtime_modes_integration")
        loader = unittest.TestLoader()
        if args.baseline:
            suite = loader.loadTestsFromName(
                "RuntimeModesIntegrationTests.test_auto_timeout_capture_is_not_discarded_as_invalid_signal", module)
        else:
            suite = loader.discover(str(ROOT / "host/tests"), pattern="test_runtime_modes_integration.py")
        discovered = suite.countTestCases()
        if discovered <= 0:
            raise RuntimeError("Нулевое число обнаруженных тестов недопустимо")
        with (args.output / "tests.log").open("x", encoding="utf-8") as stream:
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        after = hashes(PRODUCTION + SOURCE)
        expected = (discovered == 1 and result.testsRun == 1
                    and len(result.failures) == 1 and not result.errors) if args.baseline else result.wasSuccessful()
        report = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
            command=sys.orig_argv, python=sys.version, platform=platform.platform(),
            optimized=bool(sys.flags.optimize), baseline=args.baseline,
            baseline_runtime_sha256=baseline_sha, discovered=discovered,
            executed=result.testsRun, failures=len(result.failures), errors=len(result.errors),
            expected_outcome_observed=expected, source_unchanged=before == after,
            source_before=before, source_after=after,
            matrix_observations=module.MATRIX_OBSERVATIONS,
            scope="Только синтетика; это не заключение Critic-B и не аппаратная приёмка")
        (args.output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: report[key] for key in ("optimized", "baseline", "discovered", "executed",
            "failures", "errors", "expected_outcome_observed", "source_unchanged")}, ensure_ascii=False))
        return 0 if expected and before == after else 1
    finally:
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(run())

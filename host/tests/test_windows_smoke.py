"""Source/fake-backed Windows recipe tests; not frozen Windows acceptance."""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6 import QtCore, QtGui

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = load("windows_smoke_launcher", "launch_gui.py")
serial_smoke = load("serial_import_smoke", "serial_import_smoke.py")
with patch.dict(sys.modules, {"launch_gui": launcher, "serial_import_smoke": serial_smoke}):
    build = load("windows_smoke_builder", "build_windows.py")


def make_png(path):
    image = QtGui.QImage(3, 2, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor("#123456"))
    if not image.save(str(path), "PNG"):
        raise RuntimeError("Test PNG could not be saved")


class LauncherSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="smoke тест ")
        self.root = Path(self.temp.name)
        self.png = self.root / "снимок окна.png"

    def tearDown(self):
        self.temp.cleanup()

    def run_fake(self, action=None, *, no_console=False):
        gui = ModuleType("motortester.gui")
        def normal(argv):
            self.assertEqual(argv, ["--screenshot", str(self.png)])
            report = json.loads(launcher.receipt_path(self.png).read_text(encoding="utf-8"))
            self.assertEqual(report["phase"], "screenshot")
            make_png(self.png)
            return 0
        gui.main = action or normal
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"motortester.gui": gui}))
            stack.enter_context(patch.object(QtCore.QCoreApplication, "instance", return_value=SimpleNamespace(platformName=lambda: "offscreen")))
            if no_console:
                stack.enter_context(patch.object(sys, "stdout", None))
                stack.enter_context(patch.object(sys, "stderr", None))
            return launcher._screenshot_smoke(str(self.png))

    def report(self):
        return json.loads(launcher.receipt_path(self.png).read_text(encoding="utf-8"))

    def test_success_decodes_actual_png_and_records_complete_phases(self):
        self.assertEqual(self.run_fake(), 0)
        report = launcher.validate_smoke_result(self.png, require_frozen_windows=False)
        self.assertEqual(report["png"]["width"], 3)
        self.assertEqual(report["png"]["height"], 2)
        self.assertEqual([item["name"] for item in report["phases"]], list(launcher.SMOKE_PHASES))
        self.assertEqual(report["png"]["sha256"], hashlib.sha256(self.png.read_bytes()).hexdigest())
        with self.assertRaises(ValueError):
            launcher.validate_smoke_result(self.png)

    def test_import_exception_has_early_receipt_and_no_uncaught_exception(self):
        original_import = __import__
        def importer(name, *args, **kwargs):
            if name == "motortester.gui":
                self.assertEqual(self.report()["phase"], "gui-import")
                raise ImportError("контролируемая ошибка импорта")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=importer):
            self.assertNotEqual(launcher._screenshot_smoke(str(self.png)), 0)
        self.assertEqual(self.report()["error"]["type"], "ImportError")
        self.assertIn("контролируемая", launcher.receipt_path(self.png).read_text(encoding="utf-8"))

    def test_screenshot_exception_and_system_exit_fail_closed(self):
        for error in (RuntimeError("controlled"), SystemExit(0)):
            with self.subTest(error=error):
                self.png = self.root / (type(error).__name__ + ".png")
                self.assertNotEqual(self.run_fake(Mock(side_effect=error)), 0)
                self.assertEqual(self.report()["status"], "failed")
                self.assertEqual(self.report()["error"]["type"], type(error).__name__)

    def test_callback_error_is_not_silently_successful(self):
        def action(argv):
            sys.excepthook(ValueError, ValueError("Qt callback failure"), None)
            self.assertEqual(self.report()["status"], "failed")
            make_png(self.png)
            return 0
        self.assertNotEqual(self.run_fake(action), 0)
        self.assertEqual(self.report()["status"], "failed")

    def test_nonzero_and_invalid_return_values_fail(self):
        for index, result in enumerate((1, None, False)):
            self.png = self.root / f"return-{index}.png"
            self.assertNotEqual(self.run_fake(lambda argv: result), 0)
            self.assertEqual(self.report()["status"], "failed")

    def test_missing_empty_invalid_and_truncated_png_fail(self):
        for index, content in enumerate((None, b"", b"not PNG", launcher.PNG_SIGNATURE + b"broken")):
            self.png = self.root / f"invalid-{index}.png"
            def action(argv):
                if content is None:
                    self.png.unlink()
                else:
                    self.png.write_bytes(content)
                return 0
            self.assertNotEqual(self.run_fake(action), 0)
            self.assertEqual(self.report()["status"], "failed")

    def test_missing_console_streams_success_and_error(self):
        self.assertEqual(self.run_fake(no_console=True), 0)
        self.png = self.root / "без консоли ошибка.png"
        self.assertNotEqual(self.run_fake(Mock(side_effect=RuntimeError("ошибка")), no_console=True), 0)
        self.assertEqual(self.report()["error"]["type"], "RuntimeError")

    def test_existing_png_and_receipt_are_never_reused_or_clobbered(self):
        make_png(self.png)
        original = self.png.read_bytes()
        action = Mock()
        self.assertNotEqual(self.run_fake(action), 0)
        action.assert_not_called()
        self.assertEqual(self.png.read_bytes(), original)
        self.png.unlink()
        launcher.receipt_path(self.png).write_text('{"status":"passed"}')
        receipt = launcher.receipt_path(self.png).read_bytes()
        self.assertNotEqual(self.run_fake(action), 0)
        self.assertEqual(launcher.receipt_path(self.png).read_bytes(), receipt)

    def test_race_during_png_reservation_preserves_competing_file(self):
        original_open = Path.open
        def racing_open(path, mode="r", *args, **kwargs):
            if path == self.png and mode == "xb":
                with original_open(path, "wb") as stream:
                    stream.write(b"competing file")
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, "open", racing_open):
            self.assertNotEqual(self.run_fake(), 0)
        self.assertEqual(self.png.read_bytes(), b"competing file")
        self.assertEqual(self.report()["status"], "failed")

    def test_receipt_write_failure_returns_nonzero_without_console(self):
        self.png = self.root / "missing" / "out.png"
        self.assertNotEqual(self.run_fake(no_console=True), 0)

    def test_receipt_validation_rejects_wrong_scope_json_and_changed_png(self):
        self.assertEqual(self.run_fake(), 0)
        good = self.report()
        path = launcher.receipt_path(self.png)
        invalid = [dict(good, schema_version=True), dict(good, status="running"),
                   dict(good, phases=[]), dict(good, extra=True), dict(good, png={})]
        for report in invalid:
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                launcher.validate_smoke_result(self.png, require_frozen_windows=False)
        for data in (b"", b"{", b"\xff", b" " * (launcher.MAX_RECEIPT_BYTES + 1)):
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                launcher.validate_smoke_result(self.png, require_frozen_windows=False)
        path.write_text(json.dumps(good))
        self.png.write_bytes(self.png.read_bytes() + b"modified")
        with self.assertRaises(ValueError):
            launcher.validate_smoke_result(self.png, require_frozen_windows=False)

    def test_ordinary_launch_delegates_unchanged_and_bad_smoke_arity_fails(self):
        gui = ModuleType("motortester.gui")
        gui.main = Mock(return_value=19)
        for argv in (["exe"], ["exe", "--help"], ["exe", "--screenshot=x.png"]):
            with patch.object(sys, "argv", argv), patch.dict(sys.modules, {"motortester.gui": gui}):
                self.assertEqual(launcher.main(), 19)
        self.assertEqual(gui.main.call_count, 3)
        self.assertTrue(all(call.args == () for call in gui.main.call_args_list))
        for argv in (["exe", "--screenshot"], ["exe", "--screenshot", "a", "b"]):
            with patch.object(sys, "argv", argv), patch.dict(sys.modules, {"motortester.gui": None}):
                self.assertEqual(launcher.main(), 2)

    def test_real_source_launcher_with_spaces_unicode_and_no_console(self):
        env = os.environ | {"QT_QPA_PLATFORM":"offscreen", "PYTHONDONTWRITEBYTECODE":"1"}
        command = [sys.executable, "-c",
                   "import runpy,sys; sys.stdout=None; sys.stderr=None; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')",
                   str(PACKAGING / "launch_gui.py"), "--screenshot", str(self.png)]
        process = subprocess.run(command, env=env, capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))
        self.assertGreater(launcher.validate_smoke_result(self.png, require_frozen_windows=False)["png"]["bytes"], 1000)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runner тест ")
        self.root = Path(self.temp.name)
        self.log = self.root / "build log.txt"

    def tearDown(self):
        self.temp.cleanup()

    def run_child(self, code, timeout=5):
        return build.run_logged([sys.executable, "-c", code], cwd=self.root,
                                env=os.environ.copy(), log=self.log, timeout=timeout)

    def test_start_marker_flushed_before_launch_and_exact_argument_vector(self):
        command = ["mock.exe", "--screenshot", str(self.root / "снимок окна.png")]
        child = Mock(returncode=0)
        child.wait.return_value = 0
        def popen(args, **kwargs):
            self.assertEqual(args, command)
            self.assertIn(subprocess.list2cmdline(command), self.log.read_text())
            self.assertIn("START", self.log.read_text())
            kwargs["stdout"].write(b"child output\n")
            return child
        with patch.object(build.subprocess, "Popen", side_effect=popen):
            self.assertEqual(build.run_logged(command, cwd=self.root, env={}, log=self.log, timeout=60), "child output\n")
        child.wait.assert_called_once_with(timeout=60)

    def test_success_preserves_stdout_and_stderr(self):
        output = self.run_child("import sys; print('stdout'); print('stderr',file=sys.stderr)")
        self.assertIn("stdout", output)
        self.assertIn("stderr", output)
        self.assertIn("END exit_code=0", self.log.read_text())

    def test_nonzero_exit_preserves_output_and_raises(self):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.run_child("import sys; print('before failure',flush=True); sys.exit(7)")
        self.assertEqual(caught.exception.returncode, 7)
        self.assertIn("before failure", self.log.read_text())
        self.assertIn("END exit_code=7", self.log.read_text())

    def test_real_stalled_direct_child_is_terminated_reaped_with_partial_output(self):
        processes = []
        real_popen = subprocess.Popen
        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        started = time.monotonic()
        with patch.object(build.subprocess, "Popen", side_effect=capture):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_child("import time; print('before stall',flush=True); time.sleep(30)", timeout=1.0)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNotNone(processes[0].poll())
        log = self.log.read_text()
        self.assertIn("START", log)
        self.assertIn("before stall", log)
        self.assertIn("direct child reaped", log)

    def test_launch_error_logged_before_raise(self):
        with patch.object(build.subprocess, "Popen", side_effect=FileNotFoundError), self.assertRaises(OSError):
            self.run_child("unused")
        self.assertIn("FAILED launch", self.log.read_text())

    def test_timeout_log_error_does_not_prevent_cleanup(self):
        child = Mock(returncode=-9)
        child.wait.side_effect = [subprocess.TimeoutExpired("mock", 0.1), -9]
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.tell.return_value = 0
        stream.write.side_effect = [100, OSError("log full"), 100]
        with patch.object(Path, "open", return_value=stream), \
                patch.object(build.subprocess, "Popen", return_value=child), \
                self.assertRaises(subprocess.TimeoutExpired):
            self.run_child("unused", timeout=0.1)
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)

    def test_unconfirmed_cleanup_is_bounded_and_explicit(self):
        child = Mock(returncode=None)
        child.wait.side_effect = subprocess.TimeoutExpired("mock", 0.1)
        with patch.object(build.subprocess, "Popen", return_value=child), self.assertRaises(RuntimeError):
            self.run_child("unused", timeout=0.1)
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_args_list[-1].kwargs, {"timeout":5})
        self.assertIn("unconfirmed", self.log.read_text())


class BuildRecipeTests(unittest.TestCase):
    def recipe(self, mode):
        with tempfile.TemporaryDirectory(prefix="recipe тест ") as temp:
            root = Path(temp)
            output = root / "build result"
            png = output / "frozen-window.png"
            commands = []
            def fake_run(command, **kwargs):
                command = [str(item) for item in command]
                commands.append((command, kwargs))
                if "--format=json" in command:
                    return "[]"
                if "PyInstaller" in command:
                    bundle = output / "dist" / "Motortester"
                    bundle.mkdir(parents=True)
                    (bundle / "Motortester.exe").write_bytes(b"test placeholder, not an executable")
                    if mode == "stale":
                        make_png(png)
                        launcher.receipt_path(png).write_text('{"status":"passed"}')
                if "--serial-import-smoke" in command:
                    self.assertEqual(kwargs["timeout"], 30)
                    Path(command[-1]).write_text(json.dumps(serial_smoke.SUCCESS_REPORT))
                if "--screenshot" in command:
                    self.assertEqual(kwargs["timeout"], 60)
                    self.assertNotIn("QT_QPA_PLATFORM", kwargs["env"])
                    if mode == "nonzero":
                        raise subprocess.CalledProcessError(7, command)
                    if mode == "timeout":
                        raise subprocess.TimeoutExpired(command, 60)
                    if mode == "missing":
                        return ""
                    make_png(png)
                    report = {"schema_version":1, "status":"passed", "phase":"complete",
                              "phases":[{"name":phase,"utc":datetime.now(timezone.utc).isoformat()}
                                        for phase in launcher.SMOKE_PHASES],
                              "platform":"win32", "frozen":True, "qt_platform":"windows",
                              "output":png.name, "png":launcher._inspect_png(png), "error":None}
                    launcher.receipt_path(png).write_text(json.dumps(report))
                    if mode == "invalid":
                        png.write_bytes(b"broken PNG")
                    if mode == "receipt":
                        launcher.receipt_path(png).write_text('{"status":"passed"}')
                if command[:2] == ["git", "archive"]:
                    (output / "source.zip").write_bytes(b"test source archive")
                return ""
            def fake_archive(name, *args, **kwargs):
                archive = Path(name + ".zip")
                archive.write_bytes(b"test bundle archive")
                return str(archive)
            with ExitStack() as stack:
                stack.enter_context(patch.object(sys, "argv", ["build_windows.py", "--output", str(output)]))
                stack.enter_context(patch.object(build, "sys", SimpleNamespace(platform="win32", maxsize=2**63-1,
                    version_info=(3,13,16), executable="test-python")))
                stack.enter_context(patch.object(build.platform, "machine", return_value="AMD64"))
                stack.enter_context(patch.object(build.platform, "platform", return_value="fake Windows test environment"))
                stack.enter_context(patch.object(build.platform, "python_version", return_value="3.13.16"))
                stack.enter_context(patch.object(build.sysconfig, "get_config_var", return_value=None))
                stack.enter_context(patch.object(build.subprocess, "check_output", side_effect=["test-source-sha", ""]))
                stack.enter_context(patch.object(build, "run_logged", side_effect=fake_run))
                archive = stack.enter_context(patch.object(build.shutil, "make_archive", side_effect=fake_archive))
                if mode == "success":
                    self.assertEqual(build.main(), 0)
                    self.assertEqual(archive.call_count, 1)
                    self.assertIn("frozen-window.png.smoke.json", (output / "SHA256SUMS.txt").read_text())
                    manifest = json.loads((output / "dist/Motortester/build-manifest.json").read_text())
                    self.assertEqual(manifest["frozen_gui_smoke"]["status"], "passed")
                    self.assertEqual(manifest["serial_import_smoke"]["result"], serial_smoke.SUCCESS_REPORT)
                    self.assertIn("frozen-serial-import.json", (output / "SHA256SUMS.txt").read_text())
                else:
                    with self.assertRaises((OSError, ValueError, RuntimeError, subprocess.SubprocessError)):
                        build.main()
                    archive.assert_not_called()
                    self.assertFalse((output / "SHA256SUMS.txt").exists())
                    self.assertFalse((output / "dist/Motortester/build-manifest.json").exists())
                    if mode in ("missing", "invalid", "receipt"):
                        self.assertIn("FAILED frozen screenshot validation", (output / "build.log").read_text())
            if mode == "stale":
                self.assertFalse(any("--screenshot" in command for command, kwargs in commands))
            else:
                self.assertTrue(any("--screenshot" in command for command, kwargs in commands))

    def test_success_requires_valid_png_receipt_and_zero_child_exit(self):
        self.recipe("success")

    def test_all_smoke_failures_stop_before_manifest_and_zip(self):
        for mode in ("nonzero", "timeout", "missing", "invalid", "receipt", "stale"):
            with self.subTest(mode=mode):
                self.recipe(mode)


if __name__ == "__main__":
    unittest.main()

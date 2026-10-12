"""Source/fake-backed packaging tests. These are NOT Windows/frozen evidence."""
from contextlib import ExitStack
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load("serial_import_smoke", "serial_import_smoke.py")
with patch.dict(sys.modules, {"serial_import_smoke": smoke}):
    build = load("build_windows_under_test", "build_windows.py")
launcher = load("serial_launcher_under_test", "launch_gui.py")
licenses = load("serial_licenses_under_test", "collect_licenses.py")


def forbidden(*args, **kwargs):
    raise AssertionError("A constructor/device operation must never be called")


def fake_modules():
    modules = {name: ModuleType(name) for name in smoke.REQUIRED_IMPORTS}
    serial_class = type("Serial", (), {"__module__": "serial.serialwin32",
                                      "__new__": forbidden, "open": forbidden})
    modules["serial.serialwin32"].Serial = serial_class
    modules["serial"].Serial = serial_class
    modules["serial"].VERSION = modules["serial"].__version__ = "3.5"
    modules["serial"].serial_for_url = forbidden
    modules["serial.win32"].CreateFile = forbidden
    modules["motortester.transport"].SerialTransport = type(
        "SerialTransport", (), {"__module__": "motortester.transport", "__new__": forbidden,
                                "open": forbidden})
    return modules


class ImportGateTests(unittest.TestCase):
    def check(self, modules=None, *, platform="win32", os_name="nt", frozen=True):
        modules = fake_modules() if modules is None else modules
        with patch.object(smoke, "sys", SimpleNamespace(platform=platform, frozen=frozen)), \
                patch.object(smoke, "os", SimpleNamespace(name=os_name)), \
                patch.object(smoke.importlib, "import_module", side_effect=modules.__getitem__) as importer:
            report = smoke._check_imports()
        return report, importer

    def test_success_imports_exact_required_graph_and_no_constructors(self):
        report, importer = self.check()
        self.assertEqual(report, smoke.SUCCESS_REPORT)
        self.assertEqual([call.args[0] for call in importer.call_args_list], list(smoke.REQUIRED_IMPORTS))

    def test_source_and_non_windows_are_failed_distinct_scope_without_imports(self):
        for platform, os_name, frozen in (("linux", "posix", False), ("linux", "posix", True),
                                           ("win32", "nt", False), ("win32", "posix", True),
                                           ("win32", "nt", 1)):
            with self.subTest(platform=platform, os_name=os_name, frozen=frozen):
                report, importer = self.check(platform=platform, os_name=os_name, frozen=frozen)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["scope"], "source-or-non-windows")
                importer.assert_not_called()

    def test_each_missing_module_fails_closed(self):
        for name in smoke.REQUIRED_IMPORTS:
            with self.subTest(name=name):
                modules = fake_modules()
                del modules[name]
                self.assertEqual(self.check(modules)[0]["error"], "required-import-failed")

    def test_wrong_import_module_rejected(self):
        for name in smoke.REQUIRED_IMPORTS:
            for replacement in (None, ModuleType("wrong")):
                with self.subTest(name=name, replacement=replacement):
                    modules = fake_modules()
                    modules[name] = replacement
                    self.assertEqual(self.check(modules)[0]["error"], "unexpected-import-module")

    def test_wrong_or_missing_versions_rejected(self):
        for attribute in ("VERSION", "__version__"):
            for version in (None, "3.4", "3.6", 3.5):
                with self.subTest(attribute=attribute, version=version):
                    modules = fake_modules()
                    setattr(modules["serial"], attribute, version)
                    self.assertEqual(self.check(modules)[0]["error"], "unexpected-pyserial-version")

    def test_wrong_backend_identity_name_and_type_rejected(self):
        for case in ("identity", "module", "name", "function", "missing"):
            modules = fake_modules()
            if case == "identity":
                modules["serial"].Serial = type("Serial", (), {})
            elif case == "module":
                modules["serial.serialwin32"].Serial.__module__ = "serial.serialposix"
            elif case == "name":
                modules["serial.serialwin32"].Serial.__qualname__ = "Wrong"
            else:
                modules["serial.serialwin32"].Serial = modules["serial"].Serial = (
                    forbidden if case == "function" else None)
            with self.subTest(case=case):
                self.assertEqual(self.check(modules)[0]["error"], "unexpected-serial-backend")

    def test_missing_wrong_transport_class_rejected(self):
        for value in (None, forbidden, type("Wrong", (), {"__module__": "motortester.transport"}),
                      type("SerialTransport", (), {"__module__": "wrong"})):
            modules = fake_modules()
            modules["motortester.transport"].SerialTransport = value
            self.assertEqual(self.check(modules)[0]["error"], "unexpected-transport-class")

    def test_import_exception_fails_without_leaking_unbounded_details(self):
        with patch.object(smoke, "sys", SimpleNamespace(platform="win32", frozen=True)), \
                patch.object(smoke, "os", SimpleNamespace(name="nt")), \
                patch.object(smoke.importlib, "import_module", side_effect=RuntimeError("X" * 10000)):
            self.assertLess(len(json.dumps(smoke._check_imports())), smoke.MAX_REPORT_BYTES)


class OutputTests(unittest.TestCase):
    def test_safe_windows_paths(self):
        for value in ("report.json", "folder/report.json", r"C:\Work\отчёт.json", "D:/Work/result.JSON"):
            with self.subTest(value=value):
                self.assertIsInstance(smoke.validate_output_path(value), Path)

    def test_unsafe_windows_paths(self):
        paths = ["", ".", "..", "report.txt", "a.json:stream", "C:report.json", "/root.json",
                 r"\root.json", r"\\server\share\file.json", r"\\?\C:\file.json",
                 r"\\.\NUL.json", "a/../b.json", "a/./b.json", "a//b.json", "a /b.json",
                 "a./b.json", "a.json ", "a.json.", "a\x00.json", "a\n.json", "a\x7f.json",
                 "a?.json", "a*.json", 'a".json', "a|.json", "a<.json", "a>.json",
                 "a" * 241 + ".json", "C:/folder:ads/a.json"]
        for name in ("CON", "PrN", "aux", "NUL", "COM0", "COM1", "COM9", "LPT1", "LPT9",
                     "COM¹", "LPT²", "COM³", "CONIN$", "CONOUT$", "CLOCK$", "CON "):
            paths.extend((name + ".json", "C:/" + name + "/a.json"))
        for value in paths + [None, 12]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                smoke.validate_output_path(value)

    def test_success_writes_bounded_validated_report(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            with patch.object(smoke, "validate_output_path", return_value=target), \
                    patch.object(smoke, "_check_imports", return_value=smoke.SUCCESS_REPORT):
                self.assertEqual(smoke.run("result.json"), 0)
            self.assertLessEqual(target.stat().st_size, smoke.MAX_REPORT_BYTES)
            self.assertEqual(smoke.validate_report(target), smoke.SUCCESS_REPORT)

    def test_source_failure_writes_failure_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            report = {"schema_version": 1, "scope": "source-or-non-windows",
                      "status": "failed", "error": "requires-frozen-windows"}
            with patch.object(smoke, "validate_output_path", return_value=target), \
                    patch.object(smoke, "_check_imports", return_value=report):
                self.assertNotEqual(smoke.run("result.json"), 0)
            with self.assertRaises(ValueError):
                smoke.validate_report(target)

    def test_existing_file_never_clobbered_or_imported(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            target.write_bytes(b"original")
            with patch.object(smoke, "validate_output_path", return_value=target), \
                    patch.object(smoke, "_check_imports") as check:
                self.assertNotEqual(smoke.run("result.json"), 0)
                check.assert_not_called()
            self.assertEqual(target.read_bytes(), b"original")

    def test_race_after_validation_never_clobbers(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            def race(_raw):
                target.write_bytes(b"concurrent writer")
                return target
            with patch.object(smoke, "validate_output_path", side_effect=race), \
                    patch.object(smoke, "_check_imports") as check:
                self.assertNotEqual(smoke.run("result.json"), 0)
                check.assert_not_called()
            self.assertEqual(target.read_bytes(), b"concurrent writer")

    def test_existing_directory_and_missing_parent_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            for target in (Path(temp), Path(temp) / "absent" / "out.json"):
                with patch.object(smoke, "validate_output_path", return_value=target), \
                        patch.object(smoke, "_check_imports") as check:
                    self.assertNotEqual(smoke.run("result.json"), 0)
                    check.assert_not_called()

    def test_unsafe_path_never_reaches_filesystem_or_imports(self):
        with patch.object(Path, "open", side_effect=AssertionError("Unsafe path opened")), \
                patch.object(smoke, "_check_imports") as check:
            self.assertNotEqual(smoke.run("NUL.json"), 0)
            check.assert_not_called()

    def test_all_schema_fields_and_types_are_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.json"
            cases = []
            for key in smoke.SUCCESS_REPORT:
                removed = dict(smoke.SUCCESS_REPORT)
                del removed[key]
                cases.append(removed)
                changed = dict(smoke.SUCCESS_REPORT)
                changed[key] = None
                cases.append(changed)
            cases.extend([dict(smoke.SUCCESS_REPORT, extra=True),
                          dict(smoke.SUCCESS_REPORT, schema_version=True),
                          dict(smoke.SUCCESS_REPORT, frozen=1),
                          dict(smoke.SUCCESS_REPORT, device_io_performed=0),
                          dict(smoke.SUCCESS_REPORT, imports=list(reversed(smoke.REQUIRED_IMPORTS))),
                          dict(smoke.SUCCESS_REPORT, pyserial_version="3.6"), [], None])
            for report in cases:
                target.write_text(json.dumps(report), encoding="utf-8")
                with self.subTest(report=report), self.assertRaises(ValueError):
                    smoke.validate_report(target)

    def test_malformed_duplicate_oversize_and_non_utf8_reports_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.json"
            valid = json.dumps(smoke.SUCCESS_REPORT).encode()
            for data in (b"", b"{", b"[]", b"\xff", b"\xef\xbb\xbf" + valid, valid + b"{}",
                         b" " * (smoke.MAX_REPORT_BYTES + 1),
                         valid[:-1] + b', "schema_version":1}', valid + b"\x00"):
                target.write_bytes(data)
                with self.subTest(data=data[:50]), self.assertRaises((ValueError, UnicodeError)):
                    smoke.validate_report(target)
            target.unlink()
            with self.assertRaises(OSError):
                smoke.validate_report(target)

    def test_report_writer_failure_returns_nonzero(self):
        with patch.object(Path, "open", side_effect=PermissionError):
            self.assertNotEqual(smoke.run("result.json"), 0)


class LauncherTests(unittest.TestCase):
    def test_smoke_is_dispatched_before_gui_import(self):
        helper = ModuleType("serial_import_smoke")
        helper.run = Mock(return_value=7)
        with patch.object(launcher, "sys", SimpleNamespace(argv=["Motortester.exe", "--serial-import-smoke", "out.json"])), \
                patch.dict(sys.modules, {"serial_import_smoke": helper, "motortester.gui": None}):
            self.assertEqual(launcher.main(), 7)
        helper.run.assert_called_once_with("out.json")

    def test_bad_smoke_arity_does_not_import_gui(self):
        for argv in (["exe", "--serial-import-smoke"], ["exe", "--serial-import-smoke", "a", "b"]):
            with patch.object(launcher, "sys", SimpleNamespace(argv=argv, stderr=None)), \
                    patch.dict(sys.modules, {"motortester.gui": None, "serial_import_smoke": None}):
                self.assertEqual(launcher.main(), 2)

    def test_all_other_argv_delegate_unchanged(self):
        gui = ModuleType("motortester.gui")
        gui.main = Mock(return_value=23)
        for argv in (["exe"], ["exe", "--screenshot", "a.png"], ["exe", "--help"],
                     ["exe", "--serial-import-smoke=out.json"], ["exe", "x", "--serial-import-smoke"]):
            with patch.object(launcher, "sys", SimpleNamespace(argv=argv)), \
                    patch.dict(sys.modules, {"motortester.gui": gui, "serial_import_smoke": None}):
                self.assertEqual(launcher.main(), 23)
                self.assertIs(launcher.sys.argv, argv)
        self.assertEqual(gui.main.call_count, 5)
        self.assertTrue(all(call.args == () and call.kwargs == {} for call in gui.main.call_args_list))


class FrozenBuildGateTests(unittest.TestCase):
    def test_success_requires_child_and_report(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            def run(*command, timeout):
                self.assertEqual(command, (Path("Motortester.exe"), "--serial-import-smoke", target))
                self.assertEqual(timeout, 30)
                target.write_text(json.dumps(smoke.SUCCESS_REPORT))
            self.assertEqual(build.run_frozen_serial_smoke(Path("Motortester.exe"), target, run),
                             smoke.SUCCESS_REPORT)

    def test_failed_child_is_not_accepted_even_with_pass_json(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            def run(*command, **kwargs):
                target.write_text(json.dumps(smoke.SUCCESS_REPORT))
                raise subprocess.CalledProcessError(1, command)
            with self.assertRaises(subprocess.CalledProcessError):
                build.run_frozen_serial_smoke(Path("Motortester.exe"), target, run)

    def test_missing_invalid_and_timed_out_child_evidence_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            with self.assertRaises(OSError):
                build.run_frozen_serial_smoke(Path("Motortester.exe"), target, Mock())
            def invalid(*args, **kwargs):
                target.write_text('{"status":"passed"}')
            with self.assertRaises(ValueError):
                build.run_frozen_serial_smoke(Path("Motortester.exe"), target, invalid)
            target.unlink()
            with self.assertRaises(subprocess.TimeoutExpired):
                build.run_frozen_serial_smoke(Path("Motortester.exe"), target,
                                             Mock(side_effect=subprocess.TimeoutExpired("exe", 30)))

    def test_existing_report_blocks_child(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.json"
            target.write_text(json.dumps(smoke.SUCCESS_REPORT))
            child = Mock()
            with self.assertRaises(RuntimeError):
                build.run_frozen_serial_smoke(Path("Motortester.exe"), target, child)
            child.assert_not_called()

    def test_build_stops_before_archive_if_serial_gate_fails(self):
        # Fake a Windows builder. No executable or Windows APIs are run here.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "output"
            commands = []
            def child(command, **kwargs):
                commands.append(command)
                if "--format=json" in command:
                    return subprocess.CompletedProcess(command, 0, "[]")
                if "PyInstaller" in command:
                    bundle = output / "dist" / "Motortester"
                    bundle.mkdir(parents=True)
                    (bundle / "Motortester.exe").write_bytes(b"fake: not an executable")
                if "--serial-import-smoke" in command:
                    return subprocess.CompletedProcess(command, 1, "intentional child failure")
                return subprocess.CompletedProcess(command, 0, "")
            with ExitStack() as stack:
                stack.enter_context(patch.object(sys, "argv", ["build_windows.py", "--output", str(output)]))
                stack.enter_context(patch.object(build, "sys", SimpleNamespace(
                    argv=["build_windows.py", "--output", str(output)], platform="win32",
                    maxsize=2**63-1, version_info=(3, 13, 16), executable="fake-python")))
                stack.enter_context(patch.object(build.platform, "machine", return_value="AMD64"))
                stack.enter_context(patch.object(build.sysconfig, "get_config_var", return_value=None))
                stack.enter_context(patch.object(build.subprocess, "check_output", side_effect=["fake-revision", ""]))
                stack.enter_context(patch.object(build.subprocess, "run", side_effect=child))
                archive = stack.enter_context(patch.object(build.shutil, "make_archive"))
                with self.assertRaises(subprocess.CalledProcessError):
                    build.main()
                archive.assert_not_called()
            self.assertTrue(any("--serial-import-smoke" in c for c in commands))
            self.assertFalse(any("--screenshot" in c for c in commands))
            self.assertFalse((output / "SHA256SUMS.txt").exists())
            self.assertIn("exit_code=1", (output / "build.log").read_text())


class LicenseTests(unittest.TestCase):
    @staticmethod
    def distribution(version="3.5"):
        return SimpleNamespace(metadata={"Name": "pyserial", "License": "BSD"}, files=[], version=version)

    def test_exact_notice_copied_with_source_hash_inventory_and_review_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "python"
            runtime.mkdir()
            (runtime / "LICENSE.txt").write_text("fake runtime license for test")
            dest = root / "licenses"
            with patch.object(licenses.metadata, "distributions", return_value=[self.distribution()]), \
                    patch.object(licenses, "sys", SimpleNamespace(base_prefix=str(runtime))):
                licenses.collect(dest)
            inventory = json.loads((dest / "inventory.json").read_text())
            self.assertTrue(inventory[0]["manual_review_required"])
            notice = inventory[0]["vendored_notices"][0]
            self.assertEqual(notice["source_url"], licenses.PYSERIAL_LICENSE_URL)
            self.assertEqual(notice["sha256"], licenses.PYSERIAL_LICENSE_SHA256)
            self.assertEqual(notice["reference_wheel_sha256"], licenses.PYSERIAL_WHEEL_SHA256)
            self.assertEqual((dest / notice["path"]).read_bytes(), licenses.PYSERIAL_LICENSE.read_bytes())

    def test_missing_notice_aborts(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(licenses, "PYSERIAL_LICENSE", Path(temp) / "missing.txt"), \
                    self.assertRaises(OSError):
                licenses._pyserial_notice(Path(temp) / "out", "3.5")

    def test_wrong_version_aborts_even_when_notice_exists(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(RuntimeError):
            licenses._pyserial_notice(Path(temp), "3.6")

    def test_modified_notice_aborts(self):
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / "license.txt"
            bad.write_bytes(licenses.PYSERIAL_LICENSE.read_bytes() + b"changed")
            with patch.object(licenses, "PYSERIAL_LICENSE", bad), self.assertRaises(RuntimeError):
                licenses._pyserial_notice(Path(temp) / "out", "3.5")

    def test_missing_pyserial_distribution_aborts(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(licenses.metadata, "distributions", return_value=[]), self.assertRaises(RuntimeError):
                licenses.collect(Path(temp) / "out")

    def test_collector_rejects_wrong_installed_version(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(licenses.metadata, "distributions", return_value=[self.distribution("3.4")]), \
                    self.assertRaises(RuntimeError):
                licenses.collect(Path(temp) / "out")


class RecipeTests(unittest.TestCase):
    def test_exact_dependency_pin_added_without_other_version_upgrades(self):
        gui = (PACKAGING.parent / "requirements-gui.txt").read_text()
        pins = [line for line in gui.splitlines() if line and not line.startswith("#")]
        self.assertEqual(pins, ["PySide6-Essentials==6.12.0", "shiboken6==6.12.0",
                               "pyqtgraph==0.14.0", "numpy==2.5.3", "colorama==0.4.6", "pyserial==3.5"])
        lock = (PACKAGING / "requirements-win-x64.lock").read_text()
        pins = [line for line in lock.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(pins), 14)
        self.assertIn("pyserial==3.5 --hash=sha256:" + licenses.PYSERIAL_WHEEL_SHA256, pins)
        self.assertTrue(all(" --hash=sha256:" in pin for pin in pins))

    def test_spec_explicitly_collects_required_backend_graph(self):
        tree = ast.parse((PACKAGING / "motortester.spec").read_text())
        analysis = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == "Analysis")
        hidden = next(item.value for item in analysis.keywords if item.arg == "hiddenimports")
        self.assertEqual(ast.literal_eval(hidden), list(smoke.REQUIRED_IMPORTS))

    def test_real_source_imports_do_not_construct_or_enumerate(self):
        # pyserial is required for this source check; no skip can hide its absence.
        import serial
        from serial import serialutil
        from motortester import transport
        self.assertEqual(serial.VERSION, "3.5")
        self.assertIsInstance(serialutil.SerialBase, type)
        with patch.object(serial, "Serial", side_effect=forbidden), \
                patch.object(transport, "SerialTransport", side_effect=forbidden), \
                patch.object(serial, "serial_for_url", side_effect=forbidden):
            import importlib
            self.assertIs(importlib.import_module("serial.serialutil"), serialutil)
            self.assertIs(importlib.import_module("motortester.transport"), transport)
        # The actual Windows backend cannot be imported on Linux. No simulation
        # above changes that; the release gate still needs the frozen executable.


if __name__ == "__main__":
    unittest.main()

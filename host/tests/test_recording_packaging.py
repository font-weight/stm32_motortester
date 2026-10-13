"""Source/fake gate controls; no test here constitutes frozen Windows evidence."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
HOST = PACKAGING.parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load("recording_smoke", "recording_smoke.py")
serial = load("serial_import_smoke", "serial_import_smoke.py")
launcher = load("recording_launcher", "launch_gui.py")
with patch.dict(sys.modules, {"serial_import_smoke": serial, "launch_gui": launcher}):
    build = load("recording_build", "build_windows.py")


def write_fixture_report(output, executable):
    """Explicit fake-native recipe fixture, with real headless spool/archive bytes.

    Used only by build-policy tests. This helper does not run the GUI or Windows.
    Actual source GUI invocation is tested separately and cannot pass native gate.
    """
    from motortester import session_archive as sa
    from motortester.model import Capture
    from motortester import recording
    output = Path(output)
    work = smoke.work_path(output)
    work.mkdir()
    identity = sa.SessionIdentity("00000000-0000-4000-8000-000000000001",
        "00000000-0000-0000-0000-00000000007b", "simulator", "wire-build-0", "0.1", "0.1.0")
    spool_name = "recording-00000000000040008000000000000001.mtspool"
    spool = sa.SessionSpool.create(work / spool_name, identity)
    segments = []
    for ordinal in range(2):
        sequence, generation = ordinal + 1, 3 + ordinal * 3
        samples = smoke._expected_samples(sequence, generation)
        sample_bytes = struct.pack("<2048H", *samples)
        provenance = sa.SegmentProvenance(generation=generation, config_id=1, acquisition_mode=1,
            auto_timeout_ms=0, host_elapsed_ms=ordinal * 100, wire_version="0.1", start_operation="arm",
            wire_session_nonce=123, config_payload_hex=struct.pack("<HBBBBHHHI", 1, 1, 0, 0, 0, 2048, 0, 0, 0).hex(),
            begin_request_id=0, begin_sequence=10 + ordinal * 20, terminal_kind="end", terminal_request_id=0,
            terminal_sequence=20 + ordinal * 20, end_samples_crc32=zlib.crc32(sample_bytes), unavailable_reason=None)
        capture = Capture(samples, identity.session_id, sequence, 50000, "2026-10-12T00:00:00+00:00",
            trigger_index=0, firmware_version=identity.firmware_version, protocol_version=identity.protocol_version)
        raw = recording.encode(capture)
        spool.append_full(sa.FullSegment(capture, provenance))
        segments.append(dict(ordinal=ordinal, kind="capture", sequence=sequence, sample_count=2048,
            raw_sha256=hashlib.sha256(raw).hexdigest(), samples_sha256=hashlib.sha256(sample_bytes).hexdigest(),
            provenance=asdict(provenance)))
    sa.finalize(spool.finish("user_stop", "stop_ack"), work / "recording.mtrec")
    spool.close()
    report = dict(schema_version=1, status="passed", scope="frozen-windows-recording", phase="complete",
        phases=[{"name": name, "utc": datetime.now(timezone.utc).isoformat()} for name in smoke.PHASES],
        output=output.name, workdir=work.name,
        runtime=dict(platform="win32", os_name="nt", os_release="10", os_version="10.0.26100",
            machine="AMD64", python="3.13.16", implementation="CPython", python_bits=64,
            qt="6.12.0", pyside="6.12.0", qt_platform="windows", frozen=True, optimize=0, application="0.1.0"),
        imports=list(smoke.REQUIRED_IMPORTS), checks=dict(smoke.CHECKS), identity=asdict(identity),
        stop=dict(request_id=9, ack_request_id=9, wire_session_nonce=123, generation=6, last_window=2,
            status=0, termination_reason="user_stop", evidence="stop_ack"), segments=segments,
        artifacts={"archive": {"name": "recording.mtrec", **smoke.file_info(work / "recording.mtrec")},
                   "spool": {"name": spool_name, **smoke.file_info(work / spool_name)}},
        executable=smoke.file_info(Path(executable)), cleanup=dict(smoke.CLEANUP), error=None)
    output.write_text(json.dumps(report), encoding="utf-8")
    return report


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recording gate тест ")
        self.root = Path(self.temp.name)
        self.output = self.root / "receipt.json"
        self.exe = self.root / "candidate.exe"
        self.exe.write_bytes(b"fake-native-fixture-not-an-executable")
        self.good = write_fixture_report(self.output, self.exe)

    def tearDown(self):
        self.temp.cleanup()

    def validate(self):
        return smoke.validate_report(self.output, executable=self.exe)

    def rewrite(self, report):
        self.output.write_text(json.dumps(report), encoding="utf-8")

    def test_complete_fake_native_fixture_validates_only_its_gate_contract(self):
        self.assertEqual(self.validate(), self.good)

    def test_strict_top_level_and_nested_keys_types_and_values(self):
        changes = [((), "schema_version", True), ((), "schema_version", 2), ((), "extra", None),
            ((), "status", "running"), ((), "phase", "stop"), ((), "error", "failed"),
            ((), "scope", "source-or-nonnative-development"), ((), "imports", []),
            (("runtime",), "frozen", 1), (("runtime",), "frozen", False), (("runtime",), "qt_platform", "offscreen"),
            (("runtime",), "python", "3.12.14"), (("runtime",), "qt", "6.11.0"), (("runtime",), "python_bits", True),
            (("runtime",), "optimize", 1), (("runtime",), "platform", "linux"), (("runtime",), "os_name", "posix"),
            (("runtime",), "os_version", "secret\nvalue"), (("runtime",), "machine", "ARM64"),
            (("checks",), "device_io_performed", 0), (("checks",), "synthetic", False),
            (("checks",), "physical_stop_verified", True), (("checks",), "source_binding", "embedded-sha"),
            (("cleanup",), "writer_stopped", False), (("cleanup",), "acquisition_native_joined", False),
            (("cleanup",), "pending_io", True), (("cleanup",), "finalization_invalidated", True),
            (("identity",), "recording_id", "bad"), (("identity",), "origin", "device"),
            (("stop",), "request_id", True), (("stop",), "ack_request_id", 8), (("stop",), "status", True),
            (("stop",), "evidence", "unknown"), (("stop",), "wire_session_nonce", 0),
            (("executable",), "bytes", True), (("executable",), "sha256", "a" * 63),
            (("artifacts", "archive"), "name", "../recording.mtrec"),
            (("artifacts", "spool"), "name", "/outside.mtspool"),
            (("segments", 0), "ordinal", True), (("segments", 0), "ordinal", 1),
            (("segments", 0), "sample_count", 2047), (("segments", 0), "sample_count", True),
            (("segments", 0, "provenance"), "generation", True),
            (("segments", 0, "provenance"), "wire_session_nonce", 124),
            (("segments", 0, "provenance"), "begin_request_id", False),
            (("segments", 0, "provenance"), "terminal_sequence", 1),
            (("segments", 0, "provenance"), "config_payload_hex", "00" * 16)]
        for location, key, value in changes:
            with self.subTest(location=location, key=key, value=value):
                report = deepcopy(self.good)
                node = report
                for part in location:
                    node = node[part]
                node[key] = value
                self.rewrite(report)
                with self.assertRaises((ValueError, smoke.SmokeFailure)):
                    self.validate()

    def test_missing_duplicate_nonfinite_malformed_oversized_and_deep_json(self):
        for raw in (b"", b"{", b"\xff", b"{}", b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
                    b"[" * 1100 + b"0" + b"]" * 1100, b" " * (smoke.MAX_RECEIPT_BYTES + 1)):
            with self.subTest(raw=raw[:30]):
                self.output.write_bytes(raw)
                with self.assertRaises((ValueError, smoke.SmokeFailure, RecursionError)):
                    self.validate()

    def test_invalid_phases_and_segment_bounds(self):
        for key, value in (("phases", []), ("segments", []), ("segments", self.good["segments"][:1]),
                           ("segments", self.good["segments"] * 9)):
            report = deepcopy(self.good); report[key] = value; self.rewrite(report)
            with self.assertRaises(smoke.SmokeFailure):
                self.validate()
        for value in ("invalid", "2026-10-12T00:00:00", "2026-10-12T00:00:00+02:00"):
            report = deepcopy(self.good); report["phases"][0]["utc"] = value; self.rewrite(report)
            with self.assertRaises((ValueError, smoke.SmokeFailure)):
                self.validate()

    def test_receipt_and_workdir_cannot_redirect(self):
        if not hasattr(os, "symlink"):
            self.skipTest("No symlinks")
        original = self.root / "original.json"
        self.output.rename(original)
        try:
            self.output.symlink_to(original)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(smoke.SmokeFailure):
            self.validate()

    def test_stop_cannot_precede_or_extend_the_stored_window_chronology(self):
        for key, value in (("last_window", 1), ("last_window", 3), ("generation", 1), ("generation", 5)):
            report = deepcopy(self.good); report["stop"][key] = value; self.rewrite(report)
            with self.subTest(key=key, value=value), self.assertRaises(smoke.SmokeFailure):
                self.validate()
        report = deepcopy(self.good)
        report["stop"]["request_id"] = report["stop"]["ack_request_id"] = report["stop"]["generation"]
        self.rewrite(report)
        with self.assertRaises(smoke.SmokeFailure):
            self.validate()

    def test_validator_rejects_real_symlink_ancestors_for_receipt_work_and_executable(self):
        real = self.root / "real"; real.mkdir()
        output = real / "receipt.json"
        write_fixture_report(output, self.exe)
        candidate = real / "candidate.exe"; candidate.write_bytes(self.exe.read_bytes())
        alias = self.root / "alias"
        try:
            alias.symlink_to(real, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(smoke.SmokeFailure):
            smoke.validate_report(alias / "receipt.json", executable=self.exe)
        with self.assertRaises(smoke.SmokeFailure):
            smoke.file_info(alias / "receipt.json.work" / "recording.mtrec")
        with self.assertRaises(smoke.SmokeFailure):
            smoke.validate_report(output, executable=alias / "candidate.exe")

    def test_validator_rejects_modeled_windows_junction_and_reparse_ancestors(self):
        work = smoke.work_path(self.output)
        original = Path.lstat
        for target in (work, self.root):
            def modeled(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                if path == target:
                    return SimpleNamespace(st_mode=result.st_mode, st_size=result.st_size,
                        st_dev=result.st_dev, st_ino=result.st_ino, st_file_attributes=0x400)
                return result
            with self.subTest(target=target.name), patch.object(Path, "lstat", modeled), \
                    patch.object(smoke.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, create=True), \
                    self.assertRaises(smoke.SmokeFailure):
                self.validate()

    def test_artifact_and_executable_mismatch_are_rejected(self):
        for path in (self.exe, *(smoke.work_path(self.output) / item["name"] for item in self.good["artifacts"].values())):
            before = path.read_bytes()
            path.write_bytes(before + b"changed")
            with self.subTest(path=path.name), self.assertRaises(smoke.SmokeFailure):
                self.validate()
            path.write_bytes(before)

    def test_artifact_missing_empty_and_too_large_are_rejected(self):
        path = smoke.work_path(self.output) / "recording.mtrec"
        path.unlink()
        with self.assertRaises(OSError):
            self.validate()
        for size in (0, smoke.MAX_ARTIFACT_BYTES + 1):
            path.write_bytes(b"x" * size)
            with self.assertRaises(smoke.SmokeFailure):
                self.validate()


class DispatchAndBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rec-build-")
        self.root = Path(self.temp.name)
        self.exe = self.root / "candidate.exe"; self.exe.write_bytes(b"fake executable")
        self.output = self.root / "smoke.json"
        self.modules = patch.dict(sys.modules, {"recording_smoke": smoke, "serial_import_smoke": serial})
        self.modules.start()

    def tearDown(self):
        self.modules.stop(); self.temp.cleanup()

    def test_exact_recording_dispatch_and_arity(self):
        with patch.object(smoke, "run", return_value=7) as run:
            with patch.object(sys, "argv", ["exe", "--recording-smoke", "out.json"]):
                self.assertEqual(launcher.main(), 7)
            run.assert_called_once_with("out.json")
        for argv in (["exe", "--recording-smoke"], ["exe", "--recording-smoke", "a", "b"]):
            with patch.object(sys, "argv", argv), patch.dict(sys.modules, {"motortester.gui": None}):
                self.assertEqual(launcher.main(), 2)

    def test_ordinary_delegation_and_original_smokes_unchanged(self):
        gui = ModuleType("motortester.gui"); gui.main = Mock(return_value=19)
        for argv in (["exe"], ["exe", "--help"], ["exe", "--recording-smoke=out.json"]):
            with patch.object(sys, "argv", argv), patch.dict(sys.modules, {"motortester.gui": gui}):
                self.assertEqual(launcher.main(), 19)
        self.assertEqual(gui.main.call_count, 3)
        with patch.object(sys, "argv", ["exe", "--screenshot", "out.png"]), patch.object(launcher, "_screenshot_smoke", return_value=23):
            self.assertEqual(launcher.main(), 23)
        with patch.object(sys, "argv", ["exe", "--serial-import-smoke", "out.json"]), patch.object(serial, "run", return_value=29):
            self.assertEqual(launcher.main(), 29)

    def test_literal_module_capture_and_existing_spec_is_sufficient(self):
        tree = ast.parse((PACKAGING / "launch_gui.py").read_text())
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertTrue(any(node.module == "recording_smoke" and any(n.name == "run" for n in node.names) for node in imports))
        # Literal application imports already present in the frozen entry graph.
        smoke_tree = ast.parse((PACKAGING / "recording_smoke.py").read_text())
        self.assertTrue(any(isinstance(node, ast.ImportFrom) and node.module == "motortester.gui" for node in ast.walk(smoke_tree)))

    def test_success_requires_zero_exit_fresh_strict_report_and_actual_hashes(self):
        def run(executable, flag, output, **kwargs):
            self.assertEqual((executable, flag, output), (self.exe, "--recording-smoke", self.output))
            self.assertEqual(kwargs, {"timeout": 60})
            write_fixture_report(output, executable)
        report = build.run_frozen_recording_smoke(self.exe, self.output, run)
        self.assertEqual(report["status"], "passed")
        with self.assertRaises(RuntimeError):
            build.run_frozen_recording_smoke(self.exe, self.output, Mock())

    def test_missing_counterfeit_failed_child_and_timeout_block_gate(self):
        for case in ("missing", "counterfeit", "nonzero", "timeout"):
            with self.subTest(case=case):
                if self.output.exists(): self.output.unlink()
                def run(*args, **kwargs):
                    if case == "counterfeit": self.output.write_text('{"status":"passed"}')
                    if case == "nonzero": raise subprocess.CalledProcessError(1, "child")
                    if case == "timeout": raise subprocess.TimeoutExpired("child", 60)
                with self.assertRaises((OSError, ValueError, smoke.SmokeFailure, subprocess.SubprocessError)):
                    build.run_frozen_recording_smoke(self.exe, self.output, run)

    def test_preexisting_workdir_prevents_child_and_is_untouched(self):
        smoke.work_path(self.output).mkdir()
        sentinel = smoke.work_path(self.output) / "saved"; sentinel.write_bytes(b"keep")
        run = Mock()
        with self.assertRaises(RuntimeError):
            build.run_frozen_recording_smoke(self.exe, self.output, run)
        run.assert_not_called(); self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_executable_mutation_during_child_rejected_even_when_receipt_rehashes_it(self):
        def run(*args, **kwargs):
            self.exe.write_bytes(b"replacement")
            write_fixture_report(self.output, self.exe)
        with self.assertRaises(ValueError):
            build.run_frozen_recording_smoke(self.exe, self.output, run)

    def test_old_candidate_without_recording_entrypoint_cannot_pass(self):
        old = self.root / "old.py"
        old.write_text("# Old launcher surrogate: exits zero without recording support.\n")
        def run(*args, **kwargs):
            subprocess.run([sys.executable, str(old), "--recording-smoke", str(self.output)], check=True, timeout=5)
        with self.assertRaises(OSError):
            build.run_frozen_recording_smoke(self.exe, self.output, run)

    def test_full_recipe_recording_failures_block_manifest_zip_and_success(self):
        from test_windows_smoke import BuildRecipeTests
        original = write_fixture_report
        for case in ("missing", "counterfeit", "source", "hash", "nonzero", "timeout"):
            def fixture(output, executable):
                if case == "missing": return
                if case == "nonzero": raise subprocess.CalledProcessError(7, "recording child")
                if case == "timeout": raise subprocess.TimeoutExpired("recording child", 60)
                if case == "counterfeit":
                    output.write_text('{"status":"passed"}')
                    return
                report = original(output, executable)
                if case == "source":
                    report["scope"] = "source-or-nonnative-development"
                    report["runtime"]["frozen"] = False
                else:
                    report["artifacts"]["archive"]["sha256"] = "f" * 64
                output.write_text(json.dumps(report))
            with self.subTest(case=case), patch(__name__ + ".write_fixture_report", fixture):
                BuildRecipeTests().recipe("recording-gate-failure")

    def test_timeout_reaps_real_child_and_retains_failure_log(self):
        processes = []; original = subprocess.Popen
        def observe(*args, **kwargs):
            result = original(*args, **kwargs); processes.append(result); return result
        log = self.root / "child.log"
        with patch.object(build.subprocess, "Popen", side_effect=observe), self.assertRaises(subprocess.TimeoutExpired):
            build.run_logged([sys.executable, "-c", "import time;print('checkpoint',flush=True);time.sleep(60)"],
                cwd=self.root, env=os.environ.copy(), log=log, timeout=0.3)
        self.assertIsNotNone(processes[0].poll())
        self.assertIn("direct child reaped", log.read_text())
        self.assertIn("checkpoint", log.read_text())

    def test_unconfirmed_child_reaping_fails(self):
        child = Mock(returncode=None); child.wait.side_effect = subprocess.TimeoutExpired("child", 1)
        with patch.object(build.subprocess, "Popen", return_value=child), self.assertRaises(RuntimeError):
            build.run_logged(["candidate.exe"], cwd=self.root, env={}, log=self.root / "child.log", timeout=1)
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_args_list[-1].kwargs, {"timeout": 5})


class SourceExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rec source тест ")
        self.root = Path(self.temp.name)
        self.output = self.root / "запись с пробелом.json"

    def tearDown(self):
        self.temp.cleanup()

    def child(self, setup="", *, optimized=False, timeout=20):
        code = "import sys;sys.path.insert(0,sys.argv[1]);import recording_smoke as smoke\n" + setup + "\nraise SystemExit(smoke.run(sys.argv[2]))\n"
        env = os.environ | {"QT_QPA_PLATFORM": "offscreen", "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run([sys.executable, *(["-O"] if optimized else []), "-c", code, str(PACKAGING), str(self.output)],
                              cwd=HOST, env=env, capture_output=True, timeout=timeout)

    def report(self):
        return json.loads(self.output.read_text())

    def test_real_source_record_stop_save_fresh_open_navigation(self):
        process = self.child()
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace")[-4000:])
        report = smoke.validate_report(self.output, executable=sys.executable, require_frozen_windows=False)
        self.assertEqual(report["scope"], "source-or-nonnative-development")
        self.assertFalse(report["runtime"]["frozen"])
        self.assertGreaterEqual(sum(s["kind"] == "capture" for s in report["segments"]), 2)
        with self.assertRaises(smoke.SmokeFailure):
            smoke.validate_report(self.output, executable=sys.executable)
        self.assertEqual(set(p.name for p in smoke.work_path(self.output).iterdir()),
                         {a["name"] for a in report["artifacts"].values()})
        lines = [line for line in process.stdout.decode().splitlines() if line.startswith("recording-smoke live-only passed:")]
        self.assertEqual(lines, [
            "recording-smoke live-only passed: Single pre=512 edge=rising raw=2048 trigger=512",
            "recording-smoke live-only passed: Single pre=1024 edge=falling raw=2048 trigger=1024",
            "recording-smoke live-only passed: Normal pre=1024 no-edge STATUS=WAITING->STOPPED; reported, not physical"])
        # Additional live-only checks do not rewrite v1 or claim that its saved
        # recording contains the nonzero-prehistory captures.
        self.assertEqual(report["schema_version"], 1)
        self.assertTrue(all(s["provenance"]["config_payload_hex"] ==
                            "01000100000000080000000000000000" for s in report["segments"]))

    def test_omitted_live_prechecks_cannot_produce_final_success(self):
        process = self.child("smoke._prehistory_checks=lambda *args: None")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(self.report()["error"], "prehistory-checks-incomplete")
        self.assertEqual(self.report()["status"], "failed")
        self.assertNotIn(b"live-only passed", process.stdout)
        self.assertFalse(any(smoke.work_path(self.output).iterdir()))

    def test_each_nonzero_selection_is_mandatory_and_cannot_silently_fall_back(self):
        for pre in (512, 1024):
            with self.subTest(pre=pre):
                self.output = self.root / f"missing-{pre}.json"
                setup = '''from dataclasses import replace
from motortester.gui_acquisition import AcquisitionDialog
original=AcquisitionDialog._settings
def settings(self):
 value=original(self)
 return replace(value,pretrigger_samples=0) if value.pretrigger_samples == PRE else value
AcquisitionDialog._settings=settings
'''.replace("PRE", str(pre))
                process = self.child(setup)
                self.assertEqual(process.returncode, 1)
                self.assertEqual(self.report()["status"], "failed")
                self.assertEqual(self.report()["error"], "prehistory-config-unconfirmed")
                self.assertNotIn(f"Single pre={pre}".encode(), process.stdout)
                self.assertFalse(any(smoke.work_path(self.output).iterdir()))

    def test_precheck_raw_codes_trigger_metadata_and_axis_are_verified(self):
        setups = [
            # Runtime-produced metadata remains in bounds but names the wrong
            # trigger; independently checked raw bytes alone must not suffice.
            '''from dataclasses import replace
from motortester.runtime import AcquisitionRuntime
original=AcquisitionRuntime._terminal
def terminal(self,result):
 events=original(self,result)
 if self._capture is not None and self._capture.pretrigger_samples == TARGET:
  self._capture=replace(self._capture,pretrigger_samples=0,trigger_index=0)
 return events
AcquisitionRuntime._terminal=terminal
''',
            '''from motortester.device_emulator import DeviceEmulator
import struct,zlib
original=DeviceEmulator._capture
def capture(self,kind):
 result=original(self,kind)
 if struct.unpack_from('<H',self._config,8)[0] == TARGET:
  struct.pack_into('<H',self._samples,2*100,123)
  self._crc=zlib.crc32(self._samples)
 return result
DeviceEmulator._capture=capture
''',
            '''from motortester.gui import MainWindow
original=MainWindow.show_capture
def shifted_capture(self,capture,*args,**kwargs):
 original(self,capture,*args,**kwargs)
 if capture.pretrigger_samples == TARGET:
  xs,ys=self.curve.getData()
  self.curve.setData(xs+1,ys)
MainWindow.show_capture=shifted_capture
''']
        for pre in (512, 1024):
            for index, setup in enumerate(setups):
                with self.subTest(pre=pre, index=index):
                    self.output = self.root / f"capture-mismatch-{pre}-{index}.json"
                    process = self.child(setup.replace("TARGET", str(pre)))
                    self.assertEqual(process.returncode, 1)
                    self.assertEqual(self.report()["status"], "failed")
                    self.assertIn(self.report()["error"], ("prehistory-capture-mismatch", "prehistory-plot-mismatch"))
                    self.assertNotIn(f"Single pre={pre}".encode(), process.stdout)
                    self.assertFalse(any(smoke.work_path(self.output).iterdir()))

    def test_stale_waiting_or_stopped_status_and_fabricated_no_edge_capture_fail(self):
        for code in (2, 0):
            with self.subTest(code=code):
                self.output = self.root / f"stale-status-{code}.json"
                setup = '''from dataclasses import replace
from motortester.session import SessionController
original=SessionController.snapshot
def snapshot(self):
 value=original(self)
 if value.config is not None and value.config.mode == 1 and value.config.pretrigger_samples == 1024 and value.device_status is not None and int(value.device_status.state) == CODE:
  return replace(value,device_status_stale=True)
 return value
SessionController.snapshot=snapshot
smoke.PHASE_TIMEOUT=.7
'''.replace("CODE", str(code))
                process = self.child(setup)
                self.assertEqual(process.returncode, 1)
                self.assertEqual(self.report()["error"], "deadline-exceeded")
                self.assertEqual(self.report()["status"], "failed")
                self.assertNotIn(b"Normal pre=1024 no-edge STATUS", process.stdout)
                self.assertFalse(any(smoke.work_path(self.output).iterdir()))
        self.output = self.root / "fabricated-no-edge.json"
        setup = '''from dataclasses import replace
from motortester.gui import MainWindow
original=MainWindow._acquisition_state
def fabricate(self,state):
 original(self,state)
 s=state.snapshot
 if s is not None and s.config is not None and s.config.mode == 1 and s.config.pretrigger_samples == 1024 and self.capture is not None:
  self.capture=replace(self.capture,sequence=self.capture.sequence+1)
MainWindow._acquisition_state=fabricate
'''
        process = self.child(setup)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(self.report()["error"], "prehistory-wait-mismatch")
        self.assertEqual(self.report()["status"], "failed")
        self.assertNotIn(b"Normal pre=1024 no-edge STATUS", process.stdout)

    def test_raw_ack_status_and_stop_identity_are_independently_required(self):
        for kind, expected in (("CONFIGURED", "prehistory-config-unconfirmed"),
                               ("STATUS", "device-status-unconfirmed"),
                               ("STOPPED", "prehistory-stop-unconfirmed")):
            with self.subTest(kind=kind):
                self.output = self.root / (kind + ".json")
                setup = '''from dataclasses import replace
from motortester.runtime import AcquisitionRuntime
from motortester.session import EventKind
original=AcquisitionRuntime.pump
def pump(self):
 events=original(self)
 # Retain the real controller/GUI state but corrupt only the observed raw
 # frame identity; labels and host phase alone must not satisfy the smoke.
 return tuple(replace(e,frame=replace(e.frame,session=e.frame.session+1)) if e.kind is EventKind.KIND and e.frame is not None else e for e in events)
AcquisitionRuntime.pump=pump
'''.replace("KIND", kind)
                process = self.child(setup)
                self.assertEqual(process.returncode, 1)
                self.assertEqual(self.report()["error"], expected)
                self.assertEqual(self.report()["status"], "failed")
                self.assertFalse(any(smoke.work_path(self.output).iterdir()))

    def test_received_status_and_accepted_config_cannot_be_replaced_by_labels_or_host_phase(self):
        setups = [
            '''from dataclasses import replace
from motortester.session import SessionController
original=SessionController.snapshot
def snapshot(self):
 value=original(self)
 return replace(value,device_status=None)
SessionController.snapshot=snapshot
smoke.PHASE_TIMEOUT=.7
''',
            '''from motortester import gui_acquisition
gui_acquisition.reported_device_status_text=lambda state: 'Host phase READY is not received STATUS'
''',
            '''from motortester.gui_acquisition import AcquisitionDialog
original=AcquisitionDialog.refresh
def refresh(self):
 original(self)
 self.pretrigger_status.setText('Requested value is not CONFIG_ACK')
AcquisitionDialog.refresh=refresh
''']
        for index, setup in enumerate(setups):
            with self.subTest(index=index):
                self.output = self.root / f"status-mismatch-{index}.json"
                process = self.child(setup)
                self.assertEqual(process.returncode, 1)
                self.assertEqual(self.report()["status"], "failed")
                self.assertIn(self.report()["error"], ("deadline-exceeded", "device-status-unconfirmed", "prehistory-config-unconfirmed"))
                self.assertNotIn(b"Normal pre=1024 no-edge STATUS", process.stdout)
                self.assertFalse(any(smoke.work_path(self.output).iterdir()))

    def test_real_stop_during_third_window_preserves_partial_and_gaps(self):
        setup = """from motortester.acquisition import AcquisitionBridge, _EmulatorTransport
from PySide6 import QtCore
original_read=_EmulatorTransport.read
def small_reads(self,size):
 return original_read(self,min(size,97))
_EmulatorTransport.read=small_reads
original_stop=AcquisitionBridge.stop
def delayed_stop(self):
 if self.state.recording is None: return original_stop(self)
 def when_begun():
  state=self.state.snapshot
  if state.window_id>=3 and state.window_barrier and (state.terminal is None or state.terminal.window_id != state.window_id):
   original_stop(self)
  else:
   QtCore.QTimer.singleShot(1,self,when_begun)
 QtCore.QTimer.singleShot(1,self,when_begun)
 return True
AcquisitionBridge.stop=delayed_stop
"""
        process = self.child(setup)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace")[-1500:])
        report = smoke.validate_report(self.output, executable=sys.executable, require_frozen_windows=False)
        self.assertGreaterEqual(sum(s["kind"] == "capture" for s in report["segments"]), 2)
        self.assertTrue(any(s["kind"] == "partial" for s in report["segments"]))

    def test_real_stop_before_third_begin_preserves_abort_event(self):
        setup = """from motortester.acquisition import AcquisitionBridge, _EmulatorTransport
from PySide6 import QtCore
import threading
allocated=threading.Event()
original_read=_EmulatorTransport.read
def hold_third_begin(self,size):
 state=self.peer.snapshot()
 if state.window_id>=3 and state.continuation:
  allocated.set()
  return b''
 return original_read(self,size)
_EmulatorTransport.read=hold_third_begin
original_stop=AcquisitionBridge.stop
def delayed_stop(self):
 if self.state.recording is None: return original_stop(self)
 def when_allocated():
  if allocated.is_set(): original_stop(self)
  else: QtCore.QTimer.singleShot(1,self,when_allocated)
 QtCore.QTimer.singleShot(1,self,when_allocated)
 return True
AcquisitionBridge.stop=delayed_stop
"""
        process = self.child(setup)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace")[-1500:])
        report = smoke.validate_report(self.output, executable=sys.executable, require_frozen_windows=False)
        self.assertEqual([s["kind"] for s in report["segments"]], ["capture", "capture", "abort"])
        self.assertEqual(report["segments"][-1]["sample_count"], 0)
        self.assertEqual(report["segments"][-1]["provenance"]["unavailable_reason"], "no_begin")

    def test_optimized_source_and_absent_console(self):
        process = self.child("sys.stdout=sys.stderr=None", optimized=True)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(self.report()["runtime"]["optimize"], 1)
        self.assertEqual(self.report()["status"], "passed")

    def test_import_and_validator_have_no_qt_or_device_side_effects(self):
        result = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,sys.argv[1]);import recording_smoke;assert 'PySide6' not in sys.modules;assert 'serial' not in sys.modules", str(PACKAGING)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_device_constructor_enumeration_and_open_are_never_used(self):
        setup = '''import serial, serial.tools.list_ports
from motortester.transport import SerialTransport
def forbidden(*args,**kwargs): raise RuntimeError("forbidden hardware access")
serial.Serial=serial.serial_for_url=serial.tools.list_ports.comports=serial.tools.list_ports.grep=forbidden
SerialTransport.open=forbidden
'''
        self.assertEqual(self.child(setup).returncode, 0)

    def test_accidental_actual_device_selection_is_rejected_before_connect(self):
        setup = """from motortester.gui_acquisition import AcquisitionDialog
original=AcquisitionDialog.__init__
def wrong_source(self,*args,**kwargs):
 original(self,*args,**kwargs)
 self.source.findData=lambda value: 1
AcquisitionDialog.__init__=wrong_source
"""
        self.assertNotEqual(self.child(setup).returncode, 0)
        self.assertEqual(self.report()["error"], "wrong-source")
        self.assertFalse(any(smoke.work_path(self.output).iterdir()))

    def test_raw_exception_and_environment_secret_never_enter_receipt(self):
        setup = '''from motortester import gui
import os
os.environ['RECORDING_PRIVATE_TEST']='private-token-value'
def failed(*args,**kwargs): raise OSError('/private/person/private-token-value')
gui.MainWindow.show_acquisition=failed
sys.stdout=sys.stderr=None
'''
        self.assertNotEqual(self.child(setup).returncode, 0)
        data = self.output.read_text()
        self.assertNotIn("private-token-value", data); self.assertNotIn("/private/person", data)
        self.assertEqual(self.report()["status"], "failed")

    def test_callback_exception_cannot_report_success(self):
        setup = '''from motortester import gui
original=gui.MainWindow.show_acquisition
def action(self):
 original(self)
 sys.excepthook(ValueError,ValueError('private callback'),None)
gui.MainWindow.show_acquisition=action
'''
        self.assertNotEqual(self.child(setup).returncode, 0)
        self.assertEqual(self.report()["error"], "callback-failed")
        self.assertNotIn("private callback", self.output.read_text())

    def test_save_and_open_failures_cannot_report_success(self):
        for operation in ("save_recording_path", "open_path"):
            self.output = self.root / (operation + ".json")
            setup = "from motortester import gui\nsmoke.PHASE_TIMEOUT=0.7\ngui.MainWindow." + operation + "=lambda *args: False"
            process = self.child(setup)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(self.report()["status"], "failed")

    def test_sample_corruption_fails_independent_oracle_even_if_persisted_consistently(self):
        setup = '''original=smoke._expected_samples
smoke._expected_samples=lambda sequence,generation: tuple((value+1)%4096 for value in original(sequence,generation))
'''
        self.assertNotEqual(self.child(setup).returncode, 0)
        self.assertEqual(self.report()["error"], "capture-mismatch")

    def test_writer_unsettled_and_finalization_invalidated_never_export(self):
        for field in ("writer_pending", "finalization_invalidated", "source_retention_required"):
            self.output = self.root / (field + ".json")
            setup = '''from dataclasses import replace
from motortester.recording_controller import RecordingController
original=RecordingController.snapshot
def snapshot(self):
 value=original(self)
 return replace(value, FIELD=True) if value.committed_segments>=2 else value
RecordingController.snapshot=snapshot
smoke.PHASE_TIMEOUT=1.5
'''.replace("FIELD", field)
            self.assertNotEqual(self.child(setup).returncode, 0)
            self.assertEqual(self.report()["status"], "failed")
            self.assertFalse((smoke.work_path(self.output) / "recording.mtrec").exists())

    def test_real_late_disk_completion_is_retired_without_late_export(self):
        for kind in ("persist", "save"):
            self.output = self.root / ("late-" + kind + ".json")
            setup = """from motortester.recording_bridge import _DiskWriter
import time
original=_DiskWriter._perform
def delayed(self,job):
 if job.kind == KIND: time.sleep(1.2)
 return original(self,job)
_DiskWriter._perform=delayed
smoke.PHASE_TIMEOUT=.6
""".replace("KIND", repr(kind))
            self.assertNotEqual(self.child(setup).returncode, 0)
            self.assertEqual(self.report()["status"], "failed")
            self.assertFalse((smoke.work_path(self.output) / "recording.mtrec").exists())

    def test_cleanup_timeout_does_not_claim_join_or_late_save(self):
        setup = """from motortester.recording_bridge import _DiskWriter
import time
original=_DiskWriter._perform
def delayed(self,job):
 if job.kind == 'save': time.sleep(1.2)
 return original(self,job)
_DiskWriter._perform=delayed
smoke.PHASE_TIMEOUT=.6
smoke.CLEANUP_TIMEOUT=.03
original_run=smoke.run
def checked_run(output):
 result=original_run(output)
 time.sleep(1.3)
 from pathlib import Path
 if (smoke.work_path(Path(output))/'recording.mtrec').exists(): raise RuntimeError('late export')
 return result
smoke.run=checked_run
"""
        process = self.child(setup)
        self.assertEqual(process.returncode, 1, process.stderr.decode(errors="replace")[-1000:])
        self.assertEqual(self.report()["error"], "cleanup-unconfirmed")
        self.assertIsNone(self.report()["cleanup"])
        self.assertFalse((smoke.work_path(self.output) / "recording.mtrec").exists())

    def test_post_retirement_faults_are_observed_and_fail_without_changing_adapter(self):
        for field in ("finalization_invalidated", "writer_pending", "source_retention_required"):
            self.output = self.root / ("retired-" + field + ".json")
            setup = """from dataclasses import replace
from motortester.recording_bridge import RecordingSessionAdapter
original=RecordingSessionAdapter.retire
def retired(self):
 original(self)
 snapshot=self.controller.snapshot()
 if snapshot.committed_segments>=2 and self._saved is not None:
  self.controller.snapshot=lambda: replace(snapshot, FIELD=True)
RecordingSessionAdapter.retire=retired
""".replace("FIELD", field)
            process = self.child(setup)
            self.assertEqual(process.returncode, 1)
            report = self.report()
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["error"], "cleanup-unconfirmed")
            observed = "finalization_invalidated" if field == "finalization_invalidated" else "pending_io"
            self.assertIs(report["cleanup"][observed], True)
            self.assertTrue(report["cleanup"]["acquisition_native_joined"])
            self.assertTrue(report["cleanup"]["writer_stopped"])
            # The successful save preceded this injected retirement fault; keep
            # its original artifact, while refusing a successful smoke receipt.
            self.assertTrue((smoke.work_path(self.output) / "recording.mtrec").is_file())

    def test_ordinary_retirement_is_measured_without_finalization_invalidation(self):
        setup = """from motortester.recording_bridge import RecordingSessionAdapter
original=RecordingSessionAdapter.retire
observed=[]
def retired(self):
 original(self)
 observed.append(self)
RecordingSessionAdapter.retire=retired
original_run=smoke.run
def verify_run(output):
 result=original_run(output)
 if not observed: raise RuntimeError('retirement not observed')
 for adapter in observed:
  state=adapter.state
  if not state.retired or state.writer_busy or state.writer_running or state.snapshot.finalization_invalidated:
   raise RuntimeError('ordinary retirement unresolved')
 return result
smoke.run=verify_run
"""
        process = self.child(setup)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace")[-1500:])
        self.assertEqual(self.report()["cleanup"], smoke.CLEANUP)

    def test_existing_receipt_workdir_and_invalid_output_never_clobbered(self):
        self.output.write_bytes(b"original")
        self.assertNotEqual(self.child().returncode, 0)
        self.assertEqual(self.output.read_bytes(), b"original")
        self.output.unlink(); smoke.work_path(self.output).mkdir()
        sentinel = smoke.work_path(self.output) / "original"; sentinel.write_bytes(b"keep")
        self.assertNotEqual(self.child().returncode, 0)
        self.assertFalse(self.output.exists()); self.assertEqual(sentinel.read_bytes(), b"keep")
        for raw in ("", "a.txt", "a.json:stream", "a/../b.json", "NUL.json", "//server/share/a.json", "a" * 181 + ".json"):
            with self.subTest(raw=raw), patch.dict(sys.modules, {"serial_import_smoke": serial}):
                self.assertEqual(smoke.run(raw), 1)

    def test_exclusive_receipt_race_preserves_competing_content(self):
        original = Path.open
        def raced(path, mode="r", *args, **kwargs):
            if path == self.output and mode == "x+b":
                with original(path, "wb") as stream:
                    stream.write(b"competing receipt")
            return original(path, mode, *args, **kwargs)
        with patch.dict(sys.modules, {"serial_import_smoke": serial}), patch.object(Path, "open", raced):
            self.assertEqual(smoke.run(str(self.output)), 1)
        self.assertEqual(self.output.read_bytes(), b"competing receipt")
        self.assertFalse(smoke.work_path(self.output).exists())

    def test_nonregular_artifact_is_rejected_without_opening_pipe(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("POSIX named-pipe control")
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        with self.assertRaises(smoke.SmokeFailure):
            smoke.file_info(fifo)

    def test_inner_event_deadline_is_finite_and_releases_python_workers(self):
        from PySide6 import QtCore
        import threading
        done = threading.Event()
        worker = threading.Thread(target=lambda: (time.sleep(.03), done.set()))
        worker.start()
        smoke._wait(done.is_set, QtCore, time.monotonic() + 1)
        worker.join(timeout=1); self.assertFalse(worker.is_alive())
        started = time.monotonic()
        with self.assertRaises(smoke.SmokeFailure):
            smoke._wait(lambda: False, QtCore, started + .03)
        self.assertLess(time.monotonic() - started, .5)


if __name__ == "__main__":
    unittest.main()

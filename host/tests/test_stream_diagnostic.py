"""Finite fake-backed diagnostic tests. Never imports/opens a real serial port."""
from dataclasses import replace
import hashlib
import io
import struct
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from motortester import stream_diagnostic as module
from motortester.stream_diagnostic import DiagnosticLimits, DiagnosticRun, read_report
from motortester.stream_serial import SerialSettings
from motortester.stream_recording import recover_attempt
from test_stream_controller import CONFIG, decoded, wait_for
from test_stream_serial import FakeFactory


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stream-diagnostic-")
        self.root = Path(self.temp.name)
        self.runs, self.release = [], []
        self.guard = patch("motortester.transport._serial_factory",
                           side_effect=AssertionError("Real serial factory forbidden"))
        self.guard.start()

    def tearDown(self):
        for gate in self.release:
            gate.set()
        for run in self.runs:
            run.close()
            # Explicit cleanup only, after any deliberately blocked fake returns.
            for backend in getattr(run, "test_factory", FakeFactory()).backends:
                backend.close_fail = False
            deadline = time.monotonic() + 3
            while not run.join(.02) and time.monotonic() < deadline:
                run.retry_close()
            self.assertTrue(run.join(0), "fake cleanup owner did not settle")
        self.guard.stop()
        self.temp.cleanup()

    def make(self, configure=None, *, limits=None, clock_ns=time.monotonic_ns, data=True):
        factory = FakeFactory()
        def setup(backend):
            if data:
                original = backend.peer.read
                emitted = [False]
                def read(maximum):
                    result = original(maximum)
                    if backend.peer.generation and backend.peer.start_waiting is None and not emitted[0]:
                        emitted[0] = True
                        backend.peer.data()
                    return result
                backend.peer.read = read
            if configure:
                configure(backend)
        factory.configure = setup
        run = DiagnosticRun(SerialSettings("FAKE-SELECTED", 230400, 0, .025),
            fake_backend_factory=factory, fake_origin="synthetic-fake", fixture_name="inspected EncodedPeer/FakeFactory",
            event_config=CONFIG, limits=limits or DiagnosticLimits(connect_ms=200, run_ms=40,
                drain_ms=200, close_ms=200), clock_ns=clock_ns)
        run.test_factory = factory
        self.runs.append(run)
        return run, factory

    def collect(self, run, name="sample.mtstream"):
        return run.collect(str(self.root / name))

    def export(self, run, name="trace.jsonl"):
        path = self.root / name
        run.write_report(str(path))
        self.assertTrue(run.join(3))
        return path, read_report(path)

    def hold(self):
        entered, release = threading.Event(), threading.Event()
        self.release.append(release)
        return entered, release

    def background(self, run):
        result = []
        thread = threading.Thread(target=lambda: result.append(self.collect(run)))
        thread.start()
        self.addCleanup(lambda: thread.join(3))
        return result, thread

    def test_construct_inert_explicit_fake_contract_and_one_run(self):
        run, factory = self.make()
        self.assertEqual(factory.calls, [])
        self.assertIsNone(run.snapshot().controller)
        self.assertTrue(run.join(0))
        for name, value in (("settings", SerialSettings("OTHER")), ("limits", DiagnosticLimits()), ("run_id", "changed")):
            with self.assertRaises(AttributeError):
                setattr(run, name, value)
        with self.assertRaises(TypeError):
            DiagnosticRun(SerialSettings("FAKE"), fake_origin="synthetic-fake", fixture_name="x", event_config=CONFIG)
        for origin in ("device", "synthetic", None):
            with self.assertRaises(ValueError):
                DiagnosticRun(SerialSettings("FAKE"), fake_backend_factory=factory,
                    fake_origin=origin, fixture_name="x", event_config=CONFIG)
        for kwargs in ({"run_ms": True}, {"run_ms": 10001}, {"max_raw_bytes": 0}, {"max_file_bytes": module.MAX_FILE + 1}):
            with self.assertRaises(ValueError):
                DiagnosticLimits(**kwargs)
        self.collect(run)
        with self.assertRaises(RuntimeError):
            self.collect(run, "again.mtstream")
        self.assertEqual(sum(c[0] == "factory" for c in factory.calls), 1)

    def test_encoded_record_stop_raw_prefix_crc_and_fresh_reopen(self):
        run, factory = self.make()
        report = self.collect(run)
        final = json.loads(report.final)
        backend = factory.backends[0]
        b, s = final["bridge"], final["bridge"]["session"]
        self.assertEqual(b["source_received_end"], 128)
        self.assertEqual(b["source_prefix_crc32"], zlib.crc32(backend.peer.raw))
        self.assertEqual(s["source_end"]["terminal"]["device_generated_end_sample_index"], 128)
        self.assertEqual(s["stop_acknowledgement"]["request_id"], backend.peer.stop_request)
        self.assertTrue(s["clean_completion_reported"])
        self.assertTrue(final["trace_complete"])
        self.assertFalse(final["full_endpoint_event_trace"])
        self.assertTrue(final["controller_joined"])
        self.assertEqual(recover_attempt(self.root / "sample.mtstream").recording.read_raw(0, 128), backend.peer.raw)
        writes = [r for r in report.callbacks if r.kind == "write"]
        reads = [r for r in report.callbacks if r.kind == "read"]
        self.assertEqual([decoded(r.offered)[0] for r in writes], [1, 2, 8, 5])
        self.assertTrue(all(r.accepted_count == len(r.offered) for r in writes))
        self.assertTrue(all(r.begin_ns <= r.return_ns for r in report.callbacks))
        self.assertTrue(all(len(r.received) <= r.read_maximum <= 544 for r in reads))
        self.assertEqual(len({call[1] for call in factory.calls}), 1)
        self.assertNotEqual(factory.calls[0][1], threading.get_ident())
        path, reopened = self.export(run)
        self.assertTrue(reopened.complete)
        actual_file = path.read_bytes()
        self.assertEqual(reopened.file_bytes, len(actual_file))
        self.assertEqual(reopened.file_sha256, hashlib.sha256(actual_file).hexdigest())
        self.assertTrue(run.snapshot().export.complete_receipt)
        self.assertTrue(run.snapshot().export.fsync_returned)
        self.assertTrue(run.snapshot().export.readback_verified)
        script = "import sys,json; from motortester.stream_diagnostic import read_report; r=read_report(sys.argv[1]); assert r.complete; print(len(r.records))"
        proc = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(int(proc.stdout), len(reopened.records))

    def test_fragmented_rx_zero_short_writes_are_exact(self):
        def configure(b):
            b.peer.partitions = (1, 7, 33, 544)
            b.peer.accepted_prefix = 7
            original = b.peer.write
            first = [True]
            def write(data):
                if first[0]:
                    first[0] = False
                    return 0
                return original(data)
            b.hooks["write"] = write
        run, _ = self.make(configure, limits=DiagnosticLimits(connect_ms=200, run_ms=150, drain_ms=300, close_ms=200))
        report = self.collect(run)
        tx = [r for r in report.callbacks if r.kind == "write"]
        self.assertEqual(tx[0].accepted_count, 0)
        self.assertTrue(any(0 < r.accepted_count < len(r.offered) for r in tx))
        stream = b"".join(r.offered[:r.accepted_count] for r in tx)
        self.assertEqual([decoded(part + b"\0")[0] for part in stream.split(b"\0") if part], [1, 2, 8, 5])
        self.assertTrue(json.loads(report.final)["bridge"]["session"]["clean_completion_reported"])

    def test_unknown_invalid_exception_write_no_fabricated_zero_or_retry(self):
        for value in (None, -1, True, 999999, "bad", OSError("private text")):
            with self.subTest(value=type(value).__name__):
                def config(b):
                    def write(data):
                        if isinstance(value, Exception):
                            raise value
                        return value
                    b.hooks["write"] = write
                run, factory = self.make(config)
                report = self.collect(run, f"unknown-{len(self.runs)}.mtstream")
                rows = [r for r in report.callbacks if r.kind == "write"]
                self.assertEqual(len(rows), 1)
                self.assertIsNone(rows[0].accepted_count)
                self.assertIn(rows[0].outcome, ("unknown", "exception"))
                self.assertNotIn("private text", report.final.decode())
                self.assertEqual(json.loads(report.final)["bridge"]["endpoint_first_fault"]["code"], "write_count_unknown")
                self.assertEqual(len([c for c in factory.calls if c[0] == "write"]), 1)

    def test_late_callback_acceptance_is_separate_from_endpoint_rejection(self):
        # Isolate the real proxy + accepted manual controller seam at exact
        # deterministic deadlines; the production collector has no manual loop.
        from motortester.stream_controller import StreamController
        from motortester.stream_serial import SerialStreamPeer
        for advance_ms in (1999, 2000, 2001):
            with self.subTest(advance_ms=advance_ms):
                now = [0]
                def configure(b):
                    original = b.peer.write
                    def write(data):
                        accepted = original(data)
                        now[0] += advance_ms * 1000000
                        return accepted
                    b.hooks["write"] = write
                run, _ = self.make(configure, clock_ns=lambda: now[0])
                peer = SerialStreamPeer(run.settings, backend_factory=run._backend_factory)
                c = StreamController(peer, event_config=CONFIG, managed_peer=True,
                                     threaded=False, clock=lambda: now[0] // 1000000)
                try:
                    self.assertTrue(c.connect()); c.service(0)
                    self.assertTrue(c.record(str(self.root / f"late-{advance_ms}.mtstream")))
                    c.service(0)
                    writes = [r for r in run._records if r.kind == "write"]
                    self.assertEqual(len(writes), 1)
                    self.assertEqual(writes[0].accepted_count, len(writes[0].offered))
                    self.assertEqual(writes[0].return_ns - writes[0].begin_ns, advance_ms * 1000000)
                    fault = c.snapshot().bridge.endpoint_first_fault
                    if advance_ms < 2000:
                        self.assertIsNone(fault)
                    else:
                        self.assertEqual(fault.code, "request_timeout")
                finally:
                    c.close()
                    for _ in range(20):
                        c.service(now[0] // 1000000); c.execute_disk_once()
                    self.assertTrue(c.join(0))

    def test_read_error_has_no_invented_causal_request_and_logger_failure_preserves_it(self):
        def configure(b):
            b.hooks["read"] = lambda maximum: (_ for _ in ()).throw(OSError("private read"))
        run, _ = self.make(configure)
        original = run._finish
        def finish(ticket, kind, result, exc):
            original(ticket, kind, result, exc)
            if kind == "read":
                raise RuntimeError("logger failed")
        with patch.object(run, "_finish", side_effect=finish):
            report = self.collect(run)
        final = json.loads(report.final)
        self.assertEqual(final["trace_error"], "diagnostic_completion")
        self.assertFalse(final["trace_complete"])
        first = final["bridge"]["endpoint_first_fault"]
        self.assertEqual(first["code"], "transport")
        self.assertIsNone(first["causal_request"])
        self.assertEqual(next(r for r in report.callbacks if r.kind == "read").outcome, "exception")

    def test_invalid_rx_payload_bounded_and_original_rejected(self):
        run, _ = self.make(lambda b: b.hooks.update(read=lambda n: b"x" * 10000))
        report = self.collect(run)
        rows = [r for r in report.callbacks if r.kind == "read"]
        self.assertEqual(rows[0].actual_rx_length, 10000)
        self.assertEqual(len(rows[0].received), 544)
        final = json.loads(report.final)
        self.assertEqual(final["trace_error"], "invalid_rx_truncated")
        self.assertFalse(final["trace_complete"])
        self.assertEqual(final["bridge"]["endpoint_first_fault"]["code"], "transport")

    def test_unknown_produced_end_and_missing_ack_stay_distinct(self):
        for unknown, acknowledge in ((True, True), (False, False)):
            def configure(b):
                original = b.peer._end
                def end(**kwargs):
                    original(reason=2 if unknown else 0, flags=3 if unknown else 0,
                             produced=0 if unknown else None, acknowledge=acknowledge)
                b.peer._end = end
            run, _ = self.make(configure)
            report = self.collect(run, f"terminal-{unknown}.mtstream")
            final = json.loads(report.final)
            b = final["bridge"]
            self.assertFalse(b["session"]["clean_completion_reported"])
            if unknown:
                # The accepted decoder, not this collector, interprets the unknown flag/zero carrier.
                self.assertIsNone(b["source_terminal"]["terminal"]["produced_end"])
            else:
                self.assertIsNone(b["session"]["stop_acknowledgement"])
                self.assertEqual(final["run_error"], "drain_unresolved")

    def test_item_byte_status_caps_keep_final_summary_and_stop(self):
        for limit in (DiagnosticLimits(max_callbacks=3, run_ms=30, drain_ms=50, close_ms=100),
                      DiagnosticLimits(max_raw_bytes=30, run_ms=30, drain_ms=50, close_ms=100),
                      DiagnosticLimits(max_snapshots=1, run_ms=30, drain_ms=50, close_ms=100)):
            run, factory = self.make(limits=limit)
            report = self.collect(run, f"cap-{len(self.runs)}.mtstream")
            final = json.loads(report.final)
            self.assertFalse(final["trace_complete"])
            self.assertIsNotNone(final["trace_error"])
            self.assertTrue(final["controller_joined"])
            self.assertLessEqual(len(report.callbacks), limit.max_callbacks)
            self.assertLessEqual(sum(len(r.offered) + len(r.received) for r in report.callbacks), limit.max_raw_bytes)
            self.assertLessEqual(len(report.snapshots), limit.max_snapshots)
            self.assertEqual(sum(c[0] == "close" for c in factory.calls), 1)
            path, checked = self.export(run, f"cap-{len(self.runs)}.jsonl")
            self.assertTrue(checked.complete)
            self.assertFalse(run.snapshot().export.complete_receipt)

    def test_stop_before_connect_and_no_r_empty_recordings(self):
        run, factory = self.make()
        run.request_stop()
        report = self.collect(run)
        self.assertFalse(any(c[0] == "write" for c in factory.calls))
        self.assertIsNone(json.loads(report.final)["bridge"])
        run, _ = self.make(data=False)
        report = self.collect(run, "empty.mtstream")
        session = json.loads(report.final)["bridge"]["session"]
        self.assertEqual(session["selected_start_sample_index"], 0)
        self.assertEqual(session["finalized_end_sample_index"], 0)
        self.assertTrue(session["clean_completion_reported"])

    def test_open_failure_cleanup_failure_retains_owner_for_explicit_retry(self):
        run, factory = self.make(lambda b: (setattr(b, "open_fail", True), setattr(b, "close_fail", True)),
                                limits=DiagnosticLimits(connect_ms=50, run_ms=10, drain_ms=30, close_ms=20))
        report = self.collect(run)
        final = json.loads(report.final)
        self.assertFalse(final["controller_joined"])
        self.assertFalse(final["trace_complete"])
        self.assertEqual(final["serial"]["state"], "close_failed")
        self.assertFalse(run.join(0))
        factory.backends[0].close_fail = False
        self.assertTrue(run.retry_close())
        self.assertTrue(run.join(2))
        self.assertEqual(json.loads(report.final), final)
        self.assertFalse(json.loads(report.final)["controller_joined"])

    def test_blocked_read_late_completion_cannot_rewrite_frozen_report(self):
        entered, release = self.hold()
        def configure(b):
            def read(n):
                entered.set()
                if not release.wait(3):
                    raise OSError("finite fake gate")
                return b.peer.read(n)
            b.hooks["read"] = read
        run, factory = self.make(configure, limits=DiagnosticLimits(connect_ms=100, run_ms=20, drain_ms=20, close_ms=20))
        result, thread = self.background(run)
        self.assertTrue(entered.wait(2))
        start = time.monotonic()
        run.request_stop(); run.close()
        self.assertLess(time.monotonic() - start, .1)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        report = result[0]
        before = report.final
        self.assertFalse(run.join(0))
        self.assertTrue(any(r.outcome == "pending" for r in report.callbacks))
        path, checked = self.export_unresolved(run)
        self.assertTrue(checked.complete)
        self.assertFalse(run.snapshot().export.complete_receipt)
        release.set()
        self.assertTrue(run.join(2))
        self.assertEqual(report.final, before)
        self.assertEqual(len(factory.backends), 1)

    def export_unresolved(self, run):
        path = self.root / "unresolved.jsonl"
        run.write_report(str(path))
        wait_for(lambda: run.snapshot().export.state != "running")
        return path, read_report(path)

    def test_blocked_report_fsync_does_not_block_stop_and_no_premature_receipt(self):
        run, _ = self.make()
        self.collect(run)
        entered, release = self.hold()
        original = module._fsync
        def fsync(fd):
            entered.set()
            if not release.wait(3):
                raise OSError("finite fsync gate")
            return original(fd)
        with patch.object(module, "_fsync", side_effect=fsync):
            run.write_report(str(self.root / "slow.jsonl"))
            self.assertTrue(entered.wait(2))
            run.request_stop(); run.close()
            self.assertFalse(run.join(.01))
            self.assertEqual(run.snapshot().export.state, "running")
            self.assertFalse(run.snapshot().export.fsync_returned)
            self.assertFalse(run.snapshot().export.complete_receipt)
            release.set()
            self.assertTrue(run.join(2))
        self.assertTrue(run.snapshot().export.complete_receipt)

    def test_failed_export_fsync_and_exclusive_destination(self):
        for failure in ("existing", "fsync", "quota"):
            limits = DiagnosticLimits(max_file_bytes=100) if failure == "quota" else None
            run, _ = self.make(limits=limits)
            self.collect(run, failure + ".mtstream")
            path = self.root / (failure + ".jsonl")
            if failure == "existing":
                path.write_bytes(b"keep")
            with patch.object(module, "_fsync", side_effect=OSError("fake failure")) if failure == "fsync" else patch.object(module, "_fsync", wraps=module._fsync):
                run.write_report(str(path)); self.assertTrue(run.join(3))
            status = run.snapshot().export
            self.assertEqual(status.state, "failed")
            self.assertFalse(status.complete_receipt)
            if failure == "existing":
                self.assertEqual(path.read_bytes(), b"keep")
            else:
                # A structurally finished file after failed fsync does not
                # recreate the missing runtime fsync-return observation.
                if failure == "quota":
                    self.assertFalse(read_report(path).complete)
                self.assertFalse(status.fsync_returned)

    def test_bad_prefix_and_wrong_ack_never_become_accepted_terminal(self):
        for kind in ("prefix", "ack"):
            def configure(b):
                original = b.peer._end
                def end(**kwargs):
                    if kind == "prefix":
                        original(crc=0)
                    else:
                        sequence = b.peer.sequence
                        original(acknowledge=False)
                        b.peer._rx(133, struct.pack("<IQII", b.peer.generation,
                            len(b.peer.raw) // 2, sequence, 0), b.peer.stop_request + 1)
                b.peer._end = end
            run, _ = self.make(configure)
            report = self.collect(run, kind + ".mtstream")
            bridge = json.loads(report.final)["bridge"]
            self.assertIsNotNone(bridge["endpoint_first_fault"])
            self.assertFalse(bridge["session"]["clean_completion_reported"])
            if kind == "prefix":
                self.assertIsNone(bridge["source_terminal"])
            else:
                self.assertIsNotNone(bridge["source_terminal"])
                self.assertIsNone(bridge["session"]["stop_acknowledgement"])

    def test_blocked_write_and_close_preserve_pending_identity_after_timeout(self):
        for kind in ("write", "close"):
            entered, release = self.hold()
            def configure(b):
                def callback(arg):
                    entered.set()
                    if not release.wait(3):
                        raise OSError("finite callback gate")
                    return b.peer.write(arg) if kind == "write" else None
                b.hooks[kind] = callback
            run, factory = self.make(configure, limits=DiagnosticLimits(connect_ms=100,
                run_ms=20, drain_ms=20, close_ms=20))
            result, thread = self.background(run)
            self.assertTrue(entered.wait(2))
            run.request_stop(); run.close()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            report = result[0]
            final = json.loads(report.final)
            self.assertFalse(final["controller_joined"])
            self.assertEqual(final["serial_call"], kind)
            self.assertFalse(final["trace_complete"])
            self.assertFalse(run.join(0))
            release.set(); self.assertTrue(run.join(2))
            self.assertEqual(json.loads(report.final), final)
            self.assertEqual(sum(c[0] == "factory" for c in factory.calls), 1)

    def test_blocked_sample_prepare_keeps_stop_responsive_and_no_R(self):
        from motortester.stream_recording import PreparedStreamWriter
        entered, release = self.hold()
        original = PreparedStreamWriter.prepare
        def prepare(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise OSError("finite sample disk gate")
            return original(*args, **kwargs)
        run, factory = self.make(limits=DiagnosticLimits(connect_ms=100, run_ms=30,
            drain_ms=20, close_ms=20))
        with patch.object(PreparedStreamWriter, "prepare", side_effect=prepare):
            result, thread = self.background(run)
            self.assertTrue(entered.wait(2))
            before = time.monotonic(); run.request_stop(); run.close()
            self.assertLess(time.monotonic() - before, .1)
            thread.join(2)
            report = result[0]
            final = json.loads(report.final)
            self.assertFalse(final["controller_joined"])
            self.assertTrue(final["worker_unresolved"])
            self.assertIsNone(final["bridge"]["session"]["selected_start_sample_index"])
            self.assertFalse(any(command[0] == 8 for command in factory.backends[0].peer.commands))
            release.set(); self.assertTrue(run.join(2))
        self.assertEqual(json.loads(report.final), final)

    def test_source_first_cause_survives_close_and_export_failure(self):
        def configure(b):
            original = b.peer._end
            def end(**kwargs):
                original(crc=0)
                b.close_fail = True
            b.peer._end = end
        run, factory = self.make(configure, limits=DiagnosticLimits(connect_ms=100,
            run_ms=30, drain_ms=30, close_ms=20))
        report = self.collect(run)
        first = json.loads(report.final)["bridge"]["endpoint_first_fault"]
        self.assertIsNotNone(first)
        self.assertEqual(json.loads(report.final)["serial"]["state"], "close_failed")
        with patch.object(module, "_fsync", side_effect=OSError("export failure")):
            run.write_report(str(self.root / "failed-after-source.jsonl"))
            wait_for(lambda: run.snapshot().export.state == "failed")
        self.assertEqual(json.loads(report.final)["bridge"]["endpoint_first_fault"], first)
        self.assertFalse(run.snapshot().export.complete_receipt)
        factory.backends[0].close_fail = False
        self.assertTrue(run.retry_close()); self.assertTrue(run.join(2))

    def test_export_write_close_failures_and_premature_success_negative_control(self):
        for kind in ("write", "close"):
            run, _ = self.make(); report = self.collect(run, kind + ".mtstream")
            original = module._open_file
            class FailedFile:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self
                def __exit__(self, *args):
                    self.stream.close()
                    if kind == "close": raise OSError("close failure")
                def write(self, data):
                    if kind == "write": raise OSError("write failure")
                    return self.stream.write(data)
                def flush(self): return self.stream.flush()
                def fileno(self): return self.stream.fileno()
            def opening(path, mode):
                stream = original(path, mode)
                return FailedFile(stream) if mode == "xb" else stream
            with patch.object(module, "_open_file", side_effect=opening):
                run.write_report(str(self.root / (kind + ".jsonl")))
                self.assertTrue(run.join(2))
            self.assertEqual(run.snapshot().export.state, "failed")
            self.assertFalse(run.snapshot().export.complete_receipt)
        # Recomputed chain/valid footer cannot endorse a declared pending
        # callback as a complete trace: this is a semantic negative control.
        pending = replace(report.callbacks[-1], outcome="pending", return_ns=None)
        header = json.loads((self.root / "close.jsonl").read_bytes().splitlines()[0])["payload"]
        forged = replace(report, _metadata=module._freeze_tree(header),
                         callbacks=report.callbacks[:-1] + (pending,))
        output = io.BytesIO(); module._write_report(output, forged, module.MAX_FILE)
        path = self.root / "premature.jsonl"; path.write_bytes(output.getvalue())
        self.assertFalse(read_report(path).complete)

    def test_hash_chain_reorder_corruption_truncation_omission_wrong_identity(self):
        run, _ = self.make()
        report = self.collect(run)
        path, checked = self.export(run)
        self.assertTrue(checked.complete)
        lines = path.read_bytes().splitlines(keepends=True)
        cases = {"corrupt": lines[0] + lines[1].replace(b'"kind"', b'"oops"', 1) + b"".join(lines[2:]),
                 "reorder": b"".join([lines[0], lines[2], lines[1], *lines[3:]]),
                 "omit": b"".join([*lines[:2], *lines[3:]]),
                 "truncate": b"".join(lines)[:-20], "extra": b"".join(lines) + b"x"}
        for name, data in cases.items():
            with self.subTest(name=name):
                target = self.root / (name + ".jsonl"); target.write_bytes(data)
                result = read_report(target)
                self.assertFalse(result.complete)
                self.assertIsNone(result.file_bytes)
                self.assertIsNone(result.file_sha256)
                self.assertTrue(all(type(entry) is bytes for entry in result.records))
        self.assertFalse(read_report(path, expected_run_id="0" * 32).complete)
        self.assertFalse(read_report(path, expected_source_hashes={}).complete)
        self.assertEqual(checked.summary, report.final)

    def test_same_run_rehashed_rx_substitution_fails_owned_receipt(self):
        run, _ = self.make()
        report = self.collect(run)
        path = self.root / "substituted.jsonl"
        original_open = module._open_file
        original_files = []

        def canonical(value):
            return json.dumps(value, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True, allow_nan=False).encode("ascii")

        class ChangeOnClose:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self.stream
            def __exit__(self, *args):
                self.stream.close()
                original_files.append(path.read_bytes())
                rows = [json.loads(line) for line in original_files[0].splitlines()]
                row = next(row for row in rows if row["kind"] == "callback" and
                           row["payload"]["kind"] == "read" and row["payload"]["received"])
                received = row["payload"]["received"]
                row["payload"]["received"] = ("00" if received[:2] != "00" else "01") + received[2:]
                chain, encoded = "0" * 64, []
                for row in rows:
                    row["previous"] = chain
                    if row["kind"] == "final":
                        row["payload"].update(prefix_bytes=sum(map(len, encoded)), prefix_sha256=chain)
                    body = {key: value for key, value in row.items() if key != "digest"}
                    row["digest"] = chain = hashlib.sha256(canonical(body)).hexdigest()
                    encoded.append(canonical(row) + b"\n")
                path.write_bytes(b"".join(encoded))

        def opening(target, mode):
            stream = original_open(target, mode)
            return ChangeOnClose(stream) if mode == "xb" else stream

        with patch.object(module, "_open_file", side_effect=opening):
            run.write_report(str(path))
            self.assertTrue(run.join(3))
        checked = read_report(path, expected_run_id=run.run_id,
            expected_source_hashes=json.loads(original_files[0].splitlines()[0])["payload"]["module_sha256"])
        self.assertTrue(checked.complete, "replacement must remain structurally valid")
        self.assertEqual(checked.file_bytes, len(original_files[0]))
        self.assertNotEqual(checked.file_sha256, hashlib.sha256(original_files[0]).hexdigest())
        received = [json.loads(row)["payload"]["received"] for row in checked.records
                    if json.loads(row)["kind"] == "callback"]
        self.assertNotEqual(received, [record.received.hex() for record in report.callbacks])
        status = run.snapshot().export
        self.assertEqual(status.state, "failed")
        self.assertTrue(status.fsync_returned)
        self.assertTrue(status.owner_settled)
        self.assertFalse(status.readback_verified)
        self.assertFalse(status.complete_receipt)

    def test_report_short_writes_progress_and_zero_write_fails(self):
        original_open = module._open_file
        for zero in (False, True):
            with self.subTest(zero=zero):
                run, _ = self.make()
                self.collect(run, str(zero) + ".mtstream")
                path = self.root / (str(zero) + ".jsonl")
                counts = []
                class ShortFile:
                    def __init__(self, stream): self.stream = stream
                    def __enter__(self): return self
                    def __exit__(self, *args): self.stream.close()
                    def write(self, data):
                        count = 0 if zero else self.stream.write(data[:113])
                        counts.append(count)
                        return count
                    def flush(self): return self.stream.flush()
                    def fileno(self): return self.stream.fileno()
                def opening(target, mode):
                    stream = original_open(target, mode)
                    return ShortFile(stream) if mode == "xb" else stream
                with patch.object(module, "_open_file", side_effect=opening):
                    run.write_report(str(path))
                    self.assertTrue(run.join(3))
                status, checked = run.snapshot().export, read_report(path)
                self.assertEqual(status.complete_receipt, not zero)
                self.assertEqual(status.readback_verified, not zero)
                self.assertEqual(status.fsync_returned, not zero)
                self.assertEqual(checked.complete, not zero)
                if zero:
                    self.assertEqual(counts, [0])
                    self.assertEqual(status.state, "failed")
                else:
                    actual = path.read_bytes()
                    self.assertGreater(len(counts), 1)
                    self.assertEqual(sum(counts), checked.file_bytes)
                    self.assertEqual(checked.file_bytes, len(actual))
                    self.assertEqual(checked.file_sha256, hashlib.sha256(actual).hexdigest())


if __name__ == "__main__":
    unittest.main()

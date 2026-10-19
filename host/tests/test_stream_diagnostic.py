"""Finite fake-backed diagnostic tests. Never imports/opens a real serial port."""
from dataclasses import asdict, fields, replace
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

    def background(self, run, name="sample.mtstream"):
        result = []
        thread = threading.Thread(target=lambda: result.append(self.collect(run, name)))
        thread.start()
        self.addCleanup(lambda: thread.join(3))
        return result, thread

    def collect_when_ready(self, run, expected_end, name="sample.mtstream"):
        """Positive fixture: Stop follows observed R/data, never a short sleep.

        These finite observation/cleanup budgets are test bounds, not an owner
        scheduling guarantee or a change to the production protocol deadlines.
        """
        result, thread = self.background(run, name)
        def ready():
            status = run.snapshot()
            controller = status.controller
            bridge = None if controller is None else controller.bridge
            session = None if bridge is None else bridge.session
            if (status.trace_error or status.run_error or
                    (controller is not None and controller.error) or
                    (bridge is not None and bridge.endpoint_first_fault) or
                    (session is not None and session.first_failure)):
                self.fail("Unexpected failure before positive precondition: " + repr(status))
            if not thread.is_alive():
                self.fail("Collector returned before positive precondition: " + repr(status))
            return (controller is not None and controller.phase == "recording" and
                    session is not None and session.source_started and
                    session.selected_start_sample_index == 0 and
                    session.accepted_end_sample_index == expected_end)
        try:
            try:
                wait_for(ready, timeout=4)
            except AssertionError as exc:
                self.fail(str(exc) + "; last status: " + repr(run.snapshot()))
        finally:
            run.request_stop()
            thread.join(6)
        self.assertFalse(thread.is_alive(), repr(run.snapshot()))
        self.assertEqual(len(result), 1, repr(run.snapshot()))
        return result[0]

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
        run, _ = self.make(configure, limits=DiagnosticLimits(
            connect_ms=2000, run_ms=5000, drain_ms=4500, close_ms=1000))
        report = self.collect_when_ready(run, 128)
        tx = [r for r in report.callbacks if r.kind == "write"]
        self.assertEqual(tx[0].accepted_count, 0)
        self.assertTrue(any(0 < r.accepted_count < len(r.offered) for r in tx))
        stream = b"".join(r.offered[:r.accepted_count] for r in tx)
        self.assertEqual([decoded(part + b"\0")[0] for part in stream.split(b"\0") if part], [1, 2, 8, 5])
        self.assertTrue(json.loads(report.final)["bridge"]["session"]["clean_completion_reported"])

    def test_fragmented_ready_stop_survives_slow_owner_schedule(self):
        from motortester import stream_controller
        # Only the test process's owner wait is slowed. No clock/deadline moves;
        # success still requires every original raw-prefix/finalization check.
        with patch.object(stream_controller, "OWNER_POLL_SECONDS", .016):
            self.test_fragmented_rx_zero_short_writes_are_exact()

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
        self.empty_recording()

    def empty_recording(self, configure=None):
        run, _ = self.make(configure, data=False, limits=DiagnosticLimits(
            connect_ms=2000, run_ms=5000, drain_ms=4500, close_ms=1000))
        report = self.collect_when_ready(run, 0, "empty.mtstream")
        session = json.loads(report.final)["bridge"]["session"]
        self.assertEqual(session["selected_start_sample_index"], 0)
        self.assertEqual(session["finalized_end_sample_index"], 0)
        self.assertTrue(session["clean_completion_reported"])

    def test_empty_ready_stop_survives_delayed_caps_return(self):
        def configure(backend):
            original = backend.peer.read
            calls = [0]
            def read(maximum):
                calls[0] += 1
                if calls[0] == 2:
                    # Adversarial delay, not the condition for a passing test.
                    time.sleep(.060)
                return original(maximum)
            backend.peer.read = read
        self.empty_recording(configure)

    def test_short_run_budget_during_caps_keeps_no_start_or_R(self):
        entered, release = self.hold()
        stopped = threading.Event()
        now = [0]
        def configure(backend):
            original = backend.peer.read
            calls = [0]
            def read(maximum):
                calls[0] += 1
                if calls[0] == 2:
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("finite CAPS barrier expired")
                return original(maximum)
            backend.peer.read = read
        run, factory = self.make(configure, data=False, clock_ns=lambda: now[0])
        original_stop = run.request_stop
        def stop():
            result = original_stop()
            stopped.set()
            return result
        with patch.object(run, "request_stop", side_effect=stop):
            result, thread = self.background(run)
            try:
                self.assertTrue(entered.wait(2), repr(run.snapshot()))
                # Time advances only while CAPS is held. The unchanged 40 ms
                # collector budget expires before the response may return.
                now[0] = 41_000_000
                self.assertTrue(stopped.wait(2), repr(run.snapshot()))
            finally:
                run.request_stop()
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive(), repr(run.snapshot()))
        self.assertEqual(len(result), 1)
        final = json.loads(result[0].final)
        session = final["bridge"]["session"]
        self.assertEqual([command[0] for command in factory.backends[0].peer.commands], [1, 2])
        self.assertEqual(final["stop_intent_ns"] - final["record_intent_return_ns"], 41_000_000)
        self.assertIsNone(final["bridge"]["endpoint_first_fault"])
        self.assertFalse(session["source_started"])
        self.assertIsNone(session["selected_start_sample_index"])
        self.assertFalse(session["clean_completion_reported"])

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


# These bytes model the declared image negotiation only. They are independent
# test responses, never evidence that a physical image ran or sampled anything.
STR1_BUILD = 0x53545231
STR1_CAPS = (1, 1, 0, 12, 50000, 1, 128, 1)
STR1_SOURCE = "31b93659164dec376bea5b973b597196c6a4a0ad"
STR1_ARTIFACT = "90fee8a085d1e561cc6f5c28c26db26a43c1e57145ae41314bf70e9422c7b904"
STR1_CARRIER = "rehearsal:str1:" + STR1_SOURCE + ":53545231"


def str1_factory(*, build=STR1_BUILD, caps=STR1_CAPS, unknown_end=True, configure=None):
    """Adapt only EncodedPeer's finite response hook; use its real byte FIFO."""
    factory = FakeFactory()
    def setup(backend):
        peer = backend.peer
        original_rx = peer._rx
        def rx(kind, payload, request=0):
            if kind == 129:
                payload = payload[:8] + struct.pack("<I", build) + payload[12:]
            elif kind == 130:
                payload = struct.pack("<IHBBIIHHI", *caps[:7], 0, caps[7])
            return original_rx(kind, payload, request)
        peer._rx = rx
        if unknown_end:
            original_end = peer._end
            def end(**kwargs):
                original_end(reason=2, flags=3, produced=0, **kwargs)
            peer._end = end
        if configure is not None:
            configure(backend)
    factory.configure = setup
    return factory


def fresh_str1_read(path, *, report=False):
    """A new process performs the same public reader validation under normal/-O."""
    script = r"""
from dataclasses import asdict
import hashlib, json, sys
from motortester.stream_diagnostic import read_report
from motortester.stream_provenance import profile_for_identity, provenance_text
from motortester.stream_recording import recover_attempt
if sys.argv[2] == 'report':
    r = read_report(sys.argv[1])
    print(json.dumps(dict(complete=r.complete, summary=json.loads(r.summary))))
else:
    a = recover_attempt(sys.argv[1]); r = a.recording
    i = a.preparation.identity; p = profile_for_identity(i)
    t = r.terminal if r is not None else a.unbound_outcome
    print(json.dumps(dict(identity=asdict(i), profile=None if p is None else asdict(p),
      text=provenance_text(i), selected=a.selected_start is not None,
      recording=r is not None, first=None if r is None else r.first_sample_index,
      end=None if r is None else r.end_sample_index,
      clean=None if r is None else r.clean_stop_reported,
      terminal=None if t is None else asdict(t),
      raw_sha=None if r is None else hashlib.sha256(b''.join(b.raw_u16le for b in r.iter_raw())).hexdigest())))
"""
    args = [sys.executable] + (["-O"] if sys.flags.optimize else [])
    return subprocess.run(args + ["-c", script, str(path), "report" if report else "journal"],
                          capture_output=True, text=True, timeout=5)


class Str1RehearsalTests(unittest.TestCase):
    """Exact fake-only admission controls; no inherited test aggregates."""
    collect = DiagnosticTests.collect
    export = DiagnosticTests.export

    def setUp(self):
        from motortester.stream_provenance import STR1_DIAGNOSTIC_DECLARATION
        self.declaration = STR1_DIAGNOSTIC_DECLARATION
        self.temp = tempfile.TemporaryDirectory(prefix="stream-str1-rehearsal-")
        self.root = Path(self.temp.name)
        self.runs, self.controllers = [], []
        self.guard = patch("motortester.transport._serial_factory",
                           side_effect=AssertionError("Real serial factory forbidden"))
        self.guard.start()

    def tearDown(self):
        for run in self.runs:
            run.close()
            self.assertTrue(run.join(3), "diagnostic fake owner did not settle")
        for controller, factory, now in self.controllers:
            controller.close()
            for _ in range(100):
                controller.service(now[0]); controller.execute_disk_once()
                if controller.join(0):
                    break
            self.assertTrue(controller.join(0), "manual fake owner did not settle")
        self.guard.stop()
        self.temp.cleanup()

    def make_run(self, configure=None, *, data=True, limits=None, **fixture):
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
            if configure is not None:
                configure(backend)
        factory = str1_factory(configure=setup, **fixture)
        run = DiagnosticRun(SerialSettings("FAKE-STR1", 230400, 0, .025),
            fake_backend_factory=factory, fake_origin="synthetic-fake",
            fixture_name="inspected EncodedPeer/FakeFactory exact str1 rehearsal",
            event_config=CONFIG, fake_diagnostic_declaration=self.declaration,
            limits=limits or DiagnosticLimits(connect_ms=200, run_ms=60, drain_ms=200, close_ms=200))
        self.runs.append(run)
        return run, factory

    def make_controller(self, **fixture):
        from motortester.stream_controller import StreamController
        from motortester.stream_serial import SerialStreamPeer
        factory, now = str1_factory(**fixture), [0]
        peer = SerialStreamPeer(SerialSettings("FAKE-STR1"), fake_backend_factory=factory)
        controller = StreamController(peer, event_config=CONFIG, managed_peer=True,
            threaded=False, clock=lambda: now[0], fake_diagnostic_declaration=self.declaration)
        self.controllers.append((controller, factory, now))
        return controller, factory, now

    def step(self, controller, *, disk=True):
        now = next(now for c, f, now in self.controllers if c is controller)
        controller.service(now[0])
        if disk:
            controller.execute_disk_once()

    def until(self, controller, predicate, *, disk=True):
        for _ in range(200):
            if predicate(controller.snapshot()):
                return controller.snapshot()
            self.step(controller, disk=disk)
        self.fail("finite str1 controller did not settle: " + repr(controller.snapshot()))

    def connected(self, **fixture):
        controller, factory, now = self.make_controller(**fixture)
        self.assertTrue(controller.connect())
        self.until(controller, lambda s: s.phase == "connected")
        return controller, factory, now

    def opened(self, name="sample.mtstream"):
        proc = fresh_str1_read(self.root / name)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def assert_no_admission(self, controller, factory, path):
        b = controller.snapshot().bridge
        self.assertIsNotNone(b)
        self.assertIsNone(b.identity)
        self.assertIsNone(b.session)
        self.assertIsNone(b.storage)
        self.assertIsNone(b.storage_job_id)
        self.assertIsNone(b.mailbox)
        self.assertFalse(path.exists())
        self.assertFalse(any(command[0] == 8 for backend in factory.backends
                             for command in backend.peer.commands))
        self.assertTrue(all(backend.peer.generation == 0 for backend in factory.backends))

    def test_exact_declaration_is_inert_and_distinct_from_syn1(self):
        from motortester.stream_provenance import (admit_profile,
            admit_fake_diagnostic_declaration, rehearsal_identity_metadata,
            SYNTHETIC_DMA_PROFILE, ProvenanceError)
        self.assertEqual(asdict(self.declaration), dict(profile_id="str1",
            accounting_domain="completed-dma-v1", provenance_revision="0.2-draft-dma-sequence-20261019",
            source_commit=STR1_SOURCE, artifact_sha256=STR1_ARTIFACT, expected_build_id=STR1_BUILD,
            origin="device", config_id=1, nominal_fs_hz=50000))
        self.assertIs(admit_fake_diagnostic_declaration(self.declaration), self.declaration)
        self.assertEqual(rehearsal_identity_metadata(self.declaration),
            ("0.2-draft-dma-sequence-20261019|completed-dma-v1", STR1_ARTIFACT, STR1_CARRIER))
        self.assertEqual(len(STR1_CARRIER), 64)
        self.assertIs(admit_profile(SYNTHETIC_DMA_PROFILE), SYNTHETIC_DMA_PROFILE)
        with self.assertRaises(ProvenanceError):
            admit_profile(self.declaration)
        with self.assertRaises(ProvenanceError):
            admit_fake_diagnostic_declaration(SYNTHETIC_DMA_PROFILE)
        run, factory = self.make_run()
        self.assertEqual(factory.calls, [])
        self.assertIsNone(run.snapshot().controller)
        self.assertTrue(run.join(0))

    def test_each_declaration_value_and_type_rejected_before_threads_or_factory(self):
        from motortester.stream_controller import StreamController
        from motortester.stream_provenance import (ProducerProfile, ProvenanceError,
            admit_fake_diagnostic_declaration, rehearsal_identity_metadata)
        from motortester.stream_serial import SerialStreamPeer
        factory = FakeFactory()
        peer = SerialStreamPeer(SerialSettings("FAKE-STR1"), fake_backend_factory=factory)
        mutations = []
        for field in fields(self.declaration):
            original = getattr(self.declaration, field.name)
            values = (original + "x", original.encode(), None) if type(original) is str else (
                original + 1, bool(original), float(original), str(original))
            mutations.extend((field.name, value, replace(self.declaration, **{field.name: value})) for value in values)
        class DerivedProfile(ProducerProfile):
            pass
        mutations += [("object", type(value).__name__, value) for value in
                      (None, "str1", asdict(self.declaration), STR1_BUILD,
                       DerivedProfile(**asdict(self.declaration)))]
        with patch("motortester.stream_controller.threading.Thread") as threads:
            for name, value, bad in mutations:
                with self.subTest(field=name, value=value):
                    with self.assertRaises(ProvenanceError):
                        admit_fake_diagnostic_declaration(bad)
                    with self.assertRaises(ProvenanceError):
                        rehearsal_identity_metadata(bad)
                    # None explicitly means the unchanged generic path in the
                    # constructors; test invalid declarations, not that default.
                    if bad is None:
                        continue
                    with self.assertRaises(ProvenanceError):
                        StreamController(peer, event_config=CONFIG, managed_peer=True,
                                         fake_diagnostic_declaration=bad)
                    with self.assertRaises(ProvenanceError):
                        DiagnosticRun(SerialSettings("FAKE-STR1"), fake_backend_factory=factory,
                            fake_origin="synthetic-fake", fixture_name="invalid declaration",
                            event_config=CONFIG, fake_diagnostic_declaration=bad)
            threads.assert_not_called()
        self.assertEqual(factory.calls, [])
        self.assertEqual(peer.initial_status.state, "closed")

    def test_factory_selection_rejects_default_plain_direct_subclass_and_mixed_profiles(self):
        from motortester.stream_bridge import StreamBridge
        from motortester.stream_controller import StreamController
        from motortester.stream_provenance import SYNTHETIC_DMA_PROFILE
        from motortester.stream_serial import SerialStreamPeer
        from test_stream_controller import EncodedPeer
        from motortester.transport import TransportArgumentError
        factory, settings = FakeFactory(), SerialSettings("FAKE-STR1")
        with self.assertRaises(TransportArgumentError):
            SerialStreamPeer(settings, backend_factory=factory, fake_backend_factory=factory)
        with self.assertRaises(TransportArgumentError):
            SerialStreamPeer(settings, fake_backend_factory=True)
        exact = SerialStreamPeer(settings, fake_backend_factory=factory)
        self.assertIs(exact.fake_backend_selected, True)
        with self.assertRaises(AttributeError):
            exact.fake_backend_selected = False
        class DerivedPeer(SerialStreamPeer):
            pass
        cases = [(SerialStreamPeer(settings), True),
                 (SerialStreamPeer(settings, backend_factory=factory), True),
                 (exact, False), (EncodedPeer(), False), (EncodedPeer(), True),
                 (DerivedPeer(settings, fake_backend_factory=factory), True)]
        with patch("motortester.stream_controller.threading.Thread") as threads:
            for peer, managed in cases:
                with self.subTest(peer=type(peer).__name__, managed=managed), self.assertRaises(ValueError):
                    StreamController(peer, event_config=CONFIG, managed_peer=managed,
                                     fake_diagnostic_declaration=self.declaration)
            with self.assertRaises(ValueError):
                StreamController(exact, event_config=CONFIG, managed_peer=True,
                    producer_profile=SYNTHETIC_DMA_PROFILE, fake_diagnostic_declaration=self.declaration)
            with self.assertRaises(ValueError):
                StreamBridge("mixed", producer_profile=SYNTHETIC_DMA_PROFILE,
                             fake_diagnostic_declaration=self.declaration)
            for managed in (False, True):
                with self.assertRaises(ValueError):
                    StreamController(exact, event_config=CONFIG, managed_peer=managed,
                                     producer_profile=SYNTHETIC_DMA_PROFILE)
            threads.assert_not_called()
        with self.assertRaises(TypeError):
            DiagnosticRun(settings, fake_origin="synthetic-fake", fixture_name="missing factory",
                          event_config=CONFIG, fake_diagnostic_declaration=self.declaration)
        for bad in (None, False, "factory"):
            with self.assertRaises(ValueError):
                DiagnosticRun(settings, fake_backend_factory=bad, fake_origin="synthetic-fake",
                    fixture_name="bad factory", event_config=CONFIG,
                    fake_diagnostic_declaration=self.declaration)
        self.assertEqual(factory.calls, [])

    def test_each_encoded_hello_caps_mismatch_has_no_plan_session_writer_generation_or_R(self):
        mismatches = [("build", dict(build=STR1_BUILD + 1))]
        for index, name in enumerate(("config", "profile", "channel", "adc_bits", "fs_num", "fs_den", "max_block", "flags")):
            changed = list(STR1_CAPS); changed[index] += 1
            mismatches.append((name, dict(caps=tuple(changed))))
        for name, fixture in mismatches:
            with self.subTest(field=name):
                c, factory, now = self.connected(**fixture)
                path = self.root / (name + ".mtstream")
                with patch("motortester.stream_endpoint.Endpoint.plan_start") as plan, \
                     patch("motortester.stream_bridge.StreamSession") as session, \
                     patch("motortester.stream_bridge.StorageExecutor") as storage:
                    self.assertTrue(c.record(str(path)))
                    self.until(c, lambda s: s.error is not None or
                               (s.bridge is not None and s.bridge.endpoint_first_fault is not None))
                    self.assert_no_admission(c, factory, path)
                    plan.assert_not_called(); session.assert_not_called(); storage.assert_not_called()
                b = c.snapshot().bridge
                self.assertEqual(b.accepted_build_id, fixture.get("build", STR1_BUILD))
                # The existing strict codec rejects all eight altered CAPS;
                # failed payloads cannot become accepted observation evidence.
                self.assertEqual(b.accepted_caps, STR1_CAPS if name == "build" else None)
                self.assertFalse(c.record(str(path)))
                self.assertFalse(c.preview())
                self.assertEqual([x[0] for x in factory.backends[0].peer.commands], [1, 2])

    def test_negotiation_validator_checks_all_types_and_fields_without_coercion(self):
        from motortester.stream_provenance import ProvenanceError, validate_fake_diagnostic_negotiation
        from motortester.stream_wire import Caps
        caps = Caps()
        validate_fake_diagnostic_negotiation(self.declaration, STR1_BUILD, caps)
        for build in (STR1_BUILD + 1, float(STR1_BUILD), True, str(STR1_BUILD), None):
            with self.subTest(build=build), self.assertRaises(ProvenanceError):
                validate_fake_diagnostic_negotiation(self.declaration, build, caps)
        for field in fields(caps):
            original = getattr(caps, field.name)
            for value in (original + 1, bool(original), float(original), str(original)):
                with self.subTest(field=field.name, value=value), self.assertRaises(ProvenanceError):
                    validate_fake_diagnostic_negotiation(self.declaration, STR1_BUILD,
                                                        replace(caps, **{field.name: value}))

    def test_matching_collector_raw_crc_correlated_stop_unknown_end_and_owned_export(self):
        from motortester.stream_provenance import provenance_text
        run, factory = self.make_run()
        report = self.collect(run)
        final, metadata = json.loads(report.final), json.loads(report.metadata)
        peer = factory.backends[0].peer
        b, s = final["bridge"], final["bridge"]["session"]
        self.assertEqual(b["accepted_build_id"], STR1_BUILD)
        self.assertEqual(b["accepted_caps"], list(STR1_CAPS))
        self.assertEqual(metadata["selected_declaration"], asdict(self.declaration))
        self.assertEqual(metadata["origin"], "synthetic-fake")
        for field in ("firmware_image", "usb_identity", "telemetry"):
            self.assertIsNone(metadata[field])
        self.assertEqual(metadata["flash_status"], "NOT_FLASH_APPROVED")
        self.assertEqual(metadata["expected_negotiation"],
                         dict(build_id=STR1_BUILD, caps=list(STR1_CAPS)))
        notice = metadata["rehearsal_notice"]
        for marker in ("Synthetic rehearsal", "NOT_FLASH_APPROVED", "Completed-DMA",
                       "nominal Fs", "Unknown produced_end/loss", "do not authenticate"):
            self.assertIn(marker, notice)
        self.assertEqual(b["identity"]["origin"], "synthetic")
        self.assertEqual(b["identity"]["application_version"], STR1_CARRIER)
        self.assertEqual((b["source_received_end"], b["source_prefix_crc32"]),
                         (128, zlib.crc32(peer.raw)))
        self.assertIsNone(b["source_terminal"]["terminal"]["produced_end"])
        self.assertEqual(b["source_terminal"]["terminal"]["flags"], 3)
        self.assertEqual(b["source_terminal"]["terminal"]["reason"], 2)
        self.assertEqual(s["stop_acknowledgement"]["request_id"], peer.stop_request)
        self.assertEqual(s["source_end"]["terminal"]["stop_request_id"], peer.stop_request)
        self.assertIsNone(s["source_end"]["terminal"]["device_generated_end_sample_index"])
        self.assertIsNone(s["source_end"]["terminal"]["loss_samples"])
        self.assertFalse(s["clean_completion_reported"])
        self.assertEqual(s["first_failure"]["source_code"], 2)
        self.assertTrue(final["trace_complete"])
        self.assertTrue(final["controller_joined"])
        self.assertFalse(final["full_endpoint_event_trace"])
        self.assertEqual([decoded(r.offered)[0] for r in report.callbacks if r.kind == "write"], [1, 2, 8, 5])
        actual = recover_attempt(self.root / "sample.mtstream").recording
        self.assertEqual(actual.read_raw(0, 128), peer.raw)
        fresh = self.opened()
        self.assertEqual(fresh["identity"], b["identity"])
        self.assertEqual(fresh["profile"], asdict(self.declaration))
        self.assertEqual(fresh["raw_sha"], hashlib.sha256(peer.raw).hexdigest())
        self.assertFalse(fresh["clean"])
        self.assertIsNone(fresh["terminal"]["device_generated_end_sample_index"])
        self.assertIsNone(fresh["terminal"]["loss_samples"])
        self.assertEqual(fresh["terminal"]["failure"], s["first_failure"])
        visible = provenance_text(actual.identity)
        self.assertEqual(fresh["text"], visible)
        for marker in ("str1", STR1_SOURCE, STR1_ARTIFACT, "53545231", "NOT_FLASH_APPROVED",
                       "completed-dma-v1", "номинальн"):
            self.assertIn(marker, visible)
        self.assertIn("синтет", visible.lower())
        path, reopened = self.export(run)
        self.assertTrue(reopened.complete)
        self.assertEqual(reopened.summary, report.final)
        self.assertEqual(reopened.file_sha256, hashlib.sha256(path.read_bytes()).hexdigest())
        receipt = run.snapshot().export
        self.assertTrue(receipt.owner_settled and receipt.fsync_returned and
                        receipt.readback_verified and receipt.complete_receipt)
        proc = fresh_str1_read(path, report=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), dict(complete=True, summary=final))

    def test_rejected_report_keeps_expected_and_observed_negotiation_separate(self):
        def silent(backend):
            backend.hooks["read"] = lambda maximum: b""
        cases = [("unavailable", dict(configure=silent), None, None),
                 ("wrong-build", dict(build=STR1_BUILD + 1), STR1_BUILD + 1, list(STR1_CAPS)),
                 ("bad-caps", dict(caps=(2,) + STR1_CAPS[1:]), STR1_BUILD, None)]
        for name, fixture, build, caps in cases:
            with self.subTest(case=name):
                run, factory = self.make_run(data=False, **fixture)
                report = self.collect(run, name + ".mtstream")
                metadata, final = json.loads(report.metadata), json.loads(report.final)
                self.assertEqual(metadata["selected_declaration"], asdict(self.declaration))
                self.assertEqual(metadata["expected_negotiation"],
                                 dict(build_id=STR1_BUILD, caps=list(STR1_CAPS)))
                self.assertEqual(metadata["flash_status"], "NOT_FLASH_APPROVED")
                observed = final["bridge"]
                self.assertEqual(observed["accepted_build_id"], build)
                self.assertEqual(observed["accepted_caps"], caps)
                self.assertIsNone(observed["identity"])
                self.assertIsNone(observed["session"])
                self.assertIsNone(observed["storage"])
                self.assertFalse((self.root / (name + ".mtstream")).exists())
                self.assertFalse(any(command[0] == 8 for command in factory.backends[0].peer.commands))
                # Public as-of observations may be coalesced, but any observed
                # values must agree with acceptance, never expected constants.
                for raw_snapshot in report.snapshots:
                    snapshot = json.loads(raw_snapshot).get("bridge")
                    if snapshot is not None:
                        self.assertIn(snapshot["accepted_build_id"], (None, build))
                        self.assertIn(snapshot["accepted_caps"], (None, caps))
                path, reopened = self.export(run, name + ".jsonl")
                self.assertTrue(reopened.complete)
                self.assertEqual(json.loads(reopened.summary)["bridge"], observed)
                proc = fresh_str1_read(path, report=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(json.loads(proc.stdout)["summary"]["bridge"], observed)

    def test_unknown_end_empty_established_R_stays_incomplete(self):
        run, factory = self.make_run(data=False)
        report = self.collect(run)
        session = json.loads(report.final)["bridge"]["session"]
        self.assertEqual(session["selected_start_sample_index"], 0)
        self.assertEqual(session["finalized_end_sample_index"], 0)
        self.assertFalse(session["clean_completion_reported"])
        fresh = self.opened()
        self.assertTrue(fresh["recording"] and fresh["selected"])
        self.assertEqual((fresh["first"], fresh["end"]), (0, 0))
        self.assertEqual(fresh["raw_sha"], hashlib.sha256(b"").hexdigest())
        self.assertIsNone(fresh["terminal"]["device_generated_end_sample_index"])
        self.assertIsNone(fresh["terminal"]["loss_samples"])

    def test_no_R_and_nonzero_R_remain_distinct_with_unknown_end(self):
        for establish in (False, True):
            with self.subTest(establish=establish):
                c, factory, now = self.connected()
                self.assertTrue(c.preview())
                self.until(c, lambda s: s.phase == "preview")
                peer = factory.backends[0].peer
                peer.data(); self.until(c, lambda s: s.validated_samples == 128)
                path = self.root / ("nonzero.mtstream" if establish else "no-r.mtstream")
                self.assertTrue(c.record(str(path)))
                self.until(c, lambda s: s.bridge.session is not None and s.bridge.mailbox is not None,
                           disk=False)
                if establish:
                    self.until(c, lambda s: not s.worker_unresolved)
                    peer.data(); self.until(c, lambda s: s.validated_samples == 256 and
                                            s.bridge.session.accepted_end_sample_index == 256)
                self.assertTrue(c.stop())
                self.until(c, lambda s: s.bridge.session.finalized and not s.worker_unresolved)
                fresh = self.opened(path.name)
                self.assertEqual(fresh["selected"], establish)
                self.assertEqual(fresh["recording"], establish)
                self.assertIsNone(fresh["terminal"]["device_generated_end_sample_index"])
                self.assertIsNone(fresh["terminal"]["loss_samples"])
                self.assertEqual(fresh["identity"]["application_version"], STR1_CARRIER)
                self.assertEqual(c.snapshot().bridge.source_prefix_crc32, zlib.crc32(peer.raw))
                if establish:
                    self.assertEqual((fresh["first"], fresh["end"]), (128, 256))
                    self.assertEqual(fresh["raw_sha"], hashlib.sha256(peer.raw[256:]).hexdigest())
                    self.assertFalse(fresh["clean"])
                else:
                    self.assertIsNone(fresh["first"]); self.assertIsNone(fresh["end"])
                    self.assertIsNone(fresh["raw_sha"])

    def test_exact_identity_mutations_rehashed_headers_and_fresh_reader_reject(self):
        from motortester.stream_model import StreamError, StreamIdentity
        from motortester.stream_provenance import profile_for_identity, rehearsal_identity_metadata
        identity = StreamIdentity("exact-rehearsal", 3, 1, 50000, "synthetic",
                                  *rehearsal_identity_metadata(self.declaration))
        self.assertEqual(profile_for_identity(identity), self.declaration)
        changes = [("protocol_revision", None), ("protocol_revision", "0.2-draft-081721"),
                   ("protocol_revision", "0.2-draft-dma-sequence-20261019"),
                   ("protocol_revision", identity.protocol_revision.replace("completed-dma-v1", "completed-dma-v2")),
                   ("firmware_version", None), ("firmware_version", "0" * 64),
                   ("application_version", None), ("application_version", "rehearsal:unknown"),
                   ("application_version", STR1_CARRIER.replace("str1", "str2")),
                   ("application_version", STR1_CARRIER.replace(STR1_SOURCE, "0" * 40)),
                   ("application_version", STR1_CARRIER[:-8] + "53545232"),
                   ("application_version", self.declaration.identity_metadata[2]),
                   ("origin", "device"), ("config_id", 2), ("config_id", True),
                   ("nominal_fs_hz", 49999), ("nominal_fs_hz", 50000.0)]
        run, _ = self.make_run(data=False)
        self.collect(run)
        raw = (self.root / "sample.mtstream").read_bytes()
        magic, length = struct.unpack("<8sI", raw[:12])
        original = json.loads(raw[12:12 + length])
        for index, (field, value) in enumerate(changes):
            with self.subTest(field=field, value=value):
                with self.assertRaises(StreamError):
                    replace(identity, **{field: value})
                altered = json.loads(json.dumps(original))
                altered["preparation"]["identity"][field] = value
                metadata = json.dumps(altered, separators=(",", ":")).encode()
                prefix = struct.pack("<8sI", magic, len(metadata))
                path = self.root / ("mutated-%02d.mtstream" % index)
                path.write_bytes(prefix + metadata + hashlib.sha256(prefix + metadata).digest())
                with self.assertRaises(StreamError):
                    recover_attempt(path)
                proc = fresh_str1_read(path)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("StreamError", proc.stderr)
        # An unmarked old revision does not make a malformed new carrier generic.
        with self.assertRaises(StreamError):
            StreamIdentity("mixed", 3, 1, 50000, "synthetic", "legacy", "old", "rehearsal:unknown")

    def test_legacy_generic_and_syn1_journals_keep_original_fresh_meaning(self):
        from motortester.stream_model import StreamIdentity, StreamPreparation
        from motortester.stream_provenance import SYNTHETIC_DMA_PROFILE
        from motortester.stream_recording import PreparedStreamWriter
        identities = [StreamIdentity("legacy", 3, 1, 50000, "synthetic",
                                     "0.2-draft-081721", "build:00001234", "isolated-stream-bridge"),
                      StreamIdentity("syn1", 3, 1, 50000, "synthetic", *SYNTHETIC_DMA_PROFILE.identity_metadata)]
        for identity in identities:
            path = self.root / (identity.session_id + ".mtstream")
            writer = PreparedStreamWriter.prepare(path, StreamPreparation(identity, "old-reader-contract", "source_start"))
            writer.close()
            fresh = self.opened(path.name)
            self.assertEqual(fresh["identity"], asdict(identity))
            self.assertEqual(fresh["profile"], None if identity.session_id == "legacy" else asdict(SYNTHETIC_DMA_PROFILE))
            self.assertNotIn("rehearsal:str1", fresh["text"])

    def test_admission_timeout_and_transport_failure_never_start_or_prepare(self):
        for kind in ("timeout", "transport"):
            with self.subTest(kind=kind):
                def configure(backend):
                    if kind == "timeout":
                        backend.hooks["read"] = lambda maximum: b""
                    else:
                        backend.hooks["read"] = lambda maximum: (_ for _ in ()).throw(OSError("fake admission transport"))
                c, factory, now = self.connected(configure=configure)
                path = self.root / (kind + ".mtstream")
                with patch("motortester.stream_endpoint.Endpoint.plan_start") as plan, \
                     patch("motortester.stream_bridge.StreamSession") as session, \
                     patch("motortester.stream_bridge.StorageExecutor") as storage:
                    self.assertTrue(c.record(str(path))); self.step(c)
                    if kind == "timeout":
                        now[0] = 2000
                    self.until(c, lambda s: s.bridge.endpoint_first_fault is not None)
                    self.assert_no_admission(c, factory, path)
                    plan.assert_not_called(); session.assert_not_called(); storage.assert_not_called()
                b = c.snapshot().bridge
                self.assertEqual(b.endpoint_first_fault.code, "request_timeout" if kind == "timeout" else "transport")
                self.assertIsNone(b.accepted_build_id); self.assertIsNone(b.accepted_caps)
                self.assertFalse(c.preview()); self.assertFalse(c.record(str(path)))

    def test_observed_negotiation_unavailable_partial_refused_and_fresh_attempt(self):
        from motortester.stream_bridge import StreamBridge
        from motortester.stream_endpoint import WriteOffer
        bridge = StreamBridge("observation-only", fake_diagnostic_declaration=self.declaration)
        self.addCleanup(lambda: bridge.close(0))
        factory = str1_factory()
        backend = factory(port=None)
        peer = backend.peer
        self.assertIsNone(bridge.snapshot().accepted_build_id)
        self.assertIsNone(bridge.snapshot().accepted_caps)
        bridge.hello(91, 0)
        offer, = [e for e in bridge.next_write(0) if isinstance(e, WriteOffer)]
        bridge.observe_write(offer.token, peer.write(offer.data), 0)
        bridge.receive(peer.read(544), 0)
        self.assertEqual(bridge.snapshot().accepted_build_id, STR1_BUILD)
        self.assertIsNone(bridge.snapshot().accepted_caps)
        bridge.get_caps(0)
        offer, = [e for e in bridge.next_write(0) if isinstance(e, WriteOffer)]
        bridge.observe_write(offer.token, peer.write(offer.data), 0)
        bridge.receive(peer.read(544), 0)
        accepted = bridge.snapshot()
        self.assertEqual(accepted.accepted_caps, STR1_CAPS)
        bridge.hello(92, 0)  # Refused by the existing endpoint; no reset was admitted.
        self.assertEqual(bridge.snapshot().accepted_build_id, accepted.accepted_build_id)
        self.assertEqual(bridge.snapshot().accepted_caps, accepted.accepted_caps)
        # A clean synthetic terminal is used ONLY to exercise existing restart.
        # It is not substituted for the declared image's unknown-END fixture.
        c, factory, now = self.connected(unknown_end=False)
        c.preview(); self.until(c, lambda s: s.phase == "preview")
        old = c.snapshot().bridge
        c.stop(); self.until(c, lambda s: s.phase == "preview stopped")
        self.assertEqual(c.snapshot().bridge.accepted_build_id, STR1_BUILD)
        backend = factory.backends[0]
        backend.hooks["read"] = lambda maximum: b""
        self.assertTrue(c.preview()); self.step(c)
        fresh = c.snapshot().bridge
        self.assertIsNone(fresh.identity)
        self.assertIsNone(fresh.accepted_build_id); self.assertIsNone(fresh.accepted_caps)
        self.assertNotEqual(old.identity.session_id, "wire-0.2:" + format(backend.peer.nonce, "016x"))
        backend.hooks.pop("read")
        self.until(c, lambda s: s.phase == "preview" and s.revision == 2)
        self.assertEqual(c.snapshot().bridge.accepted_caps, STR1_CAPS)
        self.assertNotEqual(c.snapshot().bridge.identity, old.identity)

    def test_stale_nonce_cannot_fill_observations_and_stale_generation_cannot_admit_data(self):
        from test_stream_controller import encoded, waveform
        c, factory, now = self.connected()
        backend = factory.backends[0]
        held = []
        original_read = backend.peer.read
        def read(maximum):
            packet = original_read(maximum)
            if packet:
                held.append(packet)
            return b""
        backend.hooks["read"] = read
        c.record(str(self.root / "stale.mtstream")); self.step(c)
        for _ in range(3):
            self.step(c)
        peer = backend.peer
        self.assertTrue(held)
        backend.hooks.pop("read")
        stale = encoded(129, struct.pack("<BBBBHHII", 0, 2, 1, 12, 512, 128, STR1_BUILD, 1),
                        peer.nonce ^ 1, 1, 1)
        peer.enqueue(stale); self.step(c)
        self.assertIsNone(c.snapshot().bridge.accepted_build_id)
        self.assertIsNone(c.snapshot().bridge.accepted_caps)
        self.assertIsNone(c.snapshot().bridge.identity)
        peer.enqueue(b"".join(held))
        self.until(c, lambda s: s.phase == "recording" and s.bridge.session.accepted_end_sample_index == 0)
        accepted = c.snapshot().bridge
        peer.enqueue(encoded(152, struct.pack("<IIQHH", peer.generation + 1, 1, 0, 128, 0) + waveform(),
                             peer.nonce, peer.sequence))
        self.until(c, lambda s: s.bridge.endpoint_first_fault is not None)
        status = c.snapshot()
        self.assertEqual(status.bridge.endpoint_first_fault.code, "generation")
        self.assertEqual(status.validated_samples, 0)
        self.assertEqual(status.bridge.source_received_end, 0)
        self.assertEqual(status.bridge.accepted_build_id, accepted.accepted_build_id)
        self.assertEqual(status.bridge.accepted_caps, accepted.accepted_caps)
        self.assertFalse(c.preview())
        now[0] = 4001
        self.until(c, lambda s: s.bridge.session.finalized and not s.worker_unresolved)


if __name__ == "__main__":
    unittest.main()

"""Actual runtime/byte-peer and bounded writer integration (no hardware claim)."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import os
import json
import shutil
import textwrap
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import UUID

from motortester.device_emulator import DeviceEmulator
from motortester.model import Capture
from motortester.recording import encode
from motortester.recording_bridge import RecordingSessionAdapter, _Completion
from motortester.recording_controller import IntentKind, RecordingPhase
from motortester.runtime import AcquisitionRuntime
from motortester.session import Config, EventKind, Phase
from motortester import session_archive as archive
from motortester.wire import StreamDecoder


class Link:
    def __init__(self):
        self.now = 0
        self.peer = DeviceEmulator(all_analog_modes=True)
        self.read_limit = self.write_limit = 4096
        self.fail_write = self.fail_read = self.hold_read = False
        self.tx, self.rx, self.threads = [], [], []
        self.decoder, self.incoming = StreamDecoder(), StreamDecoder()

    def write(self, data):
        self.threads.append(threading.get_ident())
        if self.fail_write:
            raise OSError("private write detail")
        data = data[:self.write_limit]
        count = self.peer.write(data, self.now).accepted_prefix
        self.tx.extend(self.decoder.feed(data[:count], self.now).frames)
        return count

    def read(self, size):
        self.threads.append(threading.get_ident())
        if self.fail_read:
            raise OSError("private read detail")
        if self.hold_read:
            return b""
        data = self.peer.read(min(size, self.read_limit), self.now)
        self.rx.extend(self.incoming.feed(data, self.now).frames)
        return data


class Harness:
    def __init__(self, case, mode=1, **options):
        self.case = case
        self.temp = tempfile.TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        self.link = Link()
        self.runtime = AcquisitionRuntime(self.link, origin="simulator",
            clock_ms=lambda: self.link.now, utc_now=lambda: "2026-10-12T20:00:00Z")
        self.runtime.start(101)
        for _ in range(20):
            self.runtime.pump()
            if self.runtime.snapshot().phase is Phase.READY:
                break
        case.assertIs(self.runtime.snapshot().phase, Phase.READY)
        self.adapter = RecordingSessionAdapter(self.runtime, clock_ms=lambda: self.link.now, **options)
        case.addCleanup(self.cleanup)
        s = self.runtime.snapshot()
        self.identity = archive.SessionIdentity(str(UUID(int=202)), str(UUID(int=s.session)),
            "simulator", str(s.capabilities.firmware_build), f"draft-0.1@{s.source_sha}", "0.1.0")
        self.settings = Config(mode=mode, auto_timeout_ms=100 if mode == 0 else 0)
        self.path = Path(self.temp.name) / "capture.spool"
        self.events = []

    def cleanup(self):
        if self.runtime.snapshot().phase is not Phase.RECOVERY:
            self.adapter.observe(self.runtime.disconnect())
        self.adapter.retire()
        self.adapter._writer._thread.join(2)  # Test cleanup only, never production/UI.
        self.case.assertFalse(self.adapter._writer.running)

    def begin(self):
        self.case.assertTrue(self.adapter.record(self.identity, self.settings, self.path))

    def pump(self):
        events = self.runtime.pump()
        self.events.extend(events)
        self.adapter.observe(events)

    def until(self, condition, count=20000):
        for _ in range(count):
            if condition():
                return
            self.pump()
            if self.adapter.state.writer_busy:
                time.sleep(.0005)
        self.case.fail(("did not settle", self.adapter.state, self.runtime.snapshot()))

    def start(self):
        self.begin()
        self.until(lambda: self.runtime.snapshot().phase is Phase.ARMED)

    def capture(self, *, timeout=False):
        if timeout:
            self.link.now += 101
        else:
            self.case.assertTrue(self.link.peer.trigger(self.link.now))
        self.until(lambda: self.runtime.pending_capture is not None)
        return self.runtime.pending_capture

    def finish(self):
        self.adapter.stop()
        self.until(lambda: self.adapter.state.ready_to_save)
        self.case.assertNotIn(7, [f.message_type for f in self.link.tx])
        return archive.recover(self.path)

    def save(self, name="capture.mtrec"):
        path = Path(self.temp.name) / name
        self.case.assertTrue(self.adapter.save(path))
        self.until(lambda: not self.adapter.state.saving)
        self.case.assertEqual(self.adapter.state.saved_path, str(path))
        return archive.load(path)


class RecordingAdapterTests(unittest.TestCase):
    def test_modes_repeat_five_windows_single_one_and_wire_provenance(self):
        for mode in (0, 1, 2):
            with self.subTest(mode=mode):
                h = Harness(self, mode)
                h.start()
                sources, generations = [], []
                for index in range(1 if mode == 2 else 5):
                    generations.append(h.runtime.snapshot().generation)
                    source = h.capture(timeout=mode == 0 and index % 2 == 0)
                    sources.append(source)
                    h.until(lambda: h.adapter.controller.snapshot().committed_segments == index + 1
                            and h.runtime.pending_capture is None)
                    if mode != 2:
                        h.until(lambda: h.runtime.snapshot().phase is Phase.ARMED)
                h.finish()
                opened = h.save()
                self.assertEqual(len(opened.entries), len(sources))
                self.assertEqual(len(set(generations)), len(generations))
                for entry, source, generation in zip(opened.entries, sources, generations):
                    self.assertEqual(opened.read_segment(entry.ordinal), encode(source))
                    p = entry.provenance
                    self.assertEqual(p.generation, generation)
                    self.assertEqual(p.wire_session_nonce, 101)
                    self.assertEqual(p.config_payload_hex, h.settings.payload().hex())
                    self.assertEqual((p.begin_request_id, p.terminal_request_id), (0, 0))
                    self.assertIsNone(p.unavailable_reason)
                    begin = next(f for f in h.link.rx if f.sequence == p.begin_sequence)
                    terminal = next(f for f in h.link.rx if f.sequence == p.terminal_sequence)
                    self.assertEqual((begin.message_type, terminal.message_type), (144, 146))
                    self.assertEqual(p.end_samples_crc32, int.from_bytes(terminal.payload[12:], "little"))
                self.assertEqual(h.adapter.state.snapshot.coverage, "segmented")
                self.assertEqual(h.adapter.state.snapshot.intersegment_device_gap, "unknown")
                self.assertEqual(set(h.link.threads), {threading.get_ident()})

    def test_prepare_exclusive_before_config_and_stop_nonblocking(self):
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        def factory(path, identity):
            entered.set()
            gate.wait(3)
            return archive.SessionSpool.create(path, identity)
        h = Harness(self, spool_factory=factory)
        h.begin()
        self.assertTrue(entered.wait(1))
        before = time.monotonic()
        h.adapter.stop()
        for _ in range(20):
            h.pump()
        self.assertLess(time.monotonic() - before, .2)
        self.assertEqual([f.message_type for f in h.link.tx], [1, 2])
        gate.set()
        h.until(lambda: h.adapter.state.ready_to_save)
        self.assertEqual(h.finish().entries, ())
        self.assertEqual(h.adapter.state.snapshot.termination_reason, "user_stop")

    def test_existing_spool_failure_never_configures(self):
        h = Harness(self)
        h.path.write_bytes(b"keep existing")
        h.begin()
        h.until(lambda: h.adapter.state.error is not None)
        self.assertEqual(h.path.read_bytes(), b"keep existing")
        self.assertEqual([f.message_type for f in h.link.tx], [1, 2])
        self.assertFalse(h.adapter.state.ready_to_save)

    def test_blocked_member_fsync_stop_transport_pumps_exact_ack_only_after_receipt(self):
        h = Harness(self)
        h.start()
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        original = archive._sync
        disk_threads = []
        def slow(stream):
            disk_threads.append(threading.get_ident())
            entered.set()
            gate.wait(3)
            return original(stream)
        with patch.object(archive, "_sync", slow):
            source = h.capture()
            h.until(entered.is_set)
            generation = h.runtime.snapshot().generation
            self.assertEqual(h.adapter.state.snapshot.stop_evidence, "stop_ack")
            self.assertIs(h.runtime.pending_capture, source)
            self.assertIs(h.adapter.state.snapshot.pending[0].result.source, source)
            start = time.monotonic()
            h.adapter.stop()
            for _ in range(50):
                h.pump()
            self.assertLess(time.monotonic() - start, .2)
            self.assertIs(h.runtime.pending_capture, source)
            self.assertEqual(h.runtime.snapshot().generation, generation)
            gate.set()
            h.until(lambda: h.adapter.state.ready_to_save)
        self.assertEqual(set(disk_threads), {h.adapter._writer._thread.ident})
        self.assertNotEqual(disk_threads[0], threading.get_ident())
        self.assertIsNone(h.runtime.pending_capture)
        self.assertNotIn(7, [f.message_type for f in h.link.tx])
        self.assertEqual(h.save().read_segment(0), encode(source))

    def test_partial_no_begin_and_full_stop_wire_boundaries(self):
        for boundary in ("waiting", "allocated", "begin", "data", "before_end", "after_end_byte", "end"):
            with self.subTest(boundary=boundary):
                h = Harness(self)
                h.start()
                h.link.read_limit = 1
                if boundary != "waiting":
                    self.assertTrue(h.link.peer.trigger(0))
                if boundary in ("begin", "data"):
                    kind = EventKind.WINDOW_BEGIN if boundary == "begin" else EventKind.WINDOW_DATA
                    h.until(lambda: any(e.kind is kind for e in h.events))
                elif boundary in ("before_end", "after_end_byte"):
                    h.until(lambda: h.link.peer.snapshot().data_offset == 2048
                            and h.link.peer.snapshot().tx_offset is None)
                    if boundary == "after_end_byte":
                        h.pump()
                elif boundary == "end":
                    h.until(lambda: h.runtime.pending_capture is not None)
                report = h.finish()
                kinds = [entry.kind for entry in report.entries]
                expected = ([] if boundary == "waiting" else ["abort"] if boundary == "allocated"
                            else ["partial"] if boundary in ("begin", "data", "before_end") else ["capture"])
                self.assertEqual(kinds, expected)
                if kinds and kinds[0] != "capture":
                    self.assertEqual(report.entries[0].provenance.abort_reason_code, 1)
                self.assertEqual(report.outcome.stop_evidence, "stop_ack")

    def test_runtime_provenance_exact_immutable_and_missing_terminal_not_invented(self):
        h = Harness(self)
        h.start()
        h.link.read_limit = 1
        self.assertTrue(h.link.peer.trigger(0))
        h.until(lambda: any(e.kind is EventKind.WINDOW_DATA for e in h.events))
        h.adapter.observe(h.runtime.disconnect())
        source = h.runtime.incomplete_result
        facts = h.runtime.result_provenance(source)
        self.assertIsNone(facts.terminal_frame)
        self.assertEqual(facts.begin_frame.message_type, 144)
        self.assertIsNone(h.runtime.result_provenance(replace(source)))
        with self.assertRaises(FrozenInstanceError):
            facts.config_id = 99
        h.until(lambda: h.adapter.state.ready_to_save)
        report = archive.recover(h.path)
        self.assertEqual(report.outcome.stop_evidence, "unknown")
        self.assertEqual(report.entries[0].provenance.unavailable_reason, "no_validated_terminal")
        self.assertIsNone(h.runtime.result_provenance(source))

    def test_disk_failure_preserves_capture_no_ack_no_rearm(self):
        h = Harness(self)
        h.start()
        with patch.object(archive, "_sync", side_effect=OSError("private full disk detail")):
            source = h.capture()
            h.until(lambda: h.adapter.state.error is not None)
        self.assertIs(h.runtime.pending_capture, source)
        self.assertIs(h.adapter.state.snapshot.pending[0].result.source, source)
        self.assertFalse(h.adapter.state.ready_to_save)
        self.assertEqual(h.adapter.state.error, "persist_failed")
        before = [f.message_type for f in h.link.tx]
        for _ in range(20):
            h.pump()
        self.assertEqual([f.message_type for f in h.link.tx], before)
        self.assertNotIn(7, before)

    def test_stale_writer_callback_does_not_consume_current_job(self):
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        def factory(path, identity):
            entered.set(); gate.wait(3)
            return archive.SessionSpool.create(path, identity)
        h = Harness(self, spool_factory=factory)
        h.begin()
        self.assertTrue(entered.wait(1))
        job = h.adapter._job
        self.assertFalse(h.adapter._complete(_Completion(replace(job), error="prepare_failed"), 0))
        self.assertIs(h.adapter._job, job)
        gate.set()
        h.finish()

    def test_claimed_finish_recovery_invalidates_publication(self):
        h = Harness(self, mode=2)
        h.start()
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        original = archive.SessionSpool.finish
        def blocked(spool, *args):
            report = original(spool, *args)
            entered.set(); gate.wait(3)
            return report
        with patch.object(archive.SessionSpool, "finish", blocked):
            h.capture()
            h.until(entered.is_set)
            h.adapter.observe(h.runtime.disconnect())
            self.assertTrue(h.adapter.state.snapshot.finalization_invalidated)
            gate.set()
            h.until(lambda: not h.adapter.state.writer_busy)
        self.assertFalse(h.adapter.state.ready_to_save)
        self.assertFalse(h.adapter.save(Path(h.temp.name) / "invalid.mtrec"))
        self.assertEqual(h.adapter.state.error, "finalization_unresolved")
        self.assertEqual(len(archive.recover(h.path).entries), 1)




    def test_stop_command_submission_and_partial_write_boundaries(self):
        for kind in (3, 4):
            for boundary in ("queued", "prefix", "written_before_ack"):
                with self.subTest(kind=kind, boundary=boundary):
                    h = Harness(self)
                    h.begin()
                    request = 3 if kind == 3 else 4
                    h.until(lambda: h.runtime.snapshot().pending_request_id == request)
                    if boundary == "prefix":
                        h.link.write_limit = 1
                        h.pump()
                        self.assertEqual(h.runtime.snapshot().tx_offset, 1)
                    elif boundary == "written_before_ack":
                        h.link.hold_read = True
                        h.pump()
                        self.assertIsNone(h.runtime.snapshot().tx_offset)
                        h.link.hold_read = False
                    report = h.finish()
                    self.assertEqual(report.entries, ())
                    self.assertEqual(report.outcome.reason, "user_stop")
                    types = [f.message_type for f in h.link.tx]
                    self.assertEqual(types.count(4), int(kind == 4))
                    self.assertNotIn(7, types)

    def test_missing_stop_ack_times_out_unknown_but_durable_capture_retained(self):
        h = Harness(self)
        h.start()
        source = h.capture()
        h.adapter.stop()
        h.link.hold_read = True
        h.pump()  # STOP reaches device; its ACK is never read.
        h.link.now += 1001
        h.pump()
        self.assertIs(h.runtime.snapshot().phase, Phase.RECOVERY)
        h.until(lambda: h.adapter.state.ready_to_save)
        report = archive.recover(h.path)
        self.assertEqual(report.outcome.stop_evidence, "unknown")
        self.assertEqual(report.outcome.reason, "error")
        self.assertEqual(h.save().read_segment(0), encode(source))
        self.assertNotIn(7, [f.message_type for f in h.link.tx])

    def test_unknown_partial_write_does_not_retry_or_fabricate_trigger_data(self):
        h = Harness(self)
        h.begin()
        h.until(lambda: h.runtime.snapshot().pending_request_id == 3)
        h.link.write_limit = 1
        h.pump()
        h.link.fail_write = True
        h.pump()
        self.assertIs(h.runtime.snapshot().phase, Phase.RECOVERY)
        h.until(lambda: h.adapter.state.ready_to_save)
        report = archive.recover(h.path)
        self.assertEqual(report.entries, ())
        self.assertEqual(report.outcome.stop_evidence, "unknown")
        before = tuple(h.link.tx)
        for _ in range(10):
            h.pump()
        self.assertEqual(tuple(h.link.tx), before)

    def test_archive_capacity_reserve_and_host_guard_stop_without_new_arm(self):
        with patch.object(archive, "MAX_SEGMENTS", 3):
            h = Harness(self)
            h.start()
            for count in (1, 2):
                h.capture()
                h.until(lambda: h.adapter.state.snapshot.committed_segments == count
                        and h.runtime.pending_capture is None)
                if count == 1:
                    h.until(lambda: h.runtime.snapshot().phase is Phase.ARMED)
            h.until(lambda: h.adapter.state.ready_to_save)
            self.assertEqual(h.adapter.state.snapshot.termination_reason, "capacity")
            self.assertEqual([f.message_type for f in h.link.tx].count(4), 2)
            self.assertEqual(len(h.save().entries), 2)
        h2 = Harness(self, max_recording_ms=100)
        h2.start()
        h2.link.now = 100
        h2.pump()
        h2.until(lambda: h2.adapter.state.ready_to_save)
        self.assertEqual(h2.adapter.state.snapshot.termination_reason, "capacity")
        self.assertEqual(h2.adapter.state.snapshot.fault, "elapsed_time_limit")
        self.assertEqual(archive.recover(h2.path).entries, ())

    def test_save_cancel_failure_and_explicit_retry_preserve_spool(self):
        h = Harness(self, mode=2)
        h.start(); source = h.capture(); h.finish()
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        original = archive._sync
        def blocked(stream):
            entered.set(); gate.wait(3)
            return original(stream)
        destination = Path(h.temp.name) / "cancelled.mtrec"
        with patch.object(archive, "_sync", blocked):
            self.assertTrue(h.adapter.save(destination))
            self.assertTrue(entered.wait(1))
            self.assertTrue(h.adapter.cancel_save())
            gate.set()
            h.until(lambda: not h.adapter.state.saving)
        self.assertFalse(destination.exists())
        self.assertEqual(h.adapter.state.error, "save_cancelled")
        self.assertTrue(h.adapter.state.ready_to_save)
        destination.write_bytes(b"existing destination")
        self.assertTrue(h.adapter.save(destination))
        h.until(lambda: not h.adapter.state.saving)
        self.assertEqual(h.adapter.state.error, "save_failed")
        self.assertEqual(destination.read_bytes(), b"existing destination")
        self.assertEqual(h.save("retry.mtrec").read_segment(0), encode(source))

    def test_record_rejects_owned_result_and_reuses_single_writer_only_after_save(self):
        h = Harness(self, mode=2)
        h.start(); h.capture()
        thread = h.adapter._writer._thread
        next_identity = replace(h.identity, recording_id=str(UUID(int=303)))
        self.assertFalse(h.adapter.record(next_identity, h.settings, Path(h.temp.name) / "next.spool"))
        h.finish()
        self.assertFalse(h.adapter.record(next_identity, h.settings, Path(h.temp.name) / "next.spool"))
        h.save()
        self.assertTrue(h.adapter.record(next_identity, h.settings, Path(h.temp.name) / "next.spool"))
        self.assertIs(h.adapter._writer._thread, thread)
        h.until(lambda: h.runtime.snapshot().phase is Phase.ARMED)
        h.adapter.stop()
        h.until(lambda: h.adapter.state.ready_to_save)
        self.assertEqual(h.adapter.state.snapshot.committed_segments, 0)


    def test_bad_terminal_report_cannot_pass_publication_gate(self):
        h = Harness(self, mode=2)
        h.start()
        original = archive.SessionSpool.finish
        def wrong(spool, *args):
            report = original(spool, *args)
            return replace(report, outcome=archive.SessionOutcome("user_stop", "unknown"))
        with patch.object(archive.SessionSpool, "finish", wrong):
            h.capture()
            h.until(lambda: h.adapter.state.error is not None)
        self.assertEqual(h.adapter.state.error, "invalid_finalization_report")
        self.assertFalse(h.adapter.state.ready_to_save)
        self.assertFalse(h.adapter.save(Path(h.temp.name) / "forbidden.mtrec"))
        self.assertEqual(len(archive.recover(h.path).entries), 1)

    def test_runtime_full_provenance_lifetime_follows_ordinary_release(self):
        link = Link()
        runtime = AcquisitionRuntime(link, origin="simulator", clock_ms=lambda: link.now)
        runtime.start(731)
        for _ in range(20): runtime.pump()
        config = Config(mode=2)
        runtime.configure(config)
        for _ in range(20): runtime.pump()
        runtime.arm()
        for _ in range(20): runtime.pump()
        self.assertTrue(link.peer.trigger(0))
        for _ in range(100):
            runtime.pump()
            if runtime.pending_capture is not None: break
        source = runtime.pending_capture
        self.assertIsNotNone(source)
        facts = runtime.result_provenance(source)
        self.assertEqual(facts.config, config)
        self.assertEqual(facts.terminal_frame.message_type, 146)
        self.assertIsNone(runtime.result_provenance(replace(source)))
        runtime.acknowledge(source)
        self.assertIs(runtime.result_provenance(source), facts)
        for _ in range(20): runtime.pump()
        self.assertIsNone(runtime.pending_capture)
        self.assertIsNone(runtime.result_provenance(source))


    def test_wrong_spool_receipt_cannot_ack_even_identical_item_identity(self):
        h = Harness(self)
        h.start()
        entered, gate = threading.Event(), threading.Event()
        self.addCleanup(gate.set)
        original = archive.SessionSpool.append_full
        def blocked(spool, full):
            if spool.path == h.path:
                entered.set(); gate.wait(3)
            return original(spool, full)
        with patch.object(archive.SessionSpool, "append_full", blocked):
            source = h.capture()
            h.until(entered.is_set)
            job = h.adapter._job
            other = archive.SessionSpool.create(Path(h.temp.name) / "wrong.spool", h.identity)
            try:
                receipt = original(other, job.payload)
            finally:
                other.close()
            self.assertFalse(h.adapter._complete(_Completion(job, receipt), h.link.now))
            self.assertIs(h.runtime.pending_capture, source)
            self.assertEqual(h.adapter.state.snapshot.fault, "invalid_commit_receipt")
            gate.set()
            h.until(lambda: not h.adapter.state.writer_busy)
        self.assertIs(h.runtime.pending_capture, source)
        self.assertFalse(h.adapter.state.ready_to_save)
        self.assertNotIn(7, [f.message_type for f in h.link.tx])

    def test_unrepresentable_source_stays_owned_and_blocks_finalization(self):
        h = Harness(self)
        h.start()
        original = h.runtime.result_provenance
        with patch.object(h.runtime, "result_provenance", return_value=None):
            source = h.capture()
            h.adapter.stop()
            h.until(lambda: h.runtime.snapshot().phase is Phase.READY)
            self.assertTrue(h.adapter.state.snapshot.source_retention_required)
            self.assertIs(h.runtime.pending_capture, source)
            self.assertFalse(h.adapter.state.ready_to_save)
        self.assertIsNotNone(original(source))
        # Restoration of an accessor is not an implicit retry or erased fault.
        h.pump()
        self.assertTrue(h.adapter.state.snapshot.source_retention_required)
        self.assertFalse(h.adapter.state.ready_to_save)


    def test_failed_end_crc_preserves_all_raw_fragments_as_partial_with_wire_crc(self):
        h = Harness(self)
        h.start()
        descriptor = DeviceEmulator._descriptor
        changed = []
        def corrupt(peer, kind, payload, request=0, completion="", count=0):
            if peer is h.link.peer and kind == 146:
                changed.append(True)
                payload = payload[:12] + struct.pack("<I", int.from_bytes(payload[12:], "little") ^ 1)
            return descriptor(peer, kind, payload, request, completion, count)
        with patch.object(DeviceEmulator, "_descriptor", corrupt):
            self.assertTrue(h.link.peer.trigger(0))
            h.until(lambda: h.runtime.incomplete_result is not None)
            source = h.runtime.incomplete_result
            self.assertEqual(source.reason.value, "CRC")
            h.until(lambda: h.adapter.state.ready_to_save)
        self.assertEqual(changed, [True])
        opened = h.save()
        entry = opened.entries[0]
        self.assertEqual(entry.kind, "partial")
        self.assertEqual(entry.provenance.terminal_kind, "end")
        self.assertIsNone(source.samples_crc32)  # Receiver does not endorse the failed CRC.
        terminal = next(frame for frame in h.link.rx if frame.message_type == 146)
        self.assertEqual(entry.provenance.end_samples_crc32, int.from_bytes(terminal.payload[12:], "little"))
        self.assertEqual(entry.partial.reason, "CRC")
        samples = tuple(code for fragment in source.fragments for code in fragment.samples)
        self.assertEqual(len(samples), 2048)
        self.assertEqual(opened.read_segment(0), struct.pack("<2048H", *samples))
        self.assertEqual(opened.outcome.stop_evidence, "unknown")


try:
    from PySide6 import QtCore, QtWidgets
    from motortester.acquisition import AcquisitionBridge, _AcquisitionWorker, _Mailbox
    from qt_test_support import pump_events
    QT_AVAILABLE = True
except ModuleNotFoundError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "Qt dependencies unavailable")
class QtRecordingBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridges = []
        self.gates = []

    def wait(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            pump_events(5)
        self.assertTrue(condition(), "Qt bridge did not settle")

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for bridge in self.bridges:
            adapter = bridge._worker.recording if bridge._worker else None
            if not bridge._closed:
                bridge.shutdown()
            self.wait(lambda: not bridge.running and bridge._closed)
            if adapter:
                self.wait(lambda: not adapter._writer.running)
            bridge.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def make(self, **kwargs):
        bridge = AcquisitionBridge(interval_ms=1, edge_delay_ms=5, **kwargs)
        self.bridges.append(bridge)
        bridge.captures, bridge.saved, bridge.errors = [], [], []
        bridge.capture_ready.connect(bridge.captures.append)
        bridge.recording_saved.connect(bridge.saved.append)
        bridge.error.connect(bridge.errors.append)
        self.assertTrue(bridge.connect_source("emulator"))
        self.wait(lambda: bridge.state.snapshot is not None
                  and bridge.state.snapshot.phase is Phase.READY and not bridge.state.busy)
        return bridge

    def test_qt_repeated_recording_display_ack_rejected_and_async_save(self):
        bridge = self.make()
        self.assertTrue(bridge.record(Config(), str(Path(self.temp.name) / "live.spool")))
        self.wait(lambda: bridge.state.recording is not None
                  and bridge.state.recording.snapshot.committed_segments >= 4)
        self.assertGreaterEqual(len(bridge.captures), 4)
        self.assertFalse(bridge.acknowledge(bridge.captures[-1]))
        self.assertFalse(bridge.start(Config()))
        self.assertTrue(bridge.stop())
        self.wait(lambda: bridge.state.recording.ready_to_save)
        count = bridge.state.recording.snapshot.committed_segments
        self.assertGreaterEqual(count, 4)
        destination = str(Path(self.temp.name) / "live.mtrec")
        self.assertTrue(bridge.save_recording(destination))
        self.wait(lambda: bridge.saved == [destination])
        self.assertEqual(len(archive.load(destination).entries), count)
        # An explicit later ordinary start restores its original consumer ACK path.
        self.assertTrue(bridge.start(Config(mode=2)))
        self.wait(lambda: bridge.pending_capture is not None)
        self.assertIsNone(bridge.state.recording)
        self.assertTrue(bridge.acknowledge(bridge.pending_capture))

    def test_qt_stop_during_internal_barrier_is_permanent_owner_fence(self):
        bridge = self.make()
        self.assertTrue(bridge.record(Config(), str(Path(self.temp.name) / "stop.spool")))
        self.wait(lambda: bridge.state.recording is not None
                  and bridge.state.recording.snapshot.committed_segments >= 1)
        # GUI Stop is accepted even if the observed protocol state has internal STOP pending.
        bridge._state = replace(bridge.state, snapshot=replace(bridge.state.snapshot, stop_barrier=True))
        self.assertTrue(bridge.stop())
        self.wait(lambda: bridge.state.recording.ready_to_save)
        self.assertTrue(bridge.state.recording.snapshot.user_stop_requested)
        self.assertEqual(bridge.state.recording.snapshot.termination_reason, "user_stop")

    def test_qt_shutdown_retires_blocked_daemon_without_waiting_or_false_success(self):
        entered, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        original = archive._sync
        calls = [0]
        def blocked(stream):
            calls[0] += 1
            if calls[0] == 2:  # Header already durable, member writer now blocked.
                entered.set(); gate.wait(5)
            return original(stream)
        with patch.object(archive, "_sync", blocked):
            bridge = self.make()
            path = str(Path(self.temp.name) / "close.spool")
            self.assertTrue(bridge.record(Config(), path))
            self.wait(entered.is_set)
            adapter = bridge._worker.recording
            self.assertIsNotNone(adapter.runtime.pending_capture)
            before = time.monotonic()
            self.assertTrue(bridge.shutdown())
            self.wait(lambda: bridge._closed and not bridge.running)
            self.assertLess(time.monotonic() - before, .5)
            state = bridge.state.recording
            self.assertTrue(state.retired)
            self.assertTrue(state.writer_running)
            self.assertFalse(state.ready_to_save)
            self.assertEqual(state.spool_path, path)
            self.assertEqual(state.snapshot.stop_evidence, "unknown")
            self.assertTrue(state.snapshot.source_retention_required)
            self.assertEqual(bridge.saved, [])
            gate.set()
            self.wait(lambda: not adapter._writer.running)
        self.assertEqual(bridge.saved, [])
        self.assertEqual(len(archive.recover(path).entries), 1)


    def test_qt_save_cancel_queued_and_running_without_spool_loss(self):
        bridge = self.make()
        self.assertTrue(bridge.record(Config(mode=2), str(Path(self.temp.name) / "save.spool")))
        self.wait(lambda: bridge.state.recording is not None and bridge.state.recording.ready_to_save)
        # Cancel before a subsequent event-loop turn; the public mailbox restores busy state.
        destination = str(Path(self.temp.name) / "queued.mtrec")
        self.assertTrue(bridge.save_recording(destination))
        self.assertTrue(bridge.cancel_recording_save())
        self.wait(lambda: not bridge.state.busy)
        self.assertFalse(Path(destination).exists())
        entered, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        original = archive._sync
        def blocked(stream):
            entered.set(); gate.wait(3)
            return original(stream)
        with patch.object(archive, "_sync", blocked):
            self.assertTrue(bridge.save_recording(destination))
            self.wait(lambda: entered.is_set() and bridge.state.recording.saving)
            self.assertTrue(bridge.cancel_recording_save())
            # Let the acquisition worker apply the request before allowing publication.
            self.wait(lambda: bridge._worker.recording._save_cancel.is_set())
            gate.set()
            self.wait(lambda: not bridge.state.recording.saving)
        self.assertFalse(Path(destination).exists())
        self.assertEqual(bridge.saved, [])
        self.assertTrue(bridge.state.recording.ready_to_save)

    def test_qt_disconnect_during_writer_wait_saves_unknown_and_explicit_new_nonce(self):
        bridge = self.make()
        entered, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        original = archive._sync
        calls = [0]
        def blocked(stream):
            calls[0] += 1
            if calls[0] == 2:
                entered.set(); gate.wait(3)
            return original(stream)
        with patch.object(archive, "_sync", blocked):
            self.assertTrue(bridge.record(Config(), str(Path(self.temp.name) / "disconnect.spool")))
            self.wait(entered.is_set)
            nonce = bridge.state.snapshot.session
            self.assertTrue(bridge.disconnect_source())
            self.wait(lambda: not bridge.state.connected)
            self.assertTrue(bridge.state.recording.writer_busy)
            self.assertFalse(bridge.connect_source("emulator"))
            gate.set()
            self.wait(lambda: bridge.state.recording.ready_to_save)
        self.assertEqual(bridge.state.recording.snapshot.stop_evidence, "unknown")
        destination = str(Path(self.temp.name) / "disconnected.mtrec")
        self.assertTrue(bridge.save_recording(destination))
        self.wait(lambda: bridge.saved == [destination])
        self.assertTrue(bridge.connect_source("emulator"))
        self.wait(lambda: bridge.state.connected and bridge.state.snapshot.phase is Phase.READY
                  and not bridge.state.busy)
        self.assertNotEqual(bridge.state.snapshot.session, nonce)
        self.assertIsNone(bridge.state.recording)
        self.assertTrue(bridge.record(Config(mode=2), str(Path(self.temp.name) / "new.spool"), simulate_edge=False))
        self.wait(lambda: bridge.state.snapshot.phase is Phase.ARMED)
        self.assertTrue(bridge.stop())
        self.wait(lambda: bridge.state.recording.ready_to_save)
        self.assertEqual(bridge.state.recording.snapshot.committed_segments, 0)

    def test_blocked_fsync_daemon_process_exit_is_bounded_and_prefix_recoverable(self):
        path = Path(self.temp.name) / "exit.spool"
        script = textwrap.dedent("""
            import json, sys, threading, time
            from PySide6 import QtWidgets
            from motortester.acquisition import AcquisitionBridge, _AcquisitionWorker, _Mailbox
            from motortester.session import Config, Phase
            from motortester import session_archive as archive
            from qt_test_support import pump_events
            app = QtWidgets.QApplication([])
            app.setQuitOnLastWindowClosed(False)
            entered = threading.Event()
            original = archive._sync
            calls = [0]
            def blocked(stream):
                calls[0] += 1
                if calls[0] == 2:
                    entered.set()
                    threading.Event().wait()  # Deliberately never finishes.
                return original(stream)
            archive._sync = blocked
            bridge = AcquisitionBridge(interval_ms=1, edge_delay_ms=1)
            def wait(condition):
                deadline = time.monotonic() + 3
                while not condition():
                    if time.monotonic() > deadline: raise RuntimeError('fixture deadline')
                    pump_events(5)
            if not bridge.connect_source('emulator'): raise RuntimeError('connect rejected')
            wait(lambda: bridge.state.snapshot is not None and bridge.state.snapshot.phase is Phase.READY and not bridge.state.busy)
            if not bridge.record(Config(), sys.argv[1]): raise RuntimeError('record rejected')
            wait(entered.is_set)
            if not bridge.shutdown(): raise RuntimeError('shutdown rejected')
            wait(lambda: bridge._closed and not bridge.running)
            state = bridge.state.recording
            print(json.dumps(dict(retired=state.retired, writer_running=state.writer_running,
                ready_to_save=state.ready_to_save, stop_evidence=state.snapshot.stop_evidence)), flush=True)
            bridge.deleteLater()
            pump_events(20)
        """)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parent),
            str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH", "")])
        command = [sys.executable] + (["-O"] if sys.flags.optimize else []) + ["-c", script, str(path)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=6, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        facts = json.loads(result.stdout.strip())
        self.assertEqual(facts, dict(retired=True, writer_running=True,
            ready_to_save=False, stop_evidence="unknown"))
        report = archive.recover(path)
        self.assertEqual(report.entries, ())  # The uncommitted member is not invented as durable.
        self.assertTrue(report.issues)
        evidence = os.environ.get("RECORDING_BRIDGE_EVIDENCE")
        if evidence:
            folder = Path(tempfile.mkdtemp(prefix="blocked-exit-", dir=evidence))
            shutil.copyfile(path, folder / path.name)
            (folder / "stdout.json").write_text(result.stdout)
            (folder / "stderr.log").write_text(result.stderr)


    def test_each_same_path_save_and_later_recording_delivers_exact_completion(self):
        bridge = self.make()
        folder = Path(self.temp.name)
        destination = str(folder / "repeat.mtrec")
        self.assertTrue(bridge.record(Config(mode=2), str(folder / "first.spool")))
        self.wait(lambda: bridge.state.recording is not None and bridge.state.recording.ready_to_save)
        identities = []
        for index in range(3):
            if index == 2:
                self.assertTrue(bridge.record(Config(mode=2), str(folder / "later.spool")))
                self.wait(lambda: bridge.state.recording.ready_to_save and not bridge.state.busy)
            self.assertTrue(bridge.save_recording(destination))
            self.wait(lambda: len(bridge.saved) == index + 1)
            self.assertIsNone(bridge._save_requested)
            identities.append(archive.load(destination).identity.recording_id)
            Path(destination).rename(folder / f"preserved-{index}.mtrec")
        self.assertEqual(bridge.saved, [destination] * 3)
        self.assertEqual(identities[0], identities[1])
        self.assertNotEqual(identities[1], identities[2])

    def test_unobserved_completion_blocks_second_save_and_preserves_first_signal(self):
        bridge = self.make()
        folder = Path(self.temp.name)
        self.assertTrue(bridge.record(Config(mode=2), str(folder / "mailbox.spool")))
        self.wait(lambda: bridge.state.recording is not None and bridge.state.recording.ready_to_save)
        first, second = str(folder / "first.mtrec"), str(folder / "second.mtrec")
        self.assertTrue(bridge.save_recording(first))
        operation = bridge._save_requested
        # Release the GIL, but intentionally do not process GUI notifications.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with bridge._box.lock:
                complete = bridge._box.saved is not None
            if complete:
                break
            time.sleep(.002)
        self.assertTrue(complete)
        self.assertTrue(Path(first).exists())
        self.assertEqual(bridge.saved, [])
        self.assertIs(bridge._save_requested, operation)
        self.assertFalse(bridge.save_recording(second))
        self.assertFalse(Path(second).exists())
        self.wait(lambda: bridge.saved == [first])
        self.assertIsNone(bridge._save_requested)
        self.assertTrue(bridge.save_recording(second))
        self.wait(lambda: bridge.saved == [first, second])

    def test_repeated_identical_save_failures_each_settle_exact_admission(self):
        bridge = self.make()
        folder = Path(self.temp.name)
        self.assertTrue(bridge.record(Config(mode=2), str(folder / "failures.spool")))
        self.wait(lambda: bridge.state.recording is not None and bridge.state.recording.ready_to_save)
        destination = folder / "exists.mtrec"
        destination.write_bytes(b"preserve user file")
        for count in (1, 2):
            self.assertTrue(bridge.save_recording(str(destination)))
            self.wait(lambda: len(bridge.errors) == count and bridge._save_requested is None
                      and not bridge.state.busy)
            self.assertEqual(destination.read_bytes(), b"preserve user file")
        self.assertEqual(bridge.saved, [])
        destination.rename(folder / "preserved-user-file")
        self.assertTrue(bridge.save_recording(str(destination)))
        self.wait(lambda: bridge.saved == [str(destination)])

    def test_cancel_racing_publication_does_not_cancel_next_save_from_completion_slot(self):
        bridge = self.make()
        folder = Path(self.temp.name)
        self.assertTrue(bridge.record(Config(mode=2), str(folder / "late-cancel.spool")))
        self.wait(lambda: bridge.state.recording is not None and bridge.state.recording.ready_to_save)
        first, second = str(folder / "first.mtrec"), str(folder / "second.mtrec")
        entered, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        from motortester import recording_bridge
        original = recording_bridge.finalize
        def published(*args, **kwargs):
            result = original(*args, **kwargs)
            if str(result.path) == first:
                entered.set(); gate.wait(3)
            return result
        admitted = []
        def next_save(path):
            if path == first:
                admitted.append(bridge.save_recording(second))
        bridge.recording_saved.connect(next_save)
        with patch.object(recording_bridge, "finalize", published):
            self.assertTrue(bridge.save_recording(first))
            self.wait(entered.is_set)
            self.assertTrue(bridge.cancel_recording_save())
            gate.set()
            self.wait(lambda: bridge.saved == [first, second])
        self.assertEqual(admitted, [True])
        self.assertTrue(Path(second).exists())
        self.assertIsNone(bridge._save_requested)
        self.assertIsNone(bridge._box.cancel_save)

    def test_exact_cancel_token_consumed_mid_tick_and_stale_token_cannot_cancel_new_job(self):
        h = Harness(self, mode=2)
        h.start(); h.capture(); h.finish()
        box = _Mailbox()
        worker = _AcquisitionWorker(box, None, lambda: h.link.now, 1, 1)
        worker.runtime, worker.recording = h.runtime, h.adapter
        first, second = Path(h.temp.name) / "A.mtrec", Path(h.temp.name) / "B.mtrec"
        first_operation, second_operation = object(), object()
        self.assertTrue(h.adapter.save(first, operation=first_operation))
        deadline = time.monotonic() + 2
        while not first.exists() or h.adapter._writer._completion is None:
            if time.monotonic() > deadline: self.fail("writer completion did not arrive")
            time.sleep(.001)
        box.cancel_save = first_operation
        worker._recording_step()
        self.assertIsNone(box.cancel_save)
        self.assertEqual(h.adapter.state.saved_path, str(first))
        box.command = ("save_recording", (str(second), second_operation))
        worker.pump()
        self.assertFalse(h.adapter.cancel_save(first_operation))
        h.until(lambda: not h.adapter.state.saving)
        self.assertEqual(h.adapter.state.saved_path, str(second))
        self.assertTrue(second.exists())
        self.assertIs(h.adapter.save_completion.operation, second_operation)

    def test_disconnect_cancels_queued_save_admission_without_trapping_retry(self):
        bridge = self.make()
        folder = Path(self.temp.name)
        self.assertTrue(bridge.record(Config(mode=2), str(folder / "queued-close.spool")))
        self.wait(lambda: bridge.state.recording is not None and bridge.state.recording.ready_to_save)
        path = str(folder / "later.mtrec")
        self.assertTrue(bridge.save_recording(path))
        self.assertTrue(bridge.disconnect_source())
        self.wait(lambda: not bridge.state.connected and not bridge.state.busy
                  and bridge._save_requested is None)
        # Either admission was canceled, or a completed publication stays success.
        if Path(path).exists():
            self.assertEqual(bridge.saved, [path])
            Path(path).rename(folder / "first-publication.mtrec")
        self.assertTrue(bridge.save_recording(path))
        expected = len(bridge.saved) + 1
        self.wait(lambda: len(bridge.saved) == expected)
        self.assertTrue(Path(path).exists())


if __name__ == "__main__":
    unittest.main()

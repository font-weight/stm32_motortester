"""Headless recording traces; real bounded runtime plus synthetic byte peer.

Run from host with python -m unittest discover -s tests -p
 test_recording_controller.py -v, and repeat under python -O. No Qt, physical
USB, firmware timing or filesystem power-loss durability is established here.
"""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from motortester.device_emulator import DeviceEmulator
from motortester.model import Capture
from motortester.recording import encode
from motortester.recording_controller import (IntentKind as K, RecordingController,
    RecordingError, RecordingPhase, RecordingResult, MAX_RECORDING_MS)
from motortester.runtime import AcquisitionRuntime
from motortester.session import Config, Event, EventKind, Phase
from motortester.session_archive import (AbortEvent, CommitReceipt, FullSegment,
    PartialSegment, SampleFragment, SegmentProvenance, SessionIdentity, SessionSpool,
    load, finalize, recover)
from motortester.window import WindowCode, WindowResult
from motortester.wire import StreamDecoder


class Link:
    def __init__(self):
        self.now = 0
        self.peer = DeviceEmulator(all_analog_modes=True)
        self.writes = []
        self.read_limit = self.write_limit = 4096

    def write(self, data):
        self.writes.append(data[:self.write_limit])
        return self.peer.write(data[:self.write_limit], self.now).accepted_prefix

    def read(self, size):
        return self.peer.read(min(size, self.read_limit), self.now)


class Rig:
    """Explicit test adapter; disk work is invoked only by the test."""
    def __init__(self, case, mode=1, *, limit=MAX_RECORDING_MS):
        self.case = case
        self.folder = tempfile.TemporaryDirectory()
        case.addCleanup(self.folder.cleanup)
        self.link = Link()
        self.runtime = AcquisitionRuntime(self.link, origin="simulator",
            clock_ms=lambda: self.link.now, utc_now=lambda: "2026-10-12T19:00:00Z")
        self.runtime.start(101)
        for _ in range(20):
            self.runtime.pump()
            if self.runtime.snapshot().phase is Phase.READY:
                break
        case.assertIs(self.runtime.snapshot().phase, Phase.READY)
        self.config = Config(mode=mode, auto_timeout_ms=100 if mode == 0 else 0)
        s = self.runtime.snapshot()
        self.identity = SessionIdentity(str(UUID(int=1001)), str(UUID(int=s.session)),
            "simulator", str(s.capabilities.firmware_build),
            f"draft-0.1@{s.source_sha}", "0.1.0")
        self.controller = RecordingController(max_recording_ms=limit)
        self.token = self.controller.record(self.identity, self.config, s, now_ms=0)
        self.spool = None
        self.receipts = []
        self.captures = []

    def intent(self, kind):
        values = [i for i in self.controller.intents if i.kind is kind]
        self.case.assertEqual(len(values), 1, (kind, self.controller.snapshot(), self.controller.intents))
        return values[0]

    def claim(self, intent):
        self.case.assertTrue(self.controller.admit(intent, now_ms=self.link.now,
            runtime_state=self.runtime.snapshot()))

    def prepare(self, capacity=None):
        intent = self.intent(K.PREPARE)
        self.claim(intent)
        self.spool = SessionSpool.create(Path(self.folder.name) / "record.spool", self.identity)
        self.case.addCleanup(self.spool.close)
        self.case.assertTrue(self.controller.spool_prepared(intent,
            issuer=self.spool.receipt_issuer, capacity=capacity or self.spool.capacity,
            now_ms=self.link.now))
        return intent

    def command(self, kind):
        intent = self.intent(kind)
        self.claim(intent)
        if kind is K.CONFIGURE:
            events = self.runtime.configure(intent.payload)
        elif kind is K.ARM:
            events = self.runtime.arm()
        elif kind is K.STOP:
            events = self.runtime.stop()
        else:
            raise AssertionError(kind)
        self.controller.command_submitted(intent, self.runtime.snapshot(), now_ms=self.link.now)
        self.observe(events)
        return intent

    def provenance(self):
        s = self.runtime.snapshot()
        return SegmentProvenance(generation=s.generation, config_id=s.config_id,
            acquisition_mode=self.config.mode, auto_timeout_ms=self.config.auto_timeout_ms,
            host_elapsed_ms=self.link.now, wire_version="0.1", start_operation="arm",
            wire_session_nonce=s.session, config_payload_hex=self.config.payload().hex())

    def available(self):
        result = []
        capture = self.runtime.pending_capture
        if capture is not None:
            result.append(RecordingResult(FullSegment(capture, self.provenance()), capture))
        partial = self.runtime.incomplete_result
        if partial is not None:
            if partial.begin is None:
                item = AbortEvent(partial.reason.value, provenance=self.provenance())
            else:
                provenance = self.provenance()
                if partial.abort_reason is not None:
                    provenance = replace(provenance, terminal_kind="abort",
                                         abort_reason_code=partial.abort_reason)
                item = PartialSegment(partial.begin.window_id, partial.begin.sample_count,
                    tuple(SampleFragment(f.offset, f.samples) for f in partial.fragments),
                    partial.missing_ranges, partial.reason.value, provenance,
                    deferred_checks=partial.deferred_checks,
                    metadata_issues=partial.metadata_issues)
            result.append(RecordingResult(item, partial))
        return tuple(result)

    def observe(self, events=()):
        return self.controller.observe(self.token, self.runtime.snapshot(), events,
            self.available(), now_ms=self.link.now)

    def pump(self):
        self.observe(self.runtime.pump())

    def until(self, predicate):
        for _ in range(500):
            if predicate():
                return
            self.pump()
        self.case.fail(f"Trace did not finish: {self.controller.snapshot()}")

    def start(self):
        self.prepare()
        self.command(K.CONFIGURE)
        self.until(lambda: any(i.kind is K.ARM for i in self.controller.intents))
        self.command(K.ARM)
        self.until(lambda: self.runtime.snapshot().phase is Phase.ARMED)

    def capture(self):
        self.case.assertTrue(self.link.peer.trigger(self.link.now))
        self.until(lambda: self.runtime.pending_capture is not None)
        capture = self.runtime.pending_capture
        self.captures.append(capture)
        self.case.assertFalse(any(i.kind in (K.PERSIST, K.ACK_LOCAL, K.ARM)
                                 for i in self.controller.intents))
        return capture

    def settle_stop(self):
        self.command(K.STOP)
        self.until(lambda: self.controller.snapshot().stop_evidence == "stop_ack")

    def persist(self):
        intent = self.intent(K.PERSIST)
        self.claim(intent)
        item = intent.payload.result.item
        if type(item) is FullSegment:
            receipt = self.spool.append_full(item)
        elif type(item) is PartialSegment:
            receipt = self.spool.append_partial(item)
        else:
            receipt = self.spool.append_abort(item)
        self.receipts.append(receipt)
        self.case.assertTrue(self.controller.committed(intent, receipt, now_ms=self.link.now))
        return intent, receipt

    def ack(self):
        intent = self.intent(K.ACK_LOCAL)
        self.claim(intent)
        source = intent.payload.result.source
        before = len(self.link.writes)
        if type(source) is Capture:
            self.case.assertIs(source, self.runtime.pending_capture)
            self.case.assertEqual(self.runtime.acknowledge(source), ())
        elif type(source) is WindowResult:
            self.case.assertIs(source, self.runtime.take_incomplete())
        else:
            self.case.assertIs(type(source), Event)
            self.case.assertIs(source.kind, EventKind.WINDOW_ABORT)
            self.case.assertIsNone(self.runtime.pending_capture)
            self.case.assertIsNone(self.runtime.incomplete_result)
        self.case.assertTrue(self.controller.acknowledged(intent, self.runtime.snapshot(), now_ms=self.link.now))
        self.case.assertEqual(len(self.link.writes), before)
        return intent

    def finish(self):
        intent = self.intent(K.FINALIZE_READY)
        self.claim(intent)
        outcome = intent.payload
        report = self.spool.finish(outcome.reason, outcome.stop_evidence, outcome.diagnostic)
        self.case.assertTrue(self.controller.finalized(intent, now_ms=self.link.now))
        return report

    def frames(self):
        return StreamDecoder().feed(b"".join(self.link.writes), self.link.now).frames


class ControllerTests(unittest.TestCase):
    def test_auto_and_normal_repeat_four_distinct_generations_without_release(self):
        for mode in (0, 1):
            with self.subTest(mode=mode):
                rig = Rig(self, mode)
                rig.start()
                generations = []
                for _ in range(4):
                    generations.append(rig.runtime.snapshot().generation)
                    capture = rig.capture()
                    self.assertIs(rig.controller.snapshot().pending[0].result.source, capture)
                    rig.settle_stop()
                    rig.persist()
                    self.assertIs(rig.intent(K.ACK_LOCAL).payload.result.source, capture)
                    rig.ack()
                    if len(generations) < 4:
                        rig.command(K.ARM)
                        rig.until(lambda: rig.runtime.snapshot().phase is Phase.ARMED)
                cached_arm = rig.intent(K.ARM)
                rig.controller.stop(now_ms=rig.link.now)
                self.assertFalse(rig.controller.admit(cached_arm, now_ms=0,
                    runtime_state=rig.runtime.snapshot()))
                report = rig.finish()
                self.assertEqual(len(report.entries), 4)
                self.assertEqual(generations, sorted(set(generations)))
                frames = rig.frames()
                self.assertEqual(sum(f.message_type == 3 for f in frames), 1)
                self.assertEqual(sum(f.message_type == 4 for f in frames), 4)
                self.assertEqual(sum(f.message_type == 5 for f in frames), 4)
                self.assertEqual(sum(f.message_type == 7 for f in frames), 0)
                self.assertEqual([e.sequence for e in report.entries], [c.sequence for c in rig.captures])
                self.assertEqual([e.provenance.generation for e in report.entries], generations)
                self.assertEqual(report.outcome.stop_evidence, "stop_ack")
                path = Path(rig.folder.name) / "all-windows.mtrec"
                finalize(report, path)
                reopened = load(path)
                self.assertEqual([reopened.read_segment(i) for i in range(4)],
                                 [encode(c) for c in rig.captures])

    def test_single_ends_after_one_verified_durable_window(self):
        rig = Rig(self, 2)
        rig.start()
        rig.capture()
        self.assertEqual(rig.controller.snapshot().termination_reason, "single_complete")
        rig.settle_stop()
        rig.persist()
        rig.ack()
        report = rig.finish()
        self.assertEqual(report.outcome.reason, "single_complete")
        self.assertEqual(report.outcome.stop_evidence, "stop_ack")
        self.assertEqual(sum(f.message_type == 4 for f in rig.frames()), 1)
        self.assertFalse(any(f.message_type == 7 for f in rig.frames()))

    def test_missing_trigger_creates_no_window_and_time_guard_stops(self):
        rig = Rig(self, limit=10000)
        rig.start()
        rig.link.now = 9999
        rig.pump()
        self.assertEqual(rig.controller.snapshot().pending, ())
        self.assertFalse(rig.controller.intents)
        rig.link.now = 10000
        rig.controller.tick(now_ms=rig.link.now)
        self.assertEqual(rig.controller.snapshot().termination_reason, "capacity")
        rig.settle_stop()
        report = rig.finish()
        self.assertEqual(report.entries, ())
        self.assertEqual(report.outcome.diagnostic, "elapsed_time_limit")

    def test_stop_before_preparation_completes_never_configures(self):
        rig = Rig(self)
        rig.controller.stop(now_ms=0)
        rig.prepare()
        self.assertEqual([i.kind for i in rig.controller.intents], [K.FINALIZE_READY])
        report = rig.finish()
        self.assertEqual(report.entries, ())
        self.assertEqual(report.outcome.stop_evidence, "none")
        self.assertFalse(any(f.message_type in (3, 4, 5, 7) for f in rig.frames()))

    def test_stop_cancels_unclaimed_config_and_arm(self):
        for stage in ("config", "arm"):
            with self.subTest(stage=stage):
                rig = Rig(self)
                rig.prepare()
                if stage == "arm":
                    rig.command(K.CONFIGURE)
                    rig.until(lambda: any(i.kind is K.ARM for i in rig.controller.intents))
                old = rig.intent(K.CONFIGURE if stage == "config" else K.ARM)
                rig.controller.stop(now_ms=0)
                self.assertFalse(rig.controller.admit(old, now_ms=0,
                    runtime_state=rig.runtime.snapshot()))
                rig.finish()
                self.assertFalse(any(f.message_type == 4 for f in rig.frames()))

    def test_stop_during_config_and_untransmitted_or_partial_arm_drains_reply(self):
        for stage in ("config", "arm_untransmitted", "arm_partial", "armed"):
            with self.subTest(stage=stage):
                rig = Rig(self)
                rig.prepare()
                rig.command(K.CONFIGURE)
                if stage != "config":
                    rig.until(lambda: any(i.kind is K.ARM for i in rig.controller.intents))
                    rig.command(K.ARM)
                if stage == "arm_partial":
                    rig.link.write_limit = 1
                    rig.pump()
                if stage == "armed":
                    rig.until(lambda: rig.runtime.snapshot().phase is Phase.ARMED)
                rig.controller.stop(now_ms=0)
                rig.settle_stop()
                report = rig.finish()
                self.assertEqual(report.outcome.reason, "user_stop")
                self.assertEqual(report.outcome.stop_evidence, "stop_ack")
                self.assertFalse(any(f.message_type == 7 for f in rig.frames()))

    def test_stop_before_and_after_each_storage_edge_never_rearms(self):
        for stage in ("end", "stop_submitted", "persist_unclaimed", "persist_claimed", "ack_unclaimed", "ack_claimed", "next_arm"):
            with self.subTest(stage=stage):
                rig = Rig(self)
                rig.start()
                rig.capture()
                if stage != "end":
                    if stage == "stop_submitted":
                        rig.command(K.STOP)
                    else:
                        rig.settle_stop()
                if stage == "persist_claimed":
                    pending = rig.intent(K.PERSIST)
                    rig.claim(pending)
                elif stage in ("ack_unclaimed", "ack_claimed", "next_arm"):
                    rig.persist()
                    if stage == "ack_claimed":
                        pending = rig.intent(K.ACK_LOCAL)
                        rig.claim(pending)
                    elif stage == "next_arm":
                        rig.ack()
                rig.controller.stop(now_ms=0)
                if stage == "end":
                    rig.settle_stop()
                elif stage == "stop_submitted":
                    rig.until(lambda: rig.controller.snapshot().stop_evidence == "stop_ack")
                if stage == "persist_claimed":
                    receipt = rig.spool.append_full(pending.payload.result.item)
                    self.assertTrue(rig.controller.committed(pending, receipt, now_ms=0))
                elif stage not in ("ack_unclaimed", "ack_claimed", "next_arm"):
                    rig.persist()
                if stage == "ack_claimed":
                    rig.runtime.acknowledge(pending.payload.result.source)
                    rig.controller.acknowledged(pending, rig.runtime.snapshot(), now_ms=0)
                elif stage != "next_arm":
                    rig.ack()
                self.assertEqual(rig.intent(K.FINALIZE_READY).payload.reason, "user_stop")
                self.assertEqual(sum(f.message_type == 4 for f in rig.frames()), 1)
                self.assertEqual(sum(f.message_type == 7 for f in rig.frames()), 0)

    def test_never_finishing_writer_does_not_block_stop_or_polling(self):
        rig = Rig(self)
        rig.start()
        capture = rig.capture()
        rig.settle_stop()
        writer = rig.intent(K.PERSIST)
        rig.claim(writer)
        with patch("builtins.open", side_effect=AssertionError("controller performed I/O")):
            rig.controller.stop(now_ms=1)
            rig.link.now = 6001
            rig.pump()
            rig.controller.tick(now_ms=6001)
        state = rig.controller.snapshot()
        self.assertTrue(state.stop_fence)
        self.assertTrue(state.writer_pending)
        self.assertIs(state.pending[0].result.source, capture)
        self.assertEqual(state.stop_evidence, "stop_ack")
        self.assertFalse(rig.controller.intents)
        self.assertFalse(any(f.message_type == 7 for f in rig.frames()))

    def test_matching_stop_ack_suffices_without_status(self):
        rig = Rig(self)
        rig.start()
        rig.capture()
        intent = rig.command(K.STOP)
        op = rig.controller._ops[K.STOP]
        fake = replace(rig.runtime.snapshot(), phase=Phase.READY,
                       pending_stop_id=0, stop_barrier=False, window_barrier=False)
        for wrong in (Event(EventKind.STATUS, generation=op.generation),
                      Event(EventKind.STOPPED, op.request_id + 1, op.generation),
                      Event(EventKind.STOPPED, op.request_id, op.generation + 1)):
            rig.controller.observe(rig.token, fake, (wrong,), now_ms=0)
            self.assertFalse(any(i.kind is K.PERSIST for i in rig.controller.intents))
        rig.controller.observe(rig.token, fake,
            (Event(EventKind.STOPPED, op.request_id, op.generation),), now_ms=0)
        self.assertEqual(rig.intent(K.PERSIST).kind, K.PERSIST)
        self.assertEqual(rig.controller.snapshot().stop_evidence, "stop_ack")

    def test_wrong_receipts_never_acknowledge_or_discard_pending(self):
        for defect in ("malformed", "constructed", "copied", "other_spool", "other_capture", "wrong_ordinal", "member_only"):
            with self.subTest(defect=defect):
                rig = Rig(self)
                rig.start()
                capture = rig.capture()
                rig.settle_stop()
                intent = rig.intent(K.PERSIST)
                rig.claim(intent)
                item = intent.payload.result.item
                if defect == "malformed":
                    receipt = CommitReceipt(None, None, None, object())
                elif defect == "other_spool":
                    other = SessionSpool.create(Path(rig.folder.name) / "other.spool", rig.identity)
                    self.addCleanup(other.close)
                    receipt = other.append_full(item)
                elif defect == "other_capture":
                    receipt = rig.spool.append_full(FullSegment(replace(capture), item.provenance))
                elif defect == "wrong_ordinal":
                    rig.spool.append_abort(AbortEvent("unexpected earlier entry"))
                    receipt = rig.spool.append_full(item)
                elif defect == "member_only":
                    receipt = rig.spool.append_capture(encode(capture), item.provenance)
                else:
                    receipt = rig.spool.append_full(item)
                    receipt = (CommitReceipt(receipt.identity, receipt.entry, receipt.capacity_after,
                                             receipt.issuer, receipt.source_item)
                               if defect == "constructed" else replace(receipt))
                self.assertFalse(rig.controller.committed(intent, receipt, now_ms=0))
                self.assertIs(rig.controller.snapshot().pending[0].result.source, capture)
                self.assertEqual(rig.controller.snapshot().fault, "invalid_commit_receipt")
                self.assertFalse(rig.controller.intents)

    def test_writer_failure_retains_exact_pending_and_never_retries(self):
        rig = Rig(self)
        rig.start()
        capture = rig.capture()
        rig.settle_stop()
        intent = rig.intent(K.PERSIST)
        rig.claim(intent)
        rig.controller.failed(intent, "storage_or_readback", now_ms=0)
        rig.controller.stop(now_ms=1)
        rig.controller.tick(now_ms=9999)
        self.assertIs(rig.controller.snapshot().pending[0].result.source, capture)
        self.assertFalse(rig.controller.intents)
        self.assertEqual(rig.controller.snapshot().termination_reason, "error")

    def test_recovery_can_persist_and_ack_locally_but_stop_remains_unknown(self):
        rig = Rig(self)
        rig.start()
        capture = rig.capture()
        rig.observe(rig.runtime.disconnect())
        self.assertEqual(rig.controller.snapshot().stop_evidence, "unknown")
        self.assertIs(rig.intent(K.PERSIST).payload.result.source, capture)
        rig.persist()
        rig.ack()
        report = rig.finish()
        self.assertEqual(report.outcome.reason, "error")
        self.assertEqual(report.outcome.stop_evidence, "unknown")
        self.assertFalse(any(f.message_type == 7 for f in rig.frames()))
        identity = replace(rig.identity, recording_id=str(UUID(int=1002)))
        with self.assertRaises(RecordingError):
            rig.controller.record(identity, rig.config,
                replace(rig.runtime.snapshot(), phase=Phase.READY), now_ms=0)

    def test_capacity_guard_reserves_two_terminal_slots_before_config(self):
        rig = Rig(self)
        initial = rig.intent(K.PREPARE)
        rig.claim(initial)
        spool = SessionSpool.create(Path(rig.folder.name) / "capacity.spool", rig.identity)
        self.addCleanup(spool.close)
        capacity = replace(spool.capacity, entries_remaining=1)
        rig.controller.spool_prepared(initial, issuer=spool.receipt_issuer,
                                     capacity=capacity, now_ms=0)
        self.assertEqual(rig.controller.snapshot().termination_reason, "capacity")
        self.assertEqual([i.kind for i in rig.controller.intents], [K.FINALIZE_READY])

    def test_bounded_extra_terminal_is_retained_and_third_stays_with_caller(self):
        rig = Rig(self)
        rig.start()
        first = rig.capture()
        second = replace(first, sequence=first.sequence + 1)
        third = replace(first, sequence=first.sequence + 2)
        p = rig.provenance()
        second_result = RecordingResult(FullSegment(second, p), second)
        third_result = RecordingResult(FullSegment(third, p), third)
        self.assertEqual(rig.controller.observe(rig.token, rig.runtime.snapshot(),
            results=(second_result,), now_ms=0), (True,))
        self.assertEqual(rig.controller.observe(rig.token, rig.runtime.snapshot(),
            results=(third_result,), now_ms=0), (False,))
        state = rig.controller.snapshot()
        self.assertEqual(len(state.pending), 2)
        self.assertIs(state.pending[0].result.source, first)
        self.assertIs(state.pending[1].result.source, second)
        self.assertIs(third_result.source, third)
        self.assertTrue(state.stop_fence)
        self.assertFalse(any(i.kind in (K.PERSIST, K.ACK_LOCAL, K.ARM) for i in rig.controller.intents))

    def test_local_ack_admission_rechecks_actual_runtime_state(self):
        rig = Rig(self)
        rig.start()
        capture = rig.capture()
        rig.settle_stop()
        rig.persist()
        intent = rig.intent(K.ACK_LOCAL)
        active = replace(rig.runtime.snapshot(), phase=Phase.ARMED)
        self.assertFalse(rig.controller.admit(intent, now_ms=0, runtime_state=active))
        self.assertIs(rig.controller.snapshot().pending[0].result.source, capture)
        self.assertEqual(rig.controller.snapshot().fault, "unsafe_local_ack")
        self.assertTrue(any(i.kind is K.STOP for i in rig.controller.intents))

    def test_stale_callbacks_cannot_change_new_session_or_clock(self):
        rig = Rig(self)
        old = rig.intent(K.PREPARE)
        rig.controller.stop(now_ms=0)
        rig.prepare()
        rig.finish()
        identity = replace(rig.identity, recording_id=str(UUID(int=1002)))
        new = rig.controller.record(identity, rig.config, rig.runtime.snapshot(), now_ms=1)
        before = rig.controller.snapshot()
        self.assertFalse(rig.controller.spool_prepared(old, issuer=object(),
            capacity=None, now_ms=MAX_RECORDING_MS * 2))
        self.assertFalse(rig.controller.admit(old, now_ms=MAX_RECORDING_MS * 2))
        self.assertEqual(rig.controller.observe(rig.token, rig.runtime.snapshot(), now_ms=0), ())
        self.assertEqual(rig.controller.snapshot(), before)
        self.assertIsNot(new, rig.token)

    def test_snapshots_immutable_duplicate_record_and_clock_validation(self):
        rig = Rig(self)
        before = rig.controller.snapshot()
        with self.assertRaises(RecordingError):
            rig.controller.record(rig.identity, rig.config, rig.runtime.snapshot(), now_ms=0)
        with self.assertRaises(FrozenInstanceError):
            before.stop_fence = True
        rig.controller.tick(now_ms=2)
        after = rig.controller.snapshot()
        with self.assertRaises(RecordingError):
            rig.controller.tick(now_ms=1)
        self.assertEqual(rig.controller.snapshot(), after)
        for value in (True, 0, -1, MAX_RECORDING_MS + 1):
            with self.assertRaises(RecordingError):
                RecordingController(max_recording_ms=value)


    def test_real_auto_timeout_preserves_absence_of_trigger(self):
        rig = Rig(self, 0)
        rig.start()
        rig.link.now = 100
        rig.until(lambda: rig.runtime.pending_capture is not None)
        capture = rig.runtime.pending_capture
        self.assertEqual(capture.trigger_kind, "auto_timeout")
        self.assertIsNone(capture.trigger_index)
        rig.controller.stop(now_ms=100)
        rig.settle_stop()
        rig.persist()
        rig.ack()
        report = rig.finish()
        self.assertEqual(report.entries[0].provenance.auto_timeout_ms, 100)

    def test_stop_during_real_data_retains_partial_without_padding(self):
        rig = Rig(self)
        rig.start()
        rig.link.read_limit = 700
        self.assertTrue(rig.link.peer.trigger(0))
        rig.pump()
        self.assertIsNone(rig.runtime.pending_capture)
        rig.controller.stop(now_ms=0)
        rig.settle_stop()
        pending = rig.controller.snapshot().pending
        self.assertEqual(len(pending), 1)
        self.assertIs(type(pending[0].result.item), PartialSegment)
        source = pending[0].result.source
        self.assertIsNone(source.samples)
        self.assertTrue(source.fragments)
        self.assertTrue(source.missing_ranges)
        count = sum(len(f.samples) for f in source.fragments)
        self.assertLess(count, 2048)
        rig.persist()
        rig.ack()
        report = rig.finish()
        self.assertEqual(report.entries[0].kind, "partial")
        self.assertEqual(report.entries[0].sample_count, count)
        self.assertEqual(report.entries[0].partial.missing_ranges, source.missing_ranges)
        self.assertEqual(report.outcome.reason, "user_stop")

    def test_no_begin_abort_is_an_actual_event_with_no_fabricated_array(self):
        rig = Rig(self)
        rig.start()
        self.assertTrue(rig.link.peer.trigger(rig.link.now))
        rig.controller.stop(now_ms=rig.link.now)
        rig.settle_stop()
        self.assertIsNone(rig.runtime.incomplete_result)
        pending = rig.controller.snapshot().pending
        self.assertEqual(len(pending), 1)
        self.assertIs(type(pending[0].result.item), AbortEvent)
        self.assertIs(type(pending[0].result.source), Event)
        self.assertEqual(pending[0].result.item.provenance.abort_reason_code, 1)
        self.assertEqual(pending[0].result.item.provenance.wire_session_nonce, 101)
        rig.persist()
        rig.ack()
        report = rig.finish()
        self.assertEqual(report.entries[0].kind, "abort")
        self.assertIsNone(report.entries[0].sample_count)
        self.assertEqual(report.outcome.reason, "user_stop")
        self.assertEqual(report.entries[0].provenance.abort_reason_code, 1)


    def test_extra_full_and_partial_are_both_committed_in_order(self):
        rig = Rig(self)
        rig.start()
        capture = rig.capture()
        # This deliberately injected second terminal models ownership pressure;
        # firmware timing is tested by the separately owned native integration.
        from motortester.window import BeginMetadata, SampleBlock
        p = rig.provenance()
        begin = BeginMetadata(p.generation, capture.sequence + 1, p.config_id,
            1, 2048, 0, 0, 0, 0, 0, 0, 2048, 0, 72000000, 71, 19, 0)
        source = WindowResult(False, WindowCode.ABORT, begin, (SampleBlock(0, (4, 8)),),
                              ((2, 2048),), None, None, 1, (), False, ())
        item = PartialSegment(begin.window_id, 2048, (SampleFragment(0, (4, 8)),),
                              ((2, 2048),), "ABORT",
                              replace(p, terminal_kind="abort", abort_reason_code=1))
        result = RecordingResult(item, source)
        self.assertEqual(rig.controller.observe(rig.token, rig.runtime.snapshot(),
            results=(result,), now_ms=0), (True,))
        rig.settle_stop()
        rig.persist()
        rig.ack()
        self.assertEqual(rig.intent(K.PERSIST).payload.ordinal, 1)
        rig.persist()
        ack = rig.intent(K.ACK_LOCAL)
        rig.claim(ack)
        self.assertIs(ack.payload.result.source, source)
        rig.controller.acknowledged(ack, rig.runtime.snapshot(), now_ms=0)
        report = rig.finish()
        self.assertEqual([e.kind for e in report.entries], ["capture", "partial"])
        self.assertEqual(report.outcome.reason, "error")
        self.assertEqual(report.outcome.diagnostic, "extra_inflight_terminal")

    def test_commit_at_time_limit_can_only_unlock_local_ack(self):
        rig = Rig(self, limit=100)
        rig.start()
        rig.capture()
        rig.settle_stop()
        intent = rig.intent(K.PERSIST)
        rig.claim(intent)
        receipt = rig.spool.append_full(intent.payload.result.item)
        rig.link.now = 100
        rig.controller.committed(intent, receipt, now_ms=100)
        self.assertEqual(rig.controller.snapshot().termination_reason, "capacity")
        rig.ack()
        self.assertEqual([i.kind for i in rig.controller.intents], [K.FINALIZE_READY])

    def test_duplicate_receipt_and_claim_never_unlock_second_action(self):
        rig = Rig(self)
        rig.start()
        rig.capture()
        rig.settle_stop()
        intent, receipt = rig.persist()
        before = rig.controller.snapshot()
        self.assertFalse(rig.controller.committed(intent, receipt, now_ms=10000000))
        self.assertEqual(rig.controller.snapshot(), before)
        ack = rig.intent(K.ACK_LOCAL)
        rig.claim(ack)
        self.assertFalse(rig.controller.admit(ack, now_ms=10000000,
            runtime_state=rig.runtime.snapshot()))
        self.assertEqual(rig.controller.snapshot(), before)

    def test_faulted_stop_never_retries_or_claims_confirmed_quiescence(self):
        rig = Rig(self)
        rig.start()
        rig.capture()
        intent = rig.command(K.STOP)
        rig.controller.failed(intent, "unknown_write", now_ms=0)
        rig.controller.stop(now_ms=0)
        rig.controller.tick(now_ms=100)
        self.assertEqual(rig.controller.snapshot().stop_evidence, "unknown")
        self.assertFalse(rig.controller.intents)
        rig.link.now = 100
        rig.observe(rig.runtime.disconnect())
        self.assertEqual([i.kind for i in rig.controller.intents], [K.PERSIST])
        rig.persist()
        rig.ack()
        self.assertEqual(rig.intent(K.FINALIZE_READY).payload.stop_evidence, "unknown")

    def test_prepare_failure_never_configures_or_retries(self):
        rig = Rig(self)
        intent = rig.intent(K.PREPARE)
        rig.claim(intent)
        rig.controller.failed(intent, "permission_denied", now_ms=0)
        rig.controller.stop(now_ms=0)
        rig.controller.tick(now_ms=100)
        self.assertEqual(rig.controller.snapshot().phase, RecordingPhase.FAILED)
        self.assertFalse(rig.controller.intents)
        self.assertEqual(rig.controller.snapshot().pending, ())

    def test_finish_failure_retains_spool_and_does_not_retry_automatically(self):
        rig = Rig(self)
        rig.controller.stop(now_ms=0)
        rig.prepare()
        intent = rig.intent(K.FINALIZE_READY)
        rig.claim(intent)
        rig.controller.failed(intent, "finish_fsync_failed", now_ms=0)
        rig.controller.tick(now_ms=1)
        self.assertFalse(rig.controller.intents)
        self.assertEqual(rig.controller.snapshot().phase, RecordingPhase.FAILED)
        self.assertTrue(rig.spool.path.exists())

    def test_result_rejection_blocks_finalize_and_requires_source_retention(self):
        rig = Rig(self)
        rig.start()
        capture = rig.capture()
        invalid = replace(capture, sequence=capture.sequence + 1)
        wrong = RecordingResult(FullSegment(invalid,
            replace(rig.provenance(), generation=999)), invalid)
        self.assertEqual(rig.controller.observe(rig.token, rig.runtime.snapshot(),
            results=(wrong,), now_ms=0), (False,))
        rig.settle_stop()
        rig.persist()
        rig.ack()
        state = rig.controller.snapshot()
        self.assertTrue(state.source_retention_required)
        self.assertEqual(state.phase, RecordingPhase.FAILED)
        self.assertFalse(rig.controller.intents)
        self.assertIs(wrong.source, invalid)

    def test_recovery_without_result_still_requires_explicit_new_nonce(self):
        rig = Rig(self)
        rig.start()
        rig.observe(rig.runtime.disconnect())
        rig.finish()
        rig.link.peer = DeviceEmulator(all_analog_modes=True)
        rig.runtime.start(102, reset_confirmed=True)
        for _ in range(20):
            rig.runtime.pump()
            if rig.runtime.snapshot().phase is Phase.READY:
                break
        identity = replace(rig.identity, recording_id=str(UUID(int=1002)),
                           session_id=str(UUID(int=102)))
        token = rig.controller.record(identity, rig.config, rig.runtime.snapshot(), now_ms=1)
        self.assertIsNot(token, rig.token)
        self.assertEqual([i.kind for i in rig.controller.intents], [K.PREPARE])

    def test_no_arm_can_be_admitted_against_recovery_snapshot(self):
        rig = Rig(self)
        rig.prepare()
        configure = rig.intent(K.CONFIGURE)
        self.assertFalse(rig.controller.admit(configure, now_ms=0,
            runtime_state=replace(rig.runtime.snapshot(), phase=Phase.RECOVERY)))
        self.assertTrue(rig.controller.snapshot().stop_fence)
        self.assertFalse(any(i.kind in (K.CONFIGURE, K.ARM) for i in rig.controller.intents))


    def test_partial_known_source_metadata_cannot_be_substituted(self):
        mutations = {
            "reason": lambda item: replace(item, reason="fabricated_other_reason"),
            "generation": lambda item: replace(item, provenance=replace(item.provenance, generation=999)),
            "config_id": lambda item: replace(item, provenance=replace(item.provenance, config_id=999)),
            "nonce": lambda item: replace(item, provenance=replace(item.provenance, wire_session_nonce=102)),
            "config_bytes": lambda item: replace(item, provenance=replace(item.provenance,
                config_payload_hex=Config(level_code=123).payload().hex())),
            "mode": lambda item: replace(item, provenance=replace(item.provenance,
                acquisition_mode=0, auto_timeout_ms=100)),
            "abort_code": lambda item: replace(item, provenance=replace(item.provenance, abort_reason_code=2)),
            "terminal_kind": lambda item: replace(item, provenance=replace(item.provenance,
                terminal_kind="end", abort_reason_code=None)),
            "annotations": lambda item: replace(item, metadata_issues=("invented",)),
            "deferred": lambda item: replace(item, deferred_checks=("invented",)),
            "nominal_fs": lambda item: replace(item, nominal_fs_hz=49999),
            "future_host_time": lambda item: replace(item, provenance=replace(item.provenance, host_elapsed_ms=100)),
        }
        for name, mutate in mutations.items():
            with self.subTest(field=name):
                rig = Rig(self)
                rig.start()
                rig.link.read_limit = 700
                rig.link.peer.trigger(0)
                rig.pump()
                rig.controller.stop(now_ms=0)
                rig.command(K.STOP)
                for _ in range(50):
                    events = rig.runtime.pump()
                    if rig.runtime.incomplete_result is not None:
                        break
                    rig.observe(events)
                source = rig.runtime.incomplete_result
                self.assertIsNotNone(source)
                good = rig.available()[0]
                try:
                    bad = RecordingResult(mutate(good.item), source)
                except ValueError:
                    rig.controller.fault("invalid_result_conversion", now_ms=0,
                                         source_retention_required=True)
                    rig.controller.observe(rig.token, rig.runtime.snapshot(), events, now_ms=0)
                else:
                    self.assertEqual(rig.controller.observe(rig.token, rig.runtime.snapshot(),
                        events, (bad,), now_ms=0), (False,))
                self.assertTrue(rig.controller.snapshot().source_retention_required)
                self.assertIs(source, rig.runtime.incomplete_result)
                self.assertFalse(any(i.kind in (K.PERSIST, K.ACK_LOCAL, K.ARM, K.FINALIZE_READY)
                                     for i in rig.controller.intents))

    def test_partial_begin_settings_must_match_accepted_config(self):
        from motortester.window import BeginMetadata, SampleBlock
        rig = Rig(self)
        rig.start()
        p = replace(rig.provenance(), terminal_kind="abort", abort_reason_code=1)
        begin = BeginMetadata(p.generation, 1, p.config_id, 1, 2048, 0, 0,
                              0, 0, 0, 0, 2048, 0, 72000000, 71, 19, 0)
        for field, value in (("profile", 2), ("pretrigger_samples", 512), ("source", 1),
                             ("edge", 1), ("level_code", 123)):
            with self.subTest(field=field):
                source = WindowResult(False, WindowCode.ABORT, replace(begin, **{field: value}),
                    (SampleBlock(0, (4, 8)),), ((2, 2048),), None, None, 1, (), False, ())
                item = PartialSegment(1, 2048, (SampleFragment(0, (4, 8)),),
                                      ((2, 2048),), "ABORT", p)
                result = RecordingResult(item, source)
                self.assertEqual(rig.controller.observe(rig.token, rig.runtime.snapshot(),
                    results=(result,), now_ms=0), (False,))
                self.assertFalse(any(i.kind is K.PERSIST for i in rig.controller.intents))

    def test_semantically_rejected_full_result_retains_all_verified_fragments(self):
        import zlib
        import struct
        from motortester.window import BeginMetadata, SampleBlock
        rig = Rig(self)
        rig.start()
        p = rig.provenance()
        samples = tuple(range(2048))
        crc = zlib.crc32(struct.pack("<2048H", *samples))
        begin = BeginMetadata(p.generation, 1, p.config_id, 1, 2048, 0, 0,
                              0, 0, 0, 0, 2048, 0, 72000000, 71, 19, 0)
        source = WindowResult(True, WindowCode.COMPLETE, begin, (SampleBlock(0, samples),),
                              (), samples, crc, None, (), True, ("RAIL_FLAG_MISMATCH",))
        item = PartialSegment(1, 2048, (SampleFragment(0, samples),), (), "COMPLETE",
            replace(p, terminal_kind="end", end_samples_crc32=crc),
            metadata_issues=source.metadata_issues)
        result = RecordingResult(item, source)
        self.assertEqual(rig.controller.observe(rig.token, rig.runtime.snapshot(),
            results=(result,), now_ms=0), (True,))
        rig.settle_stop()
        rig.persist()
        ack = rig.intent(K.ACK_LOCAL)
        rig.claim(ack)
        rig.controller.acknowledged(ack, rig.runtime.snapshot(), now_ms=0)
        report = rig.finish()
        self.assertEqual(report.entries[0].kind, "partial")
        self.assertEqual(report.entries[0].sample_count, 2048)
        self.assertEqual(report.entries[0].partial.metadata_issues, ("RAIL_FLAG_MISMATCH",))
        self.assertEqual(report.outcome.reason, "error")

    def test_real_command_timeout_stays_unknown_and_never_retries(self):
        rig = Rig(self)
        rig.prepare()
        rig.command(K.CONFIGURE)
        rig.link.read_limit = 0
        rig.pump()  # Transmit request while withholding its reply.
        rig.link.now = 1000
        rig.pump()
        self.assertIs(rig.runtime.snapshot().phase, Phase.RECOVERY)
        self.assertTrue(rig.controller.snapshot().stop_fence)
        self.assertEqual(rig.controller.snapshot().stop_evidence, "unknown")
        self.assertEqual([i.kind for i in rig.controller.intents], [K.FINALIZE_READY])

    def test_repeated_stop_keeps_record_duration_and_intent_bounded(self):
        rig = Rig(self)
        rig.start()
        rig.controller.stop(now_ms=10)
        stop = rig.intent(K.STOP)
        for now in (20, 100, 1000, 60000):
            rig.controller.stop(now_ms=now)
            self.assertIs(rig.intent(K.STOP), stop)
            self.assertEqual(rig.controller.snapshot().elapsed_ms, 10)
            self.assertEqual(len(rig.controller.intents), 1)


    def test_no_begin_abort_writer_failure_keeps_exact_event(self):
        rig = Rig(self)
        rig.start()
        rig.link.peer.trigger(0)
        rig.controller.stop(now_ms=0)
        rig.settle_stop()
        writer = rig.intent(K.PERSIST)
        rig.claim(writer)
        source = writer.payload.result.source
        self.assertIs(type(source), Event)
        rig.controller.failed(writer, "index_fsync_failed", now_ms=0)
        self.assertIs(rig.controller.snapshot().pending[0].result.source, source)
        self.assertFalse(rig.controller.intents)
        self.assertEqual(rig.controller.snapshot().termination_reason, "error")

    def test_replayed_abort_callback_never_adds_duplicate_event(self):
        rig = Rig(self)
        rig.start()
        rig.link.peer.trigger(0)
        rig.controller.stop(now_ms=0)
        rig.settle_stop()
        source = rig.controller.snapshot().pending[0].result.source
        rig.controller.observe(rig.token, rig.runtime.snapshot(), (source,), now_ms=0)
        self.assertEqual(len(rig.controller.snapshot().pending), 1)
        rig.persist()
        rig.ack()
        rig.controller.observe(rig.token, rig.runtime.snapshot(), (source,), now_ms=0)
        self.assertEqual(rig.controller.snapshot().pending, ())
        report = rig.finish()
        self.assertEqual(len(report.entries), 1)


    def final_ready(self, reason):
        rig = Rig(self, 2 if reason == "single_complete" else 1, limit=100)
        rig.start()
        rig.capture()
        rig.settle_stop()
        rig.persist()
        rig.ack()
        if reason == "user_stop":
            rig.controller.stop(now_ms=0)
        elif reason == "capacity":
            rig.link.now = 100
            rig.controller.tick(now_ms=100)
        intent = rig.intent(K.FINALIZE_READY)
        self.assertEqual(intent.payload.reason, reason)
        return rig, intent

    def change_final_state(self, rig, change):
        if change == "recovery":
            rig.observe(rig.runtime.disconnect())
        else:
            rig.controller.fault("late_controller_fault", now_ms=rig.link.now,
                                 source_retention_required=change == "source_pending")

    def test_unclaimed_finalization_rebinds_after_fault_or_recovery(self):
        for reason in ("user_stop", "single_complete", "capacity"):
            for change in ("fault", "recovery"):
                with self.subTest(reason=reason, change=change):
                    rig, old = self.final_ready(reason)
                    self.change_final_state(rig, change)
                    before = rig.controller.snapshot()
                    self.assertFalse(rig.controller.admit(old, now_ms=10000000,
                        runtime_state=rig.runtime.snapshot()))
                    self.assertFalse(rig.controller.finalized(old, now_ms=10000000))
                    self.assertFalse(rig.controller.failed(old, "stale_callback", now_ms=10000000))
                    self.assertEqual(rig.controller.snapshot(), before)
                    fresh = rig.intent(K.FINALIZE_READY)
                    self.assertIsNot(fresh, old)
                    self.assertEqual(fresh.payload.reason, "error")
                    self.assertEqual(fresh.payload.stop_evidence,
                                     "unknown" if change == "recovery" else "stop_ack")
                    self.assertFalse(before.finalization_invalidated)
                    report = rig.finish()
                    path = Path(rig.folder.name) / "honest-terminal.mtrec"
                    finalize(report, path)
                    self.assertEqual(load(path).outcome, fresh.payload)
                    self.assertEqual(load(path).read_segment(0), encode(rig.captures[0]))

    def test_claimed_finalization_invalidates_before_or_after_durable_finish(self):
        for reason in ("user_stop", "single_complete", "capacity"):
            for change in ("fault", "recovery", "source_pending"):
                for disk_first in (False, True):
                    with self.subTest(reason=reason, change=change, disk_first=disk_first):
                        rig, old = self.final_ready(reason)
                        rig.claim(old)
                        self.assertTrue(rig.controller.snapshot().writer_pending)
                        if disk_first:
                            report = rig.spool.finish(old.payload.reason,
                                old.payload.stop_evidence, old.payload.diagnostic)
                        self.change_final_state(rig, change)
                        state = rig.controller.snapshot()
                        self.assertEqual(state.phase, RecordingPhase.FAILED)
                        self.assertTrue(state.finalization_invalidated)
                        self.assertTrue(state.writer_pending)
                        self.assertEqual(state.termination_reason, "error")
                        self.assertFalse(rig.controller.intents)
                        # A copied callback cannot settle the actual old writer.
                        self.assertFalse(rig.controller.finalized(replace(old), now_ms=10000000))
                        self.assertTrue(rig.controller.snapshot().writer_pending)
                        if not disk_first:
                            report = rig.spool.finish(old.payload.reason,
                                old.payload.stop_evidence, old.payload.diagnostic)
                        self.assertFalse(rig.controller.finalized(old, now_ms=10000000))
                        self.assertFalse(rig.controller.snapshot().writer_pending)
                        self.assertTrue(rig.controller.snapshot().finalization_invalidated)
                        settled = rig.controller.snapshot()
                        self.assertFalse(rig.controller.failed(old, "late_callback", now_ms=10000000))
                        self.assertEqual(rig.controller.snapshot(), settled)
                        self.assertEqual(recover(rig.spool.path).outcome, report.outcome)
                        self.assertEqual(len(recover(rig.spool.path).entries), 1)
                        rig.controller.tick(now_ms=rig.link.now + 1000)
                        self.assertFalse(rig.controller.intents)
                        with self.assertRaises(RecordingError):
                            rig.controller.record(replace(rig.identity,
                                recording_id=str(UUID(int=1002))), rig.config,
                                rig.runtime.snapshot(), now_ms=rig.link.now + 1000)

    def test_unclaimed_source_pending_revokes_handoff_without_replacement(self):
        for reason in ("user_stop", "single_complete", "capacity"):
            with self.subTest(reason=reason):
                rig, old = self.final_ready(reason)
                self.change_final_state(rig, "source_pending")
                self.assertFalse(rig.controller.admit(old, now_ms=rig.link.now,
                    runtime_state=rig.runtime.snapshot()))
                self.assertFalse(rig.controller.finalized(old, now_ms=rig.link.now))
                state = rig.controller.snapshot()
                self.assertTrue(state.source_retention_required)
                self.assertFalse(state.finalization_invalidated)
                self.assertEqual(state.phase, RecordingPhase.FAILED)
                self.assertFalse(rig.controller.intents)
                self.assertEqual(len(recover(rig.spool.path).entries), 1)

    def test_late_result_revokes_unclaimed_or_invalidates_claimed_finalization(self):
        for claimed in (False, True):
            with self.subTest(claimed=claimed):
                rig, old = self.final_ready("user_stop")
                if claimed:
                    rig.claim(old)
                capture = replace(rig.captures[0], sequence=rig.captures[0].sequence + 1)
                result = RecordingResult(FullSegment(capture, rig.provenance()), capture)
                accepted = rig.controller.observe(rig.token, rig.runtime.snapshot(),
                                                 results=(result,), now_ms=rig.link.now)
                self.assertEqual(accepted, (not claimed,))
                self.assertFalse(rig.controller.finalized(old, now_ms=rig.link.now))
                if claimed:
                    state = rig.controller.snapshot()
                    self.assertTrue(state.source_retention_required)
                    self.assertTrue(state.finalization_invalidated)
                    self.assertIs(result.source, capture)  # Caller still owns the rejected result.
                    self.assertFalse(rig.controller.intents)
                else:
                    self.assertIs(rig.intent(K.PERSIST).payload.result.source, capture)
                    rig.persist()
                    ack = rig.intent(K.ACK_LOCAL)
                    rig.claim(ack)
                    rig.controller.acknowledged(ack, rig.runtime.snapshot(), now_ms=rig.link.now)
                    fresh = rig.intent(K.FINALIZE_READY)
                    self.assertIsNot(fresh, old)
                    self.assertEqual(fresh.payload.reason, "error")
                    self.assertEqual(len(rig.finish().entries), 2)

    def test_finalization_blocked_in_actual_sync_rejects_late_recovery(self):
        import threading
        from motortester import session_archive as archive
        rig, old = self.final_ready("user_stop")
        rig.claim(old)
        entered, release = threading.Event(), threading.Event()
        reports, errors = [], []
        sync = archive._sync

        def blocked_sync(stream):
            entered.set()
            if not release.wait(5):
                raise OSError("test worker was not released")
            sync(stream)

        def finish_worker():
            try:
                reports.append(rig.spool.finish(old.payload.reason,
                    old.payload.stop_evidence, old.payload.diagnostic))
            except BaseException as exc:
                errors.append(exc)

        with patch.object(archive, "_sync", side_effect=blocked_sync):
            worker = threading.Thread(target=finish_worker)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                rig.observe(rig.runtime.disconnect())
                rig.controller.stop(now_ms=0)
                rig.controller.tick(now_ms=6000)
                self.assertTrue(rig.controller.snapshot().finalization_invalidated)
                self.assertTrue(rig.controller.snapshot().writer_pending)
                self.assertFalse(rig.controller.intents)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(reports), 1)
        self.assertFalse(rig.controller.finalized(old, now_ms=6000))
        self.assertEqual(rig.controller.snapshot().phase, RecordingPhase.FAILED)
        self.assertFalse(rig.controller.snapshot().writer_pending)
        self.assertEqual(reports[0].outcome.reason, "user_stop")
        self.assertEqual(rig.controller.snapshot().termination_reason, "error")
        self.assertEqual(rig.controller.snapshot().stop_evidence, "unknown")
        self.assertEqual(len(recover(rig.spool.path).entries), 1)

    def test_failure_callback_settles_invalidated_finish_without_accepting_it(self):
        rig, old = self.final_ready("user_stop")
        rig.claim(old)
        self.change_final_state(rig, "recovery")
        self.assertFalse(rig.controller.failed(replace(old), "copied_failure", now_ms=0))
        self.assertTrue(rig.controller.snapshot().writer_pending)
        self.assertFalse(rig.controller.failed(old, "old_finish_failed", now_ms=10000000))
        self.assertFalse(rig.controller.snapshot().writer_pending)
        self.assertTrue(rig.controller.snapshot().finalization_invalidated)
        self.assertFalse(rig.controller.intents)
        self.assertEqual(len(recover(rig.spool.path).entries), 1)

    def test_successful_finalized_acceptance_is_the_record_closing_boundary(self):
        rig, _ = self.final_ready("user_stop")
        report = rig.finish()
        before = rig.controller.snapshot()
        rig.observe(rig.runtime.disconnect())
        rig.controller.fault("later_transport_incident", now_ms=1)
        self.assertEqual(rig.controller.snapshot(), before)
        self.assertEqual(before.phase, RecordingPhase.FINISHED)
        self.assertEqual(report.outcome.reason, before.termination_reason)
        self.assertEqual(report.outcome.stop_evidence, before.stop_evidence)

    def test_repeated_stop_and_later_time_guard_do_not_supersede_same_outcome(self):
        rig, old = self.final_ready("user_stop")
        rig.controller.stop(now_ms=100)
        rig.controller.tick(now_ms=1000)
        self.assertIs(rig.intent(K.FINALIZE_READY), old)
        self.assertFalse(rig.controller.snapshot().finalization_invalidated)
        rig.link.now = 1000
        rig.finish()


if __name__ == "__main__":
    unittest.main()

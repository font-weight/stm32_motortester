"""Synthetic serialized-event tests. No wire, Qt, COM port or device is used."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import struct
import unittest

from motortester.stream_model import (FailureEvidence, StreamBlock, StreamError,
                                     StreamIdentity, StreamTerminal, U64_MAX)
from motortester.stream_session import (JobCompletion, JobFailure, SessionLimits, SourceEnd,
                                       SourceStarted, StopAcknowledgement, StreamSession)


IDENTITY = StreamIdentity("synthetic-session", 7, 3, 50_000, "synthetic")
TOKEN = "0123456789abcdef0123456789abcdef"


def block(first, count=2, identity=IDENTITY):
    return StreamBlock(identity, first, struct.pack("<" + "H" * count,
                                                 *(i % 65536 for i in range(first, first + count))))


def started(session, first=0):
    action = session.take_control_action()
    if action is None or action.kind != "start":
        raise RuntimeError("missing Start intent")
    session.source_started(SourceStarted(action.attempt_id, action.action_id,
                                         action.identity, first))
    return action


def completed(session, job=None, checkpointed=None):
    job = session.next_storage_job() if job is None else job
    if job is None:
        raise RuntimeError("missing storage job")
    expected = job.expected_accepted_end_sample_index
    if checkpointed is None and expected is not None:
        state = session.snapshot()
        checkpointed = (expected if job.kind in ("checkpoint", "finish") else
                        state.checkpointed_end_sample_index if state.checkpointed_end_sample_index is not None
                        else state.selected_start_sample_index)
    result = JobCompletion.for_job(job, token_id=TOKEN, accepted_end=expected,
                                   checkpointed_end=checkpointed)
    session.storage_completed(result)
    return job, result


def ready_preview(first=0, limits=SessionLimits()):
    session = StreamSession(IDENTITY, "attempt-1", limits)
    session.preview()
    started(session, first)
    session.record("recording-1")
    completed(session)
    return session


def ready_direct(first=0, limits=SessionLimits()):
    session = StreamSession(IDENTITY, "attempt-1", limits)
    session.record("recording-1")
    completed(session)
    started(session, first)
    return session


def stopping(session):
    session.stop()
    action = session.take_control_action()
    if action is None or action.kind != "stop":
        raise RuntimeError("missing Stop intent")
    return action


def end(session, end_index, *, failure=None, terminal_id="end-1", request_id=91):
    value = StreamTerminal(IDENTITY, end_index, "stopped" if failure is None else "source_failure",
                           True, end_index, end_index, 0, "synthetic_validated_terminal",
                           failure, request_id is not None, request_id)
    session.source_end(SourceEnd(terminal_id, value))


def ack(session, action, end_index, *, terminal_id="end-1", request_id=91, quiescence=True):
    session.stop_acknowledged(StopAcknowledgement(action.attempt_id, action.action_id,
                                                 action.identity, terminal_id, end_index,
                                                 request_id, quiescence))


def drain_jobs(session):
    raw = bytearray()
    kinds = []
    for _ in range(10000):
        job = session.next_storage_job()
        if job is None:
            return bytes(raw), kinds
        kinds.append(job.kind)
        if job.block is not None:
            raw.extend(job.block.raw_u16le)
        completed(session, job)
    raise RuntimeError("unbounded job production")


class LifecycleTests(unittest.TestCase):
    def test_construction_does_not_acquire_or_prepare(self):
        s = StreamSession(IDENTITY, "attempt")
        self.assertIsNone(s.take_control_action())
        self.assertIsNone(s.next_storage_job())
        self.assertFalse(s.snapshot().source_started)
        self.assertIsNone(s.snapshot().selected_start_sample_index)

    def test_direct_prepare_then_known_start_then_empty_end(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.record("recording-1")
        self.assertIsNone(s.take_control_action())
        job = s.next_storage_job()
        self.assertEqual(job.kind, "prepare")
        self.assertIsNone(job.start)
        self.assertIsNone(job.expected_accepted_end_sample_index)
        completed(s, job)
        started(s, 0)
        self.assertEqual(s.snapshot().selected_start_sample_index, 0)
        self.assertIsNone(s.snapshot().accepted_end_sample_index)
        completed(s)
        stop = stopping(s)
        end(s, 0)
        self.assertIsNone(s.next_storage_job())
        ack(s, stop, 0)
        job, _ = completed(s)
        self.assertEqual(job.kind, "finish")
        self.assertEqual(job.terminal.end_sample_index, 0)
        self.assertTrue(s.snapshot().clean_completion_reported)
        self.assertEqual(s.snapshot().finalized_end_sample_index, 0)

    def test_known_start_is_explicit_and_can_be_above_float_precision(self):
        first = 2 ** 53 + 17
        s = ready_direct(first)
        completed(s)
        s.receive(block(first, 3))
        stop = stopping(s)
        end(s, first + 3)
        ack(s, stop, first + 3)
        raw, _ = drain_jobs(s)
        self.assertEqual(raw, block(first, 3).raw_u16le)
        self.assertEqual(s.snapshot().selected_start_sample_index, first)
        self.assertEqual(s.snapshot().finalized_end_sample_index, first + 3)

    def test_failed_direct_start_never_invents_r(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.record("recording-1")
        completed(s)
        action = s.take_control_action()
        self.assertEqual(action.kind, "start")
        s.drain_failed(FailureEvidence("source_error", "START failed"))
        job, _ = completed(s)
        self.assertEqual(job.kind, "finish_unbound")
        self.assertEqual(job.unbound.reason, "failed_unbound")
        self.assertIsNone(s.snapshot().selected_start_sample_index)
        self.assertIsNone(s.snapshot().finalized_end_sample_index)
        self.assertFalse(s.snapshot().clean_completion_reported)

    def test_preview_does_not_retroactively_include_history(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.preview()
        started(s)
        s.receive(block(0, 2))
        s.record("recording-1")
        prepare = s.next_storage_job()
        s.receive(block(2, 2))
        self.assertIsNone(s.snapshot().selected_start_sample_index)
        completed(s, prepare)
        s.receive(block(4, 3))
        self.assertEqual(s.snapshot().selected_start_sample_index, 4)
        stop = stopping(s)
        end(s, 7)
        ack(s, stop, 7)
        raw, kinds = drain_jobs(s)
        self.assertEqual(raw, block(4, 3).raw_u16le)
        self.assertEqual(kinds, ["bind_first_block", "finish"])

    def test_end_wins_before_ready_and_before_data(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                s = StreamSession(IDENTITY, "attempt-1")
                s.preview()
                started(s)
                s.record("recording-1")
                prepare = s.next_storage_job()
                if ready:
                    completed(s, prepare)
                stop = stopping(s)
                end(s, 0)
                ack(s, stop, 0)
                if not ready:
                    completed(s, prepare)
                job, _ = completed(s)
                self.assertEqual(job.kind, "finish_unbound")
                self.assertEqual(job.unbound.reason, "ended_unbound")
                self.assertIsNone(s.snapshot().selected_start_sample_index)
                self.assertIsNone(s.snapshot().accepted_end_sample_index)
                with self.assertRaises(StreamError):
                    s.receive(block(0))

    def test_data_wins_then_end_while_bind_is_delayed(self):
        s = ready_preview()
        s.receive(block(0, 2))
        bind = s.next_storage_job()
        s.receive(block(2, 2))
        stop = stopping(s)
        end(s, 4)
        ack(s, stop, 4)
        self.assertIsNone(s.snapshot().accepted_end_sample_index)
        self.assertEqual(s.snapshot().source_end.terminal.end_sample_index, 4)
        self.assertIsNone(s.next_storage_job())
        completed(s, bind)
        second = s.next_storage_job()
        self.assertEqual(second.kind, "append")
        completed(s, second)
        finish = s.next_storage_job()
        self.assertEqual(finish.terminal.end_sample_index, 4)
        completed(s, finish)
        self.assertTrue(s.snapshot().clean_completion_reported)

    def test_stop_before_selection_does_not_select_drain_data(self):
        s = ready_preview()
        stop = stopping(s)
        s.receive(block(0, 2))
        self.assertIsNone(s.snapshot().selected_start_sample_index)
        end(s, 2)
        ack(s, stop, 2)
        job, _ = completed(s)
        self.assertEqual(job.kind, "finish_unbound")
        self.assertIsNone(job.expected_accepted_end_sample_index)

    def test_record_during_starting_preview_stays_next_block_policy(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.preview()
        s.record("recording-1")
        prepare, _ = completed(s)
        self.assertEqual(prepare.start_policy, "next_block")
        started(s)
        self.assertIsNone(s.snapshot().selected_start_sample_index)
        s.receive(block(0))
        self.assertEqual(s.next_storage_job().kind, "bind_first_block")

    def test_cancel_before_prepare_or_after_ready_never_starts(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                s = StreamSession(IDENTITY, "attempt-1")
                s.record("recording-1")
                if ready:
                    completed(s)
                s.cancel()
                if not ready:
                    completed(s)
                self.assertIsNone(s.take_control_action())
                job, _ = completed(s)
                self.assertEqual(job.unbound.reason, "cancelled_unbound")
                self.assertIsNone(s.snapshot().selected_start_sample_index)
                self.assertFalse(s.snapshot().clean_completion_reported)

    def test_cancel_after_start_dispatched_preserves_no_selection(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.record("recording-1")
        completed(s)
        action = s.take_control_action()
        s.cancel()
        s.source_started(SourceStarted(action.attempt_id, action.action_id, action.identity, 0))
        self.assertIsNone(s.snapshot().selected_start_sample_index)
        stop = s.take_control_action()
        end(s, 0)
        ack(s, stop, 0)
        job, _ = completed(s)
        self.assertEqual(job.unbound.reason, "cancelled_unbound")

    def test_cancel_after_selection_preserves_prefix_and_failure(self):
        s = ready_direct()
        completed(s)
        s.receive(block(0))
        s.cancel()
        stop = s.take_control_action()
        end(s, 2)
        ack(s, stop, 2)
        raw, _ = drain_jobs(s)
        self.assertEqual(raw, block(0).raw_u16le)
        self.assertEqual(s.snapshot().first_failure.detail, "recording cancelled after selection")
        self.assertFalse(s.snapshot().clean_completion_reported)

    def test_no_restart_or_automatic_rearm(self):
        s = ready_direct()
        completed(s)
        stop = stopping(s)
        end(s, 0)
        ack(s, stop, 0)
        completed(s)
        for method in (s.preview, lambda: s.record("second"), s.stop):
            with self.assertRaises(StreamError):
                method()
        self.assertIsNone(s.take_control_action())
        self.assertIsNone(s.next_storage_job())
        self.assertEqual(s.snapshot().finalized_end_sample_index, 0)
        self.assertTrue(s.snapshot().clean_completion_reported)


class BoundaryTests(unittest.TestCase):
    def test_received_queued_accepted_checkpointed_finalized_are_distinct(self):
        s = ready_direct(100)
        self.assertIsNone(s.snapshot().accepted_end_sample_index)
        completed(s)
        s.receive(block(100, 2))
        s.receive(block(102, 2))
        snap = s.snapshot()
        self.assertEqual((snap.received_end_sample_index, snap.queued_end_sample_index,
                          snap.accepted_end_sample_index, snap.checkpointed_end_sample_index,
                          snap.finalized_end_sample_index), (104, 104, 100, 100, None))
        completed(s)
        s.request_checkpoint()
        job, _ = completed(s)
        self.assertEqual(job.kind, "checkpoint")
        self.assertEqual(s.snapshot().checkpointed_end_sample_index, 102)
        self.assertEqual(s.snapshot().raw_items_held, 1)
        completed(s)
        self.assertEqual(s.snapshot().accepted_end_sample_index, 104)
        self.assertEqual(s.snapshot().checkpointed_end_sample_index, 102)
        stop = stopping(s)
        end(s, 104)
        ack(s, stop, 104)
        completed(s)
        self.assertEqual(s.snapshot().finalized_end_sample_index, 104)

    def test_no_per_data_checkpoint_job(self):
        s = ready_direct()
        completed(s)
        for i in range(30):
            s.receive(block(2 * i))
            job, _ = completed(s)
            self.assertEqual(job.kind, "append")
            self.assertEqual(s.snapshot().checkpointed_end_sample_index, 0)
        self.assertIsNone(s.next_storage_job())

    def test_auto_checkpoint_completion_may_report_previous_accepted_or_current_end(self):
        for checkpoint in (2, 4):
            with self.subTest(checkpoint=checkpoint):
                s = ready_direct()
                completed(s)
                s.receive(block(0))
                completed(s)
                s.receive(block(2))
                completed(s, checkpointed=checkpoint)
                self.assertEqual(s.snapshot().checkpointed_end_sample_index, checkpoint)

    def test_end_alone_and_ack_alone_never_finalize(self):
        for ack_first in (False, True):
            with self.subTest(ack_first=ack_first):
                s = ready_direct()
                completed(s)
                stop = stopping(s)
                if ack_first:
                    ack(s, stop, 0)
                else:
                    end(s, 0)
                self.assertIsNone(s.next_storage_job())
                self.assertFalse(s.snapshot().finalized)
                if ack_first:
                    end(s, 0)
                else:
                    ack(s, stop, 0)
                job = s.next_storage_job()
                self.assertEqual(job.kind, "finish")
                self.assertFalse(s.snapshot().finalized)
                completed(s, job)
                self.assertTrue(s.snapshot().clean_completion_reported)

    def test_ack_must_match_action_terminal_reference_and_endpoint(self):
        for field, bad in (("attempt_id", "other"), ("stop_action_id", 9),
                           ("terminal_id", "other"), ("end_sample_index", 8),
                           ("request_id", 92), ("identity", replace(IDENTITY, generation=8))):
            with self.subTest(field=field):
                s = ready_direct()
                completed(s)
                stop = stopping(s)
                end(s, 0)
                good = StopAcknowledgement(stop.attempt_id, stop.action_id, stop.identity,
                                           "end-1", 0, 91, True)
                with self.assertRaises(StreamError):
                    s.stop_acknowledged(replace(good, **{field: bad}))
                self.assertFalse(s.snapshot().clean_completion_reported)
                self.assertEqual(s.snapshot().first_failure.code, "invalid_terminal")

    def test_quiescence_false_or_timeout_keeps_incomplete_result(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                s = ready_direct()
                completed(s)
                stop = stopping(s)
                end(s, 0)
                if timeout:
                    s.drain_failed(FailureEvidence("transport_error", "bounded ACK wait expired"))
                else:
                    ack(s, stop, 0, quiescence=False)
                job, _ = completed(s)
                self.assertEqual(job.kind, "finish")
                self.assertIsNotNone(job.terminal.failure)
                self.assertFalse(s.snapshot().clean_completion_reported)

    def test_partition_invariant_saved_bytes_indices_and_digest(self):
        expected = block(13, 8).raw_u16le
        for cuts in range(1 << 7):
            s = ready_direct(13)
            completed(s)
            first = 13
            for index in range(14, 22):
                if index == 21 or cuts & (1 << (index - 14)):
                    s.receive(block(first, index - first))
                    first = index
            stop = stopping(s)
            end(s, 21)
            ack(s, stop, 21)
            raw = bytearray()
            while True:
                job = s.next_storage_job()
                if job is None:
                    break
                if job.block is not None:
                    raw.extend(job.block.raw_u16le)
                if job.kind == "finish":
                    self.assertEqual(job.prefix_sha256, hashlib.sha256(expected).hexdigest())
                completed(s, job)
            self.assertEqual(bytes(raw), expected)
            self.assertEqual(s.snapshot().selected_start_sample_index, 13)
            self.assertEqual(s.snapshot().finalized_end_sample_index, 21)
            self.assertTrue(s.snapshot().clean_completion_reported)


class FenceTests(unittest.TestCase):
    def test_inflight_raw_stays_inside_byte_and_item_bounds(self):
        for limits, code in ((SessionLimits(4, 8), "capacity_raw"),
                             (SessionLimits(100, 1), "capacity_blocks")):
            with self.subTest(limits=limits):
                s = ready_preview(limits=limits)
                s.receive(block(0))
                bind = s.next_storage_job()
                self.assertEqual(s.snapshot().raw_bytes_held, 4)
                self.assertEqual(s.snapshot().raw_items_held, 1)
                with self.assertRaises(StreamError):
                    s.receive(block(2))
                snap = s.snapshot()
                self.assertEqual(snap.first_failure.code, code)
                self.assertEqual(snap.received_end_sample_index, 4)
                self.assertEqual(snap.queued_end_sample_index, 2)
                self.assertEqual(snap.raw_items_held, 1)
                stop = s.take_control_action()
                self.assertEqual(stop.kind, "stop")
                self.assertIsNone(s.take_control_action())
                end(s, 8)
                ack(s, stop, 8)
                completed(s, bind)
                finish, _ = completed(s)
                self.assertEqual(finish.terminal.end_sample_index, 2)
                self.assertEqual(finish.terminal.device_transmitted_end_sample_index, 8)
                self.assertEqual(finish.terminal.loss_samples, 0)
                self.assertEqual(finish.terminal.failure.code, code)
                self.assertFalse(s.snapshot().clean_completion_reported)

    def test_oversized_first_selection_does_not_establish_r(self):
        s = ready_preview(limits=SessionLimits(2, 1))
        with self.assertRaises(StreamError):
            s.receive(block(0, 2))
        self.assertIsNone(s.snapshot().selected_start_sample_index)
        self.assertEqual(s.snapshot().raw_items_held, 0)
        s.drain_failed(FailureEvidence("transport_error", "later timeout"))
        job, _ = completed(s)
        self.assertEqual(job.kind, "finish_unbound")
        self.assertEqual(job.unbound.failure.code, "capacity_raw")

    def test_first_fault_survives_end_ack_and_storage_failure(self):
        s = ready_direct()
        completed(s)
        s.receive(block(0))
        job = s.next_storage_job()
        first = FailureEvidence("source_overrun", "first source fault")
        s.fault(first)
        stop = s.take_control_action()
        end(s, 2)
        ack(s, stop, 2)
        failure = JobFailure.for_job(job, FailureEvidence("host_io", "later disk error"))
        s.storage_failed(failure)
        self.assertEqual(s.snapshot().first_failure, first)
        self.assertTrue(s.snapshot().writer_fenced)
        self.assertIsNone(s.next_storage_job())
        self.assertFalse(s.snapshot().finalized)
        self.assertEqual(s.snapshot().accepted_end_sample_index, 0)

    def test_finish_freezes_source_scope_but_waits_for_storage_result(self):
        s = ready_direct()
        completed(s)
        stop = stopping(s)
        end(s, 0)
        ack(s, stop, 0)
        job = s.next_storage_job()
        self.assertTrue(s.snapshot().finalization_started)
        for late in (lambda: s.fault(FailureEvidence("transport_error", "new unrelated transport fault")),
                     s.cancel, lambda: end(s, 0), lambda: ack(s, stop, 0),
                     lambda: s.receive(block(0))):
            with self.assertRaises(StreamError):
                late()
        self.assertIsNone(s.snapshot().first_failure)
        self.assertFalse(s.snapshot().writer_fenced)
        self.assertFalse(s.snapshot().finalized)
        completed(s, job)
        self.assertTrue(s.snapshot().clean_completion_reported)

    def test_genuine_finish_failure_settles_pending_without_success(self):
        s = ready_direct()
        completed(s)
        stop = stopping(s)
        end(s, 0)
        ack(s, stop, 0)
        job = s.next_storage_job()
        s.storage_failed(JobFailure.for_job(job, FailureEvidence("host_io", "finish fsync failed")))
        self.assertIsNone(s.snapshot().pending_operation_id)
        self.assertTrue(s.snapshot().finalization_unresolved)
        self.assertFalse(s.snapshot().finalized)
        self.assertIsNone(s.snapshot().finalized_end_sample_index)
        with self.assertRaises(StreamError):
            completed(s, job)
        self.assertEqual(s.snapshot().first_failure.detail, "finish fsync failed")

    def test_identity_order_duplicate_and_data_after_end_fail_closed(self):
        for bad in (block(0, identity=replace(IDENTITY, generation=8)),
                    block(0, identity=replace(IDENTITY, nominal_fs_hz=25_000)),
                    block(0, identity=replace(IDENTITY, session_id="new-source")),
                    block(1)):
            with self.subTest(bad=bad):
                s = ready_preview()
                with self.assertRaises(StreamError):
                    s.receive(bad)
                self.assertTrue(s.snapshot().admission_fenced)
                self.assertIsNone(s.snapshot().selected_start_sample_index)
        s = ready_preview()
        s.receive(block(0))
        with self.assertRaises(StreamError):
            s.receive(block(0))
        self.assertEqual(s.snapshot().received_end_sample_index, 2)

    def test_nonfenced_terminal_must_match_received_even_when_writer_lags(self):
        s = ready_preview()
        s.receive(block(0, 4))
        stopping(s)
        with self.assertRaises(StreamError):
            end(s, 2)
        self.assertIsNone(s.snapshot().source_end)
        self.assertEqual(s.snapshot().first_failure.code, "invalid_terminal")

    def test_preselection_fault_and_data_race_is_serial_and_first_cause_sticks(self):
        first = FailureEvidence("source_error", "early source failure")
        for data_first in (False, True):
            s = ready_preview()
            if data_first:
                s.receive(block(0))
            s.fault(first)
            if not data_first:
                with self.assertRaises(StreamError):
                    s.receive(block(0))
            stop = s.take_control_action()
            end(s, 2 if data_first else 0, failure=first)
            ack(s, stop, 2 if data_first else 0)
            raw, kinds = drain_jobs(s)
            self.assertEqual(s.snapshot().first_failure, first)
            self.assertEqual(raw, block(0).raw_u16le if data_first else b"")
            self.assertEqual(kinds[-1], "finish" if data_first else "finish_unbound")
            self.assertEqual(s.snapshot().selected_start_sample_index, 0 if data_first else None)

    def test_hard_caps_and_reserved_terminal_at_capacity(self):
        for limits, count, blocks, expected_code in (
                (SessionLimits(1024 * 1024, 4096), 8192, 64, "capacity_raw"),
                (SessionLimits(1024 * 1024, 4096), 1, 4096, "capacity_blocks")):
            with self.subTest(count=count):
                s = ready_preview(limits=limits)
                for i in range(blocks):
                    s.receive(block(i * count, count))
                bind = s.next_storage_job()
                with self.assertRaises(StreamError):
                    s.receive(block(blocks * count, count))
                self.assertEqual(s.snapshot().raw_items_held, blocks)
                self.assertEqual(s.snapshot().raw_bytes_held, blocks * count * 2)
                self.assertEqual(s.snapshot().first_failure.code, expected_code)
                stop = s.take_control_action()
                self.assertEqual(stop.kind, "stop")
                end(s, (blocks + 1) * count)
                ack(s, stop, (blocks + 1) * count)
                completed(s, bind)
                raw, kinds = drain_jobs(s)
                self.assertEqual(len(raw), (blocks - 1) * count * 2)
                self.assertEqual(kinds[-1], "finish")
                self.assertEqual(s.snapshot().finalized_end_sample_index, blocks * count)
                self.assertFalse(s.snapshot().clean_completion_reported)

    def test_unsolicited_source_failure_then_late_stop_ack_preserves_cause(self):
        s = ready_preview()
        s.receive(block(0))
        first = FailureEvidence("source_overrun", "unsolicited source END")
        end(s, 2, failure=first, request_id=None)
        stop = s.take_control_action()
        ack(s, stop, 2)
        raw, kinds = drain_jobs(s)
        self.assertEqual(raw, block(0).raw_u16le)
        self.assertEqual(kinds[-1], "finish")
        self.assertEqual(s.snapshot().first_failure, first)
        self.assertFalse(s.snapshot().clean_completion_reported)

    def test_absent_terminal_keeps_unknown_tail_and_exact_prefix(self):
        s = ready_direct(25)
        completed(s)
        s.receive(block(25, 4))
        completed(s)
        s.drain_failed(FailureEvidence("transport_error", "no terminal before bounded deadline"))
        finish, _ = completed(s)
        self.assertEqual(finish.terminal.end_sample_index, 29)
        self.assertFalse(finish.terminal.terminal_observed)
        self.assertIsNone(finish.terminal.loss_samples)
        self.assertIsNone(finish.terminal.device_generated_end_sample_index)
        self.assertIsNone(finish.terminal.device_transmitted_end_sample_index)
        self.assertFalse(s.snapshot().clean_completion_reported)

    def test_preview_only_keeps_one_display_block(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.preview()
        started(s)
        for i in range(1000):
            s.receive(block(2 * i))
        snap = s.snapshot()
        self.assertEqual(snap.raw_bytes_held, 0)
        self.assertEqual(snap.raw_items_held, 0)
        self.assertEqual(snap.latest_display.first_sample_index, 1998)
        self.assertIsNone(s.next_storage_job())


class CompletionTests(unittest.TestCase):
    def test_failed_prepare_has_no_token_or_r(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.record("recording-1")
        job = s.next_storage_job()
        failure = JobFailure.for_job(job, FailureEvidence("host_io", "prepare fsync failed"))
        self.assertIsNone(failure.token_id)
        s.storage_failed(failure)
        self.assertTrue(s.snapshot().writer_fenced)
        self.assertIsNone(s.snapshot().selected_start_sample_index)
        self.assertIsNone(s.take_control_action())
        self.assertIsNone(s.next_storage_job())
        with self.assertRaises(StreamError):
            completed(s, job)

    def test_fault_before_ready_then_successful_prepare_closes_unbound(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.record("recording-1")
        job = s.next_storage_job()
        first = FailureEvidence("transport_error", "disconnected before ready")
        s.fault(first)
        completed(s, job)
        self.assertIsNone(s.take_control_action())
        finish, _ = completed(s)
        self.assertEqual(finish.kind, "finish_unbound")
        self.assertEqual(finish.unbound.failure, first)
        self.assertIsNone(s.snapshot().selected_start_sample_index)

    def test_duplicate_terminal_and_ack_fail_closed(self):
        for duplicate_ack in (False, True):
            s = ready_direct()
            completed(s)
            stop = stopping(s)
            end(s, 0)
            ack(s, stop, 0)
            with self.assertRaises(StreamError):
                if duplicate_ack:
                    ack(s, stop, 0)
                else:
                    end(s, 0)
            finish, _ = completed(s)
            self.assertIsNotNone(finish.terminal.failure)
            self.assertFalse(s.snapshot().clean_completion_reported)

    def test_stop_terminal_without_correlated_request_is_rejected(self):
        s = ready_direct()
        completed(s)
        stopping(s)
        terminal = StreamTerminal(IDENTITY, 0, "stopped", True, 0, 0, 0)
        with self.assertRaises(StreamError):
            s.source_end(SourceEnd("uncorrelated-end", terminal))
        self.assertIsNone(s.snapshot().source_end)
        self.assertFalse(s.snapshot().clean_completion_reported)

    def test_malformed_failure_fences_then_exact_result_settles_pending(self):
        s = ready_direct()
        completed(s)
        s.receive(block(0))
        job = s.next_storage_job()
        failure = JobFailure.for_job(job, FailureEvidence("host_io", "actual write failed"))
        with self.assertRaises(StreamError):
            s.storage_failed(replace(failure, operation_id=900))
        self.assertEqual(s.snapshot().pending_operation_id, job.operation_id)
        first = s.snapshot().first_failure
        s.storage_failed(failure)
        self.assertIsNone(s.snapshot().pending_operation_id)
        self.assertEqual(s.snapshot().first_failure, first)
        self.assertEqual(s.snapshot().accepted_end_sample_index, 0)
        self.assertEqual(s.snapshot().raw_items_held, 1)

    def test_stale_failure_cannot_fail_a_later_operation(self):
        s = ready_direct()
        first_job, _ = completed(s)
        s.receive(block(0))
        next_job = s.next_storage_job()
        stale = JobFailure.for_job(first_job, FailureEvidence("host_io", "stale failure"))
        with self.assertRaises(StreamError):
            s.storage_failed(stale)
        self.assertEqual(s.snapshot().pending_operation_id, next_job.operation_id)
        with self.assertRaises(StreamError):
            completed(s, next_job)
        self.assertIsNone(s.snapshot().pending_operation_id)
        self.assertEqual(s.snapshot().accepted_end_sample_index, 0)

    def test_delayed_prepare_and_single_dispatch(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.record("recording-1")
        job = s.next_storage_job()
        for _ in range(1000):
            self.assertIsNone(s.next_storage_job())
            self.assertIsNone(s.take_control_action())
        completed(s, job)
        self.assertEqual(s.take_control_action().kind, "start")

    def test_wrong_completion_fields_fence_without_advancing(self):
        for field, value in (("attempt_id", "other"), ("operation_id", 999),
                             ("kind", "checkpoint"), ("recording_id", "other"),
                             ("identity", replace(IDENTITY, generation=8)),
                             ("job_sha256", "f" * 64), ("token_id", "f" * 32),
                             ("accepted_end_sample_index", 4),
                             ("checkpointed_end_sample_index", 1)):
            with self.subTest(field=field):
                s = ready_direct()
                completed(s)
                s.receive(block(0))
                job = s.next_storage_job()
                result = JobCompletion.for_job(job, token_id=TOKEN, accepted_end=2, checkpointed_end=0)
                with self.assertRaises(StreamError):
                    s.storage_completed(replace(result, **{field: value}))
                self.assertTrue(s.snapshot().writer_fenced)
                self.assertEqual(s.snapshot().accepted_end_sample_index, 0)
                self.assertEqual(s.snapshot().checkpointed_end_sample_index, 0)
                self.assertIsNone(s.next_storage_job())
                with self.assertRaises(StreamError):
                    s.storage_completed(result)

    def test_prepare_must_not_fabricate_endpoints(self):
        s = StreamSession(IDENTITY, "attempt-1")
        s.record("recording-1")
        job = s.next_storage_job()
        bad = JobCompletion.for_job(job, token_id=TOKEN, accepted_end=0, checkpointed_end=0)
        with self.assertRaises(StreamError):
            s.storage_completed(bad)
        self.assertIsNone(s.snapshot().selected_start_sample_index)
        self.assertIsNone(s.take_control_action())

    def test_duplicate_completion_is_not_idempotent_success(self):
        s = ready_direct()
        job, result = completed(s)
        with self.assertRaises(StreamError):
            s.storage_completed(result)
        self.assertTrue(s.snapshot().writer_fenced)
        self.assertFalse(s.snapshot().finalized)
        self.assertEqual(s.snapshot().accepted_end_sample_index, 0)

    def test_stale_completion_cannot_complete_new_job(self):
        s = ready_direct()
        _, old = completed(s)
        s.receive(block(0))
        current = s.next_storage_job()
        self.assertNotEqual(old.operation_id, current.operation_id)
        with self.assertRaises(StreamError):
            s.storage_completed(old)
        self.assertEqual(s.snapshot().raw_items_held, 1)
        self.assertEqual(s.snapshot().accepted_end_sample_index, 0)

    def test_success_after_failed_operation_cannot_retry(self):
        s = ready_direct()
        completed(s)
        s.receive(block(0))
        job = s.next_storage_job()
        report = JobCompletion.for_job(job, token_id=TOKEN, accepted_end=2, checkpointed_end=0)
        s.storage_failed(JobFailure.for_job(job, FailureEvidence("host_io", "fsync failed after readable bytes")))
        with self.assertRaises(StreamError):
            s.storage_completed(report)
        self.assertEqual(s.snapshot().accepted_end_sample_index, 0)
        self.assertFalse(s.snapshot().finalized)

    def test_job_and_input_mutation_cannot_change_owned_state(self):
        identity = deepcopy(IDENTITY)
        s = StreamSession(identity, "attempt-1")
        object.__setattr__(identity, "generation", 900)
        s.record("recording-1")
        completed(s)
        started(s)
        completed(s)
        value = block(0)
        s.receive(value)
        object.__setattr__(value, "raw_u16le", b"\xff\xff\xff\xff")
        job = s.next_storage_job()
        self.assertEqual(job.block.raw_u16le, block(0).raw_u16le)
        original_digest = job.sha256
        object.__setattr__(job.block, "raw_u16le", b"\xff\xff\xff\xff")
        self.assertNotEqual(job.sha256, original_digest)
        with self.assertRaises(StreamError):
            completed(s, job)
        self.assertEqual(s.snapshot().latest_display.raw_u16le, block(0).raw_u16le)
        self.assertEqual(s.snapshot().identity.generation, 7)

    def test_snapshot_mutation_cannot_change_session(self):
        s = ready_preview()
        s.receive(block(0))
        snap = s.snapshot()
        with self.assertRaises(FrozenInstanceError):
            snap.raw_items_held = 99
        object.__setattr__(snap.identity, "generation", 999)
        object.__setattr__(snap.latest_display, "first_sample_index", 99)
        self.assertEqual(s.snapshot().identity.generation, 7)
        self.assertEqual(s.snapshot().latest_display.first_sample_index, 0)

    def test_scalar_bool_range_and_mutated_inputs_rejected_under_optimization(self):
        for limits in ((True, 2), (2, True), (0, 1), (2, 0), (1024 * 1024 + 1, 1), (2, 4097)):
            with self.subTest(limits=limits), self.assertRaises(StreamError):
                SessionLimits(*limits)
        for bad_value in (True, -1, U64_MAX + 1, 0.0):
            s = ready_preview()
            bad = block(0)
            object.__setattr__(bad, "first_sample_index", bad_value)
            with self.subTest(bad=bad_value), self.assertRaises(StreamError):
                s.receive(bad)
            self.assertTrue(s.snapshot().admission_fenced)
        s = ready_preview(U64_MAX)
        bad = block(0, 1)
        object.__setattr__(bad, "first_sample_index", U64_MAX)
        with self.assertRaises(StreamError):
            s.receive(bad)
        s = ready_direct()
        completed(s)
        s.receive(block(0))
        job = s.next_storage_job()
        result = JobCompletion.for_job(job, token_id=TOKEN, accepted_end=2, checkpointed_end=0)
        object.__setattr__(result, "accepted_end_sample_index", True)
        with self.assertRaises(StreamError):
            s.storage_completed(result)
        self.assertTrue(s.snapshot().writer_fenced)


class _RealStorageProbe:
    """Small test-only owner. Success fields come from actual synchronous returns."""
    def __init__(self, session, path):
        self.session, self.path = session, path
        self.prepared = self.writer = self.token = None
        self.jobs = []

    def step(self):
        from motortester.stream_model import StreamPreparation
        from motortester.stream_recording import PreparedStreamWriter
        job = self.session.next_storage_job()
        if job is None:
            return None
        self.jobs.append(job)
        try:
            if job.kind == "prepare":
                self.prepared = PreparedStreamWriter.prepare(
                    self.path, StreamPreparation(job.identity, job.recording_id, job.start_policy))
                self.token = self.prepared.readiness_token
            elif job.kind == "bind_first_block":
                self.writer = self.prepared.bind_first_block(self.token, job.block)
            elif job.kind == "bind_start":
                self.writer = self.prepared.bind_start(self.token, job.start)
            elif job.kind == "append":
                self.writer.append(job.block)
            elif job.kind == "checkpoint":
                self.writer.checkpoint()
            elif job.kind == "finish":
                self.writer.finish(job.terminal)
            elif job.kind == "finish_unbound":
                self.prepared.finish_unbound(self.token, job.unbound)
            else:
                raise RuntimeError("unexpected storage job")
        except (OSError, StreamError):
            self.session.storage_failed(JobFailure.for_job(
                job, FailureEvidence("host_io", "actual synthetic storage call failed")))
            raise
        result = JobCompletion.for_job(
            job, token_id=self.token.token_id,
            accepted_end=None if self.writer is None else self.writer.accepted_end_sample_index,
            checkpointed_end=None if self.writer is None else self.writer.checkpointed_end_sample_index)
        self.session.storage_completed(result)
        return job

    def drain(self):
        for _ in range(10000):
            if self.step() is None:
                return
        raise RuntimeError("unbounded probe jobs")


class StoragePairingTests(unittest.TestCase):
    """Local-journal probes; separate from pure model and from wire evidence."""
    def test_real_preview_selected_range_checkpoint_and_terminal(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from motortester.stream_recording import recover_attempt
        first = 2 ** 53 + 11
        with TemporaryDirectory() as directory:
            s = StreamSession(IDENTITY, "attempt-1")
            probe = _RealStorageProbe(s, Path(directory) / "preview.mtstream")
            s.preview()
            started(s, first)
            s.receive(block(first, 3))
            s.record("recording-1")
            probe.step()
            s.receive(block(first + 3, 2))
            bind = probe.step()
            self.assertEqual(bind.kind, "bind_first_block")
            s.receive(block(first + 5, 4))
            s.request_checkpoint()
            self.assertEqual(probe.step().kind, "checkpoint")
            stop = stopping(s)
            end(s, first + 9)
            ack(s, stop, first + 9)
            self.assertLess(s.snapshot().accepted_end_sample_index, first + 9)
            probe.drain()
            report = recover_attempt(probe.path)
            self.assertIsNone(report.recovery_issue)
            self.assertEqual(report.selected_start, bind.start)
            recording = report.recording
            self.assertEqual(recording.first_sample_index, first + 3)
            self.assertEqual(recording.end_sample_index, first + 9)
            expected = block(first + 3, 6).raw_u16le
            self.assertEqual(recording.read_raw(first + 3, 6), expected)
            self.assertEqual(probe.jobs[-1].prefix_sha256, hashlib.sha256(expected).hexdigest())
            self.assertEqual(recording.terminal, probe.jobs[-1].terminal)
            self.assertTrue(s.snapshot().clean_completion_reported)
            self.assertTrue(recording.clean_stop_reported)
            self.assertTrue(probe.writer.closed)

    def test_real_direct_established_empty_recording(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from motortester.stream_recording import recover_attempt
        with TemporaryDirectory() as directory:
            s = StreamSession(IDENTITY, "attempt-1")
            probe = _RealStorageProbe(s, Path(directory) / "empty.mtstream")
            s.record("recording-1")
            probe.step()
            started(s)
            probe.step()
            stop = stopping(s)
            end(s, 0)
            ack(s, stop, 0)
            probe.drain()
            report = recover_attempt(probe.path)
            self.assertIsNone(report.recovery_issue)
            self.assertIsNotNone(report.recording)
            self.assertEqual(report.recording.first_sample_index, 0)
            self.assertEqual(report.recording.end_sample_index, 0)
            self.assertEqual(report.recording.read_raw(0, 0), b"")
            self.assertTrue(s.snapshot().clean_completion_reported)

    def test_real_end_before_selection_has_no_indexed_recording(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from motortester.stream_recording import recover_attempt
        with TemporaryDirectory() as directory:
            s = StreamSession(IDENTITY, "attempt-1")
            probe = _RealStorageProbe(s, Path(directory) / "unbound.mtstream")
            s.preview()
            started(s)
            s.record("recording-1")
            probe.step()
            stop = stopping(s)
            end(s, 0)
            ack(s, stop, 0)
            probe.drain()
            report = recover_attempt(probe.path)
            self.assertIsNone(report.recovery_issue)
            self.assertIsNone(report.selected_start)
            self.assertIsNone(report.recording)
            self.assertEqual(report.unbound_outcome.reason, "ended_unbound")
            self.assertIsNone(s.snapshot().finalized_end_sample_index)
            self.assertFalse(s.snapshot().clean_completion_reported)

    def test_real_known_and_unknown_source_loss_survives_source_failure(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from motortester.stream_recording import recover_attempt
        for source_loss in (7, 0, None):
            with self.subTest(source_loss=source_loss), TemporaryDirectory() as directory:
                s = StreamSession(IDENTITY, "attempt-1")
                probe = _RealStorageProbe(s, Path(directory) / "source-failure.mtstream")
                s.record("recording-1")
                probe.step()
                started(s)
                probe.step()
                s.receive(block(0, 5))
                probe.step()
                first = FailureEvidence("source_overrun", "source-reported loss", source_code=3)
                terminal = StreamTerminal(
                    IDENTITY, 5, "source_failure", True,
                    None if source_loss is None else 5 + source_loss,
                    5, source_loss, "source_reported_counter_only", first)
                s.source_end(SourceEnd("source-fault-end", terminal))
                stop = s.take_control_action()
                ack(s, stop, 5, terminal_id="source-fault-end")
                probe.drain()
                report = recover_attempt(probe.path)
                self.assertIsNone(report.recovery_issue)
                self.assertEqual(report.recording.read_raw(0, 5), block(0, 5).raw_u16le)
                self.assertEqual(report.recording.terminal.failure, first)
                self.assertEqual(report.recording.terminal.loss_samples, source_loss)
                self.assertEqual(report.recording.terminal.device_transmitted_end_sample_index, 5)
                self.assertEqual(report.recording.terminal.device_generated_end_sample_index,
                                 terminal.device_generated_end_sample_index)
                self.assertFalse(report.recording.clean_stop_reported)
                self.assertFalse(s.snapshot().clean_completion_reported)

    def test_real_source_counter_is_separate_from_local_first_fault_and_shorter_archive(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from motortester.stream_recording import recover_attempt
        for source_loss in (7, 0, None):
            with self.subTest(source_loss=source_loss), TemporaryDirectory() as directory:
                s = StreamSession(IDENTITY, "attempt-1", SessionLimits(10, 1))
                probe = _RealStorageProbe(s, Path(directory) / "host-failure.mtstream")
                s.record("recording-1")
                probe.step()
                started(s)
                probe.step()
                s.receive(block(0, 5))
                probe.step()
                s.receive(block(5, 5))
                with self.assertRaises(StreamError):
                    s.receive(block(10, 1))
                first = s.snapshot().first_failure
                self.assertEqual(first.code, "capacity_raw")
                stop = s.take_control_action()
                terminal = StreamTerminal(
                    IDENTITY, 11, "source_failure", True,
                    None if source_loss is None else 11 + source_loss,
                    11, source_loss, "source_reported_counter_only",
                    FailureEvidence("source_overrun", "later source failure"))
                s.source_end(SourceEnd("source-fault-end", terminal))
                ack(s, stop, 11, terminal_id="source-fault-end")
                probe.drain()
                report = recover_attempt(probe.path)
                self.assertIsNone(report.recovery_issue)
                self.assertEqual(report.recording.read_raw(0, 10), block(0, 10).raw_u16le)
                self.assertEqual(report.recording.end_sample_index, 10)
                saved = report.recording.terminal
                self.assertEqual(saved.failure, first)
                self.assertEqual(saved.reason, "capacity")
                self.assertEqual(saved.device_transmitted_end_sample_index, 11)
                self.assertEqual(saved.loss_samples, source_loss)
                # Even source counter=0 coexists with missing host samples.
                # It is deliberately NOT replaced by an inferred total of 1.
                self.assertNotEqual(saved.loss_samples, 1)
                self.assertFalse(report.recording.clean_stop_reported)
                self.assertFalse(s.snapshot().clean_completion_reported)

    def test_real_readable_terminal_does_not_resolve_failed_finish(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from motortester.stream_recording import recover_attempt
        with TemporaryDirectory() as directory:
            s = StreamSession(IDENTITY, "attempt-1")
            probe = _RealStorageProbe(s, Path(directory) / "uncertain.mtstream")
            s.record("recording-1")
            probe.step()
            started(s)
            probe.step()
            s.receive(block(0, 3))
            probe.step()
            stop = stopping(s)
            end(s, 3)
            ack(s, stop, 3)
            with patch("motortester.stream_recording._sync", side_effect=OSError("synthetic fsync error")):
                with self.assertRaises(OSError):
                    probe.step()
            report = recover_attempt(probe.path)
            self.assertIsNotNone(report.recording)
            self.assertEqual(report.recording.read_raw(0, 3), block(0, 3).raw_u16le)
            self.assertTrue(report.recording.clean_stop_reported)
            self.assertTrue(s.snapshot().finalization_unresolved)
            self.assertFalse(s.snapshot().finalized)
            self.assertFalse(s.snapshot().clean_completion_reported)
            self.assertIsNone(s.snapshot().pending_operation_id)
            self.assertTrue(probe.writer.closed)


if __name__ == "__main__":
    unittest.main()

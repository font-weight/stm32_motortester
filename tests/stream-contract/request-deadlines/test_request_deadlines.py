"""Bounded deterministic executable examples of the proposed HOST rule."""

import unittest

from deadline_model import (BUDGET_MS, KINDS, MAX_TICK, CleanupBudgetModel,
                            RequestBudgetModel)


class RequestDeadlineTests(unittest.TestCase):
    def request(self, kind="STREAM_START", start=0, **kwargs):
        model = RequestBudgetModel()
        pending = model.admit(start, kind, 1 if kind == "HELLO" else 3, **kwargs)
        self.assertIsNotNone(pending)
        return model, pending

    def test_all_commands_admit_before_zero_byte_first_write(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                model, pending = self.request(kind, 17)
                self.assertEqual(pending.admitted_ms, 17)
                self.assertEqual(pending.deadline_ms, 2017)
                self.assertEqual(pending.accepted_bytes, 0)
                self.assertFalse(pending.sequence_committed)
                model.write(2016, pending, 0)
                self.assertTrue(model.advance(2016))
                self.assertFalse(model.advance(2017))
                self.assertEqual(model.timed_out_identity, pending.identity)
                self.assertEqual(pending.cancelled_suffix_bytes, pending.total_bytes)

    def test_no_dispatch_progress_is_needed_to_expire(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                model, pending = self.request(kind)
                self.assertFalse(model.advance(2000))
                self.assertEqual(pending.accepted_bytes, 0)

    def test_independent_timer_wakes_after_full_write_and_silent_receive(self):
        model, pending = self.request()
        model.write(100, pending, pending.total_bytes)
        self.assertEqual(model.next_timer_ms, 2000)
        # The next event is a timer wake, with no send/receive completion.
        self.assertFalse(model.advance(model.next_timer_ms))
        self.assertEqual(model.timed_out_identity, pending.identity)

    def test_late_scheduler_wake_expires_before_queued_completion(self):
        model, pending = self.request()
        model.write(100, pending, pending.total_bytes)
        self.assertEqual(model.next_timer_ms, 2000)
        self.assertFalse(model.matching_response(2500, pending))
        self.assertEqual(pending.deadline_ms, 2000)
        self.assertEqual(model.first_cause.observed_ms, 2500)

    def test_scheduler_uses_earliest_pending_deadline(self):
        model, ordinary = self.request()
        stop = model.admit(100, "STOP", 4)
        self.assertEqual(model.next_timer_ms, ordinary.deadline_ms)
        model.write(101, ordinary, ordinary.total_bytes)
        model.matching_response(102, ordinary)
        self.assertEqual(model.next_timer_ms, stop.deadline_ms)

    def test_exact_response_boundary_matrix(self):
        # 4 command types × 3 epochs × 3 boundary points = 36 finite cases.
        for kind in KINDS:
            for start in (0, 137, (1 << 53) + 17):
                for offset in (1999, 2000, 2001):
                    with self.subTest(kind=kind, start=start, offset=offset):
                        model, pending = self.request(kind, start)
                        model.write(start, pending, pending.total_bytes)
                        accepted = model.matching_response(start + offset, pending)
                        self.assertEqual(accepted, offset < 2000)
                        self.assertEqual(model.needs_recovery, offset >= 2000)
                        self.assertEqual(pending.completed, offset < 2000)

    def test_literal_command_response_pairs_without_oracle_lookup(self):
        # Independent protocol literals, deliberately not imported from RESPONSES
        # and not supplied through matching_response(), which shares that map.
        pairs = (
            ("HELLO", "HELLO_ACK"),
            ("GET_CAPS", "STREAM_CAPS"),
            ("STREAM_START", "STREAM_START_ACK"),
            ("STOP", "STOP_ACK"),
        )
        for command, expected in pairs:
            for _, actual in pairs:
                with self.subTest(command=command, response=actual):
                    model, pending = self.request(command)
                    model.write(0, pending, pending.total_bytes)
                    accepted = model.response(1999, actual,
                                              pending.identity.request_id,
                                              pending.identity.nonce)
                    self.assertEqual(accepted, actual == expected)
                    self.assertEqual(pending.completed, actual == expected)

    def test_exact_last_write_boundary_matrix(self):
        for kind in KINDS:
            for offset in (1999, 2000, 2001):
                with self.subTest(kind=kind, offset=offset):
                    model, pending = self.request(kind)
                    model.write(1, pending, pending.total_bytes - 1)
                    previous = pending.accepted_bytes
                    self.assertEqual(model.write(offset, pending, 1), offset < 2000)
                    self.assertEqual(pending.accepted_bytes,
                                     previous + int(offset < 2000))
                    self.assertEqual(pending.deadline_ms, 2000)
                    if offset == 1999:
                        self.assertFalse(model.advance(2000))
                    self.assertFalse(pending.completed)

    def test_first_write_at_deadline_is_not_issued(self):
        model, pending = self.request()
        self.assertFalse(model.write(2000, pending, pending.total_bytes))
        self.assertEqual(model.accepted_log, [])
        self.assertEqual(pending.accepted_bytes, 0)

    def test_zero_acceptance_retries_do_not_slide_budget(self):
        model, pending = self.request()
        for now in (1, 500, 1000, 1500, 1999):
            self.assertTrue(model.write(now, pending, 0))
            self.assertEqual(pending.deadline_ms, 2000)
        self.assertFalse(model.write(2000, pending, 0))
        self.assertEqual(pending.accepted_bytes, 0)

    def test_partial_trickle_does_not_slide_budget(self):
        model, pending = self.request(frame_bytes=20)
        for now in (1, 500, 1000, 1500, 1999):
            model.write(now, pending, 1)
        self.assertFalse(model.write(2000, pending, 1))
        self.assertEqual(pending.accepted_bytes, 5)
        self.assertEqual(pending.cancelled_suffix_bytes, 15)
        self.assertEqual(model.first_cause.identity, pending.identity)

    def test_completing_send_at_1999_does_not_grant_response_budget(self):
        model, pending = self.request()
        model.write(1999, pending, pending.total_bytes)
        self.assertFalse(model.matching_response(2000, pending))
        self.assertEqual(pending.deadline_ms, 2000)
        self.assertEqual(pending.cancelled_suffix_bytes, 0)

    def test_fully_valid_response_before_deadline_closes_only_that_timer(self):
        model, pending = self.request()
        model.write(10, pending, pending.total_bytes)
        self.assertTrue(model.matching_response(1999, pending))
        self.assertTrue(model.advance(9000))
        self.assertIsNone(model.first_cause)

    def test_buffered_bytes_do_not_substitute_for_timely_validation(self):
        model, pending = self.request()
        model.write(0, pending, pending.total_bytes)
        self.assertFalse(model.matching_response(1999, pending, validated=False))
        self.assertFalse(model.matching_response(2000, pending, validated=True))
        self.assertEqual(model.first_cause.name, "request_deadline")

    def test_only_full_valid_correlated_response_counts(self):
        variations = (
            {"complete": False}, {"validated": False},
            {"nonce": 999}, {"request_id": 999}, {"response_kind": "STOP_ACK"},
        )
        for variation in variations:
            with self.subTest(variation=variation):
                model, pending = self.request()
                model.write(0, pending, pending.total_bytes)
                kwargs = dict(response_kind="STREAM_START_ACK", request_id=3,
                              nonce=model.nonce, complete=True, validated=True)
                kwargs.update(variation)
                self.assertFalse(model.response(1999, **kwargs))
                self.assertFalse(pending.completed)
                self.assertFalse(model.advance(2000))

    def test_partial_response_trickle_does_not_restart_request_timer(self):
        model, pending = self.request()
        model.write(0, pending, pending.total_bytes)
        for now in (500, 1000, 1500, 1999):
            self.assertFalse(model.matching_response(now, pending, complete=False))
        self.assertFalse(model.matching_response(2000, pending))
        self.assertEqual(pending.deadline_ms, 2000)

    def test_duplicate_old_response_does_not_extend_current_request(self):
        model, hello = self.request("HELLO")
        model.write(0, hello, hello.total_bytes)
        self.assertTrue(model.matching_response(100, hello))
        caps = model.admit(200, "GET_CAPS", 2)
        model.write(201, caps, caps.total_bytes)
        self.assertFalse(model.matching_response(2199, hello))
        self.assertEqual(caps.deadline_ms, 2200)
        self.assertFalse(model.advance(2200))
        self.assertEqual(model.timed_out_identity, caps.identity)

    def test_late_response_cannot_revive_expired_request(self):
        model, pending = self.request()
        model.write(0, pending, pending.total_bytes)
        model.advance(2000)
        cause = model.first_cause
        for now in (2000, 2001, 9000):
            self.assertFalse(model.matching_response(now, pending))
            self.assertIs(model.first_cause, cause)
            self.assertFalse(pending.completed)

    def test_timeout_identity_is_actual_ordinary_request_not_queued_stop(self):
        model, ordinary = self.request()
        model.write(1, ordinary, 2)
        stop = model.admit(100, "STOP", 4)
        self.assertEqual(stop.deadline_ms, 2100)
        self.assertFalse(model.write(1999, stop, 1))
        self.assertFalse(model.advance(2000))
        self.assertEqual(model.timed_out_identity, ordinary.identity)
        self.assertEqual(model.first_cause.identity, ordinary.identity)
        self.assertEqual(stop.accepted_bytes, 0)
        self.assertTrue(stop.abandoned)

    def test_queue_wait_consumes_stops_own_budget(self):
        model, ordinary = self.request()
        stop = model.admit(100, "STOP", 4)
        model.write(1990, ordinary, ordinary.total_bytes)
        model.matching_response(1991, ordinary)
        self.assertTrue(model.write(2099, stop, stop.total_bytes))
        self.assertFalse(model.matching_response(2100, stop))
        self.assertEqual(stop.deadline_ms, 2100)
        self.assertEqual(model.timed_out_identity, stop.identity)

    def test_stop_can_write_after_frame_finishes_while_ordinary_reply_pending(self):
        model, ordinary = self.request()
        stop = model.admit(100, "STOP", 4)
        model.write(101, ordinary, ordinary.total_bytes)
        self.assertTrue(model.write(102, stop, stop.total_bytes))
        self.assertFalse(ordinary.completed)
        model.matching_response(1998, ordinary)
        self.assertTrue(model.matching_response(2099, stop))

    def test_stop_success_does_not_cancel_pending_ordinary_deadline(self):
        model, ordinary = self.request()
        model.write(0, ordinary, ordinary.total_bytes)
        stop = model.admit(100, "STOP", 4)
        model.write(101, stop, stop.total_bytes)
        self.assertTrue(model.matching_response(102, stop))
        self.assertFalse(model.advance(2000))
        self.assertEqual(model.timed_out_identity, ordinary.identity)

    def test_completion_dispatch_checks_all_pending_deadlines_first(self):
        model, ordinary = self.request()
        model.write(0, ordinary, ordinary.total_bytes)
        stop = model.admit(100, "STOP", 4)
        model.write(101, stop, stop.total_bytes)
        self.assertFalse(model.matching_response(2000, stop))
        self.assertEqual(model.timed_out_identity, ordinary.identity)
        self.assertFalse(stop.completed)

    def test_delayed_dispatch_reports_earliest_overdue_request(self):
        model, ordinary = self.request()
        stop = model.admit(100, "STOP", 4)
        self.assertFalse(model.advance(10000))
        self.assertEqual(model.timed_out_identity, ordinary.identity)
        self.assertEqual(stop.accepted_bytes, 0)

    def test_no_stop_interleaving_into_partial_request_even_at_expiry(self):
        model, ordinary = self.request()
        model.write(1, ordinary, 1)
        stop = model.admit(100, "STOP", 4)
        for now in (100, 1999, 2000, 2001):
            self.assertFalse(model.write(now, stop, stop.total_bytes))
        self.assertEqual([entry[1] for entry in model.accepted_log], [ordinary.identity])
        self.assertEqual(ordinary.cancelled_suffix_bytes, ordinary.total_bytes - 1)

    def test_rejected_admission_is_atomic_and_has_no_hidden_queue(self):
        model, ordinary = self.request()
        for now in (1, 100, 1999):
            self.assertIsNone(model.admit(now, "GET_CAPS", 5))
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(ordinary.admitted_ms, 0)
            self.assertEqual(ordinary.deadline_ms, 2000)
        self.assertEqual(model.accepted_log, [])

    def test_no_new_ordinary_or_second_stop_while_stop_pending(self):
        model = RequestBudgetModel()
        stop = model.admit(0, "STOP", 4)
        self.assertIsNone(model.admit(1, "STREAM_START", 5))
        self.assertIsNone(model.admit(2, "STOP", 6))
        self.assertEqual(model.pending(), [stop])

    def test_stop_not_admitted_during_hello_or_without_generation(self):
        model, hello = self.request("HELLO")
        self.assertIsNone(model.admit(1, "STOP", 2))
        self.assertEqual(model.pending(), [hello])
        model = RequestBudgetModel(generation_known=False)
        self.assertIsNone(model.admit(0, "STOP", 2))
        self.assertEqual(model.pending(), [])

    def test_no_same_session_retry_after_zero_partial_or_full_send(self):
        for accepted in (0, 1, 8):
            with self.subTest(accepted=accepted):
                model, pending = self.request()
                model.write(0, pending, accepted)
                model.advance(2000)
                for kind in KINDS:
                    self.assertIsNone(model.admit(2001, kind, 8))
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(pending.accepted_bytes, accepted)
                self.assertFalse(model.write(2002, pending, 8 - accepted))

    def test_earlier_storage_fault_survives_later_stop_timeout(self):
        model = RequestBudgetModel(verified_prefix=(1, 2, 4095))
        prefix = model.verified_prefix
        model.latch_first_cause(0, "disk_failure")
        original = model.first_cause
        stop = model.admit(1, "STOP", 4)
        model.write(2, stop, stop.total_bytes)
        model.advance(2001)
        self.assertIs(model.first_cause, original)
        self.assertEqual(model.first_cause.name, "disk_failure")
        self.assertEqual(model.timed_out_identity, stop.identity)
        self.assertFalse(model.matching_response(2002, stop))
        self.assertEqual(model.verified_prefix, prefix)

    def test_first_timeout_cause_survives_following_local_fault(self):
        model, pending = self.request()
        model.advance(2000)
        original = model.first_cause
        model.latch_first_cause(2001, "disconnect")
        self.assertIs(model.first_cause, original)
        self.assertEqual(model.first_cause.identity, pending.identity)

    def test_expiry_precedes_new_fault_at_same_dispatch_instant(self):
        model, pending = self.request()
        model.latch_first_cause(2000, "disconnect")
        self.assertEqual(model.first_cause.name, "request_deadline")
        self.assertEqual(model.first_cause.identity, pending.identity)

    def test_unknown_acceptance_count_requires_recovery_without_retry(self):
        model, pending = self.request()
        self.assertFalse(model.write(1, pending, None))
        self.assertEqual(model.first_cause.name, "accepted_byte_count_unknown")
        self.assertFalse(pending.accepted_count_known)
        self.assertIsNone(pending.cancelled_suffix_bytes)
        self.assertIsNone(model.admit(2, "STOP", 4))
        self.assertFalse(model.write(3, pending, 1))

    def test_hello_budget_includes_prefix_wait_and_acceptance(self):
        for prefix_accepted in (False, True):
            with self.subTest(prefix_accepted=prefix_accepted):
                model, hello = self.request("HELLO", 20, recovery_prefix=True)
                model.write(2019, hello, int(prefix_accepted))
                self.assertFalse(hello.sequence_committed)
                self.assertFalse(model.write(2020, hello, 1))
                self.assertEqual(hello.deadline_ms, 2020)
                self.assertEqual(model.timed_out_identity, hello.identity)
                self.assertFalse(hello.completed)

    def test_hello_sequence_commits_only_on_first_encoded_byte(self):
        model, hello = self.request("HELLO", recovery_prefix=True)
        model.write(0, hello, 1)
        self.assertFalse(hello.sequence_committed)
        model.write(1, hello, 1)
        self.assertTrue(hello.sequence_committed)
        self.assertEqual(hello.deadline_ms, 2000)

    def test_prefix_then_full_hello_does_not_restart_deadline(self):
        model, hello = self.request("HELLO", recovery_prefix=True)
        model.write(1000, hello, 1)
        model.write(1999, hello, hello.frame_bytes)
        self.assertFalse(model.matching_response(2000, hello))
        self.assertEqual(hello.accepted_bytes, hello.total_bytes)
        self.assertEqual(hello.deadline_ms, 2000)

    def test_hello_old_tail_or_other_response_does_not_complete_handshake(self):
        model, hello = self.request("HELLO", recovery_prefix=True)
        model.write(0, hello, hello.total_bytes)
        self.assertFalse(model.response(1990, "HELLO_ACK", 1, 999))
        self.assertFalse(model.response(1991, "STREAM_START_ACK", 1, model.nonce))
        self.assertFalse(model.matching_response(1999, hello, validated=False))
        self.assertFalse(model.advance(2000))
        self.assertIsNone(model.admit(2001, "STREAM_START", 3))

    def test_recovery_hello_requires_old_writer_isolation_before_admission(self):
        model = RequestBudgetModel(nonce=456, recovery=True)
        for now in (0, 1000, 1999):
            self.assertIsNone(model.admit(now, "HELLO", 1, recovery_prefix=True))
        self.assertEqual(model.requests, [])
        self.assertEqual(model.accepted_log, [])
        self.assertEqual(model.last_rejection, "old_writer_not_isolated")
        model.establish_old_writer_isolation(1999, verified=True)
        hello = model.admit(1999, "HELLO", 1, recovery_prefix=True)
        self.assertEqual(hello.deadline_ms, 3999)
        self.assertTrue(model.write(2000, hello, 1))

    def test_unproven_cancellation_does_not_allow_recovery_delimiter(self):
        model = RequestBudgetModel(recovery=True)
        model.establish_old_writer_isolation(100, verified=False)
        self.assertIsNone(model.admit(101, "HELLO", 1, recovery_prefix=True))
        self.assertEqual(model.accepted_log, [])

    def test_checked_deadline_arithmetic_rejects_overflow_without_admission(self):
        model = RequestBudgetModel()
        self.assertIsNone(model.admit(MAX_TICK - BUDGET_MS + 1, "GET_CAPS", 2))
        self.assertEqual(model.last_rejection, "deadline_overflow")
        self.assertEqual(model.requests, [])
        self.assertEqual(model.accepted_log, [])

    def test_last_representable_deadline_expires_at_equality(self):
        model, pending = self.request(start=MAX_TICK - BUDGET_MS)
        self.assertEqual(pending.deadline_ms, MAX_TICK)
        self.assertTrue(model.advance(MAX_TICK - 1))
        self.assertFalse(model.advance(MAX_TICK))

    def test_clock_regression_and_float_are_rejected(self):
        model, pending = self.request(start=10)
        for bad_time in (9, 10.0, -1):
            with self.subTest(bad_time=bad_time):
                with self.assertRaises(ValueError):
                    model.write(bad_time, pending, 1)
        self.assertEqual(pending.accepted_bytes, 0)

    def test_bounded_progress_partition_matrix(self):
        # 4 kinds × 4 first-write sizes × 4 later-write times = 64 cases.
        # This is bounded enumeration, not an exhaustive proof of the host.
        for kind in KINDS:
            for first in (0, 1, 7, 8):
                for later in (1, 1999, 2000, 2001):
                    with self.subTest(kind=kind, first=first, later=later):
                        model, pending = self.request(kind)
                        model.write(0, pending, first)
                        model.write(later, pending, 8 - first)
                        self.assertEqual(pending.deadline_ms, 2000)
                        model.advance(max(2000, later))
                        self.assertTrue(model.needs_recovery)
                        self.assertEqual(model.timed_out_identity, pending.identity)
                        self.assertEqual(pending.accepted_bytes,
                                         8 if later < 2000 else first)


class CleanupDeadlineTests(unittest.TestCase):
    def test_cleanup_deadline_begins_before_first_cancel_attempt(self):
        cleanup = CleanupBudgetModel(100)
        self.assertEqual(cleanup.deadline_ms, 2100)
        self.assertEqual(cleanup.progress_events, 0)
        self.assertEqual(cleanup.next_timer_ms, 2100)
        self.assertFalse(cleanup.advance(2100))
        self.assertTrue(cleanup.requires_physical_reset)

    def test_cleanup_exact_completion_boundary_matrix(self):
        for start in (0, 123, (1 << 53) + 17):
            for offset in (1999, 2000, 2001):
                with self.subTest(start=start, offset=offset):
                    cleanup = CleanupBudgetModel(start)
                    allowed = cleanup.progress(start + offset, isolated=True)
                    self.assertEqual(allowed, offset < 2000)
                    self.assertEqual(cleanup.isolated, offset < 2000)
                    self.assertEqual(cleanup.requires_physical_reset, offset >= 2000)
                    self.assertEqual(cleanup.deadline_ms, start + 2000)

    def test_cleanup_trickle_does_not_extend_budget(self):
        cleanup = CleanupBudgetModel(0)
        for now in (1, 500, 1000, 1500, 1999):
            self.assertTrue(cleanup.progress(now))
            self.assertEqual(cleanup.next_timer_ms, 2000)
        self.assertFalse(cleanup.progress(2000, isolated=True))
        self.assertIsNone(cleanup.new_session(2001, 456))

    def test_cleanup_silent_io_has_independent_timer_wake(self):
        cleanup = CleanupBudgetModel(100)
        self.assertFalse(cleanup.advance(cleanup.next_timer_ms))
        self.assertTrue(cleanup.requires_physical_reset)
        self.assertFalse(cleanup.isolated)

    def test_cleanup_late_wake_does_not_accept_queued_isolation(self):
        cleanup = CleanupBudgetModel(100)
        self.assertFalse(cleanup.progress(2500, isolated=True))
        self.assertEqual(cleanup.deadline_ms, 2100)
        self.assertIsNone(cleanup.new_session(2501, 456))

    def test_cleanup_progress_without_proof_cannot_admit_hello(self):
        cleanup = CleanupBudgetModel(0)
        cleanup.progress(100, isolated=False)
        self.assertIsNone(cleanup.new_session(101, 456))

    def test_isolated_new_nonce_then_hello_has_its_own_admission_budget(self):
        cleanup = CleanupBudgetModel(2000)
        self.assertTrue(cleanup.progress(3999, isolated=True))
        self.assertIsNone(cleanup.next_timer_ms)
        model = cleanup.new_session(4000, 456)
        self.assertIsNotNone(model)
        self.assertIsNone(model.next_timer_ms)
        hello = model.admit(4010, "HELLO", 1, recovery_prefix=True)
        self.assertEqual(hello.deadline_ms, 6010)
        model.write(6009, hello, hello.total_bytes)
        self.assertFalse(model.matching_response(6010, hello))

    def test_isolation_does_not_make_old_or_zero_nonce_fresh(self):
        cleanup = CleanupBudgetModel(0, old_nonce=123)
        cleanup.progress(1, isolated=True)
        self.assertIsNone(cleanup.new_session(2, 123))
        self.assertIsNone(cleanup.new_session(2, 0))

    def test_cleanup_failure_does_not_erase_original_request_fault(self):
        old = RequestBudgetModel(verified_prefix=(5, 10))
        request = old.admit(0, "STREAM_START", 3)
        old.write(1, request, 1)
        old.advance(2000)
        cause = old.first_cause
        cleanup = CleanupBudgetModel(old.now_ms, old_nonce=old.nonce)
        cleanup.advance(4000)
        self.assertIs(old.first_cause, cause)
        self.assertEqual(old.timed_out_identity, request.identity)
        self.assertEqual(old.verified_prefix, (5, 10))
        self.assertTrue(cleanup.requires_physical_reset)

    def test_cleanup_clock_overflow_fails_closed(self):
        cleanup = CleanupBudgetModel(MAX_TICK - BUDGET_MS + 1)
        self.assertTrue(cleanup.requires_physical_reset)
        self.assertIsNone(cleanup.next_timer_ms)
        self.assertIsNone(cleanup.new_session(MAX_TICK, 456))


if __name__ == "__main__":
    unittest.main(verbosity=2)

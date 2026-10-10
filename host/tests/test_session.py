"""Author tests for the bounded HOST-006 draft, not device acceptance."""
from dataclasses import FrozenInstanceError, replace
import struct
import unittest

from motortester.session import (
    Config, ErrorCode, EventKind as K, FaultCode, MAX_ID, MAX_TIME_MS, Phase,
    RecoveryReason as R, SessionController, SessionError, SOURCE_SHA,
)
from motortester.wire import Frame, decode_frame


def hello_payload(bits=63):
    return struct.pack("<BBBBHHII", 0, 1, 1, 12, 512, 2048, 77, bits)


def caps_payload(bits=63):
    return struct.pack("<BBHIHHIHHBBBBHHH", 1, 0, 1, 72000000, 71, 19,
                       12000000, 143, 2048, 3 if bits & 32 else 1,
                       (bits >> 2) & 7, bits & 3, 3,
                       0, 512 if bits & 32 else 0, 1024 if bits & 32 else 0)


class Harness:
    def __init__(self, bits=63):
        self.c = SessionController()
        self.now = 0
        self.rx = 1
        self.bits = bits
        self.c.start(100, self.now)

    def tx(self):
        ticket = self.c.prepare_tx()
        if ticket is None:
            raise AssertionError("Expected queued request")
        self.c.written(ticket, len(ticket.encoded) - ticket.offset, self.now)
        return ticket.frame

    def receive(self, kind, payload, request=0):
        frame = Frame(kind, 100, self.rx, request, payload)
        self.rx += 1
        return self.c.receive(frame, self.now)

    def ready(self):
        self.tx()
        self.receive(129, hello_payload(self.bits), 1)
        req = self.tx()
        self.receive(130, caps_payload(self.bits), req.request_id)
        return self

    def configured(self, config=Config()):
        self.ready()
        self.c.configure(config, self.now)
        req = self.tx()
        self.receive(131, struct.pack("<I", 1) + req.payload, req.request_id)
        return self

    def armed(self, config=Config()):
        self.configured(config)
        self.c.arm(self.now)
        req = self.tx()
        self.receive(132, struct.pack("<II", req.request_id, 1), req.request_id)
        return self

    def begin(self):
        snap = self.c.snapshot()
        c = snap.config
        return self.receive(144, struct.pack(
            "<IIIHHHHBBBBHHIHHI", snap.generation, 1, 1, 1, 2048,
            c.pretrigger_samples, c.pretrigger_samples, 0, 0, c.edge, 0,
            c.level_code, 0, 72000000, 71, 19, 0))

    def abort(self, window=1, reason=1):
        return self.receive(147, struct.pack("<IIHH", self.c.snapshot().generation,
                                            window, reason, 0))

    def end(self, window=1):
        return self.receive(146, struct.pack("<IIHHI", self.c.snapshot().generation,
                                            window, 2048, 0, 123))

    def stop(self):
        self.c.stop(self.now)
        return self.tx()

    def stopped(self, req):
        s = self.c.snapshot()
        return self.receive(133, struct.pack("<IIB3x", s.generation, s.window_id, 0),
                            req.request_id)


class SessionTests(unittest.TestCase):
    def assert_error(self, code, call, *args):
        with self.assertRaises(SessionError) as ctx:
            call(*args)
        self.assertIs(ctx.exception.code, code)
        self.assertLess(len(str(ctx.exception)), 100)

    def test_hello_exact_header_and_bounded_public_objects(self):
        h = Harness()
        t = h.c.prepare_tx()
        self.assertEqual(t.frame, Frame(1, 0, 1, 1, struct.pack("<Q", 100)))
        self.assertEqual(decode_frame(t.encoded[:-1]), t.frame)
        self.assertEqual(h.c.snapshot().source_sha, SOURCE_SHA)
        self.assertIs(h.c.prepare_tx(), t)
        for obj, attr, value in ((t, "offset", 1), (h.c.snapshot(), "session", 2),
                                 (Config(), "level_code", 30)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, attr, value)
        self.assertFalse(hasattr(h.c, "__dict__"))

    def test_full_control_success_and_repeated_stop_without_phantom_abort(self):
        h = Harness().armed()
        self.assertIs(h.c.snapshot().phase, Phase.ARMED)
        for _ in range(2):
            req = h.stop()
            events = h.stopped(req)
            self.assertEqual([e.kind for e in events], [K.STOPPED])
            self.assertIsNone(h.c.snapshot().terminal)
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        h.c.arm(0)

    def test_complete_caps_fields_and_missing_supported_path(self):
        h = Harness(bits=9).ready()
        caps = h.c.snapshot().capabilities
        self.assertEqual(caps.pretrigger_samples, (0,))
        self.assertEqual((caps.timer_clock_hz, caps.adc_clock_hz), (72000000, 12000000))
        self.assertEqual(caps.firmware_build, 77)
        h.c.configure(Config(), 0)
        other = Harness(bits=17).ready()  # A consistent Single-only peer.
        before = other.c.snapshot()
        self.assert_error(ErrorCode.UNSUPPORTED, other.c.configure, Config(), 5)
        self.assertEqual(before, other.c.snapshot())

    def test_every_caps_fixed_field_mask_and_pretrigger_mismatch(self):
        for offset in (0, 1, 2, 4, 8, 10, 12, 16, 18, 20, 21, 22, 23, 24, 26, 28):
            with self.subTest(offset=offset):
                h = Harness()
                h.tx()
                h.receive(129, hello_payload(), 1)
                req = h.tx()
                bad = bytearray(caps_payload())
                bad[offset] ^= 128 if offset in (21, 22, 23) else 1
                events = h.receive(130, bytes(bad), req.request_id)
                self.assertEqual(events[0].reason, R.CAPABILITIES)
                self.assertIs(h.c.snapshot().phase, Phase.RECOVERY)

    def test_hello_values_and_reply_match_fail_closed(self):
        for offset in (0, 1, 2, 3, 4, 6, 15):
            h = Harness()
            h.tx()
            payload = bytearray(hello_payload())
            payload[offset] ^= 128
            self.assertEqual(h.receive(129, bytes(payload), 1)[0].reason, R.CAPABILITIES)
        h = Harness()
        h.tx()
        self.assertEqual(h.receive(129, hello_payload(), 2)[0].reason, R.REPLY)
        h = Harness()
        self.assertEqual(h.receive(129, hello_payload(), 1)[0].reason, R.REPLY)

    def test_hello_cannot_be_preceded_by_status(self):
        h = Harness()
        h.tx()
        self.assertEqual(h.receive(134, bytes(24))[0].reason, R.REPLY)

    def test_partial_zero_writes_and_final_byte_deadline(self):
        h = Harness()
        t = h.c.prepare_tx()
        h.c.written(t, 0, 10000)
        self.assertEqual(h.c.snapshot().next_tx_sequence, 1)
        self.assertIs(t, h.c.prepare_tx())
        h.c.written(t, 1, 10001)
        self.assertEqual(h.c.snapshot().next_tx_sequence, 2)
        partial = h.c.prepare_tx()
        self.assertEqual(partial.offset, 1)
        self.assert_error(ErrorCode.STALE_TICKET, h.c.written, t, 1, 10002)
        self.assertEqual(h.c.tick(999999), ())  # No invented write-stall rule.
        h.c.written(partial, len(partial.encoded) - 1, 1000000)
        self.assertEqual(h.c.tick(1000999), ())
        self.assertEqual(h.c.tick(1001000)[0].reason, R.TIMEOUT)

    def test_stop_never_overtakes_partial_arm(self):
        h = Harness().configured()
        h.c.arm(0)
        t = h.c.prepare_tx()
        h.c.written(t, 1, 0)
        h.c.stop(0)
        partial = h.c.prepare_tx()
        self.assertEqual(partial.frame.message_type, 4)
        h.c.written(partial, len(partial.encoded) - partial.offset, 0)
        stop = h.tx()
        self.assertEqual(stop.message_type, 5)
        self.assertEqual(stop.sequence, t.frame.sequence + 1)
        self.assertEqual(stop.payload, struct.pack("<I", t.frame.request_id))
        self.assertEqual(h.receive(132, struct.pack("<II", t.frame.request_id, 1),
                                   t.frame.request_id), ())
        self.assertTrue(h.c.snapshot().stop_barrier)
        h.stopped(stop)
        self.assertFalse(h.c.snapshot().stop_barrier)

    def test_stop_ack_cannot_precede_earlier_command_reply(self):
        h = Harness().configured()
        h.c.arm(0)
        arm = h.tx()
        stop = h.stop()
        events = h.receive(133, struct.pack("<IIB3x", arm.request_id, 0, 0), stop.request_id)
        self.assertEqual(events[0].reason, R.REPLY)

    def test_rejected_config_preserves_previous_configuration_and_generation(self):
        h = Harness().armed()
        h.stopped(h.stop())
        before = h.c.snapshot()
        h.c.configure(Config(level_code=100), 0)
        req = h.tx()
        events = h.receive(255, struct.pack("<BBHI", 3, 1, 0, before.generation), req.request_id)
        self.assertEqual(events[0].kind, K.COMMAND_ERROR)
        after = h.c.snapshot()
        self.assertEqual((after.config, after.config_id, after.generation),
                         (before.config, before.config_id, before.generation))
        h.c.arm(0)

    def test_config_ack_requires_exact_echo_and_next_id(self):
        for cid, payload in ((2, Config().payload()), (1, Config(level_code=33).payload())):
            h = Harness().ready()
            h.c.configure(Config(), 0)
            req = h.tx()
            self.assertEqual(h.receive(131, struct.pack("<I", cid) + payload,
                                       req.request_id)[0].reason, R.PAYLOAD)

    def test_arm_failure_then_stop_error_recovers_immediately(self):
        h = Harness().configured()
        h.c.arm(0)
        arm = h.tx()
        stop = h.stop()
        h.receive(255, struct.pack("<BBHI", 4, 2, 0, 0), arm.request_id)
        events = h.receive(255, struct.pack("<BBHI", 5, 4, 0, 0), stop.request_id)
        self.assertEqual([e.kind for e in events], [K.COMMAND_ERROR, K.RECOVERY])
        self.assertEqual(h.c.snapshot().pending_stop_id, 0)
        self.assertEqual(h.c.snapshot().generation, 0)
        self.assertIsNone(h.c.prepare_tx())

    def test_waiting_never_transfer_times_out(self):
        h = Harness().armed()
        h.receive(134, struct.pack("<BBHIIIII", 2, 0, 0, 4, 0, 0, 0, 0))
        self.assertEqual(h.c.tick(MAX_TIME_MS), ())
        self.assertIs(h.c.snapshot().phase, Phase.ARMED)

    def test_early_abort_has_ids_reason_and_no_begin_or_samples(self):
        h = Harness().armed()
        events = h.abort()
        self.assertEqual([e.kind for e in events], [K.WINDOW_ABORT])
        self.assertEqual((events[0].generation, events[0].window_id, events[0].reason), (4, 1, 1))
        self.assertIsNone(h.c.snapshot().terminal.samples_crc32)
        self.assertTrue(h.c.snapshot().window_barrier)
        h.stopped(h.stop())
        h.c.arm(0)

    def test_begin_end_is_terminal_admission_not_measurement_success(self):
        h = Harness().armed()
        h.begin()
        events = h.end()
        self.assertEqual([e.kind for e in events], [K.WINDOW_END_ADMITTED])
        self.assertEqual(h.c.snapshot().terminal.samples_crc32, 123)
        self.assertTrue(h.c.snapshot().window_barrier)
        self.assert_error(ErrorCode.STATE, h.c.arm, 0)
        h.stopped(h.stop())
        self.assertEqual(h.c.snapshot().terminal.kind, K.WINDOW_END_ADMITTED)
        h.c.arm(0)

    def test_duplicate_terminal_never_produces_second_event(self):
        for terminal in ("abort", "end"):
            h = Harness().armed()
            if terminal == "end":
                h.begin()
            getattr(h, terminal)()
            last = h.c._last_rx
            self.assertEqual(h.c.receive(last, 0), ())
            self.assertEqual(h.c.receive(replace(last, sequence=last.sequence + 1), 0)[0].reason,
                             R.WINDOW)

    def test_terminal_positive_ids_and_stopped_generation_data(self):
        for window in (0, MAX_ID + 1):
            h = Harness().armed()
            self.assertEqual(h.abort(window=window)[0].reason, R.WINDOW)
        h = Harness().armed()
        h.stopped(h.stop())
        self.assertEqual(h.abort()[0].reason, R.WINDOW)

    def test_data_is_forwarded_bounded_and_downstream_owns_progress_deadline(self):
        h = Harness().armed()
        h.begin()
        h.now = 2999
        data = struct.pack("<IIHHH", 4, 1, 0, 1, 4095)
        events = h.receive(145, data)
        self.assertEqual(events[0].kind, K.WINDOW_DATA)
        self.assertFalse(hasattr(h.c, "samples"))
        self.assertEqual(h.c.tick(5998), ())
        self.assertEqual(h.c.tick(999999), ())
        self.assertEqual(h.c.protocol_fault(FaultCode.DOWNSTREAM, 999999)[0].reason,
                         R.PROTOCOL_FAULT)

    def test_bad_window_metadata_and_codes_fail_closed(self):
        h = Harness().armed()
        h.begin()
        events = h.receive(145, struct.pack("<IIHHH", 4, 1, 0, 1, 4096))
        self.assertEqual(events[0].reason, R.WINDOW)
        h = Harness().armed()
        self.assertEqual(h.end()[0].reason, R.WINDOW)

    def test_stop_ack_needs_terminal_for_begun_window(self):
        h = Harness().armed()
        h.begin()
        req = h.stop()
        self.assertEqual(h.stopped(req)[0].reason, R.WINDOW)

    def test_old_session_ignored_without_even_clock_mutation(self):
        h = Harness().armed()
        before = h.c.snapshot()
        self.assertEqual(h.c.receive(Frame(146, 99, 999, 0, b""), 10000), ())
        self.assertEqual(h.c.snapshot(), before)
        self.assertEqual(h.c.receive(Frame(5, 99, 999, 0, b""), 0)[0].reason, R.DIRECTION)

    def test_sequence_gap_conflict_stale_and_unknown_type(self):
        for mode in ("gap", "conflict", "stale", "unknown", "reply"):
            h = Harness().configured()
            frame = Frame(134, 100, h.rx, 0, struct.pack("<BBHIIIII", 0, 0, 0, 0, 0, 0, 0, 0))
            if mode == "gap":
                frame = replace(frame, sequence=h.rx + 1)
            elif mode == "conflict":
                frame = replace(h.c._last_rx, payload=b"changed")
            elif mode == "stale":
                frame = replace(frame, sequence=1)
            elif mode == "unknown":
                frame = replace(frame, message_type=136)
            else:
                frame = replace(frame, request_id=55)
            events = h.c.receive(frame, 0)
            self.assertEqual(events[0].kind, K.RECOVERY)
            self.assertIs(h.c.snapshot().phase, Phase.RECOVERY)

    def test_invalid_api_arguments_are_nonmutating(self):
        h = Harness().ready()
        for call, args in ((h.c.tick, (True,)), (h.c.tick, (-1,)),
                           (h.c.tick, (MAX_TIME_MS + 1,)),
                           (h.c.configure, (Config(source=1), 100)),
                           (h.c.configure, (Config(profile=2), 100)),
                           (h.c.configure, (Config(pretrigger_samples=1), 100)),
                           (h.c.receive, (b"frame", 100)),
                           (h.c.start, (101, 100)),
                           (h.c.protocol_fault, ("VERSION", 100))):
            before = h.c.snapshot()
            with self.assertRaises(SessionError):
                call(*args)
            self.assertEqual(before, h.c.snapshot())
        h.c.tick(5)
        before = h.c.snapshot()
        self.assert_error(ErrorCode.CLOCK_ROLLBACK, h.c.tick, 4)
        self.assertEqual(before, h.c.snapshot())

    def test_invalid_written_does_not_expire_or_mutate(self):
        h = Harness()
        ticket = h.c.prepare_tx()
        for accepted in (True, -1, len(ticket.encoded) + 1, 545):
            before = h.c.snapshot()
            with self.assertRaises(SessionError):
                h.c.written(ticket, accepted, 10000)
            self.assertEqual(before, h.c.snapshot())
        self.assert_error(ErrorCode.STALE_TICKET, h.c.written, replace(ticket), 0, 1)

    def test_transport_recovery_preserves_fragments_action_and_fresh_nonce(self):
        for method in ("transport_lost", "unknown_write"):
            h = Harness()
            ticket = h.c.prepare_tx()
            h.c.written(ticket, 0, 0)
            events = getattr(h.c, method)(1)
            self.assertEqual(h.c.snapshot().next_tx_sequence, 1)
            self.assertTrue(events[0].preserve_fragments)
            self.assertTrue(events[0].reset_transport)
            self.assertEqual(getattr(h.c, method)(2), ())
            self.assert_error(ErrorCode.STATE, h.c.start, 100, 2)
            h.c.start(101, 2)
            self.assertEqual(h.c.prepare_tx().frame.sequence, 1)
            self.assert_error(ErrorCode.STALE_TICKET, h.c.written, ticket, 1, 2)

    def test_protocol_fault_is_finite_and_triggers_recovery(self):
        for code in FaultCode:
            h = Harness().armed()
            events = h.c.protocol_fault(code, 0)
            self.assertEqual(events[0].fault, code)
            self.assertEqual(events[0].reason, R.PROTOCOL_FAULT)

    def test_max_rx_exact_replay_allowed_before_exhaustion(self):
        h = Harness().armed()
        # White-box boundary setup avoids billions of irrelevant events.
        h.c._next_rx = MAX_ID
        frame = Frame(134, 100, MAX_ID, 0, struct.pack("<BBHIIIII", 2, 0, 0, 4, 0, 0, 0, 0))
        self.assertEqual(h.c.receive(frame, 0)[0].kind, K.STATUS)
        self.assertEqual(h.c.receive(frame, 0), ())
        self.assertEqual(h.c.receive(replace(frame, sequence=MAX_ID + 1), 0)[0].reason,
                         R.EXHAUSTED)

    def test_max_tx_request_never_emit_reserved_uint32_max(self):
        h = Harness().configured()
        h.c._next_tx = h.c._next_request = MAX_ID
        h.c.arm(0)
        frame = h.tx()
        self.assertEqual((frame.sequence, frame.request_id), (MAX_ID, MAX_ID))
        self.assertEqual(h.c.stop(0)[0].reason, R.EXHAUSTED)
        self.assertIsNone(h.c.prepare_tx())

    def test_clock_near_uint64_limit_does_not_overflow_deadline(self):
        h = Harness()
        ticket = h.c.prepare_tx()
        h.c.written(ticket, len(ticket.encoded), MAX_TIME_MS - 999)
        self.assertEqual(h.c.tick(MAX_TIME_MS), ())
        self.assertIs(h.c.snapshot().phase, Phase.HELLO)

    def test_delayed_stopped_status_does_not_cancel_pending_rearm(self):
        h = Harness().armed()
        h.stopped(h.stop())
        h.c.arm(0)
        before = h.c.snapshot()
        h.receive(134, struct.pack("<BBHIIIII", 0, 0, 0, 4, 0, 0, 0, 0))
        after = h.c.snapshot()
        self.assertEqual(before.pending_request_id, after.pending_request_id)
        self.assertFalse(after.stop_barrier)
        req = h.tx()
        self.assertEqual(h.receive(132, struct.pack("<II", req.request_id, 1),
                                   req.request_id)[0].kind, K.ARMED)

    def test_bounded_stress_keeps_one_rx_one_terminal_no_history(self):
        h = Harness().armed()
        for _ in range(20000):
            h.receive(134, struct.pack("<BBHIIIII", 2, 0, 0, 4, 0, 0, 0, 0))
        self.assertIsInstance(h.c._last_rx, Frame)
        self.assertIsNone(h.c._terminal)
        self.assertIsNone(h.c._ticket)
        self.assertFalse(any(isinstance(getattr(h.c, slot), (list, dict, set))
                             for slot in h.c.__slots__))


if __name__ == "__main__":
    unittest.main()

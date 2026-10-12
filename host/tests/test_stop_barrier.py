"""Авторские проверки явной миграции барьера Stop к draft PR52.

Старое поведение c54 остаётся в исторических доказательствах HOST-007.
Здесь проверяется только новая политика; это синтетика, не приёмка прибора.
"""
from dataclasses import FrozenInstanceError
import struct
import unittest

from motortester.device_emulator import (
    DeviceEmulator, ERROR, MAX_CONTROL_DESCRIPTORS, MAX_ID, SOURCE_SHA,
    STOP_BARRIER_POLICY_SHA, STOPPED, TRANSFERRING, WAITING,
)
from motortester.wire import Frame, StreamDecoder, decode_frame, encode_frame


def config(mode=1, *, reserved=0):
    return struct.pack("<HBBBBHHHI", 1, mode, 0, 0, reserved, 2048, 0, 0,
                       100 if mode == 0 else 0)


def request_forms():
    valid = (("GET_CAPS", 2, b""), ("CONFIG", 3, config()),
             ("ARM", 4, bytes(4)), ("STOP", 5, bytes(4)),
             ("GET_STATUS", 6, b""), ("RELEASE", 7, bytes(12)))
    return (valid + tuple(("LENGTH_" + name, kind, payload + b"X")
                          for name, kind, payload in valid)
            + (("RESERVED_CONFIG", 3, config(reserved=1)),)
            + tuple(("UNKNOWN_" + str(kind), kind, b"") for kind in (0, 8, 128)))


class Peer:
    def __init__(self, *, mode=1):
        self.device = DeviceEmulator(all_analog_modes=mode != 1)
        self.mode = mode
        self.sequence = 2
        self.nonce = 77
        self.now = 0
        self.write(Frame(1, 0, 1, 1, struct.pack("<Q", self.nonce)))
        self.frame()

    def write(self, frame):
        raw = encode_frame(frame)
        result = self.device.write(raw, self.now)
        if result.accepted_prefix != len(raw):
            raise AssertionError("Неожиданный backpressure в подготовке теста")
        return raw, result

    def send(self, kind, payload=b""):
        frame = Frame(kind, self.nonce, self.sequence, self.sequence, payload)
        self.sequence += 1
        return self.write(frame)

    def frame(self):
        raw = bytearray()
        for _ in range(544):
            part = self.device.read(1, self.now)
            if not part:
                raise AssertionError("Ожидаемый кадр не допередан")
            raw.extend(part)
            if part == b"\0":
                return decode_frame(bytes(raw[:-1]))
        raise AssertionError("Кадр превышает ограничение")

    def drain_bytes(self):
        result = bytearray()
        for _ in range(16):
            part = self.device.read(4096, self.now)
            if not part:
                return bytes(result)
            result.extend(part)
        raise AssertionError("Вывод не ограничен одним окном")

    def drain(self):
        decoder = StreamDecoder()
        raw = self.drain_bytes()
        frames = []
        for offset in range(0, len(raw), 4096):
            decoded = decoder.feed(raw[offset:offset + 4096], self.now)
            if decoded.diagnostics:
                raise AssertionError(decoded.diagnostics)
            frames.extend(decoded.frames)
        return tuple(frames)

    def arm(self):
        self.send(3, config(self.mode))
        self.drain()
        self.send(4, struct.pack("<I", 1))
        self.drain()
        return self

    def stop(self):
        return self.send(5, struct.pack("<I", self.device.snapshot().generation))

    def capture(self):
        if not self.device.trigger(self.now):
            raise AssertionError("Не удалось подготовить окно")
        frame = self.frame()
        if frame.message_type != 134:
            raise AssertionError("Ожидался событийный STATUS")

    def release(self):
        t = self.device.snapshot().tombstone
        return self.send(7, struct.pack("<III", t.generation, t.window_id, t.sample_crc32))


def barrier(prefix_count, prior_caps=False):
    p = Peer()
    if prior_caps:
        p.send(2)
    stop_raw, _ = p.stop()
    if prior_caps:
        older = p.frame()
        if (older.message_type, older.request_id) != (130, 2):
            raise AssertionError("Прежний CAPS не предшествует Stop_ACK")
    s = p.device.snapshot()
    ack = encode_frame(Frame(133, p.nonce, s.next_tx_sequence,
                             p.sequence - 1, bytes(12)))
    count = {"zero": 0, "one": 1, "last": len(ack) - 1,
             "complete": len(ack)}[prefix_count]
    prefix = p.device.read(count, p.now)
    if prefix != ack[:count]:
        raise AssertionError("Неверный префикс Stop_ACK")
    return p, prefix, stop_raw, ack


class StopBarrierTests(unittest.TestCase):
    def assert_recovered(self, p, old, result):
        s = p.device.snapshot()
        self.assertEqual([d.code for d in result.diagnostics], ["PROTOCOL_RECOVERY"])
        self.assertEqual((s.state, s.reason, s.negotiated), (ERROR, 4, False))
        self.assertFalse(s.stop_barrier)
        self.assertFalse(s.continuation)
        self.assertFalse(s.held_samples)
        self.assertIsNone(s.hold_started_ms)
        self.assertEqual(s.control_descriptors, 0)
        self.assertEqual((s.generation, s.window_id, s.config_id, s.config,
                          s.terminal, s.tombstone, s.dropped_windows, s.data_offset),
                         (old.generation, old.window_id, old.config_id, old.config,
                          old.terminal, old.tombstone, old.dropped_windows, old.data_offset))
        self.assertFalse(p.device.trigger(p.now))

    def test_base_provenance_and_explicit_frozen_policy_delta(self):
        p = Peer()
        self.assertEqual(SOURCE_SHA, "c54bd955aaf3cb863ef3df043ea237ea62e74ad4")
        self.assertEqual(STOP_BARRIER_POLICY_SHA, "2017576dbe104f4c43d8d9feac28436589191fd1")
        _, result = p.send(2)
        for provenance in (p.device.snapshot().provenance, result.provenance):
            self.assertEqual(provenance.source_sha, SOURCE_SHA)
            self.assertEqual(provenance.stop_barrier_policy_sha, STOP_BARRIER_POLICY_SHA)
            self.assertTrue(provenance.timeout_provisional)
            self.assertIn("PROVISIONAL", provenance.timeout_interpretation)
            with self.assertRaises(FrozenInstanceError):
                provenance.stop_barrier_policy_sha = "changed"

    def test_all_96_barred_request_forms_recover_before_dispatch(self):
        cases = 0
        self.assertEqual(len(request_forms()), 16)
        for prior in (False, True):
            for position in ("zero", "one", "last"):
                for name, kind, payload in request_forms():
                    with self.subTest(prior=prior, position=position, request=name):
                        p, prefix, _, ack = barrier(position, prior)
                        old = p.device.snapshot()
                        self.assertTrue(old.stop_barrier)
                        _, result = p.send(kind, payload)
                        self.assert_recovered(p, old, result)
                        output = prefix + p.drain_bytes()
                        self.assertEqual(output, ack if prefix else b"")
                        self.assertEqual(p.device.snapshot().state, ERROR)
                        self.assertIsNone(p.device.snapshot().terminal)
                        self.assertEqual(p.device.snapshot().next_tx_sequence,
                                         old.next_tx_sequence)
                        cases += 1
        self.assertEqual(cases, 96)

    def test_all_forms_are_admitted_after_last_ack_before_status(self):
        for prior in (False, True):
            for name, kind, payload in request_forms():
                with self.subTest(prior=prior, request=name):
                    p, _, _, _ = barrier("complete", prior)
                    s = p.device.snapshot()
                    self.assertEqual((s.state, s.stop_barrier, s.control_descriptors),
                                     (STOPPED, False, 1))
                    request = p.sequence
                    _, result = p.send(kind, payload)
                    self.assertEqual(result.diagnostics, ())
                    frames = p.drain()
                    replies = [f for f in frames if f.request_id == request]
                    self.assertEqual(len(replies), 1)
                    self.assertTrue(p.device.snapshot().negotiated)
                    if name.startswith("LENGTH_") or name == "RESERVED_CONFIG":
                        self.assertEqual((replies[0].message_type, replies[0].payload[1]), (255, 6))
                    elif name.startswith("UNKNOWN_"):
                        self.assertEqual((replies[0].message_type, replies[0].payload[1]), (255, 5))

    def test_duplicate_session_framing_and_hello_exceptions_keep_barrier(self):
        for position in ("zero", "one", "last"):
            for which in ("duplicate", "wrong_session", "bad_crc", "invalid_hello", "same_nonce"):
                with self.subTest(position=position, which=which):
                    p, prefix, stop_raw, ack = barrier(position)
                    old = p.device.snapshot()
                    if which == "duplicate":
                        result = p.device.write(stop_raw, p.now)
                        code = "DUPLICATE"
                    elif which == "wrong_session":
                        _, result = p.write(Frame(6, p.nonce + 1, p.sequence, p.sequence, b""))
                        code = "WRONG_SESSION"
                    elif which == "bad_crc":
                        raw = bytearray(encode_frame(Frame(6, p.nonce, p.sequence, p.sequence, b"")))
                        raw[-2] ^= 1
                        result = p.device.write(bytes(raw), p.now)
                        code = "CRC"
                    else:
                        _, result = p.write(Frame(1, 0, 1, 1,
                                                  bytes(8) if which == "invalid_hello"
                                                  else struct.pack("<Q", p.nonce)))
                        code = "INVALID_HELLO" if which == "invalid_hello" else "DUPLICATE"
                    self.assertEqual([d.code for d in result.diagnostics], [code])
                    current = p.device.snapshot()
                    self.assertTrue(current.negotiated)
                    self.assertTrue(current.stop_barrier)
                    self.assertEqual(current.next_rx_sequence, old.next_rx_sequence)
                    self.assertEqual(current.last_request_id, old.last_request_id)
                    frames = StreamDecoder().feed(prefix + p.drain_bytes(), 0).frames
                    self.assertEqual([f.message_type for f in frames], [133, 134])
                    self.assertEqual(encode_frame(frames[0]), ack)
                    self.assertEqual(p.device.snapshot().state, STOPPED)

    def test_existing_order_and_counter_guards_keep_precedence(self):
        for position in ("zero", "one", "last"):
            for which in ("changed_duplicate", "sequence_gap", "request_regression", "counter_limit"):
                with self.subTest(position=position, which=which):
                    p, prefix, _, ack = barrier(position)
                    sequence = p.sequence - 1 if which == "changed_duplicate" else p.sequence
                    request = p.sequence - 1 if which == "request_regression" else sequence
                    if which == "sequence_gap":
                        sequence += 1
                    if which == "counter_limit":
                        sequence = request = MAX_ID + 1
                    _, result = p.write(Frame(6, p.nonce, sequence, request, b""))
                    s = p.device.snapshot()
                    self.assertFalse(s.negotiated)
                    self.assertEqual(s.state, ERROR)
                    self.assertEqual([d.code for d in result.diagnostics],
                                     ["COUNTER_SATURATED" if which == "counter_limit" else "PROTOCOL_RECOVERY"])
                    frames = StreamDecoder().feed(prefix + p.drain_bytes(), 0).frames
                    if prefix:
                        self.assertEqual(encode_frame(frames[0]), ack)
                        frames = frames[1:]
                    self.assertEqual([f.message_type for f in frames],
                                     [134] if which == "counter_limit" else [])
                    self.assertTrue(all(f.request_id == 0 for f in frames))

    def test_valid_fresh_hello_resets_barrier_and_old_completion_is_inert(self):
        for position in ("zero", "one", "last"):
            with self.subTest(position=position):
                p, prefix, _, ack = barrier(position)
                _, result = p.write(Frame(1, 0, 1, 1, struct.pack("<Q", p.nonce + 1)))
                self.assertEqual(result.diagnostics, ())
                s = p.device.snapshot()
                self.assertEqual((s.state, s.session, s.negotiated, s.stop_barrier),
                                 (STOPPED, p.nonce + 1, True, False))
                frames = StreamDecoder().feed(prefix + p.drain_bytes(), 0).frames
                self.assertEqual([f.message_type for f in frames], [133, 129] if prefix else [129])
                if prefix:
                    self.assertEqual(encode_frame(frames[0]), ack)
                self.assertEqual((frames[-1].session, frames[-1].sequence), (p.nonce + 1, 1))
                self.assertEqual(p.device.snapshot().control_descriptors, 0)
                self.assertEqual(p.device.snapshot().state, STOPPED)

    def test_partial_begin_data_end_abort_stop_release_finish_without_stale_callbacks(self):
        for mode in (0, 1, 2):
            for kind in (144, 145, 146, 147, 133, 135):
                for prefix_size in (1, -1):
                    with self.subTest(mode=mode, kind=kind, prefix_size=prefix_size):
                        p = Peer(mode=mode).arm()
                        if kind != 133:
                            p.capture()
                        if kind in (145, 146):
                            self.assertEqual(p.frame().message_type, 144)
                        if kind == 146:
                            for _ in range(9):
                                self.assertEqual(p.frame().message_type, 145)
                        if kind in (147, 133):
                            p.stop()
                        if kind == 135:
                            p.drain()
                            p.release()
                        prefix = p.device.read(1, p.now)
                        if prefix_size == -1:
                            prefix += p.device.read(p.device.snapshot().tx_size - 2, p.now)
                        if kind not in (147, 133):
                            p.stop()
                        old = p.device.snapshot()
                        self.assertTrue(old.stop_barrier)
                        _, result = p.send(6)
                        self.assert_recovered(p, old, result)
                        frames = StreamDecoder().feed(prefix + p.drain_bytes(), 0).frames
                        self.assertEqual([f.message_type for f in frames], [kind])
                        after = p.device.snapshot()
                        self.assertEqual(after.state, ERROR)
                        self.assertEqual(after.control_descriptors, 0)
                        self.assertEqual((after.terminal, after.tombstone, after.data_offset),
                                         (old.terminal, old.tombstone, old.data_offset))
                        p.now = 10000
                        self.assertEqual(p.device.tick(p.now), ())
                        self.assertFalse(p.device.trigger(p.now))
                        self.assertEqual(p.drain_bytes(), b"")
                        self.assertEqual(p.device.snapshot().window_id, old.window_id)

    def test_transferring_without_stop_does_not_trigger_stop_recovery(self):
        for mode in (0, 1, 2):
            with self.subTest(mode=mode):
                p = Peer(mode=mode).arm()
                p.capture()
                self.assertEqual(p.device.snapshot().state, TRANSFERRING)
                self.assertFalse(p.device.snapshot().stop_barrier)
                for kind in (2, 6):
                    request = p.sequence
                    _, result = p.send(kind)
                    self.assertEqual(result.diagnostics, ())
                    self.assertEqual(p.frame().request_id, request)
                    self.assertTrue(p.device.snapshot().negotiated)
                self.assertEqual([f.message_type for f in p.drain()], [144] + [145] * 9 + [146, 134])

    def test_prior_mandatory_replies_precede_stop_ack_without_new_violation(self):
        for kind, payload in ((2, b""), (3, config()), (6, b""), (8, b"")):
            with self.subTest(kind=kind):
                p = Peer()
                request = p.sequence
                p.send(kind, payload)
                p.stop()
                frames = p.drain()
                self.assertEqual([f.message_type for f in frames],
                                 [{2: 130, 3: 131, 6: 134, 8: 255}[kind], 133, 134])
                self.assertEqual(frames[0].request_id, request)
                self.assertEqual(p.device.snapshot().state, STOPPED)

    def test_complete_stop_allows_arm_without_reading_queued_status(self):
        for mode in (0, 1, 2):
            with self.subTest(mode=mode):
                p = Peer(mode=mode).arm()
                p.stop()
                self.assertEqual(p.frame().message_type, 133)
                s = p.device.snapshot()
                self.assertEqual((s.state, s.control_descriptors, s.stop_barrier), (STOPPED, 1, False))
                p.send(4, struct.pack("<I", 1))
                self.assertEqual([f.message_type for f in p.drain()], [132, 134])
                self.assertEqual(p.device.snapshot().state, WAITING)
                self.assertTrue(p.device.trigger(0))

    def test_zero_read_and_fragmented_request_use_actual_ack_completion(self):
        for complete_first in (False, True):
            with self.subTest(complete_first=complete_first):
                p, prefix, _, ack = barrier("last")
                before = p.device.snapshot()
                self.assertEqual(p.device.read(0, 0), b"")
                self.assertEqual(p.device.snapshot(), before)
                raw = encode_frame(Frame(6, p.nonce, p.sequence, p.sequence, b""))
                result = p.device.write(raw[:-1], 0)
                self.assertEqual(result.accepted_prefix, len(raw) - 1)
                self.assertEqual(result.diagnostics, ())
                if complete_first:
                    prefix += p.device.read(1, 0)
                    self.assertEqual(prefix, ack)
                result = p.device.write(raw[-1:], 0)
                self.assertEqual(p.device.snapshot().negotiated, complete_first)
                self.assertEqual([d.code for d in result.diagnostics],
                                 [] if complete_first else ["PROTOCOL_RECOVERY"])

    def test_recovery_burst_is_bounded_and_cannot_capture_or_rearm(self):
        p = Peer(mode=0).arm()
        p.stop()
        _, result = p.send(6)
        self.assertEqual([d.code for d in result.diagnostics], ["PROTOCOL_RECOVERY"])
        for i in range(200):
            p.now = i * 10000
            _, result = p.send(4, struct.pack("<I", 1))
            self.assertEqual([d.code for d in result.diagnostics], ["NEED_HELLO"])
            self.assertEqual(p.drain_bytes(), b"")
            s = p.device.snapshot()
            self.assertLessEqual(s.control_descriptors, MAX_CONTROL_DESCRIPTORS)
            self.assertEqual((s.rx_used, s.tx_size, s.window_id), (0, 0, 0))
            self.assertEqual(s.sample_buffer_bytes, 4096)
            self.assertEqual(s.state, ERROR)
            self.assertFalse(p.device.trigger(p.now))


if __name__ == "__main__":
    unittest.main()

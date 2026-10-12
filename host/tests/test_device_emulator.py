"""Авторские проверки HOST-007; синтетика, не приёмка устройства."""
from dataclasses import FrozenInstanceError
import gc
import struct
import tracemalloc
import unittest
import zlib

from motortester.device_emulator import (
    DeviceEmulator, DeviceError, ERROR, ErrorCode, HOLD_TIMEOUT_MS, MAX_COUNTER, MAX_ID,
    MAX_TIME_MS, SOURCE_SHA, STOPPED, TRANSFERRING, WAITING, WAIT_RELEASE,
)
from motortester.session import Config, EventKind, Phase, SessionController
from motortester.wire import Frame, StreamDecoder, decode_frame, encode_frame


def config(edge=0, level=2048):
    return struct.pack("<HBBBBHHHI", 1, 1, 0, edge, 0, level, 0, 0, 0)


class Peer:
    def __init__(self):
        self.d = DeviceEmulator()
        self.now = 0
        self.sequence = self.request = 1
        self.nonce = 100
        self.send(1, struct.pack("<Q", self.nonce))
        self.drain()

    def send(self, kind, payload=b"", *, request=None, session=None):
        if request is None:
            request = self.request
        if session is None:
            session = 0 if kind == 1 else self.nonce
        data = encode_frame(Frame(kind, session, self.sequence, request, payload))
        result = self.d.write(data, self.now)
        if result.accepted_prefix != len(data):
            raise AssertionError("Unexpected backpressure in test setup")
        self.sequence += 1
        self.request = request + 1
        return request, data

    def frame(self):
        output = bytearray()
        while True:
            part = self.d.read(1, self.now)
            if not part:
                if output:
                    raise AssertionError("Truncated output")
                return None
            output.extend(part)
            if part == b"\0":
                return decode_frame(bytes(output[:-1]))

    def drain(self):
        result = []
        while True:
            frame = self.frame()
            if frame is None:
                return result
            result.append(frame)

    def arm(self, edge=0, level=2048):
        self.send(2)
        self.drain()
        self.send(3, config(edge, level))
        self.drain()
        self.send(4, struct.pack("<I", self.d.snapshot().config_id))
        self.drain()
        return self

    def trigger(self):
        triggered = self.d.trigger(self.now)
        if not triggered:
            raise AssertionError("Trigger rejected in test setup")
        status = self.frame()
        if status.message_type != 134:
            raise AssertionError("Expected transfer status")

    def stop(self):
        self.send(5, struct.pack("<I", self.d.snapshot().generation))

    def release(self, tombstone=None):
        t = tombstone or self.d.snapshot().tombstone
        return self.send(7, struct.pack("<III", t.generation, t.window_id, t.sample_crc32))


class DeviceEmulatorTests(unittest.TestCase):
    def test_control_payloads_and_immutable_provenance(self):
        p = Peer().arm()
        s = p.d.snapshot()
        self.assertEqual(s.state, WAITING)
        self.assertEqual(s.config, config())
        self.assertTrue(s.provenance.simulator)
        self.assertTrue(s.provenance.draft)
        self.assertTrue(s.provenance.nominal_timing)
        self.assertTrue(s.provenance.synthetic_waveform)
        self.assertTrue(s.provenance.timeout_provisional)
        self.assertIn("PROVISIONAL", s.provenance.timeout_interpretation)
        self.assertEqual(s.provenance.source_sha, SOURCE_SHA)
        self.assertFalse(hasattr(p.d, "__dict__"))
        for obj, attr in ((s, "state"), (s.provenance, "simulator")):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, attr, 0)
        p.stop()
        frames = p.drain()
        self.assertEqual([f.message_type for f in frames], [133, 134])
        self.assertEqual(p.d.snapshot().state, STOPPED)
        self.assertIsNone(p.d.snapshot().terminal)

    def test_twenty_host006_closed_loop_windows_and_raw_crc(self):
        d, c, decoder = DeviceEmulator(), SessionController(), StreamDecoder()
        received = []

        def pump():
            while True:
                progress = False
                ticket = c.prepare_tx()
                if ticket is not None:
                    result = d.write(ticket.encoded[ticket.offset:], 0)
                    c.written(ticket, result.accepted_prefix, 0)
                    progress = bool(result.accepted_prefix)
                data = d.read(137, 0)
                if data:
                    progress = True
                    decoded = decoder.feed(data, 0)
                    self.assertEqual(decoded.diagnostics, ())
                    for frame in decoded.frames:
                        received.append(frame)
                        events = c.receive(frame, 0)
                        self.assertFalse(any(e.kind == EventKind.RECOVERY for e in events))
                if not progress:
                    return

        c.start(99, 0)
        pump()
        c.configure(Config(), 0)
        pump()
        for window in range(1, 21):
            start = len(received)
            c.arm(0)
            pump()
            self.assertTrue(d.trigger(0))
            pump()
            frames = received[start:]
            begin = next(f for f in frames if f.message_type == 144)
            end = next(f for f in frames if f.message_type == 146)
            self.assertEqual(struct.unpack_from("<I", begin.payload, 4)[0], window)
            raw = bytearray()
            for frame in frames:
                if frame.message_type == 145:
                    offset, count = struct.unpack_from("<HH", frame.payload, 8)
                    self.assertEqual(offset * 2, len(raw))
                    self.assertEqual(len(frame.payload), 12 + count * 2)
                    raw.extend(frame.payload[12:])
            self.assertEqual(len(raw), 4096)
            self.assertEqual(zlib.crc32(raw), struct.unpack_from("<I", end.payload, 12)[0])
            generation = c.snapshot().generation
            expected = [(73 * i + 19 * window + 7 * generation) & 4095 for i in range(2048)]
            expected[0], expected[511], expected[1023] = 2048, 0, 4095
            self.assertEqual(list(struct.unpack("<2048H", raw)), expected)
            self.assertEqual(c.snapshot().terminal.kind, EventKind.WINDOW_END_ADMITTED)
            c.stop(0)
            pump()
            self.assertEqual(c.snapshot().phase, Phase.READY)
            self.assertEqual(d.snapshot().state, STOPPED)

    def test_falling_trigger_and_rail_metadata(self):
        for edge in (0, 1):
            for level in (1, 4094):
                p = Peer().arm(edge, level)
                p.trigger()
                frames = p.drain()
                begin = frames[0]
                raw = b"".join(f.payload[12:] for f in frames if f.message_type == 145)
                self.assertEqual(struct.unpack_from("<H", begin.payload, 18)[0], 0)
                self.assertEqual(begin.payload[20:24], bytes((0, 0, edge, 0)))
                self.assertEqual(struct.unpack_from("<H", begin.payload, 26)[0], 2)
                self.assertEqual(struct.unpack_from("<H", raw)[0], level if edge == 0 else level - 1)
                self.assertIn(0, struct.unpack("<2048H", raw))
                self.assertIn(4095, struct.unpack("<2048H", raw))

    def test_stop_at_every_frame_boundary_preserves_one_terminal(self):
        for target in (144, 145, 146):
            for boundary in ("before", "first", "last", "complete"):
                with self.subTest(target=target, boundary=boundary):
                    p = Peer().arm()
                    p.trigger()
                    old = []
                    if target != 144:
                        old.append(p.frame())
                    if target == 146:
                        old.extend(p.frame() for _ in range(9))
                    prefix = b""
                    if boundary != "before":
                        prefix = p.d.read(1, 0)
                        remaining = p.d.snapshot().tx_size - 1
                        if boundary == "last":
                            prefix += p.d.read(remaining - 1, 0)
                        elif boundary == "complete":
                            prefix += p.d.read(remaining, 0)
                    before = p.d.snapshot().next_tx_sequence
                    self.assertEqual(p.d.read(0, 0), b"")
                    self.assertEqual(p.d.snapshot().next_tx_sequence, before)
                    p.stop()
                    data = bytearray(prefix)
                    while True:
                        chunk = p.d.read(17, 0)
                        if not chunk:
                            break
                        data.extend(chunk)
                    decoded = StreamDecoder().feed(bytes(data), 0)
                    self.assertEqual(decoded.diagnostics, ())
                    frames = old + list(decoded.frames)
                    terminal = [f for f in frames if f.message_type in (146, 147)]
                    expected = 146 if target == 146 and boundary != "before" else 147
                    self.assertEqual([f.message_type for f in terminal], [expected])
                    ack = next(i for i, f in enumerate(frames) if f.message_type == 133)
                    self.assertFalse(any(f.message_type in (144, 145, 146, 147) for f in frames[ack + 1:]))
                    self.assertEqual([f.sequence for f in frames], list(range(frames[0].sequence, frames[-1].sequence + 1)))
                    self.assertEqual(p.d.snapshot().state, STOPPED)
                    self.assertFalse(p.d.snapshot().held_samples)

    def test_early_abort_prior_arm_reply_and_repeated_stop(self):
        # c54 допускал два ещё не подтверждённых Stop: [132,147,133,133,134].
        # Явная миграция PR52: новый Stop разрешён только после первого ACK.
        for premature in (False, True):
            with self.subTest(premature=premature):
                p = Peer().arm()
                p.stop()
                p.drain()
                p.send(4, struct.pack("<I", 1))
                self.assertTrue(p.d.trigger(0))
                p.stop()
                if premature:
                    p.stop()
                    self.assertEqual(p.drain(), [])
                    self.assertFalse(p.d.snapshot().negotiated)
                    self.assertEqual(p.d.snapshot().state, ERROR)
                    self.assertIsNone(p.d.snapshot().terminal)
                else:
                    frames = p.drain()
                    self.assertEqual([f.message_type for f in frames], [132, 147, 133, 134])
                    p.stop()
                    self.assertEqual([f.message_type for f in p.drain()], [133, 134])
                self.assertEqual(p.d.snapshot().dropped_windows, 1)

    def test_stop_ack_last_byte_is_drain_barrier(self):
        # c54 отвечал BUSY на ранний ARM; PR52 требует восстановления без ERROR.
        for premature in (False, True):
            with self.subTest(premature=premature):
                p = Peer().arm()
                p.stop()
                prefix = p.d.read(1, 0)
                remaining = p.d.snapshot().tx_size - 1
                prefix += p.d.read(remaining - 1, 0)
                self.assertTrue(p.d.snapshot().stop_barrier)
                self.assertEqual(p.d.snapshot().state, TRANSFERRING)
                if premature:
                    p.send(4, struct.pack("<I", 1))
                prefix += p.d.read(1, 0)
                self.assertEqual(decode_frame(prefix[:-1]).message_type, 133)
                self.assertFalse(p.d.snapshot().stop_barrier)
                if premature:
                    self.assertFalse(p.d.snapshot().negotiated)
                    self.assertEqual(p.d.snapshot().state, ERROR)
                    self.assertEqual(p.drain(), [])
                    self.assertFalse(p.d.trigger(0))
                else:
                    self.assertEqual(p.d.snapshot().state, STOPPED)
                    self.assertEqual(p.d.snapshot().control_descriptors, 1)
                    p.send(4, struct.pack("<I", 1))
                    self.assertEqual([f.message_type for f in p.drain()], [132, 134])
                    self.assertEqual(p.d.snapshot().state, WAITING)

    def test_release_rearms_only_on_final_ack_byte_and_tombstone_replacement(self):
        p = Peer().arm()
        p.trigger()
        p.drain()
        first = p.d.snapshot().tombstone
        p.release()
        self.assertFalse(p.d.snapshot().held_samples)
        prefix = p.d.read(1, 0)
        prefix += p.d.read(p.d.snapshot().tx_size - 2, 0)
        self.assertFalse(p.d.trigger(0))
        prefix += p.d.read(1, 0)
        self.assertEqual(decode_frame(prefix[:-1]).message_type, 135)
        self.assertEqual(p.d.snapshot().state, WAITING)
        p.drain()
        p.release(first)
        p.drain()
        self.assertEqual(p.d.snapshot().state, WAITING)
        p.trigger()
        p.release(first)  # Old tombstone must not free the new active sample buffer.
        self.assertTrue(p.d.snapshot().held_samples)
        p.drain()
        second = p.d.snapshot().tombstone
        self.assertEqual(second.window_id, first.window_id + 1)
        p.release(first)
        self.assertEqual(p.drain()[0].payload[1], 7)
        p.release(second)
        p.drain()
        self.assertEqual(p.d.snapshot().state, WAITING)

    def test_release_wrong_crc_late_after_stop_and_stop_during_ack(self):
        for partial in (False, True):
            p = Peer().arm()
            p.trigger()
            p.drain()
            t = p.d.snapshot().tombstone
            p.send(7, struct.pack("<III", t.generation, t.window_id, t.sample_crc32 ^ 1))
            self.assertEqual(p.drain()[0].payload[1], 7)
            p.release()
            prefix = p.d.read(1, 0) if partial else b""
            p.stop()
            stream = bytearray(prefix)
            while True:
                chunk = p.d.read(4096, 0)
                if not chunk:
                    break
                stream.extend(chunk)
            frames = StreamDecoder().feed(bytes(stream), 0).frames
            self.assertEqual([f.message_type for f in frames], [135, 133, 134])
            self.assertEqual(p.d.snapshot().state, STOPPED)
            p.release(t)
            self.assertEqual(p.drain()[0].message_type, 135)
            self.assertEqual(p.d.snapshot().state, STOPPED)
            self.assertFalse(p.d.trigger(0))

    def test_fresh_hello_partial_end_and_stop_ack_callbacks_are_inert(self):
        for target in (144, 145, 146, 133, 135):
            p = Peer().arm()
            p.trigger()
            if target in (145, 146, 133, 135):
                p.frame()
            if target in (146, 133, 135):
                for _ in range(9):
                    p.frame()
            if target in (133, 135):
                p.frame()
                p.drain()
                if target == 133:
                    p.stop()
                else:
                    p.release()
            prefix = p.d.read(1, 0)
            hello = encode_frame(Frame(1, 0, 1, 1, struct.pack("<Q", 200)))
            result = p.d.write(hello, 0)
            self.assertEqual(result.accepted_prefix, len(hello))
            stream = prefix + p.d.read(4096, 0)
            frames = StreamDecoder().feed(stream, 0).frames
            self.assertEqual([f.message_type for f in frames], [target, 129])
            self.assertEqual([f.session for f in frames], [100, 200])
            self.assertEqual(frames[-1].sequence, 1)
            s = p.d.snapshot()
            self.assertEqual((s.state, s.generation, s.config_id, s.window_id), (0, 0, 0, 0))
            self.assertFalse(s.held_samples)
            self.assertIsNone(s.terminal)
            self.assertIsNone(s.hold_started_ms)

    def test_fragmentation_coalescing_and_capacity_suffix_is_caller_owned(self):
        d = DeviceEmulator()
        hello = encode_frame(Frame(1, 0, 1, 1, struct.pack("<Q", 5)))
        for byte in hello:
            self.assertEqual(d.write(bytes((byte,)), 0).accepted_prefix, 1)
        d.read(4096, 0)
        packets = [encode_frame(Frame(6, 5, i, i, b"")) for i in range(2, 52)]
        pending = b"".join(packets)
        output = bytearray()
        blocked = False
        while pending:
            r = d.write(pending, 0)
            blocked |= r.accepted_prefix < len(pending)
            pending = pending[r.accepted_prefix:]
            self.assertLessEqual(d.snapshot().control_descriptors, 8)
            output.extend(d.read(4096, 0))
        output.extend(d.read(4096, 0))
        self.assertTrue(blocked)
        frames = StreamDecoder().feed(bytes(output), 0).frames
        self.assertEqual([f.request_id for f in frames], list(range(2, 52)))
        self.assertEqual([f.sequence for f in frames], list(range(2, 52)))

    def test_invalid_arguments_fail_before_clock_or_state_changes(self):
        p = Peer().arm()
        for call, args in (
            (p.d.write, (bytearray(b"x"), 1)), (p.d.write, (b"x" * 4097, 1)),
            (p.d.write, (b"", True)), (p.d.read, (True, 1)),
            (p.d.read, (4097, 1)), (p.d.read, (-1, 1)),
            (p.d.trigger, (1.0,)), (p.d.tick, (-1,)),
            (p.d.disconnect, (MAX_TIME_MS + 1,)),
        ):
            before = p.d.snapshot()
            with self.assertRaises(DeviceError):
                call(*args)
            self.assertEqual(p.d.snapshot(), before)
        p.d.tick(10)
        for call, args in ((p.d.write, (b"", 9)), (p.d.read, (0, 9)),
                           (p.d.tick, (9,)), (p.d.trigger, (9,)), (p.d.disconnect, (9,))):
            before = p.d.snapshot()
            with self.assertRaises(DeviceError) as ctx:
                call(*args)
            self.assertEqual(ctx.exception.code, ErrorCode.CLOCK_ROLLBACK)
            self.assertEqual(p.d.snapshot(), before)

    def test_framing_payload_replay_session_direction_unknown_and_recovery(self):
        p = Peer()
        before = p.d.snapshot()
        result = p.d.write(b"\xff\x01\0", 0)
        self.assertTrue(result.diagnostics)
        self.assertEqual(p.d.snapshot().next_rx_sequence, before.next_rx_sequence)
        self.assertEqual(p.d.read(4096, 0), b"")
        request, data = p.send(3, b"bad")
        reply = p.drain()[0]
        self.assertEqual((reply.message_type, reply.request_id, reply.payload[1]), (255, request, 6))
        result = p.d.write(data, 0)
        self.assertEqual(result.diagnostics[0].code, "DUPLICATE")
        self.assertEqual(p.d.read(4096, 0), b"")
        wrong = encode_frame(Frame(6, 999, p.sequence, p.request, b""))
        self.assertEqual(p.d.write(wrong, 0).diagnostics[0].code, "WRONG_SESSION")
        p.send(136)
        self.assertEqual(p.drain()[0].payload[1], 5)
        wrong_direction = encode_frame(Frame(129, 999, 99, 99, b""))
        p.d.write(wrong_direction, 0)
        self.assertFalse(p.d.snapshot().negotiated)
        p.d.write(encode_frame(Frame(1, 0, 1, 1, struct.pack("<Q", 100))), 0)
        self.assertFalse(p.d.snapshot().negotiated)
        p.d.write(encode_frame(Frame(1, 0, 1, 1, struct.pack("<Q", 200))), 0)
        self.assertTrue(p.d.snapshot().negotiated)

    def test_config_errors_keep_previous_and_wait_release_is_busy(self):
        p = Peer().arm()
        p.trigger()
        p.drain()
        p.send(3, config())
        self.assertEqual(p.drain()[0].payload[1], 2)
        p.send(4, struct.pack("<I", 1))
        self.assertEqual(p.drain()[0].payload[1], 2)
        p.stop()
        p.drain()
        before = p.d.snapshot().config
        bad = bytearray(config())
        bad[5] = 1
        p.send(3, bytes(bad))
        self.assertEqual(p.drain()[0].payload[1], 6)
        bad[5], bad[2] = 0, 0
        p.send(3, bytes(bad))
        self.assertEqual(p.drain()[0].payload[1], 1)
        self.assertEqual(p.d.snapshot().config, before)

    def test_provisional_hold_deadline_last_end_byte_exact_and_stop_release_races(self):
        for elapsed, state in ((4999, WAIT_RELEASE), (5000, ERROR), (5001, ERROR)):
            p = Peer().arm()
            p.trigger()
            p.frame()
            for _ in range(9):
                p.frame()
            first = p.d.read(1, 100)
            remaining = p.d.snapshot().tx_size - 1
            p.d.tick(10000)
            self.assertIsNone(p.d.snapshot().hold_started_ms)
            last = p.d.read(remaining, 10001)
            self.assertEqual(decode_frame((first + last)[:-1]).message_type, 146)
            self.assertEqual(p.d.snapshot().hold_started_ms, 10001)
            p.d.tick(10001 + elapsed)
            self.assertEqual(p.d.snapshot().state, state)
            self.assertEqual(p.d.snapshot().terminal.outcome, "END")
            self.assertEqual(p.d.snapshot().terminal, p.d.snapshot().tombstone)
            self.assertTrue(p.d.snapshot().provenance.timeout_provisional)
            if state == ERROR:
                self.assertEqual(p.d.snapshot().reason, 3)
                p.now = 10001 + elapsed
                p.release()
                p.drain()
                self.assertEqual(p.d.snapshot().state, ERROR)
                self.assertFalse(p.d.trigger(p.now))
                p.stop()
                self.assertFalse(any(f.message_type in (146, 147) for f in p.drain()))
        for command in ("stop", "release"):
            p = Peer().arm()
            p.trigger()
            p.drain()
            p.now = HOLD_TIMEOUT_MS - 1
            getattr(p, command)()
            p.drain()
            p.d.tick(HOLD_TIMEOUT_MS)
            self.assertEqual(p.d.snapshot().state, STOPPED if command == "stop" else WAITING)

    def test_rx_timeout_discard_and_disconnect_forgets_nonce(self):
        p = Peer()
        p.d.write(b"\x04\x01", 0)
        diagnostics = p.d.tick(1000)
        self.assertEqual(diagnostics[0].code, "TIMEOUT")
        p.d.write(b"xx\0", 1000)
        p.d.disconnect(1000)
        self.assertEqual(p.d.snapshot().session, 0)
        hello = encode_frame(Frame(1, 0, 1, 1, struct.pack("<Q", 100)))
        p.d.write(hello, 1000)
        self.assertEqual(p.d.snapshot().session, 100)

    def test_counter_boundaries_without_wrap(self):
        p = Peer()
        p.d._next_rx = MAX_ID
        p.d._last_request = MAX_ID - 1
        packet = encode_frame(Frame(6, 100, MAX_ID, MAX_ID, b""))
        p.d.write(packet, 0)
        p.drain()
        self.assertEqual(p.d.write(packet, 0).diagnostics[0].code, "DUPLICATE")
        p.d.write(encode_frame(Frame(6, 100, MAX_ID + 1, MAX_ID + 1, b"")), 0)
        self.assertEqual(p.d.snapshot().reason, 6)
        self.assertFalse(p.d.snapshot().negotiated)
        p = Peer().arm()
        p.d._window_id = MAX_COUNTER
        self.assertFalse(p.d.trigger(0))
        self.assertEqual(p.d.snapshot().reason, 6)
        self.assertEqual(p.drain()[0].payload[1], 6)
        p = Peer()
        p.d._config_id = MAX_COUNTER - 1
        p.send(3, config())
        self.assertEqual(struct.unpack_from("<I", p.drain()[0].payload)[0], MAX_COUNTER)
        p.send(3, config())
        self.assertEqual(p.d.snapshot().reason, 6)
        p = Peer().arm()
        p.d._window_id = MAX_COUNTER - 1
        p.trigger()
        self.assertEqual(p.d.snapshot().window_id, MAX_COUNTER)
        p.stop()
        p.drain()
        self.assertEqual(p.d.snapshot().window_id, MAX_COUNTER)
        p = Peer()
        p.d._next_tx = MAX_ID
        p.send(6)
        self.assertEqual(p.frame().sequence, MAX_ID)
        self.assertEqual(p.d.read(1, 0), b"")
        self.assertEqual(p.d.snapshot().reason, 6)

    def test_retained_memory_stays_bounded_across_many_commands(self):
        p = Peer()
        tracemalloc.start()
        for _ in range(100):
            p.send(6)
            p.drain()
        gc.collect()
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(1000):
            p.send(6)
            p.drain()
        gc.collect()
        after = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        self.assertLess(after - before, 16000)
        self.assertEqual(len(p.d._samples), 4096)
        self.assertEqual(p.d.snapshot().control_descriptors, 0)


if __name__ == "__main__":
    unittest.main()

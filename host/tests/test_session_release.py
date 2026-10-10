"""Авторские проверки HOST-008: реальный byte-peer, синтетические окна."""
from dataclasses import FrozenInstanceError, replace
import struct
import unittest
import zlib

from motortester.device_emulator import DeviceEmulator, STOPPED, WAITING
from motortester.session import (
    Config, ErrorCode, EventKind as K, MAX_ID, Phase, RecoveryReason as R,
    SessionController, SessionError, WindowReceipt,
)
from motortester.window import ExpectedContext, SampleBlock, WindowCode, WindowReceiver
from motortester.wire import Frame, StreamDecoder


class BytePeer:
    """Каждый байт проходит настоящий encoder/decoder; нет скрытых assert."""

    def __init__(self):
        self.c, self.d, self.decoder = SessionController(), DeviceEmulator(), StreamDecoder()
        self.now = 0
        self.requests, self.frames, self.events, self.results = [], [], [], []
        self.context = self.receiver = None
        self.c.start(101, self.now)
        self.pump()
        self.c.configure(Config(), self.now)
        self.pump()
        self.c.arm(self.now)
        self.pump()

    def write(self, count=None):
        ticket = self.c.prepare_tx()
        if ticket is None:
            raise AssertionError("Ожидалась команда")
        data = ticket.encoded[ticket.offset:]
        if count is not None:
            data = data[:count]
        result = self.d.write(data, self.now)
        if result.diagnostics or result.accepted_prefix != len(data):
            raise AssertionError("Неожиданная ошибка записи byte-peer")
        if ticket.offset == 0 and result.accepted_prefix:
            self.requests.append(ticket.frame)
        self.c.written(ticket, result.accepted_prefix, self.now)
        return ticket

    def accept(self, frame):
        self.frames.append(frame)
        events = self.c.receive(frame, self.now)
        self.events.extend(events)
        if any(event.kind is K.RECOVERY for event in events):
            raise AssertionError(f"Неожиданное восстановление: {events}")
        for event in events:
            if event.kind is K.WINDOW_BEGIN:
                s = self.c.snapshot()
                self.context = ExpectedContext(
                    s.session, s.generation, s.config_id, s.config.mode,
                    s.config.source, s.config.edge, s.config.level_code,
                    s.config.pretrigger_samples, s.config.profile)
                self.receiver = WindowReceiver(self.context)
            if event.kind in (K.WINDOW_BEGIN, K.WINDOW_DATA, K.WINDOW_END_ADMITTED,
                               K.WINDOW_ABORT) and self.receiver is not None:
                result = self.receiver.feed(event.frame, self.now)
                if result is not None:
                    self.results.append(result)
        return events

    def read_one(self):
        while True:
            data = self.d.read(1, self.now)
            if not data:
                return None
            decoded = self.decoder.feed(data, self.now)
            if decoded.diagnostics:
                raise AssertionError("Неожиданная ошибка декодера")
            if decoded.frames:
                frame = decoded.frames[0]
                self.accept(frame)
                return frame

    def drain(self):
        while self.read_one() is not None:
            pass

    def pump(self):
        while self.c.prepare_tx() is not None:
            self.write()
            self.drain()

    def window(self):
        if not self.d.trigger(self.now):
            raise AssertionError("Byte-peer отверг запуск")
        self.drain()
        if not self.results or not self.results[-1].complete:
            raise AssertionError("Ожидалось полное окно")
        return WindowReceipt.from_window(self.context, self.results[-1])

    def reply(self, kind, payload, request=None):
        snap = self.c.snapshot()
        return Frame(kind, snap.session, snap.next_rx_sequence,
                     snap.pending_request_id if request is None else request, payload)


def receive_outcome(context, frames):
    receiver = WindowReceiver(context)
    for frame in frames:
        result = receiver.feed(frame, 0)
        if result is not None:
            return result
    raise AssertionError("Ожидался конечный результат приёмника")


class ReleaseTests(unittest.TestCase):
    def reject(self, code, function, *args):
        with self.assertRaises(SessionError) as caught:
            function(*args)
        self.assertIs(caught.exception.code, code)

    def test_twenty_real_windows_one_arm_unchanged_generation(self):
        h = BytePeer()
        generation = h.c.snapshot().generation
        for window in range(1, 21):
            receipt = h.window()
            result = h.results[-1]
            expected = [(73 * i + 19 * window + 7 * generation) & 4095
                        for i in range(2048)]
            expected[0], expected[511], expected[1023] = 2048, 0, 4095
            self.assertEqual(result.samples, tuple(expected))
            crc = zlib.crc32(struct.pack("<2048H", *expected))
            self.assertEqual((receipt.generation, receipt.window_id,
                              receipt.sample_count, receipt.samples_crc32),
                             (generation, window, 2048, crc))
            self.assertTrue(h.c.snapshot().window_barrier)
            h.c.release(receipt, h.now)
            self.assertTrue(h.c.snapshot().window_barrier)
            h.pump()
            self.assertFalse(h.c.snapshot().window_barrier)
            self.assertEqual(h.c.snapshot().generation, generation)
            self.assertIs(h.c.snapshot().phase, Phase.ARMED)
            self.assertEqual(h.d.snapshot().state, WAITING)
        self.assertEqual([r.message_type for r in h.requests].count(4), 1)
        self.assertEqual([r.message_type for r in h.requests].count(7), 20)
        self.assertEqual([e.kind for e in h.events].count(K.ARMED), 1)
        self.assertEqual([e.kind for e in h.events].count(K.WINDOW_RELEASED), 20)
        self.assertFalse(hasattr(h.c, "__dict__"))

    def test_factory_receipt_frozen_and_direct_or_replaced_receipt_rejected(self):
        h = BytePeer()
        valid = h.window()
        with self.assertRaises(FrozenInstanceError):
            valid.window_id = 2
        direct = WindowReceipt(valid.session, valid.generation, valid.window_id,
                               valid.sample_count, valid.samples_crc32)
        for receipt in (direct, replace(valid), replace(valid, samples_crc32=0)):
            before = h.c.snapshot()
            self.reject(ErrorCode.STATE, h.c.release, receipt, 99)
            self.assertEqual(h.c.snapshot(), before)
        h.c.release(valid, 0)
        self.reject(ErrorCode.STATE, h.c.release, valid, 0)
        h.pump()
        self.reject(ErrorCode.STATE, h.c.release, valid, 0)

    def test_real_receiver_loss_reordering_conflict_crc_and_metadata_refused(self):
        h = BytePeer()
        h.window()
        frames = [f for f in h.frames if f.message_type in (144, 145, 146)]
        first = frames[1]
        altered = replace(first, payload=first.payload[:12] + b"\x01\x00" + first.payload[14:])
        rail_lie = bytearray(frames[0].payload)
        struct.pack_into("<H", rail_lie, 26, 0)
        metadata = bytearray(frames[0].payload)
        struct.pack_into("<H", metadata, 24, 10)
        cases = (
            (frames[:1] + frames[2:], WindowCode.ORDER),
            (frames[:1] + [frames[2], first] + frames[3:], WindowCode.ORDER),
            (frames[:2] + [altered] + frames[2:], WindowCode.OVERLAP),
            (frames[:1] + [altered] + frames[2:], WindowCode.CRC),
            ([replace(frames[0], payload=bytes(metadata))] + frames[1:], WindowCode.CONTEXT),
            ([replace(frames[0], payload=bytes(rail_lie))] + frames[1:], WindowCode.COMPLETE),
            ([frames[0], frames[-1]], WindowCode.COUNT),
        )
        for stream, reason in cases:
            with self.subTest(reason=reason):
                result = receive_outcome(h.context, stream)
                self.assertIs(result.reason, reason)
                before = h.c.snapshot()
                self.reject(ErrorCode.STATE, WindowReceipt.from_window, h.context, result)
                self.assertEqual(h.c.snapshot(), before)
                self.assertIsNone(h.c.prepare_tx())
                if reason in (WindowCode.CRC, WindowCode.OVERLAP):
                    self.assertTrue(result.fragments)

    def test_real_receiver_deferred_external_semantics_refused(self):
        h = BytePeer()
        h.window()
        frames = [f for f in h.frames if f.message_type in (144, 145, 146)]
        context = replace(h.context, acquisition_mode=0, source=1)
        begin = bytearray(frames[0].payload)
        begin[20], begin[21] = 1, 1
        result = receive_outcome(context, [replace(frames[0], payload=bytes(begin))] + frames[1:])
        self.assertTrue(result.complete)
        self.assertEqual(result.deferred_checks, ("AUTO_EXTERNAL_EDGE_ID",))
        self.reject(ErrorCode.UNSUPPORTED, WindowReceipt.from_window, context, result)

    def test_factory_strict_types_bounds_and_forged_contents(self):
        h = BytePeer()
        h.window()
        result = h.results[-1]
        for context, outcome in ((None, result), (h.context, None),
                                 (h.context, replace(result, samples=list(result.samples))),
                                 (h.context, replace(result, fragments=list(result.fragments))),
                                 (h.context, replace(result, complete=1)),
                                 (h.context, replace(result, rail_observed=1)),
                                 (h.context, replace(result, samples_crc32=True)),
                                 (h.context, replace(result, begin=replace(result.begin, edge=False)))):
            self.reject(ErrorCode.ARGUMENT, WindowReceipt.from_window, context, outcome)
        for outcome in (replace(result, samples=(1,) * 2049),
                        replace(result, fragments=(result.fragments[0],) * 2049),
                        replace(result, fragments=(SampleBlock(0, (1,) * 2049),)),
                        replace(result, samples_crc32=1 << 32)):
            self.reject(ErrorCode.RANGE, WindowReceipt.from_window, h.context, outcome)
        samples = (123,) + result.samples[1:]
        for outcome in (replace(result, samples=samples),
                        replace(result, samples_crc32=result.samples_crc32 ^ 1),
                        replace(result, fragments=()),
                        replace(result, rail_observed=False),
                        replace(result, metadata_issues=("bad",)),
                        replace(result, missing_ranges=((1, 2),)),
                        replace(result, begin=replace(result.begin, trigger_index=1))):
            self.reject(ErrorCode.STATE, WindowReceipt.from_window, h.context, outcome)
        for context in (replace(h.context, generation=h.context.generation + 1),
                        replace(h.context, config_id=2), replace(h.context, edge=1)):
            self.reject(ErrorCode.STATE, WindowReceipt.from_window, context, result)

    def test_factory_bounds_before_any_sample_iteration(self):
        class Untouchable:
            def __eq__(self, other):
                raise AssertionError("Нельзя обходить непроверенный массив")

        h = BytePeer()
        h.window()
        result = h.results[-1]
        huge = replace(result, samples=(Untouchable(),) * 2049)
        self.reject(ErrorCode.RANGE, WindowReceipt.from_window, h.context, huge)
        bad = replace(result, fragments=(SampleBlock(0, (Untouchable(),) * 251),))
        self.reject(ErrorCode.RANGE, WindowReceipt.from_window, h.context, bad)

    def test_no_release_from_end_alone_or_abort(self):
        h = BytePeer()
        h.d.trigger(0)
        begin = h.read_one()  # STATUS
        self.assertEqual(begin.message_type, 134)
        begin = h.read_one()
        self.assertEqual(begin.message_type, 144)
        s = h.c.snapshot()
        end = h.reply(146, struct.pack("<IIHHI", s.generation, 1, 2048, 0, 0), 0)
        events = h.c.receive(end, 0)
        self.assertEqual([e.kind for e in events], [K.WINDOW_END_ADMITTED])
        result = h.receiver.feed(end, 0)
        self.assertIs(result.reason, WindowCode.COUNT)
        self.reject(ErrorCode.STATE, WindowReceipt.from_window, h.context, result)
        self.reject(ErrorCode.STATE, h.c.release, WindowReceipt(101, s.generation, 1, 2048, 0), 0)
        other = BytePeer()
        receipt = other.window()
        stopped = BytePeer()
        abort = stopped.reply(147, struct.pack("<IIHH", receipt.generation, 1, 1, 0), 0)
        stopped.c.receive(abort, 0)
        self.reject(ErrorCode.STATE, stopped.c.release, receipt, 0)

    def test_zero_partial_final_write_and_exact_deadline(self):
        h = BytePeer()
        receipt = h.window()
        h.c.release(receipt, 0)
        ticket = h.c.prepare_tx()
        sequence = h.c.snapshot().next_tx_sequence
        h.c.written(ticket, 0, 1)
        self.assertEqual(h.c.snapshot().next_tx_sequence, sequence)
        self.assertIs(h.c.prepare_tx(), ticket)
        h.c.written(ticket, 1, 2)
        self.assertEqual(h.c.snapshot().next_tx_sequence, sequence + 1)
        before = h.c.snapshot()
        self.reject(ErrorCode.STALE_TICKET, h.c.written, ticket, 1, 3)
        self.assertEqual(h.c.snapshot(), before)
        partial = h.c.prepare_tx()
        self.reject(ErrorCode.STALE_TICKET, h.c.written, replace(partial), 1, 3)
        self.reject(ErrorCode.RANGE, h.c.written, partial, len(partial.encoded), 3)
        self.assertEqual(h.c.tick(10000), ())
        h.c.written(partial, len(partial.encoded) - partial.offset, 10001)
        self.assertEqual(h.c.tick(11000), ())
        events = h.c.tick(11001)
        self.assertEqual(events[0].reason, R.TIMEOUT)
        self.assertIsNone(h.c.prepare_tx())

    def test_ack_at_999_and_1000_ms(self):
        for elapsed in (999, 1000):
            h = BytePeer()
            receipt = h.window()
            h.c.release(receipt, 0)
            h.write()
            ack = h.reply(135, struct.pack("<II", receipt.generation, receipt.window_id))
            events = h.c.receive(ack, elapsed)
            self.assertEqual(events[0].kind, K.WINDOW_RELEASED if elapsed == 999 else K.RECOVERY)

    def test_unknown_write_disconnect_and_error_preserve_terminal_no_retry(self):
        for failure in ("unknown_write", "transport_lost", "error"):
            h = BytePeer()
            receipt = h.window()
            terminal = h.c.snapshot().terminal
            h.c.release(receipt, 0)
            if failure == "error":
                h.write()
                events = h.c.receive(h.reply(255, struct.pack("<BBHI", 7, 7, 0,
                                                             receipt.generation)), 0)
                self.assertEqual([e.kind for e in events], [K.COMMAND_ERROR, K.RECOVERY])
            else:
                h.write(1)
                events = getattr(h.c, failure)(0)
            self.assertIs(h.c.snapshot().phase, Phase.RECOVERY)
            self.assertEqual(h.c.snapshot().terminal, terminal)
            self.assertTrue(events[-1].preserve_fragments)
            self.assertTrue(events[-1].reset_transport)
            self.assertIsNone(h.c.prepare_tx())
            self.reject(ErrorCode.STATE, h.c.release, receipt, 0)
            h.c.start(202, 0)
            self.reject(ErrorCode.STATE, h.c.release, receipt, 0)
            self.assertEqual(h.c.prepare_tx().frame.message_type, 1)

    def test_bad_mismatched_unsent_and_replayed_ack(self):
        for mode in ("length", "generation", "window", "request", "unsent"):
            h = BytePeer()
            receipt = h.window()
            h.c.release(receipt, 0)
            if mode != "unsent":
                h.write()
            payload = struct.pack("<II", receipt.generation + (mode == "generation"),
                                  receipt.window_id + (mode == "window"))
            if mode == "length":
                payload += b"\0"
            ack = h.reply(135, payload)
            if mode == "request":
                ack = replace(ack, request_id=ack.request_id + 1)
            events = h.c.receive(ack, 0)
            self.assertIs(events[0].kind, K.RECOVERY)
            self.assertTrue(h.c.snapshot().window_barrier)
        h = BytePeer()
        receipt = h.window()
        h.c.release(receipt, 0)
        h.write()
        ack = h.reply(135, struct.pack("<II", receipt.generation, receipt.window_id))
        h.c.receive(ack, 0)
        self.assertEqual(h.c.receive(ack, 0), ())
        replay = replace(ack, sequence=ack.sequence + 1)
        self.assertIs(h.c.receive(replay, 0)[0].kind, K.RECOVERY)

    def test_next_window_before_ack_fails_closed(self):
        h = BytePeer()
        receipt = h.window()
        h.c.release(receipt, 0)
        h.write()
        first = next(f for f in h.frames if f.message_type == 144)
        data = bytearray(first.payload)
        struct.pack_into("<I", data, 4, 2)
        before_ack = h.reply(144, bytes(data), 0)
        self.assertEqual(h.c.receive(before_ack, 0)[0].reason, R.WINDOW)
        self.assertTrue(h.c.snapshot().window_barrier)

    def test_stop_blocks_new_release_and_late_release_stays_ready(self):
        h = BytePeer()
        receipt = h.window()
        h.c.stop(0)
        before = h.c.snapshot()
        self.reject(ErrorCode.STATE, h.c.release, receipt, 0)
        self.assertEqual(h.c.snapshot(), before)
        h.pump()
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        h.c.release(receipt, 0)
        h.pump()
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        self.assertEqual(h.d.snapshot().state, STOPPED)
        self.assertEqual([e.kind for e in h.events].count(K.ARMED), 1)
        self.reject(ErrorCode.STATE, h.c.release, receipt, 0)

    def test_stop_queued_partial_and_fully_written_release_order(self):
        for accepted in (0, 1, None):
            h = BytePeer()
            receipt = h.window()
            h.c.release(receipt, 0)
            release = h.c.prepare_tx()
            if accepted is None or accepted:
                h.write(accepted)
            h.c.stop(0)
            while h.c.prepare_tx() is not None:
                h.write()
            h.drain()
            self.assertEqual([r.message_type for r in h.requests][-2:], [7, 5])
            kinds = [e.kind for e in h.events if e.kind in (K.WINDOW_RELEASED, K.STOPPED)]
            self.assertEqual(kinds, [K.WINDOW_RELEASED, K.STOPPED])
            self.assertIs(h.c.snapshot().phase, Phase.READY)
            self.assertEqual(h.d.snapshot().state, STOPPED)
            self.assertEqual(release.frame.sequence, h.requests[-2].sequence)

    def test_prior_release_ack_allows_window_before_stop_reaches_peer(self):
        h = BytePeer()
        receipt = h.window()
        h.c.release(receipt, 0)
        h.write()
        h.c.stop(0)  # Уже поставлен, но ещё не передан устройству.
        ack = h.read_one()
        self.assertEqual(ack.message_type, 135)
        self.assertTrue(h.c.snapshot().stop_barrier)
        self.assertFalse(h.c.snapshot().window_barrier)
        second = h.window()
        self.assertEqual(second.window_id, 2)
        self.reject(ErrorCode.STATE, h.c.release, second, 0)
        h.pump()
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        h.c.release(second, 0)
        h.pump()
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        begin = next(f for f in h.frames if f.message_type == 144)
        incoming = replace(begin, sequence=h.c.snapshot().next_rx_sequence)
        self.assertEqual(h.c.receive(incoming, 0)[0].reason, R.WINDOW)

    def test_stop_ack_cannot_overtake_release_ack(self):
        h = BytePeer()
        receipt = h.window()
        h.c.release(receipt, 0)
        h.write()
        h.c.stop(0)
        h.write()
        snap = h.c.snapshot()
        ack = h.reply(133, struct.pack("<IIB3x", snap.generation, snap.window_id, 0),
                      snap.pending_stop_id)
        self.assertEqual(h.c.receive(ack, 0)[0].reason, R.REPLY)

    def test_foreign_session_receipt_and_ack_do_not_contaminate(self):
        h = BytePeer()
        receipt = h.window()
        foreign = WindowReceipt.from_window(replace(h.context, session=202), h.results[-1])
        # WindowResult не содержит session: связывание context — обязанность runtime.
        before = h.c.snapshot()
        self.reject(ErrorCode.STATE, h.c.release, foreign, 0)
        self.assertEqual(h.c.snapshot(), before)
        h.c.release(receipt, 0)
        h.write()
        ack = replace(h.reply(135, struct.pack("<II", receipt.generation, receipt.window_id)),
                      session=202)
        before = h.c.snapshot()
        self.assertEqual(h.c.receive(ack, 99999), ())
        self.assertEqual(h.c.snapshot(), before)

    def test_release_exhaustion_fails_without_issuing_reserved_ids(self):
        for field in ("_next_request", "_next_tx"):
            h = BytePeer()
            receipt = h.window()
            setattr(h.c, field, MAX_ID + 1)
            events = h.c.release(receipt, 0)
            self.assertEqual(events[0].reason, R.EXHAUSTED)
            self.assertIsNone(h.c.prepare_tx())


if __name__ == "__main__":
    unittest.main()

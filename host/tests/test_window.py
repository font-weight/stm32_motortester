"""Авторские проверки HOST-004 на синтетике; независимые векторы — отдельно."""
from dataclasses import FrozenInstanceError, replace
import struct
import unittest
import zlib

from motortester.wire import Frame, StreamDecoder, encode_frame
from motortester.window import (
    ExpectedContext, WindowCode as Code, WindowError, WindowReceiver,
    MAX_TIME_MS, PROTOCOL_SOURCE_SHA,
)


CONTEXT = ExpectedContext(12345, 17, 6, 1, 0, 0, 2000, 512)
BEGIN_FIELDS = dict(
    generation=17, window_id=1, config_id=6, profile=1, sample_count=2048,
    pretrigger_samples=512, trigger_index=512, trigger_kind=0, source=0,
    edge=0, channel=0, level_code=2000, quality_flags=0,
    timer_clock_hz=72000000, timer_psc=71, timer_arr=19, external_edge_id=0,
)
SAMPLES = tuple(1 + (index * 17) % 4094 for index in range(2048))


def begin(**changes):
    values = BEGIN_FIELDS | changes
    return Frame(144, CONTEXT.session, 2, 0,
                 struct.pack("<IIIHHHHBBBBHHIHHI", *values.values()))


def data(offset, samples, generation=17, window_id=1):
    return Frame(145, CONTEXT.session, 3, 0,
                 struct.pack("<IIHH", generation, window_id, offset, len(samples))
                 + struct.pack(f"<{len(samples)}H", *samples))


def end(samples=SAMPLES, count=2048, crc=None, reserved=0):
    if crc is None:
        crc = zlib.crc32(struct.pack(f"<{len(samples)}H", *samples))
    return Frame(146, CONTEXT.session, 4, 0,
                 struct.pack("<IIHHI", 17, 1, count, reserved, crc))


def abort(reason=1, reserved=0):
    return Frame(147, CONTEXT.session, 5, 0,
                 struct.pack("<IIHH", 17, 1, reason, reserved))


def snapshot(receiver):
    return tuple(tuple(value) if isinstance(value, list) else value
                 for name in receiver.__slots__ for value in (getattr(receiver, name),))


def fill(receiver, samples=SAMPLES, start_ms=1):
    for index, offset in enumerate(range(0, 2048, 250)):
        assert receiver.feed(data(offset, samples[offset:offset + 250]), start_ms + index) is None


class WindowTests(unittest.TestCase):
    def active(self):
        receiver = WindowReceiver(CONTEXT)
        self.assertIsNone(receiver.feed(begin(), 0))
        return receiver

    def assert_incomplete(self, result, code, count=0):
        self.assertIs(result.reason, code)
        self.assertFalse(result.complete)
        self.assertFalse(result.semantic_checks_complete)
        self.assertIsNone(result.samples)
        self.assertIsNone(result.samples_crc32)
        self.assertEqual(sum(len(x.samples) for x in result.fragments), count)
        self.assertEqual(result.missing_ranges, ((count, 2048),) if count < 2048 else ())

    def test_complete_through_fragmented_host002_frames(self):
        receiver = WindowReceiver(CONTEXT)
        stream = StreamDecoder()
        frames = [begin()] + [data(i, SAMPLES[i:i + 250]) for i in range(0, 2048, 250)] + [end()]
        raw = b"".join(encode_frame(frame) for frame in frames)
        events = []
        for offset in range(0, len(raw), 97):
            decoded = stream.feed(raw[offset:offset + 97], offset // 97)
            self.assertFalse(decoded.diagnostics)
            for frame in decoded.frames:
                event = receiver.feed(frame, offset // 97)
                if event is not None:
                    events.append(event)
        self.assertEqual(len(events), 1)
        result = events[0]
        self.assertTrue(result.complete)
        self.assertTrue(result.semantic_checks_complete)
        self.assertEqual(result.samples, SAMPLES)
        self.assertEqual(result.samples_crc32, zlib.crc32(struct.pack("<2048H", *SAMPLES)))
        self.assertEqual(result.protocol_source_sha, PROTOCOL_SOURCE_SHA)
        self.assertEqual(result.validation_scope, "draft_window_receive")
        self.assertEqual(result.metadata_issues, ())
        self.assertEqual(result.missing_ranges, ())
        for obj, attr in ((result, "complete"), (result.begin, "source"),
                          (result.fragments[0], "offset"), (CONTEXT, "session")):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, attr, 7)

    def test_all_samples_without_end_are_not_complete(self):
        receiver = self.active()
        fill(receiver)
        self.assert_incomplete(receiver.tick(3009), Code.TIMEOUT, 2048)

    def test_invalid_begin_fields_are_terminal_without_sample_allocation(self):
        cases = [
            ({"generation": 18}, Code.IDENTITY), ({"window_id": 0}, Code.BOUNDS),
            ({"config_id": 7}, Code.CONTEXT), ({"profile": 2}, Code.CONTEXT),
            ({"sample_count": 65535}, Code.COUNT),
            ({"pretrigger_samples": 0}, Code.CONTEXT),
            ({"trigger_index": 511}, Code.BOUNDS),
            ({"trigger_kind": 3}, Code.ENUM), ({"source": 2}, Code.ENUM),
            ({"edge": 2}, Code.ENUM), ({"channel": 1}, Code.BOUNDS),
            ({"level_code": 0}, Code.BOUNDS), ({"level_code": 4095}, Code.BOUNDS),
            ({"quality_flags": 8}, Code.RESERVED), ({"quality_flags": 1}, Code.QUALITY),
            ({"timer_clock_hz": 1}, Code.CONTEXT), ({"timer_psc": 72}, Code.CONTEXT),
            ({"timer_arr": 20}, Code.CONTEXT), ({"external_edge_id": 1}, Code.CONTEXT),
            ({"trigger_kind": 1}, Code.CONTEXT),
            ({"trigger_kind": 2, "trigger_index": 65535, "quality_flags": 1}, Code.CONTEXT),
        ]
        for changes, code in cases:
            with self.subTest(changes=changes):
                receiver = WindowReceiver(CONTEXT)
                result = receiver.feed(begin(**changes), 0)
                self.assert_incomplete(result, code)
                self.assertIsNone(result.begin)
                self.assertIsNone(receiver.feed(begin(), 1))

    def test_exact_payload_lengths(self):
        for original in (begin(), data(0, (5,)), end(), abort()):
            for payload in (original.payload[:-1], original.payload + b"\x00"):
                with self.subTest(kind=original.message_type, size=len(payload)):
                    receiver = WindowReceiver(CONTEXT) if original.message_type == 144 else self.active()
                    self.assert_incomplete(receiver.feed(replace(original, payload=payload), 1), Code.LENGTH)

    def test_envelope_and_event_order(self):
        for frame, code in ((replace(begin(), message_type=129), Code.TYPE),
                            (replace(begin(), session=2), Code.SESSION),
                            (replace(begin(), request_id=2), Code.REQUEST),
                            (data(0, (3,)), Code.ORDER), (end(), Code.ORDER), (abort(), Code.ORDER)):
            self.assert_incomplete(WindowReceiver(CONTEXT).feed(frame, 0), code)
        self.assert_incomplete(self.active().feed(begin(), 1), Code.ORDER)

    def test_data_bounds_adc_and_ids_leave_verified_prefix(self):
        invalid = [
            (data(1, ()), Code.BOUNDS), (data(2047, (1, 2)), Code.BOUNDS),
            (data(1, (4096,)), Code.ADC), (data(1, (1,), generation=18), Code.IDENTITY),
            (data(1, (1,), window_id=2), Code.IDENTITY),
        ]
        for frame, code in invalid:
            receiver = self.active()
            receiver.feed(data(0, (111,)), 1)
            result = receiver.feed(frame, 2)
            self.assert_incomplete(result, code, 1)
            self.assertEqual(result.fragments[0].samples, (111,))

    def test_gap_conflict_and_matching_subslice_are_not_duplicates(self):
        for frame, code in ((data(5, (1,)), Code.ORDER),
                            (data(0, (1, 99)), Code.OVERLAP),
                            (data(1, (2,)), Code.OVERLAP),
                            (data(0, (1,)), Code.OVERLAP),
                            (data(0, (1, 2, 3)), Code.OVERLAP)):
            receiver = self.active()
            receiver.feed(data(0, (1, 2)), 1)
            result = receiver.feed(frame, 2)
            self.assert_incomplete(result, code, 2)
            self.assertEqual(result.fragments[0].samples, (1, 2))

    def test_exact_earlier_duplicate_never_refreshes_progress(self):
        receiver = self.active()
        first, second = data(0, (1, 2)), data(2, (3, 4))
        receiver.feed(first, 1)
        receiver.feed(second, 2)
        for now in (3, 1000, 3001):
            self.assertIsNone(receiver.feed(replace(first, sequence=now), now))
        result = receiver.feed(second, 3002)
        self.assert_incomplete(result, Code.TIMEOUT, 4)
        self.assertEqual(len(result.fragments), 2)

    def test_deadline_boundary_and_current_valid_event_precedence(self):
        for frame in (data(0, (1,)), abort(), replace(data(0, (1,)), payload=b"")):
            receiver = self.active()
            self.assertIsNone(receiver.tick(2999))
            self.assert_incomplete(receiver.feed(frame, 3000), Code.TIMEOUT)
        receiver = self.active()
        receiver.feed(data(0, (1,)), 2999)
        self.assertIsNone(receiver.tick(5998))
        self.assert_incomplete(receiver.disconnect(5999), Code.TIMEOUT, 1)

    def test_invalid_api_never_expires_or_mutates(self):
        class BytesSubclass(bytes):
            pass
        forged = []
        for value in (b"x" * (512 + 1), BytesSubclass(b""), bytearray(b"")):
            frame = begin()
            object.__setattr__(frame, "payload", value)
            forged.append(frame)
        for frame, now, code in [(None, 3000, Code.ARGUMENT),
                                 (forged[0], 3000, Code.PAYLOAD_SIZE),
                                 (forged[1], 3000, Code.ARGUMENT),
                                 (forged[2], 3000, Code.ARGUMENT),
                                 (begin(), True, Code.ARGUMENT),
                                 (begin(), 1.5, Code.ARGUMENT),
                                 (begin(), -1, Code.RANGE),
                                 (begin(), MAX_TIME_MS + 1, Code.RANGE)]:
            receiver = self.active()
            before = snapshot(receiver)
            with self.assertRaises(WindowError) as caught:
                receiver.feed(frame, now)
            self.assertIs(caught.exception.code, code)
            self.assertEqual(snapshot(receiver), before)
            self.assert_incomplete(receiver.tick(3000), Code.TIMEOUT)

    def test_idle_time_reset_clock_rollback_and_uint64_boundary(self):
        receiver = WindowReceiver(CONTEXT)
        self.assertIsNone(receiver.tick(MAX_TIME_MS - 2))
        receiver.feed(begin(), MAX_TIME_MS - 1)
        self.assertIsNone(receiver.tick(MAX_TIME_MS))
        before = snapshot(receiver)
        for fn in (receiver.tick, receiver.disconnect):
            with self.assertRaises(WindowError) as caught:
                fn(0)
            self.assertIs(caught.exception.code, Code.CLOCK_ROLLBACK)
            self.assertEqual(before, snapshot(receiver))
        self.assert_incomplete(receiver.reset(), Code.RESET)
        self.assertIsNone(receiver.feed(begin(), 0))

    def test_terminal_once_and_explicit_reset(self):
        for terminate, reason in ((lambda r: r.disconnect(2), Code.DISCONNECT),
                                  (lambda r: r.feed(abort(), 2), Code.ABORT),
                                  (lambda r: r.tick(3001), Code.TIMEOUT)):
            receiver = self.active()
            receiver.feed(data(0, (10, 11)), 1)
            self.assert_incomplete(terminate(receiver), reason, 2)
            for frame in (end(), abort(), data(2, (12,)), begin()):
                self.assertIsNone(receiver.feed(frame, 4000))
            self.assertIsNone(receiver.disconnect(4000))
            self.assertIsNone(receiver.tick(4000))
            self.assertIsNone(receiver.reset())
            self.assertIsNone(receiver.reset())
            self.assertIsNone(receiver.feed(begin(), 0))
            self.assert_incomplete(receiver.reset(), Code.RESET)
            self.assertIsNone(receiver.reset())

    def test_complete_terminal_is_not_repeated(self):
        receiver = self.active()
        fill(receiver)
        result = receiver.feed(end(), 20)
        for frame in (end(), abort(), begin()):
            self.assertIsNone(receiver.feed(frame, 21))
        self.assertIsNone(receiver.disconnect(21))
        self.assertIsNone(receiver.reset())
        self.assertEqual(result.samples, SAMPLES)

    def test_end_count_crc_and_reserved_preserve_even_full_fragments(self):
        for frame, code in ((end(count=2047), Code.COUNT), (end(crc=0), Code.CRC),
                            (end(reserved=1), Code.RESERVED)):
            receiver = self.active()
            fill(receiver)
            result = receiver.feed(frame, 20)
            self.assert_incomplete(result, code, 2048)
            self.assertEqual(tuple(x for block in result.fragments for x in block.samples), SAMPLES)
        self.assert_incomplete(self.active().feed(end(), 1), Code.COUNT)

    def test_abort_reasons_and_reserved(self):
        for reason in range(1, 7):
            result = self.active().feed(abort(reason), 1)
            self.assert_incomplete(result, Code.ABORT)
            self.assertEqual(result.abort_reason, reason)
        for frame, code in ((abort(0), Code.ENUM), (abort(7), Code.ENUM),
                            (abort(1, 1), Code.RESERVED)):
            self.assert_incomplete(self.active().feed(frame, 1), code)

    def test_rail_flag_consistency_preserves_data_and_is_not_contract_deferral(self):
        for observed in (False, True):
            samples = (0, 4095) + SAMPLES[2:] if observed else SAMPLES
            for flag in (0, 2):
                receiver = WindowReceiver(CONTEXT)
                receiver.feed(begin(quality_flags=flag), 0)
                fill(receiver, samples)
                result = receiver.feed(end(samples), 20)
                self.assertTrue(result.complete)
                self.assertEqual(result.rail_observed, observed)
                self.assertEqual(result.samples, samples)
                self.assertEqual(result.deferred_checks, ())
                self.assertEqual(result.metadata_issues,
                                 () if bool(flag) == observed else ("RAIL_FLAG_MISMATCH",))
                self.assertEqual(result.semantic_checks_complete, bool(flag) == observed)

    def test_auto_external_id_remains_raw_and_deferred(self):
        for raw_id in (0, 1, 0xFFFFFFFF):
            receiver = WindowReceiver(replace(CONTEXT, acquisition_mode=0, source=1))
            receiver.feed(begin(source=1, trigger_kind=1, external_edge_id=raw_id), 0)
            fill(receiver)
            result = receiver.feed(end(), 20)
            self.assertTrue(result.complete)
            self.assertEqual(result.begin.external_edge_id, raw_id)
            self.assertEqual(result.deferred_checks, ("AUTO_EXTERNAL_EDGE_ID",))
            self.assertFalse(result.semantic_checks_complete)

    def test_valid_auto_timeout_and_external_modes(self):
        for mode, kind, source, index, flags, raw_id in (
                (0, 2, 0, 65535, 1, 0), (0, 2, 1, 65535, 1, 0),
                (1, 1, 1, 512, 4, 7), (2, 1, 1, 512, 0, 8)):
            receiver = WindowReceiver(replace(CONTEXT, acquisition_mode=mode, source=source))
            self.assertIsNone(receiver.feed(begin(trigger_kind=kind, source=source,
                                                 trigger_index=index, quality_flags=flags,
                                                 external_edge_id=raw_id), 0))
            fill(receiver)
            self.assertTrue(receiver.feed(end(), 20).semantic_checks_complete)

    def test_context_strict_types_and_supported_ranges(self):
        for changes in ({"session": True}, {"session": 0}, {"generation": 1},
                        {"config_id": 0}, {"profile": 2}, {"source": 2},
                        {"edge": 2}, {"level_code": 4095}, {"pretrigger_samples": 513},
                        {"acquisition_mode": 3}, {"generation": 2.0}):
            with self.assertRaises(WindowError):
                replace(CONTEXT, **changes)
        with self.assertRaises(WindowError):
            WindowReceiver(None)


if __name__ == "__main__":
    unittest.main()

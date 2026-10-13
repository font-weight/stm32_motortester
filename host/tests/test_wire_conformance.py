"""Независимая HOST-002 conformance: только синтетический byte/stream draft.

Ожидания заданы по wire 124b3f32 и согласованному API HOST-002, без вызова
производственного encode для подготовки входов decode/stream. Это не Critic-B.
"""
from dataclasses import FrozenInstanceError
import gc
import importlib.util
import json
from pathlib import Path
import random
import tracemalloc
import unittest

from motortester.wire import (
    DiagnosticCode as D, Frame, StreamDecoder, WireError,
    encode_frame, decode_frame, MAX_CHUNK_SIZE, MAX_ENCODED_SIZE,
    MAX_RAW_SIZE, MAX_PAYLOAD_SIZE, INTERBYTE_TIMEOUT_MS, FRAME_TIMEOUT_MS,
)

FIXTURES = Path(__file__).parent / "fixtures" / "wire"
_spec = importlib.util.spec_from_file_location("wire_test_reference", FIXTURES / "reference.py")
ref = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ref)
VECTORS = json.loads((FIXTURES / "vectors.json").read_text(encoding="utf-8"))["vectors"]


def expected(vector):
    return Frame(vector["message_type"], vector["session"], vector["sequence"],
                 vector["request_id"], bytes.fromhex(vector["payload_hex"]))


def frame_bytes(vector):
    return bytes.fromhex(vector["frame_hex"])


def diagnostics(result):
    return [(item.code, item.count) for item in result.diagnostics]


class ConformanceAssertions(unittest.TestCase):
    def reject(self, code, function, *args):
        with self.assertRaises(WireError) as caught:
            function(*args)
        self.assertIs(caught.exception.code, code)

    def clean(self, result, frames=()):
        self.assertEqual(result.frames, tuple(frames))
        self.assertEqual(result.diagnostics, ())
        self.assertIs(type(result.frames), tuple)
        self.assertIs(type(result.diagnostics), tuple)


class IndependentVectors(ConformanceAssertions):
    def test_constants_are_wire_and_host_limits(self):
        self.assertEqual((MAX_CHUNK_SIZE, MAX_ENCODED_SIZE, MAX_RAW_SIZE,
                          MAX_PAYLOAD_SIZE, INTERBYTE_TIMEOUT_MS, FRAME_TIMEOUT_MS),
                         (4096, 543, 540, 512, 1000, 3000))

    def test_reference_crc_standard_check(self):
        self.assertEqual(ref.checksum(b"123456789"), bytes.fromhex("2639f4cb"))
        self.assertEqual(ref.checksum(b""), b"\0" * 4)

    def test_static_vectors_rederive_without_production_codec(self):
        self.assertEqual(len(VECTORS), 9)
        for vector, (name, *values) in zip(VECTORS, ref.fixture_inputs()):
            with self.subTest(name=name):
                self.assertEqual(vector["name"], name)
                body = ref.raw_message(*values)
                self.assertEqual(ref.raw_with_crc(body).hex(), vector["raw_hex"])
                self.assertEqual(ref.packet(body).hex(), vector["frame_hex"])

    def test_a_published_vector_is_literal_cross_check(self):
        self.assertEqual(VECTORS[0]["raw_hex"],
                         "00017f0006000000010203040506070801000000020000000001feff007e9ac2711a")
        self.assertEqual(VECTORS[0]["frame_hex"],
                         "0103017f020601010a010203040506070801010102020101010401feff067e9ac2711a00")

    def test_little_endian_offsets_are_explicit(self):
        raw = bytes.fromhex(VECTORS[2]["raw_hex"])
        self.assertEqual(raw[:8], bytes.fromhex("0001810009000000"))
        self.assertEqual(raw[8:16], bytes.fromhex("1032547698badcfe"))
        self.assertEqual(raw[16:20], bytes.fromhex("12345678"))
        self.assertEqual(raw[20:24], bytes.fromhex("9abcdef0"))

    def test_encode_and_decode_match_all_static_vectors(self):
        for vector in VECTORS:
            with self.subTest(name=vector["name"]):
                wire = frame_bytes(vector)
                self.assertEqual(encode_frame(expected(vector)), wire)
                self.assertEqual(decode_frame(wire[:-1]), expected(vector))
                self.assertEqual(wire[-1:], b"\0")
                self.assertNotIn(0, wire[:-1])

    def test_maximum_valid_frame_reaches_bound(self):
        self.assertEqual(len(bytes.fromhex(VECTORS[4]["raw_hex"])), 540)
        self.assertEqual(len(frame_bytes(VECTORS[4])), 544)
        self.assertEqual(len(decode_frame(frame_bytes(VECTORS[4])[:-1]).payload), 512)

    def test_every_payload_length_matches_independent_reference(self):
        for length in range(513):
            payload = bytes((n * 73 + length) % 256 for n in range(length))
            body = ref.raw_message(145, 0x1020304050607080, length, 0xF0E0D0C0, payload)
            wire = ref.packet(body)
            wanted = Frame(145, 0x1020304050607080, length, 0xF0E0D0C0, payload)
            with self.subTest(length=length):
                self.assertEqual(encode_frame(wanted), wire)
                self.assertEqual(decode_frame(wire[:-1]), wanted)

    def test_type_and_identifier_semantics_are_outside_byte_codec(self):
        for message_type in (0, 1, 127, 128, 129, 254, 255):
            body = ref.raw_message(message_type, 0, 0, 0, b"")
            self.assertEqual(decode_frame(ref.packet(body)[:-1]),
                             Frame(message_type, 0, 0, 0, b""))

    def test_returned_frame_payload_and_results_are_immutable(self):
        result = StreamDecoder().feed(frame_bytes(VECTORS[0]), 0)
        frame = result.frames[0]
        self.assertIs(type(frame.payload), bytes)
        for obj, name, value in ((frame, "payload", b"bad"),
                                 (frame, "sequence", 999),
                                 (result, "frames", ())):
            with self.subTest(name=name), self.assertRaises((FrozenInstanceError, AttributeError)):
                setattr(obj, name, value)
        rejected = StreamDecoder().feed(b"\xff\0", 0)
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            rejected.diagnostics[0].count = 123
        self.assertEqual(frame, expected(VECTORS[0]))


class InvalidFrames(ConformanceAssertions):
    def setUp(self):
        self.body = bytes.fromhex(VECTORS[0]["raw_hex"])[:-4]
        self.wire = frame_bytes(VECTORS[0])

    def test_empty_zero_and_truncated_cobs(self):
        for candidate in (b"", b"\0", b"\x02", b"\xff", b"\x03a", b"\x02\0",
                          self.wire, self.wire[:-1] + b"\0" + self.wire[:-1]):
            with self.subTest(candidate=candidate[:8]):
                self.reject(D.FRAMING, decode_frame, candidate)

    def test_raw_length_underflow_and_overflow(self):
        for raw in (b"", b"\0" * 1, b"\0" * 27, b"\0" * 541):
            self.reject(D.LENGTH, decode_frame, ref.cobs(raw))
        self.reject(D.LENGTH, decode_frame, b"\x01" * 542)
        self.reject(D.FRAME_SIZE, decode_frame, b"\x01" * 544)

    def test_declared_payload_length_requires_exact_bytes(self):
        for declared in (0, 5, 7, 512, 513, 65535):
            body = bytearray(self.body)
            body[4:6] = declared.to_bytes(2, "little")
            with self.subTest(declared=declared):
                self.reject(D.LENGTH, decode_frame, ref.packet(bytes(body))[:-1])
        for body in (self.body[:-1], self.body + b"\0"):
            self.reject(D.LENGTH, decode_frame, ref.packet(body)[:-1])

    def test_strict_major_minor_versions(self):
        for offset, values in ((0, (1, 127, 255)), (1, (0, 2, 255))):
            for value in values:
                body = bytearray(self.body)
                body[offset] = value
                self.reject(D.VERSION, decode_frame, ref.packet(bytes(body))[:-1])

    def test_all_reserved_header_bytes_are_checked(self):
        for offset in (3, 6, 7):
            for value in (1, 128, 255):
                body = bytearray(self.body)
                body[offset] = value
                self.reject(D.RESERVED, decode_frame, ref.packet(bytes(body))[:-1])

    def test_crc_is_little_endian_and_precedes_version_reserved(self):
        raw = bytearray(ref.raw_with_crc(self.body))
        raw[-4:] = raw[-4:][::-1]
        self.reject(D.CRC, decode_frame, ref.cobs(bytes(raw)))
        for offset in (0, 1, 3, 6, 7, 8, 16, 20, 24, len(raw) - 1):
            raw = bytearray(ref.raw_with_crc(self.body))
            raw[offset] ^= 1
            self.reject(D.CRC, decode_frame, ref.cobs(bytes(raw)))
        body = bytearray(self.body)
        body[0] = 1
        body[3] = 1
        self.reject(D.VERSION, decode_frame, ref.packet(bytes(body))[:-1])

    def test_every_single_bit_in_crc_is_rejected(self):
        raw = ref.raw_with_crc(self.body)
        for position in range(32):
            altered = bytearray(raw)
            altered[-4 + position // 8] ^= 1 << (position % 8)
            self.reject(D.CRC, decode_frame, ref.cobs(bytes(altered)))

    def test_incomplete_frame_never_yields_a_success(self):
        for stop in range(1, len(self.wire) - 1):
            decoder = StreamDecoder()
            result = decoder.feed(self.wire[:stop] + b"\0", 0)
            self.assertEqual(result.frames, ())
            self.assertEqual(sum(d.count for d in result.diagnostics), 1)
            self.clean(decoder.feed(self.wire, 1), (expected(VECTORS[0]),))


class StreamConformance(ConformanceAssertions):
    def setUp(self):
        self.wire = frame_bytes(VECTORS[0])
        self.frame = expected(VECTORS[0])

    def test_all_two_read_split_boundaries_for_every_fixture(self):
        for vector in VECTORS:
            wire = frame_bytes(vector)
            for split in range(len(wire) + 1):
                with self.subTest(name=vector["name"], split=split):
                    decoder = StreamDecoder()
                    first = decoder.feed(wire[:split], 0)
                    second = decoder.feed(wire[split:], 1)
                    self.assertEqual(first.frames + second.frames, (expected(vector),))
                    self.assertEqual(first.diagnostics + second.diagnostics, ())

    def test_single_byte_reads_at_maximum_frame(self):
        wire = frame_bytes(VECTORS[4])
        decoder = StreamDecoder()
        for index, value in enumerate(wire):
            result = decoder.feed(bytes([value]), index)
            self.clean(result, (expected(VECTORS[4]),) if index == len(wire) - 1 else ())

    def test_coalesced_frames_and_empty_delimiters(self):
        decoder = StreamDecoder()
        data = b"\0\0" + b"\0".join(frame_bytes(v) for v in VECTORS) + b"\0\0"
        self.clean(decoder.feed(data, 0), tuple(map(expected, VECTORS)))
        self.clean(decoder.feed(b"\0" * 4096, 9999))

    def test_random_chunking_of_ordered_stream(self):
        rng = random.Random(10002)
        wanted = [expected(VECTORS[i % 9]) for i in range(90)]
        data = b"".join(frame_bytes(VECTORS[i % 9]) for i in range(90))
        decoder = StreamDecoder()
        observed = []
        position = 0
        now = 0
        while position < len(data):
            size = rng.randint(1, 4096)
            result = decoder.feed(data[position:position + size], now)
            observed.extend(result.frames)
            self.assertEqual(result.diagnostics, ())
            position += size
            now += 1
        self.assertEqual(observed, wanted)

    def test_overflow_exact_boundary_and_discard_until_zero(self):
        decoder = StreamDecoder()
        self.clean(decoder.feed(b"\x01" * 543, 0))
        result = decoder.feed(b"\x01", 1)
        self.assertEqual(diagnostics(result), [(D.OVERFLOW, 1)])
        self.assertEqual(result.frames, ())
        self.clean(decoder.feed(self.wire[:-1], 2))
        self.clean(decoder.tick(100000))
        self.clean(decoder.feed(b"\0" + self.wire, 100000), (self.frame,))

    def test_overflow_recovers_within_same_chunk(self):
        decoder = StreamDecoder()
        result = decoder.feed(b"\x01" * 544 + self.wire + self.wire, 0)
        self.assertEqual(result.frames, (self.frame,))
        self.assertEqual(diagnostics(result), [(D.OVERFLOW, 1)])

    def test_rejections_do_not_discard_the_next_delimited_frame(self):
        bad = bytearray(bytes.fromhex(VECTORS[0]["raw_hex"]))
        bad[-1] ^= 1
        damaged = ref.cobs(bytes(bad)) + b"\0"
        result = StreamDecoder().feed(b"\xff\0" + self.wire + damaged + self.wire, 0)
        self.assertEqual(result.frames, (self.frame, self.frame))
        self.assertEqual(diagnostics(result), [(D.FRAMING, 1), (D.CRC, 1)])

    def test_each_decode_rejection_is_diagnostic_and_next_frame_recovers(self):
        raw = bytes.fromhex(VECTORS[0]["raw_hex"])
        version = bytearray(raw[:-4])
        version[1] = 2
        reserved = bytearray(raw[:-4])
        reserved[7] = 1
        corrupted = bytearray(raw)
        corrupted[-1] ^= 1
        cases = [(b"\xff\0", D.FRAMING), (b"\x01\0", D.LENGTH),
                 (ref.cobs(bytes(corrupted)) + b"\0", D.CRC),
                 (ref.packet(bytes(version)), D.VERSION),
                 (ref.packet(bytes(reserved)), D.RESERVED)]
        for data, code in cases:
            with self.subTest(code=code):
                result = StreamDecoder().feed(data + self.wire, 0)
                self.assertEqual(result.frames, (self.frame,))
                self.assertEqual(diagnostics(result), [(code, 1)])

    def test_repeated_overflow_incidents_aggregate_once_each(self):
        data = (b"\x01" * 544 + b"\0") * 7 + self.wire
        result = StreamDecoder().feed(data, 0)
        self.assertEqual(result.frames, (self.frame,))
        self.assertEqual(diagnostics(result), [(D.OVERFLOW, 7)])

    def test_diagnostics_aggregate_by_code_in_first_occurrence_order(self):
        bad = bytearray(bytes.fromhex(VECTORS[0]["raw_hex"]))
        bad[-1] ^= 1
        damaged = ref.cobs(bytes(bad)) + b"\0"
        result = StreamDecoder().feed(damaged + b"\xff\0" + damaged + b"\xff\0", 0)
        self.assertEqual(diagnostics(result), [(D.CRC, 2), (D.FRAMING, 2)])
        self.assertEqual(result.frames, ())

    def test_error_storm_diagnostics_are_bounded_and_per_call(self):
        decoder = StreamDecoder()
        result = decoder.feed(b"\xff\0" * 2048, 0)
        self.assertEqual(diagnostics(result), [(D.FRAMING, 2048)])
        self.assertLessEqual(len(result.diagnostics), len(D))
        self.clean(decoder.tick(1))
        self.clean(decoder.feed(self.wire, 1), (self.frame,))

    def test_maximum_successful_batch_including_prebuffered_frame(self):
        short = frame_bytes(VECTORS[1])
        decoder = StreamDecoder()
        self.clean(decoder.feed(short[:-1], 0))
        batch = b"\0" + short * 136 + b"\0" * 15
        self.assertEqual(len(batch), 4096)
        result = decoder.feed(batch, 0)
        self.clean(result, (expected(VECTORS[1]),) * 137)

    def test_previous_results_survive_buffer_reuse_errors_and_reset(self):
        decoder = StreamDecoder()
        retained = decoder.feed(self.wire, 0)
        for index in range(50):
            decoder.feed(b"\xff\0" + frame_bytes(VECTORS[4]), index)
        decoder.reset()
        self.assertEqual(retained.frames, (self.frame,))
        self.assertEqual(retained.diagnostics, ())


class TimeoutConformance(ConformanceAssertions):
    def setUp(self):
        self.wire = frame_bytes(VECTORS[0])
        self.frame = expected(VECTORS[0])

    def test_interbyte_just_before_deadline_succeeds(self):
        decoder = StreamDecoder()
        self.clean(decoder.feed(self.wire[:-1], 100))
        self.clean(decoder.feed(b"\0", 1099), (self.frame,))

    def test_interbyte_exact_deadline_delimiter_cannot_complete_frame(self):
        decoder = StreamDecoder()
        decoder.feed(self.wire[:-1], 100)
        result = decoder.feed(b"\0" + self.wire, 1100)
        self.assertEqual(diagnostics(result), [(D.TIMEOUT, 1)])
        self.assertEqual(result.frames, (self.frame,))

    def test_empty_feed_and_tick_do_not_refresh_interbyte_timer(self):
        for idle in ("feed", "tick"):
            decoder = StreamDecoder()
            decoder.feed(self.wire[:-1], 0)
            for now in (400, 800, 999):
                result = decoder.feed(b"", now) if idle == "feed" else decoder.tick(now)
                self.clean(result)
            result = decoder.tick(1000)
            self.assertEqual(diagnostics(result), [(D.TIMEOUT, 1)])
            self.clean(decoder.tick(2000))
            self.clean(decoder.feed(self.wire, 2000))
            self.clean(decoder.feed(self.wire, 2000), (self.frame,))

    def test_new_bytes_refresh_interbyte_but_not_whole_frame_deadline(self):
        for deadline in (2999, 3000, 3001):
            decoder = StreamDecoder()
            for index, now in enumerate((0, 900, 1800, 2700)):
                self.clean(decoder.feed(self.wire[index:index + 1], now))
            result = decoder.feed(self.wire[4:], deadline)
            if deadline == 2999:
                self.clean(result, (self.frame,))
            else:
                self.assertEqual(result.frames, ())
                self.assertEqual(diagnostics(result), [(D.TIMEOUT, 1)])
                self.clean(decoder.feed(self.wire, deadline), (self.frame,))

    def test_both_expired_deadlines_report_once_and_discard(self):
        decoder = StreamDecoder()
        decoder.feed(self.wire[:10], 0)
        result = decoder.tick(3000)
        self.assertEqual(diagnostics(result), [(D.TIMEOUT, 1)])
        self.clean(decoder.tick(6000))
        self.clean(decoder.feed(self.wire, 6000))
        self.clean(decoder.feed(self.wire, 6000), (self.frame,))

    def test_idle_delimiters_do_not_start_frame_timeout(self):
        decoder = StreamDecoder()
        self.clean(decoder.feed(b"\0" * 4096, 0))
        self.clean(decoder.tick(999999))
        self.clean(decoder.feed(self.wire, 1000000), (self.frame,))

    def test_finishing_one_frame_resets_whole_frame_timer(self):
        decoder = StreamDecoder()
        decoder.feed(self.wire[:3], 0)
        decoder.feed(self.wire[3:6], 900)
        decoder.feed(self.wire[6:9], 1800)
        self.clean(decoder.feed(self.wire[9:] + self.wire[:3], 2700), (self.frame,))
        self.clean(decoder.feed(self.wire[3:], 3600), (self.frame,))

    def test_reset_clears_partial_discard_and_clock(self):
        for initial in (self.wire[:12], b"\x01" * 544, self.wire):
            decoder = StreamDecoder()
            decoder.feed(initial, 100000)
            self.assertIsNone(decoder.reset())
            self.clean(decoder.feed(self.wire, 0), (self.frame,))
            self.assertIsNone(decoder.reset())
            self.assertIsNone(decoder.reset())
            self.clean(decoder.tick(0))


class DefensiveInputConformance(ConformanceAssertions):
    def setUp(self):
        self.wire = frame_bytes(VECTORS[0])
        self.frame = expected(VECTORS[0])

    def test_frame_integer_types_and_ranges(self):
        valid = [127, 1, 2, 3, b"ok"]
        for index, bits in enumerate((8, 64, 32, 32)):
            for value in (True, False, 1.0, "1", None):
                values = valid.copy()
                values[index] = value
                self.reject(D.ARGUMENT, Frame, *values)
            for value in (-1, 2**bits, 2**4096):
                values = valid.copy()
                values[index] = value
                self.reject(D.RANGE, Frame, *values)

    def test_frame_payload_is_exact_immutable_bytes_and_bounded(self):
        for payload in (None, "abc", bytearray(b"abc"), memoryview(b"abc"), [1, 2], 5):
            self.reject(D.ARGUMENT, Frame, 1, 1, 1, 1, payload)
        self.reject(D.PAYLOAD_SIZE, Frame, 1, 1, 1, 1, b"x" * 513)

    def test_encode_rejects_nonframes(self):
        for value in (None, (), {}, b"", 1):
            self.reject(D.ARGUMENT, encode_frame, value)

    def test_exact_bytes_rejection_does_not_iterate_user_objects(self):
        class PoisonBytes(bytes):
            def __len__(self):
                raise AssertionError("Не читать аргумент до проверки точного типа")
            def __iter__(self):
                raise AssertionError("Не итерировать недоверенный аргумент")
        class PoisonIterable:
            def __iter__(self):
                raise AssertionError("Не итерировать недоверенный аргумент")
        for value in (PoisonBytes(self.wire), PoisonIterable(), bytearray(self.wire),
                      memoryview(self.wire), self.wire.hex(), 4096, None):
            self.reject(D.ARGUMENT, decode_frame, value)
            self.reject(D.ARGUMENT, StreamDecoder().feed, value, 0)
            self.reject(D.ARGUMENT, Frame, 1, 1, 1, 1, value)

    def test_invalid_data_and_timestamps_do_not_mutate_partial_frame(self):
        calls = [lambda d: d.feed(b"\0" * 4097, 5000),
                 lambda d: d.feed(bytearray(b"\0"), 5000),
                 lambda d: d.feed(self.wire, -1),
                 lambda d: d.feed(self.wire, 2**64),
                 lambda d: d.feed(self.wire, True),
                 lambda d: d.tick(2**64), lambda d: d.tick(-1),
                 lambda d: d.tick(1.0), lambda d: d.tick(None)]
        codes = [D.CHUNK_SIZE, D.ARGUMENT, D.RANGE, D.RANGE, D.ARGUMENT,
                 D.RANGE, D.RANGE, D.ARGUMENT, D.ARGUMENT]
        for call, code in zip(calls, codes):
            decoder = StreamDecoder()
            self.clean(decoder.feed(self.wire[:10], 10))
            self.reject(code, call, decoder)
            self.clean(decoder.feed(self.wire[10:], 11), (self.frame,))

    def test_time_rollback_is_rejected_without_changing_frame_or_baseline(self):
        for call in (lambda d: d.feed(b"\0", 9), lambda d: d.tick(9)):
            decoder = StreamDecoder()
            decoder.feed(self.wire[:10], 10)
            self.reject(D.CLOCK_ROLLBACK, call, decoder)
            self.clean(decoder.feed(self.wire[10:], 10), (self.frame,))
        decoder = StreamDecoder()
        self.clean(decoder.tick(100))
        self.reject(D.CLOCK_ROLLBACK, decoder.feed, self.wire, 99)
        self.clean(decoder.feed(self.wire, 100), (self.frame,))

    def test_invalid_call_does_not_leave_discard_mode(self):
        decoder = StreamDecoder()
        decoder.feed(b"\x01" * 544, 10)
        self.reject(D.CHUNK_SIZE, decoder.feed, b"\0" * 4097, 20)
        self.reject(D.ARGUMENT, decoder.feed, bytearray(b"\0"), 20)
        self.clean(decoder.feed(self.wire, 11))
        self.clean(decoder.feed(self.wire, 11), (self.frame,))

    def test_exact_integer_subclasses_are_rejected(self):
        class IntSubclass(int):
            def __index__(self):
                raise AssertionError("Не преобразовывать недоверенный integer")
        self.reject(D.ARGUMENT, Frame, IntSubclass(1), 1, 1, 1, b"")
        self.reject(D.ARGUMENT, StreamDecoder().feed, self.wire, IntSubclass(1))
        self.reject(D.ARGUMENT, StreamDecoder().tick, IntSubclass(1))

    def test_clock_upper_limit_and_equal_timestamps(self):
        decoder = StreamDecoder()
        now = 2**64 - 1
        self.clean(decoder.feed(self.wire[:10], now))
        self.clean(decoder.tick(now))
        self.clean(decoder.feed(self.wire[10:], now), (self.frame,))
        self.reject(D.RANGE, decoder.tick, now + 1)
        self.reject(D.CLOCK_ROLLBACK, decoder.tick, 0)
        decoder.reset()
        self.clean(decoder.tick(0))

    def test_huge_bytes_rejected_before_copying_or_retention(self):
        huge = b"\0" * (8 * 1024 * 1024)
        decoder = StreamDecoder()
        decoder.feed(self.wire[:10], 0)
        tracemalloc.start()
        try:
            self.reject(D.CHUNK_SIZE, decoder.feed, huge, 1000000)
            self.reject(D.FRAME_SIZE, decode_frame, huge)
            self.reject(D.PAYLOAD_SIZE, Frame, 1, 1, 1, 1, huge)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 256 * 1024, "Отклонение не должно копировать огромный вход")
        self.clean(decoder.feed(self.wire[10:], 1), (self.frame,))

    def test_repeated_bad_chunks_do_not_accumulate_history(self):
        decoder = StreamDecoder()
        noise = b"\xff\0" * 2048
        decoder.feed(noise, 0)
        gc.collect()
        tracemalloc.start()
        try:
            for now in range(100):
                result = decoder.feed(noise, now)
                self.assertEqual(diagnostics(result), [(D.FRAMING, 2048)])
            del result
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(current, 64 * 1024)
        self.assertLess(peak, 256 * 1024)
        self.clean(decoder.feed(self.wire, 100), (self.frame,))


if __name__ == "__main__":
    unittest.main()

"""Авторские проверки HOST-002; независимое соответствие проверяется отдельно.

Фиксированный вектор взят из draft A 124b3f32. Для отрицательных кадров
используются отдельные побитовый CRC и COBS-сборщик, а не encode_frame().
Это программные проверки; USB, Windows и аппаратные измерения не выполнены.
"""
from dataclasses import FrozenInstanceError
import unittest

from motortester.wire import (
    Diagnostic, DiagnosticCode as Code, Frame, StreamDecoder, StreamResult,
    WireError, decode_frame, encode_frame, MAX_CHUNK_SIZE, MAX_ENCODED_SIZE,
    MAX_PAYLOAD_SIZE, MAX_RAW_SIZE, MAX_TIME_MS,
)


GOLDEN_RAW = bytes.fromhex(
    "00017f0006000000010203040506070801000000020000000001feff007e9ac2711a"
)
GOLDEN = bytes.fromhex(
    "0103017f020601010a010203040506070801010102020101010401feff067e9ac2711a00"
)
GOLDEN_FRAME = Frame(127, 0x0807060504030201, 1, 2, b"\x00\x01\xfe\xff\x00\x7e")


def reference_crc(data):
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def reference_cobs(raw):
    output = bytearray(b"\x00")
    marker = 0
    code = 1
    for byte in raw:
        if byte:
            output.append(byte)
            code += 1
        if not byte or code == 255:
            output[marker] = code
            marker = len(output)
            output.append(0)
            code = 1
    output[marker] = code
    return bytes(output)


def repaired_raw(raw):
    return bytes(raw[:-4]) + reference_crc(raw[:-4]).to_bytes(4, "little")


def snapshot(parser):
    return tuple(bytes(value) if isinstance(value, bytearray) else value
                 for name in parser.__slots__ for value in (getattr(parser, name),))


class WireCodecTests(unittest.TestCase):
    def assert_error(self, code, function, *args):
        with self.assertRaises(WireError) as caught:
            function(*args)
        self.assertIs(caught.exception.code, code)
        self.assertLess(len(str(caught.exception)), 100)

    def test_pinned_external_vector_in_both_directions(self):
        self.assertEqual(reference_crc(b"123456789"), 0xCBF43926)
        self.assertEqual(reference_crc(GOLDEN_RAW[:-4]), 0x1A71C29A)
        self.assertEqual(reference_cobs(GOLDEN_RAW) + b"\x00", GOLDEN)
        self.assertEqual(decode_frame(GOLDEN[:-1]), GOLDEN_FRAME)
        self.assertEqual(encode_frame(GOLDEN_FRAME), GOLDEN)

    def test_payloads_and_long_cobs_blocks_against_separate_reference(self):
        for size in (0, 1, 229, 230, 253, 254, 255, 508, 509, 512):
            for payload in (b"\xff" * size, b"\x00" * size,
                            bytes(index % 256 for index in range(size))):
                with self.subTest(size=size, prefix=payload[:2]):
                    raw = (b"\x00\x01\xff\x00" + size.to_bytes(2, "little")
                           + b"\x00\x00" + b"\xff" * 16 + payload)
                    raw += reference_crc(raw).to_bytes(4, "little")
                    expected = reference_cobs(raw) + b"\x00"
                    frame = Frame(255, 2**64 - 1, 2**32 - 1, 2**32 - 1, payload)
                    self.assertEqual(encode_frame(frame), expected)
                    self.assertEqual(decode_frame(expected[:-1]), frame)
                    self.assertLessEqual(len(expected), 544)
                    self.assertNotIn(0, expected[:-1])
        maximum = Frame(255, 2**64 - 1, 2**32 - 1, 2**32 - 1, b"\xff" * 512)
        self.assertEqual(len(encode_frame(maximum)), 544)
        self.assertEqual((MAX_RAW_SIZE, MAX_ENCODED_SIZE, MAX_PAYLOAD_SIZE),
                         (540, 543, 512))

    def test_strict_fields_and_payload_before_encoding(self):
        base = [127, 0, 0, 0, b""]
        for field, maximum in enumerate((255, 2**64 - 1, 2**32 - 1, 2**32 - 1)):
            for value in (True, False, 1.0, "1", None):
                arguments = base.copy()
                arguments[field] = value
                self.assert_error(Code.ARGUMENT, Frame, *arguments)
            for value in (-1, maximum + 1, 2**10000):
                arguments = base.copy()
                arguments[field] = value
                self.assert_error(Code.RANGE, Frame, *arguments)
        for value in (None, "", bytearray(b"a"), memoryview(b"a"), [1]):
            self.assert_error(Code.ARGUMENT, Frame, *base[:4], value)
        self.assert_error(Code.PAYLOAD_SIZE, Frame, *base[:4], b"a" * 513)
        for value in (None, {}, GOLDEN_RAW):
            self.assert_error(Code.ARGUMENT, encode_frame, value)

    def test_bytes_and_frame_are_immutable(self):
        frame = decode_frame(GOLDEN[:-1])
        with self.assertRaises(FrozenInstanceError):
            frame.sequence = 12
        with self.assertRaises(TypeError):
            frame.payload[0] = 9
        self.assertEqual(frame.payload, GOLDEN_FRAME.payload)

    def test_framing_lengths_and_delimiter_are_rejected(self):
        for value in (None, "", bytearray(GOLDEN), memoryview(GOLDEN)):
            self.assert_error(Code.ARGUMENT, decode_frame, value)
        for encoded in (b"", b"\x00", b"\x02", b"\x02\x00",
                        b"\xff" + b"x" * 253, GOLDEN):
            self.assert_error(Code.FRAMING, decode_frame, encoded)
        self.assert_error(Code.FRAME_SIZE, decode_frame, b"\x01" * 544)
        for encoded in (b"\x01", b"\x01" * 542,
                        reference_cobs(GOLDEN_RAW[:27])):
            self.assert_error(Code.LENGTH, decode_frame, encoded)
        for end in range(1, len(GOLDEN) - 1):
            with self.subTest(end=end), self.assertRaises(WireError):
                decode_frame(GOLDEN[:end])

    def test_validation_order_length_crc_version_reserved(self):
        raw = bytearray(GOLDEN_RAW)
        raw[0] = 1
        raw[3] = 1
        raw[4:6] = b"\x01\x02"  # 513: длина отвергается до CRC и версии.
        self.assert_error(Code.LENGTH, decode_frame, reference_cobs(raw))
        raw[4:6] = b"\x06\x00"
        self.assert_error(Code.CRC, decode_frame, reference_cobs(raw))
        self.assert_error(Code.VERSION, decode_frame, reference_cobs(repaired_raw(raw)))
        for index, value in ((0, 1), (1, 0), (1, 2)):
            raw = bytearray(GOLDEN_RAW)
            raw[index] = value
            self.assert_error(Code.VERSION, decode_frame, reference_cobs(repaired_raw(raw)))
        for index in (3, 6, 7):
            raw = bytearray(GOLDEN_RAW)
            raw[index] = 1
            self.assert_error(Code.RESERVED, decode_frame, reference_cobs(repaired_raw(raw)))
        raw = bytearray(GOLDEN_RAW)
        raw[-1] ^= 1
        self.assert_error(Code.CRC, decode_frame, reference_cobs(raw))


class WireStreamTests(unittest.TestCase):
    def assert_codes(self, result, *codes):
        self.assertEqual(result.diagnostics, tuple(Diagnostic(code, 1) for code in codes))

    def test_all_chunk_boundaries_and_bytewise_delivery(self):
        frames = (GOLDEN_FRAME,
                  Frame(255, 2**64 - 1, 2**32 - 1, 2**32 - 1, b"\xff" * 512))
        for frame in frames:
            encoded = encode_frame(frame)
            for split in range(len(encoded) + 1):
                with self.subTest(size=len(encoded), split=split):
                    parser = StreamDecoder()
                    first = parser.feed(encoded[:split], 10)
                    second = parser.feed(encoded[split:], 11)
                    self.assertEqual(first.frames + second.frames, (frame,))
                    self.assertFalse(first.diagnostics or second.diagnostics)
            parser = StreamDecoder()
            delivered = []
            for now, byte in enumerate(encoded):
                result = parser.feed(bytes((byte,)), now)
                delivered.extend(result.frames)
                self.assertFalse(result.diagnostics)
            self.assertEqual(delivered, [frame])

    def test_coalesced_frames_empty_delimiters_and_order(self):
        second = Frame(144, 23, 12, 0, b"\xff\x00")
        parser = StreamDecoder()
        result = parser.feed(b"\x00\x00" + GOLDEN + b"\x00"
                             + encode_frame(second) + b"\x00", 0)
        self.assertEqual(result.frames, (GOLDEN_FRAME, second))
        self.assertFalse(result.diagnostics)
        self.assertEqual(parser.tick(100000), StreamResult((), ()))

    def test_error_aggregation_and_immediate_next_frame_recovery(self):
        corrupt = bytearray(GOLDEN_RAW)
        corrupt[-1] ^= 1
        bad_crc = reference_cobs(corrupt) + b"\x00"
        parser = StreamDecoder()
        result = parser.feed(b"\x02\x00" + GOLDEN + bad_crc
                             + b"\x02\x00" + GOLDEN, 0)
        self.assertEqual(result.frames, (GOLDEN_FRAME, GOLDEN_FRAME))
        self.assertEqual(result.diagnostics, (Diagnostic(Code.FRAMING, 2),
                                              Diagnostic(Code.CRC, 1)))
        self.assertEqual(parser.tick(0), StreamResult((), ()))

    def test_exact_overflow_and_discard_through_delimiter(self):
        parser = StreamDecoder()
        self.assertEqual(parser.feed(b"\x01" * 543, 0), StreamResult((), ()))
        result = parser.feed(b"\x01", 0)
        self.assert_codes(result, Code.OVERFLOW)
        self.assertFalse(result.frames)
        self.assertEqual(parser.feed(GOLDEN, 1), StreamResult((), ()))
        self.assertEqual(parser.feed(GOLDEN, 2).frames, (GOLDEN_FRAME,))
        parser.reset()
        result = parser.feed(b"\x01" * 543 + b"\x00" + GOLDEN, 0)
        self.assert_codes(result, Code.LENGTH)
        self.assertEqual(result.frames, (GOLDEN_FRAME,))

    def test_interbyte_deadline_idle_tick_and_empty_feed(self):
        parser = StreamDecoder()
        parser.feed(GOLDEN[:-1], 100)
        self.assertEqual(parser.feed(b"", 1099), StreamResult((), ()))
        result = parser.feed(b"\x00" + GOLDEN, 1100)
        self.assert_codes(result, Code.TIMEOUT)
        self.assertEqual(result.frames, (GOLDEN_FRAME,))
        parser.reset()
        parser.feed(GOLDEN[:-1], 100)
        self.assertEqual(parser.feed(b"\x00", 1099).frames, (GOLDEN_FRAME,))
        parser.reset()
        parser.feed(b"\x01", 0)
        self.assert_codes(parser.tick(1000), Code.TIMEOUT)
        self.assertEqual(parser.tick(9999), StreamResult((), ()))
        self.assertEqual(parser.feed(GOLDEN, 9999), StreamResult((), ()))
        self.assertEqual(parser.feed(GOLDEN, 9999).frames, (GOLDEN_FRAME,))

    def test_total_deadline_despite_recent_bytes(self):
        for delimiter_time, expected in ((2999, (GOLDEN_FRAME,)), (3000, ())):
            parser = StreamDecoder()
            parser.feed(GOLDEN[:1], 0)
            parser.feed(GOLDEN[1:2], 900)
            parser.feed(GOLDEN[2:3], 1800)
            parser.feed(GOLDEN[3:-1], 2700)
            result = parser.feed(b"\x00", delimiter_time)
            self.assertEqual(result.frames, expected)
            self.assert_codes(result, *((Code.TIMEOUT,) if not expected else ()))
            self.assertEqual(parser.feed(GOLDEN, delimiter_time).frames, (GOLDEN_FRAME,))
        parser.reset()
        parser.feed(b"\x01", 0)
        self.assert_codes(parser.tick(3000), Code.TIMEOUT)  # Оба срока, одно событие.

    def test_invalid_call_cannot_consume_expire_or_change_clock(self):
        invalid = ((bytearray(GOLDEN), 2000, Code.ARGUMENT),
                   (b"x" * 4097, 2000, Code.CHUNK_SIZE),
                   (b"", True, Code.ARGUMENT), (b"", 0.0, Code.ARGUMENT),
                   (b"", -1, Code.RANGE), (b"", MAX_TIME_MS + 1, Code.RANGE),
                   (b"", 99, Code.CLOCK_ROLLBACK))
        parser = StreamDecoder()
        parser.feed(GOLDEN[:-1], 100)
        before = snapshot(parser)
        for data, now, code in invalid:
            with self.assertRaises(WireError) as caught:
                parser.feed(data, now)
            self.assertIs(caught.exception.code, code)
            self.assertEqual(snapshot(parser), before)
        self.assertEqual(parser.feed(b"\x00", 1099).frames, (GOLDEN_FRAME,))
        parser.tick(MAX_TIME_MS)
        before = snapshot(parser)
        with self.assertRaises(WireError) as caught:
            parser.tick(MAX_TIME_MS - 1)
        self.assertIs(caught.exception.code, Code.CLOCK_ROLLBACK)
        self.assertEqual(snapshot(parser), before)

    def test_reset_clears_partial_discarding_and_time_baseline(self):
        for partial in (GOLDEN[:12], b"\x01" * 544):
            parser = StreamDecoder()
            parser.feed(partial, MAX_TIME_MS)
            parser.reset()
            result = parser.feed(GOLDEN, 0)
            self.assertEqual(result.frames, (GOLDEN_FRAME,))
            self.assertFalse(result.diagnostics)

    def test_maximum_results_and_repeated_noise_are_bounded(self):
        parser = StreamDecoder()
        tiny = encode_frame(Frame(0, 0, 0, 0, b""))
        self.assertEqual(len(tiny), 30)
        parser.feed(tiny[:-1], 0)
        result = parser.feed(b"\x00" + tiny * 136 + b"\x00" * 15, 0)
        self.assertEqual(len(result.frames), 137)
        self.assertFalse(result.diagnostics)
        for now in range(100):
            result = parser.feed(b"\x02\x00" * (MAX_CHUNK_SIZE // 2), now)
            self.assertEqual(result.frames, ())
            self.assertEqual(result.diagnostics, (Diagnostic(Code.FRAMING, 2048),))
            self.assertEqual(len(parser._buffer), 543)
            self.assertEqual(parser._used, 0)
        self.assertEqual(parser.tick(99), StreamResult((), ()))
        self.assertFalse(hasattr(parser, "__dict__"))

    def test_returned_batches_do_not_alias_parser_storage(self):
        parser = StreamDecoder()
        result = parser.feed(GOLDEN + b"\x02\x00", 0)
        parser.feed(encode_frame(Frame(1, 0, 0, 0, b"z" * 512)), 1)
        parser.reset()
        self.assertEqual(result.frames, (GOLDEN_FRAME,))
        self.assert_codes(result, Code.FRAMING)
        self.assertIs(type(result.frames), tuple)
        self.assertIs(type(result.diagnostics), tuple)
        with self.assertRaises(FrozenInstanceError):
            result.frames = ()
        with self.assertRaises(FrozenInstanceError):
            result.diagnostics[0].count = 2


if __name__ == "__main__":
    unittest.main()

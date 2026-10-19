"""Retained actual PR192 byte fixtures plus independent malformed-frame builders.

Fixtures copied without regeneration from vectors-v0.2-stream.json, SHA256
 e2b1a0db86143f2b787d70ed93269cf64c74c5a7867684f51b10b7210e375b0b.
Synthetic responses here are HOST tests, never firmware proof.
"""
import struct
import unittest
import zlib
from motortester import stream_wire as w

POSITIVES = {'hello': '0103020102080101010101010101010102010101020101010d08070605040302015d6029c000', 'hello_ack': '01030281021001010a080706050403020101010102010101010402010c030280069b28350101010105a860366c00', 'get_caps': '01030202010101010a080706050403020102010102020101054feebe0c00', 'caps': '01030282021801010a08070605040302010201010202010102010101020101040c50c301020101010280010102010101056729375d00', 'start': '01030208020401010a0807060504030201030101020301010201010105d87669f400', 'start_ack': '01030288021001010a0807060504030201030101020301010203010102010101010101010101010105897bceb900', 'stop': '01030205020401010a080706050403020104010102040101020301010530439db800', 'final_data': '01030298021c01010a080706050403020104010101010101020301010201010101010101010101010204010101010201010808ff0fa61d616700', 'end': '01030299022801010a080706050403020105010101010101020301010201010102040101010101010204010101010101020401010597bdf6a301010102040101053f0be0ea00', 'stop_ack': '01030285021401010a0807060504030201060101020401010203010102040101010101010205010101010101056557e09b00'}

NEGATIVES = {'zero_count': '01030298021401010a0807060504030201040101010101010203010102010101010101010101010101010101053e20d59100', 'oversized_count': '01030298031601010a08070605040302010401010101010102030101020101010101010101010101028101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010514be18d400', 'out_of_range_code': '01030298021601010a0807060504030201040101010101010203010102010101010101010101010102010101010610c3749b7400', 'reserved_data': '01030298021601010a0807060504030201040101010101010203010102010101010101010101010102010201010105c20390d100', 'wrong_config': '01030298021601010a0807060504030201040101010101010203010102020101010101010101010102010101010105c5b9aa8300', 'wrong_generation': '01030298021601010a0807060504030201040101010101010204010102010101010101010101010102010101010105e75af50c00', 'missing_first_sample': '01030298021601010a0807060504030201040101010101010203010102010101020101010101010102010101010105d18523f400', 'index_overflow': '01030298021601010a08070605040302010401010101010102030101020101010affffffffffffffff01010101010516751d5b00', 'wrong_rate': '01030282021801010a08070605040302010201010202010102010101020101040c51c30102010101028001010201010105f6b85ff300', 'false_clean_tail': '01030299022801010a080706050403020105010101010101020301010201010102040101010101010208010101010101020401010597bdf6a30101010204010105dea9eb5100'}


def independent_cobs(raw):
    # Split on zeros, then split long runs. Does not call production COBS.
    result = bytearray()
    for piece in raw.split(b"\x00"):
        while len(piece) >= 254:
            result.extend(b"\xff" + piece[:254])
            piece = piece[254:]
        result.extend(bytes((len(piece) + 1,)) + piece)
    return bytes(result)


def frame(kind, payload=b"", *, nonce=0x0102030405060708, sequence=1,
          request=0, major=0, minor=2, reserved=0, reserved2=0, size=None):
    raw = struct.pack("<BBBBHHQII", major, minor, int(kind), reserved,
                      len(payload) if size is None else size, reserved2,
                      nonce, sequence, request) + payload
    return independent_cobs(raw + struct.pack("<I", zlib.crc32(raw))) + b"\0"


def fixture(name):
    return bytes.fromhex(POSITIVES.get(name, NEGATIVES.get(name)))


class WireTests(unittest.TestCase):
    def test_actual_positive_frames_round_trip(self):
        for name in POSITIVES:
            with self.subTest(name=name):
                encoded = fixture(name)
                decoded = w.decode_frame(encoded)
                w.decode_payload(decoded)
                self.assertEqual(w.encode_frame(decoded.kind, decoded.nonce, decoded.sequence,
                                                decoded.request_id, decoded.payload), encoded)
                self.assertEqual(frame(decoded.kind, decoded.payload, nonce=decoded.nonce,
                                       sequence=decoded.sequence, request=decoded.request_id), encoded)

    def test_actual_negative_payloads_and_context_negatives(self):
        contextual = {"wrong_generation", "missing_first_sample"}
        for name in NEGATIVES:
            with self.subTest(name=name):
                decoded = w.decode_frame(fixture(name), "device")
                if name in contextual:
                    self.assertIsInstance(w.decode_payload(decoded), w.Data)
                else:
                    with self.assertRaises(w.WireError):
                        w.decode_payload(decoded)

    def test_cobs_bounds_and_zero_runs(self):
        for raw in (b"", b"\0", b"\0" * 540, b"a" * 540,
                    bytes(range(256)), b"x" * 254, b"x" * 508, b"x" * 254 + b"\0"):
            with self.subTest(length=len(raw)):
                encoded = w.cobs_encode(raw)
                self.assertLessEqual(len(encoded), w.MAX_ENCODED)
                self.assertEqual(w.cobs_decode(encoded), raw)
                self.assertEqual(independent_cobs(raw), encoded)
        for encoded in (b"", b"\0", b"\x03a", b"\x03a\0", b"\x01" * 544):
            with self.assertRaises(w.WireError):
                w.cobs_decode(encoded)
        with self.assertRaises(w.WireError):
            w.cobs_encode(b"a" * 541)

    def test_header_and_direction_negatives(self):
        for opts in ({"major": 1}, {"minor": 1}, {"reserved": 1}, {"reserved2": 1},
                     {"size": 513}, {"size": 1}, {"sequence": 0}):
            with self.subTest(opts=opts), self.assertRaises(w.WireError):
                w.decode_frame(frame(2, **opts))
        for kind in (0, 3, 254):
            with self.assertRaises(w.WireError):
                w.decode_frame(frame(kind))
        with self.assertRaises(w.WireError):
            w.decode_frame(fixture("stop"), "device")
        with self.assertRaises(w.WireError):
            w.decode_frame(fixture("end"), "host")
        raw = bytearray(w.cobs_decode(fixture("end")[:-1]))
        raw[-1] ^= 1
        with self.assertRaises(w.WireError):
            w.decode_frame(independent_cobs(bytes(raw)) + b"\0")

    def test_exact_payload_sizes_all_messages(self):
        for name in POSITIVES:
            decoded = w.decode_frame(fixture(name))
            for bad in (decoded.payload + b"\0", decoded.payload[:-1] if decoded.payload else b"x"):
                with self.subTest(name=name), self.assertRaises(w.WireError):
                    w.decode_payload(w.decode_frame(frame(decoded.kind, bad, nonce=decoded.nonce,
                                                         sequence=decoded.sequence, request=decoded.request_id)))

    def test_full_data_and_u64_exclusive_end(self):
        for first in (0, w.U64_MAX - 128):
            p = struct.pack("<IIQHH128H", 3, 1, first, 128, 0, *range(128))
            result = w.decode_payload(w.decode_frame(frame(152, p)))
            self.assertEqual(result.end_index, first + 128)
            self.assertEqual(len(result.raw_samples), 256)
        with self.assertRaises(w.WireError):
            w.decode_payload(w.decode_frame(frame(152, struct.pack("<IIQHHH", 3, 1, w.U64_MAX, 1, 0, 0))))

    def test_terminal_known_and_unknown_tail(self):
        for produced, reason, flags, stop in ((0, 0, 0, 4), (0, 2, 3, 0), (8, 1, 2, 0),
                                             (4, 2, 0, 0), (0, 5, 7, 0)):
            prefix = 0 if produced == 0 else 4
            p = struct.pack("<IIQQIIHHI", 3, 1, prefix, produced, 0 if not prefix else 4,
                            0 if not prefix else 123, reason, flags, stop)
            result = w.decode_payload(w.decode_frame(frame(153, p)))
            self.assertEqual(result.produced_end, None if flags & 1 else produced)
        for produced, flags, reason, stop in ((1, 3, 2, 0), (0, 1, 2, 0), (3, 2, 2, 0),
                                             (8, 0, 2, 0), (4, 8, 2, 0), (4, 0, 7, 0), (4, 0, 0, 0)):
            with self.subTest(produced=produced, flags=flags), self.assertRaises(w.WireError):
                w.decode_payload(w.decode_frame(frame(153, struct.pack("<IIQQIIHHI", 3, 1, 4, produced, 4, 123, reason, flags, stop))))

    def test_negotiation_profile_fields(self):
        for name in ("hello_ack", "caps"):
            original = w.decode_frame(fixture(name))
            excluded = set(range(8, 12)) if name == "hello_ack" else set()
            for index in range(len(original.payload)):
                if index in excluded:
                    continue  # firmware_build is an opaque identifier.
                p = bytearray(original.payload)
                p[index] ^= 1
                with self.subTest(name=name, index=index), self.assertRaises(w.WireError):
                    w.decode_payload(w.decode_frame(frame(original.kind, bytes(p))))

    def test_encoder_rejects_counter_types(self):
        for bad in (True, -1, 2**64, 1.0, None):
            with self.subTest(bad=bad), self.assertRaises(w.WireError):
                w.encode_frame(w.Kind.GET_CAPS, bad, 2, 2, b"")


if __name__ == "__main__":
    unittest.main()

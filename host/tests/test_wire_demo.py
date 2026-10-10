"""End-to-end synthetic replay boundaries; no hardware or session acceptance."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from motortester.model import CaptureError
from motortester.recording import load
from motortester.wire import decode_frame, encode_frame
from motortester.wire_demo import (CONTEXT, FRAMES_PER_WINDOW, synthetic_window,
    capture_from_synthetic_stream, save_synthetic_stream, run_demo)

STAMP = "2026-10-10T12:00:00+00:00"


class WireDemoTests(unittest.TestCase):
    def setUp(self):
        self.samples, self.data = synthetic_window()
        self.parts = [p + b"\0" for p in self.data.split(b"\0") if p]

    def test_twenty_windows_and_fragment_sizes_preserve_every_code(self):
        for window in range(1, 21):
            sequence = (window - 1) * FRAMES_PER_WINDOW + 1
            expected, wire = synthetic_window(window, sequence)
            self.assertEqual(expected[:4], (0, window, 256, 4095))
            self.assertEqual(len(expected), 2048)
            for chunk in (1, 7, 29, 97, 4096):
                with self.subTest(window=window, chunk=chunk):
                    capture = capture_from_synthetic_stream(wire, first_sequence=sequence,
                        chunk_size=chunk, received_at_utc=STAMP)
                    self.assertEqual(capture.samples, expected)
                    self.assertEqual(capture.sequence, window)
                    self.assertEqual(capture.origin, "simulator")
                    self.assertEqual(capture.nominal_fs_hz, 50_000)
                    self.assertEqual(capture.trigger_index, 512)
                    self.assertEqual(capture.received_at_utc, STAMP)
                    self.assertIsNone(capture.firmware_version)
                    self.assertIn("draft-0.1@", capture.protocol_version)

    def test_missing_corrupt_reordered_repeated_and_trailing_frames_reject(self):
        bad = [self.data + self.parts[-1], self.data + b"\x05\x01",
               self.parts[1] + self.parts[0] + b"".join(self.parts[2:]),
               self.parts[0] + self.parts[0] + b"".join(self.parts[1:])]
        for index in range(len(self.parts)):
            bad.append(b"".join(self.parts[:index] + self.parts[index + 1:]))
            changed = bytearray(self.parts[index]); changed[-2] ^= 1
            bad.append(b"".join(self.parts[:index] + [bytes(changed)] + self.parts[index + 1:]))
        for index in list(range(0, len(self.data), 173)) + [len(self.data) - 1]:
            bad.append(self.data[:index])
        for index, wire in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(CaptureError):
                capture_from_synthetic_stream(wire)

    def test_crc_valid_wrong_session_sequence_end_crc_and_metadata_reject(self):
        begin = decode_frame(self.parts[0][:-1])
        end = decode_frame(self.parts[-1][:-1])
        flags = bytearray(begin.payload); struct.pack_into("<H", flags, 26, 0)
        damaged_end = bytearray(end.payload); damaged_end[-1] ^= 1
        variants = [
            encode_frame(replace(begin, session=CONTEXT.session + 1)) + b"".join(self.parts[1:]),
            encode_frame(replace(begin, sequence=2)) + b"".join(self.parts[1:]),
            encode_frame(replace(begin, payload=bytes(flags))) + b"".join(self.parts[1:]),
            b"".join(self.parts[:-1]) + encode_frame(replace(end, payload=bytes(damaged_end))),
        ]
        for wire in variants:
            with self.subTest(prefix=wire[:15]), self.assertRaises(CaptureError):
                capture_from_synthetic_stream(wire)

    def test_bad_api_inputs_are_bounded_and_not_coerced(self):
        for data in (None, bytearray(self.data), "data", b"", b"x" * 16385):
            with self.subTest(data_type=type(data)), self.assertRaises(CaptureError):
                capture_from_synthetic_stream(data)
        for option in (True, 0, -1, 4097, "97", 1.5):
            with self.subTest(chunk=option), self.assertRaises(CaptureError):
                capture_from_synthetic_stream(self.data, chunk_size=option)
        for number in (True, 0, 21, "1", 1.5):
            with self.subTest(window=number), self.assertRaises(CaptureError):
                synthetic_window(number)
        for sequence in (True, 0, 2**32 - FRAMES_PER_WINDOW, 2**32 - 1, "1"):
            with self.subTest(sequence=sequence), self.assertRaises(CaptureError):
                synthetic_window(first_sequence=sequence)
        with self.assertRaises(CaptureError):
            capture_from_synthetic_stream(self.data, received_at_utc="not a timestamp")

    def test_largest_supported_sequence_stops_before_reserved_maximum(self):
        first = 2**32 - FRAMES_PER_WINDOW - 1
        samples, data = synthetic_window(first_sequence=first)
        capture = capture_from_synthetic_stream(data, first_sequence=first)
        self.assertEqual(capture.samples, samples)
        self.assertEqual(decode_frame(data.split(b"\0")[-2]).sequence, 2**32 - 2)

    def test_failed_decode_never_publishes_and_existing_record_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "проверка.mtcap"
            with self.assertRaises(CaptureError):
                save_synthetic_stream(path, self.data[:-1])
            self.assertFalse(path.exists())
            capture = save_synthetic_stream(path, self.data, received_at_utc=STAMP)
            original = path.read_bytes()
            self.assertEqual(load(path), capture)
            with self.assertRaises(OSError):
                save_synthetic_stream(path, self.data)
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(CaptureError):
                save_synthetic_stream(path, self.data[:-1])
            self.assertEqual(path.read_bytes(), original)

    def test_cli_outputs_manifest_files_and_graphs_without_clobber(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "поток"
            manifest = run_demo(target, windows=20)
            self.assertEqual(len(manifest["records"]), 20)
            self.assertEqual(json.loads((target / "manifest.json").read_text()), manifest)
            for index, record in enumerate(manifest["records"], 1):
                path = target / record["file"]
                capture = load(path)
                self.assertEqual(capture.samples[:4], (0, index, 256, 4095))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
                self.assertEqual(len(path.with_suffix(".csv").read_text().splitlines()), 2049)
                self.assertIn("<svg", path.with_suffix(".svg").read_text())
            with self.assertRaises(FileExistsError):
                run_demo(target)
            self.assertEqual(len(list(target.glob("*.mtcap"))), 20)


if __name__ == "__main__":
    unittest.main()

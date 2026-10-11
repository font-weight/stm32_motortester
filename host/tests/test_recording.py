"""HOST-001 H02–H05: независимые проверки исходных данных и файла.

Fixture собран без encode(), чтобы симметричная ошибка encode/decode не
подменяла проверку порядка байтов, метаданных и целостности. Это внутренний
формат прототипа, не USB-протокол и не аппаратное испытание.
"""
import csv
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from motortester.model import Capture, CaptureError
from motortester.recording import decode, encode, export_csv, load, save


# Независимые контрольные значения формата первой версии.
FILE_MAGIC = b"MTCAP\x00\x01\x00"
FILE_HEADER = struct.Struct("<8sII")
MAX_METADATA_BYTES = 16_384
MAX_SAMPLE_COUNT = 65_536
MAX_FILE_BYTES = 16 + MAX_METADATA_BYTES + 2 * MAX_SAMPLE_COUNT + 32
RAW_CODES = (0, 1, 255, 256, 2048, 4095, 17)
RAW_BYTES = b"\x00\x00\x01\x00\xff\x00\x00\x01\x00\x08\xff\x0f\x11\x00"
SESSION = "97f9c5dd-6658-43c7-a35d-ea5296b206a0"
STAMP = "2026-10-10T11:00:00.123456+00:00"


def example_capture(**changes):
    values = dict(
        samples=RAW_CODES, session_id=SESSION, sequence=19,
        nominal_fs_hz=50_000, received_at_utc=STAMP, origin="simulator",
        trigger_kind="signal", trigger_index=2, trigger_edge="falling",
        trigger_level_code=256, pretrigger_samples=2, firmware_version=None,
        protocol_version=None, sensor="Синтетический источник; датчика нет",
        quality_flags=("rail_observed",), application_version="0.1.0", complete=True,
    )
    values.update(changes)
    return Capture(**values)


def example_metadata(**changes):
    # Не выводим ожидаемые поля из Capture/asdict или кода сериализатора.
    values = dict(
        format_version=1, channel_count=1, adc_bits=12,
        sample_encoding="uint16-le", time_basis="nominal_unverified",
        amplitude_unit="adc_code", calibration="uncalibrated",
        timestamp_origin="host_receive", sample_count=7,
        session_id=SESSION, sequence=19, nominal_fs_hz=50_000,
        received_at_utc=STAMP, origin="simulator", trigger_kind="signal",
        trigger_index=2, trigger_edge="falling", trigger_level_code=256,
        pretrigger_samples=2, firmware_version=None, protocol_version=None,
        sensor="Синтетический источник; датчика нет",
        quality_flags=["rail_observed"], application_version="0.1.0", complete=True,
    )
    values.update(changes)
    return values


def make_file(metadata=None, *, raw=RAW_BYTES, count=7, magic=FILE_MAGIC):
    """Собрать правильную SHA-256 даже для семантически неверного файла."""
    if metadata is None:
        metadata = example_metadata()
    if not isinstance(metadata, bytes):
        metadata = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    body = FILE_HEADER.pack(magic, len(metadata), count) + metadata + raw
    return body + hashlib.sha256(body).digest()


class CaptureValidationTests(unittest.TestCase):
    def test_raw_array_and_record_are_immutable(self):
        capture = example_capture()
        self.assertIs(type(capture.samples), tuple)
        with self.assertRaises(TypeError):
            capture.samples[0] = 123
        with self.assertRaises(FrozenInstanceError):
            capture.samples = (123,)
        with self.assertRaises(FrozenInstanceError):
            capture.sequence = 100

    def test_raw_sample_types_and_range_are_not_coerced(self):
        for samples in ((), [0, 1], (True,), (False,), (1.0,), ("1",),
                        (-1,), (4096,), (65535,), (None,)):
            with self.subTest(samples=samples), self.assertRaises(CaptureError):
                example_capture(samples=samples, trigger_index=0,
                                pretrigger_samples=0)

    def test_metadata_types_ranges_and_enums_are_validated(self):
        bad_values = {
            "session_id": [None, 123, "", "not-a-uuid"],
            "sequence": [True, 1.0, "1", -1, 2**32],
            "nominal_fs_hz": [True, "50000", None, 0, 0.5, -1, 1_000_001,
                              float("nan"), float("inf"), -float("inf")],
            "origin": [None, 1, "synthetic?", ""],
            "trigger_kind": [None, 1, "unknown"],
            "trigger_edge": [None, 1, "both"],
            "trigger_index": [True, 1.0, "2", -1, 7, None],
            "trigger_level_code": [True, 1.0, "256", -1, 4096],
            "pretrigger_samples": [True, 2.0, "2", -1, 7],
            "firmware_version": [True, 1, "", "x" * 129],
            "protocol_version": [True, 1, "", "x" * 129],
            "application_version": [None, True, 1, "", "x" * 129],
            "complete": [False, None, 1, "true"],
            "sensor": [None, 1, "", "x" * 257],
            "quality_flags": [None, [], (1,), ("",), ("x" * 65,), ("x",) * 17],
        }
        for key, values in bad_values.items():
            for value in values:
                with self.subTest(field=key, value=value), self.assertRaises(CaptureError):
                    example_capture(**{key: value})

    def test_huge_integer_sample_rate_is_a_controlled_input_error(self):
        with self.assertRaises(CaptureError):
            example_capture(nominal_fs_hz=10**400)

    def test_incomplete_samples_are_rejected_instead_of_zero_filled(self):
        for flags in (("incomplete",), ("missing_samples",)):
            with self.subTest(flags=flags), self.assertRaises(CaptureError):
                example_capture(quality_flags=flags)

    def test_text_unsafe_for_encoding_or_svg_is_rejected(self):
        for character in ("\x00", "\x1f", "\ud800", "\ufffe", "\uffff"):
            with self.subTest(character=repr(character)), self.assertRaises(CaptureError):
                example_capture(quality_flags=("flag" + character,))

    def test_utc_timestamp_preserved_and_ambiguous_or_invalid_time_rejected(self):
        for stamp in (STAMP, "2026-10-10T11:00:00Z", "2026-10-10T00:00:00+00:00"):
            with self.subTest(stamp=stamp):
                self.assertEqual(example_capture(received_at_utc=stamp).received_at_utc, stamp)
        for stamp in (None, 1, "", "yesterday", "2026-10-10",
                      "2026-10-10T11:00:00", "2026-10-10T11:00:00+01:00",
                      "2026-02-30T11:00:00Z"):
            with self.subTest(stamp=stamp), self.assertRaises(CaptureError):
                example_capture(received_at_utc=stamp)

    def test_utc_timestamp_text_length_accepts_64_and_rejects_65(self):
        # fromisoformat принимает длинную дробную часть: ограничение длины
        # должно проверяться отдельно, без усечения исходных метаданных.
        longest = "2026-10-10T11:00:00." + "1" * 43 + "Z"
        too_long = "2026-10-10T11:00:00." + "1" * 44 + "Z"
        self.assertEqual(len(longest), 64)
        self.assertEqual(len(too_long), 65)
        capture = example_capture(received_at_utc=longest)
        self.assertEqual(capture.received_at_utc, longest)
        self.assertEqual(decode(encode(capture)).received_at_utc, longest)
        with self.assertRaises(CaptureError):
            example_capture(received_at_utc=too_long)
        with self.assertRaises(CaptureError):
            decode(make_file(example_metadata(received_at_utc=too_long)))

    def test_trigger_and_pretrigger_must_agree(self):
        with self.assertRaises(CaptureError):
            example_capture(trigger_index=3, pretrigger_samples=2)
        with self.assertRaises(CaptureError):
            example_capture(trigger_kind="auto_timeout", trigger_index=0)
        capture = example_capture(trigger_kind="auto_timeout", trigger_index=None)
        self.assertIsNone(capture.trigger_index)
        self.assertEqual(capture.time_seconds(0), 0)

    def test_nominal_time_uses_trigger_and_valid_integer_index(self):
        capture = example_capture()
        self.assertEqual(capture.time_seconds(0), -2 / 50_000)
        self.assertEqual(capture.time_seconds(2), 0)
        self.assertEqual(capture.time_seconds(6), 4 / 50_000)
        for index in (True, 1.0, "1", -1, 7):
            with self.subTest(index=index), self.assertRaises(CaptureError):
                capture.time_seconds(index)

    def test_boundary_codes_raise_rail_warning_without_changing_samples(self):
        for code in (0, 4095):
            capture = example_capture(samples=(code,), trigger_index=0,
                                      pretrigger_samples=0)
            self.assertTrue(capture.rail_warning)
            self.assertEqual(capture.samples, (code,))
        self.assertFalse(example_capture(samples=(1, 2048, 4094),
                                        trigger_index=0, pretrigger_samples=0).rail_warning)


class RecordingFormatTests(unittest.TestCase):
    def test_decode_independent_binary_fixture(self):
        self.assertEqual(decode(make_file()), example_capture())

    def test_encoder_contains_little_endian_raw_codes_and_honest_metadata(self):
        capture = example_capture()
        data = encode(capture)
        magic, metadata_size, sample_count = FILE_HEADER.unpack_from(data)
        self.assertEqual(magic, FILE_MAGIC)
        self.assertEqual(sample_count, 7)
        end = FILE_HEADER.size + metadata_size
        self.assertEqual(data[end:-32], RAW_BYTES)
        self.assertEqual(json.loads(data[FILE_HEADER.size:end]), example_metadata())
        self.assertEqual(data[-32:], hashlib.sha256(data[:-32]).digest())
        self.assertEqual(len(data), 16 + metadata_size + 14 + 32)
        self.assertEqual(capture.samples, RAW_CODES)

    def test_all_metadata_and_raw_samples_round_trip(self):
        capture = example_capture(origin="device", firmware_version="test-fw-0",
                                  protocol_version="test-protocol-0",
                                  quality_flags=("rail_observed", "test_flag"))
        self.assertEqual(decode(encode(capture)), capture)

    def test_minimum_and_maximum_sample_counts_round_trip(self):
        for count in (1, MAX_SAMPLE_COUNT):
            with self.subTest(count=count):
                samples = tuple(i % 4096 for i in range(count))
                capture = example_capture(samples=samples, trigger_index=0,
                                          pretrigger_samples=0)
                self.assertEqual(decode(encode(capture)), capture)
        with self.assertRaises(CaptureError):
            example_capture(samples=(1,) * (MAX_SAMPLE_COUNT + 1))

    def test_absent_event_and_timestamp_are_preserved(self):
        capture = example_capture(trigger_kind="auto_timeout", trigger_index=None,
                                  received_at_utc="2026-10-10T11:00:00Z")
        opened = decode(encode(capture))
        self.assertEqual(opened, capture)
        self.assertIsNone(opened.trigger_index)
        self.assertEqual(opened.time_seconds(0), 0)

    def test_every_truncation_of_control_fixture_is_rejected(self):
        data = make_file()
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(CaptureError):
                decode(data[:length])

    def test_extra_bytes_and_excessive_file_are_rejected(self):
        for data in (make_file() + b"\x00", make_file() * 2,
                     b"\x00" * (MAX_FILE_BYTES + 1)):
            with self.subTest(length=len(data)), self.assertRaises(CaptureError):
                decode(data)

    def test_corruption_of_header_metadata_samples_or_digest_is_rejected(self):
        data = make_file()
        for offset in (0, 8, 12, 16, len(data) - 34, len(data) - 32, len(data) - 1):
            damaged = bytearray(data)
            damaged[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(CaptureError):
                decode(bytes(damaged))

    def test_unknown_magic_and_header_version_are_rejected(self):
        for magic in (b"NOTMTCAP", b"MTCAP\x00\x02\x00", b"MTCAP\x00\x00\x00"):
            with self.subTest(magic=magic), self.assertRaises(CaptureError):
                decode(make_file(magic=magic))

    def test_header_length_and_count_limits_are_enforced(self):
        for meta_length, count in ((0, 7), (MAX_METADATA_BYTES + 1, 7),
                                   (0xFFFFFFFF, 7), (1, 0),
                                   (1, MAX_SAMPLE_COUNT + 1), (1, 0xFFFFFFFF)):
            body = FILE_HEADER.pack(FILE_MAGIC, meta_length, count) + b"{}"
            data = body + hashlib.sha256(body).digest()
            with self.subTest(meta_length=meta_length, count=count), self.assertRaises(CaptureError):
                decode(data)
        for count in (6, 8):
            with self.subTest(count=count), self.assertRaises(CaptureError):
                decode(make_file(count=count))

    def test_constant_metadata_must_match_format_and_have_exact_types(self):
        constants = ("format_version", "channel_count", "adc_bits", "sample_count")
        for key in constants:
            original = example_metadata()[key]
            for value in (True, float(original), str(original), 0, original + 1, None):
                with self.subTest(field=key, value=value), self.assertRaises(CaptureError):
                    decode(make_file(example_metadata(**{key: value})))
        for key in ("sample_encoding", "time_basis", "amplitude_unit", "calibration",
                    "timestamp_origin"):
            for value in (None, 1, "unknown"):
                with self.subTest(field=key, value=value), self.assertRaises(CaptureError):
                    decode(make_file(example_metadata(**{key: value})))

    def test_missing_and_unknown_metadata_are_rejected(self):
        for key in example_metadata():
            metadata = example_metadata()
            del metadata[key]
            with self.subTest(missing=key), self.assertRaises(CaptureError):
                decode(make_file(metadata))
        with self.assertRaises(CaptureError):
            decode(make_file(example_metadata(unrecognized_extension="value")))

    def test_duplicate_json_fields_are_rejected_even_when_values_agree(self):
        metadata = json.dumps(example_metadata()).encode("utf-8")
        for field, value in (("sequence", 19), ("sequence", 20),
                             ("format_version", 1), ("nominal_fs_hz", 50000)):
            duplicate = metadata[:-1] + f',"{field}":{value}}}'.encode("ascii")
            with self.subTest(field=field, value=value), self.assertRaises(CaptureError):
                decode(make_file(duplicate))
        # JSON-экранирование не должно скрывать дубликат логического имени.
        with self.assertRaises(CaptureError):
            decode(make_file(metadata[:-1] + b',"\\u0073equence":19}'))

    def test_metadata_requires_utf8_without_encoding_autodetection(self):
        text = json.dumps(example_metadata(), ensure_ascii=False)
        self.assertEqual(decode(make_file(text.encode("utf-8"))), example_capture())
        for encoding in ("utf-16", "utf-16-le", "utf-16-be",
                         "utf-32", "utf-32-le", "utf-32-be", "utf-8-sig"):
            with self.subTest(encoding=encoding), self.assertRaises(CaptureError):
                decode(make_file(text.encode(encoding)))

    def test_invalid_json_encoding_and_non_object_metadata_are_rejected(self):
        for metadata in (b"", b"{", b"\xff", b"[]", b"null", b"1", b"true", b'"text"',
                         b"[" * 1100 + b"0" + b"]" * 1100):
            with self.subTest(length=len(metadata)), self.assertRaises(CaptureError):
                decode(make_file(metadata))

    def test_non_finite_or_wrong_type_metadata_is_not_silently_coerced(self):
        cases = (("sequence", True), ("sequence", 19.0), ("sequence", "19"),
                 ("nominal_fs_hz", "50000"), ("nominal_fs_hz", True),
                 ("nominal_fs_hz", 0), ("nominal_fs_hz", -1),
                 ("nominal_fs_hz", float("nan")), ("nominal_fs_hz", float("inf")),
                 ("trigger_index", 2.0), ("trigger_index", True),
                 ("trigger_level_code", "256"), ("pretrigger_samples", 2.0),
                 ("received_at_utc", 123), ("quality_flags", "rail_observed"),
                 ("quality_flags", [None]), ("sensor", 123),
                 ("firmware_version", 1), ("protocol_version", False),
                 ("application_version", None), ("application_version", 1),
                 ("complete", 1), ("complete", "true"), ("complete", False),
                 ("quality_flags", ["missing_samples"]))
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(CaptureError):
                decode(make_file(example_metadata(**{field: value})))

    def test_huge_integer_in_valid_json_is_rejected_with_capture_error(self):
        with self.assertRaises(CaptureError):
            decode(make_file(example_metadata(nominal_fs_hz=10**400)))

    def test_uint16_values_above_adc_range_are_not_clipped(self):
        for code in (4096, 65535):
            raw = struct.pack("<7H", 0, 1, 255, 256, 2048, code, 17)
            with self.subTest(code=code), self.assertRaises(CaptureError):
                decode(make_file(raw=raw))

    def test_metadata_sample_count_cannot_disagree_with_payload(self):
        with self.assertRaises(CaptureError):
            decode(make_file(example_metadata(sample_count=6)))

    def test_loader_reads_no_more_than_file_limit_plus_one(self):
        with mock.patch("builtins.open", mock.mock_open(read_data=b"x" * (MAX_FILE_BYTES + 1))) as opened:
            with self.assertRaises(CaptureError):
                load("oversized.mtcap")
            opened().read.assert_called_once_with(MAX_FILE_BYTES + 1)


class SafeFileOperationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "окно №19.mtcap"
        self.capture = example_capture()

    def assert_no_temporary_files(self):
        self.assertEqual(list(self.root.glob(".motortester-*")), [])

    def test_save_then_reopen_from_unicode_path(self):
        save(self.path, self.capture)
        self.assertEqual(load(self.path), self.capture)
        self.assert_no_temporary_files()

    def test_existing_recording_is_not_clobbered_by_default(self):
        self.path.write_bytes(b"existing data")
        with self.assertRaises(FileExistsError):
            save(self.path, self.capture)
        self.assertEqual(self.path.read_bytes(), b"existing data")
        self.assert_no_temporary_files()

    def test_overwrite_publishes_complete_new_recording(self):
        old = b"previous complete recording"
        self.path.write_bytes(old)
        real_replace = os.replace
        transitions = []

        def verify_then_replace(source, destination):
            transitions.append(True)
            self.assertEqual(Path(source).parent, self.root)
            self.assertEqual(self.path.read_bytes(), old)
            self.assertEqual(load(source), self.capture)
            real_replace(source, destination)

        with mock.patch("motortester.recording.os.replace", side_effect=verify_then_replace):
            save(self.path, self.capture, overwrite=True)
        self.assertEqual(transitions, [True])
        self.assertEqual(load(self.path), self.capture)
        self.assert_no_temporary_files()

    def test_fsync_failure_preserves_existing_file_and_removes_temporary(self):
        self.path.write_bytes(b"previous")
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                with mock.patch("motortester.recording.os.fsync", side_effect=OSError("disk failure")):
                    with self.assertRaises(OSError):
                        save(self.path, self.capture, overwrite=overwrite)
                self.assertEqual(self.path.read_bytes(), b"previous")
                self.assert_no_temporary_files()

    def test_failed_new_file_save_does_not_publish_partial_recording(self):
        with mock.patch("motortester.recording.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                save(self.path, self.capture)
        self.assertFalse(self.path.exists())
        self.assert_no_temporary_files()

    def test_publish_failure_does_not_destroy_previous_file(self):
        self.path.write_bytes(b"previous")
        with mock.patch("motortester.recording.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                save(self.path, self.capture, overwrite=True)
        self.assertEqual(self.path.read_bytes(), b"previous")
        self.assert_no_temporary_files()

    def test_no_clobber_holds_when_destination_appears_during_save(self):
        real_link = os.link

        def create_competing_file_then_link(source, destination):
            Path(destination).write_bytes(b"concurrent writer")
            real_link(source, destination)

        with mock.patch("motortester.recording.os.link", side_effect=create_competing_file_then_link):
            with self.assertRaises(FileExistsError):
                save(self.path, self.capture)
        self.assertEqual(self.path.read_bytes(), b"concurrent writer")
        self.assert_no_temporary_files()

    def test_failed_serialization_never_touches_existing_recording(self):
        self.path.write_bytes(b"previous")
        with mock.patch("motortester.recording.encode", side_effect=CaptureError("invalid")):
            with self.assertRaises(CaptureError):
                save(self.path, self.capture, overwrite=True)
        self.assertEqual(self.path.read_bytes(), b"previous")
        self.assert_no_temporary_files()

    def test_unavailable_parent_is_reported_without_creating_destination(self):
        destination = self.root / "missing" / "capture.mtcap"
        with self.assertRaises(OSError):
            save(destination, self.capture)
        self.assertFalse(destination.exists())
        self.assert_no_temporary_files()

    def test_csv_has_declared_units_raw_values_order_and_provenance(self):
        path = self.root / "отсчёты.csv"
        original = encode(self.capture)
        export_csv(path, self.capture)
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0], ["sample_index", "time_nominal_s", "adc_code",
                                   "origin", "session_id", "window_sequence"])
        self.assertEqual(len(rows), 8)
        for index, row in enumerate(rows[1:]):
            self.assertEqual(len(row), 6)
            self.assertEqual(int(row[0]), index)
            self.assertEqual(float(row[1]), (index - 2) / 50_000)
            self.assertEqual(int(row[2]), RAW_CODES[index])
            self.assertEqual(row[3:], ["simulator", SESSION, "19"])
        self.assertEqual(encode(self.capture), original)

    def test_csv_default_no_clobber_and_explicit_overwrite(self):
        path = self.root / "samples.csv"
        path.write_bytes(b"previous csv")
        with self.assertRaises(FileExistsError):
            export_csv(path, self.capture)
        self.assertEqual(path.read_bytes(), b"previous csv")
        export_csv(path, self.capture, overwrite=True)
        self.assertTrue(path.read_text(encoding="utf-8").startswith("sample_index,time_nominal_s"))
        self.assert_no_temporary_files()


if __name__ == "__main__":
    unittest.main()

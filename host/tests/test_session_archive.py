"""Synthetic session storage tests; no hardware/Windows/physical Stop evidence."""
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
from uuid import UUID
import zlib
import zipfile

from motortester import session_archive as sa
from motortester.model import CaptureError
from motortester.recording import decode, encode

SESSION = "97f9c5dd-6658-43c7-a35d-ea5296b206a0"
RECORDING = "791d31e9-15d3-4e26-897a-bbe6d8ac73ce"
STAMP = "2026-10-12T18:00:00.123456Z"
IDENTITY = sa.SessionIdentity(RECORDING, SESSION, "simulator", None, None, "0.1.0")
PROVENANCE = sa.SegmentProvenance(2, 1, 0, 100, 0, "0.1", "arm")
RAW = (0, 1, 255, 256, 2048, 4095, 17)


def capture_bytes(sequence=1, **changes):
    """Independent v1 binary fixture, deliberately noncanonical JSON whitespace."""
    meta = dict(format_version=1, channel_count=1, adc_bits=12,
        sample_encoding="uint16-le", time_basis="nominal_unverified",
        amplitude_unit="adc_code", calibration="uncalibrated", timestamp_origin="host_receive",
        sample_count=7, session_id=SESSION, sequence=sequence, nominal_fs_hz=50000,
        received_at_utc=STAMP, origin="simulator", trigger_kind="signal", trigger_index=2,
        trigger_edge="falling", trigger_level_code=256, pretrigger_samples=2,
        firmware_version=None, protocol_version=None, sensor="Синтетический источник",
        quality_flags=["rail_observed"], application_version="0.1.0", complete=True)
    meta.update(changes)
    text = json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8")
    body = struct.pack("<8sII", b"MTCAP\x00\x01\x00", len(text), 7) + text + struct.pack("<7H", *RAW)
    return body + hashlib.sha256(body).digest()


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_zip(path, members, mutate_info=None):
    """Independent writer of the documented ZIP profile."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", allowZip64=False) as archive:
            for name, data in members:
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100600 << 16
                info.compress_type = zipfile.ZIP_STORED
                if mutate_info:
                    mutate_info(info)
                archive.writestr(info, data)


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "сеанс.spool"
        self.dest = self.root / "запись.mtrec"
        self.spool = sa.SessionSpool.create(self.path, IDENTITY)
        self.addCleanup(self.spool.close)

    def finish(self, count=3):
        for n in range(count):
            self.spool.append_capture(capture_bytes(n + 1), replace(PROVENANCE, host_elapsed_ms=n))
        report = self.spool.finish("user_stop", "stop_ack", "Синтетический тест")
        sa.finalize(report, self.dest)
        return report

    def members(self):
        with zipfile.ZipFile(self.dest) as archive:
            return [(i.filename, archive.read(i)) for i in archive.infolist()]

    def rewrite(self, mutate, mutate_info=None):
        members = self.members()
        mutate(members)
        write_zip(self.dest, members, mutate_info)

    def mutate_manifest(self, mutate):
        def change(members):
            obj = json.loads(members[0][1]); mutate(obj)
            members[0] = ("manifest.json", json_bytes(obj))
        self.rewrite(change)

    def reject(self):
        with self.assertRaises(CaptureError):
            sa.load(self.dest)


class RoundTripTests(ArchiveCase):
    def test_three_verbatim_windows_partial_and_no_begin_event(self):
        originals = [capture_bytes(7), capture_bytes(9, trigger_kind="external"),
                     capture_bytes(10, trigger_kind="auto_timeout", trigger_index=None)]
        receipts = [self.spool.append_capture(data, replace(PROVENANCE, host_elapsed_ms=n))
                    for n, data in enumerate(originals)]
        partial = sa.PartialSegment(11, 10,
            (sa.SampleFragment(1, (0, 4095)), sa.SampleFragment(7, (255, 256))),
            ((0, 1), (3, 7), (9, 10)), "Stop во время окна", nominal_fs_hz=50000,
            received_at_utc=STAMP)
        self.spool.append_partial(partial)
        self.spool.append_abort(sa.AbortEvent("ABORT до BEGIN"))
        report = self.spool.finish("user_stop", "stop_ack")
        opened = sa.finalize(report, self.dest)
        self.assertEqual(sa.load(self.dest), opened)
        self.assertEqual(len(opened.entries), 5)
        for n, original in enumerate(originals):
            self.assertEqual(opened.read_segment(n), original)
            self.assertEqual(decode(opened.read_segment(n)).samples, RAW)
            self.assertNotEqual(original, encode(decode(original)))
            self.assertEqual(receipts[n].identity, IDENTITY)
            self.assertEqual(receipts[n].entry, opened.entries[n])
        self.assertEqual(opened.read_segment(3), b"\0\0\xff\x0f\xff\0\0\x01")
        self.assertEqual(opened.entries[3].partial.fragments, ((1, 2), (7, 2)))
        self.assertEqual(opened.entries[3].partial.missing_ranges, partial.missing_ranges)
        self.assertEqual(json.loads(opened.read_segment(4))["kind"], "no_begin_abort")
        self.assertIsNone(opened.entries[4].sample_count)
        manifest = json.loads(self.members()[0][1])
        self.assertEqual(manifest["coverage"], "segmented")
        self.assertEqual(manifest["intersegment_device_gap"], "unknown")
        self.assertTrue(self.path.exists())

    def test_zero_data_and_full_data_without_verified_end_stay_partial(self):
        self.spool.append_partial(sa.PartialSegment(1, 7, (), ((0, 7),), "Нет DATA"))
        self.spool.append_partial(sa.PartialSegment(2, 7, (sa.SampleFragment(0, RAW),), (), "Нет END/CRC"))
        archive = sa.finalize(self.spool.finish("error"), self.dest)
        self.assertEqual(archive.read_segment(0), b"")
        self.assertEqual(archive.entries[1].kind, "partial")
        self.assertEqual(archive.entries[1].sample_count, 7)

    def test_empty_stop_and_preflight_nonmutation(self):
        before = self.path.read_bytes()
        self.spool.preflight_capture(capture_bytes())
        self.spool.preflight_partial(sa.PartialSegment(1, 7, (), ((0, 7),), "abort"))
        self.spool.preflight_abort(sa.AbortEvent("abort"))
        self.assertTrue(self.spool.capacity.can_reserve_windows(2))
        self.assertEqual(before, self.path.read_bytes())
        archive = sa.finalize(self.spool.finish("user_stop"), self.dest)
        self.assertEqual(archive.entries, ())
        self.assertEqual(archive.outcome.stop_evidence, "none")

    def test_issuer_exact_object_and_verified_receipt_guard(self):
        full = sa.FullSegment(decode(capture_bytes()), PROVENANCE)
        receipt = self.spool.append_full(full)
        digest = hashlib.sha256(encode(full.capture)).hexdigest()
        args = dict(issuer=self.spool.receipt_issuer, item=full, sha256=digest)
        self.assertTrue(receipt.matches(IDENTITY, 0, **args))
        self.assertIs(receipt.source_capture, full.capture)
        self.assertFalse(receipt.matches(IDENTITY, 1, **args))
        self.assertFalse(receipt.matches(IDENTITY, 0, **dict(args, issuer=object())))
        self.assertFalse(receipt.matches(IDENTITY, 0, **dict(args, item=replace(full))))
        self.assertFalse(replace(receipt).matches(IDENTITY, 0, **args))
        self.assertFalse(sa.CommitReceipt(receipt.identity, receipt.entry, receipt.capacity_after,
                                         receipt.issuer, full).matches(IDENTITY, 0, **args))
        other = sa.SessionSpool.create(self.root / "other", IDENTITY)
        self.addCleanup(other.close)
        self.assertFalse(receipt.matches(IDENTITY, 0, **dict(args, issuer=other.receipt_issuer)))
        report = self.spool.finish("single_complete", "single_release_ack")
        for obj, key in ((receipt, "entry"), (report, "outcome"), (PROVENANCE, "generation")):
            with self.assertRaises(FrozenInstanceError): setattr(obj, key, None)
        self.assertEqual(sa.finalize(report, self.dest).outcome.stop_evidence, "single_release_ack")

    def test_abort_event_does_not_consume_a_wire_sequence(self):
        self.spool.append_capture(capture_bytes(1))
        self.spool.append_abort(sa.AbortEvent("Нет BEGIN", sequence=500))
        self.spool.append_capture(capture_bytes(2))
        self.assertEqual(len(self.spool.finish("error").entries), 3)


class ValidationTests(ArchiveCase):
    def test_identity_and_provenance_types_ranges_unknowns(self):
        cases = dict(generation=[True, 1, 0xFFFFFFFF, -1, "2"], config_id=[False, 0, 0xFFFFFFFF, 1.0],
            acquisition_mode=[True, -1, 3, "0", None], auto_timeout_ms=[True, 0, 99, 60001, None],
            host_elapsed_ms=[True, -1, 1 << 64, 1.0], wire_version=["0.2", 1, ""],
            start_operation=["software_trigger", 8, ""])
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(sa.SessionArchiveError):
                    replace(PROVENANCE, **{field: value})
        for mode in (1, 2):
            sa.SegmentProvenance(acquisition_mode=mode, auto_timeout_ms=0)
            with self.assertRaises(sa.SessionArchiveError):
                sa.SegmentProvenance(acquisition_mode=mode, auto_timeout_ms=100)
        for field, value in (("recording_id", "x"), ("session_id", True), ("origin", "measured"),
                             ("firmware_version", ""), ("protocol_version", 1), ("application_version", "\0")):
            with self.subTest(field=field), self.assertRaises(sa.SessionArchiveError):
                replace(IDENTITY, **{field: value})

    def test_capture_identity_sequence_and_mode_consistency(self):
        for field, value in (("session_id", RECORDING), ("origin", "device"), ("firmware_version", "1"),
                             ("protocol_version", "0.1"), ("application_version", "0.2")):
            with self.subTest(field=field), self.assertRaises(sa.SessionArchiveError):
                self.spool.append_capture(capture_bytes(**{field: value}))
        self.spool.append_capture(capture_bytes(2), PROVENANCE)
        for seq in (1, 2):
            with self.assertRaises(sa.SessionArchiveError): self.spool.append_capture(capture_bytes(seq))
        for mode in (1, 2):
            with self.assertRaises(sa.SessionArchiveError):
                self.spool.append_capture(capture_bytes(3, trigger_kind="auto_timeout", trigger_index=None),
                    sa.SegmentProvenance(acquisition_mode=mode, auto_timeout_ms=0))
        self.spool.append_capture(capture_bytes(4), replace(PROVENANCE, host_elapsed_ms=100))
        with self.assertRaises(sa.SessionArchiveError):
            self.spool.append_capture(capture_bytes(5), replace(PROVENANCE, host_elapsed_ms=99))

    def test_partial_ranges_types_counts_and_sample_codes(self):
        valid = sa.PartialSegment(1, 10, (sa.SampleFragment(1, (1, 2)),), ((0, 1), (3, 10)), "abort")
        cases = dict(sequence=[True, -1, 1 << 32], expected_samples=[0, True, 65537],
            fragments=[[], (sa.SampleFragment(8, (1, 2, 3)),),
                (sa.SampleFragment(1, (1, 2)), sa.SampleFragment(2, (3,))),
                (sa.SampleFragment(3, (1,)), sa.SampleFragment(1, (2,))),
                (sa.SampleFragment(0, (1,)),) * (sa.MAX_FRAGMENTS + 1)],
            missing_ranges=[(), ((False, 1), (3, 10)), ((0, 1), (4, 10)), [[0, 1], [3, 10]]],
            reason=["", "x" * 1025], nominal_fs_hz=[True, float("nan"), float("inf"), 10**400],
            received_at_utc=["yesterday", "2026-10-12T12:00:00+01:00"])
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field), self.assertRaises(sa.SessionArchiveError):
                    replace(valid, **{field: value})
        for samples in ((), [1], (True,), (-1,), (4096,), (1.0,), (1,) * 65537):
            with self.subTest(samples=str(samples)[:30]), self.assertRaises(sa.SessionArchiveError):
                sa.SampleFragment(0, samples)

    def test_outcomes_and_v1_corruption_rejected_before_mutation(self):
        before = self.path.read_bytes()
        for reason, evidence in (("success", "none"), (True, "none"), ("user_stop", True),
            ("user_stop", "single_release_ack"), ("recovered", "stop_ack"), ("single_complete", "single_release_ack")):
            with self.assertRaises(sa.SessionArchiveError): self.spool.finish(reason, evidence)
        with self.assertRaises(sa.SessionArchiveError): self.spool.finish("error", diagnostic="x" * (sa.MAX_TEXT + 1))
        original = capture_bytes()
        for bad in (original[:-1], original + b"0", bytearray(original), b"x" * 147505,
                    original[:-32] + b"x" * 32, capture_bytes(complete=False)):
            with self.assertRaises(CaptureError): self.spool.append_capture(bad)
        self.assertEqual(self.path.read_bytes(), before)

    def test_wire_facts_roundtrip_and_config_nonce_crc_contradictions(self):
        nonce = 17
        identity = replace(IDENTITY, session_id=str(UUID(int=nonce)))
        spool = sa.SessionSpool.create(self.root / "wire.spool", identity)
        self.addCleanup(spool.close)
        original = decode(capture_bytes(session_id=identity.session_id, pretrigger_samples=0, trigger_index=0))
        capture = replace(original, samples=tuple(i % 4096 for i in range(2048)))
        data = encode(capture)
        config = struct.pack("<HBBBBHHHI", 1, 0, 0, 1, 0, 256, 0, 0, 100).hex()
        crc = zlib.crc32(struct.pack("<2048H", *capture.samples)) & 0xffffffff
        provenance = replace(PROVENANCE, wire_session_nonce=nonce, config_payload_hex=config,
            begin_request_id=0, begin_sequence=10, terminal_kind="end", terminal_request_id=0,
            terminal_sequence=12, end_samples_crc32=crc, unavailable_reason=None)
        receipt = spool.append_capture(data, provenance)
        opened = sa.finalize(spool.finish("user_stop", "stop_ack"), self.dest)
        self.assertEqual(opened.entries[0].provenance, provenance)
        self.assertEqual(opened.read_segment(0), data)
        self.assertEqual(receipt.entry.provenance.config_payload_hex, config)
        for field, value in (("wire_session_nonce", True), ("wire_session_nonce", 0),
            ("begin_request_id", 1), ("terminal_request_id", True), ("begin_sequence", 0),
            ("terminal_sequence", 10), ("end_samples_crc32", True), ("terminal_kind", "type8"),
            ("config_payload_hex", config + "00"), ("config_payload_hex", "x" * 32),
            ("unavailable_reason", "not_supplied")):
            with self.subTest(field=field), self.assertRaises(sa.SessionArchiveError):
                replace(provenance, **{field: value})
        for change in (dict(wire_session_nonce=18), dict(end_samples_crc32=crc ^ 1)):
            other = sa.SessionSpool.create(self.root / next(iter(change)), identity)
            self.addCleanup(other.close)
            before = other.path.read_bytes()
            with self.assertRaises(sa.SessionArchiveError): other.append_capture(data, replace(provenance, **change))
            self.assertEqual(other.path.read_bytes(), before)
        with self.assertRaises(sa.SessionArchiveError):
            sa.SegmentProvenance(unavailable_reason=None)
        with self.assertRaises(sa.SessionArchiveError):
            replace(provenance, terminal_kind="abort")

    def test_partial_and_abort_digest_exact_item_receipts(self):
        items = (sa.PartialSegment(1, 7, (), ((0, 7),), "abort"), sa.AbortEvent("no begin"))
        receipts = (self.spool.append_partial(items[0]), self.spool.append_abort(items[1]))
        for n, (item, receipt) in enumerate(zip(items, receipts)):
            self.assertEqual(sa.segment_digest(item), receipt.entry.sha256)
            args = dict(issuer=self.spool.receipt_issuer, item=item, sha256=sa.segment_digest(item))
            self.assertTrue(receipt.matches(IDENTITY, n, **args))
            self.assertFalse(receipt.matches(IDENTITY, n, **dict(args, item=replace(item))))
        with self.assertRaises(sa.SessionArchiveError): sa.segment_digest(object())

    def test_numeric_abort_and_partial_validation_annotations_survive(self):
        provenance = sa.SegmentProvenance(terminal_kind="abort", terminal_request_id=0,
            terminal_sequence=19, abort_reason_code=3, unavailable_reason="BEGIN/context unavailable")
        partial = sa.PartialSegment(1, 7, (), ((0, 7),), "ABORT", provenance,
            deferred_checks=("AUTO_EXTERNAL_EDGE_ID",), metadata_issues=("RAIL_FLAG_MISMATCH",))
        self.spool.append_partial(partial)
        self.spool.append_abort(sa.AbortEvent("ABORT до BEGIN", 2, provenance))
        archive = sa.finalize(self.spool.finish("error"), self.dest)
        self.assertEqual(archive.entries[0].provenance.abort_reason_code, 3)
        self.assertEqual(archive.entries[0].partial.deferred_checks, partial.deferred_checks)
        self.assertEqual(archive.entries[0].partial.metadata_issues, partial.metadata_issues)
        self.assertEqual(json.loads(archive.read_segment(1))["provenance"]["abort_reason_code"], 3)
        for value in (True, 0, 7):
            with self.assertRaises(sa.SessionArchiveError): replace(provenance, abort_reason_code=value)
        for value in ([], ("x",) * 17, ("x" * 129,), (True,)):
            with self.assertRaises(sa.SessionArchiveError): replace(partial, deferred_checks=value)


class CapacityTests(ArchiveCase):
    def test_fixed_caps_and_all_admission_bounds(self):
        self.assertEqual((sa.MAX_TOTAL_BYTES, sa.MAX_SEGMENTS, sa.MAX_EVENTS,
                          sa.MAX_MANIFEST_BYTES, sa.MAX_DIAGNOSTIC_BYTES),
                         (268435456, 32768, 4096, 8388608, 1048576))
        for limit, value, operation in (
            ("MAX_SEGMENTS", 0, lambda: self.spool.append_capture(capture_bytes())),
            ("MAX_EVENTS", 0, lambda: self.spool.append_abort(sa.AbortEvent("abort"))),
            ("MAX_MANIFEST_BYTES", sa.MAX_HEADER_BYTES, lambda: self.spool.append_capture(capture_bytes())),
            ("MAX_TOTAL_BYTES", sa.TERMINAL_RESERVE + self.path.stat().st_size,
             lambda: self.spool.append_capture(capture_bytes()))):
            before = self.path.read_bytes()
            with self.subTest(limit=limit), mock.patch.object(sa, limit, value):
                with self.assertRaises(sa.CapacityError): operation()
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.spool.finish("capacity").outcome.reason, "capacity")

    def test_count_boundary_no_eviction_and_terminal_reserve(self):
        with mock.patch.object(sa, "MAX_SEGMENTS", 2):
            self.spool.append_capture(capture_bytes(1)); self.spool.append_capture(capture_bytes(2))
            before = self.path.read_bytes()
            with self.assertRaises(sa.CapacityError): self.spool.append_capture(capture_bytes(3))
            self.assertFalse(self.spool.capacity.can_reserve_windows())
            self.assertEqual(self.path.read_bytes(), before)
            report = self.spool.finish("capacity")
        self.assertEqual([e.sequence for e in report.entries], [1, 2])

    def test_oversized_sparse_spool_rejected_before_read(self):
        self.spool.close()
        with self.path.open("r+b") as stream: stream.truncate(sa.MAX_TOTAL_BYTES + 1)
        with mock.patch.object(sa, "_read_frame", side_effect=AssertionError("read")):
            with self.assertRaises(sa.SessionArchiveError): sa.recover(self.path)

    def test_capacity_snapshots_reject_wrong_types_ranges_and_bad_reservation(self):
        capacity = self.spool.capacity
        for name in asdict(capacity):
            for value in (True, -1, 1.0, "1", sa.MAX_TOTAL_BYTES + 1):
                with self.subTest(name=name, value=value), self.assertRaises(sa.SessionArchiveError):
                    replace(capacity, **{name: value})
        for count in (0, True, -1, 32769):
            with self.assertRaises(sa.SessionArchiveError): capacity.can_reserve_windows(count)


class RecoveryTests(ArchiveCase):
    def test_every_byte_cut_across_header_members_indexes_and_finish(self):
        header_end = self.path.stat().st_size
        self.spool.append_capture(capture_bytes(1)); first_end = self.path.stat().st_size
        self.spool.append_capture(capture_bytes(2)); second_end = self.path.stat().st_size
        self.spool.finish("user_stop", "stop_ack")
        original = self.path.read_bytes(); trial = self.root / "cut.spool"
        for cut in range(header_end, len(original) + 1):
            trial.write_bytes(original[:cut]); report = sa.recover(trial)
            with self.subTest(cut=cut):
                self.assertEqual(len(report.entries), int(cut >= first_end) + int(cut >= second_end))
                self.assertEqual(trial.read_bytes(), original[:cut])
                if cut == len(original):
                    self.assertEqual(report.outcome.stop_evidence, "stop_ack"); self.assertEqual(report.issues, ())
                else:
                    self.assertEqual(report.outcome, sa.SessionOutcome("recovered", "unknown"))
                    self.assertEqual(len(report.issues), 1)
        for cut in range(header_end):
            trial.write_bytes(original[:cut])
            with self.subTest(header_cut=cut), self.assertRaises(sa.SessionArchiveError): sa.recover(trial)

    def test_corrupt_pair_stops_scan_and_preserves_later_tail_as_unknown(self):
        self.spool.append_capture(capture_bytes(1)); second = self.path.stat().st_size
        self.spool.append_capture(capture_bytes(2)); third = self.path.stat().st_size
        self.spool.append_capture(capture_bytes(3)); self.spool.finish("user_stop", "stop_ack")
        original = self.path.read_bytes()
        for offset in (second, second + 4, second + 8, second + 40, third - 40, third - 1):
            damaged = bytearray(original); damaged[offset] ^= 1; self.path.write_bytes(damaged)
            report = sa.recover(self.path)
            with self.subTest(offset=offset):
                self.assertEqual(len(report.entries), 1)
                self.assertEqual(report.issues[0].offset, second)
                self.assertEqual(report.issues[0].byte_count, len(original) - second)
                self.assertEqual(report.outcome.reason, "recovered")

    def test_recovered_file_cannot_be_upgraded_and_changed_source_is_detected(self):
        self.spool.append_capture(capture_bytes()); self.spool.close()
        before = self.path.read_bytes(); report = sa.recover(self.path)
        opened = sa.finalize(report, self.dest)
        self.assertEqual(opened.outcome, sa.SessionOutcome("recovered", "unknown"))
        self.assertEqual(opened.issues[0].kind, "missing_finish")
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(sa.SessionArchiveError):
            sa.finalize(replace(report, outcome=sa.SessionOutcome("user_stop", "stop_ack")), self.dest, overwrite=True)
        old = self.dest.read_bytes()
        with self.path.open("ab") as stream: stream.write(b"tail")
        with self.assertRaises(sa.SessionArchiveError): sa.finalize(report, self.dest, overwrite=True)
        self.assertEqual(self.dest.read_bytes(), old)

    def test_post_finish_tail_removes_clean_stop_claim(self):
        self.spool.append_capture(capture_bytes()); self.spool.finish("user_stop", "stop_ack")
        with self.path.open("ab") as stream: stream.write(b"extra")
        report = sa.recover(self.path)
        self.assertEqual(len(report.entries), 1); self.assertEqual(report.outcome.stop_evidence, "unknown")

    def test_process_exit_skips_python_cleanup_at_every_write_and_sync_boundary(self):
        code = '''import os,sys
from pathlib import Path
from unittest import mock
from test_session_archive import IDENTITY,capture_bytes
from motortester import session_archive as sa
s=sa.SessionSpool.create(Path(sys.argv[1]),IDENTITY); target=int(sys.argv[2]); count=0
s.append_capture(capture_bytes(1)); write=sa._write_all; sync=sa._sync
def step(fn,*args):
 global count
 fn(*args); count+=1
 if count==target: os._exit(73)
with mock.patch.object(sa,'_write_all',side_effect=lambda *a:step(write,*a)), mock.patch.object(sa,'_sync',side_effect=lambda *a:step(sync,*a)):
 s.append_capture(capture_bytes(2))
os._exit(74)
'''
        env = os.environ.copy(); here = Path(__file__).resolve()
        env["PYTHONPATH"] = os.pathsep.join((str(here.parent), str(here.parents[1])))
        for boundary in range(1, 8):
            path = self.root / f"process-{boundary}"
            result = subprocess.run([sys.executable, "-c", code, str(path), str(boundary)],
                                    env=env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 73, result.stderr)
            report = sa.recover(path)
            self.assertEqual(len(report.entries), 1 if boundary < 6 else 2)
            self.assertEqual(report.outcome.stop_evidence, "unknown")


class FaultTests(ArchiveCase):
    def test_exclusive_create_and_creation_faults(self):
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError): sa.SessionSpool.create(self.path, IDENTITY)
        self.assertEqual(self.path.read_bytes(), before)
        for function in ("_write_all", "_sync"):
            path = self.root / function
            with mock.patch.object(sa, function, side_effect=OSError("ENOSPC")):
                with self.assertRaises(OSError): sa.SessionSpool.create(path, IDENTITY)
            self.assertTrue(path.exists())

    def test_receipt_follows_both_syncs_and_readback(self):
        observations = []; sync = sa._sync; read = sa._read_frame
        def spy(stream): observations.append(len(sa.recover(self.path).entries)); sync(stream)
        with mock.patch.object(sa, "_sync", side_effect=spy), mock.patch.object(sa, "_read_frame", wraps=read) as reads:
            receipt = self.spool.append_capture(capture_bytes())
        self.assertEqual(observations, [0, 1]); self.assertGreaterEqual(reads.call_count, 2)
        self.assertEqual(receipt.entry.sha256, hashlib.sha256(capture_bytes()).hexdigest())

    def test_every_append_write_sync_and_readback_failure_closes_writer(self):
        for function, failures in (("_write_all", range(1, 6)), ("_sync", (1, 2)), ("_read_frame", (1, 2))):
            real = getattr(sa, function)
            for failure in failures:
                spool = sa.SessionSpool.create(self.root / f"{function}-{failure}", IDENTITY)
                spool.append_capture(capture_bytes(1)); calls = 0
                def fault(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == failure: raise OSError("disk failure")
                    return real(*args, **kwargs)
                with mock.patch.object(sa, function, side_effect=fault):
                    with self.assertRaises(OSError): spool.append_capture(capture_bytes(2))
                report = sa.recover(spool.path)
                expected = 2 if function == "_read_frame" or (function == "_sync" and failure == 2) else 1
                self.assertEqual(len(report.entries), expected)
                with self.assertRaises(sa.SessionArchiveError): spool.append_capture(capture_bytes(3))

    def test_short_writes_loop_and_zero_invalid_writes_fail(self):
        class Writer:
            def __init__(self): self.data = bytearray()
            def write(self, view): self.data.extend(view[:2]); return min(2, len(view))
        writer = Writer(); sa._write_all(writer, b"abcdefgh"); self.assertEqual(writer.data, b"abcdefgh")
        for value in (0, None, True, -1, 100):
            with self.assertRaises(OSError): sa._write_all(mock.Mock(write=mock.Mock(return_value=value)), b"abc")

    def test_torn_finish_never_fabricates_stop(self):
        self.spool.append_capture(capture_bytes()); real = sa._write_all; calls = 0
        def fault(stream, data):
            nonlocal calls
            calls += 1
            if calls == 2: real(stream, data[:3]); raise OSError("ENOSPC")
            real(stream, data)
        with mock.patch.object(sa, "_write_all", side_effect=fault):
            with self.assertRaises(OSError): self.spool.finish("user_stop", "stop_ack")
        self.assertEqual(sa.recover(self.path).outcome.stop_evidence, "unknown")

    def test_finalization_faults_cancel_and_no_clobber_preserve_every_source(self):
        report = self.finish(); previous = self.dest.read_bytes(); source = self.path.read_bytes()
        for target in ("_sync", "os.replace", "load", "zipfile.ZipFile.writestr"):
            with self.subTest(target=target), mock.patch("motortester.session_archive." + target,
                                                      side_effect=OSError("save failure")):
                with self.assertRaises(OSError): sa.finalize(report, self.dest, overwrite=True)
            self.assertEqual(self.dest.read_bytes(), previous); self.assertEqual(self.path.read_bytes(), source)
            self.assertEqual(list(self.root.glob(".motortester-session-*")), [])
        for stop in range(1, 7):
            calls = 0
            def cancel():
                nonlocal calls
                calls += 1
                return calls == stop
            with self.assertRaises(sa.SaveCancelled): sa.finalize(report, self.dest, overwrite=True, cancelled=cancel)
            self.assertEqual(self.dest.read_bytes(), previous); self.assertEqual(self.path.read_bytes(), source)
            self.assertEqual(list(self.root.glob(".motortester-session-*")), [])
        with self.assertRaises(FileExistsError): sa.finalize(report, self.dest)
        self.assertEqual(self.dest.read_bytes(), previous)

    def test_publication_is_last_atomic_step_after_validated_complete_archive(self):
        report = self.finish(); self.dest.write_bytes(b"previous"); real = os.replace
        def publish(source, target):
            self.assertEqual(self.dest.read_bytes(), b"previous")
            self.assertEqual(sa.load(source).entries, report.entries)
            real(source, target)
        with mock.patch.object(sa.os, "replace", side_effect=publish):
            result = sa.finalize(report, self.dest, overwrite=True)
        self.assertEqual(sa.load(self.dest), result)
        racepath = self.root / "race"; real_link = os.link
        def race(source, target): Path(target).write_bytes(b"competitor"); real_link(source, target)
        with mock.patch.object(sa.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError): sa.finalize(report, racepath)
        self.assertEqual(racepath.read_bytes(), b"competitor")

    def test_source_alias_open_writer_and_unavailable_directory_are_not_overwritten(self):
        self.spool.append_capture(capture_bytes())
        with self.assertRaises(sa.SessionArchiveError): self.spool.finalize(self.dest)
        report = self.spool.finish("user_stop"); alias = self.root / "alias"; os.link(self.path, alias)
        for target in (self.path, alias):
            with self.assertRaises(sa.SessionArchiveError): sa.finalize(report, target, overwrite=True)
        with self.assertRaises(OSError): sa.finalize(report, self.root / "absent" / "file")

    def test_sync_flush_and_fsync_failures_are_propagated(self):
        stream = mock.Mock()
        stream.flush.side_effect = OSError("flush")
        with mock.patch.object(sa.os, "fsync") as sync:
            with self.assertRaises(OSError): sa._sync(stream)
            sync.assert_not_called()
        stream.flush.side_effect = None
        with mock.patch.object(sa.os, "fsync", side_effect=OSError("fsync")):
            with self.assertRaises(OSError): sa._sync(stream)

    def test_all_finish_write_and_sync_faults_preserve_committed_window(self):
        for function, failures in (("_write_all", (1, 2, 3)), ("_sync", (1,))):
            real = getattr(sa, function)
            for failure in failures:
                spool = sa.SessionSpool.create(self.root / f"finish-{function}-{failure}", IDENTITY)
                spool.append_capture(capture_bytes()); calls = 0
                def fault(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == failure: raise OSError("finish failure")
                    return real(*args, **kwargs)
                with mock.patch.object(sa, function, side_effect=fault):
                    with self.assertRaises(OSError): spool.finish("user_stop", "stop_ack")
                recovered = sa.recover(spool.path)
                self.assertEqual(len(recovered.entries), 1)
                # Fully readable explicit END retains its assertion even if fsync raised;
                # no successful finish result was delivered to the controller.
                self.assertEqual(recovered.outcome.reason, "user_stop" if function == "_sync" else "recovered")
                with self.assertRaises(sa.SessionArchiveError): spool.finish("user_stop")

    def test_process_exit_immediately_before_and_after_publication(self):
        report = self.finish(1); source = self.path.read_bytes()
        code = r'''import os,sys
from unittest import mock
from motortester import session_archive as sa
real=os.replace
before=sys.argv[3]=='before'
def publish(source,target):
 if before: os._exit(73)
 real(source,target); os._exit(73)
with mock.patch.object(sa.os,'replace',side_effect=publish):
 sa.finalize(sa.recover(sys.argv[1]),sys.argv[2],overwrite=True)
'''
        env = os.environ.copy(); env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        for point in ("before", "after"):
            self.dest.write_bytes(b"previous")
            result = subprocess.run([sys.executable, "-c", code, str(self.path), str(self.dest), point],
                                    env=env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 73, result.stderr)
            if point == "before": self.assertEqual(self.dest.read_bytes(), b"previous")
            else: self.assertEqual(sa.load(self.dest).entries, report.entries)
            self.assertEqual(self.path.read_bytes(), source)


class MalformedArchiveTests(ArchiveCase):
    def setUp(self): super().setUp(); self.finish(); self.original = self.dest.read_bytes()
    def restore(self): self.dest.write_bytes(self.original)

    def test_unknown_missing_duplicate_fields_and_invalid_json(self):
        for field in json.loads(self.members()[0][1]):
            self.restore(); self.mutate_manifest(lambda o, f=field: o.pop(f)); self.reject()
        for data in (b'{}', b'[]', b'null', b'\xff', b'\xef\xbb\xbf{}', b'{"x":0,"x":0}', b'[' * 17 + b'0' + b']' * 17):
            self.restore(); self.rewrite(lambda m, d=data: m.__setitem__(0, ("manifest.json", d))); self.reject()
        self.restore(); self.mutate_manifest(lambda o: o.update(extra=1)); self.reject()
        self.restore()
        self.rewrite(lambda m: m.__setitem__(0, ("manifest.json", m[0][1][:-1] + b',"format_version":1}')))
        self.reject()

    def test_versions_counts_hashes_identity_provenance_and_order(self):
        mutations = [lambda o: o.update(format_version=True), lambda o: o.update(format_version=2),
            lambda o: o.update(segment_count=True), lambda o: o.update(segment_count=4),
            lambda o: o.update(segment_count=32769), lambda o: o.update(coverage="continuous"),
            lambda o: o.update(intersegment_device_gap="zero"), lambda o: o["identity"].update(origin="device"),
            lambda o: o["segments"][0].update(sha256="0" * 64), lambda o: o["segments"][0].update(byte_count=True),
            lambda o: o["segments"][0].update(sample_count=6), lambda o: o["segments"][0].update(sequence=True),
            lambda o: o["segments"][0].update(ordinal=1), lambda o: o["segments"][0].update(unknown=1),
            lambda o: o["segments"][0]["provenance"].update(generation=True),
            lambda o: o["segments"][0]["provenance"].update(wire_version="0.2"), lambda o: o["segments"].reverse(),
            lambda o: o["termination"].update(stop_evidence="physical_stop"),
            lambda o: o["termination"].update(reason="single_complete"),
            lambda o: o["diagnostics"].update(byte_count=True), lambda o: o["diagnostics"].update(sha256="a" * 64)]
        for mutate in mutations: self.restore(); self.mutate_manifest(mutate); self.reject()

    def test_duplicate_traversal_unknown_names_directories_links_and_special_files(self):
        for name in ("../evil", "/evil", "C:/evil", "segments\\00000000.mtcap", "segments/../a.mtcap",
                     "segments/00000000.mtcap/", "unknown.json", "manifest.json\0evil"):
            self.restore(); self.rewrite(lambda m, n=name: m.append((n, b"x"))); self.reject()
        self.restore(); self.rewrite(lambda m: m.append(m[1])); self.reject()
        for mode in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO):
            self.restore(); self.rewrite(lambda _: None, lambda i: setattr(i, "external_attr", (mode | 0o600) << 16)); self.reject()

    def test_compression_extras_comments_unsupported_zip_profile(self):
        for field, value in (("compress_type", zipfile.ZIP_DEFLATED), ("extra", b"\0\0\0\0"),
                             ("comment", b"comment"), ("create_system", 0), ("extract_version", 45)):
            self.restore(); self.rewrite(lambda _: None, lambda i: setattr(i, field, value)); self.reject()
        self.restore()
        with zipfile.ZipFile(self.dest, "a") as archive: archive.comment = b"comment"
        self.reject()

    def test_encryption_descriptors_local_header_mismatch_crc_and_bad_adc(self):
        for flag in (1, 8):
            data = bytearray(self.original); cd = data.index(b"PK\x01\x02")
            struct.pack_into("<H", data, 6, flag); struct.pack_into("<H", data, cd + 8, flag)
            self.dest.write_bytes(data); self.reject()
        for offset in (0, 6, 8, 14, 18, 22, 26, 28, 30, 60):
            data = bytearray(self.original); data[offset] ^= 1; self.dest.write_bytes(data); self.reject()
        self.restore()
        def bad_adc(m):
            data = bytearray(m[1][1]); struct.pack_into("<H", data, len(data) - 34, 65535)
            data[-32:] = hashlib.sha256(data[:-32]).digest(); m[1] = (m[1][0], bytes(data))
            obj = json.loads(m[0][1]); obj["segments"][0]["sha256"] = hashlib.sha256(data).hexdigest()
            m[0] = (m[0][0], json_bytes(obj))
        self.rewrite(bad_adc); self.reject()

    def test_truncations_trailing_bytes_and_self_extracting_preamble(self):
        for cut in set(range(80)) | set(range(len(self.original) - 80, len(self.original))):
            self.dest.write_bytes(self.original[:cut]); self.reject()
        for data in (self.original + b"extra", b"preamble" + self.original):
            self.dest.write_bytes(data); self.reject()

    def test_outer_directory_and_member_caps_before_expensive_read(self):
        end = list(struct.unpack("<4s4H2IH", self.original[-22:]))
        for index, value in ((4, 65535), (5, sa.MAX_DIRECTORY_BYTES + 1), (6, 0xFFFFFFFF), (1, 1), (2, 1), (3, 0)):
            changed = end[:]; changed[index] = value
            self.dest.write_bytes(self.original[:-22] + struct.pack("<4s4H2IH", *changed))
            with mock.patch.object(sa.zipfile, "ZipFile", side_effect=AssertionError("directory allocation")): self.reject()
        with self.dest.open("wb") as stream: stream.truncate(sa.MAX_ARCHIVE_BYTES + 1)
        with mock.patch.object(sa.zipfile, "ZipFile", side_effect=AssertionError("directory allocation")): self.reject()
        for field in ("MAX_MANIFEST_BYTES", "MAX_TOTAL_BYTES"):
            self.restore()
            with mock.patch.object(sa, field, 1), mock.patch.object(sa, "_zip_read", side_effect=AssertionError("read")): self.reject()
        self.restore(); self.mutate_manifest(lambda o: o["segments"][0].update(byte_count=147505)); self.reject()

    def test_selected_member_is_bounded_and_revalidated_against_immutable_index(self):
        archive = sa.load(self.dest)
        self.rewrite(lambda m: m.__setitem__(1, (m[1][0], b"bad")))
        with self.assertRaises(sa.SessionArchiveError): archive.read_segment(0)
        for index in (-1, True, 3):
            with self.assertRaises(sa.SessionArchiveError): archive.read_segment(index)

    def test_partial_descriptor_boolean_range_and_adc_corruption_rejected(self):
        spool = sa.SessionSpool.create(self.root / "partial", IDENTITY)
        self.addCleanup(spool.close)
        spool.append_partial(sa.PartialSegment(1, 7, (sa.SampleFragment(1, (2,)),), ((0, 1), (2, 7)), "abort"))
        sa.finalize(spool.finish("error"), self.dest, overwrite=True)
        original = self.dest.read_bytes()
        for mutate in (lambda o: o["segments"][0]["partial"].update(missing_ranges=[[False, 1], [2, 7]]),
                       lambda o: o["segments"][0]["partial"].update(fragments=[[2, 1]]),
                       lambda o: o["segments"][0].update(sample_count=2)):
            self.dest.write_bytes(original); self.mutate_manifest(mutate); self.reject()
        self.dest.write_bytes(original)
        def corrupt(m):
            bad = b"\x00\x10"; m[1] = (m[1][0], bad)
            obj = json.loads(m[0][1]); obj["segments"][0]["sha256"] = hashlib.sha256(bad).hexdigest()
            m[0] = (m[0][0], json_bytes(obj))
        self.rewrite(corrupt); self.reject()

    def test_true_oversized_manifest_rejected_before_member_read(self):
        self.rewrite(lambda m: m.__setitem__(0, ("manifest.json", b" " * (sa.MAX_MANIFEST_BYTES + 1))))
        with mock.patch.object(sa, "_zip_read", side_effect=AssertionError("oversized JSON read")):
            self.reject()

class AllocationGuardTests(ArchiveCase):
    def assert_early_json_rejection(self, data):
        with mock.patch.object(sa.json, "loads", side_effect=AssertionError("generic decoder reached")):
            with self.assertRaises(CaptureError):
                sa._json_load(data, 8 * 1024 * 1024)

    def test_segment_and_object_count_guards_precede_general_decoder(self):
        self.assert_early_json_rejection(b'{"segments":[' + b'0,' * 32768 + b'0]}')
        self.assert_early_json_rejection(json_bytes({f"k{i}": 0 for i in range(33)}))
        self.assertEqual(len(sa._json_load(b'{"segments":[' + b'0,' * 32767 + b'0]}',
                                          8 * 1024 * 1024)["segments"]), 32768)
        obj = {f"k{i}": 0 for i in range(32)}
        self.assertEqual(sa._json_load(json_bytes(obj), 1000), obj)

    def test_each_named_collection_and_pair_has_an_early_independent_bound(self):
        for name, limit in (("fragments", 1024), ("missing_ranges", 1025),
                            ("deferred_checks", 16), ("metadata_issues", 16),
                            ("recovery_issues", 1)):
            value = [0, 1] if name in ("fragments", "missing_ranges") else None
            self.assert_early_json_rejection(json_bytes({name: [value] * (limit + 1)}))
            expected = {name: [value] * limit}
            self.assertEqual(sa._json_load(json_bytes(expected), 65536), expected)
        self.assert_early_json_rejection(b'{"fragments":[[0,1,2]]}')
        self.assert_early_json_rejection(b'{"\\u0066ragments":[' + b'[0,1],' * 1024 + b'[0,1]]}')

    def test_key_string_numeric_and_total_node_budgets_before_decode(self):
        self.assert_early_json_rejection(json_bytes({"k" * 65: 0}))
        self.assert_early_json_rejection(json_bytes({"message": "x" * 65537}))
        self.assert_early_json_rejection(b'0.' + b'0' * 62 + b'1')
        self.assertEqual(sa._json_load(json_bytes({"k" * 64: 0}), 1000), {"k" * 64: 0})
        self.assertEqual(sa._json_load(b'0.' + b'0' * 61 + b'1', 1000), 1e-62)
        # Key strings are not nodes; containers and scalar values each count once.
        with mock.patch.object(sa, "MAX_JSON_NODES", 7, create=True):
            self.assertEqual(sa._json_load(b'[[0,0],[0,0]]', 100), [[0, 0], [0, 0]])
            self.assert_early_json_rejection(b'[[0,0],[0,0,0]]')

    def test_configured_node_budget_boundary_without_general_tree_allocation(self):
        # Keep every array below its own cap while reaching the independent total.
        full = b'[' + b'0,' * 32767 + b'0]'
        remaining = 3 * 1024 * 1024 - 1 - 95 * 32769 - 1
        final = b'[' + b'0,' * (remaining - 1) + b'0]'
        boundary = b'[' + (full + b',') * 95 + final + b']'
        sentinel = object()
        with mock.patch.object(sa.json, "loads", return_value=sentinel) as decoder:
            self.assertIs(sa._json_load(boundary, 8 * 1024 * 1024), sentinel)
            decoder.assert_called_once()
        self.assert_early_json_rejection(boundary[:-2] + b',0]]')

    def test_utf8_escaped_unicode_and_surrogate_pairs_count_as_characters(self):
        for text in ("я" * 65536, "😀" * 65536):
            for ascii_only in (False, True):
                data = json.dumps(text, ensure_ascii=ascii_only).encode("utf-8")
                self.assertEqual(sa._json_load(data, 1024 * 1024), text)
        self.assert_early_json_rejection(json.dumps("😀" * 65537).encode())
        for data in (b'"\\uZZZZ"', b'"\\u123"', b'"\\q"', b'"unterminated',
                     b'"\\"', b'{"x":}', b'[0,]', b'{"x":0,}', b'"\x80\x80"'):
            with self.subTest(data=data), self.assertRaises(CaptureError):
                sa._json_load(data, 1000)

    @staticmethod
    def directory_fixture(actual, declared):
        # Independent valid per-entry ZIP profile with empty payloads. The body is
        # not a session; the sentinel observes resource preflight before semantics.
        locals_, directory = [], []
        offset = 0
        for n in range(actual):
            name = (b"manifest.json" if n == 0 else b"diagnostics.json" if n == actual - 1
                    else f"segments/{n - 1:08d}.mtcap".encode())
            local = struct.pack("<4s5H3I2H", b"PK\x03\x04", 20, 0, 0, 0, 33, 0, 0, 0, len(name), 0) + name
            locals_.append(local)
            directory.append(struct.pack("<4s6H3I5H2I", b"PK\x01\x02", 0x0314, 20, 0, 0,
                                         0, 33, 0, 0, 0, len(name), 0, 0, 0, 0,
                                         0o100600 << 16, offset) + name)
            offset += len(local)
        central = b"".join(directory)
        return b"".join(locals_) + central + struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0,
                                                       declared, declared, len(central), offset, 0)

    def test_actual_directory_count_guard_precedes_zipinfo_allocation(self):
        for actual, declared in ((3, 2), (32771, 32770)):
            self.dest.write_bytes(self.directory_fixture(actual, declared))
            with mock.patch.object(sa.zipfile, "ZipFile", side_effect=AssertionError("ZipInfo allocation")):
                self.reject()
        self.dest.write_bytes(self.directory_fixture(32770, 32770))
        with mock.patch.object(sa.zipfile, "ZipFile", side_effect=RuntimeError("boundary reached")) as decoder:
            with self.assertRaises(sa.SessionArchiveError): sa.load(self.dest)
            decoder.assert_called_once()

    def test_directory_names_and_features_are_checked_before_generic_decoder(self):
        self.finish(1)
        original = self.dest.read_bytes()
        for change in (lambda raw, at: raw.__setitem__(slice(at + 46, at + 47), b"!"),
                       lambda raw, at: struct.pack_into("<H", raw, at + 8, 1),
                       lambda raw, at: struct.pack_into("<H", raw, at + 28, 33)):
            raw = bytearray(original); at = raw.index(b"PK\x01\x02"); change(raw, at)
            self.dest.write_bytes(raw)
            with mock.patch.object(sa.zipfile, "ZipFile", side_effect=AssertionError("ZipInfo allocation")):
                self.reject()

    def test_nonascii_and_noncanonical_diagnostic_hashes_are_controlled_errors(self):
        self.finish(1); original = self.dest.read_bytes()
        for value in ("я" * 64, "A" * 64, "0" * 63, "g" * 64, "0" * 64 + "\n", None, True):
            self.dest.write_bytes(original)
            self.mutate_manifest(lambda obj: obj["diagnostics"].update(sha256=value))
            self.reject()

    def test_unissued_malformed_receipts_do_not_serialize_any_fields(self):
        for fields in ((None, None, None), ([], {}, "capacity"),
                       (IDENTITY, sa.SegmentCommit(0, "capture", 0, "x", None, None, None), None)):
            receipt = sa.CommitReceipt(*fields, object())
            with mock.patch.object(sa, "_header", side_effect=AssertionError("unsafe serialization")), \
                 mock.patch.object(sa, "_entry_dict", side_effect=AssertionError("unsafe serialization")):
                self.assertFalse(receipt.matches(None, 0, issuer=object(), item=None, sha256="0" * 64))

    def test_maximum_supported_window_fragment_and_diagnostic_values_roundtrip(self):
        capture = replace(decode(capture_bytes()), samples=(4095,) * 65536)
        self.spool.append_full(sa.FullSegment(capture))
        fragments = tuple(sa.SampleFragment(1 + 63 * i, (i % 4096,)) for i in range(1024))
        missing = [(0, 1)]
        missing.extend((fragments[i].offset + 1, fragments[i + 1].offset) for i in range(1023))
        missing.append((fragments[-1].offset + 1, 65536))
        partial = sa.PartialSegment(2, 65536, fragments, tuple(missing), "x" * 1024,
                                   deferred_checks=("d" * 128,) * 16,
                                   metadata_issues=("m" * 128,) * 16)
        self.spool.append_partial(partial)
        message = "😀" * 65536
        archive = sa.finalize(self.spool.finish("error", diagnostic=message), self.dest)
        self.assertEqual(decode(archive.read_segment(0)), capture)
        self.assertEqual(archive.entries[1].partial.missing_ranges, tuple(missing))
        self.assertEqual(archive.read_segment(1), b"".join(i.to_bytes(2, "little") for i in range(1024)))
        self.assertEqual(archive.outcome.diagnostic, message)

    def test_dense_valid_shape_near_manifest_byte_cap_fits_node_budget(self):
        # The densest supported partial shape uses adjacent one-sample fragments.
        # Exercise the entire near-8-MiB lexical pass without allocating the general
        # decoder's nearly 3-million-node Python tree in this unit test.
        partial = sa.PartialSegment(1, 1024,
            tuple(sa.SampleFragment(i, (1,)) for i in range(1024)), (), "x",
            provenance=sa.SegmentProvenance(unavailable_reason="x"))
        entry = sa.SegmentCommit(0, "partial", 2048, "0" * 64, 1, 1024,
                                 partial.provenance, partial.details())
        record = json_bytes(asdict(entry))
        count = (8 * 1024 * 1024 - 4096) // (len(record) + 9)
        records = [record.replace(b'"ordinal":0', f'"ordinal":{n}'.encode()).replace(
                   b'"sequence":1', f'"sequence":{n + 1}'.encode()) for n in range(count)]
        data = b'{"segments":[' + b','.join(records) + b']}'
        self.assertGreater(len(data), 8 * 1024 * 1024 - 20000)
        self.assertLessEqual(len(data), 8 * 1024 * 1024)
        sentinel = object()
        with mock.patch.object(sa.json, "loads", return_value=sentinel) as decoder:
            self.assertIs(sa._json_load(data, 8 * 1024 * 1024), sentinel)
            decoder.assert_called_once()


if __name__ == "__main__": unittest.main()

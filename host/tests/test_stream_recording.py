"""Synthetic indexed-storage controls; no hardware, transport, GUI or timing proof."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest import mock

from motortester.stream_model import (FailureEvidence, StreamBlock, StreamError,
    StreamIdentity, StreamStart, StreamTerminal, StreamPreparation,
    PreparedReadyToken, UnboundOutcome, U64_MAX)
from motortester import stream_recording as recording
from motortester.stream_recording import (StreamCapacityError, StreamLimits, StreamWriter, recover,
    PreparedStreamWriter, recover_attempt)

RAW = b"\x00\x00\x01\x00\xff\x00\x00\x01\xff\x0f\x00\x10\xff\xff"
CODES = (0, 1, 255, 256, 4095, 4096, 65535)


def identity(**changes):
    values = dict(session_id="source-s1", generation=4, config_id=9,
                  nominal_fs_hz=50000, origin="synthetic", protocol_revision=None,
                  firmware_version=None, application_version="test")
    values.update(changes)
    return StreamIdentity(**values)


def start(first=0):
    return StreamStart(identity(), first, "local-recording-r1", "caller_selected_boundary")


def terminal(end, **changes):
    values = dict(identity=identity(), end_sample_index=end, reason="stopped",
                  terminal_observed=True, device_generated_end_sample_index=end,
                  device_transmitted_end_sample_index=end, loss_samples=0,
                  boundary_evidence="synthetic_test_terminal", stop_requested=True,
                  stop_request_id=17)
    values.update(changes)
    return StreamTerminal(**values)


def header(*, first=100, limits=None):
    # Independent explicit schema, not encoded through asdict/model/writer.
    obj = {"start": {"identity": {"session_id": "source-s1", "generation": 4,
        "config_id": 9, "nominal_fs_hz": 50000, "origin": "synthetic",
        "protocol_revision": None, "firmware_version": None, "application_version": "test"},
        "first_sample_index": first, "recording_id": "local-recording-r1",
        "boundary_evidence": "caller_selected_boundary"},
        "limits": limits or {"max_raw_bytes": 67108864, "max_blocks": 65536}}
    return header_bytes(json.dumps(obj, separators=(",", ":")).encode())


def header_bytes(data, magic=b"MTSTRM\x01\x00"):
    body = struct.pack("<8sI", magic, len(data)) + data
    return body + hashlib.sha256(body).digest()


def frame(previous, kind, payload):
    head = struct.pack("<cI", kind, len(payload))
    digest = hashlib.sha256(previous[-32:] + head + payload).digest()
    return head + payload + digest


def fixed_fixture():
    head = header()
    block = frame(head, b"B", struct.pack("<QI", 100, 7) + RAW)
    checkpoint = frame(block, b"C", struct.pack("<QQ", 107, 1))
    return head, block, checkpoint


def stat_fields(info, **changes):
    fields = {name: getattr(info, name) for name in
              ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
    fields["st_file_attributes"] = getattr(info, "st_file_attributes", 0)
    fields.update(changes)
    return SimpleNamespace(**fields)


def changed_lstat(target, kind="identity", active=lambda: True):
    """Inject an OS observation, leaving every production path guard intact."""
    real = Path.lstat
    def lstat(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if path != target or not active():
            return info
        if kind == "identity":
            return stat_fields(info, st_ino=info.st_ino + 1)
        if kind == "reparse":
            return stat_fields(info, st_file_attributes=getattr(info, "st_file_attributes", 0) | 0x400)
        raise AssertionError("Unknown observation fault")
    return lstat


def after_read(opener, change):
    class Reader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def __getattr__(self, name): return getattr(self.stream, name)
        def read(self, count):
            data = self.stream.read(count)
            change()
            return data
    return lambda path: Reader(opener(path))


def native_mutation_result(test, error, *, windows_error, operation):
    """Windows prevention is fixture evidence, never evidence of a path guard."""
    if os.name == "nt":
        test.assertIsInstance(error, PermissionError)
        test.assertEqual(error.winerror, windows_error)
    else:
        test.assertIsInstance(error, StreamError)
    print("native_mutation_outcome " + json.dumps(dict(operation=operation,
        outcome="windows_prevented_fixture" if os.name == "nt" else "mutation_guard_rejected",
        winerror=windows_error if os.name == "nt" else None,
        guard_exercised=os.name != "nt"), sort_keys=True), flush=True)


@contextmanager
def unchanged_metadata(saved):
    """Model equal timestamps without changing bytes, identity, size, or guards."""
    real_lstat, real_fstat = Path.lstat, os.fstat
    def lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        return (stat_fields(info, st_mtime_ns=saved._path_fingerprint[3],
                            st_ctime_ns=saved._path_fingerprint[4])
                if path == saved.path else info)
    def fstat(fd):
        info = real_fstat(fd)
        return (stat_fields(info, st_mtime_ns=saved._fingerprint[3],
                            st_ctime_ns=saved._fingerprint[4])
                if (info.st_dev, info.st_ino) == saved._fingerprint[:2] else info)
    with mock.patch.object(Path, "lstat", lstat), mock.patch.object(os, "fstat", fstat):
        yield


class ModelTests(unittest.TestCase):
    def test_immutable_exact_raw_and_u64_bounds(self):
        block = StreamBlock(identity(), 2**53 + 3, RAW)
        self.assertEqual(block.sample_count, 7)
        self.assertEqual(block.end_sample_index, 2**53 + 10)
        with self.assertRaises(FrozenInstanceError):
            block.first_sample_index = 0
        self.assertEqual(StreamBlock(identity(), U64_MAX - 1, b"\xff\xff").end_sample_index, U64_MAX)
        for first in (True, -1, 1.0, U64_MAX, U64_MAX + 1):
            with self.subTest(first=first), self.assertRaises(StreamError):
                StreamBlock(identity(), first, RAW)
        for raw in (bytearray(RAW), memoryview(RAW), [0], b"", b"x", b"x" * 16386):
            with self.subTest(raw=type(raw)), self.assertRaises(StreamError):
                StreamBlock(identity(), 0, raw)
        self.assertEqual(StreamBlock(identity(), 0, b"x" * 16384).sample_count, 8192)

    def test_identity_start_limits_and_evidence_types(self):
        for field, values in {"generation": [True, -1, 1.0, 2**64],
            "config_id": [False, -1], "nominal_fs_hz": [True, 0, 1.0, 1000001],
            "session_id": ["", "x" * 129, "a\n", "\ud800"],
            "origin": ["simulator", True], "protocol_revision": ["x" * 65]}.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(StreamError):
                    identity(**{field: value})
        for args in ((-1, 1), (True, 1), (3, 1), (2, -1), (2, True), (67108866, 1), (2, 65537)):
            with self.assertRaises(StreamError):
                StreamLimits(*args)
        for code in ("unbounded_future_code", True):
            with self.assertRaises(StreamError):
                FailureEvidence(code, "detail")
        for detail in ("", "secret\npath", "x" * 257):
            with self.assertRaises(StreamError):
                FailureEvidence("source_error", detail)
        with self.assertRaises(StreamError):
            start(True)
        with self.assertRaises(StreamError):
            StreamStart(identity(), 0, "")

    def test_terminal_boundaries_unknown_and_bool(self):
        self.assertTrue(terminal(9).clean_stop_reported)
        for values in (dict(terminal_observed=False), dict(loss_samples=None),
            dict(device_generated_end_sample_index=None), dict(device_transmitted_end_sample_index=None),
            dict(stop_requested=False, stop_request_id=None), dict(loss_samples=1),
            dict(device_generated_end_sample_index=10)):
            self.assertFalse(terminal(9, **values).clean_stop_reported)
        for values in (dict(end_sample_index=True), dict(terminal_observed=1),
            dict(loss_samples=True), dict(device_generated_end_sample_index=8),
            dict(device_transmitted_end_sample_index=10), dict(stop_requested=False),
            dict(reason="source_failure"), dict(failure=FailureEvidence("source_error", "a"))):
            with self.subTest(values=values), self.assertRaises(StreamError):
                terminal(9, **values)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.path = self.directory / "stream.mtstream"

    def tearDown(self):
        self.temp.cleanup()

    def writer(self, first=0, limits=StreamLimits()):
        writer = StreamWriter.create(self.path, start(first), limits)
        self.addCleanup(writer.close)
        return writer

    def test_independent_fixed_vector_and_partitioned_reads(self):
        self.path.write_bytes(b"".join(fixed_fixture()))
        saved = recover(self.path)
        self.assertEqual(saved.identity, identity())
        self.assertEqual(saved.start.recording_id, "local-recording-r1")
        self.assertEqual((saved.first_sample_index, saved.end_sample_index), (100, 107))
        self.assertEqual(saved.read_raw(100, 7), RAW)
        self.assertEqual(saved.read_samples(100, 7), CODES)
        self.assertEqual(saved.recovery_issue, "missing_terminal")
        self.assertFalse(saved.clean_stop_reported)
        self.assertEqual(b"".join(b.raw_u16le for b in saved.iter_raw(chunk_samples=2)), RAW)

    def test_fresh_process_roundtrip_and_first_cause(self):
        writer = self.writer(100)
        writer.append(StreamBlock(identity(), 100, RAW))
        failure = FailureEvidence("ambiguous_dma", "unknown source loss", "stop", 31, 7)
        end = terminal(107, reason="source_failure", terminal_observed=False,
                       device_generated_end_sample_index=None,
                       device_transmitted_end_sample_index=None, loss_samples=None,
                       failure=failure)
        writer.finish(end)
        saved = recover(self.path)
        self.assertEqual(saved.terminal, end)
        self.assertEqual(saved.terminal.failure, failure)
        self.assertIsNone(saved.terminal.loss_samples)
        code = ("import json,sys; from motortester.stream_recording import recover; "
                "r=recover(sys.argv[1]); print(json.dumps([r.first_sample_index,r.end_sample_index,"
                "r.read_raw(100,7).hex(),r.terminal.failure.code,r.terminal.loss_samples,r.clean_stop_reported]))")
        result = subprocess.run([sys.executable, "-O", "-c", code, str(self.path)],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), [100, 107, RAW.hex(), "ambiguous_dma", None, False])

    def test_empty_and_high_index_intervals(self):
        for first, raw in ((0, b""), (U64_MAX, b""), (U64_MAX - 7, RAW), (2**53 + 1, RAW)):
            path = self.directory / str(first)
            writer = StreamWriter.create(path, start(first))
            if raw:
                writer.append(StreamBlock(identity(), first, raw))
            writer.finish(terminal(first + len(raw) // 2))
            saved = recover(path)
            self.assertTrue(saved.clean_stop_reported)
            self.assertEqual(saved.read_raw(first, len(raw) // 2), raw)
            self.assertEqual(saved.read_raw(saved.end_sample_index, 0), b"")

    def test_input_partition_does_not_change_raw_or_terminal(self):
        data = bytes(range(256)) * 1000
        expected_end = len(data) // 2 + 123
        for stride in (2, 256, 16384):
            path = self.directory / f"p{stride}"
            writer = StreamWriter.create(path, start(123))
            for position in range(0, len(data), stride):
                writer.append(StreamBlock(identity(), 123 + position // 2, data[position:position + stride]))
            writer.finish(terminal(expected_end))
            saved = recover(path)
            self.assertEqual(len(saved._entries), (len(data) + 16383) // 16384)
            self.assertEqual(b"".join(b.raw_u16le for b in saved.iter_raw()), data)
            self.assertEqual(saved.terminal, terminal(expected_end))

    def test_coalescing_fsync_count_and_crash_tail(self):
        with mock.patch.object(recording.os, "fsync", wraps=os.fsync) as sync:
            writer = self.writer()
            for first in range(0, 8192 * 64, 128):
                writer.append(StreamBlock(identity(), first, b"\xff\xff" * 128))
            self.assertEqual(sync.call_count, 2)  # header + 1MiB checkpoint, not 4096 fsyncs
            self.assertEqual(writer.checkpointed_end_sample_index, 8192 * 64)
            writer.append(StreamBlock(identity(), 8192 * 64, RAW))
            self.assertEqual(writer.accepted_end_sample_index, 8192 * 64 + 7)
            writer.close()
        saved = recover(self.path)
        self.assertEqual(saved.end_sample_index, 8192 * 64)
        self.assertEqual(len(saved._entries), 64)
        self.assertEqual(saved.recovery_issue, "missing_terminal")  # RAM-only tail leaves no bytes
        self.assertIsNone(saved.terminal)

    def test_explicit_checkpoint_and_noop_checkpoint(self):
        writer = self.writer(50)
        before = self.path.stat().st_size
        for _ in range(10):
            writer.checkpoint()
        self.assertEqual(self.path.stat().st_size, before)
        writer.append(StreamBlock(identity(), 50, RAW))
        self.assertEqual(writer.checkpointed_end_sample_index, 50)
        writer.checkpoint()
        after = self.path.stat().st_size
        writer.checkpoint()
        self.assertEqual(self.path.stat().st_size, after)
        self.assertEqual(writer.checkpointed_end_sample_index, 57)
        writer.close()
        self.assertEqual(recover(self.path).read_raw(50, 7), RAW)

    def test_rejection_fences_identity_order_duplicates_and_invalid_types(self):
        for name, bad in (("gap", StreamBlock(identity(), 8, RAW)),
            ("overlap", StreamBlock(identity(), 6, RAW)), ("duplicate", StreamBlock(identity(), 0, RAW)),
            ("identity", StreamBlock(identity(config_id=10), 7, RAW)), ("type", RAW)):
            path = self.directory / name
            writer = StreamWriter.create(path, start())
            writer.append(StreamBlock(identity(), 0, RAW))
            with self.assertRaises(StreamError):
                writer.append(bad)
            self.assertTrue(writer.closed)
            for later in (lambda: writer.append(StreamBlock(identity(), 7, RAW)),
                          lambda: writer.finish(terminal(7)), writer.checkpoint):
                with self.assertRaises(StreamError):
                    later()
            saved = recover(path)
            self.assertEqual(saved.read_raw(0, 7), RAW)
            self.assertEqual(saved.terminal.reason, "host_failure")
            self.assertFalse(saved.terminal.terminal_observed)
            self.assertIsNone(saved.terminal.loss_samples)
            self.assertEqual(saved.terminal.failure.code,
                             "input_identity" if name == "identity" else "invalid_input" if name == "type" else "input_order")

    def test_quota_and_terminal_reserve(self):
        for name, limits, cause in (("raw", StreamLimits(14, 3), "capacity_raw"),
                                    ("blocks", StreamLimits(100, 1), "capacity_blocks")):
            path = self.directory / name
            writer = StreamWriter.create(path, start(), limits)
            writer.append(StreamBlock(identity(), 0, RAW))
            writer.checkpoint()
            with self.assertRaises(StreamCapacityError):
                writer.append(StreamBlock(identity(), 7, b"\x00\x00"))
            saved = recover(path)
            self.assertEqual(saved.limits, limits)
            self.assertEqual(saved.read_raw(0, 7), RAW)
            self.assertEqual(saved.terminal.failure.code, cause)
            self.assertEqual(saved.terminal.reason, "capacity")
            self.assertLess(self.path.stat().st_size if self.path.exists() else path.stat().st_size,
                            recording.MAX_FILE_BYTES)
        writer = self.writer(limits=StreamLimits(0, 0))
        with self.assertRaises(StreamCapacityError):
            writer.append(StreamBlock(identity(), 0, RAW))
        self.assertEqual(recover(self.path).end_sample_index, 0)

    def test_actual_hard_raw_bound_and_default_frame_count(self):
        writer = self.writer()
        block = b"\x00\xff" * 8192
        for first in range(0, recording.MAX_RAW_BYTES // 2, 8192):
            writer.append(StreamBlock(identity(), first, block))
        self.assertEqual(writer._blocks, 4096)
        with self.assertRaises(StreamCapacityError):
            writer.append(StreamBlock(identity(), recording.MAX_RAW_BYTES // 2, b"\x00\x00"))
        saved = recover(self.path)
        self.assertEqual(saved.end_sample_index, recording.MAX_RAW_BYTES // 2)
        self.assertEqual(saved.terminal.reason, "capacity")
        self.assertEqual(saved.read_samples(saved.end_sample_index - 2, 2), (65280, 65280))
        self.assertLess(self.path.stat().st_size, recording.MAX_FILE_BYTES)

    def test_invalid_terminal_fences_without_losing_prefix(self):
        writer = self.writer()
        writer.append(StreamBlock(identity(), 0, RAW))
        with self.assertRaises(StreamError):
            writer.finish(terminal(8))
        saved = recover(self.path)
        self.assertEqual(saved.end_sample_index, 7)
        self.assertEqual(saved.terminal.failure.code, "invalid_terminal")

    def test_bounded_lazy_ranges_and_no_padding(self):
        writer = self.writer(999)
        data = bytes(range(256)) * 1600
        for offset in range(0, len(data), 16384):
            writer.append(StreamBlock(identity(), 999 + offset // 2, data[offset:offset + 16384]))
        writer.finish(terminal(999 + len(data) // 2))
        saved = recover(self.path)
        rng = random.Random(9)
        for _ in range(30):
            first = rng.randrange(0, len(data) // 2 - 1000)
            self.assertEqual(saved.read_raw(999 + first, 1000), data[first * 2:(first + 1000) * 2])
        for first, count in ((998, 1), (999, 65537), (True, 1), (999, True), (999, 1.0),
                             (999, -1), (saved.end_sample_index, 1)):
            with self.assertRaises(StreamError):
                saved.read_raw(first, count)
        with self.assertRaises(StreamError):
            list(saved.iter_raw(chunk_samples=8193))
        self.assertEqual(len(saved.read_raw(999, 65536)), 131072)

    def test_fast_tail_mutation_observations_and_fresh_recovery(self):
        writer = self.writer()
        writer.append(StreamBlock(identity(), 0, RAW))
        writer.finish(terminal(7))
        saved = recover(self.path)
        original = self.path.read_bytes()
        # Preserve the original fast same-size terminal-digest mutation. Its
        # timestamps need not advance; this does not corrupt the requested B.
        self.path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        observations = []
        handle_reads, path_reads = [], []
        real_fstat, real_lstat = os.fstat, Path.lstat
        def fstat(fd):
            info = real_fstat(fd)
            handle_reads.append(recording._path_fingerprint(info))
            return info
        def lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path == self.path:
                path_reads.append(recording._path_fingerprint(info))
            return info
        real_observe = recording._observe
        def observe(stream, path):
            value = real_observe(stream, path)
            observations.append(value)
            return value
        rejected = False
        with mock.patch.object(recording, "_observe", observe), \
                mock.patch.object(os, "fstat", fstat), mock.patch.object(Path, "lstat", lstat):
            try:
                self.assertEqual(saved.read_raw(0, 1), RAW[:2])
            except StreamError:
                rejected = True
        expected = (saved._fingerprint, saved._path_fingerprint, saved._ancestor_fingerprint)
        if not rejected:
            self.assertTrue(observations)
            self.assertTrue(all(value == expected for value in observations))
        fresh = recover(self.path)
        self.assertEqual(fresh.recovery_issue, "invalid_tail")
        self.assertIsNone(fresh.terminal)
        self.assertFalse(fresh.clean_stop_reported)
        print("fast_tail_mutation_probe " + json.dumps(dict(
            saved_handle=saved._fingerprint, saved_path=saved._path_fingerprint,
            handle_reads=handle_reads, path_reads=path_reads,
            read_observations=[dict(handle=value[0], path=value[1]) for value in observations],
            original_sha256=hashlib.sha256(original).hexdigest(),
            mutated_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
            lazy_rejected=rejected, fresh_recovery_issue=fresh.recovery_issue,
            fresh_end=fresh.end_sample_index, fresh_clean_stop=fresh.clean_stop_reported),
            sort_keys=True), flush=True)

    def test_lazy_same_metadata_selected_block_corruption(self):
        writer = self.writer()
        writer.append(StreamBlock(identity(), 0, RAW))
        writer.finish(terminal(7))
        saved = recover(self.path)
        data = bytearray(self.path.read_bytes())
        data[saved._entries[0].offset + recording._FRAME.size + recording._BLOCK.size] ^= 1
        self.path.write_bytes(data)
        with unchanged_metadata(saved), self.assertRaisesRegex(StreamError, "целостность блока"):
            saved.read_raw(0, 1)

    def test_lazy_same_metadata_terminal_tail_keeps_recovery_snapshot(self):
        writer = self.writer()
        writer.append(StreamBlock(identity(), 0, RAW))
        writer.finish(terminal(7))
        saved = recover(self.path)
        data = self.path.read_bytes()
        self.path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        # Lazy access rehashes selected blocks, not a fresh whole-file recovery.
        with unchanged_metadata(saved):
            self.assertEqual(saved.read_raw(0, 1), RAW[:2])
        self.assertTrue(saved.clean_stop_reported)  # The earlier recovery snapshot.
        fresh = recover(self.path)
        self.assertEqual(fresh.recovery_issue, "invalid_tail")
        self.assertFalse(fresh.clean_stop_reported)

    def test_lazy_replacement_and_midread_mutation(self):
        writer = self.writer()
        writer.append(StreamBlock(identity(), 0, RAW))
        writer.finish(terminal(7))
        original = self.path.read_bytes()
        saved = recover(self.path)
        replacement = self.directory / "replacement"
        replacement.write_bytes(original)
        os.replace(replacement, self.path)
        with self.assertRaises(StreamError):
            saved.read_raw(0, 1)
        saved = recover(self.path)
        real = recording._regular
        calls = 0
        def changed(stream):
            nonlocal calls
            calls += 1
            value = real(stream)
            return value if calls < 3 else (*value[:-1], value[-1] + 1)
        with mock.patch.object(recording, "_regular", side_effect=changed), self.assertRaises(StreamError):
            saved.read_raw(0, 1)

    def test_no_clobber_symlink_special_and_parent_checks(self):
        self.path.write_bytes(b"old")
        with self.assertRaises(FileExistsError):
            self.writer()
        self.assertEqual(self.path.read_bytes(), b"old")
        self.path.unlink()
        alias = self.directory / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(StreamError):
            StreamWriter.create(alias / "new", start())
        self.assertFalse((self.directory / "new").exists())
        source = self.directory / "source"
        source.write_bytes(b"".join(fixed_fixture()))
        self.path.symlink_to(source)
        with self.assertRaises((StreamError, OSError)):
            recover(self.path)
        self.path.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.path)
            with self.assertRaises(StreamError):
                recover(self.path)

    def test_file_size_limit_checked_before_read(self):
        with open(self.path, "wb") as stream:
            stream.truncate(recording.MAX_FILE_BYTES + 1)
        with self.assertRaises(StreamError):
            recover(self.path)

    def test_corruption_before_and_after_checkpoint(self):
        head, block, checkpoint = fixed_fixture()
        for payload in (block[:-1], block[:-1] + b"x", block + checkpoint[:-1],
                        frame(head, b"B", struct.pack("<QI", 101, 7) + RAW),
                        frame(head, b"B", struct.pack("<QI", 100, 8) + RAW),
                        block + frame(block, b"C", struct.pack("<QQ", 108, 1)),
                        block + frame(block, b"C", struct.pack("<QQ", 107, 2))):
            self.path.write_bytes(head + payload)
            saved = recover(self.path)
            self.assertEqual(saved.end_sample_index, 100)
            self.assertFalse(saved.clean_stop_reported)
            self.assertEqual(saved.recovery_issue, "invalid_tail")
        for tail in (b"garbage", frame(checkpoint, b"B", struct.pack("<QI", 107, 7) + RAW),
                     struct.pack("<cI", b"B", 0xFFFFFFFF), frame(checkpoint, b"X", b"x")):
            self.path.write_bytes(head + block + checkpoint + tail)
            saved = recover(self.path)
            self.assertEqual(saved.end_sample_index, 107)
            self.assertEqual(saved.read_raw(100, 7), RAW)
            self.assertEqual(saved.discarded_tail_bytes, len(tail))

    def test_header_wrong_version_unsafe_schema_and_duplicate_fields(self):
        for data in (b"[]", b"null", b'{"start":NaN}', b"[" * 2000 + b"]" * 2000,
                     b'{"start":1,"start":2}', b"\xff", b'{"start":{},"limits":{}}'):
            self.path.write_bytes(header_bytes(data))
            with self.assertRaises(StreamError):
                recover(self.path)
        self.path.write_bytes(header_bytes(b"{}", magic=b"MTSTRM\x02\x00"))
        with self.assertRaises(StreamError):
            recover(self.path)
        self.path.write_bytes(struct.pack("<8sI", b"MTSTRM\x01\x00", 0xFFFFFFFF))
        with self.assertRaises(StreamError):
            recover(self.path)

    def test_torn_at_every_byte_small_checkpoint_fixture(self):
        head, block, checkpoint = fixed_fixture()
        whole = head + block + checkpoint
        for cut in range(len(whole)):
            self.path.write_bytes(whole[:cut])
            if cut < len(head):
                with self.assertRaises(StreamError):
                    recover(self.path)
            else:
                saved = recover(self.path)
                self.assertEqual(saved.end_sample_index, 100)
                self.assertFalse(saved.clean_stop_reported)
        self.path.write_bytes(whole)
        self.assertEqual(recover(self.path).end_sample_index, 107)

    def test_torn_terminal_and_trailing_bytes_never_claim_clean_stop(self):
        writer = self.writer(100)
        writer.append(StreamBlock(identity(), 100, RAW))
        writer.checkpoint()
        boundary = self.path.stat().st_size
        writer.append(StreamBlock(identity(), 107, RAW))
        writer.finish(terminal(114))
        data = self.path.read_bytes()
        for cut in (boundary, boundary + 1, boundary + 18, len(data) - 32, len(data) - 1):
            self.path.write_bytes(data[:cut])
            saved = recover(self.path)
            self.assertEqual(saved.end_sample_index, 107)
            self.assertFalse(saved.clean_stop_reported)
        self.path.write_bytes(data + b"x")
        saved = recover(self.path)
        self.assertEqual(saved.end_sample_index, 107)
        self.assertEqual(saved.recovery_issue, "invalid_tail")
        self.path.write_bytes(data)
        self.assertEqual(recover(self.path).end_sample_index, 114)
        self.assertTrue(recover(self.path).clean_stop_reported)

    def test_short_zero_and_failed_writes(self):
        class Short:
            def __init__(self): self.data = bytearray()
            def write(self, data):
                self.data.extend(data[:3]); return min(3, len(data))
        stream = Short()
        recording._write_all(stream, RAW)
        self.assertEqual(bytes(stream.data), RAW)
        for count in (0, None, True, -1, 10000):
            stream = mock.Mock()
            stream.write.return_value = count
            with self.assertRaises(OSError):
                recording._write_all(stream, RAW)
        writer = self.writer()
        writer.append(StreamBlock(identity(), 0, RAW))
        writer.checkpoint()
        original = recording._write_all
        def torn(stream, data):
            original(stream, data[:10])
            raise OSError("synthetic disk full")
        with mock.patch.object(recording, "_write_all", side_effect=torn):
            with self.assertRaises(OSError):
                writer.append(StreamBlock(identity(), 7, b"\x00\x00" * 8192))
        self.assertTrue(writer.closed)
        self.assertEqual(writer.first_failure.code, "host_io")
        self.assertEqual(recover(self.path).end_sample_index, 7)

    def test_failed_checkpoint_fsync_does_not_update_inprocess_receipt(self):
        writer = self.writer()
        writer.append(StreamBlock(identity(), 0, RAW))
        with mock.patch.object(recording.os, "fsync", side_effect=OSError("synthetic sync failure")):
            with self.assertRaises(OSError):
                writer.checkpoint()
        self.assertTrue(writer.closed)
        self.assertEqual(writer.checkpointed_end_sample_index, 0)
        # OS-visible complete frame can recover, but cannot certify fsync returned.
        saved = recover(self.path)
        self.assertEqual(saved.end_sample_index, 7)
        self.assertFalse(saved.clean_stop_reported)

    def test_finish_fsync_failure_retains_source_failure_if_bytes_visible(self):
        writer = self.writer()
        writer.append(StreamBlock(identity(), 0, RAW))
        fault = FailureEvidence("source_overrun", "first source failure", "receive", 10)
        end = terminal(7, reason="source_failure", failure=fault, loss_samples=None)
        with mock.patch.object(recording.os, "fsync", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError): writer.finish(end)
        self.assertEqual(writer.first_failure, fault)
        self.assertEqual(writer.checkpointed_end_sample_index, 0)
        self.assertEqual(recover(self.path).terminal.failure, fault)
        self.assertFalse(recover(self.path).clean_stop_reported)

    def test_invalid_admission_preserves_first_cause_if_failure_save_fails(self):
        writer = self.writer()
        writer.append(StreamBlock(identity(), 0, RAW))
        with mock.patch.object(recording.os, "fsync", side_effect=OSError("synthetic")):
            with self.assertRaises(StreamError) as result:
                writer.append(StreamBlock(identity(), 8, RAW))
        self.assertIsInstance(result.exception.__cause__, OSError)
        self.assertEqual(writer.first_failure.code, "input_order")

    def test_failed_create_flush_and_clean_descriptor_lifetime(self):
        with mock.patch.object(recording, "_sync", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError): self.writer()
        self.assertTrue(self.path.exists())
        self.assertFalse(recover(self.path).clean_stop_reported)
        stream = mock.Mock()
        stream.flush.side_effect = OSError("flush")
        with mock.patch.object(recording.os, "fsync") as fsync:
            with self.assertRaises(OSError): recording._sync(stream)
            fsync.assert_not_called()

    def test_hard_index_limit_and_recovery_memory(self):
        previous = header(first=0)
        with self.path.open("wb") as stream:
            stream.write(previous)
            for first in range(65536):
                block = frame(previous, b"B", struct.pack("<QI", first, 1) + b"\x00\xff")
                checkpoint = frame(block, b"C", struct.pack("<QQ", first + 1, first + 1))
                stream.write(block + checkpoint)
                previous = checkpoint
            stream.write(frame(previous, b"B", struct.pack("<QI", 65536, 1) + b"\x00\xff"))
        tracemalloc.start()
        saved = recover(self.path)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(len(saved._entries), 65536)
        self.assertEqual(saved.end_sample_index, 65536)
        self.assertEqual(saved.recovery_issue, "invalid_tail")
        self.assertEqual(saved.read_samples(65535, 1), (65280,))
        self.assertLess(peak, 24 * 1024 * 1024)  # CPython allocation evidence, not a platform promise.

    def test_lazy_read_work_is_bounded_and_hash_checked(self):
        writer = self.writer()
        for first in range(0, 8192 * 4, 8192):
            writer.append(StreamBlock(identity(), first, b"\xff\xff" * 8192))
        writer.finish(terminal(8192 * 4))
        saved = recover(self.path)
        real_open = recording._open_read
        reads = []
        class Meter:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def __getattr__(self, key): return getattr(self.stream, key)
            def read(self, count):
                reads.append(count)
                return self.stream.read(count)
        with mock.patch.object(recording, "_open_read", side_effect=lambda path: Meter(real_open(path))):
            self.assertEqual(saved.read_samples(8191, 2), (65535, 65535))
        self.assertEqual(sum(reads), 2 * (5 + 12 + 16384 + 32))
        # Simulated corrupted readback under unchanged stat still fails SHA verification.
        class Corrupt(Meter):
            def read(self, count):
                data = self.stream.read(count)
                return data[:-1] + bytes([data[-1] ^ 1]) if count > 32 else data
        with mock.patch.object(recording, "_open_read", side_effect=lambda path: Corrupt(real_open(path))):
            with self.assertRaises(StreamError): saved.read_raw(0, 1)

    def test_stable_cross_api_mtime_disagreement_and_ctime_separation(self):
        self.path.write_bytes(b"".join(fixed_fixture()))
        real = Path.lstat
        def changed(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            if path != self.path:
                return info
            proxy = mock.Mock(wraps=info)
            for key in ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns"):
                setattr(proxy, key, getattr(info, key))
            proxy.st_file_attributes = 0
            proxy.st_mtime_ns += 1
            return proxy
        with mock.patch.object(Path, "lstat", changed):
            with self.assertRaises(StreamError): recover(self.path)
        def different_ctime(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            if path != self.path:
                return info
            proxy = mock.Mock(wraps=info)
            for key in ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns"):
                setattr(proxy, key, getattr(info, key))
            proxy.st_file_attributes = 0
            proxy.st_ctime_ns = 1
            return proxy
        with mock.patch.object(Path, "lstat", different_ctime):
            self.assertEqual(recover(self.path).end_sample_index, 107)

    def test_checkpoint_overshoot_does_not_exceed_pending_budget(self):
        writer = self.writer()
        with mock.patch.object(recording, "CHECKPOINT_BYTES", 16386):
            writer.append(StreamBlock(identity(), 0, b"\x01\x00" * 8192))
            writer.append(StreamBlock(identity(), 8192, RAW))
            self.assertEqual(writer.checkpointed_end_sample_index, 8192)
            self.assertEqual(writer._pending_bytes, len(RAW))
            writer.finish(terminal(8199))
        saved = recover(self.path)
        self.assertEqual(saved.read_samples(8191, 8), (1,) + CODES)

    def test_unknown_or_inconsistent_terminal_metadata_is_never_clean(self):
        head, block, checkpoint = fixed_fixture()
        obj = asdict(terminal(107))
        mutations = (dict(end_sample_index=108), dict(loss_samples=True), dict(terminal_observed=1),
                     dict(identity=asdict(identity(generation=5))), dict(extra=1))
        for changes in mutations:
            bad = dict(obj, **changes)
            self.path.write_bytes(head + block + checkpoint +
                                  frame(checkpoint, b"T", json.dumps(bad).encode()))
            saved = recover(self.path)
            self.assertEqual(saved.end_sample_index, 107)
            self.assertIsNone(saved.terminal)
            self.assertEqual(saved.recovery_issue, "invalid_tail")

    def test_uncheckpointed_valid_frames_are_not_accepted(self):
        head, block, checkpoint = fixed_fixture()
        self.path.write_bytes(head + block)
        saved = recover(self.path)
        self.assertEqual(saved.end_sample_index, 100)
        self.assertEqual(saved.recovery_issue, "uncheckpointed_tail")
        self.assertEqual(saved.discarded_tail_bytes, len(block))
        with self.assertRaises(StreamError): saved.read_raw(100, 1)


class PathObservationTests(unittest.TestCase):
    """Durable R2 regressions for the three independently reproduced families."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "data"
        self.directory.mkdir()
        self.path = self.directory / "journal"

    def writer(self):
        writer = StreamWriter.create(self.path, start())
        self.addCleanup(writer.close)
        writer.append(StreamBlock(identity(), 0, RAW))
        writer.checkpoint()
        writer.append(StreamBlock(identity(), 7, RAW))
        return writer

    def replace_leaf(self):
        self.path.rename(self.root / "original")
        self.path.write_bytes(b"unrelated file must remain untouched")

    def test_native_checkpoint_replacement_or_windows_prevention(self):
        writer = self.writer()
        if os.name == "nt":
            before = self.path.read_bytes()
            with self.assertRaises(PermissionError) as result: self.replace_leaf()
            native_mutation_result(self, result.exception, windows_error=32, operation="v1_before_checkpoint")
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse((self.root / "original").exists())
            self.assertEqual(writer.checkpointed_end_sample_index, 7)
            return
        self.replace_leaf()
        with self.assertRaises(StreamError) as result: writer.checkpoint()
        native_mutation_result(self, result.exception, windows_error=32, operation="v1_before_checkpoint")
        self.assertTrue(writer.closed)
        self.assertEqual(writer.checkpointed_end_sample_index, 7)
        self.assertEqual(recover(self.root / "original").end_sample_index, 7)
        self.assertEqual(self.path.read_bytes(), b"unrelated file must remain untouched")

    def test_native_finish_replacement_or_windows_prevention(self):
        writer = self.writer()
        if os.name == "nt":
            before = self.path.read_bytes()
            with self.assertRaises(PermissionError) as result: self.replace_leaf()
            native_mutation_result(self, result.exception, windows_error=32, operation="v1_before_finish")
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse((self.root / "original").exists())
            self.assertEqual(writer.checkpointed_end_sample_index, 7)
            return
        self.replace_leaf()
        with self.assertRaises(StreamError) as result: writer.finish(terminal(14))
        native_mutation_result(self, result.exception, windows_error=32, operation="v1_before_finish")
        self.assertTrue(writer.closed)
        self.assertEqual(writer.checkpointed_end_sample_index, 7)
        self.assertIsNone(recover(self.root / "original").terminal)

    def test_checkpoint_rejects_truncation_without_sparse_rewrite(self):
        writer = self.writer()
        self.path.write_bytes(b"")
        with self.assertRaises(StreamError): writer.checkpoint()
        self.assertEqual(writer.checkpointed_end_sample_index, 7)
        self.assertEqual(self.path.stat().st_size, 0)
        self.assertEqual(writer.first_failure.code, "host_io")

    def test_observed_leaf_change_rejected_before_publication(self):
        for operation in ("checkpoint", "finish"):
            for kind in ("identity", "reparse"):
                with self.subTest(operation=operation, kind=kind):
                    self.path.unlink(missing_ok=True)
                    writer = self.writer()
                    before = self.path.read_bytes()
                    with mock.patch.object(Path, "lstat", changed_lstat(self.path, kind)):
                        with self.assertRaises(StreamError):
                            writer.checkpoint() if operation == "checkpoint" else writer.finish(terminal(14))
                    self.assertTrue(writer.closed)
                    self.assertEqual(writer.checkpointed_end_sample_index, 7)
                    self.assertEqual(self.path.read_bytes(), before)
                    self.assertIsNone(recover(self.path).terminal)

    def test_native_after_fsync_replacement_or_windows_prevention(self):
        for operation in ("checkpoint", "finish"):
            with self.subTest(operation=operation):
                self.path.unlink(missing_ok=True)
                (self.root / "original").unlink(missing_ok=True)
                writer = self.writer()
                sync = recording._sync
                def changed(stream):
                    sync(stream)
                    self.replace_leaf()
                with mock.patch.object(recording, "_sync", side_effect=changed):
                    with self.assertRaises(PermissionError if os.name == "nt" else StreamError) as result:
                        writer.checkpoint() if operation == "checkpoint" else writer.finish(terminal(14))
                native_mutation_result(self, result.exception, windows_error=32, operation="v1_after_" + operation)
                self.assertTrue(writer.closed)
                self.assertEqual(writer.checkpointed_end_sample_index, 7)
                original = self.path if os.name == "nt" else self.root / "original"
                self.assertEqual(recover(original).end_sample_index, 14)
                if os.name == "nt":
                    self.assertFalse((self.root / "original").exists())
                else:
                    self.assertEqual(self.path.read_bytes(), b"unrelated file must remain untouched")

    def test_observed_leaf_change_rejected_after_fsync(self):
        for operation in ("checkpoint", "finish"):
            for kind in ("identity", "reparse"):
                with self.subTest(operation=operation, kind=kind):
                    self.path.unlink(missing_ok=True)
                    writer = self.writer()
                    active = False
                    sync = recording._sync
                    def changed(stream):
                        nonlocal active
                        sync(stream)
                        active = True
                    with mock.patch.object(Path, "lstat", changed_lstat(self.path, kind, lambda: active)), \
                            mock.patch.object(recording, "_sync", changed):
                        with self.assertRaises(StreamError):
                            writer.checkpoint() if operation == "checkpoint" else writer.finish(terminal(14))
                    self.assertTrue(active)
                    self.assertTrue(writer.closed)
                    self.assertEqual(writer.checkpointed_end_sample_index, 7)
                    self.assertEqual(recover(self.path).end_sample_index, 14)

    def ctime_drift(self):
        real = Path.lstat
        calls = 0
        def lstat(path, *args, **kwargs):
            nonlocal calls
            info = real(path, *args, **kwargs)
            if path != self.path:
                return info
            calls += 1
            fields = {name: getattr(info, name) for name in
                      ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
            fields["st_ctime_ns"] += int(calls > 1)
            return SimpleNamespace(**fields, st_file_attributes=0)
        return lstat

    def test_recover_rejects_same_api_path_ctime_drift(self):
        self.path.write_bytes(b"".join(fixed_fixture()))
        with mock.patch.object(Path, "lstat", self.ctime_drift()), self.assertRaises(StreamError):
            recover(self.path)

    def test_lazy_read_rejects_same_api_path_ctime_drift(self):
        self.path.write_bytes(b"".join(fixed_fixture()))
        saved = recover(self.path)
        with mock.patch.object(Path, "lstat", self.ctime_drift()), self.assertRaises(StreamError):
            saved.read_raw(100, 1)

    def swapped_ancestor_reader(self):
        real = recording._open_read
        changed = False
        directory, root = self.directory, self.root
        class Swap:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def __getattr__(self, name): return getattr(self.stream, name)
            def read(self, count):
                nonlocal changed
                data = self.stream.read(count)
                if not changed:
                    changed = True
                    directory.rename(root / "renamed")
                    directory.symlink_to(root / "renamed", target_is_directory=True)
                return data
        return lambda path: Swap(real(path))

    def test_native_recovery_ancestor_swap_or_windows_prevention(self):
        self.path.write_bytes(b"".join(fixed_fixture()))
        with mock.patch.object(recording, "_open_read", self.swapped_ancestor_reader()):
            with self.assertRaises(PermissionError if os.name == "nt" else StreamError) as result:
                recover(self.path)
        native_mutation_result(self, result.exception, windows_error=5, operation="v1_recovery_ancestor")
        if os.name == "nt":
            self.assertFalse((self.root / "renamed").exists())
            self.assertEqual(self.path.read_bytes(), b"".join(fixed_fixture()))

    def test_native_lazy_ancestor_swap_or_windows_prevention(self):
        self.path.write_bytes(b"".join(fixed_fixture()))
        saved = recover(self.path)
        with mock.patch.object(recording, "_open_read", self.swapped_ancestor_reader()):
            with self.assertRaises(PermissionError if os.name == "nt" else StreamError) as result:
                saved.read_raw(100, 1)
        native_mutation_result(self, result.exception, windows_error=5, operation="v1_lazy_ancestor")
        if os.name == "nt":
            self.assertFalse((self.root / "renamed").exists())
            self.assertEqual(saved.read_raw(100, 1), RAW[:2])

    def test_observed_final_ancestor_change_rejected(self):
        self.path.write_bytes(b"".join(fixed_fixture()))
        saved = recover(self.path)
        for operation in ("recover", "lazy"):
            for kind in ("identity", "reparse"):
                with self.subTest(operation=operation, kind=kind):
                    active = False
                    def change():
                        nonlocal active
                        active = True
                    opener = after_read(recording._open_read, change)
                    with mock.patch.object(Path, "lstat", changed_lstat(self.directory, kind, lambda: active)), \
                            mock.patch.object(recording, "_open_read", opener):
                        with self.assertRaises(StreamError):
                            recover(self.path) if operation == "recover" else saved.read_raw(100, 1)
                    self.assertTrue(active)


class PreparedAttemptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "attempt"

    def prepare(self, policy="next_block", *, path=None, limits=StreamLimits()):
        prepared = PreparedStreamWriter.prepare(path or self.path,
            StreamPreparation(identity(), "local-recording-r1", policy), limits)
        self.addCleanup(prepared.close)
        return prepared

    def test_preparation_has_no_selected_r_and_cannot_recreate_token(self):
        prepared = self.prepare()
        token = prepared.readiness_token
        self.assertEqual(token.preparation, prepared.preparation)
        self.assertEqual(len(token.token_id), 32)
        report = recover_attempt(self.path)
        self.assertIsNone(report.selected_start)
        self.assertIsNone(report.recording)
        self.assertIsNone(report.unbound_outcome)
        self.assertFalse(hasattr(report, "readiness_token"))
        self.assertFalse(hasattr(report, "first_sample_index"))
        self.assertEqual(report.recovery_issue, "missing_selection_or_terminal")
        with self.assertRaises(AttributeError): prepared.readiness_token = token
        with self.assertRaises(FrozenInstanceError): token.token_id = "0" * 32
        with self.assertRaises(StreamError): recover(self.path)
        prepared.close()  # The following v1 fixture is unrelated to the open attempt.
        self.path.unlink()
        self.path.write_bytes(b"".join(fixed_fixture()))
        with self.assertRaises(StreamError): recover_attempt(self.path)
        self.assertEqual(recover(self.path).read_raw(100, 7), RAW)

    def test_first_whole_block_and_checkpoint_above_float_precision(self):
        prepared = self.prepare()
        fd = prepared._writer._stream.fileno()
        first = 2**60 + 123
        writer = prepared.bind_first_block(prepared.readiness_token, StreamBlock(identity(), first, RAW))
        self.addCleanup(writer.close)
        self.assertEqual(writer._stream.fileno(), fd)
        self.assertEqual(writer.accepted_end_sample_index, first + 7)
        self.assertEqual(writer.checkpointed_end_sample_index, first)
        report = recover_attempt(self.path)
        self.assertEqual(report.selected_start.first_sample_index, first)
        self.assertIsNone(report.recording)
        self.assertEqual(report.recovery_issue, "uncheckpointed_tail")
        writer.checkpoint()
        report = recover_attempt(self.path)
        self.assertEqual(report.recording.read_raw(first, 7), RAW)
        self.assertIsNone(report.recording.terminal)
        prepared.close()  # Ownership has transferred; the active writer stays open.
        self.assertFalse(writer.closed)
        writer.finish(terminal(first + 7))
        report = recover_attempt(self.path)
        self.assertTrue(report.recording.clean_stop_reported)
        self.assertEqual(report.recording.read_samples(first, 7), CODES)

    def test_both_serialized_data_end_race_orders_and_first_cause(self):
        first = self.prepare()
        outcome = UnboundOutcome("failed_unbound", FailureEvidence("source_overrun", "first source fault"),
            terminal_observed=True, device_generated_end_sample_index=None,
            device_transmitted_end_sample_index=900, loss_samples=None)
        first.finish_unbound(first.readiness_token, outcome)
        with self.assertRaises(StreamError):
            first.bind_first_block(first.readiness_token, StreamBlock(identity(), 900, RAW))
        report = recover_attempt(self.path)
        self.assertEqual(report.unbound_outcome, outcome)
        self.assertIsNone(report.selected_start)
        self.assertIsNone(report.recording)
        self.assertIsNone(report.unbound_outcome.loss_samples)
        second = self.prepare(path=self.root / "second")
        writer = second.bind_first_block(second.readiness_token, StreamBlock(identity(), 900, RAW))
        self.addCleanup(writer.close)
        with self.assertRaises(StreamError): second.finish_unbound(second.readiness_token, outcome)
        writer.finish(terminal(907, reason="source_failure", failure=outcome.failure,
                               loss_samples=None, terminal_observed=False))
        result = recover_attempt(self.root / "second")
        self.assertEqual(result.recording.read_raw(900, 7), RAW)
        self.assertEqual(result.recording.terminal.failure, outcome.failure)

    def test_direct_start_failed_start_and_legitimate_empty_completion(self):
        prepared = self.prepare("source_start")
        writer = prepared.bind_start(prepared.readiness_token,
            StreamStart(identity(), 0, "local-recording-r1", "caller_validated_START_ACK"))
        self.addCleanup(writer.close)
        report = recover_attempt(self.path)
        self.assertEqual(report.selected_start.first_sample_index, 0)
        self.assertIsNone(report.recording)
        self.assertEqual(report.recovery_issue, "selection_without_commit")
        writer.finish(terminal(0))
        report = recover_attempt(self.path)
        self.assertEqual(report.recording.first_sample_index, 0)
        self.assertEqual(report.recording.end_sample_index, 0)
        self.assertEqual(report.recording.read_raw(0, 0), b"")
        self.assertTrue(report.recording.clean_stop_reported)
        failed = self.prepare("source_start", path=self.root / "failed")
        failed.finish_unbound(failed.readiness_token, UnboundOutcome("failed_unbound",
            FailureEvidence("source_error", "START failed", "start", 11, 4)))
        no_start = recover_attempt(self.root / "failed")
        self.assertIsNone(no_start.selected_start)
        self.assertIsNone(no_start.recording)
        self.assertEqual(no_start.unbound_outcome.failure.source_code, 4)

    def test_policy_token_and_cross_attempt_fences(self):
        first, other = self.prepare(), self.prepare(path=self.root / "other")
        for token in (other.readiness_token, replace(first.readiness_token), "0" * 32, None):
            with self.assertRaises(StreamError): first.bind_first_block(token, StreamBlock(identity(), 0, RAW))
        self.assertFalse(first.closed)
        self.assertFalse(other.closed)
        writer = first.bind_first_block(first.readiness_token, StreamBlock(identity(), 0, RAW))
        self.addCleanup(writer.close)
        with self.assertRaises(StreamError): first.bind_first_block(first.readiness_token, StreamBlock(identity(), 7, RAW))
        writer.finish(terminal(7))
        with self.assertRaises(StreamError): first.finish_unbound(first.readiness_token, UnboundOutcome("ended_unbound"))
        other.finish_unbound(other.readiness_token, UnboundOutcome("cancelled_unbound"))
        self.assertEqual(recover_attempt(self.root / "other").unbound_outcome.reason, "cancelled_unbound")
        with self.assertRaises(StreamError): other.bind_first_block(other.readiness_token, StreamBlock(identity(), 0, RAW))
        restarted = self.prepare(path=self.root / "restart")
        self.assertNotEqual(restarted.readiness_token.token_id, first.readiness_token.token_id)
        with self.assertRaises(StreamError): restarted.bind_first_block(first.readiness_token, StreamBlock(identity(), 0, RAW))
        restarted.close()
        with self.assertRaises(StreamError): restarted.bind_first_block(restarted.readiness_token, StreamBlock(identity(), 0, RAW))
        with self.assertRaises(FileExistsError): self.prepare(path=self.root / "restart")

    def test_wrong_policy_identity_and_capacity_reject_before_r_binding(self):
        cases = (("wrong_policy", "source_start", StreamLimits(), StreamBlock(identity(), 3, RAW)),
            ("identity", "next_block", StreamLimits(), StreamBlock(identity(config_id=10), 3, RAW)),
            ("rate", "next_block", StreamLimits(), StreamBlock(identity(nominal_fs_hz=1), 3, RAW)),
            ("raw_quota", "next_block", StreamLimits(2, 1), StreamBlock(identity(), 3, RAW)),
            ("index_quota", "next_block", StreamLimits(14, 0), StreamBlock(identity(), 3, RAW)),
            ("mutable", "next_block", StreamLimits(), bytearray(RAW)))
        for name, policy, limits, block in cases:
            with self.subTest(name=name):
                prepared = self.prepare(policy, path=self.root / name, limits=limits)
                with self.assertRaises(StreamError): prepared.bind_first_block(prepared.readiness_token, block)
                self.assertTrue(prepared.closed)
                report = recover_attempt(self.root / name)
                self.assertIsNone(report.selected_start)
                self.assertIsNone(report.recording)
                self.assertEqual(report.unbound_outcome.reason, "failed_unbound")
        prepared = self.prepare("next_block", path=self.root / "wrong_start")
        with self.assertRaises(StreamError): prepared.bind_start(prepared.readiness_token, start())
        self.assertIsNone(recover_attempt(self.root / "wrong_start").selected_start)

    def test_forged_block_is_revalidated_before_binding(self):
        for value in (True, U64_MAX, -1):
            path = self.root / str(value)
            prepared = self.prepare(path=path)
            block = StreamBlock(identity(), 0, RAW)
            object.__setattr__(block, "first_sample_index", value)
            with self.assertRaises(StreamError): prepared.bind_first_block(prepared.readiness_token, block)
            self.assertIsNone(recover_attempt(path).selected_start)
        with self.assertRaises(StreamError): PreparedReadyToken("0" * 31, StreamPreparation(identity(), "r", "next_block"))
        for outcome in (dict(reason="unknown"), dict(reason="failed_unbound"),
            dict(reason="ended_unbound", loss_samples=True),
            dict(reason="ended_unbound", device_generated_end_sample_index=1, device_transmitted_end_sample_index=2)):
            with self.assertRaises(StreamError): UnboundOutcome(**outcome)

    def test_prepare_failures_return_no_readiness_and_preserve_readable_header(self):
        for stage in ("_write_all", "_sync"):
            path = self.root / stage
            with mock.patch.object(recording, stage, side_effect=OSError("synthetic pre-readiness fault")):
                with self.assertRaises(OSError): self.prepare(path=path)
            if stage == "_sync":
                report = recover_attempt(path)
                self.assertIsNone(report.selected_start)
                self.assertFalse(hasattr(report, "readiness_token"))
            else:
                with self.assertRaises(StreamError): recover_attempt(path)
    def test_native_pre_readiness_swap_or_windows_prevention(self):
        real_sync = recording._sync
        def swapped(stream):
            real_sync(stream)
            self.path.rename(self.root / "original")
            self.path.write_bytes(b"unrelated")
        with mock.patch.object(recording, "_sync", side_effect=swapped):
            with self.assertRaises(PermissionError if os.name == "nt" else StreamError) as result:
                self.prepare()
        native_mutation_result(self, result.exception, windows_error=32, operation="v2_pre_readiness")
        original = self.path if os.name == "nt" else self.root / "original"
        self.assertIsNone(recover_attempt(original).selected_start)
        if os.name == "nt":
            self.assertFalse((self.root / "original").exists())
        else:
            self.assertEqual(self.path.read_bytes(), b"unrelated")

    def test_observed_pre_readiness_leaf_change_returns_no_token(self):
        for kind in ("identity", "reparse"):
            with self.subTest(kind=kind):
                path = self.root / kind
                active = False
                sync = recording._sync
                def changed(stream):
                    nonlocal active
                    sync(stream)
                    active = True
                with mock.patch.object(Path, "lstat", changed_lstat(path, kind, lambda: active)), \
                        mock.patch.object(recording, "_sync", changed):
                    with self.assertRaises(StreamError): self.prepare(path=path)
                self.assertTrue(active)
                report = recover_attempt(path)
                self.assertIsNone(report.selected_start)
                self.assertIsNone(report.recording)
                self.assertFalse(hasattr(report, "readiness_token"))

    def test_bind_io_failure_does_not_invent_r_or_success(self):
        prepared = self.prepare()
        with mock.patch.object(recording, "_write_all", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                prepared.bind_first_block(prepared.readiness_token, StreamBlock(identity(), 100, RAW))
        self.assertTrue(prepared.closed)
        self.assertEqual(prepared.first_failure.code, "host_io")
        report = recover_attempt(self.path)
        self.assertIsNone(report.selected_start)
        self.assertIsNone(report.recording)
        with self.assertRaises(StreamError): prepared.bind_first_block(prepared.readiness_token, StreamBlock(identity(), 100, RAW))

    def test_unbound_terminal_sync_failure_and_first_cause(self):
        prepared = self.prepare()
        cause = FailureEvidence("ambiguous_dma", "unknown source loss", "receive", 1, 2)
        outcome = UnboundOutcome("failed_unbound", cause)
        with mock.patch.object(recording, "_sync", side_effect=OSError("sync failed")):
            with self.assertRaises(OSError): prepared.finish_unbound(prepared.readiness_token, outcome)
        report = recover_attempt(self.path)
        self.assertEqual(report.unbound_outcome.failure, cause)
        self.assertEqual(prepared.first_failure, cause)
        self.assertIsNone(report.recording)
        self.assertIsNone(report.selected_start)

    def test_start_and_unbound_terminals_are_last_and_policy_specific(self):
        prepared = self.prepare("source_start")
        writer = prepared.bind_start(prepared.readiness_token, start())
        self.addCleanup(writer.close)
        writer.finish(terminal(0))
        data = self.path.read_bytes()
        self.path.write_bytes(data + b"x")
        report = recover_attempt(self.path)
        self.assertIsNone(report.recording)
        self.assertEqual(report.recovery_issue, "invalid_tail")
        path = self.root / "unbound"
        ended = self.prepare(path=path)
        ended.finish_unbound(ended.readiness_token, UnboundOutcome("ended_unbound"))
        path.write_bytes(path.read_bytes() + b"x")
        report = recover_attempt(path)
        self.assertIsNone(report.unbound_outcome)
        self.assertIsNone(report.recording)
        self.assertIsNone(report.selected_start)
        self.assertEqual(report.recovery_issue, "invalid_tail")

    def test_all_byte_cuts_prepared_first_block_checkpoint_and_unbound_terminal(self):
        prepared = self.prepare()
        header_end = self.path.stat().st_size
        writer = prepared.bind_first_block(prepared.readiness_token, StreamBlock(identity(), 100, RAW))
        self.addCleanup(writer.close)
        block_end = self.path.stat().st_size
        writer.checkpoint()
        checkpoint_end = self.path.stat().st_size
        writer.close()
        data = self.path.read_bytes()
        for cut in range(checkpoint_end + 1):
            self.path.write_bytes(data[:cut])
            if cut < header_end:
                with self.assertRaises(StreamError): recover_attempt(self.path)
            else:
                report = recover_attempt(self.path)
                self.assertEqual(report.selected_start is not None, cut >= block_end)
                self.assertEqual(report.recording is not None, cut == checkpoint_end)
                if report.recording is not None:
                    self.assertEqual(report.recording.read_raw(100, 7), RAW)
        path = self.root / "unbound"
        empty = self.prepare(path=path)
        header_end = path.stat().st_size
        empty.finish_unbound(empty.readiness_token, UnboundOutcome("cancelled_unbound"))
        data = path.read_bytes()
        for cut in range(header_end, len(data) + 1):
            path.write_bytes(data[:cut])
            report = recover_attempt(path)
            self.assertIsNone(report.recording)
            self.assertIsNone(report.selected_start)
            self.assertEqual(report.unbound_outcome is not None, cut == len(data))

    def test_all_byte_cuts_known_start_marker_and_empty_terminal(self):
        prepared = self.prepare("source_start")
        header_end = self.path.stat().st_size
        writer = prepared.bind_start(prepared.readiness_token, start())
        self.addCleanup(writer.close)
        marker_end = self.path.stat().st_size
        writer.finish(terminal(0))
        data = self.path.read_bytes()
        for cut in range(header_end, len(data) + 1):
            self.path.write_bytes(data[:cut])
            report = recover_attempt(self.path)
            self.assertEqual(report.selected_start is not None, cut >= marker_end)
            self.assertEqual(report.recording is not None, cut == len(data))

    def test_known_start_empty_terminal_with_failed_sync_is_not_old_receipt(self):
        prepared = self.prepare("source_start")
        writer = prepared.bind_start(prepared.readiness_token, start(123))
        with mock.patch.object(recording, "_sync", side_effect=OSError("fsync failed")):
            with self.assertRaises(OSError): writer.finish(terminal(123))
        report = recover_attempt(self.path)
        self.assertEqual(writer.first_failure.code, "host_io")
        self.assertEqual(report.recording.end_sample_index, 123)
        self.assertTrue(report.recording.clean_stop_reported)  # Source claims, not successful old finish.

    def test_fresh_process_attempt_recovery_and_exact_physical_bounds(self):
        prepared = self.prepare()
        prepared.finish_unbound(prepared.readiness_token, UnboundOutcome("ended_unbound",
            terminal_observed=True, device_generated_end_sample_index=99,
            device_transmitted_end_sample_index=99, loss_samples=None))
        code = ("import json,sys; from motortester.stream_recording import recover_attempt; "
                "r=recover_attempt(sys.argv[1]); print(json.dumps([r.selected_start is None,"
                "r.recording is None,r.unbound_outcome.reason,r.unbound_outcome.loss_samples,"
                "hasattr(r,'readiness_token')]))")
        result = subprocess.run([sys.executable, "-O", "-c", code, str(self.path)],
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), [True, True, "ended_unbound", None, False])
        self.assertEqual(recording.MAX_FILE_BYTES, 73801809)
        self.assertEqual(recording.MAX_ATTEMPT_FILE_BYTES, 73805942)
        self.assertEqual(recording.MAX_ATTEMPT_FILE_BYTES - recording.MAX_FILE_BYTES, 4133)


    def test_independent_version_two_schema_and_corruption_controls(self):
        # Build the v2 header independently of every production model/encoder.
        old = json.loads(header()[12:-32])
        source_start = old["start"]
        preparation = {"identity": source_start["identity"],
                       "recording_id": "local-recording-r1", "start_policy": "next_block"}
        obj = {"preparation": preparation, "limits": old["limits"]}
        encode = lambda value: json.dumps(value, separators=(",", ":")).encode()
        head = header_bytes(encode(obj), b"MTSTRM\x02\x00")
        block = frame(head, b"B", struct.pack("<QI", 100, 7) + RAW)
        check = frame(block, b"C", struct.pack("<QQ", 107, 1))
        self.path.write_bytes(head + block + check)
        saved = recover_attempt(self.path).recording
        self.assertEqual(saved.read_raw(100, 7), RAW)
        for bad_head in (dict(obj, extra=1), dict(obj, preparation=dict(preparation, first_sample_index=None)),
                         dict(obj, preparation=dict(preparation, start_policy="unknown"))):
            self.path.write_bytes(header_bytes(encode(bad_head), b"MTSTRM\x02\x00"))
            with self.assertRaises(StreamError): recover_attempt(self.path)
        # A complete valid hash does not make the wrong grammar acceptable.
        for suffix in (frame(head, b"S", encode(source_start)),
                       frame(head, b"C", struct.pack("<QQ", 100, 0)),
                       frame(head, b"T", encode(asdict(terminal(100)))),
                       frame(head, b"U", encode(dict(asdict(UnboundOutcome("ended_unbound")), extra=1))),
                       struct.pack("<cI", b"U", 4097),
                       frame(head, b"B", struct.pack("<QI", 100, 6) + RAW)):
            self.path.write_bytes(head + suffix)
            report = recover_attempt(self.path)
            self.assertIsNone(report.recording)
            self.assertIsNone(report.selected_start)
            self.assertIsNone(report.unbound_outcome)
            self.assertEqual(report.recovery_issue, "invalid_tail")
        for offset in (len(head) + 17, len(head) + len(block) - 1):
            damaged = bytearray(head + block + check)
            damaged[offset] ^= 1
            self.path.write_bytes(damaged)
            report = recover_attempt(self.path)
            self.assertIsNone(report.recording)
            self.assertIsNone(report.selected_start)
        direct = dict(obj, preparation=dict(preparation, start_policy="source_start"))
        head = header_bytes(encode(direct), b"MTSTRM\x02\x00")
        declared = frame(head, b"S", encode(source_start))
        for suffix in (frame(head, b"B", struct.pack("<QI", 100, 7) + RAW),
                       declared + frame(declared, b"S", encode(source_start)),
                       declared + frame(declared, b"U", encode(asdict(UnboundOutcome("ended_unbound"))))):
            self.path.write_bytes(head + suffix)
            report = recover_attempt(self.path)
            self.assertIsNone(report.recording)
            self.assertIsNone(report.unbound_outcome)
            self.assertEqual(report.recovery_issue, "invalid_tail")

    def test_prepare_flush_failure_and_exclusive_no_clobber(self):
        real_open = recording._open_file
        class FlushFailure:
            def __init__(self, stream): self.stream = stream
            def __getattr__(self, name): return getattr(self.stream, name)
            def flush(self): raise OSError("synthetic flush failure")
        with mock.patch.object(recording, "_open_file", side_effect=lambda *a, **k: FlushFailure(real_open(*a, **k))):
            with mock.patch.object(recording.os, "fsync") as sync:
                with self.assertRaises(OSError): self.prepare()
                sync.assert_not_called()
        report = recover_attempt(self.path)
        self.assertIsNone(report.recording)
        self.assertFalse(hasattr(report, "readiness_token"))
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError): self.prepare()
        self.assertEqual(self.path.read_bytes(), before)

    def test_direct_start_validation_and_noop_marker_checkpoint(self):
        for name, declared in (("identity", replace(start(), identity=identity(generation=5))),
                               ("recording", replace(start(), recording_id="other"))):
            prepared = self.prepare("source_start", path=self.root / name)
            with self.assertRaises(StreamError): prepared.bind_start(prepared.readiness_token, declared)
            report = recover_attempt(self.root / name)
            self.assertIsNone(report.selected_start)
            self.assertEqual(report.unbound_outcome.reason, "failed_unbound")
        prepared = self.prepare("source_start")
        writer = prepared.bind_start(prepared.readiness_token, start(100))
        self.addCleanup(writer.close)
        size = self.path.stat().st_size
        with mock.patch.object(recording.os, "fsync") as sync:
            for _ in range(20): writer.checkpoint()
            sync.assert_not_called()
        self.assertEqual(self.path.stat().st_size, size)
        self.assertIsNone(recover_attempt(self.path).recording)
        writer.append(StreamBlock(identity(), 100, RAW))
        writer.finish(terminal(107))
        report = recover_attempt(self.path)
        self.assertEqual(report.recording.read_raw(100, 7), RAW)

    def publication_writer(self, operation, path):
        prepared = self.prepare(path=path)
        writer = None
        if operation in ("checkpoint", "finish"):
            writer = prepared.bind_first_block(prepared.readiness_token, StreamBlock(identity(), 100, RAW))
            self.addCleanup(writer.close)
        return prepared, writer

    def publish(self, operation, prepared, writer):
        if operation == "bind":
            return prepared.bind_first_block(prepared.readiness_token, StreamBlock(identity(), 100, RAW))
        if operation == "unbound":
            return prepared.finish_unbound(prepared.readiness_token, UnboundOutcome("ended_unbound"))
        if operation == "checkpoint":
            return writer.checkpoint()
        return writer.finish(terminal(107))

    def test_native_version_two_replacement_or_windows_prevention(self):
        for operation in ("bind", "unbound", "checkpoint", "finish"):
            with self.subTest(operation=operation):
                path = self.root / operation
                prepared, writer = self.publication_writer(operation, path)
                original = self.root / (operation + "-original")
                if os.name == "nt":
                    before = path.read_bytes()
                    with self.assertRaises(PermissionError) as result: path.rename(original)
                    native_mutation_result(self, result.exception, windows_error=32, operation="v2_before_" + operation)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertFalse(original.exists())
                    self.assertIsNone(recover_attempt(path).recording)
                    continue
                path.rename(original)
                path.write_bytes(b"must not change")
                with self.assertRaises(StreamError) as result: self.publish(operation, prepared, writer)
                native_mutation_result(self, result.exception, windows_error=32, operation="v2_before_" + operation)
                self.assertEqual(path.read_bytes(), b"must not change")
                self.assertIsNone(recover_attempt(original).recording)

    def test_observed_version_two_leaf_change_before_publication(self):
        for operation in ("bind", "unbound", "checkpoint", "finish"):
            for kind in ("identity", "reparse"):
                with self.subTest(operation=operation, kind=kind):
                    path = self.root / (operation + "-" + kind)
                    prepared, writer = self.publication_writer(operation, path)
                    before = path.read_bytes()
                    # Binding first selects an empty in-process interval. A
                    # failed first B must still leave no selected R on disk.
                    receipt = 100 if operation == "bind" else prepared._writer.checkpointed_end_sample_index
                    with mock.patch.object(Path, "lstat", changed_lstat(path, kind)):
                        with self.assertRaises(StreamError): self.publish(operation, prepared, writer)
                    self.assertTrue(prepared._writer.closed)
                    self.assertEqual(prepared._writer.checkpointed_end_sample_index, receipt)
                    self.assertEqual(path.read_bytes(), before)
                    report = recover_attempt(path)
                    self.assertIsNone(report.recording)
                    self.assertIsNone(report.unbound_outcome)
                    if operation in ("bind", "unbound"):
                        self.assertIsNone(report.selected_start)

    def test_native_unbound_after_sync_swap_or_windows_prevention(self):
        prepared = self.prepare()
        real_sync = recording._sync
        def changed(stream):
            real_sync(stream)
            self.path.rename(self.root / "unbound-original")
            self.path.write_bytes(b"new path")
        with mock.patch.object(recording, "_sync", side_effect=changed):
            with self.assertRaises(PermissionError if os.name == "nt" else StreamError) as result:
                prepared.finish_unbound(prepared.readiness_token, UnboundOutcome("ended_unbound"))
        native_mutation_result(self, result.exception, windows_error=32, operation="v2_unbound_after_sync")
        self.assertTrue(prepared._writer.closed)
        self.assertIsNone(prepared._writer.checkpointed_end_sample_index)
        original = self.path if os.name == "nt" else self.root / "unbound-original"
        if os.name == "nt":
            self.assertFalse((self.root / "unbound-original").exists())
        else:
            self.assertEqual(self.path.read_bytes(), b"new path")
        # Readable U after unsuccessful call is not a completion receipt.
        self.assertEqual(recover_attempt(original).unbound_outcome.reason, "ended_unbound")

    def test_observed_unbound_leaf_change_after_sync(self):
        for kind in ("identity", "reparse"):
            with self.subTest(kind=kind):
                path = self.root / kind
                prepared = self.prepare(path=path)
                active = False
                sync = recording._sync
                def changed(stream):
                    nonlocal active
                    sync(stream)
                    active = True
                with mock.patch.object(Path, "lstat", changed_lstat(path, kind, lambda: active)), \
                        mock.patch.object(recording, "_sync", changed):
                    with self.assertRaises(StreamError):
                        prepared.finish_unbound(prepared.readiness_token, UnboundOutcome("ended_unbound"))
                self.assertTrue(active)
                self.assertTrue(prepared._writer.closed)
                self.assertIsNone(prepared._writer.checkpointed_end_sample_index)
                self.assertEqual(recover_attempt(path).unbound_outcome.reason, "ended_unbound")

    def finished_attempt(self, path):
        prepared = self.prepare(path=path)
        writer = prepared.bind_first_block(prepared.readiness_token, StreamBlock(identity(), 100, RAW))
        self.addCleanup(writer.close)
        writer.finish(terminal(107))
        return recover_attempt(path).recording

    def test_version_two_lazy_read_same_api_ctime_drift(self):
        path = self.path
        saved = self.finished_attempt(path)
        real_lstat = Path.lstat
        calls = 0
        def drift(target, *a, **kw):
            nonlocal calls
            info = real_lstat(target, *a, **kw)
            if target != path: return info
            calls += 1
            return stat_fields(info, st_ctime_ns=info.st_ctime_ns + int(calls > 1))
        with mock.patch.object(Path, "lstat", drift):
            with self.assertRaises(StreamError): saved.read_raw(100, 1)

    def test_native_version_two_ancestor_swap_or_windows_prevention(self):
        directory = self.root / "data"
        directory.mkdir()
        path = directory / "attempt"
        saved = self.finished_attempt(path)
        real_open = recording._open_attempt_read
        renamed = self.root / "renamed"
        swapped = False
        def change():
            nonlocal swapped
            if not swapped:
                swapped = True
                directory.rename(renamed)
                directory.symlink_to(renamed, target_is_directory=True)
        with mock.patch.object(recording, "_open_attempt_read", after_read(real_open, change)):
            with self.assertRaises(PermissionError if os.name == "nt" else StreamError) as result:
                saved.read_raw(100, 1)
        native_mutation_result(self, result.exception, windows_error=5, operation="v2_lazy_ancestor")
        if os.name == "nt":
            self.assertFalse(renamed.exists())
            self.assertEqual(saved.read_raw(100, 1), RAW[:2])

    def test_observed_version_two_final_ancestor_change(self):
        directory = self.root / "data"
        directory.mkdir()
        path = directory / "attempt"
        saved = self.finished_attempt(path)
        for operation in ("recover", "lazy"):
            for kind in ("identity", "reparse"):
                with self.subTest(operation=operation, kind=kind):
                    active = False
                    def change():
                        nonlocal active
                        active = True
                    opener = after_read(recording._open_attempt_read, change)
                    with mock.patch.object(Path, "lstat", changed_lstat(directory, kind, lambda: active)), \
                            mock.patch.object(recording, "_open_attempt_read", opener):
                        with self.assertRaises(StreamError):
                            recover_attempt(path) if operation == "recover" else saved.read_raw(100, 1)
                    self.assertTrue(active)


if __name__ == "__main__":
    unittest.main()

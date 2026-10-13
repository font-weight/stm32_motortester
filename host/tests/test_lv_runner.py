"""Qt-free LV runner checks. All peers and measurements here are synthetic."""
from dataclasses import replace
import errno
import hashlib
import json
import os
import signal
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from types import SimpleNamespace
import unittest
import weakref
from unittest.mock import patch
import zlib

from motortester import lv_collector as lv
from motortester import lv_runner as runner
from motortester import recording
from motortester.device_emulator import DeviceEmulator, WAITING, STOPPED
from motortester.session import Config
from motortester.runtime import AcquisitionRuntime
from motortester.wire import Frame, decode_frame, encode_frame


def packets(data):
    return [decode_frame(p) for p in data.split(b"\0") if p]


class Peer(runner._SyntheticSerial):
    """Fault fixture, never an actual serial backend. Histories are test-only."""
    def __init__(self, *, signal=True, fault=None, fail_attempt=0,
                 write_size=4096, read_size=4096, **settings):
        super().__init__(**settings)
        self.signal, self.fault = signal, fault
        self.fail_attempt = fail_attempt
        self.write_size, self.read_size = write_size, read_size
        self.tx = bytearray()
        self.rx = bytearray()
        self.pending = bytearray()
        self.owner_ids = set()
        self.arm_count = self.close_count = 0
        self.used_fault = False
        self.settings = settings
        self._tx_decoder = bytearray()

    def open(self):
        self.owner_ids.add(threading.get_ident())
        super().open()

    def close(self):
        self.owner_ids.add(threading.get_ident())
        self.close_count += 1
        super().close()

    def write(self, data):
        self.owner_ids.add(threading.get_ident())
        accepted = data[:self.write_size]
        self.tx.extend(accepted)
        self._tx_decoder.extend(accepted)
        if self._tx_decoder.endswith(b"\0"):
            frame = decode_frame(bytes(self._tx_decoder[:-1]))
            self._tx_decoder.clear()
            if frame.message_type == 4:
                self.arm_count += 1
                if self.fault == "unknown_arm" and self.arm_count == self.fail_attempt + 1:
                    self.device.write(accepted[:3], runner.monotonic_ms())
                    raise OSError("private backend details must not escape")
        return self.device.write(accepted, runner.monotonic_ms()).accepted_prefix

    def read(self, size):
        self.owner_ids.add(threading.get_ident())
        now = runner.monotonic_ms()
        if self.signal and self.device.snapshot().state == WAITING:
            self.device.trigger(now)
        if not self.pending:
            chunks = []
            for _ in range(32):
                raw = self.device.read(4096, runner.monotonic_ms())
                if not raw:
                    break
                chunks.append(raw)
            else:
                raise AssertionError("unbounded fixture")
            raw = b"".join(chunks)
            if raw and not self.used_fault:
                frames = packets(raw)
                kinds = [f.message_type for f in frames]
                applicable = self.arm_count == self.fail_attempt + 1
                if self.fault == "caps" and 130 in kinds:
                    i = kinds.index(130)
                    payload = bytearray(frames[i].payload)
                    payload[21] = 2  # Advertise only Normal, reject requested Auto.
                    frames[i] = replace(frames[i], payload=bytes(payload))
                    self.used_fault = True
                elif self.fault == "config" and 131 in kinds:
                    i = kinds.index(131)
                    frames[i] = replace(frames[i], message_type=255,
                        payload=struct.pack("<BBHI", 3, 1, 0, 0))
                    self.used_fault = True
                elif applicable and self.fault == "arm_ack" and 132 in kinds:
                    i = kinds.index(132)
                    frames[i] = replace(frames[i], payload=b"\0" * 8)
                    self.used_fault = True
                elif applicable and self.fault == "stop_ack" and 133 in kinds:
                    i = kinds.index(133)
                    frames[i] = replace(frames[i], request_id=frames[i].request_id + 1)
                    self.used_fault = True
                elif applicable and self.fault == "no_begin" and 132 in kinds:
                    frames = [f for f in frames if f.message_type == 132]
                    self.used_fault = True
                elif applicable and self.fault == "partial" and 145 in kinds:
                    frames = frames[:kinds.index(145) + 1]
                    self.used_fault = True
                elif applicable and self.fault == "duplicate" and 145 in kinds:
                    i = kinds.index(145)
                    frames.insert(i + 1, frames[i])
                    self.used_fault = True
                elif applicable and self.fault == "sample_crc" and 146 in kinds:
                    i = kinds.index(146)
                    payload = bytearray(frames[i].payload)
                    payload[-1] ^= 1
                    frames[i] = replace(frames[i], payload=bytes(payload))
                    self.used_fault = True
                elif applicable and self.fault == "frame_crc" and 145 in kinds:
                    i = kinds.index(145)
                    encoded = bytearray(encode_frame(frames[i]))
                    encoded[-3] ^= 1
                    self.pending.extend(b"".join(encode_frame(f) for f in frames[:i]))
                    self.pending.extend(encoded)
                    self.pending.extend(b"".join(encode_frame(f) for f in frames[i + 1:]))
                    self.used_fault = True
                    frames = []
                elif applicable and self.fault == "stop_timeout" and 133 in kinds:
                    frames = [f for f in frames if f.message_type != 133]
                    self.used_fault = True
                raw = b"".join(encode_frame(f) for f in frames)
            self.pending.extend(raw)
        if self.used_fault and self.fault == "partial" and not self.pending:
            raise OSError("synthetic disconnect after partial raw data")
        result = bytes(self.pending[:min(size, self.read_size)])
        del self.pending[:len(result)]
        self.rx.extend(result)
        return result


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.handles = []
        self.addCleanup(self._retire_all)

    def _retire_all(self):
        for handle in self.handles:
            handle.stop()
            result = handle.wait(1000)
            self.assertFalse(result.owner_alive, "test left an unresolved owner")

    def request(self, name="опыт с пробелами", **options):
        return runner.RunRequest(self.root / name,
            lv.RunIdentity("simulator", "1" * 40, "2" * 40),
            Config(mode=0, edge=1, level_code=1600, auto_timeout_ms=100),
            **options)

    def start(self, request=None, **peer_options):
        peer = Peer(**peer_options)
        with patch.object(runner, "_SyntheticSerial", return_value=peer):
            handle = runner.run_synthetic(request or self.request())
            self.handles.append(handle)
            # Constructor is asynchronous; retain patch until factory called.
            self.until(lambda: peer.is_open or not handle.snapshot().owner_alive)
        return handle, peer

    def until(self, predicate, seconds=5):
        end = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= end:
                self.fail("bounded fixture condition not reached")
            time.sleep(0.001)

    def finish(self, handle, seconds=6):
        end = time.monotonic() + seconds
        result = handle.wait(100)
        while result.owner_alive and time.monotonic() < end:
            result = handle.wait(100)
        self.assertFalse(result.owner_alive, result)
        return result

    def body(self, handle, number=0):
        return json.loads((Path(handle.snapshot().directory) / f"attempt-{number:02d}.json").read_bytes())["body"]

    def test_real_clock_twenty_plus_warmup_exact_bytes_reopen_no_release(self):
        before = runner.monotonic_ms()
        handle, peer = self.start(signal=False)
        result = self.finish(handle)
        self.assertIs(result.status, runner.Status.COMPLETE)
        self.assertEqual((result.warmup_completed, result.series_completed, result.matched_stops), (True, 20, 21))
        self.assertTrue(result.transport_closed)
        self.assertFalse(result.physical_verified)
        self.assertGreaterEqual(result.elapsed_ms, 2100)
        self.assertLess(result.elapsed_ms, 6000)
        self.assertEqual(len(peer.owner_ids), 1)
        self.assertNotIn(threading.get_ident(), peer.owner_ids)
        tx = packets(bytes(peer.tx))
        self.assertEqual([f.message_type for f in tx], [1, 2, 3] + [4, 5] * 21)
        self.assertEqual(len(set(f.request_id for f in tx)), 45)
        index = lv.verify_run(result.directory)
        self.assertEqual((index["state"], index["artifact_integrity"], index["prior_writer_outcome"]),
                         ("sealed_unverified", "RECOVERED_VERIFIED", "UNKNOWN"))
        digests, window_ids, generations = set(), set(), set()
        for number in range(21):
            body = self.body(handle, number)
            capture = recording.load(Path(result.directory) / f"attempt-{number:02d}.mtcap")
            begin = body["accepted"]["begin"]["decoded"]
            expected = [(73 * i + 19 * begin["window_id"] + 7 * begin["generation"]) % 4096 for i in range(2048)]
            expected[511], expected[1023] = 0, 4095
            self.assertEqual(capture.samples, tuple(expected))
            raw = struct.pack("<2048H", *expected)
            self.assertEqual(zlib.crc32(raw), body["accepted"]["end"]["decoded"]["samples_crc32"])
            self.assertEqual(body["accepted"]["config"]["decoded"]["auto_timeout_ms"], 100)
            self.assertEqual((begin["trigger_kind"], begin["trigger_index"], begin["edge"], begin["level_code"]), (2, 65535, 1, 1600))
            self.assertEqual(capture.trigger_kind, "auto_timeout")
            self.assertEqual(body["attempt"]["role"], "warmup" if number == 0 else "series")
            self.assertTrue(body["stop_matched"])
            self.assertTrue(all(r["at_ms"] >= before for r in body["journal"]["records"]))
            self.assertEqual(capture.origin, "simulator")
            digests.add(hashlib.sha256(raw).hexdigest())
            window_ids.add(begin["window_id"])
            generations.add(begin["generation"])
        self.assertEqual((len(digests), len(window_ids), len(generations)), (21, 21, 21))
        self.assertEqual(len(list(Path(result.directory).iterdir())), 43)

    def test_trigger_and_exact_partial_transport_preserve_config(self):
        handle, peer = self.start(read_size=97, write_size=3)
        result = self.finish(handle, seconds=10)
        self.assertIs(result.status, runner.Status.COMPLETE)
        self.assertEqual([f.message_type for f in packets(bytes(peer.tx))], [1, 2, 3] + [4, 5] * 21)
        for number in range(21):
            body = self.body(handle, number)
            capture = recording.load(Path(result.directory) / f"attempt-{number:02d}.mtcap")
            self.assertEqual((capture.trigger_kind, capture.trigger_index, capture.samples[0]), ("signal", 0, 1599))
            self.assertTrue(body["stop_matched"])

    def test_device_entrypoint_rejects_all_evidence_before_any_effect(self):
        evil = SimpleNamespace(__str__=lambda: self.fail("must not inspect device arguments"))
        with patch.object(runner, "SerialTransport", side_effect=AssertionError("no constructor")), \
             patch.object(runner, "_SyntheticSerial", side_effect=AssertionError("no factory")), \
             patch.object(Path, "lstat", side_effect=AssertionError("no filesystem")):
            for values in ({}, {"port": "COM17"}, {"approved": True},
                           {"evidence": evil}, {"identity": lv.RunIdentity("device", "1" * 40, "2" * 40)}):
                with self.subTest(values=tuple(values)):
                    with self.assertRaises(runner.AdmissionError):
                        runner.run_device(**values)

    def test_invalid_inputs_and_tbd_application_identity_rejected_without_worker(self):
        original = self.request()
        for changes in ({"identity": replace(original.identity, origin="device")},
                        {"identity": replace(original.identity, application_commit="TBD")},
                        {"config": Config(mode=1)}, {"limits": {}},
                        {"directory": "bad\npath"}, {"directory": "../elsewhere"},
                        {"directory": "CON"}, {"directory": "thing:stream"},
                        {"directory": "bad\ud800"}, {"directory": "x" * 1025}):
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises((runner.RunnerError, ValueError)):
                    replace(original, **changes)
        for field in runner.Limits.__dataclass_fields__:
            for invalid in (True, 0, -1, float("nan"), 10**20):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(runner.RunnerError):
                        runner.Limits(**{field: invalid})

    def test_existing_folder_and_parent_symlink_are_not_followed(self):
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "keep"
        marker.write_bytes(b"original")
        link = self.root / "link"
        link.symlink_to(existing, target_is_directory=True)
        for directory in (existing, link / "run"):
            with self.subTest(directory=directory):
                handle, peer = self.start(replace(self.request(), directory=directory))
                result = self.finish(handle)
                self.assertIs(result.status, runner.Status.FAILED)
                self.assertFalse(result.folder_created)
                self.assertEqual(peer.tx, b"")
        self.assertEqual(marker.read_bytes(), b"original")
        self.assertFalse((existing / "run").exists())

    def test_reparse_directory_rejected(self):
        info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400,
                               st_dev=1, st_ino=2)
        with patch.object(Path, "lstat", return_value=info):
            with self.assertRaises(runner.RunnerError):
                runner._stat_directory(self.root)

    def test_faults_retain_evidence_no_replacement_arm(self):
        faults = ("caps", "config", "arm_ack", "unknown_arm", "stop_ack", "no_begin",
                  "partial", "duplicate", "sample_crc", "frame_crc", "stop_timeout")
        for fault in faults:
            with self.subTest(fault=fault):
                limits = runner.Limits(attempt_ms=300 if fault == "no_begin" else 3000)
                handle, peer = self.start(self.request(fault, limits=limits), fault=fault)
                result = self.finish(handle)
                self.assertIs(result.status, runner.Status.FAILED)
                self.assertLessEqual(peer.arm_count, 1)
                self.assertNotIn(7, [f.message_type for f in packets(bytes(peer.tx))])
                self.assertEqual(result.series_completed, 0)
                body = self.body(handle)
                self.assertNotEqual(body["attempt"]["outcome"], "completed")
                self.assertIsNotNone(body["journal"])
                if fault == "partial":
                    self.assertGreater(len(body["incomplete"]["fragments"]), 0)
                    self.assertTrue(body["incomplete"]["missing_ranges"])
                if fault in ("caps", "config", "no_begin", "arm_ack"):
                    self.assertIsNone(body["accepted"]["begin"])
                if fault == "duplicate":
                    self.assertGreater(body["journal"]["repeated_data_blocks"], 0)
                if fault == "sample_crc":
                    self.assertEqual(body["incomplete"]["reason"], "CRC")

    def test_failure_at_each_planned_attempt_is_never_replaced(self):
        for failed in range(21):
            with self.subTest(failed=failed):
                handle, peer = self.start(self.request(f"fault-{failed}"), fault="sample_crc", fail_attempt=failed)
                result = self.finish(handle)
                self.assertIs(result.status, runner.Status.FAILED)
                self.assertEqual(peer.arm_count, failed + 1)
                self.assertEqual(result.series_completed, max(0, failed - 1))
                self.assertEqual(self.body(handle, failed)["incomplete"]["reason"], "CRC")
                index = lv.verify_run(result.directory)
                self.assertEqual(sum(a["outcome"] == "completed" for a in index["attempts"]), failed)

    def test_stop_before_disk_and_disk_full_keeps_capture(self):
        original = recording.atomic_write
        peers = []
        def fail(path, data, **options):
            if Path(path).suffix == ".mtcap":
                self.assertEqual(peers[0].device.snapshot().state, STOPPED)
                raise OSError(errno.ENOSPC, "private disk detail")
            return original(path, data, **options)
        peer = Peer()
        peers.append(peer)
        with patch.object(runner, "_SyntheticSerial", return_value=peer), \
             patch.object(recording, "atomic_write", side_effect=fail):
            handle = runner.run_synthetic(self.request())
            self.handles.append(handle)
            result = self.finish(handle)
        self.assertIs(result.status, runner.Status.FAILED)
        self.assertTrue(result.retained_capture)
        self.assertEqual((peer.arm_count, result.matched_stops), (1, 1))
        self.assertNotIn("private", str(result))
        self.assertEqual(lv.verify_run(result.directory)["attempts"][0]["outcome"], "pending")
        evidence = handle.retained_evidence()
        self.assertFalse(evidence.durable)
        self.assertEqual(len(evidence.capture.samples), 2048)
        self.assertIsNone(evidence.incomplete)
        self.assertLessEqual(len(evidence.index_json), lv.MAX_INDEX_BYTES)
        self.assertLessEqual(len(evidence.journal_json), lv.MAX_SIDECAR_BYTES)
        changed = json.loads(evidence.index_json)
        changed["state"] = "forged"
        self.assertNotEqual(json.loads(handle.retained_evidence().index_json)["state"], "forged")
        with self.assertRaises(AttributeError):
            evidence.capture.samples = ()

    def test_blocked_intent_fsync_cancel_has_no_late_arm(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = os.fsync
        calls = 0
        def block(fd):
            nonlocal calls
            calls += 1
            if calls == 2:  # start index, then first durable ARM intent
                entered.set()
                if not release.wait(5):
                    raise AssertionError("fixture release missing")
            return original(fd)
        peer = Peer()
        with patch.object(runner, "_SyntheticSerial", return_value=peer), patch.object(os, "fsync", side_effect=block):
            handle = runner.run_synthetic(self.request())
            self.handles.append(handle)
            self.assertTrue(entered.wait(2))
            before = time.monotonic()
            pending = handle.retire(20)
            self.assertLess(time.monotonic() - before, 0.15)
            self.assertIs(pending.status, runner.Status.PENDING)
            self.assertEqual((pending.writer_operation, pending.writer_settlement), ("ARM_INTENT", "ENTERED"))
            self.assertTrue(pending.owner_alive)
            self.assertFalse(pending.transport_closed)
            with self.assertRaises(runner.RunnerError):
                runner.run_synthetic(self.request("must-not-start"))
            self.assertEqual(peer.arm_count, 0)
            release.set()
            result = self.finish(handle)
        self.assertIs(result.status, runner.Status.CANCELLED)
        self.assertEqual(peer.arm_count, 0)
        self.assertFalse((self.root / "must-not-start").exists())
        self.assertEqual(self.body(handle)["attempt"]["outcome"], "cancelled")

    def test_cancel_between_arm_queue_and_first_write(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = lv.LVCollector.arm_next
        def block(c):
            original(c)
            entered.set()
            if not release.wait(5):
                raise AssertionError("fixture release missing")
        with patch.object(lv.LVCollector, "arm_next", block):
            handle, peer = self.start()
            self.assertTrue(entered.wait(2))
            handle.stop()
            release.set()
            result = self.finish(handle)
        self.assertIs(result.status, runner.Status.CANCELLED)
        self.assertEqual(peer.arm_count, 0)

    def test_cancel_during_blocked_persistence_retains_owner_and_stops_plan(self):
        for target in (0, 20):
            with self.subTest(target=target):
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                original = recording.atomic_write
                def block(path, data, **options):
                    if Path(path).name == f"attempt-{target:02d}.mtcap":
                        entered.set()
                        if not release.wait(5):
                            raise AssertionError("fixture release missing")
                    return original(path, data, **options)
                with patch.object(recording, "atomic_write", side_effect=block):
                    handle, peer = self.start(self.request(f"blocked-{target}"))
                    self.assertTrue(entered.wait(4))
                    pending = handle.retire(10)
                    self.assertIs(pending.status, runner.Status.PENDING)
                    self.assertTrue(pending.retained_capture)
                    with self.assertRaises(runner.RunnerError):
                        handle.retained_evidence()
                    self.assertEqual(peer.device.snapshot().state, STOPPED)
                    release.set()
                    result = self.finish(handle)
                self.assertIs(result.status, runner.Status.CANCELLED)
                self.assertEqual(peer.arm_count, target + 1)
                self.assertEqual(result.writer_settlement, "RETURNED_VERIFIED")
                index = lv.verify_run(result.directory)
                self.assertEqual(index["prior_writer_outcome"], "UNKNOWN")
                self.assertEqual(index["state"], "sealed_unverified" if target == 20 else "cancelled")

    def test_storage_deadline_after_slow_success_fences_next_arm(self):
        original = recording.atomic_write
        def slow(path, data, **options):
            if Path(path).name == "attempt-00.mtcap":
                time.sleep(0.15)
            return original(path, data, **options)
        limits = runner.Limits(storage_ms=100)
        with patch.object(recording, "atomic_write", side_effect=slow):
            handle, peer = self.start(self.request(limits=limits))
            result = self.finish(handle)
        self.assertIs(result.status, runner.Status.FAILED)
        self.assertEqual(result.reason, "STORAGE_DEADLINE")
        self.assertEqual(peer.arm_count, 1)
        self.assertTrue(result.warmup_completed)

    def test_attempt_deadline_and_run_deadline_are_monotonic(self):
        for limits, reason in ((runner.Limits(attempt_ms=30), "ATTEMPT_DEADLINE"),
                               (runner.Limits(run_ms=40), "RUN_DEADLINE")):
            with self.subTest(reason=reason):
                handle, peer = self.start(self.request(reason, limits=limits), signal=False)
                result = self.finish(handle)
                self.assertIs(result.status, runner.Status.FAILED)
                self.assertEqual(result.reason, reason)
                self.assertEqual(peer.arm_count, 1)
                self.assertLess(result.elapsed_ms, 1500)

    def test_close_hang_is_pending_not_closed_or_replaceable(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        peer = Peer(fault="sample_crc")
        original = peer.close
        def close():
            entered.set()
            if not release.wait(5):
                raise AssertionError("fixture release missing")
            original()
        peer.close = close
        with patch.object(runner, "_SyntheticSerial", return_value=peer):
            handle = runner.run_synthetic(self.request())
            self.handles.append(handle)
            self.assertTrue(entered.wait(3))
            result = handle.wait(10)
            self.assertIs(result.status, runner.Status.PENDING)
            self.assertTrue(result.owner_alive)
            self.assertFalse(result.transport_closed)
            with self.assertRaises(runner.RunnerError):
                handle.retained_evidence()
            with self.assertRaises(runner.RunnerError):
                runner.run_synthetic(self.request("replacement"))
            release.set()
            result = self.finish(handle)
        self.assertTrue(result.transport_closed)
        self.assertFalse(result.owner_alive)

    def test_corrupt_readback_stale_index_and_existing_sidecar_fail_closed(self):
        for fault in ("readback", "stale", "sidecar"):
            with self.subTest(fault=fault):
                original = recording.atomic_write
                def corrupt(path, data, **options):
                    result = original(path, data, **options)
                    path = Path(path)
                    if path.name == "attempt-00.mtcap":
                        if fault == "readback":
                            path.write_bytes(b"corrupt")
                        elif fault == "stale":
                            (path.parent / "index.json").write_bytes(b"other writer")
                        else:
                            (path.parent / "attempt-00.json").write_bytes(b"existing evidence")
                    return result
                with patch.object(recording, "atomic_write", side_effect=corrupt):
                    handle, peer = self.start(self.request(fault))
                    result = self.finish(handle)
                self.assertIs(result.status, runner.Status.FAILED)
                self.assertEqual(peer.arm_count, 1)
                self.assertTrue(result.retained_capture)
                if fault == "stale":
                    self.assertEqual((Path(result.directory) / "index.json").read_bytes(), b"other writer")
                if fault == "sidecar":
                    self.assertEqual((Path(result.directory) / "attempt-00.json").read_bytes(), b"existing evidence")
                self.assertEqual(len(handle.retained_evidence().capture.samples), 2048)

    def test_public_wait_bounds_and_finished_status_do_not_expire_later(self):
        handle, peer = self.start()
        for value in (True, -1, 1001, float("inf")):
            with self.assertRaises(runner.RunnerError):
                handle.wait(value)
        result = self.finish(handle)
        with patch.object(runner, "monotonic_ms", return_value=runner.monotonic_ms() + runner.MAX_RUN_MS + 1):
            self.assertIs(handle.snapshot().status, runner.Status.COMPLETE)
            self.assertIs(handle.stop().status, runner.Status.COMPLETE)

    def test_import_is_stdlib_only_and_does_not_install_signal_handlers(self):
        code = """
import builtins, signal
old = builtins.__import__
def guarded(name, *args, **kw):
    if name.split('.')[0] in ('serial', 'PySide6', 'numpy', 'pyqtgraph'):
        raise AssertionError('optional dependency imported')
    return old(name, *args, **kw)
builtins.__import__ = guarded
before = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
import motortester.lv_runner
if before != (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)):
    raise AssertionError('import changed signal policy')
"""
        process = subprocess.run([sys.executable] + (["-O"] if sys.flags.optimize else []) + ["-S", "-c", code],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
            capture_output=True, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_blocked_initial_index_cancellation_does_not_claim_folder_durability(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = recording.atomic_write
        calls = 0
        def block(path, data, **options):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("fixture release missing")
            return original(path, data, **options)
        peer = Peer()
        with patch.object(runner, "_SyntheticSerial", return_value=peer), \
             patch.object(recording, "atomic_write", side_effect=block):
            handle = runner.run_synthetic(self.request())
            self.handles.append(handle)
            self.assertTrue(entered.wait(2))
            result = handle.retire(10)
            self.assertIs(result.status, runner.Status.PENDING)
            self.assertFalse(result.folder_created)
            self.assertEqual(peer.tx, b"")
            release.set()
            result = self.finish(handle)
        self.assertIs(result.status, runner.Status.CANCELLED)
        self.assertTrue(result.folder_created)
        self.assertEqual(peer.tx, b"")

    def test_no_poll_required_for_late_intent_deadline_fence(self):
        original = recording.atomic_write
        def delay_intent(path, data, **options):
            if Path(path).name == "index.json":
                body = json.loads(data)["body"]
                if body["attempts"][0]["outcome"] == "pending":
                    time.sleep(0.15)
            return original(path, data, **options)
        limits = runner.Limits(storage_ms=100)
        with patch.object(recording, "atomic_write", side_effect=delay_intent):
            handle, peer = self.start(self.request(limits=limits))
            time.sleep(0.4)  # No handle poll or wait while its deadline passes.
            result = self.finish(handle)
        self.assertIs(result.status, runner.Status.FAILED)
        self.assertEqual(result.reason, "STORAGE_DEADLINE")
        self.assertEqual(peer.arm_count, 0)

    def test_cancel_at_wait_partial_end_and_matching_stop_boundaries(self):
        for stage in ("WAIT", "PARTIAL", "END", "STOP_ACK"):
            with self.subTest(stage=stage):
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                original = lv.LVCollector.pump
                blocked = False
                def pump(c):
                    nonlocal blocked
                    result = original(c)
                    s = c.runtime.snapshot()
                    match = ((stage == "WAIT" and c.state is lv.State.ACTIVE and s.generation != 0 and s.window_id == 0) or
                             (stage == "PARTIAL" and c.state is lv.State.ACTIVE and s.window_id != 0) or
                             (stage == "END" and c.state is lv.State.STOPPING) or
                             (stage == "STOP_ACK" and c.state is lv.State.SAVEABLE))
                    if match and not blocked:
                        blocked = True
                        entered.set()
                        if not release.wait(5):
                            raise AssertionError("fixture release missing")
                    return result
                with patch.object(lv.LVCollector, "pump", pump):
                    request = self.request(stage)
                    if stage == "WAIT":
                        request = replace(request, config=replace(request.config, auto_timeout_ms=60000))
                    handle, peer = self.start(request, signal=stage != "WAIT")
                    self.assertTrue(entered.wait(3))
                    pending = handle.retire(10)
                    self.assertIs(pending.status, runner.Status.PENDING)
                    release.set()
                    result = self.finish(handle)
                self.assertIs(result.status, runner.Status.CANCELLED)
                self.assertEqual(peer.arm_count, 1)
                body = self.body(handle)
                self.assertEqual(body["attempt"]["outcome"], "cancelled")
                self.assertTrue(body["stop_matched"])
                self.assertEqual(result.last_stop_generation, result.current_arm_request_id)

    def test_completed_windows_are_not_retained_as_a_twenty_window_buffer(self):
        captures = []
        original = AcquisitionRuntime.acknowledge
        def observe_ack(runtime, capture):
            self.assertIs(capture, runtime.pending_capture)
            s = runtime.snapshot()
            self.assertFalse(s.stop_barrier or s.window_barrier)
            self.assertEqual(s.phase.value, "READY")
            captures.append(weakref.ref(capture))
            return original(runtime, capture)
        tracemalloc.start()
        try:
            with patch.object(AcquisitionRuntime, "acknowledge", observe_ack):
                handle, peer = self.start()
                result = self.finish(handle, seconds=30)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertIs(result.status, runner.Status.COMPLETE)
        self.assertEqual(len(captures), 21)
        self.assertTrue(all(reference() is None for reference in captures))
        self.assertLess(peak, 64 * 1024 * 1024)
        evidence = handle.retained_evidence()
        self.assertIsNone(evidence.capture)
        self.assertIsNone(evidence.incomplete)
        self.assertEqual(json.loads(evidence.journal_json)["records"], [])

    def test_retained_evidence_absent_without_collector_and_independently_bounded(self):
        (self.root / "old").mkdir()
        handle, peer = self.start(self.request("old"))
        self.finish(handle)
        self.assertIsNone(handle.retained_evidence())
        self.assertEqual(runner._bounded_json("é", 4), b'"\xc3\xa9"')
        with self.assertRaises(runner.RunnerError):
            runner._bounded_json("é", 3)
        with self.assertRaises(runner.RunnerError):
            runner._bounded_json({"records": ["x" * 8192] * 1024}, lv.MAX_SIDECAR_BYTES)

    def test_cli_rejects_non_main_thread_before_creating_owner(self):
        directory = self.root / "wrong-thread"
        results = []
        def invoke():
            try:
                runner.main([str(directory), "--application-commit", "1" * 40,
                             "--application-tree", "2" * 40])
            except SystemExit as exc:
                results.append(exc.code)
        with patch.object(sys, "stderr"):
            thread = threading.Thread(target=invoke)
            thread.start()
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [2])
        self.assertFalse(directory.exists())

    @unittest.skipIf(os.name == "nt", "POSIX process signal delivery; Windows is separately unverified")
    def test_cli_sigint_and_sigterm_cancel_without_claiming_success(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                directory = self.root / f"signal-{sig}"
                process = subprocess.Popen([sys.executable] + (["-O"] if sys.flags.optimize else []) + ["-m", "motortester.lv_runner", str(directory),
                    "--application-commit", "1" * 40, "--application-tree", "2" * 40,
                    "--timeout-ms", "60000"],
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.addCleanup(lambda p=process: p.kill() if p.poll() is None else None)
                def armed_intent():
                    try:
                        return json.loads((directory / "index.json").read_bytes())["body"]["attempts"][0]["intent_committed"]
                    except (OSError, ValueError):
                        return False
                self.until(armed_intent)
                process.send_signal(sig)
                stdout, stderr = process.communicate(timeout=4)
                self.assertEqual(process.returncode, 2, stderr)
                self.assertIn(b"CANCELLED", stdout)
                self.assertIn(b"SIGNAL_STOP", stdout)
                self.assertNotIn(b"COMPLETE", stdout)
                index = lv.verify_run(directory)
                self.assertEqual(index["state"], "cancelled")


class FenceTests(unittest.TestCase):
    def make(self, count=None):
        fence = runner._Fence(runner.Limits())
        calls = []
        serial = SimpleNamespace(write=lambda data: calls.append(data) or (len(data) if count is None else count),
                                 read=lambda size: b"")
        transport = runner._FencedTransport(serial, fence)
        transport.stop_identity = lambda: (1, 9)
        return transport, fence, calls

    def test_every_unsent_arm_prefix_suffix_fenced(self):
        arm = encode_frame(Frame(4, 1, 4, 4, struct.pack("<I", 1)))
        stop = encode_frame(Frame(5, 1, 5, 9, struct.pack("<I", 4)))
        for count in range(len(arm)):
            with self.subTest(accepted=count):
                transport, fence, calls = self.make(count)
                self.assertEqual(transport.write(arm), count)
                fence.stop("OPERATOR_STOP")
                with self.assertRaises(runner.RunnerError):
                    transport.write(arm[count:])
                with self.assertRaises(runner.RunnerError):
                    transport.write(stop)
                self.assertEqual(calls, [arm])

    def test_safe_matching_stop_only_and_no_release(self):
        stop = encode_frame(Frame(5, 1, 5, 9, struct.pack("<I", 4)))
        transport, fence, calls = self.make()
        fence.stop("OPERATOR_STOP")
        self.assertEqual(transport.write(stop), len(stop))
        self.assertEqual(calls, [stop])
        for frame in (Frame(5, 1, 6, 10, struct.pack("<I", 4)),
                      Frame(4, 1, 6, 10, struct.pack("<I", 1)),
                      Frame(7, 1, 6, 10, b"\0" * 12)):
            transport, fence, calls = self.make()
            fence.stop("OPERATOR_STOP")
            with self.assertRaises(runner.RunnerError):
                transport.write(encode_frame(frame))
            self.assertEqual(calls, [])

    def test_admitted_backend_call_is_not_claimed_cancelled(self):
        transport, fence, calls = self.make()
        entered, release = threading.Event(), threading.Event()
        arm = encode_frame(Frame(4, 1, 4, 4, struct.pack("<I", 1)))
        returned = []
        def write(data):
            entered.set()
            release.wait(2)
            calls.append(data)
            return len(data)
        transport.serial.write = write
        owner = threading.Thread(target=lambda: returned.append(transport.write(arm)))
        owner.start()
        self.assertTrue(entered.wait(1))
        fence.stop("OPERATOR_STOP")
        self.assertTrue(owner.is_alive())
        release.set()
        owner.join(1)
        self.assertFalse(owner.is_alive())
        self.assertEqual(returned, [len(arm)])
        with self.assertRaises(runner.RunnerError):
            transport.write(arm)
        self.assertEqual(calls, [arm])

    def test_output_and_transport_bounds_checked_before_effect(self):
        transport, fence, calls = self.make()
        transport.bytes = runner.MAX_WIRE_BYTES
        with self.assertRaises(runner.RunnerError):
            transport.read(1)
        transport, fence, calls = self.make()
        transport.calls = runner.MAX_IO_CALLS
        with self.assertRaises(runner.RunnerError):
            transport.read(1)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

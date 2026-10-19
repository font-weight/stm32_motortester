"""Finite synthetic encoded-byte peer + real journal tests; no device/Qt needed."""
from dataclasses import replace
import hashlib
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from motortester.stream_controller import StreamController, render_display, RECENT_SAMPLES
from motortester import stream_recording as storage
from motortester import stream_controller as module
from motortester.stream_session import SessionLimits
from motortester.stream_view import EventConfig, EventDetector

CONFIG = EventConfig(1000, 500, "above", 8, 12, 3, 0, 256, 0, 4095)


def encoded(kind, payload, nonce, sequence, request=0):
    """Independent finite test encoder; no production codecs or normalized inputs."""
    raw = struct.pack("<BBBBHHQII", 0, 2, kind, 0, len(payload), 0, nonce, sequence, request) + payload
    raw += struct.pack("<I", zlib.crc32(raw))
    out = bytearray()
    for run in raw.split(b"\0"):
        while len(run) >= 254:
            out += b"\xff" + run[:254]
            run = run[254:]
        out += bytes((len(run) + 1,)) + run
    return bytes(out) + b"\0"


def decoded(frame):
    raw, at = bytearray(), 0
    frame = frame[:-1]
    while at < len(frame):
        n = frame[at]; at += 1
        if not n or at + n - 1 > len(frame):
            raise AssertionError("Bad test host COBS")
        raw += frame[at:at + n - 1]; at += n - 1
        if n != 255 and at < len(frame):
            raw += b"\0"
    if zlib.crc32(raw[:-4]) != struct.unpack("<I", raw[-4:])[0]:
        raise AssertionError("Bad test host CRC")
    header = struct.unpack("<BBBBHHQII", raw[:24])
    return header[2], header[6], header[7], header[8], bytes(raw[24:-4])


def waveform(blocks=1):
    # One threshold context per 128 samples, with an exact known plateau/tail.
    values = ([100] * 20 + [2000] * 8 + [800] * 9 + [100] * 91) * blocks
    return struct.pack("<" + "H" * len(values), *values)


class EncodedPeer:
    """Synthetic FIFO assertion only; byte read/write callbacks do no I/O.

    Tests may enqueue finite chunks and hold responses. This is not a production
    transport, firmware model, isolation proof, acquisition clock or USB claim.
    """
    def __init__(self, *, partitions=(544,), accepted_prefix=544):
        self.lock = threading.Lock()
        self.incoming = bytearray()
        self.tx = bytearray()
        self.partitions = partitions
        self.partition_at = 0
        self.accepted_prefix = accepted_prefix
        self.nonce = 0
        self.sequence = 1
        self.generation = self.last_data = self.stop_request = 0
        self.raw = b""
        self.commands = []
        self.auto_start = self.auto_stop = True
        self.start_waiting = None
        self.stop_waiting = None
        self.unknown_write = False

    def _rx(self, kind, payload, request=0):
        frame = encoded(kind, payload, self.nonce, self.sequence, request)
        self.sequence += 1
        self.incoming += frame
        return frame

    def read(self, maximum):
        with self.lock:
            n = min(maximum, self.partitions[self.partition_at % len(self.partitions)], len(self.incoming))
            self.partition_at += 1
            out = bytes(self.incoming[:n]); del self.incoming[:n]
            return out

    def write(self, data):
        with self.lock:
            if self.unknown_write:
                return None
            n = min(len(data), self.accepted_prefix)
            self.tx += data[:n]
            while b"\0" in self.tx:
                end = self.tx.index(0) + 1
                frame = bytes(self.tx[:end]); del self.tx[:end]
                if frame == b"\0":
                    continue
                kind, nonce, sequence, request, payload = decoded(frame)
                self.commands.append((kind, nonce, sequence, request))
                if kind == 1:
                    self.nonce = struct.unpack("<Q", payload)[0]
                    self.sequence, self.generation, self.last_data, self.raw = 1, 0, 0, b""
                    self._rx(129, struct.pack("<BBBBHHII", 0, 2, 1, 12, 512, 128, 0x1234, 1), request)
                elif kind == 2:
                    self._rx(130, struct.pack("<IHBBIIHHI", 1, 1, 0, 12, 50000, 1, 128, 0, 1), request)
                elif kind == 8:
                    self.generation = request
                    self.start_waiting = request
                    if self.auto_start:
                        self._start()
                elif kind == 5:
                    self.stop_request = self.stop_waiting = request
                    if self.auto_stop:
                        self._end()
            return n

    def _start(self):
        self._rx(136, struct.pack("<IIQ", self.generation, 1, 0), self.start_waiting)
        self.start_waiting = None

    def start(self):
        with self.lock:
            self._start()

    def _end(self, *, reason=0, flags=0, produced=None, crc=None, acknowledge=True):
        end_sequence = self.sequence
        end = len(self.raw) // 2
        self._rx(153, struct.pack("<IIQQIIHHI", self.generation, 1, end,
            end if produced is None else produced, self.last_data,
            zlib.crc32(self.raw) if crc is None else crc, reason, flags, self.stop_request))
        if acknowledge:
            self._rx(133, struct.pack("<IQII", self.generation, end, end_sequence, flags), self.stop_request)
        self.stop_waiting = None

    def end(self, **kwargs):
        with self.lock:
            self._end(**kwargs)

    def data(self, raw=None, *, split=None):
        raw = waveform() if raw is None else raw
        with self.lock:
            first = len(self.raw) // 2
            frame = encoded(152, struct.pack("<IIQHH", self.generation, 1, first, len(raw) // 2, 0) + raw,
                            self.nonce, self.sequence)
            self.last_data = self.sequence
            self.sequence += 1
            self.raw += raw
            if split is None:
                self.incoming += frame
                return frame
            self.incoming += frame[:split]
            return frame[split:]

    def enqueue(self, data):
        with self.lock:
            self.incoming += data


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Bounded worker wait timed out")
        time.sleep(.001)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stream-flow-")
        self.path = str(Path(self.temp.name) / "joined.mtstream")
        self.controllers = []
        self.releases = []
        self.now = 0

    def tearDown(self):
        for event in self.releases:
            event.set()
        for controller in self.controllers:
            controller.close()
            if controller._threaded:
                self.assertTrue(controller.join(4), "worker remained unresolved after test release")
            else:
                for _ in range(5):
                    controller.service(controller._clock())
                    controller.execute_disk_once()
                self.assertFalse(controller.snapshot().worker_unresolved)
        self.temp.cleanup()

    def make(self, peer=None, **kwargs):
        peer = peer or EncodedPeer()
        options = {"threaded": False, **kwargs}
        if not options["threaded"]:
            options.setdefault("clock", lambda: self.now)
        controller = StreamController(peer, event_config=CONFIG, **options)
        self.controllers.append(controller)
        return controller, peer

    def step(self, c, *, disk=True, display=False):
        c.service(c._clock())
        if disk:
            c.execute_disk_once()
        if display:
            c.execute_display_once()

    def until(self, c, predicate, *, disk=True, limit=3000):
        for _ in range(limit):
            if predicate(c.snapshot()):
                return c.snapshot()
            self.step(c, disk=disk)
        self.fail("Finite scheduler exhausted: " + repr(c.snapshot()))

    def preview(self, c):
        self.assertTrue(c.preview())
        self.until(c, lambda s: s.phase == "preview")

    def record(self, c):
        self.assertTrue(c.record(self.path))
        self.until(c, lambda s: bool(s.bridge and s.bridge.session and s.bridge.session.source_started))

    def finish(self, c):
        c.stop()
        return self.until(c, lambda s: s.phase == "saved" and s.can_preview)

    def raw(self, recording):
        return b"".join(b.raw_u16le for b in recording.iter_raw())

    def test_direct_record_actual_start_ack_and_saved_fresh_reader(self):
        c, p = self.make()
        p.auto_start = False
        self.assertTrue(c.record(self.path))
        self.until(c, lambda s: bool(p.start_waiting))
        self.assertIsNone(c.snapshot().bridge.session.selected_start_sample_index)
        self.assertTrue(Path(self.path).exists())
        p.start()
        self.until(c, lambda s: s.bridge.session.selected_start_sample_index == 0)
        for _ in range(6):
            p.data(); self.until(c, lambda s: s.validated_samples == len(p.raw) // 2)
        status = self.finish(c)
        fresh = storage.recover_attempt(self.path).recording
        self.assertEqual(fresh.first_sample_index, 0)
        self.assertEqual(self.raw(fresh), p.raw)
        self.assertEqual(fresh.identity, status.bridge.identity)
        self.assertEqual(status.bridge.storage.selected_sha256, hashlib.sha256(p.raw).hexdigest())
        self.assertTrue(fresh.terminal.clean_stop_reported)
        self.assertTrue(status.bridge.session.clean_completion_reported)
        self.assertEqual(fresh.terminal, status.bridge.session.source_end.terminal)
        self.assertEqual(status.validated_blocks, 6)

    def test_preview_record_nonzero_boundary_partial_frame_and_partition_invariance(self):
        for parts in ((544,), (1, 7, 31, 2, 89)):
            with self.subTest(parts=parts):
                self.path = str(Path(self.temp.name) / ("part" + str(parts[0]) + ".mtstream"))
                c, p = self.make(EncodedPeer(partitions=parts, accepted_prefix=7))
                self.preview(c)
                p.data(); self.until(c, lambda s: s.validated_samples == 128)
                # A frame received in part before readiness is eligible only
                # when its last bytes validate after owner acceptance of prepare.
                suffix = p.data(split=100)
                for _ in range(200):
                    self.step(c)
                self.assertEqual(c.snapshot().validated_samples, 128)
                self.assertTrue(c.record(self.path))
                self.until(c, lambda s: bool(s.bridge.storage and s.bridge.storage.operation == "prepare" and not s.bridge.mailbox.result_pending))
                self.assertIsNone(c.snapshot().bridge.session.selected_start_sample_index)
                p.enqueue(suffix)
                self.until(c, lambda s: s.validated_samples == 256)
                self.assertEqual(c.snapshot().bridge.session.selected_start_sample_index, 128)
                p.data(); self.until(c, lambda s: s.validated_samples == 384)
                status = self.finish(c)
                fresh = storage.recover_attempt(self.path).recording
                self.assertEqual((fresh.first_sample_index, fresh.end_sample_index), (128, 384))
                self.assertEqual(self.raw(fresh), p.raw[256:])
                self.assertEqual(status.bridge.storage.selected_sha256, hashlib.sha256(p.raw[256:]).hexdigest())
                live = c._detector.retained_events
                offline = EventDetector(CONFIG, 4096)
                for block in fresh.iter_raw():
                    offline.feed(block)
                offline.finish()
                self.assertEqual([e.trigger_index for e in offline.retained_events], [e.trigger_index for e in live if e.trigger_index >= 128])

    def test_empty_is_bound_but_stop_during_prepare_is_unbound(self):
        c, p = self.make()
        self.record(c)
        status = self.finish(c)
        fresh = storage.recover_attempt(self.path).recording
        self.assertEqual((fresh.first_sample_index, fresh.end_sample_index), (0, 0))
        self.assertTrue(status.bridge.session.clean_completion_reported)
        self.path = str(Path(self.temp.name) / "unbound.mtstream")
        c, p = self.make()
        c.record(self.path)
        self.until(c, lambda s: s.bridge is not None and s.bridge.storage_job_id is not None, disk=False)
        c.stop(); self.step(c, disk=False)
        self.until(c, lambda s: bool(s.bridge.session.finalized))
        report = storage.recover_attempt(self.path)
        self.assertIsNone(report.recording)
        self.assertIsNone(report.selected_start)
        self.assertIsNotNone(report.unbound_outcome)
        self.assertFalse(any(command[0] == 8 for command in p.commands))
        self.until(c, lambda s: s.can_open and s.can_preview)
        old = c.snapshot().bridge.identity
        self.assertTrue(c.preview())
        self.until(c, lambda s: s.phase == "preview" and s.bridge.identity != old)

    def test_reserved_stop_command_slot_and_no_automatic_restart(self):
        c, p = self.make()
        self.assertTrue(c.preview())
        self.assertFalse(c.record(self.path))
        self.assertTrue(c.stop())
        self.step(c)
        self.assertEqual(p.commands, [])
        self.preview(c)
        first_identity = c.snapshot().bridge.identity
        c.stop(); c.stop()
        self.until(c, lambda s: s.phase == "preview stopped")
        c.stop(); self.step(c)
        self.assertEqual(sum(x[0] == 5 for x in p.commands), 1)
        self.assertTrue(c.preview())
        self.until(c, lambda s: s.phase == "preview" and s.bridge.identity != first_identity)
        self.assertNotEqual(c.snapshot().bridge.identity.session_id, first_identity.session_id)

    def test_unknown_write_permanently_fences_restart_and_preserves_request(self):
        c, p = self.make()
        self.record(c)
        p.unknown_write = True
        c.stop(); self.step(c)
        status = c.snapshot()
        self.assertEqual(status.bridge.session.first_failure.operation, "STOP")
        self.assertEqual(status.bridge.session.first_failure.request_id, 4)
        self.assertFalse(c.preview())
        self.assertFalse(c.record(self.path + "new"))
        self.now = 5000
        self.until(c, lambda s: s.bridge.session.finalized or s.bridge.session.writer_fenced)
        fresh = storage.recover_attempt(self.path).recording
        self.assertEqual(fresh.terminal.failure, c.snapshot().bridge.session.first_failure)
        self.assertFalse(fresh.terminal.clean_stop_reported)

    def test_existing_destination_is_not_overwritten_and_no_start(self):
        Path(self.path).write_bytes(b"preserve")
        c, p = self.make()
        c.record(self.path)
        self.until(c, lambda s: bool(s.bridge and s.bridge.session and s.bridge.session.first_failure))
        self.assertEqual(Path(self.path).read_bytes(), b"preserve")
        self.assertFalse(any(command[0] == 8 for command in p.commands))
        self.assertFalse(c.snapshot().can_preview)

    def test_latest_display_slot_keeps_all_raw_and_detector_updates(self):
        c, p = self.make()
        self.record(c)
        received = []
        self.assertTrue(c.bind_display(c.snapshot().revision, received.append))
        for n in range(600):
            p.data(); self.now += 2
            self.until(c, lambda s: s.validated_blocks == n + 1)
        status = self.finish(c)
        self.assertEqual(status.ring_samples, RECENT_SAMPLES)
        self.assertEqual(status.detector_status.processed_until, 600 * 128)
        self.assertEqual(status.detector_status.next_ordinal, 600)
        self.assertGreater(status.displays_replaced, 0)
        self.assertEqual(received, [])
        self.now += 100; self.step(c, display=True)
        self.assertEqual(len(received), 1)
        self.assertLessEqual(received[0].envelope.point_count, 2048)
        self.assertEqual(received[0].source.raw, p.raw[-RECENT_SAMPLES * 2:])
        fresh = storage.recover_attempt(self.path).recording
        self.assertEqual(self.raw(fresh), p.raw)
        self.assertEqual(fresh.end_sample_index, 600 * 128)

    def test_held_real_prepare_current_time_acceptance_and_close(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = storage.PreparedStreamWriter.prepare
        def held(*args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("test prepare barrier timeout")
            return original(*args, **kwargs)
        with patch.object(storage.PreparedStreamWriter, "prepare", held):
            c, p = self.make(threaded=True)
            c.record(self.path)
            self.assertTrue(entered.wait(4))
            steps = c.snapshot().receiver_steps
            c.stop()
            wait_for(lambda: c.snapshot().receiver_steps > steps and not c.snapshot().stop_pending)
            self.assertFalse(any(command[0] == 8 for command in p.commands))
            self.assertTrue(c.snapshot().worker_unresolved)
            c.close()
            wait_for(lambda: c.snapshot().bridge.closed)
            self.assertFalse(c.join(0))
            self.assertFalse(c.record(self.path + "other"))
            release.set()
            self.assertTrue(c.join(4))
        self.assertFalse(any(command[0] == 8 for command in p.commands))
        self.assertFalse(c.snapshot().bridge.session.clean_completion_reported)


    def test_prepare_result_accepted_after_receive_deadline_cannot_select_R(self):
        c, p = self.make()
        self.preview(c)
        p.data(); self.until(c, lambda s: s.validated_blocks == 1)
        c.record(self.path)
        self.until(c, lambda s: s.bridge.storage_job_id is not None, disk=False)
        c.execute_disk_once()  # Prepared on disk; owner has not accepted readiness.
        self.assertTrue(c.snapshot().bridge.mailbox.operation_id is not None)
        self.now = 1001
        self.step(c, disk=False)  # ticks before acceptance; DATA deadline wins.
        self.assertIsNone(c.snapshot().bridge.session.selected_start_sample_index)
        self.assertEqual(c.snapshot().bridge.endpoint_first_fault.code, "data_timeout")
        self.now = 4001
        self.until(c, lambda s: s.bridge.session.finalized)
        report = storage.recover_attempt(self.path)
        self.assertIsNone(report.recording)
        self.assertIsNotNone(report.unbound_outcome.failure)

    def test_held_real_append_bounded_queue_and_Stop_without_disk_progress(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = storage.StreamWriter.append
        def held(writer, *args, **kwargs):
            entered.set()
            if not release.wait(8):
                raise RuntimeError("append barrier timeout")
            return original(writer, *args, **kwargs)
        with patch.object(storage.StreamWriter, "append", held):
            c, p = self.make(threaded=True)
            c.record(self.path)
            wait_for(lambda: c.snapshot().phase == "recording")
            p.data(); self.assertTrue(entered.wait(4))
            for count in range(2, 258):
                p.data()
                wait_for(lambda: c.snapshot().validated_blocks == count)
            wait_for(lambda: c.snapshot().bridge.session.stop_acknowledgement is not None)
            status = c.snapshot()
            self.assertEqual(status.bridge.session.raw_items_held, 256)
            self.assertEqual(status.peak_raw_items, 256)
            self.assertEqual(status.bridge.session.raw_bytes_held, 256 * 256)
            self.assertTrue(status.bridge.mailbox.running)
            self.assertFalse(status.bridge.mailbox.result_pending)
            self.assertEqual(status.bridge.session.first_failure.code, "capacity_blocks")
            self.assertEqual(status.detector_status.processed_until, 257 * 128)
            self.assertEqual(status.bridge.session.accepted_end_sample_index, 0)
            c.stop()  # reserved/idempotent even with all raw slots occupied
            wait_for(lambda: not c.snapshot().stop_pending)
            release.set()
            wait_for(lambda: c.snapshot().bridge.session.finalized)
        fresh = storage.recover_attempt(self.path).recording
        self.assertEqual(self.raw(fresh), p.raw[:256 * 256])
        self.assertEqual(fresh.end_sample_index, 256 * 128)
        self.assertEqual(fresh.terminal.failure.code, "capacity_blocks")
        self.assertEqual(fresh.terminal.device_transmitted_end_sample_index, 257 * 128)
        self.assertFalse(fresh.terminal.clean_stop_reported)

    def test_real_fsync_hold_keeps_owner_deadline_service_and_Stop(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = storage._sync
        # Hold actual preparation fsync during an already-running Preview.
        # Writer readiness cannot be accepted until this real call returns.
        armed = threading.Event()
        def held(stream):
            if armed.is_set():
                entered.set()
                if not release.wait(6):
                    raise RuntimeError("fsync barrier timeout")
            return original(stream)
        now = [0]
        with patch.object(storage, "_sync", held):
            c, p = self.make(threaded=True, clock=lambda: now[0])
            c.preview()
            wait_for(lambda: c.snapshot().phase == "preview")
            armed.set(); c.record(self.path)
            self.assertTrue(entered.wait(4))
            p.auto_stop = False
            c.stop()
            wait_for(lambda: any(command[0] == 5 for command in p.commands))
            now[0] = 2001
            wait_for(lambda: c.snapshot().bridge.endpoint_first_fault is not None)
            first = c.snapshot().bridge.endpoint_first_fault
            self.assertEqual(first.code, "data_timeout")
            self.assertTrue(c.snapshot().bridge.mailbox.running)
            self.assertFalse(c.snapshot().bridge.session.clean_completion_reported)
            release.set()
            now[0] = 5001
            wait_for(lambda: c.snapshot().bridge.session.finalized)
        fresh = storage.recover_attempt(self.path)
        self.assertIsNone(fresh.recording)
        self.assertEqual(fresh.unbound_outcome.failure, c.snapshot().bridge.session.first_failure)

    def test_held_finish_and_failed_fsync_are_not_success_receipts(self):
        c, p = self.make()
        self.record(c)
        p.data(); self.until(c, lambda s: s.validated_blocks == 1)
        self.until(c, lambda s: s.bridge.session.accepted_end_sample_index == 128)
        original = storage._sync
        def failed_sync(stream):
            original(stream)
            raise OSError("test fsync result failure after bytes were readable")
        with patch.object(storage, "_sync", failed_sync):
            c.stop()
            self.until(c, lambda s: s.bridge.session.finalization_unresolved)
        status = c.snapshot()
        report = storage.recover_attempt(self.path)
        self.assertIsNotNone(report.recording.terminal)
        self.assertFalse(status.bridge.session.clean_completion_reported)
        self.assertFalse(status.bridge.storage.returned)
        self.assertFalse(status.can_preview)
        self.assertIsNotNone(status.bridge.storage.failure)

    def test_close_during_finish_keeps_real_completion_separate_from_admission(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = storage.StreamWriter.finish
        def held(writer, *args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("finish barrier timeout")
            return original(writer, *args, **kwargs)
        with patch.object(storage.StreamWriter, "finish", held):
            c, p = self.make(threaded=True)
            c.record(self.path)
            wait_for(lambda: c.snapshot().phase == "recording")
            p.data(); wait_for(lambda: c.snapshot().validated_blocks == 1)
            c.stop(); self.assertTrue(entered.wait(4))
            self.assertTrue(c.snapshot().bridge.session.source_end)
            self.assertTrue(c.snapshot().bridge.session.stop_acknowledgement)
            c.close()
            wait_for(lambda: c.snapshot().bridge.closed)
            self.assertFalse(c.join(0))
            self.assertFalse(c.snapshot().can_open)
            release.set(); self.assertTrue(c.join(4))
        self.assertTrue(storage.recover_attempt(self.path).recording.terminal.clean_stop_reported)
        self.assertFalse(c.snapshot().bridge.session.clean_completion_reported)
        self.assertTrue(c.snapshot().bridge.storage.returned)
        other, peer = self.make()
        self.preview(other)
        self.assertNotEqual(other.snapshot().bridge.identity, c.snapshot().bridge.identity)
        p.enqueue(b"old delayed bytes")
        self.assertEqual(other.snapshot().validated_blocks, 0)

    def test_slow_display_worker_coalesces_without_raw_or_detector_loss(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = module.render_display
        def held(source):
            entered.set()
            if not release.wait(6):
                raise RuntimeError("display barrier timeout")
            return original(source)
        with patch.object(module, "render_display", held):
            c, p = self.make(threaded=True)
            c.record(self.path)
            wait_for(lambda: c.snapshot().phase == "recording")
            output = []
            c.bind_display(c.snapshot().revision, output.append)
            p.data(); self.assertTrue(entered.wait(4))
            for count in range(2, 65):
                p.data(); wait_for(lambda: c.snapshot().validated_blocks == count)
            c.stop(); wait_for(lambda: c.snapshot().phase == "saved")
            status = c.snapshot()
            self.assertEqual(status.validated_samples, 8192)
            self.assertEqual(status.detector_status.next_ordinal, 64)
            self.assertTrue(status.display_running)
            self.assertLessEqual(status.peak_raw_items, 256)
            # Closing invalidates the consumer even while its envelope is held.
            c.close(); release.set(); self.assertTrue(c.join(4))
            self.assertEqual(output, [])
        self.assertEqual(self.raw(storage.recover_attempt(self.path).recording), p.raw)

    def test_live_context_crossing_R_is_not_reused_by_fresh_archive_index(self):
        c, p = self.make()
        self.preview(c)
        p.data(struct.pack("<128H", *([100] * 120 + [2000] * 8)))
        self.until(c, lambda s: s.validated_blocks == 1)
        c.record(self.path)
        self.until(c, lambda s: s.phase == "waiting for boundary")
        p.data(struct.pack("<128H", *([2000] * 8 + [100] * 120)))
        self.until(c, lambda s: s.validated_blocks == 2)
        self.finish(c)
        live_event = c._detector.retained_events[0]
        self.assertLess(live_event.start_index, 128)
        fresh = storage.recover_attempt(self.path).recording
        detector = EventDetector(CONFIG, 4096)
        for block in fresh.iter_raw():
            detector.feed(block)
        detector.finish()
        archived_event = detector.retained_events[0]
        self.assertEqual(archived_event.start_index, 128)
        self.assertIn("left_truncated", archived_event.flags)
        self.assertIn("baseline_unsettled", archived_event.flags)

    def test_old_display_revision_and_incomplete_END_cannot_replace_new_source(self):
        c, p = self.make()
        self.preview(c)
        old = c.snapshot().revision
        results = []
        c.bind_display(old, results.append)
        p.data(); self.until(c, lambda s: s.validated_blocks == 1)
        p.auto_stop = False
        c.stop(); self.until(c, lambda s: p.stop_waiting is not None)
        p.end(acknowledge=False)
        self.until(c, lambda s: s.bridge.session.source_end is not None)
        self.assertFalse(c.snapshot().can_preview)
        self.assertFalse(c.snapshot().can_open)
        self.now = 2001; self.step(c)
        self.assertFalse(c.snapshot().can_preview)
        self.assertFalse(c.bind_display(old + 1, results.append))
        c.close(); c.execute_display_once()
        self.assertEqual(results, [])


    def test_preview_data_before_ready_stays_unrecorded_and_END_before_R_is_unbound(self):
        c, p = self.make()
        self.preview(c)
        c.record(self.path)
        self.until(c, lambda s: s.bridge.storage_job_id is not None, disk=False)
        p.data(); self.until(c, lambda s: s.validated_blocks == 1, disk=False)
        self.assertIsNone(c.snapshot().bridge.session.selected_start_sample_index)
        c.stop()
        self.until(c, lambda s: s.bridge.session.stop_acknowledgement is not None, disk=False)
        self.until(c, lambda s: s.bridge.session.finalized)
        report = storage.recover_attempt(self.path)
        self.assertIsNone(report.recording)
        self.assertEqual(report.unbound_outcome.device_transmitted_end_sample_index, 128)
        self.assertIsNone(report.selected_start)
        self.assertEqual(c.snapshot().detector_status.processed_until, 128)

    def test_stop_inside_partial_DATA_completes_whole_validated_tail(self):
        c, p = self.make()
        self.record(c)
        p.auto_stop = False
        tail = p.data(split=70)
        self.step(c)
        self.assertEqual(c.snapshot().validated_blocks, 0)
        c.stop()
        self.until(c, lambda s: p.stop_waiting is not None)
        p.enqueue(tail); p.end()
        self.until(c, lambda s: s.phase == "saved" and s.can_preview)
        fresh = storage.recover_attempt(self.path).recording
        self.assertEqual(self.raw(fresh), p.raw)
        self.assertEqual(fresh.end_sample_index, 128)

    def test_saved_attempt_restarts_with_new_identity_and_stale_display_rejected(self):
        c, p = self.make()
        self.record(c)
        old_revision = c.snapshot().revision
        old_identity = c.snapshot().bridge.identity
        old_results = []
        c.bind_display(old_revision, old_results.append)
        p.data(); self.until(c, lambda s: s.validated_blocks == 1)
        self.finish(c)
        self.assertTrue(c.snapshot().can_preview)
        self.assertTrue(c.record(self.path + "-next"))
        self.until(c, lambda s: s.phase == "recording" and s.revision > old_revision)
        self.assertNotEqual(c.snapshot().bridge.identity, old_identity)
        self.assertFalse(c.bind_display(old_revision, old_results.append))
        self.assertFalse(c.request_view(old_revision, 0, 128))
        c.execute_display_once()
        self.assertEqual(old_results, [])
        self.finish(c)

    def test_malformed_terminal_does_not_manufacture_clean_saved_outcome(self):
        c, p = self.make()
        self.record(c)
        p.data(); self.until(c, lambda s: s.validated_blocks == 1)
        p.auto_stop = False
        c.stop(); self.until(c, lambda s: p.stop_waiting is not None)
        p.end(crc=0)
        self.until(c, lambda s: s.bridge.endpoint_first_fault is not None)
        self.now = 4001
        self.until(c, lambda s: s.bridge.session.finalized)
        self.assertFalse(c.snapshot().bridge.session.clean_completion_reported)
        report = storage.recover_attempt(self.path)
        self.assertFalse(report.recording.terminal.terminal_observed)
        self.assertFalse(report.recording.terminal.clean_stop_reported)

    def test_rolling_event_metadata_and_unavailable_original_range_are_bounded(self):
        c, p = self.make()
        self.preview(c)
        received = []
        c.bind_display(c.snapshot().revision, received.append)
        first_event = None
        for count in range(4200):
            p.data()
            self.until(c, lambda s: s.validated_blocks == count + 1)
            if count == 0:
                first_event = c._detector.retained_events[0]
        status = c.snapshot()
        self.assertEqual(status.detector_status.indexed_events, 4096)
        self.assertEqual(status.detector_status.next_ordinal, 4200)
        self.assertEqual(status.detector_status.evicted_events, 104)
        self.assertEqual(status.ring_samples, 65536)
        c.request_view(status.revision, first_event.start_index, first_event.end_index)
        self.now = 100; self.step(c, display=True)
        result = received[-1]
        self.assertEqual(result.envelope.outcome, "unavailable")
        self.assertEqual(result.envelope.viewport.start, first_event.start_index)
        self.assertEqual(result.envelope.point_count, 0)
        self.assertIsNone(result.source.loss_samples)
        self.assertEqual(result.source.events[0].ordinal, 104)


    def test_Stop_during_negotiation_admits_no_writer_and_can_start_fresh(self):
        c, p = self.make(EncodedPeer(partitions=(1,)))
        c.record(self.path); self.step(c, disk=False)
        c.stop()
        self.until(c, lambda s: s.phase == "stopped before START" and s.can_preview)
        self.assertFalse(Path(self.path).exists())
        self.assertFalse(any(command[0] == 8 for command in p.commands))
        self.assertTrue(c.preview())
        self.until(c, lambda s: s.phase == "preview")

    def test_close_during_actual_append_preserves_unresolved_owner_until_return(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = storage.StreamWriter.append
        def held(writer, *args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("close append barrier timeout")
            return original(writer, *args, **kwargs)
        with patch.object(storage.StreamWriter, "append", held):
            c, p = self.make(threaded=True)
            c.record(self.path); wait_for(lambda: c.snapshot().phase == "recording")
            p.data(); self.assertTrue(entered.wait(4))
            c.close(); wait_for(lambda: c.snapshot().bridge.closed)
            self.assertTrue(c.snapshot().worker_unresolved)
            self.assertFalse(c.join(0))
            self.assertFalse(c.preview())
            self.assertFalse(c.record(self.path + "other"))
            release.set(); self.assertTrue(c.join(4))
        self.assertFalse(c.snapshot().bridge.session.clean_completion_reported)
        report = storage.recover_attempt(self.path)
        self.assertTrue(report.recording is None or report.recording.terminal is None)
        self.assertTrue(c.snapshot().bridge.storage.closed)

    def test_real_append_error_leaves_no_hidden_clean_or_automatic_retry(self):
        c, p = self.make()
        self.record(c)
        self.until(c, lambda s: bool(s.bridge.storage and s.bridge.storage.operation == "bind_start"))
        p.data()
        with patch.object(storage.StreamWriter, "append", side_effect=OSError("test actual append failed")) as call:
            self.until(c, lambda s: bool(s.bridge.session.first_failure))
            self.until(c, lambda s: s.bridge.session.stop_acknowledgement is not None)
            self.assertEqual(call.call_count, 1)
        status = c.snapshot()
        self.assertEqual(status.bridge.session.first_failure.code, "host_io")
        self.assertEqual(status.validated_blocks, 1)
        self.assertEqual(status.detector_status.processed_until, 128)
        self.assertFalse(status.bridge.session.clean_completion_reported)
        self.assertFalse(status.bridge.storage.returned)
        self.assertFalse(c.record(self.path + "retry"))
        report = storage.recover_attempt(self.path)
        self.assertTrue(report.recording is None or report.recording.end_sample_index < 128)


    def test_old_encoded_bytes_after_clean_restart_do_not_enter_new_recording(self):
        c, p = self.make()
        self.preview(c)
        old_frame = p.data(); self.until(c, lambda s: s.validated_blocks == 1)
        c.stop(); self.until(c, lambda s: s.phase == "preview stopped")
        c.record(self.path); self.until(c, lambda s: s.phase == "recording")
        p.enqueue(old_frame)
        for _ in range(4):
            self.step(c)
        self.assertEqual(c.snapshot().validated_blocks, 0)
        self.assertEqual(c.snapshot().bridge.source_received_end, 0)
        self.assertIsNone(c.snapshot().bridge.endpoint_first_fault)
        p.data(); self.until(c, lambda s: s.validated_blocks == 1)
        self.finish(c)
        self.assertEqual(self.raw(storage.recover_attempt(self.path).recording), p.raw)

    def test_Stop_before_matching_START_ACK_never_establishes_late_R(self):
        peer = EncodedPeer()
        peer.auto_start = peer.auto_stop = False
        c, p = self.make(peer)
        c.record(self.path)
        self.until(c, lambda s: p.start_waiting is not None)
        c.stop(); self.until(c, lambda s: p.stop_waiting is not None)
        self.assertIsNotNone(c.snapshot().bridge.pending_start)
        self.assertIsNotNone(c.snapshot().bridge.pending_stop)
        p.start(); p.end()
        self.until(c, lambda s: s.bridge.session.finalized and s.can_open)
        self.assertIsNone(c.snapshot().bridge.session.selected_start_sample_index)
        self.assertIsNone(storage.recover_attempt(self.path).recording)


    def test_elapsed_callback_matrix_expires_before_acceptance_and_fresh_reopen(self):
        # Regression from independent 15d4412 witnesses. Equality is expired.
        for boundary in (1000, 1001):
            for mode in ("read_data", "read_empty", "read_error", "write_full",
                         "write_partial", "write_zero", "write_unknown", "write_error"):
                with self.subTest(boundary=boundary, mode=mode):
                    clock = [0]
                    c, p = self.make(clock=lambda clock=clock: clock[0])
                    path = self.path + mode + str(boundary)
                    c.record(path); self.until(c, lambda s: s.phase == "recording")
                    if mode == "read_data":
                        p.data()
                    is_write = mode.startswith("write")
                    original = p.write if is_write else p.read
                    if is_write:
                        c.stop()
                    def crossed(value):
                        clock[0] = boundary
                        if mode.endswith("error"):
                            raise OSError("callback crossed deadline")
                        if mode == "write_partial":
                            p.accepted_prefix = 1
                        if mode == "write_zero":
                            return 0
                        if mode == "write_unknown":
                            return None
                        if mode == "read_empty":
                            return b""
                        return original(value)
                    if is_write:
                        p.write = crossed
                    else:
                        p.read = crossed
                    clock[0] = 999
                    c.service(999)
                    if is_write:
                        p.write = original
                    else:
                        p.read = original
                    fault = c.snapshot().bridge.endpoint_first_fault
                    self.assertIsNotNone(fault)
                    self.assertEqual(fault.code, "data_timeout")
                    self.assertFalse(c.snapshot().bridge.session.clean_completion_reported)
                    clock[0] = 5000
                    self.until(c, lambda s: s.bridge.session.finalized and not s.worker_unresolved)
                    fresh = storage.recover_attempt(path).recording
                    self.assertFalse(fresh.clean_stop_reported)
                    self.assertEqual(fresh.terminal.failure.code, "transport_error")
                    self.assertIn("wire data_timeout;", fresh.terminal.failure.detail)
                    self.assertEqual((fresh.first_sample_index, fresh.end_sample_index), (0, 0))
                    self.assertEqual(self.raw(fresh), b"")

    def test_known_write_exception_and_ambiguous_read_have_distinct_durable_cause(self):
        for lane in ("write", "read"):
            with self.subTest(lane=lane):
                clock = [0]
                c, p = self.make(clock=lambda clock=clock: clock[0])
                path = self.path + lane
                c.record(path); self.until(c, lambda s: s.phase == "recording")
                original = getattr(p, lane)
                def failed(_):
                    raise OSError("actual accepted write prefix is unknowable")
                setattr(p, lane, failed)
                if lane == "write":
                    c.stop()
                self.step(c)
                setattr(p, lane, original)
                clock[0] = 4001
                self.until(c, lambda s: s.bridge.session.finalized and not s.worker_unresolved)
                live = c.snapshot().bridge.session.first_failure
                fresh = storage.recover_attempt(path).recording
                expected = ("STOP", 4) if lane == "write" else (None, None)
                self.assertEqual((live.operation, live.request_id), expected)
                self.assertEqual(fresh.terminal.failure, live)
                self.assertFalse(fresh.clean_stop_reported)

    def test_elapsed_owner_batches_are_observed_before_subsequent_admission(self):
        for lane in ("completion", "detector", "display_copy"):
            with self.subTest(lane=lane):
                clock = [0]
                c, p = self.make(clock=lambda clock=clock: clock[0])
                path = self.path + lane
                c.record(path); self.until(c, lambda s: s.phase == "recording")
                if lane == "completion":
                    p.data(); self.step(c)
                    target, name, boundary = c._bridge, "accept_storage_completion", 1001
                elif lane == "detector":
                    p.data()
                    target, name, boundary = c._detector, "feed", 2000
                else:
                    p.data(); self.step(c)
                    c.bind_display(c.snapshot().revision, lambda _: None)
                    target, name, boundary = c, "_copy_display", 1001
                original = getattr(target, name)
                def elapsed(*args):
                    result = original(*args)
                    clock[0] = boundary
                    return result
                clock[0] = 999
                with patch.object(target, name, elapsed):
                    c.service(999)
                self.assertEqual(c.snapshot().validated_samples, 128)
                self.assertEqual(c.snapshot().bridge.endpoint_first_fault.code, "data_timeout")
                clock[0] = 5000
                self.until(c, lambda s: s.bridge.session.finalized and not s.worker_unresolved)
                fresh = storage.recover_attempt(path).recording
                self.assertFalse(fresh.clean_stop_reported)
                self.assertEqual(fresh.terminal.failure.code, "transport_error")
                self.assertIn("wire data_timeout;", fresh.terminal.failure.detail)

    def test_manual_default_clock_does_not_freeze_on_service_argument(self):
        # Same production clock contract in manual and threaded modes. Explicit
        # patch of the default monotonic source avoids native timing assertions.
        clock = [0]
        with patch("motortester.stream_controller.time.monotonic_ns", side_effect=lambda: clock[0] * 1_000_000):
            c = StreamController(EncodedPeer(), event_config=CONFIG, threaded=False)
            self.controllers.append(c)
            p = c._peer
            c.record(self.path); self.until(c, lambda s: s.phase == "recording")
            original = p.read
            p.data()
            def crossed(maximum):
                data = original(maximum)
                clock[0] = 1000
                return data
            p.read = crossed
            clock[0] = 999
            c.service(999)
            p.read = original
            self.assertEqual(c.snapshot().validated_samples, 0)
            self.assertEqual(c.snapshot().bridge.endpoint_first_fault.code, "data_timeout")
            clock[0] = 5000
            self.until(c, lambda s: s.bridge.session.finalized and not s.worker_unresolved)
            self.assertFalse(storage.recover_attempt(self.path).recording.clean_stop_reported)

    def test_threaded_callback_elapsed_clock_reaches_real_archive(self):
        for lane in ("read", "write"):
            with self.subTest(lane=lane):
                clock = [0]
                c, p = self.make(threaded=True, clock=lambda clock=clock: clock[0])
                path = self.path + lane
                c.record(path)
                wait_for(lambda: c.snapshot().phase == "recording")
                original = getattr(p, lane)
                entered, release = threading.Event(), threading.Event()
                self.releases.append(release)
                def crossed(value):
                    result = original(value)
                    entered.set()
                    if not release.wait(4):
                        raise RuntimeError("test callback barrier expired")
                    return result
                setattr(p, lane, crossed)
                clock[0] = 999
                if lane == "read":
                    p.data()
                else:
                    c.stop()
                self.assertTrue(entered.wait(4))
                clock[0] = 1001
                release.set()
                wait_for(lambda: c.snapshot().bridge.endpoint_first_fault is not None)
                setattr(p, lane, original)
                self.assertEqual(c.snapshot().bridge.endpoint_first_fault.code, "data_timeout")
                clock[0] = 5000
                wait_for(lambda: c.snapshot().bridge.session.finalized and not c.snapshot().worker_unresolved)
                fresh = storage.recover_attempt(path).recording
                self.assertFalse(fresh.clean_stop_reported)
                self.assertEqual(fresh.terminal.failure.code, "transport_error")
                self.assertIn("wire data_timeout;", fresh.terminal.failure.detail)


    def test_unmanaged_peer_never_assumes_serial_lifecycle_ownership(self):
        c, peer = self.make()
        calls = []
        peer.open = lambda: calls.append("open")
        peer.close = lambda: calls.append("close")
        self.assertFalse(c.connect()); self.assertFalse(c.disconnect()); self.assertFalse(c.retry_close())
        self.record(c); self.finish(c)
        c.close(); self.step(c)
        self.assertEqual(calls, [])
        self.assertIsNone(c.snapshot().serial)


if __name__ == "__main__":
    unittest.main()

"""Independent encoded peer traces through the joined controller and real files.

Explicit ticks exercise dispatch order, not actual scheduling or throughput.
"""
from dataclasses import replace
from copy import copy, deepcopy
import hashlib
from pathlib import Path
import struct
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
import zlib

from motortester.stream_bridge import StreamBridge
from motortester import stream_endpoint as e
from motortester import stream_recording as r
from motortester.stream_model import StreamError
from motortester.stream_session import SessionLimits

NONCE = 0x102030405060708


def literal(kind, payload=b"", *, nonce=NONCE, sequence=1, request=0, bad_crc=False):
    """Independent envelope/COBS builder. Does not call implementation codecs."""
    raw = struct.pack("<BBBBHHQII", 0, 2, kind, 0, len(payload), 0, nonce, sequence, request) + payload
    raw += struct.pack("<I", zlib.crc32(raw) ^ int(bad_crc))
    encoded = bytearray()
    runs = raw.split(b"\0")
    for index, run in enumerate(runs):
        while len(run) >= 254:
            encoded.extend(b"\xff" + run[:254])
            run = run[254:]
        encoded.append(len(run) + 1)
        encoded.extend(run)
    return bytes(encoded) + b"\0"


def samples(count=128, seed=0):
    return struct.pack("<" + "H" * count, *((seed + i * 97) % 4096 for i in range(count)))


class Peer:
    def __init__(self, bridge, nonce=NONCE):
        self.bridge, self.nonce = bridge, nonce
        self.sequence, self.generation, self.stop_request = 1, 3, 4
        self.raw, self.last_data, self.end_sequence = b"", 0, None
        self.now = 0

    def rx(self, kind, payload, *, request=0, sequence=None, nonce=None, bad_crc=False):
        sequence = self.sequence if sequence is None else sequence
        frame = literal(kind, payload, nonce=self.nonce if nonce is None else nonce,
                        sequence=sequence, request=request, bad_crc=bad_crc)
        self.bridge.receive(frame, self.now)
        if sequence == self.sequence:
            self.sequence += 1
        return frame

    def tx(self):
        result = []
        for _ in range(3):
            events = self.bridge.next_write(self.now)
            offers = [x for x in events if isinstance(x, e.WriteOffer)]
            if not offers:
                return result
            offer, = offers
            self.bridge.observe_write(offer.token, len(offer.data), self.now)
            result.append(offer)
        raise AssertionError("TX did not bound")

    def negotiate(self):
        self.bridge.hello(self.nonce, self.now)
        offer, = self.tx()
        if offer.data != literal(1, struct.pack("<Q", self.nonce), nonce=0, request=1):
            raise AssertionError("independent HELLO byte oracle differs")
        self.rx(129, struct.pack("<BBBBHHII", 0, 2, 1, 12, 512, 128, 0x1234, 1), request=1)
        self.bridge.get_caps(self.now)
        self.tx()
        self.rx(130, struct.pack("<IHBBIIHHI", 1, 1, 0, 12, 50000, 1, 128, 0, 1), request=2)

    def started(self):
        offer, = self.tx()
        self.generation = offer.request.request_id
        self.stop_request = self.generation + 1
        self.rx(136, struct.pack("<IIQ", self.generation, 1, 0), request=self.generation)

    def data(self, raw=None, *, first=None, generation=None, config=1, **kwargs):
        raw = samples() if raw is None else raw
        first = len(self.raw) // 2 if first is None else first
        generation = self.generation if generation is None else generation
        seq = self.sequence
        frame = self.rx(152, struct.pack("<IIQHH", generation, config, first, len(raw) // 2, 0) + raw, **kwargs)
        self.raw += raw
        self.last_data = seq
        return frame

    def stop(self):
        self.bridge.stop(self.now)
        offers = self.tx()
        self.stop_request = next(x.request.request_id for x in offers if x.request.kind == 5)

    def end(self, *, reason=0, flags=0, produced=None, stop=None, crc=None, **kwargs):
        self.end_sequence = self.sequence
        self.flags = flags
        produced = (0 if flags & 1 else len(self.raw) // 2) if produced is None else produced
        return self.rx(153, struct.pack("<IIQQIIHHI", self.generation, 1,
            len(self.raw) // 2, produced, self.last_data, zlib.crc32(self.raw) if crc is None else crc,
            reason, flags, self.stop_request if stop is None else stop), **kwargs)

    def ack(self, **kwargs):
        return self.rx(133, struct.pack("<IQII", self.generation, len(self.raw) // 2,
                                       self.end_sequence, self.flags), request=self.stop_request, **kwargs)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "record.mtstream"

    def ready(self, **kwargs):
        bridge = StreamBridge("attempt", **kwargs)
        self.addCleanup(lambda: self.shutdown(bridge))
        peer = Peer(bridge)
        peer.negotiate()
        return bridge, peer

    def shutdown(self, bridge):
        bridge.close(bridge._now)
        if bridge.disk_executor is None:
            return
        for _ in range(3):
            bridge.disk_executor.execute_one()
            bridge.accept_storage_completion(bridge._now)
            if bridge.snapshot().mailbox.close_acknowledged is not None:
                return
        self.fail("disk shutdown ownership remained unresolved")

    def blocked(self, bridge, target, receiver_action):
        """Finite test-only barrier, real method runs after release; no sleep."""
        entered, release = Event(), Event()
        errors = []
        original = getattr(r.StreamWriter, target)
        def held(writer, *args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("bounded test barrier timed out")
            return original(writer, *args)
        def disk_role():
            try:
                bridge.disk_executor.execute_one()
            except BaseException as exc:
                errors.append(exc)
        with patch.object(r.StreamWriter, target, held):
            worker = Thread(target=disk_role, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(3), "writer never reached barrier")
                receiver_action()
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive(), "disk role did not settle after release")
        self.assertEqual(errors, [])

    def disk(self, bridge):
        """Bounded test driver: one explicit job and return per iteration."""
        for _ in range(512):
            job = bridge.dispatch_storage(bridge._now)
            if job is None:
                return
            bridge.storage_tick(bridge._now)
        self.fail("storage dispatch did not terminate")

    def direct(self, **kwargs):
        bridge, peer = self.ready(**kwargs)
        bridge.record(self.path, "recording", 0)
        self.assertIsNone(bridge.snapshot().pending_start)
        self.assertEqual(peer.tx(), [])
        self.disk(bridge)
        peer.started()
        self.disk(bridge)
        return bridge, peer

    def clean(self, bridge, peer):
        peer.stop()
        peer.end()
        self.assertFalse(bridge.snapshot().session.clean_completion_reported)
        peer.ack()
        self.disk(bridge)
        self.assertTrue(bridge.snapshot().session.clean_completion_reported)
        report = r.recover_attempt(self.path)
        self.assertIsNone(report.recovery_issue)
        self.assertTrue(report.recording.clean_stop_reported)
        return report.recording

    def test_direct_nonempty_and_empty_real_journal(self):
        for count in (0, 4, 128):
            with self.subTest(count=count):
                self.path = Path(self.temp.name) / (str(count) + ".stream")
                bridge, peer = self.direct()
                if count:
                    peer.data(samples(count))
                recording = self.clean(bridge, peer)
                self.assertEqual(recording.first_sample_index, 0)
                self.assertEqual(recording.read_raw(0, count), peer.raw)
                identity = recording.identity
                self.assertEqual((identity.generation, identity.config_id, identity.nominal_fs_hz), (3, 1, 50000))
                self.assertEqual(identity.firmware_version, "build:00001234")
                self.assertEqual(identity.origin, "synthetic")
                self.assertEqual(identity.session_id, "wire-0.2:0102030405060708")
                self.assertTrue(bridge.snapshot().storage.closed)

    def test_record_requires_real_readiness_and_original_token(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        self.assertFalse(self.path.exists())
        self.assertEqual(peer.tx(), [])
        original_prepare = r.PreparedStreamWriter.prepare
        original_bind = r.PreparedStreamWriter.bind_start
        tokens = []
        def prepare(*args):
            result = original_prepare(*args)
            tokens.append(result.readiness_token)
            return result
        def bind(prepared, token, start):
            self.assertIs(token, tokens[0])
            return original_bind(prepared, token, start)
        with patch.object(r.PreparedStreamWriter, "prepare", side_effect=prepare), patch.object(r.PreparedStreamWriter, "bind_start", bind):
            bridge.storage_tick(9000)  # Reservation has no premature START deadline.
            self.assertEqual(bridge.snapshot().pending_start.admitted_ms, 9000)
            peer.now = 9000
            peer.started()
            self.disk(bridge)
        self.clean(bridge, peer)

    def test_preview_late_readiness_selects_next_whole_block(self):
        bridge, peer = self.ready()
        bridge.preview(0)
        peer.started()
        peer.data(samples(seed=11))
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        peer.data(samples(seed=22))
        self.assertIsNone(bridge.snapshot().session.selected_start_sample_index)
        bridge.storage_tick(0)
        selected = samples(seed=33)
        peer.data(selected)
        self.assertEqual(bridge.snapshot().session.selected_start_sample_index, 256)
        recording = self.clean(bridge, peer)
        self.assertEqual(recording.read_raw(256, 128), selected)
        self.assertEqual(bridge.snapshot().source_prefix_crc32, zlib.crc32(peer.raw))
        self.assertEqual(bridge.snapshot().storage.selected_sha256, hashlib.sha256(selected).hexdigest())
        self.assertNotEqual(bridge.snapshot().storage.selected_sha256, hashlib.sha256(peer.raw).hexdigest())
        with self.assertRaises(StreamError):
            recording.read_raw(0, 1)

    def test_preview_end_or_fault_before_boundary_remains_unbound(self):
        for failure in (False, True):
            self.path = Path(self.temp.name) / (str(failure) + ".stream")
            bridge, peer = self.ready()
            bridge.preview(0)
            peer.started()
            peer.data()
            bridge.record(self.path, "recording", 0)
            bridge.dispatch_storage(0)
            if failure:
                peer.rx(152, b"bad")
            else:
                peer.stop()
                peer.end()
                peer.ack()
            bridge.storage_tick(0)
            self.disk(bridge)
            report = r.recover_attempt(self.path)
            self.assertIsNone(report.selected_start)
            self.assertIsNone(report.recording)
            self.assertEqual(report.unbound_outcome.reason, "failed_unbound" if failure else "ended_unbound")

    def test_start_stop_concurrent_partial_writes(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        self.disk(bridge)
        offer, = bridge.next_write(0)
        bridge.observe_write(offer.token, 7, 0)
        bridge.stop(0)
        rest, = bridge.next_write(0)
        self.assertEqual(rest.data, offer.data[7:])
        bridge.observe_write(rest.token, len(rest.data), 0)
        stop_offer, = bridge.next_write(0)
        self.assertEqual(stop_offer.request.action_id, 2)
        bridge.observe_write(stop_offer.token, len(stop_offer.data), 0)
        peer.rx(136, struct.pack("<IIQ", 3, 1, 0), request=3)
        peer.end()
        peer.ack()
        self.disk(bridge)
        report = r.recover_attempt(self.path)
        self.assertIsNone(report.selected_start)  # Stop preceded successful START_ACK.
        self.assertEqual(report.unbound_outcome.reason, "ended_unbound")

    def test_partial_and_unknown_tx_timeout_keep_first_pending_ids(self):
        for accepted in (0, 5, None, -1, True, 9999):
            with self.subTest(accepted=accepted):
                self.path = Path(self.temp.name) / (str(accepted) + ".stream")
                bridge, peer = self.ready()
                bridge.record(self.path, "recording", 0)
                self.disk(bridge)
                offer, = bridge.next_write(0)
                bridge.stop(0)
                bridge.observe_write(offer.token, accepted, 0)
                if accepted in (0, 5):
                    bridge.tick(2000)
                first = bridge.snapshot().endpoint_first_fault
                self.assertEqual((first.ordinary.request_id, first.stop.request_id), (3, 4))
                self.disk(bridge)
                report = r.recover_attempt(self.path)
                failure = report.unbound_outcome.failure
                self.assertEqual((failure.operation, failure.request_id), ("START", 3))
                self.assertIn("stop=4", failure.detail)
                self.assertFalse(bridge.snapshot().session.clean_completion_reported)

    def test_duplicate_data_end_ack_do_not_establish_new_progress(self):
        bridge, peer = self.direct()
        frame = peer.data()
        bridge.receive(frame, 0)
        self.assertEqual(bridge.snapshot().session.raw_items_held, 1)
        peer.stop()
        end = peer.end()
        bridge.receive(end, 0)
        self.disk(bridge)
        self.assertFalse(bridge.snapshot().session.finalized)
        ack = peer.ack()
        bridge.receive(ack, 0)
        self.disk(bridge)
        self.assertTrue(bridge.snapshot().session.clean_completion_reported)
        self.assertEqual(r.recover_attempt(self.path).recording.read_raw(0, 128), peer.raw)

    def test_stale_nonce_cannot_start_or_finish(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        self.disk(bridge)
        peer.tx()
        peer.rx(136, struct.pack("<IIQ", 3, 1, 0), request=3, nonce=NONCE - 1)
        self.assertFalse(bridge.snapshot().session.source_started)
        bridge.tick(2000)
        self.disk(bridge)
        self.assertIsNone(r.recover_attempt(self.path).selected_start)

    def test_malformed_or_old_data_preserves_checkpoint(self):
        for bad in ("crc", "generation", "config", "gap", "old", "short_then_data", "code"):
            with self.subTest(bad=bad):
                self.path = Path(self.temp.name) / (bad + ".stream")
                bridge, peer = self.direct()
                raw = samples(1 if bad == "short_then_data" else 128)
                old = peer.data(raw)
                self.disk(bridge)
                bridge.checkpoint(0)
                self.disk(bridge)
                if bad == "old":
                    peer.data()
                    bridge.receive(old, 0)
                else:
                    kwargs = {"bad_crc": True} if bad == "crc" else {"generation": 9} if bad == "generation" else {"config": 2} if bad == "config" else {"first": 999} if bad == "gap" else {}
                    peer.data(b"\x00\x10" if bad == "code" else samples(), **kwargs)
                self.disk(bridge)
                recording = r.recover_attempt(self.path).recording
                self.assertFalse(recording.clean_stop_reported)
                self.assertEqual(recording.read_raw(0, len(raw) // 2), raw)
                self.assertIsNotNone(recording.terminal.failure)

    def test_ack_before_end_and_malformed_end_ack_fail(self):
        for bad in ("ack_first", "end_crc", "ack_terminal"):
            self.path = Path(self.temp.name) / (bad + ".stream")
            bridge, peer = self.direct()
            peer.data()
            peer.stop()
            if bad == "ack_first":
                peer.end_sequence, peer.flags = peer.sequence + 1, 0
                peer.ack()
            elif bad == "end_crc":
                peer.end(crc=123)
            else:
                peer.end()
                peer.end_sequence += 100
                peer.ack()
            self.disk(bridge)
            self.assertFalse(bridge.snapshot().session.clean_completion_reported)
            self.assertFalse(r.recover_attempt(self.path).recording.clean_stop_reported)

    def test_known_and_unknown_source_tail_keep_counts(self):
        for flags, produced, loss in ((2, 141, 13), (3, 0, None)):
            self.path = Path(self.temp.name) / (str(flags) + ".stream")
            bridge, peer = self.direct()
            peer.data()
            peer.end(reason=1, flags=flags, produced=produced, stop=0)
            peer.tx()  # Failure END requested the one late STOP.
            peer.ack()
            self.disk(bridge)
            terminal = r.recover_attempt(self.path).recording.terminal
            self.assertEqual(terminal.device_generated_end_sample_index, produced if flags == 2 else None)
            self.assertEqual(terminal.device_transmitted_end_sample_index, 128)
            self.assertEqual(terminal.loss_samples, loss)
            self.assertEqual(terminal.failure.source_code, 1)
            self.assertFalse(terminal.clean_stop_reported)

    def test_queued_plus_inflight_capacity_and_stop_slot(self):
        for limits in (SessionLimits(512, 8), SessionLimits(4096, 2)):
            self.path = Path(self.temp.name) / (str(limits.max_raw_bytes) + ".stream")
            bridge, peer = self.direct(session_limits=limits)
            one, two = samples(seed=1), samples(seed=2)
            peer.data(one)
            bridge.dispatch_storage(0)  # In-flight append remains owned raw.
            peer.data(two)
            peer.data(samples(seed=3))
            snap = bridge.snapshot()
            self.assertEqual((snap.session.raw_bytes_held, snap.session.raw_items_held), (512, 2))
            self.assertEqual(snap.source_received_end, 384)
            self.assertEqual(snap.session.received_end_sample_index, 384)
            self.assertTrue(snap.session.first_failure.code.startswith("capacity_"))
            stop_offer, = peer.tx()
            self.assertEqual(stop_offer.request.action_id, 2)
            peer.data(samples(seed=4))  # Validate but do not admit post-fence raw.
            peer.end()
            peer.ack()
            bridge.storage_tick(0)
            self.disk(bridge)
            recording = r.recover_attempt(self.path).recording
            self.assertEqual(recording.end_sample_index, 256)
            self.assertEqual(recording.read_raw(0, 256), one + two)
            self.assertEqual(recording.terminal.device_generated_end_sample_index, 512)
            self.assertEqual(recording.terminal.loss_samples, 0)  # Device reported loss, not invented host loss.
            self.assertTrue(recording.terminal.failure.code.startswith("capacity_"))

    def test_prepare_failure_no_start_even_if_header_readable(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        with patch.object(r.os, "fsync", side_effect=OSError("disk")):
            bridge.storage_tick(0)
        self.assertEqual(peer.tx(), [])
        self.assertIsNone(bridge.snapshot().pending_start)
        self.assertTrue(bridge.snapshot().session.writer_fenced)
        report = r.recover_attempt(self.path)
        self.assertIsNone(report.selected_start)
        self.assertIsNone(report.recording)
        self.assertEqual(report.recovery_issue, "missing_selection_or_terminal")

    def test_append_checkpoint_final_failure_and_reopen_information_limit(self):
        for operation in ("append", "checkpoint", "finish"):
            self.path = Path(self.temp.name) / (operation + ".stream")
            bridge, peer = self.direct()
            peer.data()
            self.disk(bridge)
            bridge.checkpoint(0)
            self.disk(bridge)
            if operation == "append":
                peer.data()
            elif operation == "checkpoint":
                peer.data()
                self.disk(bridge)
                bridge.checkpoint(0)
            else:
                peer.stop()
                peer.end()
                peer.ack()
            bridge.dispatch_storage(0)
            target = "_check_file" if operation == "append" else "_sync_file"
            with patch.object(r.StreamWriter, target, side_effect=OSError("disk")):
                status = bridge.storage_tick(0)
            self.assertFalse(status.returned)
            self.assertTrue(bridge.snapshot().session.writer_fenced)
            self.assertFalse(bridge.snapshot().session.clean_completion_reported)
            self.assertIsNone(bridge.dispatch_storage(0))
            recording = r.recover_attempt(self.path).recording
            self.assertEqual(recording.read_raw(0, 128), peer.raw[:256])
            if operation == "finish":
                # T bytes survived in cache despite failed fsync. Reopen cannot
                # recover the lost process's failure receipt or disprove it.
                self.assertTrue(recording.clean_stop_reported)
                self.assertTrue(bridge.snapshot().session.finalization_unresolved)
            else:
                self.assertFalse(recording.clean_stop_reported)

    def test_cancel_unbound_and_selected_then_fresh_attempt(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        bridge.cancel(0)
        bridge.storage_tick(0)
        self.disk(bridge)
        self.assertEqual(peer.tx(), [])
        self.assertEqual(r.recover_attempt(self.path).unbound_outcome.reason, "cancelled_unbound")
        self.path = Path(self.temp.name) / "selected.stream"
        bridge, peer = self.direct()
        peer.data()
        bridge.cancel(0)
        peer.tx()
        peer.end()
        peer.ack()
        self.disk(bridge)
        self.assertFalse(r.recover_attempt(self.path).recording.clean_stop_reported)
        events = bridge.recover(0)
        self.assertTrue(any(isinstance(x, e.RecoveryRequired) for x in events))
        self.path = Path(self.temp.name) / "fresh.stream"
        fresh = StreamBridge("fresh")
        self.addCleanup(lambda: self.shutdown(fresh))
        new_peer = Peer(fresh, NONCE + 1)
        new_peer.negotiate()
        fresh.record(self.path, "new-recording", 0)
        self.disk(fresh)
        new_peer.started()
        self.disk(fresh)
        new_peer.data(samples(4, seed=345))
        self.clean(fresh, new_peer)
        self.assertIsNone(fresh.snapshot().session.first_failure)
        self.assertIsNone(fresh.snapshot().endpoint_first_fault)

    def test_close_abandons_dispatched_work_without_reuse(self):
        bridge, peer = self.direct()
        peer.data()
        self.disk(bridge)
        bridge.checkpoint(0)
        self.disk(bridge)
        peer.data()
        bridge.dispatch_storage(0)
        bridge.close(0)
        bridge.close(0)
        self.assertTrue(bridge.snapshot().closed)
        self.assertFalse(bridge.snapshot().storage.closed)
        self.assertTrue(bridge.snapshot().mailbox.close_requested)
        self.assertIsNone(bridge.snapshot().mailbox.close_acknowledged)
        bridge.disk_executor.execute_one()
        bridge.accept_storage_completion(0)
        self.assertTrue(bridge.snapshot().storage.closed)
        self.assertTrue(bridge.snapshot().mailbox.close_acknowledged)
        with self.assertRaises(StreamError):
            bridge.storage_tick(0)
        recording = r.recover_attempt(self.path).recording
        self.assertEqual(recording.end_sample_index, 128)
        self.assertFalse(recording.clean_stop_reported)
        other = StreamBridge("other")
        self.addCleanup(lambda: self.shutdown(other))
        other_peer = Peer(other, NONCE + 1)
        other_peer.negotiate()
        other.record(self.path, "other", 0)
        self.disk(other)
        self.assertEqual(other_peer.tx(), [])
        self.assertEqual(r.recover_attempt(self.path).recording.end_sample_index, 128)





    def test_receive_performs_no_disk_and_reserved_job_executes_once(self):
        bridge, peer = self.direct()
        with patch.object(r.StreamWriter, "append", side_effect=AssertionError("RX performed disk")):
            peer.data()
            operation = bridge.dispatch_storage(0)
            self.assertEqual(bridge.dispatch_storage(0), operation)
        original = r.StreamWriter.append
        calls = []
        def append(writer, block):
            calls.append(block)
            return original(writer, block)
        with patch.object(r.StreamWriter, "append", append):
            bridge.storage_tick(0)
            self.assertIsNone(bridge.storage_tick(0))
        self.assertEqual(len(calls), 1)
        self.clean(bridge, peer)

    def test_writer_noop_return_cannot_fabricate_requested_endpoints(self):
        bridge, peer = self.direct()
        peer.data()
        bridge.dispatch_storage(0)
        with patch.object(r.StreamWriter, "append", return_value=None):
            bridge.storage_tick(0)
        snap = bridge.snapshot()
        self.assertFalse(snap.storage.returned)
        self.assertEqual(snap.session.accepted_end_sample_index, 0)
        self.assertTrue(snap.session.writer_fenced)
        self.assertIsNone(r.recover_attempt(self.path).recording)

    def test_unknown_stop_write_names_stop_even_while_start_awaits_ack(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        self.disk(bridge)
        start, = bridge.next_write(0)
        bridge.observe_write(start.token, len(start.data), 0)
        bridge.stop(0)
        stop, = bridge.next_write(0)
        self.assertEqual(stop.request.kind.name, "STOP")
        bridge.observe_write(stop.token, None, 0)
        self.disk(bridge)
        failure = r.recover_attempt(self.path).unbound_outcome.failure
        self.assertEqual((failure.operation, failure.request_id), ("STOP", 4))
        self.assertIn("ordinary=3; stop=4", failure.detail)
        first = bridge.snapshot().endpoint_first_fault
        self.assertEqual((first.ordinary.request_id, first.stop.request_id), (3, 4))

    def test_ambiguous_protocol_fault_keeps_both_lanes_without_invented_culprit(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        self.disk(bridge)
        peer.tx()
        bridge.stop(0)
        peer.tx()
        peer.rx(136, struct.pack("<IIQ", 3, 1, 0), request=3, bad_crc=True)
        self.disk(bridge)
        failure = r.recover_attempt(self.path).unbound_outcome.failure
        self.assertIsNone(failure.operation)
        self.assertIsNone(failure.request_id)
        self.assertIn("ordinary=3; stop=4", failure.detail)

    def test_fragmented_data_ready_boundary_and_coalesced_terminal(self):
        bridge, peer = self.ready()
        bridge.preview(0)
        peer.started()
        peer.data()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        raw = samples(4, seed=72)
        frame = literal(152, struct.pack("<IIQHH", 3, 1, 128, 4, 0) + raw, sequence=5)
        for byte in frame[:-1]:
            bridge.receive(bytes((byte,)), 0)
        self.assertIsNone(bridge.snapshot().session.selected_start_sample_index)
        bridge.storage_tick(0)
        bridge.receive(frame[-1:], 0)
        peer.raw += raw
        peer.last_data, peer.sequence = 5, 6
        self.assertEqual(bridge.snapshot().session.selected_start_sample_index, 128)
        peer.stop()
        end = literal(153, struct.pack("<IIQQIIHHI", 3, 1, 132, 132, 5,
            zlib.crc32(peer.raw), 0, 0, 4), sequence=6)
        ack = literal(133, struct.pack("<IQII", 3, 132, 6, 0), sequence=7, request=4)
        bridge.receive(end + ack, 0)
        self.disk(bridge)
        self.assertTrue(bridge.snapshot().session.clean_completion_reported)
        self.assertEqual(r.recover_attempt(self.path).recording.read_raw(128, 4), raw)

    def test_coalesced_capacity_fault_is_local_and_keeps_received_endpoint(self):
        bridge, peer = self.direct(session_limits=SessionLimits(2, 1))
        peer.stop()
        raw = samples(4)
        data = literal(152, struct.pack("<IIQHH", 3, 1, 0, 4, 0) + raw, sequence=4)
        end = literal(153, struct.pack("<IIQQIIHHI", 3, 1, 4, 4, 4,
            zlib.crc32(raw), 0, 0, 4), sequence=5)
        ack = literal(133, struct.pack("<IQII", 3, 4, 5, 0), sequence=6, request=4)
        bridge.receive(data + end + ack, 0)
        self.disk(bridge)
        terminal = r.recover_attempt(self.path).recording.terminal
        self.assertIsNone(terminal.failure.request_id)
        self.assertIsNone(terminal.failure.operation)
        self.assertEqual(bridge.snapshot().session.received_end_sample_index, 4)
        self.assertEqual(terminal.failure.code, "capacity_raw")
        self.assertEqual(terminal.device_generated_end_sample_index, 4)
        self.assertFalse(terminal.clean_stop_reported)

    def test_invalid_clocks_and_wrong_tx_tokens_never_create_success(self):
        bridge, peer = self.direct()
        before = bridge.snapshot()
        for now in (True, -1, 0.5, float("nan"), float("inf"), 2**63):
            with self.assertRaises(ValueError):
                bridge.receive(b"", now)
            self.assertEqual(bridge.snapshot(), before)
        peer.stop()
        # A completed offer is no longer owned, even with an old token.
        bridge.observe_write(99999, 0, 0)
        self.disk(bridge)
        self.assertFalse(bridge.snapshot().session.clean_completion_reported)
        self.assertEqual(r.recover_attempt(self.path).recording.terminal.failure.code, "transport_error")

    def test_end_or_fault_after_readiness_before_first_selected_data(self):
        for fault in (False, True):
            self.path = Path(self.temp.name) / ("ready-" + str(fault) + ".stream")
            bridge, peer = self.ready()
            bridge.preview(0)
            peer.started()
            peer.data()
            bridge.record(self.path, "recording", 0)
            self.disk(bridge)
            if fault:
                bridge.transport_fault(0)
            else:
                peer.stop()
                peer.end()
                peer.ack()
            self.disk(bridge)
            report = r.recover_attempt(self.path)
            self.assertIsNone(report.selected_start)
            self.assertEqual(report.unbound_outcome.reason, "failed_unbound" if fault else "ended_unbound")

    def test_failed_start_error_keeps_request_without_inventing_zero_R(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        self.disk(bridge)
        peer.tx()
        peer.rx(255, struct.pack("<BBHI", 8, 1, 0, 3), request=3)
        self.disk(bridge)
        report = r.recover_attempt(self.path)
        self.assertIsNone(report.selected_start)
        self.assertIsNone(report.recording)
        self.assertEqual(report.unbound_outcome.failure.request_id, 3)
        self.assertEqual(report.unbound_outcome.failure.operation, "START")

    def test_old_nonce_end_and_ack_cannot_complete_current_attempt(self):
        for kind in (153, 133):
            self.path = Path(self.temp.name) / ("stale-" + str(kind) + ".stream")
            bridge, peer = self.direct()
            peer.data()
            peer.stop()
            if kind == 153:
                peer.end(nonce=NONCE - 1)
            else:
                peer.end()
                peer.ack(nonce=NONCE - 1)
            self.disk(bridge)
            self.assertFalse(bridge.snapshot().session.finalized)
            bridge.tick(2000)
            self.disk(bridge)
            result = r.recover_attempt(self.path).recording
            self.assertFalse(result.clean_stop_reported)
            self.assertEqual(result.terminal.failure.request_id, None if kind == 153 else 4)
            self.assertIn("stop=4", result.terminal.failure.detail)
            self.assertEqual(result.read_raw(0, 128), peer.raw)

    def test_late_failure_after_terminal_dispatch_is_separate_evidence(self):
        bridge, peer = self.direct()
        peer.data()
        self.disk(bridge)
        peer.stop()
        peer.end()
        peer.ack()
        bridge.dispatch_storage(0)
        self.assertTrue(bridge.snapshot().session.finalization_started)
        peer.data(samples())  # Protocol failure after frozen interval boundary.
        self.assertIsNotNone(bridge.snapshot().late_fault)
        self.assertFalse(bridge.snapshot().session.clean_completion_reported)
        bridge.storage_tick(0)
        self.assertTrue(bridge.snapshot().session.clean_completion_reported)
        self.assertTrue(r.recover_attempt(self.path).recording.clean_stop_reported)
        self.assertEqual(bridge.snapshot().session.finalized_end_sample_index, 128)
        self.assertIsNotNone(bridge.snapshot().late_fault)

    def test_close_error_is_not_reported_as_confirmed_close(self):
        bridge, peer = self.direct()
        peer.data()
        self.disk(bridge)
        original = r.StreamWriter.close
        calls = []
        def failed_close(writer):
            calls.append(writer)
            raise OSError("close")
        with patch.object(r.StreamWriter, "close", failed_close):
            bridge.close(0)
            bridge.close(0)
            bridge.disk_executor.execute_one()
            bridge.accept_storage_completion(0)
            self.assertFalse(bridge.disk_executor.execute_one())
        self.assertEqual(len(calls), 1)
        self.assertFalse(bridge.snapshot().storage.closed)
        self.assertEqual(bridge.snapshot().storage.failure.operation, "close")
        original(calls[0])  # Test fixture cleanup, not a production retry.

    def test_storage_capacity_preserves_writer_first_rejection(self):
        bridge, peer = self.direct(storage_limits=r.StreamLimits(max_raw_bytes=256))
        peer.data()
        self.disk(bridge)
        bridge.checkpoint(0)
        self.disk(bridge)
        peer.data()
        self.disk(bridge)
        snap = bridge.snapshot()
        self.assertEqual(snap.session.first_failure.code, "capacity_raw")
        self.assertEqual(snap.storage.failure.code, "capacity_raw")
        report = r.recover_attempt(self.path)
        self.assertEqual(report.recording.terminal.failure.code, "capacity_raw")
        self.assertEqual(report.recording.read_raw(0, 128), peer.raw[:256])

    def test_preview_bind_failure_and_unbound_finish_failure_no_retry(self):
        for operation in ("bind_first_block", "finish_unbound", "bind_start"):
            self.path = Path(self.temp.name) / (operation + ".stream")
            bridge, peer = self.ready()
            if operation != "bind_start":
                bridge.preview(0)
                peer.started()
            bridge.record(self.path, "recording", 0)
            self.disk(bridge)
            if operation == "bind_start":
                peer.started()
            elif operation == "bind_first_block":
                peer.data()
            else:
                peer.stop()
                peer.end()
                peer.ack()
            bridge.dispatch_storage(0)
            target = "_sync_file" if operation == "finish_unbound" else "_frame"
            with patch.object(r.StreamWriter, target, side_effect=OSError("disk")):
                bridge.storage_tick(0)
            self.assertEqual(bridge.snapshot().storage.operation, operation)
            self.assertFalse(bridge.snapshot().storage.returned)
            self.assertIsNone(bridge.dispatch_storage(0))
            self.assertFalse(bridge.snapshot().session.clean_completion_reported)
            report = r.recover_attempt(self.path)
            self.assertIsNone(report.recording)
            if operation == "finish_unbound":
                self.assertEqual(report.unbound_outcome.reason, "ended_unbound")
                self.assertTrue(bridge.snapshot().session.finalization_unresolved)


    def test_disk_prepare_publication_requires_owner_acceptance_for_start(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        operation = bridge.dispatch_storage(0)
        self.assertTrue(bridge.disk_executor.execute_one())
        self.assertTrue(bridge.snapshot().mailbox.result_pending)
        self.assertIsNone(bridge.snapshot().pending_start)
        self.assertEqual(peer.tx(), [])
        for _ in range(3):
            self.assertFalse(bridge.disk_executor.execute_one())
            self.assertEqual(bridge.dispatch_storage(0), operation)
        bridge.tick(1)
        with self.assertRaises(ValueError):
            bridge.accept_storage_completion(0)
        self.assertTrue(bridge.snapshot().mailbox.result_pending)
        bridge.accept_storage_completion(9000)
        self.assertEqual(bridge.snapshot().pending_start.admitted_ms, 9000)
        self.assertIsNone(bridge.accept_storage_completion(9000))
        peer.now = 9000
        peer.started()
        self.disk(bridge)
        self.clean(bridge, peer)

    def test_preview_data_before_accept_is_not_selected_even_after_disk_return(self):
        bridge, peer = self.ready()
        bridge.preview(0)
        peer.started()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        bridge.disk_executor.execute_one()
        peer.data(samples(seed=5))
        self.assertIsNone(bridge.snapshot().session.selected_start_sample_index)
        bridge.accept_storage_completion(0)
        selected = samples(seed=6)
        peer.data(selected)
        result = self.clean(bridge, peer)
        self.assertEqual(result.first_sample_index, 128)
        self.assertEqual(result.read_raw(128, 128), selected)

    def test_current_time_acceptance_enforces_expired_source_deadline(self):
        bridge, peer = self.ready()
        bridge.preview(0)
        peer.started()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        bridge.disk_executor.execute_one()
        bridge.accept_storage_completion(1000)  # No intervening explicit tick.
        self.assertEqual(bridge.snapshot().endpoint_first_fault.code, "data_timeout")
        self.assertIsNone(bridge.snapshot().session.selected_start_sample_index)
        self.disk(bridge)
        report = r.recover_attempt(self.path)
        self.assertEqual(report.unbound_outcome.reason, "failed_unbound")
        self.assertEqual(report.unbound_outcome.failure.code, "transport_error")

    def test_held_prepare_does_not_hold_mailbox_or_receiver_deadlines(self):
        bridge, peer = self.ready()
        bridge.preview(0)
        peer.started()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        def receiver():
            self.assertTrue(bridge.snapshot().mailbox.running)
            peer.data()
            self.assertFalse(bridge.disk_executor.execute_one())
            bridge.tick(1000)
            self.assertEqual(bridge.snapshot().endpoint_first_fault.code, "data_timeout")
            self.assertIsNone(bridge.accept_storage_completion(1000))
        self.blocked(bridge, "_sync_file", receiver)
        self.assertTrue(bridge.snapshot().mailbox.result_pending)
        bridge.accept_storage_completion(1001)
        self.disk(bridge)
        self.assertEqual(r.recover_attempt(self.path).unbound_outcome.reason, "failed_unbound")

    def test_held_append_keeps_raw_charged_and_stop_independent(self):
        bridge, peer = self.direct(session_limits=SessionLimits(512, 2))
        first = samples(seed=1)
        peer.data(first)
        bridge.dispatch_storage(0)
        def receiver():
            peer.data(samples(seed=2))
            peer.data(samples(seed=3))
            snap = bridge.snapshot()
            self.assertTrue(snap.mailbox.running)
            self.assertEqual(snap.session.raw_bytes_held, 512)
            self.assertEqual(snap.session.raw_items_held, 2)
            self.assertEqual(snap.session.first_failure.code, "capacity_raw")
            offers = peer.tx()
            self.assertEqual(offers[0].request.kind.name, "STOP")
            peer.end()
            peer.ack()
        self.blocked(bridge, "append", receiver)
        self.assertEqual(bridge.snapshot().session.raw_bytes_held, 512)
        bridge.accept_storage_completion(0)
        self.assertEqual(bridge.snapshot().session.raw_bytes_held, 256)
        self.disk(bridge)
        recording = r.recover_attempt(self.path).recording
        self.assertEqual(recording.end_sample_index, 256)
        self.assertEqual(recording.read_raw(0, 128), first)
        self.assertEqual(recording.terminal.failure.code, "capacity_raw")

    def test_cancel_while_prepare_or_append_runs_does_not_rearm(self):
        for operation in ("prepare", "append"):
            self.path = Path(self.temp.name) / ("cancel-held-" + operation)
            if operation == "prepare":
                bridge, peer = self.ready()
                bridge.record(self.path, "recording", 0)
                target = "_sync_file"
            else:
                bridge, peer = self.direct()
                peer.data()
                target = "append"
            bridge.dispatch_storage(0)
            def receiver():
                bridge.cancel(0)
                if operation == "append":
                    peer.tx()
                    peer.end()
                    peer.ack()
            self.blocked(bridge, target, receiver)
            bridge.accept_storage_completion(1)
            peer.now = 1
            self.disk(bridge)
            report = r.recover_attempt(self.path)
            if operation == "prepare":
                self.assertEqual(peer.tx(), [])
                self.assertIsNone(report.selected_start)
                self.assertEqual(report.unbound_outcome.reason, "cancelled_unbound")
            else:
                self.assertFalse(report.recording.clean_stop_reported)
                self.assertEqual(report.recording.read_raw(0, 128), peer.raw)

    def test_close_before_claim_cancels_reservation_without_creating_file(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        bridge.close(0)
        self.assertFalse(self.path.exists())
        self.assertIsNone(bridge.snapshot().mailbox.close_acknowledged)
        self.assertTrue(bridge.disk_executor.execute_one())
        self.assertFalse(self.path.exists())
        self.assertTrue(bridge.snapshot().mailbox.close_acknowledged)
        bridge.accept_storage_completion(100)
        self.assertFalse(bridge.snapshot().session.source_started)
        self.assertFalse(bridge.snapshot().storage.returned)

    def test_close_during_prepare_bind_append_finish_only_settles_old_ownership(self):
        for operation in ("prepare", "bind_start", "bind_first_block", "append", "finish", "finish_unbound"):
            with self.subTest(operation=operation):
                self.path = Path(self.temp.name) / ("close-held-" + operation)
                if operation in ("prepare", "bind_start"):
                    bridge, peer = self.ready()
                    bridge.record(self.path, "recording", 0)
                    if operation == "bind_start":
                        self.disk(bridge)
                        peer.started()
                    target = "_sync_file" if operation == "prepare" else "_frame"
                elif operation in ("bind_first_block", "finish_unbound"):
                    bridge, peer = self.ready()
                    bridge.preview(0)
                    peer.started()
                    bridge.record(self.path, "recording", 0)
                    self.disk(bridge)
                    if operation == "bind_first_block":
                        peer.data()
                        target = "append"
                    else:
                        peer.stop()
                        peer.end()
                        peer.ack()
                        target = "_sync_file"
                else:
                    bridge, peer = self.direct()
                    peer.data()
                    target = "append"
                    if operation == "finish":
                        self.disk(bridge)
                        peer.stop()
                        peer.end()
                        peer.ack()
                        target = "_sync_file"
                bridge.dispatch_storage(0)
                def receiver():
                    bridge.close(0)
                    self.assertTrue(bridge.snapshot().closed)
                    self.assertTrue(bridge.snapshot().mailbox.running)
                    self.assertIsNone(bridge.snapshot().mailbox.close_acknowledged)
                    self.assertFalse(bridge.snapshot().session.clean_completion_reported)
                self.blocked(bridge, target, receiver)
                self.assertTrue(bridge.snapshot().mailbox.close_acknowledged)
                bridge.accept_storage_completion(1)
                self.assertFalse(bridge.snapshot().session.clean_completion_reported)
                self.assertIsNone(bridge.snapshot().storage_job_id)
                self.assertTrue(bridge.snapshot().storage.closed)
                self.assertFalse(bridge.disk_executor.execute_one())
                if operation == "finish":
                    self.assertTrue(r.recover_attempt(self.path).recording.clean_stop_reported)
                    self.assertTrue(bridge.snapshot().session.finalization_unresolved)
                elif operation == "finish_unbound":
                    self.assertEqual(r.recover_attempt(self.path).unbound_outcome.reason, "ended_unbound")

    def test_close_after_publication_preserves_result_then_separate_close_ack(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        bridge.disk_executor.execute_one()
        bridge.close(0)
        self.assertTrue(bridge.snapshot().mailbox.result_pending)
        self.assertIsNone(bridge.snapshot().mailbox.close_acknowledged)
        self.assertFalse(bridge.disk_executor.execute_one())
        status = bridge.accept_storage_completion(1)
        self.assertTrue(status.returned)  # Actual prepare returned, although abandoned.
        self.assertFalse(bridge.snapshot().session.source_started)
        self.assertTrue(bridge.disk_executor.execute_one())
        bridge.accept_storage_completion(1)
        self.assertTrue(bridge.snapshot().mailbox.close_acknowledged)
        self.assertIsNone(r.recover_attempt(self.path).selected_start)

    def test_old_result_cannot_enter_new_attempt_after_shutdown(self):
        old, peer = self.ready()
        old.record(self.path, "old-recording", 0)
        old.dispatch_storage(0)
        new_path = Path(self.temp.name) / "new-attempt"
        new = StreamBridge("new-attempt")
        self.addCleanup(lambda: self.shutdown(new))
        next_peer = Peer(new, NONCE + 1)
        next_peer.negotiate()
        new.record(new_path, "new-recording", 0)
        new.dispatch_storage(0)
        def receiver():
            old.close(0)
            self.assertIsNone(new.accept_storage_completion(0))
            self.assertIsNone(new.snapshot().pending_start)
        self.blocked(old, "_sync_file", receiver)
        old.accept_storage_completion(1)
        self.assertFalse(old.snapshot().session.source_started)
        self.assertIsNone(new.accept_storage_completion(1))
        new.disk_executor.execute_one()
        new.accept_storage_completion(1)
        self.assertEqual(new.snapshot().pending_start.nonce, NONCE + 1)
        next_peer.now = 1
        next_peer.started()
        self.disk(new)
        self.path = new_path
        self.clean(new, next_peer)
        self.assertIsNone(new.snapshot().session.first_failure)

    def test_foreign_copied_work_and_wrong_completion_are_fenced(self):
        # Adversarial internal fault injection, not a public completion input.
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        bridge.disk_executor.execute_one()
        executor = bridge.disk_executor
        result = executor._result
        executor._result = replace(result)  # Equal fields are not the issued object.
        bridge.accept_storage_completion(0)
        self.assertEqual(bridge.snapshot().session.first_failure.code, "host_io")
        self.assertFalse(bridge.snapshot().session.source_started)
        self.assertTrue(bridge.snapshot().mailbox.close_requested)
        self.assertIsNone(bridge.accept_storage_completion(0))
        self.path = Path(self.temp.name) / "wrong-generation"
        other, other_peer = self.ready()
        other.record(self.path, "recording", 0)
        other.dispatch_storage(0)
        other.disk_executor.execute_one()
        result = other.disk_executor._result
        wrong = replace(result.completion, identity=replace(result.completion.identity, generation=99))
        # Simulate a producer bug, while retaining an internally issued envelope.
        other.disk_executor._result = other.disk_executor._issued_result = replace(result, completion=wrong)
        other.accept_storage_completion(0)
        self.assertEqual(other.snapshot().session.first_failure.code, "host_io")
        self.assertFalse(other.snapshot().session.source_started)
        self.assertIsNone(other.accept_storage_completion(0))


    def test_result_with_foreign_job_identity_does_not_leave_pending_ghost(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        bridge.disk_executor.execute_one()
        result = bridge.disk_executor._result
        wrong = replace(result, job=replace(result.job, attempt_id="foreign"))
        bridge.disk_executor._result = bridge.disk_executor._issued_result = wrong
        bridge.accept_storage_completion(0)
        snap = bridge.snapshot()
        self.assertIsNone(snap.storage_job_id)
        self.assertIsNone(snap.session.pending_operation_id)
        self.assertEqual(snap.session.first_failure.code, "host_io")
        self.assertFalse(snap.session.source_started)
        self.assertTrue(snap.mailbox.close_requested)

    def test_receiver_methods_reject_disk_thread_and_slots_remain_owned(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        failures = []
        def wrong_role():
            for method in (lambda: bridge.storage_tick(0), lambda: bridge.accept_storage_completion(0),
                           lambda: bridge.close(0), lambda: bridge.receive(b"", 0)):
                try:
                    method()
                except StreamError:
                    failures.append(True)
        worker = Thread(target=wrong_role, daemon=True)
        worker.start()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 4)
        self.assertFalse(self.path.exists())
        self.disk(bridge)
        peer.started()
        self.disk(bridge)
        self.clean(bridge, peer)


    def test_valid_error_preserves_exact_lane_with_both_requests_pending(self):
        for rejected, request, operation in ((8, 3, "START"), (5, 4, "STOP")):
            self.path = Path(self.temp.name) / ("error-" + operation)
            bridge, peer = self.ready()
            bridge.record(self.path, "recording", 0)
            self.disk(bridge)
            peer.tx()
            bridge.stop(0)
            peer.tx()
            peer.rx(255, struct.pack("<BBHI", rejected, 1, 0, 3), request=request)
            self.disk(bridge)
            saved = r.recover_attempt(self.path).unbound_outcome.failure
            self.assertEqual((saved.operation, saved.request_id), (operation, request))
            first = bridge.snapshot().endpoint_first_fault
            self.assertEqual(first.causal_request.request_id, request)
            self.assertEqual(bridge.snapshot().recovery.causal_request.request_id, request)
            self.assertIn("ordinary=3; stop=4", saved.detail)

    def test_unmatched_error_remains_ambiguous_in_saved_failure(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        self.disk(bridge)
        peer.tx()
        bridge.stop(0)
        peer.tx()
        peer.rx(255, struct.pack("<BBHI", 5, 1, 0, 3), request=999)
        self.disk(bridge)
        saved = r.recover_attempt(self.path).unbound_outcome.failure
        self.assertIsNone(saved.operation)
        self.assertIsNone(saved.request_id)
        self.assertIsNone(bridge.snapshot().endpoint_first_fault.causal_request)

    def test_operation_and_close_failures_survive_held_finalization(self):
        bridge, peer = self.direct()
        peer.data()
        self.disk(bridge)
        peer.stop()
        peer.end()
        peer.ack()
        bridge.dispatch_storage(0)
        original_close = r.StreamWriter.close
        owners = []
        def fail_close(writer):
            owners.append(writer)
            raise OSError("close failed")
        with patch.object(r.os, "fsync", side_effect=OSError("fsync failed")), patch.object(r.StreamWriter, "close", fail_close):
            self.blocked(bridge, "_sync_file", lambda: bridge.close(0))
        bridge.accept_storage_completion(1)
        snap = bridge.snapshot()
        self.assertFalse(snap.storage.returned)
        self.assertEqual(snap.storage.failure.operation, "finish")
        self.assertFalse(snap.mailbox.close_acknowledged)
        self.assertFalse(snap.storage.closed)
        self.assertTrue(snap.session.finalization_unresolved)
        self.assertFalse(bridge.disk_executor.execute_one())
        for writer in owners:
            original_close(writer)  # Test-only descriptor cleanup, no hidden retry.

    def test_prepare_error_result_pending_is_retained_through_close(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        with patch.object(r.os, "fsync", side_effect=OSError("fsync failed")):
            bridge.disk_executor.execute_one()
        failed = bridge.disk_executor._result
        bridge.close(0)
        self.assertIs(bridge.disk_executor._result, failed)
        self.assertFalse(bridge.disk_executor.execute_one())
        status = bridge.accept_storage_completion(1)
        self.assertFalse(status.returned)
        self.assertEqual(status.failure.operation, "prepare")
        bridge.disk_executor.execute_one()
        bridge.accept_storage_completion(1)
        self.assertTrue(bridge.snapshot().mailbox.close_acknowledged)
        self.assertFalse(bridge.snapshot().session.source_started)


    def test_published_prepare_then_end_or_cancel_never_establishes_R(self):
        for cancelled in (False, True):
            self.path = Path(self.temp.name) / ("published-" + str(cancelled))
            bridge, peer = self.ready()
            bridge.preview(0)
            peer.started()
            bridge.record(self.path, "recording", 0)
            bridge.dispatch_storage(0)
            bridge.disk_executor.execute_one()
            if cancelled:
                bridge.cancel(0)
                peer.tx()
            else:
                peer.stop()
            peer.end()
            peer.ack()
            bridge.accept_storage_completion(1)
            self.disk(bridge)
            report = r.recover_attempt(self.path)
            self.assertIsNone(report.selected_start)
            self.assertEqual(report.unbound_outcome.reason, "cancelled_unbound" if cancelled else "ended_unbound")
            self.assertIsNone(report.recording)

    def test_owner_handles_cannot_be_copied_into_second_writer(self):
        bridge, peer = self.ready()
        bridge.record(self.path, "recording", 0)
        bridge.dispatch_storage(0)
        for owner in (bridge, bridge.disk_executor):
            for copier in (copy, deepcopy):
                with self.assertRaises(StreamError):
                    copier(owner)
        bridge.disk_executor.execute_one()
        bridge.accept_storage_completion(0)
        peer.started()
        self.disk(bridge)
        self.clean(bridge, peer)

    def test_bounded_multiblock_backlog_replay_equality(self):
        bridge, peer = self.direct(session_limits=SessionLimits(8192, 32))
        expected = bytearray()
        for number in range(100):
            raw = samples(seed=number * 13)
            expected.extend(raw)
            peer.data(raw)
            if number % 13 == 12:
                self.disk(bridge)
                self.assertEqual(bridge.snapshot().session.raw_bytes_held, 0)
        recording = self.clean(bridge, peer)
        self.assertEqual(recording.read_raw(0, len(expected) // 2), bytes(expected))
        self.assertEqual(b"".join(block.raw_u16le for block in recording.iter_raw(chunk_samples=137)), bytes(expected))
        self.assertEqual(bridge.snapshot().storage.selected_sha256, hashlib.sha256(expected).hexdigest())


if __name__ == "__main__":
    unittest.main()

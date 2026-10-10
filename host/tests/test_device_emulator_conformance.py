"""HOST-007 independent byte-peer conformance, frozen unaccepted c54 draft.

No firmware implementation or production byte codec is the oracle. HOST-006 is
used only in the explicitly named closed-loop integration tests. PROVISIONAL
hold-timeout tests are isolated at the bottom and cannot accept shared semantics.
All setup and checks remain effective with Python -O (unittest, no bare asserts).
"""
from dataclasses import FrozenInstanceError, fields
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tracemalloc
import unittest

from motortester.device_emulator import DeviceEmulator, DeviceError

FIXTURES = Path(__file__).parent / "fixtures" / "device_emulator"
_spec = importlib.util.spec_from_file_location("host007_independent_reference", FIXTURES / "reference.py")
ref = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ref
_spec.loader.exec_module(ref)
GOLDEN = json.loads((FIXTURES / "golden-vectors.json").read_text())
MAX_ID, MAX_TIME = ref.MAX_ID, ref.MAX_TIME


def choose(frames, kind):
    return [frame for frame in frames if frame.kind == kind]


def code_names(diagnostics):
    return tuple(getattr(item.code, "value", item.code) for item in diagnostics)


class Peer:
    """Independent raw host. Unaccepted input is retained and retried explicitly."""
    def __init__(self, case, nonce=ref.NONCE):
        self.case, self.device, self.nonce = case, DeviceEmulator(), nonce
        self.sequence, self.request, self.now = 2, 2, 0
        self.collector = ref.Collector()
        self.received = bytearray()

    def pull(self, count=4096, now=None):
        if now is not None:
            self.now = now
        result = self.device.read(count, self.now)
        self.case.assertIs(type(result), bytes)
        self.case.assertLessEqual(len(result), count)
        self.received.extend(result)
        self.collector.feed(result)
        return result

    def drain(self, count=4096, now=None):
        start = len(self.collector.frames)
        for _ in range(12000):
            if not self.pull(count, now):
                self.case.assertFalse(self.collector.pending, "device stalled inside an output frame")
                return self.collector.frames[start:]
        self.case.fail("bounded drain did not finish")

    def one(self, now=None):
        start = len(self.collector.frames)
        for _ in range(545):
            chunk = self.pull(1, now)
            self.case.assertTrue(chunk, "expected an output frame")
            if len(self.collector.frames) > start:
                return self.collector.frames[-1]
        self.case.fail("output frame exceeds 544-byte maximum")

    def until(self, kind, now=None):
        for _ in range(32):
            frame = self.one(now)
            if frame.kind == kind:
                return frame
        self.case.fail(f"output kind {kind} not reached")

    def accept(self, data, now=None):
        if now is not None:
            self.now = now
        result = self.device.write(data, self.now)
        self.case.assertIs(type(result.accepted_prefix), int)
        self.case.assertGreaterEqual(result.accepted_prefix, 0)
        self.case.assertLessEqual(result.accepted_prefix, len(data))
        self.case.assertIs(type(result.diagnostics), tuple)
        self.case.assertLessEqual(len(result.diagnostics), 32)
        return result

    def all(self, data, now=None):
        pending = data
        for _ in range(256):
            if not pending:
                return
            result = self.accept(pending[:4096], now)
            pending = pending[result.accepted_prefix:]
            if result.accepted_prefix == 0:
                self.case.assertTrue(self.pull(), "neither RX nor TX made progress")
        self.case.fail("bounded command write did not finish")

    def command(self, kind, payload=b"", *, sequence=None, request=None, nonce=None):
        sequence = self.sequence if sequence is None else sequence
        request = self.request if request is None else request
        raw = ref.packet(kind, self.nonce if nonce is None else nonce, sequence, request, payload)
        self.all(raw)
        self.sequence, self.request = sequence + 1, request + 1
        return raw

    def hello(self, nonce=None):
        if nonce is not None:
            self.nonce = nonce
        self.all(ref.hello(self.nonce))
        self.sequence, self.request = 2, 2
        return self.drain()

    def ready(self, edge=0, level=2048):
        self.hello()
        self.command(2)
        caps = self.drain()
        self.case.assertEqual(choose(caps, 130)[0].payload, ref.caps())
        self.command(3, ref.config(edge=edge, level=level))
        replies = self.drain()
        self.case.assertEqual(choose(replies, 131)[0].payload, ref.uint(1, 4) + ref.config(edge=edge, level=level))
        return self

    def armed(self, edge=0, level=2048):
        self.ready(edge, level)
        generation = self.request
        self.command(4, ref.uint(1, 4))
        replies = self.drain()
        self.case.assertEqual(choose(replies, 132)[0].payload, ref.uint(generation, 4) + ref.uint(1, 4))
        self.case.assertEqual(self.device.snapshot().state, 2)
        return self

    def trigger(self):
        self.case.assertTrue(self.device.trigger(self.now))

    def completed(self, edge=0, level=2048):
        self.armed(edge, level)
        self.trigger()
        frames = self.drain()
        self.case.assertEqual(len(choose(frames, 146)), 1)
        self.case.assertEqual(self.device.snapshot().state, 6)
        return self, frames

    def stop(self):
        generation = self.device.snapshot().generation
        self.command(5, ref.uint(generation, 4))
        return self.drain()


class Checks(unittest.TestCase):
    def peer(self):
        return Peer(self)

    def expect_error(self, frames, kind, code, generation=0):
        errors = choose(frames, 255)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].payload, ref.error(kind, code, generation))

    def contiguous(self, frames, nonce=ref.NONCE, start=1):
        relevant = [frame for frame in frames if frame.session == nonce]
        self.assertEqual([frame.sequence for frame in relevant], list(range(start, start + len(relevant))))

    def reconstruct(self, frames, generation, window, edge=0, level=2048):
        begin, end = choose(frames, 144), choose(frames, 146)
        self.assertEqual(len(begin), 1)
        self.assertEqual(begin[0].payload, ref.begin(generation, window, edge=edge, level=level))
        self.assertEqual(len(end), 1)
        self.assertFalse(choose(frames, 147))
        raw, cursor = bytearray(), 0
        for block in choose(frames, 145):
            payload = block.payload
            self.assertEqual(ref.number(payload[:4]), generation)
            self.assertEqual(ref.number(payload[4:8]), window)
            offset, count = ref.number(payload[8:10]), ref.number(payload[10:12])
            self.assertEqual(offset, cursor)
            self.assertGreaterEqual(count, 1)
            self.assertLessEqual(count, 250)
            self.assertEqual(len(payload), 12 + 2 * count)
            raw.extend(payload[12:])
            cursor += count
        self.assertEqual(cursor, 2048)
        self.assertEqual(len(raw), 4096)
        expected = ref.samples(generation, window, edge, level)
        self.assertEqual(bytes(raw), expected)
        self.assertEqual(end[0].payload, ref.end(generation, window, edge, level))
        self.assertEqual(ref.number(end[0].payload[-4:]), ref.crc32(raw))
        codes = [ref.number(raw[index:index + 2]) for index in range(0, 4096, 2)]
        self.assertTrue(all(0 <= code <= 4095 for code in codes))
        flags = ref.number(begin[0].payload[26:28])
        self.assertEqual(bool(flags & 2), any(code in (0, 4095) for code in codes))
        self.assertEqual(flags & ~2, 0)
        return bytes(raw)


class GoldenAndBasics(Checks):
    def test_frozen_sources_and_independent_vectors(self):
        self.assertEqual(GOLDEN["source_sha"], ref.PIN)
        self.assertEqual(GOLDEN["base_sha"], ref.BASE)
        self.assertEqual(ref.crc32(b"123456789"), 0xCBF43926)
        self.assertEqual(GOLDEN["vectors"], ref.vectors())
        self.assertEqual(ref.config().hex(), "01000100000000080000000000000000")
        self.assertEqual((len(ref.hello_ack()), len(ref.caps()), len(ref.begin())), (16, 30, 40))
        self.assertEqual((FIXTURES / "samples-g4-w1-rising.bin").read_bytes(), ref.samples())
        hashes = {"wire-v0.1.md": "e2576cd70b103ed580f57e132e1d3b6c9e8da3d8f491a9e12c74d47ae0764b6e",
                  "session-guard.md": "cddb17168be9d936b1b19c4a2363026988e656ff50ed526c750ec7165cb7a6ae"}
        for name, expected in hashes.items():
            self.assertEqual(hashlib.sha256((FIXTURES / "source" / name).read_bytes()).hexdigest(), expected)
        for vector in GOLDEN["vectors"]:
            decoded = ref.frame(bytes.fromhex(vector["packet_hex"]))
            self.assertEqual(decoded.payload.hex(), vector["payload_hex"])
            self.assertEqual(decoded.kind, vector["type"])

    def test_exact_hello_caps_config_arm_ack_payloads(self):
        p = self.peer().armed()
        frames = p.collector.frames
        expected = {129: ref.hello_ack(), 130: ref.caps(), 131: ref.uint(1, 4) + ref.config(),
                    132: ref.uint(4, 4) + ref.uint(1, 4)}
        for kind, payload in expected.items():
            self.assertEqual(choose(frames, kind)[0].payload, payload)
        self.contiguous(frames)
        snapshot = p.device.snapshot()
        self.assertEqual(snapshot.config, ref.config())
        self.assertEqual(snapshot.generation, 4)
        self.assertEqual(snapshot.config_id, 1)
        self.assertEqual(snapshot.window_id, 0)
        self.assertEqual(snapshot.state, 2)

    def test_immutable_results_and_out_of_band_provenance(self):
        p = self.peer()
        result = p.accept(ref.hello())
        snapshot = p.device.snapshot()
        for obj in (result, snapshot, snapshot.provenance):
            self.assertFalse(hasattr(obj, "__dict__"))
            with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
                setattr(obj, fields(obj)[0].name, None)
        provenance = snapshot.provenance
        for name in ("simulator", "draft", "nominal_timing", "synthetic_waveform", "timeout_provisional"):
            self.assertIs(getattr(provenance, name), True)
        self.assertEqual(provenance.source_sha, ref.PIN)
        self.assertIn("PROVISIONAL", provenance.timeout_interpretation)
        frames = p.drain()
        self.assertEqual(len(choose(frames, 129)[0].payload), 16)
        self.assertEqual(result.provenance, provenance)

    def test_read_zero_never_allocates_sequence_or_terminal(self):
        p = self.peer()
        p.all(ref.hello())
        before = p.device.snapshot()
        self.assertEqual(p.pull(0), b"")
        after = p.device.snapshot()
        self.assertEqual(after.next_tx_sequence, before.next_tx_sequence)
        self.assertEqual(after.terminal, before.terminal)
        p.pull(1)
        self.assertEqual(p.device.snapshot().next_tx_sequence, before.next_tx_sequence + 1)
        p.drain(1)
        self.contiguous(p.collector.frames)

    def test_every_hello_command_chunk_boundary(self):
        raw = ref.hello()
        for boundary in range(len(raw) + 1):
            with self.subTest(boundary=boundary):
                p = self.peer()
                p.all(raw[:boundary])
                p.all(raw[boundary:])
                frames = p.drain(1)
                self.assertEqual(choose(frames, 129)[0].packet,
                                 bytes.fromhex(next(row["packet_hex"] for row in GOLDEN["vectors"] if row["name"] == "HELLO_ACK")))

    def test_every_config_chunk_boundary_and_all_one_byte_writes(self):
        raw = ref.packet(3, sequence=3, request=3, payload=ref.config())
        for boundary in range(len(raw) + 1):
            with self.subTest(boundary=boundary):
                p = self.peer()
                p.hello()
                p.command(2)
                p.drain()
                p.all(raw[:boundary])
                p.all(raw[boundary:])
                self.assertEqual(choose(p.drain(1), 131)[0].payload, ref.uint(1, 4) + ref.config())
        p = self.peer()
        commands = ref.hello() + ref.packet(2) + ref.packet(3, sequence=3, request=3, payload=ref.config())
        for octet in commands:
            p.all(bytes((octet,)))
        self.assertEqual([f.kind for f in p.drain(1) if f.request], [129, 130, 131])

    def test_coalesced_commands_preserve_each_reply(self):
        p = self.peer()
        raw = (ref.hello() + ref.packet(2) + ref.packet(3, sequence=3, request=3, payload=ref.config())
               + ref.packet(4, sequence=4, request=4, payload=ref.uint(1, 4))
               + ref.packet(5, sequence=5, request=5, payload=ref.uint(4, 4)))
        p.all(raw)
        frames = p.drain(1)
        self.assertEqual([(f.kind, f.request) for f in frames if f.request],
                         [(129, 1), (130, 2), (131, 3), (132, 4), (133, 5)])
        self.assertFalse(choose(frames, 147))
        self.assertEqual(p.device.snapshot().state, 0)
        self.contiguous(frames)

    def test_trigger_only_waiting_and_normal_wait_has_no_timeout(self):
        p = self.peer()
        self.assertFalse(p.device.trigger(0))
        p.ready()
        self.assertFalse(p.device.trigger(0))
        p.command(4, ref.uint(1, 4))
        p.drain()
        p.now = 10**9
        p.device.tick(p.now)
        frames = p.drain()
        self.assertEqual(p.device.snapshot().state, 2)
        self.assertFalse(any(f.kind in (144, 145, 146, 147) for f in frames))
        p.trigger()
        self.assertFalse(p.device.trigger(p.now))
        p.drain()
        self.assertFalse(p.device.trigger(p.now))

    def test_rising_falling_level_extremes_full_reconstruction(self):
        for edge in (0, 1):
            for level in (1, 2048, 4094):
                with self.subTest(edge=edge, level=level):
                    p, frames = self.peer().completed(edge, level)
                    self.reconstruct(frames, 4, 1, edge, level)
                    self.assertTrue(p.device.snapshot().held_samples)
                    self.assertEqual(p.device.snapshot().terminal.outcome, "END")
                    self.assertEqual(p.device.snapshot().terminal.sample_crc32, ref.crc32(ref.samples(4, 1, edge, level)))
                    self.contiguous(p.collector.frames)


class ReleaseLifecycle(Checks):
    def test_matching_release_rearms_only_after_last_ack_byte(self):
        p, frames = self.peer().completed()
        request, sequence = p.request, p.device.snapshot().next_tx_sequence
        p.command(7, ref.release())
        expected = ref.packet(135, sequence=sequence, request=request, payload=ref.uint(4, 4) + ref.uint(1, 4))
        prefix = p.pull(len(expected) - 1)
        self.assertEqual(prefix, expected[:-1])
        self.assertFalse(p.device.trigger(p.now), "rearm before RELEASE_ACK complete")
        self.assertEqual(p.device.snapshot().window_id, 1)
        self.assertEqual(p.pull(0), b"")
        self.assertFalse(p.device.trigger(p.now))
        self.assertEqual(p.pull(1), expected[-1:])
        self.assertEqual(p.device.snapshot().state, 2)
        p.drain()
        p.trigger()
        second = p.drain(1)
        self.reconstruct(second, 4, 2)
        self.assertEqual(p.device.snapshot().generation, 4)
        self.assertEqual(p.device.snapshot().tombstone.window_id, 2)

    def test_exact_duplicate_ignored_new_request_ack_no_second_rearm(self):
        p, _ = self.peer().completed()
        raw = p.command(7, ref.release())
        self.assertEqual(len(choose(p.drain(), 135)), 1)
        before = p.device.snapshot()
        p.all(raw)
        self.assertEqual(p.drain(), [])
        self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence)
        p.command(7, ref.release())
        frames = p.drain()
        self.assertEqual(len(choose(frames, 135)), 1)
        self.assertEqual(p.device.snapshot().state, 2)
        self.assertEqual(p.device.snapshot().window_id, 1)
        p.trigger()
        self.reconstruct(p.drain(), 4, 2)
        self.contiguous(p.collector.frames)

    def test_bad_generation_window_crc_and_reserved_length(self):
        cases = [(ref.uint(5, 4) + ref.release()[4:], 7),
                 (ref.release()[:4] + ref.uint(0, 4) + ref.release()[8:], 7),
                 (ref.release()[:4] + ref.uint(2, 4) + ref.release()[8:], 7),
                 (ref.release()[:-4] + ref.uint(ref.crc32(ref.samples()) ^ 1, 4), 7),
                 (ref.release()[:-1], 6), (ref.release() + b"\0", 6)]
        for payload, error_code in cases:
            with self.subTest(payload=payload.hex()):
                p, _ = self.peer().completed()
                p.command(7, payload)
                self.expect_error(p.drain(), 7, error_code, 4)
                self.assertEqual(p.device.snapshot().state, 6)
                self.assertTrue(p.device.snapshot().held_samples)
                p.command(7, ref.release())
                self.assertEqual(len(choose(p.drain(), 135)), 1)
                self.assertEqual(p.device.snapshot().state, 2)

    def test_latest_completed_tombstone_replaces_prior(self):
        p, _ = self.peer().completed()
        p.command(7, ref.release())
        p.drain()
        p.trigger()
        self.reconstruct(p.drain(), 4, 2)
        p.command(7, ref.release())
        self.expect_error(p.drain(), 7, 7, 4)
        self.assertEqual(p.device.snapshot().state, 6)
        p.command(7, ref.release(4, 2))
        self.assertEqual(len(choose(p.drain(), 135)), 1)
        self.assertEqual(p.device.snapshot().state, 2)

    def test_late_release_after_stop_ack_does_not_rearm(self):
        p, _ = self.peer().completed()
        terminal = p.device.snapshot().terminal
        p.stop()
        self.assertFalse(p.device.snapshot().held_samples)
        for _ in range(2):
            p.command(7, ref.release())
            self.assertEqual(len(choose(p.drain(), 135)), 1)
            self.assertEqual(p.device.snapshot().state, 0)
            self.assertFalse(p.device.trigger(p.now))
            self.assertEqual(p.device.snapshot().tombstone, terminal)
        self.assertEqual(len(choose(p.collector.frames, 146)), 1)
        self.assertFalse(choose(p.collector.frames, 147))

    def test_stop_before_during_and_after_release_ack_cancels_continuation(self):
        for accepted in (0, 1, "last_minus_one", "full"):
            with self.subTest(accepted=accepted):
                p, _ = self.peer().completed()
                request, sequence = p.request, p.device.snapshot().next_tx_sequence
                p.command(7, ref.release())
                ack = ref.packet(135, sequence=sequence, request=request,
                                 payload=ref.uint(4, 4) + ref.uint(1, 4))
                count = len(ack) - 1 if accepted == "last_minus_one" else len(ack) if accepted == "full" else accepted
                if count:
                    self.assertEqual(p.pull(count), ack[:count])
                p.command(5, ref.uint(4, 4))
                p.drain(1)
                relevant = [f.kind for f in p.collector.frames if f.kind in (135, 133)]
                self.assertEqual(relevant, [135, 133])
                self.assertEqual(p.device.snapshot().state, 0)
                self.assertFalse(p.device.snapshot().stop_barrier)
                self.assertFalse(p.device.trigger(p.now))
                self.assertEqual(len(choose(p.collector.frames, 146)), 1)
                self.assertFalse(choose(p.collector.frames, 147))
                self.contiguous(p.collector.frames)

    def test_release_of_aborted_or_never_allocated_window_is_bad(self):
        p = self.peer().armed()
        p.command(7, ref.release())
        self.expect_error(p.drain(), 7, 7, 4)
        p.trigger()
        p.stop()
        p.command(7, ref.release())
        self.expect_error(p.drain(), 7, 7, 4)
        self.assertEqual(p.device.snapshot().state, 0)

    def test_config_and_arm_busy_while_wait_release(self):
        p, _ = self.peer().completed()
        for kind, payload in ((3, ref.config()), (4, ref.uint(1, 4))):
            p.command(kind, payload)
            self.expect_error(p.drain(), kind, 2, 4)
        self.assertEqual(p.device.snapshot().config_id, 1)
        self.assertEqual(p.device.snapshot().window_id, 1)
        p.command(7, ref.release())
        p.drain()
        self.assertEqual(p.device.snapshot().state, 2)


class StopAndAdmission(Checks):
    def capture_trace(self):
        p = self.peer().armed()
        p.trigger()
        start = len(p.received)
        frames = p.drain()
        offsets, cursor = [], 0
        for frame in frames:
            offsets.append((frame, cursor))
            cursor += len(frame.packet)
        return bytes(p.received[start:]), offsets

    def prefix(self, peer, data):
        position = 0
        while position < len(data):
            chunk = peer.pull(min(4096, len(data) - position))
            self.assertTrue(chunk)
            self.assertEqual(chunk, data[position:position + len(chunk)])
            position += len(chunk)

    def test_stop_at_each_terminal_boundary_no_gaps_or_old_data(self):
        raw, offsets = self.capture_trace()
        first = {kind: next((frame, pos) for frame, pos in offsets if frame.kind == kind)
                 for kind in (144, 145, 146)}
        end_frame, end_pos = first[146]
        cuts = {"before_BEGIN": first[144][1], "mid_BEGIN": first[144][1] + 1,
                "mid_DATA": first[145][1] + 20, "before_first_END_byte": end_pos,
                "after_first_END_byte": end_pos + 1, "after_full_END": end_pos + len(end_frame.packet)}
        for label, cutoff in cuts.items():
            with self.subTest(boundary=label):
                p = self.peer().armed()
                p.trigger()
                self.prefix(p, raw[:cutoff])
                before = p.device.snapshot()
                self.assertEqual(p.pull(0), b"")
                self.assertEqual(p.device.snapshot().next_tx_sequence, before.next_tx_sequence)
                self.assertEqual(p.device.snapshot().terminal, before.terminal)
                if cutoff <= end_pos:
                    self.assertIsNone(before.terminal)
                else:
                    self.assertEqual(before.terminal.outcome, "END")
                p.command(5, ref.uint(4, 4))
                self.assertTrue(p.device.snapshot().stop_barrier)
                p.drain(1)
                frames = p.collector.frames
                terminals = [f for f in frames if f.kind in (146, 147)]
                self.assertEqual(len(terminals), 1)
                self.assertEqual(terminals[0].kind, 147 if cutoff <= end_pos else 146)
                if cutoff <= end_pos:
                    self.assertEqual(terminals[0].payload, ref.abort())
                ack = choose(frames, 133)[0]
                self.assertEqual(ack.payload, ref.uint(4, 4) + ref.uint(1, 4) + b"\0" * 4)
                self.assertLess(frames.index(terminals[0]), frames.index(ack))
                self.assertFalse(any(f.kind in (144, 145, 146, 147) for f in frames[frames.index(ack) + 1:]))
                self.assertFalse(p.device.snapshot().stop_barrier)
                self.assertFalse(p.device.snapshot().held_samples)
                self.assertEqual(p.device.snapshot().state, 0)
                self.contiguous(frames)
                p.command(5, ref.uint(4, 4))
                later = p.drain()
                self.assertEqual(len(choose(later, 133)), 1)
                self.assertFalse(any(f.kind in (146, 147) for f in later))

    def test_every_byte_cut_inside_first_data_frame_finishes_started_frame(self):
        raw, offsets = self.capture_trace()
        frame, offset = next((f, pos) for f, pos in offsets if f.kind == 145)
        # Every possible boundary of one maximum-size DATA frame, not just USB-sized cuts.
        for cut in range(1, len(frame.packet)):
            with self.subTest(cut=cut):
                p = self.peer().armed()
                p.trigger()
                self.prefix(p, raw[:offset + cut])
                p.command(5, ref.uint(4, 4))
                p.drain()
                data = choose(p.collector.frames, 145)
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0].packet, frame.packet)
                self.assertEqual(len(choose(p.collector.frames, 147)), 1)
                self.assertEqual(len(choose(p.collector.frames, 133)), 1)
                self.contiguous(p.collector.frames)

    def test_stop_ack_final_byte_is_barrier_boundary(self):
        p = self.peer().armed()
        request, sequence = p.request, p.device.snapshot().next_tx_sequence
        p.command(5, ref.uint(4, 4))
        expected = ref.packet(133, sequence=sequence, request=request,
                              payload=ref.uint(4, 4) + ref.uint(0, 4) + b"\0" * 4)
        self.assertEqual(p.pull(len(expected) - 1), expected[:-1])
        self.assertTrue(p.device.snapshot().stop_barrier)
        self.assertEqual(p.device.snapshot().state, 4)
        p.command(4, ref.uint(1, 4))
        self.assertEqual(p.pull(1), expected[-1:])
        self.assertFalse(p.device.snapshot().stop_barrier)
        self.assertEqual(p.device.snapshot().state, 0)
        self.expect_error(p.drain(), 4, 2, 4)
        generation = p.request
        p.command(4, ref.uint(1, 4))
        self.assertEqual(choose(p.drain(), 132)[0].payload[:4], ref.uint(generation, 4))
        self.assertEqual(p.device.snapshot().state, 2)

    def test_prior_arm_reply_then_single_abort_then_stop_ack(self):
        p = self.peer().ready()
        p.command(4, ref.uint(1, 4))
        p.trigger()
        p.command(5, ref.uint(4, 4))
        frames = p.drain(1)
        mandatory = [f.kind for f in frames if f.kind in (132, 146, 147, 133)]
        self.assertEqual(mandatory, [132, 147, 133])
        self.assertFalse(choose(frames, 144))
        self.assertFalse(choose(frames, 145))
        self.contiguous(p.collector.frames)

    def test_prior_config_or_error_reply_survives_stop(self):
        for payload, reply_kind in ((ref.config(), 131), (ref.config(profile=2), 255)):
            with self.subTest(reply=reply_kind):
                p = self.peer()
                p.hello()
                p.command(2)
                p.drain()
                p.command(3, payload)
                p.command(5, ref.uint(0, 4))
                frames = p.drain()
                self.assertEqual([f.kind for f in frames if f.request], [reply_kind, 133])
                self.assertFalse(choose(frames, 147))

    def test_stop_without_window_no_phantom_abort_and_stale_generation(self):
        p = self.peer().ready()
        for _ in range(2):
            frames = p.stop()
            self.assertEqual(len(choose(frames, 133)), 1)
            self.assertFalse(choose(frames, 147))
        generation = p.request
        p.command(4, ref.uint(1, 4))
        p.drain()
        p.command(5, ref.uint(0, 4))
        self.expect_error(p.drain(), 5, 4, generation)
        self.assertEqual(p.device.snapshot().state, 2)
        frames = p.stop()
        self.assertFalse(choose(frames, 147))
        self.assertEqual(ref.number(choose(frames, 133)[0].payload[:4]), generation)


class FreshHelloIsolation(Checks):
    def check_new_hello(self, p):
        previous_generation = p.device.snapshot().generation
        first = p.pull(1)
        self.assertEqual(len(first), 1)
        old_pending = bytes(p.collector.pending)
        self.assertTrue(old_pending)
        old_frame_count = len(p.collector.frames)
        new_nonce = ref.NONCE + 1
        p.all(ref.hello(new_nonce))
        state = p.device.snapshot()
        self.assertEqual(state.session, new_nonce)
        self.assertEqual(state.generation, 0)
        self.assertIsNone(state.config)
        self.assertEqual(state.window_id, 0)
        rest = p.drain(1)
        self.assertGreaterEqual(len(rest), 2)
        self.assertEqual(rest[0].session, ref.NONCE)
        self.assertTrue(rest[0].packet.startswith(old_pending))
        self.assertEqual(rest[1].kind, 129)
        self.assertEqual(rest[1].session, new_nonce)
        self.assertEqual(rest[1].sequence, 1)
        self.assertFalse(any(f.session == ref.NONCE for f in rest[1:]))
        after = p.device.snapshot()
        self.assertEqual(after.session, new_nonce)
        self.assertTrue(after.negotiated)
        self.assertEqual(after.state, 0)
        self.assertEqual(after.generation, 0)
        self.assertEqual(after.config_id, 0)
        self.assertEqual(after.window_id, 0)
        self.assertFalse(after.stop_barrier)
        self.assertFalse(after.held_samples)
        self.assertIsNone(after.terminal)
        self.assertIsNone(after.tombstone)
        self.assertIsNone(after.hold_started_ms)
        self.assertEqual(after.next_rx_sequence, 2)
        self.assertFalse(p.device.trigger(p.now))
        self.contiguous(p.collector.frames, new_nonce)

    def test_old_reply_callbacks_cannot_mutate_new_session(self):
        for target in (129, 131, 132, 133, 135):
            with self.subTest(old_kind=target):
                p = self.peer()
                if target == 129:
                    p.all(ref.hello())
                elif target in (131, 132):
                    p.hello()
                    p.command(2)
                    p.drain()
                    p.command(3, ref.config())
                    if target == 132:
                        p.drain()
                        p.command(4, ref.uint(1, 4))
                else:
                    p, _ = p.completed()
                    p.command(5 if target == 133 else 7, ref.uint(4, 4) if target == 133 else ref.release())
                self.check_new_hello(p)

    def test_old_begin_data_end_callbacks_cannot_mutate_new_session(self):
        # Find boundaries via independent decoding, then replay the same visible
        # setup, not by introspecting the emulator's TX descriptors.
        reference = self.peer().armed()
        reference.trigger()
        trace = reference.drain()
        for target in (144, 145, 146):
            with self.subTest(old_kind=target):
                p = self.peer().armed()
                p.trigger()
                for frame in trace:
                    if frame.kind == target:
                        break
                    self.assertEqual(p.one().packet, frame.packet)
                self.check_new_hello(p)

    def test_duplicate_current_nonce_does_not_reset_even_after_recovery(self):
        p = self.peer().armed()
        before = p.device.snapshot()
        p.all(ref.hello())
        self.assertEqual(p.drain(), [])
        after = p.device.snapshot()
        self.assertEqual(after.generation, before.generation)
        self.assertEqual(after.config, before.config)
        self.assertEqual(after.next_rx_sequence, before.next_rx_sequence)
        p.all(ref.packet(6, sequence=100, request=100))
        p.drain()
        self.assertFalse(p.device.snapshot().negotiated)
        p.all(ref.hello())
        self.assertFalse(choose(p.drain(), 129))
        self.assertFalse(p.device.snapshot().negotiated)
        p.hello(ref.NONCE + 1)
        self.assertTrue(p.device.snapshot().negotiated)

    def test_disconnect_drops_started_bytes_without_stale_completion(self):
        p = self.peer().armed()
        p.trigger()
        p.pull(1)
        p.device.disconnect(1)
        self.assertFalse(p.device.snapshot().negotiated)
        self.assertFalse(p.device.snapshot().held_samples)
        p.now = 1
        self.assertEqual(p.device.read(4096, 1), b"")
        # A real transport reset discards its incomplete receive frame.
        p.collector = ref.Collector()
        p.received.clear()
        p.hello()
        self.assertTrue(p.device.snapshot().negotiated)
        self.assertEqual(p.device.snapshot().generation, 0)
        self.assertIsNone(p.device.snapshot().terminal)


class MalformedAndGuards(Checks):
    def test_undecodable_frames_no_wire_error_or_command_mutation(self):
        valid = ref.packet(6)
        crc_raw = bytearray(ref.uncobs(valid[:-1]))
        crc_raw[-1] ^= 0x80
        malformed = [b"\x02\0", ref.cobs(bytes(crc_raw)) + b"\0",
                     ref.packet(6, major=1), ref.packet(6, minor=2),
                     ref.packet(6, reserved=1), ref.packet(6, reserved16=1),
                     ref.packet(6, declared_length=1), b"\x01" * 544 + b"\0"]
        for raw in malformed:
            with self.subTest(raw=raw[:30].hex()):
                p = self.peer()
                p.hello()
                before = p.device.snapshot()
                result = p.accept(raw)
                self.assertEqual(result.accepted_prefix, len(raw))
                frames = p.drain()
                self.assertFalse(choose(frames, 255), "undecodable frame invented ERROR request_id")
                after = p.device.snapshot()
                self.assertEqual(after.session, before.session)
                self.assertEqual(after.generation, before.generation)
                self.assertEqual(after.next_rx_sequence, before.next_rx_sequence)
                self.assertEqual(after.last_request_id, before.last_request_id)
                p.command(6)
                replies = p.drain()
                self.assertEqual(len([f for f in replies if f.kind == 134 and f.request == 2]), 1)

    def test_empty_delimiters_noise_and_timeout_recover_at_delimiter(self):
        p = self.peer()
        p.hello()
        self.assertEqual(p.accept(b"\0" * 4096).accepted_prefix, 4096)
        self.assertEqual(p.drain(), [])
        command = ref.packet(6)
        p.all(command[:5])
        diagnostics = p.device.tick(1000)
        self.assertIn("TIMEOUT", code_names(diagnostics))
        p.now = 1000
        p.all(command[5:])
        self.assertFalse([f for f in p.drain() if f.request == 2])
        p.all(command)
        self.assertEqual(len([f for f in p.drain() if f.request == 2]), 1)

    def test_invalid_hello_header_payload_nonce_leaves_active_state(self):
        candidates = [ref.packet(1, 0, 1, 1, ref.uint(0, 8)),
                      ref.packet(1, 0, 1, 1, b"short"),
                      ref.packet(1, 0, 1, 1, ref.uint(ref.NONCE + 1, 8) + b"\0"),
                      ref.packet(1, 1, 1, 1, ref.uint(ref.NONCE + 1, 8)),
                      ref.packet(1, 0, 2, 1, ref.uint(ref.NONCE + 1, 8)),
                      ref.packet(1, 0, 1, 2, ref.uint(ref.NONCE + 1, 8))]
        for raw in candidates:
            with self.subTest(raw=raw.hex()):
                p = self.peer().armed()
                before = p.device.snapshot()
                p.all(raw)
                self.assertEqual(p.drain(), [])
                after = p.device.snapshot()
                for name in ("session", "negotiated", "generation", "config_id", "state", "next_rx_sequence", "last_request_id"):
                    self.assertEqual(getattr(after, name), getattr(before, name))

    def test_wrong_session_is_ignored_and_direction_precedes_session_guard(self):
        p = self.peer().armed()
        before = p.device.snapshot()
        p.all(ref.packet(5, session=ref.NONCE + 1, sequence=5, request=5, payload=ref.uint(4, 4)))
        self.assertEqual(p.drain(), [])
        self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence)
        self.assertEqual(p.device.snapshot().state, 2)
        p.all(ref.packet(129, session=ref.NONCE + 1, sequence=5, request=5, payload=ref.hello_ack()))
        p.drain()
        self.assertFalse(p.device.snapshot().negotiated)
        self.assertFalse(p.device.trigger(p.now))

    def test_all_declared_device_types_wrong_direction_require_hello(self):
        for kind in (129, 130, 131, 132, 133, 134, 135, 144, 145, 146, 147, 255):
            with self.subTest(kind=kind):
                p = self.peer().ready()
                p.command(kind)
                frames = p.drain()
                self.assertFalse(choose(frames, 255))
                self.assertFalse(p.device.snapshot().negotiated)
                self.assertFalse(p.device.trigger(0))

    def test_unknown_types_valid_header_get_error_and_consume_sequence(self):
        for kind in (0, 8, 128, 136, 143, 148, 254):
            with self.subTest(kind=kind):
                p = self.peer()
                p.hello()
                p.command(kind)
                self.expect_error(p.drain(), kind, 5)
                self.assertEqual(p.device.snapshot().next_rx_sequence, 3)
                p.command(6)
                self.assertEqual(len([f for f in p.drain() if f.request == 3]), 1)

    def test_exact_previous_replay_ignored_conflict_gap_old_and_bad_requests_recover(self):
        p = self.peer()
        p.hello()
        raw = p.command(6)
        p.drain()
        sequence = p.device.snapshot().next_tx_sequence
        p.all(raw)
        self.assertEqual(p.drain(), [])
        self.assertEqual(p.device.snapshot().next_tx_sequence, sequence)
        invalid = [(2, 2, 2, b""), (6, 2, 3, b""), (6, 4, 4, b""),
                   (6, 1, 1, b""), (6, 3, 2, b""), (6, 3, 0, b""),
                   (6, 3, 1, b""), (6, 3, 2**32 - 1, b"")]
        for kind, seq, request, payload in invalid:
            with self.subTest(kind=kind, sequence=seq, request=request):
                p = self.peer()
                p.hello()
                p.command(6)
                p.drain()
                p.all(ref.packet(kind, sequence=seq, request=request, payload=payload))
                p.drain()
                self.assertFalse(p.device.snapshot().negotiated)
                p.all(ref.packet(6, sequence=3, request=3))
                self.assertFalse([f for f in p.drain() if f.request == 3])
                p.hello(ref.NONCE + 1)
                self.assertTrue(p.device.snapshot().negotiated)

    def test_request_gaps_are_allowed_sequence_gaps_are_not(self):
        p = self.peer()
        p.hello()
        p.command(6, request=500)
        self.assertEqual([f.request for f in p.drain() if f.request], [500])
        self.assertEqual(p.device.snapshot().last_request_id, 500)
        self.assertTrue(p.device.snapshot().negotiated)

    def test_invalid_lengths_all_known_commands_consume_then_error(self):
        valid = {2: b"", 3: ref.config(), 4: ref.uint(1, 4), 5: ref.uint(0, 4), 6: b"", 7: ref.release()}
        for kind, payload in valid.items():
            for wrong in ((payload + b"\0",) if not payload else (payload[:-1], payload + b"\0")):
                with self.subTest(kind=kind, length=len(wrong)):
                    p = self.peer()
                    p.hello()
                    p.command(kind, wrong)
                    self.expect_error(p.drain(), kind, 6)
                    self.assertEqual(p.device.snapshot().next_rx_sequence, 3)
                    p.command(6)
                    self.assertEqual(len([f for f in p.drain() if f.request == 3]), 1)

    def test_invalid_config_never_changes_prior_valid_settings(self):
        cases = [ref.config(profile=0), ref.config(profile=2), ref.config(mode=0, timeout=100),
                 ref.config(mode=2), ref.config(mode=3), ref.config(source=1), ref.config(source=2),
                 ref.config(edge=2), ref.config(level=0), ref.config(level=4095), ref.config(level=65535),
                 ref.config(pre=512), ref.config(pre=1024), ref.config(pre=1), ref.config(timeout=100),
                 ref.config(reserved8=1), ref.config(reserved16=1)]
        for payload in cases:
            with self.subTest(payload=payload.hex()):
                p = self.peer().ready()
                before = p.device.snapshot()
                p.command(3, payload)
                expected = 6 if payload[5] or payload[10:12] != b"\0\0" else 1
                self.expect_error(p.drain(), 3, expected)
                self.assertEqual(p.device.snapshot().config, before.config)
                self.assertEqual(p.device.snapshot().config_id, before.config_id)

    def test_arm_without_or_stale_config_does_not_capture(self):
        p = self.peer()
        p.hello()
        p.command(4, ref.uint(0, 4))
        self.expect_error(p.drain(), 4, 3)
        p = self.peer().ready()
        p.command(4, ref.uint(2, 4))
        self.expect_error(p.drain(), 4, 3)
        self.assertFalse(p.device.trigger(0))


class LateTombstoneIsolation(Checks):
    def test_previous_tombstone_release_cannot_free_or_rearm_current_window(self):
        for partial_data in (False, True):
            with self.subTest(partial_data=partial_data):
                p, _ = self.peer().completed()
                p.command(7, ref.release())
                p.drain()
                p.trigger()
                if partial_data:
                    p.until(144)
                    p.pull(1)
                before = p.device.snapshot()
                self.assertEqual(before.window_id, 2)
                self.assertTrue(before.held_samples)
                request = p.request
                p.command(7, ref.release())
                # An old matching tombstone gets an ACK, never authority over
                # the different, currently held window-2 buffer/continuation.
                self.assertTrue(p.device.snapshot().held_samples)
                self.assertFalse(p.device.trigger(p.now))
                frames = p.drain(1)
                self.assertEqual(len([f for f in frames if f.kind == 135 and f.request == request]), 1)
                window_frames = [f for f in p.collector.frames if f.kind in (144, 145, 146)
                                 and ref.number(f.payload[4:8]) == 2]
                self.reconstruct(window_frames, 4, 2)
                self.assertEqual(p.device.snapshot().state, 6)
                self.assertTrue(p.device.snapshot().held_samples)
                self.assertFalse(p.device.trigger(p.now))
                p.command(7, ref.release(4, 2))
                p.drain()
                self.assertEqual(p.device.snapshot().state, 2)


class BoundsAndStrictArguments(Checks):
    def unchanged_error(self, device, method, *args, code=None):
        before = device.snapshot()
        with self.assertRaises(DeviceError) as caught:
            method(*args)
        if code is not None:
            self.assertEqual(caught.exception.code.name, code)
        self.assertEqual(device.snapshot(), before)

    def test_all_public_mutators_reject_wrong_python_time_and_rollback_atomically(self):
        p = self.peer().armed()
        p.trigger()
        p.pull(1)
        p.now = 10
        p.device.tick(10)
        for bad in (True, False, 1.0, "11", None, object()):
            for method, args in ((p.device.write, (b"", bad)), (p.device.read, (0, bad)),
                                 (p.device.trigger, (bad,)), (p.device.tick, (bad,)),
                                 (p.device.disconnect, (bad,))):
                with self.subTest(method=method.__name__, bad=repr(bad)):
                    self.unchanged_error(p.device, method, *args, code="ARGUMENT")
        for bad, code in ((-1, "RANGE"), (MAX_TIME + 1, "RANGE"), (9, "CLOCK_ROLLBACK")):
            for method, args in ((p.device.write, (b"", bad)), (p.device.read, (0, bad)),
                                 (p.device.trigger, (bad,)), (p.device.tick, (bad,)),
                                 (p.device.disconnect, (bad,))):
                with self.subTest(method=method.__name__, bad=bad):
                    self.unchanged_error(p.device, method, *args, code=code)

    def test_invalid_input_type_and_read_bounds_before_len_or_copy(self):
        class Poison:
            def __bytes__(self):
                raise AssertionError("invalid data copied")
            def __len__(self):
                raise AssertionError("invalid data length inspected")
        class PoisonBytes(bytes):
            def __len__(self):
                raise AssertionError("bytes subclass inspected before rejection")
        class IntSubclass(int):
            pass
        p = self.peer().armed()
        for data in (bytearray(b"x"), memoryview(b"x"), "x", [1], None, Poison(), PoisonBytes(b"x")):
            self.unchanged_error(p.device, p.device.write, data, 0, code="ARGUMENT")
        self.unchanged_error(p.device, p.device.write, b"x" * 4097, 0, code="CHUNK_SIZE")
        for count in (True, False, 1.0, "1", None, IntSubclass(1)):
            self.unchanged_error(p.device, p.device.read, count, 0, code="ARGUMENT")
        for count in (-1, 4097, 2**100):
            self.unchanged_error(p.device, p.device.read, count, 0)
        self.unchanged_error(p.device, p.device.tick, IntSubclass(0), code="ARGUMENT")
        p.device.tick(MAX_TIME)
        self.assertEqual(p.device.snapshot().now_ms, MAX_TIME)

    def test_backpressure_retains_caller_suffix_and_all_mandatory_replies(self):
        p = self.peer()
        p.hello()
        commands = b"".join(ref.packet(6, sequence=i, request=i) for i in range(2, 112))
        self.assertLessEqual(len(commands), 4096)
        result = p.accept(commands)
        self.assertLess(result.accepted_prefix, len(commands), "unbounded command acceptance with stalled TX")
        self.assertIn("BACKPRESSURE", code_names(result.diagnostics))
        pending = commands[result.accepted_prefix:]
        self.assertTrue(pending)
        # The suffix remains entirely test-owned. Zero acceptance never lets the
        # producer discard it; alternating one-byte drains stresses all edges.
        for _ in range(15000):
            snap = p.device.snapshot()
            self.assertLessEqual(snap.control_descriptors, 8)
            self.assertLessEqual(snap.rx_used, 543)
            self.assertLessEqual(snap.tx_size, 544)
            self.assertEqual(snap.sample_buffer_bytes, 4096)
            if pending:
                item = p.accept(pending)
                pending = pending[item.accepted_prefix:]
            output = p.pull(1)
            if not pending and not output:
                break
        else:
            self.fail("backpressure workload did not drain")
        requests = [f.request for f in p.collector.frames if f.request > 1]
        self.assertEqual(requests, list(range(2, 112)))
        self.contiguous(p.collector.frames)

    def test_stalled_response_capacity_never_consumes_stop_silently(self):
        p = self.peer().armed()
        # Queue enough independent ordinary replies to force input backpressure,
        # then append Stop. This intentionally exceeds the usual host outstanding
        # request policy to test the emulator's adversarial resource boundary.
        raw = b"".join(ref.packet(6, sequence=i, request=i) for i in range(5, 25))
        raw += ref.packet(5, sequence=25, request=25, payload=ref.uint(4, 4))
        p.all(raw)
        p.drain(1)
        replies = [f for f in p.collector.frames if f.request >= 5]
        self.assertEqual([f.request for f in replies], list(range(5, 26)))
        self.assertEqual(replies[-1].kind, 133)
        self.assertEqual(p.device.snapshot().state, 0)
        self.assertFalse(choose(p.collector.frames, 147))

    def test_retained_memory_stays_bounded_across_repeated_cycles(self):
        p = self.peer().ready()
        tracemalloc.start()
        try:
            baselines = []
            for cycle in range(180):
                generation = p.request
                p.command(4, ref.uint(1, 4))
                p.drain()
                p.trigger()
                p.drain()
                p.command(5, ref.uint(generation, 4))
                p.drain()
                snap = p.device.snapshot()
                self.assertLessEqual(snap.control_descriptors, 8)
                self.assertLessEqual(snap.rx_used, 543)
                self.assertLessEqual(snap.tx_size, 544)
                self.assertEqual(snap.sample_buffer_bytes, 4096)
                self.assertFalse(snap.held_samples)
                self.assertEqual(snap.window_id, cycle + 1)
                # Test transcript retention is caller-owned and deliberately
                # cleared before measuring device steady-state retention.
                p.collector.frames.clear()
                p.received.clear()
                if cycle in (19, 179):
                    gc.collect()
                    baselines.append(tracemalloc.get_traced_memory()[0])
            self.assertLess(baselines[1] - baselines[0], 32768,
                            "emulator retains per-window histories instead of fixed bounded state")
        finally:
            tracemalloc.stop()

    def test_maximum_request_and_sequence_accept_once_then_no_wrap(self):
        p = self.peer()
        p.hello()
        # Explicit white-box boundary staging; oracle remains the c54 rule, not
        # the implementation. Reaching this boundary by 2^32 calls is impractical.
        p.device._next_rx = MAX_ID
        p.device._last_request = MAX_ID - 1
        raw = ref.packet(6, sequence=MAX_ID, request=MAX_ID)
        p.all(raw)
        self.assertEqual([f.request for f in p.drain() if f.request], [MAX_ID])
        before = p.device.snapshot()
        p.all(raw)
        self.assertEqual(p.drain(), [])
        self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence)
        p.all(ref.packet(6, sequence=MAX_ID + 1, request=MAX_ID + 1))
        p.drain()
        self.assertFalse(p.device.snapshot().negotiated)
        p.hello(ref.NONCE + 1)
        self.assertEqual(p.device.snapshot().next_rx_sequence, 2)

    def test_last_tx_sequence_commits_only_first_byte_then_exhausts(self):
        p = self.peer()
        p.hello()
        p.device._next_tx = MAX_ID
        p.command(6)
        self.assertEqual(p.pull(0), b"")
        self.assertEqual(p.device.snapshot().next_tx_sequence, MAX_ID)
        self.assertTrue(p.pull(1))
        self.assertEqual(p.device.snapshot().next_tx_sequence, MAX_ID + 1)
        frames = p.drain(1)
        self.assertEqual([f.sequence for f in frames], [MAX_ID])
        p.command(6)
        frames = p.drain()
        self.assertFalse(frames, "no legal fresh TX sequence remains")
        self.assertFalse(p.device.snapshot().negotiated)
        self.assertEqual(p.device.snapshot().session, ref.NONCE)
        p.hello(ref.NONCE + 1)
        self.assertEqual(p.device.snapshot().next_tx_sequence, 2)

    def test_config_window_and_dropped_saturation_fail_closed(self):
        for counter in ("_config_id", "_window_id", "_dropped"):
            with self.subTest(counter=counter):
                p = self.peer().ready()
                if counter == "_config_id":
                    setattr(p.device, counter, 2**32 - 1)
                    p.command(3, ref.config())
                else:
                    p.command(4, ref.uint(1, 4))
                    p.drain()
                    if counter == "_window_id":
                        setattr(p.device, counter, 2**32 - 1)
                        self.assertFalse(p.device.trigger(0))
                    else:
                        p.trigger()
                        setattr(p.device, counter, 2**32 - 1)
                        p.command(5, ref.uint(4, 4))
                frames = p.drain()
                self.assertFalse(p.device.snapshot().negotiated)
                self.assertEqual(p.device.snapshot().reason, 6)
                self.assertFalse(p.device.trigger(0))
                statuses = [f for f in frames if f.kind == 134 and f.payload[:2] == b"\5\6"]
                self.assertTrue(statuses, "saturation needs visible ERROR/COUNTER_SATURATED when TX available")
                self.assertFalse(any(f.kind in (144, 145, 146) for f in frames))


class Host006ClosedLoop(Checks):
    def pump(self, p, host, events, tx_chunk=7, rx_chunk=19, corrupt_sample=False):
        from motortester.wire import Frame
        for _ in range(15000):
            progress = False
            ticket = host.prepare_tx()
            if ticket is not None:
                suffix = ticket.encoded[ticket.offset:ticket.offset + tx_chunk]
                result = p.accept(suffix)
                events.extend(host.written(ticket, result.accepted_prefix, p.now))
                progress = bool(result.accepted_prefix)
            before = len(p.collector.frames)
            output = p.pull(rx_chunk)
            progress = progress or bool(output)
            for parsed in p.collector.frames[before:]:
                payload = parsed.payload
                if corrupt_sample and parsed.kind == 145:
                    # Keep the per-frame header/shape valid, but change a sample
                    # without changing WINDOW_END's sample-array CRC. The control
                    # module deliberately does not validate the sample array.
                    payload = payload[:12] + ref.uint(ref.number(payload[12:14]) ^ 1, 2) + payload[14:]
                events.extend(host.receive(Frame(parsed.kind, parsed.session, parsed.sequence,
                                                 parsed.request, payload), p.now))
            self.assertFalse([e for e in events if e.kind.name == "RECOVERY"])
            if not progress:
                self.assertFalse(p.collector.pending)
                return
        self.fail("HOST006 byte closed-loop failed to settle")

    def configured(self):
        from motortester.session import Config, SessionController
        p, host, events = self.peer(), SessionController(), []
        events.extend(host.start(ref.NONCE, 0))
        self.pump(p, host, events)
        self.assertEqual(host.snapshot().phase.name, "READY")
        events.extend(host.configure(Config(), 0))
        self.pump(p, host, events)
        self.assertEqual(host.snapshot().config_id, 1)
        return p, host, events

    def test_twenty_arm_trigger_bytes_end_stop_cycles_exact_identity_and_crc(self):
        p, host, events = self.configured()
        for cycle in range(20):
            with self.subTest(cycle=cycle + 1):
                events.clear()
                events.extend(host.arm(0))
                self.pump(p, host, events, tx_chunk=(1, 64, 4096)[cycle % 3], rx_chunk=(1, 17, 4096)[cycle % 3])
                generation = host.snapshot().generation
                self.assertEqual(p.device.snapshot().generation, generation)
                start = len(p.collector.frames)
                p.trigger()
                self.pump(p, host, events, rx_chunk=(1, 31, 4096)[cycle % 3])
                frames = p.collector.frames[start:]
                raw = self.reconstruct(frames, generation, cycle + 1)
                self.assertEqual(len(raw), 4096)
                self.assertEqual(sum(e.kind.name == "WINDOW_END_ADMITTED" for e in events), 1)
                # END_ADMITTED is only a control event. Independent reconstruction
                # above supplies the complete-array/2048-code/CRC evidence.
                self.assertEqual(host.snapshot().phase.name, "ARMED")
                events.extend(host.stop(0))
                self.pump(p, host, events, tx_chunk=1, rx_chunk=1)
                self.assertEqual(host.snapshot().phase.name, "READY")
                self.assertFalse(host.snapshot().stop_barrier)
                self.assertFalse(host.snapshot().window_barrier)
                self.assertEqual(p.device.snapshot().state, 0)
                self.assertFalse(p.device.snapshot().held_samples)
                self.assertEqual(sum(e.kind.name == "STOPPED" for e in events), 1)
        self.assertEqual(len(choose(p.collector.frames, 146)), 20)
        self.assertEqual(len(choose(p.collector.frames, 133)), 20)
        self.assertFalse(choose(p.collector.frames, 147))
        self.contiguous(p.collector.frames)

    def test_end_admission_explicitly_is_not_sample_validation(self):
        p, host, events = self.configured()
        events.clear()
        events.extend(host.arm(0))
        self.pump(p, host, events)
        start = len(p.collector.frames)
        p.trigger()
        self.pump(p, host, events, corrupt_sample=True)
        self.assertEqual(sum(e.kind.name == "WINDOW_END_ADMITTED" for e in events), 1)
        frames = p.collector.frames[start:]
        altered = bytearray()
        for block in choose(frames, 145):
            data = block.payload[12:]
            altered.extend(ref.uint(ref.number(data[:2]) ^ 1, 2) + data[2:])
        self.assertNotEqual(ref.crc32(altered), ref.number(choose(frames, 146)[0].payload[-4:]))
        self.assertNotEqual(bytes(altered), ref.samples())

    def test_existing_session_and_wire_files_keep_own_pins(self):
        import motortester.session as session
        import motortester.wire as wire
        self.assertEqual(session.SOURCE_SHA, "b6ae8025baf1a47e8173997029d2d8b858ada74c")
        # HOST-007's byte-for-byte session pin describes its historical run,
        # not a prohibition on later reviewed controller extensions. Keep that
        # evidence immutable; current candidates bind their own source hashes.
        manifest = json.loads((FIXTURES / "evidence" / "manifest.json").read_text())
        self.assertEqual(manifest["base_sha"], "477057aadc9bc8e757bc8ef817125377e816d212")
        for section in ("sources_before_sha256", "sources_after_sha256"):
            self.assertEqual(manifest[section]["host/motortester/session.py"],
                             "4049e5fda8499010bcdcad781af5a5ebfefde5de2b583e04b606b59bf1f61f61")
        self.assertEqual(hashlib.sha256(Path(wire.__file__).read_bytes()).hexdigest(),
                         "a8fa8edf58c22e87994674ad23fc654e4da93e229cfe21d482dbec3116bc8230")


class ProvisionalHoldTimeout(Checks):
    """UNACCEPTED assumption: deadline starts at final accepted END byte.

    These checks exercise a disclosed candidate interpretation; passing them is
    not acceptance of c54 semantics. A's PR14 clarification remains outstanding.
    """
    def test_provisional_deadline_just_before_exact_and_after_preserves_end(self):
        for elapsed in (4999, 5000, 5001):
            with self.subTest(provisional_elapsed=elapsed):
                p, _ = self.peer().completed()
                terminal = p.device.snapshot().terminal
                anchor = p.device.snapshot().hold_started_ms
                self.assertEqual(anchor, 0)
                p.device.tick(anchor + elapsed)
                p.now = anchor + elapsed
                frames = p.drain()
                self.assertEqual(p.device.snapshot().state, 6 if elapsed < 5000 else 5)
                self.assertEqual(p.device.snapshot().reason, 0 if elapsed < 5000 else 3)
                self.assertEqual(p.device.snapshot().terminal, terminal)
                self.assertEqual(p.device.snapshot().tombstone, terminal)
                self.assertFalse(any(f.kind in (146, 147) for f in frames))
                if elapsed >= 5000:
                    self.assertTrue([f for f in frames if f.kind == 134 and f.payload[:2] == b"\5\3"])
                    self.assertFalse(p.device.trigger(p.now))

    def test_provisional_anchor_is_final_end_byte_not_trigger_or_first_byte(self):
        reference = self.peer().armed()
        reference.trigger()
        trace = reference.drain()
        p = self.peer().armed()
        p.trigger()
        for frame in trace:
            if frame.kind == 146:
                end_frame = frame
                break
            self.assertEqual(p.one().packet, frame.packet)
        self.assertEqual(p.pull(1), end_frame.packet[:1])
        self.assertIsNone(p.device.snapshot().hold_started_ms)
        p.device.tick(6000)
        p.now = 6000
        self.assertEqual(p.device.snapshot().state, 4)
        self.assertEqual(p.pull(len(end_frame.packet) - 1), end_frame.packet[1:])
        self.assertEqual(p.device.snapshot().hold_started_ms, 6000)
        p.drain()
        p.device.tick(10999)
        self.assertEqual(p.device.snapshot().state, 6)
        p.device.tick(11000)
        self.assertEqual(p.device.snapshot().state, 5)
        self.assertEqual(p.device.snapshot().reason, 3)

    def test_provisional_release_before_deadline_ack_after_deadline_rearms(self):
        p, _ = self.peer().completed()
        p.now = 4999
        request, sequence = p.request, p.device.snapshot().next_tx_sequence
        p.command(7, ref.release())
        ack = ref.packet(135, sequence=sequence, request=request, payload=ref.uint(4, 4) + ref.uint(1, 4))
        self.assertEqual(p.pull(len(ack) - 1), ack[:-1])
        self.assertFalse(p.device.trigger(p.now))
        self.assertEqual(p.pull(1, 9000), ack[-1:])
        self.assertEqual(p.device.snapshot().state, 2)
        self.assertEqual(p.device.snapshot().reason, 0)

    def test_provisional_release_at_or_after_deadline_acks_without_rearm(self):
        for elapsed in (5000, 5001):
            with self.subTest(provisional_elapsed=elapsed):
                p, _ = self.peer().completed()
                p.now = elapsed
                p.command(7, ref.release())
                frames = p.drain()
                self.assertEqual(len(choose(frames, 135)), 1)
                self.assertFalse(p.device.trigger(elapsed))
                self.assertNotEqual(p.device.snapshot().state, 2)
                self.assertEqual(len(choose(p.collector.frames, 146)), 1)
                self.assertFalse(choose(p.collector.frames, 147))

    def test_provisional_partial_release_crossing_deadline_cannot_continue(self):
        p, _ = self.peer().completed()
        raw = ref.packet(7, sequence=p.sequence, request=p.request, payload=ref.release())
        p.all(raw[:-1], 4999)
        p.all(raw[-1:], 5000)
        frames = p.drain()
        self.assertEqual(len(choose(frames, 135)), 1)
        self.assertFalse(p.device.trigger(5000))
        self.assertNotEqual(p.device.snapshot().state, 2)

    def test_provisional_stop_before_exact_after_deadline_has_one_terminal(self):
        for elapsed in (4999, 5000, 5001):
            with self.subTest(provisional_elapsed=elapsed):
                p, _ = self.peer().completed()
                p.now = elapsed
                p.stop()
                self.assertEqual(p.device.snapshot().state, 0)
                self.assertEqual(len(choose(p.collector.frames, 146)), 1)
                self.assertFalse(choose(p.collector.frames, 147))
                self.assertEqual(p.device.snapshot().tombstone.outcome, "END")

    def test_provisional_deadline_arithmetic_near_uint64_maximum_does_not_wrap(self):
        p = self.peer().armed()
        p.now = MAX_TIME - 10
        p.trigger()
        p.drain()
        self.assertEqual(p.device.snapshot().hold_started_ms, MAX_TIME - 10)
        p.device.tick(MAX_TIME)
        self.assertEqual(p.device.snapshot().state, 6)
        self.assertEqual(p.device.snapshot().reason, 0)


class RemainingDeclaredBoundaries(Checks):
    def test_every_remaining_command_split_boundary(self):
        for kind, setup, payload, reply in ((2, "hello", b"", 130),
                                            (4, "ready", ref.uint(1, 4), 132),
                                            (5, "armed", ref.uint(4, 4), 133),
                                            (6, "ready", b"", 134),
                                            (7, "completed", ref.release(), 135)):
            sample = ref.packet(kind, payload=payload)
            for boundary in range(len(sample) + 1):
                with self.subTest(kind=kind, boundary=boundary):
                    p = self.peer()
                    getattr(p, setup)()
                    request = p.request
                    raw = ref.packet(kind, sequence=p.sequence, request=request, payload=payload)
                    p.all(raw[:boundary])
                    p.all(raw[boundary:])
                    frames = p.drain(1)
                    self.assertEqual([(f.kind, f.request) for f in frames if f.request], [(reply, request)])
                    self.contiguous(p.collector.frames)

    def test_repeated_stop_while_abort_pending_or_partial_has_one_terminal(self):
        for accepted in (0, 1, "last_minus_one"):
            with self.subTest(accepted=accepted):
                p = self.peer().armed()
                p.trigger()
                sequence = p.device.snapshot().next_tx_sequence
                p.command(5, ref.uint(4, 4))
                expected = ref.packet(147, sequence=sequence, request=0, payload=ref.abort())
                count = len(expected) - 1 if accepted == "last_minus_one" else accepted
                if count:
                    self.assertEqual(p.pull(count), expected[:count])
                p.command(5, ref.uint(4, 4))
                p.drain(1)
                self.assertEqual(len(choose(p.collector.frames, 147)), 1)
                self.assertFalse(choose(p.collector.frames, 146))
                replies = [(f.kind, f.request) for f in p.collector.frames if f.kind == 133]
                self.assertEqual(replies, [(133, 5), (133, 6)])
                abort_index = p.collector.frames.index(choose(p.collector.frames, 147)[0])
                self.assertTrue(all(p.collector.frames.index(f) > abort_index for f in choose(p.collector.frames, 133)))
                self.assertEqual(p.device.snapshot().state, 0)
                self.assertFalse(p.device.snapshot().stop_barrier)
                self.contiguous(p.collector.frames)

    def test_new_hello_during_partial_abort_cancels_old_stop_completion(self):
        p = self.peer().armed()
        p.trigger()
        p.command(5, ref.uint(4, 4))
        self.assertTrue(p.pull(1))
        p.all(ref.hello(ref.NONCE + 1))
        frames = p.drain(1)
        self.assertEqual([f.kind for f in frames if f.kind in (129, 133, 147)], [147, 129])
        self.assertEqual(p.device.snapshot().session, ref.NONCE + 1)
        self.assertEqual(p.device.snapshot().state, 0)
        self.assertEqual(p.device.snapshot().generation, 0)
        self.assertFalse(p.device.snapshot().stop_barrier)
        self.assertIsNone(p.device.snapshot().terminal)

    def test_rx_interbyte_and_total_timeout_exact_thresholds(self):
        for elapsed in (999, 1000, 1001):
            with self.subTest(interbyte_elapsed=elapsed):
                p = self.peer()
                p.hello()
                command = ref.packet(6)
                p.all(command[:1])
                p.all(command[1:], elapsed)
                replies = [f for f in p.drain() if f.request == 2]
                self.assertEqual(len(replies), 1 if elapsed < 1000 else 0)
                if elapsed >= 1000:
                    p.all(command)
                    self.assertEqual(len([f for f in p.drain() if f.request == 2]), 1)
        p = self.peer()
        p.hello()
        raw = ref.packet(6)
        for index, now in enumerate((0, 999, 1998, 2997)):
            p.all(raw[index:index + 1], now)
        result = p.device.tick(3000)
        self.assertIn("TIMEOUT", code_names(result))
        p.all(raw[4:], 3000)
        self.assertEqual(p.drain(), [])
        p.all(raw)
        self.assertEqual(len([f for f in p.drain() if f.request == 2]), 1)

    def test_crc_drop_followed_by_sequence_gap_requires_recovery(self):
        p = self.peer().ready()
        raw = bytearray(ref.uncobs(ref.packet(6, sequence=p.sequence, request=p.request)[:-1]))
        raw[-4] ^= 1
        p.all(ref.cobs(bytes(raw)) + b"\0")
        self.assertEqual(p.drain(), [])
        p.all(ref.packet(6, sequence=p.sequence + 1, request=p.request + 1))
        p.drain()
        self.assertFalse(p.device.snapshot().negotiated)
        self.assertFalse(p.device.trigger(0))

    def test_not_negotiated_commands_never_arm_or_invent_reply(self):
        p = self.peer()
        for kind, payload in ((2, b""), (3, ref.config()), (4, ref.uint(1, 4)), (5, ref.uint(0, 4)),
                              (6, b""), (7, ref.release())):
            p.all(ref.packet(kind, payload=payload))
            self.assertEqual(p.drain(), [])
            self.assertFalse(p.device.snapshot().negotiated)
            self.assertFalse(p.device.trigger(0))
        p.hello()
        self.assertTrue(p.device.snapshot().negotiated)


if __name__ == "__main__":
    unittest.main()

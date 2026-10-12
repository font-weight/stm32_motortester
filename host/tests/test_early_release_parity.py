"""Авторские public-byte проверки узкой defensive parity; без firmware-копии."""
import importlib.util
from pathlib import Path
import struct
import sys
import unittest

from motortester.device_emulator import DeviceEmulator

ROOT = Path(__file__).parent / 'fixtures' / 'early_release_parity'
_spec = importlib.util.spec_from_file_location('early_release_raw', ROOT / 'raw_peer.py')
raw = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = raw
_spec.loader.exec_module(raw)
END_SIZE = 46  # 24 header + 16 payload + 4 CRC + COBS code/delimiter.


def diagnostics(result):
    return tuple((str(getattr(d.code, 'value', d.code)), d.count) for d in result.diagnostics)


def setup(case, mode=1, edge=0, prefix=1, *, automatic=False, enabled=True):
    p = raw.Peer(case, lambda: DeviceEmulator(all_analog_modes=enabled))
    p.mode, p.edge = mode, edge
    p.write(raw.hello())
    case.assertEqual([f.kind for f in p.drain()[1]], [129])
    config = struct.pack('<HBBBBHHHI', 1, mode, 0, edge, 0, 2048, 0, 0,
                         100 if mode == 0 else 0)
    p.command(3, config)
    case.assertEqual(p.one().payload, raw.u(1) + config)
    p.command(4, raw.u(1))
    case.assertEqual([f.kind for f in p.drain()[1]], [132, 134])
    capture(case, p, automatic=automatic)
    case.assertEqual(p.read(prefix), p.end[:prefix])
    return p


def capture(case, p, *, automatic=False):
    if automatic:
        p.now += 100
        p.device.tick(p.now)
    else:
        triggered = p.device.trigger(p.now)
        case.assertTrue(triggered)
    case.assertEqual(p.one().kind, 134)
    begin = p.one()
    case.assertEqual(begin.kind, 144)
    case.assertEqual(begin.payload[20], 2 if automatic else 0)
    data = bytearray()
    for offset in range(0, 2048, 250):
        frame = p.one()
        case.assertEqual(frame.kind, 145)
        generation, window, first, count = struct.unpack('<IIHH', frame.payload[:12])
        case.assertEqual(first, offset)
        case.assertEqual(count, min(250, 2048 - offset))
        case.assertEqual(len(frame.payload), 12 + 2 * count)
        data.extend(frame.payload[12:])
    p.samples = bytes(data)
    p.buffer_identity = id(p.device._samples)  # Только наблюдение storage, без mutation.
    case.assertEqual(p.samples, bytes(p.device._samples))
    case.assertEqual(len(p.samples), 4096)
    p.release = raw.u(generation) + raw.u(window) + raw.u(raw.crc(p.samples))
    payload = raw.u(generation) + raw.u(window) + raw.u(2048, 2) + b'\0\0' + p.release[-4:]
    p.end = raw.packet(146, p.device.snapshot().next_tx_sequence, 0, payload)
    case.assertEqual(len(p.end), END_SIZE)
    case.assertEqual(p.device.snapshot().tx_size, 0)


def stable_storage(case, p):
    case.assertEqual(id(p.device._samples), p.buffer_identity)
    case.assertEqual(bytes(p.device._samples), p.samples)
    case.assertEqual(p.device.snapshot().sample_buffer_bytes, 4096)


def release_reply(case, p, code, *, payload=None):
    request = p.request
    p.command(7, p.release if payload is None else payload)
    frames = p.drain()[1]
    replies = [f for f in frames if f.request == request]
    case.assertEqual(len(replies), 1)
    reply = replies[0]
    if code is None:
        case.assertEqual((reply.kind, reply.payload), (135, p.release[:8]))
    else:
        case.assertEqual((reply.kind, reply.payload),
                         (255, bytes((7, code, 0, 0)) + raw.u(p.device.snapshot().generation)))
    return frames


class Checks(unittest.TestCase):
    def unchanged_hold(self, p, before):
        after = p.device.snapshot()
        for name in ('state', 'reason', 'session', 'negotiated', 'generation', 'window_id',
                     'config', 'config_id', 'held_samples', 'terminal', 'tombstone',
                     'tx_offset', 'tx_size', 'hold_started_ms', 'dropped_windows',
                     'data_offset', 'continuation', 'next_tx_sequence'):
            self.assertEqual(getattr(after, name), getattr(before, name), name)
        stable_storage(self, p)
        self.assertFalse(p.device.trigger(p.now))

    def finish_release(self, p, *, expected_state=None):
        before = p.device.snapshot()
        request = p.request
        p.command(7, p.release)
        after = p.device.snapshot()
        self.assertFalse(after.held_samples)
        self.assertIsNone(after.hold_started_ms)
        self.assertEqual(after.state, before.state)
        # A queued STATUS from final END precedes the ACK; no frame is discarded.
        while p.device.snapshot().control_descriptors > 1:
            self.assertEqual(p.one().kind, 134)
        ack = raw.packet(135, p.device.snapshot().next_tx_sequence, request, p.release[:8])
        self.assertEqual(p.read(1), ack[:1])
        self.assertFalse(p.device.trigger(p.now))
        stable_storage(self, p)
        self.assertEqual(p.read(len(ack) - 2), ack[1:-1])
        self.assertEqual(p.device.snapshot().state, before.state)
        self.assertFalse(p.device.trigger(p.now))
        stable_storage(self, p)
        self.assertEqual(p.read(1), ack[-1:])
        wanted = (0 if p.mode == 2 else 2) if expected_state is None else expected_state
        self.assertEqual(p.device.snapshot().state, wanted)
        self.assertEqual(p.device.snapshot().continuation, wanted == 2)
        stable_storage(self, p)
        p.drain()
        return ack

    def partial_case(self, mode, edge, prefix, *, automatic=False):
        p = setup(self, mode, edge, prefix, automatic=automatic)
        before = p.device.snapshot()
        self.assertEqual(before.tx_offset, prefix)
        self.assertTrue(before.held_samples)
        self.assertIsNone(before.hold_started_ms)
        self.assertEqual(before.terminal.outcome, 'END')
        request = p.request
        _, result = p.command(7, p.release)
        self.assertEqual(diagnostics(result), ())
        self.unchanged_hold(p, before)
        self.assertEqual(p.device.snapshot().control_descriptors, before.control_descriptors + 1)
        self.assertEqual(p.read(END_SIZE - prefix), p.end[prefix:])
        self.assertEqual(p.device.snapshot().hold_started_ms, p.now)
        self.assertEqual(p.device.snapshot().state, 6)
        reply = p.one()
        self.assertEqual((reply.kind, reply.request, reply.payload),
                         (255, request, b'\7\2\0\0' + raw.u(3)))
        p.drain()
        self.finish_release(p)
        # Replay is ACK-only: no second rearm, new window, timer reset or sample reuse.
        before = p.device.snapshot()
        release_reply(self, p, None)
        after = p.device.snapshot()
        self.assertEqual((after.state, after.continuation, after.window_id),
                         (before.state, before.continuation, before.window_id))
        stable_storage(self, p)
        if mode == 2:
            self.assertFalse(p.device.trigger(p.now))
            p.command(4, raw.u(1))
            p.drain()
            self.assertEqual(p.device.snapshot().state, 2)
            self.assertTrue(p.device.trigger(p.now))


class EveryPartialEnd(Checks):
    pass


def matrix_case(mode, edge, prefix):
    def test(self):
        self.partial_case(mode, edge, prefix)
    return test


for _mode in range(3):
    for _edge in range(2):
        for _prefix in range(1, END_SIZE):
            setattr(EveryPartialEnd, f'test_mode_{_mode}_edge_{_edge}_prefix_{_prefix:02d}',
                    matrix_case(_mode, _edge, _prefix))


class BoundaryAndPrecedence(Checks):
    def test_zero_end_no_tombstone_is_bad_release_in_every_mode_edge(self):
        for mode in range(3):
            for edge in range(2):
                with self.subTest(mode=mode, edge=edge):
                    p = setup(self, mode, edge, 0)
                    before = p.device.snapshot()
                    self.assertIsNone(before.tombstone)
                    self.assertEqual(p.read(0), b'')
                    self.assertIsNone(p.device.snapshot().tombstone)
                    request = p.request
                    p.command(7, p.release)
                    self.unchanged_hold(p, before)
                    reply = p.one()
                    self.assertEqual((reply.kind, reply.request, reply.payload[:2]), (255, request, b'\7\7'))
                    self.assertIsNone(p.device.snapshot().tombstone)
                    p.drain()
                    self.finish_release(p)

    def test_final_end_admits_release_in_every_mode_edge(self):
        for mode in range(3):
            for edge in range(2):
                with self.subTest(mode=mode, edge=edge):
                    p = setup(self, mode, edge, END_SIZE)
                    self.finish_release(p)

    def test_auto_timeout_partial_end_both_edges(self):
        for edge in range(2):
            for prefix in (1, END_SIZE - 1):
                with self.subTest(edge=edge, prefix=prefix):
                    self.partial_case(0, edge, prefix, automatic=True)

    def test_wrong_tuple_and_wrong_length_precede_busy(self):
        for prefix in (0, 1, END_SIZE - 1, END_SIZE):
            for field in (0, 1, 2, 'short', 'long'):
                with self.subTest(prefix=prefix, field=field):
                    p = setup(self, prefix=prefix)
                    before = p.device.snapshot()
                    payload = bytearray(p.release)
                    if field == 'short':
                        payload = payload[:-1]
                    elif field == 'long':
                        payload.append(0)
                    else:
                        payload[4 * field] ^= 1
                    request = p.request
                    p.command(7, bytes(payload))
                    self.unchanged_hold(p, before)
                    frames = p.drain()[1]
                    replies = [f for f in frames if f.request == request]
                    self.assertEqual([(f.kind, f.payload[:2]) for f in replies],
                                     [(255, bytes((7, 6 if isinstance(field, str) else 7)))])
                    self.assertTrue(p.device.snapshot().held_samples)

    def test_duplicate_busy_release_does_not_execute_twice(self):
        p = setup(self)
        packet, first = p.command(7, p.release)
        self.assertEqual(diagnostics(first), ())
        before = p.device.snapshot()
        repeat = p.write(packet)
        self.assertEqual(diagnostics(repeat), (('DUPLICATE', 1),))
        self.assertEqual(p.device.snapshot().control_descriptors, before.control_descriptors)
        self.unchanged_hold(p, before)
        frames = p.drain()[1]
        self.assertEqual([(f.kind, f.payload[:2]) for f in frames if f.request], [(255, b'\7\2')])
        self.finish_release(p)

    def test_frame_session_request_precedence(self):
        forms = ('wrong_session', 'bad_crc', 'bad_version', 'bad_flags',
                 'sequence_gap', 'request_reuse', 'saturated', 'wrong_direction')
        for form in forms:
            with self.subTest(form=form):
                p = setup(self)
                before = p.device.snapshot()
                args = dict(kind=7, sequence=p.sequence, request=p.request, payload=p.release)
                if form == 'wrong_session':
                    args['session'] = raw.NONCE + 1
                elif form == 'bad_version':
                    args['version'] = b'\0\2'
                elif form == 'bad_flags':
                    args['reserved'] = 1
                elif form == 'sequence_gap':
                    args['sequence'] += 1
                elif form == 'request_reuse':
                    args['request'] -= 1
                elif form == 'saturated':
                    args['request'] = 2**32 - 1
                elif form == 'wrong_direction':
                    args['kind'] = 135
                packet = raw.packet(**args)
                if form == 'bad_crc':
                    packet = packet[:-2] + bytes((packet[-2] ^ 1 or 2,)) + b'\0'
                result = p.write(packet)
                wanted = {'wrong_session': 'WRONG_SESSION', 'bad_crc': 'CRC',
                          'bad_version': 'VERSION', 'bad_flags': 'RESERVED',
                          'saturated': 'COUNTER_SATURATED'}.get(form, 'PROTOCOL_RECOVERY')
                self.assertEqual(diagnostics(result), ((wanted, 1),))
                after = p.device.snapshot()
                recovery = form in ('sequence_gap', 'request_reuse', 'saturated', 'wrong_direction')
                if recovery:
                    self.assertFalse(after.negotiated)
                    self.assertEqual(after.state, 5)
                    self.assertFalse(after.continuation)
                else:
                    self.unchanged_hold(p, before)
                    self.assertEqual(after.next_rx_sequence, before.next_rx_sequence)
                    self.assertEqual(after.last_request_id, before.last_request_id)
                stable_storage(self, p)
                frames = p.drain()[1]
                self.assertFalse(any(f.kind in (135, 255) for f in frames))
                if recovery:
                    self.assertFalse(p.device.trigger(p.now))
                    stable_storage(self, p)

    def test_valid_stop_before_partial_and_final_end_preserves_raw_samples(self):
        for mode in range(3):
            for prefix in (0, 1, END_SIZE - 1, END_SIZE):
                with self.subTest(mode=mode, prefix=prefix):
                    p = setup(self, mode=mode, prefix=prefix)
                    p.command(5, raw.u(3))
                    self.assertTrue(p.device.snapshot().stop_barrier)
                    self.assertFalse(p.device.trigger(p.now))
                    stable_storage(self, p)
                    frames = p.drain()[1]
                    self.assertEqual(sum(f.kind == 147 for f in frames), int(prefix == 0))
                    self.assertEqual(sum(f.kind == 133 for f in frames), 1)
                    self.assertEqual(p.device.snapshot().state, 0)
                    self.assertFalse(p.device.snapshot().held_samples)
                    self.assertFalse(p.device.snapshot().continuation)
                    stable_storage(self, p)
                    release_reply(self, p, 7 if prefix == 0 else None)
                    self.assertEqual(p.device.snapshot().state, 0)
                    stable_storage(self, p)

    def test_stop_barrier_precedes_release_tuple_length_and_busy(self):
        for prefix in (1, END_SIZE - 1):
            for form in ('matching', 'crc', 'length'):
                with self.subTest(prefix=prefix, form=form):
                    p = setup(self, prefix=prefix)
                    p.command(5, raw.u(3))
                    payload = p.release
                    if form == 'crc':
                        payload = payload[:-1] + bytes((payload[-1] ^ 1,))
                    elif form == 'length':
                        payload = payload[:-1]
                    _, result = p.command(7, payload)
                    self.assertEqual(diagnostics(result), (('PROTOCOL_RECOVERY', 1),))
                    self.assertFalse(p.device.snapshot().negotiated)
                    self.assertFalse(p.device.snapshot().held_samples)
                    stable_storage(self, p)
                    tail, frames = p.drain()
                    self.assertEqual(tail, p.end[prefix:])
                    self.assertEqual([f.kind for f in frames], [146])
                    self.assertFalse(p.device.trigger(p.now))

    def test_fresh_hello_before_partial_final_end_makes_old_callback_inert(self):
        for prefix in (0, 1, END_SIZE - 1, END_SIZE):
            for busy_queued in (False, True):
                with self.subTest(prefix=prefix, busy_queued=busy_queued):
                    p = setup(self, prefix=prefix)
                    if busy_queued:
                        p.command(7, p.release)
                    p.write(raw.hello(raw.NONCE + 1))
                    after = p.device.snapshot()
                    self.assertEqual((after.state, after.generation, after.window_id, after.config), (0, 0, 0, None))
                    self.assertIsNone(after.tombstone)
                    self.assertIsNone(after.hold_started_ms)
                    self.assertFalse(after.held_samples)
                    stable_storage(self, p)
                    frames = p.drain()[1]
                    self.assertEqual([f.kind for f in frames], [146, 129] if 0 < prefix < END_SIZE else [129])
                    self.assertEqual((frames[-1].session, frames[-1].sequence), (raw.NONCE + 1, 1))
                    self.assertEqual(p.device.snapshot().state, 0)
                    self.assertIsNone(p.device.snapshot().hold_started_ms)
                    self.assertFalse(p.device.trigger(p.now))
                    stable_storage(self, p)

    def test_delayed_final_end_anchor_and_exact_hold_deadline(self):
        for mode in range(3):
            for elapsed in (4999, 5000):
                with self.subTest(mode=mode, elapsed=elapsed):
                    p = setup(self, mode=mode)
                    p.command(7, p.release)
                    p.now = 20000
                    p.device.tick(p.now)
                    self.assertEqual(p.device.snapshot().state, 4)
                    self.assertIsNone(p.device.snapshot().hold_started_ms)
                    self.assertTrue(p.device.snapshot().held_samples)
                    stable_storage(self, p)
                    self.assertEqual(p.read(END_SIZE - 1), p.end[1:])
                    self.assertEqual(p.device.snapshot().hold_started_ms, 20000)
                    replies = p.drain()[1]
                    self.assertEqual([f.kind for f in replies], [255, 134])
                    p.now += elapsed
                    p.device.tick(p.now)
                    self.assertEqual(p.device.snapshot().state, 6 if elapsed == 4999 else 5)
                    self.assertTrue(p.device.snapshot().held_samples)
                    stable_storage(self, p)
                    expected = (0 if mode == 2 else 2) if elapsed == 4999 else 5
                    self.finish_release(p, expected_state=expected)
                    if elapsed == 5000:
                        self.assertFalse(p.device.trigger(p.now))
                        release_reply(self, p, None)
                        self.assertEqual(p.device.snapshot().state, 5)
                        stable_storage(self, p)

    def test_old_tombstone_during_new_window_does_not_release_current_storage(self):
        for mode in (0, 1):
            p = setup(self, mode=mode, prefix=END_SIZE)
            old = p.release
            self.finish_release(p)
            capture(self, p)
            before = p.device.snapshot()
            self.assertEqual(before.window_id, 2)
            request = p.request
            p.command(7, old)
            self.unchanged_hold(p, before)
            ack = p.one()
            self.assertEqual((ack.kind, ack.request, ack.payload), (135, request, old[:8]))
            self.assertTrue(p.device.snapshot().held_samples)
            self.assertEqual(p.read(1), p.end[:1])
            request = p.request
            p.command(7, old)
            frames = p.drain()[1]
            self.assertEqual([(f.kind, f.payload[:2]) for f in frames if f.request == request], [(255, b'\7\7')])
            self.assertTrue(p.device.snapshot().held_samples)
            stable_storage(self, p)
            self.finish_release(p)

    def test_busy_errors_obey_existing_backpressure_and_boundaries(self):
        p = setup(self)
        for _ in range(6):
            p.command(7, p.release)
        before = p.device.snapshot()
        self.assertEqual(before.control_descriptors, 6)
        packet = raw.packet(7, p.sequence, p.request, p.release)
        result = p.device.write(packet, p.now)
        self.assertEqual(result.accepted_prefix, 0)
        self.assertEqual(diagnostics(result), (('BACKPRESSURE', 1),))
        self.unchanged_hold(p, before)
        self.assertEqual(p.device.snapshot().rx_used, 0)
        self.assertLessEqual(before.tx_size, 544)
        frames = p.drain()[1]
        self.assertEqual(sum(f.kind == 255 and f.payload[:2] == b'\7\2' for f in frames), 6)
        self.assertFalse(any(f.kind == 135 for f in frames))
        self.finish_release(p)

    def test_default_normal_and_prior_reply_order_are_preserved(self):
        p = setup(self, enabled=False)
        p.command(2)
        p.command(7, p.release)
        stable_storage(self, p)
        frames = p.drain()[1]
        self.assertEqual([f.kind for f in frames], [146, 130, 255, 134])
        self.assertEqual(frames[2].payload[:2], b'\7\2')
        self.assertEqual(p.device.snapshot().state, 6)
        self.finish_release(p)

    def test_release_is_evaluated_only_when_its_last_input_byte_arrives(self):
        for finish_end_first in (False, True):
            p = setup(self)
            packet = raw.packet(7, p.sequence, p.request, p.release)
            before = p.device.snapshot()
            result = p.write(packet[:-1])
            self.assertEqual(diagnostics(result), ())
            self.unchanged_hold(p, before)
            self.assertEqual(p.device.snapshot().control_descriptors, 0)
            if finish_end_first:
                self.assertEqual(p.read(END_SIZE - 1), p.end[1:])
            p.write(packet[-1:])
            self.assertEqual(p.device.snapshot().held_samples, not finish_end_first)
            frames = p.drain()[1]
            replies = [f for f in frames if f.request == p.request]
            self.assertEqual([f.kind for f in replies], [135 if finish_end_first else 255])
            if not finish_end_first:
                self.assertEqual(replies[0].payload[:2], b'\7\2')
            stable_storage(self, p)

    def test_supported_host_cannot_validate_receipt_before_complete_end(self):
        from motortester.session import SessionError, WindowReceipt
        from motortester.window import ExpectedContext, WindowReceiver
        from motortester.wire import Frame
        p = setup(self, prefix=END_SIZE - 1)
        context = ExpectedContext(raw.NONCE, 3, 1, 1, 0, 0, 2048, 0)
        receiver = WindowReceiver(context)
        result = None
        for frame in p.frames:
            if frame.kind in (144, 145, 146):
                result = receiver.feed(Frame(frame.kind, frame.session, frame.sequence,
                                             frame.request, frame.payload), p.now)
                self.assertIsNone(result)
        with self.assertRaises(SessionError):
            WindowReceipt.from_window(context, result)
        self.assertEqual(p.read(1), b'\0')
        frame = p.frames[-1]
        self.assertEqual(frame.kind, 146)
        result = receiver.feed(Frame(frame.kind, frame.session, frame.sequence,
                                     frame.request, frame.payload), p.now)
        receipt = WindowReceipt.from_window(context, result)
        self.assertEqual(receipt.samples_crc32, raw.crc(p.samples))

    def test_last_uint64_clock_and_provenance_are_unchanged(self):
        p = setup(self)
        p.now = 2**64 - 1
        p.command(7, p.release)
        self.assertIsNone(p.device.snapshot().hold_started_ms)
        frames = p.drain()[1]
        self.assertEqual([f.kind for f in frames], [146, 255, 134])
        self.assertEqual(p.device.snapshot().hold_started_ms, p.now)
        provenance = p.device.snapshot().provenance
        self.assertEqual(provenance.source_sha, 'c54bd955aaf3cb863ef3df043ea237ea62e74ad4')
        self.assertEqual(provenance.stop_barrier_policy_sha, '2017576dbe104f4c43d8d9feac28436589191fd1')
        self.assertTrue(provenance.timeout_provisional)
        self.assertIn('PROVISIONAL', provenance.timeout_interpretation)
        self.finish_release(p)

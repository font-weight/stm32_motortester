"""Новые независимые проверки миграции Stop, PR52 2017576d.

Это свежие сценарии после замены среды, не восстановленные старые журналы
и не заключение Critic-B. Проверяется только синтетический host byte-peer.
"""
import importlib.util
from pathlib import Path
import sys
import unittest

from motortester.device_emulator import DeviceEmulator, DeviceError

ROOT = Path(__file__).parent / 'fixtures' / 'stop_barrier_r2'
_spec = importlib.util.spec_from_file_location('stop_r2_raw_peer', ROOT / 'raw_peer.py')
raw = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = raw
_spec.loader.exec_module(raw)

FORMS = [(f'valid_{kind}', kind, payload) for kind, payload in (
    (2, b''), (3, raw.CONFIG), (4, raw.u(1)), (5, raw.u(3)),
    (6, b''), (7, raw.u(3) + raw.u(1) + raw.u(0)))]
FORMS += [(f'length_{kind}', kind, payload + b'\0') for _, kind, payload in FORMS[:]]
FORMS += [('reserved_config', 3, raw.CONFIG[:5] + b'\1' + raw.CONFIG[6:])]
FORMS += [(f'unknown_{kind}', kind, b'') for kind in (0, 8, 128)]
FORM_BY_NAME = {name: (kind, payload) for name, kind, payload in FORMS}
PREFIXES = ('zero', 'one', 'last_minus_one')
MATRIX_NAMES = tuple(f'test_matrix_{name}_{prefix}_caps_{int(caps)}'
                     for name, _, _ in FORMS for prefix in PREFIXES for caps in (False, True))


def diagnostics(result):
    return tuple((str(getattr(d.code, 'value', d.code)), d.count) for d in result.diagnostics)


def prepare(case, prefix='zero', caps=False, factory=DeviceEmulator, *, completed=False):
    peer = raw.Peer(case, factory).armed()
    if completed:
        captured = peer.device.trigger(0)
        case.assertTrue(captured)
        end = next(f for f in peer.drain()[1] if f.kind == 146)
        case.assertEqual(peer.device.snapshot().tombstone.sample_crc32, int.from_bytes(end.payload[-4:], 'little'))
    start_tx = peer.device.snapshot().next_tx_sequence
    if caps:
        peer.command(2)
    stop_request = peer.request
    stop_raw, _ = peer.command(5, raw.u(3))
    ack = raw.packet(133, start_tx + int(caps), stop_request, raw.u(3) + raw.u(int(completed)) + b'\0' * 4)
    count = {'zero': 0, 'one': 1, 'last_minus_one': len(ack) - 1, 'final': len(ack)}[prefix]
    if caps and count:
        preceding = peer.one()
        case.assertEqual((preceding.kind, preceding.request, preceding.sequence), (130, stop_request - 1, start_tx))
        case.assertEqual(preceding.payload, raw.CAPS)
    case.assertEqual(peer.read(count), ack[:count])
    case.assertEqual(peer.device.snapshot().stop_barrier, prefix != 'final')
    return peer, ack, count, stop_raw


def observe_matrix(case, name, prefix, caps):
    peer, ack, count, _ = prepare(case, prefix, caps, completed=name == 'valid_7')
    before = peer.device.snapshot()
    kind, payload = FORM_BY_NAME[name]
    if name == 'valid_7':
        payload = raw.u(before.generation) + raw.u(before.window_id) + raw.u(before.tombstone.sample_crc32)
    command, result = peer.command(kind, payload)
    after = peer.device.snapshot()
    tail, frames = peer.drain(1)
    return dict(peer=peer, before=before, after=after, final=peer.device.snapshot(),
                tail=tail, frames=frames, ack=ack, count=count, command=command,
                result=result, kind=kind, name=name, caps=caps, prefix=prefix)


class Checks(unittest.TestCase):
    def recovery(self, peer, before, result, *, admitted=True, reason=4, code='PROTOCOL_RECOVERY'):
        after = peer.device.snapshot()
        self.assertEqual(diagnostics(result), ((code, 1),))
        self.assertEqual((after.negotiated, after.state, after.reason, after.stop_barrier), (False, 5, reason, False))
        self.assertEqual((after.session, after.config, after.config_id, after.generation, after.window_id,
                          after.dropped_windows, after.terminal, after.tombstone),
                         (before.session, before.config, before.config_id, before.generation, before.window_id,
                          before.dropped_windows, before.terminal, before.tombstone))
        self.assertEqual(after.next_rx_sequence, before.next_rx_sequence + int(admitted))
        self.assertEqual(after.last_request_id, before.last_request_id + int(admitted))
        self.assertFalse(after.held_samples)
        self.assertFalse(after.continuation)
        self.assertEqual(after.control_descriptors, int(reason == 6))
        self.assertEqual(after.sample_buffer_bytes, 4096)
        self.assertFalse(peer.device.trigger(peer.now))

    def candidate(self, observation):
        o = observation
        # Проверяем состояние сразу после команды, до окончания старого TX.
        self.assertEqual((o['after'].negotiated, o['after'].state, o['after'].reason), (False, 5, 4))
        self.recovery(o['peer'], o['before'], o['result'])
        self.assertEqual(o['tail'], o['ack'][o['count']:] if o['count'] else b'')
        self.assertEqual([(f.kind, f.request) for f in o['frames']],
                         [(133, raw.decode(o['ack']).request)] if o['count'] else [])
        self.assertFalse(any(f.kind in (132, 144, 145, 146, 147, 255) for f in o['frames']))
        self.assertEqual(o['final'].next_tx_sequence, o['before'].next_tx_sequence)
        self.assertEqual(o['final'].tx_size, 0)
        self.assertEqual(o['final'].rx_used, 0)
        self.assertEqual(o['final'].control_descriptors, 0)


class StopBarrierMatrix(Checks):
    pass


def _matrix_test(name, prefix, caps):
    def test(self):
        self.candidate(observe_matrix(self, name, prefix, caps))
    return test


for _name, _, _ in FORMS:
    for _prefix in PREFIXES:
        for _caps in (False, True):
            setattr(StopBarrierMatrix, f'test_matrix_{_name}_{_prefix}_caps_{int(_caps)}',
                    _matrix_test(_name, _prefix, _caps))


class PositiveBoundaryControls(Checks):
    def test_final_ack_byte_admits_all_six_commands_with_status_still_queued(self):
        for name, kind, payload in FORMS[:6]:
            with self.subTest(name=name):
                p, ack, count, _ = prepare(self, 'final')
                before = p.device.snapshot()
                self.assertEqual((before.state, before.control_descriptors, before.tx_size), (0, 1, 0))
                request = p.request
                _, result = p.command(kind, payload)
                self.assertEqual(diagnostics(result), ())
                frames = p.drain(1)[1]
                expected = {2: 130, 3: 131, 4: 132, 5: 133, 6: 134, 7: 255}[kind]
                replies = [f for f in frames if f.request]
                self.assertEqual([(f.kind, f.request) for f in replies], [(expected, request)])
                self.assertTrue(p.device.snapshot().negotiated)
                self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence + 1)
                if kind == 4:
                    self.assertEqual(p.device.snapshot().generation, request)
                    self.assertEqual(p.device.snapshot().state, 2)
                elif kind == 3:
                    self.assertEqual(p.device.snapshot().config_id, 2)
                elif kind == 7:
                    self.assertEqual(replies[0].payload, bytes((7, 7, 0, 0)) + raw.u(3))
                self.assertFalse(any(f.kind in (144, 145, 146, 147) for f in frames))

    def test_final_ack_byte_restores_payload_errors_and_unknown_types(self):
        for name, kind, payload in FORMS[6:]:
            with self.subTest(name=name):
                p, _, _, _ = prepare(self, 'final')
                request = p.request
                _, result = p.command(kind, payload)
                self.assertEqual(diagnostics(result), ())
                replies = [f for f in p.drain()[1] if f.request]
                code = 5 if name.startswith('unknown') else 6
                self.assertEqual([(f.kind, f.request, f.payload) for f in replies],
                                 [(255, request, bytes((kind, code, 0, 0)) + raw.u(3))])
                self.assertTrue(p.device.snapshot().negotiated)

    def test_partial_queued_stopped_status_does_not_extend_barrier(self):
        p, _, _, _ = prepare(self, 'final')
        self.assertTrue(p.read(1))
        self.assertFalse(p.device.snapshot().stop_barrier)
        request = p.request
        p.command(4, raw.u(1))
        frames = p.drain(1)[1]
        self.assertEqual([f.kind for f in frames], [134, 132, 134])
        self.assertEqual(frames[0].payload, raw.status(0))
        self.assertEqual(frames[1].payload, raw.u(request) + raw.u(1))
        self.assertEqual(p.device.snapshot().state, 2)

    def test_ordinary_transfer_without_stop_remains_caps_and_busy(self):
        p = raw.Peer(self, DeviceEmulator).armed()
        self.assertTrue(p.device.trigger(0))
        p.command(2)
        p.command(3, raw.CONFIG)
        p.command(4, raw.u(1))
        frames = p.drain()[1]
        replies = [f for f in frames if f.request]
        self.assertEqual([f.kind for f in replies], [130, 255, 255])
        self.assertEqual([f.payload[:2] for f in replies[1:]], [b'\3\2', b'\4\2'])
        self.assertTrue(p.device.snapshot().negotiated)
        self.assertEqual(len([f for f in frames if f.kind == 146]), 1)

    def test_valid_late_release_after_final_ack_preserves_tombstone_no_rearm(self):
        p = raw.Peer(self, DeviceEmulator).armed()
        self.assertTrue(p.device.trigger(0))
        frames = p.drain()[1]
        end = next(f for f in frames if f.kind == 146)
        terminal = p.device.snapshot().terminal
        p.command(5, raw.u(3))
        ack = p.one()
        self.assertEqual(ack.kind, 133)
        self.assertEqual(p.device.snapshot().control_descriptors, 1)
        p.command(7, raw.u(3) + raw.u(1) + end.payload[-4:])
        tail = p.drain()[1]
        self.assertEqual([f.kind for f in tail], [134, 135])
        self.assertEqual(p.device.snapshot().terminal, terminal)
        self.assertEqual(p.device.snapshot().tombstone, terminal)
        self.assertEqual(p.device.snapshot().state, 0)
        self.assertFalse(p.device.trigger(0))


class AdmissionGuardControls(Checks):
    def test_wrong_session_is_ignored_before_barrier_guard(self):
        for prefix in PREFIXES:
            with self.subTest(prefix=prefix):
                p, ack, count, _ = prepare(self, prefix)
                before = p.device.snapshot()
                result = p.write(raw.packet(3, p.sequence, p.request, raw.CONFIG, raw.NONCE + 1))
                self.assertEqual(diagnostics(result), (('WRONG_SESSION', 1),))
                after = p.device.snapshot()
                self.assertTrue(after.negotiated)
                self.assertTrue(after.stop_barrier)
                self.assertEqual((after.next_rx_sequence, after.last_request_id),
                                 (before.next_rx_sequence, before.last_request_id))
                self.assertEqual([f.kind for f in p.drain()[1]], [133, 134])

    def test_exact_duplicate_stop_has_no_second_ack_or_sequence_consumption(self):
        for prefix in PREFIXES:
            with self.subTest(prefix=prefix):
                p, ack, count, stop = prepare(self, prefix)
                before = p.device.snapshot()
                result = p.write(stop)
                self.assertEqual(diagnostics(result), (('DUPLICATE', 1),))
                self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence)
                tail, frames = p.drain(1)
                self.assertEqual([f.kind for f in frames], [133, 134])
                self.assertEqual(tail[:len(ack) - count], ack[count:])
                self.assertEqual(p.device.snapshot().state, 0)

    def test_changed_duplicate_and_order_errors_keep_pre_dispatch_recovery(self):
        mutations = ((-1, -1, raw.u(99)), (1, 0, b''), (0, -1, b''))
        for sequence_delta, request_delta, payload in mutations:
            with self.subTest(sequence_delta=sequence_delta, request_delta=request_delta):
                p, ack, count, _ = prepare(self, 'one')
                before = p.device.snapshot()
                result = p.write(raw.packet(5 if payload else 6, p.sequence + sequence_delta,
                                           p.request + request_delta, payload))
                self.recovery(p, before, result, admitted=False)
                self.assertEqual(p.drain(1)[0], ack[count:])
                self.assertEqual(p.device.snapshot().state, 5)

    def test_new_request_id_gap_is_admitted_before_recovery(self):
        p, ack, count, _ = prepare(self, 'one')
        before = p.device.snapshot()
        _, result = p.command(6, request_gap=17)
        self.assertEqual(diagnostics(result), (('PROTOCOL_RECOVERY', 1),))
        self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence + 1)
        self.assertEqual(p.device.snapshot().last_request_id, before.last_request_id + 18)
        self.assertFalse(p.device.snapshot().negotiated)
        self.assertEqual(p.drain()[0], ack[count:])

    def test_saturated_rx_counter_keeps_reason_six_not_barrier_reason_four(self):
        for sequence, request in ((1, 6), (raw.MAX_ID + 1, 6), (5, raw.MAX_ID + 1)):
            with self.subTest(sequence=sequence, request=request):
                p, ack, count, _ = prepare(self, 'one')
                before = p.device.snapshot()
                result = p.write(raw.packet(6, sequence, request))
                self.recovery(p, before, result, admitted=False, reason=6, code='COUNTER_SATURATED')
                frames = p.drain()[1]
                self.assertEqual([f.kind for f in frames], [133, 134])
                self.assertEqual(frames[-1].payload[:2], b'\5\6')
                self.assertEqual(p.device.snapshot().state, 5)

    def test_device_direction_is_recovery_even_for_wrong_session(self):
        p, ack, count, _ = prepare(self, 'one')
        before = p.device.snapshot()
        result = p.write(raw.packet(130, p.sequence, p.request, session=raw.NONCE + 1))
        self.recovery(p, before, result, admitted=False)
        self.assertEqual(p.drain()[0], ack[count:])

    def test_crc_framing_version_reserved_fail_without_consuming_request(self):
        corrupt = bytearray(raw.packet(6, 5, 5))
        corrupt[-2] ^= 1
        variants = [(bytes(corrupt), 'CRC'), (b'\5\1\0', 'FRAMING'),
                    (raw.packet(6, 5, 5, version=b'\0\2'), 'VERSION'),
                    (raw.packet(6, 5, 5, reserved=1), 'RESERVED')]
        for broken, code in variants:
            with self.subTest(code=code):
                p, ack, count, _ = prepare(self, 'one')
                before = p.device.snapshot()
                result = p.write(broken)
                self.assertEqual(diagnostics(result), ((code, 1),))
                self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence)
                self.assertTrue(p.device.snapshot().stop_barrier)
                self.assertTrue(p.device.snapshot().negotiated)
                self.assertEqual([f.kind for f in p.drain()[1]], [133, 134])

    def test_fragmented_command_is_admitted_only_on_input_delimiter(self):
        for final_ack_first in (False, True):
            with self.subTest(final_ack_first=final_ack_first):
                p, ack, count, _ = prepare(self, 'last_minus_one')
                command = raw.packet(4, p.sequence, p.request, raw.u(1))
                before = p.device.snapshot()
                p.write(command[:-1])
                self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence)
                if final_ack_first:
                    self.assertEqual(p.read(1), ack[-1:])
                result = p.write(command[-1:])
                if final_ack_first:
                    self.assertEqual(diagnostics(result), ())
                    self.assertTrue(p.device.snapshot().negotiated)
                    self.assertEqual(p.device.snapshot().state, 2)
                    self.assertEqual([f.kind for f in p.drain()[1]], [132, 134])
                else:
                    self.recovery(p, before, result)
                    self.assertEqual(p.drain()[0], ack[-1:])

    def test_fresh_hello_resets_before_barrier_and_old_completion_is_inert(self):
        for prefix in PREFIXES:
            with self.subTest(prefix=prefix):
                p, ack, count, _ = prepare(self, prefix)
                p.write(raw.hello(raw.NONCE + 1))
                snap = p.device.snapshot()
                self.assertEqual((snap.session, snap.state, snap.generation, snap.config_id,
                                  snap.next_rx_sequence, snap.last_request_id), (raw.NONCE + 1, 0, 0, 0, 2, 1))
                self.assertTrue(snap.negotiated)
                self.assertFalse(snap.stop_barrier)
                frames = p.drain(1)[1]
                self.assertEqual([f.kind for f in frames], ([133] if count else []) + [129])
                self.assertEqual((frames[-1].session, frames[-1].sequence, frames[-1].request), (raw.NONCE + 1, 1, 1))
                self.assertEqual(p.device.snapshot().state, 0)
                self.assertIsNone(p.device.snapshot().terminal)

    def test_same_nonce_and_invalid_hello_do_not_remove_barrier(self):
        for hello, code in ((raw.hello(), 'DUPLICATE'), (raw.hello(0), 'INVALID_HELLO'),
                            (raw.packet(1, 2, 1, raw.u(raw.NONCE + 1, 8), 0), 'INVALID_HELLO')):
            with self.subTest(code=code, hello=hello.hex()):
                p, _, _, _ = prepare(self)
                before = p.device.snapshot()
                result = p.write(hello)
                self.assertEqual(diagnostics(result), ((code, 1),))
                self.assertTrue(p.device.snapshot().stop_barrier)
                self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence)
                self.assertEqual([f.kind for f in p.drain()[1]], [133, 134])

    def test_recovered_session_rejects_commands_and_requires_fresh_nonce(self):
        p, _, _, _ = prepare(self)
        p.command(6)
        p.write(raw.hello())
        self.assertFalse(p.device.snapshot().negotiated)
        for kind, payload in ((2, b''), (3, raw.CONFIG), (4, raw.u(1))):
            _, result = p.command(kind, payload)
            self.assertEqual(diagnostics(result), (('NEED_HELLO', 1),))
            self.assertEqual(p.drain()[0], b'')
        p.write(raw.hello(raw.NONCE + 1))
        self.assertEqual([f.kind for f in p.drain()[1]], [129])
        self.assertTrue(p.device.snapshot().negotiated)


class TerminalAndBoundsControls(Checks):
    def test_repeated_stop_before_or_during_abort_has_at_most_one_terminal(self):
        for prefix in ('zero', 'one', 'last_minus_one', 'final'):
            with self.subTest(prefix=prefix):
                p = raw.Peer(self, DeviceEmulator).armed()
                self.assertTrue(p.device.trigger(0))
                sequence = p.device.snapshot().next_tx_sequence
                p.command(5, raw.u(3))
                abort = raw.packet(147, sequence, 0, raw.u(3) + raw.u(1) + b'\1\0\0\0')
                count = {'zero': 0, 'one': 1, 'last_minus_one': len(abort) - 1, 'final': len(abort)}[prefix]
                self.assertEqual(p.read(count), abort[:count])
                before = p.device.snapshot()
                _, result = p.command(5, raw.u(3))
                self.recovery(p, before, result)
                tail, frames = p.drain(1)
                self.assertEqual(tail, abort[count:] if count else b'')
                terminals = [f for f in p.frames if f.kind in (146, 147)]
                self.assertEqual([f.kind for f in terminals], [147] if count else [])
                self.assertEqual(p.device.snapshot().dropped_windows, 1)
                self.assertFalse(any(f.kind == 133 for f in p.frames))
                self.assertEqual(p.device.snapshot().state, 5)

    def test_started_older_caps_finishes_but_queued_stop_ack_is_discarded(self):
        p = raw.Peer(self, DeviceEmulator).armed()
        p.command(2)
        expected = raw.packet(130, 5, 4, raw.CAPS)
        self.assertEqual(p.read(1), expected[:1])
        p.command(5, raw.u(3))
        before = p.device.snapshot()
        _, result = p.command(4, raw.u(1))
        self.recovery(p, before, result)
        self.assertEqual(p.drain(1)[0], expected[1:])
        self.assertEqual(p.device.snapshot().next_tx_sequence, 6)
        self.assertEqual(p.device.snapshot().state, 5)

    def test_old_partial_begin_data_end_finish_without_phantom_terminal(self):
        reference = raw.Peer(self, DeviceEmulator).armed()
        self.assertTrue(reference.device.trigger(0))
        trace = reference.drain()[1]
        for kind in (144, 145, 146):
            with self.subTest(kind=kind):
                p = raw.Peer(self, DeviceEmulator).armed()
                self.assertTrue(p.device.trigger(0))
                for frame in trace:
                    if frame.kind == kind:
                        target = frame
                        break
                    self.assertEqual(p.one().packet, frame.packet)
                self.assertEqual(p.read(1), target.packet[:1])
                p.command(5, raw.u(3))
                before = p.device.snapshot()
                _, result = p.command(6)
                self.recovery(p, before, result)
                self.assertEqual(p.drain(1)[0], target.packet[1:])
                terminals = [f.kind for f in p.frames if f.kind in (146, 147)]
                self.assertEqual(terminals, [146] if kind == 146 else [])
                self.assertEqual(p.device.snapshot().dropped_windows, 0 if kind == 146 else 1)
                self.assertFalse(any(f.kind == 133 for f in p.frames))

    def test_queued_release_ack_cannot_rearm_after_barrier_recovery(self):
        p = raw.Peer(self, DeviceEmulator).armed()
        self.assertTrue(p.device.trigger(0))
        end = next(f for f in p.drain()[1] if f.kind == 146)
        p.command(7, raw.u(3) + raw.u(1) + end.payload[-4:])
        self.assertTrue(p.read(1))
        p.command(5, raw.u(3))
        before = p.device.snapshot()
        _, result = p.command(6)
        self.recovery(p, before, result)
        self.assertEqual([f.kind for f in p.drain(1)[1]], [135])
        self.assertFalse(p.device.trigger(0))
        self.assertEqual(p.device.snapshot().state, 5)
        self.assertEqual(len([f for f in p.frames if f.kind == 146]), 1)

    def test_coalesced_stop_then_ordinary_request_recovers_once_then_needs_hello(self):
        p = raw.Peer(self, DeviceEmulator).armed()
        result = p.write(raw.packet(5, 4, 4, raw.u(3)) + raw.packet(6, 5, 5) + raw.packet(4, 6, 6, raw.u(1)))
        self.assertEqual(diagnostics(result), (('PROTOCOL_RECOVERY', 1), ('NEED_HELLO', 1)))
        self.assertEqual(p.device.snapshot().next_rx_sequence, 6)
        self.assertEqual(p.device.snapshot().last_request_id, 5)
        self.assertEqual(p.drain()[0], b'')
        self.assertEqual(p.device.snapshot().generation, 3)

    def test_queue_backpressure_preserves_unaccepted_suffix_and_bounded_state(self):
        p = raw.Peer(self, DeviceEmulator).armed()
        commands = b''.join(raw.packet(2, index, index) for index in range(4, 44))
        offset, requests = 0, []
        for step in range(100):
            result = p.device.write(commands[offset:], 0)
            self.assertGreaterEqual(result.accepted_prefix, 0)
            self.assertLessEqual(result.accepted_prefix, len(commands) - offset)
            offset += result.accepted_prefix
            snap = p.device.snapshot()
            self.assertLessEqual(snap.control_descriptors, 8)
            self.assertLessEqual(snap.rx_used, 543)
            self.assertLessEqual(snap.tx_size, 544)
            self.assertEqual(snap.sample_buffer_bytes, 4096)
            requests.extend(f.request for f in p.drain(1)[1] if f.kind == 130)
            if offset == len(commands):
                break
        self.assertEqual(offset, len(commands))
        self.assertEqual(requests, list(range(4, 44)))
        self.assertEqual(p.device.snapshot().next_rx_sequence, 44)

    def test_invalid_api_calls_and_clock_rollback_are_atomic_during_barrier(self):
        p, _, _, _ = prepare(self, 'one')
        p.now = 100
        p.device.tick(100)
        calls = [('ARGUMENT', lambda: p.device.read(True, 100)),
                 ('RANGE', lambda: p.device.read(4097, 100)),
                 ('ARGUMENT', lambda: p.device.write(bytearray(), 100)),
                 ('CHUNK_SIZE', lambda: p.device.write(b'x' * 4097, 100)),
                 ('ARGUMENT', lambda: p.device.tick(True)),
                 ('RANGE', lambda: p.device.tick(raw.MAX_TIME + 1)),
                 ('CLOCK_ROLLBACK', lambda: p.device.tick(99)),
                 ('CLOCK_ROLLBACK', lambda: p.device.read(1, 99)),
                 ('CLOCK_ROLLBACK', lambda: p.device.write(b'', 99)),
                 ('CLOCK_ROLLBACK', lambda: p.device.trigger(99)),
                 ('CLOCK_ROLLBACK', lambda: p.device.disconnect(99))]
        for code, operation in calls:
            with self.subTest(code=code, operation=operation):
                before = p.device.snapshot()
                with self.assertRaises(DeviceError) as caught:
                    operation()
                self.assertEqual(caught.exception.code.value, code)
                self.assertEqual(p.device.snapshot(), before)
        p.device.tick(raw.MAX_TIME)
        self.assertTrue(p.device.snapshot().stop_barrier)
        p.now = raw.MAX_TIME
        self.assertEqual([f.kind for f in p.drain(1)[1]], [133, 134])

    def test_rx_overflow_and_timeout_do_not_dispatch_during_barrier(self):
        for suffix, elapsed, code in ((b'x' * 544 + b'\0', 0, 'OVERFLOW'), (b'\2', 1000, 'TIMEOUT')):
            with self.subTest(code=code):
                p, _, _, _ = prepare(self)
                before = p.device.snapshot()
                result = p.write(suffix)
                if elapsed:
                    result = p.device.tick(elapsed)
                    codes = tuple((d.code, d.count) for d in result)
                    p.now = elapsed
                    p.write(b'\0')
                else:
                    codes = diagnostics(result)
                self.assertEqual(codes, ((code, 1),))
                self.assertEqual(p.device.snapshot().next_rx_sequence, before.next_rx_sequence)
                self.assertTrue(p.device.snapshot().stop_barrier)
                self.assertLessEqual(p.device.snapshot().rx_used, 543)
                self.assertEqual([f.kind for f in p.drain()[1]], [133, 134])

    def test_zero_read_does_not_commit_sequence_or_ack_boundary(self):
        p, _, _, _ = prepare(self)
        before = p.device.snapshot()
        for _ in range(100):
            self.assertEqual(p.device.read(0, 0), b'')
        self.assertEqual(p.device.snapshot(), before)
        self.assertTrue(p.device.snapshot().stop_barrier)

    def test_disconnect_truncates_partial_ack_and_resets_every_barrier(self):
        p, _, _, _ = prepare(self, 'one')
        p.device.disconnect(0)
        s = p.device.snapshot()
        self.assertEqual((s.session, s.state, s.next_rx_sequence, s.next_tx_sequence), (0, 0, 1, 1))
        self.assertFalse(s.stop_barrier)
        self.assertFalse(s.negotiated)
        self.assertEqual((s.tx_size, s.control_descriptors, s.rx_used), (0, 0, 0))
        self.assertEqual(p.device.read(4096, 0), b'')

    def test_independent_codec_vectors_and_exact_matrix_inventory(self):
        self.assertEqual(raw.crc(b'123456789'), 0xcbf43926)
        self.assertEqual(len(raw.CAPS), 30)
        for payload in (b'', b'\0' * 512, b'x' * 254, bytes(range(256)) * 2):
            encoded = raw.packet(8, 2, 2, payload)
            self.assertEqual(raw.decode(encoded).payload, payload)
            self.assertLessEqual(len(encoded), 544)
        self.assertEqual(len(FORMS), 16)
        self.assertEqual(len(set(MATRIX_NAMES)), 96)
        actual = unittest.defaultTestLoader.getTestCaseNames(StopBarrierMatrix)
        self.assertEqual(set(actual), set(MATRIX_NAMES))


if __name__ == '__main__':
    unittest.main()

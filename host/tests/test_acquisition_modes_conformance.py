"""Независимые проверки ограниченного analog Auto/Normal/Single.

Ожидаемые байты не импортируются из авторских helpers. Реальные COBS/CRC bytes
проходят StreamDecoder → SessionController → WindowReceiver → receipt factory.
Точная гонка expiry-before-input — локальная политика симуляции, не wire-норма.
"""
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import unittest

from motortester.device_emulator import DeviceEmulator, DeviceError
from motortester.session import (Config, ErrorCode, EventKind, Phase,
                                RecoveryReason, SessionController, SessionError,
                                WindowReceipt)
from motortester.window import (ExpectedContext, SampleBlock, WindowCode,
                               WindowReceiver)
from motortester.wire import StreamDecoder

FIXTURES = Path(__file__).parent / 'fixtures' / 'acquisition_modes'
_spec = importlib.util.spec_from_file_location('independent_analog_modes_oracle', FIXTURES / 'reference.py')
ref = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ref
_spec.loader.exec_module(ref)


def context_for(controller):
    s = controller.snapshot()
    c = s.config
    return ExpectedContext(s.session, s.generation, s.config_id, c.mode, c.source,
                           c.edge, c.level_code, c.pretrigger_samples, c.profile)


class Link:
    """Транспорт теста, без таймеров ОС и скрытых повторов команд."""
    def __init__(self, case, mode=1, edge=0, timeout=None, all_modes=True,
                 configure=True, arm=True, drain_arm=True):
        self.case, self.now = case, 0
        self.controller = SessionController()
        self.device = DeviceEmulator(all_analog_modes=all_modes)
        self.decoder = StreamDecoder()
        self.frames, self.events, self.commands, self.results = [], [], [], []
        self.rx, self.tx = bytearray(), bytearray()
        self.receiver, self.context = None, None
        self.controller.start(ref.NONCE, 0)
        self.send()
        self.drain()
        self.send()
        self.drain()
        case.assertIs(self.controller.snapshot().phase, Phase.READY)
        if configure:
            timeout = (100 if mode == 0 else 0) if timeout is None else timeout
            self.controller.configure(Config(mode=mode, edge=edge,
                                             auto_timeout_ms=timeout), 0)
            self.send()
            self.drain()
            if arm:
                self.arm(drain_arm)

    def arm(self, drain=True):
        self.controller.arm(self.now)
        self.send()
        if drain:
            self.drain()
            self.case.assertIs(self.controller.snapshot().phase, Phase.ARMED)

    def send(self, count=None):
        ticket = self.controller.prepare_tx()
        self.case.assertIsNotNone(ticket)
        if ticket.offset == 0:
            self.commands.append(ticket.frame)
            self.case.assertEqual(ticket.encoded, ref.packet(ticket.frame.message_type,
                ticket.frame.session, ticket.frame.sequence, ticket.frame.request_id,
                ticket.frame.payload))
        raw = ticket.encoded[ticket.offset:]
        if count is not None:
            raw = raw[:count]
        response = self.device.write(raw, self.now)
        self.case.assertEqual(response.accepted_prefix, len(raw))
        self.case.assertEqual(response.diagnostics, ())
        self.tx.extend(raw)
        self.case.assertEqual(self.controller.written(ticket, len(raw), self.now), ())
        return ticket

    def ingest(self, raw):
        self.rx.extend(raw)
        decoded = self.decoder.feed(raw, self.now)
        self.case.assertEqual(decoded.diagnostics, ())
        for frame in decoded.frames:
            self.frames.append(frame)
            events = self.controller.receive(frame, self.now)
            self.events.extend(events)
            self.case.assertNotIn(EventKind.RECOVERY, [e.kind for e in events])
            for event in events:
                if event.kind is EventKind.WINDOW_BEGIN:
                    self.context = context_for(self.controller)
                    self.receiver = WindowReceiver(self.context)
                if event.kind in (EventKind.WINDOW_BEGIN, EventKind.WINDOW_DATA,
                                  EventKind.WINDOW_END_ADMITTED, EventKind.WINDOW_ABORT):
                    if self.receiver is not None:
                        result = self.receiver.feed(frame, self.now)
                        if result is not None:
                            self.results.append((self.context, result))
                            self.receiver = None
        return decoded.frames

    def read(self, count):
        raw = self.device.read(count, self.now)
        self.ingest(raw)
        return raw

    def drain(self, chunk=97):
        start = len(self.frames)
        for _ in range(10000):
            if not self.read(chunk):
                return self.frames[start:]
        self.case.fail('bounded drain did not finish')

    def one(self):
        start = len(self.frames)
        for _ in range(544):
            self.case.assertTrue(self.read(1))
            if len(self.frames) > start:
                return self.frames[-1]
        self.case.fail('frame exceeded wire bound')

    def until(self, kind):
        for _ in range(32):
            frame = self.one()
            if frame.message_type == kind:
                return frame
        self.case.fail('frame kind not reached')

    def capture(self, timeout=False, chunk=97):
        old = len(self.results)
        if timeout:
            self.now += self.controller.snapshot().config.auto_timeout_ms
            self.device.tick(self.now)
        else:
            self.case.assertTrue(self.device.trigger(self.now))
        self.drain(chunk)
        self.case.assertEqual(len(self.results), old + 1)
        context, result = self.results[-1]
        self.case.assertTrue(result.semantic_checks_complete)
        return context, result, WindowReceipt.from_window(context, result)

    def release(self, receipt, drain=True):
        self.case.assertEqual(self.controller.release(receipt, self.now), ())
        self.send()
        if drain:
            return self.drain()


class RawPeer:
    """Независимый клиент устройства: payload и framing только из oracle."""
    def __init__(self, case, mode=0, edge=0, timeout=None, all_modes=True):
        self.case, self.now, self.session = case, 0, ref.NONCE
        self.device = DeviceEmulator(all_analog_modes=all_modes)
        self.decoder, self.frames = StreamDecoder(), []
        self.sequence, self.request = 1, 1
        self.write(ref.packet(1, 0, 1, 1, struct.pack('<Q', self.session)))
        self.sequence, self.request = 2, 2
        self.drain()
        self.command(2)
        self.drain()
        self.config = ref.config(mode, edge, (100 if mode == 0 else 0) if timeout is None else timeout)
        self.command(3, self.config)
        self.drain()
        self.generation = self.request
        self.command(4, struct.pack('<I', 1))
        self.drain()

    def write(self, raw):
        result = self.device.write(raw, self.now)
        self.case.assertEqual(result.accepted_prefix, len(raw))
        return result

    def command(self, kind, payload=b''):
        raw = ref.packet(kind, self.session, self.sequence, self.request, payload)
        self.sequence += 1
        self.request += 1
        self.case.assertEqual(self.write(raw).diagnostics, ())
        return raw

    def read(self, count):
        raw = self.device.read(count, self.now)
        decoded = self.decoder.feed(raw, self.now)
        self.case.assertEqual(decoded.diagnostics, ())
        self.frames.extend(decoded.frames)
        return raw

    def one(self):
        start = len(self.frames)
        for _ in range(544):
            self.case.assertTrue(self.read(1))
            if len(self.frames) > start:
                return self.frames[-1]
        self.case.fail('frame exceeded wire bound')

    def drain(self):
        start = len(self.frames)
        for _ in range(10000):
            if not self.read(97):
                return self.frames[start:]
        self.case.fail('raw peer drain did not finish')

    def until(self, kind):
        for _ in range(32):
            frame = self.one()
            if frame.message_type == kind:
                return frame
        self.case.fail('frame kind not reached')

    def complete(self, timeout=False):
        if timeout:
            self.now += 100
            self.device.tick(self.now)
        else:
            self.case.assertTrue(self.device.trigger(self.now))
        frames = self.drain()
        terminal = next(f for f in frames if f.message_type == 146)
        generation, window, _, _, crc = struct.unpack('<IIHHI', terminal.payload)
        return struct.pack('<III', generation, window, crc)


class Checks(unittest.TestCase):
    def unchanged(self, owner, operation, error=SessionError):
        before = owner.snapshot()
        with self.assertRaises(error):
            operation()
        self.assertEqual(owner.snapshot(), before)

    def result(self, *, mode=0, timeout=False, variant='valid', edge=0):
        source = 1 if variant == 'deferred' else 0
        context = ExpectedContext(ref.NONCE, 4, 1, mode, source, edge, 2048, 0)
        begin = ref.begin(edge=edge, timeout=timeout)
        if variant == 'deferred':
            begin = ref.begin(edge=edge, kind=1, source=1, external=1, quality=6)
        if variant == 'rail':
            begin = ref.begin(edge=edge, timeout=timeout, quality=1 if timeout else 0)
        values = ref.samples(4, 1, edge, timeout=timeout)
        payloads = [(144, begin)]
        blocks = [(145, ref.data(values[i:i + 250], i)) for i in range(0, 2048, 250)]
        if variant == 'missing':
            blocks.pop()
        if variant == 'reorder':
            blocks[0], blocks[1] = blocks[1], blocks[0]
        if variant == 'duplicate':
            blocks.insert(1, blocks[0])
        payloads.extend(blocks)
        payloads.append((146, ref.end(values, crc=0 if variant == 'crc' else None)))
        receiver, decoder, outcome = WindowReceiver(context), StreamDecoder(), None
        for sequence, (kind, payload) in enumerate(payloads, 1):
            raw = ref.packet(kind, ref.NONCE, sequence, 0, payload)
            for pos in range(0, len(raw), 31):
                decoded = decoder.feed(raw[pos:pos + 31], 0)
                self.assertEqual(decoded.diagnostics, ())
                for frame in decoded.frames:
                    result = receiver.feed(frame, 0)
                    if result is not None:
                        self.assertIsNone(outcome)
                        outcome = result
        self.assertIsNotNone(outcome)
        return context, outcome


class CapabilityAndArguments(Checks):
    def test_oracle_crc_check_and_pins(self):
        self.assertEqual(ref.crc32(b'123456789'), 0xCBF43926)
        import motortester.device_emulator as emulator
        import motortester.session as session
        import motortester.window as window
        self.assertEqual(emulator.SOURCE_SHA, ref.EMULATOR_PIN)
        self.assertEqual(session.SOURCE_SHA, ref.SESSION_PIN)
        self.assertEqual(window.PROTOCOL_SOURCE_SHA, ref.WINDOW_PIN)
        self.assertEqual(session.VALIDATION_SCOPE, 'draft_analog_control')
        self.assertTrue(emulator.PROVENANCE.timeout_provisional)
        self.assertEqual(emulator.HOLD_TIMEOUT_MS, 5000)

    def test_constructor_strict_bool_keyword_and_default_capabilities(self):
        for bad in (0, 1, None, 'true', [], object()):
            with self.subTest(value=repr(bad)), self.assertRaises(DeviceError):
                DeviceEmulator(all_analog_modes=bad)
        with self.assertRaises(TypeError):
            DeviceEmulator(True)
        for opted in (False, True):
            with self.subTest(opted=opted):
                link = Link(self, all_modes=opted, configure=False)
                self.assertEqual(link.frames[0].payload, ref.hello(opted))
                self.assertEqual(link.frames[1].payload, ref.caps(opted))
                caps = link.controller.snapshot().capabilities
                self.assertEqual((caps.capability_bits, caps.mode_bits, caps.source_bits,
                                  caps.edge_bits, caps.pretrigger_samples),
                                 (0x1D if opted else 9, 7 if opted else 2, 1, 3, (0,)))
        device = DeviceEmulator()
        device.write(ref.packet(1, 0, 1, 1, struct.pack('<Q', ref.NONCE)), 0)
        frame = StreamDecoder().feed(device.read(4096, 0), 0).frames[0]
        self.assertEqual(frame.payload, ref.hello(False))

    def test_all_six_mode_edge_configs_and_limits_are_exact(self):
        for mode in range(3):
            for edge in range(2):
                for level in (1, 4094):
                    for timeout in ((100, 60000) if mode == 0 else (0,)):
                        with self.subTest(mode=mode, edge=edge, level=level, timeout=timeout):
                            link = Link(self, configure=False)
                            config = Config(mode=mode, edge=edge, level_code=level,
                                            auto_timeout_ms=timeout)
                            link.controller.configure(config, 0)
                            link.send()
                            link.drain()
                            expected = ref.config(mode, edge, timeout, level)
                            self.assertEqual(link.commands[-1].payload, expected)
                            self.assertEqual(link.frames[-1].payload, struct.pack('<I', 1) + expected)
                            self.assertEqual(link.device.snapshot().config, expected)

    def test_unsupported_default_and_other_features_reject_before_mutation(self):
        for opted in (False, True):
            link = Link(self, all_modes=opted, configure=False)
            cases = [Config(source=1), Config(profile=2), Config(pretrigger_samples=512)]
            if not opted:
                cases.extend((Config(mode=0, auto_timeout_ms=100), Config(mode=2)))
            for config in cases:
                with self.subTest(opted=opted, config=config):
                    self.unchanged(link.controller,
                        lambda: link.controller.configure(config, 60001))
                    self.assertIsNone(link.controller.prepare_tx())

    def test_config_wrong_types_huge_values_and_mode_timeout_boundaries(self):
        for field in Config.__dataclass_fields__:
            for bad in (True, 1.5, '1', None, -1, 1 << 1000):
                with self.subTest(field=field, bad=repr(bad)), self.assertRaises(SessionError):
                    Config(**{field: bad})
        for mode, timeout in ((0, 0), (0, 99), (0, 60001), (1, 100), (2, 100)):
            with self.subTest(mode=mode, timeout=timeout), self.assertRaises(SessionError):
                Config(mode=mode, auto_timeout_ms=timeout)
        for mode in (0, 2):
            link = Link(self, mode=mode)
            corrupted = Config(mode=mode, auto_timeout_ms=100 if mode == 0 else 0)
            object.__setattr__(corrupted, 'auto_timeout_ms', 1 << 1000)
            self.unchanged(link.controller, lambda: link.controller.configure(corrupted, 60001))
            for bad in (True, -1, 1.0, None, 1 << 1000):
                with self.subTest(mode=mode, bad=repr(bad)):
                    self.unchanged(link.device, lambda: link.device.tick(bad), DeviceError)
                    self.unchanged(link.device, lambda: link.device.trigger(bad), DeviceError)
                    self.unchanged(link.device, lambda: link.device.read(1, bad), DeviceError)
                    self.unchanged(link.device, lambda: link.device.write(b'', bad), DeviceError)
            self.unchanged(link.device, lambda: link.device.read(True, 60001), DeviceError)
            self.unchanged(link.device, lambda: link.device.read(4097, 60001), DeviceError)
            self.unchanged(link.device, lambda: link.device.write(bytearray(), 60001), DeviceError)
            self.unchanged(link.device, lambda: link.device.write(b'x' * 4097, 60001), DeviceError)

    def test_session_rejects_mode_incorrect_timeout_and_bad_trigger_metadata(self):
        for mode in range(3):
            invalid = [dict(index=65535), dict(kind=1), dict(external=1),
                       dict(source=1), dict(quality=0x80), dict(quality=3)]
            if mode != 0:
                invalid.append(dict(timeout=True))
            else:
                invalid.extend((dict(timeout=True, index=0), dict(timeout=True, quality=2)))
            for fields in invalid:
                with self.subTest(mode=mode, fields=fields):
                    link = Link(self, mode=mode)
                    snapshot = link.controller.snapshot()
                    raw = ref.packet(144, snapshot.session, snapshot.next_rx_sequence, 0,
                                     ref.begin(**fields))
                    frame = StreamDecoder().feed(raw, 0).frames[0]
                    events = link.controller.receive(frame, 0)
                    self.assertIs(link.controller.snapshot().phase, Phase.RECOVERY)
                    self.assertIs(events[-1].reason, RecoveryReason.WINDOW)
                    self.assertIsNone(link.controller.prepare_tx())

    def test_raw_invalid_config_preserves_previous_config(self):
        for mode in range(3):
            peer = RawPeer(self, mode=mode)
            peer.command(5, struct.pack('<I', peer.generation))
            peer.drain()
            initial = peer.device.snapshot()
            wrong = [ref.config(mode, timeout=99 if mode == 0 else 100),
                     ref.config(mode, timeout=60001), ref.config(3),
                     ref.config(mode, source=1), ref.config(mode, pre=512),
                     ref.config(mode, reserved=1), ref.config(mode, level=0),
                     ref.config(mode, level=4095)]
            for payload in wrong:
                with self.subTest(mode=mode, payload=payload.hex()):
                    peer.command(3, payload)
                    frames = peer.drain()
                    self.assertEqual([f.message_type for f in frames], [255])
                    self.assertIn(frames[0].payload[1], (1, 6))
                    after = peer.device.snapshot()
                    self.assertEqual((after.config, after.config_id, after.state),
                                     (initial.config, initial.config_id, initial.state))

    def test_inconsistent_caps_fail_closed_for_each_advertised_dimension(self):
        for overrides in ({'modes': 2}, {'sources': 3}, {'pre_count': 3},
                          {'pre1': 512}, {'edges': 4}):
            with self.subTest(overrides=overrides):
                c = SessionController()
                c.start(ref.NONCE, 0)
                ticket = c.prepare_tx()
                c.written(ticket, len(ticket.encoded), 0)
                frame = StreamDecoder().feed(ref.packet(129, ref.NONCE, 1, 1, ref.hello()), 0).frames[0]
                c.receive(frame, 0)
                ticket = c.prepare_tx()
                c.written(ticket, len(ticket.encoded), 0)
                frame = StreamDecoder().feed(ref.packet(130, ref.NONCE, 2, 2, ref.caps(**overrides)), 0).frames[0]
                events = c.receive(frame, 0)
                self.assertIs(c.snapshot().phase, Phase.RECOVERY)
                self.assertIs(events[-1].reason, RecoveryReason.CAPABILITIES)
                self.unchanged(c, lambda: c.arm(0))


class WaitingAndCaptures(Checks):
    def test_normal_single_no_event_wait_has_no_command_or_receiver_deadline(self):
        for mode in (1, 2):
            with self.subTest(mode=mode):
                link = Link(self, mode=mode)
                receiver = WindowReceiver(context_for(link.controller))
                for now in (999, 1000, 2999, 3000, 60000, (1 << 64) - 1):
                    link.now = now
                    self.assertEqual(link.controller.tick(now), ())
                    self.assertEqual(link.device.tick(now), ())
                    self.assertIsNone(receiver.tick(now))
                    self.assertEqual(link.drain(), [])
                    self.assertIs(link.controller.snapshot().phase, Phase.ARMED)
                    self.assertEqual(link.device.snapshot().state, 2)
                    self.assertEqual(link.device.snapshot().window_id, 0)

    def test_every_valid_time_entrypoint_observes_exact_auto_expiry(self):
        for operation in ('tick', 'read_zero', 'write_empty', 'trigger'):
            with self.subTest(operation=operation):
                link = Link(self, mode=0)
                before = link.device.snapshot()
                link.now = 100
                if operation == 'tick':
                    self.assertEqual(link.device.tick(100), ())
                elif operation == 'read_zero':
                    self.assertEqual(link.device.read(0, 100), b'')
                elif operation == 'write_empty':
                    self.assertEqual(link.device.write(b'', 100).accepted_prefix, 0)
                else:
                    self.assertFalse(link.device.trigger(100))
                after = link.device.snapshot()
                self.assertEqual(after.window_id, 1)
                self.assertEqual(after.next_tx_sequence, before.next_tx_sequence)
                link.drain()
                self.assertEqual(len(link.results), 1)
                self.assertEqual(link.results[0][1].begin.trigger_kind, 2)

    def test_auto_endpoints_before_exact_after_and_large_jump_one_window(self):
        for timeout in (100, 60000):
            for offset in (0, 1, 100000000):
                with self.subTest(timeout=timeout, offset=offset):
                    link = Link(self, mode=0, timeout=timeout)
                    link.now = timeout - 1
                    self.assertEqual(link.controller.tick(link.now), ())
                    self.assertEqual(link.device.tick(link.now), ())
                    self.assertEqual(link.drain(), [])
                    self.assertEqual(link.device.snapshot().window_id, 0)
                    link.now = timeout + offset
                    self.assertEqual(link.controller.tick(link.now), ())
                    self.assertEqual(link.device.tick(link.now), ())
                    link.drain()
                    self.assertEqual(len(link.results), 1)
                    self.assertEqual(link.device.snapshot().window_id, 1)
                    self.assertEqual(link.device.snapshot().state, 6)
                    self.assertEqual(sum(f.message_type == 144 for f in link.frames), 1)
                    self.assertEqual(sum(f.message_type == 146 for f in link.frames), 1)
                    self.assertFalse(link.device.trigger(link.now))
                    self.assertEqual(link.drain(), [])

    def test_zero_pretrigger_real_event_is_ready_at_arm_execution_before_ack(self):
        for mode in range(3):
            for edge in range(2):
                with self.subTest(mode=mode, edge=edge):
                    link = Link(self, mode=mode, edge=edge, drain_arm=False)
                    self.assertNotIn(132, [f.message_type for f in link.frames])
                    self.assertTrue(link.device.trigger(0))
                    link.drain()
                    self.assertEqual(len(link.results), 1)
                    self.assertEqual(link.results[0][1].begin.trigger_kind, 0)
                    self.assertEqual(link.results[0][1].begin.trigger_index, 0)
                    self.assertEqual(link.results[0][1].samples, ref.samples(4, 1, edge))

    def test_auto_starts_at_arm_execution_even_with_unfinished_arm_ack(self):
        link = Link(self, mode=0, drain_arm=False)
        self.assertEqual(link.device.snapshot().state, 2)
        self.assertEqual(link.device.snapshot().generation, 4)
        self.assertTrue(link.read(1))
        self.assertNotIn(132, [f.message_type for f in link.frames])
        link.now = 99
        link.device.tick(link.now)
        self.assertEqual(link.device.snapshot().window_id, 0)
        link.now = 100
        link.device.tick(link.now)
        self.assertEqual(link.device.snapshot().window_id, 1)
        link.drain()
        types = [f.message_type for f in link.frames]
        self.assertLess(types.index(132), types.index(144))
        self.assertEqual(len(link.results), 1)
        self.assertEqual(link.results[0][1].begin.trigger_kind, 2)

    def test_command_and_receiver_progress_deadlines_are_distinct_from_auto_wait(self):
        link = Link(self, mode=0, timeout=60000, drain_arm=False)
        self.assertEqual(link.controller.tick(999), ())
        events = link.controller.tick(1000)
        self.assertIs(events[-1].reason, RecoveryReason.TIMEOUT)
        self.assertIs(link.controller.snapshot().phase, Phase.RECOVERY)
        link = Link(self, mode=0, timeout=60000)
        self.assertTrue(link.device.trigger(0))
        link.until(144)
        self.assertIsNotNone(link.receiver)
        self.assertIsNone(link.receiver.tick(2999))
        result = link.receiver.tick(3000)
        self.assertFalse(result.complete)
        self.assertIs(result.reason, WindowCode.TIMEOUT)
        self.assertEqual(link.controller.tick(3000), ())
        self.assertIs(link.controller.snapshot().phase, Phase.ARMED)

    def test_local_expiry_before_input_tie_policy_real_before_timeout_at_tie(self):
        for moment, accepted, kind in ((99, True, 0), (100, False, 2), (101, False, 2)):
            with self.subTest(moment=moment):
                link = Link(self, mode=0)
                link.now = moment
                self.assertIs(link.device.trigger(moment), accepted)
                link.drain()
                self.assertEqual(len(link.results), 1)
                self.assertEqual(link.results[0][1].begin.trigger_kind, kind)

    def test_invalid_calls_at_expiry_do_not_advance_auto_clock_or_allocate_window(self):
        link = Link(self, mode=0)
        for operation in (lambda: link.device.read(-1, 100),
                          lambda: link.device.write(bytearray(), 100),
                          lambda: link.device.write(b'x' * 4097, 100)):
            self.unchanged(link.device, operation, DeviceError)
        self.assertEqual(link.device.snapshot().window_id, 0)
        self.assertTrue(link.device.trigger(99))
        link.now = 99
        link.drain()
        self.assertEqual(link.results[-1][1].begin.trigger_kind, 0)

    def test_twenty_new_mode_captures_exact_bytes_samples_crc_and_release(self):
        records = []
        for mode in (0, 2):
            link = Link(self, mode=mode, edge=1 if mode == 2 else 0)
            generations = []
            for index in range(10):
                timed_out = mode == 0 and index % 2 == 1
                context, result, receipt = link.capture(timed_out, chunk=(1, 31, 97, 4096)[index % 4])
                generations.append(context.generation)
                expected = ref.samples(context.generation, index + 1, context.edge,
                                       context.level_code, timed_out)
                self.assertEqual(result.samples, expected)
                self.assertEqual(result.samples_crc32, ref.crc32(ref.sample_bytes(expected)))
                self.assertEqual(result.begin.trigger_kind, 2 if timed_out else 0)
                self.assertEqual(result.begin.trigger_index, 65535 if timed_out else 0)
                self.assertEqual(result.begin.external_edge_id, 0)
                self.assertEqual(result.begin.quality_flags, 3 if timed_out else 2)
                self.assertTrue(result.rail_observed)
                current = [f for f in link.frames if f.message_type in (144, 145, 146)
                           and struct.unpack_from('<II', f.payload) == (context.generation, index + 1)]
                self.assertEqual([f.message_type for f in current], [144] + [145] * 9 + [146])
                self.assertEqual(current[0].payload, ref.begin(context.generation, index + 1,
                                 context.edge, context.level_code, timed_out))
                for frame, offset in zip(current[1:-1], range(0, 2048, 250)):
                    self.assertEqual(frame.payload, ref.data(expected[offset:offset + 250],
                                     offset, context.generation, index + 1))
                self.assertEqual(current[-1].payload, ref.end(expected, context.generation, index + 1))
                self.assertEqual(link.device.snapshot().state, 6)
                self.assertTrue(link.controller.snapshot().window_barrier)
                link.release(receipt, drain=False)
                self.assertEqual(link.commands[-1].payload, struct.pack('<III', context.generation,
                                 index + 1, result.samples_crc32))
                self.assertTrue(link.read(1))
                self.assertTrue(link.controller.snapshot().window_barrier)
                self.assertIs(link.controller.snapshot().phase, Phase.ARMED)
                self.assertFalse(link.device.trigger(link.now))
                link.drain()
                self.assertFalse(link.controller.snapshot().window_barrier)
                self.assertEqual(link.device.snapshot().state, 0 if mode == 2 else 2)
                self.assertIs(link.controller.snapshot().phase, Phase.READY if mode == 2 else Phase.ARMED)
                records.append(dict(mode=mode, edge=context.edge, generation=context.generation,
                                    window=index + 1, timeout=timed_out, crc=result.samples_crc32))
                if mode == 2:
                    self.assertFalse(link.device.trigger(link.now))
                    self.assertEqual(link.drain(), [])
                    if index < 9:
                        link.arm()
            self.assertEqual(len(set(generations)), 1 if mode == 0 else 10)
            self.assertEqual(sum(c.message_type == 4 for c in link.commands), 1 if mode == 0 else 10)
            self.assertEqual(sum(e.kind is EventKind.WINDOW_RELEASED for e in link.events), 10)
            self.assertEqual(len(link.results), 10)
            target = os.environ.get('ACQUISITION_CAPTURE_DIR')
            if target:
                output = Path(target)
                output.mkdir(parents=True, exist_ok=True)
                for suffix, raw in (('device', link.rx), ('host', link.tx)):
                    with (output / f'mode-{mode}-{suffix}.bin').open('xb') as f:
                        f.write(raw)
        self.assertEqual(len(records), 20)
        if os.environ.get('ACQUISITION_CAPTURE_DIR'):
            with (Path(os.environ['ACQUISITION_CAPTURE_DIR']) / 'captures.json').open('x') as f:
                json.dump(records, f, indent=2)
                f.write('\n')

    def test_auto_rewait_begins_on_last_release_ack_byte_not_release_command(self):
        link = Link(self, mode=0)
        _, _, receipt = link.capture()
        link.release(receipt, drain=False)
        self.assertTrue(link.read(1))
        link.now = 300
        link.device.tick(link.now)
        self.assertEqual(link.device.snapshot().window_id, 1)
        self.assertFalse(link.device.trigger(link.now))
        frame = link.one()
        self.assertEqual(frame.message_type, 135)
        link.drain()
        link.now = 399
        link.device.tick(link.now)
        self.assertEqual(link.drain(), [])
        link.now = 400
        link.device.tick(link.now)
        link.drain()
        self.assertEqual(len(link.results), 2)
        self.assertEqual(link.results[-1][1].begin.trigger_kind, 2)
        self.assertEqual(link.controller.snapshot().generation, 4)


class ReceiptFactory(Checks):
    def test_factory_accepts_mode_correct_real_and_auto_timeout_receiver_results(self):
        for mode in range(3):
            for edge in range(2):
                for timeout in ((False, True) if mode == 0 else (False,)):
                    with self.subTest(mode=mode, edge=edge, timeout=timeout):
                        context, result = self.result(mode=mode, timeout=timeout, edge=edge)
                        receipt = WindowReceipt.from_window(context, result)
                        self.assertEqual((receipt.session, receipt.generation, receipt.window_id,
                                          receipt.sample_count, receipt.samples_crc32),
                                         (ref.NONCE, 4, 1, 2048, result.samples_crc32))

    def test_factory_itself_rejects_bad_receiver_results_without_release_wrapper(self):
        for variant in ('missing', 'reorder', 'crc', 'rail', 'deferred'):
            with self.subTest(variant=variant):
                context, result = self.result(variant=variant, timeout=variant != 'deferred')
                with self.assertRaises(SessionError):
                    WindowReceipt.from_window(context, result)
        context, result = self.result(variant='duplicate', timeout=True)
        self.assertTrue(result.semantic_checks_complete)
        self.assertIsNotNone(WindowReceipt.from_window(context, result))

    def test_factory_itself_rejects_corruption_and_mismatched_context(self):
        for mode, timeout in ((0, True), (0, False), (2, False)):
            context, result = self.result(mode=mode, timeout=timeout)
            corruptions = [replace(result, complete=False), replace(result, reason=WindowCode.CRC),
                replace(result, missing_ranges=((0, 1),)), replace(result, deferred_checks=('UNKNOWN',)),
                replace(result, metadata_issues=('RAIL_FLAG_MISMATCH',)),
                replace(result, abort_reason=1), replace(result, samples=result.samples[:-1]),
                replace(result, samples_crc32=result.samples_crc32 ^ 1),
                replace(result, rail_observed=False), replace(result, fragments=result.fragments[:-1]),
                replace(result, samples=(True,) + result.samples[1:]),
                replace(result, samples=(4096,) + result.samples[1:]),
                replace(result, fragments=(SampleBlock(1, result.fragments[0].samples),) + result.fragments[1:])]
            for field, value in (('generation', 5), ('config_id', 2), ('sample_count', 2047),
                                 ('trigger_index', 0 if timeout else 65535),
                                 ('trigger_kind', 0 if timeout else 2), ('external_edge_id', 1),
                                 ('quality_flags', 2 if timeout else 3), ('edge', 1),
                                 ('source', 1), ('channel', 1), ('level_code', 2047),
                                 ('timer_arr', 20), ('pretrigger_samples', 512)):
                corruptions.append(replace(result, begin=replace(result.begin, **{field: value})))
            for index, corrupted in enumerate(corruptions):
                with self.subTest(mode=mode, timeout=timeout, corruption=index):
                    with self.assertRaises(SessionError):
                        WindowReceipt.from_window(context, corrupted)
            for changed in (replace(context, generation=5), replace(context, config_id=2),
                            replace(context, edge=1), replace(context, level_code=2047),
                            replace(context, source=1), replace(context, pretrigger_samples=512)):
                with self.subTest(mode=mode, context=changed), self.assertRaises(SessionError):
                    WindowReceipt.from_window(changed, result)
            if timeout:
                for other in (1, 2):
                    with self.assertRaises(SessionError):
                        WindowReceipt.from_window(replace(context, acquisition_mode=other), result)

    def test_factory_strict_types_large_inputs_and_session_binds_receipt(self):
        context, result = self.result(mode=0, timeout=True)
        for invalid_context, invalid_result in ((None, result), (context, None), (context, object())):
            with self.assertRaises(SessionError):
                WindowReceipt.from_window(invalid_context, invalid_result)
        for field, value in (('generation', 1 << 1000), ('sample_count', True),
                             ('quality_flags', 1 << 1000)):
            with self.assertRaises(SessionError):
                WindowReceipt.from_window(context, replace(result, begin=replace(result.begin, **{field: value})))
        for mode in (0, 2):
            link = Link(self, mode=mode)
            context, result, receipt = link.capture(timeout=mode == 0)
            raw = WindowReceipt(receipt.session, receipt.generation, receipt.window_id,
                                receipt.sample_count, receipt.samples_crc32)
            self.unchanged(link.controller, lambda: link.controller.release(raw, link.now))
            foreign = WindowReceipt.from_window(replace(context, session=context.session + 1), result)
            self.unchanged(link.controller, lambda: link.controller.release(foreign, link.now))
            link.release(receipt)
            self.unchanged(link.controller, lambda: link.controller.release(receipt, link.now))


class ReleaseAndBarriers(Checks):
    def test_raw_release_identity_crc_duplicate_and_single_no_rearm(self):
        for mode in range(3):
            with self.subTest(mode=mode):
                peer = RawPeer(self, mode=mode)
                release = peer.complete(timeout=mode == 0)
                generation, window, crc = struct.unpack('<III', release)
                for bad in ((generation + 1, window, crc), (generation, window + 1, crc),
                            (generation, window, crc ^ 1)):
                    peer.command(7, struct.pack('<III', *bad))
                    frames = peer.drain()
                    self.assertEqual([f.message_type for f in frames], [255])
                    self.assertEqual(frames[0].payload[:2], b'\x07\x07')
                    self.assertTrue(peer.device.snapshot().held_samples)
                    self.assertEqual(peer.device.snapshot().state, 6)
                first = peer.command(7, release)
                self.assertTrue(peer.read(1))
                self.assertEqual(peer.device.snapshot().state, 6)
                self.assertFalse(peer.device.trigger(peer.now))
                duplicate = peer.write(first)
                self.assertEqual([(d.code, d.count) for d in duplicate.diagnostics], [('DUPLICATE', 1)])
                frames = peer.drain()
                self.assertEqual(sum(f.message_type == 135 for f in frames), 1)
                self.assertEqual(peer.device.snapshot().state, 0 if mode == 2 else 2)
                before = peer.device.snapshot()
                peer.command(7, release)
                frames = peer.drain()
                self.assertEqual([f.message_type for f in frames], [135])
                after = peer.device.snapshot()
                self.assertEqual((after.generation, after.window_id, after.state, after.continuation),
                                 (before.generation, before.window_id, before.state, before.continuation))
                if mode == 2:
                    self.assertFalse(peer.device.trigger(peer.now + 60000))
                    self.assertEqual(peer.device.snapshot().window_id, 1)

    def test_raw_tombstone_replacement_and_late_release_does_not_clear_new_window(self):
        for mode in (0, 2):
            with self.subTest(mode=mode):
                peer = RawPeer(self, mode=mode)
                old = peer.complete()
                peer.command(7, old)
                peer.drain()
                if mode == 2:
                    peer.generation = peer.request
                    peer.command(4, struct.pack('<I', 1))
                    peer.drain()
                self.assertTrue(peer.device.trigger(peer.now))
                peer.until(144)
                peer.command(7, old)
                frame = peer.one()
                self.assertEqual(frame.message_type, 135)
                self.assertTrue(peer.device.snapshot().held_samples)
                self.assertEqual(peer.device.snapshot().window_id, 2)
                frames = peer.drain()
                end = next(f for f in frames if f.message_type == 146)
                generation, window, _, _, crc = struct.unpack('<IIHHI', end.payload)
                new = struct.pack('<III', generation, window, crc)
                peer.command(7, old)
                frames = peer.drain()
                self.assertEqual([f.message_type for f in frames], [255])
                self.assertEqual(frames[0].payload[:2], b'\x07\x07')
                self.assertTrue(peer.device.snapshot().held_samples)
                peer.command(5, struct.pack('<I', peer.generation))
                peer.drain()
                peer.command(7, new)
                frames = peer.drain()
                self.assertEqual([f.message_type for f in frames], [135])
                self.assertEqual(peer.device.snapshot().state, 0)
                self.assertFalse(peer.device.trigger(peer.now + 60000))
                self.assertEqual(peer.device.snapshot().window_id, 2)

    def test_duplicate_auto_release_does_not_restart_existing_wait_deadline(self):
        peer = RawPeer(self, mode=0)
        release = peer.complete()
        peer.command(7, release)
        peer.drain()
        peer.now = 99
        peer.command(7, release)
        peer.drain()
        peer.now = 100
        peer.device.tick(peer.now)
        peer.drain()
        self.assertEqual(peer.device.snapshot().window_id, 2)
        begins = [f for f in peer.frames if f.message_type == 144]
        self.assertEqual(len(begins), 2)
        self.assertEqual(begins[-1].payload[20], 2)

    def test_pending_stop_blocks_commands_after_single_release_ack(self):
        for mode in (0, 2):
            with self.subTest(mode=mode):
                link = Link(self, mode=mode)
                _, _, receipt = link.capture()
                link.release(receipt, drain=False)
                self.assertTrue(link.read(1))
                self.assertEqual(link.controller.stop(link.now), ())
                link.send()
                self.assertEqual(link.one().message_type, 135)
                self.assertTrue(link.controller.snapshot().stop_barrier)
                self.assertIs(link.controller.snapshot().phase, Phase.READY if mode == 2 else Phase.ARMED)
                self.unchanged(link.controller, lambda: link.controller.arm(link.now))
                self.unchanged(link.controller, lambda: link.controller.configure(Config(), link.now))
                self.assertIsNone(link.controller.prepare_tx())
                self.assertFalse(link.device.trigger(link.now))
                self.assertEqual(link.until(133).message_type, 133)
                self.assertIs(link.controller.snapshot().phase, Phase.READY)
                self.assertFalse(link.controller.snapshot().stop_barrier)
                link.drain()
                link.now += 60000
                self.assertFalse(link.device.trigger(link.now))
                self.assertEqual(link.drain(), [])
                self.assertEqual(sum(f.message_type in (146, 147) for f in link.frames), 1)

    def reach(self, peer, boundary):
        if boundary in ('before_expiry', 'at_expiry'):
            peer.now = 99 if boundary == 'before_expiry' else 100
            return
        peer.now = 100
        peer.device.tick(peer.now)
        if peer.config[2] == 2:
            self.assertTrue(peer.device.trigger(peer.now))
        if boundary == 'allocated':
            return
        if boundary == 'begin_first':
            self.assertEqual(peer.one().message_type, 134)
            self.assertTrue(peer.read(1))
            return
        peer.until(144)
        if boundary == 'data_first':
            self.assertTrue(peer.read(1))
            return
        for _ in range(9):
            self.assertEqual(peer.one().message_type, 145)
        if boundary == 'end_unstarted':
            return
        if boundary == 'end_first':
            self.assertTrue(peer.read(1))
            return
        end = peer.one()
        self.assertEqual(end.message_type, 146)
        peer.drain()
        if boundary == 'end_complete':
            return
        generation, window, _, _, crc = struct.unpack('<IIHHI', end.payload)
        peer.command(7, struct.pack('<III', generation, window, crc))
        self.assertTrue(peer.read(1))
        self.assertEqual(boundary, 'release_ack_first')

    def test_stop_at_auto_expiry_and_every_window_boundary_has_one_terminal(self):
        boundaries = ('before_expiry', 'at_expiry', 'allocated', 'begin_first',
                      'data_first', 'end_unstarted', 'end_first', 'end_complete',
                      'release_ack_first')
        for mode, boundary in ((m, b) for m in (0, 2) for b in boundaries
                               if m == 0 or b != 'at_expiry'):
            with self.subTest(mode=mode, boundary=boundary):
                peer = RawPeer(self, mode=mode)
                self.reach(peer, boundary)
                peer.command(5, struct.pack('<I', peer.generation))
                peer.drain()
                types = [f.message_type for f in peer.frames]
                terminal = [kind for kind in types if kind in (146, 147)]
                expected = [] if boundary == 'before_expiry' else [
                    146 if boundary in ('end_first', 'end_complete', 'release_ack_first') else 147]
                self.assertEqual(terminal, expected)
                self.assertEqual(types.count(133), 1)
                self.assertFalse(any(kind in (144, 145, 146, 147) for kind in types[types.index(133) + 1:]))
                self.assertEqual(peer.device.snapshot().state, 0)
                self.assertFalse(peer.device.snapshot().continuation)
                self.assertFalse(peer.device.snapshot().held_samples)
                peer.command(5, struct.pack('<I', peer.generation))
                later = peer.drain()
                self.assertFalse(any(f.message_type in (144, 145, 146, 147) for f in later))
                self.assertFalse(peer.device.trigger(peer.now + 1000000))
                self.assertEqual(peer.device.snapshot().window_id, 0 if boundary == 'before_expiry' else 1)
                self.assertLessEqual(peer.device.snapshot().control_descriptors, 8)
                self.assertEqual(peer.device.snapshot().sample_buffer_bytes, 4096)

    def test_hello_at_expiry_and_every_boundary_preserves_only_started_old_frame(self):
        boundaries = ('before_expiry', 'at_expiry', 'allocated', 'begin_first',
                      'data_first', 'end_unstarted', 'end_first', 'end_complete',
                      'release_ack_first')
        for mode, boundary in ((m, b) for m in (0, 2) for b in boundaries
                               if m == 0 or b != 'at_expiry'):
            with self.subTest(mode=mode, boundary=boundary):
                peer = RawPeer(self, mode=mode)
                self.reach(peer, boundary)
                start = len(peer.frames)
                fresh = ref.NONCE + 1
                self.assertEqual(peer.write(ref.packet(1, 0, 1, 1, struct.pack('<Q', fresh))).diagnostics, ())
                peer.drain()
                frames = peer.frames[start:]
                expected_old = {'begin_first': 144, 'data_first': 145,
                                'end_first': 146, 'release_ack_first': 135}.get(boundary)
                self.assertEqual([f.message_type for f in frames],
                                 ([expected_old] if expected_old is not None else []) + [129])
                if expected_old is not None:
                    self.assertEqual(frames[0].session, ref.NONCE)
                self.assertEqual((frames[-1].session, frames[-1].sequence, frames[-1].request_id),
                                 (fresh, 1, 1))
                self.assertEqual(frames[-1].payload, ref.hello(True))
                snapshot = peer.device.snapshot()
                self.assertEqual((snapshot.state, snapshot.generation, snapshot.window_id,
                                  snapshot.config_id, snapshot.config, snapshot.terminal, snapshot.tombstone),
                                 (0, 0, 0, 0, None, None, None))
                self.assertFalse(peer.device.trigger(peer.now + 1000000))
                self.assertEqual(peer.device.read(4096, peer.now + 1000000), b'')

    def test_constructor_policy_survives_fresh_hello_and_disconnect(self):
        for opted in (False, True):
            for reset in ('hello', 'disconnect'):
                with self.subTest(opted=opted, reset=reset):
                    peer = RawPeer(self, mode=1, all_modes=opted)
                    if reset == 'disconnect':
                        peer.device.disconnect(0)
                    fresh = ref.NONCE + 2
                    peer.write(ref.packet(1, 0, 1, 1, struct.pack('<Q', fresh)))
                    frames = peer.drain()
                    self.assertEqual(frames[-1].payload, ref.hello(opted))
                    peer.session, peer.sequence, peer.request = fresh, 2, 2
                    peer.command(2)
                    self.assertEqual(peer.drain()[-1].payload, ref.caps(opted))

    def test_provisional_hold_timeout_remains_separate_and_does_not_rearm(self):
        for mode in (0, 2):
            with self.subTest(mode=mode):
                peer = RawPeer(self, mode=mode)
                release = peer.complete(timeout=mode == 0)
                end_time = peer.now
                peer.now = end_time + 4999
                peer.device.tick(peer.now)
                self.assertEqual(peer.device.snapshot().state, 6)
                peer.now += 1
                peer.device.tick(peer.now)
                self.assertEqual(peer.device.snapshot().state, 5)
                self.assertEqual(peer.device.snapshot().reason, 3)
                peer.command(7, release)
                peer.drain()
                self.assertFalse(peer.device.trigger(peer.now + 60000))
                self.assertEqual(peer.device.snapshot().window_id, 1)
                self.assertEqual(sum(f.message_type in (146, 147) for f in peer.frames), 1)


if __name__ == '__main__':
    unittest.main()

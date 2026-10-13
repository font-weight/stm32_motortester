"""Independent HOST-008 conformance for unaccepted Normal/analog drafts.

Expected bytes/samples derive from pinned documents and a separate small oracle.
The caller path uses real StreamDecoder and WindowReceiver, never a mock receipt.
No bare assert or skipped dependency is allowed; tests also execute with -O.
"""
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
from pathlib import Path
import struct
import sys
import unittest

from motortester.device_emulator import DeviceEmulator
from motortester.session import (Config, ErrorCode, EventKind, FaultCode, Phase,
                                RecoveryReason, SessionController, SessionError,
                                WindowReceipt)
from motortester.window import (BeginMetadata, ExpectedContext, SampleBlock,
                               WindowCode, WindowReceiver, WindowResult)
from motortester.wire import Frame, StreamDecoder

FIXTURES = Path(__file__).parent / 'fixtures' / 'session_release'
_spec = importlib.util.spec_from_file_location('host008_independent_reference', FIXTURES / 'reference.py')
ref = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ref
_spec.loader.exec_module(ref)


def kinds(events):
    return [event.kind for event in events]


def context_for(controller):
    s = controller.snapshot()
    c = s.config
    return ExpectedContext(s.session, s.generation, s.config_id, c.mode, c.source,
                           c.edge, c.level_code, c.pretrigger_samples, c.profile)


class Link:
    """A test-owned virtual transport. Every peer exchange is real bytes."""
    def __init__(self, case, *, edge=0, level=2048):
        self.case = case
        self.controller, self.device, self.decoder = SessionController(), DeviceEmulator(), StreamDecoder()
        self.now, self.receiver, self.context = 0, None, None
        self.events, self.frames, self.commands, self.results = [], [], [], []
        self.controller.start(ref.NONCE, self.now)
        self.send()
        self.drain()
        self.send()  # GET_CAPS, queued by accepted HELLO_ACK.
        self.drain()
        self.controller.configure(Config(edge=edge, level_code=level), self.now)
        self.send()
        self.drain()
        self.controller.arm(self.now)
        self.send()
        self.drain()
        case.assertIs(self.controller.snapshot().phase, Phase.ARMED)
        case.assertEqual(self.controller.snapshot().generation, 4)

    def accept_frame(self, frame):
        events = self.controller.receive(frame, self.now)
        self.events.extend(events)
        self.frames.append(frame)
        for event in events:
            if event.kind is EventKind.WINDOW_BEGIN:
                self.context = context_for(self.controller)
                self.receiver = WindowReceiver(self.context)
            if event.kind in (EventKind.WINDOW_BEGIN, EventKind.WINDOW_DATA,
                              EventKind.WINDOW_END_ADMITTED, EventKind.WINDOW_ABORT) and self.receiver:
                outcome = self.receiver.feed(frame, self.now)
                if outcome is not None:
                    self.results.append((self.context, outcome))
        return events

    def ingest(self, raw):
        decoded = self.decoder.feed(raw, self.now)
        self.case.assertEqual(decoded.diagnostics, ())
        for frame in decoded.frames:
            self.accept_frame(frame)
        return decoded.frames

    def send(self, count=None):
        ticket = self.controller.prepare_tx()
        self.case.assertIsNotNone(ticket)
        if ticket.offset == 0:
            self.commands.append(ticket.frame)
        remaining = ticket.encoded[ticket.offset:]
        raw = remaining if count is None else remaining[:count]
        result = self.device.write(raw, self.now)
        self.case.assertEqual(result.accepted_prefix, len(raw))
        self.case.assertEqual(result.diagnostics, ())
        events = self.controller.written(ticket, result.accepted_prefix, self.now)
        self.case.assertEqual(events, ())
        return ticket

    def drain(self, chunk=97):
        start = len(self.frames)
        for _ in range(10000):
            raw = self.device.read(chunk, self.now)
            if not raw:
                return self.frames[start:]
            self.ingest(raw)
        self.case.fail('bounded device drain did not finish')

    def one(self):
        start = len(self.frames)
        for _ in range(544):
            raw = self.device.read(1, self.now)
            self.case.assertTrue(raw, 'expected complete device frame')
            self.ingest(raw)
            if len(self.frames) != start:
                return self.frames[-1]
        self.case.fail('frame exceeded maximum encoded size')

    def until(self, kind):
        for _ in range(32):
            frame = self.one()
            if frame.message_type == kind:
                return frame
        self.case.fail('expected frame kind not reached')

    def completed(self, chunk=97):
        self.case.assertTrue(self.device.trigger(self.now))
        self.drain(chunk)
        self.case.assertIs(self.controller.snapshot().phase, Phase.ARMED)
        context, result = self.results[-1]
        self.case.assertTrue(result.semantic_checks_complete)
        return context, result, WindowReceipt.from_window(context, result)

    def release(self, receipt):
        result = self.controller.release(receipt, self.now)
        self.case.assertEqual(result, ())
        self.send()
        return self.drain()

    def inject(self, kind, payload, *, request=0, sequence=None, session=None):
        snapshot = self.controller.snapshot()
        sequence = snapshot.next_rx_sequence if sequence is None else sequence
        session = snapshot.session if session is None else session
        raw = ref.packet(kind, session, sequence, request, payload)
        # A separate decoder allows intentionally old/foreign frames without
        # confusing the emulator's still-pending output used in other scenarios.
        decoded = StreamDecoder().feed(raw, self.now)
        self.case.assertEqual(decoded.diagnostics, ())
        self.case.assertEqual(len(decoded.frames), 1)
        return self.accept_frame(decoded.frames[0])


class Checks(unittest.TestCase):
    def link(self, **kwargs):
        return Link(self, **kwargs)

    def invalid_unchanged(self, controller, callable_, code=None):
        before = controller.snapshot()
        ticket = controller.prepare_tx()
        before = controller.snapshot()
        with self.assertRaises(SessionError) as caught:
            callable_()
        if code is not None:
            self.assertIs(caught.exception.code, code)
        self.assertEqual(controller.snapshot(), before)
        self.assertIs(controller.prepare_tx(), ticket)

    def recovery(self, controller, events, reason):
        self.assertIn(EventKind.RECOVERY, kinds(events))
        event = next(item for item in events if item.kind is EventKind.RECOVERY)
        self.assertIs(event.reason, reason)
        self.assertTrue(event.preserve_fragments)
        self.assertTrue(event.reset_transport)
        self.assertIs(controller.snapshot().phase, Phase.RECOVERY)
        self.assertIsNone(controller.prepare_tx())
        self.assertEqual(controller.tick(controller.snapshot().now_ms), ())

    def stream_result(self, variant='valid', *, generation=4, window=1,
                      edge=0, level=2048, session=ref.NONCE):
        mode, source = (0, 1) if variant == 'deferred' else (1, 0)
        context = ExpectedContext(session, generation, 1, mode, source, edge, level, 0)
        receiver, decoder = WindowReceiver(context), StreamDecoder()
        values = ref.samples(generation, window, edge, level)
        quality = 0 if variant == 'metadata' else 2
        begin = ref.begin(generation, window, edge=edge, level=level, quality=quality, mode=mode, source=source)
        payloads = [(144, begin)]
        blocks = [(145, ref.data(values[i:i + 250], i, generation, window)) for i in range(0, 2048, 250)]
        if variant == 'missing':
            blocks = blocks[:-1]
        if variant == 'reordered':
            blocks[0], blocks[1] = blocks[1], blocks[0]
        if variant == 'conflicting':
            conflict = list(values[:250])
            conflict[9] ^= 1
            blocks.insert(1, (145, ref.data(tuple(conflict), 0, generation, window)))
        if variant == 'altered_crc':
            altered = list(values[:250])
            altered[9] ^= 1
            blocks[0] = (145, ref.data(tuple(altered), 0, generation, window))
        if variant == 'end_only':
            blocks = []
        if variant == 'duplicate':
            blocks.insert(1, blocks[0])
        payloads.extend(blocks)
        payloads.append((146, ref.end(values, generation, window)))
        outcome = None
        for sequence, (kind, payload) in enumerate(payloads, 1):
            raw = ref.packet(kind, session, sequence, 0, payload)
            for offset in range(0, len(raw), 31):
                decoded = decoder.feed(raw[offset:offset + 31], 0)
                self.assertEqual(decoded.diagnostics, ())
                for frame in decoded.frames:
                    candidate = receiver.feed(frame, 0)
                    if candidate is not None:
                        self.assertIsNone(outcome)
                        outcome = candidate
        self.assertIsNotNone(outcome)
        return context, outcome


class ByteCycles(Checks):
    def test_twenty_real_byte_windows_one_arm_same_generation(self):
        link = self.link()
        golden = json.loads((FIXTURES / 'golden-vectors.json').read_text())
        self.assertEqual(golden['windows'], ref.vectors())
        self.assertEqual(ref.crc32(b'123456789'), 0xCBF43926)
        for row in golden['windows']:
            context, outcome, receipt = link.completed(chunk=(1, 97, 4096)[row['window'] % 3])
            self.assertEqual(outcome.samples, ref.samples(4, row['window']))
            self.assertEqual(ref.crc32(ref.sample_bytes(outcome.samples)), row['samples_crc32'])
            self.assertEqual(outcome.samples_crc32, row['samples_crc32'])
            self.assertEqual((receipt.session, receipt.generation, receipt.window_id,
                              receipt.sample_count, receipt.samples_crc32),
                             (ref.NONCE, 4, row['window'], 2048, row['samples_crc32']))
            old_events = len(link.events)
            link.controller.release(receipt, link.now)
            ticket = link.controller.prepare_tx()
            self.assertEqual(ticket.frame.payload.hex(), row['release_payload_hex'])
            self.assertEqual(ticket.encoded.hex(), row['release_packet_hex'])
            self.assertTrue(link.controller.snapshot().window_barrier)
            self.assertFalse(link.device.trigger(link.now))
            link.send()
            self.assertTrue(link.controller.snapshot().window_barrier)
            self.assertFalse(link.device.trigger(link.now))
            link.until(135)
            released = link.events[old_events:]
            self.assertEqual(kinds(released).count(EventKind.WINDOW_RELEASED), 1)
            self.assertNotIn(EventKind.WINDOW_END_ADMITTED, kinds(released))
            self.assertNotIn(EventKind.ARMED, kinds(released))
            event = next(item for item in released if item.kind is EventKind.WINDOW_RELEASED)
            self.assertEqual((event.generation, event.window_id), (4, row['window']))
            self.assertEqual(event.frame.payload.hex(), row['release_ack_payload_hex'])
            self.assertFalse(link.controller.snapshot().window_barrier)
            link.drain()
            self.assertIs(link.controller.snapshot().phase, Phase.ARMED)
            self.assertEqual(link.controller.snapshot().generation, 4)
        self.assertEqual([f.message_type for f in link.commands].count(4), 1)
        self.assertEqual([f.message_type for f in link.commands].count(7), 20)
        self.assertEqual(len(link.results), 20)

    def test_falling_edge_low_and_high_thresholds(self):
        for level in (1, 4094):
            with self.subTest(level=level):
                link = self.link(edge=1, level=level)
                _, result, receipt = link.completed()
                self.assertEqual(result.samples, ref.samples(4, 1, 1, level))
                self.assertEqual(result.samples[0], level - 1)
                link.release(receipt)
                self.assertTrue(link.device.trigger(link.now))

    def test_admitted_end_without_data_is_not_a_receipt(self):
        link = self.link()
        link.inject(144, ref.begin())
        link.inject(146, ref.end())
        self.assertIn(EventKind.WINDOW_END_ADMITTED, kinds(link.events))
        context, outcome = link.results[-1]
        self.assertFalse(outcome.complete)
        self.assertIs(outcome.reason, WindowCode.COUNT)
        before = link.controller.snapshot()
        with self.assertRaises(SessionError):
            WindowReceipt.from_window(context, outcome)
        direct = WindowReceipt(ref.NONCE, 4, 1, 2048, ref.crc32(ref.sample_bytes(ref.samples())))
        self.invalid_unchanged(link.controller, lambda: link.controller.release(direct, 0))
        self.assertEqual(link.controller.snapshot(), before)

    def test_next_window_before_ack_fails_closed(self):
        for state in ('unreleased', 'queued', 'sent'):
            with self.subTest(state=state):
                link = self.link()
                _, _, receipt = link.completed()
                if state != 'unreleased':
                    link.controller.release(receipt, 0)
                if state == 'sent':
                    link.send()
                terminal = link.controller.snapshot().terminal
                events = link.inject(144, ref.begin(window=2))
                self.recovery(link.controller, events, RecoveryReason.WINDOW)
                self.assertEqual(link.controller.snapshot().terminal, terminal)


class ReceiptFactory(Checks):
    def test_actual_receiver_negative_streams_refuse_factory(self):
        expected = {'missing': WindowCode.COUNT, 'reordered': WindowCode.ORDER,
                    'conflicting': WindowCode.OVERLAP, 'altered_crc': WindowCode.CRC,
                    'end_only': WindowCode.COUNT, 'metadata': WindowCode.COMPLETE,
                    'deferred': WindowCode.COMPLETE}
        for variant, reason in expected.items():
            with self.subTest(variant=variant):
                context, outcome = self.stream_result(variant)
                self.assertIs(outcome.reason, reason)
                if variant == 'metadata':
                    self.assertEqual(outcome.metadata_issues, ('RAIL_FLAG_MISMATCH',))
                if variant == 'deferred':
                    self.assertEqual(outcome.deferred_checks, ('AUTO_EXTERNAL_EDGE_ID',))
                link = self.link()
                _, _, good = link.completed()
                before = link.controller.snapshot()
                with self.assertRaises(SessionError):
                    WindowReceipt.from_window(context, outcome)
                self.assertEqual(link.controller.snapshot(), before)
                self.assertIsNone(link.controller.prepare_tx())
                self.assertEqual(good.window_id, 1)

    def test_exact_duplicate_data_is_valid_without_double_count(self):
        context, outcome = self.stream_result('duplicate')
        receipt = WindowReceipt.from_window(context, outcome)
        self.assertEqual(len(outcome.samples), 2048)
        self.assertEqual(len(outcome.fragments), 9)
        self.assertEqual(receipt.samples_crc32, ref.crc32(ref.sample_bytes(ref.samples())))

    def test_factory_exact_types_and_frozen_receipt(self):
        context, outcome = self.stream_result()
        for left, right in ((object(), outcome), (context, object()), (None, outcome), (context, None)):
            with self.subTest(left=type(left), right=type(right)):
                with self.assertRaises(SessionError) as caught:
                    WindowReceipt.from_window(left, right)
                self.assertIs(caught.exception.code, ErrorCode.ARGUMENT)
        receipt = WindowReceipt.from_window(context, outcome)
        with self.assertRaises(FrozenInstanceError):
            receipt.window_id = 5
        self.assertFalse(hasattr(receipt, '__dict__'))

    def test_factory_checks_begin_context_and_provenance_trust_boundary(self):
        context, outcome = self.stream_result()
        for changes in ({'generation': 5}, {'config_id': 2}, {'edge': 1},
                        {'level_code': 2047}, {'pretrigger_samples': 512}):
            with self.subTest(changes=changes):
                with self.assertRaises(SessionError):
                    WindowReceipt.from_window(replace(context, **changes), outcome)
        # WindowResult deliberately has no session: changing this trusted caller
        # input cannot be detected by factory, but current controller must reject.
        receipt = WindowReceipt.from_window(replace(context, session=ref.NONCE + 1), outcome)
        link = self.link()
        link.completed()
        self.invalid_unchanged(link.controller, lambda: link.controller.release(receipt, 0))

    def test_factory_revalidates_forged_result_fields(self):
        context, result = self.stream_result()
        variants = (
            {'complete': 1}, {'reason': 'COMPLETE'}, {'begin': None},
            {'missing_ranges': ((0, 1),)}, {'samples': result.samples[:-1]},
            {'samples': (True,) + result.samples[1:]}, {'samples': (4096,) + result.samples[1:]},
            {'samples_crc32': result.samples_crc32 ^ 1}, {'abort_reason': 1},
            {'deferred_checks': ('unknown',)}, {'metadata_issues': ('unknown',)},
            {'rail_observed': False}, {'rail_observed': 1},
            {'fragments': result.fragments[:-1]},
            {'fragments': (replace(result.fragments[0], offset=1),) + result.fragments[1:]},
            {'fragments': (replace(result.fragments[0], samples=(7,) + result.fragments[0].samples[1:]),) + result.fragments[1:]},
            {'begin': replace(result.begin, sample_count=2047)},
            {'begin': replace(result.begin, quality_flags=0)},
            {'begin': replace(result.begin, timer_arr=20)},
            {'begin': replace(result.begin, window_id=0xFFFFFFFF)},
        )
        for change in variants:
            with self.subTest(field=next(iter(change))):
                with self.assertRaises(SessionError):
                    WindowReceipt.from_window(context, replace(result, **change))

    def test_bounds_before_untrusted_iteration_or_copying(self):
        context, result = self.stream_result()
        class Bomb:
            def __iter__(self):
                raise AssertionError('untrusted iterable was visited')
            def __len__(self):
                raise AssertionError('untrusted length was visited')
        for changes in ({'samples': Bomb()}, {'fragments': Bomb()},
                        {'missing_ranges': Bomb()}, {'metadata_issues': Bomb()},
                        {'deferred_checks': Bomb()}, {'samples': (0,) * 2049},
                        {'fragments': (result.fragments[0],) * 2049},
                        {'fragments': (SampleBlock(0, (0,) * 2049),)},
                        {'begin': replace(result.begin, sample_count=1 << 200)}):
            with self.subTest(field=next(iter(changes))):
                with self.assertRaises(SessionError):
                    WindowReceipt.from_window(context, replace(result, **changes))
        forged = replace(context)
        object.__setattr__(forged, 'generation', 1 << 200)
        with self.assertRaises(SessionError):
            WindowReceipt.from_window(forged, result)

    def test_direct_and_replaced_receipts_do_not_authorize(self):
        link = self.link()
        _, _, receipt = link.completed()
        direct = WindowReceipt(receipt.session, receipt.generation, receipt.window_id,
                               receipt.sample_count, receipt.samples_crc32)
        self.invalid_unchanged(link.controller, lambda: link.controller.release(direct, 0))
        for field in ('session', 'generation', 'window_id', 'samples_crc32'):
            with self.subTest(field=field):
                changed = replace(receipt, **{field: getattr(receipt, field) + 1})
                self.invalid_unchanged(link.controller, lambda: link.controller.release(changed, 0))
        self.invalid_unchanged(link.controller, lambda: link.controller.release(None, 0), ErrorCode.ARGUMENT)
        forged = replace(receipt)
        object.__setattr__(forged, 'sample_count', 1 << 200)
        self.invalid_unchanged(link.controller, lambda: link.controller.release(forged, 0))

    def test_real_receipts_with_wrong_ids_count_or_crc_cannot_match_end(self):
        link = self.link()
        link.completed()
        for kwargs in ({'generation': 5}, {'window': 2}, {'session': ref.NONCE + 1},
                       {'edge': 1}, {'level': 2047}):
            with self.subTest(kwargs=kwargs):
                context, result = self.stream_result(**kwargs)
                receipt = WindowReceipt.from_window(context, result)
                self.invalid_unchanged(link.controller, lambda: link.controller.release(receipt, 0))


class WritesAndRecovery(Checks):
    def pending(self, *, send=False):
        link = self.link()
        context, result, receipt = link.completed()
        link.controller.release(receipt, 0)
        if send:
            link.send()
        return link, receipt

    def test_request_reserved_on_queue_sequence_commits_first_positive(self):
        link = self.link()
        _, _, receipt = link.completed()
        before = link.controller.snapshot()
        link.controller.release(receipt, 0)
        queued = link.controller.snapshot()
        self.assertEqual(queued.next_request_id, before.next_request_id + 1)
        self.assertEqual(queued.next_tx_sequence, before.next_tx_sequence)
        ticket = link.controller.prepare_tx()
        self.assertEqual(link.controller.written(ticket, 0, 10), ())
        self.assertIs(link.controller.prepare_tx(), ticket)
        self.assertEqual(link.controller.snapshot().next_tx_sequence, before.next_tx_sequence)
        self.assertEqual(link.controller.tick(10000), ())
        link.now = 10000
        link.send(1)
        self.assertEqual(link.controller.snapshot().next_tx_sequence, before.next_tx_sequence + 1)
        next_ticket = link.controller.prepare_tx()
        self.assertEqual(next_ticket.offset, 1)
        self.invalid_unchanged(link.controller, lambda: link.controller.written(ticket, 1, 10000), ErrorCode.STALE_TICKET)
        self.invalid_unchanged(link.controller, lambda: link.controller.written(replace(next_ticket), 1, 10000), ErrorCode.STALE_TICKET)
        self.assertEqual(link.controller.tick(20000), ())
        # Simulate truthful final-byte completion without relying on the peer's
        # provisional 5000 ms hold timeout in this controller-only deadline test.
        link.controller.written(next_ticket, len(next_ticket.encoded) - 1, 20000)
        self.assertEqual(link.controller.snapshot().next_tx_sequence, before.next_tx_sequence + 1)
        self.assertEqual(link.controller.tick(20999), ())
        self.recovery(link.controller, link.controller.tick(21000), RecoveryReason.TIMEOUT)

    def test_ack_at_999_or_1000_after_final_byte(self):
        for delta in (999, 1000):
            with self.subTest(delta=delta):
                link, _ = self.pending(send=True)
                request = link.controller.snapshot().pending_request_id
                link.now = delta
                events = link.inject(135, ref.ack(4, 1), request=request)
                if delta == 999:
                    self.assertEqual(kinds(events), [EventKind.WINDOW_RELEASED])
                else:
                    self.recovery(link.controller, events, RecoveryReason.TIMEOUT)

    def test_unknown_write_disconnect_fault_do_not_retry_or_erase_terminal(self):
        for mode in ('unknown_write', 'transport_lost', 'protocol_fault'):
            for accepted in (0, 1, None):
                with self.subTest(mode=mode, accepted=accepted):
                    link, receipt = self.pending()
                    if accepted != 0:
                        link.send(accepted)
                    terminal = link.controller.snapshot().terminal
                    if mode == 'protocol_fault':
                        events = link.controller.protocol_fault(FaultCode.DOWNSTREAM, 0)
                        reason = RecoveryReason.PROTOCOL_FAULT
                    else:
                        events = getattr(link.controller, mode)(0)
                        reason = RecoveryReason.UNKNOWN_WRITE if mode == 'unknown_write' else RecoveryReason.TRANSPORT_LOST
                    self.recovery(link.controller, events, reason)
                    self.assertEqual(link.controller.snapshot().terminal, terminal)
                    self.invalid_unchanged(link.controller, lambda: link.controller.release(receipt, 0))

    def test_valid_release_error_enters_explicit_recovery(self):
        link, _ = self.pending(send=True)
        request = link.controller.snapshot().pending_request_id
        terminal = link.controller.snapshot().terminal
        events = link.inject(255, struct.pack('<BBHI', 7, 7, 0, 4), request=request)
        self.assertEqual(kinds(events), [EventKind.COMMAND_ERROR, EventKind.RECOVERY])
        self.recovery(link.controller, events, RecoveryReason.DEVICE_ERROR)
        self.assertEqual(link.controller.snapshot().terminal, terminal)

    def test_invalid_arguments_leave_pending_and_clock_unchanged(self):
        link, receipt = self.pending()
        c = link.controller
        ticket = c.prepare_tx()
        for count in (-1, True, 1.5, 545, len(ticket.encoded) + 1):
            self.invalid_unchanged(c, lambda: c.written(ticket, count, 2000))
        for now in (-1, True, 1.5, 1 << 64):
            self.invalid_unchanged(c, lambda: c.release(receipt, now))
        self.invalid_unchanged(c, lambda: c.release(receipt, 2000), ErrorCode.STATE)
        self.assertEqual(c.snapshot().now_ms, 0)
        self.invalid_unchanged(c, lambda: c.arm(2000), ErrorCode.STATE)
        self.invalid_unchanged(c, lambda: c.configure(Config(), 2000), ErrorCode.STATE)

    def test_foreign_ack_cannot_advance_deadline_or_mutate_pending(self):
        link, _ = self.pending(send=True)
        before = link.controller.snapshot()
        link.now = 1000
        events = link.inject(135, ref.ack(4, 1), request=before.pending_request_id, session=ref.NONCE + 1)
        self.assertEqual(events, ())
        self.assertEqual(link.controller.snapshot(), before)
        self.recovery(link.controller, link.controller.tick(1000), RecoveryReason.TIMEOUT)

    def test_incomplete_fragments_survive_disconnect_without_erasing_prior_terminal(self):
        link = self.link()
        _, _, receipt = link.completed()
        link.release(receipt)
        prior = link.controller.snapshot().terminal
        self.assertTrue(link.device.trigger(0))
        link.until(145)
        incomplete = link.receiver.disconnect(0)
        self.assertIs(incomplete.reason, WindowCode.DISCONNECT)
        self.assertIsNone(incomplete.samples)
        self.assertEqual(incomplete.fragments[0].samples, ref.samples(4, 2)[:250])
        self.assertEqual(incomplete.missing_ranges, ((250, 2048),))
        events = link.controller.transport_lost(0)
        self.recovery(link.controller, events, RecoveryReason.TRANSPORT_LOST)
        self.assertEqual(link.controller.snapshot().terminal, prior)
        self.assertEqual(incomplete.fragments[0].samples, ref.samples(4, 2)[:250])
        with self.assertRaises(SessionError):
            WindowReceipt.from_window(link.context, incomplete)

    def test_fresh_hello_requires_explicit_external_reset_and_new_context(self):
        link, receipt = self.pending()
        link.send(1)
        link.controller.unknown_write(0)
        self.invalid_unchanged(link.controller, lambda: link.controller.start(ref.NONCE, 0))
        # Reset all external components before invoking the controller contract.
        link.device.disconnect(0)
        link.decoder = StreamDecoder()
        link.receiver = None
        link.controller.start(ref.NONCE + 1, 0)
        link.send()
        link.drain()
        link.send()
        link.drain()
        self.assertIs(link.controller.snapshot().phase, Phase.READY)
        self.assertIsNone(link.controller.snapshot().terminal)
        self.invalid_unchanged(link.controller, lambda: link.controller.release(receipt, 0))
        self.assertEqual(link.device.snapshot().session, ref.NONCE + 1)


class Replies(Checks):
    def pending(self):
        link = self.link()
        _, _, receipt = link.completed()
        link.controller.release(receipt, 0)
        link.send()
        return link, link.controller.snapshot().pending_request_id

    def test_wrong_ack_ids_lengths_and_types_fail_closed(self):
        cases = [('generation', 135, ref.ack(5, 1), 0),
                 ('window', 135, ref.ack(4, 2), 0),
                 ('short', 135, ref.ack(4, 1)[:-1], 0),
                 ('long', 135, ref.ack(4, 1) + b'\0', 0),
                 ('request', 135, ref.ack(4, 1), 1),
                 ('zero request', 135, ref.ack(4, 1), -5),
                 ('wrong kind', 132, ref.ack(4, 1), 0),
                 ('error reserved', 255, struct.pack('<BBHI', 7, 7, 1, 4), 0),
                 ('error rejected', 255, struct.pack('<BBHI', 4, 7, 0, 4), 0)]
        for name, kind, payload, delta in cases:
            with self.subTest(case=name):
                link, request = self.pending()
                events = link.inject(kind, payload, request=request + delta)
                self.assertIn(EventKind.RECOVERY, kinds(events))
                self.assertNotIn(EventKind.WINDOW_RELEASED, kinds(events))
                self.assertTrue(link.controller.snapshot().window_barrier)
                self.assertIsNone(link.controller.prepare_tx())

    def test_ack_before_final_write_is_not_accepted(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                link = self.link()
                _, _, receipt = link.completed()
                link.controller.release(receipt, 0)
                if partial:
                    link.send(1)
                request = link.controller.snapshot().pending_request_id
                events = link.inject(135, ref.ack(4, 1), request=request)
                self.recovery(link.controller, events, RecoveryReason.REPLY)

    def test_immediate_exact_ack_replay_is_ignored_conflict_is_recovery(self):
        link, request = self.pending()
        events = link.inject(135, ref.ack(4, 1), request=request)
        self.assertEqual(kinds(events), [EventKind.WINDOW_RELEASED])
        ack = link.frames[-1]
        self.assertEqual(link.controller.receive(ack, 0), ())
        conflict = replace(ack, payload=ref.ack(4, 2))
        self.recovery(link.controller, link.controller.receive(conflict, 0), RecoveryReason.SEQUENCE)

    def test_new_sequence_duplicate_or_old_ack_is_rejected(self):
        for old_sequence in (False, True):
            with self.subTest(old_sequence=old_sequence):
                link, request = self.pending()
                link.inject(135, ref.ack(4, 1), request=request)
                old_ack = link.frames[-1]
                if old_sequence:
                    link.inject(134, struct.pack('<BBHIIIII', 2, 0, 0, 4, 1, 0, 0, 0))
                    events = link.controller.receive(old_ack, 0)
                    self.recovery(link.controller, events, RecoveryReason.SEQUENCE)
                else:
                    events = link.inject(135, ref.ack(4, 1), request=request)
                    self.recovery(link.controller, events, RecoveryReason.REPLY)

    def test_duplicate_receipt_cannot_clear_active_new_window(self):
        link = self.link()
        _, _, old = link.completed()
        link.release(old)
        self.assertTrue(link.device.trigger(0))
        link.until(144)
        self.invalid_unchanged(link.controller, lambda: link.controller.release(old, 0))
        link.drain()
        _, result = link.results[-1]
        self.assertEqual(result.begin.window_id, 2)
        self.invalid_unchanged(link.controller, lambda: link.controller.release(old, 0))

    def test_duplicate_receipt_after_ack_is_refused_without_rearm_or_history(self):
        link = self.link()
        _, _, receipt = link.completed()
        link.release(receipt)
        initial_generation = link.controller.snapshot().generation
        before = len(link.events)
        self.invalid_unchanged(link.controller, lambda: link.controller.release(receipt, 0), ErrorCode.STATE)
        self.assertEqual(link.events[before:], [])
        self.assertEqual(link.controller.snapshot().generation, initial_generation)
        self.assertTrue(link.device.trigger(0))
        self.assertFalse(link.device.trigger(0))


class StopRaces(Checks):
    def test_stop_before_release_admission_then_late_release_stays_stopped(self):
        link = self.link()
        _, _, receipt = link.completed()
        link.controller.stop(0)
        self.invalid_unchanged(link.controller, lambda: link.controller.release(receipt, 0), ErrorCode.STATE)
        link.send()
        link.drain()
        self.assertIs(link.controller.snapshot().phase, Phase.READY)
        before = len(link.events)
        link.release(receipt)
        self.assertEqual(kinds(link.events[before:]).count(EventKind.WINDOW_RELEASED), 1)
        self.assertNotIn(EventKind.ARMED, kinds(link.events[before:]))
        self.assertIs(link.controller.snapshot().phase, Phase.READY)
        self.assertFalse(link.device.trigger(0))

    def test_stop_queued_partial_and_final_release_preserve_reply_order(self):
        for accepted in (0, 1, 'all'):
            with self.subTest(accepted=accepted):
                link = self.link()
                _, _, receipt = link.completed()
                link.controller.release(receipt, 0)
                if accepted:
                    link.send(None if accepted == 'all' else accepted)
                link.controller.stop(0)
                stop_id = link.controller.snapshot().pending_stop_id
                self.invalid_unchanged(link.controller, lambda: link.controller.release(receipt, 0))
                if accepted != 'all':
                    self.assertEqual(link.controller.prepare_tx().frame.message_type, 7)
                    link.send()
                self.assertEqual(link.controller.prepare_tx().frame.message_type, 5)
                link.send()
                frames = link.drain()
                replies = [frame for frame in frames if frame.message_type in (135, 133)]
                self.assertEqual([frame.message_type for frame in replies], [135, 133])
                self.assertEqual(replies[1].request_id, stop_id)
                self.assertIs(link.controller.snapshot().phase, Phase.READY)
                self.assertFalse(link.controller.snapshot().window_barrier)
                self.assertFalse(link.device.trigger(0))

    def test_release_ack_during_pending_stop_allows_lawful_pre_stop_data(self):
        link = self.link()
        _, _, receipt = link.completed()
        link.controller.release(receipt, 0)
        link.send()
        link.controller.stop(0)  # Stop not yet sent to the peer.
        link.until(135)
        self.assertTrue(link.controller.snapshot().stop_barrier)
        self.assertFalse(link.controller.snapshot().window_barrier)
        self.assertIs(link.controller.snapshot().phase, Phase.ARMED)
        self.invalid_unchanged(link.controller, lambda: link.controller.release(receipt, 0))
        self.assertTrue(link.device.trigger(0))
        link.until(144)
        self.assertIs(link.controller.snapshot().phase, Phase.ARMED)
        self.assertEqual(link.controller.snapshot().window_id, 2)
        link.send()  # Stop reaches peer after legitimate BEGIN.
        frames = link.drain()
        self.assertIn(147, [frame.message_type for frame in frames])
        self.assertIn(133, [frame.message_type for frame in frames])
        self.assertIs(link.controller.snapshot().phase, Phase.READY)
        self.assertEqual(link.results[-1][1].reason, WindowCode.ABORT)
        self.assertFalse(link.device.trigger(0))
        events = link.inject(144, ref.begin(window=3))
        self.recovery(link.controller, events, RecoveryReason.WINDOW)

    def test_stop_after_release_ack_prevents_rearm_and_rejects_old_data(self):
        link = self.link()
        _, _, receipt = link.completed()
        link.release(receipt)
        link.controller.stop(0)
        self.invalid_unchanged(link.controller, lambda: link.controller.release(receipt, 0))
        link.send()
        link.drain()
        self.assertFalse(link.device.trigger(0))
        self.assertIs(link.controller.snapshot().phase, Phase.READY)
        events = link.inject(145, ref.data(ref.samples()[:1]))
        self.recovery(link.controller, events, RecoveryReason.WINDOW)

    def test_stop_ack_cannot_overtake_unanswered_release(self):
        link = self.link()
        _, _, receipt = link.completed()
        link.controller.release(receipt, 0)
        link.controller.stop(0)
        link.send()
        link.send()
        events = link.inject(133, struct.pack('<IIB3x', 4, 1, 0),
                             request=link.controller.snapshot().pending_stop_id)
        self.recovery(link.controller, events, RecoveryReason.REPLY)

    def test_no_new_ordinary_request_during_stop_and_earlier_caps_processed(self):
        c, d, decoder = SessionController(), DeviceEmulator(), StreamDecoder()
        c.start(ref.NONCE, 0)
        def send():
            ticket = c.prepare_tx()
            result = d.write(ticket.encoded[ticket.offset:], 0)
            self.assertEqual(result.accepted_prefix, len(ticket.encoded) - ticket.offset)
            c.written(ticket, result.accepted_prefix, 0)
        def read():
            events = []
            for _ in range(50):
                raw = d.read(4096, 0)
                if not raw:
                    return events
                result = decoder.feed(raw, 0)
                self.assertEqual(result.diagnostics, ())
                for frame in result.frames:
                    events.extend(c.receive(frame, 0))
            self.fail('bounded read did not finish')
        send()
        read()
        c.stop(0)
        send()  # Earlier GET_CAPS.
        events = read()
        self.assertIn(EventKind.CAPABILITIES, kinds(events))
        self.invalid_unchanged(c, lambda: c.configure(Config(), 0))
        self.invalid_unchanged(c, lambda: c.arm(0))
        send()
        self.assertIn(EventKind.STOPPED, kinds(read()))
        self.assertIs(c.snapshot().phase, Phase.READY)

    def test_prior_release_ack_cancels_only_its_deadline_before_stop_timeout(self):
        link = self.link()
        _, _, receipt = link.completed()
        link.controller.release(receipt, 0)
        release_id = link.controller.snapshot().pending_request_id
        link.send()
        link.now = 500
        link.controller.stop(500)
        link.send()
        link.now = 999
        events = link.inject(135, ref.ack(4, 1), request=release_id)
        self.assertEqual(kinds(events), [EventKind.WINDOW_RELEASED])
        self.assertTrue(link.controller.snapshot().stop_barrier)
        self.assertEqual(link.controller.tick(1499), ())
        self.recovery(link.controller, link.controller.tick(1500), RecoveryReason.TIMEOUT)

    def test_stale_ack_after_completed_stop_cannot_rearm(self):
        link = self.link()
        _, _, receipt = link.completed()
        link.controller.release(receipt, 0)
        request = link.controller.snapshot().pending_request_id
        link.send()
        link.controller.stop(0)
        link.send()
        link.drain()
        self.assertIs(link.controller.snapshot().phase, Phase.READY)
        events = link.inject(135, ref.ack(4, 1), request=request)
        self.recovery(link.controller, events, RecoveryReason.REPLY)
        self.assertNotIn(EventKind.ARMED, kinds(events))


class BoundedState(Checks):
    def test_controller_retains_no_sample_result_or_receipt_history(self):
        link = self.link()
        for window in range(1, 26):
            _, result, receipt = link.completed()
            link.release(receipt)
            self.assertEqual(link.controller.snapshot().terminal.window_id, window)
            self.assertEqual(link.controller.snapshot().pending_request_id, 0)
            self.assertIsNone(link.controller.prepare_tx())
            # Traverse only the controller-owned data graph; do not follow module
            # globals, classes or enum internals through gc (those are shared).
            pending = [getattr(link.controller, slot) for slot in link.controller.__slots__]
            seen = set()
            while pending:
                value = pending.pop()
                if id(value) in seen:
                    continue
                seen.add(id(value))
                self.assertNotIsInstance(value, (WindowResult, SampleBlock, WindowReceipt))
                self.assertNotIsInstance(value, (list, dict, set, bytearray))
                if isinstance(value, (bytes, tuple)):
                    self.assertLessEqual(len(value), 544)
                if type(value) is tuple:
                    pending.extend(value)
                elif type(value).__module__ in ('motortester.session', 'motortester.wire'):
                    pending.extend(getattr(value, slot) for slot in getattr(type(value), '__slots__', ()))
            self.assertLessEqual(len(seen), 80)

    def test_exhaustion_never_wraps_request_or_sequence(self):
        for field in ('_next_request', '_next_tx'):
            with self.subTest(field=field):
                link = self.link()
                _, _, receipt = link.completed()
                # Test-only boundary setup avoids billions of valid exchanges.
                setattr(link.controller, field, 0xFFFFFFFF)
                events = link.controller.release(receipt, 0)
                self.recovery(link.controller, events, RecoveryReason.EXHAUSTED)


if __name__ == '__main__':
    unittest.main()

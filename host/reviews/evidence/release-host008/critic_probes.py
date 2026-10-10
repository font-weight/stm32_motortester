"""Independent Critic-B HOST-008 probes; imports no author/test oracle code.

Use PYTHONPATH pointing at the exact reviewed host checkout. All traffic in
Peer uses the real emulator, decoder, session controller and window receiver.
"""
from dataclasses import fields, replace
import itertools
import struct
import unittest

from motortester.device_emulator import DeviceEmulator
from motortester.session import (Config, ErrorCode, EventKind as K, FaultCode,
                                MAX_ID, Phase, RecoveryReason as R,
                                SessionController, SessionError, WindowReceipt)
from motortester.window import (BeginMetadata, ExpectedContext, SampleBlock,
                               WindowCode, WindowReceiver, WindowResult)
from motortester.wire import Frame, StreamDecoder


def crc(raw):
    value = 0xffffffff
    for b in raw:
        value ^= b
        for _ in range(8):
            value = (value >> 1) ^ ((-(value & 1)) & 0xedb88320)
    return value ^ 0xffffffff


def expected(generation, window, edge=0, level=2048):
    # Published HOST-007 deterministic synthetic waveform, not a measurement.
    values = [(73*i + 19*window + 7*generation) % 4096 for i in range(2048)]
    values[0], values[511], values[1023] = level-edge, 0, 4095
    return tuple(values)


class Peer:
    def __init__(self, case, *, edge=0, level=2048, session=0x7353ef028ea134af):
        self.case = case
        self.c, self.d, self.decoder = SessionController(), DeviceEmulator(), StreamDecoder()
        self.now = 0
        self.context = self.receiver = None
        self.results, self.events, self.frames, self.commands = [], [], [], []
        self.c.start(session, 0)
        self.pump()
        self.c.configure(Config(edge=edge, level_code=level), 0)
        self.pump()
        self.c.arm(0)
        self.pump()
        case.assertIs(self.c.snapshot().phase, Phase.ARMED)

    def send(self, count=None):
        ticket = self.c.prepare_tx()
        self.case.assertIsNotNone(ticket)
        if not ticket.offset:
            self.commands.append(ticket.frame)
        raw = ticket.encoded[ticket.offset:]
        if count is not None:
            raw = raw[:count]
        response = self.d.write(raw, self.now)
        self.case.assertEqual(response.accepted_prefix, len(raw))
        self.case.assertEqual(response.diagnostics, ())
        self.case.assertEqual(self.c.written(ticket, len(raw), self.now), ())
        return ticket

    def accept(self, frame):
        self.frames.append(frame)
        events = self.c.receive(frame, self.now)
        self.events.extend(events)
        for event in events:
            if event.kind is K.WINDOW_BEGIN:
                s = self.c.snapshot()
                self.context = ExpectedContext(s.session, s.generation, s.config_id,
                    s.config.mode, s.config.source, s.config.edge, s.config.level_code,
                    s.config.pretrigger_samples, s.config.profile)
                self.receiver = WindowReceiver(self.context)
            if event.kind in (K.WINDOW_BEGIN, K.WINDOW_DATA, K.WINDOW_END_ADMITTED, K.WINDOW_ABORT) and self.receiver:
                outcome = self.receiver.feed(event.frame, self.now)
                if outcome is not None:
                    self.results.append(outcome)
        return events

    def drain(self, chunks=(1, 2, 7, 63, 257)):
        count = 0
        for size in itertools.cycle(chunks):
            data = self.d.read(size, self.now)
            if not data:
                return
            count += 1
            self.case.assertLess(count, 20000, 'bounded drain')
            decoded = self.decoder.feed(data, self.now)
            self.case.assertEqual(decoded.diagnostics, ())
            for frame in decoded.frames:
                self.accept(frame)

    def pump(self):
        while self.c.prepare_tx() is not None:
            self.send()
            self.drain()

    def window(self):
        self.case.assertTrue(self.d.trigger(self.now))
        self.drain()
        result = self.results[-1]
        self.case.assertIs(result.reason, WindowCode.COMPLETE)
        return WindowReceipt.from_window(self.context, result)

    def ack(self, receipt, *, request=None, sequence=None, session=None):
        s = self.c.snapshot()
        return Frame(135, s.session if session is None else session,
            s.next_rx_sequence if sequence is None else sequence,
            s.pending_request_id if request is None else request,
            struct.pack('<II', receipt.generation, receipt.window_id))


class CriticProbes(unittest.TestCase):
    def rejected(self, callback, controller=None, code=None):
        before = controller.snapshot() if controller else None
        with self.assertRaises(SessionError) as got:
            callback()
        if code is not None:
            self.assertIs(got.exception.code, code)
        if controller:
            self.assertEqual(controller.snapshot(), before)

    def test_20_real_windows_split_stream_same_generation_and_independent_crc(self):
        p = Peer(self, edge=1, level=1)
        generation = p.c.snapshot().generation
        self.assertEqual(crc(b'123456789'), 0xcbf43926)
        for window in range(1, 21):
            receipt = p.window()
            values = expected(generation, window, 1, 1)
            self.assertEqual(p.results[-1].samples, values)
            self.assertEqual(receipt.samples_crc32, crc(struct.pack('<2048H', *values)))
            self.assertEqual(receipt.sample_count, 2048)
            self.assertEqual(receipt.window_id, window)
            p.c.release(receipt, 0)
            ticket = p.c.prepare_tx()
            self.assertEqual(ticket.frame.payload, struct.pack('<III', generation, window, receipt.samples_crc32))
            while p.c.prepare_tx() is not None:
                p.send(1)
                self.assertTrue(p.c.snapshot().window_barrier)
            p.drain()
            self.assertFalse(p.c.snapshot().window_barrier)
            self.assertEqual(p.c.snapshot().generation, generation)
            self.assertIs(p.c.snapshot().phase, Phase.ARMED)
        self.assertEqual(sum(e.kind is K.ARMED for e in p.events), 1)
        self.assertEqual(sum(e.kind is K.WINDOW_RELEASED for e in p.events), 20)
        self.assertEqual(sum(f.message_type == 4 for f in p.commands), 1)
        self.assertEqual(sum(f.message_type == 7 for f in p.commands), 20)

    def test_all_release_split_positions_with_queued_stop_preserve_order(self):
        template = Peer(self)
        receipt = template.window()
        template.c.release(receipt, 0)
        size = len(template.c.prepare_tx().encoded)
        for split in range(size + 1):
            with self.subTest(split=split, encoded_size=size):
                p = Peer(self)
                r = p.window()
                before = p.c.snapshot()
                p.c.release(r, 0)
                ticket = p.c.prepare_tx()
                self.assertEqual(p.c.snapshot().next_request_id, before.next_request_id + 1)
                p.c.written(ticket, 0, 0)
                self.assertIs(p.c.prepare_tx(), ticket)
                self.assertEqual(p.c.snapshot().next_tx_sequence, before.next_tx_sequence)
                if split:
                    p.send(split)
                    self.assertEqual(p.c.snapshot().next_tx_sequence, before.next_tx_sequence + 1)
                    self.rejected(lambda: p.c.written(ticket, 0, 0), p.c, ErrorCode.STALE_TICKET)
                p.c.stop(0)
                self.rejected(lambda: p.c.release(r, 0), p.c, ErrorCode.STATE)
                while p.c.prepare_tx() is not None:
                    p.send()
                p.drain()
                self.assertEqual([f.message_type for f in p.commands][-2:], [7, 5])
                self.assertEqual([e.kind for e in p.events if e.kind in (K.WINDOW_RELEASED, K.STOPPED)],
                                 [K.WINDOW_RELEASED, K.STOPPED])
                self.assertIs(p.c.snapshot().phase, Phase.READY)
                self.assertFalse(p.c.snapshot().window_barrier)
        print('CRITIC_DETAIL=release split cases', size + 1)

    def test_factory_rejects_nonexact_context_metadata_and_sample_types(self):
        p = Peer(self)
        p.window()
        context, result = p.context, p.results[-1]
        class IntSubclass(int): pass
        class TupleSubclass(tuple): pass
        count = 0
        for field in fields(context):
            for value in (True, 1.0, None, IntSubclass(1)):
                corrupted = replace(context)
                object.__setattr__(corrupted, field.name, value)
                self.rejected(lambda: WindowReceipt.from_window(corrupted, result))
                count += 1
        for field in fields(result.begin):
            for value in (True, 1.0, None, IntSubclass(1)):
                corrupted = replace(result.begin, **{field.name: value})
                self.rejected(lambda: WindowReceipt.from_window(context, replace(result, begin=corrupted)))
                count += 1
        for name in ('fragments', 'samples', 'missing_ranges', 'deferred_checks', 'metadata_issues'):
            corrupted = replace(result, **{name: TupleSubclass(getattr(result, name))})
            self.rejected(lambda: WindowReceipt.from_window(context, corrupted))
        for value in (True, 1.0, None, IntSubclass(1)):
            corrupted = replace(result, samples=(value,) + result.samples[1:])
            self.rejected(lambda: WindowReceipt.from_window(context, corrupted))
        print('CRITIC_DETAIL=context/metadata scalar type cases', count)

    def test_factory_missing_attributes_fail_as_session_errors(self):
        p = Peer(self)
        p.window()
        for original in (p.context, p.results[-1], p.results[-1].begin):
            for field in fields(original):
                corrupted = replace(original)
                object.__delattr__(corrupted, field.name)
                if isinstance(original, ExpectedContext):
                    call = lambda: WindowReceipt.from_window(corrupted, p.results[-1])
                elif isinstance(original, WindowResult):
                    call = lambda: WindowReceipt.from_window(p.context, corrupted)
                else:
                    call = lambda: WindowReceipt.from_window(p.context, replace(p.results[-1], begin=corrupted))
                self.rejected(call)

    def test_maximum_fragment_count_and_bad_shape_before_sample_access(self):
        p = Peer(self)
        p.window()
        result = p.results[-1]
        unit = tuple(SampleBlock(i, (v,)) for i,v in enumerate(result.samples))
        rebuilt = replace(result, fragments=unit)
        self.assertEqual(WindowReceipt.from_window(p.context, rebuilt).samples_crc32, result.samples_crc32)
        class Bomb:
            def __eq__(self, value): raise AssertionError('visited sample before complete shape validation')
        invalids = (
            replace(result, samples=(Bomb(),)*2049),
            replace(result, fragments=(SampleBlock(0, (Bomb(),)),)*2049),
            replace(result, fragments=(SampleBlock(0, (Bomb(),)*251),)),
            replace(result, fragments=(SampleBlock(0,(Bomb(),)), SampleBlock(3,(1,)))),
            replace(result, fragments=unit[:-1]),
            replace(result, fragments=unit+(SampleBlock(0,(1,)),)),
        )
        for invalid in invalids:
            self.rejected(lambda: WindowReceipt.from_window(p.context, invalid))

    def test_every_sample_consistent_mutation_with_old_crc_is_rejected(self):
        p = Peer(self)
        p.window()
        result = p.results[-1]
        # Alter both representations so simple tuple equality cannot catch this;
        # the factory must recompute the complete raw-sample CRC.
        for index in range(2048):
            values = list(result.samples)
            values[index] ^= 1
            values = tuple(values)
            blocks = tuple(SampleBlock(i, values[i:i+250]) for i in range(0,2048,250))
            bad = replace(result, samples=values, fragments=blocks)
            self.rejected(lambda: WindowReceipt.from_window(p.context, bad))
        print('CRITIC_DETAIL=all 2048 sample positions independently CRC rejected')

    def test_context_links_complete_results_but_never_authenticates_session(self):
        p = Peer(self)
        valid = p.window()
        # These two legal metadata flags are both admissible. An externally
        # timing-unverified flag is not exclusive to external trigger in draft.
        result = p.results[-1]
        altered = replace(result, begin=replace(result.begin, quality_flags=6))
        self.assertEqual(WindowReceipt.from_window(p.context, altered), valid)
        foreign = WindowReceipt.from_window(replace(p.context, session=p.context.session+1), result)
        self.rejected(lambda: p.c.release(foreign, 1000), p.c)
        self.rejected(lambda: p.c.release(replace(valid), 1000), p.c)
        self.rejected(lambda: p.c.release(WindowReceipt(*valid._values()), 1000), p.c)
        p.c.release(valid, 0)
        self.rejected(lambda: p.c.release(valid, 1000), p.c)
        self.rejected(lambda: p.c.configure(Config(), 1000), p.c)
        self.rejected(lambda: p.c.arm(1000), p.c)

    def test_real_missing_and_corrupt_data_fail_factory_before_release(self):
        p = Peer(self)
        p.window()
        frames = [f for f in p.frames if f.message_type in (144,145,146)]
        corrupted = bytearray(frames[1].payload)
        corrupted[20] ^= 1
        streams = [frames[:1]+frames[2:],
                   frames[:1]+[replace(frames[1], payload=bytes(corrupted))]+frames[2:],
                   [frames[0],frames[-1]],
                   frames[:1]+[frames[2],frames[1]]+frames[3:]]
        for stream in streams:
            receiver = WindowReceiver(p.context)
            result = None
            for frame in stream:
                value = receiver.feed(frame, 0)
                if value is not None:
                    result = value
            self.assertIsNotNone(result)
            self.assertFalse(result.complete)
            self.rejected(lambda: WindowReceipt.from_window(p.context, result))
            self.assertIsNone(p.c.prepare_tx())

    def test_new_request_stale_ack_foreign_ack_and_exact_duplicate(self):
        p = Peer(self)
        first = p.window()
        p.c.release(first, 0)
        p.send()
        ack1 = p.ack(first)
        p.drain()
        second = p.window()
        p.c.release(second, 0)
        p.send()
        old_foreign = replace(ack1, session=ack1.session+1)
        before = p.c.snapshot()
        self.assertEqual(p.c.receive(old_foreign, 100000), ())
        self.assertEqual(p.c.snapshot(), before)
        stale = replace(ack1, sequence=before.next_rx_sequence)
        events = p.c.receive(stale, 0)
        self.assertIs(events[0].kind, K.RECOVERY)
        self.assertIs(events[0].reason, R.REPLY)
        self.assertTrue(p.c.snapshot().window_barrier)
        q = Peer(self)
        receipt = q.window()
        q.c.release(receipt, 0)
        q.send()
        ack = q.ack(receipt)
        self.assertEqual([e.kind for e in q.c.receive(ack,0)], [K.WINDOW_RELEASED])
        self.assertEqual(q.c.receive(ack,0), ())
        self.assertEqual(q.c.receive(replace(ack,sequence=ack.sequence+1),0)[0].kind,K.RECOVERY)

    def test_response_deadline_starts_last_byte_no_retry_after_error(self):
        for delay in (999,1000):
            p = Peer(self)
            r = p.window()
            p.c.release(r,0)
            t = p.c.prepare_tx()
            # Controller-only: emulator's old 5000ms holding policy is outside
            # this test. Do not feed fictitious elapsed traffic to the peer.
            p.c.written(t,1,100)
            self.assertEqual(p.c.tick(10000),())
            t = p.c.prepare_tx()
            p.c.written(t,len(t.encoded)-t.offset,10000)
            events=p.c.receive(p.ack(r),10000+delay)
            self.assertEqual(events[0].kind,K.WINDOW_RELEASED if delay==999 else K.RECOVERY)
            if delay==1000:
                self.assertIs(events[0].reason,R.TIMEOUT)
                self.assertIsNone(p.c.prepare_tx())

    def test_stop_then_late_release_never_rearms_and_old_window_is_barred(self):
        p=Peer(self)
        r=p.window()
        begin=next(f for f in p.frames if f.message_type==144)
        p.c.stop(0)
        self.rejected(lambda:p.c.release(r,0),p.c)
        p.pump()
        p.c.release(r,0)
        p.pump()
        self.assertIs(p.c.snapshot().phase,Phase.READY)
        self.assertFalse(p.d.trigger(0))
        self.assertEqual(sum(e.kind is K.ARMED for e in p.events),1)
        self.rejected(lambda:p.c.release(r,0),p.c)
        stale=replace(begin,sequence=p.c.snapshot().next_rx_sequence)
        self.assertIs(p.c.receive(stale,0)[0].reason,R.WINDOW)

    def test_release_failure_preserves_terminal_and_blocks_old_context(self):
        for mode in ('unknown_write','transport_lost','protocol_fault','error'):
            p=Peer(self)
            r=p.window()
            terminal=p.c.snapshot().terminal
            p.c.release(r,0)
            p.send(None if mode=='error' else 1)
            if mode=='error':
                s=p.c.snapshot()
                events=p.c.receive(Frame(255,s.session,s.next_rx_sequence,s.pending_request_id,
                    struct.pack('<BBHI',7,7,0,s.generation)),0)
            elif mode=='protocol_fault':
                events=p.c.protocol_fault(FaultCode.DOWNSTREAM,0)
            else:
                events=getattr(p.c,mode)(0)
            self.assertIs(p.c.snapshot().phase,Phase.RECOVERY)
            self.assertEqual(p.c.snapshot().terminal,terminal)
            self.assertTrue(events[-1].preserve_fragments)
            self.assertTrue(events[-1].reset_transport)
            self.assertIsNone(p.c.prepare_tx())
            p.c.start(p.context.session+1,0)
            self.rejected(lambda:p.c.release(r,0),p.c)

    def test_release_and_stop_last_allowed_identifiers_no_reserved_emission(self):
        for attr in ('_next_request','_next_tx'):
            p=Peer(self)
            r=p.window()
            setattr(p.c,attr,MAX_ID)
            p.c.release(r,0)
            t=p.c.prepare_tx()
            self.assertLessEqual(t.frame.sequence,MAX_ID)
            self.assertLessEqual(t.frame.request_id,MAX_ID)
            events=p.c.stop(0)
            self.assertIs(events[0].reason,R.EXHAUSTED)
            self.assertIsNone(p.c.prepare_tx())

    def test_multiple_generations_stop_release_arm_window_ids_still_increase(self):
        p=Peer(self)
        receipts=[]
        for window in range(1,11):
            r=p.window()
            self.assertEqual(r.window_id,window)
            receipts.append(r)
            p.c.stop(0)
            p.pump()
            p.c.release(r,0)
            p.pump()
            p.c.arm(0)
            p.pump()
            for stale in receipts:
                self.rejected(lambda:p.c.release(stale,0),p.c)
        self.assertEqual(len({r.generation for r in receipts}),10)


if __name__ == '__main__':
    unittest.main(verbosity=2)

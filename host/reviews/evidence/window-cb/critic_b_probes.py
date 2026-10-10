"""Independent reviewer probes for HOST-004 exact SHA cb271a13 (synthetic only).

Run with PYTHONPATH pointing to the archived candidate's host directory.
No imports from candidate test/fixture helpers; no production writes.
"""
import gc
import random
import tracemalloc
import unittest
from motortester.wire import Frame, StreamDecoder, DiagnosticCode
from motortester.window import ExpectedContext, WindowReceiver, WindowCode, WindowError


def number(value, size):
    return value.to_bytes(size, 'little')


def checksum(raw):
    value = 0xffffffff
    for x in raw:
        value ^= x
        for _ in range(8):
            low = value & 1
            value >>= 1
            if low:
                value ^= 0xedb88320
    return value ^ 0xffffffff


def encode(raw):
    # Deliberately separate block-based COBS writer.
    out = bytearray()
    pending = bytearray()
    for x in raw:
        if x == 0:
            out.append(len(pending) + 1)
            out.extend(pending)
            pending.clear()
        else:
            pending.append(x)
            if len(pending) == 254:
                out.append(255)
                out.extend(pending)
                pending.clear()
    out.append(len(pending) + 1)
    out.extend(pending)
    return bytes(out) + b'\0'


def packed(kind, payload, seq=2, session=71, corrupt_crc=False):
    body = bytes((0, 1, kind, 0)) + number(len(payload), 2) + b'\0\0'
    body += number(session, 8) + number(seq, 4) + b'\0'*4 + payload
    return encode(body + number(checksum(body) ^ int(corrupt_crc), 4))


def ctx(mode=1, source=0, edge=0, pre=512):
    return ExpectedContext(71, 23, 3, mode, source, edge, 2000, pre)


def begin(context, window=9, timeout=False, rail=True):
    kind = 2 if timeout else context.source
    external_id = 0 if kind != 1 else 13
    flags = int(timeout) + 2 * int(rail) + (4 if kind == 1 else 0)
    fields = [(23,4), (window,4), (3,4), (1,2), (2048,2),
              (context.pretrigger_samples,2), (65535 if timeout else context.pretrigger_samples,2),
              (kind,1), (context.source,1), (context.edge,1), (0,1), (2000,2), (flags,2),
              (72000000,4), (71,2), (19,2), (external_id,4)]
    return b''.join(number(v,n) for v,n in fields)


def data(offset, values, window=9):
    return number(23,4)+number(window,4)+number(offset,2)+number(len(values),2)+b''.join(number(v,2) for v in values)


def end(values, window=9, bad_crc=False):
    raw = b''.join(number(v,2) for v in values)
    return number(23,4)+number(window,4)+number(2048,2)+b'\0\0'+number(checksum(raw)^int(bad_crc),4)


class ReviewerProbes(unittest.TestCase):
    def feed(self, receiver, kind, payload, now=0):
        decoded = StreamDecoder().feed(packed(kind,payload),0)
        self.assertEqual(decoded.diagnostics, ())
        self.assertEqual(len(decoded.frames), 1)
        return receiver.feed(decoded.frames[0], now)

    def start(self):
        receiver = WindowReceiver(ctx())
        self.assertIsNone(self.feed(receiver,144,begin(ctx())))
        return receiver

    def test_crc_known_answer(self):
        self.assertEqual(checksum(b'123456789'),0xcbf43926)

    def test_48_mode_source_edge_pretrigger_combinations_random_partitions(self):
        rng = random.Random(4004)
        count = 0
        for mode in range(3):
            for source in range(2):
                for edge in range(2):
                    for pre in (0,512,1024):
                        for timeout in ((False,True) if mode==0 else (False,)):
                            count += 1
                            context=ctx(mode,source,edge,pre)
                            values=(0,4095)+tuple(rng.randrange(4096) for _ in range(2046))
                            payloads=[(144,begin(context,timeout=timeout))]
                            blocks=[]
                            offset=0
                            while offset<2048:
                                n=min(rng.randrange(1,251),2048-offset)
                                block=data(offset,values[offset:offset+n])
                                payloads.append((145,block)); blocks.append(block); offset+=n
                                if rng.randrange(4)==0:
                                    payloads.append((145,rng.choice(blocks)))
                            payloads.append((146,end(values)))
                            wire=b''.join(packed(k,p,seq=i+2) for i,(k,p) in enumerate(payloads))
                            receiver=WindowReceiver(context); decoder=StreamDecoder(); outputs=[]
                            offset=0; clock=0
                            while offset<len(wire):
                                size=rng.randrange(1,1000)
                                batch=decoder.feed(wire[offset:offset+size],clock)
                                self.assertEqual(batch.diagnostics,())
                                for frame in batch.frames:
                                    result=receiver.feed(frame,clock)
                                    if result is not None: outputs.append(result)
                                offset+=size; clock+=1
                            self.assertEqual(len(outputs),1)
                            result=outputs[0]
                            self.assertTrue(result.complete)
                            self.assertEqual(result.samples,values)
                            self.assertEqual(result.samples_crc32,checksum(b''.join(number(v,2) for v in values)))
                            self.assertEqual(len(result.fragments),len(blocks))
                            self.assertEqual(result.missing_ranges,())
                            self.assertEqual(result.semantic_checks_complete,not(mode==0 and source==1 and not timeout))
                            self.assertIsNone(receiver.feed(Frame(146,71,99,0,end(values)),clock))
        self.assertEqual(count,48)

    def test_frame_crc_corruption_never_enters_fragments(self):
        receiver=self.start(); decoder=StreamDecoder()
        self.assertIsNone(self.feed(receiver,145,data(0,(10,20,30)),1))
        batch=decoder.feed(packed(145,data(3,(40,50)),corrupt_crc=True),2)
        self.assertEqual(batch.frames,())
        self.assertEqual(tuple(x.code for x in batch.diagnostics),(DiagnosticCode.CRC,))
        # The dispatcher is outside scope. Do not silently consume diagnostics;
        # on terminal link recovery this seam must preserve the verified prefix.
        result=receiver.disconnect(3)
        self.assertEqual(result.reason,WindowCode.DISCONNECT)
        self.assertIsNone(result.samples)
        self.assertEqual(tuple(x for b in result.fragments for x in b.samples),(10,20,30))
        self.assertEqual(result.missing_ranges,((3,2048),))

    def test_crc_failed_full_window_cannot_later_be_promoted(self):
        receiver=self.start(); values=tuple(i%4096 for i in range(2048))
        for offset in range(0,2048,249):
            self.assertIsNone(self.feed(receiver,145,data(offset,values[offset:offset+249]),1))
        result=self.feed(receiver,146,end(values,bad_crc=True),2)
        self.assertEqual(result.reason,WindowCode.CRC)
        self.assertFalse(result.complete)
        self.assertIsNone(result.samples); self.assertIsNone(result.samples_crc32)
        self.assertEqual(result.missing_ranges,())
        self.assertEqual(tuple(x for b in result.fragments for x in b.samples),values)
        self.assertIsNone(self.feed(receiver,146,end(values),3))
        self.assertIsNone(receiver.reset())
        self.assertFalse(result.complete)

    def test_seeded_random_payloads_are_bounded_explicit_outcomes(self):
        rng=random.Random(742); accepted=(11,22,33)
        for i in range(4000):
            receiver=self.start()
            self.assertIsNone(self.feed(receiver,145,data(0,accepted),1))
            kind=(144,145,146,147)[i%4]
            payload=rng.randbytes(rng.randrange(513))
            result=receiver.feed(Frame(kind,71,3,0,payload),2)
            self.assertIsNotNone(result)
            self.assertFalse(result.complete)
            self.assertIsNone(result.samples)
            self.assertEqual(tuple(x for b in result.fragments for x in b.samples),accepted)
            self.assertEqual(result.missing_ranges,((3,2048),))
            self.assertIsNone(receiver.feed(Frame(147,71,4,0,b''),3))

    def test_actual_singleton_allocation_and_release_are_bounded(self):
        gc.collect(); tracemalloc.start()
        try:
            receiver=self.start()
            for offset in range(2048):
                self.assertIsNone(receiver.feed(Frame(145,71,3,0,data(offset,(offset+1,))),0))
            accepted, peak=tracemalloc.get_traced_memory()
            result=receiver.reset()
            self.assertEqual(len(result.fragments),2048)
            self.assertIsNone(result.samples)
            self.assertEqual(result.missing_ranges,())
            del result; gc.collect()
            released,_=tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        print(f'RESOURCE singleton: accepted={accepted}, peak={peak}, after_release={released}',flush=True)
        self.assertLess(peak,1024*1024)
        self.assertLess(released,128*1024)

    def test_bad_api_after_terminal_does_not_change_time_base(self):
        receiver=self.start(); result=receiver.disconnect(10)
        self.assertFalse(result.complete)
        forged=Frame(145,71,3,0,b'')
        object.__setattr__(forged,'payload',b'x'*(1024*1024))
        with self.assertRaises(WindowError): receiver.feed(forged,999999)
        self.assertIsNone(receiver.tick(11))
        self.assertIsNone(receiver.reset())
        self.assertIsNone(self.feed(receiver,144,begin(ctx()),0))

    def test_2048_received_then_abort_is_still_incomplete(self):
        receiver=self.start(); values=(5,)*2048
        for offset in range(0,2048,250):
            self.assertIsNone(self.feed(receiver,145,data(offset,values[offset:offset+250]),0))
        result=self.feed(receiver,147,number(23,4)+number(9,4)+number(2,2)+b'\0\0',1)
        self.assertEqual(result.reason,WindowCode.ABORT)
        self.assertEqual(result.abort_reason,2)
        self.assertFalse(result.complete)
        self.assertIsNone(result.samples); self.assertIsNone(result.samples_crc32)
        self.assertEqual(result.missing_ranges,())
        self.assertEqual(tuple(x for b in result.fragments for x in b.samples),values)


if __name__=='__main__':
    unittest.main(verbosity=2)

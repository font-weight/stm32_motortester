"""Critic-B-only adversarial probes. No production/test-tree modifications.
Run with PYTHONPATH=<exact archive>/host python3.12 [-O] this_file.py.
Oracle: issue24 and shared b6ae8025; this does not import either author's tests.
"""
import struct
import unittest
from dataclasses import replace
from motortester.session import SessionController, Config, SessionError, Phase, MAX_ID, FaultCode
from motortester.wire import Frame

NONCE=0xabcdef

def hello(bits=63):
    return struct.pack('<BBBBHHII',0,1,1,12,512,2048,123,bits)
def caps(bits=63, edges=3):
    return struct.pack('<BBHIHHIHHBBBBHHH',1,0,1,72000000,71,19,12000000,143,2048,
                       3 if bits&32 else 1,(bits>>2)&7,bits&3,edges,0,512 if bits&32 else 0,1024 if bits&32 else 0)
def kinds(events): return tuple(e.kind.name for e in events)

def send(c,now=0):
    t=c.prepare_tx()
    if t is None: raise AssertionError('Missing outgoing ticket')
    e=c.written(t,len(t.encoded)-t.offset,now)
    if e: raise AssertionError(e)
    return t.frame

def rx(c,kind,payload,request=0,now=0):
    return c.receive(Frame(kind,NONCE,c.snapshot().next_rx_sequence,request,payload),now)

def at(stage,bits=63,edges=3):
    c=SessionController(); c.start(NONCE,0)
    if stage=='HELLO': return c
    send(c); rx(c,129,hello(bits),1)
    if stage=='CAPS': return c
    send(c); rx(c,130,caps(bits,edges),2)
    if stage=='READY': return c
    c.configure(Config(),0)
    if stage=='CONFIG': return c
    f=send(c); rx(c,131,struct.pack('<I',1)+f.payload,f.request_id)
    if stage=='CONFIGURED': return c
    c.arm(0)
    if stage=='ARM': return c
    f=send(c); rx(c,132,struct.pack('<II',f.request_id,1),f.request_id)
    if stage=='ARMED': return c
    c.stop(0)
    if stage=='STOP': return c
    raise ValueError(stage)

def begin(c,window=1,quality=0):
    s=c.snapshot(); q=s.config
    return struct.pack('<IIIHHHHBBBBHHIHHI',s.generation,window,s.config_id,1,2048,
                       q.pretrigger_samples,q.pretrigger_samples,0,0,q.edge,0,q.level_code,quality,72000000,71,19,0)

class IndependentCriticProbes(unittest.TestCase):
    def test_every_command_every_write_split_and_deadline(self):
        counts={}
        for stage in ('HELLO','CAPS','CONFIG','ARM','STOP'):
            n=len(at(stage).prepare_tx().encoded); counts[stage]=n-1
            for split in range(1,n):
                with self.subTest(stage=stage,split=split):
                    c=at(stage); t=c.prepare_tx(); nextseq=c.snapshot().next_tx_sequence
                    self.assertEqual(c.written(t,0,10000),()); self.assertIs(c.prepare_tx(),t)
                    self.assertEqual(c.snapshot().next_tx_sequence,nextseq)
                    self.assertEqual(c.written(t,split,10001),())
                    self.assertEqual(c.snapshot().next_tx_sequence,nextseq+1)
                    p=c.prepare_tx(); self.assertIsNot(p,t); self.assertEqual(p.offset,split)
                    self.assertEqual(c.tick(100000),())
                    self.assertEqual(c.written(p,n-split,100000),())
                    self.assertEqual(c.tick(100999),())
                    events=c.tick(101000)
                    self.assertEqual(kinds(events),('RECOVERY',)); self.assertEqual(events[0].reason.name,'TIMEOUT')
                    self.assertIsNone(c.prepare_tx()); self.assertEqual(c.tick(200000),())
        print('Exhaustive command split cases:',counts,flush=True)

    def test_all_64_capability_masks_and_4_edge_masks(self):
        for bits in range(64):
            for edges in range(4):
                with self.subTest(bits=bits,edges=edges):
                    c=at('READY',bits,edges)
                    self.assertIs(c.snapshot().phase,Phase.READY)
                    before=c.snapshot()
                    if bits&8 and bits&1 and edges&1:
                        self.assertEqual(c.configure(Config(),0),())
                    else:
                        with self.assertRaises(SessionError): c.configure(Config(),5000)
                        self.assertEqual(c.snapshot(),before)

    def test_stop_serializes_behind_caps_and_config(self):
        for stage in ('CAPS','CONFIG'):
            for split in (0,1):
                c=at(stage); t=c.prepare_tx(); c.written(t,split,0); c.stop(0)
                ordinary=send(c); stop=send(c)
                self.assertLess(ordinary.sequence,stop.sequence)
                self.assertEqual(stop.payload,bytes(4))
                reply=caps() if stage=='CAPS' else struct.pack('<I',1)+ordinary.payload
                rx(c,130 if stage=='CAPS' else 131,reply,ordinary.request_id)
                self.assertTrue(c.snapshot().stop_barrier)
                self.assertEqual(kinds(rx(c,133,bytes(12),stop.request_id)),('STOPPED',))
                self.assertFalse(c.snapshot().stop_barrier)
        c=at('CONFIG'); f=send(c); c.stop(0); s=send(c)
        self.assertEqual(kinds(rx(c,255,struct.pack('<BBHI',3,1,0,0),f.request_id)),('COMMAND_ERROR',))
        self.assertIsNone(c.snapshot().config)
        self.assertEqual(kinds(rx(c,133,bytes(12),s.request_id)),('STOPPED',))
        with self.assertRaises(SessionError): c.arm(0)

    def test_pending_arm_rejection_keeps_previous_generation_until_stop_error(self):
        c=at('ARMED'); old=c.snapshot().generation
        c.stop(0); s=send(c); rx(c,133,struct.pack('<III',old,0,0),s.request_id)
        c.arm(0); a=send(c); c.stop(0); s=send(c)
        self.assertEqual(s.payload,struct.pack('<I',a.request_id))
        rx(c,255,struct.pack('<BBHI',4,2,0,old),a.request_id)
        self.assertEqual(c.snapshot().generation,old)
        self.assertEqual(kinds(rx(c,255,struct.pack('<BBHI',5,4,0,old),s.request_id)),('COMMAND_ERROR','RECOVERY'))
        self.assertIsNone(c.prepare_tx())

    def test_invalid_calls_cannot_hide_or_trigger_expired_deadline(self):
        c=at('ARM'); send(c); c.stop(0); t=c.prepare_tx(); before=c.snapshot()
        malformed=object.__new__(Frame)
        for name,value in [('message_type',129),('session',NONCE),('sequence',1),('request_id',1),('payload',b'x'*513)]:
            object.__setattr__(malformed,name,value)
        calls=[lambda:c.configure(Config(),1000),lambda:c.arm(1000),lambda:c.stop(1000),
               lambda:c.start(NONCE+1,1000),lambda:c.written(t,-1,1000),lambda:c.receive(malformed,1000),
               lambda:c.protocol_fault('DOWNSTREAM',1000),lambda:c.tick(True)]
        for fn in calls:
            with self.assertRaises(SessionError): fn()
            self.assertEqual(c.snapshot(),before)
            self.assertIs(c.prepare_tx(),t)
        self.assertEqual(kinds(c.tick(1000)),('RECOVERY',))

    def test_foreign_every_type_and_matching_malformed_type_recover_boundedly(self):
        for stage in ('HELLO','CAPS','READY','ARMED'):
            for kind in range(256):
                c=at(stage); before=c.snapshot()
                result=c.receive(Frame(kind,NONCE+1,0xffffffff,0xffffffff,b'bad'),2**64-1)
                if 1<=kind<=7:
                    self.assertEqual(kinds(result),('RECOVERY',))
                else:
                    self.assertEqual(result,()); self.assertEqual(c.snapshot(),before)
                c=at(stage)
                result=rx(c,kind,b'bad')
                self.assertEqual(kinds(result),('RECOVERY',))
                self.assertLessEqual(len(result),2)

    def test_data_limits_and_terminal_barrier(self):
        for offset,count,code,ok in [(0,1,0,True),(2047,1,4095,True),(1798,250,1,True),
                                     (1799,250,1,False),(2048,1,1,False),(0,0,1,False),
                                     (0,1,4096,False),(65535,1,1,False)]:
            c=at('ARMED'); rx(c,144,begin(c)); g=c.snapshot().generation
            payload=struct.pack('<IIHH',g,1,offset,count)+struct.pack('<H',code)*count
            self.assertEqual(kinds(rx(c,145,payload)),('WINDOW_DATA',) if ok else ('RECOVERY',))
        for terminal in (146,147):
            for after in (144,145,146,147):
                c=at('ARMED'); rx(c,144,begin(c)); g=c.snapshot().generation
                payload=struct.pack('<IIHHI',g,1,2048,0,123) if terminal==146 else struct.pack('<IIHH',g,1,1,0)
                rx(c,terminal,payload)
                candidate={144:begin(c),145:struct.pack('<IIHHH',g,1,0,1,1),
                           146:struct.pack('<IIHHI',g,1,2048,0,123),147:struct.pack('<IIHH',g,1,1,0)}[after]
                self.assertEqual(kinds(rx(c,after,candidate)),('RECOVERY',))

    def test_repeated_rearm_window_number_and_stale_generation(self):
        c=at('CONFIGURED')
        for window in range(1,101):
            c.arm(0); a=send(c); rx(c,132,struct.pack('<II',a.request_id,1),a.request_id)
            self.assertEqual(kinds(rx(c,147,struct.pack('<IIHH',a.request_id,window,1,0))),('WINDOW_ABORT',))
            self.assertEqual(c.snapshot().window_id,window)
            self.assertEqual(c.snapshot().terminal.window_id,window)
            c.stop(0); s=send(c)
            rx(c,133,struct.pack('<III',a.request_id,window,0),s.request_id)
            self.assertFalse(c.snapshot().window_barrier)
        c.arm(0); a=send(c); rx(c,132,struct.pack('<II',a.request_id,1),a.request_id)
        self.assertEqual(kinds(rx(c,147,struct.pack('<IIHH',a.request_id-2,101,1,0))),('RECOVERY',))

    def test_max_tx_reservation_accounts_for_unstarted_ordinary(self):
        for nextseq in (MAX_ID-1,MAX_ID):
            for firstbytes in (0,1,34):
                c=at('CONFIGURED'); c._next_tx=nextseq; c._next_request=MAX_ID-1
                c.arm(0); t=c.prepare_tx()
                c.written(t,min(firstbytes,len(t.encoded)),0)
                result=c.stop(0)
                if nextseq==MAX_ID:
                    self.assertEqual(kinds(result),('RECOVERY',)); self.assertIsNone(c.prepare_tx())
                else:
                    self.assertEqual(result,())
                    if c.prepare_tx().frame.message_type==4: send(c)
                    s=send(c); self.assertEqual(s.sequence,MAX_ID)
                    self.assertEqual(s.request_id,MAX_ID)

    def test_downstream_fault_after_end_never_leaves_reusable_measurement(self):
        c=at('ARMED'); rx(c,144,begin(c)); g=c.snapshot().generation
        self.assertEqual(kinds(rx(c,146,struct.pack('<IIHHI',g,1,2048,0,123))),('WINDOW_END_ADMITTED',))
        fault=c.protocol_fault(FaultCode.DOWNSTREAM,3000)
        self.assertEqual(kinds(fault),('RECOVERY',))
        self.assertTrue(fault[0].preserve_fragments); self.assertTrue(fault[0].reset_transport)
        self.assertEqual(fault[0].fault,FaultCode.DOWNSTREAM)
        self.assertIsNone(c.prepare_tx())
        c.start(NONCE+1,3000)
        self.assertIsNone(c.snapshot().terminal); self.assertIsNone(c.snapshot().config)
        self.assertEqual(c.snapshot().window_id,0)

if __name__=='__main__': unittest.main(verbosity=2)

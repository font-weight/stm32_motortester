import ctypes as C
import os
import struct
import unittest
import zlib

lib = C.CDLL(os.environ.get('TEST_LIBRARY', './test_link.so'))
WRITE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(C.c_ubyte), C.c_size_t)
lib.poll_link.argtypes = (C.c_uint, WRITE, C.c_void_p)
for name in ('get',):
    getattr(lib, name).restype = C.c_uint
OK, TRIGGERED, READY, BUSY, ARGUMENT, STALE, BAD_RELEASE, BLOCKED, RECOVERY = range(9)
IDLE, BEGIN, DATA, END, END_QUEUED, WAIT_RELEASE, STOPPING, FAILED = range(8)
PHASE, ARMED, ACTIVE, LAST, CRC, TOMB, COUNT, HEAD, OFFSET, SAMPLES, SINCE, ACQ, GEN, SEQ, SESSION, TOMBWIN, STOPPING_TX, CRC_RUNNING = range(18)

def u16(p, i=0): return struct.unpack_from('<H', p, i)[0]
def u32(p, i=0): return struct.unpack_from('<I', p, i)[0]

def decode(wire):
    assert wire[-1:] == b'\0', 'partial trailing frame'
    messages = []
    for encoded in wire.split(b'\0')[:-1]:
        assert encoded, 'empty frame'
        raw=bytearray(); i=0
        while i<len(encoded):
            run=encoded[i]; i+=1
            assert run and i+run-1<=len(encoded)
            raw.extend(encoded[i:i+run-1]); i+=run-1
            if run!=255 and i<len(encoded): raw.append(0)
        assert len(raw)>=28 and raw[:2]==b'\0\1'
        assert raw[3]==0 and u16(raw,6)==0
        length=u16(raw,4)
        assert len(raw)==28+length
        assert u32(raw,len(raw)-4)==zlib.crc32(raw[:-4])
        messages.append(dict(type=raw[2],session=struct.unpack_from('<Q',raw,8)[0],
            sequence=u32(raw,16),request=u32(raw,20),payload=bytes(raw[24:-4])))
    return messages

class Fixture(unittest.TestCase):
    def setUp(self):
        self.assertEqual(lib.reset_all(),0)
        self.now=0; self.tick=0; self.data=bytearray(); self.limit=64
        self.block_every=0;self.calls=0;self.io_error=0
        self.callback=WRITE(self.write)
        self.drain()
    def write(self,_,p,n):
        self.calls+=1
        assert 0<n<=64
        if self.io_error:return self.io_error
        if self.block_every and self.calls%self.block_every==0:return 0
        k=min(n,self.limit)
        self.data.extend(C.string_at(p,k));return k
    def poll(self):
        self.tick+=1
        if self.tick%50==0:self.now+=1
        before=self.calls
        r=lib.poll_link(self.now,self.callback,None)
        self.assertLessEqual(self.calls-before,1)
        return r
    def drain(self):
        for _ in range(50000):
            if not lib.get(COUNT):return
            self.assertIn(self.poll(),(OK,BLOCKED))
        self.fail('failed to drain')
    def wait_for(self,predicate):
        for _ in range(50000):
            if predicate():return
            self.assertIn(self.poll(),(OK,BLOCKED))
        self.fail('boundary not reached')
    def capture(self,mode=1,pre=512,edge=0,auto=False,seed=0,rearm=True):
        if rearm:
            self.assertEqual(lib.configure(mode,pre,edge),OK)
            self.assertEqual(lib.arm(11,self.now),OK)
        low=(1000+seed)%1900+1; high=3000+seed%700
        prior=high if edge else low
        for _ in range(pre+7):self.assertEqual(lib.sample(prior,self.now),OK)
        trigger=prior if auto else (low if edge else high)
        if auto:self.now+=100
        self.assertEqual(lib.sample(trigger,self.now),TRIGGERED)
        expected=[prior]*pre+[trigger]
        for i in range(2048-pre-1):
            value=(i*37+seed*19)&4095
            expected.append(value)
            self.assertEqual(lib.sample(value,self.now),READY if i==2048-pre-2 else OK)
        return expected
    def transfer(self):self.wait_for(lambda:lib.get(PHASE)==WAIT_RELEASE)
    def messages(self):return decode(bytes(self.data))
    def raw_samples(self):
        b=C.create_string_buffer(4096);lib.copy_samples(b);return b.raw
    def assert_window(self,expected,pre,edge,auto,window=1):
        messages=self.messages(); body=[m for m in messages if 144<=m['type']<=146 and u32(m['payload'],4)==window]
        self.assertEqual([m['type'] for m in body],[144]+[145]*9+[146])
        self.assertEqual([m['sequence'] for m in messages],list(range(1,len(messages)+1)))
        self.assertTrue(all(m['session']==0x1234 for m in messages))
        begin=body[0]['payload']
        self.assertEqual(len(begin),40)
        self.assertEqual((u32(begin),u32(begin,4),u32(begin,8)),(11,window,7))
        self.assertEqual((u16(begin,12),u16(begin,14),u16(begin,16)),(1,2048,pre))
        self.assertEqual(u16(begin,18),65535 if auto else pre)
        self.assertEqual(tuple(begin[20:24]),(2 if auto else 0,0,edge,0))
        self.assertEqual(u16(begin,24),2048)
        self.assertEqual(u16(begin,26),(1 if auto else 0)|(2 if 0 in expected or 4095 in expected else 0))
        self.assertEqual((u32(begin,28),u16(begin,32),u16(begin,34),u32(begin,36)),(72000000,71,19,0))
        codes=[]
        for m in body[1:-1]:
            p=m['payload']; n=u16(p,10)
            self.assertEqual((u32(p),u32(p,4)),(11,window))
            self.assertEqual(u16(p,8),len(codes));self.assertTrue(1<=n<=250)
            self.assertEqual(len(p),12+2*n)
            codes.extend(struct.unpack('<'+'H'*n,p[12:]))
        self.assertEqual(codes,expected)
        end=body[-1]['payload'];self.assertEqual(len(end),16)
        self.assertEqual((u32(end),u32(end,4),u16(end,8),u16(end,10)),(11,window,2048,0))
        crc=zlib.crc32(struct.pack('<2048H',*expected))
        self.assertEqual(u32(end,12),crc);self.assertEqual(lib.get(CRC),crc)
        return crc

class Nominal(Fixture):
    def test_twenty_four_full_windows(self):
        index=0
        for mode in range(3):
            for pre in (0,512,1024):
                for edge in range(2):
                    for auto in ((False,True) if mode==0 else (False,)):
                        with self.subTest(mode=mode,pre=pre,edge=edge,auto=auto):
                            self.setUp();index+=1
                            self.limit=(1,7,63,64)[index%4];self.block_every=5
                            expected=self.capture(mode,pre,edge,auto,index)
                            frozen=self.raw_samples();self.transfer()
                            self.assertEqual(self.raw_samples(),frozen)
                            self.assertEqual(lib.sample(3000,self.now),BUSY)
                            self.assertEqual(self.raw_samples(),frozen)
                            self.assertEqual(lib.configure(mode,pre,edge),BUSY)
                            self.assertEqual(lib.arm(22,self.now),BUSY)
                            crc=self.assert_window(expected,pre,edge,auto)
                            self.assertEqual(lib.release_link(11,1,crc,12,self.now),OK)
                            self.assertEqual(lib.get(ARMED),int(mode!=2))
                            self.assertEqual(lib.get(PHASE),IDLE)
                            self.drain();before=self.raw_samples();state=lib.get(ACQ)
                            self.assertEqual(lib.release_link(11,1,crc,13,self.now),OK)
                            self.assertEqual(lib.get(ACQ),state);self.assertEqual(self.raw_samples(),before)
                            self.drain()
        self.assertEqual(index,24)

    def test_twenty_continuous_windows_one_session(self):
        for window in range(1,21):
            expected=self.capture(pre=1024,seed=window,rearm=(window==1))
            self.limit=(1,7,63,64)[window%4];self.block_every=7
            frozen=self.raw_samples();self.transfer()
            self.assertEqual(frozen,self.raw_samples())
            crc=self.assert_window(expected,1024,0,False,window)
            self.assertEqual(lib.release_link(11,window,crc,12+window,self.now),OK)
            self.drain()
        self.assertEqual(lib.get(LAST),20)

class Stops(Fixture):
    def test_stop_boundaries(self):
        for boundary in ('idle','armed','trigger','frozen','begin_queued','begin_partial','data_partial','end_queued','end_partial','end_finished'):
            with self.subTest(boundary=boundary):
                self.setUp();window=0;committed=False
                if boundary!='idle':
                    if boundary in ('armed','trigger'):
                        self.assertEqual(lib.configure(1,0,0),OK);self.assertEqual(lib.arm(11,self.now),OK)
                        if boundary=='trigger':
                            self.assertEqual(lib.sample(1000,self.now),OK);self.assertEqual(lib.sample(3000,self.now),TRIGGERED);window=1
                    else:
                        self.capture();window=1
                        if boundary in ('begin_queued','begin_partial'):
                            self.limit=0 if boundary=='begin_queued' else 1;self.poll()
                        elif boundary=='data_partial':
                            self.wait_for(lambda:lib.get(HEAD)==145);self.limit=1;self.poll()
                        elif boundary in ('end_queued','end_partial','end_finished'):
                            self.wait_for(lambda:lib.get(PHASE)==END and lib.get(COUNT)<2)
                            self.limit=0;self.poll()
                            self.assertEqual(lib.get(PHASE),END_QUEUED)
                            if boundary in ('end_partial','end_finished'):
                                self.limit=1
                                self.wait_for(lambda:lib.get(HEAD)==146 and lib.get(OFFSET)>0)
                                committed=True
                                if boundary=='end_finished':self.transfer()
                gen=0 if boundary=='idle' else 11
                self.assertEqual(lib.stop_link(gen,19,self.now),OK)
                self.assertEqual(lib.stop_link(gen,20,self.now),BUSY)
                self.limit=7;self.drain();self.assertEqual(lib.get(PHASE),IDLE)
                self.assertEqual(lib.get(ARMED),0);self.assertEqual(lib.get(ACTIVE),0)
                messages=self.messages();types=[m['type'] for m in messages]
                self.assertEqual(types.count(147),int(window!=0 and not committed))
                self.assertEqual(types.count(146),int(committed))
                self.assertEqual(types[-2:],[133,134])
                self.assertEqual([m['sequence'] for m in messages],list(range(1,len(messages)+1)))
                ack=messages[-2]['payload'];self.assertEqual((u32(ack),u32(ack,4)),(gen,window))
                self.assertEqual(ack[8:],bytes(4))
                self.assertEqual(lib.stop_link(gen,21,self.now),OK);self.drain()
                self.assertEqual(sum(m['type']==147 for m in self.messages()),int(window!=0 and not committed))
    def test_stop_reserve_preserves_all_earlier_replies(self):
        self.capture();self.limit=1;self.poll()
        self.assertEqual(lib.reply(20,self.now),0);self.assertEqual(lib.reply(21,self.now),0)
        self.assertEqual(lib.get(COUNT),3)
        self.assertEqual(lib.stop_link(11,22,self.now),OK)
        self.assertEqual(lib.get(COUNT),6)
        self.limit=7;self.drain();messages=self.messages()
        self.assertEqual([m['type'] for m in messages],[129,144,255,255,147,133,134])
        self.assertEqual([m['request'] for m in messages if m['request']],[1,20,21,22])
    def test_dropped_counter_saturation_fails_closed(self):
        if not lib.has_dropped_counter():self.skipTest('snapshot predates dropped counter')
        lib.force_dropped(0xfffffffe)
        self.assertEqual(lib.stop_link(0,19,self.now),OK);self.drain()
        self.assertEqual(u32(self.messages()[-1]['payload'],20),0xfffffffe)
        self.capture();self.assertEqual(lib.stop_link(11,20,self.now),RECOVERY)
        self.assertEqual(lib.get(PHASE),FAILED);self.assertEqual(lib.get(ARMED),0)
        self.assertEqual(lib.get(COUNT),0)
    def test_cancelled_window_status_counter(self):
        self.capture();self.assertEqual(lib.stop_link(11,19,self.now),OK);self.drain()
        status=self.messages()[-1]['payload']
        self.assertEqual(u32(status,20),1,'cancelled allocated window must increment dropped_windows')
    def test_dropped_counter_accumulates_only_aborted_windows(self):
        self.capture();self.assertEqual(lib.stop_link(11,20,self.now),OK);self.drain()
        self.assertEqual(u32(self.messages()[-1]['payload'],20),1)
        self.assertEqual(lib.stop_link(11,21,self.now),OK);self.drain()
        self.assertEqual(u32(self.messages()[-1]['payload'],20),1)
        self.capture();self.transfer()
        self.assertEqual(lib.stop_link(11,22,self.now),OK);self.drain()
        self.assertEqual(u32(self.messages()[-1]['payload'],20),1)
        self.capture();self.assertEqual(lib.stop_link(11,23,self.now),OK);self.drain()
        self.assertEqual(u32(self.messages()[-1]['payload'],20),2)
    def test_tombstone_release_after_committed_stop(self):
        self.capture();self.wait_for(lambda:lib.get(PHASE)==END);self.limit=1
        self.wait_for(lambda:lib.get(HEAD)==146 and lib.get(OFFSET)>0)
        crc=lib.get(CRC)
        self.assertEqual(lib.release_link(11,1,crc,18,self.now),BUSY)
        self.assertEqual(lib.stop_link(11,19,self.now),OK);self.limit=64;self.drain()
        self.assertEqual(lib.release_link(11,1,crc,20,self.now),OK)
        self.assertEqual(lib.get(ARMED),0);self.assertEqual(lib.get(ACQ),0);self.drain()
        self.assertEqual(self.messages()[-1]['type'],135)
    def test_wrong_generation_stop_no_mutation(self):
        self.capture();state=(lib.get(PHASE),self.raw_samples(),lib.get(COUNT))
        self.assertEqual(lib.stop_link(12,19,self.now),STALE)
        self.assertEqual((lib.get(PHASE),self.raw_samples(),lib.get(COUNT)),state)

class Release(Fixture):
    def test_bad_release_no_mutation_and_rearm(self):
        self.capture();self.transfer();crc=lib.get(CRC)
        for g,w,c in ((12,1,crc),(11,2,crc),(11,1,crc^1)):
            self.assertEqual(lib.release_link(g,w,c,12,self.now),BAD_RELEASE)
            self.assertEqual(lib.get(PHASE),WAIT_RELEASE)
        self.assertEqual(lib.release_link(11,1,crc,13,self.now),OK);self.drain()
        self.capture(rearm=False,seed=10)
        snapshot=self.raw_samples();self.assertEqual(lib.release_link(11,1,crc,14,self.now),OK)
        self.assertEqual(self.raw_samples(),snapshot);self.assertEqual(lib.get(PHASE),BEGIN)
        self.transfer();self.assertEqual(lib.get(TOMBWIN),2)
        self.assertEqual(lib.release_link(11,1,crc,15,self.now),BAD_RELEASE)
    def test_release_timeout_boundary(self):
        self.capture();self.transfer();since=lib.get(SINCE)
        self.now=since+4999;self.assertEqual(self.poll(),OK)
        self.now=since+5000;self.assertEqual(self.poll(),RECOVERY)
        self.assertEqual(lib.get(ARMED),0)
    def test_late_release_cannot_bypass_timeout(self):
        self.capture();self.transfer();crc=lib.get(CRC)
        self.now=lib.get(SINCE)+5000
        self.assertEqual(lib.release_link(11,1,crc,12,self.now),RECOVERY,
            "release at deadline must not bypass timeout by arriving before poll")
        self.assertEqual(lib.get(ARMED),0)
    def test_release_full_reply_fails_closed(self):
        self.capture();self.transfer();crc=lib.get(CRC)
        for req in range(20,23):self.assertEqual(lib.reply(req,self.now),0)
        self.assertEqual(lib.release_link(11,1,crc,23,self.now),RECOVERY)
        self.assertEqual(lib.get(ARMED),0);self.assertEqual(lib.get(PHASE),FAILED)

class Recovery(Fixture):
    def test_full_bulk_retries_immutable(self):
        expected=self.capture();self.assertEqual(lib.reply(20,self.now),0);self.assertEqual(lib.reply(21,self.now),0)
        self.limit=0;snapshot=self.raw_samples();seq=lib.get(SEQ)
        for _ in range(100):self.assertEqual(self.poll(),BLOCKED)
        self.assertEqual(lib.get(PHASE),BEGIN);self.assertEqual(lib.get(SAMPLES),0)
        self.assertEqual(lib.get(CRC_RUNNING),0xffffffff);self.assertEqual(lib.get(SEQ),seq)
        self.assertEqual(snapshot,self.raw_samples())
        self.limit=64;self.transfer();self.assert_window(expected,512,0,False)
    def test_stall_fails_closed(self):
        self.capture();self.limit=0;self.assertEqual(self.poll(),BLOCKED)
        self.now+=1000;self.assertEqual(self.poll(),RECOVERY)
        self.assertEqual(lib.get(ARMED),0);self.assertEqual(lib.get(PHASE),FAILED)
        calls=self.calls;self.assertEqual(self.poll(),RECOVERY);self.assertEqual(calls,self.calls)
    def test_io_error_fails_closed(self):
        self.capture();self.io_error=-1;self.assertEqual(self.poll(),RECOVERY)
        self.assertEqual(lib.get(ARMED),0)
    def test_invalid_backend_return_fails_closed(self):
        self.capture();self.io_error=65;self.assertEqual(self.poll(),RECOVERY)
        self.assertEqual(lib.get(ARMED),0)
    def test_clock_wrap_timeout(self):
        self.now=0xffffff00
        self.assertEqual(lib.hello(0x2345,self.now),0);lib.link_reinitialize();self.drain()
        self.capture();self.transfer();since=lib.get(SINCE)
        self.now=(since+4999)&0xffffffff;self.assertEqual(self.poll(),OK)
        self.now=(since+5000)&0xffffffff;self.assertEqual(self.poll(),RECOVERY)
    def test_sample_fault_fails_closed(self):
        self.assertEqual(lib.configure(1,0,0),OK);self.assertEqual(lib.arm(11,self.now),OK)
        self.assertEqual(lib.sample(4096,self.now),RECOVERY)
        self.assertEqual(lib.get(ARMED),0);self.assertEqual(lib.get(PHASE),FAILED)
    def test_window_counter_saturation(self):
        self.assertEqual(lib.configure(1,0,0),OK);self.assertEqual(lib.arm(11,self.now),OK)
        lib.force_last(0xfffffffe)
        self.assertEqual(lib.sample(1000,self.now),OK);self.assertEqual(lib.sample(3000,self.now),RECOVERY)
        self.assertEqual(lib.get(ARMED),0)
    def test_sequence_saturation(self):
        self.capture();lib.force_sequence(0xffffffff);self.assertEqual(self.poll(),RECOVERY)
        self.assertEqual(lib.get(ARMED),0)

class Sessions(Fixture):
    def test_new_hello_invalidates_old_link_state(self):
        self.capture();self.wait_for(lambda:lib.get(PHASE)==END);self.limit=1
        self.wait_for(lambda:lib.get(HEAD)==146 and lib.get(OFFSET)>0)
        self.assertEqual(lib.hello(0x2345,self.now),0)
        self.limit=64
        self.assertEqual(self.poll(),RECOVERY,'new session must not consume old END completion as current event')
        self.assertEqual(lib.get(ARMED),0)
    def test_old_end_with_reused_ids_is_ignored(self):
        self.capture();self.wait_for(lambda:lib.get(PHASE)==END);self.limit=1
        self.wait_for(lambda:lib.get(HEAD)==146 and lib.get(OFFSET)>0)
        self.assertEqual(lib.hello(0x2345,self.now),0);lib.link_reinitialize()
        self.capture(seed=55);self.limit=64;self.poll()
        self.assertNotEqual(lib.get(PHASE),WAIT_RELEASE)
        self.assertEqual(lib.get(TOMB),0)
        self.transfer();messages=self.messages()
        new=[m for m in messages if m['session']==0x2345]
        self.assertEqual([m['sequence'] for m in new],list(range(1,len(new)+1)))
        self.assertEqual([m['type'] for m in new],[129,144]+[145]*9+[146])
    def test_old_stop_ack_does_not_finish_new_stop(self):
        self.assertEqual(lib.stop_link(0,20,self.now),OK);self.limit=1;self.poll()
        self.assertEqual(lib.get(HEAD),133);self.assertGreater(lib.get(OFFSET),0)
        self.assertEqual(lib.hello(0x2345,self.now),0)
        # Raw TX test: old ACK survives HELLO; new Stop is submitted only after old bridge reset.
        lib.link_reinitialize()
        self.assertEqual(lib.stop_link(0,21,self.now),OK)
        self.limit=64;self.poll()
        self.assertEqual(lib.get(PHASE),STOPPING,'old-session STOP_ACK must not clear current Stop barrier')
        self.assertEqual(lib.get(STOPPING_TX),1)
        self.drain();self.assertEqual(lib.get(PHASE),IDLE)

if __name__=='__main__':unittest.main(verbosity=2)

"""Critic-B probes for exact fd1d0ad. No author/conformance test or codec imports.
Run PYTHONPATH=<exact archive>/host python critic_probes.py.
Only private counter injection is used to reach uint32 exhaustion.
"""
import binascii
import gc
import json
from pathlib import Path
import random
import struct
import tracemalloc
import unittest
from motortester.device_emulator import DeviceEmulator, DeviceError

NONCE = 0xA1B2C3D4E5F60708
MAX = 2**32-2
ROOT = Path(__import__('motortester.device_emulator',fromlist=['x']).__file__).parents[2]


def crc_slow(data):
    result=0xffffffff
    for value in data:
        result ^= value
        for _ in range(8):
            result=(result>>1)^ (0xedb88320 if result&1 else 0)
    return result^0xffffffff


def cobs(raw):
    result=bytearray(b'\0'); index=0; count=1
    for x in raw:
        if x==0:
            result[index]=count;index=len(result);result.append(0);count=1
        else:
            result.append(x);count+=1
            if count==255:
                result[index]=count;index=len(result);result.append(0);count=1
    result[index]=count
    return bytes(result)+b'\0'


def packet(kind,nonce,seq,req,payload=b''):
    raw=struct.pack('<BBBBHHQII',0,1,kind,0,len(payload),0,nonce,seq,req)+payload
    return cobs(raw+struct.pack('<I',binascii.crc32(raw)))


def decode(raw):
    if not raw or raw[-1]!=0:raise ValueError('missing delimiter')
    encoded=raw[:-1];out=bytearray();pos=0
    while pos<len(encoded):
        size=encoded[pos]
        if not size or pos+size>len(encoded):raise ValueError('invalid COBS')
        out.extend(encoded[pos+1:pos+size]);pos+=size
        if size<255 and pos<len(encoded):out.append(0)
    if not 28<=len(out)<=540:raise ValueError('length')
    a,b,k,r,length,r2,nonce,seq,req=struct.unpack('<BBBBHHQII',out[:24])
    if (a,b,r,r2)!=(0,1,0,0) or length!=len(out)-28:raise ValueError('header')
    if binascii.crc32(out[:-4])!=int.from_bytes(out[-4:],'little'):raise ValueError('CRC')
    return (k,nonce,seq,req,bytes(out[24:-4]))


def frames(raw):
    if raw and raw[-1]!=0:raise ValueError('partial frame')
    return [decode(x+b'\0') for x in raw.split(b'\0') if x]


def config(edge=0,level=2048):return struct.pack('<HBBBBHHHI',1,1,0,edge,0,level,0,0,0)


class Peer:
    def __init__(self,edge=0,level=2048,arm=True):
        self.d=DeviceEmulator();self.nonce=NONCE;self.seq=self.req=2;self.now=0;self.raw=bytearray();self.edge=edge;self.level=level
        self.put(packet(1,0,1,1,struct.pack('<Q',self.nonce)));self.drain()
        self.cmd(2);self.drain();self.cmd(3,config(edge,level));self.drain()
        if arm:self.cmd(4,struct.pack('<I',1));self.drain()
    def put(self,raw):
        for _ in range(10000):
            if not raw:return
            accepted=self.d.write(raw[:4096],self.now).accepted_prefix
            if not 0<=accepted<=min(4096,len(raw)):raise ValueError('accepted prefix')
            raw=raw[accepted:]
            if accepted==0:
                if not self.pull(1):raise ValueError('deadlock')
        raise ValueError('put iteration limit')
    def cmd(self,kind,payload=b''):
        p=packet(kind,self.nonce,self.seq,self.req,payload);self.seq+=1;self.req+=1;self.put(p);return p
    def pull(self,count=4096):
        b=self.d.read(count,self.now);self.raw.extend(b);return b
    def drain(self,count=4096):
        start=len(self.raw)
        for _ in range(12000):
            if not self.pull(count):return bytes(self.raw[start:])
        raise ValueError('drain iteration limit')
    def read_exact(self,n):
        result=b''
        while len(result)<n:
            x=self.pull(min(4096,n-len(result)))
            if not x:raise ValueError('premature empty')
            result+=x
        return result
    def one(self):
        result=b''
        while not result.endswith(b'\0'):
            x=self.pull(1)
            if not x:raise ValueError('premature frame end')
            result+=x
            if len(result)>544:raise ValueError('output bound')
        return decode(result),result
    def trigger(self):
        if not self.d.trigger(self.now):raise ValueError('trigger rejected')


class CriticProbes(unittest.TestCase):
    def check_seq(self,raw,nonce=NONCE):
        fs=[f for f in frames(raw) if f[1]==nonce]
        self.assertEqual([f[2] for f in fs],list(range(1,len(fs)+1)))
        return fs
    def reconstruct(self,raw,g,w,edge,level):
        fs=frames(raw);begin=[f for f in fs if f[0]==144];end=[f for f in fs if f[0]==146]
        self.assertEqual(len(begin),1);self.assertEqual(len(end),1)
        self.assertEqual(begin[0][4],struct.pack('<IIIHHHHBBBBHHIHHI',g,w,1,1,2048,0,0,0,0,edge,0,level,2,72000000,71,19,0))
        data=bytearray();cursor=0
        for f in fs:
            if f[0]!=145:continue
            generation,window,offset,count=struct.unpack('<IIHH',f[4][:12])
            self.assertEqual((generation,window,offset),(g,w,cursor));self.assertTrue(1<=count<=250);self.assertEqual(len(f[4]),12+2*count)
            cursor+=count;data.extend(f[4][12:])
        self.assertEqual(cursor,2048);self.assertEqual(len(data),4096)
        codes=list(struct.unpack('<2048H',data));expected=[(i*73+w*19+g*7)%4096 for i in range(2048)]
        expected[0]=level-(edge==1);expected[511]=0;expected[1023]=4095
        self.assertEqual(codes,expected)
        crc=crc_slow(data);self.assertEqual(crc,binascii.crc32(data))
        self.assertEqual(end[0][4],struct.pack('<IIHHI',g,w,2048,0,crc))
        return crc
    def test_01_golden_crc_and_payload_vectors(self):
        source=json.loads((ROOT/'host/tests/fixtures/device_emulator/golden-vectors.json').read_text())
        values=source['vectors']
        self.assertEqual(len(values),19)
        for v in values:
            raw=bytes.fromhex(v['packet_hex']);payload=bytes.fromhex(v['payload_hex'])
            self.assertEqual(decode(raw),(v['type'],v['session'],v['sequence'],v['request_id'],payload))
            self.assertEqual(raw,packet(v['type'],v['session'],v['sequence'],v['request_id'],payload))
        self.assertEqual(crc_slow(b'123456789'),0xcbf43926)
    def test_02_release_loops_stop_reconfigure_extreme_thresholds(self):
        for edge,level in [(0,1),(0,4094),(1,1),(1,4094)]:
            p=Peer(edge,level);g=p.d.snapshot().generation
            for w in range(1,21):
                p.trigger();raw=p.drain(1 if w%7==0 else 31);crc=self.reconstruct(raw,g,w,edge,level)
                p.cmd(7,struct.pack('<III',g,w,crc))
                # Latest non-started WAIT_RELEASE status may still be queued; it was drained above.
                ack=packet(135,NONCE,p.d.snapshot().next_tx_sequence,p.req-1,struct.pack('<II',g,w))
                self.assertFalse(p.d.trigger(0));prefix=p.read_exact(len(ack)-1)
                self.assertEqual(prefix,ack[:-1]);self.assertFalse(p.d.trigger(0))
                self.assertEqual(p.read_exact(1),b'\0');self.assertEqual(p.d.snapshot().state,2);p.drain()
                old=p.cmd(7,struct.pack('<III',g,w,crc));self.assertEqual([f[0] for f in frames(p.drain())],[135])
                s=p.d.snapshot();p.put(old);self.assertEqual(p.drain(),b'');self.assertEqual(p.d.snapshot().window_id,s.window_id)
            p.cmd(5,struct.pack('<I',g));self.assertIn(133,[f[0] for f in frames(p.drain())]);self.assertFalse(p.d.snapshot().held_samples)
            self.check_seq(p.raw)
    def test_03_stop_at_every_byte_of_complete_window(self):
        base=Peer();base.trigger();window=base.drain();segments=window.split(b'\0');end_start=0
        for part in segments:
            if not part:continue
            f=decode(part+b'\0')
            if f[0]==146:break
            end_start+=len(part)+1
        for cut in range(len(window)+1):
            with self.subTest(cut=cut):
                p=Peer();p.trigger();prefix=p.read_exact(cut) if cut else b''
                self.assertEqual(prefix,window[:cut]);s=p.d.snapshot();p.d.read(0,0)
                self.assertEqual(p.d.snapshot().next_tx_sequence,s.next_tx_sequence)
                g=p.d.snapshot().generation;p.cmd(5,struct.pack('<I',g));p.cmd(5,struct.pack('<I',g));p.drain()
                fs=self.check_seq(p.raw);term=[f for f in fs if f[0] in (146,147)]
                self.assertEqual(len(term),1);self.assertEqual(term[0][0],146 if cut>end_start else 147)
                ack=[i for i,f in enumerate(fs) if f[0]==133];self.assertEqual(len(ack),2)
                self.assertFalse(any(f[0] in (144,145,146,147) for f in fs[ack[0]+1:]));self.assertEqual(p.d.snapshot().state,0)
                self.assertEqual(p.d.snapshot().dropped_windows,int(cut<=end_start))
        print('CRITIC exhaustive Stop cuts',len(window)+1,flush=True)
    def test_04_new_hello_every_offset_of_all_window_frame_kinds(self):
        base=Peer();base.trigger();window=base.drain();offset=0;regions=[]
        for part in window.split(b'\0'):
            if not part:continue
            raw=part+b'\0';kind=decode(raw)[0]
            regions.append((offset,raw,kind));offset+=len(raw)
        # All offsets in BEGIN, first/last DATA, END and final STATUS, with intermediate DATA boundary coverage.
        tested=0
        for index,(offset,raw,kind) in enumerate(regions):
            cuts=range(1,len(raw)) if kind!=145 or index in (2,10) else [1,len(raw)//2,len(raw)-1]
            for cut in cuts:
                p=Peer();p.trigger();p.read_exact(offset+cut)
                n=NONCE+7;p.put(packet(1,0,1,1,struct.pack('<Q',n)))
                new_start=len(p.raw);after=p.drain(13);self.assertTrue(after.startswith(raw[cut:]))
                fs=frames(bytes(p.raw));new=[f for f in fs if f[1]==n]
                self.assertEqual([(f[0],f[2],f[3]) for f in new],[(129,1,1)])
                self.assertEqual(fs[-2][0],kind)
                s=p.d.snapshot();self.assertEqual((s.session,s.state,s.config_id,s.generation,s.window_id,s.held_samples,s.terminal,s.tombstone,s.stop_barrier),(n,0,0,0,0,False,None,None,False))
                tested+=1
        print('CRITIC stale-window callbacks cuts',tested,flush=True)
    def test_05_all_message_types_direction_session_order(self):
        device_types={129,130,131,132,133,134,135,144,145,146,147,255}
        for foreign in (False,True):
            for kind in range(256):
                p=Peer(arm=False);prior=p.d.snapshot();p.put(packet(kind,NONCE+int(foreign),p.seq,p.req))
                s=p.d.snapshot();result=frames(p.drain())
                if kind in device_types:
                    self.assertFalse(s.negotiated);self.assertEqual(s.state,5);self.assertEqual(result,[])
                elif kind==1 or foreign:
                    self.assertTrue(s.negotiated);self.assertEqual(s.next_rx_sequence,prior.next_rx_sequence);self.assertEqual(result,[])
                elif kind in (2,6):
                    self.assertEqual([f[0] for f in result],[130 if kind==2 else 134])
                else:
                    self.assertEqual([f[0] for f in result],[255]);self.assertEqual(result[0][4][:2],bytes([kind,6 if kind in (3,4,5,7) else 5]))
    def test_06_backpressure_random_chunks_mandatory_reply_conservation(self):
        rng=random.Random(147007);p=Peer(arm=False);all_requests=[];raw=bytearray()
        for i in range(600):
            kind=(6,2,5,3)[i%4];payload=b'' if kind in(2,6) else struct.pack('<I',0) if kind==5 else config()
            raw.extend(packet(kind,NONCE,p.seq,p.req,payload));all_requests.append((p.req,{6:134,2:130,5:133,3:None}[kind]));p.seq+=1;p.req+=1
        remaining=bytes(raw);backpressure=0;sample_id=id(p.d._samples)
        while remaining:
            n=rng.randrange(1,min(len(remaining),4096)+1);r=p.d.write(remaining[:n],0);remaining=remaining[r.accepted_prefix:]
            if r.accepted_prefix<n:backpressure+=1
            s=p.d.snapshot();self.assertLessEqual(s.control_descriptors,8);self.assertLessEqual(s.tx_size,544);self.assertLessEqual(s.rx_used,543);self.assertEqual(id(p.d._samples),sample_id)
            if r.accepted_prefix==0 or rng.random()<0.2:p.pull(rng.randrange(1,70))
        p.drain();self.assertGreater(backpressure,0);fs=self.check_seq(p.raw)
        for req,kind in all_requests:
            got=[f for f in fs if f[3]==req];self.assertEqual(len(got),1)
            self.assertIn(got[0][0],(131,255) if kind is None else (kind,))
        print('CRITIC backpressure events',backpressure,'mandatory replies',len(all_requests),flush=True)
    def test_07_boundaries_recovery_and_invalid_calls_at_deadline(self):
        p=Peer();p.trigger();raw=p.drain();s=p.d.snapshot();p.now=5000
        for fn in (lambda:p.d.write(bytearray(),5000),lambda:p.d.read(True,5000),lambda:p.d.trigger(True),lambda:p.d.tick(-1),lambda:p.d.disconnect(2**64)):
            with self.assertRaises(DeviceError):fn()
            self.assertEqual(p.d.snapshot(),s)
        p.d.tick(5000);self.assertEqual((p.d.snapshot().state,p.d.snapshot().reason),(5,3))
        p=Peer(arm=False);p.d._next_rx=MAX;p.d._last_request=MAX-1;p.seq=p.req=MAX
        raw=p.cmd(6);p.drain();p.put(raw);self.assertEqual(p.drain(),b'')
        p.cmd(6);self.assertFalse(p.d.snapshot().negotiated);self.assertEqual(p.d.snapshot().reason,6)
        p.put(packet(1,0,1,1,struct.pack('<Q',NONCE)));self.assertFalse(p.d.snapshot().negotiated)
        p.put(packet(1,0,1,1,struct.pack('<Q',NONCE+1)));p.drain();self.assertTrue(p.d.snapshot().negotiated)
    def test_08_old_tombstone_during_new_end_and_abort(self):
        for second_outcome in ('END','ABORT'):
            p=Peer();p.trigger();first=p.drain();g=p.d.snapshot().generation;crc=self.reconstruct(first,g,1,0,2048)
            p.cmd(7,struct.pack('<III',g,1,crc));p.drain();p.trigger();p.one();p.read_exact(1)
            p.cmd(7,struct.pack('<III',g,1,crc));self.assertTrue(p.d.snapshot().held_samples);self.assertFalse(p.d.trigger(0))
            if second_outcome=='ABORT':p.cmd(5,struct.pack('<I',g))
            raw=p.drain();self.assertEqual(p.d.snapshot().terminal.outcome,second_outcome)
            p.cmd(7,struct.pack('<III',g,1,crc));fs=frames(p.drain())
            self.assertEqual([f[0] for f in fs],[255] if second_outcome=='END' else [135]);self.assertFalse(p.d.snapshot().continuation if second_outcome=='ABORT' else False)
            self.check_seq(p.raw)
    def test_09_retained_memory_control_pressure(self):
        p=Peer(arm=False);p.raw.clear();tracemalloc.start()
        def rounds(n):
            for _ in range(n):
                p.cmd(6);p.drain();p.raw.clear()
            gc.collect();return tracemalloc.get_traced_memory()[0]
        rounds(100);a=rounds(200);b=rounds(5000);tracemalloc.stop()
        self.assertLess(b-a,32768);self.assertEqual(len(p.d._samples),4096);self.assertFalse(hasattr(p.d,'__dict__'))
        print('CRITIC retained memory delta',b-a,flush=True)

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(CriticProbes)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)

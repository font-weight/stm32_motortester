#!/usr/bin/env python3
"""Read-only cross-team experiment. No verdict, installs, GUI, or physical I/O."""
import dataclasses
import enum
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'snapshots/pr74/host'))
from motortester.wire import Frame, StreamDecoder, encode_frame
from motortester.session import SessionController, Config, Phase, EventKind, WindowReceipt, SessionError
from motortester.window import ExpectedContext, WindowReceiver
from motortester.device_emulator import DeviceEmulator

def clean(v):
    if dataclasses.is_dataclass(v): return clean(dataclasses.asdict(v))
    if isinstance(v, enum.Enum): return v.value
    if isinstance(v, bytes): return v.hex()
    if isinstance(v, dict): return {k:clean(x) for k,x in v.items()}
    if isinstance(v, (tuple,list)): return [clean(x) for x in v]
    return v

def decode(data, now=100):
    decoder = StreamDecoder(); frames=[]
    for pos in range(0,len(data),64):
        r=decoder.feed(data[pos:pos+64],now); assert not r.diagnostics, r
        frames.extend(r.frames)
    return frames

def compact(frames):
    return [{'type':f.message_type,'session':f.session,'seq':f.sequence,'request':f.request_id,
             'payload':f.payload.hex() if len(f.payload)<50 else 'omitted-data'} for f in frames]

def parser_replay():
    base=Path('/workspace/shared/wait-release-package-root-r1')
    records=[]
    for suite in ('wait','modes'):
        oracle=json.loads((base/suite/'independent-wire.json').read_text())
        for row in oracle:
            p=Path(row['path']); data=p.read_bytes()
            for chunk in (1,7,64,4096):
                d=StreamDecoder(); frames=[]
                for pos in range(0,len(data),chunk):
                    r=d.feed(data[pos:pos+chunk],100); assert not r.diagnostics,(p,chunk,r)
                    frames.extend(r.frames)
                assert len(frames)==row['complete_frames'],p
                expected=row['frames']
                assert [(f.message_type,f.session,f.sequence,f.request_id,f.payload.hex()) for f in frames]==[(x['type'],x['session'],x['sequence'],x['request'],x['payload_hex']) for x in expected],p
                tail=len(data.rsplit(b'\0',1)[-1]); assert tail==row['incomplete_tail_bytes']
            records.append({'path':str(p),'sha256':hashlib.sha256(data).hexdigest(),'frames':len(frames),'tail_bytes':tail})
    assert len(records)==78 and sum(r['tail_bytes']>0 for r in records)==6
    return {'streams':78,'chunk_sizes':[1,7,64,4096],'decoder_runs':312,'intentional_partial_tails':6,'records':records,
            'scope':'Existing native/sanitized A output snapshots; parser framing, not full historical session replay.'}

class Native:
    def __init__(self,name):
        self.proc=subprocess.Popen([str(ROOT/'device_bridge')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.log=[]; self.name=name; self.op('INIT')
    def op(self,cmd):
        self.proc.stdin.write(cmd+'\n');self.proc.stdin.flush(); line=self.proc.stdout.readline()
        if not line: raise RuntimeError(self.proc.stderr.read())
        r=json.loads(line);self.log.append({'command':cmd,**r});return r
    def rx(self,b,t): return self.op(f'RX {t} {b.hex()}')
    def drain(self,t): return self.op(f'POLL {t} 64 150')
    def close(self):
        self.proc.stdin.close(); self.proc.wait(timeout=5)
        stderr=self.proc.stderr.read(); assert self.proc.returncode==0,(self.proc.returncode,stderr)
        (ROOT/'results'/f'native-{self.name}.json').write_text(json.dumps(self.log,indent=2)+'\n')

class Pair:
    def __init__(self,name):
        self.d=Native(name); self.h=SessionController(); self.decoder=StreamDecoder();self.events=[];self.frames=[]
        self.context=None;self.receiver=None;self.window=None
        self.h.start(11,0);self.exchange(0)
        assert self.h.snapshot().phase is Phase.READY
        self.h.configure(Config(),0);self.exchange(0)
        self.h.arm(0);self.exchange(0)
        s=self.h.snapshot();assert s.phase is Phase.ARMED and s.generation==4
        self.context=ExpectedContext(s.session,s.generation,s.config_id,1,0,0,2048,0)
        self.receiver=WindowReceiver(self.context)
    def accept(self,r,t):
        data=bytes.fromhex(r['wire']);new=[]
        for pos in range(0,len(data),64):
            decoded=self.decoder.feed(data[pos:pos+64],t);assert not decoded.diagnostics
            for frame in decoded.frames:
                self.frames.append(frame)
                events=self.h.receive(frame,t); self.events.extend(events);new.extend(events)
                if self.receiver and frame.message_type in (144,145,146,147) and any(e.kind in (EventKind.WINDOW_BEGIN,EventKind.WINDOW_DATA,EventKind.WINDOW_END_ADMITTED,EventKind.WINDOW_ABORT) for e in events):
                    window=self.receiver.feed(frame,t)
                    if window: self.window=window
        return new
    def exchange(self,t):
        for _ in range(8):
            ticket=self.h.prepare_tx()
            if not ticket: break
            self.accept(self.d.rx(ticket.encoded,t),t)
            assert not self.h.written(ticket,len(ticket.encoded),t)
            self.accept(self.d.drain(t),t)
        else: raise AssertionError('exchange bound')
    def capture(self):
        self.accept(self.d.op('CAPTURE 100 1'),100)
        assert self.window and self.window.complete,self.window
        assert self.h.snapshot().terminal.kind is EventKind.WINDOW_END_ADMITTED
        self.receipt=WindowReceipt.from_window(self.context,self.window)
        assert not [e for e in self.events if e.kind is EventKind.RECOVERY]
    def finish(self):
        r={'host_snapshot':clean(self.h.snapshot()),'events':clean(self.events),'frames':compact(self.frames),
           'window_complete':bool(self.window and self.window.complete),'sample_count':len(self.window.samples) if self.window else 0,
           'device':self.d.op('STATE')}
        self.d.close();return r

def host_native_cases():
    results={}
    p=Pair('timeout-recovery');p.capture()
    assert p.accept(p.d.drain(5099),5099)==[]
    events=p.accept(p.d.drain(5100),5100)
    assert len(events)==1 and events[0].kind is EventKind.RECOVERY and events[0].reason.value=='DEVICE_ERROR' and events[0].reset_transport and events[0].preserve_fragments
    blocked={}
    for label,call in [('stop',lambda:p.h.stop(5101)),('release',lambda:p.h.release(p.receipt,5101))]:
        try: call()
        except SessionError as exc: blocked[label]=exc.code.value
        else: raise AssertionError(label+' unexpectedly accepted')
    pre=clean(p.h.snapshot());dev=p.d.op('STATE')
    assert dev['ready'] and dev['session_active'] and dev['tombstone'] and not dev['armed'] and dev['starts']==1
    # Reset only this synthetic byte decoder; no claim of a real port reset.
    p.decoder.reset();p.h.start(12,5102);p.exchange(5102)
    assert p.h.snapshot().phase is Phase.READY
    results['timeout']=p.finish();results['timeout'].update(error_snapshot=pre,blocked_commands=blocked,device_after_error=dev,synthetic_fresh_hello_recovered=True)
    p=Pair('release-exact');p.capture();p.h.release(p.receipt,5100);p.exchange(5100)
    assert p.h.snapshot().phase is Phase.RECOVERY
    assert not any(e.kind is EventKind.WINDOW_RELEASED for e in p.events)
    tail=p.frames[-2:];assert [f.message_type for f in tail]==[134,135] and tail[0].payload[:2]==bytes([5,3])
    results['release_exact']=p.finish();assert results['release_exact']['device']['starts']==1
    p=Pair('stop-exact');p.capture();p.h.stop(5100);p.exchange(5100)
    assert p.h.snapshot().phase is Phase.READY
    assert not any(e.kind is EventKind.RECOVERY for e in p.events)
    assert not any(f.message_type==134 and f.payload[:2]==bytes([5,3]) for f in p.frames)
    results['stop_exact']=p.finish()
    p=Pair('stop-partial-error');p.capture()
    p.accept(p.d.op('POLL 5100 64 1'),5100)
    partial=p.d.op('POLL 5100 1 1');assert len(bytes.fromhex(partial['wire']))==1
    p.accept(partial,5100);p.h.stop(5100);p.exchange(5100)
    assert p.h.snapshot().phase is Phase.RECOVERY
    assert any(f.message_type==133 for f in p.frames)
    assert not any(e.kind is EventKind.STOPPED for e in p.events)
    results['stop_partial_error']=p.finish()
    return results

class Emulated:
    def __init__(self):
        self.d=DeviceEmulator(all_analog_modes=True);self.sequence=2;self.log=[]
        self.send(Frame(1,0,1,1,struct.pack('<Q',11)),0);self.drain(0)
        self.command(3,Config().payload(),0);self.drain(0)
        self.command(4,struct.pack('<I',1),0);self.drain(0)
        assert self.d.trigger(100)
    def send(self,f,t):
        b=encode_frame(f);r=self.d.write(b,t);assert r.accepted_prefix==len(b)
        self.log.append({'time_ms':t,'frame':clean(f),'encoded_hex':b.hex(),'accepted_prefix':r.accepted_prefix})
        return r
    def command(self,kind,payload,t):
        f=Frame(kind,11,self.sequence,self.sequence,payload);self.sequence+=1;return self.send(f,t)
    def drain(self,t):
        data=b''
        for _ in range(16):
            b=self.d.read(4096,t);data+=b
            if not b:return data
        raise AssertionError('drain bound')
    def release(self,t):
        s=self.d.snapshot();x=s.tombstone
        return self.command(7,struct.pack('<III',x.generation,x.window_id,x.sample_crc32),t)
    def start_end_prefix(self):
        data=b''
        for _ in range(10000):
            data+=self.d.read(1,100)
            if self.d.snapshot().terminal is not None:
                s=self.d.snapshot();assert s.tx_offset==1 and s.hold_started_ms is None;return data
        raise AssertionError('END prefix bound')

def emulator_cases():
    results={}
    e=Emulated();e.drain(100);assert e.d.snapshot().hold_started_ms==100
    e.d.tick(5099);assert e.d.snapshot().state==6
    e.d.tick(5100);assert (e.d.snapshot().state,e.d.snapshot().reason)==(5,3)
    e.release(5100);frames=decode(e.drain(5100));assert [f.message_type for f in frames]==[134,135]
    e.release(5101);assert [f.message_type for f in decode(e.drain(5101))]==[135]
    e.command(3,Config().payload(),5102);e.command(4,struct.pack('<I',1),5102)
    busy=decode(e.drain(5102));assert all(f.message_type==255 and f.payload[1]==2 for f in busy) and len(busy)==2
    assert e.d.snapshot().state==5 and not e.d.snapshot().continuation
    results['boundary_exact_release_tombstone_busy']={'state':clean(e.d.snapshot()),'frames':compact(frames),'busy_frames':compact(busy)}
    e=Emulated();prefix=e.start_end_prefix();rest=e.drain(1099)
    assert e.d.snapshot().hold_started_ms==1099
    e.d.tick(5100);assert e.d.snapshot().state==6
    e.d.tick(6098);assert e.d.snapshot().state==6
    e.d.tick(6099);assert (e.d.snapshot().state,e.d.snapshot().reason)==(5,3)
    results['delayed_end_anchor']={'state':clean(e.d.snapshot()),'prefix_bytes':len(prefix),'remainder_bytes':len(rest)}
    for stale in (False,True):
        e=Emulated();e.drain(100)
        e.command(5,struct.pack('<I',2 if stale else 3),5100)
        frames=decode(e.drain(5100));snap=e.d.snapshot()
        if stale:
            assert [(f.message_type,f.payload[:2].hex()) for f in frames]==[(134,'0503'),(255,'0504')]
            assert snap.state==5 and snap.held_samples
        else:
            assert [f.message_type for f in frames]==[133,134] and snap.state==0
            assert not any(f.message_type==147 for f in frames)
        results['stale_stop' if stale else 'valid_stop']={'state':clean(snap),'frames':compact(frames)}
    results['premature_release_during_partial_end']=emulator_premature_release()
    return results

def emulator_premature_release():
    e=Emulated();prefix=e.start_end_prefix();boundary=clean(e.d.snapshot())
    e.release(1099);rest=e.drain(1099);frames=decode(prefix+rest);snap=e.d.snapshot()
    assert boundary['tx_offset']==1 and boundary['hold_started_ms'] is None
    assert snap.state==2 and snap.continuation and not snap.held_samples
    assert sum(f.message_type==135 for f in frames)==1
    return {'state':clean(snap),'frames':compact(frames),'boundary':boundary,
        'commands':e.log,'accepted_prefix_hex':prefix.hex(),'accepted_remainder_hex':rest.hex(),
        'release_ack':sum(f.message_type==135 for f in frames),'busy_error':sum(f.message_type==255 and f.payload[:2]==bytes([7,2]) for f in frames)}

def native_premature_release():
    d=Native('premature-release-partial-end')
    for frame in (Frame(1,0,1,1,struct.pack('<Q',11)),Frame(3,11,2,2,Config().payload()),Frame(4,11,3,3,struct.pack('<I',1))):
        d.rx(encode_frame(frame),0);d.drain(0)
    d.op('CAPTURE 100 0');r=d.op('ENDPREFIX');prefix=bytes.fromhex(r['wire']);boundary=dict(r)
    assert r['tombstone'] and r['anchor']==0
    frames=decode(prefix);data=b''.join(f.payload[12:] for f in frames if f.message_type==145)
    assert len(data)==4096
    frame=Frame(7,11,4,4,struct.pack('<III',3,1,zlib.crc32(data)))
    d.rx(encode_frame(frame),1099);r=d.drain(1099);frames=decode(prefix+bytes.fromhex(r['wire']))
    assert r['phase']==5 and r['anchor']==1099 and r['starts']==1
    assert sum(f.message_type==146 for f in frames)==1
    assert not any(f.message_type==135 for f in frames)
    assert any(f.message_type==255 and f.payload[:2]==bytes([7,2]) for f in frames)
    before=d.drain(5100);assert before['phase']==5 and before['starts']==1
    expired=d.drain(6099);assert expired['phase']==8 and expired['starts']==1
    result={'frames':compact(frames),'boundary':boundary,'release_command':clean(frame),'encoded_release_hex':encode_frame(frame).hex(),
            'after_end':r,'at_first_byte_plus5000':before,'at_final_byte_plus5000':expired,
            'release_ack':0,'busy_error':1,'scope':'Adversarial raw command with CRC computed from DATA, before full END; not a receipt emitted by B SessionController.'}
    d.close();return result

def main():
    result={'scope':'Read-only compatibility experiments; not Critic-B acceptance','parser':parser_replay(),
            'host_native':host_native_cases(),'emulator':emulator_cases(),'native_premature_release':native_premature_release()}
    (ROOT/'results/audit.json').write_text(json.dumps(clean(result),indent=2)+'\n')
    print('PASS: 78 A streams x 4 chunk sizes; 4 public-API native A/B session experiments; 5 emulator scenarios.')
    print('Host ERROR => RECOVERY/reset_transport=True; exact-deadline RELEASE_ACK ignored after ERROR; exact Stop succeeds; partially started ERROR beats Stop in host.')
    print('Premature RELEASE with one END byte: B ACK + WAITING; A BUSY + WAIT_RELEASE, then ERROR at final-END+5000.')

if __name__=='__main__':main()

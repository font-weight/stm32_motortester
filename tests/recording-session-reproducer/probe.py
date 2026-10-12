"""Exact native software bridge to production recorder through A-owned adapter."""
import argparse
import importlib.util
import json
import hashlib
import struct
from dataclasses import asdict
from pathlib import Path
import shutil
import sys

ap=argparse.ArgumentParser()
ap.add_argument('--host-root',type=Path,required=True)
ap.add_argument('--bridge',type=Path,required=True)
ap.add_argument('--out',type=Path,required=True)
a=ap.parse_args()
if not sys.flags.isolated or not sys.dont_write_bytecode: raise ValueError('Use -I -B')
a.out.mkdir(exist_ok=False)
sys.path.insert(0,str(a.host_root))
spec=importlib.util.spec_from_file_location('bridge_adapter',Path(__file__).with_name('adapter.py'))
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)
spec2=importlib.util.spec_from_file_location('recorder_adapter',Path(__file__).with_name('recorder_adapter.py'))
T=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(T)
from motortester.wire import StreamDecoder

class NativeLink(A.Link):
    def __init__(self,native,chunk):
        super().__init__(native);self.chunk=chunk;self.txcap=64;self.autopoll=True
    def write(self,data):
        data=bytes(data[:self.chunk]);self.writes.append(data)
        d=self.op('RX %d %s'%(self.now,data.hex()))
        A.require(d['result']==0,'native RX error');return len(data)
    def read(self,size):
        if self.autopoll:self.op('POLL %d %d 1'%(self.now,self.txcap))
        return super().read(size)
    def poll(self,cap=64,count=1):return self.op('POLL %d %d %d'%(self.now,cap,count))

def run(name,mode,chunk,stop_at=None, ack_split=False, bad_receipt=False):
    out=a.out/name;out.mkdir();native=A.Native(a.bridge,out)
    link=NativeLink(native,chunk);result={'status':'RUNNING','mode':mode,'chunk':chunk,'stop_at':stop_at,'physical_verified':False}
    rig=None
    try:
        rig=T.Rig(link,mode,out)
        rig.start();captures=[];generations=[];items=[]
        rounds=1 if mode==2 or stop_at else 3
        for number in range(1,rounds+1):
            generations.append(rig.runtime.snapshot().generation)
            link.now+=100
            if stop_at=='waiting': pass
            else:link.op('SAMPLES %d %d %d %d'%(link.now,mode,number,1 if stop_at=='no_begin' else 2048))
            if stop_at in ('begin','data','end_prefix'):
                link.autopoll=False
                decoder=StreamDecoder();seen=[]
                for _ in range(10000):
                    d=link.poll(1 if stop_at=='end_prefix' else 64)
                    batch=decoder.feed(bytes.fromhex(d['wire']),link.now)
                    seen.extend(f.message_type for f in batch.frames)
                    # END is type146; stop after first END byte, before delimiter.
                    if stop_at=='begin' and 144 in seen:break
                    if stop_at=='data' and 145 in seen:break
                    if stop_at=='end_prefix' and d['head_type']==146 and d['offset']>0:break
                else:raise ValueError('boundary not reached '+stop_at)
                # Deliver only bytes already accepted from native TX.
                rig.pump();result['boundary']=d
            elif not stop_at:
                rig.until(lambda:rig.runtime.pending_capture is not None)
                captures.append(rig.runtime.pending_capture)
                A.require(not any(i.kind in (T.K.PERSIST,T.K.ACK_LOCAL,T.K.ARM) for i in rig.controller.intents),'premature storage/ACK/ARM')
            if stop_at:rig.controller.stop(now_ms=link.now)
            link.autopoll=True
            if ack_split:
                link.txcap=1;rig.command(T.K.STOP)
                rig.until(lambda:link.states[-1]['head_type']==133 and link.states[-1]['offset']==link.states[-1]['tx_length']-1)
                A.require(rig.controller.snapshot().stop_evidence!='stop_ack','ACK recognized before last byte')
                A.require(not any(i.kind in (T.K.PERSIST,T.K.ACK_LOCAL,T.K.ARM) for i in rig.controller.intents),'partial ACK released ownership')
                rig.log('ack_last_byte_held')
                rig.pump()
                A.require(rig.controller.snapshot().stop_evidence=='stop_ack','complete ACK not recognized')
                rig.intent(T.K.PERSIST);rig.log('persist_before_status');link.txcap=64
            else:rig.settle_stop()
            A.require(not link.states[-1]['hw'] and not link.states[-1]['armed'],'native not stopped')
            before=link.states[-1]['starts']
            # The trailing STATUS is observational. Storage becomes eligible on ACK;
            # continue servicing transport while a model disk stall exceeds hold time.
            has_pending=bool(rig.controller.snapshot().pending)
            if has_pending:rig.intent(T.K.PERSIST)
            for _ in range(6):
                rig.pump();link.now+=900
            rig.pump()
            A.require(link.states[-1]['starts']==before and not link.states[-1]['hw'],'disk delay rearmed native')
            A.require(not any(i.kind in (T.K.ACK_LOCAL,T.K.ARM) for i in rig.controller.intents),'receipt missing but ACK/ARM offered')
            if has_pending:
                if bad_receipt:
                    intent=rig.intent(T.K.PERSIST);rig.claim(intent)
                    source=rig.runtime.pending_capture
                    A.require(not rig.controller.committed(intent,object(),now_ms=link.now),'bad receipt accepted')
                    A.require(rig.runtime.pending_capture is source,'bad receipt consumed source')
                    A.require(not any(i.kind in (T.K.ACK_LOCAL,T.K.ARM) for i in rig.controller.intents),'bad receipt allowed ACK/ARM')
                    rig.log('invalid_receipt_rejected')
                    result.update(status='EXPECTED_NEGATIVE_PASS',command_types=[f.message_type for f in rig.frames()])
                    A.require(7 not in result['command_types'] and result['command_types'].count(4)==1,'negative rearmed')
                    result['native_exit']=native.close()
                    return {'name':name,'status':result['status']}
                items.append(rig.intent(T.K.PERSIST).payload.result.item)
                rig.persist(blocked=not stop_at and number==1);rig.ack()
            if number<rounds:
                rig.command(T.K.ARM);rig.until(lambda:rig.runtime.snapshot().phase is T.Phase.ARMED)
        if not stop_at and mode!=2:
            old=rig.intent(T.K.ARM);rig.controller.stop(now_ms=link.now)
            A.require(not rig.controller.admit(old,now_ms=link.now,runtime_state=rig.runtime.snapshot()),'stale ARM admitted')
        report=rig.finish();path=out/'recording.mtrec';T.finalize(report,path);loaded=T.load(path)
        A.require(loaded.entries==report.entries and loaded.outcome==report.outcome,'archive metadata readback')
        expected=[]
        for ordinal,item in enumerate(items):
            data=loaded.read_segment(ordinal)
            if type(item) is T.FullSegment:
                A.require(data==T.encode(item.capture),'full original payload')
            elif type(item) is T.PartialSegment:
                raw=b''.join(struct.pack('<%dH'%len(f.samples),*f.samples) for f in item.fragments)
                A.require(data==raw,'partial original fragments')
                for fragment in item.fragments:
                    for offset,code in enumerate(fragment.samples,fragment.offset):
                        expected_code=3000 if offset==0 else 0 if offset==511 else 4095 if offset==1023 else (73*offset+19)&4095
                        A.require(code==expected_code,'partial synthetic source mismatch')
                entry=loaded.entries[ordinal]
                A.require(entry.partial.missing_ranges==item.missing_ranges,'partial gap metadata')
            else:
                body=json.loads(data)
                A.require(body==dict(kind='no_begin_abort',reason=item.reason,sequence=item.sequence,provenance=asdict(item.provenance)),'no-BEGIN event metadata')
                A.require(loaded.entries[ordinal].sample_count is None,'abort fabricated samples')
            expected.append(dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
        A.save(out/'expected.json',dict(entries=report.entries,outcome=report.outcome,members=expected))
        if captures:
            A.require([loaded.read_segment(i) for i in range(len(captures))]==[T.encode(c) for c in captures],'raw capture roundtrip')
            for number,c in enumerate(captures,1):
                expected=[(73*i+19*number)&4095 for i in range(2048)]
                if mode:expected[0]=3000
                expected[511]=0;expected[1023]=4095
                A.require(c.samples==tuple(expected),'native samples changed')
        frames=rig.frames();types=[f.message_type for f in frames]
        A.require(7 not in types,'RELEASE emitted');A.require(types.count(4)==rounds,'ARM count');A.require(types.count(5)==rounds,'STOP count')
        A.require(generations==sorted(set(generations)),'generation reused')
        A.require(report.outcome.stop_evidence=='stop_ack','wrong Stop evidence')
        expected_entries=0 if stop_at=='waiting' else rounds
        A.require(len(report.entries)==expected_entries,'terminal result loss')
        result.update(status='PASS',command_types=types,generations=generations,entries=report.entries,outcome=report.outcome,native_exit=native.close())
    except BaseException as error:
        result.update(status='FAILED',error=type(error).__name__+': '+str(error));raise
    finally:
        native.dispose()
        if rig:
            A.save(out/'api.json',rig.api)
            if rig.spool:rig.spool.close()
        A.save(out/'result.json',result);A.save(out/'native.json',native.log)
        A.save(out/'transport.json',{'writes':link.writes,'reads':link.reads,'states':link.states})
        (out/'host.wire').write_bytes(b''.join(link.writes));(out/'device.wire').write_bytes(link.wire)
    return {'name':name,'status':result['status']}

results=[]
try:
    for chunk in (64,1):
        for mode in (0,1,2):results.append(run('mode%d-chunk%d'%(mode,chunk),mode,chunk))
    results.append(run('ack-last-byte',1,64,ack_split=True))
    results.append(run('bad-receipt',1,64,bad_receipt=True))
    for boundary in ('waiting','no_begin','begin','data','end_prefix'):
        results.append(run('stop-'+boundary,1,1,boundary))
finally:A.save(a.out/'summary.json',results)
print(json.dumps(results))

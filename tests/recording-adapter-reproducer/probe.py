"""Native byte transport through the exact production PR154 recording adapter."""
import argparse
from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import threading
import time
from unittest.mock import patch
from uuid import UUID
import zlib

p=argparse.ArgumentParser();p.add_argument('--host-root',type=Path,required=True);p.add_argument('--bridge',type=Path,required=True);p.add_argument('--ipc-helper',type=Path,required=True);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
if not sys.flags.isolated or not sys.dont_write_bytecode:raise ValueError('Use -I -B')
args.out.mkdir(exist_ok=False);sys.path.insert(0,str(args.host_root))
spec=importlib.util.spec_from_file_location('native_ipc',args.ipc_helper);A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)
from motortester.recording_bridge import RecordingSessionAdapter
from motortester.runtime import AcquisitionRuntime
from motortester.session import Config, Phase, EventKind
from motortester import session_archive as S
from motortester.recording import decode
require=A.require

class Link(A.Link):
    def __init__(self,native,chunk):super().__init__(native);self.chunk=chunk;self.auto=True
    def write(self,data):
        data=bytes(data[:self.chunk]);self.writes.append(data);d=self.op('RX %d %s'%(self.now,data.hex()));require(d['result']==0,'native RX rejected');return len(data)
    def read(self,size):
        if self.auto:self.op('POLL %d 64 1'%self.now)
        return super().read(size)

# Independent artifact decoder: no candidate/B wire decoder is imported.
def frames(wire):
    require(wire.endswith(b'\0'),'incomplete wire');result=[]
    for packet in wire.split(b'\0')[:-1]:
        raw=bytearray();at=0
        while at<len(packet):
            code=packet[at];at+=1;require(code and at+code-1<=len(packet),'COBS bounds')
            raw.extend(packet[at:at+code-1]);at+=code-1
            if code<255 and at<len(packet):raw.append(0)
        require(len(raw)>=28,'short frame')
        major,minor,kind,flags,size,reserved,nonce,seq,request=struct.unpack_from('<BBBBHHQII',raw)
        require((major,minor,flags,reserved)==(0,1,0,0) and len(raw)==28+size,'header')
        require(zlib.crc32(raw[:-4])==int.from_bytes(raw[-4:],'little'),'wire CRC')
        result.append(dict(kind=kind,nonce=nonce,seq=seq,request=request,payload=bytes(raw[24:-4])))
    return result

def audit(path,wire,commands,source_times):
    archive=S.load(path);incoming=frames(wire);outgoing=frames(commands)
    require(not any(f['kind']==7 for f in outgoing),'wire RELEASE')
    expected=[]
    for ordinal,e in enumerate(archive.entries):
        q=e.provenance;require(q.wire_session_nonce==101,'nonce')
        require(q.host_elapsed_ms==source_times[str(q.generation)+':'+str(e.sequence)],'metadata time changed after source observation')
        terminal=[f for f in incoming if f['kind'] in (146,147) and struct.unpack_from('<II',f['payload'])==(q.generation,e.sequence)]
        require(len(terminal)==1,'unique terminal');terminal=terminal[0]
        require((q.terminal_kind,q.terminal_request_id,q.terminal_sequence)==('end' if terminal['kind']==146 else 'abort',terminal['request'],terminal['seq']),'terminal identities')
        begins=[f for f in incoming if f['kind']==144 and struct.unpack_from('<II',f['payload'])==(q.generation,e.sequence)]
        acks=[f for f in incoming if f['kind']==131 and int.from_bytes(f['payload'][:4],'little')==q.config_id]
        require(len(acks)==1 and acks[0]['payload'][4:].hex()==q.config_payload_hex,'CONFIG_ACK identity')
        config=[f for f in outgoing if f['kind']==3 and f['request']==acks[0]['request']]
        require(len(config)==1 and config[0]['payload'].hex()==q.config_payload_hex,'exact configuration')
        data=archive.read_segment(ordinal)
        if e.kind=='abort':
            require(not begins and q.begin_request_id is None and q.begin_sequence is None and q.unavailable_reason=='no_begin','no-BEGIN identity')
            require(e.sample_count is None and e.partial is None and q.end_samples_crc32 is None,'fabricated abort data')
        else:
            require(len(begins)==1,'unique BEGIN');begin=begins[0]
            require((q.begin_request_id,q.begin_sequence,q.unavailable_reason)==(begin['request'],begin['seq'],None),'BEGIN identities')
            b=struct.unpack('<IIIHHHHBBBBHHIHHI',begin['payload'])
            require(b[2]==q.config_id,'BEGIN config')
            blocks=[f for f in incoming if f['kind']==145 and struct.unpack_from('<II',f['payload'])==(q.generation,e.sequence)]
            samples=[];ranges=[]
            for f in blocks:
                _,_,offset,count=struct.unpack_from('<IIHH',f['payload']);require(len(f['payload'])==12+2*count,'DATA size')
                ranges.append((offset,count));samples.extend(struct.unpack_from('<%dH'%count,f['payload'],12))
            for offset,code in enumerate(samples):
                original=3000 if q.acquisition_mode!=0 and offset==0 else 0 if offset==511 else 4095 if offset==1023 else (73*offset+19*e.sequence)&4095
                require(code==original,'native original code')
            if e.kind=='capture':
                capture=decode(data);require(capture.samples==tuple(samples) and len(samples)==2048,'full samples')
                require(capture.trigger_kind==('auto_timeout' if q.acquisition_mode==0 else 'signal'),'trigger kind')
                require(capture.trigger_index==(None if q.acquisition_mode==0 else 0),'trigger index')
                crc=int.from_bytes(terminal['payload'][12:16],'little')
                require(q.end_samples_crc32==crc==zlib.crc32(struct.pack('<2048H',*samples)) and q.abort_reason_code is None,'END CRC')
            else:
                require(data==struct.pack('<%dH'%len(samples),*samples),'partial samples')
                require(e.partial.fragments==tuple(ranges),'partial offsets')
                require(e.partial.missing_ranges==((len(samples),2048),),'partial complement')
                nominal=b[13]/((b[14]+1)*(b[15]+1))
                require(e.partial.nominal_fs_hz==nominal and q.end_samples_crc32 is None,'partial nominal Fs')
        if terminal['kind']==147:require(q.abort_reason_code==int.from_bytes(terminal['payload'][8:10],'little'),'ABORT reason')
        expected.append(dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
    return dict(identity=archive.identity,entries=archive.entries,outcome=archive.outcome,members=expected)

class Rig:
    def __init__(self,out,chunk):
        self.out=out;out.mkdir();self.native=A.Native(args.bridge,out);self.link=Link(self.native,chunk)
        self.lock=threading.RLock();self.events=[];self.sources=[];self.facts=[];self.seen=set();self.adapter=None;self.started=0;self.source_times={}
        self.runtime=AcquisitionRuntime(self.link,origin='simulator',clock_ms=lambda:self.link.now,utc_now=lambda:'2026-10-12T21:00:00Z')
        self.runtime.start(101);self.until(lambda:self.runtime.snapshot().phase is Phase.READY)
        self.adapter=RecordingSessionAdapter(self.runtime,clock_ms=lambda:self.link.now)
    def pump(self):
        events=self.runtime.pump()
        for source in (self.runtime.pending_capture,self.runtime.incomplete_result):
            if source is not None and id(source) not in self.seen:
                facts=self.runtime.result_provenance(source);require(facts is not None,'missing retained facts')
                require(self.runtime.result_provenance(replace(source)) is None,'foreign source accepted')
                self.seen.add(id(source));self.sources.append(source);self.facts.append(facts)
                key=str(facts.generation)+':'+str(int.from_bytes(facts.begin_frame.payload[4:8],'little'))
                self.source_times[key]=self.link.now-self.started
        for event in events:
            if event.kind is EventKind.WINDOW_ABORT:
                self.source_times.setdefault(str(event.generation)+':'+str(event.window_id),self.link.now-self.started)
        with self.lock:
            if self.adapter:self.adapter.observe(events)
        self.events.append(dict(now=self.link.now,events=events,phase=self.runtime.snapshot().phase,generation=self.runtime.snapshot().generation,host_bytes=sum(map(len,self.link.writes)),device_bytes=len(self.link.wire),state=self.adapter.state if self.adapter else None))
        time.sleep(.001)
    def until(self,predicate):
        deadline=time.monotonic()+10
        for _ in range(10000):
            if predicate():return
            require(time.monotonic()<deadline,'wait timeout');self.pump()
        raise ValueError('pump bound')
    def begin(self,index,mode):
        snap=self.runtime.snapshot();identity=S.SessionIdentity(str(UUID(int=1000+index)),str(UUID(int=snap.session)),'simulator',str(snap.capabilities.firmware_build),f'draft-0.1@{snap.source_sha}','0.1.0')
        self.config=Config(mode=mode,level_code=2048+100*index,auto_timeout_ms=100 if mode==0 else 0)
        self.started=self.link.now
        with self.lock:require(self.adapter.record(identity,self.config,self.out/('spool-%d'%index)),'record admission')
        self.until(lambda:self.runtime.snapshot().phase is Phase.ARMED)
        for old in self.sources:require(self.runtime.result_provenance(old) is None,'previous source leaked into new generation')
    def stop(self):
        with self.lock:require(self.adapter.stop(),'Stop rejected')
    def samples(self,mode,number,count=2048):
        self.link.now+=100;self.link.op('SAMPLES %d %d %d %d'%(self.link.now,mode,number,count))
    def save(self,index,kinds):
        self.until(lambda:self.adapter.state.ready_to_save)
        token=object();path=self.out/('recording-%d.mtrec'%index)
        with self.lock:require(self.adapter.save(path,operation=token),'Save rejected')
        self.until(lambda:self.adapter.save_completion is not None and self.adapter.save_completion.operation is token)
        require(self.adapter.save_completion.error is None and self.adapter.save_completion.path==str(path),'Save completion')
        expected=audit(path,bytes(self.link.wire),b''.join(self.link.writes),self.source_times)
        require([e.kind for e in expected['entries']]==kinds,'unexpected terminal archive kinds')
        require(expected['outcome'].stop_evidence=='stop_ack','Stop not proven at protocol level')
        require(expected['outcome'].reason==('single_complete' if self.config.mode==2 else 'user_stop'),'wrong termination')
        A.save(self.out/('expected-%d.json'%index),expected)
        for source in self.sources:require(self.runtime.result_provenance(source) is None,'ACK retained stale facts')
    def close(self):
        if self.adapter:
            with self.lock:self.adapter.observe(self.runtime.disconnect());self.adapter.retire()
            self.adapter._writer._thread.join(5);require(not self.adapter._writer.running,'writer did not retire')
        self.native.dispose();A.save(self.out/'api.json',self.events);A.save(self.out/'native.json',self.native.log)
        A.save(self.out/'facts.json',self.facts);A.save(self.out/'source-times.json',self.source_times);(self.out/'host.wire').write_bytes(b''.join(self.link.writes));(self.out/'device.wire').write_bytes(self.link.wire)

def held_persist(rig,stop):
    original=S._sync;entered=threading.Event();release=threading.Event();owner=threading.get_ident();thread_ids=[]
    def delayed(stream):
        thread_ids.append(threading.get_ident())
        require(thread_ids[-1]!=owner and thread_ids[-1]==rig.adapter._writer._thread.ident,'sync on wrong thread')
        entered.set();require(release.wait(10),'sync barrier timed out');return original(stream)
    with patch.object(S,'_sync',delayed):
        try:
            rig.until(entered.is_set)
            require(rig.adapter.state.snapshot.stop_evidence=='stop_ack','writer before Stop ACK')
            source=rig.runtime.pending_capture;facts=rig.runtime.result_provenance(source)
            require(source is not None and facts is not None,'writer source missing')
            before=rig.link.states[-1]['starts']
            if stop:rig.stop()
            for _ in range(6):rig.pump();rig.link.now+=900
            rig.pump()
            require(rig.runtime.pending_capture is source and rig.runtime.result_provenance(source) is facts,'blocked source facts changed')
            require(rig.link.states[-1]['starts']==before and not rig.link.states[-1]['hw'],'blocked writer restarted native')
            require(rig.adapter.state.writer_busy,'writer not blocked')
        finally:
            release.set()
            rig.events.append(dict(kind='writer_barrier',owner_thread=owner,writer_threads=thread_ids,model_delay_ms=5400,user_stop=stop))

results=[]
try:
    # One actual adapter/runtime records, saves, then records again with new config.
    r=Rig(args.out/'cross-generation',1)
    try:
        r.begin(1,1);r.samples(1,1);held_persist(r,True);r.save(1,['capture'])
        r.begin(2,1);r.samples(1,2);r.link.auto=False
        for _ in range(1000):
            d=r.link.op('POLL %d 64 1'%r.link.now)
            if d['phase']==2 and d['offset']==0 and any(f['kind']==145 and int.from_bytes(f['payload'][:4],'little')==r.runtime.snapshot().generation for f in frames(bytes(r.link.wire[:r.link.wire.rfind(0)+1]))):break
        else:raise ValueError('partial cut not reached')
        r.pump();r.stop();r.link.auto=True;r.save(2,['partial'])
        r.begin(3,1);r.samples(1,3,1);r.stop();r.save(3,['abort'])
        results.append(dict(case='cross-generation',status='PASS',archives=3))
    finally:r.close()
    for name,mode,number_windows in [('auto',0,3),('single',2,1)]:
        r=Rig(args.out/name,64)
        try:
            r.begin(1,mode)
            for number in range(1,number_windows+1):
                previous=r.runtime.snapshot().generation;r.samples(mode,number)
                if number==1:held_persist(r,False)
                if number<number_windows:r.until(lambda:r.runtime.snapshot().phase is Phase.ARMED and r.runtime.snapshot().generation!=previous)
                elif mode!=2:
                    r.until(lambda:r.runtime.pending_capture is not None);r.stop()
            r.save(1,['capture']*number_windows);results.append(dict(case=name,status='PASS',archives=1))
        finally:r.close()
finally:A.save(args.out/'summary.json',results)
print(json.dumps(results))

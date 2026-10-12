"""Synchronous probe adapter: real recorder intents, native runtime and durable spool."""
from dataclasses import replace
from pathlib import Path
from uuid import UUID
import threading
from unittest.mock import patch
from motortester import session_archive as archive
from motortester.model import Capture
from motortester.recording import encode
from motortester.recording_controller import IntentKind as K, RecordingController, RecordingResult
from motortester.runtime import AcquisitionRuntime
from motortester.session import Config, Phase, Event, EventKind
from motortester.session_archive import (SessionIdentity, SessionSpool, SegmentProvenance,
    FullSegment, PartialSegment, AbortEvent, SampleFragment, finalize, load)
from motortester.window import WindowResult
from motortester.wire import StreamDecoder

def require(ok,message):
    if not ok:raise ValueError(message)

class Rig:
    def __init__(self,link,mode,folder):
        self.link=link;self.folder=folder;self.api=[];self.spool=None
        self.runtime=AcquisitionRuntime(link,origin='simulator',clock_ms=lambda:link.now,
            utc_now=lambda:'2026-10-12T20:00:00Z')
        self.runtime.start(101)
        for _ in range(500):
            self.runtime.pump()
            if self.runtime.snapshot().phase is Phase.READY:break
        require(self.runtime.snapshot().phase is Phase.READY,'handshake incomplete')
        self.config=Config(mode=mode,auto_timeout_ms=100 if mode==0 else 0)
        s=self.runtime.snapshot()
        self.identity=SessionIdentity(str(UUID(int=1001)),str(UUID(int=s.session)),
            'simulator',str(s.capabilities.firmware_build),f'draft-0.1@{s.source_sha}','0.1.0')
        self.controller=RecordingController()
        self.token=self.controller.record(self.identity,self.config,s,now_ms=link.now)
    def log(self,kind,**values):
        self.api.append(dict(kind=kind,time=self.link.now,wire_bytes=len(self.link.wire),
            host_bytes=sum(map(len,self.link.writes)),**values))
    def intent(self,kind):
        found=[i for i in self.controller.intents if i.kind is kind]
        require(len(found)==1,'expected exact intent '+str(kind));return found[0]
    def claim(self,intent):
        require(self.controller.admit(intent,now_ms=self.link.now,runtime_state=self.runtime.snapshot()),'intent rejected')
        self.log('admit',intent=intent.kind)
    def command(self,kind):
        intent=self.intent(kind);self.claim(intent)
        events=({K.CONFIGURE:lambda:self.runtime.configure(intent.payload),K.ARM:self.runtime.arm,K.STOP:self.runtime.stop}[kind])()
        self.controller.command_submitted(intent,self.runtime.snapshot(),now_ms=self.link.now)
        self.log('command_submitted',intent=kind);self.observe(events)
    def provenance(self):
        s=self.runtime.snapshot()
        return SegmentProvenance(generation=s.generation,config_id=s.config_id,
            acquisition_mode=self.config.mode,auto_timeout_ms=self.config.auto_timeout_ms,
            host_elapsed_ms=self.link.now,wire_version='0.1',start_operation='arm',
            wire_session_nonce=s.session,config_payload_hex=self.config.payload().hex())
    def available(self):
        results=[];capture=self.runtime.pending_capture;partial=self.runtime.incomplete_result
        if capture is not None:results.append(RecordingResult(FullSegment(capture,self.provenance()),capture))
        if partial is not None:
            if partial.begin is None:item=AbortEvent(partial.reason.value,provenance=self.provenance())
            else:
                provenance=self.provenance()
                if partial.abort_reason is not None:provenance=replace(provenance,terminal_kind='abort',abort_reason_code=partial.abort_reason)
                item=PartialSegment(partial.begin.window_id,partial.begin.sample_count,
                    tuple(SampleFragment(f.offset,f.samples) for f in partial.fragments),partial.missing_ranges,
                    partial.reason.value,provenance,deferred_checks=partial.deferred_checks,metadata_issues=partial.metadata_issues)
            results.append(RecordingResult(item,partial))
        return tuple(results)
    def observe(self,events=()):
        results=self.available()
        self.controller.observe(self.token,self.runtime.snapshot(),events,results,now_ms=self.link.now)
        self.log('observe',events=events,phase=self.runtime.snapshot().phase,generation=self.runtime.snapshot().generation,stop_evidence=self.controller.snapshot().stop_evidence,intents=[i.kind for i in self.controller.intents])
    def pump(self):self.observe(self.runtime.pump())
    def until(self,predicate):
        for _ in range(1000):
            if predicate():return
            self.pump()
        raise ValueError('bounded pump exceeded')
    def start(self):
        i=self.intent(K.PREPARE);self.claim(i)
        self.spool=SessionSpool.create(self.folder/'record.spool',self.identity)
        require(self.controller.spool_prepared(i,issuer=self.spool.receipt_issuer,capacity=self.spool.capacity,now_ms=self.link.now),'prepare completion')
        self.command(K.CONFIGURE);self.until(lambda:any(i.kind is K.ARM for i in self.controller.intents))
        self.command(K.ARM);self.until(lambda:self.runtime.snapshot().phase is Phase.ARMED)
    def settle_stop(self):
        self.command(K.STOP);self.until(lambda:self.controller.snapshot().stop_evidence=='stop_ack')
        self.log('matching_stop_ack')
    def persist(self, blocked=False):
        i=self.intent(K.PERSIST);self.claim(i);item=i.payload.result.item
        self.log('persist_begin',item=item)
        def append():
            if type(item) is FullSegment:return self.spool.append_full(item)
            if type(item) is PartialSegment:return self.spool.append_partial(item)
            return self.spool.append_abort(item)
        if blocked:
            entered=threading.Event();release=threading.Event();values=[];errors=[]
            original=archive._sync
            def delayed(stream):
                entered.set()
                require(release.wait(10),'writer barrier timeout')
                return original(stream)
            def worker():
                try:values.append(append())
                except BaseException as error:errors.append(error)
            starts=self.link.states[-1]['starts'];source=i.payload.result.source
            with patch.object(archive,'_sync',delayed):
                thread=threading.Thread(target=worker,daemon=True);thread.start()
                try:
                    require(entered.wait(5),'writer did not reach fsync')
                    self.log('writer_blocked')
                    for _ in range(6):
                        self.pump();self.link.now+=900
                    self.pump()
                    require(not values and not errors,'writer returned before released')
                    require(self.link.states[-1]['starts']==starts and not self.link.states[-1]['hw'],'writer delay restarted native')
                    require(not any(x.kind in (K.ACK_LOCAL,K.ARM) for x in self.controller.intents),'uncommitted writer allowed ACK/ARM')
                    require(self.runtime.pending_capture is source,'blocked writer lost source')
                    self.log('writer_delay_verified',elapsed=5400)
                finally:
                    release.set();thread.join(5)
            require(not thread.is_alive() and not errors and len(values)==1,'writer completion failed')
            r=values[0]
        else:r=append()
        require(self.controller.committed(i,r,now_ms=self.link.now),'receipt rejected')
        self.log('durable_receipt',receipt=r);return i,r
    def ack(self):
        i=self.intent(K.ACK_LOCAL);self.claim(i);source=i.payload.result.source
        before=len(self.link.writes)
        if type(source) is Capture:
            require(source is self.runtime.pending_capture,'capture identity')
            require(self.runtime.acknowledge(source)==(),'local ACK emitted events')
        elif type(source) is WindowResult:require(source is self.runtime.take_incomplete(),'partial identity')
        else:
            require(type(source) is Event and source.kind is EventKind.WINDOW_ABORT,'event identity')
            require(self.runtime.pending_capture is None and self.runtime.incomplete_result is None,'event ownership')
        require(self.controller.acknowledged(i,self.runtime.snapshot(),now_ms=self.link.now),'ACK callback rejected')
        require(len(self.link.writes)==before,'local ACK wrote transport');self.log('local_ack')
    def finish(self):
        i=self.intent(K.FINALIZE_READY);self.claim(i);o=i.payload
        r=self.spool.finish(o.reason,o.stop_evidence,o.diagnostic)
        require(self.controller.finalized(i,now_ms=self.link.now),'finish rejected');self.log('finalized');return r
    def frames(self):return StreamDecoder().feed(b''.join(self.link.writes),self.link.now).frames

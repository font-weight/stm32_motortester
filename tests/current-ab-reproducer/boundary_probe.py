#!/usr/bin/env python3
"""Disposable actual PR108 runtime adapter; no replacement host parser."""
import argparse, dataclasses, enum, hashlib, importlib, json, os, selectors, subprocess, sys, time
from pathlib import Path

def clean(v):
 if dataclasses.is_dataclass(v): return clean(dataclasses.asdict(v))
 if isinstance(v,enum.Enum): return v.value
 if isinstance(v,bytes): return v.hex()
 if isinstance(v,dict): return {k:clean(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)): return [clean(x) for x in v]
 return v

def require(c,m):
 if not c: raise ValueError(m)
def save(p,v): p.write_text(json.dumps(clean(v),indent=2)+'\n')
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
class Native:
 def __init__(self,exe,out):
  self.log=[];self.stderr=(out/'native.stderr').open('wb')
  self.proc=subprocess.Popen([str(exe)],cwd=out,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr)
 def op(self,cmd):
  row={'command':cmd,'response_hex':''};self.log.append(row)
  self.proc.stdin.write((cmd+'\n').encode());self.proc.stdin.flush();deadline=time.monotonic()+5;raw=bytearray()
  with selectors.DefaultSelector() as sel:
   sel.register(self.proc.stdout,selectors.EVENT_READ)
   while b'\n' not in raw:
    left=deadline-time.monotonic();require(left>0 and sel.select(left),'native timeout')
    x=os.read(self.proc.stdout.fileno(),4096);require(x,'native EOF');raw.extend(x);row['response_hex']=raw.hex();require(len(raw)<=65536,'native oversized')
  require(raw.endswith(b'\n') and raw.count(b'\n')==1,'native framing');v=json.loads(raw);require(type(v) is dict,'native object');row['reply']=v;return v
 def close(self):
  self.proc.stdin.close();code=self.proc.wait(timeout=5);require(code==0,'native exit');return code
 def dispose(self):
  if self.proc.poll() is None:self.proc.kill()
  self.proc.wait(timeout=5)
  for p in (self.proc.stdin,self.proc.stdout):
   if p and not p.closed:p.close()
  self.stderr.close()
class Link:
 def __init__(self,native,full=False):
  self.n=native;self.full=full;self.now=0;self.rx=bytearray();self.wire=bytearray();self.writes=[];self.reads=[];self.states=[];self.limit=4096;self.stop_te=False;self.fail_read=False
  self.accept(self.n.op('INIT'))
 def accept(self,d):
  self.states.append(d)
  if self.full:self.now=d['now_ms']
  b=bytes.fromhex(d['wire']);self.rx.extend(b);self.wire.extend(b);return d
 def op(self,cmd,now=None,drain=False):
  if now is not None:self.now=now
  d=self.accept(self.n.op(cmd))
  if drain:d=self.accept(self.n.op('POLL %d 64 150'%self.now))
  return d
 def write(self,b):
  self.writes.append(b)
  if self.full:
   op='STOP_TE ' if self.stop_te else 'RX ';self.stop_te=False;self.op(op+b.hex())
  else:self.op('RX %d %s'%(self.now,b.hex()),drain=True)
  return len(b)
 def read(self,size):
  if self.fail_read:self.reads.append({'error':'modeled physical transport failure'});raise OSError('modeled reset observed')
  b=bytes(self.rx[:min(size,self.limit)]);del self.rx[:len(b)];self.reads.append(b);return b
p=argparse.ArgumentParser();p.add_argument('--host-root',type=Path,required=True);p.add_argument('--helper',type=Path,required=True);p.add_argument('--runtime-bridge',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
require(sys.flags.isolated and sys.dont_write_bytecode and not sys.flags.optimize,'require -I -B no optimization');a.out.mkdir(exist_ok=False)
sys.path.insert(0,str(a.host_root));R=importlib.import_module('motortester.runtime');S=importlib.import_module('motortester.session');W=importlib.import_module('motortester.wire');REC=importlib.import_module('motortester.recording')
for mod in (R,S,W,REC):require(Path(mod.__file__).is_relative_to(a.host_root),'source path')
inputs={str(p):digest(p) for p in sorted(a.host_root.rglob('*')) if p.is_file()};inputs.update({str(a.helper):digest(a.helper),str(a.runtime_bridge):digest(a.runtime_bridge),str(Path(__file__)):digest(Path(__file__))});save(a.out/'INPUTS_BEFORE.json',inputs)
summary=[]
def run(name,fn,full=False):
 out=a.out/name;out.mkdir();n=None;record={'case':name,'status':'RUNNING','actual_outer_runtime':True,'full_A_runtime':full,'snapshots':[],'events':[]}
 try:
  n=Native(a.runtime_bridge if full else a.helper,out);l=Link(n,full);r=R.AcquisitionRuntime(l,origin='device',clock_ms=lambda:l.now,utc_now=lambda:'2026-10-12T05:53:00Z')
  def pump(label='pump'):
   events=r.pump();record['events'].extend(events);record['snapshots'].append({'label':label,'events':events,'snapshot':r.snapshot(),'pending_capture':r.pending_capture is not None,'incomplete':r.incomplete_result is not None});return events
  def until(test):
   for _ in range(30):
    if test():return
    pump();require(r.snapshot().phase is not S.Phase.RECOVERY,'unexpected setup recovery')
   raise ValueError('pump bound')
  def noio():
   before=(len(l.writes),len(l.reads));[pump('no-auto-I/O') for _ in range(3)];require(before==(len(l.writes),len(l.reads)),'unexpected recovery I/O')
  def fails(fn):
   try:fn()
   except ValueError as e:return {'type':type(e).__name__,'text':str(e)}
   raise ValueError('action unexpectedly accepted')
  def reset_checks():
   record['reset_without_confirmation']=fails(lambda:r.start(12));record['reset_reused_nonce']=fails(lambda:r.start(11,reset_confirmed=True));before=(len(l.writes),len(l.reads));r.start(12,reset_confirmed=True);require(r.snapshot().phase is S.Phase.HELLO and before==(len(l.writes),len(l.reads)),'reset HELLO preparation');record['reset_prepared']=r.snapshot();record['discarded_transport_bytes_at_modeled_reset']=bytes(l.rx);l.rx.clear();l.fail_read=False;l.op('INIT');until(lambda:r.snapshot().phase is S.Phase.READY);record['reset_final']=r.snapshot();require(r.snapshot().session==12,'fresh HELLO session')
  def capture():
   l.op('CAPTURE 100 1',100);until(lambda:r.pending_capture is not None);c=r.pending_capture;require(c.samples==(3000,)+(2500,)*2047,'exact genuine fixture raw');require(c.origin=='device' and c.nominal_fs_hz==50000,'capture metadata');file=out/'capture.mtcap';REC.save(file,c);require(REC.load(file)==c,'saved capture');return c,file,digest(file)
  r.start(11);until(lambda:r.snapshot().phase is S.Phase.READY);r.configure(S.Config());until(lambda:r.snapshot().pending_request_id==0);r.arm();until(lambda:r.snapshot().phase is S.Phase.ARMED)
  commands=W.StreamDecoder().feed(b''.join(l.writes),l.now).frames;require([f.message_type for f in commands]==[1,2,3,4] and [f.request_id for f in commands]==[1,2,3,4],'actual HELLO/CAPS/CONFIG/ARM IDs')
  ctx=locals();fn(ctx)
  record['native_exit']=n.close();record['status']='PASS'
 except BaseException as e:
  record.update(status='FAILED',error_type=type(e).__name__,error=str(e));raise
 finally:
  if n:
   n.dispose();record['native_returncode']=n.proc.returncode;save(out/'native-trace.json',n.log)
  if 'l' in locals():
   save(out/'transport.json',{'writes':l.writes,'reads':l.reads,'device_states':l.states,'unread_hex':bytes(l.rx)});(out/'device.wire').write_bytes(l.wire);(out/'host.wire').write_bytes(b''.join(l.writes));record['final_snapshot']=r.snapshot();record['final_capture']=r.pending_capture;record['final_incomplete']=r.incomplete_result
   decoder=W.StreamDecoder();record['device_frames']=[]
   for i in range(0,len(l.wire),4096):
    frames=decoder.feed(bytes(l.wire[i:i+4096]),l.now);require(not frames.diagnostics,'wire evidence decoder');record['device_frames'].extend(frames.frames)
  save(out/'RESULTS.json',record);summary.append({'case':name,'status':record['status']});save(a.out/'RESULTS.json',summary)
def error_case(c,allocated,reason):
 r,l,record,pump=c['r'],c['l'],c['record'],c['pump']
 if allocated:l.op('PARTIAL 200',200);pump('drain CAPTURING status')
 mark=len(l.wire);l.op('FAULT 201 %d'%reason,201,True);events=pump('ADC ERROR');record['fault_wire']=bytes(l.wire[mark:]);record['fault_snapshot']=r.snapshot();frames=W.StreamDecoder().feed(record['fault_wire'],l.now).frames;require([f.message_type for f in frames]==([147,134] if allocated else [134]) and frames[-1].payload[:2]==bytes([5,reason]),'exact ADC wire')
 require(r.snapshot().phase is S.Phase.RECOVERY and r.pending_capture is None and r.incomplete_result is None,'error ownership')
 require(events[-1].kind is S.EventKind.RECOVERY and events[-1].reason is S.RecoveryReason.DEVICE_ERROR and events[-1].frame is None and events[-1].fault is None,'generic device error')
 if allocated:require(events[0].kind is S.EventKind.WINDOW_ABORT and events[0].reason==reason+1,'early ABORT retained')
 else:require(len(events)==1,'waiting no fabricated event')
 record['arm_in_recovery']=c['fails'](r.arm);c['noio']();c['reset_checks']()
def stop_te(c):
 r,l,record=c['r'],c['l'],c['record'];l.op('PARTIAL');c['pump']('drain CAPTURING status');require(l.states[-1]['window']==1,'allocated window');r.stop();record['pending_stop_before_write']=r.snapshot();l.stop_te=True;events=c['pump']('real TE and Stop');record['fault_events']=events
 require([e.kind for e in events]==[S.EventKind.WINDOW_ABORT,S.EventKind.STOPPED,S.EventKind.STATUS],'Stop event order');s=r.snapshot();require(s.phase is S.Phase.READY and not s.stop_barrier and not s.window_barrier and s.pending_stop_id==0,'Stop final state');require(r.pending_capture is None and r.incomplete_result is None,'Stop fabricated capture')
 before=len(l.writes);[c['pump']('after Stop') for _ in range(3)];require(len(l.writes)==before,'auto rearm');d=l.op('POLL');require(d['transfer_selected']==1 and d['dropped']==1 and d['starts']==1 and not d['hardware_running'] and not d['pending'] and d['recovery_steps']==0,'actual driver state')
def released_capture(c):
 r,l,record=c['r'],c['l'],c['record'];cap,file,h=c['capture']();record['equal_copy_rejected']=c['fails'](lambda:r.acknowledge(dataclasses.replace(cap)));r.acknowledge(cap);c['until'](lambda:r.pending_capture is None);require(l.states[-1]['starts']==2,'Normal release rearm');l.op('FAULT 201 2',201,True);c['pump']('ADC after released capture');require(r.snapshot().phase is S.Phase.RECOVERY,'post release ADC recovery');require(cap.samples==(3000,)+(2500,)*2047 and REC.load(file)==cap and digest(file)==h,'caller-held Capture changed');record['caller_capture']=cap;record['saved_sha256']=h;c['noio']();c['reset_checks']();require(REC.load(file)==cap and digest(file)==h,'reset damaged file')
def held_capture_expiry(c):
 r,l,record=c['r'],c['l'],c['record'];cap,file,h=c['capture']();before=len(l.wire);d=l.op('POLL 5100 64 150',5100);require(d['ready'] and d['session_active'] and not d['armed'],'A expiry serviceable terminal');record['expiry_wire']=bytes(l.wire[before:]);frames=W.StreamDecoder().feed(record['expiry_wire'],l.now).frames;require(len(frames)==1 and frames[0].message_type==134 and frames[0].payload[:2]==bytes([5,3]),'exact A hold expiry ERROR3');events=c['pump']('actual A hold expiry ERROR3');require(r.snapshot().phase is S.Phase.RECOVERY and r.pending_capture is cap,'expiry capture retention');require(events[-1].reason is S.RecoveryReason.DEVICE_ERROR,'hold expiry reason');record['recovery_events']=events;record['reset_owned_rejected']=c['fails'](lambda:r.start(12,reset_confirmed=True));before=len(l.writes);r.acknowledge(cap);require(r.pending_capture is None and len(l.writes)==before,'local retain ack');require(REC.load(file)==cap and digest(file)==h,'capture file changed');record['caller_capture']=cap;record['saved_sha256']=h;c['noio']();c['reset_checks']();require(REC.load(file)==cap and digest(file)==h,'reset damaged file')

def incomplete(c):
 r,l,record=c['r'],c['l'],c['record'];l.limit=900;l.op('CAPTURE 100 1',100);c['pump']('first capture prefix');require(r.pending_capture is None,'unexpected complete prefix');events=r.disconnect();record['disconnect_events']=events;partial=r.incomplete_result;require(partial is not None and partial.fragments and not partial.complete and partial.samples is None,'missing exact partial');record['partial_before']=partial;record['reset_owned_rejected']=c['fails'](lambda:r.start(12,reset_confirmed=True));require(r.take_incomplete() is partial and r.incomplete_result is None,'transfer identity');c['noio']();c['reset_checks']();record['caller_partial_after']=partial;require(clean(partial)==clean(record['partial_before']),'partial mutated')
try:
 run('waiting_adc_transfer',lambda c:error_case(c,False,1))
 run('allocated_early_abort_deadline',lambda c:error_case(c,True,2))
 run('full_runtime_te_pending_stop',stop_te,True)
 run('caller_released_capture_then_adc',released_capture)
 run('held_capture_actual_A_expiry',held_capture_expiry)
 run('incomplete_disconnect_reset_ownership',incomplete)
finally:
 after={p:digest(Path(p)) for p in inputs};save(a.out/'INPUTS_AFTER.json',after);require(inputs==after,'input changed')
print(json.dumps(summary))

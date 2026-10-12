#!/usr/bin/env python3
"""Twenty unique modeled-A windows through immutable published B runtime."""
import argparse,dataclasses,hashlib,importlib,importlib.util,json,os,struct,sys,zlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--host-root',type=Path,required=True);p.add_argument('--bridge',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
if not sys.flags.isolated or not sys.dont_write_bytecode or sys.flags.optimize:raise ValueError('require -I -B no optimization')
a.out.mkdir(exist_ok=False)
spec=importlib.util.spec_from_file_location('test_adapter',Path(__file__).with_name('adapter.py'));adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
require,save,clean=adapter.require,adapter.save,adapter.clean
sys.path.insert(0,str(a.host_root));R=importlib.import_module('motortester.runtime');S=importlib.import_module('motortester.session');W=importlib.import_module('motortester.wire');M=importlib.import_module('motortester.model');REC=importlib.import_module('motortester.recording')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for m in (R,S,W,M,REC):require(Path(m.__file__).resolve().is_relative_to(a.host_root),'host path')
inputs={str(p):sha(p) for p in a.host_root.rglob('*') if p.is_file()};inputs.update({str(p):sha(p) for p in Path(__file__).parent.glob('*.py')});inputs[str(a.bridge)]=sha(a.bridge);save(a.out/'INPUTS_BEFORE.json',inputs)
record={'status':'RUNNING','windows':[],'events':[],'snapshots':[],'physical_verification':False,'full_A_runtime':False};n=None
try:
 n=adapter.Native(a.bridge,a.out);l=adapter.Link(n);l.window=0
 r=R.AcquisitionRuntime(l,origin='simulator',clock_ms=lambda:l.now,utc_now=lambda:'2026-10-12T06:00:%02dZ'%l.window)
 def pump(label):
  e=r.pump();record['events'].extend(e);record['snapshots'].append({'label':label,'events':e,'snapshot':r.snapshot()});require(r.snapshot().phase is not S.Phase.RECOVERY,'unexpected recovery');return e
 def until(test,label):
  for _ in range(30):
   if test():return
   pump(label)
  raise ValueError('bounded pump exceeded')
 r.start(11);until(lambda:r.snapshot().phase is S.Phase.READY,'handshake');r.configure(S.Config());until(lambda:r.snapshot().pending_request_id==0,'configure');r.arm();until(lambda:r.snapshot().phase is S.Phase.ARMED,'arm')
 caller=[]
 for window in range(1,21):
  l.window=window;before_wire=len(l.wire);l.op('WINDOW %d %d'%(window*100,window),window*100);until(lambda:r.pending_capture is not None,'window-%02d'%window);c=r.pending_capture;caller.append(c)
  expected=[(73*i+19*window)&4095 for i in range(2048)];expected[0]=3000+window;expected[511]=0;expected[1023]=4095;raw=struct.pack('<2048H',*expected);actual=struct.pack('<2048H',*c.samples);require(actual==raw,'exact raw codes')
  expected_fields={'samples':tuple(expected),'session_id':'00000000-0000-0000-0000-00000000000b','sequence':window,'nominal_fs_hz':50000.0,'received_at_utc':'2026-10-12T06:00:%02dZ'%window,'origin':'simulator','trigger_kind':'signal','trigger_index':0,'trigger_edge':'rising','trigger_level_code':2048,'pretrigger_samples':0,'firmware_version':'0','protocol_version':'draft-0.1@b6ae8025baf1a47e8173997029d2d8b858ada74c','sensor':'Синтетический byte-peer; прибор не подключён','quality_flags':('draft_protocol','nominal_timing_unverified','rail_observed'),'application_version':'0.1.0','complete':True}
  require(set(expected_fields)==set(M.Capture.__dataclass_fields__) and dataclasses.asdict(c)==expected_fields,'all current Capture fields')
  prefix=a.out/('window-%02d'%window);path=prefix.with_suffix('.mtcap');REC.save(path,c);loaded=REC.load(path);require(loaded==c and dataclasses.asdict(loaded)==expected_fields,'all fields reopen')
  encoded=path.read_bytes();require(REC.encode(loaded)==encoded,'bit-exact file reencode');magic,meta_len,count=struct.unpack_from('<8sII',encoded);require(magic==b'MTCAP\x00\x01\x00' and count==2048,'file header');meta=json.loads(encoded[16:16+meta_len]);require(encoded[16+meta_len:-32]==raw and hashlib.sha256(encoded[:-32]).digest()==encoded[-32:],'file raw and SHA256')
  constants={'format_version':1,'channel_count':1,'adc_bits':12,'sample_encoding':'uint16-le','time_basis':'nominal_unverified','amplitude_unit':'adc_code','calibration':'uncalibrated','timestamp_origin':'host_receive','sample_count':2048};require(all(type(meta[k]) is type(v) and meta[k]==v for k,v in constants.items()),'MTCAP constants')
  prefix.with_suffix('.expected.u16le').write_bytes(raw);prefix.with_suffix('.received.u16le').write_bytes(actual);prefix.with_suffix('.wire').write_bytes(l.wire[before_wire:]);save(prefix.with_suffix('.metadata.json'),meta)
  row={'window':window,'raw_sha256':hashlib.sha256(raw).hexdigest(),'samples_crc32':zlib.crc32(raw),'file_sha256':sha(path),'file_bytes':len(encoded),'capture_fields_checked':list(expected_fields),'metadata':meta,'device_before_ack':l.states[-1],'host_before_ack':r.snapshot()};record['windows'].append(row)
  writes=len(l.writes);[pump('retained-before-ack') for _ in range(2)];require(len(l.writes)==writes and r.pending_capture is c,'premature release')
  if window<20:
   r.acknowledge(c);until(lambda:r.pending_capture is None,'release');require(l.states[-1]['starts']==window+1 and l.states[-1]['hw'],'Normal continuation');require(r.snapshot().generation==4,'generation changed')
  else:
   r.stop();until(lambda:r.snapshot().phase is S.Phase.READY,'final Stop');require(r.pending_capture is c,'Stop lost Capture');writes=len(l.writes);r.acknowledge(c);pump('local final retention');require(len(l.writes)==writes and r.pending_capture is None,'Stop caused RELEASE/rearm')
  save(a.out/'observation-in-progress.json',record)
 before=len(l.writes);[pump('no-rearm') for _ in range(3)];require(len(l.writes)==before and l.states[-1]['starts']==20 and not l.states[-1]['hw'] and not l.states[-1]['armed'],'final stopped')
 require(len({x['raw_sha256'] for x in record['windows']})==20 and len({x['file_sha256'] for x in record['windows']})==20,'unique windows/files')
 for w,c in enumerate(caller,1):require(REC.load(a.out/('window-%02d.mtcap'%w))==c,'retained caller object/file changed')
 decoder=W.StreamDecoder();frames=[]
 for i in range(0,len(l.wire),4096):
  b=decoder.feed(bytes(l.wire[i:i+4096]),l.now);require(not b.diagnostics,'wire diagnostic');frames.extend(b.frames)
 commands=W.StreamDecoder().feed(b''.join(l.writes),l.now);require(not commands.diagnostics,'command diagnostic')
 require([f.message_type for f in commands.frames]==[1,2,3,4]+[7]*19+[5],'one ARM / nineteen RELEASE / final Stop')
 require([f.request_id for f in commands.frames]==list(range(1,25)),'unique commands')
 require([f.message_type for f in frames].count(144)==20 and [f.message_type for f in frames].count(146)==20 and not any(f.message_type==147 for f in frames),'twenty complete terminals')
 for f in frames:
  if f.message_type==146:
   gen,window,count,reserved,crc=struct.unpack('<IIHHI',f.payload);require(gen==4 and count==2048 and reserved==0 and crc==record['windows'][window-1]['samples_crc32'],'original END CRC')
 record.update(status='TWENTY_SOFTWARE_ROUNDTRIP_PASS',host_commands=commands.frames,device_frames=frames,final_snapshot=r.snapshot(),native_exit=n.close())
except BaseException as e:record.update(status='FAILED',error_type=type(e).__name__,error=str(e));raise
finally:
 if n:n.dispose();record['native_returncode']=n.proc.returncode;save(a.out/'native-trace.json',n.log)
 if 'l' in locals():save(a.out/'transport.json',{'writes':l.writes,'reads':l.reads,'states':l.states});(a.out/'all-device.wire').write_bytes(l.wire);(a.out/'all-host.wire').write_bytes(b''.join(l.writes))
 save(a.out/'RESULTS.json',record);after={p:sha(Path(p)) for p in inputs};save(a.out/'INPUTS_AFTER.json',after);require(inputs==after,'input changed')
print(json.dumps({'status':record['status'],'windows':len(record['windows'])}))

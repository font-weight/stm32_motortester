#!/usr/bin/env python3
"""Two-checkout native ADC/frozen-host probe. No target or physical transport I/O."""
import argparse
import dataclasses
import enum
import hashlib
import importlib
import json
import os
from pathlib import Path
import selectors
import shutil
import struct
import subprocess
import sys
import time
import zlib

MANIFEST_SHA = 'fc1f7fb39eaad19b66bcc5c68a17beb13ef5806d7d3fb53aa371eb8d3cd66772'
HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
ENV = {}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require(condition, message):
    if not condition:
        raise ValueError(message)

def clean(value):
    if dataclasses.is_dataclass(value):
        return clean(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value

def save(path, value):
    path.write_text(json.dumps(clean(value), indent=2)+'\n')

def canonical(raw):
    path = Path(raw)
    require(path.is_absolute() and str(path) == os.path.normpath(raw), 'path must be absolute and normalized')
    require(path.resolve() == path, 'path must have no symlink aliases')
    return path

def dispose(pair):
    proc = getattr(pair, 'proc', None)
    if proc is not None:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                pipe.close()
    stderr = getattr(pair, 'stderr', None)
    if stderr is not None:
        stderr.close()

class Pair:
 def __init__(self,exe,out,name,chunk):
  self.out=out;self.name=name;self.chunk=chunk;self.log=[];self.events=[];self.frames=[];self.results=[];self.receiver=None;self.context=None;self.wire=b'';self.faultwire=b'';self.faultmark=None;self.current_t=0
  self.stderr=(out/(name+'.stderr')).open('w');self.proc=subprocess.Popen([str(exe)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,text=False,env=ENV,cwd=out)
  self.h=SessionController();self.d=StreamDecoder();self.op('INIT');self.h.start(11,0);self.exchange(0);require(self.h.snapshot().phase is Phase.READY,'hello/caps')
  self.h.configure(Config(),0);self.exchange(0);self.h.arm(0);self.exchange(0);require(self.h.snapshot().phase is Phase.ARMED,'arm')
 def op(self, command):
  self.log.append({'command':command, 'response_hex':''})
  self.proc.stdin.write((command+'\n').encode('ascii'))
  self.proc.stdin.flush()
  deadline = time.monotonic() + 5
  response = bytearray()
  with selectors.DefaultSelector() as selector:
   selector.register(self.proc.stdout, selectors.EVENT_READ)
   while b'\n' not in response:
    remaining = deadline - time.monotonic()
    require(remaining > 0 and selector.select(remaining), 'native response timeout')
    chunk = os.read(self.proc.stdout.fileno(), 4096)
    require(chunk, 'native response EOF')
    response.extend(chunk)
    self.log[-1]['response_hex'] = response.hex()
    require(len(response) <= 65536, 'native response too large')
  require(response.endswith(b'\n') and response.count(b'\n') == 1, 'native response framing')
  reply = json.loads(response)
  require(type(reply) is dict, 'native response is not an object')
  self.log[-1].update(reply)
  return reply
 def accept(self,r,t):
  raw=bytes.fromhex(r['wire']);self.wire+=raw
  if self.faultmark is not None:self.faultwire+=raw
  for at in range(0,len(raw),self.chunk):
   decoded=self.d.feed(raw[at:at+self.chunk],t);require(not decoded.diagnostics,'wire decoder diagnostics')
   for frame in decoded.frames:
    self.frames.append(frame);events=self.h.receive(frame,t);self.events.extend(events)
    for event in events:
     if event.kind is EventKind.WINDOW_BEGIN:
      s=self.h.snapshot();self.context=ExpectedContext(s.session,s.generation,s.config_id,1,0,0,2048,0);self.receiver=WindowReceiver(self.context)
     if event.kind in (EventKind.WINDOW_BEGIN,EventKind.WINDOW_DATA,EventKind.WINDOW_END_ADMITTED) and self.receiver is not None:
      result=self.receiver.feed(frame,t)
      if result:self.results.append(result);self.receiver=None
     # An early ABORT is admitted by the session before BEGIN and has no sample
     # assembler to terminate. No fabricated incomplete/complete window is made.
 def exchange(self,t):
  for _ in range(8):
   ticket=self.h.prepare_tx()
   if ticket is None:return
   reply=self.op('RX '+str(t)+' '+ticket.encoded.hex());require(not self.h.written(ticket,len(ticket.encoded),t),'host write recovery');self.accept(reply,t)
   self.accept(self.op('POLL '+str(t)+' 64 150'),t)
  raise AssertionError('exchange bound')
 def close(self):
  self.proc.stdin.close();code=self.proc.wait(timeout=5);self.stderr.close();require(code==0,'native exit')
  save(self.out/(self.name+'.trace.json'),self.log);(self.out/(self.name+'.wire')).write_bytes(self.wire);(self.out/(self.name+'.fault-wire')).write_bytes(self.faultwire)
 def run(self,reason,allocated,prior):
  if prior:
   self.accept(self.op('CAPTURE 100 1'),100);require(len(self.results)==1 and self.results[0].complete,'prior verified capture')
   verified=self.results[0];saved=clean(verified);raw=struct.pack('<2048H',*verified.samples);require(len(raw)==4096 and zlib.crc32(raw)==verified.samples_crc32,'prior raw CRC')
   receipt=WindowReceipt.from_window(self.context,verified);self.h.release(receipt,101);self.exchange(101)
   require(self.h.snapshot().phase is Phase.ARMED,'normal release continuation')
  else:verified=saved=raw=receipt=None
  if allocated:self.accept(self.op('PARTIAL 200'),200)
  before=self.op('STATE');eventmark=len(self.events);framemark=len(self.frames);self.faultmark=len(self.wire)
  self.accept(self.op('FAULT 201 '+str(reason)),201);self.accept(self.op('POLL 201 64 150'),201)
  frames=self.frames[framemark:];events=self.events[eventmark:];state=self.op('STATE')
  require([f.message_type for f in frames]==([147,134] if allocated else [134]),'fault wire order')
  status=frames[-1];values=struct.unpack('<BBHIIIII',status.payload)
  require(values[0:2]==(5,reason) and values[-1]==allocated,'STATUS cause/drop')
  require(state['ready'] and state['session_active'] and not state['hw'] and not state['armed'] and state['starts']==before['starts'],'firmware fault state')
  recovery=[e for e in events if e.kind is EventKind.RECOVERY]
  require(len(recovery)==1 and recovery[0].reason is RecoveryReason.DEVICE_ERROR and recovery[0].preserve_fragments and recovery[0].reset_transport,'conservative recovery')
  require(self.h.snapshot().phase is Phase.RECOVERY and self.h.prepare_tx() is None,'host no outgoing command')
  require(recovery[0].frame is None and recovery[0].fault is None,'recovery cause interface changed')
  if allocated:
   abort=events[0];require(abort.kind is EventKind.WINDOW_ABORT and abort.reason==reason+1,'early ABORT admitted')
   # Direct assembler-only diagnostic is an explicit integration boundary, not
   # a production routing implementation or expected completed window.
   s=self.h.snapshot();context=ExpectedContext(s.session,s.generation,s.config_id,1,0,0,2048,0)
   bare=WindowReceiver(context).feed(frames[0],201);require(not bare.complete and bare.reason is WindowCode.ORDER,'early assembler behavior')
  else:bare=None
  blocked={}
  operations=[('arm',lambda:self.h.arm(202)),('configure',lambda:self.h.configure(Config(),202)),('stop',lambda:self.h.stop(202))]
  if receipt is not None:operations.append(('release_previous_receipt',lambda:self.h.release(receipt,202)))
  for label,fn in operations:
   try:fn()
   except SessionError as exc:blocked[label]=exc.code.value
   else:raise AssertionError('host command unexpectedly accepted: '+label)
  require(all(code=='STATE' for code in blocked.values()),'wrong refusal')
  require(self.h.prepare_tx() is None,'rearm queued')
  if verified:
   require(clean(verified)==saved and struct.pack('<2048H',*verified.samples)==raw and len(self.results)==1,'prior verified raw mutated')
  else:require(not self.results,'fault fabricated complete window')
  out={'case':self.name,'chunk':self.chunk,'reason':reason,'allocated':allocated,'prior_verified_window':bool(prior),'fault_frames':frames,'fault_events':events,'host_snapshot':self.h.snapshot(),'device_before':before,'device_after':state,'blocked':blocked,'completed_window_results':len(self.results),'prior_samples':len(verified.samples) if verified else 0,'prior_raw_sha256':hashlib.sha256(raw).hexdigest() if raw else None,'prior_raw_crc32':verified.samples_crc32 if verified else None,'prior_result_unchanged':bool(verified),'early_abort_bare_assembler_result':bare,'numeric_STATUS_reason_exposed_in_recovery_event':False,'scope':'Public APIs only; test caller retains immutable WindowResult. No host runtime/GUI/file/transport adapter is in this snapshot.'}
  self.close();save(self.out/(self.name+'.json'),out);return out

def main():
    global ENV, SessionController, Config, Phase, EventKind, RecoveryReason
    global WindowReceipt, SessionError, ExpectedContext, WindowReceiver, WindowCode, StreamDecoder
    require(not sys.flags.optimize, 'optimized Python is unsupported')
    require(sys.flags.isolated and sys.dont_write_bytecode, 'run with Python -I -B')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-root', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    firmware = canonical(args.firmware_root)
    out = canonical(args.out)
    checkout = PACKAGE.parents[1]
    require(firmware.is_dir(), 'firmware root must exist')
    require(not out.exists() and not out.is_symlink(), 'output already exists')
    require(out.parent.is_dir(), 'output parent must exist')
    for source_root in (checkout, firmware):
        require(not out.is_relative_to(source_root) and not source_root.is_relative_to(out), 'output overlaps source checkout')
    manifest_path = HERE/'inputs.json'
    require(sha(manifest_path) == MANIFEST_SHA, 'manifest hash mismatch')
    manifest = json.loads(manifest_path.read_bytes())
    expected_keys = {'scope','firmware_commit','host_commit','firmware_files','package_files','local_files','production_c','matrix'}
    require(set(manifest) == expected_keys, 'manifest schema mismatch')
    units = ['firmware/app/mt_control','firmware/app/mt_capture_link',
             'firmware/protocol/mt_wire','firmware/protocol/mt_command',
             'firmware/protocol/mt_session','firmware/acquisition/acquisition',
             'firmware/transport/mt_tx']
    require(manifest['production_c'] == [unit+'.c' for unit in units], 'compile inventory mismatch')
    require(set(manifest['firmware_files']) == {unit+suffix for unit in units for suffix in ('.c','.h')}, 'source inventory mismatch')
    expected_package = {'snapshots/a-native/tests/wait-release/fixtures/test_wait_release.c'} | {
        'snapshots/pr74/host/motortester/'+name+'.py' for name in ('__init__','wire','session','window')}
    require(set(manifest['package_files']) == expected_package and set(manifest['local_files']) == {'device_bridge.c'}, 'package inventory mismatch')
    matrix = {'reason':[1,2],'allocated':[0,1],'prior':[0,1],'chunk':[1,64],'build':['strict','sanitize']}
    require(manifest['matrix'] == matrix, 'case matrix mismatch')
    pinned = {manifest_path:MANIFEST_SHA, Path(__file__).resolve():sha(Path(__file__).resolve())}
    for root, group in ((firmware,'firmware_files'),(PACKAGE,'package_files'),(HERE,'local_files')):
        for name, digest in manifest[group].items():
            path = root/name
            require(path.is_file() and path.resolve() == path, 'missing or aliased input: '+str(path))
            pinned[path] = digest
    for path, digest in pinned.items():
        require(sha(path) == digest, 'input hash mismatch: '+str(path))
    cc = shutil.which('gcc', path='/usr/bin:/bin')
    require(cc is not None, 'native GCC unavailable; sanitizer is mandatory')
    cc = str(Path(cc).resolve())
    host_root = PACKAGE/'snapshots/pr74/host'
    sys.path.insert(0, str(host_root))
    mods = {name:importlib.import_module('motortester'+('.'+name if name else ''))
            for name in ('','wire','session','window')}
    for name, module in mods.items():
        expected = host_root/'motortester'/((name or '__init__')+'.py')
        require(Path(module.__file__).resolve() == expected, 'unexpected host import')
    StreamDecoder = mods['wire'].StreamDecoder
    for name in ('SessionController','Config','Phase','EventKind','RecoveryReason','WindowReceipt','SessionError'):
        globals()[name] = getattr(mods['session'], name)
    for name in ('ExpectedContext','WindowReceiver','WindowCode'):
        globals()[name] = getattr(mods['window'], name)
    for path, digest in pinned.items():
        require(sha(path) == digest, 'input changed during imports')
    out.mkdir()
    (out/'tmp').mkdir()
    ENV = {'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','TZ':'UTC',
           'TMPDIR':str(out/'tmp'),'ASAN_OPTIONS':'detect_leaks=0',
           'UBSAN_OPTIONS':'halt_on_error=1','PYTHONDONTWRITEBYTECODE':'1'}
    receipt = {'status':'RUNNING','scope':manifest['scope'],
               'firmware_reference_commit':manifest['firmware_commit'],
               'host_reference_commit':manifest['host_commit'],
               'firmware_subset_only':True,'not_latest_B_assertion':True,
               'manifest_sha256':MANIFEST_SHA,
               'inputs_before':{str(path):digest for path,digest in pinned.items()},
               'compiler':cc,'compiler_sha256':sha(Path(cc)),
               'python':str(Path(sys.executable).resolve()),'python_version':sys.version,
               'python_sha256':sha(Path(sys.executable).resolve()),'commands':[],
               'hardware_verified':False,'shared_contract_accepted':False,
               'runtime_storage_verified':False,'case_rows':[]}
    save(out/'RESULTS.json',receipt)
    def run(label, argv, directory):
        command = {'label':label,'argv':argv,'cwd':str(directory),'environment':ENV}
        receipt['commands'].append(command)
        save(out/'RESULTS.json',receipt)
        try:
            process = subprocess.run(argv,cwd=directory,env=ENV,capture_output=True,timeout=120)
        except (subprocess.TimeoutExpired,OSError) as error:
            command.update(exit=None,error=type(error).__name__)
            (out/(label+'.stdout')).write_bytes(getattr(error,'stdout',None) or b'')
            (out/(label+'.stderr')).write_bytes(getattr(error,'stderr',None) or b'')
            raise
        (out/(label+'.stdout')).write_bytes(process.stdout)
        (out/(label+'.stderr')).write_bytes(process.stderr)
        command['exit'] = process.returncode
        require(process.returncode == 0, label+' failed')
    try:
        run('compiler-version',[cc,'--version'],out)
        expected_names = {f'reason{reason}-allocated{allocated}-prior{prior}-chunk{chunk}'
                          for reason in (1,2) for allocated in (0,1)
                          for prior in (0,1) for chunk in (1,64)}
        for mode in ('strict','sanitize'):
            directory = out/mode
            directory.mkdir()
            exe = directory/'bridge'
            argv = [cc,'-std=c11','-Wall','-Wextra','-Wpedantic','-Wconversion',
                    '-Wsign-conversion','-Werror','-O1','-g','-I'+str(firmware),
                    str(HERE/'device_bridge.c')]
            argv += [str(firmware/path) for path in manifest['production_c']]
            if mode == 'sanitize':
                argv += ['-fsanitize=address,undefined','-fno-omit-frame-pointer','-no-pie']
            argv += ['-o',str(exe)]
            run('compile-'+mode,argv,directory)
            observed = []
            for reason in (1,2):
                for allocated in (0,1):
                    for prior in (0,1):
                        for chunk in (1,64):
                            name = f'reason{reason}-allocated{allocated}-prior{prior}-chunk{chunk}'
                            pair = Pair.__new__(Pair)
                            try:
                                Pair.__init__(pair,exe,directory,name,chunk)
                                result = pair.run(reason,allocated,prior)
                            except BaseException:
                                save(directory/(name+'.failed-trace.json'),getattr(pair,'log',[]))
                                save(directory/(name+'.failed-events.json'),getattr(pair,'events',[]))
                                (directory/(name+'.partial-wire')).write_bytes(getattr(pair,'wire',b''))
                                raise
                            finally:
                                dispose(pair)
                            stderr = (directory/(name+'.stderr')).read_bytes()
                            require(b'AddressSanitizer' not in stderr and b'runtime error:' not in stderr, 'sanitizer diagnostic')
                            observed.append(name)
                            receipt['case_rows'].append({'mode':mode,'case':name,
                                'wire_sha256':sha(directory/(name+'.wire')),
                                'fault_wire_sha256':sha(directory/(name+'.fault-wire')),
                                'blocked':result['blocked'],'prior_raw_sha256':result['prior_raw_sha256']})
                            save(out/'RESULTS.json',receipt)
            require(len(observed) == 16 and set(observed) == expected_names, 'case inventory incomplete')
        rows = receipt['case_rows']
        require(len(rows) == 32, 'missing scenario result')
        for row in rows[:16]:
            other = [item for item in rows[16:] if item['case'] == row['case']]
            require(len(other) == 1 and {k:v for k,v in row.items() if k!='mode'} ==
                    {k:v for k,v in other[0].items() if k!='mode'}, 'strict/sanitized mismatch')
        for path,digest in pinned.items():
            require(sha(path) == digest, 'input changed during run')
        receipt.update(status='FROZEN_ADC_HOST_COMPONENT_TESTS_PASS',cases=32,inputs_unchanged=True)
    except BaseException as error:
        receipt.update(status='FAILED',error_type=type(error).__name__,error=str(error))
        raise
    finally:
        save(out/'RESULTS.json',receipt)
    print(json.dumps({'status':receipt['status'],'cases':32,'output':str(out)}))

if __name__ == '__main__':
    try:
        main()
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError) as error:
        print(json.dumps({'status':'REJECTED','error':str(error)}))
        sys.exit(1)

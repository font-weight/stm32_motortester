#!/usr/bin/env python3
"""Critic-B reconstruction. No repository writes. Every output directory exclusive."""
import argparse, hashlib, importlib.util, json, os, pathlib, shutil, subprocess, sys
sys.dont_write_bytecode=True
ROOT=pathlib.Path(__file__).resolve().parents[1]; S=ROOT/'source'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def inventory():return {str(p.relative_to(ROOT)):sha(p) for root in [S,ROOT/'reviewer'] for p in sorted(root.rglob('*')) if p.is_file()}
p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--sanitize',action='store_true');p.add_argument('--case',action='append');a=p.parse_args()
O=pathlib.Path(a.out).resolve();assert O.is_relative_to(ROOT) and not O.is_relative_to(S) and not O.is_relative_to(ROOT/'reviewer');O.mkdir(parents=True,exist_ok=False)
env=os.environ.copy();env.update(TMPDIR=str(O),PYTHONDONTWRITEBYTECODE='1',ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
shutil.copytree(ROOT/'reviewer',O/'reviewer-snapshot');before=inventory();(O/'inputs-before.json').write_text(json.dumps(before,indent=2)+'\n');record={'commands':[],'result':'FAIL'}
def run(cmd,check=True):
 r=subprocess.run(list(map(str,cmd)),env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);entry={'argv':list(map(str,cmd)),'exit_status':r.returncode,'output':r.stdout};record['commands'].append(entry)
 with (O/'commands.log').open('a') as f:f.write('+ '+' '.join(map(str,cmd))+'\n'+r.stdout)
 print(r.stdout,end='',flush=True)
 if check and r.returncode:raise RuntimeError('Command failed; see preserved log')
 return r
try:
 tree=json.loads((ROOT/'tree.json').read_text());lookup={x['path']:x['sha'] for x in tree['tree'] if x['type']=='blob'};manifest=[]
 for f in sorted(S.rglob('*')):
  if not f.is_file():continue
  data=f.read_bytes();path=str(f.relative_to(S));blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest();assert blob==lookup[path],(path,blob,lookup[path]);manifest.append({'path':path,'git_blob':blob,'sha256':sha(f)})
 (O/'source-manifest.json').write_text(json.dumps({'commit':'b607392cb4f1cc19918e2795359df4680b645643','tree':tree['sha'],'files':manifest},indent=2)+'\n')
 run(['gcc','--version'])
 patches=S/'firmware/third_party/usb/patches';vendor=S/'firmware/third_party/usb/vendor/tinyusb/src';build=O/'build';build.mkdir();intermediate=O/'intermediate';intermediate.mkdir()
 run(['python3',patches/'apply_setup_patch.py',vendor/'device/usbd.c',intermediate/'usbd.setup.c'])
 run(['python3',patches/'apply_usb_bridge_patch.py','core',intermediate/'usbd.setup.c',build/'usbd.c'])
 run(['python3',patches/'apply_f103_reset_patch.py',vendor/'portable/st/stm32_fsdev/dcd_stm32_fsdev.c',intermediate/'fsdev.reset.c'])
 run(['python3',patches/'apply_usb_bridge_patch.py','fsdev',intermediate/'fsdev.reset.c',build/'dcd_stm32_fsdev.c'])
 spec=importlib.util.spec_from_file_location('instrument_fake',S/'tests/usb-bridge/supporting/instrument_fake.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);mod.generate(S/'tests/usb-cdc/supporting',O,O)
 # The author fake time function is renamed only in its translation unit, then real mt_usb_time.c is linked.
 project=['app/mt_runtime','app/mt_adc_control','app/mt_control','app/mt_control_rx','app/mt_capture_link','platform/adc_timer_dma','platform/mt_timebase','platform/mt_usb_time','acquisition/acquisition','protocol/mt_wire','protocol/mt_command','protocol/mt_session','protocol/mt_stream','transport/mt_tx','transport/mt_usb_cdc','transport/mt_usb_bridge','transport/mt_usb_epoch']
 upstream=[vendor/'tusb.c',vendor/'common/tusb_fifo.c',vendor/'class/cdc/cdc_device.c',build/'usbd.c']
 sources=upstream+[S/'firmware'/(x+'.c') for x in project]+[O/'fake_dcd_instrumented.c',ROOT/'reviewer/runtime_probe.c']
 includes=['-DSTM32F103xB','-DCFG_MT_USB_BRIDGE=1','-I'+str(S/'firmware/boot'),'-I'+str(S/'firmware/platform'),'-I'+str(S/'firmware/app'),'-I'+str(S/'firmware/transport'),'-I'+str(S/'tests/usb-bridge/supporting'),'-I'+str(S/'tests/usb-cdc/supporting'),'-isystem',str(vendor)]
 common=['-g','-O1' if a.sanitize else '-O2','-fno-lto','-fno-omit-frame-pointer','-Wall','-Wextra']+(['-fsanitize=address,undefined'] if a.sanitize else [])
 objs=[]
 for i,f in enumerate(sources):
  obj=O/(str(i)+'_'+f.stem+'.o');objs.append(obj)
  flags=['-std=gnu11'] if f in upstream else ['-std=c11','-Wpedantic','-Wconversion','-Wsign-conversion','-Werror']
  if f.name=='fake_dcd_instrumented.c':flags+=['-Dtusb_time_millis_api=test_unused_fake_time_api']
  run(['gcc',*common,*flags,*includes,'-MD','-MF',str(obj)+'.d','-c',f,'-o',obj])
 dependencies=set()
 for dep in O.glob('*.o.d'):
  text=dep.read_text().replace('\\\n',' ');dependencies.update(text.split(':',1)[1].split())
 (O/'gcc-dependency-closure.json').write_text(json.dumps({path:sha(pathlib.Path(path)) for path in sorted(dependencies)},indent=2)+'\n')
 exe=O/'runtime_probe';run(['gcc',*common,*objs,'-o',exe]);record['executable_sha256']=sha(exe)
 cases=a.case or ['transfer_wait','deadline_wait','transfer_capture','deadline_capture','time_before','reset_before','suspend_before','poison_before','time_stop','reset_stop','suspend_stop','poison_stop','time_admitted','reset_admitted','suspend_admitted','poison_admitted','time_outputgap','reset_outputgap','suspend_outputgap','poison_outputgap','time_outputgap_deadline','time_outputgap_capture','time_outputgap_capture_deadline','reset_outputgap_capture','suspend_outputgap_capture','poison_outputgap_capture','stop_failed','adc_clock','adc_clock_plan','sequence_limit']
 results=[]
 for case in cases:
  r=run([exe,case,O],check=False);(O/(case+'.log')).write_text(r.stdout);results.append({'case':case,'exit_status':r.returncode})
 record['cases']=results;record['result']='PASS' if all(x['exit_status']==0 for x in results) else 'FAIL'
except BaseException as e:
 record['error']=str(e);raise
finally:
 after=inventory();record['inputs_unchanged']=before==after;(O/'inputs-after.json').write_text(json.dumps(after,indent=2)+'\n');(O/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
 if before!=after:raise RuntimeError('Inputs changed')
print(record['result'],O,flush=True)
sys.exit(0 if record['result']=='PASS' else 1)

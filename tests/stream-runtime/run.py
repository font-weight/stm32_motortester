#!/usr/bin/env python3
"""Pinned native STREAM runtime composition. Linux, no device/network access."""
import argparse, hashlib, importlib.util, json, os, shutil, signal, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
PROJECT=['app/mt_live_runtime','app/mt_live_adc','platform/adc_timer_dma','platform/mt_timebase','platform/mt_live_clock','protocol/mt_live_device','protocol/mt_wire','acquisition/mt_sample_queue','transport/mt_usb_epoch','transport/mt_usb_bridge']
WRAPS=['tud_cdc_n_read','tud_cdc_n_read_flush','tud_cdc_n_write','tud_cdc_n_write_flush','mt_adc_dma_read_block','mt_live_adc_service','mt_live_device_step','mt_usb_bridge_cleaned','mt_usb_bridge_snapshot']
def require(ok,msg):
 if not ok: raise ValueError(msg)
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def canonical(s):
 p=Path(s); require(p.is_absolute() and str(p)==os.path.normpath(s) and p.resolve()==p,'absolute normalized non-symlink path required: '+s); return p
def disjoint(a,b): return not(a==b or a.is_relative_to(b) or b.is_relative_to(a))
def main():
 require(sys.flags.isolated and sys.dont_write_bytecode and not sys.flags.optimize,'run python3 -I -B without -O (upstream instrumentation has assertions)')
 ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--source',required=True); ap.add_argument('--output',required=True); ap.add_argument('--cc',default='/usr/bin/gcc'); ap.add_argument('--case',action='append'); ap.add_argument('--strict-only',action='store_true'); a=ap.parse_args()
 source,out=canonical(a.source),canonical(a.output); cc=Path(a.cc).resolve()
 require(source.is_dir() and cc.is_file(),'missing source/compiler'); require(not out.exists() and out.parent.is_dir() and all(disjoint(out,x) for x in (source,HERE)),'output must be fresh and outside source/package')
 manifest=json.loads((HERE/'source-manifest.json').read_text()); files=manifest['files']
 def inputs(root):
  result={}
  for rel,h in files.items():
   p=root/rel; require(p.is_file() and p.resolve()==p and sha(p)==h,'source pin mismatch: '+rel); result[rel]=h
  return result
 before=inputs(source); out.mkdir(); stage=out/'source'
 for rel in files:
  p=stage/rel; p.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source/rel,p)
 fixture=out/'fixture'; fixture.mkdir()
 for name in ('test_integration.h','test_runtime.c'): shutil.copyfile(HERE/name,fixture/name)
 (out/'tmp').mkdir(); env={'PATH':str(cc.parent)+':/usr/bin:/bin','LANG':'C','LC_ALL':'C','TZ':'UTC','TMPDIR':str(out/'tmp'),'PYTHONDONTWRITEBYTECODE':'1','ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'}
 receipt={'status':'RUNNING','source_head':manifest['source_head'],'inputs_before':before,'package_inputs':{p.name:sha(p) for p in HERE.iterdir() if p.is_file()},'compiler':str(cc),'compiler_sha256':sha(cc),'python':sys.version,'environment':env,'physical_verification':False,'full_image_build':False,'transaction_bound_qualified':False,'fake_seams':['MMIO','DCD','nested IRQ guard','CTR rearm'],'commands':[],'cases':{}}
 def save(): (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
 def run(label,argv):
  row={'label':label,'argv':list(map(str,argv)),'cwd':str(out)}; receipt['commands'].append(row); save()
  with (out/(label+'.stdout')).open('wb') as so,(out/(label+'.stderr')).open('wb') as se:
   p=subprocess.Popen(row['argv'],cwd=out,env=env,stdout=so,stderr=se,start_new_session=True)
   try: row['exit']=p.wait(timeout=180)
   except BaseException:
    os.killpg(p.pid,signal.SIGKILL); p.wait(); raise
  save(); require(row['exit']==0,label+' failed; see retained stdout/stderr'); return (out/(label+'.stdout')).read_text()
 try:
  run('compiler-version',[cc,'--version']); vendor=stage/'firmware/third_party/usb/vendor/tinyusb/src'; patches=stage/'firmware/third_party/usb/patches'
  for mode in (('strict',) if a.strict_only else ('strict','sanitize')):
   b=out/mode
   for d in ('stage1','patched','generator-input/build'): (b/d).mkdir(parents=True)
   def job(label,argv): return run(mode+'-'+label,argv)
   job('patch-setup',[sys.executable,'-I','-B',patches/'apply_setup_patch.py',vendor/'device/usbd.c',b/'stage1/usbd.c'])
   job('patch-reset',[sys.executable,'-I','-B',patches/'apply_f103_reset_patch.py',vendor/'portable/st/stm32_fsdev/dcd_stm32_fsdev.c',b/'stage1/dcd_stm32_fsdev.c'])
   for kind,name in [('core','usbd.c'),('fsdev','dcd_stm32_fsdev.c')]: job('patch-'+kind,[sys.executable,'-I','-B',patches/'apply_usb_bridge_patch.py',kind,b/'stage1'/name,b/'patched'/name])
   job('patch-stream-ctr',[sys.executable,'-I','-B',patches/'apply_stream_ctr_patch.py',b/'patched/dcd_stm32_fsdev.c',b/'generator-input/build/dcd_stm32_fsdev.c'])
   spec=importlib.util.spec_from_file_location('fake_generator',stage/'tests/usb-bridge/supporting/instrument_fake.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); module.generate(stage/'tests/usb-cdc/supporting',b,b/'generator-input')
   inc=['-DSTM32F103xB','-DCFG_MT_USB_BRIDGE=1','-DCFG_MT_USB_CTR_FAIRNESS=1','-I'+str(stage/'firmware/boot'),'-I'+str(fixture),'-I'+str(stage/'tests/usb-cdc/supporting')]+['-I'+str(stage/'firmware'/d) for d in ('app','platform','protocol','transport','acquisition')]+['-isystem',str(vendor)]
   flags=['-g','-O1','-fno-lto','-Wall','-Wextra','-fno-omit-frame-pointer']
   if mode=='sanitize': flags+=['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
   macros=job('config',[cc,'-std=c11',*inc,'-dM','-E','-include','tusb.h','-x','c','/dev/null'])
   for k,v in {'CFG_TUD_TASK_EVENTS_PER_RUN':'1','CFG_TUD_CDC_TX_BUFSIZE':'64','CFG_TUD_CDC_RX_BUFSIZE':'64','CFG_MT_USB_BRIDGE':'1','CFG_MT_USB_CTR_FAIRNESS':'1','CFG_TUD_CDC':'1','CFG_TUD_CDC_TX_OVERWRITABLE_IF_NOT_CONNECTED':'0','CFG_TUD_CDC_RX_PERSISTENT':'0','CFG_TUD_CDC_TX_PERSISTENT':'0'}.items(): require('#define '+k+' '+v+'\n' in macros,'configuration '+k)
   upstream=[vendor/'common/tusb_fifo.c',vendor/'tusb.c',b/'patched/usbd.c',vendor/'class/cdc/cdc_device.c']; sources=upstream+[stage/('firmware/'+p+'.c') for p in PROJECT]+[fixture/'test_runtime.c',b/'fake_dcd_instrumented.c']; objects=[]
   for i,p in enumerate(sources):
    o=b/(str(i)+'-'+p.stem+'.o'); objects.append(o); opts=['-std=gnu11'] if p in upstream else ['-std=c11','-pedantic-errors','-Werror','-Wconversion','-Wsign-conversion','-Wshadow']; job('compile-'+str(i),[cc,*flags,*opts,*inc,'-c',p,'-o',o])
   exe=b/'test-runtime'; job('link',[cc,*flags,*objects,*['-Wl,--wrap='+x for x in WRAPS],'-o',exe]); symbols=job('symbols',['/usr/bin/nm',exe]); require(not any(' '+s in symbols for s in ('mt_runtime_init','mt_control_init','mt_tx_init','mt_capture_link_init')),'legacy allocation/code linked')
   inventory=job('list',[exe,'--list']).splitlines(); require(len(inventory)==len(set(inventory)) and inventory,'invalid case inventory'); cases=a.case or inventory; require(set(cases)<=set(inventory),'unknown case'); receipt['cases'][mode]={}
   for case in cases:
    rows=[json.loads(s) for s in job('case-'+case,[exe,case]).splitlines()]; require(rows[-1]['case']==case and rows[-1]['pass'],'case did not pass'); receipt['cases'][mode][case]=rows
  if not a.strict_only: require(receipt['cases']['strict']==receipt['cases']['sanitize'],'strict/sanitize mismatch')
  receipt['status']='PASS'
 except BaseException as e: receipt.update(status='FAILED',error=type(e).__name__+': '+str(e)); raise
 finally:
  try: receipt['inputs_after']=inputs(source); receipt['staged_inputs']=inputs(stage); receipt['source_unchanged']=receipt['inputs_after']==before
  except BaseException as e: receipt.update(status='FAILED_INPUT_DRIFT',input_error=str(e))
  save()
 require(receipt['status']=='PASS',receipt['status']); print(json.dumps({'status':'PASS','cases_per_mode':len(receipt['cases']['strict']),'receipt':str(out/'receipt.json')}))
if __name__=='__main__': main()

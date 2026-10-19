#!/usr/bin/env python3
"""Pinned diagnostic-enabled STREAM boot checks. No hardware or network access."""
import argparse, hashlib, json, os, shutil, signal, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
PROJECT=['boot/mt_boot_time','boot/mt_boot_usb','platform/mt_clock_startup','platform/mt_timebase','platform/mt_usb_time','platform/mt_live_clock','platform/adc_timer_dma','platform/mt_stream_diag','app/mt_live_runtime','app/mt_live_adc','protocol/mt_live_device','protocol/mt_wire','acquisition/mt_sample_queue','transport/mt_usb_descriptors','transport/mt_usb_epoch','transport/mt_usb_bridge']
WRAPS=['mt_clock_mmio_read32','mt_clock_mmio_write32','mt_usb_descriptors_init','mt_clock_apply','mt_boot_time_start','mt_usb_time_bind','mt_live_runtime_init','mt_boot_usb_prepare','tusb_rhport_init','mt_live_runtime_step','mt_stream_diag_init']
VARIANTS={'schema0':{'schema':0},'schema2':{'schema':2},'mode0':{'mode':0},'mode2':{'mode':2},'bound0':{'bound':0},'bound500':{'bound':500},'bound_max':{'bound':4294967295},'fixed_pullup0':{'fixed':0},'dtr2':{'dtr':2},'priority16':{'priority':16},'poll0':{'poll':0},'poll_too_large':{'poll':1000001},'hse_invalid':{'hse':8000001},'hse_mode_invalid':{'hse_mode':2},'descriptor_vid0':{'vid':0,'failure':3}}
def need(ok,msg):
 if not ok: raise ValueError(msg)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def canon(p):
 q=Path(p); need(q.is_absolute() and q.resolve()==q and str(q)==os.path.normpath(p),'absolute normalized nonsymlink path required'); return q
def disjoint(a,b):return not(a==b or a.is_relative_to(b) or b.is_relative_to(a))
def input_c(changes=None):
 v={'schema':1,'mode':1,'bound':1,'hse':8000000,'poll':100,'priority':5,'hse_mode':0,'fixed':1,'dtr':0,'vid':4660,'failure':2}; v.update(changes or {})
 return '''/* SYNTHETIC BOARD INPUT: no USB identity/power/hardware approval. */
#include "mt_board_inputs.h"
#include "mt_stream_timing.h"
const mt_board_inputs mt_board={.verified_hse_hz=%(hse)su,.clock_poll_budget=%(poll)su,.usb_irq_priority=%(priority)su,.firmware_build=7u,.hse_mode=%(hse_mode)s,.require_dtr=%(dtr)su,.verified_fixed_pullup=%(fixed)su,.usb={.vid=%(vid)su,.pid=1u,.bcd_device=0x0100u,.bus_power_ma=100u,.manufacturer="NATIVE TEST",.product="NO DEVICE",.serial="TEST0001"}};
const mt_stream_timing_inputs mt_stream_timing={%(schema)su,%(mode)su,%(bound)su};
const unsigned fixture_expected_failure=%(failure)su;
'''%v

def main():
 need(sys.flags.isolated and sys.dont_write_bytecode and not sys.flags.optimize,'use python3 -I -B without -O')
 ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--source',required=True); ap.add_argument('--output',required=True); ap.add_argument('--cc',default='/usr/bin/gcc'); ap.add_argument('--strict-only',action='store_true'); a=ap.parse_args(); source,out=canon(a.source),canon(a.output); cc=Path(a.cc).resolve(); need(source.is_dir() and cc.is_file(),'missing source/compiler'); need(not out.exists() and out.parent.is_dir() and disjoint(source,out) and disjoint(HERE,out),'fresh external output required')
 manifest=json.loads((HERE/'source-manifest.json').read_text())
 def check(root):
  result={}
  for rel,h in manifest['files'].items():
   p=root/rel; need(p.is_file() and p.resolve()==p and sha(p)==h,'source pin mismatch: '+rel); result[rel]=h
  return result
 before=check(source); out.mkdir(); stage=out/'source'; fixture=out/'fixture'; fixture.mkdir(); (out/'tmp').mkdir()
 for rel in before:
  p=stage/rel; p.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source/rel,p)
 for n in ('test_boot.c',):shutil.copyfile(HERE/n,fixture/n)
 receipt={'status':'RUNNING','source_head':manifest['source_head'],'input_hashes':before,'package_hashes':{p.name:sha(p) for p in HERE.iterdir() if p.is_file()},'compiler':str(cc),'compiler_sha256':sha(cc),'python':sys.version,'physical_verification':False,'transaction_bound_qualified':False,'fake_seams':['DWT MMIO','MMIO','Cortex-M CPU mask/admit/halt','DCD initialization and IRQ registers','CTR rearm','loop exit longjmp'],'commands':[],'cases':{}}
 env={'PATH':str(cc.parent)+':/usr/bin:/bin','LC_ALL':'C','LANG':'C','TZ':'UTC','TMPDIR':str(out/'tmp'),'PYTHONDONTWRITEBYTECODE':'1','ASAN_OPTIONS':'detect_leaks=0:halt_on_error=1','UBSAN_OPTIONS':'halt_on_error=1:print_stacktrace=1'}; receipt['environment']=env
 def save(): (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
 def run(label,argv):
  row={'label':label,'argv':list(map(str,argv)),'cwd':str(out)}; receipt['commands'].append(row); save()
  with (out/(label+'.stdout')).open('wb') as so,(out/(label+'.stderr')).open('wb') as se:
   p=subprocess.Popen(row['argv'],stdout=so,stderr=se,cwd=out,env=env,start_new_session=True)
   try:row['exit']=p.wait(timeout=180)
   except BaseException:os.killpg(p.pid,signal.SIGKILL);p.wait();raise
  save(); need(row['exit']==0,label+' failed; inspect retained logs'); return (out/(label+'.stdout')).read_text()
 try:
  run('compiler-version',[cc,'--version'])
  declaration={'schema_version':1,'input_kind':'diagnostic_provisional','declared_transaction_bound_ms':1,'source_commit':manifest['source_head'],'profile':'stream-only-v1'}; (out/'declaration.json').write_text(json.dumps(declaration)); run('generate-declaration',[sys.executable,'-I','-B',stage/'firmware/build/stream_timing.py','--input',out/'declaration.json','--output',out/'declared-timing.c'])
  v=stage/'firmware/third_party/usb/vendor/tinyusb/src'; patches=stage/'firmware/third_party/usb/patches'
  for mode in (('strict',) if a.strict_only else ('strict','sanitize')):
   b=out/mode; (b/'stage1').mkdir(parents=True); (b/'patched').mkdir()
   def job(label,argv):return run(mode+'-'+label,argv)
   job('setup-patch',[sys.executable,'-I','-B',patches/'apply_setup_patch.py',v/'device/usbd.c',b/'stage1/usbd.c']); job('bridge-patch',[sys.executable,'-I','-B',patches/'apply_usb_bridge_patch.py','core',b/'stage1/usbd.c',b/'patched/usbd.c'])
   inc=['-DSTM32F103xB','-DCFG_MT_USB_CTR_FAIRNESS=1','-DCFG_MT_STREAM_DIAGNOSTICS=1']+['-I'+str(stage/'firmware'/x) for x in ('boot','app','platform','protocol','transport','acquisition')]+['-isystem',str(v)]
   flags=['-g','-O1','-fno-lto','-fno-short-enums','-fno-omit-frame-pointer','-Wall','-Wextra']
   if mode=='sanitize':flags+=['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
   strict=['-std=c11','-pedantic-errors','-Werror','-Wconversion','-Wsign-conversion','-Wshadow']
   upstream=[v/'common/tusb_fifo.c',v/'tusb.c',b/'patched/usbd.c',v/'class/cdc/cdc_device.c']; sources=upstream+[stage/('firmware/'+x+'.c') for x in PROJECT]+[stage/'firmware/boot/mt_stream_main.c',fixture/'test_boot.c']; objects=[]
   for i,p in enumerate(sources):
    target=b/(str(i)+'-'+p.stem+'.o'); objects.append(target); opts=['-std=gnu11'] if p in upstream else strict; extra=['-Dmain=mt_stream_boot_entry'] if p.name=='mt_stream_main.c' else []; job('compile-'+str(i),[cc,*flags,*opts,*inc,*extra,'-c',p,'-o',target])
   def executable(label,changes):
    c=b/(label+'-inputs.c'); o=b/(label+'-inputs.o'); exe=b/label; text=input_c(changes); extras=[]
    if label=='boot':
     text='\n'.join(line for line in text.splitlines() if not line.startswith('const mt_stream_timing_inputs')); generated=b/'declared-timing.o'; job('compile-generated-declaration',[cc,*flags,*strict,*inc,'-c',out/'declared-timing.c','-o',generated]); extras.append(generated)
    c.write_text(text); job('inputs-'+label,[cc,*flags,*strict,*inc,'-c',c,'-o',o]); job('link-'+label,[cc,*flags,*objects,o,*extras,*['-Wl,--wrap='+x for x in WRAPS],'-o',exe]); return exe
   exe=executable('boot',{}); nm=job('symbols',['/usr/bin/nm',exe]); need(not any(' '+x in nm for x in ('mt_runtime_init','mt_control_init','mt_capture_link_init','mt_tx_init')),'legacy runtime linked')
   cases=job('list',[exe,'--list']).splitlines(); need(len(cases)==len(set(cases)),'duplicate native case'); receipt['cases'][mode]={}
   for case in cases:
    if case=='invalid_inputs':continue
    row=json.loads(job('case-'+case,[exe,case])); need(row['pass'] and row['case']==case,'native case output'); receipt['cases'][mode][case]=row
   for label,changes in VARIANTS.items():
    target=executable(label,changes); row=json.loads(job('case-input-'+label,[target,'invalid_inputs'])); need(row['pass'] and row['failure_stage']==changes.get('failure',2),'bad input accepted'); receipt['cases'][mode]['input_'+label]=row
  if not a.strict_only:need(receipt['cases']['strict']==receipt['cases']['sanitize'],'mode result mismatch')
  receipt['status']='PASS'
 except BaseException as e:receipt.update(status='FAILED',error=type(e).__name__+': '+str(e));raise
 finally:
  try:receipt['source_unchanged']=before==check(source);receipt['staged_unchanged']=before==check(stage)
  except BaseException as e:receipt.update(status='FAILED_INPUT_DRIFT',input_error=str(e))
  save()
 need(receipt['status']=='PASS',receipt['status']); print(json.dumps({'status':'PASS','cases_per_mode':len(receipt['cases']['strict']),'receipt':str(out/'receipt.json')}))
if __name__=='__main__':main()

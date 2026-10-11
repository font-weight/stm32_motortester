#!/usr/bin/env python3
"""Fresh-output snapshot build of real runtime dependencies and modeled I/O."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
HERE=Path(__file__).resolve().parent
P=argparse.ArgumentParser(description=__doc__)
P.add_argument('--out',required=True,type=Path)
P.add_argument('--sanitize',action='store_true')
P.add_argument('--case',action='append',dest='cases')
P.add_argument('--runtime',type=Path,default=HERE.parent/'runtime-draft')
P.add_argument('--base',type=Path,default=HERE.parent/'usb-bridge-publication/base-checkout')
P.add_argument('--adc',type=Path,default=HERE.parent/'adc-commit-draft')
P.add_argument('--bridge',type=Path,default=HERE.parent/'usb-bridge-draft')
P.add_argument('--epoch',type=Path,default=HERE.parent/'usb-epoch-draft')
P.add_argument('--dependencies',type=Path,default=HERE.parent/'usb-dependencies')
P.add_argument('--cdc-support',type=Path,default=HERE.parent/'usb-cdc-tests')
P.add_argument('--bridge-support',type=Path,default=HERE.parent/'usb-bridge-tests')
A=P.parse_args()
ROOT=HERE.parent
BASE=A.base.resolve()
ADC=A.adc.resolve()
BRIDGE=A.bridge.resolve()
EPOCH=A.epoch.resolve()
VENDOR=A.dependencies.resolve()
CDC=A.cdc_support.resolve()
SUPPORT=A.bridge_support.resolve()
RUNTIME=A.runtime.resolve()
OUT=A.out.resolve()
SOURCE_ROOTS=[HERE,BASE,ADC,BRIDGE,EPOCH,VENDOR,CDC,SUPPORT,RUNTIME]
for source in SOURCE_ROOTS:
    source=source.resolve()
    if OUT == source or OUT.is_relative_to(source) or source.is_relative_to(OUT):
        P.error(f'output overlaps protected input root: {source}')
if OUT.exists(): P.error('output must be fresh and nonexistent')
PROJECT=['app/mt_runtime','platform/mt_timebase','platform/adc_timer_dma','app/mt_adc_control',
         'app/mt_control','app/mt_control_rx','app/mt_capture_link','acquisition/acquisition',
         'protocol/mt_command','protocol/mt_session','protocol/mt_stream','protocol/mt_wire',
         'transport/mt_tx','transport/mt_usb_cdc','transport/mt_usb_epoch','transport/mt_usb_bridge']
INPUTS={}
for name in PROJECT:
    for suffix in ('.c','.h'):
        relative='firmware/'+name+suffix
        source=next((root/relative for root in [RUNTIME,ADC,BRIDGE,EPOCH,BASE] if (root/relative).is_file()),None)
        if source is None: raise SystemExit('missing input '+relative)
        INPUTS[relative]=source
for path in (VENDOR/'vendor/tinyusb/src').rglob('*'):
    if path.is_file(): INPUTS['vendor/'+str(path.relative_to(VENDOR/'vendor'))]=path
for name in ('source-manifest.json','SHA256SUMS.sources'): INPUTS['dependency/'+name]=VENDOR/name
for line in (VENDOR/'SHA256SUMS.sources').read_text().splitlines():
    expected,name=line.split(None,1)
    source=VENDOR/name.strip()
    if source not in INPUTS.values(): INPUTS['dependency/'+name.strip()]=source
INPUTS.update({'patched/usbd.c':BRIDGE/'build/usbd.c','patched/dcd_stm32_fsdev.c':BRIDGE/'build/dcd_stm32_fsdev.c',
 'support/fake_dcd.c':CDC/'fake_dcd.c','support/fake_dcd.h':CDC/'fake_dcd.h',
 'support/config/tusb_config.h':CDC/'config-f1/tusb_config.h',
 'support/instrument_fake.py':SUPPORT/'instrument_fake.py',
 'tests/test_runtime.c':HERE/'test_runtime.c','tests/test_integration.h':HERE/'test_integration.h',
 'tests/run.py':Path(__file__).resolve()})
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def fingerprint(): return {str(path.resolve()):sha(path) for path in sorted(set(INPUTS.values()))}
BEFORE=fingerprint()
OUT.mkdir(parents=True,exist_ok=False)
STAGE=OUT/'snapshot'
RECORD={'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'invocation':sys.argv,
 'source_roots':[str(x) for x in SOURCE_ROOTS], 'inputs_before':BEFORE,
 'results':[],'commands':[],'status':'RUNNING','sanitize':A.sanitize,
 'scope':'Real TIME001 ADC003 controller RX USB005 TinyUSB core/CDC/FIFO. Fake MMIO, DCD, IRQ mask; synthetic ADC samples.',
 'hardware_claimed':False,'target_irq_claimed':False,'timing_claimed':False,'leak_sanitizer_claimed':False}
(OUT/'inputs-before.json').write_text(json.dumps(BEFORE,indent=2)+'\n')
ENV=dict(os.environ)
COMMON=['-g','-O1','-fno-lto','-Wall','-Wextra','-fno-omit-frame-pointer']
if A.sanitize:
    COMMON+=['-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-pie','-no-pie']
    ENV.update(ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
RECORD['runtime_environment']={k:ENV.get(k) for k in ('ASAN_OPTIONS','UBSAN_OPTIONS')}
CC=ENV.get('CC','cc')
EXIT=0
with (OUT/'commands.log').open('w') as log:
    def run(command,required=True):
        log.write('+ '+shlex.join(command)+'\n');log.flush()
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=ENV,cwd=OUT)
        RECORD['commands'].append({'argv':command,'exit_status':result.returncode,'output':result.stdout})
        log.write(result.stdout);log.flush();print(result.stdout,end='',flush=True)
        if required and result.returncode: raise RuntimeError(f'command failed ({result.returncode}): {command[0]}')
        return result
    try:
        for relative,source in INPUTS.items():
            target=STAGE/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
            assert sha(target)==BEFORE[str(source.resolve())]
        # Verify pristine inputs before using patched USB005 core separately.
        for line in (VENDOR/'SHA256SUMS.sources').read_text().splitlines():
            expected,name=line.split(None,1); assert sha(VENDOR/name.strip())==expected
        # Existing test-only generator changes DCD hooks and event pump only.
        spec=importlib.util.spec_from_file_location('instrument_fake',STAGE/'support/instrument_fake.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        proxy=OUT/'generator-input';(proxy/'build').mkdir(parents=True)
        shutil.copyfile(STAGE/'patched/dcd_stm32_fsdev.c',proxy/'build/dcd_stm32_fsdev.c')
        module.generate(STAGE/'support',OUT,proxy)
        GENERATED=OUT/'fake_dcd_instrumented.c'
        RECORD['generated_sha256']={str(GENERATED):sha(GENERATED)}
        INCS=['-DSTM32F103xB','-DCFG_MT_USB_BRIDGE=1','-I'+str(STAGE/'support/config'),
              '-I'+str(STAGE/'support'),'-I'+str(STAGE/'tests')]
        for directory in ['app','platform','transport','protocol','acquisition']:
            INCS+=['-I'+str(STAGE/'firmware'/directory)]
        INCS+=['-isystem',str(STAGE/'vendor/tinyusb/src')]
        upstream=[STAGE/'vendor/tinyusb/src/common/tusb_fifo.c',STAGE/'vendor/tinyusb/src/tusb.c',
                  STAGE/'patched/usbd.c',STAGE/'vendor/tinyusb/src/class/cdc/cdc_device.c']
        project=[STAGE/('firmware/'+x+'.c') for x in PROJECT]+[STAGE/'tests/test_runtime.c',GENERATED]
        run([CC,'--version']);objects=[]
        for index,source in enumerate(upstream+project):
            obj=OUT/(str(index)+'_'+source.stem+'.o');objects.append(str(obj))
            flags=['-std=gnu11'] if source in upstream else ['-std=c11','-pedantic-errors','-Werror','-Wconversion','-Wshadow']
            run([CC,*COMMON,*flags,*INCS,'-c',str(source),'-o',str(obj)])
        exe=OUT/'test_runtime';run([CC,*COMMON,*objects,'-o',str(exe)])
        RECORD['executable_sha256']=sha(exe)
        names=run([str(exe),'--list']).stdout.splitlines()
        if A.cases:
            if not set(A.cases).issubset(names): raise ValueError('unknown requested case')
            names=A.cases
        for name in names:
            response=run([str(exe),name],required=False)
            result={'case':name,'pass':response.returncode==0,'exit_status':response.returncode}
            if response.returncode==0: result.update(json.loads(response.stdout.strip()))
            else: result['output']=response.stdout
            RECORD['results'].append(result)
        RECORD['status']='PASS' if all(x['pass'] for x in RECORD['results']) else 'FAIL'
        if RECORD['status']=='FAIL':EXIT=1
    except Exception as error:
        RECORD['status']='FAIL';RECORD['error']=repr(error);print(error,file=sys.stderr);EXIT=1
    finally:
        AFTER=fingerprint();RECORD['inputs_after']=AFTER;RECORD['inputs_unchanged']=BEFORE==AFTER
        if BEFORE!=AFTER: RECORD['status']='FAIL_INPUT_CHANGED';EXIT=1
        RECORD['finish_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        (OUT/'inputs-after.json').write_text(json.dumps(AFTER,indent=2)+'\n')
        (OUT/'results.json').write_text(json.dumps(RECORD['results'],indent=2)+'\n')
        (OUT/'receipt.json').write_text(json.dumps(RECORD,indent=2)+'\n')
print(f"{RECORD['status']}: {sum(x['pass'] for x in RECORD['results'])}/{len(RECORD['results'])}; {OUT/'receipt.json'}")
raise SystemExit(EXIT)

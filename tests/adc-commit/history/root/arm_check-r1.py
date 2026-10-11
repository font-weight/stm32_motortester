#!/usr/bin/env python3
"""Compile pinned candidate and baseline ADC objects; no target execution."""
import argparse, hashlib, json, subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--candidate',type=Path,required=True);p.add_argument('--base',type=Path,required=True);p.add_argument('--toolchain',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
roots=[a.candidate.resolve(),a.base.resolve(),a.toolchain.resolve()];out=a.out.resolve()
if a.out.exists() or a.out.is_symlink() or any(out.is_relative_to(x) for x in roots): p.error('choose a fresh output outside all input roots')
files=sorted(set([Path(__file__).resolve()]+[f for root in roots[:2] for f in (root/'firmware').rglob('*') if f.is_file() and 'third_party' not in f.parts]))
def hashes():return {str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
before=hashes();commands=[];a.out.mkdir(parents=True,exist_ok=False)
bin=a.toolchain/'bin';flags=['-std=c11','-Os','-mcpu=cortex-m3','-mthumb','-mfloat-abi=soft','-ffreestanding','-ffunction-sections','-fdata-sections','-fno-common','-fno-short-enums','-fstack-usage','-Wall','-Wextra','-Wpedantic','-Wconversion','-Wsign-conversion','-isystem',str(a.toolchain/'include/newlib')]
def run(cmd):
 r=subprocess.run([str(x) for x in cmd],capture_output=True,text=True);commands.append({'argv':[str(x) for x in cmd],'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr});r.check_returncode();return r.stdout
try:
 run([bin/'arm-none-eabi-gcc','--version'])
 for label,root in [('candidate',a.candidate),('base',a.base)]:
  for kind,source in [('driver','platform/adc_timer_dma.c'),('binding','app/mt_adc_control.c')]:
   stem=label+'-'+kind;obj=out/(stem+'.o')
   run([bin/'arm-none-eabi-gcc']+flags+['-I'+str(root/'firmware/app'),'-I'+str(a.base/'firmware/app'),'-MD','-MF',str(out/(stem+'.d')),'-c',str(root/'firmware'/source),'-o',str(obj)])
   for tool,args,suffix in [('size',['-A'],'size'),('nm',['-u'],'nm'),('objdump',['-dr'],'disassembly')]:
    (out/(stem+'-'+suffix+'.txt')).write_text(run([bin/('arm-none-eabi-'+tool),*args,obj]))
finally:
 after=hashes();(out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n');(out/'inputs.json').write_text(json.dumps({'before':before,'after':after,'inputs_unchanged':before==after},indent=2)+'\n')
 if before!=after:raise RuntimeError('source inputs changed')
print('Four ARM objects compiled; source inputs unchanged. No full ELF or hardware result.')

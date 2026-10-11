#!/usr/bin/env python3
"""Compile object-only ABI sizes for candidate and preserved baseline headers."""
import argparse,hashlib,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--candidate',type=Path,required=True);p.add_argument('--base',type=Path,required=True);p.add_argument('--toolchain',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();here=Path(__file__).resolve().parent;out=a.out.resolve()
for x in (a.candidate,a.base,a.toolchain,here):
 root=x.resolve()
 if out==root or root in out.parents:p.error('output overlaps inputs')
if a.out.exists() or a.out.is_symlink():p.error('fresh output required')
files=[here/'abi_probe.c']+[r/'firmware'/x for r in (a.candidate,a.base) for x in ('platform/adc_timer_dma.h','app/mt_adc_control.h')];before={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in files};out.mkdir(parents=True,exist_ok=False);cmds=[];sizes={}
for label,src,flags in [('candidate',a.candidate,[]),('base',a.base,['-DCRITIC_BASE'])]:
 obj=out/(label+'.o');cmd=[str(a.toolchain/'bin/arm-none-eabi-gcc'),'-std=c11','-Os','-mcpu=cortex-m3','-mthumb','-fno-short-enums','-ffreestanding','-Wall','-Wextra','-Werror','-I'+str(src/'firmware/app'),'-I'+str(a.base/'firmware/app'),'-isystem',str(a.toolchain/'include/newlib'),*flags,'-c',str(here/'abi_probe.c'),'-o',str(obj)]
 for argv in (cmd,[str(a.toolchain/'bin/arm-none-eabi-nm'),'-S','--size-sort',str(obj)]):
  r=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);cmds.append(dict(argv=argv,exit_status=r.returncode,output=r.stdout));assert r.returncode==0,cmds[-1]
 sizes[label]={line.split()[-1]:int(line.split()[1],16) for line in r.stdout.splitlines()};sizes[label]['object_sha256']=hashlib.sha256(obj.read_bytes()).hexdigest()
after={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in files};assert before==after
assert sizes['candidate']['critic_driver_size']==sizes['base']['critic_driver_size']==860
assert sizes['candidate']['critic_binding_size']==44 and sizes['base']['critic_binding_size']==24
assert sizes['candidate']['critic_guard_size']==16
(out/'receipt.json').write_text(json.dumps(dict(result='PASS',scope='ARM object ABI only; no runtime/link',inputs=before,inputs_unchanged=True,commands=cmds,sizes=sizes),indent=2)+'\n');print(json.dumps(sizes,indent=2))

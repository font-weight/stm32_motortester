#!/usr/bin/env python3
"""Wrap the unchanged existing runtime runner in clean-environment TX overlay."""
import argparse, datetime, hashlib, json, os
from pathlib import Path
import shutil, subprocess, sys
sys.dont_write_bytecode=True
P=argparse.ArgumentParser(description=__doc__)
for name in ['runtime_tests','runtime','base','candidate','adc','bridge','epoch','dependencies','cdc_support','bridge_support']:
    P.add_argument('--'+name.replace('_','-'),required=True,type=Path)
P.add_argument('--out',required=True,type=Path);P.add_argument('--sanitize',action='store_true')
A=P.parse_args();HERE=Path(__file__).absolute().parent
compiler=shutil.which('cc',path='/usr/bin:/bin')
if not compiler:P.error('native compiler unavailable')
compiler=str(Path(compiler).resolve())
toolchain={'driver':{'resolved':compiler,'sha256':hashlib.sha256(Path(compiler).read_bytes()).hexdigest()}}
for name in ['cc1','as','ld','collect2']:
    result=subprocess.run([compiler,'-print-prog-name='+name],env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if result.returncode:P.error('compiler helper query failed: '+name)
    tool=shutil.which(result.stdout.strip(),path='/usr/bin:/bin')
    if not tool:P.error('compiler helper unavailable: '+name)
    tool=str(Path(tool).resolve());toolchain[name]={'resolved':tool,'sha256':hashlib.sha256(Path(tool).read_bytes()).hexdigest()}
roots=[getattr(A,n) for n in ['runtime_tests','runtime','base','candidate','adc','bridge','epoch','dependencies','cdc_support','bridge_support']]+[HERE]
files=set(f for root in roots for f in root.rglob('*') if f.is_file())
protected={p for root in roots for p in [Path(os.path.abspath(root)),root.resolve()]}
protected.update(p for f in files for p in [Path(os.path.abspath(f)).parent,f.resolve().parent])
protected.update(Path(item['resolved']).parent for item in toolchain.values())
def overlap(a,b):return a==b or a in b.parents or b in a.parents
for out in [Path(os.path.abspath(A.out)),A.out.resolve()]:
    if any(overlap(out,p) for p in protected):P.error('output overlaps protected input')
if os.path.lexists(A.out):P.error('output must be fresh')
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def fingerprint():return {str(f):{'resolved':str(f.resolve()),'sha256':sha(f)} for f in sorted(files)}
for root,digest in [(A.base,'e5b38052c5fef29cb178b15ca589a2414047b51f473046571f04ab29800fa79e'),(A.candidate,'b706eefbfeedd22941980b456d1a81fb0bbb7da91b8d895b206f43137b369ddf')]:
    if sha(root/'firmware/transport/mt_tx.c')!=digest:P.error('TX source SHA mismatch')
before=fingerprint();A.out.mkdir(parents=True);(A.out/'temp').mkdir()
record={'status':'RUNNING','input_before':before,'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'invocation':sys.argv,'sanitize':A.sanitize,'toolchain_before':toolchain}
env={'PATH':'/usr/bin:/bin','LC_ALL':'C','LANG':'C','HOME':str(A.out.resolve()),'TMPDIR':str((A.out/'temp').resolve()),'PYTHONDONTWRITEBYTECODE':'1','CC':compiler}
try:
    overlay=A.out/'base-overlay';shutil.copytree(A.base,overlay)
    shutil.copyfile(A.candidate/'firmware/transport/mt_tx.c',overlay/'firmware/transport/mt_tx.c')
    shutil.copyfile(A.candidate/'firmware/transport/mt_tx.h',overlay/'firmware/transport/mt_tx.h')
    command=['/usr/bin/python3',str(A.runtime_tests/'run.py'),'--out',str(A.out/'run'),'--base',str(overlay)]
    for name in ['runtime','adc','bridge','epoch','dependencies','cdc_support','bridge_support']:
        command+=['--'+name.replace('_','-'),str(getattr(A,name))]
    if A.sanitize:command+=['--sanitize']
    record['command']=command;record['environment']=env
    result=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    (A.out/'commands.log').write_text(result.stdout);record['exit']=result.returncode
    inner=json.loads((A.out/'run/receipt.json').read_text());record['runtime_results']=inner['results']
    # Prove that selection order actually used the candidate overlay TX.
    actual=A.out/'run/snapshot/firmware/transport/mt_tx.c';record['compiled_tx_sha256']=sha(actual)
    assert sha(actual)==sha(A.candidate/'firmware/transport/mt_tx.c')
    assert inner['inputs_unchanged'] and result.returncode==0 and inner['status']=='PASS'
    record['status']='PASS'
except Exception as error:record['status']='FAIL';record['error']=repr(error)
finally:
    record['input_after']=fingerprint();record['inputs_unchanged']=before==record['input_after']
    if not record['inputs_unchanged']:record['status']='FAIL_INPUT_CHANGED'
    record['toolchain_after']={name:{**item,'sha256':sha(Path(item['resolved']))} for name,item in toolchain.items()}
    record['toolchain_unchanged']=record['toolchain_before']==record['toolchain_after']
    if not record['toolchain_unchanged']:record['status']='FAIL_TOOLCHAIN_CHANGED'
    (A.out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(record['status'],str(A.out/'receipt.json'));sys.exit(record['status']!='PASS')

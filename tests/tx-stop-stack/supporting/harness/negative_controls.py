#!/usr/bin/env python3
"""Run compile-valid local mutants; assertion failures, not builds, detect them."""
import argparse, concurrent.futures, hashlib, json, os
from pathlib import Path
import subprocess, sys
sys.dont_write_bytecode=True
P=argparse.ArgumentParser(description=__doc__)
P.add_argument('--base',required=True,type=Path);P.add_argument('--candidate',required=True,type=Path)
P.add_argument('--original',required=True,type=Path);P.add_argument('--out',required=True,type=Path)
A=P.parse_args();HERE=Path(__file__).absolute().parent
def overlap(a,b):return a==b or a in b.parents or b in a.parents
roots=[A.base,A.candidate,A.original,HERE]
paths=[f for root in roots for f in root.rglob('*') if f.is_file()]
protected={x for root in roots for x in [Path(os.path.abspath(root)),root.resolve()]}
protected.update(x for p in paths for x in [Path(os.path.abspath(p)).parent,p.resolve().parent])
for out in [Path(os.path.abspath(A.out)),A.out.resolve()]:
    if any(overlap(out,p) for p in protected):P.error('output overlaps protected input')
if os.path.lexists(A.out):P.error('output must be fresh')
def fingerprints():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
before=fingerprints();A.out.mkdir(parents=True)
env={'PATH':'/usr/bin:/bin','LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1'}
mutants=['drop_reason','drop_tail','wrong_session','wrong_type','skip_zero_init','wrong_length','retain_as_reply','ignore_prior_replies','weaken_capacity','early_mutation']
def test(name):
    dest=A.out/name
    cmd=['/usr/bin/python3',str(HERE/'run.py'),'--base',str(A.base),'--candidate',str(A.candidate),'--original',str(A.original),'--out',str(dest),'--mutant',name]
    result=subprocess.run(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    receipt=json.loads((dest/'receipt.json').read_text())
    compiler=receipt['toolchain_before']['driver']['resolved']
    compiler_commands=[r for r in receipt['commands'] if r['argv'][0]==compiler]
    compiler_success=bool(compiler_commands) and all(r['exit']==0 for r in compiler_commands)
    failed=[r for r in receipt['results'] if not r['pass']]
    assertions=all('Assertion ' in r.get('output','') for r in failed)
    detected=result.returncode!=0 and compiler_success and bool(failed) and assertions and receipt['inputs_unchanged'] and receipt['toolchain_unchanged']
    return {'mutant':name,'detected':detected,'compiler_success':compiler_success,'failed_assertions':[r['case'] for r in failed],'exit':result.returncode,'receipt':str(dest/'receipt.json')}
record={'status':'RUNNING','input_before':before,'results':[]}
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:record['results']=list(pool.map(test,mutants))
    record['status']='PASS' if all(x['detected'] for x in record['results']) else 'FAIL'
except Exception as error:record['status']='FAIL';record['error']=repr(error)
finally:
    record['input_after']=fingerprints();record['inputs_unchanged']=record['input_before']==record['input_after']
    if not record['inputs_unchanged']:record['status']='FAIL_INPUT_CHANGED'
    (A.out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print(record['status'],[(x['mutant'],x['detected']) for x in record['results']]);sys.exit(record['status']!='PASS')

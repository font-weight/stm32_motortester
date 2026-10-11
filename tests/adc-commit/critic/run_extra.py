#!/usr/bin/env python3
"""Recompile exact-span assertions against a preserved supporting ADC run snapshot."""
import argparse,hashlib,json,os,shutil,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--base-run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();here=Path(__file__).resolve().parent;base=a.base_run.resolve();out=a.out.resolve()
for root in (here,base):
 if out==root or root in out.parents:p.error('output must be disjoint from inputs')
if a.out.exists() or a.out.is_symlink():p.error('output must be fresh')
receipt=json.loads((base/'receipt.json').read_text());assert receipt['status']=='PASS' and receipt['inputs_unchanged']
def hashes():return {str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in (base/'snapshot').rglob('*') if x.is_file()}
before=hashes();out.mkdir(parents=True,exist_ok=False);shutil.copytree(base/'snapshot',out/'snapshot');testdir=out/'snapshot/tests/adc-commit';src=(testdir/'test_admission.c').read_text();assert src.count('int main(void)')==1
(testdir/'test_admission_fixture.c').write_text(src.replace('int main(void)','int supporting_main(void)'))
shutil.copyfile(here/'test_extra.c',testdir/'test_extra.c');commands=[];result='FAIL'
try:
 for old in receipt['commands'][-2:]:
  argv=[x.replace(str(base),str(out)).replace('/test_admission.c','/test_extra.c') for x in old['argv']]
  env=os.environ.copy();env.update({k:v for k,v in receipt['runtime_environment'].items() if v is not None})
  r=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env);commands.append(dict(argv=argv,exit_status=r.returncode,output=r.stdout));assert r.returncode==0,commands[-1];print(r.stdout,end='')
 result='PASS'
finally:
 after=hashes()
 if before!=after:result='FAIL_INPUT_CHANGED'
 (out/'receipt.json').write_text(json.dumps(dict(status=result,input_before=before,input_after=after,inputs_unchanged=before==after,commands=commands,fixture_change='Only int main(void) renamed to int supporting_main(void); all assertions retained',test_sha256=hashlib.sha256((here/'test_extra.c').read_bytes()).hexdigest()),indent=2)+'\n')
assert result=='PASS'

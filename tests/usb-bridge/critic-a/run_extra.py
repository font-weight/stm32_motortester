#!/usr/bin/env python3
"""Recompile supplemental checks from a fresh supporting core run's exact commands."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--base-run',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
here=Path(__file__).resolve().parent;base=a.base_run.resolve();out=a.out.resolve()
receipt=json.loads((base/'receipt.json').read_text());assert receipt['result']=='PASS'
for root in [base,here]+[Path(x).resolve() for x in receipt['source_roots']]:
 if out==root or root in out.parents: p.error('output must be outside all source/base-run inputs')
inputs={Path(x):v for x,v in receipt['inputs'].items()};inputs[base/'fake_dcd_instrumented.c']=hashlib.sha256((base/'fake_dcd_instrumented.c').read_bytes()).hexdigest();inputs[here/'test_extra.c']=hashlib.sha256((here/'test_extra.c').read_bytes()).hexdigest()
for path,d in inputs.items(): assert hashlib.sha256(path.read_bytes()).hexdigest()==d,path
out.mkdir(parents=True,exist_ok=False);commands=[]
env=os.environ.copy();env.update(ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
try:
 for item in receipt['commands']:
  cmd=item['argv']; changed=[]
  for part in cmd:
   if part.endswith('/test_integration.c'): part=str(here/'test_extra.c')
   elif part.startswith(str(base)+'/') and not part.endswith('fake_dcd_instrumented.c'): part=str(out)+part[len(str(base)):]
   changed.append(part)
  r=subprocess.run(changed,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env)
  commands.append(dict(argv=changed,exit_status=r.returncode,output=r.stdout))
  assert r.returncode==0,commands[-1]
  print(r.stdout,end='')
 for path,d in inputs.items(): assert hashlib.sha256(path.read_bytes()).hexdigest()==d,path
 result='PASS'
finally:
 changed_inputs=[str(path) for path,d in inputs.items() if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=d]
 if changed_inputs: result='FAIL'
 (out/'receipt.json').write_text(json.dumps(dict(base_run=str(base),inputs={str(k):v for k,v in inputs.items()},commands=commands,changed_inputs=changed_inputs,result=locals().get('result','FAIL')),indent=2)+'\n')
assert result=='PASS'
print('PASS: 10 supplemental scenarios; supporting fixture reused; inputs unchanged')

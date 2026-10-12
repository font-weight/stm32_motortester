#!/usr/bin/env python3
"""Run buffer-only EP0 negative/control traces against generated supporting bodies."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--supporting',type=Path,required=True);p.add_argument('--model-build',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
support=a.supporting.resolve();build=a.model_build.resolve();out=a.out.resolve();test=HERE/'test_legal_ep0_buffers.c'
for root in [support,build,HERE]:
 if out==root or root in out.parents:p.error('output must be outside input trees')
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
# These manifests were produced from exact pinned input by the supporting runner.
pins={'original':'62b73c5fab0cbda70df6bd1bc1c9a7df0c8a59ea02c38c884b24b55e44ddfc9f','candidate':'1326ac072ff0f687f6589e5045184c77d578687f13e58b85b1e783aa2946fe97'}
for name,pin in pins.items():assert json.loads((build/name/'extraction-manifest.json').read_text())['source_sha256']['driver']==pin
sources=[test]+sorted((support/'src').glob('*'))+[f for name in pins for f in sorted((build/name).glob('*.inc'))]+[build/name/'extraction-manifest.json' for name in pins]
before={str(f):sha(f) for f in sources if f.is_file()};out.mkdir(parents=True,exist_ok=False);records=[]
for variant in pins:
 for san in [False,True]:
  binary=out/(variant+('-san' if san else '-strict'))
  argv=['gcc','-std=c11','-pedantic-errors','-Wall','-Wextra','-Werror','-Wshadow','-O1','-g','-fno-omit-frame-pointer','-fno-lto','-no-pie','-DMODEL_ENDPOINTS=3','-DEXPECT_FIXED='+str(int(variant=='candidate'))]
  if variant=='candidate':argv+=['-DSTM32F103xB=1']
  if san:argv+=['-fsanitize=address,undefined','-fno-sanitize-recover=all']
  argv+=['-I',str(build/variant),'-I',str(support/'src'),str(test),'-o',str(binary)]
  env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
  for cmd in [argv,[str(binary)]]:
   r=subprocess.run(cmd,env=env,text=True,capture_output=True);records.append({'argv':cmd,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr});(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n');print(r.stdout+r.stderr,end='',flush=True);assert r.returncode==0
assert before=={str(f):sha(f) for f in sources if f.is_file()}
receipt={'result':'PASS','original_negative_directions':2,'candidate_clean_directions':2,'binaries':4,'inputs_sha256':before,'scope':'shared register model; exclusive buffer-only EP0 fields, not real MMIO','ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS']};(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')

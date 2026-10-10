#!/usr/bin/env python3
"""Disposable-input reproduction: reject ARM output inside any source tree before writing."""
import argparse, hashlib, json, subprocess
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--runner',type=Path,required=True); p.add_argument('--out',type=Path,required=True); a=p.parse_args()
a.out.mkdir(parents=True,exist_ok=False)
results=[]
for name in ('bridge','epoch','adapter','setup','reset','dependencies'):
 for mode in ('equal','nested','symlink'):
  root=a.out/(name+'-'+mode); root.mkdir(); sources={n:root/n for n in ('bridge','epoch','adapter','setup','reset','dependencies')}
  for d in sources.values(): d.mkdir()
  dst=sources[name] if mode=='equal' else sources[name]/'new-output'
  if mode=='symlink':
   (root/'alias').symlink_to(sources[name],target_is_directory=True); dst=root/'alias'/'new-output'
  before=sorted(str(x.relative_to(root)) for x in root.rglob('*'))
  cmd=['python3',str(a.runner),'--output',str(dst)]
  for n,d in sources.items(): cmd += ['--'+n,str(d)]
  r=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  after=sorted(str(x.relative_to(root)) for x in root.rglob('*'))
  results.append(dict(source=name,mode=mode,command=cmd,exit_status=r.returncode,source_unchanged=before==after,output=r.stdout))
(a.out/'receipt.json').write_text(json.dumps(dict(runner_sha256=hashlib.sha256(a.runner.read_bytes()).hexdigest(),cases=results),indent=2)+'\n')
print(json.dumps({ 'rejected_without_writes':sum(x['source_unchanged'] and x['exit_status']!=0 for x in results),'cases':len(results)}))

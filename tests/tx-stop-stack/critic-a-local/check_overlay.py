#!/usr/bin/env python3
"""Verify optional TX002 overlay against a supplied unchanged-memory recipe.
The reference receipt supplies dependency root paths. They must exist locally;
use a newly generated reference when relocating this check.
"""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path as P
ap=argparse.ArgumentParser(description=__doc__)
for n in ['recipe','previous-recipe','reference','candidate','out']:ap.add_argument('--'+n,type=P,required=True)
a=ap.parse_args()
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
ref=json.loads(a.reference.read_text());argv=ref['invocation'][1:]
assert len(argv)%2==0
pairs=dict(zip(argv[::2],argv[1::2]));pairs.pop('--out');pairs.pop('--tx-stop',None)
required={'--base','--adc','--bridge','--epoch','--runtime','--irq','--dependencies','--config','--toolchain','--memory','--descriptors'}
assert set(pairs)==required
files=[a.recipe,a.previous_recipe,a.reference,P(__file__),a.candidate/'firmware/transport/mt_tx.c',a.candidate/'firmware/transport/mt_tx.h']
roots=[P(x) for x in pairs.values()]+[a.candidate]
inputs={P(p) for p in ref['inputs_before']}|set(files)
assert all(p.is_file() for p in inputs)
assert sha(a.candidate/'firmware/transport/mt_tx.c')=='b706eefbfeedd22941980b456d1a81fb0bbb7da91b8d895b206f43137b369ddf'
assert sha(a.candidate/'firmware/transport/mt_tx.h')=='68f013a8ffb6e946e9cd92f3e760bf593746afdc7097d11d837a101c5ab83dd9'
protected=[d for p in inputs for d in [p.absolute().parent,p.resolve().parent]]+[d for p in roots for d in [p.absolute(),p.resolve()]]
raw=a.out.absolute();out=a.out.resolve()
if os.path.lexists(raw) or any(q==p or q in p.parents or p in q.parents for p in protected for q in [raw,out]):ap.error('fresh disjoint output required')
before={str(p.resolve()):sha(p) for p in inputs}
out.mkdir(parents=True);runs={};record={'status':'RUNNING','invocation':sys.argv,'inputs_before':before,'runs':runs}
try:
 for label,descs,mem in [('baseline',False,False),('descriptors',True,False),('memory',False,True),('both',True,True)]:
  modes={}
  for name,recipe,tx in [('previous',a.previous_recipe,False),('new-off',a.recipe,False),('new-on',a.recipe,True)]:
   target=out/(label+'-'+name)
   opts={k:v for k,v in pairs.items() if k not in ['--memory','--descriptors']}
   if descs:opts['--descriptors']=pairs['--descriptors']
   if mem:opts['--memory']=pairs['--memory']
   if tx:opts['--tx-stop']=str(a.candidate)
   cmd=[sys.executable,str(recipe),*[x for k,v in opts.items() for x in [k,v]],'--out',str(target)]
   r=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
   (out/(label+'-'+name+'.log')).write_text(repr(cmd)+'\n'+r.stdout+r.stderr)
   assert r.returncode==0,r.stdout+r.stderr
   d=json.loads((target/'receipt.json').read_text());assert d['status']=='PASS_RELOCATABLE_ONLY' and d['inputs_unchanged']
   exp={'tusb_time_millis_api'}
   if not descs:exp|={'tud_descriptor_configuration_cb','tud_descriptor_device_cb','tud_descriptor_string_cb'}
   if not mem:exp|={'memcmp','memcpy','memmove','memset'}
   assert set(d['expected_external_symbols'])==exp
   compiles=[c for c in d['commands'] if c['label'].startswith('compile-')];assert len(compiles)==23+int(descs)+int(mem)
   objects={k.split('-',1)[-1]:v for k,v in d['objects_sha256'].items() if k!='components.o'}
   compiled=target/'snapshot/firmware/transport/mt_tx.c'
   assert sha(compiled)==('b706eefbfeedd22941980b456d1a81fb0bbb7da91b8d895b206f43137b369ddf' if tx else 'e5b38052c5fef29cb178b15ca589a2414047b51f473046571f04ab29800fa79e')
   modes[name]={'command':cmd,'receipt_sha256':sha(target/'receipt.json'),'object_hashes':objects,'combined':d['objects_sha256']['components.o'],'expected_externals':sorted(exp),'units':len(compiles)}
  assert modes['previous']['object_hashes']==modes['new-off']['object_hashes']
  assert modes['previous']['combined']==modes['new-off']['combined']
  changed=[n for n,h in modes['previous']['object_hashes'].items() if modes['new-on']['object_hashes'][n]!=h]
  assert changed==['mt_tx.o'],changed
  assert modes['new-on']['combined']!=modes['previous']['combined']
  runs[label]=modes
 record['inputs_after']={str(p.resolve()):sha(p) for p in inputs};assert record['inputs_after']==before
 record['status']='PASS'
finally:(out/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
print('PASS: 12 component links, four combinations; all overlay-off objects exact; only TX and aggregate changed with overlay')

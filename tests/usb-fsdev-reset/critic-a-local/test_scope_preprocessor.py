#!/usr/bin/env python3
"""Check exact-source scope and non-F103 conditional invariance; no MMIO execution."""
from pathlib import Path
import argparse,hashlib,json,subprocess
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--upstream',type=Path,required=True);p.add_argument('--patched',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
sha=lambda b:hashlib.sha256(b).hexdigest()
u=a.upstream.read_bytes();v=a.patched.read_bytes()
assert sha(u)=='62b73c5fab0cbda70df6bd1bc1c9a7df0c8a59ea02c38c884b24b55e44ddfc9f'
assert sha(v)=='1326ac072ff0f687f6589e5045184c77d578687f13e58b85b1e783aa2946fe97'
sig='static void handle_bus_reset(uint8_t rhport) {'
def split(b):
 s=b.decode();assert s.count(sig)==1;start=s.index(sig);end=s.index('\n}\n',start)+2;return s[:start],s[start:end],s[end:]
us,ub,ue=split(u);vs,vb,ve=split(v);assert us==vs and ue==ve
rows=[]
for macro in [None,'STM32F102xB','STM32F103x6','STM32F103xE','STM32F105xC','TUP_USBIP_FSDEV_CH32','STM32F103xB']:
 argv=['gcc','-E','-P','-x','c','-']+(['-D'+macro] if macro else [])
 outputs=[]
 for body in [ub,vb]:
  r=subprocess.run(argv,input=body,text=True,capture_output=True);assert r.returncode==0 and not r.stderr;outputs.append(r.stdout)
 if macro!='STM32F103xB':assert outputs[0]==outputs[1]
 else:
  assert outputs[1].count('ep_write(i, 0, false);')==1
  assert outputs[1].count('tu_memclr(xfer_status, sizeof(xfer_status));')==1
  stripped='\n'.join(s for s in outputs[1].splitlines() if 'ep_write(i, 0, false);' not in s and 'tu_memclr(xfer_status, sizeof(xfer_status));' not in s)+'\n'
  assert stripped==outputs[0]
 rows.append({'macro':macro,'argv':argv,'upstream_preprocessed_sha256':sha(outputs[0].encode()),'patched_preprocessed_sha256':sha(outputs[1].encode()),'expected_delta_only':True})
a.out.write_text(json.dumps({'result':'PASS','upstream_sha256':sha(u),'patched_sha256':sha(v),'outside_reset_body_unchanged':True,'cases':rows,'scope':'conditional preprocessing only, not other-device build or execution'},indent=2)+'\n')
print('PASS: 6 non-F103 preprocessing controls unchanged; F103 exactly two intended operations; all other source bytes unchanged')

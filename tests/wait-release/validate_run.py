#!/usr/bin/env python3
"""Explicit aggregate gate for the collector. Expected negative controls are named."""
import argparse,json,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('--expected-fail',default='');p.add_argument('--cases',type=int,default=25)
a=p.parse_args();root=Path(a.directory);expected=set(filter(None,a.expected_fail.split(',')))
rows=[line.split('\t') for line in (root/'results.tsv').read_text().splitlines()]
assert len(rows)==a.cases*2
bycase={}
for case,mode,code in rows:
 assert mode in ('native','sanitized')
 assert mode not in bycase.setdefault(case,{})
 bycase[case][mode]=int(code)
assert len(bycase)==a.cases and expected<=set(bycase)
for case,modes in bycase.items():
 assert set(modes)=={'native','sanitized'}
 for mode,code in modes.items():assert code==(1 if case in expected else 0),(case,mode,code)
 normal=root/('native-'+case);san=root/('sanitized-'+case)
 assert (normal/'run.log').read_bytes()==(san/'run.log').read_bytes(),case
 data=(san/'run.log').read_bytes()
 assert b'AddressSanitizer' not in data and b'runtime error:' not in data
 paths=sorted(p.name for p in (normal/'build').glob('*.bin'))
 assert paths==sorted(p.name for p in (san/'build').glob('*.bin'))
 for name in paths:assert (normal/'build'/name).read_bytes()==(san/'build'/name).read_bytes()
print(f'PASS aggregate: {a.cases} cases x2 modes; {len(expected)} explicitly expected negative cases; all logs and wire snapshots identical')
subprocess.run([sys.executable,'-I','-B',str(Path(__file__).with_name('decode_wire.py')),str(root),'--output',str(root/'independent-wire.json')],check=True)

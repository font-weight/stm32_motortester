#!/usr/bin/env python3
"""Reproduce read-only object/disassembly closure evidence and check code pointers."""
import pathlib,json,subprocess,hashlib,re
P=pathlib.Path('/workspace/shared/device-main-arm/run-r2');O=pathlib.Path(__file__).parent;T=pathlib.Path('/workspace/shared/arm-resource-evidence/toolchain/root/usr/bin')
R=json.loads((P/'RESULTS.json').read_text());G=json.loads((O/'graph-initial.json').read_text());fn={f['name'] for f in G.values()}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for n in ['objdump','readelf']:
 p=T/('arm-none-eabi-'+n);assert sha(p)==R['inputs_before'][str(p)]
elf=P/'TEST_ONLY_INVALID_BOARD_STATIC_PLACEMENT_NOT_FLASHABLE.elf';assert sha(elf)==R['objects_sha256'][elf.name]
out=subprocess.run([str(T/'arm-none-eabi-objdump'),'-dr',str(elf)],capture_output=True,text=True,check=True).stdout
assert out==(P/'final-disassembly.stdout').read_text();(O/'regenerated-disassembly.txt').write_text(out)
with (O/'object-relocations.txt').open('w') as f:
 for obj in R['objects_sha256']:
  if obj.endswith('.o') and obj!='combined.o':
   p=P/obj if obj!='reset.o' else P/'boot'/obj
   assert sha(p)==R['objects_sha256'][obj]
   f.write('\nFILE '+obj+'\n');f.write(subprocess.run([str(T/'arm-none-eabi-readelf'),'-rW',str(p)],capture_output=True,text=True,check=True).stdout)
rels=[];allrels=[];obj='';sec=''
for line in (O/'object-relocations.txt').read_text().splitlines():
 if line.startswith('FILE '):obj=line[5:].strip()
 m=re.match("Relocation section '([^']+)'",line)
 if m:sec=m[1]
 if re.match(r'^[0-9a-f]+\s+[0-9a-f]+\s+R_ARM_',line):
  sym=line.split()[-1];item=dict(object=obj,section=sec,offset=line.split()[0],symbol=sym);allrels.append(item)
  if 'R_ARM_ABS32' in line and (sym in fn or sym.startswith('.text.')):rels.append(item)
assert not [x for x in allrels if x['symbol'] in ('usbd_defer_func','.text.usbd_defer_func')]
class_ptrs=[x for x in rels if x['section']=='.rel.rodata._usbd_driver']
assert [(x['offset'],x['symbol']) for x in class_ptrs]==[('00000004','cdcd_init'),('00000008','cdcd_deinit'),('0000000c','cdcd_reset'),('00000010','cdcd_open'),('00000014','cdcd_control_xfer_cb'),('00000018','cdcd_xfer_cb')]
assert any(x['object']=='15-mt_usb_cdc.o' and x['symbol']=='mt_usb_cdc_write_impl' for x in rels)
assert not (P/'final-undefined.stdout').read_text().strip()
assert not re.search(r'\b(__aeabi_\w+|malloc|calloc|realloc|free|_sbrk)\b',(P/'final-symbols.stdout').read_text())
# Explicit stack-switch and new exception instructions absent.
ins=[x for f in G.values() for x in f['instructions']]
assert not any(x['op'] in ('svc','swi') for x in ins)
assert all(x['operand'].startswith('PRIMASK,') for x in ins if x['op']=='msr')
json.dump(rels,open(O/'code-pointer-relocations.json','w'),indent=2)
print('PASS: regenerated objdump identical; linked object hashes match; no deferred producer caller/address reference; actual CDC table/writer bindings; no unresolved/helper closure or stack-switch instructions.')

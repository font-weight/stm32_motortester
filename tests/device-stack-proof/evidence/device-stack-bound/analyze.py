#!/usr/bin/env python3
"""Read-only, address-keyed conservative function-level stack analysis, BOOT004 Os."""
import pathlib,re,json,hashlib,collections
P=pathlib.Path('/workspace/shared/device-main-arm/run-r2'); O=pathlib.Path(__file__).parent
R=json.loads((P/'RESULTS.json').read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
# Actual linked object membership, including reset, excludes optimization experiments.
objects={k for k in R['objects_sha256'] if k.endswith('.o') and k!='combined.o'}
sus={}
for obj in objects:
 p=P/(obj[:-2]+'.su')
 if p.exists():
  vals={}
  for line in p.read_text().splitlines():
   loc,frame,qual=line.split('\t');fn=loc.rsplit(':',1)[1]
   assert fn not in vals,(obj,fn)
   vals[fn]={'frame':int(frame),'qualifier':qual,'source':loc,'su':p.name}
  sus[obj]=vals
# Input section ranges are authoritative: avoid conflating duplicate local function names.
s=(P/'TEST_ONLY_STATIC_PLACEMENT.map').read_text()
pat=re.compile(r'^ (\.text[^\s]*)\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+(\S+\.o)$',re.M)
F={}
for sec,addr,size,path in pat.findall(s):
 addr=int(addr,16);size=int(size,16);obj=pathlib.Path(path).name
 if not size:continue
 assert obj in objects
 name=sec.removeprefix('.text.').removeprefix('startup.')
 matches=[(n,v) for n,v in sus.get(obj,{}).items() if n==name or re.sub(r'\.\d+(?=\.|$)','',name)==n]
 assert len(matches)<=1,(obj,name,matches)
 f={'address':hex(addr),'end':hex(addr+size),'size':size,'name':name,'object':obj,'section':sec,'calls':[],'indirect':[],'instructions':[]}
 if matches:f.update(matches[0][1])
 elif obj=='reset.o' and name in ('Reset_Handler','Default_Handler'):f.update(frame=0,qualifier='handwritten-proven-zero',source='boot/mt_reset_stm32f103c8.S')
 else:f.update(frame=None,qualifier='MISSING')
 assert addr not in F,(hex(addr),name)
 F[addr]=f
ranges=sorted(F)
def owner(addr):
 import bisect
 n=bisect.bisect_right(ranges,addr)-1
 return ranges[n] if n>=0 and addr<int(F[ranges[n]]['end'],16) else None
for line in (P/'final-disassembly.stdout').read_text().splitlines():
 m=re.match(r'^\s*([0-9a-f]+):\s+((?:[0-9a-f]{4}(?:[0-9a-f]{4})?\s+)+)\s*([^\s]+)(?:\s+(.*))?$',line)
 if not m:continue
 a=int(m[1],16);o=owner(a)
 if o is None:continue
 op=m[3];operand=m[4] or ''
 F[o]['instructions'].append({'address':hex(a),'op':op,'operand':operand})
 if op in ('bl','bl.w','blx','bx','b','b.n','b.w') or re.fullmatch(r'b(eq|ne|cs|cc|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)(\.[nw])?',op):
  t=re.match(r'([0-9a-f]+)\s+<',operand)
  if t:
   ta=int(t[1],16);to=owner(ta)
   if to!=o:
    F[o]['calls'].append({'site':hex(a),'op':op,'target':hex(to) if to is not None else None,'raw_target':hex(ta)})
   elif op in ('bl','bl.w','blx'):
    F[o]['calls'].append({'site':hex(a),'op':op,'target':hex(to),'raw_target':hex(ta)})
  elif operand.strip()!='lr':F[o]['indirect'].append({'site':hex(a),'op':op,'operand':operand})
json.dump({hex(a):f for a,f in F.items()},open(O/'graph-initial.json','w'),indent=2)
print('function sections',len(F),'missing .su',[(hex(a),f['name'],f['object']) for a,f in F.items() if f['frame'] is None]);print('qualifiers',collections.Counter(f['qualifier'] for f in F.values()))
print('indirect calls:')
for a,f in F.items():
 for c in f['indirect']:print(hex(a),f['name'],f['object'],c['site'],c['op'],c['operand'])
print('unresolved direct',[(hex(a),f['name'],c) for a,f in F.items() for c in f['calls'] if c['target'] is None])

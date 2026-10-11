#!/usr/bin/env python3
"""Subject-specific independent graph/CFG audit; no production writes or execution."""
from pathlib import Path
import re,struct,json,collections,bisect,hashlib
ROOT=Path(__file__).resolve().parent
P=Path('/workspace/shared/device-main-arm/run-r2')
W=ROOT/'replay'
B=(P/'TEST_ONLY_INVALID_BOARD_STATIC_PLACEMENT_NOT_FLASHABLE.elf').read_bytes()
assert hashlib.sha256(B).hexdigest()=='0450e7ee104e19d6a1c018c2f6d1bdcb6f4b74544063044e14c41c01b586c2ae'
H=struct.unpack_from('<16sHHIIIIIHHHHHH',B)
SH=[struct.unpack_from('<IIIIIIIIII',B,H[6]+i*H[11]) for i in range(H[12])]
def read(a,n):
 matches=[s for s in SH if s[1]!=8 and s[2]&2 and s[3]<=a and a+n<=s[3]+s[5]]
 assert len(matches)==1,(hex(a),n)
 s=matches[0];return B[s[4]+a-s[3]:s[4]+a-s[3]+n]
# All map text inputs, not selected by board value or entrypoint reachability.
F={}; pending=None; rodata=[]
for line in (P/'TEST_ONLY_STATIC_PLACEMENT.map').read_text().splitlines():
 parts=line.split()
 if len(parts)==1 and parts[0].startswith(('.text','.rodata')):pending=parts[0];continue
 if len(parts)==3 and pending:parts=[pending]+parts;pending=None
 if len(parts)==4 and parts[0].startswith('.rodata') and parts[-1].endswith('.o'):
  rodata.append((int(parts[1],16),int(parts[1],16)+int(parts[2],16)))
 if len(parts)==4 and parts[0].startswith('.text') and parts[-1].endswith('.o'):
  sec,begin,length,obj=parts; a=int(begin,16);n=int(length,16)
  if n:F[a]={'end':a+n,'object':Path(obj).name,'name':sec.removeprefix('.text.').removeprefix('startup.')}
starts=sorted(F)
def owner(a):
 i=bisect.bisect_right(starts,a)-1
 assert i>=0 and a<F[starts[i]]['end'],hex(a)
 return starts[i]
# Verify all executable ELF bytes are represented by text inputs or only inter-input zero padding.
exec_ranges=[]
for s in SH:
 if s[2]&4:
  assert s[1]!=8
  for a in range(s[3],s[3]+s[5]):
   i=bisect.bisect_right(starts,a)-1
   if not (i>=0 and a<F[starts[i]]['end']) and not any(b<=a<e for b,e in rodata):assert read(a,1)==b'\0',hex(a)
  exec_ranges.append([hex(s[3]),s[5]])
I={};data_bytes=set();code_bytes=set()
for line in (P/'final-disassembly.stdout').read_text().splitlines():
 m=re.fullmatch(r'\s*([0-9a-f]+):\s+([0-9a-f]{2,8}(?:\s+[0-9a-f]{4})?)\s+(\S+)(?:\s+(.*))?',line)
 if not m:continue
 a=int(m[1],16)
 if not any(b<=a<f['end'] for b,f in F.items()):continue
 enc=m[2].split();op=m[3];v=(m[4] or '').split('@')[0].strip();n=sum(len(x)//2 for x in enc)
 # Objdump prints each instruction halfword in display byte order; data .word is word order.
 binary=b''.join(int(x,16).to_bytes(len(x)//2,'little') for x in enc)
 assert read(a,n)==binary,(hex(a),op,enc)
 if op.startswith('.'):data_bytes.update(range(a,a+n));continue
 assert a not in I
 I[a]=(n,op,v);code_bytes.update(range(a,a+n))
# Every nonpadding linked function byte decodes to an instruction or explicit literal/switch data.
for a,f in F.items():
 assert a in I
 for b in range(a,f['end']):assert b in code_bytes|data_bytes,(f,hex(b))
# All STT_FUNC entries fit matching input functions; overridden weak fallback text is retained explicitly.
functions={};aliases={}
for s in SH:
 if s[1]!=2:continue
 names=SH[s[6]];strings=B[names[4]:names[4]+names[5]]
 for off in range(s[4],s[4]+s[5],s[9]):
  name,value,size,info,other,idx=struct.unpack_from('<IIIBBH',B,off)
  if info&15!=2:continue
  name=strings[name:].split(b'\0')[0].decode();a=value&~1
  assert a in F and a+size<=F[a]['end'] and value&1,(name,hex(a),size)
  aliases.setdefault(a,[]).append(name);functions[a]=size
assert len(F)==363 and len(functions)==360
fallback={F[a]['name'] for a in set(F)-set(functions)}
assert fallback=={'dcd_deinit','dcd_edpt_xfer_fifo','dcd_edpt0_status_complete'}
# Discover every computed switch from instructions, infer base/count from adjacent actual guard sequence.
J={};guards=[]
for a,(n,op,v) in I.items():
 if op in ('tbb','tbh') or (op=='ldr.w' and v.startswith('pc,') and '[sp]' not in v):
  bef=sorted(x for x in I if owner(x)==owner(a) and x<a)
  branch=bef[-1] if op in ('tbb','tbh') else bef[-2]
  cmp_site=bef[-2] if op in ('tbb','tbh') else bef[-3]
  bn,bop,bv=I[branch];cn,cop,cv=I[cmp_site]
  assert bop in ('bhi.n','bhi.w') and cop=='cmp',(hex(a),bop,cop)
  reg,lim=cv.split(', ');count=int(lim[1:])+1
  if op in ('tbb','tbh'):
   assert v=='[pc, '+reg+(']' if op=='tbb' else ', lsl #1]')
   base=a+4;stride=1 if op=='tbb' else 2
  else:
   an,aop,av=I[bef[-1]];base_reg=av.split(',')[0]
   assert aop=='add' and av==base_reg+', pc, #4'
   base=((bef[-1]+4)&~3)+4;stride=4
   assert v=='pc, ['+base_reg+', '+reg+', lsl #2]'
  words=[int.from_bytes(read(base+k*stride,stride),'little') for k in range(count)]
  targets=[(x&~1) if stride==4 else a+4+x*2 for x in words]
  assert all(t in I and owner(t)==owner(a) for t in targets)
  J[a]=targets;guards.append(dict(site=hex(a),cmp=hex(cmp_site),branch=hex(branch),count=count,base=hex(base)))
assert len(J)==9
# Explicit audited callback map is an input, not automatically inferred from an address-taking superset.
resolved=json.loads((W/'indirect-resolutions.json').read_text())
callback={int(x['site'],16):[int(t,16) for t in x['resolved_addresses']] for x in resolved}
assert len(callback)==53
# Decode all branches, including CBZ/CBNZ omitted from the supporting interfunction recognizer.
conditional=re.compile(r'b(eq|ne|cs|cc|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)(\.n|\.w)?$')
E={a:set() for a in F};CFG={};indirect=set();transfers=[];sp_effect={};ret=set()
for a,(n,op,v) in I.items():
 f=owner(a);nexts=[a+n];d=0
 if op in ('push','pop') or (op in ('stmdb','ldmia.w') and v.startswith('sp!')):
  regs=v[v.index('{')+1:v.index('}')].split(', ');assert all('-' not in r for r in regs)
  d=4*len(regs)*(1 if op in ('push','stmdb') else -1)
 elif v.startswith('sp,') and op in ('add','add.w','addw','sub','sub.w','subw'):
  args=v.split(', ');assert args[1]=='sp' or args[1].startswith('#')
  d=int(args[-1][1:])*(-1 if op.startswith('add') else 1)
 elif op in ('ldr','ldr.w') and ', [sp], #' in v:d=-int(v.split('#')[-1])
 # No other SP assignment, writeback or conditional stack adjustment exists in this exact image.
 elif v.startswith('sp,') and op!='stmia.w':raise AssertionError(('unclassified SP destination',hex(a),op,v))
 if '[sp,' in v and ('!' in v or '], ' in v):raise AssertionError(('unclassified stack writeback',hex(a),op,v))
 if op=='msr':assert v.startswith('PRIMASK,')
 if op.startswith(('vpush','vpop','svc','swi')):raise AssertionError((hex(a),op,v))
 if a in J:nexts=J[a]
 elif op in ('bl','bl.w','b','b.n','b.w') or conditional.fullmatch(op) or op in ('cbz','cbnz'):
  target=int(re.search(r'([0-9a-f]+)\s+<',v)[1],16);to=owner(target)
  call=op in ('bl','bl.w');cond=conditional.fullmatch(op) or op in ('cbz','cbnz')
  if to!=f or call:
   assert target==to,(hex(a),'middle-of-function edge',hex(target))
   E[f].add(to);transfers.append([hex(a),hex(to),op])
  if not call:
   nexts=([target] if to==f else [])+([a+n] if cond else [])
   if to!=f:ret.add(a)
 elif op in ('bx','blx'):
  if v=='lr':assert op=='bx';nexts=[];ret.add(a)
  else:
   assert a in callback;indirect.add(a);E[f].update(callback[a])
   if op=='bx':nexts=[];ret.add(a)
 elif ((op in ('pop','ldmia.w') and re.search(r'\bpc\b',v)) or (op in ('ldr','ldr.w') and v.startswith('pc, [sp], #'))):nexts=[];ret.add(a)
 elif v.startswith('pc,'):raise AssertionError(('unclassified PC assignment',hex(a),op,v))
 sp_effect[a]=d;CFG[a]=nexts
assert indirect==set(callback)
# Conditional IT-block instructions contain no hidden stack/control modification.
it_covered=[]
for a,(n,op,v) in I.items():
 if re.fullmatch(r'it[te]{0,3}',op):
  cursor=a+n
  for k in range(len(op)-1):
   nn,oo,vv=I[cursor];assert sp_effect[cursor]==0 and cursor not in ret and cursor not in J
   assert not oo.startswith(('b','cb','tb')) or oo.startswith(('bic','bfi')),(hex(cursor),oo)
   it_covered.append(cursor);cursor+=nn
# Solve all local CFG/SP states and verify immediate switch guard dominance.
peaks={};states={};dom_records=[]
for f in F:
 q=collections.deque([(f,0)]);seen={};peak=0;pred=collections.defaultdict(set)
 while q:
  a,depth=q.popleft()
  if a in seen:assert seen[a]==depth,(hex(a),'SP-inconsistent loop');continue
  seen[a]=depth;after=depth+sp_effect[a];assert after>=0
  peak=max(peak,depth,after)
  if a in ret:assert after==0
  for nxt in CFG[a]:assert nxt in I and owner(nxt)==f;pred[nxt].add(a);q.append((nxt,after))
 peaks[f]=peak;states[f]=seen
 for guard in (x for x in guards if owner(int(x['site'],16))==f):
  a=int(guard['site'],16);branch=int(guard['branch'],16);cmp_site=int(guard['cmp'],16)
  # Removing the guard's fallthrough edge must make dispatch unreachable.
  reached=set();todo=[f]
  while todo:
   x=todo.pop()
   if x in reached:continue
   reached.add(x)
   todo.extend(n for n in CFG[x] if not (x==branch and n==x+I[x][0]))
  assert a not in reached
  assert pred[branch]=={cmp_site},guard
  dom_records.append(guard)
# Cross-check separately produced GCC .su peaks, without using .su to compute this machine bound.
subject=json.loads((W/'graph-initial.json').read_text())
assert all(peaks[a]==subject[hex(a)]['frame'] for a in F)
# Topological longest path over machine-derived local frames. Fail on any unresolved SCC.
pending={a:len(E[a]) for a in F};parents=collections.defaultdict(set)
for a,ts in E.items():
 for t in ts:parents[t].add(a)
queue=collections.deque(a for a,n in pending.items() if n==0);U={};paths={}
while queue:
 a=queue.popleft();child=max(E[a],key=lambda t:U[t],default=None)
 U[a]=peaks[a]+(U[child] if child is not None else 0);paths[a]=[a]+(paths[child] if child is not None else [])
 for p in parents[a]:
  pending[p]-=1
  if pending[p]==0:queue.append(p)
assert len(U)==len(F),'recursive SCC or unresolved edge'
for a,f in F.items():assert E[a]=={int(c['target'],16) for c in json.loads((W/'graph-resolved.json').read_text())[hex(a)]['calls']}
def addr(name):
 a=[x for x,f in F.items() if f['name']==name];assert len(a)==1;return a[0]
result={name:{'bytes':U[addr(name)],'path':[(F[a]['name'],peaks[a]) for a in paths[addr(name)]]} for name in ('main','mt_runtime_step','USB_HP_CAN1_TX_IRQHandler','USB_LP_CAN1_RX0_IRQHandler','tud_task_ext')}
assert result['main']['bytes']==2640 and result['USB_HP_CAN1_TX_IRQHandler']['bytes']==264
result.update(functions=len(F),defined_function_addresses=len(functions),overridden_weak_text=sorted(fallback),instruction_count=len(I),indirect_sites=len(indirect),switch_guard_dominance=dom_records,it_instructions_checked=len(it_covered),executable_ranges=exec_ranges,all_frames_agree=True,all_direct_edges_agree=True,recursion_absent=True,conditional_terminal_total=2640+264+3*36,rounded8=(2640+264+3*36+7)//8*8)
(ROOT/'independent-machine-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

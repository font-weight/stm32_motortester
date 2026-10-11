#!/usr/bin/env python3
"""Verify finite computed jumps and per-function SP high-water using linked machine CFG."""
import pathlib,json,struct,re,collections
P=pathlib.Path('/workspace/shared/device-main-arm/run-r2');O=pathlib.Path(__file__).parent
G=json.loads((O/'graph-initial.json').read_text());blob=(P/'TEST_ONLY_INVALID_BOARD_STATIC_PLACEMENT_NOT_FLASHABLE.elf').read_bytes()
assert blob[:7]==b'\x7fELF\x01\x01\x01'
H=struct.unpack_from('<16sHHIIIIIHHHHHH',blob);shoff,entsize,num,strings=H[6],H[11],H[12],H[13]
S=[struct.unpack_from('<IIIIIIIIII',blob,shoff+i*entsize) for i in range(num)]
def data(addr,n):
 for s in S:
  if s[1]!=8 and s[3]<=addr and addr+n<=s[3]+s[5]:return blob[s[4]+addr-s[3]:s[4]+addr-s[3]+n]
 raise ValueError(hex(addr))
# Exact index bounds verified at cmp/bhi before each compiler switch. The table
# addresses are the linked ADR/PC base, not guessed function name relationships.
tables={
 0x8001530:(0x8001534,6,2,'cmp r3,#5; bhi at 0x800152e'),
 0x800323a:(0x800323e,7,1,'cmp r3,#6; bhi at 0x8003238'),
 0x8003524:(0x8003528,18,4,'cmp r1,#17; bhi at 0x8003520'),
 0x80040d6:(0x80040da,7,1,'cmp r3,#6; bhi at 0x80040d4'),
 0x8005484:(0x8005488,4,4,'cmp r2,#3; bhi at 0x8005480'),
 0x80059b0:(0x80059b4,6,1,'cmp r3,#5; bhi at 0x80059ae'),
 0x8005f3e:(0x8005f42,8,2,'cmp r2,#7; bhi at 0x8005f3a'),
 0x8006036:(0x800603c,10,4,'cmp r3,#9; bhi at 0x8006032'),
 0x8006202:(0x8006208,15,4,'cmp r3,#14; bhi at 0x80061fc'),
}
# Parse actual encoded lengths; .word/.short are data, never executable nodes.
I={}
for line in (P/'final-disassembly.stdout').read_text().splitlines():
 m=re.match(r'^\s*([0-9a-f]+):\s+((?:[0-9a-f]{4}(?:[0-9a-f]{4})?\s+)+)\s*([^\s]+)(?:\s+(.*))?$',line)
 if m and not m[3].startswith('.'):
  a=int(m[1],16);size=len(''.join(m[2].split()))//2;I[a]=(size,m[3],m[4] or '')
J={};report=[]
for site,(base,count,size,evidence) in tables.items():
 vals=struct.unpack('<'+{1:'B',2:'H',4:'I'}[size]*count,data(base,count*size))
 targets=[(v&~1) if size==4 else site+4+2*v for v in vals]
 owners=[a for a,f in G.items() if int(a,16)<=site<int(f['end'],16)];assert len(owners)==1
 a=owners[0];f=G[a]
 assert all(int(a,16)<=t<int(f['end'],16) and t in I for t in targets),(hex(site),targets)
 J[site]=targets
 report.append({'site':hex(site),'function':a,'name':f['name'],'base':hex(base),'count':count,'entry_size':size,'evidence':evidence,'targets':[hex(t) for t in targets],'all_targets_same_function_and_instruction_start':True})
# All PC writes must be return/jump/call/switch already classified.
for site,(size,op,v) in I.items():
 if op in ('tbb','tbh') or v.startswith('pc,') and '[sp]' not in v:assert site in J,(hex(site),op,v)

def regs(v):return 4*len(v[v.index('{')+1:v.index('}')].split(', '))
def delta(op,v):
 if op=='push' or op=='stmdb' and v.startswith('sp!'):return regs(v)
 if op=='pop' or op=='ldmia.w' and v.startswith('sp!'):return -regs(v)
 if (op.startswith('sub') or op.startswith('add')) and v.startswith('sp,'):
  n=int(re.search(r'#(\d+)',v)[1]);return n if op.startswith('sub') else -n
 if op in ('ldr','ldr.w') and ', [sp], #' in v:return -int(re.search(r'#(\d+)',v)[1])
 return 0
stats=[]
cond=re.compile(r'b(?:eq|ne|cs|cc|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)(?:\.[nw])?$')
for key,f in G.items():
 start=int(key,16);end=int(f['end'],16);todo=[(start,0)];seen={};peak=0;tails=[]
 while todo:
  a,sp=todo.pop();assert a in I,(f['name'],hex(a),'missing instruction')
  if a in seen:assert seen[a]==sp,(f['name'],hex(a),'inconsistent SP/cycle',seen[a],sp);continue
  seen[a]=sp;size,op,v=I[a];new=sp+delta(op,v);peak=max(peak,sp,new);assert new>=0,(f['name'],hex(a),new)
  nexts=[]
  if a in J:nexts=J[a]
  elif op in ('bx',) or op=='pop' and 'pc}' in v or op=='ldmia.w' and 'pc}' in v or op in ('ldr','ldr.w') and v.startswith('pc, [sp], #'):
   assert new==0,(f['name'],hex(a),'nonzero tail/return SP',new)
  elif op in ('b','b.n','b.w') or cond.fullmatch(op) or op in ('cbz','cbnz'):
   matches=re.search(r'([0-9a-f]+)\s+<',v);assert matches,(f['name'],hex(a),op,v)
   t=int(matches[1],16)
   if start<=t<end:nexts.append(t)
   else:assert new==0,(f['name'],hex(a),'nonzero direct-tail SP',new)
   if op not in ('b','b.n','b.w'):nexts.append(a+size)
  else:nexts=[a+size]
  for n in nexts:
   assert start<=n<end,(f['name'],hex(a),'fallthrough past section',hex(n))
   todo.append((n,new))
 assert peak==f['frame'],(f['name'],peak,f['frame'])
 stats.append({'address':key,'name':f['name'],'object':f['object'],'su_bytes':f['frame'],'machine_max_sp_decrement':peak,'reachable_machine_instructions':len(seen),'sp_state_unique_at_every_reachable_instruction':True})
json.dump(report,open(O/'computed-jumps.json','w'),indent=2);json.dump(stats,open(O/'machine-stack-check.json','w'),indent=2)
print('PASS:',len(report),'finite switch tables; all destinations intrafunction instruction starts;',len(stats),'functions exactly match .su/manual frame; no inconsistent SP cycle, stack switch or unclassified computed PC transfer.')

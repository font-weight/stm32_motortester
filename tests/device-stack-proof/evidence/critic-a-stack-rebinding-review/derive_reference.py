from pathlib import Path
import json,struct,hashlib,re,shutil
R=Path(__file__).resolve().parent;D=Path('/workspace/shared/final-stack-rebinding-design');P=Path('/workspace/shared/device-main-arm/run-r2');S=Path('/workspace/shared/device-stack-bound')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')).hexdigest()
class ELF:
 def __init__(self,path):
  self.b=Path(path).read_bytes();self.h=struct.unpack_from('<16sHHIIIIIHHHHHH',self.b);assert self.b[:7]==b'\x7fELF\x01\x01\x01' and self.h[2]==40
  self.sh=[struct.unpack_from('<10I',self.b,self.h[6]+i*self.h[11]) for i in range(self.h[12])];sn=self.bytes(self.sh[self.h[13]])
  self.names=[self.zstr(sn,s[0]) for s in self.sh];assert len(self.names)==len(set(self.names));self.sec=dict(zip(self.names,self.sh));self.sym=[];self.syms_by_table={}
  self.ph=[struct.unpack_from('<8I',self.b,self.h[5]+i*self.h[9]) for i in range(self.h[10])]
  for idx,s in enumerate(self.sh):
   if s[1]!=2:continue
   names=self.bytes(self.sh[s[6]]);rows=[]
   for offset in range(s[4],s[4]+s[5],s[9]):
    n,v,size,info,other,ndx=struct.unpack_from('<IIIBBH',self.b,offset)
    section=self.names[ndx] if 0<ndx<len(self.sh) else {0:'SHN_UNDEF',65521:'SHN_ABS'}.get(ndx,str(ndx))
    row=[self.zstr(names,n),v,size,info&15,info>>4,other,section];self.sym.append(row);rows.append((row,ndx))
   self.syms_by_table[idx]=rows
 def zstr(self,b,i):return b[i:b.index(b'\0',i)].decode()
 def bytes(self,s):return self.b[s[4]:s[4]+s[5]] if s[1]!=8 else b''
 def data(self,a,n):
  match=[s for s in self.sh if s[1]!=8 and s[2]&2 and s[3]<=a and a+n<=s[3]+s[5]];assert len(match)==1,(hex(a),n)
  s=match[0];return self.b[s[4]+a-s[3]:s[4]+a-s[3]+n]
 def symbol(self,n):
  rows=[x for x in self.sym if x[0]==n];assert len(rows)==1,(n,rows);return rows[0]
 def relocations(self):
  out=[]
  for s in self.sh:
   if s[1]!=9:continue
   assert s[9]==8
   for off in range(s[4],s[4]+s[5],8):
    a,info=struct.unpack_from('<II',self.b,off);row,ndx=self.syms_by_table[s[6]][info>>8];out.append({'section':self.names[s[7]],'offset':a,'type':info&255,'target':row,'target_ndx':ndx})
  return out

def ranges(map_path):
 rows=[];pending=None
 for line in Path(map_path).read_text().splitlines():
  parts=line.split()
  if len(parts)==1 and parts[0].startswith('.text'):pending=parts[0];continue
  if len(parts)==3 and pending:parts=[pending,*parts];pending=None
  if len(parts)==4 and parts[0].startswith('.text') and parts[-1].endswith('.o'):
   sec,a,n,obj=parts;n=int(n,16)
   if n:rows.append([Path(obj).name,sec,int(a,16),n])
 return sorted(rows)
if __name__=='__main__':
 ref=json.loads((D/'DRAFT_REFERENCE_MANIFEST.json').read_text());assert sha(D/'DRAFT_REFERENCE_MANIFEST.json')=='c447746787bceb3c603e3847b6e6308937d4773a14673ad0d040ac7f2e88730d'
 old=P/'TEST_ONLY_INVALID_BOARD_STATIC_PLACEMENT_NOT_FLASHABLE.elf';e=ELF(old);assert sha(old)==ref['baseline_elf_sha256'];assert old.read_bytes()==Path('/workspace/shared/device-main-arm/critic-a-replay-r1/TEST_ONLY_INVALID_BOARD_STATIC_PLACEMENT_NOT_FLASHABLE.elf').read_bytes()
 raw=json.loads((P/'RESULTS.json').read_text());cmd=next(x['argv'] for x in raw['commands'] if x['label']=='test-only-static-link');objects=[Path(x) for x in cmd[cmd.index(str(P/'Os-mt_device_main.o')):-1]];assert len(objects)==33 and len({p.name for p in objects})==33
 derived=[{'ordinal':i,'name':p.name,'sha256':sha(p)} for i,p in enumerate(objects)];assert derived==ref['original_objects_in_order']
 rr=ranges(P/'TEST_ONLY_STATIC_PLACEMENT.map');assert len(rr)==ref['text_input_ranges']['count']==363 and digest(rr)==ref['text_input_ranges']['sorted_records_sha256']
 # Match every input range to an original object section, rather than trusting map names alone.
 obj={p.name:ELF(p) for p in objects}
 for name,section,a,n in rr:
  s=obj[name].sec[section];assert s[5]==n and s[2]&4;assert e.sec['.text'][3]<=a and a+n<=e.sec['.text'][3]+e.sec['.text'][5]
 funcs=sorted(x for x in e.sym if x[3]==2);assert len(funcs)==410 and len({x[1] for x in funcs})==360;assert digest(funcs)==ref['function_symbol_multiset']['sorted_records_sha256']
 immutable=sorted(x for x in e.sym if x[3]!=4 and x[0] not in {'__boot_stack_bytes','__StackLimit','__data_load'} and not(x[3]==3 and x[6]=='.unsupported_stubs'));assert len(immutable)==1053 and digest(immutable)==ref['immutable_symbol_multiset']['sorted_records_sha256']
 for reg in ref['immutable_loaded_regions']:assert hashlib.sha256(e.data(reg['vma'],reg['length'])).hexdigest()==reg['sha256']
 reset=e.symbol('Reset_Handler')[1]&~1;board=e.symbol('mt_board');load=e.symbol('__data_load')[1];literal=reset+0x58
 assert (reset,board[1],board[2],literal,load)==(0x08007d90,0x08007e68,48,0x08007de8,0x08007e98)
 assert e.data(literal,4)==struct.pack('<I',load)
 # Decode actual Thumb literal LDR r0: aligned (PC+4) plus immediate*4.
 insn=struct.unpack('<H',e.data(reset+0x1a,2))[0];assert insn==0x480f and ((reset+0x1a+4)&~3)+(insn&255)*4==literal
 assert e.data(reset+0x48,4)==bytes.fromhex('fee70000')
 rel=[x for x in obj['reset.o'].relocations() if x['section']=='.text.Reset_Handler' and x['offset']==0x58];assert len(rel)==1 and rel[0]['type']==2 and rel[0]['target'][0]=='__data_load'
 layout=ref['fixed_layout'];assert e.h[4]==layout['entry']
 for sec,addr,size in [('.isr_vector',layout['vector_base'],236),('.text',layout['text_base'],32172),('.data',layout['data_vma'],layout['data_size']),('.bss',layout['bss_vma'],layout['bss_size'])]:assert e.sec[sec][3]==addr and e.sec[sec][5]==size
 assert e.symbol('__static_end')[1]==layout['static_end'] and e.symbol('__StackTop')[1]==layout['stack_top'];assert layout['stack_top']-layout['static_end']==8104
 # Rehash the original audited proof bundle, not only the summary digest.
 ins=json.loads((S/'INPUTS_SHA256.json').read_text());arts=json.loads((S/'ARTIFACTS_SHA256.json').read_text());assert len(ins)==344 and len(arts)==29
 for p,h in ins.items():assert sha(p)==h
 for p,h in arts.items():assert sha(S/p)==h
 for p,h in ref['proof_artifacts'].items():assert sha(S/p)==h
 assert sha('/workspace/shared/critic-a-device-stack-review/CRITIC_A_CONDITIONAL_STACK_REVIEW.md')==ref['independent_review_sha256']
 for p,h in ref['original_boot_files'].items():assert sha(P/'boot'/p)==h
 bounds=ref['reviewed_stack_bounds'];assert bounds=={'startup':288,'foreground_including_main':2640,'usb_irq_software':264,'exception_allowance':36,'normal':2940,'including_terminal_hardfault_nmi':3012,'rounded_to_8':3016}
 for name in ('DESIGN_RU.md','DRAFT_REFERENCE_MANIFEST.json','ARTIFACTS_SHA256.json'):shutil.copyfile(D/name,R/name)
 (R/'derived-ranges.json').write_text(json.dumps(rr,indent=2)+'\n');(R/'derived-symbols.json').write_text(json.dumps({'functions':funcs,'immutable':immutable},indent=2)+'\n')
 result={'status':'PASS_REFERENCE_DERIVATION','reference_sha256':sha(D/'DRAFT_REFERENCE_MANIFEST.json'),'design_sha256':sha(D/'DESIGN_RU.md'),'baseline_elf_sha256':sha(old),'ordered_objects':derived,'range_count':len(rr),'function_records':len(funcs),'immutable_records':len(immutable),'regions_checked':4,'old_proof_inputs_rehashed':len(ins),'old_proof_artifacts_rehashed':len(arts),'reset_literal_address':hex(literal),'reset_literal_input_relocation':rel,'max_geometric_reserve':8104,'baseline_program_headers':e.ph,'status_scope':'Design/reference derivation only; not implementation qualification.'}
 (R/'reference-derivation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ('ordered_objects','baseline_program_headers','reset_literal_input_relocation')},indent=2))

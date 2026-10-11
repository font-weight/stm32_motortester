#!/usr/bin/env python3
"""Extract draft reference DATA from the reviewed ELF. Not a candidate gate."""
import pathlib,struct,json,hashlib
O=pathlib.Path(__file__).resolve().parent
P=pathlib.Path('/workspace/shared/device-main-arm/run-r2')
S=pathlib.Path('/workspace/shared/device-stack-bound')
def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
def digest(x):return hashlib.sha256(canonical(x)).hexdigest()
p=P/'TEST_ONLY_INVALID_BOARD_STATIC_PLACEMENT_NOT_FLASHABLE.elf';b=p.read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',b);ss=[struct.unpack_from('<IIIIIIIIII',b,h[6]+i*h[11]) for i in range(h[12])];sn=b[ss[h[13]][4]:ss[h[13]][4]+ss[h[13]][5]]
def name(s):return sn[s[0]:].split(b'\0')[0].decode()
sections={name(s):s for s in ss}
def data(a,n):
 for s in ss:
  if s[1]!=8 and s[3]<=a and a+n<=s[3]+s[5]:return b[s[4]+a-s[3]:s[4]+a-s[3]+n]
 raise ValueError(hex(a))
syms=[]
for s in ss:
 if s[1]!=2:continue
 ns=ss[s[6]];ns=b[ns[4]:ns[4]+ns[5]]
 for off in range(s[4],s[4]+s[5],s[9]):
  n,v,z,i,o,idx=struct.unpack_from('<IIIBBH',b,off)
  syms.append([ns[n:].split(b'\0')[0].decode(),v,z,i&15,i>>4,o,name(ss[idx]) if 0<idx<len(ss) else {0:'SHN_UNDEF',65521:'SHN_ABS'}.get(idx,str(idx))])
funcs=sorted(s for s in syms if s[3]==2)
filtered=sorted(s for s in syms if s[3]!=4 and s[0] not in ('__boot_stack_bytes','__StackLimit','__data_load') and not(s[3]==3 and s[6]=='.unsupported_stubs'))
G=json.loads((S/'graph-resolved.json').read_text());ranges=sorted([f['object'],f['section'],int(k,16),f['size']] for k,f in G.items())
order=json.loads((O/'PINNED_33_OBJECTS_ORDER.json').read_text());originals=[{'ordinal':x['ordinal'],'name':pathlib.Path(x['path']).name,'sha256':x['sha256']} for x in order]
regions=[]
for label,a,n in [('vectors',0x08000000,236),('immutable_text_prefix',0x080000ec,0x08007de8-0x080000ec),('immutable_text_suffix',0x08007dec,0x08007e68-0x08007dec),('initial_data',0x20000000,128)]:regions.append({'label':label,'vma':a,'length':n,'sha256':hashlib.sha256(data(a,n)).hexdigest()})
reference={'status':'DRAFT_REFERENCE_DATA_REQUIRES_INDEPENDENT_REVIEW','not_production_gate':True,'baseline_elf_sha256':sha(p),'baseline_source':'Reviewed run-r2 invalid-zero-board static-placement ELF; no board-value pruning used','canonical_json':'ASCII json.dumps(sort_keys=True,separators=(comma,colon),ensure_ascii=True); no trailing newline for hashes','original_objects_in_order':originals,'immutable_loaded_regions':regions,'single_variable_noninstruction_literal':{'vma':0x08007de8,'length':4,'input_object':'reset.o','input_section':'.text.Reset_Handler','section_offset':0x58,'relocation':'R_ARM_ABS32','symbol':'__data_load','baseline_value':0x08007e98,'rule':'little-endian uint32 equal to validated new .data LMA; input literal is nonexecuted data, not instruction bytes'},'fixed_layout':{'entry':h[4],'vector_base':0x08000000,'text_base':0x080000ec,'mt_board_vma':0x08007e68,'mt_board_size':48,'new_strings_start':0x08007e98,'data_vma':0x20000000,'data_size':128,'bss_vma':0x20000080,'bss_size':12248,'static_end':0x20003058,'stack_top':0x20005000,'flash_end_under_original_linker_model':0x08010000},'text_input_ranges':{'count':len(ranges),'canonical_record':['object_basename','input_section_name','vma_integer','size_integer'],'sorted_records_sha256':digest(ranges)},'function_symbol_multiset':{'record':['name','value','size','type','bind','other','section_name_or_reserved_index_label'],'count':len(funcs),'unique_addresses':len({s[1] for s in funcs}),'sorted_records_sha256':digest(funcs)},'immutable_symbol_multiset':{'count':len(filtered),'sorted_records_sha256':digest(filtered),'reference_exclusions':['all STT_FILE records','symbols __boot_stack_bytes, __StackLimit, __data_load','STT_SECTION for empty .unsupported_stubs'],'candidate_extra_exclusions':'Only separately validated three board_* local OBJECT records and three local $d mapping records at their exact starts; no other new symbol. Exclusions for FILE and empty stubs metadata do not authorize loaded bytes.'},'reviewed_stack_bounds':{'startup':288,'foreground_including_main':2640,'usb_irq_software':264,'exception_allowance':36,'normal':2940,'including_terminal_hardfault_nmi':3012,'rounded_to_8':3016},'proof_artifacts':{n:sha(S/n) for n in ['REPORT_RU.md','INPUTS_SHA256.json','ARTIFACTS_SHA256.json','graph-resolved.json','machine-stack-check.json','computed-jumps.json','code-pointer-relocations.json','indirect-resolutions.json','bounds.json']},'independent_review_sha256':sha('/workspace/shared/critic-a-device-stack-review/CRITIC_A_CONDITIONAL_STACK_REVIEW.md'),'original_boot_files':{n:sha(P/'boot'/n) for n in ['stm32f103c8.ld','vector_map.json','tools/check_boot_image.py','tools/run_boot_link_tests.py']}}
(O/'DRAFT_REFERENCE_MANIFEST.json').write_text(json.dumps(reference,indent=2)+'\n')
print('Draft reference bytes',len((O/'DRAFT_REFERENCE_MANIFEST.json').read_bytes()),'sha256',sha(O/'DRAFT_REFERENCE_MANIFEST.json'))
print('immutable symbol count',len(filtered),'text range count',len(ranges),'function symbol count',len(funcs))

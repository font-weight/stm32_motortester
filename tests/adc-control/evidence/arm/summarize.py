#!/usr/bin/env python3
"""Reproduce known-frame estimates; external callbacks/runtime are NOT zero-cost."""
import hashlib,json,pathlib,re,sys
ROOT=pathlib.Path(__file__).resolve().parent
RUN1,RUN2=sys.argv[1:] if len(sys.argv)==3 else ('run-1','run-2')
A=ROOT/'runs'/RUN1;B=ROOT/'runs'/RUN2
r=json.loads((A/'report.json').read_text());r2=json.loads((B/'report.json').read_text());obj=A/'objects'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
checks=[]
for p in sorted((A/'objects').iterdir()):
 if p.suffix not in ('.o','.su'):continue
 q=B/'objects'/p.name
 checks.append({'artifact':'objects/'+p.name,'sha256':sha(p),'second_sha256':sha(q),'identical':p.read_bytes()==q.read_bytes()})
for name in ['modules-relocatable.o','abi_probe.c','abi-symbol-sizes.txt']:
 p=A/name;q=B/name
 checks.append({'artifact':name,'sha256':sha(p),'second_sha256':sha(q),'identical':p.read_bytes()==q.read_bytes()})
assert all(c['identical'] for c in checks)
(ROOT/'reproduction.json').write_text(json.dumps({'runs':[RUN1,RUN2],'all_identical':True,'checks':checks},indent=2)+'\n')
frames={};nodes={};calls={};indirect=[];global_names={}
for x in r['stack_usage']:
 assert x['kind']=='static'
 frames[x['unit'],x['function']]=x['bytes']
for f in obj.glob('*.symbols.txt'):
 for line in f.read_text().splitlines():
  m=re.match(r'[0-9a-f]+\s+[0-9a-f]+\s+([Tt])\s+(\S+)$',line)
  if m:
   name=m[2];unit=f.name.split('.')[0];key=(unit,name)
   framekey=(unit,re.sub(r'\.(?:\d+)$','',name)) if key not in frames else key
   assert framekey in frames,key
   nodes[key]=frames[framekey]
   if m[1]=='T':
    assert name not in global_names;global_names[name]=key
for f in obj.glob('*.disassembly.txt'):
 unit=f.name.split('.')[0];caller=None
 for line in f.read_text().splitlines():
  m=re.match(r'[0-9a-f]+ <([^>]+)>:',line)
  if m:caller=(unit,m[1])
  m=re.search(r'R_ARM_THM_(CALL|JUMP24)\s+(\S+)',line)
  if m:
   target=(unit,m[2]) if (unit,m[2]) in nodes else global_names.get(m[2],('EXTERNAL',m[2]))
   calls.setdefault(caller,[]).append({'target':target,'kind':m[1],'evidence':f.name+': '+line.strip()})
  m=re.search(r'\b(blx|bx)\s+(r\d+|ip|sl|fp)\b',line)
  if m:indirect.append({'caller':caller,'kind':m[1],'evidence':f.name+': '+line.strip()})
# Union of bound function pointers, as installed by mt_adc_control_init.
# These are source-confirmed edges, not relocation-resolved direct calls.
bound={
 ('mt_control','mt_control_receive'):[('mt_adc_control',x) for x in ['start','stop','profile']],
 ('mt_control','mt_control_sample'):[('mt_adc_control','stop')],
 ('mt_control','mt_control_recover'):[('mt_adc_control','stop')],
 ('adc_timer_dma','rd'):[('adc_timer_dma','mt_adc_dma_mmio_read32')],
 ('adc_timer_dma','wr'):[('adc_timer_dma','mt_adc_dma_mmio_write32')],
}
for caller,targets in bound.items():
 assert any(tuple(i['caller'])==caller for i in indirect),caller
 for target in targets:
  calls.setdefault(caller,[]).append({'target':target,'kind':'BOUND_INDIRECT_ASSUMPTION','evidence':'mt_adc_control_init assigns control callbacks; rd/wr assume supplied native MMIO helpers'})
def best(node,seen=()):
 assert node not in seen,('recursion',node,seen)
 if node[0]=='EXTERNAL':return 0,[node]
 options=[(0,[])]
 for edge in calls.get(node,[]):options.append(best(tuple(edge['target']),seen+(node,)))
 amount,path=max(options,key=lambda x:x[0])
 return nodes[node]+amount,[node]+path
summary_roots=['mt_control_receive','mt_control_poll','mt_control_sample','mt_control_recover','mt_adc_control_init','mt_adc_control_service','mt_adc_dma_start','mt_adc_dma_read_block','mt_stream_feed']
chains=[]
for name in summary_roots:
 amount,path=best(global_names[name]);chains.append({'root':name,'conservative_known_frame_sum_bytes':amount,'chain':[{'unit':u,'function':f,'self_frame_bytes':nodes.get((u,f)),'excluded':u=='EXTERNAL'} for u,f in path]})
stop=[global_names['mt_control_receive'],global_names['mt_capture_link_stop'],global_names['mt_tx_stop'],('mt_tx','append')]
for a,b in zip(stop,stop[1:]):assert any(tuple(e['target'])==b and e['kind']=='CALL' for e in calls[a]),(a,b)
stop_bytes=sum(nodes[n] for n in stop)
assert stop_bytes==2944
allmax=max(best(n)[0] for n in nodes)
assert allmax==stop_bytes
model={'method':'Sum compiler static self-frames along disassembly call graph; include tail-jump frames conservatively. Inlined code already belongs to containing frame. Callback edges use bound adapter and native-MMIO assumptions. Unknown runtime and external callback frames are excluded, not zero-cost.',
 'all_frames_static':True,'functions_with_frames':len(nodes),'known_graph_acyclic':True,'entry_chains':chains,
 'max_known_frame_sum_bytes':allmax,
 'maximum_chain_direct_non_tail_edges_verified':True,
 'verified_stop_chain':[{'unit':u,'function':f,'self_frame_bytes':nodes[u,f]} for u,f in stop],
 'maximum_chain_frames_live_together':'Prologues allocate 640,1696,592,16 B; inspected direct call sites occur before matching epilogues.',
 'indirect_calls':indirect,'bound_callback_edges':[{'caller':a,'targets':b} for a,b in bound.items()],
 'excluded_unknown_callbacks':['mt_adc_control.start: now_ms(clock_context), driver.io.now_us(user)','adc_timer_dma.now: driver.io.now_us(user)','mt_tx_poll: USB write(context,bytes,length)','Any other supplied read32/write32 implementation instead of native helpers'],
 'exclusions':['libc memcmp/memcpy/memmove/memset call frames','foreground caller/main/stream-to-control dispatch frame','USB service glue and USB stack','IRQ entry hardware stacking, handlers and nesting','linker veneers','RTOS/task stack switching, if later added','recursive/reentrant callbacks prohibited by model'],
 'call_graph':[{'caller':a,'edges':b} for a,b in sorted(calls.items())],
 'actual_hardware_stack_high_water_measured':False,'complete_stack_upper_bound_proven':False}
(ROOT/'stack-chains.json').write_text(json.dumps(model,indent=2)+'\n')
text=sum(x['text_rodata'] for x in r['object_sizes']);owner=r['abi_sizes']['resource_owner_model']
summary={'scope':'IO-001: ten Cortex-M3 module objects including controller, ADC binding/backend and RX stream; not full firmware',
 'status':'ARM_OBJECT_COMPILE_REPRODUCED','complete_firmware_budget_proven':False,'independent_review_score':None,
 'source_manifest_sha256':r['source_manifest_sha256'],'combined_object_sha256':r['combined_hash']['sha256'],
 'text_rodata_bytes':text,'module_data_bytes':sum(x['data'] for x in r['object_sizes']),'module_bss_bytes':sum(x['bss'] for x in r['object_sizes']),
 'owner_model_bytes':owner,'owner_fields':[{ 'field':f,'type':t,'size':r['abi_sizes'][t],'offset':r['owner_field_offsets'][f]} for f,t in [('control','mt_control'),('driver','mt_adc_dma_t'),('binding','mt_adc_control'),('rx_stream','mt_stream'),('rx_message','mt_wire_message')]],
 'duplicates_avoided':['session/TX/link/acquisition already embedded in control','DMA ring/snapshot embedded in ADC driver','service block local on stack, not persistent owner'],
 'known_frame_conservative_max_bytes':allmax,
 'sram_model_plus_known_frame_bytes':owner+allmax,
 'documented_limits_bytes':{'flash':65536,'sram':20480},
 'arithmetic_headroom_before_missing_costs_bytes':{'flash':65536-text,'sram_before_stack':20480-owner,'sram_after_known_frames':20480-owner-allmax,'sram_with_hypothetical_4096_stack':20480-owner-4096},
 'hypothetical_4096_stack_proven_sufficient':False,'two_builds_byte_identical':True,
 'remaining_undefined_symbols':sorted(re.findall(r'U\s+(\S+)',r['undefined_symbols'])),
 'unverified':['full ELF/BIN/HEX with linker memory ranges','startup/vector table/clock/timebase glue','USB code/buffers/PMA ownership','standard runtime code/stack','external callbacks and foreground callers','IRQ nesting/stack hardware high-water','WCET and <2560 us service deadline','hardware operation and measurement','independent quality-gate verdict']}
(ROOT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2));print(json.dumps(chains,indent=2))

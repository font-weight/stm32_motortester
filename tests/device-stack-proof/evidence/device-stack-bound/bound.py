#!/usr/bin/env python3
import pathlib,json,sys,collections
import runpy
O=pathlib.Path(__file__).parent;B=runpy.run_path(str(O/'bindings.py'))['B'];G=json.loads((O/'graph-initial.json').read_text())
def addr(obj,name):
 matches=[a for a,f in G.items() if f['object']==obj and f['name']==name]
 assert len(matches)==1,(obj,name,matches)
 return matches[0]
resolved=[]
for a,f in G.items():
 for c in f['indirect']:
  b=B[c['site']];targets=[addr(*t) for t in b['targets']]
  resolved.append(dict(function=a,function_name=f['name'],object=f['object'],**c,**b,resolved_addresses=targets))
  for t in targets:f['calls'].append(dict(site=c['site'],op=c['op'],target=t,indirect=True))
assert len(B)==len(resolved)
assert all(f['frame'] is not None and f['qualifier'] in ('static','handwritten-proven-zero') for f in G.values())
E={a:{c['target'] for c in f['calls']} for a,f in G.items()}
# Conservative: keep caller full .su frame across all child calls/tail calls.
vis=set();active=[];memo={};cycles=[]
def longest(a):
 if a in active:
  cycles.append(active[active.index(a):]+[a]);raise ValueError('CYCLE '+str(cycles))
 if a in memo:return memo[a]
 active.append(a)
 choices=[longest(t) for t in E[a]]
 n,path=max(choices,default=(0,[]),key=lambda x:x[0]);active.pop()
 memo[a]=(G[a]['frame']+n,[a]+path);return memo[a]
def reach(a):
 todo=[a];seen=set()
 while todo:
  n=todo.pop()
  if n in seen:continue
  seen.add(n);todo.extend(E[n])
 return seen
results={}
for obj,name in [('Os-mt_device_main.o','main'),('5-mt_runtime.o','mt_runtime_step'),('mt_usb_irq.o','USB_HP_CAN1_TX_IRQHandler'),('mt_usb_irq.o','USB_LP_CAN1_RX0_IRQHandler'),('22-usbd.o','tud_task_ext'),('23-dcd_stm32_fsdev.o','dcd_int_handler'),('Os-mt_boot_cpu.o','mt_boot_cpu_halt'),('reset.o','Reset_Handler'),('reset.o','Default_Handler')]:
 a=addr(obj,name);n,p=longest(a);r=reach(a)
 results[name]={'root':a,'bound_bytes':n,'path':[{'address':x,'name':G[x]['name'],'object':G[x]['object'],'frame':G[x]['frame']} for x in p],'reachable_count':len(r),'reachable':sorted(r),'reachable_indirect_sites':[x for x in resolved if x['function'] in r]}
# pre-admission main edges exclude runtime step only; caller88 persists throughout.
main=addr('Os-mt_device_main.o','main');step=addr('5-mt_runtime.o','mt_runtime_step')
preds=E[main]-{step};n,p=max((longest(x) for x in preds),key=lambda x:x[0]);results['pre_admission']={'bound_bytes':G[main]['frame']+n,'path':[{'address':x,'name':G[x]['name'],'object':G[x]['object'],'frame':G[x]['frame']} for x in [main]+p]}
json.dump(G,open(O/'graph-resolved.json','w'),indent=2)
json.dump(resolved,open(O/'indirect-resolutions.json','w'),indent=2)
json.dump(results,open(O/'bounds.json','w'),indent=2)
for name,res in results.items():print(name,res['bound_bytes'],res.get('reachable_count',''), ' -> '.join(x['name']+'('+str(x['frame'])+')' for x in res['path']))
# All graph components also cycle-free, even linked but unused public APIs.
for a in G:longest(a)
print('All graph cycles',cycles)
for name in ['usbd_defer_func','tud_deinit']:
 a=addr('22-usbd.o',name);print(name,'direct/indirect predecessors',[(p,G[p]['name']) for p in G if a in E[p]])

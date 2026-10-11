#!/usr/bin/env python3
"""Replay the bounded local static analysis after strict immutable input checks."""
import pathlib,hashlib,json,subprocess,sys,os,datetime
O=pathlib.Path(__file__).resolve().parent
manifest=json.loads((O/'INPUTS_SHA256.json').read_text())
def verify():
 errors=[]
 for p,h in manifest.items():
  q=pathlib.Path(p)
  if not q.is_file() or hashlib.sha256(q.read_bytes()).hexdigest()!=h:errors.append(p)
 if errors:raise RuntimeError('Input SHA mismatch: '+str(errors))
verify();commands=[]
for script,outname in [('analyze.py','initial.txt'),('inventory.py','inventory-check.txt'),('verify_machine.py','machine-check.txt'),('bound.py','bounds.txt')]:
 argv=[sys.executable,'-I','-B',str(O/script)]
 p=subprocess.run(argv,cwd=O,capture_output=True,text=True,check=False,timeout=120)
 (O/outname).write_text(p.stdout);(O/(outname+'.stderr')).write_text(p.stderr)
 commands.append({'argv':argv,'exit_code':p.returncode,'stdout':outname,'stderr':outname+'.stderr'})
 if p.returncode:raise RuntimeError(script+' failed: '+p.stderr)
verify()
bounds=json.loads((O/'bounds.json').read_text());fg=bounds['main']['bound_bytes'];irq=max(bounds['USB_HP_CAN1_TX_IRQHandler']['bound_bytes'],bounds['USB_LP_CAN1_RX0_IRQHandler']['bound_bytes']);entry=36;normal=fg+irq+entry;fault=normal+2*entry
result={'status':'SUPPORTING_ANALYSIS_COMPLETE_INDEPENDENT_ACCEPTANCE_PENDING','input_manifest_sha256':hashlib.sha256((O/'INPUTS_SHA256.json').read_bytes()).hexdigest(),'inputs_unchanged':True,'commands':commands,'python_version':sys.version,'foreground_including_main':fg,'usb_irq_software':irq,'single_exception_allowance':entry,'normal_combined_upper_bound':normal,'with_terminal_hardfault_nmi_upper_bound':fault,'rounded_up_to_8':(fault+7)//8*8,'not_hardware_measurement':True,'not_production_stack_selection':True}
(O/'REPLAY_RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))

#!/usr/bin/env python3
"""Audit saved USB005 ARM object evidence, without loading or executing ARM code."""
import argparse, collections, hashlib, json, re
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);p.add_argument('--compare-run',type=Path);a=p.parse_args();d=a.run
ops={'xfer':'dcd_edpt_xfer','fifo':'dcd_edpt_xfer_fifo','open':'dcd_edpt_open','stall':'dcd_edpt_stall','clear_stall':'dcd_edpt_clear_stall','address':'dcd_set_address','status':'dcd_edpt0_status_complete','sof':'dcd_sof_enable','close_all':'dcd_edpt_close_all'}
def relocs(name):
    lines=(d/(name+'-objdump.stdout')).read_text().splitlines();section=None;items=[]
    for line in lines:
        m=re.match(r'Disassembly of section (.*):',line)
        if m:section=m[1]
        m=re.search(r'^\s*([0-9a-f]+):\s+(R_ARM_[A-Z0-9_]+)\s+(\S+)',line)
        if m:items.append({'section':section,'offset':m[1],'kind':m[2],'target':m[3]})
    return items
core=relocs('core-on');bridge=relocs('bridge-on');dcd=relocs('dcd-on');adapter=relocs('adapter-on')
is_call=lambda x:x['kind'] in ('R_ARM_THM_CALL','R_ARM_THM_JUMP24')
core_calls=[x for x in core if is_call(x)]
coverage={}
for suffix,raw in ops.items():
    name='mt_usb_bridge_'+suffix
    calls=[x for x in core_calls if x['target']==name]
    bridge_calls=[x for x in bridge if x['section']=='.text.'+name and is_call(x)]
    targets={x['target'] for x in bridge_calls}
    expected={'mt_usb_bridge_lock','permitted','mt_usb_bridge_unlock',raw}
    coverage[name]={'raw_target':raw,'core_call_relocations':calls,'bridge_call_relocations':bridge_calls,'expected_guard_and_raw_targets_present':expected.issubset(targets)}
    assert calls,name+' missing core relocation'
    assert expected.issubset(targets),(name,expected-targets)
raw_leaks=[x for x in core_calls if x['target'] in ops.values()]
assert not raw_leaks,raw_leaks
address=[x for x in dcd if x['section']=='.text.dcd_set_address' and is_call(x)]
assert {x['target'] for x in address}=={'dcd_edpt_xfer','mt_usb_bridge_address_result'}
adapter_scopes={}
for suffix in ('open_epoch','write','step'):
    name='mt_usb_cdc_'+suffix
    targets={x['target'] for x in adapter if x['section']=='.text.'+name and is_call(x)}
    adapter_scopes[name]={'targets':sorted(targets),'entry_and_leave_present':{'mt_usb_bridge_app_enter','mt_usb_bridge_leave'}.issubset(targets)}
    assert adapter_scopes[name]['entry_and_leave_present']
# Same diagnostics except path/line-number shifts from added preprocessor branches.
def warning_messages(name):
    return [line.split('warning:',1)[1].strip() for line in (d/(name+'-compile.stderr')).read_text().splitlines() if 'warning:' in line]
warning_identity=warning_messages('dcd-on')==warning_messages('dcd-off')==warning_messages('dcd-base')
assert warning_identity
summary=json.loads((d/'summary.json').read_text());jobs=summary['objects']
resource_totals={}
for variant,names in [('enabled',['bridge-on','core-on','dcd-on','adapter-on']),('optout',['core-off','dcd-off','adapter-off']),('prior_baseline',['core-base','dcd-base','adapter-base'])]:
    resource_totals[variant]={key:sum(jobs[n][key] for n in names) for key in ('text','data','bss')}
repro=None
if a.compare_run:
    before=json.loads((a.compare_run/'summary.json').read_text())
    repro={n:jobs[n]['object_sha256']==before['objects'][n]['object_sha256'] for n in jobs}
    assert all(repro.values()),repro
result={'coverage':coverage,'guarded_raw_core_call_relocations':raw_leaks,'other_raw_core_dcd_call_relocations':[x for x in core_calls if x['target'].startswith('dcd_')],'address_call_relocations':address,'address_result_constant_true':'Pinned FSDEV edpt_xfer always returns true; dcd_set_address disassembly contains movs r0,#1 before jumping to mt_usb_bridge_address_result. Hook wiring does not prove failed target submissions or physical ACK.','adapter_scope_relocations':adapter_scopes,'dcd_warning_messages_identical_to_baseline':warning_identity,'dcd_warning_messages':warning_messages('dcd-on'),'selected_object_totals_bytes':resource_totals,'all_object_hashes_equal_to_compare_run':repro,'scope':'Static preprocessing, relocation/disassembly, sizes and compiler local stack frames. Symbol-presence assertions alone do not prove control flow, interrupt exclusion or target behavior. No firmware link.'}
(d/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
(d/'selected-relocations.txt').write_text(json.dumps({'coverage':coverage,'address':address,'adapter':adapter_scopes},indent=2)+'\n')
print(json.dumps({'shim_count':len(coverage),'raw_guarded_leaks':raw_leaks,'warnings_identical':warning_identity,'totals':resource_totals,'reproducible':repro},indent=2))

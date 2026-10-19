#!/usr/bin/env python3
"""Selected native sensitivity controls; mutations exist only in --output."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

p=argparse.ArgumentParser()
p.add_argument('source',type=Path)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
source=a.source.resolve(); out=a.output.resolve()
try:
    out.mkdir(parents=True,exist_ok=False)
except FileExistsError:
    p.error(f'output already exists; refusing to overwrite evidence: {out}')
print(f'Results: {out}')
here=Path(__file__).resolve().parent
mutants=[
    ('wrong_tail_offset','all_tails','d->stop_offset = d->next_half ? MT_ADC_DMA_BLOCK_SAMPLES : 0u;','d->stop_offset = 0u;'),
    ('wrong_exclusive_end','all_tails','d->stop_dma_end = d->first_sample_index + k;','d->stop_dma_end = d->first_sample_index + k + 1u;'),
    ('forgotten_history','external_history','if (!history || !d->stream_history_valid) return hardware_off(d);','(void)history;'),
    ('overwritten_first_cause','fault_then_retry','if (d->first_fault == MT_ADC_DMA_FAULT_NONE) d->first_fault = fault;','d->first_fault = fault;'),
    ('unchecked_stop_overflow','direct_stop_overflow','if (UINT64_MAX - d->first_sample_index < k) {','if (false) {'),
    ('unchecked_sample_code','bad_codes','if (sample > 4095u) {','if (false) {'),
    ('lying_hardware_off','stop_failures','out->hardware_off = result == MT_ADC_DMA_OK;','out->hardware_off = true;'),
    ('missing_pending_event_cause','pending_event_precedence',
     'if (d->stream_history_valid &&\n                (events & (d->next_half ? DMA_HT : DMA_TC)) != 0u)\n                invalidate_history(d, MT_ADC_DMA_FAULT_DEADLINE);',
     '/* Mutation removes pending event cause before disable. */'),
]
cc=os.environ.get('CC','cc')
base=(source/'firmware/platform/adc_timer_dma.c').read_text()
results=[]
for name,case,old,new in mutants:
    assert base.count(old)==1,(name,'mutation anchor changed')
    folder=out/name; platform=folder/'firmware/platform'; platform.mkdir(parents=True,exist_ok=False)
    (platform/'adc_timer_dma.c').write_text(base.replace(old,new))
    shutil.copy2(source/'firmware/platform/adc_timer_dma.h',platform/'adc_timer_dma.h')
    command=[cc,'-std=c11','-Wall','-Wextra','-Wpedantic','-Wconversion','-Wsign-conversion','-Werror','-O2','-I'+str(platform),str(platform/'adc_timer_dma.c'),str(here/'test_stop_certificate.c'),'-o',str(folder/'test')]
    build=subprocess.run(command,capture_output=True,text=True)
    (folder/'build.log').write_text(' '.join(command)+'\n'+build.stdout+build.stderr)
    result={'mutation':name,'case':case,'build_returncode':build.returncode}
    if build.returncode==0:
        run=subprocess.run([str(folder/'test'),case],capture_output=True,text=True)
        (folder/'run.log').write_text(run.stdout+run.stderr+f'\nexit={run.returncode}\n')
        result['run_returncode']=run.returncode
        result['killed']=run.returncode==1
    else: result['killed']=False
    results.append(result)
    print(name, 'KILLED' if result['killed'] else 'NOT PROVEN')
# Independent sensitivity check for the actual legacy adapter range guard.
name='unchecked_legacy_narrowing'
folder=out/name; folder.mkdir(parents=True,exist_ok=False)
app=(source/'firmware/app/mt_adc_control.c').read_text()
old='block.first_trigger_offset_us != MT_ADC_DMA_SAMPLE_US ||\n        block.first_sample_index > UINT32_MAX - (block.count - 1u))'
new='block.first_trigger_offset_us != MT_ADC_DMA_SAMPLE_US)'
assert app.count(old)==1,(name,'mutation anchor changed')
(folder/'mt_adc_control.c').write_text(app.replace(old,new))
command=[cc,'-std=c11','-Wall','-Wextra','-Wpedantic','-Wconversion','-Wsign-conversion','-Werror','-O2','-I'+str(source/'firmware/app'),'-I'+str(source/'firmware/platform'),str(folder/'mt_adc_control.c'),str(here/'test_legacy_narrowing.c'),'-o',str(folder/'test')]
build=subprocess.run(command,capture_output=True,text=True)
(folder/'build.log').write_text(' '.join(command)+'\n'+build.stdout+build.stderr)
result={'mutation':name,'case':'isolated_legacy_narrowing','build_returncode':build.returncode}
if build.returncode==0:
    run=subprocess.run([str(folder/'test')],capture_output=True,text=True)
    (folder/'run.log').write_text(run.stdout+run.stderr+f'\nexit={run.returncode}\n')
    result['run_returncode']=run.returncode
    result['killed']=run.returncode!=0 and 'Assertion' in run.stderr
else: result['killed']=False
results.append(result)
print(name, 'KILLED' if result['killed'] else 'NOT PROVEN')
(out/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
sys.exit(0 if all(x['killed'] for x in results) else 1)

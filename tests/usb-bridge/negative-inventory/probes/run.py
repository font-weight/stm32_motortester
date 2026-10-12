#!/usr/bin/env python3
"""Independent observations, not a bridge implementation or concurrency proof."""
import hashlib, json, pathlib, re, shlex, subprocess
root = pathlib.Path(__file__).resolve().parent.parent
out = root/'evidence'; out.mkdir(exist_ok=True)
source = pathlib.Path('/workspace/shared/usb-dependencies/vendor/tinyusb/src')
harness = pathlib.Path('/workspace/shared/usb-cdc-tests')
commands=[]; inputs={}; results=[]
def run(args):
    args = [str(a) for a in args]
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    commands.append(dict(argv=args, exit_code=p.returncode, output=p.stdout))
    if p.returncode: raise RuntimeError(shlex.join(args)+'\n'+p.stdout)
    return p.stdout
def remember(p):
    inputs[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
try:
    run(['gcc','--version'])
    for mode in ['buffered','f1']:
        build=out/mode; build.mkdir(exist_ok=True)
        cfg=harness/('config-'+mode)
        common=['-O1','-g','-fno-lto','-Wall','-Wextra','-I'+str(cfg),'-I'+str(harness)]
        if mode=='f1': common+=['-DSTM32F103xB']
        sources=[source/p for p in ['tusb.c','common/tusb_fifo.c','device/usbd.c','class/cdc/cdc_device.c']]+[harness/'fake_dcd.c',root/'probes/core_probe.c']
        objs=[]
        for i,p in enumerate(sources):
            remember(p); obj=build/(str(i)+'_'+p.stem+'.o'); objs.append(obj)
            flags=['-std=gnu11','-I'+str(source)] if i<4 else ['-std=c11','-pedantic-errors','-Werror','-isystem',str(source)]
            run(['gcc',*common,*flags,'-c',p,'-o',obj])
        remember(cfg/'tusb_config.h')
        exe=build/'core_probe'
        run(['gcc',*objs,'-Wl,--wrap=dcd_edpt_xfer','-Wl,--wrap=dcd_edpt_xfer_fifo','-Wl,--wrap=dcd_edpt0_status_complete','-Wl,--wrap=dcd_edpt_close_all','-Wl,--wrap=dcd_sof_enable','-o',exe])
        for scenario in ['weak_fifo_bypass','active_tx_reset','active_rx_reset','failed_cdc_completion','failed_ep0_completion','config_status_false','config_teardown_order']:
            result=run([exe,scenario]); print(mode,scenario,result.strip()); results.append(dict(mode=mode,scenario=scenario,observed=result.strip(),meaning='negative control reproduced'))
        (build/'core_symbols.txt').write_text(run(['nm',objs[2]]))
        macro = run(['gcc',*common,'-I'+str(source),'-include','tusb.h','-dM','-E','-x','c','/dev/null'])
        (build/'effective_macros.txt').write_text(macro)
    # Extract exact pinned SET_ADDRESS function into a tiny same-TU test.
    p=source/'portable/st/stm32_fsdev/dcd_stm32_fsdev.c'; remember(p)
    text=p.read_text(); match=re.search(r'void dcd_set_address\(.*?\n\}',text,re.S); assert match
    fragment=match.group(0)
    c='''#include <stdbool.h>\n#include <stddef.h>\n#include <stdint.h>\n#include <assert.h>\n#include <stdio.h>\n#define TUSB_DIR_IN_MASK 0x80\nstatic unsigned actual, wrapped;\nbool dcd_edpt_xfer(uint8_t,uint8_t,uint8_t*,uint16_t,bool);\n'''+fragment+'''
bool dcd_edpt_xfer(uint8_t p,uint8_t e,uint8_t*b,uint16_t n,bool i) { (void)p;(void)e;(void)b;(void)n;(void)i; ++actual; return false; }
bool __wrap_dcd_edpt_xfer(uint8_t p,uint8_t e,uint8_t*b,uint16_t n,bool i) { (void)p;(void)e;(void)b;(void)n;(void)i; ++wrapped; return false; }
int main(void) { dcd_set_address(0,7); assert(actual==1 && wrapped==0); printf("exact FSDEV SET_ADDRESS: internal xfer=false, void returned, actual=%u, wrapped=%u\\n",actual,wrapped); }
'''
    extract=out/'exact_address_probe.c'; extract.write_text(c)
    for opt in ['O0','O2']:
        exe=out/('address_'+opt)
        run(['gcc','-std=c11','-pedantic-errors','-Wall','-Wextra','-Werror','-'+opt,extract,'-Wl,--wrap=dcd_edpt_xfer','-o',exe])
        observed=run([exe]); print(observed.strip()); results.append(dict(mode=opt,scenario='exact_address_false',observed=observed.strip(),meaning='negative control reproduced; exact function extracted, not full MMIO driver'))
finally:
    (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    (out/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
    (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    (out/'commands.log').write_text('\n'.join('$ '+shlex.join(c['argv'])+'\n'+c['output']+'exit='+str(c['exit_code']) for c in commands)+'\n')

#!/usr/bin/env python3
"""Reproducible native-only shim/core guard tests. Never writes source inputs."""
import argparse, hashlib, json, os, pathlib, subprocess
p=argparse.ArgumentParser(); p.add_argument('--sanitize', action='store_true'); p.add_argument('--integration', action='store_true'); p.add_argument('--adapter', action='store_true'); a=p.parse_args(); a.integration = a.integration or a.adapter
here=pathlib.Path(__file__).resolve().parent
bridge=pathlib.Path(os.getenv('USB_BRIDGE_ROOT',str(here.parent/'usb-bridge-draft')))
epoch=pathlib.Path(os.getenv('USB_EPOCH_ROOT',str(here.parent/'usb-epoch-draft')))
dep=pathlib.Path(os.getenv('USB_DEPENDENCIES',str(here.parent/'usb-dependencies')))
cdc=pathlib.Path(os.getenv('USB_CDC_TESTS',str(here.parent/'usb-cdc-tests')))
build=here/('build'+('-adapter' if a.adapter else '-integration' if a.integration else '-shims')+('-sanitize' if a.sanitize else '')); build.mkdir(exist_ok=True)
cc=os.getenv('CC','gcc'); commands=[]; hashes={}; results=[]
def track(path):
    path=path.resolve(); hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest(); return path
common=['-g','-O1','-fno-lto','-Wall','-Wextra','-fno-omit-frame-pointer']
if a.sanitize: common += ['-fsanitize=address,undefined']
incs=['-DSTM32F103xB','-DCFG_MT_USB_BRIDGE=1','-I'+str(cdc/'config-f1'),'-I'+str(here),'-I'+str(bridge/'firmware/transport'),'-I'+str(epoch/'firmware/transport'),'-isystem',str(dep/'vendor/tinyusb/src')]
project=[bridge/'firmware/transport/mt_usb_bridge.c',epoch/'firmware/transport/mt_usb_epoch.c',here/'test_shims.c']
upstream=[dep/'vendor/tinyusb/src/common/tusb_fifo.c']
if a.integration:
    project[-1]=here/'test_integration.c'; project.append(build/'fake_dcd_instrumented.c')
    upstream += [dep/'vendor/tinyusb/src/tusb.c',bridge/'build/usbd.c',dep/'vendor/tinyusb/src/class/cdc/cdc_device.c']
    import instrument_fake
    instrument_fake.generate(cdc,build,bridge); track(bridge/'build/dcd_stm32_fsdev.c'); track(here/'instrument_fake.py'); track(cdc/'fake_dcd.c'); track(cdc/'fake_dcd.h')
    incs += ['-I'+str(cdc)]
if a.adapter:
    adapter=pathlib.Path(os.getenv('USB_ADAPTER_ROOT',str(here.parent/'usb-adapter-draft')))
    incs += ['-DTEST_ADAPTER=1','-I'+str(adapter/'firmware/transport')]
    project += [bridge/'firmware/transport/mt_usb_cdc.c']
    for name in ['acquisition/acquisition','app/mt_capture_link','app/mt_control','app/mt_control_rx','protocol/mt_command','protocol/mt_session','protocol/mt_stream','protocol/mt_wire','transport/mt_tx']:
        project += [adapter/('firmware/'+name+'.c')]; track(adapter/('firmware/'+name+'.h'))
    track(bridge/'firmware/transport/mt_usb_cdc.h')
for path in [here/'run.py',bridge/'firmware/transport/mt_usb_bridge.h',epoch/'firmware/transport/mt_usb_epoch.h',cdc/'config-f1/tusb_config.h',dep/'source-manifest.json',dep/'SHA256SUMS.sources']:
    track(path)
if a.integration: track(here/'test_integration.h')
env=os.environ.copy()
if a.sanitize: env.update(ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
receipt=build/'receipt.json'; receipt.unlink(missing_ok=True)
with (build/'commands.log').open('w') as log:
    def run(cmd):
        commands.append({'argv':cmd}); log.write('+ '+' '.join(cmd)+'\n'); log.flush()
        r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env)
        commands[-1].update(exit_status=r.returncode,output=r.stdout); log.write(r.stdout); log.flush()
        print(r.stdout,end='')
        if r.returncode: raise SystemExit(r.returncode)
        return r.stdout
    try:
        run([cc,'--version']); objs=[]
        for line in (dep/'SHA256SUMS.sources').read_text().splitlines():
            expected,name=line.split(None,1); path=track(dep/name.strip()); assert hashes[str(path)] == expected, str(path)
        for i,src in enumerate(upstream+project):
            track(src); obj=build/f'{i}_{src.stem}.o'; objs.append(str(obj))
            flags=['-std=gnu11'] if src in upstream else ['-std=c11','-pedantic-errors','-Werror']
            run([cc]+common+flags+incs+['-c',str(src),'-o',str(obj)])
        exe=build/'test'; run([cc]+common+objs+['-o',str(exe)])
        out=run([str(exe)]); (build/'results.jsonl').write_text(out)
        for line in out.splitlines():
            if line.startswith('{'): results.append(json.loads(line))
        assert results and all(x['pass'] for x in results)
        track(exe)
    finally:
        receipt.write_text(json.dumps({'commands':commands,'inputs':hashes,'sanitizers':a.sanitize,'lsan_claimed':False,'scope':'native fake PRIMASK and fake DCD only; not IRQ/hardware evidence','results':results},indent=2)+'\n')
print(f'PASS: {len(results)} native scenarios; receipt {receipt}')

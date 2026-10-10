#!/usr/bin/env python3
"""Compile genuine upstream core/FIFO/CDC separately from strict project C11."""
import argparse, hashlib, json, os, pathlib, subprocess, sys
p = argparse.ArgumentParser(); p.add_argument('--sanitize', action='store_true'); p.add_argument('--adapter', action='store_true'); p.add_argument('--hwfifo', action='store_true'); a = p.parse_args()
here = pathlib.Path(__file__).resolve().parent
def locate(variable, marker, fallback):
    if os.getenv(variable): return pathlib.Path(os.environ[variable]).resolve()
    for parent in here.parents:
        if (parent / marker).is_file(): return parent
    return fallback.resolve()
fw = locate('USB_FIRMWARE_ROOT', 'firmware/transport/mt_usb_cdc.c', here.parent / 'usb-adapter-draft')
dep = pathlib.Path(os.getenv('USB_DEPENDENCIES', str(fw / 'firmware/third_party/usb'))).resolve()
if not (dep / 'source-manifest.json').is_file() and not os.getenv('USB_DEPENDENCIES'):
    dep = (here.parent / 'usb-dependencies').resolve()
config = here / ('config-f1' if a.hwfifo else 'config-buffered')
build = here / (('build-sanitize' if a.sanitize else 'build') + ('-f1' if a.hwfifo else '')); build.mkdir(exist_ok=True)
cc = os.getenv('CC', 'gcc'); common = ['-g', '-O1', '-Wall', '-Wextra', '-fno-omit-frame-pointer']
if a.sanitize: common += ['-fsanitize=address,undefined']
includes = (['-DSTM32F103xB'] if a.hwfifo else []) + ['-I'+str(config), '-I'+str(here), '-isystem', str(dep/'vendor/tinyusb/src'), '-I'+str(fw)]
upstream = [dep/'vendor/tinyusb/src'/x for x in ['tusb.c', 'common/tusb_fifo.c', 'device/usbd.c', 'class/cdc/cdc_device.c']]
project = [here/'fake_dcd.c', here/('test_adapter.c' if a.adapter else 'test_core.c')]
if a.adapter: project += [fw/'firmware'/x for x in [
    'acquisition/acquisition.c', 'app/mt_capture_link.c', 'app/mt_control.c', 'app/mt_control_rx.c',
    'protocol/mt_command.c', 'protocol/mt_session.c', 'protocol/mt_stream.c', 'protocol/mt_wire.c',
    'transport/mt_tx.c', 'transport/mt_usb_cdc.c']]
commands, inputs, objects = [], [], []
receipt = build / ('adapter-receipt.json' if a.adapter else 'core-receipt.json')
receipt.unlink(missing_ok=True)
for path in [config/'tusb_config.h', here/'fake_dcd.h', here/'build.py', dep/'source-manifest.json', dep/'SHA256SUMS.sources'] + [src.with_suffix('.h') for src in project if src.is_relative_to(fw/'firmware')]:
    inputs.append({'path':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
log = build / ('adapter.log' if a.adapter else 'core.log')
with log.open('w') as out:
    def run(cmd):
        print('+ ' + ' '.join(cmd), flush=True); out.write('+ ' + ' '.join(cmd)+'\n'); out.flush(); commands.append(cmd)
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print(r.stdout, end=''); out.write(r.stdout); out.flush()
        if r.returncode: raise SystemExit(r.returncode)
    run([cc, '--version'])
    for n, src in enumerate(upstream + project):
        obj = build / (str(n)+'_'+src.stem+'.o'); objects.append(str(obj))
        inputs.append({'path':str(src), 'sha256':hashlib.sha256(src.read_bytes()).hexdigest()})
        dialect = ['-std=gnu11'] if src in upstream else ['-std=c11', '-pedantic-errors', '-Werror']
        inc = includes if src in project else [('-I' if x == '-isystem' else x) for x in includes]
        run([cc]+common+dialect+inc+['-c', str(src), '-o', str(obj)])
    exe = build / ('test_adapter' if a.adapter else 'test_core')
    run([cc]+common+objects+['-o', str(exe)])
    if a.sanitize: os.environ['ASAN_OPTIONS'] = 'detect_leaks=0:halt_on_error=1'; os.environ['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
    os.environ['USB_TEST_ARTIFACT_DIR'] = str(build)
    run([str(exe)])
receipt.write_text(json.dumps({'commands':commands, 'inputs':inputs, 'config_sha256':hashlib.sha256((config/'tusb_config.h').read_bytes()).hexdigest(), 'sanitize':a.sanitize, 'hwfifo':a.hwfifo, 'lsan_claimed':False, 'asan_options':os.getenv('ASAN_OPTIONS') if a.sanitize else None, 'ubsan_options':os.getenv('UBSAN_OPTIONS') if a.sanitize else None, 'executable_sha256':hashlib.sha256(exe.read_bytes()).hexdigest()},indent=2)+'\n')

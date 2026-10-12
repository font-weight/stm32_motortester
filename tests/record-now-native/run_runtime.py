#!/usr/bin/env python3
"""Native Single/record-now guarded-runtime tests; no target or host proof."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ['app/mt_runtime', 'platform/mt_timebase', 'platform/adc_timer_dma',
           'app/mt_adc_control', 'app/mt_control', 'app/mt_control_rx',
           'app/mt_capture_link', 'acquisition/acquisition', 'protocol/mt_command',
           'protocol/mt_session', 'protocol/mt_stream', 'protocol/mt_wire',
           'transport/mt_tx', 'transport/mt_usb_cdc', 'transport/mt_usb_epoch',
           'transport/mt_usb_bridge']

def snapshot():
    roots = [ROOT/'firmware', ROOT/'tests/record-now-native',
             ROOT/'tests/current-ab-reproducer', ROOT/'tests/usb-cdc/supporting',
             ROOT/'tests/usb-bridge/supporting']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in roots for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() or out == ROOT or ROOT in out.parents:
        raise ValueError('Use a fresh output directory outside the checkout')
    out.mkdir(parents=True)
    before = snapshot()
    (out/'inputs-before.json').write_text(json.dumps(before, indent=2)+'\n')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',
               UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    sys.dont_write_bytecode = True
    commands = []
    def run(label, argv):
        argv = list(map(str, argv)); commands.append({'label': label, 'argv': argv})
        result = subprocess.run(argv, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        (out/(label+'.log')).write_bytes(result.stdout)
        if result.returncode:
            sys.stdout.buffer.write(result.stdout)
            raise RuntimeError(f'{label}: exit {result.returncode}')
        return result.stdout
    cc = os.environ.get('CC', 'cc')
    run('compiler', [cc, '--version'])
    v = ROOT/'firmware/third_party/usb'; vendor = v/'vendor/tinyusb/src'
    results = []
    try:
        for mode in ('strict', 'sanitize'):
            m = out/mode; m.mkdir()
            for folder in ('stage1', 'patched', 'generator-input/build'):
                (m/folder).mkdir(parents=True)
            def job(label, argv):
                return run(mode+'-'+label, argv)
            job('setup', [sys.executable, '-I', '-B', v/'patches/apply_setup_patch.py',
                          vendor/'device/usbd.c', m/'stage1/usbd.c'])
            job('reset', [sys.executable, '-I', '-B', v/'patches/apply_f103_reset_patch.py',
                          vendor/'portable/st/stm32_fsdev/dcd_stm32_fsdev.c', m/'stage1/dcd_stm32_fsdev.c'])
            for kind, name in [('core', 'usbd.c'), ('fsdev', 'dcd_stm32_fsdev.c')]:
                job(kind, [sys.executable, '-I', '-B', v/'patches/apply_usb_bridge_patch.py',
                           kind, m/'stage1'/name, m/'patched'/name])
            shutil.copyfile(m/'patched/dcd_stm32_fsdev.c', m/'generator-input/build/dcd_stm32_fsdev.c')
            spec = importlib.util.spec_from_file_location('instrument_fake', ROOT/'tests/usb-bridge/supporting/instrument_fake.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            module.generate(ROOT/'tests/usb-cdc/supporting', m, m/'generator-input')
            incs = ['-DSTM32F103xB', '-DCFG_MT_USB_BRIDGE=1', '-I'+str(ROOT/'firmware/boot'),
                    '-I'+str(ROOT/'tests/current-ab-reproducer'), '-I'+str(ROOT/'tests/usb-cdc/supporting')]
            incs += ['-I'+str(ROOT/'firmware'/d) for d in ('app', 'platform', 'transport', 'protocol', 'acquisition')]
            incs += ['-isystem', str(vendor)]
            flags = ['-g', '-O1', '-fno-lto', '-Wall', '-Wextra', '-fno-omit-frame-pointer']
            if mode == 'sanitize':
                flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
            macros = job('config', [cc, '-std=c11', *incs, '-dM', '-E', '-include', 'tusb.h', '-x', 'c', '/dev/null'])
            for name, val in {'CFG_TUD_TASK_EVENTS_PER_RUN':'1', 'CFG_TUD_CDC_TX_BUFSIZE':'64',
                              'CFG_TUD_CDC_RX_BUFSIZE':'64', 'CFG_MT_USB_BRIDGE':'1', 'CFG_TUD_CDC':'1'}.items():
                if ('#define '+name+' '+val+'\n').encode() not in macros:
                    raise ValueError('wrong configuration: '+name)
            upstream = [vendor/'common/tusb_fifo.c', vendor/'tusb.c', m/'patched/usbd.c', vendor/'class/cdc/cdc_device.c']
            sources = upstream+[ROOT/('firmware/'+p+'.c') for p in PROJECT]+[ROOT/'tests/record-now-native/test_runtime.c', m/'fake_dcd_instrumented.c']
            objects = []
            for i, source in enumerate(sources):
                target = m/(str(i)+'.o'); objects.append(target)
                options = ['-std=gnu11'] if source in upstream else ['-std=c11', '-pedantic-errors', '-Werror', '-Wconversion', '-Wshadow']
                job('compile-'+str(i), [cc, *flags, *options, *incs, '-c', source, '-o', target])
            binary = m/'runtime'
            job('link', [cc, *flags, *objects, '-o', binary])
            cases = job('list', [binary, '--list']).decode().splitlines()
            if len(cases) != 29 or len(set(cases)) != 29:
                raise ValueError('unexpected case inventory')
            for operation in (4, 8):
                for case in cases:
                    data = json.loads(job(str(operation)+'-'+case, [binary, case, operation]))
                    if data.get('pass') is not True or data.get('case') != case:
                        raise ValueError('invalid result')
                    results.append(dict(data, mode=mode, operation=operation))
            print(mode+': 29 cases × ARM/RECORD_NOW = 58 executions passed', flush=True)
    finally:
        after = snapshot()
        (out/'inputs-after.json').write_text(json.dumps(after, indent=2)+'\n')
        (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        (out/'results.json').write_text(json.dumps(results, indent=2)+'\n')
        if before != after:
            raise RuntimeError('source inputs changed during execution')

if __name__ == '__main__':
    main()

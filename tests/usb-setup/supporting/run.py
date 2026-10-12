#!/usr/bin/env python3
"""Build real core/FIFO/CDC with an explicit usbd.c override; never edit inputs.

Each invocation requires a fresh output directory. Failed regression expectations
remain in the evidence; --expect upstream describes known negative controls only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

HERE = Path(__file__).resolve().parent
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--usbd', type=Path)
p.add_argument('--out', required=True, type=Path)
p.add_argument('--expect', choices=('fixed', 'upstream'), default='fixed')
p.add_argument('--dependencies', type=Path, default=HERE.parent / 'usb-dependencies')
p.add_argument('--harness', type=Path, default=HERE.parent / 'usb-cdc-tests')
p.add_argument('--sanitize', action='store_true')
p.add_argument('--cc', default=os.environ.get('CC', 'gcc'))
a = p.parse_args()
out = a.out.resolve()
if out.exists():
    p.error('Output directory exists; choose a fresh path to preserve prior evidence')
out.mkdir(parents=True)
dep, harness = a.dependencies.resolve(), a.harness.resolve()
tusb = dep / 'vendor/tinyusb/src'
usbd = (a.usbd or tusb / 'device/usbd.c').resolve()
scenarios = ['rejected_' + str(n) for n in (1, 255, 256, 257, 511, 512, 513, 1025)]
scenarios += ['supersession', 'capacity_supersession', 'mixed_1', 'mixed_256', 'mixed_257', 'reset_queued_setups']
negative = {'rejected_' + str(n) for n in (1, 255, 257, 511, 513, 1025)} | {'mixed_1', 'mixed_257'}
commands, matrix = [], []
inputs = {}
status = 1

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def record(path):
    inputs[str(path.resolve())] = sha(path)

def run(argv, check=True, cwd=None):
    argv = [str(x) for x in argv]
    env = os.environ.copy()
    if a.sanitize:
        env['ASAN_OPTIONS'] = 'detect_leaks=0:halt_on_error=1'
        env['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
    r = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    entry = {'argv': argv, 'cwd': str(cwd) if cwd else None, 'exit_code': r.returncode, 'output': r.stdout}
    commands.append(entry)
    print('$ ' + shlex.join(argv) + '\n' + r.stdout, end='', flush=True)
    if check and r.returncode:
        raise RuntimeError('Command failed with exit ' + str(r.returncode))
    return r

try:
    run([a.cc, '--version'])
    run(['ld', '--version'])
    run(['sha256sum', '-c', 'SHA256SUMS.sources'], cwd=dep)
    # Includes all frozen dependency headers and their version/provenance inputs.
    for path in sorted((dep / 'vendor').rglob('*')):
        if path.is_file(): record(path)
    for path in (dep / 'source-manifest.json', dep / 'SHA256SUMS.sources',
                 harness / 'fake_dcd.c', harness / 'fake_dcd.h', harness / 'test_core.c',
                 HERE / 'test_setup_queue.c', Path(__file__).resolve(), usbd): record(path)
    for mode in ('buffered', 'f1'):
        folder = out / mode
        folder.mkdir()
        config = harness / ('config-' + mode)
        record(config / 'tusb_config.h')
        common = ['-g', '-O1', '-Wall', '-Wextra', '-fno-omit-frame-pointer', '-fno-lto']
        if a.sanitize: common += ['-fsanitize=address,undefined', '-fno-pie']
        includes = ['-I' + str(config), '-I' + str(harness)]
        if mode == 'f1': includes += ['-DSTM32F103xB']
        upstream = [tusb / 'tusb.c', tusb / 'common/tusb_fifo.c', usbd, tusb / 'class/cdc/cdc_device.c']
        sources = upstream + [harness / 'fake_dcd.c', HERE / 'test_setup_queue.c']
        objects = []
        for n, src in enumerate(sources):
            obj = folder / (str(n) + '_' + src.stem + '.o')
            objects.append(obj)
            flags = ['-std=gnu11', '-I' + str(tusb)] if n < 4 else ['-std=c11', '-pedantic-errors', '-Werror', '-isystem', str(tusb)]
            run([a.cc, *common, *includes, *flags, '-c', src, '-o', obj])
        exe = folder / 'test_setup_queue'
        link_flags = ['-no-pie'] if a.sanitize else []
        run([a.cc, *common, *objects, *link_flags, '-o', exe])
        for scenario in scenarios:
            r = run([exe, scenario], check=False)
            expect_failure = a.expect == 'upstream' and scenario in negative
            matches = ((r.returncode == 1 and 'TEST_FAIL EP0_RESPONSE_MISSING ' in r.stdout)
                       if expect_failure else (r.returncode == 0 and 'PASS ' + scenario in r.stdout))
            matches &= 'AddressSanitizer' not in r.stdout and 'runtime error:' not in r.stdout
            matrix.append({'mode': mode, 'scenario': scenario, 'exit_code': r.returncode,
                           'expected': 'known_missing_ep0_response' if expect_failure else 'pass',
                           'expectation_met': bool(matches), 'executable_sha256': sha(exe)})
        # Preserve the existing complete EP0/CDC core regression against the same objects.
        core_obj = folder / '6_test_core.o'
        run([a.cc, *common, *includes, '-std=c11', '-pedantic-errors', '-Werror', '-isystem',
             str(tusb), '-c', harness / 'test_core.c', '-o', core_obj])
        core_exe = folder / 'test_core'
        run([a.cc, *common, *objects[:-1], core_obj, *link_flags, '-o', core_exe])
        r = run([core_exe], check=False)
        matrix.append({'mode': mode, 'scenario': 'existing_core', 'exit_code': r.returncode,
                       'expected': 'pass', 'expectation_met': r.returncode == 0,
                       'executable_sha256': sha(core_exe)})
    status = 0 if all(x['expectation_met'] for x in matrix) else 1
finally:
    changed = [path for path, digest in inputs.items() if sha(Path(path)) != digest]
    if changed: status = 1
    (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    (out / 'commands.log').write_text('\n'.join('$ ' + shlex.join(x['argv']) + '\n' + x['output'] + '\nexit=' + str(x['exit_code']) for x in commands) + '\n')
    (out / 'input-manifest.json').write_text(json.dumps(inputs, indent=2) + '\n')
    (out / 'matrix.json').write_text(json.dumps(matrix, indent=2) + '\n')
    (out / 'receipt.json').write_text(json.dumps({'expectation_profile': a.expect,
        'sanitizers': ['ASan', 'UBSan'] if a.sanitize else [], 'lsan_claimed': False,
        'asan_options': 'detect_leaks=0:halt_on_error=1' if a.sanitize else None,
        'ubsan_options': 'halt_on_error=1:print_stacktrace=1' if a.sanitize else None,
        'usbd_path': str(usbd), 'usbd_sha256': sha(usbd), 'input_files_changed': changed,
        'scenarios_executed': len(matrix), 'expectations_met': sum(x['expectation_met'] for x in matrix),
        'runner_exit_code': status, 'scope': 'serialized OS_NONE native fake DCD; no hardware/IRQ/epoch proof'}, indent=2) + '\n')
print('RESULT', status, sum(x['expectation_met'] for x in matrix), '/', len(matrix), 'expectations met')
sys.exit(status)

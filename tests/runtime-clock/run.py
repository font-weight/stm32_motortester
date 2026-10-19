#!/usr/bin/env python3
"""Current-source runtime regression; separate from the pinned A/B reproducer.

Run: python3 -I -B tests/runtime-clock/run.py --output /tmp/runtime-clock-UNIQUE
The historical manifest is verified, never rewritten or used to claim that the
current candidate is the historical input. Changed inputs are listed in receipt.
Changing supporting_runtime.c invalidates the historical package byte-identical
claim in PROVENANCE.md. Unchanged reproduce.py/inputs.json must reject this
current fixture; use the original pinned package for historical A/B reproduction.
The old helper main was never executed there. This runner retains 27 old oracles,
retains 11 coherent-clock cases, and adds 27 timed-admission cases. It explicitly
updates the old deadline-recovery expectation to the existing acquisition-error
STATUS contract under MT_RUNTIME_CLOCK_TESTS.
No host source, device, network, GUI or physical timing measurement is involved.
"""
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
PACKAGE = ROOT / 'tests/current-ab-reproducer'
PROJECT = ['app/mt_runtime', 'platform/mt_timebase', 'platform/adc_timer_dma',
           'app/mt_adc_control', 'app/mt_control', 'app/mt_control_rx',
           'app/mt_capture_link', 'acquisition/acquisition', 'protocol/mt_command',
           'protocol/mt_session', 'protocol/mt_stream', 'protocol/mt_wire',
           'transport/mt_tx', 'transport/mt_usb_cdc', 'transport/mt_usb_epoch',
           'transport/mt_usb_bridge']
MANIFEST_SHA256 = '9b0e2fc1f4f6b2b3dc1e96367f670a22a9263f80e24ce62eddf9754a3ff3e993'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    require(sys.flags.isolated and sys.dont_write_bytecode and not sys.flags.optimize,
            'run with python3 -I -B, without -O')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cc', default='/usr/bin/gcc', type=Path)
    args = parser.parse_args()
    out, cc = args.output, args.cc.resolve()
    require(out.is_absolute() and out.resolve() == out and not out.exists()
            and out.parent.is_dir(), 'output must be a fresh absolute nonsymlink path')
    require(not out.is_relative_to(ROOT) and not ROOT.is_relative_to(out),
            'output must be outside source checkout')
    require(cc.is_file(), 'compiler must exist')
    require(sha(PACKAGE / 'inputs.json') == MANIFEST_SHA256,
            'historical manifest identity changed')
    manifest = json.loads((PACKAGE / 'inputs.json').read_text())
    paths = [ROOT / item['path'] for item in manifest['a']]
    paths += [PACKAGE / name for name in ('supporting_runtime.c', 'test_integration.h',
                                        'inputs.json', 'reproduce.py')]
    paths += [Path(__file__).resolve()]
    before = {str(path.relative_to(ROOT)): sha(path) for path in paths}
    historical_differences = []
    for group, base in [('a', ROOT), ('package', PACKAGE)]:
        for item in manifest[group]:
            path = base / item['path']
            if path in paths and sha(path) != item['sha256']:
                historical_differences.append({'group': group, 'path': item['path'],
                    'historical_sha256': item['sha256'], 'current_sha256': sha(path)})
    out.mkdir()
    stage = out / 'source'
    for path in paths:
        target = stage / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    env = {'PATH': str(cc.parent)+':/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
           'TZ': 'UTC', 'PYTHONDONTWRITEBYTECODE': '1',
           'ASAN_OPTIONS': 'detect_leaks=0:halt_on_error=1',
           'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}
    receipt = {'status': 'RUNNING', 'test_kind': 'current_source_runtime_regression',
               'historical_reproducer_run': False, 'physical_verification': False,
               'historical_package_byte_identity_preserved': False,
               'historical_pin_gate': 'Expected to reject this current supporting_runtime.c; '
                                     'historical reproduce.py and inputs.json are unchanged.',
               'historical_cases_unchanged': 27, 'coherent_clock_cases': 11,
               'timed_admission_cases': 27,
               'timed_cen_boundary': 'successful pre-CNT to post-CNT observations; '
                   'includes pre-snapshot tail and post-snapshot head; full validation '
                   'and actor checks complete before accepting each callback; 20 us limit',
               'legacy_negative_controls': 'NULL timed callback preserves 32 us '
                   'legacy correlation and 37 us pair-only CEN rejection at 0.6 us/MMIO',
               'historical_case_oracle_updates': [{
                   'historical': 'adc_service_deadline_enters_recovery',
                   'current': 'adc_service_deadline_stops_and_reports_reason2',
                   'historical_assertions': 'step()==MT_RUNTIME_RECOVERY; !ready; '
                       '!hardware_running; pending==1; next step()==MT_RUNTIME_OK; quiescent',
                   'reason': 'Historical helper main was not executed by pinned reproducer; '
                             'current production preserves session, stops hardware and '
                             'publishes acquisition-error STATUS state5/reason2.'}],
               'host_or_windows_execution': False, 'leak_detection': False,
               'historical_manifest_sha256': MANIFEST_SHA256,
               'historical_input_differences': historical_differences,
               'inputs_before': before, 'compiler': str(cc), 'compiler_sha256': sha(cc),
               'python': sys.version, 'environment': env, 'commands': [], 'cases': {}}

    def save():
        (out / 'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')

    def run(label, argv):
        row = {'label': label, 'argv': list(map(str, argv)), 'cwd': str(out)}
        receipt['commands'].append(row)
        save()
        with (out/(label+'.stdout')).open('wb') as stdout, (out/(label+'.stderr')).open('wb') as stderr:
            result = subprocess.run(row['argv'], cwd=out, env=env, stdout=stdout,
                                    stderr=stderr, timeout=180, check=False)
        row['exit'] = result.returncode
        save()
        require(result.returncode == 0, label+' failed; retained stdout/stderr')
        return (out/(label+'.stdout')).read_text()

    try:
        run('compiler-version', [cc, '--version'])
        vendor_root = stage/'firmware/third_party/usb'
        vendor = vendor_root/'vendor/tinyusb/src'
        patches = vendor_root/'patches'
        package = stage/'tests/current-ab-reproducer'
        for mode in ('strict', 'sanitize'):
            build = out/mode
            for folder in ('stage1', 'patched', 'generator-input/build'):
                (build/folder).mkdir(parents=True, exist_ok=True)
            def job(label, argv):
                return run(mode+'-'+label, argv)
            job('patch-setup', [sys.executable, '-I', '-B', patches/'apply_setup_patch.py',
                               vendor/'device/usbd.c', build/'stage1/usbd.c'])
            job('patch-reset', [sys.executable, '-I', '-B', patches/'apply_f103_reset_patch.py',
                               vendor/'portable/st/stm32_fsdev/dcd_stm32_fsdev.c',
                               build/'stage1/dcd_stm32_fsdev.c'])
            for kind, name in [('core', 'usbd.c'), ('fsdev', 'dcd_stm32_fsdev.c')]:
                job('patch-'+kind, [sys.executable, '-I', '-B', patches/'apply_usb_bridge_patch.py',
                                   kind, build/'stage1'/name, build/'patched'/name])
            shutil.copyfile(build/'patched/dcd_stm32_fsdev.c',
                            build/'generator-input/build/dcd_stm32_fsdev.c')
            spec = importlib.util.spec_from_file_location('instrument_fake',
                       stage/'tests/usb-bridge/supporting/instrument_fake.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.generate(stage/'tests/usb-cdc/supporting', build, build/'generator-input')
            incs = ['-DSTM32F103xB', '-DCFG_MT_USB_BRIDGE=1', '-DMT_RUNTIME_CLOCK_TESTS=1',
                    '-I'+str(stage/'firmware/boot'), '-I'+str(package),
                    '-I'+str(stage/'tests/usb-cdc/supporting')]
            incs += ['-I'+str(stage/'firmware'/d) for d in
                     ('app', 'platform', 'transport', 'protocol', 'acquisition')]
            incs += ['-isystem', str(vendor)]
            flags = ['-g', '-O1', '-fno-lto', '-Wall', '-Wextra', '-fno-omit-frame-pointer']
            if mode == 'sanitize':
                flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                          '-fno-pie', '-no-pie']
            macros = job('effective-config', [cc, '-std=c11', *incs, '-dM', '-E',
                         '-include', 'tusb.h', '-x', 'c', '/dev/null'])
            for name, value in {'CFG_TUD_TASK_EVENTS_PER_RUN': '1',
                    'CFG_TUD_CDC_TX_BUFSIZE': '64', 'CFG_TUD_CDC_RX_BUFSIZE': '64',
                    'CFG_MT_USB_BRIDGE': '1', 'CFG_TUD_CDC': '1'}.items():
                require('#define '+name+' '+value+'\n' in macros, 'configuration '+name)
            upstream = [vendor/'common/tusb_fifo.c', vendor/'tusb.c',
                        build/'patched/usbd.c', vendor/'class/cdc/cdc_device.c']
            sources = upstream+[stage/('firmware/'+name+'.c') for name in PROJECT]
            sources += [package/'supporting_runtime.c', build/'fake_dcd_instrumented.c']
            objects = []
            for index, source in enumerate(sources):
                target = build/(str(index)+'-'+source.stem+'.o')
                objects.append(target)
                options = ['-std=gnu11'] if source in upstream else [
                    '-std=c11', '-pedantic-errors', '-Werror', '-Wconversion',
                    '-Wsign-conversion', '-Wshadow']
                job('compile-'+str(index), [cc, *flags, *options, *incs,
                                          '-c', source, '-o', target])
            executable = build/'runtime-clock'
            job('link', [cc, *flags, *objects, '-Wl,--wrap=mt_timebase_snapshot',
                         '-Wl,--wrap=mt_adc_dma_stop',
                         '-o', executable])
            cases = job('list', [executable, '--list']).splitlines()
            require(len(cases) == len(set(cases)) == 66, 'runtime case inventory')
            receipt['cases'][mode] = {}
            for case in cases:
                output = job('case-'+case, [executable, case])
                rows = [json.loads(line) for line in output.splitlines()]
                require(rows[-1]['case'] == case and rows[-1]['pass'], 'case output')
                receipt['cases'][mode][case] = rows
        require(receipt['cases']['strict'] == receipt['cases']['sanitize'],
                'strict/sanitizer outcome mismatch')
        receipt['status'] = 'PASS'
    except BaseException as exc:
        receipt.update(status='FAILED', error=type(exc).__name__+': '+str(exc))
        raise
    finally:
        after = {str(path.relative_to(ROOT)): sha(path) for path in paths}
        staged = {relative: sha(stage/relative) for relative in before}
        receipt['inputs_after'] = after
        receipt['source_unchanged'] = before == after
        receipt['staged_inputs_unchanged'] = before == staged
        if before != after or before != staged:
            receipt['status'] = 'FAILED_INPUT_DRIFT'
        save()
    require(receipt['status'] == 'PASS', receipt['status'])
    print(json.dumps({'status': 'PASS', 'cases_per_mode': len(receipt['cases']['strict']),
                      'historical_input_differences': len(historical_differences),
                      'receipt': str(out/'receipt.json'), 'physical_verification': False}))


if __name__ == '__main__':
    main()

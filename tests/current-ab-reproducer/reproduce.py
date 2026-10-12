#!/usr/bin/env python3
"""Pinned two-checkout Linux native evidence. Never opens a device or network."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys

MANIFEST_SHA256 = '9b0e2fc1f4f6b2b3dc1e96367f670a22a9263f80e24ce62eddf9754a3ff3e993'
HERE = Path(__file__).resolve().parent
PROJECT = ['app/mt_runtime', 'platform/mt_timebase', 'platform/adc_timer_dma',
           'app/mt_adc_control', 'app/mt_control', 'app/mt_control_rx',
           'app/mt_capture_link', 'acquisition/acquisition', 'protocol/mt_command',
           'protocol/mt_session', 'protocol/mt_stream', 'protocol/mt_wire',
           'transport/mt_tx', 'transport/mt_usb_cdc', 'transport/mt_usb_epoch',
           'transport/mt_usb_bridge']
HELPER = ['app/mt_control', 'app/mt_capture_link', 'protocol/mt_wire',
          'protocol/mt_command', 'protocol/mt_session', 'acquisition/acquisition',
          'transport/mt_tx']

def require(value, message):
    if not value:
        raise ValueError(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def canonical(raw):
    p = Path(raw)
    require(p.is_absolute() and str(p) == os.path.normpath(raw),
            'paths must be absolute and normalized')
    require(p.resolve() == p, 'symlink aliases are not allowed: '+str(p))
    return p

def disjoint(a, b):
    return not (a == b or a.is_relative_to(b) or b.is_relative_to(a))

def check_inputs(manifest, roots):
    checked = {}
    for root in roots.values():
        require(not any(p.is_symlink() for p in root.rglob('*')), 'symlink input entry: '+str(root))
    host_files = {str(p.relative_to(roots['b'])) for p in roots['b'].rglob('*') if p.is_file()}
    require(host_files == {r['path'] for r in manifest['b']}, 'unexpected/missing B file inventory')
    allowed_package = {r['path'] for r in manifest['package']} | {'reproduce.py', 'inputs.json', 'README.md', 'PROVENANCE.md'}
    package_files = {str(p.relative_to(roots['package'])) for p in roots['package'].rglob('*') if p.is_file()}
    require(package_files <= allowed_package, 'unexpected package file inventory')
    for rel, expected in manifest['a_include_directory_files'].items():
        directory = roots['a']/rel
        actual = sorted(p.name for p in directory.iterdir() if p.is_file())
        require(actual == expected, 'unexpected include-search file inventory: '+rel)
    vendor = roots['a']/'firmware/third_party/usb'
    require(sorted(str(p.relative_to(vendor)) for p in vendor.rglob('*') if p.is_file()) == manifest['a_vendor_files'],
            'unexpected vendor file inventory')
    for group, root in roots.items():
        seen = set()
        for item in manifest[group]:
            rel = Path(item['path'])
            require(not rel.is_absolute() and '..' not in rel.parts and
                    str(rel) == item['path'] and item['path'] not in seen,
                    'invalid manifest path')
            seen.add(item['path'])
            p = root / rel
            require(p.resolve() == p and p.is_file(), 'missing/aliased input '+str(p))
            require(p.stat().st_size == item['bytes'] and sha(p) == item['sha256'] and
                    stat.S_IMODE(p.stat().st_mode) == item['mode'],
                    'input bytes/mode mismatch '+str(p))
            checked[group+'/'+str(rel)] = item['sha256']
    return checked

def main():
    require(sys.platform.startswith('linux') and sys.version_info >= (3, 11),
            'requires Linux and Python 3.11+')
    require(sys.flags.isolated and sys.dont_write_bytecode and not sys.flags.optimize,
            'run with python3 -I -B, without -O')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--a-root', required=True)
    p.add_argument('--host-root', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--cc', default='/usr/bin/gcc')
    args = p.parse_args()
    a, host, out = map(canonical, (args.a_root, args.host_root, args.output))
    require(a.is_dir() and host.is_dir(), 'source roots must exist')
    require(disjoint(a, host), 'A and B must be distinct checkouts')
    require(not out.exists() and out.parent.is_dir(), 'output must be a fresh child of an existing directory')
    require(all(disjoint(out, root) for root in (a, host, HERE)),
            'output must not overlap source/package roots')
    cc = Path(args.cc)
    require(cc.is_absolute() and cc.is_file(), 'compiler must be an existing absolute path')
    cc = cc.resolve()
    require(sha(HERE/'inputs.json') == MANIFEST_SHA256, 'manifest identity mismatch')
    manifest = json.loads((HERE/'inputs.json').read_text())
    require(manifest['schema'] == 1, 'manifest version')
    roots = {'a': a, 'b': host, 'package': HERE}
    before = check_inputs(manifest, roots)
    before['runner'] = sha(Path(__file__))
    before['manifest'] = sha(HERE/'inputs.json')
    out.mkdir()
    (out/'tmp').mkdir()
    env = {'PATH': str(cc.parent)+':/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
           'TZ': 'UTC', 'TMPDIR': str(out/'tmp'), 'PYTHONDONTWRITEBYTECODE': '1',
           'ASAN_OPTIONS': 'detect_leaks=0:halt_on_error=1',
           'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}
    receipt = {'status': 'RUNNING', 'a_head': manifest['a_head'],
               'b_head': manifest['b_head'], 'inputs_before': before,
               'compiler': str(cc), 'compiler_sha256': sha(cc),
               'python': sys.version, 'python_executable': sys.executable,
               'environment': env, 'commands': [], 'physical_verification': False,
               'gui_or_windows_execution': False, 'leak_detection': False}
    source_a = a
    stage_a = out/'source-a'
    def check_stage():
        require(not any(p.is_symlink() for p in stage_a.rglob('*')), 'symlink in staged sources')
        actual = {str(p.relative_to(stage_a)) for p in stage_a.rglob('*') if p.is_file()}
        require(actual == {r['path'] for r in manifest['a']}, 'staged source inventory drift')
        for item in manifest['a']:
            path = stage_a/item['path']
            require(path.resolve() == path and sha(path) == item['sha256'] and
                    stat.S_IMODE(path.stat().st_mode) == item['mode'], 'staged source drift '+str(path))
    def save():
        (out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    def run(label, argv, cwd=out, timeout=180):
        row = {'label': label, 'argv': list(map(str, argv)), 'cwd': str(cwd),
               'timeout_seconds': timeout}
        receipt['commands'].append(row)
        save()
        with (out/(label+'.stdout')).open('wb') as stdout, (out/(label+'.stderr')).open('wb') as stderr:
            proc = subprocess.Popen(row['argv'], cwd=cwd, env=env, stdout=stdout,
                                    stderr=stderr, start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except BaseException as exc:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                row['error'] = type(exc).__name__+': '+str(exc)
                save()
                raise
        row['exit'] = code
        if code != 0:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                row['failed_group_cleanup'] = 'sent SIGKILL'
            except ProcessLookupError:
                row['failed_group_cleanup'] = 'group already exited'
        save()
        require(code == 0, label+' failed; retained stdout/stderr')
        return (out/(label+'.stdout')).read_bytes()
    try:
        stage_a.mkdir()
        for item in manifest['a']:
            target = stage_a/item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source_a/item['path']).read_bytes())
            target.chmod(item['mode'])
        check_stage()
        receipt['a_source_root'] = str(source_a)
        receipt['a_compile_root'] = str(stage_a)
        receipt['staged_a_files'] = len(manifest['a'])
        a = stage_a
        run('compiler-version', [cc, '--version'])
        v = a/'firmware/third_party/usb'
        vendor = v/'vendor/tinyusb/src'
        patches = v/'patches'
        for mode in ('strict', 'sanitize'):
            m = out/mode
            m.mkdir()
            for folder in ('stage1', 'patched', 'generator-input/build'):
                (m/folder).mkdir(parents=True)
            def job(label, argv):
                return run(mode+'-'+label, argv, m)
            job('patch-setup', [sys.executable, '-I', '-B', patches/'apply_setup_patch.py',
                               vendor/'device/usbd.c', m/'stage1/usbd.c'])
            job('patch-reset', [sys.executable, '-I', '-B', patches/'apply_f103_reset_patch.py',
                               vendor/'portable/st/stm32_fsdev/dcd_stm32_fsdev.c', m/'stage1/dcd_stm32_fsdev.c'])
            for kind, name in [('core', 'usbd.c'), ('fsdev', 'dcd_stm32_fsdev.c')]:
                job('patch-'+kind, [sys.executable, '-I', '-B', patches/'apply_usb_bridge_patch.py',
                                    kind, m/'stage1'/name, m/'patched'/name])
            shutil.copyfile(m/'patched/dcd_stm32_fsdev.c', m/'generator-input/build/dcd_stm32_fsdev.c')
            spec = importlib.util.spec_from_file_location('instrument_fake', a/'tests/usb-bridge/supporting/instrument_fake.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.generate(a/'tests/usb-cdc/supporting', m, m/'generator-input')
            incs = ['-DSTM32F103xB', '-DCFG_MT_USB_BRIDGE=1', '-I'+str(a/'firmware/boot'),
                    '-I'+str(HERE), '-I'+str(a/'tests/usb-cdc/supporting')]
            incs += ['-I'+str(a/'firmware'/d) for d in ('app', 'platform', 'transport', 'protocol', 'acquisition')]
            incs += ['-isystem', str(vendor)]
            flags = ['-g', '-O1', '-fno-lto', '-Wall', '-Wextra', '-fno-omit-frame-pointer']
            if mode == 'sanitize':
                flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
            macros = job('effective-config', [cc, '-std=c11', *incs, '-dM', '-E', '-include', 'tusb.h', '-x', 'c', '/dev/null'])
            for name, val in {'CFG_TUD_TASK_EVENTS_PER_RUN': '1', 'CFG_TUD_CDC_TX_BUFSIZE': '64',
                              'CFG_TUD_CDC_RX_BUFSIZE': '64', 'CFG_MT_USB_BRIDGE': '1', 'CFG_TUD_CDC': '1'}.items():
                require(('#define '+name+' '+val+'\n').encode() in macros, 'configuration '+name)
            upstream = [vendor/'common/tusb_fifo.c', vendor/'tusb.c', m/'patched/usbd.c', vendor/'class/cdc/cdc_device.c']
            sources = upstream+[a/('firmware/'+name+'.c') for name in PROJECT]+[HERE/'runtime_bridge.c', m/'fake_dcd_instrumented.c']
            objects = []
            for i, source in enumerate(sources):
                target = m/(str(i)+'-'+source.stem+'.o')
                objects.append(target)
                options = ['-std=gnu11'] if source in upstream else ['-std=c11', '-pedantic-errors', '-Werror', '-Wconversion', '-Wshadow']
                job('compile-'+str(i), [cc, *flags, *options, *incs, '-c', source, '-o', target])
            job('link-runtime', [cc, *flags, *objects, '-o', m/'runtime-bridge'])
            job('compile-window', [cc, *flags, '-std=c11', '-Wpedantic', '-Wconversion', '-Wsign-conversion',
                                  '-Werror', '-I'+str(a), HERE/'window_bridge.c',
                                  *[a/('firmware/'+name+'.c') for name in HELPER], '-o', m/'window-bridge'])
            job('boundary', [sys.executable, '-I', '-B', HERE/'boundary_probe.py', '--host-root', host,
                             '--helper', m/'window-bridge', '--runtime-bridge', m/'runtime-bridge', '--out', m/'boundary'])
            job('roundtrip', [sys.executable, '-I', '-B', HERE/'roundtrip_probe.py', '--host-root', host,
                              '--bridge', m/'window-bridge', '--out', m/'roundtrip'])
        parity = {}
        for kind, expected in [('boundary', 39), ('roundtrip', 107)]:
            left = out/'strict'/kind
            right = out/'sanitize'/kind
            def artifacts(root):
                return {str(p.relative_to(root)): sha(p) for p in root.rglob('*') if p.is_file()
                        and p.name not in ('INPUTS_BEFORE.json', 'INPUTS_AFTER.json')}
            x, y = artifacts(left), artifacts(right)
            require(x == y, kind+' strict/sanitizer artifact mismatch')
            require(len(x) == expected, kind+' unexpected artifact count')
            parity[kind] = x
        receipt['parity'] = parity
        receipt['status'] = 'PASS'
    except BaseException as exc:
        receipt.update(status='FAILED', error=type(exc).__name__+': '+str(exc))
        raise
    finally:
        try:
            if stage_a.exists():
                check_stage()
            after = check_inputs(manifest, roots)
            after['runner'] = sha(Path(__file__))
            after['manifest'] = sha(HERE/'inputs.json')
            receipt['inputs_after'] = after
            receipt['inputs_unchanged'] = before == after
            if before != after:
                receipt['status'] = 'FAILED_INPUT_DRIFT'
        except BaseException as exc:
            receipt.update(status='FAILED_INPUT_DRIFT', input_check_error=str(exc))
        save()
    require(receipt['status'] == 'PASS', receipt['status'])
    print(json.dumps({'status': receipt['status'], 'receipt': str(out/'receipt.json'),
                      'boundary_cases_per_mode': 6, 'roundtrip_windows_per_mode': 20}))

if __name__ == '__main__':
    main()

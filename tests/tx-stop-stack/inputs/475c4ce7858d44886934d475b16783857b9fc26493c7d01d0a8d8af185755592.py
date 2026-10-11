#!/usr/bin/env python3
"""Compile real components and link -r only; never produce a flashable firmware."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

NAMES = ('base', 'adc', 'bridge', 'epoch', 'runtime', 'irq', 'dependencies', 'config', 'toolchain')
PARTS = ('app', 'platform', 'protocol', 'transport', 'acquisition')
OVERLAYS = {
    'adc': ('app/mt_adc_control', 'platform/adc_timer_dma'),
    'bridge': ('transport/mt_usb_bridge', 'transport/mt_usb_cdc'),
    'epoch': ('transport/mt_usb_epoch',), 'runtime': ('app/mt_runtime',),
    'irq': ('platform/mt_irq_guard',),
}
PROJECT = ('acquisition/acquisition', 'app/mt_capture_link', 'app/mt_adc_control',
    'app/mt_control', 'app/mt_control_rx', 'app/mt_runtime', 'platform/adc_timer_dma',
    'platform/mt_timebase', 'platform/mt_irq_guard', 'protocol/mt_wire',
    'protocol/mt_stream', 'protocol/mt_session', 'protocol/mt_command',
    'transport/mt_tx', 'transport/mt_usb_epoch', 'transport/mt_usb_cdc', 'transport/mt_usb_bridge')
VENDOR = ('tusb.c', 'common/tusb_fifo.c', 'class/cdc/cdc_device.c',
          'device/usbd.c', 'portable/st/stm32_fsdev/dcd_stm32_fsdev.c',
          'portable/st/stm32_fsdev/fsdev_common.c')
EXPECTED_EXTERNALS = {'memcmp', 'memcpy', 'memmove', 'memset',
    'tud_descriptor_configuration_cb', 'tud_descriptor_device_cb',
    'tud_descriptor_string_cb', 'tusb_time_millis_api'}
SANITIZE = ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'OBJC_INCLUDE_PATH',
    'GCC_EXEC_PREFIX', 'COMPILER_PATH', 'LIBRARY_PATH', 'DEPENDENCIES_OUTPUT',
    'SUNPRO_DEPENDENCIES', 'LD_PRELOAD', 'LD_LIBRARY_PATH')

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in NAMES:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--descriptors', type=Path, help='optional USB006 source overlay')
    parser.add_argument('--memory', type=Path, help='optional MEM001 source overlay')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--check-paths-only', action='store_true')
    a = parser.parse_args()
    roots = {n: getattr(a, n).resolve(strict=True) for n in NAMES}
    overlays = dict(OVERLAYS)
    project = PROJECT
    expected_externals = set(EXPECTED_EXTERNALS)
    if a.descriptors is not None:
        roots['descriptors'] = a.descriptors.resolve(strict=True)
        overlays['descriptors'] = ('transport/mt_usb_descriptors',)
        project += ('transport/mt_usb_descriptors',)
        expected_externals -= {'tud_descriptor_configuration_cb',
            'tud_descriptor_device_cb', 'tud_descriptor_string_cb'}
    if a.memory is not None:
        roots['memory'] = a.memory.resolve(strict=True)
        project += ('platform/mt_memory',)
        expected_externals -= {'memcmp', 'memcpy', 'memmove', 'memset'}
    raw = a.out.absolute()
    if raw.exists() or raw.is_symlink():
        parser.error('output already exists')
    out = raw.resolve()
    protected = list(roots.values()) + [Path(__file__).resolve().parent]
    inputs = {}
    for path in [out, *roots.values(), Path(__file__).resolve()]:
        if any(c in str(path) for c in ('\n', '\r', ':', '#', '$', '\\', chr(34), chr(39))):
            parser.error('unsupported Make-dependency path character: ' + str(path))
    def tree(source, prefix, suffixes=None):
        if not source.is_dir():
            parser.error('missing input directory: ' + str(source))
        for directory, dirs, files in os.walk(source, followlinks=False):
            if any((Path(directory) / d).is_symlink() for d in dirs):
                parser.error('nested directory symlinks unsupported')
            for name in files:
                path = Path(directory) / name
                if suffixes is None or path.suffix in suffixes:
                    inputs[prefix + str(path.relative_to(source))] = path.resolve(strict=True)
    for part in PARTS:
        tree(roots['base'] / 'firmware' / part, 'firmware/' + part + '/', ('.c', '.h'))
    for name, units in overlays.items():
        for unit in units:
            for suffix in ('.c', '.h'):
                rel = 'firmware/' + unit + suffix
                inputs[rel] = (roots[name] / rel).resolve(strict=True)
    if a.memory is not None:
        rel = 'firmware/platform/mt_memory.c'
        inputs[rel] = (roots['memory'] / rel).resolve(strict=True)
    tree(roots['dependencies'] / 'vendor', 'vendor/')
    inputs['config/tusb_config.h'] = (roots['config'] / 'tusb_config.h').resolve(strict=True)
    inputs['vendor/tinyusb/src/device/usbd.c'] = (roots['bridge'] / 'build/usbd.c').resolve(strict=True)
    inputs['vendor/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c'] = (roots['bridge'] / 'build/dcd_stm32_fsdev.c').resolve(strict=True)
    inputs['tools/check_firmware_closure.py'] = Path(__file__).resolve()
    for unit in project:
        if 'firmware/' + unit + '.c' not in inputs:
            parser.error('missing source: ' + unit)
    for unit in VENDOR:
        if 'vendor/tinyusb/src/' + unit not in inputs:
            parser.error('missing vendor source: ' + unit)
    tc = roots['toolchain']
    tools = {n: (tc / 'bin' / ('arm-none-eabi-' + n)).resolve(strict=True)
             for n in ('gcc', 'ld', 'nm', 'size', 'readelf', 'objdump', 'as')}
    tools['cc1'] = (tc / 'lib/gcc/arm-none-eabi/14.2.1/cc1').resolve(strict=True)
    tools['selected-as'] = (tc / 'lib/arm-none-eabi/bin/as').resolve(strict=True)
    extra = list(tools.values())
    for directory in (tc / 'include/newlib', tc / 'lib/gcc/arm-none-eabi/14.2.1/include'):
        if not directory.is_dir():
            parser.error('missing compiler headers')
        extra.extend(p.resolve(strict=True) for p in directory.rglob('*') if p.is_file())
    all_inputs = set(inputs.values()) | set(extra)
    for p in all_inputs:
        if not p.is_file():
            parser.error('input is not a file: ' + str(p))
        protected.append(p.parent)
    for p in protected:
        if out == p or out in p.parents or p in out.parents:
            parser.error('output overlaps input: ' + str(p))
    if a.check_paths_only:
        print('PASS: read-only path preflight'); return 0
    before = {str(p): sha(p) for p in sorted(all_inputs)}
    out.mkdir(parents=True, exist_ok=False)
    stage = out / 'snapshot'
    env = {k: v for k, v in os.environ.items() if k not in SANITIZE}
    env.update(LC_ALL='C', PATH=str(tc / 'bin') + ':/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1')
    record = {'start_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'invocation': sys.argv, 'inputs_before': before,
        'input_map': {k: str(v) for k, v in inputs.items()}, 'commands': [],
        'descriptor_overlay': a.descriptors is not None,
        'memory_overlay': a.memory is not None,
        'status': 'RUNNING', 'scope': 'Real component relocatable link only; no stubs, startup, final ELF, hardware execution or total stack/resource claim',
        'sanitized_environment_keys': list(SANITIZE)}
    def run(label, command):
        command = list(map(str, command))
        result = subprocess.run(command, cwd=out, env=env, capture_output=True, text=True, input='')
        (out / (label + '.stdout')).write_text(result.stdout)
        (out / (label + '.stderr')).write_text(result.stderr)
        record['commands'].append({'label': label, 'argv': command, 'exit': result.returncode})
        with (out / 'commands.log').open('a') as log:
            log.write('+ ' + shlex.join(command) + '\n' + result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(label + ' failed: ' + str(result.returncode))
        return result.stdout
    status = 0
    try:
        for rel, source in inputs.items():
            target = stage / rel; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            assert sha(target) == before[str(source)]
        run('compiler-version', [tools['gcc'], '--version'])
        for name, expected in [('cc1', tools['cc1']), ('as', tools['selected-as'])]:
            actual = Path(run('selected-' + name, [tools['gcc'], '-print-prog-name=' + name]).strip()).resolve()
            if actual != expected or sha(actual) != before[str(expected)]:
                raise RuntimeError('compiler selected an unbound program: ' + str(actual))
        flags = ['-std=c11', '-Os', '-mcpu=cortex-m3', '-mthumb', '-mfloat-abi=soft',
            '-ffreestanding', '-fno-common', '-fno-short-enums', '-ffunction-sections',
            '-fdata-sections', '-fstack-usage', '-Wall', '-Wextra', '-Wpedantic',
            '-Wconversion', '-Wsign-conversion', '-DSTM32F103xB', '-DCFG_MT_USB_BRIDGE=1',
            '-isystem', str(tc / 'include/newlib'), '-I' + str(stage / 'config')]
        flags += ['-I' + str(stage / 'firmware' / p) for p in PARTS]
        for p in ('tinyusb/src', 'cmsis-device-f1/Include', 'cmsis-core/CMSIS/Core/Include', 'tinyusb/src/portable/st/stm32_fsdev'):
            flags += ['-isystem', str(stage / 'vendor' / p)]
        objects = []
        dependencies = {}
        jobs = [('project', 'firmware/' + p + '.c') for p in project]
        jobs += [('vendor', 'vendor/tinyusb/src/' + p) for p in VENDOR]
        for index, (kind, rel) in enumerate(jobs):
            name = str(index) + '-' + Path(rel).stem
            obj = out / (name + '.o')
            strict = ['-Werror', '-pedantic-errors'] if kind == 'project' else ['-std=gnu11', '-Werror']
            if kind == 'vendor' and Path(rel).name in ('dcd_stm32_fsdev.c', 'fsdev_common.c'):
                strict += ['-Wno-error=conversion']
            if rel == 'firmware/platform/mt_memory.c':
                strict += ['-fno-builtin', '-fno-tree-loop-distribute-patterns']
            run('compile-' + name, [tools['gcc'], *flags, *strict, '-MD', '-MF', out / (name + '.d'), '-c', stage / rel, '-o', obj])
            words = shlex.split((out / (name + '.d')).read_text().replace('\\\n', ' '))
            for word in words[1:]:
                header = Path(word).resolve(strict=True)
                digest = sha(header)
                if header.is_relative_to(stage):
                    rel_header = str(header.relative_to(stage))
                    if rel_header not in inputs or digest != before[str(inputs[rel_header])]:
                        raise RuntimeError('unbound snapshot dependency: ' + str(header))
                elif str(header) not in before or digest != before[str(header)]:
                    raise RuntimeError('unbound compiler dependency: ' + str(header))
                dependencies[str(header)] = digest
            objects.append(obj)
        combined = out / 'components.o'
        run('link-relocatable', [tools['ld'], '-r', '-Map=' + str(out / 'components.map'), '-o', combined, *objects])
        record['undefined_symbols'] = run('undefined', [tools['nm'], '-u', combined]).splitlines()
        observed = {line.split()[-1] for line in record['undefined_symbols'] if line.strip()}
        record['expected_external_symbols'] = sorted(expected_externals)
        if observed != expected_externals:
            raise RuntimeError('external symbol boundary changed: ' + repr(sorted(observed)))
        run('totals', [tools['size'], combined])
        run('symbols', [tools['nm'], '-S', '--size-sort', combined])
        run('sections', [tools['size'], '-A', combined])
        run('elf', [tools['readelf'], '-h', '-A', '-S', combined])
        run('relocations', [tools['objdump'], '-r', combined])
        record['compiled_dependencies_sha256'] = dependencies
        record['objects_sha256'] = {p.name: sha(p) for p in objects + [combined]}
        record['status'] = 'PASS_RELOCATABLE_ONLY'
    except Exception as exc:
        record['status'] = 'FAIL'; record['error'] = str(exc); status = 1
    finally:
        after = {str(p): sha(p) for p in sorted(all_inputs)}
        record['inputs_after'] = after; record['inputs_unchanged'] = before == after
        if before != after:
            record['status'] = 'FAIL_INPUT_CHANGED'; status = 1
        record['finish_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (out / 'receipt.json').write_text(json.dumps(record, indent=2) + '\n')
    print(record['status'] + ': ' + str(out / 'receipt.json'))
    return status

if __name__ == '__main__':
    raise SystemExit(main())

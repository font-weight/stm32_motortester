#!/usr/bin/env python3
"""Publication-only orchestration of unchanged supporting/critic test sources.

Requires Python 3, a C compiler, and POSIX sh. --arm additionally requires the
documented arm-none-eabi tools and newlib headers, selected with TC or --tc.
No tools are installed or downloaded. All generated files live under --out.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='New output directory outside the checkout')
    parser.add_argument('--arm', action='store_true')
    parser.add_argument('--tc', type=Path, default=os.environ.get('TC'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    tests = root / 'tests/usb-cdc'
    deps = root / 'firmware/third_party/usb'
    out = args.out.resolve()
    assert root != out and root not in out.parents, 'Output must be outside the checkout'
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    records = []

    def run(argv, name, cwd=root, expect=0):
        argv = [str(a) for a in argv]
        result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        text = '$ ' + shlex.join(argv) + '\n' + result.stdout + '\nexit_code=' + str(result.returncode) + '\n'
        (out / (name + '.log')).write_text(text)
        print(text, end='', flush=True)
        records.append({'name': name, 'argv': argv, 'exit_code': result.returncode,
                        'expected_exit_code': expect, 'log': name + '.log'})
        (out / 'commands.json').write_text(json.dumps(records, indent=2) + '\n')
        assert result.returncode == expect, name + ': unexpected exit status'
        return result.stdout

    run([sys.executable, tests / 'verify-package.py'], 'verify-package')
    supporting = out / 'supporting'
    shutil.copytree(tests / 'supporting', supporting)
    env['USB_DEPENDENCIES'] = str(deps)
    env['USB_FIRMWARE_ROOT'] = str(root)
    for kind, flags in [('buffered', []), ('hwfifo', ['--hwfifo'])]:
        for target, target_flags in [('core', []), ('adapter', ['--adapter'])]:
            run([sys.executable, supporting / 'build.py', *target_flags, *flags], 'supporting-' + kind + '-' + target)
            run([sys.executable, supporting / 'build.py', *target_flags, '--sanitize', *flags], 'supporting-' + kind + '-' + target + '-sanitized')

    critic = out / 'critic'
    shutil.copytree(tests / 'critic', critic)
    snapshot = critic / 'snapshot/firmware/transport'
    snapshot.mkdir(parents=True)
    for name in ['mt_usb_cdc.c', 'mt_usb_cdc.h']:
        shutil.copyfile(root / 'firmware/transport' / name, snapshot / name)
    for source in (root / 'firmware').glob('*/*.h'):
        target = critic / 'snapshot' / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # Original independent runner expects its snapshot path. Copies are local
    # reproduction inputs, never a second shipped production implementation.
    run(['sh', critic / 'run_call_order.sh'], 'critic-call-order', cwd=critic)

    if args.arm:
        assert args.tc, '--arm requires --tc or TC'
        tc = Path(args.tc).resolve()
        assert (tc / 'bin/arm-none-eabi-gcc').is_file(), 'ARM compiler unavailable'
        env.update(TC=str(tc), USB_DEPS=str(deps), OUT=str(out / 'adapter-arm'),
                   PATH=str(tc / 'bin') + os.pathsep + env.get('PATH', ''))
        run(['sh', tests / 'arm-check.sh'], 'adapter-arm')
        assert hashlib.sha256((out / 'adapter-arm/mt_usb_cdc.o').read_bytes()).hexdigest() == '04f45e1e81bfa5d691a9b5efda529da51de4268e3ead9a0b0ea9df4d245776e3'
        run([sys.executable, critic / 'test_configuration_guards.py'], 'critic-config-guards', cwd=critic)
        units = (deps / 'SOURCES.list').read_text().splitlines()
        config = deps / 'evidence/config'
        common = ['-Os', '-mcpu=cortex-m3', '-mthumb', '-mfloat-abi=soft',
                  '-ffreestanding', '-ffunction-sections', '-fdata-sections',
                  '-fno-common', '-fno-short-enums', '-fstack-usage', '-Wall',
                  '-Wextra', '-Wpedantic', '-Wconversion', '-Wsign-conversion',
                  '-DSTM32F103xB', '-isystem', str(tc / 'include/newlib')]
        for directory in [config, deps / 'vendor/tinyusb/src',
                          deps / 'vendor/cmsis-device-f1/Include',
                          deps / 'vendor/cmsis-core/CMSIS/Core/Include']:
            common.append('-I' + str(directory))
        expected = {row['object']: row['sha256'] for row in json.loads((deps / 'evidence/reproduction.json').read_text())}
        objects = []
        totals = dict(text_rodata=0, data=0, bss=0, warnings=0)
        for unit in units:
            source = deps / unit
            name = source.stem
            obj = out / (name + '.o')
            output = run([tc / 'bin/arm-none-eabi-gcc', '-std=gnu11', *common,
                          '-MMD', '-MF', out / (name + '.d'), '-c', source, '-o', obj], name + '-compile')
            digest = hashlib.sha256(obj.read_bytes()).hexdigest()
            assert digest == expected[obj.name], name + ': changed object checksum (different toolchain must be reviewed separately)'
            size = run([tc / 'bin/arm-none-eabi-size', '-A', obj], name + '-size')
            row = dict(source=unit, object=obj.name, sha256=digest,
                       text_rodata=0, data=0, bss=0, warnings=output.count('warning:'))
            for line in size.splitlines():
                fields = line.split()
                if len(fields) < 2 or not fields[0].startswith('.'):
                    continue
                section = fields[0]
                key = ('text_rodata' if section.startswith(('.text', '.rodata')) else
                       'data' if section.startswith('.data') else 'bss' if section.startswith('.bss') else None)
                if key:
                    row[key] += int(fields[1])
            for key in totals:
                totals[key] += row[key]
            objects.append(row)
            run([tc / 'bin/arm-none-eabi-nm', '-S', '-a', obj], name + '-symbols')
            run([tc / 'bin/arm-none-eabi-nm', '-g', '-P', obj], name + '-global-symbols')
            run([tc / 'bin/arm-none-eabi-nm', '-u', obj], name + '-unresolved')
        assert totals == dict(text_rodata=10796, data=29, bss=637, warnings=7), totals
        negative = run([tc / 'bin/arm-none-eabi-gcc', '-std=c11', *common,
                        '-c', deps / 'vendor/tinyusb/src/portable/st/stm32_fsdev/fsdev_common.c',
                        '-o', out / 'strict-c11-negative.o'], 'dependency-strict-c11-negative', expect=1)
        assert 'asm' in negative and 'error:' in negative
        (out / 'dependency-arm-summary.json').write_text(json.dumps({'objects': objects, 'totals': totals}, indent=2) + '\n')
    (out / 'summary.json').write_text(json.dumps({
        'result': 'PASS', 'native_matrix': 'eight real-core variants plus independent call-order tests',
        'arm_requested': args.arm, 'lsan_claimed': False,
        'scope': 'Synthetic foreground adapter evidence only; no firmware link or physical USB validation',
        'unresolved': ['production reset-before-queue latch', 'overflow containment',
                       'endpoint/event epoch barrier', 'time-health integration', 'target hardware']
    }, indent=2) + '\n')
    print('PASS: requested USB-001 reproduction stages complete. Acceptance remains blocked.')


if __name__ == '__main__':
    main()

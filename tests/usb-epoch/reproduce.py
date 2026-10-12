#!/usr/bin/env python3
"""Rerun unchanged author, supporting, and critic suites outside the checkout.

Requires Python 3.9+, POSIX sh/bash, GCC/gcov and the documented ARM GCC toolchain
layout (usr/bin + usr/include/newlib, sibling package archives/hash receipt).
No installation, downloads, GitHub writes, hardware access or full link.
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
    parser.add_argument('--out', type=Path, required=True, help='A new directory outside the checkout')
    parser.add_argument('--tc', type=Path, required=True, help='Documented ARM toolchain usr directory')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    tests = root/'tests/usb-epoch'
    out = args.out.resolve()
    tc = args.tc.resolve()
    assert root != out and root not in out.parents, 'Output must be outside the checkout'
    assert (tc/'bin/arm-none-eabi-gcc').is_file(), 'ARM compiler unavailable'
    assert (tc.parents[1]/'SHA256SUMS.packages').is_file(), 'Official package provenance receipt required'
    out.mkdir(parents=True, exist_ok=False)
    records = []
    env = dict(os.environ, TC=str(tc), ARM_TOOLCHAIN_ROOT=str(tc))

    def run(argv, name, cwd=root, extra_env=None):
        argv = [str(a) for a in argv]
        command_env = dict(env, **(extra_env or {}))
        p = subprocess.run(argv, cwd=cwd, env=command_env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log = '$ '+shlex.join(argv)+'\n'+p.stdout+'\nexit_code='+str(p.returncode)+'\n'
        (out/(name+'.log')).write_text(log)
        print(log, end='', flush=True)
        records.append(dict(name=name, argv=argv, cwd=str(cwd), exit_code=p.returncode, log=name+'.log'))
        (out/'commands.json').write_text(json.dumps(records, indent=2)+'\n')
        assert p.returncode == 0, name+': unexpected exit status'

    run([sys.executable, tests/'verify-package.py'], 'verify-package')
    run(['sh', tests/'run.sh'], 'author-native', extra_env={'OUT':str(out/'author-native')})
    run(['sh', tests/'arm-check.sh'], 'author-arm', extra_env={'OUT':str(out/'author-arm')})
    os_object = out/'author-arm/mt_usb_epoch.o'
    assert hashlib.sha256(os_object.read_bytes()).hexdigest() == '2b63118f5e871c51c6675a4203f18dbba7f0d16239dbd2715623c1ef90154159', 'Exact documented -Os ARM object differs'

    support = out/'supporting'
    support.mkdir()
    for name in ['tests', 'inputs']:
        shutil.copytree(tests/'supporting'/name, support/name)
    # No historical evidence or shared report path is copied or required.
    run([sys.executable, support/'tests/run.py'], 'supporting-full', cwd=support)
    o2_object = support/'evidence/mt_usb_epoch.arm.o'
    assert hashlib.sha256(o2_object.read_bytes()).hexdigest() == 'e92ba76dbfc3fe56a5ec152275c6188ad8b112b7cced8d5f5d840001e220d4a7', 'Exact documented -O2 ARM object differs'
    critic = out/'critic'
    critic.mkdir()
    for name in ['run.sh', 'test_api_boundaries.c']:
        shutil.copyfile(tests/'critic'/name, critic/name)
    run(['bash', critic/'run.sh'], 'critic-native', cwd=critic, extra_env={'SOURCE_ROOT':str(root), 'BUILD_DIR':str(critic/'build')})
    run(['gcc', '-std=c11', '-Wall', '-Wextra', '-Wpedantic', '-Wconversion', '-Wsign-conversion', '-Werror', '-fanalyzer', '-c', root/'firmware/transport/mt_usb_epoch.c', '-o', out/'analyzed.o'], 'static-analyzer')
    summary = dict(result='PASS', scope='Serialized metadata software only',
                   author_os_object_sha256=hashlib.sha256(os_object.read_bytes()).hexdigest(),
                   supporting_o2_object_sha256=hashlib.sha256(o2_object.read_bytes()).hexdigest(),
                   limitations=['LeakSanitizer disabled', 'No TinyUSB/IRQ bridge', 'No full firmware link or ARM execution', 'No physical USB/ADC/latency validation', 'Existing TinyUSB counterexamples remain negative evidence'])
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()

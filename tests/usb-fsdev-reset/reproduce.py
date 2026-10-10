#!/usr/bin/env python3
"""Reproduce USB-004 patch, native model, reviewer checks and complete ARM objects.

Run from an immutable full checkout. All generated files go to a fresh external
output directory. This wrapper copies or invokes reviewed tools unchanged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--toolchain', type=Path, required=True,
                        help='existing root containing bin/arm-none-eabi-gcc and include/newlib')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    root = here.parents[1]
    tc = args.toolchain.resolve()
    out = args.output.resolve()
    # Validate before creating anything; resolve symlink aliases as well.
    for protected in [root, tc]:
        if out == protected or protected in out.parents:
            parser.error('output must be outside checkout and toolchain trees')
    if out.exists() or args.output.is_symlink():
        parser.error('output must not exist')
    if not (tc / 'bin/arm-none-eabi-gcc').is_file():
        parser.error('ARM compiler missing from toolchain/bin')
    dep = root / 'firmware/third_party/usb'
    patches = dep / 'patches'
    supporting = here / 'supporting'
    critic = here / 'critic-a-local'
    upstream = dep / 'vendor/tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c'
    manifest_path = here / 'package-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    protected_files = [manifest_path]
    for row in manifest['files']:
        path = root / row['path']
        if path.resolve() != path or not path.is_file():
            parser.error('unexpected input type or symlink: ' + str(path))
        if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            parser.error('package hash mismatch: ' + row['path'])
        protected_files.append(path)
    protected_files += [p for p in (dep / 'vendor').rglob('*') if p.is_file()]
    protected_files += [dep / 'evidence/config/tusb_config.h', dep / 'SHA256SUMS.sources']
    before = {str(p): digest(p) for p in sorted(set(protected_files))}
    out.mkdir(parents=True, exist_ok=False)
    (out / 'inputs-before.json').write_text(json.dumps(before, indent=2) + '\n')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    records = []

    def run(label, argv, cwd=None):
        result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                                capture_output=True, text=True)
        record = {'label': label, 'argv': [str(x) for x in argv],
                  'cwd': str(cwd) if cwd else None, 'exit_code': result.returncode,
                  'stdout': result.stdout, 'stderr': result.stderr}
        records.append(record)
        (out / 'commands.json').write_text(json.dumps(records, indent=2) + '\n')
        (out / (label + '.log')).write_text(result.stdout + result.stderr)
        if result.returncode != 0:
            raise RuntimeError(label + ' failed; see its preserved log')
        print(label + ': PASS', flush=True)

    try:
        run('dependency-hashes', ['sha256sum', '-c', 'SHA256SUMS.sources'], dep)
        # Complete original/patched DCD translation units, relocatable objects only.
        run('arm', [sys.executable, here / 'arm_check.py', '--dependencies', dep,
                    '--toolchain', tc, '--output', out / 'arm'])
        candidate = out / 'arm/dcd_stm32_fsdev.c'
        run('supporting-tooling', [sys.executable, supporting / 'tools/test_runner.py'])
        run('model', [sys.executable, supporting / 'tools/run.py', '--candidate', candidate,
                      '--out', out / 'model'])
        run('legal-ep0', [sys.executable, critic / 'run_legal_ep0_buffers.py',
            '--supporting', supporting, '--model-build', out / 'model', '--out', out / 'legal-ep0'])
        run('scope-preprocessor', [sys.executable, critic / 'test_scope_preprocessor.py',
            '--upstream', upstream, '--patched', candidate, '--out', out / 'preprocessor.json'])
        run('all-ctr', [sys.executable, critic / 'run_all_ctr_patterns.py',
            '--supporting', supporting, '--candidate', candidate, '--out', out / 'all-ctr'])
        # The reviewer's patch-tool script is path-relative. Copy it and fixtures
        # byte-for-byte into a fresh output directory, without editing its logic.
        patch_test = out / 'patch-tool'
        (patch_test / 'inputs').mkdir(parents=True)
        shutil.copyfile(critic / 'patch-tool/test_patch_tool.py', patch_test / 'test_patch_tool.py')
        for name in ['apply_f103_reset_patch.py', 'f103-reset-cleanup.json',
                     '0002-f103-reset-completion-cleanup.patch']:
            shutil.copyfile(patches / name, patch_test / 'inputs' / name)
        shutil.copyfile(upstream, patch_test / 'inputs/upstream-usbd.c')
        run('patch-tool', [sys.executable, patch_test / 'test_patch_tool.py'])
        model = json.loads((out / 'model/summary.json').read_text())
        assert model['runs'] == 23 and model['mutation_rejections'] == 11 and model['inputs_unchanged']
        arm = json.loads((out / 'arm/summary.json').read_text())
        assert arm['object_sha256'] == {
            'upstream': '21065ac3d4295f783d35b08f2fd7389702ce68111c592a9ebb663378f9441f02',
            'patched': 'c5f5f547e9cb1a46703b69fee6aac91875733639a39bbdb44773a8d0380453b3'}
        result = {'result': 'PASS scoped reproduction', 'model_runs': model['runs'],
                  'model_mutation_rejections': model['mutation_rejections'],
                  'object_sha256': arm['object_sha256'],
                  'candidate_sha256': digest(candidate),
                  'all_ctr': json.loads((out / 'all-ctr/receipt.json').read_text()),
                  'legal_ep0': json.loads((out / 'legal-ep0/receipt.json').read_text()),
                  'patch_tool_invocations': json.loads((patch_test / 'patch-tool-results.json').read_text())['invocations'],
                  'scope': 'extracted-function software model and complete ARM compile only; no whole-DCD runtime, full link, IRQ timing, board or USB test'}
    finally:
        after = {path: digest(Path(path)) for path in before}
        (out / 'inputs-after.json').write_text(json.dumps(after, indent=2) + '\n')
        if before != after:
            raise RuntimeError('protected input changed during reproduction')
    result['inputs_unchanged'] = True
    (out / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

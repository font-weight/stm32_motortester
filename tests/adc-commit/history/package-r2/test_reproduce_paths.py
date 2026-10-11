#!/usr/bin/env python3
"""Disposable regression fixtures for outer-wrapper alias and environment isolation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument('--checkout', type=Path, required=True)
p.add_argument('--toolchain', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
repo, tc, out = a.checkout.resolve(), a.toolchain.resolve(), a.out.resolve()
for root in (repo, tc, Path(__file__).resolve().parent):
    if out == root or out.is_relative_to(root) or root.is_relative_to(out):
        p.error('fresh disjoint fixture root required')
if a.out.exists() or a.out.is_symlink():
    p.error('fixture root must not exist')
wrapper = repo / 'tests/adc-commit/reproduce.py'
manifest = json.loads((repo / 'tests/adc-commit/parent-inputs.json').read_text())
def hashes(root):
    return {str(f): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in root.rglob('*') if f.is_file() and '.git' not in f.parts}
before = hashes(repo)
out.mkdir(parents=True, exist_ok=False)
fixture = out / 'valid-layout'
real, proxy = fixture / 'real-tools', fixture / 'proxy'
real.mkdir(parents=True); (proxy / 'bin').mkdir(parents=True)
for name in ('gcc', 'size', 'nm', 'objdump'):
    tool = 'arm-none-eabi-' + name
    shutil.copy2(tc / 'bin' / tool, real / tool)
    (proxy / 'bin' / tool).symlink_to(real / tool)
# Match Critic-A's valid-compiler fixture, including the relative GCC library layout.
for root in (fixture, proxy):
    for name in ('lib', 'include'):
        (root / name).symlink_to(tc / name, target_is_directory=True)
(out / 'checkout-alias').symlink_to(repo, target_is_directory=True)
script_alias = out / 'script-alias'
script_alias.mkdir(); (script_alias / 'reproduce.py').symlink_to(wrapper)
header_proxy, header_real = out / 'header-proxy', out / 'header-backing'
(header_proxy / 'include').mkdir(parents=True); header_real.mkdir()
(header_proxy / 'bin').symlink_to(proxy / 'bin', target_is_directory=True)
(header_proxy / 'lib').symlink_to(tc / 'lib', target_is_directory=True)
(header_proxy / 'include/newlib').symlink_to(header_real, target_is_directory=True)
shutil.copyfile(tc / 'include/newlib/stdint.h', header_real / 'stdint.h')
file_proxy, file_real = out / 'header-file-proxy', out / 'header-file-backing'
(file_proxy / 'include/newlib').mkdir(parents=True); file_real.mkdir()
(file_proxy / 'bin').symlink_to(proxy / 'bin', target_is_directory=True)
(file_proxy / 'lib').symlink_to(tc / 'lib', target_is_directory=True)
shutil.copyfile(tc / 'include/newlib/stdint.h', file_real / 'stdint.h')
(file_proxy / 'include/newlib/stdint.h').symlink_to(file_real / 'stdint.h')
# A minimal relocated checkout contains exactly the package and its 20 inputs.
relocated = out / 'relocated checkout'
shutil.copytree(repo / 'tests/adc-commit', relocated / 'tests/adc-commit')
for rel in set(manifest['production_sha256']) | {r['path'] for r in manifest['inputs']}:
    target = relocated / rel; target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(repo / rel, target)
alias_copy = out / 'aliased-input-checkout'
shutil.copytree(relocated, alias_copy)
input_backing = out / 'input-backing'; input_backing.mkdir()
relative = 'firmware/platform/adc_timer_dma.h'
original = alias_copy / relative
original.rename(input_backing / 'adc_timer_dma.h')
original.symlink_to(input_backing / 'adc_timer_dma.h')
clean = {'PATH': '/usr/bin:/bin', 'CC': '/usr/bin/cc', 'LANG': 'C', 'LC_ALL': 'C',
         'PYTHONDONTWRITEBYTECODE': '1'}
existing_output = out / 'existing-output'
existing_output.mkdir()
cases = [
    ('existing', wrapper, proxy, existing_output),
    ('inside-checkout', wrapper, proxy, repo / 'must-not-exist/new'),
    ('checkout-ancestor', wrapper, proxy, repo.parent),
    ('checkout-alias', wrapper, proxy, out / 'checkout-alias/must-not-exist/new'),
    ('advertised-tools', wrapper, proxy, proxy / 'must-not-exist/new'),
    ('valid-tool-backing-parent', wrapper, proxy, real / 'must-not-exist/new'),
    ('header-directory-alias', wrapper, header_proxy, header_real / 'must-not-exist/new'),
    ('header-file-alias', wrapper, file_proxy, file_real / 'must-not-exist/new'),
    ('script-invocation-alias', script_alias / 'reproduce.py', proxy,
     script_alias / 'must-not-exist/new'),
    ('source-file-backing-parent', alias_copy / 'tests/adc-commit/reproduce.py', proxy,
     input_backing / 'must-not-exist/new'),
    ('missing-toolchain', wrapper, out / 'missing-toolchain', out / 'never-created'),
]
# The selected native compiler and GCC's separately located cc1 are protected too.
native_real, native_alias = out / 'native-backing', out / 'native-alias'
native_real.mkdir(); native_alias.mkdir()
shutil.copy2('/usr/bin/cc', native_real / 'cc')
(native_alias / 'cc').symlink_to(native_real / 'cc')
cases.append(('native-compiler-alias', wrapper, proxy, native_real / 'must-not-exist/new'))
internal, internal_real = out / 'internal-layout', out / 'internal-backing'
(internal / 'bin').mkdir(parents=True); internal_real.mkdir()
for name in ('gcc', 'size', 'nm', 'objdump'):
    shutil.copy2(tc / 'bin' / ('arm-none-eabi-' + name), internal / 'bin' / ('arm-none-eabi-' + name))
(internal / 'include').symlink_to(tc / 'include', target_is_directory=True)
probe = subprocess.run([str(tc / 'bin/arm-none-eabi-gcc'), '-print-prog-name=cc1'],
                       env=clean, text=True, capture_output=True, check=True)
cc1 = Path(probe.stdout.strip()).resolve()
shutil.copy2(cc1, internal_real / 'cc1')
link = internal / cc1.relative_to(tc)
link.parent.mkdir(parents=True, exist_ok=True); link.symlink_to(internal_real / 'cc1')
cases.append(('compiler-support-alias', wrapper, internal, internal_real / 'must-not-exist/new'))
records = []
for name, script, toolchain, destination in cases:
    existed = destination.exists()
    parent_existed = destination.parent.exists()
    argv = [sys.executable, str(script), '--toolchain', str(toolchain), '--out', str(destination)]
    case_env = dict(clean, CC=str(native_alias / 'cc')) if name == 'native-compiler-alias' else clean
    result = subprocess.run(argv, env=case_env, text=True, capture_output=True)
    record = dict(case=name, argv=argv, environment=case_env, exit=result.returncode, stdout=result.stdout,
                  stderr=result.stderr, output_created=not existed and destination.exists(),
                  parent_created=not parent_existed and destination.parent.exists())
    records.append(record)
    if result.returncode != 2 or record['output_created'] or record['parent_created']:
        raise RuntimeError('refusal regression failed: ' + json.dumps(record))
# Poisoned compiler/Python environment must not reach any inner historical runner.
poison = out / 'poison'; poison.mkdir()
(poison / 'assert.h').write_text('#error inherited compiler include path leaked\n')
(poison / 'cc1').write_text('#!/bin/sh\nexit 91\n'); (poison / 'cc1').chmod(0o755)
dirty = dict(clean, CPATH=str(poison), C_INCLUDE_PATH=str(poison),
             CPLUS_INCLUDE_PATH=str(poison), COMPILER_PATH=str(poison),
             GCC_EXEC_PREFIX=str(poison) + '/', LIBRARY_PATH=str(poison),
             CFLAGS='-DNDEBUG', CPPFLAGS='-DNDEBUG', LDFLAGS='invalid',
             PYTHONOPTIMIZE='2', PYTHONPATH=str(poison), TMPDIR=str(poison))
positive = out / 'positive relocated run'
argv = [sys.executable, str(relocated / 'tests/adc-commit/reproduce.py'),
        '--toolchain', str(proxy), '--out', str(positive)]
result = subprocess.run(argv, env=dirty, text=True, capture_output=True)
(out / 'positive.log').write_text(result.stdout + result.stderr)
receipt = json.loads((positive / 'receipt.json').read_text()) if (positive / 'receipt.json').is_file() else {}
if result.returncode or receipt.get('status') != 'PASS' or not receipt['inputs_unchanged']:
    raise RuntimeError('positive relocated/environment run failed: ' + result.stderr)
allowed = {'PATH', 'CC', 'LANG', 'LC_ALL', 'PYTHONDONTWRITEBYTECODE', 'TMPDIR'}
if set(receipt['child_environment']) != allowed or receipt['python_flags'] != ['-I', '-B']:
    raise RuntimeError('unexpected child environment or Python flags')
if before != hashes(repo):
    raise RuntimeError('original checkout changed')
summary = dict(status='PASS', test_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), negative_cases=records, positive=dict(argv=argv,
    exit=result.returncode, receipt=str(positive / 'receipt.json'),
    receipt_sha256=hashlib.sha256((positive / 'receipt.json').read_bytes()).hexdigest(),
    dirty_environment=dirty, actual_child_environment=receipt['child_environment']),
    original_checkout_unchanged=True, scope='Disposable fixture copies; no production edits')
(out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print('PASS: 13 refusal cases and full valid-toolchain relocated run under polluted environment.')

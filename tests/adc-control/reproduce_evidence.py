#!/usr/bin/env python3
"""Reproduce preserved synthetic evidence without duplicating base sources."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EVIDENCE = HERE / 'evidence'
OUT = Path(os.environ.get('OUT', tempfile.mkdtemp(prefix='io001-evidence-'))).resolve()
OUT.mkdir(parents=True, exist_ok=True)
ENV = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',
           UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')

def run(command, cwd=ROOT, expected=0):
    print('+', ' '.join(map(str, command)), flush=True)
    result = subprocess.run(list(map(str, command)), cwd=cwd, env=ENV)
    if result.returncode != expected:
        raise SystemExit(f'Unexpected exit {result.returncode}; expected {expected}')

# The independent harness uses a historical snapshot layout. Reconstruct it in
# OUT from hash-verified repository files and the few preserved historical deltas.
# Its older author test is manifest evidence only, not an input to this harness.
for variant in ('final', 'evidence-v1'):
    saved = EVIDENCE / 'supporting'
    if variant != 'final':
        saved /= variant
    work = OUT / variant
    work.mkdir(exist_ok=False)
    for name in ('test_independent.c', 'run.sh', 'snapshot.sha256'):
        shutil.copyfile(saved / name, work / name)
    for line in (saved / 'snapshot.sha256').read_text().splitlines():
        digest, path = line.split('  ', 1)
        relative = Path(path).relative_to('snapshot')
        source = saved / 'overrides' / relative
        if not source.exists():
            source = ROOT / relative
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f'Source identity mismatch: {source}')
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    run(['bash', 'run.sh'], work)

critic = EVIDENCE / 'critic-a'
flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-Wconversion', '-pedantic', '-I', ROOT]
adapter = ROOT / 'firmware/app/mt_adc_control.c'
oracle = critic / 'independent_adapter.c'
for name, extra in (('strict', ['-O2']), ('sanitized', ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer'])):
    binary = OUT / ('critic-' + name)
    run(['cc', *flags, *extra, oracle, adapter, '-o', binary])
    run([binary])

# The exact critic reproducer must fail on the preserved pre-fix adapter and
# pass on the final implementation. Exit 1 below is expected failure evidence.
baseline = OUT / 'evidence-v1/snapshot/firmware/app/mt_adc_control.c'
for label, source, expected in (('baseline', baseline, 1), ('final', adapter, 0)):
    binary = OUT / ('correlation-gap-' + label)
    run(['cc', *flags, '-O2', critic / 'correlation_gap_repro.c', source, '-o', binary])
    run([binary], expected=expected)
print('IO-001 supporting, critic and preserved defect reproductions passed.', flush=True)
print('Generated outputs:', OUT, flush=True)

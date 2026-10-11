#!/usr/bin/env python3
"""Local mutant copies verify that the supporting harness catches guarded defects."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

P = argparse.ArgumentParser()
P.add_argument('--base', required=True, type=Path)
P.add_argument('--candidate', required=True, type=Path)
P.add_argument('--out', required=True, type=Path)
A = P.parse_args()
HERE = Path(__file__).resolve().parent
BASE, CANDIDATE, OUT = A.base.resolve(), A.candidate.resolve(), A.out.resolve()
for source in (HERE, BASE, CANDIDATE):
    if OUT == source or OUT.is_relative_to(source) or source.is_relative_to(OUT):
        P.error('output overlaps a source tree')
if OUT.exists():
    P.error('--out must be fresh')
FILES = ['firmware/platform/adc_timer_dma.c', 'firmware/platform/adc_timer_dma.h',
         'firmware/app/mt_adc_control.c', 'firmware/app/mt_adc_control.h']
def hashes():
    return {name: hashlib.sha256((CANDIDATE / name).read_bytes()).hexdigest() for name in FILES}
BEFORE = hashes()
TEXT = (CANDIDATE / FILES[0]).read_text()
MUTANTS = {
    'missing-initial-admission': ('if (!guard->allowed(guard->context)) {', 'if (0) {'),
    'missing-post-clock-admission': ('if (guard && !guard->allowed(guard->context)) {', 'if (0) {'),
    'cleanup-before-leave': ('    if (guard) guard->leave(guard->context, key);',
        '    if (guard && commit_fault == MT_ADC_DMA_FAULT_NONE) guard->leave(guard->context, key);'),
}
for original, replacement in MUTANTS.values():
    if TEXT.count(original) != 1:
        raise SystemExit('mutation target changed; inspect manually')
OUT.mkdir(parents=True, exist_ok=False)
RECORDS = []
for name, (original, replacement) in MUTANTS.items():
    mutant = OUT / name / 'input'
    for relative in FILES:
        target = mutant / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CANDIDATE / relative, target)
    (mutant / FILES[0]).write_text(TEXT.replace(original, replacement))
    command = [sys.executable, str(HERE / 'run.py'), '--base', str(BASE),
               '--candidate', str(mutant), '--out', str(OUT / name / 'run')]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (OUT / name / 'wrapper.log').write_text(result.stdout)
    receipt = json.loads((OUT / name / 'run/receipt.json').read_text())
    failed = receipt['commands'][-1]
    # Compilation and both unchanged regression executables must first pass.
    detected = result.returncode != 0 and failed['argv'] == [str(OUT / name / 'run/admission')]
    assert detected, (name, result.stdout)
    assert receipt['inputs_unchanged']
    RECORDS.append({'name': name, 'command': command, 'exit': result.returncode,
                    'expected_rejection_detected': detected, 'receipt': str(OUT / name / 'run/receipt.json'),
                    'original': original, 'replacement': replacement})
AFTER = hashes()
assert BEFORE == AFTER
(OUT / 'summary.json').write_text(json.dumps({'status': 'PASS', 'production_before': BEFORE,
    'production_after': AFTER, 'negative_controls': RECORDS}, indent=2) + '\n')
print('Three isolated negative controls rejected by the admission harness; production unchanged.')

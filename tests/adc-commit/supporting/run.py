#!/usr/bin/env python3
"""Isolated native ADC-003 runner; never edits candidate/base/test inputs."""
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

HERE = Path(__file__).resolve().parent
P = argparse.ArgumentParser()
P.add_argument('--base', required=True, type=Path)
P.add_argument('--candidate', required=True, type=Path)
P.add_argument('--out', required=True, type=Path)
P.add_argument('--sanitize', action='store_true')
P.add_argument('--legacy-only', action='store_true')
A = P.parse_args()
BASE, CANDIDATE, OUT = A.base.resolve(), A.candidate.resolve(), A.out.resolve()
# Validate all destinations BEFORE creating even their parent directories.
for root in (HERE, BASE, CANDIDATE):
    if OUT == root or OUT.is_relative_to(root) or root.is_relative_to(OUT):
        P.error(f'--out overlaps source tree: {root}')
if OUT.exists():
    P.error('--out must name a fresh nonexistent directory')
for root in (BASE, CANDIDATE):
    if not root.is_dir():
        P.error(f'missing source tree: {root}')
SOURCES = [
    'firmware/platform/adc_timer_dma.c', 'firmware/app/mt_adc_control.c',
    'firmware/app/mt_control.c', 'firmware/app/mt_capture_link.c',
    'firmware/protocol/mt_command.c', 'firmware/protocol/mt_session.c',
    'firmware/protocol/mt_wire.c', 'firmware/transport/mt_tx.c',
    'firmware/acquisition/acquisition.c',
]
INPUTS = {}
for relative in SOURCES + [str(Path(x).with_suffix('.h')) for x in SOURCES]:
    source = CANDIDATE / relative
    if not source.is_file():
        source = BASE / relative
    INPUTS[relative] = source
for relative in ('tests/platform/test_adc_timer_dma.c', 'tests/adc-control/test_adc_control.c'):
    INPUTS[relative] = HERE / 'legacy' / relative
if not A.legacy_only:
    INPUTS['tests/adc-commit/test_admission.c'] = HERE / 'test_admission.c'
TRACK = sorted(set(INPUTS.values()) | {Path(__file__).resolve(), HERE / 'REMOTE_BASE.json',
    HERE / 'legacy/REMOTE_INPUTS.json'})
if (CANDIDATE / 'BASE_INPUTS.json').is_file():
    TRACK.append(CANDIDATE / 'BASE_INPUTS.json')
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def fingerprint():
    return {str(path): sha(path) for path in TRACK}
def blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
# Validate old tests and dependency sources against the immutable GitHub tree.
REMOTE = json.loads((HERE / 'REMOTE_BASE.json').read_text())
BLOBS = {x['path']: x['sha'] for x in REMOTE['files']}
VERIFIED = {}
for relative, path in INPUTS.items():
    if path.is_relative_to(BASE) or path.is_relative_to(HERE / 'legacy'):
        actual = blob(path)
        if actual != BLOBS[relative]:
            raise SystemExit(f'immutable input mismatch: {path}: {actual} != {BLOBS[relative]}')
        VERIFIED[relative] = actual
BEFORE = fingerprint()
OUT.mkdir(parents=True, exist_ok=False)
RECEIPT = {'invocation': sys.argv, 'start_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'base_commit': REMOTE['commit'], 'verified_git_blobs': VERIFIED,
    'sources_before': BEFORE, 'commands': [], 'sanitize': A.sanitize,
    'legacy_only': A.legacy_only, 'hardware_claimed': False, 'primask_claimed': False,
    'leak_sanitizer_claimed': False, 'status': 'RUNNING'}
STAGE = OUT / 'snapshot'
CC = os.environ.get('CC', 'cc')
FLAGS = ['-std=c11', '-pedantic-errors', '-Wall', '-Wextra', '-Werror', '-Wconversion',
    '-Wshadow', '-g', '-O1' if A.sanitize else '-O2', '-fno-omit-frame-pointer',
    '-I' + str(STAGE / 'firmware/platform')]
if A.sanitize:
    FLAGS += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
ENV = dict(os.environ)
if A.sanitize:
    ENV['ASAN_OPTIONS'] = 'detect_leaks=0:halt_on_error=1'
    ENV['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
RECEIPT['runtime_environment'] = {x: ENV.get(x) for x in ('ASAN_OPTIONS', 'UBSAN_OPTIONS')}
EXIT = 0
with (OUT / 'run.log').open('w') as LOG:
    def run(command):
        print('+', shlex.join(command), flush=True)
        LOG.write('+ ' + shlex.join(command) + '\n'); LOG.flush()
        record = {'argv': command}
        RECEIPT['commands'].append(record)
        result = subprocess.run(command, env=ENV, cwd=OUT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        record['exit'] = result.returncode
        print(result.stdout, end='', flush=True)
        LOG.write(result.stdout); LOG.flush()
        if result.returncode:
            raise RuntimeError(f'command failed ({result.returncode}): {shlex.join(command)}')
    try:
        for relative, source in INPUTS.items():
            target = STAGE / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            assert sha(source) == sha(target)
        run([CC, '--version'])
        targets = [('legacy-driver', 'tests/platform/test_adc_timer_dma.c', SOURCES[:1]),
                   ('legacy-controller', 'tests/adc-control/test_adc_control.c', SOURCES)]
        if not A.legacy_only:
            targets.append(('admission', 'tests/adc-commit/test_admission.c', SOURCES))
        RECEIPT['executables'] = {}
        for name, test, sources in targets:
            binary = OUT / name
            run([CC, *FLAGS, str(STAGE / test), *[str(STAGE / p) for p in sources], '-o', str(binary)])
            run([str(binary)])
            RECEIPT['executables'][name] = sha(binary)
        RECEIPT['status'] = 'PASS'
    except Exception as exc:
        RECEIPT['status'] = 'FAIL'; RECEIPT['error'] = repr(exc)
        print(str(exc), file=sys.stderr); LOG.write(str(exc) + '\n'); EXIT = 1
    finally:
        AFTER = fingerprint()
        RECEIPT['sources_after'] = AFTER
        RECEIPT['inputs_unchanged'] = BEFORE == AFTER
        if BEFORE != AFTER:
            RECEIPT['status'] = 'FAIL_INPUT_CHANGED'; EXIT = 1
        RECEIPT['finish_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (OUT / 'receipt.json').write_text(json.dumps(RECEIPT, indent=2) + '\n')
print('Receipt:', OUT / 'receipt.json', flush=True)
raise SystemExit(EXIT)

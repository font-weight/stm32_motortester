"""Фиксирует неизменность исходников и реальные результаты; не даёт приёмку."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True, help='Новая папка журналов, без перезаписи')
args = parser.parse_args()
out = Path(args.output).resolve()
out.mkdir(parents=True, exist_ok=False)


def hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / 'host').rglob('*'))
            if p.is_file() and p.suffix != '.pyc'
            and 'acquisition_modes/evidence/' not in str(p)}


before = hashes()
(out / 'source-before.json').write_text(json.dumps(before, indent=2) + '\n')
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
           PYTHONPATH='host:/tmp/motortester-deps', QT_QPA_PLATFORM='offscreen')
commands = [('new-normal', [sys.executable, str(HERE / 'run_suite.py'), '--require-gui']),
            ('new-optimized', [sys.executable, '-O', str(HERE / 'run_suite.py'), '--require-gui']),
            ('full-host', [sys.executable, str(HERE / 'run_suite.py'), '--pattern', 'test_*.py', '--require-gui'])]
records = []
for name, command in commands:
    run_env = dict(env)
    if name == 'new-normal':
        run_env['ACQUISITION_CAPTURE_DIR'] = str(out / 'captures')
    else:
        run_env.pop('ACQUISITION_CAPTURE_DIR', None)
    started = time.time()
    completed = subprocess.run(command, cwd=ROOT, env=run_env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    (out / f'{name}.log').write_text(completed.stdout)
    records.append(dict(name=name, command=command, cwd=str(ROOT), exit_code=completed.returncode,
                        elapsed_seconds=round(time.time() - started, 3)))
    print(name, 'exit', completed.returncode, flush=True)
    print(completed.stdout[-700:], flush=True)
after = hashes()
(out / 'source-after.json').write_text(json.dumps(after, indent=2) + '\n')
changed = sorted(set(before) ^ set(after) | {p for p in before.keys() & after.keys() if before[p] != after[p]})
manifest = dict(local_base='ef2880c0655d155284c176424a12378b3b66adf4',
                overlay_manifest_sha256='a4596d0f5eea1318841390600bcc763d9aa02ad66649ec4a06fece3de37a0042',
                commands=records, source_stable=not changed, changed_paths=changed,
                source_file_count=len(before),
                evidence_hashes={str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted(out.rglob('*')) if p.is_file()})
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
if changed:
    print('SOURCE_CHANGED', changed)
sys.exit(0 if not changed and all(r['exit_code'] == 0 for r in records) else 1)

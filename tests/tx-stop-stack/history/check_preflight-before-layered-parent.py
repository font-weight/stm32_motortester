#!/usr/bin/env python3
"""Exercise the package wrapper's no-write guards using disposable fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

P = argparse.ArgumentParser(description=__doc__)
P.add_argument('--checkout', required=True, type=Path)
P.add_argument('--toolchain', required=True, type=Path)
P.add_argument('--out', required=True, type=Path)
A = P.parse_args()
C, T, O = A.checkout.resolve(), A.toolchain.resolve(), A.out.resolve()
PYTHON = [str(Path('/usr/bin/python3').resolve()), '-I', '-B']
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}
REL = 'tests/tx-stop-stack/reproduce.py'
if not sys.flags.isolated or not sys.dont_write_bytecode:
    P.error('invoke with /usr/bin/python3 -I -B')
for path in (Path(__file__).absolute().parent, Path(__file__).resolve().parent):
    if O == path or O.is_relative_to(path) or path.is_relative_to(O):
        P.error('output overlaps own script')
# Reuse the entire production wrapper preflight before this test writes fixtures.
pre = subprocess.run([*PYTHON, str(C / REL), '--toolchain', str(T), '--out', str(A.out), '--preflight-only'],
                     env=ENV, capture_output=True, text=True)
if pre.returncode:
    P.error('complete prerequisite preflight failed: ' + pre.stdout + pre.stderr)
O.mkdir(parents=True, exist_ok=False)
rows = []
tracked = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in C.rglob('*') if p.is_file() and '.git' not in p.parts}

def check(name, target, checkout=C, toolchain=T, env=ENV, accept=False):
    existed = os.path.lexists(target)
    argv = [*PYTHON, str(checkout / REL), '--toolchain', str(toolchain), '--out', str(target), '--preflight-only']
    r = subprocess.run(argv, env=env, capture_output=True, text=True)
    (O / (name + '.log')).write_text(r.stdout + r.stderr)
    passed = r.returncode == (0 if accept else 2) and (existed or not os.path.lexists(target))
    rows.append(dict(case=name, passed=passed, exit=r.returncode, argv=argv, no_output_created=existed or not os.path.lexists(target)))
    if not passed:
        raise RuntimeError(name + ': ' + r.stdout + r.stderr)

def clone(name):
    dest = O / name
    shutil.copytree(C, dest, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    return dest

try:
    check('checkout-child', C / 'tx002-must-not-exist')
    check('checkout-ancestor', C.parent)
    alias = O / 'output-alias'; alias.symlink_to(C, target_is_directory=True)
    check('resolved-output-alias', alias / 'tx002-must-not-exist')
    for name, relative in [('own-script', REL), ('nested-script', 'tests/tx-stop-stack/supporting/harness/run.py'),
                           ('evidence', 'tests/tx-stop-stack/critic-a-local/CRITIC_A_TX002_REVIEW.md')]:
        copy = clone(name + '-checkout')
        backing = O / (name + '-backing'); backing.mkdir()
        source = copy / relative
        moved = backing / source.name
        source.rename(moved); source.symlink_to(moved)
        check(name + '-alias', backing / 'must-not-exist', checkout=copy)
    bad = clone('bad-checkout')
    production = bad / 'firmware/transport/mt_tx.c'
    production.write_text(production.read_text() + '\n/* deliberate mismatch */\n')
    check('mismatched-production', O / 'bad-output', checkout=bad)
    missing = O / 'missing-toolchain'; missing.mkdir()
    check('missing-toolchain', O / 'missing-output', toolchain=missing)
    # Valid external-driver and external-header proxies, without assumed cc1 paths.
    proxy = O / 'tool-proxy'; (proxy / 'bin').mkdir(parents=True); (proxy / 'include').mkdir()
    for n in ('gcc', 'as', 'ld', 'nm', 'size', 'readelf', 'objdump'):
        (proxy / 'bin' / ('arm-none-eabi-' + n)).symlink_to(T / 'bin' / ('arm-none-eabi-' + n))
    (proxy / 'include/newlib').symlink_to(T / 'include/newlib', target_is_directory=True)
    check('valid-external-tool-proxy', O / 'valid-proxy-output', toolchain=proxy, accept=True)
    driver = (T / 'bin/arm-none-eabi-gcc').resolve()
    helper = Path(subprocess.check_output([str(driver), '-print-prog-name=cc1'], env=ENV, text=True).strip()).resolve()
    check('driver-backing-parent', driver.parent / 'tx002-must-not-exist', toolchain=proxy)
    check('helper-backing-parent', helper.parent / 'tx002-must-not-exist', toolchain=proxy)
    check('header-backing-parent', (T / 'include/newlib').resolve() / 'tx002-must-not-exist', toolchain=proxy)
    poison = {**ENV, 'CC': '/usr/bin/false', 'CFLAGS': '-DNDEBUG -include missing.h', 'CPATH': '/missing',
        'C_INCLUDE_PATH': '/missing', 'GCC_EXEC_PREFIX': '/missing/', 'COMPILER_PATH': '/missing',
        'LIBRARY_PATH': '/missing', 'LD_PRELOAD': '/missing.so', 'PYTHONPATH': str(O / 'poison'),
        'PYTHONHOME': '/missing', 'PYTHONOPTIMIZE': '2', 'PYTHONSTARTUP': '/missing', 'TMPDIR': '/missing'}
    (O / 'poison').mkdir(); (O / 'poison/sitecustomize.py').write_text('raise RuntimeError("poison imported")\n')
    check('hostile-environment', O / 'poison-output', env=poison, accept=True)
    check('fresh-normal', O / 'normal-output', accept=True)
    status = 'PASS'
except Exception as exc:
    status = 'FAIL'; error = repr(exc)
finally:
    unchanged = all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in tracked.items())
    if not unchanged:
        status = 'FAIL_INPUT_CHANGED'
    receipt = dict(status=status, results=rows, inputs_unchanged=unchanged,
                   initial_preflight=dict(argv=pre.args, exit=pre.returncode, output=pre.stdout + pre.stderr))
    if 'error' in locals(): receipt['error'] = error
    (O / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(status, len(rows), str(O / 'receipt.json'))
raise SystemExit(status != 'PASS')

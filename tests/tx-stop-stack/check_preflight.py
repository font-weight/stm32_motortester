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
try:
    C, T, O = A.checkout.absolute(), A.toolchain.absolute(), A.out.resolve()
except (OSError, RuntimeError) as exc:
    P.error('cannot resolve input or output: ' + str(exc))
PYTHON = [str(Path('/usr/bin/python3').resolve()), '-I', '-B']
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}
REL = 'tests/tx-stop-stack/reproduce.py'
if not sys.flags.isolated or not sys.dont_write_bytecode:
    P.error('invoke with /usr/bin/python3 -I -B')
def path_forms(value):
    value = Path(value).absolute()
    forms = {value}
    current, pending, links = Path(value.anchor), list(value.parts[1:]), 0
    while pending:
        part = pending.pop(0)
        if part == '..':
            current = current.parent
            continue
        if part == '.':
            continue
        next_path = current / part
        if next_path.is_symlink():
            links += 1
            if links > 80:
                raise OSError('symlink loop or excessive alias chain: ' + str(value))
            target = Path(os.readlink(next_path))
            target = target if target.is_absolute() else next_path.parent / target
            full = target.joinpath(*pending)
            forms.add(full)
            current, pending = Path(full.anchor), list(full.parts[1:])
        else:
            current = next_path
    forms.add(current)
    return forms

try:
    for spelling in path_forms(Path(__file__)):
        for root in (spelling.parent, spelling.parent.resolve()):
            for output in path_forms(A.out):
                if output == root or output.is_relative_to(root) or root.is_relative_to(output):
                    P.error('output overlaps own script backing directory')
except (OSError, RuntimeError) as exc:
    P.error('cannot resolve protected input: ' + str(exc))
# Reuse the entire production wrapper preflight before this test writes fixtures.
pre = subprocess.run([*PYTHON, str(C / REL), '--toolchain', str(T), '--out', str(A.out), '--preflight-only'],
                     env=ENV, capture_output=True, text=True)
if pre.returncode:
    P.error('complete prerequisite preflight failed: ' + pre.stdout + pre.stderr)
O.mkdir(parents=True, exist_ok=False)
rows = []
tracked = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in C.rglob('*') if p.is_file() and '.git' not in p.parts}

def check(name, target, checkout=C, toolchain=T, env=ENV, accept=False, entrypoint=None):
    existed = os.path.lexists(target)
    argv = [*PYTHON, str(entrypoint if entrypoint is not None else checkout / REL), '--toolchain', str(toolchain), '--out', str(target), '--preflight-only']
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
    # Exact layered alias: lexical parent, resolved parent directory, final
    # target parent are all distinct. Keep a valid proxy as a positive control.
    layered = O / 'layered-tool-interface'; layered.mkdir()
    intermediate = O / 'layered-tool-intermediate'; intermediate.mkdir()
    (layered / 'bin').symlink_to(intermediate, target_is_directory=True)
    (layered / 'include').mkdir()
    (layered / 'include/newlib').symlink_to(T / 'include/newlib', target_is_directory=True)
    for n in ('gcc', 'as', 'ld', 'nm', 'size', 'readelf', 'objdump'):
        (intermediate / ('arm-none-eabi-' + n)).symlink_to(T / 'bin' / ('arm-none-eabi-' + n))
    driver_path = layered / 'bin/arm-none-eabi-gcc'
    roots = [driver_path.absolute().parent, driver_path.absolute().parent.resolve(), driver_path.resolve().parent]
    assert len(set(roots)) == 3
    check('layered-tool-valid', O / 'layered-tool-valid-output', toolchain=layered, accept=True)
    check('layered-tool-intermediate-parent', intermediate / 'must-not-exist', toolchain=layered)
    # Replay the unchanged original wrapper on a disposable, rehashed fixture.
    # Only wrapper bytes/its manifest entry differ; assertions and test sources
    # are unchanged. The old preflight must accept this exact missed directory.
    legacy = clone('legacy-wrapper-checkout')
    source = legacy / REL
    source.write_bytes((C / 'tests/tx-stop-stack/history/reproduce-before-layered-parent.py').read_bytes())
    manifest_path = legacy / 'tests/tx-stop-stack/package-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for row in manifest['files']:
        if row['path'] == REL:
            data = source.read_bytes(); row['bytes'] = len(data)
            row['sha256'] = hashlib.sha256(data).hexdigest()
            row['git_blob'] = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    check('layered-old-wrapper-acceptance', intermediate / 'old-preflight-accepted',
          checkout=legacy, toolchain=layered, accept=True)
    entry = O / 'layered-companion-interface'; entry.mkdir()
    entry_backing = O / 'layered-companion-intermediate'; entry_backing.mkdir()
    (entry / 'bin').symlink_to(entry_backing, target_is_directory=True)
    (entry_backing / 'check_preflight.py').symlink_to(C / 'tests/tx-stop-stack/check_preflight.py')
    own_path = entry / 'bin/check_preflight.py'
    own_roots = [own_path.absolute().parent, own_path.absolute().parent.resolve(), own_path.resolve().parent]
    assert len(set(own_roots)) == 3
    own_out = entry_backing / 'must-not-exist'
    own_argv = [*PYTHON, str(own_path), '--checkout', str(C), '--toolchain', str(T), '--out', str(own_out)]
    response = subprocess.run(own_argv, env=ENV, capture_output=True, text=True)
    (O / 'layered-companion-entrypoint.log').write_text(response.stdout + response.stderr)
    passed = response.returncode == 2 and not own_out.exists()
    rows.append(dict(case='layered-companion-entrypoint', passed=passed, exit=response.returncode,
                     argv=own_argv, no_output_created=not own_out.exists()))
    assert passed
    (O / 'layered-fixtures.json').write_text(json.dumps(dict(
        tool_roots=list(map(str, roots)), companion_roots=list(map(str, own_roots)),
        unchanged_old_wrapper_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        legacy_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest()), indent=2) + '\n')
    # Portable current FILE -> FILE regression, including this companion itself.
    chain_entry, chain_mid = O / 'file-chain-entry', O / 'file-chain-mid'
    chain_entry.mkdir(); chain_mid.mkdir()
    (chain_mid / 'reproduce.py').symlink_to(C / REL)
    (chain_entry / 'reproduce.py').symlink_to(chain_mid / 'reproduce.py')
    check('file-file-script-intermediate-parent', chain_mid / 'must-not-exist/new',
          entrypoint=chain_entry / 'reproduce.py')
    chain_proxy, tool_mid = O / 'file-tool-proxy', O / 'file-tool-mid'
    (chain_proxy / 'bin').mkdir(parents=True); tool_mid.mkdir()
    for n in ('gcc', 'as', 'ld', 'nm', 'size', 'readelf', 'objdump'):
        name = 'arm-none-eabi-' + n
        (tool_mid / name).symlink_to(T / 'bin' / name)
        (chain_proxy / 'bin' / name).symlink_to(tool_mid / name)
    for name in ('lib', 'include'):
        (chain_proxy / name).symlink_to(T / name, target_is_directory=True)
    check('file-file-tool-intermediate-parent', tool_mid / 'must-not-exist/new',
          toolchain=chain_proxy)
    check('file-file-tool-valid-proxy', O / 'file-tool-valid-output',
          toolchain=chain_proxy, accept=True)
    (chain_mid / 'check_preflight.py').symlink_to(C / 'tests/tx-stop-stack/check_preflight.py')
    (chain_entry / 'check_preflight.py').symlink_to(chain_mid / 'check_preflight.py')
    for name, script, output, toolchain in [
        ('file-file-companion-script-parent', chain_entry / 'check_preflight.py',
         chain_mid / 'must-not-exist/new', T),
        ('file-file-companion-tool-parent', C / 'tests/tx-stop-stack/check_preflight.py',
         tool_mid / 'must-not-exist/new', chain_proxy),
    ]:
        argv = [*PYTHON, str(script), '--checkout', str(C), '--toolchain', str(toolchain),
                '--out', str(output)]
        response = subprocess.run(argv, env=ENV, capture_output=True, text=True)
        (O / (name + '.log')).write_text(response.stdout + response.stderr)
        passed = response.returncode == 2 and not output.exists() and not output.parent.exists()
        rows.append(dict(case=name, passed=passed, exit=response.returncode, argv=argv,
                         no_output_created=not output.exists(), no_parent_created=not output.parent.exists()))
        assert passed
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

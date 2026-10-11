#!/usr/bin/env python3
"""Offline TX002 reproduction from its exact checkout; fresh disjoint output only."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

P = argparse.ArgumentParser(description=__doc__)
P.add_argument('--toolchain', required=True, type=Path,
               help='ARM GCC 14.2.1 interface: bin/arm-none-eabi-* and include/newlib')
P.add_argument('--out', required=True, type=Path)
P.add_argument('--preflight-only', action='store_true')
A = P.parse_args()
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RAW = A.out.absolute()
try:
    OUT = A.out.resolve()
except (OSError, RuntimeError) as exc:
    P.error('cannot resolve output: ' + str(exc))
TOOL = A.toolchain.absolute()
ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'LANG': 'C',
       'PYTHONDONTWRITEBYTECODE': '1'}
PYTHON = [str(Path('/usr/bin/python3').resolve()), '-I', '-B']
PROTECTED = {}
PROBES = []
TOOLS = set()
TRACK = set()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

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

def protect(path, label, file=False):
    """Trace stable input/output aliases before mkdir; not a TOCTOU sandbox."""
    try:
        roots = set()
        for spelling in path_forms(path):
            root = spelling.parent if file else spelling
            roots.update((root, root.resolve()))
        for root in roots:
            for output in path_forms(A.out):
                if output == root or output.is_relative_to(root) or root.is_relative_to(output):
                    P.error('output overlaps protected input backing directory: ' + label)
            PROTECTED[str(root)] = label
    except (OSError, RuntimeError) as exc:
        P.error('cannot resolve protected input: ' + str(exc))

def regular(path):
    protect(path, 'package or inherited input', file=True)
    if not path.is_file() or not path.resolve().is_relative_to(REPO):
        P.error('missing or escaping checkout input: ' + str(path))
    for part in (path, *path.parents):
        if part == REPO:
            break
        if part.is_symlink():
            P.error('checkout symlink unsupported: ' + str(path))

def safe_rel(value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        P.error('unsafe relative manifest path')
    return path

def tool(path, label):
    path = Path(path)
    protect(path, label, file=True)
    if not path.is_file():
        P.error('missing tool: ' + label)
    TOOLS.add(path.resolve())
    return path.resolve()

def probe(argv, stdin=None):
    result = subprocess.run(list(map(str, argv)), cwd=HERE, env=ENV,
                            input=stdin, capture_output=True, text=True)
    PROBES.append(dict(argv=list(map(str, argv)), exit=result.returncode,
                       stdout=result.stdout, stderr=result.stderr))
    if result.returncode:
        P.error('read-only compiler preflight failed: ' + str(argv[0]))
    return result

def selected(compiler, name, is_file=False):
    value = probe([compiler, ('-print-file-name=' if is_file else '-print-prog-name=') + name]).stdout.strip()
    path = Path(value) if Path(value).is_absolute() else None
    if path is None:
        found = shutil.which(value, path=ENV['PATH'])
        path = Path(found) if found else None
    if path is None or not path.is_file():
        P.error('selected compiler helper is unavailable: ' + name)
    return tool(path, 'selected ' + name)

def header_tree(root):
    if not root.is_dir():
        P.error('missing header directory: ' + str(root))
    seen = set()
    for parent, dirs, names in os.walk(root, followlinks=True):
        path = Path(parent)
        protect(path, 'header directory')
        if path.resolve() in seen:
            dirs[:] = []
            continue
        seen.add(path.resolve())
        for name in names:
            file = path / name
            if file.is_file():
                protect(file, 'header backing file', file=True)
                TRACK.add(file.resolve())

def compiler_preflight(compiler, flags):
    helpers = {name: selected(compiler, name) for name in ('cc1', 'as', 'ld', 'collect2')}
    for line in probe([compiler, '-print-search-dirs']).stdout.splitlines():
        if ': ' in line:
            for value in line.split(': ', 1)[1].lstrip('=').split(os.pathsep):
                if value and Path(value).is_absolute():
                    protect(Path(value), 'compiler search directory')
    active = False
    for line in probe([compiler, *flags, '-E', '-x', 'c', '-v', '-'], stdin='').stderr.splitlines():
        if '#include <...> search starts here:' in line:
            active = True
            continue
        if 'End of search list.' in line:
            active = False
        if active and Path(line.strip()).is_dir():
            header_tree(Path(line.strip()))
    return helpers

if not sys.flags.isolated or not sys.dont_write_bytecode:
    P.error('invoke explicitly as /usr/bin/python3 -I -B reproduce.py ...')
if RAW.exists() or RAW.is_symlink():
    P.error('output must be a fresh nonexistent path')
protect(REPO, 'checkout')
protect(Path(__file__), 'wrapper script', file=True)
protect(TOOL, 'advertised ARM toolchain')
for path in (sys.executable, '/usr/bin/python3', '/usr/bin/cc', '/usr/bin/gcc', '/bin/sh', '/bin/bash'):
    tool(Path(path), path)
protect(Path(sys.base_prefix), 'Python installation')
for file in ('input-map.json', 'package-manifest.json'):
    regular(HERE / file)
    TRACK.add(HERE / file)
MAP = json.loads((HERE / 'input-map.json').read_text())
MANIFEST = json.loads((HERE / 'package-manifest.json').read_text())
for row in MANIFEST['files']:
    path = REPO / safe_rel(row['path'])
    regular(path)
    if sha(path) != row['sha256'] or blob(path) != row['git_blob']:
        P.error('package checksum mismatch: ' + row['path'])
    TRACK.add(path)
actual = {str(path.relative_to(REPO)) for path in HERE.rglob('*') if path.is_file()}
actual.discard('tests/tx-stop-stack/package-manifest.json')
actual.update(MAP['production_sha256'])
if actual != {row['path'] for row in MANIFEST['files']}:
    P.error('package inventory differs from frozen manifest')
for row in MAP['files']:
    safe_rel(row['virtual_path'])
    path = REPO / safe_rel(row['repository_path'])
    regular(path)
    if sha(path) != row['sha256'] or blob(path) != row['git_blob']:
        P.error('pinned source/evidence mismatch: ' + row['virtual_path'])
    TRACK.add(path)
NATIVE = tool(Path('/usr/bin/cc'), 'native compiler')
compiler_preflight(NATIVE, [])
selected(NATIVE, 'libasan.so', True)
selected(NATIVE, 'libubsan.so', True)
ARM = {name: tool(TOOL / 'bin' / ('arm-none-eabi-' + name), 'ARM ' + name)
       for name in ('gcc', 'as', 'ld', 'nm', 'size', 'readelf', 'objdump')}
AH = compiler_preflight(ARM['gcc'], ['-mcpu=cortex-m3', '-mthumb', '-ffreestanding',
                                    '-isystem', str(TOOL / 'include/newlib')])
header_tree(TOOL / 'include/newlib')
GCC_INCLUDE = Path(probe([ARM['gcc'], '-print-file-name=include']).stdout.strip()).resolve()
header_tree(GCC_INCLUDE)
# The historical interface is normalized in output. Actual helpers are discovered
# from GCC first; valid driver/helper aliases may live outside the advertised root.
TC_FILES = {}
for rel, expected in MAP['toolchain'].items():
    path = Path(rel)
    if path.parts[0] == 'bin':
        actual_tool = ARM[path.name.removeprefix('arm-none-eabi-')]
    elif path.name == 'cc1':
        actual_tool = AH['cc1']
    elif path.name == 'as':
        actual_tool = AH['as']
    elif rel.startswith('include/newlib/'):
        actual_tool = TOOL / rel
    elif '/include/' in rel:
        actual_tool = GCC_INCLUDE / rel.split('/include/', 1)[1]
    else:
        P.error('unrecognized historical tool interface: ' + rel)
    protect(actual_tool, 'selected toolchain file', file=True)
    if not actual_tool.is_file() or sha(actual_tool) != expected:
        P.error('pinned ARM toolchain mismatch: ' + rel)
    TC_FILES[rel] = actual_tool.resolve()
    TRACK.add(actual_tool.resolve())
TRACK.update(TOOLS)
def fingerprint():
    return {str(path): sha(path) for path in sorted(TRACK)}
BEFORE = fingerprint()
if A.preflight_only:
    print('PASS: read-only complete package/toolchain preflight; no output created')
    raise SystemExit(0)
OUT.mkdir(parents=True, exist_ok=False)
(OUT / 'tmp').mkdir()
ENV.update(TMPDIR=str(OUT / 'tmp'), HOME=str(OUT), CC=str(NATIVE))
R = dict(status='RUNNING', parent_commit=MAP['parent_commit'], parent_tree=MAP['parent_tree'],
         started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
         invocation=sys.argv, input_sha256_before=BEFORE, commands=[],
         compiler_preflight=PROBES, protected_backing_directories=PROTECTED,
         child_environment=ENV, package_manifest_sha256=sha(HERE / 'package-manifest.json'),
         scope='Local TX002 native/immutable regression and ARM relocatable evidence only. Historical modeled runtime is not a complete current device test.',
         hardware_claimed=False, full_elf_claimed=False, independent_acceptance_claimed=False)

def run(name, argv):
    argv = list(map(str, argv))
    result = subprocess.run(argv, cwd=OUT, env=ENV, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log = OUT / (name + '.log')
    log.write_text(result.stdout)
    R['commands'].append(dict(name=name, argv=argv, exit=result.returncode,
                              log=log.name, log_sha256=sha(log)))
    print(name + ': ' + str(result.returncode) + '\n' + result.stdout, flush=True)
    if result.returncode:
        raise RuntimeError('command failed: ' + name)

try:
    M = OUT / 'materialized'
    for row in MAP['files']:
        dest = M / row['virtual_path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / row['repository_path'], dest)
        dest.chmod(0o755 if row['executable'] else 0o644)
        if sha(dest) != row['sha256']:
            raise RuntimeError('materialization checksum failed')
    TC = OUT / 'toolchain'
    for rel, source in TC_FILES.items():
        dest = TC / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(source)
    R['normalized_toolchain'] = {rel: str(src) for rel, src in TC_FILES.items()}
    H = M / 'tx-stop-stack-tests/harness'
    B = M / 'usb-bridge-publication/base-checkout'
    C = M / 'tx-stop-stack-draft'
    O = M / 'tx-stop-stack-tests/original'
    common = ['--base', B, '--candidate', C, '--original', O]
    for label, script, extra in (
        ('native-strict', 'run.py', []), ('native-sanitize', 'run.py', ['--sanitize']),
        ('mutations', 'negative_controls.py', []), ('native-runner', 'test_runner.py', [])):
        run(label, [*PYTHON, H / script, *common, '--out', OUT / label, *extra])
    run('pinned-app-control', [*PYTHON, H / 'pinned_regressions.py', '--pinned',
        M / 'tx-stop-stack-tests/pinned-regressions', '--candidate', C, '--out', OUT / 'pinned-app-control'])
    for variant, source in [('old', B), ('candidate', C)]:
        for mode in ['strict', 'sanitize']:
            label = 'oracle-' + variant + '-' + mode
            run(label, [*PYTHON, M / 'critic-a-tx-stack-review/run_oracle.py',
                '--source-root', source, '--wire-root', B, '--out', OUT / label,
                *(['--sanitize'] if mode == 'sanitize' else [])])
    for mode in ['strict', 'sanitize']:
        label = 'historical-runtime-' + mode
        args = ['--runtime-tests', M / 'runtime-tests', '--runtime', M / 'runtime-draft',
                '--base', B, '--candidate', C, '--adc', M / 'adc-commit-draft',
                '--bridge', M / 'usb-bridge-draft', '--epoch', M / 'usb-epoch-draft',
                '--dependencies', M / 'usb-dependencies', '--cdc-support', M / 'usb-cdc-tests',
                '--bridge-support', M / 'usb-bridge-tests']
        run(label, [*PYTHON, H / 'runtime_overlay.py', *args, '--out', OUT / label,
                    *(['--sanitize'] if mode == 'sanitize' else [])])
    recipe = M / 'arm-closure-tx-stack-draft/tools/check_firmware_closure.py'
    run('arm', [*PYTHON, M / 'tx-stop-stack-arm/runner/compare_arm.py',
        '--baseline', M / 'arm-closure-memory-root-r1', '--old-source', B / 'firmware/transport/mt_tx.c',
        '--candidate', C, '--recipe', recipe, '--toolchain', TC, '--input-root', M, '--out', OUT / 'arm'])
    run('overlay-matrix', [*PYTHON, M / 'critic-a-tx-stack-review/check_overlay.py',
        '--recipe', recipe, '--previous-recipe', M / 'arm-closure-memory-draft/tools/check_firmware_closure.py',
        '--reference', OUT / 'arm/recipe-baseline/receipt.json', '--candidate', C, '--out', OUT / 'overlay-matrix'])
    R['status'] = 'PASS_LOCAL_TX002_ONLY'
except Exception as exc:
    R['status'] = 'FAIL'
    R['error'] = repr(exc)
finally:
    try:
        R['input_sha256_after'] = fingerprint()
        R['inputs_unchanged'] = R['input_sha256_after'] == BEFORE
    except Exception as exc:
        R['inputs_unchanged'] = False
        R['fingerprint_error'] = repr(exc)
    if not R['inputs_unchanged']:
        R['status'] = 'FAIL_INPUT_CHANGED'
    R['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT / 'receipt.json').write_text(json.dumps(R, indent=2) + '\n')
print(R['status'] + ': ' + str(OUT / 'receipt.json'))
raise SystemExit(0 if R['status'] == 'PASS_LOCAL_TX002_ONLY' else 1)

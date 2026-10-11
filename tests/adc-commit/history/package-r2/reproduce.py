#!/usr/bin/env python3
"""Reproduce the frozen ADC003 package offline, from a checked-out candidate."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
P = argparse.ArgumentParser(description=__doc__)
P.add_argument('--out', required=True, type=Path)
P.add_argument('--toolchain', required=True, type=Path,
               help='ARM GCC root with bin/arm-none-eabi-* and include/newlib')
A = P.parse_args()
OUT, TOOL = A.out.resolve(), A.toolchain.resolve()
PROTECTED = {}

def protect(path, label, file=False):
    """Protect lexical and actual backing directories, not just advertised roots."""
    lexical = path.absolute()
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        P.error('cannot resolve protected input: ' + str(exc))
    for root in (lexical.parent if file else lexical,
                 resolved.parent if file else resolved):
        if OUT == root or OUT.is_relative_to(root) or root.is_relative_to(OUT):
            P.error('output overlaps protected input backing directory: ' + label)
        PROTECTED[str(root)] = label

protect(REPO, 'checkout')
protect(A.toolchain, 'advertised toolchain')
protect(Path(__file__), 'wrapper script', file=True)
protect(Path(sys.executable), 'Python interpreter', file=True)
protect(Path(sys.base_prefix), 'Python installation')
if A.out.exists() or A.out.is_symlink():
    P.error('output must be a fresh nonexistent path')
# Do not inherit compiler search-path injection, optimization, loader or temp settings.
# CC is the sole explicit compiler selection; it becomes an absolute verified path.
NATIVE = shutil.which(os.environ.get('CC', 'cc'), path='/usr/bin:/bin')
if not NATIVE:
    P.error('native C compiler is unavailable')
protect(Path(NATIVE), 'native C compiler', file=True)
NATIVE = str(Path(NATIVE).resolve())
ENV = {'PATH': '/usr/bin:/bin', 'CC': NATIVE, 'LANG': 'C', 'LC_ALL': 'C',
       'PYTHONDONTWRITEBYTECODE': '1'}
PYTHON = [str(Path(sys.executable).resolve()), '-I', '-B']
TOOL_FILES = [Path(NATIVE), Path(sys.executable).resolve()]
PROBES = []
for name in ('gcc', 'size', 'nm', 'objdump'):
    if not (TOOL / 'bin' / ('arm-none-eabi-' + name)).is_file():
        P.error('ARM tool input missing; no output created: ' + name)
    tool = TOOL / 'bin' / ('arm-none-eabi-' + name)
    protect(tool, 'ARM ' + name, file=True)
    TOOL_FILES.append(tool.resolve())
if not (TOOL / 'include/newlib/stdint.h').is_file():
    P.error('newlib headers missing; no output created')
protect(TOOL / 'include/newlib', 'newlib headers')


def protect_header_tree(root):
    # Walk directory aliases as well as individual header aliases; prune cycles.
    seen = set()
    for parent, dirs, files in os.walk(root, followlinks=True):
        actual = Path(parent).resolve()
        protect(Path(parent), 'header directory')
        if actual in seen:
            dirs[:] = []
            continue
        seen.add(actual)
        for name in files:
            path = Path(parent) / name
            if path.is_symlink():
                protect(path, 'header backing file', file=True)


def probe(argv, stdin=None):
    result = subprocess.run([str(x) for x in argv], cwd=HERE, env=ENV,
                            input=stdin, text=True, capture_output=True)
    PROBES.append(dict(argv=[str(x) for x in argv], exit=result.returncode,
                       stdout=result.stdout, stderr=result.stderr))
    if result.returncode:
        P.error('compiler preflight failed; no output created: ' + str(argv[0]))
    return result

# GCC support programs and search/include directories may themselves be aliases.
# These queries only print metadata/preprocessor stdout, before any output mkdir.
for compiler, flags in (
    (Path(NATIVE), []),
    (TOOL / 'bin/arm-none-eabi-gcc', ['-mcpu=cortex-m3', '-mthumb',
      '-mfloat-abi=soft', '-ffreestanding', '-isystem', str(TOOL / 'include/newlib')]),
):
    for name in ('cc1', 'as', 'ld', 'collect2'):
        value = probe([compiler, '-print-prog-name=' + name]).stdout.strip()
        program = Path(value) if Path(value).is_absolute() else None
        if program is None:
            found = shutil.which(value, path=ENV['PATH'])
            program = Path(found) if found else None
        if program is not None and program.is_file():
            protect(program, 'compiler support program ' + name, file=True)
            TOOL_FILES.append(program.resolve())
    search = probe([compiler, '-print-search-dirs']).stdout
    for line in search.splitlines():
        if ': ' not in line:
            continue
        for value in line.split(': ', 1)[1].lstrip('=').split(os.pathsep):
            if value and Path(value).is_absolute():
                protect(Path(value), 'compiler search directory')
    includes = probe([compiler, *flags, '-E', '-x', 'c', '-v', '-'], stdin='').stderr
    active = False
    for line in includes.splitlines():
        if '#include <...> search starts here:' in line:
            active = True
            continue
        if 'End of search list.' in line:
            active = False
        if active:
            path = Path(line.strip())
            if path.is_absolute() and path.is_dir():
                protect_header_tree(path)
protect_header_tree(TOOL / 'include/newlib')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def regular(path):
    protect(path, 'checkout input', file=True)
    if not path.is_file() or not path.resolve().is_relative_to(REPO):
        P.error('missing or escaping checkout input: ' + str(path))
    for part in (path, *path.parents):
        if part == REPO:
            break
        if part.is_symlink():
            P.error('symlink checkout input: ' + str(path))

for file in ('parent-inputs.json', 'package-manifest.json'):
    regular(HERE / file)
MANIFEST = json.loads((HERE / 'package-manifest.json').read_text())
INPUTS = json.loads((HERE / 'parent-inputs.json').read_text())
TRACK = {HERE / 'package-manifest.json'}
for row in MANIFEST['files']:
    rel = Path(row['path'])
    if rel.is_absolute() or '..' in rel.parts:
        P.error('unsafe manifest path')
    path = REPO / rel
    regular(path)
    if sha(path) != row['sha256'] or blob(path) != row['git_blob']:
        P.error('package checksum mismatch: ' + str(rel))
    TRACK.add(path)
expected = {r['path'] for r in MANIFEST['files']}
actual = {p.relative_to(REPO).as_posix() for p in HERE.rglob('*') if p.is_file()}
actual.discard('tests/adc-commit/package-manifest.json')
actual.update(INPUTS['production_sha256'])
if actual != expected:
    P.error('package inventory differs from manifest')
for row in INPUTS['inputs']:
    rel = row['path']
    path = HERE / row['comparative_copy'] if row['comparative_copy'] else REPO / rel
    regular(path)
    if sha(path) != row['sha256'] or blob(path) != row['git_blob']:
        P.error('immutable parent input mismatch: ' + rel)
    TRACK.add(path)
for rel, digest in INPUTS['production_sha256'].items():
    regular(REPO / rel)
    if sha(REPO / rel) != digest:
        P.error('frozen production input mismatch: ' + rel)
    TRACK.add(REPO / rel)

def fingerprint():
    return {p.relative_to(REPO).as_posix(): sha(p) for p in sorted(TRACK)}

BEFORE = fingerprint()
OUT.mkdir(parents=True, exist_ok=False)
(OUT / 'tmp').mkdir()
ENV['TMPDIR'] = str(OUT / 'tmp')
RECEIPT = dict(status='RUNNING', parent_commit=INPUTS['parent_commit'],
               parent_tree=INPUTS['parent_tree'], invocation=sys.argv,
               started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               input_sha256_before=BEFORE, commands=[], hardware_claimed=False,
               independent_acceptance_claimed=False, full_elf_claimed=False,
               child_environment=ENV, python_flags=PYTHON[1:], compiler_preflight=PROBES,
               protected_backing_directories=PROTECTED,
               tool_sha256={str(p): sha(p) for p in sorted(set(TOOL_FILES))})

def run(argv):
    result = subprocess.run([str(x) for x in argv], cwd=OUT, env=ENV,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    RECEIPT['commands'].append(dict(argv=[str(x) for x in argv], exit=result.returncode,
                                    output=result.stdout))
    print(result.stdout, end='', flush=True)
    if result.returncode:
        raise RuntimeError('reproduction command failed: ' + str(argv[1]))

try:
    harness, base, candidate = (OUT / n for n in ('harness', 'base', 'candidate'))
    shutil.copytree(HERE / 'supporting', harness)
    for row in INPUTS['inputs']:
        rel = row['path']
        src = HERE / row['comparative_copy'] if row['comparative_copy'] else REPO / rel
        dst = base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if rel.startswith('tests/'):
            dst = harness / 'legacy' / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    for rel in INPUTS['production_sha256']:
        dst = candidate / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)
    for label, source, extra in (
        ('candidate-strict', candidate, []),
        ('candidate-sanitized', candidate, ['--sanitize']),
        ('baseline-strict', base, ['--legacy-only']),
        ('baseline-sanitized', base, ['--legacy-only', '--sanitize']),
    ):
        run([*PYTHON, harness / 'run.py', '--base', base, '--candidate', source,
             '--out', OUT / label, *extra])
    run([*PYTHON, harness / 'negative_controls.py', '--base', base,
         '--candidate', candidate, '--out', OUT / 'negative'])
    for mode in ('strict', 'sanitized'):
        run([*PYTHON, HERE / 'critic/run_extra.py', '--base-run',
             OUT / ('candidate-' + mode), '--out', OUT / ('extra-' + mode)])
    run([*PYTHON, HERE / 'arm_check.py', '--base', base, '--candidate', candidate,
         '--toolchain', TOOL, '--out', OUT / 'arm'])
    run([*PYTHON, HERE / 'critic/run_abi.py', '--base', base, '--candidate', candidate,
         '--toolchain', TOOL, '--out', OUT / 'abi'])
    RECEIPT['status'] = 'PASS'
except Exception as exc:
    RECEIPT['status'] = 'FAIL'
    RECEIPT['error'] = repr(exc)
    raise
finally:
    AFTER = fingerprint()
    RECEIPT['input_sha256_after'] = AFTER
    RECEIPT['inputs_unchanged'] = BEFORE == AFTER
    if BEFORE != AFTER:
        RECEIPT['status'] = 'FAIL_INPUT_CHANGED'
    RECEIPT['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT / 'receipt.json').write_text(json.dumps(RECEIPT, indent=2) + '\n')
    if BEFORE != AFTER:
        raise RuntimeError('checkout inputs changed')
print('PASS: native, ASan/UBSan, baseline, mutants, boundary tests, ARM objects and ABI.')
print('No hardware, full firmware link or independent final acceptance is claimed.')

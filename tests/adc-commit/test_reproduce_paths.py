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
p.add_argument('--preflight-only', action='store_true')
a = p.parse_args()
try:
    repo, tc, out = a.checkout.resolve(), a.toolchain.resolve(), a.out.resolve()
except (OSError, RuntimeError) as exc:
    p.error('cannot resolve input or output: ' + str(exc))
protected, queries = {}, []

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
            for output in path_forms(a.out):
                if output == root or output.is_relative_to(root) or root.is_relative_to(output):
                    p.error('output overlaps protected input backing directory: ' + label)
            protected[str(root)] = label
    except (OSError, RuntimeError) as exc:
        p.error('cannot resolve protected input: ' + str(exc))


def required(path, label, directory=False):
    protect(path, label, file=not directory)
    if not (path.is_dir() if directory else path.is_file()):
        p.error('required input missing before mkdir: ' + label)
    return path

protect(a.checkout, 'checkout')
protect(a.toolchain, 'advertised toolchain')
required(Path(__file__), 'companion script')
required(Path(sys.executable), 'Python interpreter')
protect(Path(sys.base_prefix), 'Python installation')
if a.out.exists() or a.out.is_symlink():
    p.error('fixture root must not exist')
required(repo, 'checkout', directory=True)
required(tc / 'lib', 'ARM libraries', directory=True)
required(tc / 'include', 'ARM includes', directory=True)
required(tc / 'include/newlib/stdint.h', 'newlib stdint.h')
for name in ('gcc', 'size', 'nm', 'objdump'):
    required(tc / 'bin' / ('arm-none-eabi-' + name), 'ARM ' + name)
required(Path('/usr/bin/cc'), 'native compiler')
clean = {'PATH': '/usr/bin:/bin', 'CC': '/usr/bin/cc', 'LANG': 'C', 'LC_ALL': 'C',
         'PYTHONDONTWRITEBYTECODE': '1'}


def walk_inputs(root, label, forbid_aliases=False):
    """Protect every visited backing parent; reject broken/looping source aliases."""
    files, seen = [], set()
    for parent, dirs, names in os.walk(root, followlinks=not forbid_aliases):
        dirs[:] = [x for x in dirs if x != '.git']
        directory = Path(parent)
        protect(directory, label + ' directory')
        actual = directory.resolve()
        if actual in seen:
            dirs[:] = []
            continue
        seen.add(actual)
        for name in dirs:
            child = directory / name
            protect(child, label + ' directory')
            if forbid_aliases and child.is_symlink():
                p.error('source directory alias is not supported: ' + str(child))
        for name in names:
            child = directory / name
            required(child, label + ' file')
            if forbid_aliases and child.is_symlink():
                p.error('source file alias is not supported: ' + str(child))
            files.append(child)
    return files

# The companion hashes/copies the entire package, including retained evidence.
# Inspect that full consumed set before its first mkdir, rather than only manifests.
source_files = walk_inputs(repo, 'source/evidence', forbid_aliases=True)
wrapper = required(repo / 'tests/adc-commit/reproduce.py', 'main wrapper')
required(repo / 'tests/adc-commit/test_reproduce_paths.py', 'companion checkout copy')

def read_json(path):
    required(path, 'manifest')
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        p.error('invalid manifest before mkdir: ' + str(exc))


def safe_relative(value):
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        p.error('unsafe manifest path')
    return relative


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

manifest = read_json(repo / 'tests/adc-commit/parent-inputs.json')
package = read_json(repo / 'tests/adc-commit/package-manifest.json')
for row in package['files']:
    path = required(repo / safe_relative(row['path']), 'package input')
    if digest(path) != row['sha256']:
        p.error('package input checksum mismatch before mkdir: ' + row['path'])
expected = {r['path'] for r in package['files']}
actual = {f.relative_to(repo).as_posix() for f in source_files
          if f.is_relative_to(repo / 'tests/adc-commit')}
actual.discard('tests/adc-commit/package-manifest.json')
actual.update(manifest['production_sha256'])
if actual != expected:
    p.error('package inventory mismatch before mkdir')
for rel, sha in manifest['production_sha256'].items():
    if digest(required(repo / safe_relative(rel), 'production input')) != sha:
        p.error('production input checksum mismatch before mkdir')
for row in manifest['inputs']:
    # Both are consumed: the parent comparison copy and the current checkout path.
    required(repo / safe_relative(row['path']), 'relocation input')
    path = (repo / 'tests/adc-commit' / safe_relative(row['comparative_copy'])
            if row['comparative_copy'] else repo / safe_relative(row['path']))
    if digest(required(path, 'parent input')) != row['sha256']:
        p.error('parent input checksum mismatch before mkdir')


def probe(argv, stdin=None):
    result = subprocess.run([str(x) for x in argv], env=clean,
                            cwd=Path(__file__).resolve().parent, input=stdin,
                            text=True, capture_output=True)
    queries.append(dict(argv=[str(x) for x in argv], exit=result.returncode,
                        stdout=result.stdout, stderr=result.stderr))
    if result.returncode:
        p.error('compiler metadata preflight failed before mkdir: ' + str(argv[0]))
    return result

walk_inputs(tc / 'include/newlib', 'newlib headers')
cc1 = None
support_install_relative = None
for compiler, flags in (
    (Path('/usr/bin/cc'), []),
    (tc / 'bin/arm-none-eabi-gcc', ['-mcpu=cortex-m3', '-mthumb',
      '-mfloat-abi=soft', '-ffreestanding', '-isystem', str(tc / 'include/newlib')]),
):
    for name in ('cc1', 'as', 'ld', 'collect2'):
        value = probe([compiler, '-print-prog-name=' + name]).stdout.strip()
        program = Path(value) if Path(value).is_absolute() else None
        if program is None:
            found = shutil.which(value, path=clean['PATH'])
            program = Path(found) if found else None
        if program is not None and program.is_file():
            required(program, 'compiler support ' + name)
            if compiler.name == 'arm-none-eabi-gcc' and name == 'cc1':
                cc1 = program.resolve()
    search = probe([compiler, '-print-search-dirs']).stdout
    for line in search.splitlines():
        if ': ' not in line:
            continue
        label, values = line.split(': ', 1)
        for value in values.lstrip('=').split(os.pathsep):
            if value and Path(value).is_absolute():
                protect(Path(value), 'compiler search directory')
        if compiler.name == 'arm-none-eabi-gcc' and label == 'install':
            # Use GCC's lexical installation path relative to the actual binary,
            # without assuming its resolved cc1 is beneath the advertised proxy.
            support_install_relative = Path(os.path.relpath(
                values.strip(), compiler.resolve().parent))
    includes = probe([compiler, *flags, '-E', '-x', 'c', '-v', '-'], stdin='').stderr
    active = False
    for line in includes.splitlines():
        if '#include <...> search starts here:' in line:
            active = True
            continue
        if 'End of search list.' in line:
            active = False
        if active:
            directory = Path(line.strip())
            if directory.is_absolute() and directory.is_dir():
                walk_inputs(directory, 'compiler headers')
if cc1 is None or support_install_relative is None:
    p.error('ARM cc1/install metadata missing before mkdir')
future_root = out / 'internal-layout'
future_link = (future_root / 'bin' / support_install_relative / 'cc1').resolve()
if not future_link.is_relative_to(future_root):
    p.error('GCC installation is not relocatable into the fixture layout; no output created')


def hashes(root):
    return {str(f): digest(f) for f in walk_inputs(root, 'source/evidence', forbid_aliases=True)}

before = hashes(repo)
if a.preflight_only:
    print('PASS: companion preflight only; no output created')
    raise SystemExit(0)
out.mkdir(parents=True, exist_ok=False)
(out / 'preflight.json').write_text(json.dumps(dict(status='PASS',
    protected_backing_directories=protected, queries=queries,
    source_sha256=before, compiler_cc1=str(cc1),
    support_install_relative=str(support_install_relative)), indent=2) + '\n')
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
shutil.copy2(cc1, internal_real / 'cc1')
link = (internal / 'bin' / support_install_relative / 'cc1').resolve()
link.parent.mkdir(parents=True, exist_ok=True); link.symlink_to(internal_real / 'cc1')
cases.append(('compiler-support-alias', wrapper, internal, internal_real / 'must-not-exist/new'))
# A directory alias plus a file symlink has a distinct resolved invocation parent.
nested_script_real = out / 'nested-script-real'; nested_script_real.mkdir()
(nested_script_real / 'reproduce.py').symlink_to(wrapper)
nested_script_alias = out / 'nested-script-alias'
nested_script_alias.symlink_to(nested_script_real, target_is_directory=True)
nested_tools, nested_bin = out / 'nested-tools', out / 'nested-tool-link-parent'
nested_tools.mkdir(); nested_bin.mkdir()
(nested_tools / 'bin').symlink_to(nested_bin, target_is_directory=True)
for name in ('gcc', 'size', 'nm', 'objdump'):
    tool = 'arm-none-eabi-' + name
    (nested_bin / tool).symlink_to(real / tool)
for name in ('lib', 'include'):
    (nested_tools / name).symlink_to(tc / name, target_is_directory=True)
cases.extend([
    ('layered-script-invocation-parent', nested_script_alias / 'reproduce.py', proxy,
     nested_script_real / 'must-not-exist/new'),
    ('layered-tool-link-parent', wrapper, nested_tools, nested_bin / 'must-not-exist/new'),
])
# Current file-to-file chain regression: three distinct file parents.
file_script_entry, file_script_mid = out / 'file-script-entry', out / 'file-script-mid'
file_script_entry.mkdir(); file_script_mid.mkdir()
(file_script_mid / 'reproduce.py').symlink_to(wrapper)
(file_script_entry / 'reproduce.py').symlink_to(file_script_mid / 'reproduce.py')
file_tool_proxy, file_tool_mid = out / 'file-tool-proxy', out / 'file-tool-mid'
(file_tool_proxy / 'bin').mkdir(parents=True); file_tool_mid.mkdir()
for name in ('gcc', 'size', 'nm', 'objdump'):
    tool = 'arm-none-eabi-' + name
    (file_tool_mid / tool).symlink_to(real / tool)
    (file_tool_proxy / 'bin' / tool).symlink_to(file_tool_mid / tool)
for name in ('lib', 'include'):
    (file_tool_proxy / name).symlink_to(tc / name, target_is_directory=True)
cases.extend([
    ('file-file-script-intermediate-parent', file_script_entry / 'reproduce.py', proxy,
     file_script_mid / 'must-not-exist/new'),
    ('file-file-tool-intermediate-parent', wrapper, file_tool_proxy,
     file_tool_mid / 'must-not-exist/new'),
])
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
# Audit the companion's own boundary using these same disposable dependencies.
self_script = Path(__file__).absolute()
self_alias_dir = out / 'companion-script-alias'; self_alias_dir.mkdir()
self_alias = self_alias_dir / 'test_reproduce_paths.py'; self_alias.symlink_to(self_script.resolve())
evidence_copy = out / 'aliased-evidence-checkout'; shutil.copytree(relocated, evidence_copy)
evidence_backing = out / 'evidence-backing'; evidence_backing.mkdir()
evidence_path = evidence_copy / 'tests/adc-commit/evidence/current/selection.json'
evidence_path.rename(evidence_backing / 'selection.json')
evidence_path.symlink_to(evidence_backing / 'selection.json')
missing_source = out / 'missing-source-checkout'; shutil.copytree(relocated, missing_source)
missing_file = missing_source / 'tests/platform/test_adc_timer_dma.c'
missing_file.rename(missing_file.with_name('preserved-test_adc_timer_dma.c'))
missing_script = out / 'missing-script-checkout'; shutil.copytree(relocated, missing_script)
missing_file = missing_script / 'tests/adc-commit/critic/run_extra.py'
missing_file.rename(missing_file.with_name('preserved-run_extra.py'))
missing_header = out / 'missing-header-toolchain'
(missing_header / 'include/newlib').mkdir(parents=True)
(missing_header / 'bin').symlink_to(proxy / 'bin', target_is_directory=True)
(missing_header / 'lib').symlink_to(tc / 'lib', target_is_directory=True)
companion_cases = [
    ('existing', self_script, repo, proxy, existing_output),
    ('checkout-root', self_script, repo, proxy, repo / 'must-not-exist/new'),
    ('checkout-ancestor', self_script, repo, proxy, repo.parent),
    ('tool-root', self_script, repo, proxy, proxy / 'must-not-exist/new'),
    ('tool-backing-parent', self_script, repo, proxy, real / 'must-not-exist/new'),
    ('header-directory-alias', self_script, repo, header_proxy, header_real / 'must-not-exist/new'),
    ('header-file-alias', self_script, repo, file_proxy, file_real / 'must-not-exist/new'),
    ('script-alias', self_alias, repo, proxy, self_alias_dir / 'must-not-exist/new'),
    ('source-backing-parent', self_script, alias_copy, proxy, input_backing / 'must-not-exist/new'),
    ('evidence-backing-parent', self_script, evidence_copy, proxy, evidence_backing / 'must-not-exist/new'),
    ('missing-source', self_script, missing_source, proxy, out / 'missing-source-output'),
    ('missing-script', self_script, missing_script, proxy, out / 'missing-script-output'),
    ('missing-header', self_script, repo, missing_header, out / 'missing-header-output'),
    ('compiler-support-backing', self_script, repo, internal, internal_real / 'must-not-exist/new'),
]
nested_companion_real = out / 'nested-companion-real'; nested_companion_real.mkdir()
(nested_companion_real / 'test_reproduce_paths.py').symlink_to(self_script.resolve())
nested_companion_alias = out / 'nested-companion-alias'
nested_companion_alias.symlink_to(nested_companion_real, target_is_directory=True)
companion_cases.extend([
    ('layered-script-invocation-parent', nested_companion_alias / 'test_reproduce_paths.py',
     repo, proxy, nested_companion_real / 'must-not-exist/new'),
    ('layered-tool-link-parent', self_script, repo, nested_tools,
     nested_bin / 'must-not-exist/new'),
])
file_self_entry, file_self_mid = out / 'file-companion-entry', out / 'file-companion-mid'
file_self_entry.mkdir(); file_self_mid.mkdir()
(file_self_mid / 'test_reproduce_paths.py').symlink_to(self_script.resolve())
(file_self_entry / 'test_reproduce_paths.py').symlink_to(file_self_mid / 'test_reproduce_paths.py')
companion_cases.extend([
    ('file-file-script-intermediate-parent', file_self_entry / 'test_reproduce_paths.py',
     repo, proxy, file_self_mid / 'must-not-exist/new'),
    ('file-file-tool-intermediate-parent', self_script, repo, file_tool_proxy,
     file_tool_mid / 'must-not-exist/new'),
])
companion_records = []
for name, script, source, toolchain, destination in companion_cases:
    existed, parent_existed = destination.exists(), destination.parent.exists()
    argv = [sys.executable, '-I', '-B', str(script), '--preflight-only',
            '--checkout', str(source), '--toolchain', str(toolchain), '--out', str(destination)]
    result = subprocess.run(argv, env=clean, text=True, capture_output=True)
    record = dict(case=name, argv=argv, exit=result.returncode, stdout=result.stdout,
                  stderr=result.stderr, output_created=not existed and destination.exists(),
                  parent_created=not parent_existed and destination.parent.exists())
    companion_records.append(record)
    if result.returncode != 2 or record['output_created'] or record['parent_created']:
        raise RuntimeError('companion refusal regression failed: ' + json.dumps(record))
valid_preflight_out = out / 'positive-companion-preflight'
argv = [sys.executable, '-I', '-B', str(self_script), '--preflight-only',
        '--checkout', str(repo), '--toolchain', str(file_tool_proxy), '--out', str(valid_preflight_out)]
preflight = subprocess.run(argv, env=clean, text=True, capture_output=True)
if preflight.returncode or valid_preflight_out.exists():
    raise RuntimeError('valid aliased-tool companion preflight failed: ' + preflight.stderr)
companion_positive_preflight = dict(argv=argv, exit=preflight.returncode,
    stdout=preflight.stdout, stderr=preflight.stderr, output_created=False)
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
        '--toolchain', str(file_tool_proxy), '--out', str(positive)]
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
summary = dict(status='PASS', invocation=sys.argv, input_toolchain=str(tc), preflight_sha256=hashlib.sha256((out / 'preflight.json').read_bytes()).hexdigest(), source_file_count=len(before), test_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), negative_cases=records, companion_negative_cases=companion_records, companion_positive_preflight=companion_positive_preflight, positive=dict(argv=argv,
    exit=result.returncode, receipt=str(positive / 'receipt.json'),
    receipt_sha256=hashlib.sha256((positive / 'receipt.json').read_bytes()).hexdigest(),
    dirty_environment=dirty, actual_child_environment=receipt['child_environment']),
    original_checkout_unchanged=True, scope='Disposable fixture copies; no production edits')
(out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print('PASS: main 17 refusals; companion 18 refusals and chained-tool preflight; full relocated run.')

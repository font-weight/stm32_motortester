"""Pinned actual recording-adapter/native provenance probe; no installation/device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

MANIFEST = '39235596e9a791b113d87788ded0e2c3163d524e8215208c076b962e293dac90'
HERE = Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require(condition, message):
    if not condition:
        raise ValueError(message)

def canonical(value):
    p = Path(value)
    require(p.is_absolute() and str(p) == os.path.normpath(value) and p.resolve() == p,
            'Use normalized absolute paths without symlink aliases')
    return p

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a-root', required=True)
    parser.add_argument('--host-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--cc', default='/usr/bin/gcc')
    args = parser.parse_args()
    require(sys.flags.isolated and sys.dont_write_bytecode and not sys.flags.optimize, 'Use -I -B without -O')
    roots = {'a': canonical(args.a_root), 'b': canonical(args.host_root), 'probe': HERE}
    out = canonical(args.output)
    require(not out.exists() and out.parent.is_dir(), 'Output must be new with an existing parent')
    for root in roots.values():
        require(root.is_dir() and not (out == root or out.is_relative_to(root) or root.is_relative_to(out)),
                'Output and inputs must not overlap')
    require(sha(HERE / 'inputs.json') == MANIFEST, 'Input manifest mismatch')
    manifest = json.loads((HERE / 'inputs.json').read_bytes())
    original = {}
    for group, root in roots.items():
        for entry in manifest[group]:
            p = root / entry['path']
            require(p.resolve() == p and p.is_file(), 'Missing/aliased input: ' + str(p))
            require(p.stat().st_size == entry['bytes'] and sha(p) == entry['sha256'], 'Input bytes mismatch: ' + str(p))
            original[str(p)] = entry['sha256']
    original[str(HERE / 'reproduce.py')] = sha(HERE / 'reproduce.py')
    original[str(HERE / 'inputs.json')] = MANIFEST
    cc = Path(args.cc).resolve()
    require(Path(args.cc).is_absolute() and cc.is_file(), 'Compiler must be an existing absolute path')
    out.mkdir()
    receipt = dict(status='RUNNING', a_commit=manifest['a_commit'], b_commit=manifest['b_commit'],
                   inputs=original, compiler=str(cc), compiler_sha256=sha(cc), python_version=sys.version,
                   commands=[], physical_verified=False, full_runtime=False, leak_detection=False)
    def save():
        (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    save()
    shutil.copytree(HERE, out / 'author-source')
    def command(argv, name):
        row = dict(argv=list(map(str, argv)), cwd=str(out), timeout_seconds=180)
        receipt['commands'].append(row)
        save()
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1')
        with (out / (name + '.stdout')).open('wb') as stdout, (out / (name + '.stderr')).open('wb') as stderr:
            process = subprocess.Popen(row['argv'], cwd=out, env=env, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            try:
                row['exit'] = process.wait(timeout=180)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                row['interrupted'] = True
                save()
                raise
            if row['exit']:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        save()
        require(row['exit'] == 0, name + ' failed; diagnostics retained')
    try:
        staged = {}
        for group in ('a', 'b'):
            stage = out / ('source-' + group)
            stage.mkdir()
            for entry in manifest[group]:
                p = stage / entry['path']
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes((roots[group] / entry['path']).read_bytes())
                require(sha(p) == entry['sha256'], 'Staging drift')
                staged[str(p)] = entry['sha256']
        receipt['staged_inputs'] = staged
        a, b = out / 'source-a', out / 'source-b'
        command([cc, '--version'], 'compiler-version')
        production = [a / row['path'] for row in manifest['a'] if row['path'].startswith('firmware/') and row['path'].endswith('.c')]
        for mode in ('strict', 'sanitize'):
            flags = ['-g', '-O1', '-fno-lto', '-Wall', '-Wextra', '-fno-omit-frame-pointer', '-std=c11',
                     '-Wpedantic', '-Wconversion', '-Wsign-conversion', '-Werror']
            if mode == 'sanitize':
                flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
            binary = out / (mode + '-bridge')
            command([cc, *flags, '-I' + str(a), a / 'tests/recording-session-reproducer/native_bridge.c', *production, '-o', binary], mode + '-compile')
            command([sys.executable, '-I', '-B', HERE / 'probe.py', '--host-root', b,
                     '--bridge', binary, '--ipc-helper', a / 'tests/recording-session-reproducer/adapter.py', '--out', out / mode], mode + '-probe')
            command([sys.executable, '-O', '-I', '-B', HERE / 'probe.py', '--host-root', b,
                     '--bridge', binary, '--ipc-helper', a / 'tests/recording-session-reproducer/adapter.py', '--out', out / (mode + '-optimized')], mode + '-optimized-probe')
        for variant in ('strict', 'strict-optimized', 'sanitize', 'sanitize-optimized'):
            command([sys.executable, '-I', '-B', HERE / 'readback.py', '--host-root', b,
                     '--out', out / variant], variant + '-readback')
        parity = ['host.wire', 'device.wire']
        cases = sorted(p.name for p in (out / 'strict').iterdir() if p.is_dir())
        for case in cases:
            for relative in parity:
                expected = (out / 'strict' / case / relative).read_bytes()
                for mode in ('strict-optimized', 'sanitize', 'sanitize-optimized'):
                    require((out / mode / case / relative).read_bytes() == expected,
                            'Compiler/Python-mode wire parity failed: ' + case)
        receipt['byte_identical_wire_cases'] = cases
        require(all(sha(Path(p)) == value for p, value in {**original, **staged}.items()), 'Input drift')
        receipt['status'] = 'PASS'
    except BaseException as error:
        receipt.update(status='FAILED', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        save()
    print(json.dumps({'status': receipt['status'], 'modes': 2, 'cases_per_mode': len(cases)}))

if __name__ == '__main__':
    main()

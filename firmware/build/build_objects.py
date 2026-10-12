#!/usr/bin/env python3
"""Build the pinned firmware object composition offline; never create a board ELF."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

INPUT_SHA = '3cb2115e52ec22ac8b1246d03d63776e3f89ac1743193e4e3d0a1a574f0596f9'
TOOL_SHA = '22f0c2204b8b381a26af3b910851b43b04f34549b6677cdb5b76f304c4e37b43'

def require(ok, message):
    if not ok:
        raise ValueError(message)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

# Canonical-only contract adapted from the qualified BOOT001 runner.
# Stable, cooperative filesystem required; these checks are not a TOCTOU sandbox.
def strict_path(value, kind='file', executable=False):
    raw = os.fspath(value)
    require(not any(c in raw for c in "\n\r:#$\\\"'"), 'unsupported path character: ' + raw)
    p = Path(raw)
    require(p.is_absolute() and os.path.normpath(raw) == raw,
            'absolute normalized path required: ' + raw)
    for part in [*reversed(p.parents), p]:
        require(not part.is_symlink(), 'symlink component forbidden: ' + str(part))
    require(p.resolve(strict=True) == p, 'canonical backing path required: ' + raw)
    st = p.stat()
    require(stat.S_ISREG(st.st_mode) if kind == 'file' else stat.S_ISDIR(st.st_mode),
            'wrong input type: ' + raw)
    if executable:
        require(os.access(p, os.X_OK), 'not executable: ' + raw)
    return p

def inside(p, q):
    return p == q or q in p.parents

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    script = strict_path(__file__)
    root = strict_path(script.parents[2], 'dir')
    tc = strict_path(args.toolchain, 'dir')
    require(not any(c in args.out for c in "\n\r:#$\\\"'"), 'unsupported output path character')
    out = Path(args.out)
    require(out.is_absolute() and os.path.normpath(args.out) == args.out,
            'output must be absolute normalized path')
    require(not out.exists() and not out.is_symlink(), 'output must not exist')
    strict_path(out.parent, 'dir')
    # Resolve only the active interpreter supplied by the launcher; no file aliases
    # are accepted in source, toolchain, output, manifests or dependency inputs.
    python = strict_path(Path(sys.executable).resolve(), executable=True)
    patch = strict_path('/usr/bin/patch', executable=True)
    protected = [root, tc, script.parent, python.parent,
                 strict_path(Path(sys.base_prefix).resolve(), 'dir'), patch.parent]
    for p in protected:
        require(not inside(out, p) and not inside(p, out),
                'output overlaps protected input directory: ' + str(p))
    inputs = {str(script): digest(script), str(python): digest(python), str(patch): digest(patch)}
    def manifest(name, expected):
        p = strict_path(script.parent / name)
        require(p.stat().st_size < 1048576, 'oversize manifest')
        require(digest(p) == expected, 'manifest hash mismatch: ' + name)
        inputs[str(p)] = expected
        return json.loads(p.read_text())
    source = manifest('inputs.json', INPUT_SHA)
    tool = manifest('toolchain.json', TOOL_SHA)
    def pin_tree(base, rows):
        require(len(rows) <= 500, 'oversize input set')
        for rel, expected in rows.items():
            r = Path(rel)
            require(not r.is_absolute() and '..' not in r.parts, 'invalid manifest path')
            p = strict_path(base / r)
            require(p.stat().st_size <= 70 * 1024 * 1024, 'oversize input file')
            require(digest(p) == expected, 'input hash mismatch: ' + rel)
            inputs[str(p)] = expected
    pin_tree(root, source['files'])
    pin_tree(tc, tool['files'])
    require(len(source['components']) == 32, 'unexpected source set')
    tools = {n: strict_path(tc / 'bin' / ('arm-none-eabi-' + n), executable=True)
             for n in ('gcc', 'as', 'ld', 'nm', 'size', 'readelf')}
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
           'PYTHONDONTWRITEBYTECODE': '1'}
    # Read-only tool probes before output creation; selected support tools must
    # belong to the hash-pinned toolchain. Host shared libraries are not hermetic.
    probes = []
    for executable in [*tools.values(), python, patch]:
        argv = [str(executable), '--version']
        p = subprocess.run(argv, env=env, cwd=root, capture_output=True, text=True, timeout=30)
        probes.append({'argv': argv, 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        require(p.returncode == 0, 'version probe failed')
    for name, selection in [('cc1', []), ('as', []),
                            ('as', ['-B' + str(tc / 'bin/arm-none-eabi-')])]:
        argv = [str(tools['gcc']), *selection, '-print-prog-name=' + name]
        p = subprocess.run(argv, env=env, cwd=root, capture_output=True, text=True, timeout=30)
        probes.append({'argv': argv, 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        require(p.returncode == 0, 'tool probe failed')
        selected = Path(os.path.abspath(os.path.normpath(p.stdout.strip())))
        strict_path(selected, executable=True)
        require(str(selected) in inputs, 'unbound compiler support tool: ' + name)
        if selection:
            require(selected == tools['as'], 'unexpected reset assembler selection')
    out.mkdir()
    (out / 'tmp').mkdir()
    env['TMPDIR'] = str(out / 'tmp')
    result = {'status': 'RUNNING', 'scope': 'Relocatable objects only; no board ELF or acceptance',
              'inputs_before': inputs, 'probes': probes, 'commands': [], 'objects': []}
    def run(label, argv):
        argv = list(map(str, argv))
        try:
            p = subprocess.run(argv, env=env, cwd=out, capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as exc:
            def text(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
            (out / (label + '.stdout')).write_text(text(getattr(exc, 'stdout', None)))
            (out / (label + '.stderr')).write_text(text(getattr(exc, 'stderr', None)))
            result['commands'].append({'label': label, 'argv': argv, 'exit': None,
                                       'exception': type(exc).__name__, 'error': str(exc)})
            raise
        (out / (label + '.stdout')).write_text(p.stdout)
        (out / (label + '.stderr')).write_text(p.stderr)
        result['commands'].append({'label': label, 'argv': argv, 'exit': p.returncode})
        require(p.returncode == 0, label + ' failed; see preserved stderr')
        return p.stdout
    try:
        third = root / 'firmware/third_party/usb'
        helpers = third / 'patches'
        vendor = third / 'vendor'
        py = [python, '-I', '-B']
        for folder in ('stage1-core', 'stage1-fsdev', 'patched-core', 'patched-fsdev'):
            (out / folder).mkdir()
        core = out / 'patched-core/usbd.c'
        fsdev = out / 'patched-fsdev/dcd_stm32_fsdev.c'
        run('patch-setup', [*py, helpers / 'apply_setup_patch.py', vendor / 'tinyusb/src/device/usbd.c', out / 'stage1-core/usbd.c'])
        run('patch-reset', [*py, helpers / 'apply_f103_reset_patch.py', vendor / 'tinyusb/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c', out / 'stage1-fsdev/dcd_stm32_fsdev.c'])
        run('patch-core', [*py, helpers / 'apply_usb_bridge_patch.py', 'core', out / 'stage1-core/usbd.c', core])
        run('patch-fsdev', [*py, helpers / 'apply_usb_bridge_patch.py', 'fsdev', out / 'stage1-fsdev/dcd_stm32_fsdev.c', fsdev])
        patched = {'@patched/usbd.c': core, '@patched/dcd_stm32_fsdev.c': fsdev}
        generated = {str(p): digest(p) for p in patched.values()}
        includes = ['-I' + str(root / 'firmware' / part)
                    for part in ('boot', 'platform', 'app', 'protocol', 'transport', 'acquisition')]
        for p in [tc / 'include/newlib', *(vendor / part for part in
                  ('tinyusb/src', 'cmsis-device-f1/Include', 'cmsis-core/CMSIS/Core/Include', 'tinyusb/src/portable/st/stm32_fsdev'))]:
            includes += ['-isystem', str(p)]
        objects = []
        dependencies = {}
        for row in source['components']:
            src = patched.get(row['source'], root / row['source'])
            obj = out / row['object']
            dep = obj.with_suffix('.d')
            run('compile-' + obj.stem, [tools['gcc'], *row['flags'], *includes,
                                      '-MD', '-MF', dep, '-c', src, '-o', obj])
            require(digest(obj) == row['expected_object_sha256'], 'object differs: ' + obj.name)
            for value in shlex.split(dep.read_text().replace('\\\n', ' '))[1:]:
                # GCC dependency spelling may contain trusted relative includes.
                # Reject aliases before normalizing those lexical dot components.
                raw = Path(value)
                for part in [*reversed(raw.parents), raw]:
                    require(not part.is_symlink(), 'symlink dependency forbidden')
                p = strict_path(os.path.normpath(value))
                expected = inputs.get(str(p), generated.get(str(p)))
                require(expected is not None and digest(p) == expected,
                        'unbound compiler dependency: ' + str(p))
                require(p.name != 'tusb_config.h' or p == root / 'firmware/boot/tusb_config.h',
                        'wrong application config')
                dependencies[str(p)] = expected
            objects.append(obj)
            result['objects'].append({'file': obj.name, 'sha256': digest(obj)})
        reset = out / 'reset.o'
        reset_flags = ['-B' + str(tc / 'bin/arm-none-eabi-'), '-mcpu=cortex-m3', '-mthumb',
                       '-mfloat-abi=soft', '-ffreestanding', '-fno-common', '-fno-short-enums',
                       '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-fno-stack-protector',
                       '-fno-lto', '-fno-pic', '-fno-pie', '-ffunction-sections', '-fdata-sections',
                       '-g0', '-Os', '-Wall', '-Wextra', '-Werror']
        run('compile-reset', [tools['gcc'], *reset_flags, '-c', root / 'firmware/boot/mt_reset_stm32f103c8.S', '-o', reset])
        require(digest(reset) == source['reset_object_sha256'], 'reset object differs')
        objects.append(reset)
        result['objects'].append({'file': reset.name, 'sha256': digest(reset)})
        checker = root / 'tests/boot-link/tools/check_boot_image.py'
        run('boot-input-audit', [*py, checker, 'inputs', '--readelf', tools['readelf'],
                              '--reset-object', reset, '--vector-map', root / 'tests/boot-link/vector_map.json', *objects])
        combined = out / 'combined.o'
        run('link-relocatable', [tools['ld'], '--fatal-warnings', '-r', '-Map=' + str(out / 'combined.map'), '-o', combined, *objects])
        undefined = run('undefined', [tools['nm'], '-u', combined])
        require({line.split()[-1] for line in undefined.splitlines()} ==
                {'mt_board', '__StackTop', '__bss_start', '__bss_end', '__data_start', '__data_end', '__data_load'},
                'unexpected unresolved symbols')
        result.update(status='PASS_RELOCATABLE_ONLY', dependencies=dependencies,
                      combined_sha256=digest(combined), undefined=undefined,
                      size=run('size', [tools['size'], combined]))
    except Exception as exc:
        result.update(status='FAILED', error=str(exc))
        raise
    finally:
        after, errors = {}, {}
        for name in inputs:
            try:
                after[name] = digest(Path(name))
            except OSError as exc:
                after[name] = None
                errors[name] = str(exc)
        result['inputs_after'] = after
        result['postcheck_errors'] = errors
        result['inputs_unchanged'] = after == inputs
        if after != inputs:
            result['status'] = 'FAILED'
            result.setdefault('error', 'input drift')
            result['changed_inputs'] = [name for name in inputs if after[name] != inputs[name]]
        (out / 'RESULTS.json').write_text(json.dumps(result, indent=2) + '\n')
    require(result['status'] == 'PASS_RELOCATABLE_ONLY', 'postcheck failed')
    print(json.dumps({'status': result['status'], 'objects': len(result['objects']), 'output': str(out)}))

if __name__ == '__main__':
    main()

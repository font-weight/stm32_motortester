#!/usr/bin/env python3
"""Fresh-output native USB bridge tests with before/after input preservation."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_output_path(output, source_roots):
    """Resolve symlinks before any creation or generated-source write."""
    output = Path(output).resolve()
    for source in source_roots:
        protected = Path(source).resolve()
        if output == protected or protected in output.parents:
            raise ValueError(f'refusing output inside source/input tree: {protected}')
    return output


def snapshot_sources(inventory):
    result = {}
    for path in sorted({Path(p).resolve() for p in inventory()}):
        try:
            result[str(path)] = sha(path)
        except OSError as error:
            result[str(path)] = {'read_error': type(error).__name__, 'message': str(error)}
    return result


def require_unchanged(before, after):
    if before != after:
        raise RuntimeError('source/input files changed during execution')


def guarded_run(output, source_roots, inventory, work, metadata=None):
    """Exclusive output creation; capture input hashes even when work fails."""
    before = snapshot_sources(inventory)
    record = dict(metadata or {})
    record.update(inputs=before, commands=[], results=[], result='FAIL', inputs_unchanged=False)
    out = None
    error = None
    try:
        resolved = validate_output_path(output, source_roots)
        resolved.mkdir(parents=True, exist_ok=False)
        out = resolved
        (out / 'inputs-before.json').write_text(json.dumps(before, indent=2) + '\n')
        if any(not isinstance(value, str) for value in before.values()):
            raise RuntimeError('an input could not be hashed before execution')
        work(out, record)
        record['result'] = 'PASS'
    except BaseException as problem:
        error = problem
        record['error'] = {'type': type(problem).__name__, 'message': str(problem)}
    finally:
        after = snapshot_sources(inventory)
        record['inputs_after'] = after
        record['inputs_unchanged'] = before == after
        try:
            require_unchanged(before, after)
        except RuntimeError as changed:
            record['result'] = 'FAIL'
            record['preservation_error'] = str(changed)
            if error is None:
                error = changed
        if out is not None:
            (out / 'inputs-after.json').write_text(json.dumps(after, indent=2) + '\n')
            (out / 'receipt.json').write_text(json.dumps(record, indent=2) + '\n')
        elif error is not None:
            # Invalid/existing destination must not receive any writes. Still
            # compare inputs after the failed attempt and expose the verdict.
            print(json.dumps({'output_rejected': str(output),
                              'inputs_unchanged': before == after,
                              'inputs_before': before, 'inputs_after': after,
                              'error': record.get('error')}), file=sys.stderr)
    if error is not None:
        raise error
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True,
                        help='new, nonexistent output directory outside every source tree')
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--integration', action='store_true')
    parser.add_argument('--adapter', action='store_true')
    args = parser.parse_args(argv)
    args.integration = args.integration or args.adapter
    bridge = Path(os.getenv('USB_BRIDGE_ROOT', str(HERE.parent / 'usb-bridge-draft'))).resolve()
    epoch = Path(os.getenv('USB_EPOCH_ROOT', str(HERE.parent / 'usb-epoch-draft'))).resolve()
    dep = Path(os.getenv('USB_DEPENDENCIES', str(HERE.parent / 'usb-dependencies'))).resolve()
    cdc = Path(os.getenv('USB_CDC_TESTS', str(HERE.parent / 'usb-cdc-tests'))).resolve()
    adapter = Path(os.getenv('USB_ADAPTER_ROOT', str(HERE.parent / 'usb-adapter-draft'))).resolve()
    source_roots = [HERE, bridge, epoch, dep, cdc] + ([adapter] if args.adapter else [])
    upstream = [dep / 'vendor/tinyusb/src/common/tusb_fifo.c']
    project = [bridge / 'firmware/transport/mt_usb_bridge.c',
               epoch / 'firmware/transport/mt_usb_epoch.c',
               HERE / ('test_integration.c' if args.integration else 'test_shims.c')]
    explicit = [HERE / 'run.py', HERE / 'instrument_fake.py', HERE / 'test_shims.c',
                HERE / 'test_integration.c', HERE / 'test_integration.h',
                HERE / 'test_runner.py', dep / 'source-manifest.json',
                dep / 'SHA256SUMS.sources', cdc / 'config-f1/tusb_config.h']
    trees = [bridge / 'firmware', epoch / 'firmware', dep / 'vendor']
    if args.integration:
        upstream += [dep / 'vendor/tinyusb/src/tusb.c', bridge / 'build/usbd.c',
                     dep / 'vendor/tinyusb/src/class/cdc/cdc_device.c']
        explicit += [bridge / 'build/dcd_stm32_fsdev.c', cdc / 'fake_dcd.c', cdc / 'fake_dcd.h']
    if args.adapter:
        project += [bridge / 'firmware/transport/mt_usb_cdc.c']
        trees += [adapter / 'firmware']
        for name in ['acquisition/acquisition', 'app/mt_capture_link', 'app/mt_control',
                     'app/mt_control_rx', 'protocol/mt_command', 'protocol/mt_session',
                     'protocol/mt_stream', 'protocol/mt_wire', 'transport/mt_tx']:
            project += [adapter / ('firmware/' + name + '.c')]
    explicit += upstream + project

    def inventory():
        paths = list(explicit)
        for tree in trees:
            paths += [p for p in tree.rglob('*') if p.is_file()]
        return paths

    common = ['-g', '-O1', '-fno-lto', '-Wall', '-Wextra', '-fno-omit-frame-pointer']
    if args.sanitize:
        common += ['-fsanitize=address,undefined']
    includes = ['-DSTM32F103xB', '-DCFG_MT_USB_BRIDGE=1', '-I' + str(cdc / 'config-f1'),
                '-I' + str(HERE), '-I' + str(bridge / 'firmware/transport'),
                '-I' + str(epoch / 'firmware/transport'), '-isystem', str(dep / 'vendor/tinyusb/src')]
    if args.integration:
        includes += ['-I' + str(cdc)]
    if args.adapter:
        includes += ['-DTEST_ADAPTER=1', '-I' + str(adapter / 'firmware/transport')]
    env = os.environ.copy()
    if args.sanitize:
        env.update(ASAN_OPTIONS='detect_leaks=0:halt_on_error=1',
                   UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    compiler = os.getenv('CC', 'gcc')

    def work(out, record):
        with (out / 'commands.log').open('w') as log:
            def run(command):
                item = {'argv': command}
                record['commands'].append(item)
                log.write('+ ' + ' '.join(command) + '\n')
                log.flush()
                response = subprocess.run(command, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, env=env)
                item.update(exit_status=response.returncode, output=response.stdout)
                log.write(response.stdout)
                log.flush()
                print(response.stdout, end='')
                if response.returncode:
                    raise RuntimeError(f'command failed with status {response.returncode}: {command[0]}')
                return response.stdout

            run([compiler, '--version'])
            for line in (dep / 'SHA256SUMS.sources').read_text().splitlines():
                expected, name = line.split(None, 1)
                path = (dep / name.strip()).resolve()
                if sha(path) != expected:
                    raise RuntimeError(f'pristine dependency hash mismatch: {path}')
            actual_project = list(project)
            if args.integration:
                import instrument_fake
                instrument_fake.generate(cdc, out, bridge)
                generated = out / 'fake_dcd_instrumented.c'
                actual_project += [generated]
                record['generated_sha256'] = {str(generated): sha(generated)}
            objects = []
            for index, source in enumerate(upstream + actual_project):
                obj = out / f'{index}_{source.stem}.o'
                objects.append(str(obj))
                flags = ['-std=gnu11'] if source in upstream else ['-std=c11', '-pedantic-errors', '-Werror']
                run([compiler] + common + flags + includes + ['-c', str(source), '-o', str(obj)])
            exe = out / 'test'
            run([compiler] + common + objects + ['-o', str(exe)])
            output = run([str(exe)])
            (out / 'results.jsonl').write_text(output)
            record['results'] = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
            if not record['results'] or not all(item['pass'] for item in record['results']):
                raise RuntimeError('test output lacks successful assertion-backed scenarios')
            record['executable_sha256'] = sha(exe)

    record = guarded_run(args.out, source_roots, inventory, work,
                         {'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          'sanitizers': args.sanitize, 'lsan_claimed': False,
                          'scope': 'native fake PRIMASK and fake DCD only; not IRQ/hardware evidence',
                          'source_roots': [str(path) for path in source_roots]})
    print(f"PASS: {len(record['results'])} native scenarios; inputs unchanged; receipt {args.out.resolve() / 'receipt.json'}")
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as failure:
        print(f'FAIL: {failure}', file=sys.stderr)
        raise SystemExit(1)

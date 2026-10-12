#!/usr/bin/env python3
"""Path-isolation/input-preservation regressions using disposable synthetic inputs."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = run.validate_output_path(args.out, [run.HERE])
    out.mkdir(parents=True, exist_ok=False)
    source = out / 'synthetic-inputs'
    source.mkdir()
    fixture = source / 'input.c'
    fixture.write_text('original synthetic input\n')
    checks = []

    def inventory():
        return [fixture] + [p for p in source.rglob('*') if p.is_file()]

    def attempt(name, dest, work, exception):
        before = run.snapshot_sources(inventory)
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            try:
                run.guarded_run(dest, [source], inventory, work)
            except exception as error:
                message = str(error)
            else:
                raise AssertionError(f'{name}: expected {exception.__name__}')
        after = run.snapshot_sources(inventory)
        check = {'case': name, 'pass': True, 'expected_exception': exception.__name__,
                 'message': message, 'stderr': captured.getvalue(),
                 'before': before, 'after': after}
        checks.append(check)
        return check

    def forbidden_work(dest, record):
        raise AssertionError('work must not run for a rejected output path')

    attempt('source_equal_rejected_before_work', source, forbidden_work, ValueError)
    nested = source / 'new-output' / 'nested'
    attempt('source_nested_rejected_before_mkdir', nested, forbidden_work, ValueError)
    assert not (source / 'new-output').exists()
    alias = out / 'alias'
    alias.symlink_to(source, target_is_directory=True)
    attempt('source_symlink_alias_rejected', alias / 'must-not-exist', forbidden_work, ValueError)
    assert not (source / 'must-not-exist').exists()
    existing = out / 'existing-empty'
    existing.mkdir()
    attempt('existing_empty_output_rejected', existing, forbidden_work, FileExistsError)
    assert list(existing.iterdir()) == []
    (existing / 'marker').write_text('keep me')
    attempt('existing_populated_output_untouched', existing, forbidden_work, FileExistsError)
    assert (existing / 'marker').read_text() == 'keep me' and not (existing / 'receipt.json').exists()
    for check in checks:
        assert check['before'] == check['after']

    def fails(dest, record):
        (dest / 'partial-output').write_text('failed work has evidence')
        raise ValueError('intentional synthetic failure')

    dest = out / 'work-failed'
    attempt('failed_work_records_both_hashes', dest, fails, ValueError)
    receipt = json.loads((dest / 'receipt.json').read_text())
    assert receipt['result'] == 'FAIL' and receipt['inputs_unchanged']
    assert (dest / 'inputs-before.json').exists() and (dest / 'inputs-after.json').exists()

    def changed(dest, record):
        (dest / 'generated.txt').write_text('generated')
        fixture.write_text('changed synthetic input\n')

    dest = out / 'source-changed'
    attempt('source_change_rejected_after_success', dest, changed, RuntimeError)
    receipt = json.loads((dest / 'receipt.json').read_text())
    assert receipt['result'] == 'FAIL' and not receipt['inputs_unchanged']
    assert receipt['inputs'] != receipt['inputs_after'] and receipt['preservation_error']

    def changed_and_failed(dest, record):
        fixture.write_text('changed again, then failed\n')
        raise ValueError('original work error')

    dest = out / 'source-changed-and-failed'
    attempt('source_change_preserved_alongside_work_failure', dest, changed_and_failed, ValueError)
    receipt = json.loads((dest / 'receipt.json').read_text())
    assert not receipt['inputs_unchanged'] and receipt['error']['message'] == 'original work error'
    assert receipt['preservation_error']

    def removed(dest, record):
        fixture.unlink()

    dest = out / 'source-removed'
    attempt('deleted_source_records_missing_after_hash', dest, removed, RuntimeError)
    receipt = json.loads((dest / 'receipt.json').read_text())
    assert receipt['inputs_after'][str(fixture.resolve())]['read_error'] == 'FileNotFoundError'
    fixture.write_text('restored synthetic fixture for remaining tests\n')

    def added(dest, record):
        (source / 'new.h').write_text('new synthetic input\n')

    dest = out / 'source-added'
    attempt('new_source_detected_by_inventory_rescan', dest, added, RuntimeError)
    receipt = json.loads((dest / 'receipt.json').read_text())
    assert str((source / 'new.h').resolve()) not in receipt['inputs']
    assert str((source / 'new.h').resolve()) in receipt['inputs_after']

    def succeeds(dest, record):
        (dest / 'only-output').write_text('ok')

    receipt = run.guarded_run(out / 'valid-fresh', [source], inventory, succeeds)
    assert receipt['result'] == 'PASS' and receipt['inputs_unchanged']
    checks.append({'case': 'fresh_disjoint_output_succeeds', 'pass': True})

    # Exercise the actual command runner failure path without compiling or
    # changing any production inputs. /bin/false is a deliberate fake compiler.
    env = os.environ.copy()
    env['CC'] = '/bin/false'
    dest = out / 'actual-compiler-failure'
    response = subprocess.run([sys.executable, str(run.HERE / 'run.py'), '--out', str(dest)],
                              capture_output=True, text=True, env=env)
    assert response.returncode == 1
    receipt = json.loads((dest / 'receipt.json').read_text())
    assert receipt['inputs_unchanged'] and receipt['result'] == 'FAIL'
    assert receipt['commands'][0]['exit_status'] == 1
    checks.append({'case': 'actual_failed_compiler_preserves_inputs', 'pass': True,
                   'stdout': response.stdout, 'stderr': response.stderr})
    (out / 'tooling-regressions.json').write_text(json.dumps(checks, indent=2) + '\n')
    print(f'PASS: {len(checks)} runner tooling regressions; synthetic mutations only; {out}')


if __name__ == '__main__':
    main()

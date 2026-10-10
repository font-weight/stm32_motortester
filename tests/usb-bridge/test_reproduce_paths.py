#!/usr/bin/env python3
"""Packaging-only output isolation checks; never run firmware or alter inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--toolchain', required=True, type=Path)
    a = parser.parse_args()
    here = Path(__file__).resolve().parent
    root = here.parents[1]
    tc = a.toolchain.resolve()
    out = a.output.resolve()
    for source in [root, tc]:
        if out == source or source in out.parents:
            parser.error('test output must be outside source and toolchain')
    if out.exists() or a.output.is_symlink():
        parser.error('test output must not exist')
    def inventory():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(here.rglob('*')) if p.is_file()}
    before = inventory()
    out.mkdir(parents=True, exist_ok=False)
    records = []
    def attempt(label, target, compiler=tc, script=here/'reproduce.py'):
        argv = [sys.executable,str(script),'--output',str(target),'--toolchain',str(compiler)]
        r = subprocess.run(argv,capture_output=True,text=True,
                           env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        records.append({'case':label,'argv':argv,'exit_code':r.returncode,
                        'stdout':r.stdout,'stderr':r.stderr})
        assert r.returncode != 0,label+' unexpectedly accepted'
    alias = out/'checkout-alias'
    alias.symlink_to(root, target_is_directory=True)
    existing = out/'existing';existing.mkdir();(existing/'marker').write_text('keep\n')
    dangling = out/'dangling';dangling.symlink_to(out/'nonexistent')
    attempt('checkout-equal',root)
    attempt('checkout-nested',root/'usb005-must-not-create')
    attempt('checkout-symlink-alias',alias/'usb005-must-not-create')
    attempt('toolchain-equal',tc)
    attempt('toolchain-nested',tc/'usb005-must-not-create')
    attempt('existing-output',existing)
    attempt('dangling-output-symlink',dangling)
    attempt('missing-compiler',out/'missing-compiler-output',out/'missing-toolchain')
    # Synthetic incomplete checkout tests missing inputs without deleting real data.
    synthetic = out/'synthetic-checkout';test = synthetic/'tests/usb-bridge';test.mkdir(parents=True)
    shutil.copyfile(here/'reproduce.py',test/'reproduce.py')
    (test/'package-manifest.json').write_text(json.dumps({'files':[
        {'path':'firmware/absent.c','bytes':0,'sha256':hashlib.sha256(b'').hexdigest()}]}))
    (test/'inherited-inputs.json').write_text('{"files":[]}')
    attempt('missing-checkout-input',out/'missing-input-output',script=test/'reproduce.py')
    assert not (root/'usb005-must-not-create').exists()
    assert not (tc/'usb005-must-not-create').exists()
    assert not (out/'missing-compiler-output').exists()
    assert not (out/'missing-input-output').exists()
    assert not (out/'nonexistent').exists()
    assert list(existing.iterdir()) == [existing/'marker']
    assert (existing/'marker').read_text()=='keep\n'
    after = inventory()
    assert before==after
    result = {'result':'PASS','cases':records,'input_count':len(before),
              'inputs_unchanged':before==after,'scope':'packaging path validation only'}
    (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS:',len(records),'packaging path regressions; inputs unchanged')


if __name__=='__main__':
    main()

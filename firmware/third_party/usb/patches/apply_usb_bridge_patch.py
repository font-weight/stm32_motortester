#!/usr/bin/env python3
"""Apply one hash-pinned USB005 patch to a new build file; preserve inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind', choices=('core', 'fsdev'))
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / 'usb005-patches.json').read_text())['inputs'][a.kind]
    patch_path = here / manifest['patch']
    source, patch = a.source.read_bytes(), patch_path.read_bytes()
    for data, key in [(source, 'source_sha256'), (patch, 'patch_sha256')]:
        if sha(data) != manifest[key]:
            p.error(f'{key} differs from pinned input')
    if a.output.exists() or a.output.is_symlink():
        p.error('output exists; choose a new file')
    target = a.output.resolve()
    if target.is_relative_to(a.source.resolve().parent) or target.is_relative_to(here):
        p.error('output must be outside source and patch directories')
    with tempfile.TemporaryDirectory(prefix='usb005-patch-') as temporary:
        tmp = Path(temporary)
        (tmp / 'input.c').write_bytes(source)
        (tmp / 'change.patch').write_bytes(patch)
        command = ['patch', '--batch', '--fuzz=0', '--posix', '--output',
                   str(tmp / 'result.c'), str(tmp / 'input.c'), str(tmp / 'change.patch')]
        subprocess.run(command, check=True)
        result = (tmp / 'result.c').read_bytes()
        if sha(result) != manifest['result_sha256']:
            p.error('patched result differs from pinned output')
        if a.source.read_bytes() != source or patch_path.read_bytes() != patch:
            p.error('input changed during patch application')
        with a.output.open('xb') as stream:
            stream.write(result)
    print(json.dumps({'kind': a.kind, **manifest, 'inputs_unchanged': True}, sort_keys=True))


if __name__ == '__main__':
    main()

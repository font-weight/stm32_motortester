#!/usr/bin/env python3
"""Verify USB002 publication bytes and the unchanged historical evidence subset."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(root):
    scope = root / 'tests/usb-epoch'
    manifest = json.loads((scope / 'package-manifest.json').read_text())
    assert manifest['pr44_context_commit'] == '06178f31be79c3e7dbc5b46281a67a5c1d1da7f0'
    assert manifest['manifest_self_excluded'] is True
    names = set()
    for row in manifest['files']:
        name = row['path']
        assert name not in names and not Path(name).is_absolute() and '..' not in Path(name).parts, name
        names.add(name)
        path = root / name
        assert path.is_file() and not path.is_symlink(), name
        data = path.read_bytes()
        data.decode('utf-8')
        assert b'\0' not in data and len(data) == row['size_bytes'], name
        assert digest(data) == row['sha256'], name
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == row['git_blob_sha'], name
    actual = {str(p.relative_to(root)) for p in scope.rglob('*') if p.is_file()}
    actual |= {str(p.relative_to(root)) for p in (root/'firmware/transport').glob('*') if p.name in ['mt_usb_epoch.c','mt_usb_epoch.h','USB_EPOCH_README.md']}
    assert actual == names | {'tests/usb-epoch/package-manifest.json'}, 'Missing or untracked USB002 file'

    for row in json.loads((scope/'input-provenance.json').read_text())['files']:
        data = (root/row['path']).read_bytes()
        assert len(data) == row['bytes'] and digest(data) == row['sha256'], row['path']
    support = scope/'supporting'
    selection = json.loads((support/'text-selection.json').read_text())
    included = {row['path']:row for row in selection['included']}
    omitted = {row['path']:row for row in selection['omitted_generated_binary_outputs']}
    assert not (included.keys() & omitted.keys())
    for name, row in included.items():
        data = (support/name).read_bytes()
        assert len(data) == row['bytes'] and digest(data) == row['sha256'], name
    for line in (support/'SHA256SUMS').read_text().splitlines():
        expected, raw_name = line.split(maxsplit=1)
        name = raw_name.removeprefix('./')
        row = included.get(name) or omitted.get(name)
        assert row and row['sha256'] == expected, name
    for suffix in ['c', 'h']:
        assert (support/f'inputs/mt_usb_epoch.{suffix}').read_bytes() == (root/f'firmware/transport/mt_usb_epoch.{suffix}').read_bytes()
    for row in json.loads((scope/'critic/source-manifest.json').read_text()):
        data = (root/row['path']).read_bytes()
        assert len(data) == row['bytes'] and digest(data) == row['sha256'], row['path']
    print(f"PASS: {len(names)} USB002 text files (manifest excluded), exact source/critic bindings, {len(included)} original supporting texts; {len(omitted)} documented generated binary outputs omitted.")
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    verify(args.root.resolve())

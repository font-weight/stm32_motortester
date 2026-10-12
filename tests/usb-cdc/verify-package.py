#!/usr/bin/env python3
"""Verify exact text additions and the inherited firmware inputs used by USB-001."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(root):
    manifest = json.loads((root / 'tests/usb-cdc/package-manifest.json').read_text())
    assert manifest['base_commit'] == '06178f31be79c3e7dbc5b46281a67a5c1d1da7f0'
    for row in manifest['files'] + manifest['base_firmware_inputs']:
        path = root / row['path']
        assert path.is_file() and not path.is_symlink(), row['path']
        data = path.read_bytes()
        assert len(data) == row['size_bytes'], row['path']
        assert hashlib.sha256(data).hexdigest() == row['sha256'], row['path']
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == row['git_blob_sha'], row['path']
        data.decode('utf-8')
        assert b'\0' not in data, row['path']
    deps = root / 'firmware/third_party/usb'
    sources = json.loads((deps / 'source-manifest.json').read_text())['files']
    actual = {str(p.relative_to(deps)) for p in (deps / 'vendor').rglob('*') if p.is_file()}
    assert len(sources) == 35 and actual == {r['local_path'] for r in sources}
    for row in sources:
        data = (deps / row['local_path']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['sha256'], row['local_path']
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == row['git_blob_sha'], row['local_path']
    supporting = root / 'tests/usb-cdc/supporting'
    encodings = json.loads((supporting / 'text-encoding.json').read_text())
    decoded = {}
    for row in encodings['files']:
        data = bytes.fromhex((supporting / row['text_path']).read_text())
        assert len(data) == row['original_size_bytes'], row['original_path']
        assert hashlib.sha256(data).hexdigest() == row['original_sha256'], row['original_path']
        decoded[row['original_path']] = data
    assert len(decoded) == 12
    for line in (supporting / 'SHA256SUMS.harness').read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        data = decoded[path] if path in decoded else (supporting / path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, path
    matrix = json.loads((supporting / 'evidence/matrix.json').read_text())
    assert len(matrix['matrix']) == 8 and all(r['result'] == 'PASS' for r in matrix['matrix'])
    for row in matrix['matrix']:
        for name, digest in row['artifacts'].items():
            path = 'evidence/' + row['configuration'] + '/' + name
            data = decoded[path] if path in decoded else (supporting / path).read_bytes()
            assert hashlib.sha256(data).hexdigest() == digest, path
    critic = root / 'tests/usb-cdc/critic'
    for line in (critic / 'REVIEW_SHA256SUMS').read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        assert hashlib.sha256((critic / path).read_bytes()).hexdigest() == digest, path
    for row in json.loads((critic / 'source-manifest.json').read_text())['inputs']:
        data = (root / row['path']).read_bytes()
        assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256'], row['path']
    print(f"PASS: {len(manifest['files'])} text additions (manifest excluded), "
          f"{len(manifest['base_firmware_inputs'])} unchanged base firmware inputs, 35 unchanged upstream files; "
          'supporting manifest/matrix, 12 lossless captures, and independent critic bindings verified.')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    verify(args.root.resolve())

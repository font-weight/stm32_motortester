"""Stage exact portable test inputs. Never import or execute the staged code."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

MANIFEST_SHA256 = '20291be94fdd7c5f19831f4f105a2686041310535b09869948e4d62b0a717fff'
INPUT_NAMES = ('observer', 'baseline', 'inspector', 'companion', 'original_init', 'source_zip')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ordinary_path(path, directory=False):
    path = path.absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
            raise ValueError('symlink or reparse path: ' + str(item))
        if item != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('not an ordinary directory: ' + str(item))
    return path


def regular_bytes(path, size):
    """Read a bounded byte snapshot, not code. No final-component symlinks."""
    path = ordinary_path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('not an ordinary input file: ' + str(path))
    with path.open('rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('not an ordinary opened input: ' + str(path))
        data = stream.read(size + 1)
    if len(data) != size:
        raise ValueError('input size mismatch: ' + str(path))
    return data


def stage(args):
    manifest_path = Path(__file__).resolve().with_name('inputs.json')
    with manifest_path.open('rb') as stream:
        raw_manifest = stream.read(32769)
    if len(raw_manifest) > 32768 or digest(raw_manifest) != MANIFEST_SHA256:
        raise ValueError('unrecognized input manifest')
    manifest = json.loads(raw_manifest)
    checkout = Path(args.checkout).absolute()
    output = Path(args.output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('output must not already exist')
    if not output.parent.is_dir() or output.parent.resolve() != output.parent:
        raise ValueError('output parent must exist and be canonical')
    ordinary_path(output.parent, directory=True)
    payloads = []
    for row in manifest['files']:
        source = checkout / row['path'] if row['input'] == 'checkout' else Path(getattr(args, row['input']))
        data = regular_bytes(source, row['bytes'])
        if digest(data) != row['sha256']:
            raise ValueError('input SHA256 mismatch: ' + str(source))
        payloads.append((row, data))
    # Nothing is created until every supplied byte snapshot has matched its pin.
    output.mkdir(mode=0o700)
    for row, data in payloads:
        path = output / row['destination']
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
    for row, data in payloads:
        if regular_bytes(output / row['destination'], row['bytes']) != data:
            raise ValueError('staged bytes changed: ' + row['destination'])
    receipt = {'schema_version': 1, 'status': 'PORTABLE_TEST_INPUTS_STAGED',
               'helper_commit': manifest['helper_commit'], 'manifest_sha256': MANIFEST_SHA256,
               'file_count': len(payloads), 'payload_bytes': sum(len(data) for _, data in payloads),
               'tests_executed': False, 'candidate_executions': 0,
               'actual_windows_execution': False, 'launch_authorized': False}
    encoded = (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode()
    with (output / 'STAGING_RECEIPT.json').open('xb') as stream:
        stream.write(encoded)
    if (output / 'STAGING_RECEIPT.json').read_bytes() != encoded:
        raise ValueError('receipt readback mismatch')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', required=True)
    parser.add_argument('--output', required=True)
    for name in INPUT_NAMES:
        parser.add_argument('--' + name.replace('_', '-'), required=True)
    args = parser.parse_args()
    try:
        result = stage(args)
    except (OSError, ValueError, KeyError) as error:
        print('STAGING_FAILED: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

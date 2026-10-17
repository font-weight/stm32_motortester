"""Package an already retained, hash-bound kit; no execution or upload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

# Bind the independently tested retainer before importing its path checks.
RETAINER_SHA256 = '3ab02204cf20771c1ff67ca0ec05c8d8c6cb639a93ed56a653ba487fc856b08c'
MAX_ZIP = 514 * 1024 * 1024
CHUNK = 65536


def helper():
    import types
    path = Path(__file__).with_name('retain.py')
    # Hash and execute the same bounded source bytes; never consume a .pyc cache.
    with path.open('rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError('retainer-not-regular')
        raw = source.read(20067)
    if len(raw) != 20066 or hashlib.sha256(raw).hexdigest() != RETAINER_SHA256:
        raise ValueError('retainer-body-mismatch')
    module = types.ModuleType('_kit_retainer')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def package(*, kit, output, outer_manifest_sha256, mode='accepted'):
    h = helper()
    kit, output = h.absolute(kit), h.absolute(output)
    h.require(output != kit and kit not in output.parents and output not in kit.parents,
              'zip-overlap')
    root_identity = h.shared(h.checked(kit, 'directory'))[:2]
    h.checked(output.parent, 'directory')
    h.no_alias(output.parent, output.name, absent=True)
    h.require(isinstance(outer_manifest_sha256, str)
              and h.HEX.fullmatch(outer_manifest_sha256), 'invalid-outer-hash')
    h.require(mode in ('accepted', 'diagnostics'), 'invalid-mode')
    io = h.IO()
    outer_info, raw, outer_stat = io.read(kit / 'OUTER_MANIFEST.json', maximum=h.MAX_PLAN, collect=True)
    h.require(outer_info['sha256'] == outer_manifest_sha256, 'outer-hash-mismatch')
    body = h.parse_json(raw)
    h.keys(body, ('schema_version', 'status', 'mode', 'application_commit', 'expected_manifest',
                  'files', 'executable', 'scope', 'external_gates'))
    expected_status = 'BYTES_RETAINED_ONLY' if mode == 'accepted' else 'INCOMPLETE_DIAGNOSTICS_ONLY'
    h.require(type(body['schema_version']) is int and body['schema_version'] == 1
              and body['mode'] == mode and body['status'] == expected_status
              and body['application_commit'] == h.APP_COMMIT, 'outer-contract')
    h.require(type(body['files']) is list and 1 <= len(body['files']) <= 64, 'outer-files-limit')
    rows, seen, total = [], set(), 0
    for entry in body['files']:
        h.keys(entry, ('path', 'bytes', 'sha256', 'status'))
        parts = h.relative(entry['path'])
        h.require(parts[0] in ('build', 'evidence') and len(parts) >= 2
                  and entry['status'] == 'bytes-match', 'outer-file-role')
        h.require(entry['path'].lower() not in seen, 'outer-alias')
        seen.add(entry['path'].lower())
        info = h.info_value({'bytes': entry['bytes'], 'sha256': entry['sha256']}, h.MAX_TOTAL)
        total += info['bytes']
        rows.append({'destination': entry['path'], 'info': info})
    h.require(total <= (h.MAX_TOTAL if mode == 'accepted' else h.MAX_DIAGNOSTICS_TOTAL), 'outer-payload-limit')
    expected_info = h.info_value(body['expected_manifest'], h.MAX_PLAN)
    h.inventory(kit, rows)
    files = {r['destination']: r['info'] for r in rows}
    files['EXPECTED_MANIFEST.json'] = expected_info
    files['OUTER_MANIFEST.json'] = outer_info
    snapshots = {}
    for name, info in files.items():
        _, _, snapshots[name] = io.read(kit.joinpath(*h.relative(name)), info)
    h.require(snapshots['OUTER_MANIFEST.json'] == outer_stat, 'outer-changed')
    h.no_alias(output.parent, output.name, absent=True)
    # Exclusive creation: failed or partial ZIPs are kept, never replaced/deleted.
    with output.open('xb') as target:
        target_identity = h.shared(os.fstat(target.fileno()))[:2]
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            for name in sorted(files):
                path, expected = kit.joinpath(*h.relative(name)), files[name]
                h.require(h.stable(h.checked(path, 'file')) == snapshots[name], 'zip-source-changed')
                zi = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                zi.create_system = 3
                zi.external_attr = (stat.S_IFREG | 0o644) << 16
                digest, count = hashlib.sha256(), 0
                with path.open('rb') as source, archive.open(zi, 'w') as dest:
                    h.require(h.shared(os.fstat(source.fileno())) == h.shared(h.checked(path, 'file')),
                              'zip-source-handle')
                    while True:
                        chunk = source.read(min(CHUNK, expected['bytes'] - count + 1))
                        if not chunk:
                            break
                        count += len(chunk)
                        h.require(count <= expected['bytes'], 'zip-source-grew')
                        digest.update(chunk)
                        dest.write(chunk)
                        h.require(target.tell() <= MAX_ZIP, 'zip-byte-limit')
                h.require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'],
                          'zip-source-identity')
        target.flush()
        os.fsync(target.fileno())
        h.require(target.tell() <= MAX_ZIP, 'zip-byte-limit')
    zip_stat = h.stable(h.checked(output, 'file'))
    h.require(h.shared(h.checked(output, 'file'))[:2] == target_identity, 'zip-output-replaced')
    # Read actual member bytes back, not just central-directory metadata.
    with zipfile.ZipFile(output, 'r') as archive:
        members = archive.infolist()
        h.require(len(members) == len(files) and {m.filename for m in members} == set(files),
                  'zip-inventory')
        for member in members:
            expected = files[member.filename]
            h.require(member.file_size == expected['bytes'] and member.compress_type == zipfile.ZIP_STORED,
                      'zip-member-size')
            digest, count = hashlib.sha256(), 0
            with archive.open(member) as source:
                while True:
                    chunk = source.read(CHUNK)
                    if not chunk:
                        break
                    count += len(chunk)
                    h.require(count <= expected['bytes'], 'zip-readback-limit')
                    digest.update(chunk)
            h.require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'],
                      'zip-readback-identity')
    digest, size = hashlib.sha256(), 0
    with output.open('rb') as source:
        for chunk in iter(lambda: source.read(CHUNK), b''):
            size += len(chunk)
            h.require(size <= MAX_ZIP, 'zip-byte-limit')
            digest.update(chunk)
    h.inventory(kit, rows)
    for name, snapshot in snapshots.items():
        h.require(h.stable(h.checked(kit.joinpath(*h.relative(name)), 'file')) == snapshot,
                  'terminal-kit-changed')
    h.require(h.stable(h.checked(output, 'file')) == zip_stat, 'terminal-zip-changed')
    h.require(h.shared(h.checked(kit, 'directory'))[:2] == root_identity, 'terminal-kit-root-changed')
    return {'status': 'KIT_ZIP_BYTES_VERIFIED_ONLY', 'mode': mode, 'files': len(files),
            'zip': {'bytes': size, 'sha256': digest.hexdigest()},
            'outer_manifest': outer_info, 'upload_download': 'not-performed',
            'distribution_authorized_by_this_helper': False, 'physical_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('kit', 'output', 'outer-manifest-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--mode', choices=('accepted', 'diagnostics'), default='accepted')
    try:
        result = package(**vars(parser.parse_args()))
    except Exception as error:
        # Do not disclose filenames or attacker-controlled exception messages.
        print(json.dumps({'status': 'KIT_ZIP_REJECTED', 'error_type': type(error).__name__,
                          'partial_output_must_not_be_consumed': True}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

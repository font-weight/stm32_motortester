"""Data-only final-payload supplement; existing archive validation stays mandatory."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import struct
import sys
import zipfile

APP_COMMIT = 'dcafc5638bc6c3ddef6cba25a7a55016fe8e8933'
APP_TREE = '61a9bd025081acec80997dff950d947eeceb3f4b'
SOURCE_PINS = {
    'host/packaging/select_windows_runtime.py': (5686, 'f4144a5318f4607a207531559c03365d2351473b40b5431ddfb4f7c3d381712b'),
    'host/packaging/motortester.spec': (1867, 'b754e84f96be7c8d8c8284a29457c83567f02c53344a99235857a60386d3e492'),
}
MIB = 1024 * 1024
MAX_META, MAX_FILE, MAX_ZIP = 4 * MIB, 256 * MIB, 200 * MIB
MAX_TOTAL, MAX_READ = 1024 * MIB, 2048 * MIB
MAX_ENTRIES, MAX_FILES, MAX_NAME, MAX_DEPTH = 8192, 4096, 1024, 16
CANDIDATE = 'Motortester-windows-x64-candidate.zip'
MANIFEST = 'build-manifest.json'
FIXED_ARTIFACTS = {CANDIDATE, 'source.zip', 'frozen-window.png',
                   'frozen-window.png.smoke.json', 'frozen-serial-import.json', 'frozen-recording.json'}
RESERVED = {'con', 'prn', 'aux', 'nul', 'clock$', 'conin$', 'conout$'} | {
    prefix + digit for prefix in ('com', 'lpt') for digit in '0123456789¹²³'}


class Invalid(ValueError):
    """Fixed diagnostic codes only; never disclose untrusted input content."""


def require(ok, code):
    if not ok:
        raise Invalid(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def hexhash(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None, 'invalid_hash')
    return value


def relative(value):
    require(type(value) is str and 0 < len(value.encode('utf-8')) <= MAX_NAME, 'unsafe_name')
    require('\\' not in value and not any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*'
                                         for c in value), 'unsafe_name')
    parts = value.split('/')
    require(len(parts) <= MAX_DEPTH and all(p and p not in ('.', '..') and not p.endswith(('.', ' '))
            and p.split('.', 1)[0].rstrip(' ').casefold() not in RESERVED for p in parts), 'unsafe_name')
    return value


def absolute(path):
    raw = os.fspath(path)
    require(type(raw) is str and 0 < len(raw) <= 4096 and not raw.startswith(('//', '\\\\'))
            and '..' not in Path(raw).parts, 'unsafe_root')
    drive, tail = ntpath.splitdrive(raw)
    require((not drive and ':' not in raw) or (os.name == 'nt' and re.fullmatch('[A-Za-z]:', drive)
            and tail.startswith(('\\', '/')) and ':' not in tail), 'unsafe_root')
    path = Path(os.path.abspath(raw))
    require(len(path.parts) <= 64, 'unsafe_root')
    for component in path.parts[1:]:
        relative(component)
    return path


def no_redirect(path):
    path = absolute(path)
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400),
                'redirected_path')
        require(part == path or stat.S_ISDIR(info.st_mode), 'unsafe_ancestor')
    return path


def shared(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stat.S_IFMT(info.st_mode), info.st_nlink)


def fingerprint(info):
    return shared(info) + (info.st_ctime_ns, getattr(info, 'st_birthtime_ns', None),
                           getattr(info, 'st_file_attributes', 0))


class Reads:
    def __init__(self):
        self.total, self.seen = 0, {}

    def read(self, path, cap, keep=False):
        path = no_redirect(path)
        before = path.stat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= cap,
                'file_type_or_limit')
        self.total += before.st_size
        require(self.total <= MAX_READ, 'read_budget')
        h, chunks, size = hashlib.sha256(), [], 0
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            require(shared(opened) == shared(before), 'changed_open_identity')
            while True:
                chunk = stream.read(min(MIB, cap - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                require(size <= cap and size <= before.st_size, 'changed_length')
                h.update(chunk)
                if keep:
                    chunks.append(chunk)
            require(fingerprint(os.fstat(stream.fileno())) == fingerprint(opened), 'changed_handle')
        final = no_redirect(path).stat()
        require(size == before.st_size and fingerprint(final) == fingerprint(before), 'changed_path')
        identity = (fingerprint(before), fingerprint(opened), h.hexdigest())
        require(path not in self.seen or self.seen[path] == identity, 'changed_repeated')
        self.seen[path] = identity
        return {'bytes': size, 'sha256': h.hexdigest()}, b''.join(chunks) if keep else None

    def unchanged(self):
        for path, (old, _, _) in self.seen.items():
            require(fingerprint(no_redirect(path).stat()) == old, 'changed_final')


def decode(raw):
    require(len(raw) <= MAX_META, 'metadata_limit')
    text = raw.decode('utf-8')
    quoted, escaped, depth, nodes, width = False, False, 0, 0, 0
    for char in text:
        if quoted:
            width += 1
            require(width <= MAX_NAME * 12, 'metadata_string_limit')
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted, width = True, 0
            nodes += 1
        elif char in '{}[],:':
            nodes += 1
            depth += (1 if char in '{[' else -1 if char in '}]' else 0)
            require(0 <= depth <= 16, 'metadata_depth')
        require(nodes <= 100000, 'metadata_nodes')
    require(not quoted and depth == 0, 'metadata_truncated')
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def bad_constant(_):
        raise Invalid('metadata_constant')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)


def bind_policy(source, reads):
    observed = {}
    for name, (size, expected) in SOURCE_PINS.items():
        identity, raw = reads.read(source / name, size, keep=True)
        require(identity == {'bytes': size, 'sha256': expected}, 'source_pin')
        observed[name] = identity
        if name.endswith('select_windows_runtime.py'):
            selector = raw
    # Only these reviewed, exact pure source bytes execute. Never import a package,
    # resolve bytecode caches, run the spec, or load a DLL / candidate module.
    policy = {'__name__': 'exact_bound_runtime_policy'}
    exec(compile(selector, '<exact-bound-runtime-policy>', 'exec', dont_inherit=True), policy)
    require(len(policy['EXCLUDED_DESTINATIONS']) == 47 and len(policy['REQUIRED_DESTINATIONS']) == 7,
            'policy_shape')
    return policy, observed


class Names:
    """Reject duplicate destinations, case-alias components and file/dir shadows."""
    def __init__(self):
        self.spelling, self.kinds, self.explicit = {}, {}, set()

    def add(self, name, directory):
        relative(name)
        key = name.casefold()
        require(key not in self.explicit, 'duplicate_name')
        self.explicit.add(key)
        parts = name.split('/')
        for index in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:index])
            folded = prefix.casefold()
            kind = 'dir' if index < len(parts) or directory else 'file'
            require(folded not in self.spelling or self.spelling[folded] == prefix, 'case_alias')
            require(folded not in self.kinds or self.kinds[folded] == kind, 'file_directory_collision')
            self.spelling[folded], self.kinds[folded] = prefix, kind


def directory_inventory(root):
    require(stat.S_ISDIR(no_redirect(root).stat().st_mode), 'directory_required')
    names, files, directories, count = Names(), set(), set(), 0
    def walk(folder, prefix=''):
        nonlocal count
        with os.scandir(folder) as entries:
            for entry in entries:
                count += 1
                require(count <= MAX_ENTRIES, 'entry_limit')
                rel = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400),
                        'redirected_path')
                directory = stat.S_ISDIR(info.st_mode)
                require(directory or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1), 'file_type_or_limit')
                names.add(rel, directory)
                if directory:
                    directories.add(rel)
                    walk(Path(entry.path), rel + '/')
                else:
                    files.add(rel)
                    require(len(files) <= MAX_FILES, 'file_count')
    walk(root)
    return files, directories


def check_policy(files, directories, policy):
    excluded = {x.casefold() for x in policy['EXCLUDED_DESTINATIONS']}
    basenames = {x.rsplit('/', 1)[-1] for x in excluded}
    for name in files | directories:
        key = name.casefold()
        internal = key.removeprefix('_internal/')
        basename = key.rsplit('/', 1)[-1]
        require(internal not in excluded and basename not in basenames
                and not policy['_needs_review'](basename), 'excluded_or_related_destination')
    index = {name.casefold(): name for name in files}
    present = []
    for required in policy['REQUIRED_DESTINATIONS']:
        key = '_internal/' + required.casefold()
        require(key in index, 'missing_keeper')
        present.append({'destination': required, 'actual_path': index[key]})
    return {'excluded_absent_count': 47, 'required_present_count': 7,
            'excluded_absent': list(policy['EXCLUDED_DESTINATIONS']), 'required_present': present}


def bind_keeper_bytes(observation, inventory):
    """Each selected keeper must be a nonempty, already-hashed ordinary file."""
    for keeper in observation['required_present']:
        identity = inventory[keeper['actual_path']]
        require(identity['bytes'] > 0, 'empty_keeper')
        keeper.update(identity)


def ordinary_zip_extra(extra):
    """Bounded ZIP extra-field framing; ZIP64 is unnecessary for this format."""
    offset = 0
    while offset < len(extra):
        require(len(extra) - offset >= 4, 'zip_extra_bounds')
        kind, size = struct.unpack_from('<HH', extra, offset)
        offset += 4
        require(size <= len(extra) - offset, 'zip_extra_bounds')
        require(kind != 0x0001, 'zip64_member')
        offset += size


def ordinary_zip_member(raw, item, central_offset):
    # Inspect both central metadata and bounded local metadata. ZipFile resolves
    # ZIP64 lengths for callers, so checking its final sizes or EOCD alone is not
    # enough. This does not decode executable or PyInstaller archive content.
    require(item.extract_version < 45 and item.volume == 0, 'zip64_or_multidisk_member')
    ordinary_zip_extra(item.extra)
    offset = item.header_offset
    require(0 <= offset <= central_offset - 30, 'zip_local_bounds')
    signature, version, flags, method, _, _, crc, compressed, size, name_size, extra_size = \
        struct.unpack_from('<4s5H3I2H', raw, offset)
    require(signature == b'PK\x03\x04', 'zip_local_header')
    require(version < 45 and compressed != 0xffffffff and size != 0xffffffff, 'zip64_member')
    start = offset + 30
    data_start = start + name_size + extra_size
    require(data_start <= central_offset and item.compress_size <= central_offset - data_start,
            'zip_local_bounds')
    ordinary_zip_extra(raw[start + name_size:data_start])
    require(flags == item.flag_bits and method == item.compress_type, 'zip_local_metadata')
    if not flags & 8:
        require((crc, compressed, size) == (item.CRC, item.compress_size, item.file_size), 'zip_local_metadata')
    else:
        require(crc in (0, item.CRC) and compressed in (0, item.compress_size) and size in (0, item.file_size),
                'zip_local_metadata')
    encoding = 'utf-8' if flags & 0x800 else 'cp437'
    require(raw[start:start + name_size].decode(encoding) == item.orig_filename, 'zip_local_name')


def zip_inventory(raw):
    # Bound central-directory allocations before constructing ZipFile. The ordinary
    # candidate is below 200 MiB and needs neither ZIP64 nor multi-disk support.
    tail_start = max(0, len(raw) - 65557)
    end = raw.rfind(b'PK\x05\x06', tail_start)
    require(end >= 0 and len(raw) - end >= 22, 'zip_end')
    sig, disk, cd_disk, disk_n, total_n, cd_size, cd_offset, comment_len = struct.unpack_from('<4s4H2IH', raw, end)
    require(disk == cd_disk == 0 and 0 < disk_n == total_n <= MAX_ENTRIES
            and cd_size <= MAX_META and cd_offset + cd_size == end
            and end + 22 + comment_len == len(raw), 'zip_bounds')
    names, files, directories, total = Names(), {}, set(), 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(len(infos) == total_n, 'zip_count')
        for item in infos:
            require(item.orig_filename == item.filename and not (item.flag_bits & 1), 'zip_name_or_encryption')
            require(item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'zip_compression')
            ordinary_zip_member(raw, item, cd_offset)
            directory = item.is_dir()
            name = item.filename[:-1] if directory else item.filename
            names.add(name, directory)
            require(name == 'Motortester' if directory and '/' not in name else name.startswith('Motortester/'),
                    'zip_root')
            mode = (item.external_attr >> 16) & 0xFFFF
            require(stat.S_IFMT(mode) in ((0, stat.S_IFDIR) if directory else (0, stat.S_IFREG))
                    and not (item.external_attr & 0x400), 'zip_entry_type')
            require(not (item.external_attr & 0x10) or directory, 'zip_entry_type')
            require(0 <= item.file_size <= MAX_FILE and 0 <= item.compress_size <= MAX_ZIP, 'zip_member_limit')
            total += item.file_size
            require(total <= MAX_TOTAL, 'zip_total_limit')
            if directory:
                require(item.file_size == item.CRC == 0, 'zip_directory_data')
                if name != 'Motortester':
                    directories.add(name[len('Motortester/'):])
                continue
            require(len(files) < MAX_FILES, 'file_count')
            h, size = hashlib.sha256(), 0
            with archive.open(item) as member:
                while True:
                    chunk = member.read(min(MIB, item.file_size - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    require(size <= item.file_size, 'zip_member_length')
                    h.update(chunk)
            require(size == item.file_size, 'zip_member_length')
            files[name[len('Motortester/'):]] = {'bytes': size, 'sha256': h.hexdigest()}
    return files, directories


def artifact_cap(name):
    return MAX_ZIP if name == CANDIDATE else 20 * MIB if name == 'source.zip' else 2 * MIB


def verify(source_root, build_root, manifest_sha256, archive_sha256):
    hexhash(manifest_sha256); hexhash(archive_sha256)
    source, build = no_redirect(source_root), no_redirect(build_root)
    reads = Reads()
    policy, source_inputs = bind_policy(source, reads)
    checker_id, _ = reads.read(absolute(__file__), 65536)
    report_id, raw = reads.read(build / 'archive-verification.json', MAX_META, keep=True)
    require(report_id['sha256'] == archive_sha256, 'archive_report_pin')
    archive = decode(raw)
    require(type(archive) is dict and archive.get('status') == 'PASS'
            and archive.get('scope') == 'on-runner-archive-content-only'
            and archive.get('source_sha') == APP_COMMIT
            and type(archive.get('source_files')) is int and archive['source_files'] == 306
            and archive.get('recording_required') is True
            and type(archive.get('recording_source_zip_hashes_verified')) is int
            and archive['recording_source_zip_hashes_verified'] == 23
            and archive.get('hardware_verified') is False and archive.get('bytes_delivered') is False,
            'archive_report_contract')
    hashes, sizes = archive.get('artifacts_sha256'), archive.get('artifact_bytes')
    require(type(hashes) is dict and type(sizes) is dict and len(hashes) == 8 and set(hashes) == set(sizes)
            and FIXED_ARTIFACTS <= set(hashes), 'artifact_inventory')
    extra = set(hashes) - FIXED_ARTIFACTS
    require('frozen-recording.json.work/recording.mtrec' in extra and len(extra) == 2
            and all(name.startswith('frozen-recording.json.work/') and len(name.split('/')) == 2 for name in extra),
            'recording_artifact_inventory')
    actual_artifacts, candidate_raw = {}, None
    for name in sorted(hashes):
        relative(name); hexhash(hashes[name])
        require(type(sizes[name]) is int and 0 <= sizes[name] <= artifact_cap(name), 'artifact_size')
        identity, data = reads.read(build / name, artifact_cap(name), keep=name == CANDIDATE)
        require(identity == {'bytes': sizes[name], 'sha256': hashes[name]}, 'archive_artifact_join')
        actual_artifacts[name] = identity
        if name == CANDIDATE:
            candidate_raw = data
    sums_id, sums_raw = reads.read(build / 'SHA256SUMS.txt', MIB, keep=True)
    require(sums_id['sha256'] == archive.get('checksum_file_sha256'), 'checksum_join')
    sums = {}
    for line in sums_raw.decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None and match[2] not in sums, 'checksum_shape')
        sums[match[2]] = match[1]
    require(sums == hashes, 'checksum_inventory')
    bundle = build / 'dist/Motortester'
    manifest_id, raw = reads.read(bundle / MANIFEST, MAX_META, keep=True)
    require(manifest_id['sha256'] == manifest_sha256 == archive.get('build_manifest_sha256'), 'manifest_join')
    manifest = decode(raw)
    require(type(manifest) is dict and manifest.get('source_commit') == APP_COMMIT
            and manifest.get('status') == 'candidate-not-release-approved'
            and manifest.get('usb_verified') is False, 'manifest_contract')
    expected = manifest.get('files')
    require(type(expected) is dict and 0 < len(expected) < MAX_FILES and 'Motortester.exe' in expected
            and MANIFEST not in expected, 'manifest_inventory')
    names = Names()
    for name, expected_hash in expected.items():
        names.add(name, False); hexhash(expected_hash)
    files, directories = directory_inventory(bundle)
    require(files == set(expected) | {MANIFEST}, 'directory_inventory')
    actual_files, total = {MANIFEST: manifest_id}, manifest_id['bytes']
    for name in sorted(expected):
        identity, _ = reads.read(bundle / name, MAX_FILE)
        require(identity['sha256'] == expected[name], 'directory_hash')
        actual_files[name] = identity
        total += identity['bytes']
        require(total <= MAX_TOTAL, 'directory_total_limit')
    require(type(archive.get('binary_files')) is int and archive['binary_files'] == len(expected),
            'archive_file_count')
    onedir_policy = check_policy(files, directories, policy)
    bind_keeper_bytes(onedir_policy, actual_files)
    zip_files, zip_directories = zip_inventory(candidate_raw)
    require(zip_files == actual_files and zip_directories == directories, 'zip_directory_join')
    zip_policy = check_policy(set(zip_files), zip_directories, policy)
    bind_keeper_bytes(zip_policy, zip_files)
    # Rehash external anchors and candidate, recheck all file identities and the
    # directory inventory. This is observation, not a concurrent-writer sandbox.
    for path, cap in ((build / CANDIDATE, MAX_ZIP), (build / 'archive-verification.json', MAX_META),
                      (bundle / MANIFEST, MAX_META)):
        reads.read(path, cap)
    reads.unchanged()
    require(directory_inventory(bundle) == (files, directories), 'directory_changed')
    rows = [{'path': name, **identity} for name, identity in sorted(actual_files.items())]
    canonical = json.dumps(rows, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')
    return {
        'schema_version': 1, 'status': 'PASS', 'scope': 'data-only-final-reduced-payload-correspondence',
        'application_commit': APP_COMMIT, 'application_tree': APP_TREE,
        'source_binding': 'expected revision; clean Git source/tree guard remains external',
        'actual_checker': checker_id,
        'actual_source_inputs': source_inputs, 'actual_source_zip': actual_artifacts['source.zip'],
        'actual_candidate_zip': actual_artifacts[CANDIDATE], 'actual_build_manifest': manifest_id,
        'actual_archive_verification_report': report_id, 'actual_checksums': sums_id,
        'actual_artifacts': actual_artifacts,
        'actual_onedir': {'file_count': len(rows), 'bytes': total, 'files': rows,
                          'directory_count': len(directories), 'directories': sorted(directories),
                          'file_inventory_sha256': digest(canonical),
                          'hash_format': 'ASCII JSON sorted file rows; keys sorted; separators comma/colon; no newline'},
        'onedir_policy': onedir_policy, 'zip_policy': zip_policy,
        'archive_validator_required': True, 'archive_validator_pass_report_bound': True,
        'pyinstaller_archive_decoded_by_this_checker': False, 'source_tree_verified_by_this_checker': False,
        'native_code_executed': False, 'candidate_python_executed': False, 'payload_modified': False,
        'authenticated_origins': False, 'tls_capability_verified': False, 'hardware_verified': False,
        'physical_stop_verified': False, 'windows_10_verified': False, 'legal_clearance': False,
        'bytes_delivered': False,
    }


def write_report(output, report, source, build):
    output = absolute(output)
    parent = no_redirect(output.parent)
    for root in (no_redirect(source), no_redirect(build)):
        require(not output.is_relative_to(root), 'output_overlaps_inputs')
    require(output != absolute(__file__), 'output_overlaps_inputs')
    raw = (json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('ascii')
    require(len(raw) <= MAX_META, 'report_limit')
    target, created = parent / output.name, None
    try:
        with target.open('xb') as stream:
            created = os.fstat(stream.fileno())
            require(stream.write(raw) == len(raw), 'report_short_write')
    except BaseException:
        if created is not None:
            try:
                current = target.lstat()
                if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                    target.unlink()
            except OSError:
                pass
        raise


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise Invalid('arguments')
    parser = Parser(description=__doc__)
    for flag in ('source-root', 'build-root', 'expected-manifest-sha256', 'expected-archive-report-sha256', 'output'):
        parser.add_argument('--' + flag, required=True)
    try:
        args = parser.parse_args(argv)
        result = verify(args.source_root, args.build_root, args.expected_manifest_sha256,
                        args.expected_archive_report_sha256)
        write_report(args.output, result, args.source_root, args.build_root)
    except Exception as error:
        code = str(error) if isinstance(error, Invalid) else 'io_or_parse_error'
        if not re.fullmatch('[a-z_]{1,64}', code):
            code = 'invalid_input'
        print('REDUCED_PAYLOAD_FAILED code=' + code, file=sys.stderr)
        return 1
    print('Reduced payload correspondence PASS; origin, TLS, hardware and release approval remain unverified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

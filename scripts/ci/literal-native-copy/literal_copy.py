"""Fixed run18 literal-origin observation. Never load native libraries or grant rights."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import types

MIB = 1024 * 1024
MANAGED_LIMIT = 123 * MIB
AUXILIARY_RESERVE = 5 * MIB
PATH_LIMIT = 99
REFERENCE_HASH = '2cd3be37cbc9737927f6832a5366fc29b835bd5fd9a4f6ea76b288a1d1c1dc61'
BASELINE_HASH = 'fb864aa52ccf4db813478a41ba256b9f6bc64c711403884d9ef0b8603926a8df'
HELPER_HASH = '5b8ca95c9e9ea14f0a06d2edc8735c1bef6bc53d51985bfa0ae75adc37ee922c'
CLEARANCE = ('package_membership_verified', 'corresponding_source_verified',
             'legal_clearance', 'replacement_verified')

class Invalid(ValueError):
    pass

def need(value, message):
    if not value:
        raise Invalid(message)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    raw = os.fspath(value)
    need(type(raw) is str and 0 < len(raw) <= 4096 and
         not any(ord(c) < 32 for c in raw), 'Invalid path text')
    p = Path(raw)
    need(p.is_absolute() and os.path.normpath(raw) == raw and str(p) == raw,
         'Absolute canonical path required')
    need(all(x not in ('.', '..', '') and not x.endswith((' ', '.'))
             for x in p.parts[1:]), 'Aliased path component')
    if os.name == 'nt':
        need(re.fullmatch(r'[A-Za-z]:', p.drive) is not None, 'UNC/device path')
    else:
        need(p.anchor == '/' and '\\' not in raw, 'Noncanonical POSIX root')
    for part in p.parts[1:]:
        need(not any(c in part for c in ':<>"|?*'), 'ADS/unsafe path component')
        stem=part.split('.')[0].rstrip(' ').upper()
        need(stem not in ('CON','PRN','AUX','NUL','CONIN$','CONOUT$','CLOCK$') and
             re.fullmatch(r'(COM|LPT)[0-9¹²³]',stem) is None,
             'Reserved device component')
    return p

def safe_info(path, missing=False, directory=False):
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            if missing:
                return None
            raise
        need(not stat.S_ISLNK(info.st_mode) and
             not getattr(info, 'st_file_attributes', 0) & 0x400,
             'Link or reparse point rejected')
        is_dir = part != path or directory
        need(stat.S_ISDIR(info.st_mode) if is_dir else stat.S_ISREG(info.st_mode),
             'Unexpected filesystem type')
    return info

def identity(s):
    # mtime is comparable across these APIs; Windows ctime semantics differ.
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)

def timestamp(s):
    return (s.st_mtime_ns, s.st_ctime_ns)

class Reader:
    """One finite unique-leaf plan; every returned/read-back/written byte is charged."""
    def __init__(self, allowed):
        self.allowed = {}
        self.used = {}
        self.read_bytes = self.write_bytes = 0
        self.open_events = []
        self.extend(allowed)

    @property
    def total(self):
        return self.read_bytes + self.write_bytes

    def extend(self, allowed):
        candidate = dict(self.allowed)
        for path, cap in allowed.items():
            path = canonical(path)
            key = str(path).casefold()
            need(key not in candidate, 'Duplicate or aliased managed leaf')
            need(type(cap) is int and 0 < cap <= 16 * MIB, 'Invalid leaf cap')
            candidate[key] = (path, cap)
        need(len(candidate) <= PATH_LIMIT, 'Managed path limit')
        self.allowed = candidate

    def read(self, path, missing=False, remember=True):
        key = str(path).casefold()
        need(key in self.allowed and path == self.allowed[key][0], 'Nonallowlisted read')
        cap = self.allowed[key][1]
        before = safe_info(path, missing=missing)
        if before is None:
            if remember:
                need(key not in self.used or self.used[key] is None, 'Input disappeared')
                self.used[key] = None
            return None
        need(0 < before.st_size <= cap, 'Leaf size limit')
        need(before.st_size <= MANAGED_LIMIT - self.total, 'Managed byte limit before open')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        self.open_events.append(key)
        try:
            opened = os.fstat(fd)
            need(stat.S_ISREG(opened.st_mode) and
                 not getattr(opened, 'st_file_attributes', 0) & 0x400, 'Unsafe opened handle')
            need(identity(opened) == identity(before), 'Opened identity differs')
            chunks, count = [], 0
            while count < before.st_size:
                amount = min(65536, before.st_size - count, MANAGED_LIMIT - self.total)
                need(amount > 0, 'Managed byte limit')
                chunk = os.read(fd, amount)
                self.read_bytes += len(chunk)
                need(len(chunk) <= amount and self.total <= MANAGED_LIMIT, 'Read bound')
                need(bool(chunk), 'Short read')
                chunks.append(chunk)
                count += len(chunk)
            after = os.fstat(fd)
            need(stat.S_ISREG(after.st_mode) and
                 not getattr(after, 'st_file_attributes', 0) & 0x400, 'Unsafe final handle')
        finally:
            os.close(fd)
        final = safe_info(path)
        need(identity(before) == identity(after) == identity(final) and
             timestamp(before) == timestamp(final) and timestamp(opened) == timestamp(after),
             'Input changed during read')
        raw = b''.join(chunks)
        record = (identity(final), timestamp(final), sha(raw))
        if remember:
            need(key not in self.used or self.used[key] == record, 'Repeated input differs')
            self.used[key] = record
        return raw

    def recheck(self):
        for key, record in list(self.used.items()):
            path = self.allowed[key][0]
            raw = self.read(path, missing=record is None, remember=False)
            if record is None:
                need(raw is None, 'Previously absent origin appeared')
            else:
                final = safe_info(path)
                need(raw is not None and (identity(final), timestamp(final), sha(raw)) == record,
                     'Final input differs')

    def write_report(self, path, raw):
        key = str(path).casefold()
        need(key in self.allowed and path == self.allowed[key][0], 'Unplanned output')
        need(0 < len(raw) <= self.allowed[key][1], 'Output size limit')
        need(len(raw) <= MANAGED_LIMIT - self.total, 'Output byte limit before open')
        safe_info(path.parent, directory=True)
        need(safe_info(path, missing=True) is None, 'Output already exists')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            opened = os.fstat(fd)
            need(stat.S_ISREG(opened.st_mode) and opened.st_size == 0 and
                 not getattr(opened, 'st_file_attributes', 0) & 0x400, 'Unsafe output handle')
            offset = 0
            while offset < len(raw):
                chunk = raw[offset:offset + 65536]
                amount = os.write(fd, chunk)
                need(0 < amount <= len(chunk), 'Short/invalid write')
                self.write_bytes += amount
                need(self.total <= MANAGED_LIMIT, 'Output managed byte limit')
                offset += amount
            after = os.fstat(fd)
            need(stat.S_ISREG(after.st_mode) and after.st_size == len(raw) and
                 not getattr(after, 'st_file_attributes', 0) & 0x400 and
                 (opened.st_dev, opened.st_ino) == (after.st_dev, after.st_ino),
                 'Output handle changed')
        finally:
            os.close(fd)
        final = safe_info(path)
        need(identity(final) == identity(after), 'Output path identity differs')
        need(self.read(path, remember=False) == raw, 'Output readback differs')


def origin_path(declaration):
    # Production uses these literal absolute Windows leaves, never discovery.
    return canonical(declaration)


def bind(helper, reference_raw, baseline_raw, current_raw, toc_raw, expected):
    need(sha(reference_raw) == REFERENCE_HASH, 'Run18 reference pin')
    need(sha(baseline_raw) == BASELINE_HASH, 'Run17 baseline pin')
    ref, current = helper.decode(reference_raw), helper.decode(current_raw)
    fixed = helper.native_rows(ref['native_rows'])
    baseline = helper.native_rows(helper.decode(baseline_raw))
    observed = helper.native_rows(current['native_rows'])
    need(len(fixed) == 46 and set(fixed) == set(baseline) == set(observed), 'Fixed46 set')
    need(ref['context']['run_id'] == '38572185383' and ref['context']['attempt'] == '1',
         'Reference event')
    need(type(current.get('schema_version')) is int and current['schema_version'] == 1 and
         current.get('status') == 'observed_with_unresolved_origins' and
         current.get('errors') == [] and current.get('final_inputs_unchanged') is True and
         current.get('run17_baseline_matches') is True and
         current.get('baseline_sha256') == BASELINE_HASH and
         all(current.get(k) is False for k in CLEARANCE), 'Current observation contract')
    context = current.get('context')
    need(type(context) is dict and set(context) == set(ref['context']), 'Context schema')
    mutable = {'run_id', 'attempt', 'workflow_sha', 'image_version'}
    for key, value in ref['context'].items():
        if key not in mutable:
            need(context[key] == value, 'Immutable context differs: ' + key)
    for key in ('run_id', 'attempt', 'workflow_sha'):
        need(context[key] == expected[key], 'Current event differs: ' + key)
    need(type(context['image_version']) is str and
         re.fullmatch(r'[0-9.]{1,64}', context['image_version']) is not None, 'Image version')
    need(current.get('input_hashes', {}).get('toc') == sha(toc_raw), 'Actual TOC binding')
    toc = helper.parse_collect(toc_raw)
    rows = []
    for key in sorted(fixed):
        row, base, now = fixed[key], baseline[key], observed[key]
        for field in ('path', 'bytes', 'sha256'):
            need(row[field] == base[field] == now[field], 'Native row drift')
        declaration = row['declared_source']
        helper.declared_windows_path(declaration)
        need(now.get('declared_source') == declaration and now.get('supplying_package') is None,
             'Literal declaration drift')
        need(now.get('source_copy', {}).get('status') == 'outside_observer_allowlist',
             'Unexpected prior origin observation')
        need(row['path'].startswith('_internal/'), 'Unexpected bundle destination')
        destination = row['path'][len('_internal/'):]
        toc_key = destination.casefold()
        need(toc_key in toc and toc[toc_key] == (destination, declaration, 'BINARY'),
             'COLLECT onedir destination join')
        rows.append({k: row[k] for k in ('path', 'bytes', 'sha256', 'declared_source')})
    return rows, context


def observe(config):
    """Internal engine; production entry is main, not a configurable mapping API."""
    bundle = canonical(config['bundle'])
    safe_info(bundle, directory=True)
    paths = {k: canonical(config[k]) for k in
             ('self', 'helper', 'reference', 'baseline', 'current', 'toc', 'output')}
    need(not (paths['output'] == bundle or bundle in paths['output'].parents),
         'Output overlaps bundle')
    need(safe_info(paths['output'], missing=True) is None, 'Output already exists')
    caps = {'self': 256*1024, 'helper': 128*1024, 'reference': 128*1024,
            'baseline': 65536, 'current': 2*MIB, 'toc': 2*MIB, 'output': 2*MIB}
    reader = Reader({paths[k]: caps[k] for k in paths})
    raw = {k: reader.read(paths[k]) for k in paths if k != 'output'}
    need(sha(raw['helper']) == HELPER_HASH, 'Published observer helper pin')
    helper = types.ModuleType('_pinned_native_observer')
    helper.__file__ = str(paths['helper'])
    # Execute only the already hash-admitted pure helper body; main is not called.
    exec(compile(raw['helper'], str(paths['helper']), 'exec'), helper.__dict__)
    rows, context = bind(helper, raw['reference'], raw['baseline'], raw['current'], raw['toc'],
                         config['expected'])
    bundle_paths = {r['path']: canonical(bundle / r['path']) for r in rows}
    origins = {r['path']: origin_path(r['declared_source']) for r in rows}
    planned = {}
    for r in rows:
        for p in (bundle_paths[r['path']], origins[r['path']]):
            need(p not in planned, 'Duplicate planned native leaf')
            planned[p] = 16*MIB
    reader.extend(planned)
    need(len(reader.allowed) == 99, 'Exact managed profile')
    # Complete the entire fixed bundle validation barrier before any origin read.
    copies = {}
    for row in rows:
        payload = reader.read(bundle_paths[row['path']])
        need(len(payload) == row['bytes'] and sha(payload) == row['sha256'],
             'Bundle body differs: ' + row['path'])
        copies[row['path']] = payload
    results = []
    for row in rows:
        result = dict(row)
        path = origins[row['path']]
        try:
            info = safe_info(path, missing=True)
            if info is not None and info.st_size != row['bytes']:
                result['source_copy'] = {'status': 'size_mismatch', 'observed_bytes': info.st_size}
            else:
                payload = reader.read(path, missing=True)
                if payload is None:
                    result['source_copy'] = {'status': 'absent'}
                else:
                    same = payload == copies[row['path']]
                    result['source_copy'] = {'status': 'bytes_equal' if same else 'different_bytes',
                                             'bytes': len(payload), 'sha256': sha(payload)}
        except (OSError, ValueError) as exc:
            result['source_copy'] = {'status': 'error', 'error_type': type(exc).__name__,
                                     'message': str(exc)[:240]}
        result['supplying_package'] = None
        results.append(result)
    equal = all(r['source_copy']['status'] == 'bytes_equal' for r in results)
    report = {'schema_version': 1, 'status': 'UNCOMMITTED_LITERAL_OBSERVATION',
              'scope': 'Fresh literal byte comparison only; no historical supplier attribution',
              'context': context, 'all_bundle_verified': True,
              'all_literal_bytes_equal': equal, 'native_rows': results,
              'source_hashes': {k: sha(v) for k, v in raw.items()},
              'managed_path_count': len(reader.allowed),
              'final_input_check_recorded_only_in_cli_receipt': True,
              **{k: False for k in CLEARANCE}}
    encoded = (json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2)+'\n').encode('utf-8')
    reader.write_report(paths['output'], encoded)
    reader.recheck()
    need(reader.read(paths['output'], remember=False) == encoded, 'Final report changed')
    receipt = {'schema_version': 1,
               'status': 'observed_literal_byte_equality' if equal else 'incomplete',
               'report_bytes': len(encoded), 'report_sha256': sha(encoded),
               'all_bundle_verified': True, 'all_literal_bytes_equal': equal,
               'successfully_observed_inputs_unchanged': True,
               'managed_path_count': len(reader.allowed), 'managed_path_limit': PATH_LIMIT,
               'read_bytes': reader.read_bytes, 'write_bytes': reader.write_bytes,
               'managed_bytes': reader.total, 'managed_limit': MANAGED_LIMIT,
               'external_auxiliary_reserve': AUXILIARY_RESERVE,
               'observed_input_count': len(reader.used),
               **{k: False for k in CLEARANCE}}
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'helper', 'reference', 'baseline', 'current', 'toc', 'output',
                 'run-id', 'attempt', 'workflow-sha'):
        parser.add_argument('--'+name, required=True)
    args = vars(parser.parse_args(argv))
    need(os.name == 'nt' and sys.version_info[:3] == (3, 13, 16) and struct.calcsize('P') == 8,
         'Production CLI requires Windows x64 CPython3.13.16')
    need(re.fullmatch(r'[0-9]{1,20}', args['run_id']) is not None and
         re.fullmatch(r'[0-9]{1,4}', args['attempt']) is not None and
         re.fullmatch(r'[0-9a-f]{40}', args['workflow_sha']) is not None, 'Event identity grammar')
    args['self'] = str(Path(__file__).absolute())
    args['expected'] = {k: args[k] for k in ('run_id', 'attempt', 'workflow_sha')}
    result = observe(args)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'observed_literal_byte_equality' else 1

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        print(type(error).__name__ + ': ' + str(error)[:400], file=sys.stderr)
        raise SystemExit(1)

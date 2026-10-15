"""Bounded metadata joins and original-receipt retention; no candidate DLL/EXE execution."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import textwrap
import types

KIB = 1024
LIMIT = 5 * 1024 * KIB
FAILURE_RESERVE = 16 * KIB
PINS_SHA = '77322f4c0edd2e08a4500fc3e6da99f9ac5fa6e6837c8d735baf419d98fd4c76'
CAPS = dict(self=64, pins=16, workflow=64, literal=32, observer=64, baseline=16,
            reference=128, current=128, toc=256, manifest=128, provenance=512,
            archive=16, checksums=4, serial=4, gui=64, recording=64,
            notice_source=64, notice_copy=64)
OUTPUTS = {'current.original.bin':128, 'current.identity.json':4, 'package-gate.json':16,
           'literal-observation.json':256, 'literal.stdout':4, 'literal.stderr':4,
           'composition.json':16, 'failure.json':4}
FALSE_FIELDS = ('prior_package_execution_verified', 'native_comparison_verified',
                'actual_windows_execution', 'corresponding_source_verified',
                'legal_clearance', 'replacement_verified', 'launch_authorized')

class Rejected(ValueError):
    pass

def need(value, message):
    if not value:
        raise Rejected(message)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def record(raw):
    return {'bytes':len(raw), 'sha256':sha(raw)}

def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                       indent=2) + '\n').encode('ascii')

def same(left, right):
    return encoded(left) == encoded(right)

def canonical(value):
    raw = os.fspath(value)
    need(type(raw) is str and 0 < len(raw) <= 1024 and
         not any(ord(c) < 32 or ord(c) == 127 for c in raw), 'path-text')
    p = Path(raw)
    need(p.is_absolute() and str(p) == raw and os.path.normpath(raw) == raw and
         len(p.parts) <= 64, 'path-canonical')
    if os.name == 'nt':
        need(re.fullmatch('[A-Za-z]:', p.drive) is not None, 'path-drive')
    else:
        need(p.anchor == '/' and '\\' not in raw, 'path-anchor')
    for part in p.parts[1:]:
        need(part not in ('', '.', '..') and not part.endswith((' ', '.')) and
             not any(c in part for c in ':<>"|?*'), 'path-component')
        stem = part.split('.')[0].rstrip(' ').upper()
        need(stem not in ('CON','PRN','AUX','NUL','CONIN$','CONOUT$','CLOCK$') and
             re.fullmatch(r'(COM|LPT)[0-9¹²³]', stem) is None, 'path-device')
    return p

def shared(s):
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns

def times(s):
    return s.st_mtime_ns, s.st_ctime_ns

def regular(s):
    return stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and not getattr(s, 'st_file_attributes', 0) & 0x400

# Pure data validators vendored verbatim from inspector 54d63be3; no I/O functions.
MIB = 1024 * 1024

MAX_FILE = 256 * MIB

MAX_ARCHIVE = 64 * MIB

MAX_PATH_PARTS = 16  # Actual run14 notice paths have nine directory components.

MAX_NAME = 1024

PINNED = {
    'altgraph': '0.17.5', 'colorama': '0.4.6', 'numpy': '2.5.3',
    'packaging': '26.3', 'pefile': '2024.8.26', 'pip': '26.2.1',
    'pyinstaller': '6.22.3', 'pyinstaller-hooks-contrib': '2026.8',
    'pyqtgraph': '0.14.0', 'pyserial': '3.5', 'pyside6-essentials': '6.12.0',
    'pywin32-ctypes': '0.2.3', 'setuptools': '84.0.0', 'shiboken6': '6.12.0',
}

MAX_RECORDING_ARTIFACT = 2 * MIB

RECORDING_REPORT = 'frozen-recording.json'

RECORDING_WORK = RECORDING_REPORT + '.work'

RECORDING_KEYS = {'runtime': ('application',
             'frozen',
             'implementation',
             'machine',
             'optimize',
             'os_name',
             'os_release',
             'os_version',
             'platform',
             'pyside',
             'python',
             'python_bits',
             'qt',
             'qt_platform'),
 'checks': ('coverage',
            'device_io_performed',
            'fresh_gui_open_and_selection',
            'independent_pattern_match',
            'intersegment_device_gap',
            'observed_bytes_and_provenance_match',
            'physical_stop_verified',
            'source_binding',
            'synthetic',
            'time_basis'),
 'identity': ('application_version',
              'firmware_version',
              'origin',
              'protocol_version',
              'recording_id',
              'session_id'),
 'stop': ('ack_request_id',
          'evidence',
          'generation',
          'last_window',
          'request_id',
          'status',
          'termination_reason',
          'wire_session_nonce'),
 'artifacts': ('archive', 'spool'),
 'executable': ('bytes', 'sha256'),
 'cleanup': ('acquisition_native_joined',
             'finalization_invalidated',
             'pending_io',
             'windows_closed',
             'writer_stopped'),
 'top_level': ('artifacts',
               'checks',
               'cleanup',
               'error',
               'executable',
               'identity',
               'imports',
               'output',
               'phase',
               'phases',
               'runtime',
               'schema_version',
               'scope',
               'segments',
               'status',
               'stop',
               'workdir'),
 'phase_entry': ('name', 'utc'),
 'segment_entry': ('kind',
                   'ordinal',
                   'provenance',
                   'raw_sha256',
                   'sample_count',
                   'samples_sha256',
                   'sequence'),
 'segment_provenance': ('abort_reason_code',
                        'acquisition_mode',
                        'auto_timeout_ms',
                        'begin_request_id',
                        'begin_sequence',
                        'config_id',
                        'config_payload_hex',
                        'end_samples_crc32',
                        'generation',
                        'host_elapsed_ms',
                        'start_operation',
                        'terminal_kind',
                        'terminal_request_id',
                        'terminal_sequence',
                        'unavailable_reason',
                        'wire_session_nonce',
                        'wire_version'),
 'artifact_entry': ('bytes', 'name', 'sha256')}

RECORDING_IMPORTS = ['motortester.gui',
 'motortester.gui_acquisition',
 'motortester.acquisition',
 'motortester.recording_bridge',
 'motortester.recording_controller',
 'motortester.session_archive',
 'motortester.device_emulator',
 'motortester.runtime',
 'motortester.session',
 'motortester.wire',
 'motortester.window',
 'motortester.recording',
 'motortester.model']

RECORDING_PHASES = ['entry', 'gui', 'connect', 'record', 'stop', 'save', 'open', 'navigation', 'shutdown', 'complete']

RECORDING_CHECKS = {'synthetic': True,
 'device_io_performed': False,
 'physical_stop_verified': False,
 'coverage': 'segmented',
 'intersegment_device_gap': 'unknown',
 'time_basis': 'per_segment_nominal_unverified',
 'source_binding': 'external-clean-build-manifest',
 'observed_bytes_and_provenance_match': True,
 'independent_pattern_match': True,
 'fresh_gui_open_and_selection': True}

RECORDING_CLEANUP = {'acquisition_native_joined': True,
 'writer_stopped': True,
 'windows_closed': True,
 'pending_io': False,
 'finalization_invalidated': False}

def hex_id(value, length=64):
    require(type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None,
            'invalid_identity')
    return value

def name(value):
    require(type(value) is str and 0 < len(value.encode('utf-8')) <= MAX_NAME,
            'invalid_name')
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value), 'invalid_name')
    return value

def relative(value):
    name(value)
    require('\\' not in value and ':' not in value and not value.startswith('/'), 'unsafe_path')
    parts = value.split('/')
    require(len(parts) <= MAX_PATH_PARTS, 'path_depth_limit')
    require(all(p not in ('', '.', '..') and not p.endswith((' ', '.')) for p in parts), 'unsafe_path')
    require(not any(c in '<>"|?*' for c in value), 'unsafe_path')
    # Include documented superscript digits and conservatively reject legacy
    # console/clock/zero aliases. Apply to each component and extension spelling.
    for part in parts:
        stem = part.split('.', 1)[0].rstrip(' ').upper()
        require(stem not in ('CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$', 'CLOCK$')
                and re.fullmatch(r'(COM|LPT)[0-9¹²³]', stem) is None, 'unsafe_path')
    return value

def norm_dist(value):
    name(value)
    return re.sub('[-_.]+', '-', value).lower()

def package_versions(rows):
    require(type(rows) is list and len(rows) == len(PINNED), 'package_set')
    result = {}
    for row in rows:
        require(type(row) is dict and set(row) == {'name', 'version'}, 'package_shape')
        key = norm_dist(row['name'])
        require(key not in result and type(row['version']) is str, 'duplicate_distribution')
        result[key] = row['version']
    require(result == PINNED, 'dependency_version')

def recording_keys(value, label):
    require(type(value) is dict and set(value) == set(RECORDING_KEYS[label]), 'recording_shape')

def recording_integer(value, low, high):
    require(type(value) is int and low <= value <= high, 'recording_integer')

def recording_text(value, maximum=128):
    name(value)
    require(len(value) <= maximum, 'recording_text')

def recording_exact(value, expected):
    require(type(value) is type(expected) and value == expected, 'recording_value')

def recording_uuid(value):
    recording_text(value, 36)
    require(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value) is not None,
            'recording_uuid')
    return int(value.replace('-', ''), 16)

def same_json(left, right):
    """Type-aware value equality after both bounded JSON trees are validated."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(same_json(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(same_json(a, b) for a, b in zip(left, right))
    return left == right

def recording_structure(report):
    """Validate receipt assertions; no imports, archive extraction or execution."""
    recording_keys(report, 'top_level')
    for key, expected in {'schema_version': 1, 'status': 'passed', 'scope': 'frozen-windows-recording',
                          'phase': 'complete', 'error': None, 'output': RECORDING_REPORT,
                          'workdir': RECORDING_WORK, 'imports': RECORDING_IMPORTS}.items():
        recording_exact(report[key], expected)
    for key, expected in (('checks', RECORDING_CHECKS), ('cleanup', RECORDING_CLEANUP)):
        recording_keys(report[key], key)
        for field, value in expected.items():
            recording_exact(report[key][field], value)
    runtime = report['runtime']
    recording_keys(runtime, 'runtime')
    for key in set(runtime) - {'python_bits', 'optimize', 'frozen'}:
        recording_text(runtime[key], 256)
    for key, expected in {'platform': 'win32', 'os_name': 'nt', 'frozen': True,
                          'python': '3.13.16', 'implementation': 'CPython', 'python_bits': 64,
                          'qt': '6.12.0', 'pyside': '6.12.0', 'qt_platform': 'windows', 'optimize': 0}.items():
        recording_exact(runtime[key], expected)
    require(runtime['machine'].upper() in ('AMD64', 'X86_64'), 'recording_runtime')
    phases = report['phases']
    require(type(phases) is list and len(phases) == len(RECORDING_PHASES), 'recording_phases')
    previous = None
    for entry, expected in zip(phases, RECORDING_PHASES):
        recording_keys(entry, 'phase_entry')
        recording_exact(entry['name'], expected)
        recording_text(entry['utc'], 64)
        stamp = datetime.fromisoformat(entry['utc'])
        require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0
                and (previous is None or stamp >= previous), 'recording_timestamp')
        previous = stamp
    # UTC order is metadata only. Monotonic wait/child deadlines are not inferred.
    identity = report['identity']
    recording_keys(identity, 'identity')
    recording_uuid(identity['recording_id'])
    nonce = recording_uuid(identity['session_id'])
    recording_exact(identity['origin'], 'simulator')
    recording_exact(identity['application_version'], runtime['application'])
    for key in ('firmware_version', 'protocol_version'):
        recording_text(identity[key])
    stop = report['stop']
    recording_keys(stop, 'stop')
    for key in ('request_id', 'ack_request_id', 'last_window'):
        recording_integer(stop[key], 1, 0xFFFFFFFE)
    recording_integer(stop['generation'], 2, 0xFFFFFFFE)
    recording_integer(stop['wire_session_nonce'], 1, 0xFFFFFFFFFFFFFFFF)
    require(nonce == stop['wire_session_nonce'] and stop['request_id'] == stop['ack_request_id']
            and stop['request_id'] > stop['generation'], 'recording_stop')
    for key, expected in {'status': 0, 'termination_reason': 'user_stop', 'evidence': 'stop_ack'}.items():
        recording_exact(stop[key], expected)
    segments = report['segments']
    require(type(segments) is list and 2 <= len(segments) <= 16, 'recording_segments')
    full, last_sequence, last_generation = 0, 0, 0
    for ordinal, entry in enumerate(segments):
        recording_keys(entry, 'segment_entry')
        recording_exact(entry['ordinal'], ordinal)
        require(entry['kind'] in ('capture', 'partial', 'abort'), 'recording_segment_kind')
        recording_integer(entry['sequence'], 1, 0xFFFFFFFE)
        recording_integer(entry['sample_count'], 0, 2048)
        hex_id(entry['raw_sha256']); hex_id(entry['samples_sha256'])
        provenance = entry['provenance']
        recording_keys(provenance, 'segment_provenance')
        recording_integer(provenance['generation'], 2, 0xFFFFFFFE)
        recording_integer(provenance['config_id'], 1, 0xFFFFFFFE)
        recording_integer(provenance['host_elapsed_ms'], 0, 45000)
        require(entry['sequence'] > last_sequence
                and last_generation < provenance['generation'] <= stop['generation'], 'recording_chronology')
        last_sequence, last_generation = entry['sequence'], provenance['generation']
        for key, expected in {'wire_session_nonce': nonce, 'acquisition_mode': 1, 'auto_timeout_ms': 0,
                              'wire_version': '0.1', 'start_operation': 'arm',
                              'config_payload_hex': '01000100000000080000000000000000',
                              'terminal_request_id': 0}.items():
            recording_exact(provenance[key], expected)
        recording_integer(provenance['terminal_sequence'], 1, 0xFFFFFFFE)
        if entry['kind'] == 'abort':
            for key, expected in {'begin_request_id': None, 'begin_sequence': None,
                                  'unavailable_reason': 'no_begin'}.items():
                recording_exact(provenance[key], expected)
            recording_exact(entry['sample_count'], 0)
        else:
            recording_exact(provenance['begin_request_id'], 0)
            recording_integer(provenance['begin_sequence'], 1, 0xFFFFFFFE)
            require(provenance['terminal_sequence'] > provenance['begin_sequence'], 'recording_chronology')
            recording_exact(provenance['unavailable_reason'], None)
        if entry['kind'] == 'capture':
            full += 1
            recording_exact(entry['sample_count'], 2048)
            recording_exact(provenance['terminal_kind'], 'end')
            recording_exact(provenance['abort_reason_code'], None)
            recording_integer(provenance['end_samples_crc32'], 0, 0xFFFFFFFF)
        else:
            recording_exact(provenance['terminal_kind'], 'abort')
            recording_exact(provenance['end_samples_crc32'], None)
            recording_integer(provenance['abort_reason_code'], 1, 6)
    require(full >= 2 and stop['last_window'] == last_sequence, 'recording_stop_window')
    recording_keys(report['executable'], 'executable')
    recording_integer(report['executable']['bytes'], 1, min(MAX_ARCHIVE, MAX_FILE, 128 * MIB))
    hex_id(report['executable']['sha256'])
    recording_keys(report['artifacts'], 'artifacts')
    for key, item in report['artifacts'].items():
        recording_keys(item, 'artifact_entry')
        recording_integer(item['bytes'], 1, min(MAX_FILE, MAX_RECORDING_ARTIFACT))
        hex_id(item['sha256'])
        relative(item['name'])
        require(item['name'] == 'recording.mtrec' if key == 'archive' else
                re.fullmatch(r'recording-[0-9a-f]{32}\.mtspool', item['name']) is not None,
                'recording_artifact_name')
    return full

require = need

class IO:
    """A single actual-byte ledger; failure handling cannot reset its counters."""
    def __init__(self):
        self.read_bytes = self.write_bytes = self.emitted_bytes = 0
        self.allowed = {}; self.seen = {}; self.dirs = {}; self.opens = []
        self.failing = False

    @property
    def total(self):
        return self.read_bytes + self.write_bytes + self.emitted_bytes

    def reserve(self, n):
        ceiling = LIMIT if self.failing else LIMIT - FAILURE_RESERVE
        need(type(n) is int and n >= 0 and n <= ceiling - self.total, 'auxiliary-budget')

    def extend(self, paths):
        planned = dict(self.allowed)
        for path, cap in paths.items():
            path = canonical(path); key = str(path).casefold()
            need(key not in planned, 'managed-path-alias')
            need(type(cap) is int and 0 < cap <= 512*KIB, 'leaf-cap')
            planned[key] = (path, cap)
        need(len(planned) <= 26, 'managed-path-count')
        self.allowed = planned

    def safe(self, path, directory=False):
        path = canonical(path)
        for item in [*reversed(path.parents), path]:
            s = item.lstat()
            need(not stat.S_ISLNK(s.st_mode) and not getattr(s,'st_file_attributes',0)&0x400,
                 'path-redirect')
            if item != path or directory:
                need(stat.S_ISDIR(s.st_mode), 'directory-type')
                identity = (s.st_dev, s.st_ino)
                need(item not in self.dirs or self.dirs[item] == identity, 'directory-changed')
                need(item in self.dirs or len(self.dirs) < 1664, 'directory-count')
                self.dirs[item] = identity
            else:
                need(regular(s), 'file-type')
        return s

    def cap(self, path):
        key = str(path).casefold()
        need(key in self.allowed and self.allowed[key][0] == path, 'unplanned-path')
        return self.allowed[key][1]

    def read(self, path, remember=True):
        cap = self.cap(path); before = self.safe(path)
        need(0 <= before.st_size <= cap, 'leaf-size'); self.reserve(before.st_size)
        fd = os.open(path, os.O_RDONLY | getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0))
        self.opens.append(str(path))
        try:
            first = os.fstat(fd)
            need(regular(first) and shared(first) == shared(before), 'read-open-identity')
            chunks = []; count = 0
            while count < before.st_size:
                n = min(65536, before.st_size-count); self.reserve(n)
                b = os.read(fd, n); self.read_bytes += len(b)
                need(0 < len(b) <= n, 'short-read'); chunks.append(b); count += len(b)
            last = os.fstat(fd)
            need(regular(last) and shared(last) == shared(first) and times(last) == times(first),
                 'read-handle-changed')
        finally:
            os.close(fd)
        final = self.safe(path)
        need(shared(final) == shared(before) and times(final) == times(before), 'read-path-changed')
        raw = b''.join(chunks); identity = (shared(final), times(final), sha(raw))
        need(path not in self.seen or self.seen[path] == identity, 'repeated-input-changed')
        if remember:
            self.seen[path] = identity
        return raw

    def write(self, path, raw):
        need(len(raw) <= self.cap(path), 'output-size'); self.reserve(2*len(raw))
        self.safe(path.parent, True)
        with os.scandir(path.parent) as entries:
            for count, entry in enumerate(entries, 1):
                need(count <= 4096, 'output-parent-limit')
                need(entry.name.casefold() != path.name.casefold(), 'output-exists-or-alias')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0), 0o600)
        try:
            first = os.fstat(fd)
            need(regular(first) and first.st_size == 0, 'write-open')
            offset = 0
            while offset < len(raw):
                chunk = raw[offset:offset+65536]; self.reserve(len(chunk))
                n = os.write(fd, chunk)
                need(0 < n <= len(chunk), 'short-write'); self.write_bytes += n; offset += n
            last = os.fstat(fd)
            need(regular(last) and last.st_size == len(raw) and
                 (first.st_dev,first.st_ino) == (last.st_dev,last.st_ino), 'write-handle')
        finally:
            os.close(fd)
        need(shared(self.safe(path)) == shared(last), 'write-path')
        need(self.read(path) == raw, 'write-readback')

    def unchanged(self):
        for path, expected in list(self.seen.items()):
            s = self.safe(path)
            need((shared(s),times(s)) == expected[:2], 'terminal-input-changed')
        for path, expected in list(self.dirs.items()):
            s = self.safe(path, True)
            need((s.st_dev,s.st_ino) == expected, 'terminal-directory-changed')

    def emit(self, value, fd=1):
        value = dict(value)
        value['managed_bytes'] = self.total
        value['terminal_bytes'] = 0
        for _ in range(8):
            raw = encoded(value)
            if value['terminal_bytes'] == len(raw) and value['managed_bytes'] == self.total+len(raw):
                break
            value['terminal_bytes'] = len(raw)
            value['managed_bytes'] = self.total+len(raw)
        else:
            raise Rejected('terminal-size-fixed-point')
        need(len(raw) <= 4096, 'terminal-output-size')
        offset = 0
        while offset < len(raw):
            chunk = raw[offset:]; self.reserve(len(chunk)); n = os.write(fd, chunk)
            need(0 < n <= len(chunk), 'terminal-write'); self.emitted_bytes += n; offset += n

def exact_keys(value, keys, label):
    need(type(value) is dict and set(value) == set(keys), label+'-keys')


def utc_stamp(value):
    recording_text(value,64)
    stamp=datetime.fromisoformat(value)
    need(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds()==0,'utc-timestamp')
    return stamp


def phases(value, expected):
    need(type(value) is list and len(value)==len(expected),'phase-count')
    previous=None
    for row, expected_name in zip(value,expected):
        exact_keys(row,('name','utc'),'phase')
        recording_exact(row['name'],expected_name)
        recording_text(row['utc'],64)
        stamp=datetime.fromisoformat(row['utc'])
        need(stamp.utcoffset() is not None,'gui-aware-timestamp')


def smoke_structures(serial, gui, recording):
    expected={'schema_version':1,'status':'passed','device_io_performed':False,
      'frozen':True,'os_name':'nt','platform':'win32','pyserial_version':'3.5',
      'scope':'frozen-windows-serial-import-only','serial_backend':'serial.serialwin32.Serial',
      'transport_class':'motortester.transport.SerialTransport',
      'imports':['serial','serial.serialutil','serial.serialwin32','serial.win32','motortester.transport']}
    need(same(serial,expected),'serial-structure')
    exact_keys(gui,('schema_version','status','phase','phases','platform','frozen',
                    'qt_platform','output','png','error'),'gui')
    for key,value in {'schema_version':1,'status':'passed','phase':'complete',
      'platform':'win32','frozen':True,'qt_platform':'windows','output':'frozen-window.png',
      'error':None}.items(): recording_exact(gui[key],value)
    phases(gui['phases'],['entry','gui-import','gui-imported','screenshot','png-validation','complete'])
    exact_keys(gui['png'],('bytes','sha256','width','height'),'png')
    hex_id(gui['png']['sha256'])
    recording_integer(gui['png']['bytes'],9,2*MIB)
    for key in ('width','height'):recording_integer(gui['png'][key],1,8192)
    need(gui['png']['width']*gui['png']['height']<=16000000,'png-dimensions')
    return recording_structure(recording)


def metadata_origin(value, native=False, row=None, origins=None):
    need(type(value) is dict and type(value.get('status')) is str,'origin-object')
    status=value['status']
    if status in ('outside_allowed_origins','namespace_or_no_file','no_collect_entry'):
        exact_keys(value,('status',),'unresolved-origin')
        need(not native or status!='namespace_or_no_file','native-namespace')
        need(native or status!='no_collect_entry','pure-no-collect')
        return
    need(status == ('hash_correspondence' if native else 'declared_input'),'origin-status')
    kind=value.get('origin_kind')
    need(type(kind) is str and kind in ('environment','interpreter','constructed'),'origin-kind')
    need(native or kind!='constructed','pure-constructed')
    keys={'status','origin_kind','relative_path','bytes','sha256'}
    if kind=='environment':keys|={'distribution','version'}
    exact_keys(value,keys,'origin')
    relative(value['relative_path']);hex_id(value['sha256'])
    recording_integer(value['bytes'],0,MAX_FILE)
    if kind=='environment':
        need(value['distribution'] in PINNED and type(value['distribution']) is str and
             value['version']==PINNED[value['distribution']] and type(value['version']) is str,
             'origin-distribution')
    parts=value['relative_path'].split('/')
    need(value['relative_path'].lower().endswith(('.dll','.pyd','.exe') if native else ('.py','.pyc')),
         'origin-file-suffix')
    if kind=='interpreter':
        need((len(parts)==1 or (len(parts)==2 and parts[0]=='DLLs')) if native else
             parts[0]=='Lib','interpreter-origin-path')
    if kind=='constructed':recording_exact(value['relative_path'],'Motortester.exe')
    if origins is not None:
        key=(kind,value['relative_path'].casefold())
        normalized=dict(value,relative_path=key[1])
        normalized.pop('status')  # Native and pure status differ; physical metadata does not.
        need(key not in origins or same(origins[key],normalized),'origin-identity-conflict')
        origins[key]=normalized
    if native:
        need(row is not None and same(value['bytes'],row['bytes']) and
             value['sha256']==row['sha256'],'native-origin-correspondence')


def nonoverlapping(spans, maximum):
    end=0
    for start,length in sorted(spans):
        need(start>=end and start+length<=maximum,'inventory-interval')
        end=start+length


def package_structures(manifest, provenance, archive):
    """Validate only serialized assertions; never upgrade them to execution evidence."""
    for key,value in {'status':'candidate-not-release-approved','python':'3.13.16',
                      'machine':'AMD64'}.items():recording_exact(manifest[key],value)
    utc_stamp(manifest['build_time_utc'])
    recording_text(manifest['platform'],256)
    need(manifest['platform'].startswith('Windows-'),'manifest-platform')
    package_versions(manifest['packages'])
    files=manifest['files'];need(type(files) is dict and 0<len(files)<4096,'manifest-files')
    folded={'build-manifest.json'}
    for path,hashed in files.items():
        relative(path);hex_id(hashed)
        need(path.casefold() not in folded and path.casefold()!='build-manifest.json','manifest-file-alias')
        folded.add(path.casefold())
    for path in files:
        parts=path.casefold().split('/')
        need(not any('/'.join(parts[:i]) in folded for i in range(1,len(parts))),
             'manifest-file-ancestor-collision')
    need('Motortester.exe' in files,'manifest-executable')
    exact_keys(manifest['serial_import_smoke'],('report','sha256','result'),'serial-extension')
    exact_keys(manifest['recording_smoke'],('report','sha256','result','source_binding','source_files'),
               'recording-extension')
    recording_exact(manifest['recording_smoke']['report'],RECORDING_REPORT)
    recording_exact(manifest['recording_smoke']['source_binding'],
                    'external clean checkout; not binary-to-source attestation')
    recording_integer(archive['binary_files'],1,4095)
    need(archive['binary_files']==len(files),'archive-binary-count')
    for key,value in {'schema_version':1,'scope':'read-only-candidate-provenance-inventory',
      'source_binding':'manifest-recorded; external source guard required',
      'inspector_commit_binding':'externally asserted; actual script bytes verified',
      'pyinstaller_version':'6.22.3','legal_clearance':False,
      'corresponding_source_verified':False,'replacement_verified':False,
      'limitations':['Stored module bytes are not decompressed, unmarshaled or executed.',
        'Build-table source associations are not source-to-bytecode proof.',
        'Origin hashes do not establish upstream build/source provenance or rights.',
        'Inventory does not test recipient replacement, source access or delivery.']}.items():
        need(same(provenance[key],value),'provenance-qualification-'+key)
    for key in ('pyz_sha256','pure_toc_sha256','collect_toc_sha256','executable_sha256',
                'build_manifest_sha256','inspector_sha256'):hex_id(provenance[key])
    for key in ('source_commit','inspector_commit'):hex_id(provenance[key],40)
    for key,low,high in [('payload_count',1,4095),('payload_bytes',1,1024*MIB),
                        ('native_count',1,4095),('native_unknown_count',0,4095),
                        ('executable_bytes',1,MAX_ARCHIVE),('pyz_bytes',17,MAX_ARCHIVE)]:
        recording_integer(provenance[key],low,high)
    need(provenance['payload_count']==len(files) and
         provenance['executable_sha256']==files['Motortester.exe'],'payload-projection')
    native=provenance['native_files']
    need(type(native) is list and len(native)==provenance['native_count'],'native-count')
    names=set();unknowns=0;total=0;origins={}
    for row in native:
        exact_keys(row,('path','bytes','sha256','origin'),'native-row')
        relative(row['path']);hex_id(row['sha256']);recording_integer(row['bytes'],0,MAX_FILE)
        need(row['path'] in files and files[row['path']]==row['sha256'] and
             row['path'].casefold() not in names and row['path'].lower().endswith(('.dll','.pyd','.exe')),
             'native-row-file')
        names.add(row['path'].casefold());total+=row['bytes']
        metadata_origin(row['origin'],True,row,origins)
        if row['origin'].get('origin_kind')=='constructed':
            need(row['bytes']==provenance['executable_bytes'] and
                 row['sha256']==provenance['executable_sha256'],'constructed-executable-projection')
        unknowns+=row['origin']['status']!='hash_correspondence'
        if row['path']=='Motortester.exe':
            need(row['bytes']==provenance['executable_bytes'],'executable-bytes-projection')
    need(names=={p.casefold() for p in files if p.lower().endswith(('.dll','.pyd','.exe'))},
         'native-complete-projection')
    need(unknowns==provenance['native_unknown_count'] and
         total<=provenance['payload_bytes']<=total+(len(files)-len(native))*MAX_FILE,
         'native-summary')
    recording_exact(provenance['inventory_status'],
                    'complete_with_unknown_origins' if unknowns else 'complete')
    entries=provenance['carchive_entries']
    need(type(entries) is list and 0<len(entries)<=16384,'carchive-count')
    names=set();spans=[];pyzs=[]
    for row in entries:
        exact_keys(row,('name','type','offset','stored_bytes','declared_uncompressed_bytes',
                        'compressed','stored_sha256'),'carchive-row')
        relative(row['name']);hex_id(row['stored_sha256'])
        need(row['name'].casefold() not in names and type(row['type']) is str and
             row['type'] in ('z','s','m','M') and type(row['compressed']) is bool,'carchive-kind')
        names.add(row['name'].casefold())
        recording_integer(row['offset'],0,MAX_ARCHIVE)
        recording_integer(row['stored_bytes'],1,MAX_ARCHIVE)
        recording_integer(row['declared_uncompressed_bytes'],0,MAX_ARCHIVE)
        need(row['compressed'] or row['stored_bytes']==row['declared_uncompressed_bytes'],
             'carchive-uncompressed-size')
        spans.append((row['offset'],row['stored_bytes']))
        if row['type']=='z':pyzs.append(row)
    nonoverlapping(spans,provenance['executable_bytes']-91)
    need(len(pyzs)==1 and pyzs[0]['name']=='PYZ.pyz' and pyzs[0]['compressed'] is False and
         pyzs[0]['stored_bytes']==provenance['pyz_bytes'] and
         pyzs[0]['stored_sha256']==provenance['pyz_sha256'],'pyz-projection')
    options=provenance['carchive_options']
    need(type(options) is list and len(options)+len(entries)<=16384 and
         all(type(v) is str and v=='pyi-contents-directory _internal' for v in options),
         'carchive-options')
    toc_bytes=sum(((18+len(label.encode('utf-8'))+1+15)//16)*16
                  for label in [row['name'] for row in entries]+options)
    need(max(start+length for start,length in spans)+toc_bytes+88+2<=
         provenance['executable_bytes'],'carchive-table-projection')
    modules=provenance['pyz_modules']
    need(type(modules) is list and 0<len(modules)<=16384,'pyz-module-count')
    names=set();spans=[]
    for row in modules:
        exact_keys(row,('name','kind','offset','stored_bytes','stored_sha256','source_association'),
                   'pyz-module')
        name(row['name']);need(all(p.isidentifier() for p in row['name'].split('.')),'module-name')
        need(row['name'] not in names and type(row['kind']) is str and
             row['kind'] in ('module','package','namespace'),'module-kind')
        names.add(row['name'])
        recording_integer(row['offset'],17,provenance['pyz_bytes']-1)
        recording_integer(row['stored_bytes'],0,provenance['pyz_bytes'])
        metadata_origin(row['source_association'],origins=origins)
        if row['kind']=='namespace':
            need(row['stored_bytes']==0 and row['stored_sha256'] is None and
                 row['source_association']=={'status':'namespace_or_no_file'},'namespace-projection')
        else:
            need(row['stored_bytes']>0 and row['source_association']['status']!='namespace_or_no_file',
                 'module-length')
            hex_id(row['stored_sha256']);spans.append((row['offset'],row['stored_bytes']))
    nonoverlapping(spans,provenance['pyz_bytes']-1)
    declared=provenance['declared_only_modules']
    need(type(declared) is list and len(declared)+len(modules)<=16384,'declared-only-count')
    for row in declared:
        exact_keys(row,('name','status','source_association'),'declared-only')
        name(row['name']);need(all(p.isidentifier() for p in row['name'].split('.')),'module-name')
        need(row['name'] not in names and row['status']=='declared_not_embedded','declared-only-name')
        names.add(row['name']);metadata_origin(row['source_association'],origins=origins)


def recording_projection(recording, raw, source_map, full):
    return dict(scope='recording-receipt-and-artifact-byte-correspondence',
      receipt_sha256=sha(raw),receipt_bytes=len(raw),native_success_receipt=True,
      runtime_execution_observed_by_inspector=False,archive_contents_validated=False,
      timing_budgets_verified_by_inspector=False,
      source_files_binding='externally asserted; not checked against source.zip',source_files=source_map,
      segment_count=len(recording['segments']),full_segment_count=full,
      artifacts={k:{'path':RECORDING_WORK+'/'+v['name'],'bytes':v['bytes'],'sha256':v['sha256']}
                 for k,v in recording['artifacts'].items()},
      synthetic=True,device_io_performed=False,physical_stop_verified=False)


class Gate:
    def __init__(self, config, io=None):
        self.io = io or IO(); self.config = config; self.identity = None
        run, attempt, workflow = (config[x] for x in ('run_id','attempt','workflow_sha'))
        need(re.fullmatch('[0-9]{1,20}',run) and re.fullmatch('[0-9]{1,4}',attempt) and
             re.fullmatch('[0-9a-f]{40}',workflow), 'event-identity')
        self.event = {'run_id':run,'attempt':attempt,'workflow_sha':workflow}
        temp = canonical(config['runner_temp']); self.io.safe(temp,True)
        self.output = temp / ('literal46-'+run+'-'+attempt)
        self.output.mkdir(mode=0o700); self.io.safe(self.output,True)
        build = temp / ('motortester-'+run+'-'+attempt)
        self.paths = {'current':temp/('native-observation-'+run+'-'+attempt+'.json'),
          'toc':build/'pyinstaller/motortester/COLLECT-00.toc',
          'manifest':build/'dist/Motortester/build-manifest.json',
          'provenance':temp/('provenance-'+run+'-'+attempt+'.json'),
          'archive':build/'archive-verification.json','checksums':build/'SHA256SUMS.txt',
          'serial':build/'frozen-serial-import.json','gui':build/'frozen-window.png.smoke.json',
          'recording':build/'frozen-recording.json',
          'notice_source':temp/('native-notice-source-'+run+'-'+attempt+'.bin'),
          'notice_copy':temp/('native-notice-copy-'+run+'-'+attempt+'.bin')}
        self.io.extend({self.paths['current']:CAPS['current']*KIB,
                        **{self.output/n:c*KIB for n,c in OUTPUTS.items()}})
        self.raw = {}

    def layout(self, names):
        self.io.safe(self.output,True); found=set()
        with os.scandir(self.output) as entries:
            for count, entry in enumerate(entries,1):
                need(count <= len(names) and entry.name in names and entry.name not in found,
                     'output-layout')
                self.io.safe(self.output/entry.name); found.add(entry.name)
        need(found == set(names), 'output-layout-missing')

    def retain_current(self):
        raw = self.io.read(self.paths['current']); self.raw['current'] = raw
        self.io.write(self.output/'current.original.bin',raw)
        identity = {'schema_version':1,'status':'retained_original',**self.event,
                    'original_path':str(self.paths['current']),
                    'original_bytes':len(raw),'original_sha256':sha(raw),
                    'retained_file':'current.original.bin','retained_bytes':len(raw),
                    'retained_sha256':sha(raw)}
        self.io.write(self.output/'current.identity.json',encoded(identity))
        self.layout({'current.original.bin','current.identity.json'}); self.io.unchanged()
        self.identity = identity

    def module(self, role):
        raw = self.raw[role]; need(same(record(raw),self.pins['pinned'][role]),'tool-pin-'+role)
        module = types.ModuleType('_aux_'+role); module.__file__=str(self.paths[role])
        exec(compile(raw,module.__file__,'exec'),module.__dict__)
        return module

    def check_joins(self):
        need(self.identity is not None,'retention-required')
        for role in ('self','pins','workflow','literal','observer','baseline','reference'):
            self.paths[role] = canonical(self.config[role])
        self.io.extend({p:CAPS[k]*KIB for k,p in self.paths.items() if k != 'current'})
        need(len(self.io.allowed) == 26,'exact-auxiliary-plan')
        for role,path in self.paths.items():
            if role != 'current': self.raw[role]=self.io.read(path)
        need(sha(self.raw['pins']) == PINS_SHA,'pins-body')
        self.pins = json.loads(self.raw['pins'])
        for role, expected in self.pins['pinned'].items():
            need(same(record(self.raw[role]),expected),'pinned-input-'+role)
        observer=self.module('observer'); literal=self.module('literal')
        current, reference = map(observer.decode,(self.raw['current'],self.raw['reference']))
        need(type(current) is dict and set(current)==set(reference),'current-schema')
        need(type(current['schema_version']) is int and current['schema_version']==1 and
             current['status']=='observed_with_unresolved_origins' and current['errors']==[] and
             current['final_inputs_unchanged'] is True and current['run17_baseline_matches'] is True,
             'current-status')
        need(type(current['read_bytes']) is int and
             0<=current['read_bytes']<=observer.INPUT_READ_LIMIT,'current-read-bytes')
        need(current['scope']==reference['scope'],'current-scope')
        need(all(current[k] is False for k in literal.CLEARANCE),'current-clearance')
        need(current['baseline_sha256']==sha(self.raw['baseline']),'current-baseline')
        hashes=current['input_hashes']; need(type(hashes) is dict and
              set(hashes)=={'manifest','provenance','archive','toc'},'input-hash-schema')
        for name,value in hashes.items():
            need(type(value) is str and re.fullmatch('[a-f0-9]{64}',value) and
                 value==sha(self.raw[name]),'input-hash-'+name)
        rows, context=literal.bind(observer,self.raw['reference'],self.raw['baseline'],
                                   self.raw['current'],self.raw['toc'],self.event)
        need(same(observer.native_rows(current['native_rows']),
                  observer.native_rows(reference['native_rows'])),'exact-native-row-schema')
        manifest, provenance, archive, _, targets, _=observer.bind_inputs(
            self.raw['manifest'],self.raw['provenance'],self.raw['archive'],
            self.raw['baseline'],self.raw['toc'])
        need(set(manifest)==set(self.pins['manifest_keys']) and
             set(archive)==set(self.pins['archive_keys']) and
             set(provenance)==set(self.pins['provenance_keys']),'package-schema')
        need(manifest['machine']=='AMD64' and manifest['usb_verified'] is False and
             manifest['windows_10_manual_acceptance']=='not performed by this script' and
             manifest['license_review']=='required; see LICENSE-REVIEW.md','manifest-qualification')
        need(archive['scope']=='on-runner-archive-content-only' and archive['bytes_delivered'] is False
             and archive['hardware_verified'] is False and
             archive['license_review']==manifest['license_review'],'archive-qualification')
        need(type(archive['source_files']) is int and archive['source_files']==304 and
             type(archive['binary_files']) is int and archive['binary_files']>0,'archive-counts')
        package_structures(manifest,provenance,archive)
        need(provenance['payload_bytes']+len(self.raw['manifest'])<=1024*MIB,
             'archive-expanded-byte-projection')
        for row in rows:
            target=targets[row['path'].casefold()]
            need(all(same(target[k],row[k]) for k in ('path','bytes','sha256')),'historical-row-join')
        serial, gui, recording=map(observer.decode,(self.raw['serial'],self.raw['gui'],self.raw['recording']))
        for role, receipt in [('serial',serial),('gui',gui),('recording',recording)]:
            need(type(receipt) is dict and set(receipt)==set(self.pins[role+'_keys']) and
                 type(receipt['schema_version']) is int and receipt['schema_version']==1 and
                 receipt['status']=='passed','smoke-envelope-'+role)
        full=smoke_structures(serial,gui,recording)
        expected_summary=recording_projection(recording,self.raw['recording'],self.pins['source_map'],full)
        need(same(archive['expected_inspector_recording'],expected_summary) and
             same(provenance['recording_smoke'],expected_summary),'recording-summary-projection')
        need(same(recording['executable'],{'bytes':provenance['executable_bytes'],
             'sha256':provenance['executable_sha256']}),'recording-executable-projection')
        need(serial['device_io_performed'] is False and serial['frozen'] is True and
             serial['platform']=='win32' and gui['frozen'] is True and gui['platform']=='win32' and
             gui['error'] is None and recording['error'] is None and
             gui['phase']=='complete' and recording['phase']=='complete','smoke-qualification')
        need(recording['checks']['synthetic'] is True and
             recording['checks']['device_io_performed'] is False and
             recording['checks']['physical_stop_verified'] is False,'recording-qualification')
        need(same(manifest['serial_import_smoke']['result'],serial) and
             manifest['serial_import_smoke']['report']=='frozen-serial-import.json' and
             manifest['serial_import_smoke']['sha256']==sha(self.raw['serial']) and
             same(manifest['frozen_gui_smoke'],gui),'serial-gui-join')
        need(same(manifest['recording_smoke']['source_files'],self.pins['source_map']) and
             len(self.pins['source_map'])==23,'authoritative-source-map')
        fn=self.pins['recording_function']
        code=textwrap.dedent('\n'.join(self.raw['workflow'].decode('utf-8').splitlines()[
            fn['first_line']-1:fn['last_line']])+'\n')
        need(sha(code.encode())==fn['sha256'],'recording-function-pin')
        env={'SOURCE_SHA':self.pins['application'],'INSPECTOR_SHA':observer.INSPECTOR,
             'INSPECTOR_FILE_SHA256':observer.INSPECTOR_HASH}
        namespace={'os':types.SimpleNamespace(environ=env),'hashlib':hashlib,
                   'same_json':same,'decode':observer.decode}
        exec(compile(code,'<pinned-recording-postcondition>','exec'),namespace)
        namespace['verify_recording_postcondition'](provenance,archive,self.raw['recording'],
                                                   manifest,sha(self.raw['manifest']))
        spool=recording['artifacts']['spool']['name']
        need(type(spool) is str and re.fullmatch('recording-[0-9a-f]{32}\\.mtspool',spool),
             'recording-spool-name')
        names={'Motortester-windows-x64-candidate.zip','source.zip','frozen-window.png',
               'frozen-window.png.smoke.json','frozen-serial-import.json','frozen-recording.json',
               'frozen-recording.json.work/recording.mtrec','frozen-recording.json.work/'+spool}
        checks={}
        for line in self.raw['checksums'].decode('ascii').splitlines():
            match=re.fullmatch('([0-9a-f]{64})  (.+)',line)
            need(match is not None and match[2] in names and match[2] not in checks,'checksum-line')
            checks[match[2]]=match[1]
        need(set(checks)==names and same(checks,archive['artifacts_sha256']) and
             set(archive['artifact_bytes'])==names and all(type(n) is int and n>0
             for n in archive['artifact_bytes'].values()) and
             archive['checksum_file_sha256']==sha(self.raw['checksums']),'checksum-map')
        for artifact,size in archive['artifact_bytes'].items():
            cap=(200*MIB if artifact=='Motortester-windows-x64-candidate.zip' else
                 20*MIB if artifact=='source.zip' else 65536 if artifact==RECORDING_REPORT else
                 2*MIB if artifact.endswith('.png') or artifact.startswith(RECORDING_WORK+'/') else MIB)
            recording_integer(size,1,cap)
        for role,name in [('serial','frozen-serial-import.json'),('gui','frozen-window.png.smoke.json'),
                          ('recording','frozen-recording.json')]:
            need(checks[name]==sha(self.raw[role]) and archive['artifact_bytes'][name]==len(self.raw[role]),
                 'receipt-original-'+role)
        for role in ('archive','spool'):
            a=recording['artifacts'][role]; name='frozen-recording.json.work/'+a['name']
            need(role!='archive' or a['name']=='recording.mtrec','recording-archive-name')
            need(name in names and a['sha256']==checks[name] and
                 type(a['bytes']) is int and a['bytes']==archive['artifact_bytes'][name],
                 'recording-artifact-join')
            need(same(archive['expected_inspector_recording']['artifacts'][role],
                      {'path':name,'bytes':a['bytes'],'sha256':a['sha256']}),'recording-summary-artifact')
        need(gui['output']=='frozen-window.png' and gui['png']['sha256']==checks['frozen-window.png'] and
             type(gui['png']['bytes']) is int and gui['png']['bytes']==archive['artifact_bytes']['frozen-window.png'],
             'gui-png-metadata-join')
        notice=current['cpython_notice']; expected=reference['cpython_notice']
        need(set(notice)==set(expected) and notice['matches_run17_hash'] is True and
             notice['source_copy_equal'] is True,'notice-schema')
        for role in ('source','copy'):
            raw=self.raw['notice_'+role]; item=notice[role]
            name='native-notice-'+role+'-'+self.event['run_id']+'-'+self.event['attempt']+'.bin'
            want={'status':'retained_original',**record(raw),'retention':{'file':name,**record(raw)}}
            need(same(item,want) and sha(raw)==expected[role]['sha256'] and
                 manifest['files'].get('licenses/CPython-LICENSE.txt')==sha(raw),'notice-join')
        need(self.raw['notice_source']==self.raw['notice_copy'],'notice-copy')
        need(sum(row['bytes'] for row in provenance['native_files'])+len(self.raw['notice_source'])<=
             provenance['payload_bytes'],'known-payload-byte-projection')
        self.result={'schema_version':1,'status':'UNCOMMITTED_AUXILIARY_METADATA',
                     'scope':'data-only-receipt-joins','context':context,'native_row_count':46,
                     'original_identity':record(encoded(self.identity)),
                     'inputs':{k:record(v) for k,v in self.raw.items()},
                     'mandatory_prior_package_stage_still_required':True,
                     **{k:False for k in FALSE_FIELDS}}
        return self.result

    def finalize(self):
        need(hasattr(self,'result'),'joins-required')
        for role,path in self.paths.items():
            need(self.io.read(path)==self.raw[role],'final-input-bytes')
        raw=encoded(self.result); self.io.write(self.output/'package-gate.json',raw)
        need(self.io.read(self.output/'current.original.bin')==self.raw['current'] and
             self.io.read(self.output/'current.identity.json')==encoded(self.identity) and
             self.io.read(self.output/'package-gate.json')==raw,'final-output-bytes')
        self.layout({'current.original.bin','current.identity.json','package-gate.json'})
        self.io.unchanged()
        return {'schema_version':1,'status':'data_only_joins_validated','report':record(raw),
                'original':record(self.raw['current']),'managed_bytes_before_terminal':self.io.total,
                'managed_limit':LIMIT,'planned_auxiliary_leaves':26,
                **{k:False for k in FALSE_FIELDS}}

    def verified_retention(self):
        if self.identity is None:
            return None
        previous_failure_mode = self.io.failing
        self.io.failing = False  # Keep the final 16 KiB for failure output, not rereads.
        try:
            need(self.io.read(self.output/'current.original.bin') == self.raw['current'] and
                 self.io.read(self.output/'current.identity.json') == encoded(self.identity),
                 'failure-retention-changed')
            # Recheck this pair after the last readback, not unrelated failed inputs.
            for path in (self.output/'current.original.bin',self.output/'current.identity.json'):
                current=self.io.safe(path)
                expected=self.io.seen[path]
                need((shared(current),times(current))==expected[:2],
                     'failure-retention-terminal-changed')
            return self.identity
        except Exception:
            return None
        finally:
            self.io.failing = previous_failure_mode

    def failure(self, error):
        self.io.failing=True
        retained = self.verified_retention()
        result={'schema_version':1,'status':'AUXILIARY_REJECTED',
                'failure_artifact_status':'UNCOMMITTED_FAILURE_METADATA',
                'reason':type(error).__name__+': '+str(error)[:160],
                'retained_original':retained,'retention_completed':retained is not None,
                'observed_original':record(self.raw['current']) if 'current' in self.raw else None,
                'full_original_hash_available':'current' in self.raw,
                'target_rows':46,'native_rows_observed':0,
                **{k:False for k in FALSE_FIELDS}}
        self.io.write(self.output/'failure.json',encoded(result))
        # Only the terminal message commits a fresh retention observation.
        if retained is not None and self.verified_retention() is None:
            result['retained_original'] = None
            result['retention_completed'] = False
        return result

class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise Rejected('arguments')

def main():
    io=IO(); gate=None
    try:
        parser=Parser(description=__doc__,add_help=False)
        for name in ('runner-temp','run-id','attempt','workflow-sha','workflow','literal',
                     'observer','baseline','reference'):
            parser.add_argument('--'+name,required=True)
        config=vars(parser.parse_args()); config['self']=str(Path(__file__).absolute())
        config['pins']=str(Path(__file__).absolute().with_name('pins.json'))
        gate=Gate(config,io); gate.retain_current(); gate.check_joins()
        io.emit(gate.finalize()); return 0
    except Exception as error:
        io.failing=True
        try:
            result=gate.failure(error) if gate else {'status':'AUXILIARY_SETUP_REJECTED',
                   'reason':type(error).__name__,'full_original_hash_available':False,
                   **{k:False for k in FALSE_FIELDS}}
            io.emit(result,2)
        except Exception:
            # Existing originals/partial outputs are intentionally left untouched.
            try:io.emit({'status':'AUXILIARY_REJECTED','failure_output_unavailable':True,
                         'observed_original':record(gate.raw['current']) if gate and 'current' in gate.raw else None,
                         'retention_completed':False,
                         'retention_verification_available':False,
                         **{k:False for k in FALSE_FIELDS}},2)
            except Exception:pass
        return 1

if __name__=='__main__':
    raise SystemExit(main())

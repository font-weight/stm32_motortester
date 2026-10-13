"""Pinned-format fixtures only; these tests do not build/run a Windows candidate."""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import marshal
import os
from pathlib import Path
import random
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / 'packaging' / 'inspect_candidate_provenance.py'
spec = importlib.util.spec_from_file_location('candidate_provenance_under_test', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
SOURCE = 'db86d2247f02dbc6f770a674654f5c5bf9d92f99'
INSPECTOR = 'a' * 40
MAGIC = importlib.util.MAGIC_NUMBER
TEST_RECORDING_SOURCE_FILES = ('motortester/__init__.py',
 'motortester/__main__.py',
 'motortester/acquisition.py',
 'motortester/device_emulator.py',
 'motortester/emulator.py',
 'motortester/gui.py',
 'motortester/gui_acquisition.py',
 'motortester/lv_collector.py',
 'motortester/model.py',
 'motortester/plotting.py',
 'motortester/recording.py',
 'motortester/recording_bridge.py',
 'motortester/recording_controller.py',
 'motortester/runtime.py',
 'motortester/session.py',
 'motortester/session_archive.py',
 'motortester/transport.py',
 'motortester/window.py',
 'motortester/wire.py',
 'motortester/wire_demo.py',
 'packaging/launch_gui.py',
 'packaging/recording_smoke.py',
 'packaging/build_windows.py')


def pyz(entries=None, toc=None):
    entries = entries or [('PySide6._config', 0, b'never execute module bytes'),
                          ('namespace', 3, b'')]
    data, rows = bytearray(b'PYZ\0' + MAGIC + b'\0' * 9), []
    for label, kind, raw in entries:
        stored = zlib.compress(raw) if kind != 3 else b''
        rows.append((label, (kind, len(data), len(stored))))
        data.extend(stored)
    offset = len(data)
    data.extend(marshal.dumps(rows) if toc is None else toc)
    data[8:12] = struct.pack('!i', offset)
    return bytes(data)


def carchive(pyz_bytes=None, entries=None):
    pyz_bytes = pyz() if pyz_bytes is None else pyz_bytes
    entries = entries or [('bootstrap', 'm', 1, b'not a code object'),
                          ('pyi-contents-directory _internal', 'o', 0, b''),
                          ('PYZ.pyz', 'z', 0, pyz_bytes)]
    body, table = bytearray(), bytearray()
    for label, typ, compression, raw in entries:
        stored = zlib.compress(raw) if compression else raw
        encoded = label.encode() + b'\0'
        size = (p.ENTRY.size + len(encoded) + 15) // 16 * 16
        encoded = encoded.ljust(size - p.ENTRY.size, b'\0')
        table.extend(p.ENTRY.pack(size, len(body), len(stored), len(raw), compression, typ.encode()) + encoded)
        body.extend(stored)
    cookie = p.COOKIE.pack(p.COOKIE_MAGIC, len(body) + len(table) + p.COOKIE.size,
                           len(body), len(table), 313, b'python313.dll'.ljust(64, b'\0'))
    return b'MZfixture-stub' + body + table + cookie


@contextlib.contextmanager
def windows_stat_fixture(path, path_changes=None, handle_changes=None):
    """Model pinned Windows path birthtime versus handle ChangeTime on Linux."""
    actual_stat, actual_fstat = Path.stat, p.os.fstat
    counts = {'path': 0, 'handle': 0}
    def shape(real, api):
        counts[api] += 1
        values = dict(st_mode=real.st_mode, st_dev=real.st_dev, st_ino=real.st_ino,
                      st_size=real.st_size, st_mtime_ns=real.st_mtime_ns,
                      st_ctime_ns=1000000000 if api == 'path' else 2000000000,
                      st_birthtime_ns=1000000000)
        changes = path_changes if api == 'path' else handle_changes
        values.update((changes or {}).get(counts[api], {}))
        return SimpleNamespace(**values)
    def path_stat(value, *args, **kwargs):
        real = actual_stat(value, *args, **kwargs)
        return shape(real, 'path') if value == path and kwargs.get('follow_symlinks', True) else real
    def handle_stat(fd):
        return shape(actual_fstat(fd), 'handle')
    with mock.patch.object(Path, 'stat', path_stat), mock.patch.object(p.os, 'fstat', handle_stat):
        yield counts


class Fixture:
    def __init__(self, root):
        self.root = root
        self.build = root / 'build'
        self.python = root / 'python'
        self.bundle = self.build / 'dist/Motortester'
        self.work = self.build / 'pyinstaller/motortester'
        self.site = self.build / 'venv/Lib/site-packages'
        for path in (self.python, self.bundle, self.work, self.site):
            path.mkdir(parents=True)
        self.reader_hashes = {}
        self.members = {dist: [] for dist in p.PINNED}
        for path in p.READER_HASHES:
            data = ('fixture versioned reader source: ' + path).encode()
            self.put(self.site / path, data)
            self.reader_hashes[path] = p.digest(data)
            self.members['pyinstaller'].append(path)
        self.module_path = self.site / 'PySide6/_config.py'
        self.put(self.module_path, b'raise RuntimeError("EXTRACTED_CODE_WAS_EXECUTED")\n')
        self.native_path = self.site / 'PySide6/example.dll'
        self.put(self.native_path, b'fixture-native-bytes')
        self.members['pyside6-essentials'] += ['PySide6/_config.py', 'PySide6/example.dll']
        self.dist_folders = {}
        for dist, version in p.PINNED.items():
            folder = self.site / (dist.replace('-', '_') + '-' + version + '.dist-info')
            self.dist_folders[dist] = folder
            self.put(folder / 'METADATA', f'Name: {dist}\nVersion: {version}\n'.encode())
            self.put(folder / 'RECORD', ''.join(x + ',,\n' for x in self.members[dist]).encode())
        self.pyz = pyz()
        self.exe = carchive(self.pyz)
        self.put(self.work / 'PYZ-00.pyz', self.pyz)
        self.put(self.work / 'Motortester.exe', self.exe)
        self.put(self.bundle / 'Motortester.exe', self.exe)
        self.put(self.bundle / '_internal/PySide6/example.dll', self.native_path.read_bytes())
        self.put(self.bundle / '_internal/unknown.dll', b'outside-origin')
        self.put(self.bundle / 'notes.txt', b'non-native payload')
        self.collect = [('Motortester.exe', str(self.work / 'Motortester.exe'), 'EXECUTABLE'),
                        ('PySide6/example.dll', str(self.native_path), 'BINARY'),
                        ('unknown.dll', str(root / 'DO_NOT_READ_SECRET.dll'), 'BINARY')]
        self.pure = [('PySide6._config', str(self.module_path), 'PYMODULE'), ('namespace', None, 'PYMODULE')]
        self.tables()
        self.manifest = dict(status='candidate-not-release-approved', source_commit=SOURCE,
                             python='3.13.16', machine='AMD64', platform='Windows-fixture',
                             build_time_utc='2026-10-12T17:26:33+00:00', usb_verified=False,
                             windows_10_manual_acceptance='not performed by this script',
                             frozen_gui_smoke={}, serial_import_smoke={},
                             license_review='required; see LICENSE-REVIEW.md',
                             packages=[dict(name=k, version=v) for k, v in p.PINNED.items()])
        self.rehash()

    @staticmethod
    def put(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def tables(self):
        self.put(self.work / 'COLLECT-00.toc', repr((self.collect,)).encode())
        self.put(self.work / 'PYZ-00.toc', repr((str(self.work / 'PYZ-00.pyz'), self.pure)).encode())

    def rehash(self):
        self.manifest['files'] = {x.relative_to(self.bundle).as_posix(): p.digest(x.read_bytes())
                                  for x in self.bundle.rglob('*') if x.is_file() and x.name != 'build-manifest.json'}
        self.save_manifest()

    def save_manifest(self):
        self.raw_manifest = json.dumps(self.manifest).encode()
        self.put(self.bundle / 'build-manifest.json', self.raw_manifest)

    def inspect(self, **overrides):
        args = dict(build=self.build, source_sha=SOURCE, manifest_sha=p.digest(self.raw_manifest),
                    inspector_commit=INSPECTOR, inspector_sha=p.digest(SCRIPT.read_bytes()))
        args.update(overrides)
        with mock.patch.object(p, 'runtime_guard'), mock.patch.object(p.sys, 'base_prefix', str(self.python)), \
                mock.patch.object(p, 'READER_HASHES', self.reader_hashes):
            return p.inspect_candidate(**args)


class RecordingFixture(Fixture):
    """Opaque artifact bytes and fake-native receipt; no Windows/archive execution."""
    def __init__(self, root, kinds=('capture', 'capture')):
        super().__init__(root)
        self.recording_path = self.build / 'frozen-recording.json'
        self.recording_work = self.build / 'frozen-recording.json.work'
        self.recording_work.mkdir()
        spool = 'recording-00000000000040008000000000000002.mtspool'
        self.put(self.recording_work / 'recording.mtrec', b'opaque generated archive fixture')
        self.put(self.recording_work / spool, b'opaque generated spool fixture')
        segments = []
        for i, kind in enumerate(kinds):
            provenance = dict(generation=3 + 3 * i, config_id=1, acquisition_mode=1, auto_timeout_ms=0,
                host_elapsed_ms=i * 100, wire_version='0.1', start_operation='arm', wire_session_nonce=123,
                config_payload_hex='01000100000000080000000000000000', begin_request_id=0,
                begin_sequence=10 + 20 * i, terminal_request_id=0, terminal_sequence=20 + 20 * i,
                terminal_kind='end' if kind == 'capture' else 'abort',
                end_samples_crc32=123 if kind == 'capture' else None,
                abort_reason_code=None if kind == 'capture' else 1,
                unavailable_reason='no_begin' if kind == 'abort' else None)
            if kind == 'abort': provenance.update(begin_request_id=None, begin_sequence=None)
            segments.append(dict(ordinal=i, kind=kind, sequence=i + 1,
                sample_count=2048 if kind == 'capture' else 0 if kind == 'abort' else 17,
                raw_sha256='a' * 64, samples_sha256='b' * 64, provenance=provenance))
        self.recording = dict(schema_version=1, status='passed', scope='frozen-windows-recording',
            phase='complete', error=None, output='frozen-recording.json', workdir='frozen-recording.json.work',
            phases=[dict(name=v, utc=f'2026-10-12T00:00:{i:02d}+00:00') for i, v in enumerate(
                ('entry', 'gui', 'connect', 'record', 'stop', 'save', 'open', 'navigation', 'shutdown', 'complete'))],
            runtime=dict(platform='win32', os_name='nt', os_release='10', os_version='10.0.fixture',
                machine='AMD64', python='3.13.16', implementation='CPython', python_bits=64,
                qt='6.12.0', pyside='6.12.0', qt_platform='windows', frozen=True, optimize=0, application='0.1.0'),
            imports=['motortester.gui', 'motortester.gui_acquisition', 'motortester.acquisition',
                'motortester.recording_bridge', 'motortester.recording_controller', 'motortester.session_archive',
                'motortester.device_emulator', 'motortester.runtime', 'motortester.session', 'motortester.wire',
                'motortester.window', 'motortester.recording', 'motortester.model'],
            checks=dict(synthetic=True, device_io_performed=False, physical_stop_verified=False,
                coverage='segmented', intersegment_device_gap='unknown', time_basis='per_segment_nominal_unverified',
                source_binding='external-clean-build-manifest', observed_bytes_and_provenance_match=True,
                independent_pattern_match=True, fresh_gui_open_and_selection=True),
            identity=dict(recording_id='00000000-0000-4000-8000-000000000001',
                session_id='00000000-0000-0000-0000-00000000007b', origin='simulator',
                firmware_version='fixture', protocol_version='0.1', application_version='0.1.0'),
            stop=dict(request_id=3 * len(kinds) + 2, ack_request_id=3 * len(kinds) + 2,
                wire_session_nonce=123, generation=3 * len(kinds) + 1, last_window=len(kinds), status=0,
                termination_reason='user_stop', evidence='stop_ack'), segments=segments,
            artifacts={key: dict(name=name, bytes=(self.recording_work / name).stat().st_size,
                                  sha256=p.digest((self.recording_work / name).read_bytes()))
                for key, name in [('archive', 'recording.mtrec'), ('spool', spool)]},
            executable=dict(bytes=len(self.exe), sha256=p.digest(self.exe)),
            cleanup=dict(acquisition_native_joined=True, writer_stopped=True, windows_closed=True,
                pending_io=False, finalization_invalidated=False))
        self.manifest['recording_smoke'] = dict(report='frozen-recording.json', sha256='0' * 64,
            source_binding='external clean checkout; not binary-to-source attestation',
            source_files={key: 'c' * 64 for key in TEST_RECORDING_SOURCE_FILES})
        self.save_recording()

    def save_recording(self, raw=None, bind_result=True, bind_hash=True):
        raw = json.dumps(self.recording).encode() if raw is None else raw
        self.recording_path.write_bytes(raw)
        extension = self.manifest['recording_smoke']
        if bind_hash: extension['sha256'] = p.digest(raw)
        if bind_result: extension['result'] = copy.deepcopy(self.recording)
        self.save_manifest()


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_recording_variant_binds_bytes_and_reports_only_qualified_summary(self):
        f = RecordingFixture(self.root / 'new')
        before = {x.relative_to(f.root).as_posix(): p.digest(x.read_bytes()) for x in f.root.rglob('*') if x.is_file()}
        report = f.inspect()
        after = {x.relative_to(f.root).as_posix(): p.digest(x.read_bytes()) for x in f.root.rglob('*') if x.is_file()}
        self.assertEqual(before, after)
        summary = report['recording_smoke']
        self.assertEqual(summary['receipt_sha256'], p.digest(f.recording_path.read_bytes()))
        self.assertEqual(summary['receipt_bytes'], f.recording_path.stat().st_size)
        self.assertEqual(summary['segment_count'], 2)
        self.assertEqual(summary['full_segment_count'], 2)
        self.assertEqual(summary['source_files'], f.manifest['recording_smoke']['source_files'])
        for key in ('runtime_execution_observed_by_inspector', 'archive_contents_validated',
                    'timing_budgets_verified_by_inspector', 'device_io_performed', 'physical_stop_verified'):
            self.assertIs(summary[key], False)
        self.assertEqual(report['native_unknown_count'], 1)
        self.assertEqual(report['inventory_status'], 'complete_with_unknown_origins')
        for key in ('legal_clearance', 'corresponding_source_verified', 'replacement_verified'):
            self.assertIs(report[key], False)
        legacy = Fixture(self.root / 'old').inspect()
        self.assertNotIn('recording_smoke', legacy)
        self.assertEqual(set(report) - {'recording_smoke'}, set(legacy))

    def test_recording_partial_abort_and_independent_spool_uuid(self):
        f = RecordingFixture(self.root, ('capture', 'capture', 'partial', 'abort'))
        self.assertNotEqual(f.recording['identity']['recording_id'].replace('-', ''),
                            f.recording['artifacts']['spool']['name'][10:-8])
        self.assertEqual(f.inspect()['recording_smoke']['segment_count'], 4)
        for count in (0, 2048):
            changed = copy.deepcopy(f.recording); changed['segments'][2]['sample_count'] = count
            self.assertEqual(p.recording_structure(changed), 2)
        for case in ('partial-reason', 'abort-begin', 'abort-count', 'capture-crc', 'partial-crc', 'abort-reason'):
            changed = copy.deepcopy(f.recording)
            if case == 'partial-reason': changed['segments'][2]['provenance']['unavailable_reason'] = 'unknown'
            if case == 'abort-begin': changed['segments'][3]['provenance']['begin_sequence'] = 1
            if case == 'abort-count': changed['segments'][3]['sample_count'] = 1
            if case == 'capture-crc': changed['segments'][0]['provenance']['end_samples_crc32'] = None
            if case == 'partial-crc': changed['segments'][2]['provenance']['end_samples_crc32'] = 0
            if case == 'abort-reason': changed['segments'][3]['provenance']['abort_reason_code'] = 7
            with self.subTest(case=case), self.assertRaises(p.Invalid): p.recording_structure(changed)

    def test_recording_exact_manifest_extension_and_external_source_keys(self):
        f = RecordingFixture(self.root)
        original = copy.deepcopy(f.manifest)
        mutations = [('extra-root', lambda m: m.update(other_smoke={})),
                     ('extra-extension', lambda m: m['recording_smoke'].update(other='x')),
                     ('wrong-report', lambda m: m['recording_smoke'].update(report='../private.json')),
                     ('wrong-binding', lambda m: m['recording_smoke'].update(source_binding='embedded')),
                     ('unknown-source', lambda m: m['recording_smoke']['source_files'].update({'/private/secret': 'c' * 64})),
                     ('missing-source', lambda m: m['recording_smoke']['source_files'].pop(TEST_RECORDING_SOURCE_FILES[0])),
                     ('wrong-source-hash', lambda m: m['recording_smoke']['source_files'].update({TEST_RECORDING_SOURCE_FILES[0]: True}))]
        for key in original['recording_smoke']:
            mutations.append(('missing-' + key, lambda m, key=key: m['recording_smoke'].pop(key)))
        for label, mutate in mutations:
            f.manifest = copy.deepcopy(original); mutate(f.manifest); f.save_manifest()
            with self.subTest(label=label), self.assertRaises(p.Invalid): f.inspect()

    def test_recording_all_nested_key_sets_reject_missing_and_extra(self):
        good = RecordingFixture(self.root).recording
        objects = []
        def visit(value, path=()):
            if type(value) is dict:
                objects.append(path)
                for key, item in value.items(): visit(item, path + (key,))
            elif type(value) is list:
                for i, item in enumerate(value): visit(item, path + (i,))
        visit(good)
        for path in objects:
            node = good
            for key in path: node = node[key]
            for key in list(node) + ['UNEXPECTED']:
                changed = copy.deepcopy(good); target = changed
                for part in path: target = target[part]
                if key == 'UNEXPECTED': target[key] = None
                else: target.pop(key)
                with self.subTest(path=path, key=key), self.assertRaises(p.Invalid):
                    p.recording_structure(changed)

    def test_recording_recursive_types_reject_bool_numeric_and_null_substitutions(self):
        good = RecordingFixture(self.root).recording
        values = []
        def visit(value, path=()):
            if type(value) is dict:
                for key, item in value.items(): visit(item, path + (key,))
            elif type(value) is list:
                for i, item in enumerate(value): visit(item, path + (i,))
            else: values.append((path, value))
        visit(good)
        for path, value in values:
            replacements = ([True, float(value), float('inf')] if type(value) is int else
                            [int(value)] if type(value) is bool else ['null'] if value is None else [None])
            for replacement in replacements:
                changed = copy.deepcopy(good); node = changed
                for key in path[:-1]: node = node[key]
                node[path[-1]] = replacement
                with self.subTest(path=path, replacement=replacement), self.assertRaises((p.Invalid, ValueError)):
                    p.recording_structure(changed)
        self.assertFalse(p.same_json({'x': [True]}, {'x': [1]}))
        self.assertFalse(p.same_json({'x': [1]}, {'x': [1.0]}))

    def test_recording_source_or_non_native_receipt_cannot_be_success(self):
        f = RecordingFixture(self.root)
        cases = [('scope', 'source-or-nonnative-development'), ('platform', 'linux'),
                 ('os_name', 'posix'), ('frozen', False), ('qt_platform', 'offscreen'),
                 ('python', '3.12.14'), ('qt', '6.11.0'), ('pyside', '6.11.0'),
                 ('machine', 'ARM64'), ('optimize', 1), ('implementation', 'PyPy')]
        for key, value in cases:
            changed = copy.deepcopy(f.recording)
            (changed if key == 'scope' else changed['runtime'])[key] = value
            with self.subTest(key=key), self.assertRaises(p.Invalid): p.recording_structure(changed)

    def test_recording_stop_segment_chronology_and_nullable_semantics(self):
        good = RecordingFixture(self.root).recording
        changes = [(('stop', 'ack_request_id'), 1), (('stop', 'request_id'), 1),
                   (('stop', 'wire_session_nonce'), 124), (('stop', 'generation'), 5),
                   (('stop', 'last_window'), 1), (('stop', 'status'), 1),
                   (('segments', 1, 'sequence'), 1), (('segments', 1, 'ordinal'), 0),
                   (('segments', 1, 'provenance', 'generation'), 3),
                   (('segments', 0, 'provenance', 'generation'), 1),
                   (('segments', 0, 'provenance', 'terminal_sequence'), 10),
                   (('segments', 0, 'provenance', 'wire_session_nonce'), 124),
                   (('segments', 0, 'provenance', 'begin_request_id'), 1),
                   (('segments', 0, 'provenance', 'terminal_request_id'), 1),
                   (('segments', 0, 'provenance', 'config_payload_hex'), '00' * 16),
                   (('segments', 0, 'sample_count'), 2047)]
        for path, value in changes:
            changed = copy.deepcopy(good); node = changed
            for key in path[:-1]: node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(p.Invalid): p.recording_structure(changed)
        # A later final Stop generation is valid; it need not equal the last segment.
        changed = copy.deepcopy(good); changed['stop'].update(generation=20, request_id=21, ack_request_id=21)
        self.assertEqual(p.recording_structure(changed), 2)

    def test_recording_phase_order_is_not_monotonic_deadline_proof(self):
        good = RecordingFixture(self.root).recording
        changed = copy.deepcopy(good); changed['phases'][-1]['utc'] = '2050-01-01T00:00:00+00:00'
        self.assertEqual(p.recording_structure(changed), 2)
        for value in ('bad', '2026-10-12T00:00:00', '2026-10-12T00:00:00+02:00',
                      '2025-10-12T00:00:00+00:00'):
            changed = copy.deepcopy(good); changed['phases'][-1]['utc'] = value
            with self.subTest(value=value), self.assertRaises((p.Invalid, ValueError)):
                p.recording_structure(changed)
        changed = copy.deepcopy(good); changed['phases'][0]['name'] = 'complete'
        with self.assertRaises(p.Invalid): p.recording_structure(changed)

    def test_recording_original_receipt_bytes_not_nested_json_substitute(self):
        f = RecordingFixture(self.root)
        original = f.recording_path.read_bytes()
        f.save_recording(raw=original + b' ', bind_hash=False)
        with self.assertRaisesRegex(p.Invalid, 'recording_receipt_hash'): f.inspect()
        f.save_recording(raw=original + b' ')
        self.assertEqual(f.inspect()['recording_smoke']['receipt_bytes'], len(original) + 1)
        f.manifest['recording_smoke']['result']['runtime']['os_version'] = 'different'
        f.save_manifest()
        with self.assertRaisesRegex(p.Invalid, 'recording_receipt_result'): f.inspect()
        f.recording_path.unlink()
        with self.assertRaises(OSError): f.inspect()

    def test_recording_duplicate_nonfinite_deep_and_oversized_json_rejected(self):
        f = RecordingFixture(self.root)
        original = f.recording_path.read_bytes()
        malformed = [b'', b'{', b'\xff', b'{"schema_version":1,' + original[1:],
                     original.replace(b'"schema_version": 1', b'"schema_version": NaN'),
                     original.replace(b'"schema_version": 1', b'"schema_version": 1e999'),
                     b'[' * 10 + b'0' + b']' * 10, b' ' * 65537]
        for raw in malformed:
            f.save_recording(raw=raw)
            with self.subTest(raw=raw[:30]), self.assertRaises((ValueError, p.Invalid)): f.inspect()
        # Duplicate keys inside the embedded result are rejected by manifest decoding too.
        f.save_recording()
        f.raw_manifest = f.raw_manifest.replace(b'"recording_smoke": {', b'"recording_smoke": {"report":"x",')
        (f.bundle / 'build-manifest.json').write_bytes(f.raw_manifest)
        with self.assertRaisesRegex(p.Invalid, 'duplicate_json_key'): f.inspect()

    def test_recording_hash_size_and_exact_artifact_name_bindings(self):
        f = RecordingFixture(self.root)
        original = copy.deepcopy(f.recording)
        mutations = [(('executable', 'sha256'), 'd' * 64), (('executable', 'bytes'), len(f.exe) + 1),
                     (('artifacts', 'archive', 'sha256'), 'd' * 64),
                     (('artifacts', 'spool', 'bytes'), 1),
                     (('artifacts', 'archive', 'name'), '../recording.mtrec'),
                     (('artifacts', 'spool', 'name'), 'recording-' + 'A' * 32 + '.mtspool'),
                     (('artifacts', 'spool', 'name'), '/private/secret.mtspool')]
        for path, value in mutations:
            f.recording = copy.deepcopy(original); node = f.recording
            for key in path[:-1]: node = node[key]
            node[path[-1]] = value; f.save_recording()
            with self.subTest(path=path, value=value), self.assertRaises(p.Invalid): f.inspect()
        f.recording = original; f.save_recording()
        target = f.recording_work / 'recording.mtrec'; target.write_bytes(b'changed')
        with self.assertRaisesRegex(p.Invalid, 'recording_artifact_hash'): f.inspect()

    def test_recording_extra_case_and_directory_artifacts_rejected(self):
        for label in ('extra.bin', 'RECORDING.MTREC', 'recording-' + 'f' * 32 + '.mtspool', 'directory'):
            f = RecordingFixture(self.root / str(len(list(self.root.iterdir()))))
            extra = f.recording_work / label
            if label == 'directory': extra.mkdir()
            else: extra.write_bytes(b'not permitted')
            with self.subTest(label=label), self.assertRaises(p.Invalid): f.inspect()

    def test_recording_receipt_work_and_artifact_redirects_rejected(self):
        for kind in ('receipt', 'work', 'artifact'):
            f = RecordingFixture(self.root / kind)
            target = f.recording_path if kind == 'receipt' else f.recording_work if kind == 'work' else f.recording_work / 'recording.mtrec'
            moved = target.with_name(target.name + '.moved'); target.rename(moved)
            target.symlink_to(moved, target_is_directory=kind == 'work')
            with self.subTest(kind=kind), self.assertRaisesRegex(p.Invalid, 'redirected_path'): f.inspect()

    def test_recording_receipt_and_artifact_mutations_rechecked_before_output(self):
        for kind in ('receipt', 'archive', 'spool', 'extra', 'ancestor'):
            f = RecordingFixture(self.root / kind); original = p.Reads.read; changed = False
            def mutate(reader, path, *args, **kwargs):
                nonlocal changed
                result = original(reader, path, *args, **kwargs)
                if Path(path) == f.module_path and not changed:
                    changed = True
                    if kind == 'extra': (f.recording_work / 'extra').write_bytes(b'bad')
                    elif kind == 'ancestor':
                        moved = f.recording_work.with_name('moved'); f.recording_work.rename(moved)
                        f.recording_work.symlink_to(moved, target_is_directory=True)
                    else:
                        target = f.recording_path if kind == 'receipt' else f.recording_work / f.recording['artifacts'][kind]['name']
                        target.write_bytes(target.read_bytes() + b'changed')
                return result
            with self.subTest(kind=kind), mock.patch.object(p.Reads, 'read', mutate), self.assertRaises(p.Invalid):
                f.inspect()

    def test_recording_receipt_text_never_leaks_and_output_remains_exclusive(self):
        f = RecordingFixture(self.root / 'input')
        f.recording['runtime']['os_version'] = '/private/SENTINEL'
        f.recording['identity']['firmware_version'] = '/private/SENTINEL'
        f.save_recording(); report = f.inspect()
        self.assertNotIn('SENTINEL', json.dumps(report))
        self.assertNotIn(str(f.root), json.dumps(report))
        output = self.root / 'inventory.json'; p.write_report(output, report, f.build)
        first = output.read_bytes()
        with self.assertRaises(FileExistsError): p.write_report(output, report, f.build)
        self.assertEqual(output.read_bytes(), first)
        with self.assertRaises(p.Invalid): p.write_report(f.recording_path, report, f.build)

    def test_recording_limits_boundaries_and_existing_stricter_exe_cap(self):
        f = RecordingFixture(self.root / 'two')
        self.assertEqual(p.recording_structure(f.recording), 2)
        g = RecordingFixture(self.root / 'sixteen', ('capture',) * 16)
        self.assertEqual(p.recording_structure(g.recording), 16)
        for segments in (f.recording['segments'][:1], g.recording['segments'] + [g.recording['segments'][-1]]):
            changed = copy.deepcopy(f.recording); changed['segments'] = segments
            with self.assertRaises(p.Invalid): p.recording_structure(changed)
        for path, maximum in [(('executable', 'bytes'), p.MAX_ARCHIVE),
                              (('artifacts', 'archive', 'bytes'), 2 * p.MIB),
                              (('segments', 0, 'provenance', 'host_elapsed_ms'), 45000)]:
            changed = copy.deepcopy(f.recording); node = changed
            for key in path[:-1]: node = node[key]
            node[path[-1]] = maximum; p.recording_structure(changed)
            node[path[-1]] = maximum + 1
            with self.subTest(path=path), self.assertRaises(p.Invalid): p.recording_structure(changed)
        raw = json.dumps(f.recording).encode(); raw += b' ' * (65536 - len(raw))
        f.save_recording(raw=raw); self.assertEqual(f.inspect()['recording_smoke']['receipt_bytes'], 65536)
        f.save_recording(raw=raw + b' ')
        with self.assertRaises(p.Invalid): f.inspect()

    def test_recording_artifact_actual_size_boundary_and_reparse_metadata(self):
        f = RecordingFixture(self.root)
        artifact = f.recording_work / 'recording.mtrec'
        artifact.write_bytes(b'x' * (2 * p.MIB))
        f.recording['artifacts']['archive'].update(bytes=2 * p.MIB, sha256=p.digest(artifact.read_bytes()))
        f.save_recording()
        self.assertEqual(f.inspect()['recording_smoke']['artifacts']['archive']['bytes'], 2 * p.MIB)
        artifact.write_bytes(b'x' * (2 * p.MIB + 1))
        # Even a declaration within the cap cannot authorize an oversized actual read.
        with self.assertRaises(p.Invalid): f.inspect()
        actual = Path.lstat
        def reparse(path, *args, **kwargs):
            info = actual(path, *args, **kwargs)
            if path == f.recording_path:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with mock.patch.object(Path, 'lstat', reparse), self.assertRaisesRegex(p.Invalid, 'redirected_path'):
            f.inspect()

    def test_recording_integer_uuid_digest_and_import_boundaries(self):
        f = RecordingFixture(self.root)
        for nonce in (1, 0xFFFFFFFFFFFFFFFF):
            changed = copy.deepcopy(f.recording)
            raw = f'{nonce:032x}'
            changed['identity']['session_id'] = f'{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}'
            changed['stop']['wire_session_nonce'] = nonce
            for row in changed['segments']: row['provenance']['wire_session_nonce'] = nonce
            self.assertEqual(p.recording_structure(changed), 2)
        for key, value in [('generation', 1), ('request_id', 0xFFFFFFFF), ('wire_session_nonce', 2**64)]:
            changed = copy.deepcopy(f.recording); changed['stop'][key] = value
            with self.subTest(key=key), self.assertRaises(p.Invalid): p.recording_structure(changed)
        for path, value in [(('identity', 'recording_id'), 'A' * 36),
                            (('segments', 0, 'raw_sha256'), 'A' * 64),
                            (('segments', 0, 'provenance', 'end_samples_crc32'), 2**32),
                            (('segments', 0, 'provenance', 'config_id'), 0)]:
            changed = copy.deepcopy(f.recording); node = changed
            for key in path[:-1]: node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(p.Invalid): p.recording_structure(changed)
        for imports in (f.recording['imports'][:-1], f.recording['imports'][::-1],
                        f.recording['imports'] + ['motortester.extra']):
            changed = copy.deepcopy(f.recording); changed['imports'] = imports
            with self.assertRaises(p.Invalid): p.recording_structure(changed)

    def test_full_generated_candidate_is_read_only_unknown_and_qualified(self):
        f = Fixture(self.root)
        before = {str(x): p.digest(x.read_bytes()) for x in f.root.rglob('*') if x.is_file()}
        report = f.inspect()
        after = {str(x): p.digest(x.read_bytes()) for x in f.root.rglob('*') if x.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(report['native_count'], 3)
        self.assertEqual(report['native_unknown_count'], 1)
        self.assertEqual(report['inventory_status'], 'complete_with_unknown_origins')
        self.assertEqual(report['source_commit'], SOURCE)
        self.assertEqual(report['executable_sha256'], p.digest(f.exe))
        self.assertEqual(report['pyz_sha256'], p.digest(f.pyz))
        self.assertEqual([x['name'] for x in report['pyz_modules']], ['PySide6._config', 'namespace'])
        for key in ('legal_clearance', 'corresponding_source_verified', 'replacement_verified'):
            self.assertIs(report[key], False)
        encoded = json.dumps(report)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn('DO_NOT_READ_SECRET', encoded)
        self.assertFalse((self.root / 'DO_NOT_READ_SECRET.dll').exists())

    def test_pinned_archive_parser_does_not_unmarshal_or_decompress_module_code(self):
        # Deliberately not a marshal code object. Inventory records stored bytes.
        with mock.patch.object(marshal, 'loads', side_effect=AssertionError('marshal used')), \
                mock.patch.object(marshal, 'load', side_effect=AssertionError('marshal used')), \
                mock.patch.object(zlib, 'decompress', side_effect=AssertionError('inflate used')):
            raw = pyz([('covered.module', 0, b'not code')])
            rows, options, actual, modules = p.archive_inventory(carchive(raw), MAGIC)
        self.assertEqual(actual, raw)
        self.assertEqual(modules[0]['name'], 'covered.module')
        self.assertEqual(len(rows), 2)
        self.assertEqual(options, ['pyi-contents-directory _internal'])

    def test_wrong_binding_identities(self):
        f = Fixture(self.root)
        for key, value in [('source_sha', 'b' * 40), ('manifest_sha', 'b' * 64),
                           ('inspector_sha', 'b' * 64), ('inspector_commit', 'not-a-commit')]:
            with self.subTest(key=key), self.assertRaises(p.Invalid):
                f.inspect(**{key: value})

    def test_wrong_platform_packages_and_duplicate_manifest_paths(self):
        f = Fixture(self.root)
        original = json.loads(json.dumps(f.manifest))
        changes = [lambda m: m.update(python='3.12.0'), lambda m: m.update(machine='ARM64'),
                   lambda m: m.update(status='release'), lambda m: m['packages'][0].update(version='99'),
                   lambda m: m['packages'].__setitem__(0, m['packages'][1]),
                   lambda m: m['files'].update({'motortester.EXE': m['files']['Motortester.exe']}),
                   lambda m: m['files'].update({'../secret': '0' * 64})]
        for i, change in enumerate(changes):
            with self.subTest(case=i):
                f.manifest = json.loads(json.dumps(original)); change(f.manifest); f.save_manifest()
                with self.assertRaises(p.Invalid):
                    f.inspect()

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(p.Invalid, 'duplicate_json_key'):
            json.loads('{"files":{},"files":{}}', object_pairs_hook=p.json_object)

    def test_json_allocation_guards_and_nonfinite_values(self):
        for data in [b'[' * 20 + b']' * 20, b'{"x":' + b'1' * 100 + b'}']:
            with self.subTest(data=data[:10]), mock.patch.object(p.json, 'loads',
                    side_effect=AssertionError('allocated JSON')), self.assertRaises(p.Invalid):
                p.manifest_json(data)
        with self.assertRaises(p.Invalid): p.manifest_json(b'{"x":NaN}')
        with mock.patch.object(p, 'MAX_NODES', 4), self.assertRaises(p.Invalid):
            p.manifest_json(b'{"x":[],"y":[]}')
        self.assertEqual(p.manifest_json(b'{"x":[]}'), {'x': []})

    def test_changed_missing_unlisted_and_stale_inputs(self):
        cases = [('payload', lambda f: (f.bundle / 'notes.txt').write_bytes(b'changed')),
                 ('missing', lambda f: (f.work / 'COLLECT-00.toc').unlink()),
                 ('extra', lambda f: (f.bundle / 'extra.dll').write_bytes(b'extra')),
                 ('stale-pyz', lambda f: (f.work / 'PYZ-00.pyz').write_bytes(b'wrong')),
                 ('origin', lambda f: f.native_path.write_bytes(b'changed')),
                 ('reader', lambda f: (f.site / next(iter(f.reader_hashes))).write_bytes(b'changed'))]
        for key, change in cases:
            with self.subTest(key=key):
                f = Fixture(self.root / key); change(f)
                with self.assertRaises((p.Invalid, OSError)):
                    f.inspect()

    def test_toc_missing_native_is_unknown_and_duplicate_is_error(self):
        f = Fixture(self.root)
        f.collect.pop(); f.tables()
        report = f.inspect()
        row = next(x for x in report['native_files'] if x['path'].endswith('unknown.dll'))
        self.assertEqual(row['origin'], {'status': 'no_collect_entry'})
        f.collect.append(f.collect[0]); f.tables()
        with self.assertRaisesRegex(p.Invalid, 'duplicate_collect'):
            f.inspect()

    def test_final_collect_mapping_collisions_rejected_before_origin_join(self):
        for executable, binary in [('_internal/example.dll', 'example.dll'),
                                   ('_INTERNAL/EXAMPLE.dll', 'example.dll'),
                                   ('_internal\\nested\\example.dll', 'nested/example.dll')]:
            rows = [(executable, '/allowed/one.dll', 'EXECUTABLE'),
                    (binary, '/allowed/two.dll', 'BINARY')]
            for ordered in (rows, rows[::-1]):
                with self.subTest(rows=ordered), self.assertRaisesRegex(p.Invalid, 'duplicate_collect_destination'):
                    p.collect_map(repr((ordered,)).encode())
        valid = [('Motortester.exe', '/work/Motortester.exe', 'EXECUTABLE'),
                 ('example.dll', '/allowed/example.dll', 'BINARY')]
        self.assertEqual(set(p.collect_map(repr((valid,)).encode())),
                         {'Motortester.exe', '_internal/example.dll'})

    def test_device_and_invalid_paths_rejected_before_output_access(self):
        invalid = ['CONOUT$', 'CONIN$', 'CLOCK$', 'COM0.json', 'LPT0.txt',
                   'COM¹.json', 'COM².dll', 'COM³.txt', 'LPT¹', 'LPT².dll', 'LPT³',
                   'bad*.json', 'bad?.json', 'bad|.json', 'bad<.json', 'bad>.json',
                   'bad".json', 'ConOut$.txt', 'con .txt', 'Lpt1 .txt']
        for value in invalid:
            for rel in (value, 'safe/' + value, value + '/safe.json'):
                with self.subTest(path=rel), self.assertRaises(p.Invalid): p.relative(rel)
            with self.subTest(output=value), \
                    mock.patch.object(Path, 'lstat', side_effect=AssertionError('PATH_WAS_PROBED')), \
                    mock.patch.object(Path, 'open', side_effect=AssertionError('OUTPUT_WAS_OPENED')), \
                    self.assertRaises(p.Invalid):
                p.write_report(self.root / value, {}, self.root / 'build')
        for value in ('console.json', 'component.dll', 'com10.json', 'LPT10.txt', 'normal-file_1.json'):
            self.assertEqual(p.relative(value), value)

    def test_constructed_exe_origin_is_mandatory_and_fixed(self):
        f = Fixture(self.root)
        for source, typ in [(str(self.root / 'other.exe'), 'EXECUTABLE'),
                            (str(f.work / 'Motortester.exe'), 'BINARY')]:
            f.collect[0] = ('Motortester.exe', source, typ); f.tables()
            with self.subTest(source=source, typ=typ), self.assertRaises(p.Invalid): f.inspect()

    def test_pure_table_mismatch_source_and_duplicate(self):
        for case in ('missing', 'duplicate', 'namespace', 'type', 'path'):
            with self.subTest(case=case):
                f = Fixture(self.root / case)
                if case == 'missing': f.pure.pop()
                if case == 'duplicate': f.pure.append(f.pure[0])
                if case == 'namespace': f.pure[1] = ('namespace', str(f.module_path), 'PYMODULE')
                if case == 'type': f.pure[0] = (*f.pure[0][:2], 'PYMODULE-2')
                f.tables()
                if case == 'path': (f.work / 'PYZ-00.toc').write_text(repr(('/private/other.pyz', f.pure)))
                with self.assertRaises(p.Invalid): f.inspect()

    def test_literal_loader_rejects_executable_and_unsupported_grammar(self):
        for data in [b'__import__("os").getcwd()', b'(1,)', b'{"a":"b"}', b'("x" + "y",)',
                     b'(True,)', b'([x for x in []],)', b'("secret",)\nprint("bad")']:
            with self.subTest(data=data), self.assertRaises((p.Invalid, SyntaxError)):
                p.literals(data)
        self.assertEqual(p.literals(b'([("name", None, "PYMODULE")],)'), ([('name', None, 'PYMODULE')],))

    def test_path_and_module_rules(self):
        for path in ['/etc/passwd', '../x', 'a/../b', 'C:/x', '//server/x', 'a\\b', 'a:b',
                     'a//b', 'a/.', 'a/CON.txt', 'a/trailing.', 'a/space ', 'a/\x00b']:
            with self.subTest(path=path), self.assertRaises(p.Invalid): p.relative(path)
        for label in ['x/y', 'x..y', '.x', 'x:y', 'x y', '1x']:
            with self.subTest(label=label), self.assertRaises(p.Invalid): p.module_name(label)
        self.assertEqual(p.relative('_internal/PySide6/QtCore.pyd'), '_internal/PySide6/QtCore.pyd')

    def test_symlink_and_fake_reparse_rejected(self):
        target = self.root / 'target'; target.write_bytes(b'content')
        link = self.root / 'link'; link.symlink_to(target)
        with self.assertRaisesRegex(p.Invalid, 'redirected_path'): p.no_redirect(link)
        info = target.stat()
        fake = mock.Mock(st_mode=info.st_mode, st_file_attributes=0x400)
        with mock.patch.object(Path, 'lstat', return_value=fake), self.assertRaises(p.Invalid):
            p.no_redirect(target)

    def test_metadata_distribution_and_record_ambiguity(self):
        for case in ('duplicate', 'version', 'owner'):
            with self.subTest(case=case):
                f = Fixture(self.root / case)
                folder = f.dist_folders['colorama']
                if case == 'duplicate':
                    f.put(f.site / 'duplicate.dist-info/METADATA', b'Name: colorama\nVersion: 0.4.6\n')
                    f.put(f.site / 'duplicate.dist-info/RECORD', b'')
                if case == 'version': (folder / 'METADATA').write_text('Name: colorama\nVersion: 0\n')
                if case == 'owner': (folder / 'RECORD').write_text('PySide6/example.dll,,\n')
                with self.assertRaises(p.Invalid): f.inspect()

    def test_record_scripts_are_not_followed(self):
        f = Fixture(self.root)
        (f.dist_folders['colorama'] / 'RECORD').write_text(
            '../../../Scripts/nonexistent.exe,,\n/private/secret,,\nC:/private/foreign.exe,,\n')
        self.assertEqual(f.inspect()['native_count'], 3)

    def test_absolute_record_inside_site_can_own_exact_file(self):
        f = Fixture(self.root)
        (f.dist_folders['pyside6-essentials'] / 'RECORD').write_text(
            str(f.module_path) + ',,\n' + str(f.native_path) + ',,\n')
        self.assertEqual(f.inspect()['native_unknown_count'], 1)

    def test_declared_only_module_is_not_reported_as_embedded(self):
        f = Fixture(self.root)
        f.pure.append(('declared_but_omitted', str(f.module_path), 'PYMODULE')); f.tables()
        report = f.inspect()
        self.assertNotIn('declared_but_omitted', [x['name'] for x in report['pyz_modules']])
        self.assertEqual(report['declared_only_modules'][0]['status'], 'declared_not_embedded')

    def test_interpreter_and_outside_source_are_distinct(self):
        f = Fixture(self.root)
        src = f.python / 'DLLs/_ssl.pyd'; f.put(src, b'cpython fixture')
        args = dict(site=f.site, interpreter=f.python, work=f.work, reads=p.Reads(), owners={})
        result = p.origin(str(src), native=True, expected=p.digest(src.read_bytes()), **args)
        self.assertEqual(result['origin_kind'], 'interpreter')
        unknown = p.origin(str(self.root / 'secret.dll'), native=True, **args)
        self.assertEqual(unknown, {'status': 'outside_allowed_origins'})
        with self.assertRaises(p.Invalid): p.origin(str(f.python / '../secret.dll'), native=True, **args)
        with self.assertRaisesRegex(p.Invalid, 'self_referential_origin'):
            p.origin(str(f.bundle / '_internal/unknown.dll'), native=True, **args)

    def test_windows_collect_spelling_and_unknown_metadata_owner(self):
        table = ([(r'PySide6\QtCore.pyd', r'C:\build\venv\Lib\site-packages\PySide6\QtCore.pyd', 'EXTENSION')],)
        result = p.collect_map(repr(table).encode())
        self.assertIn('_internal/PySide6/QtCore.pyd', result)
        f = Fixture(self.root)
        (f.dist_folders['pyside6-essentials'] / 'RECORD').write_text('PySide6/_config.py,,\n')
        with self.assertRaisesRegex(p.Invalid, 'unowned_environment_file'): f.inspect()

    def test_output_exclusive_outside_input_and_size_bound(self):
        f = Fixture(self.root / 'input')
        report = f.inspect()
        out = self.root / 'receipt.json'
        p.write_report(out, report, f.build)
        self.assertEqual(json.loads(out.read_text()), report)
        with self.assertRaises(FileExistsError): p.write_report(out, report, f.build)
        with self.assertRaises(p.Invalid): p.write_report(f.bundle / 'new.json', report, f.build)
        with mock.patch.object(p, 'MAX_REPORT', 1), self.assertRaises(p.Invalid):
            p.write_report(self.root / 'large.json', report, f.build)
        self.assertFalse((self.root / 'large.json').exists())

    def test_windows_path_and_handle_ctime_semantics_can_differ_stably(self):
        path = self.root / 'stable.bin'; path.write_bytes(b'unchanged')
        reads = p.Reads()
        with windows_stat_fixture(path) as calls:
            self.assertEqual(reads.read(path), b'unchanged')
            self.assertEqual(reads.read(path, keep=False), (9, p.digest(b'unchanged')))
            reads.unchanged()
        self.assertEqual(calls, {'path': 5, 'handle': 4})
        self.assertEqual(path.read_bytes(), b'unchanged')

    def test_cross_api_identity_size_mtime_and_type_mismatch_still_rejected(self):
        path = self.root / 'identity.bin'; path.write_bytes(b'unchanged')
        original = path.stat()
        changes = {'st_dev': original.st_dev + 1, 'st_ino': original.st_ino + 1,
                   'st_size': original.st_size + 1, 'st_mtime_ns': original.st_mtime_ns + 1,
                   'st_mode': stat.S_IFCHR}
        for field, changed in changes.items():
            with self.subTest(field=field), windows_stat_fixture(path, handle_changes={1: {field: changed}}), \
                    self.assertRaisesRegex(p.Invalid, 'input_changed_open_identity'):
                p.Reads().read(path)

    def test_same_api_path_and_handle_changes_remain_errors(self):
        path = self.root / 'mutation.bin'; path.write_bytes(b'unchanged')
        original = path.stat()
        common = {'st_dev': original.st_dev + 1, 'st_ino': original.st_ino + 1,
                  'st_size': original.st_size + 1, 'st_mtime_ns': original.st_mtime_ns + 1,
                  'st_mode': stat.S_IFCHR, 'st_birthtime_ns': 1000000001}
        for api in ('path', 'handle'):
            changes = dict(common, st_ctime_ns=1000000001 if api == 'path' else 2000000001)
            for field, changed in changes.items():
                kwargs = {api + '_changes': {2: {field: changed}}}
                with self.subTest(api=api, field=field), windows_stat_fixture(path, **kwargs), \
                        self.assertRaisesRegex(p.Invalid, 'input_changed_' + api):
                    p.Reads().read(path)

    def test_repeated_read_preserves_handle_timestamp_comparison(self):
        path = self.root / 'repeated.bin'; path.write_bytes(b'unchanged')
        for field, value in [('st_ctime_ns', 2000000001), ('st_birthtime_ns', 1000000001)]:
            changes = {3: {field: value}, 4: {field: value}}
            with self.subTest(field=field), windows_stat_fixture(path, handle_changes=changes):
                reads = p.Reads(); reads.read(path)
                with self.assertRaisesRegex(p.Invalid, 'input_changed_repeated'): reads.read(path)

    def test_final_path_sweep_keeps_same_api_timestamp_guards(self):
        path = self.root / 'final.bin'; path.write_bytes(b'unchanged')
        for field in ('st_ctime_ns', 'st_birthtime_ns'):
            with self.subTest(field=field), windows_stat_fixture(path, path_changes={3: {field: 1000000001}}):
                reads = p.Reads(); reads.read(path)
                with self.assertRaisesRegex(p.Invalid, 'input_changed_final_path'): reads.unchanged()

    def test_content_rehash_rejects_same_size_with_restored_mtime(self):
        path = self.root / 'bytes.bin'; path.write_bytes(b'first')
        before = path.stat()
        with windows_stat_fixture(path):
            reads = p.Reads(); reads.read(path)
            path.write_bytes(b'other')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            with self.assertRaisesRegex(p.Invalid, 'input_changed_repeated'): reads.read(path)
        self.assertEqual(path.read_bytes(), b'other')

    def test_mutation_and_read_denial_are_not_hidden(self):
        path = self.root / 'file'; path.write_bytes(b'first')
        reads = p.Reads(); reads.read(path); path.write_bytes(b'other')
        with self.assertRaisesRegex(p.Invalid, 'input_changed'): reads.read(path)
        with self.assertRaisesRegex(p.Invalid, 'input_changed'): reads.unchanged()
        with mock.patch.object(Path, 'open', side_effect=PermissionError('PRIVATE_PATH')), \
                self.assertRaises(PermissionError):
            p.Reads().read(path)

    def test_public_error_does_not_leak_path_or_emit_success(self):
        out = self.root / 'no-report.json'
        args = ['--build-root', '/private/secret', '--expected-source-sha', SOURCE,
                '--expected-manifest-sha256', '0' * 64, '--inspector-commit', INSPECTOR,
                '--expected-inspector-sha256', '0' * 64, '--output', str(out)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(p, 'inspect_candidate', side_effect=OSError('/private/secret')), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(p.main(args), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertNotIn('/private', stderr.getvalue())
        self.assertFalse(out.exists())

    def test_mutation_phase_codes_are_fixed_and_path_free(self):
        args = ['--build-root', '/private/secret', '--expected-source-sha', SOURCE,
                '--expected-manifest-sha256', '0' * 64, '--inspector-commit', INSPECTOR,
                '--expected-inspector-sha256', '0' * 64, '--output', str(self.root / 'no-report.json')]
        for phase in ('open_identity', 'handle', 'path', 'length', 'repeated', 'final_path'):
            code = 'input_changed_' + phase
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(phase=phase), \
                    mock.patch.object(p, 'inspect_candidate', side_effect=p.Invalid(code)), \
                    mock.patch.object(p, 'write_report', side_effect=AssertionError('REPORT_WAS_WRITTEN')), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(p.main(args), 1)
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(stderr.getvalue(), 'PROVENANCE_INSPECTION_FAILED code=' + code + '\n')

    def test_cli_parse_errors_do_not_echo_private_arguments(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(p.main(['--unexpected', '/private/secret']), 1)
        self.assertIn('code=invalid_arguments', error.getvalue())
        self.assertNotIn('/private', error.getvalue())

    def test_root_syntax_rejected_before_filesystem_access(self):
        for root in ['//server/share', '\\\\server\\share', 'C:private', 'file:secret', '/a/../b']:
            with self.subTest(root=root), mock.patch.object(Path, 'lstat', side_effect=AssertionError('read')), \
                    self.assertRaises(p.Invalid):
                p.no_redirect(root)

    def test_literal_complexity_rejected_before_ast_allocation(self):
        with mock.patch.object(p.ast, 'parse', side_effect=AssertionError('AST allocated')):
            with mock.patch.object(p, 'MAX_NODES', 3), self.assertRaises(p.Invalid):
                p.literals(b'("a", "b", "c")')
            with mock.patch.object(p, 'MAX_DEPTH', 1), self.assertRaises(p.Invalid):
                p.literals(b'(((())))')

    def test_runtime_guard_requires_actual_isolated_windows_environment(self):
        with self.assertRaises(p.Invalid): p.runtime_guard(self.root)

    def test_each_runtime_guard_and_positive_fixed_layout(self):
        build = self.root / 'build'; exe = build / 'venv/Scripts/python.exe'
        Fixture.put(exe, b'never execute fixture interpreter')
        with contextlib.ExitStack() as stack:
            for key, value in dict(platform='win32', version_info=(3, 13, 16), maxsize=2**63 - 1,
                                   version='3.13.16 [MSC v.1944 64 bit (AMD64)]',
                                   executable=str(exe), dont_write_bytecode=True,
                                   flags=SimpleNamespace(isolated=1, no_site=1),
                                   implementation=SimpleNamespace(name='cpython')).items():
                stack.enter_context(mock.patch.object(p.sys, key, value))
            stack.enter_context(mock.patch.object(p.sys, '_is_gil_enabled', return_value=True, create=True))
            stack.enter_context(mock.patch.object(p.sysconfig, 'get_config_var', return_value=0))
            p.runtime_guard(build)
            cases = [('platform', 'linux'), ('version_info', (3, 13, 15)), ('maxsize', 2**31 - 1),
                     ('version', '64 bit (ARM64)'), ('executable', str(self.root / 'missing.exe')),
                     ('dont_write_bytecode', False), ('flags', SimpleNamespace(isolated=0, no_site=1)),
                     ('flags', SimpleNamespace(isolated=1, no_site=0))]
            for key, value in cases:
                with self.subTest(key=key, value=value), mock.patch.object(p.sys, key, value), \
                        self.assertRaises((p.Invalid, OSError)):
                    p.runtime_guard(build)
            with mock.patch.object(p.sysconfig, 'get_config_var', return_value=1), self.assertRaises(p.Invalid):
                p.runtime_guard(build)
            with mock.patch.object(p.sys, '_is_gil_enabled', return_value=False), self.assertRaises(p.Invalid):
                p.runtime_guard(build)

    def test_ancestor_escape_and_ancestor_mutation_are_detected(self):
        real = self.root / 'real'; real.mkdir(); (real / 'file').write_bytes(b'bytes')
        alias = self.root / 'alias'; alias.symlink_to(real, target_is_directory=True)
        with self.assertRaises(p.Invalid): p.Reads().read(alias / 'file')
        reads = p.Reads(); reads.read(real / 'file')
        moved = self.root / 'moved'; real.rename(moved); real.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(p.Invalid): reads.unchanged()
        with self.assertRaises(p.Invalid): p.write_report(alias / 'receipt.json', {}, moved)

    def test_input_case_collision_and_candidate_depth_boundary(self):
        root = self.root / 'candidate'; root.mkdir()
        (root / 'One').write_bytes(b'1'); (root / 'one').write_bytes(b'2')
        with self.assertRaises(p.Invalid): p.candidate_files(root)
        nested = self.root / 'nested'; (nested / 'a/b').mkdir(parents=True)
        with mock.patch.object(p, 'MAX_PATH_PARTS', 2):
            self.assertEqual(p.candidate_files(nested), set())
            (nested / 'a/b/c').mkdir()
            with self.assertRaises(p.Invalid): p.candidate_files(nested)

    def test_output_write_failure_removes_only_new_partial_file(self):
        build = self.root / 'build'; build.mkdir()
        target = self.root / 'report.json'
        original_open = Path.open
        class Broken:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def fileno(self): return self.stream.fileno()
            def write(self, data):
                self.stream.write(data[:3]); self.stream.flush()
                raise OSError('PRIVATE write error')
            def __exit__(self, *args): self.stream.close()
        def broken_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return Broken(stream) if path == target else stream
        with mock.patch.object(Path, 'open', broken_open), self.assertRaises(OSError):
            p.write_report(target, {'inventory_status': 'complete'}, build)
        self.assertFalse(target.exists())
        target.write_bytes(b'old user file')
        with self.assertRaises(FileExistsError): p.write_report(target, {}, build)
        self.assertEqual(target.read_bytes(), b'old user file')

    def test_report_size_boundary_is_checked_during_encoding(self):
        build = self.root / 'build'; build.mkdir()
        report = {'a': 'b'}
        length = len((json.dumps(report, indent=2, sort_keys=True) + '\n').encode())
        with mock.patch.object(p, 'MAX_REPORT', length):
            p.write_report(self.root / 'exact.json', report, build)
        with mock.patch.object(p, 'MAX_REPORT', length - 1), self.assertRaises(p.Invalid):
            p.write_report(self.root / 'too-big.json', report, build)
        self.assertFalse((self.root / 'too-big.json').exists())

    def test_generic_library_error_is_sanitized(self):
        args = ['--build-root', str(self.root), '--expected-source-sha', SOURCE,
                '--expected-manifest-sha256', '0' * 64, '--inspector-commit', INSPECTOR,
                '--expected-inspector-sha256', '0' * 64, '--output', str(self.root / 'out')]
        error = io.StringIO()
        with mock.patch.object(p, 'inspect_candidate', side_effect=RuntimeError('/private/secret')), \
                contextlib.redirect_stderr(error):
            self.assertEqual(p.main(args), 1)
        self.assertNotIn('/private', error.getvalue())

    def test_caps_for_directory_and_installed_metadata_enumeration(self):
        root = self.root / 'dirs'; root.mkdir()
        (root / 'a').mkdir(); (root / 'b').mkdir()
        with mock.patch.object(p, 'MAX_FILES', 1):
            self.assertEqual(p.candidate_files(root), set())
            (root / 'c').mkdir()
            with self.assertRaises(p.Invalid): p.candidate_files(root)
        f = Fixture(self.root / 'fixture')
        count = len(list(f.site.iterdir()))
        with mock.patch.object(p, 'MAX_FILES', count):
            self.assertEqual(f.inspect()['native_count'], 3)
            (f.site / 'extra').write_bytes(b'')
            with self.assertRaisesRegex(p.Invalid, 'environment_entry_limit'): f.inspect()

    def test_aggregate_record_limit_and_metadata_byte_boundary(self):
        f = Fixture(self.root)
        folder = f.dist_folders['colorama']
        # The three reader members and two PySide members already consume five rows.
        (folder / 'RECORD').write_text(''.join(f'unused{i},,\n' for i in range(5)))
        with mock.patch.object(p, 'MAX_ENTRIES', 10):
            self.assertEqual(f.inspect()['native_count'], 3)
            with (folder / 'RECORD').open('a') as out: out.write('one_more,,\n')
            with self.assertRaisesRegex(p.Invalid, 'record_shape'): f.inspect()
        with mock.patch.object(p, 'MAX_META', 5):
            self.assertEqual(p.TocData(b'[' + struct.pack('<i', 0)).parse(), [])
            with self.assertRaises(p.Invalid): p.TocData(b'[' + struct.pack('<i', 0) + b'x')

    def test_literal_multiline_and_wide_tokens_rejected_before_tokenizer(self):
        for raw in [b'("""' + b'x\n' * 100 + b'""",)', b"('line\nline',)",
                    b"('" + b'x' * (p.MAX_NAME * 12 + 2) + b"',)"]:
            with self.subTest(size=len(raw)), mock.patch.object(p.tokenize, 'generate_tokens',
                    side_effect=AssertionError('tokenizer allocated')), self.assertRaises(p.Invalid):
                p.literals(raw)

    def test_source_has_no_exec_eval_marshal_loader_subprocess_network_or_assert_guards(self):
        tree = ast.parse(SCRIPT.read_text())
        forbidden = {'eval', 'exec', 'compile', '__import__'}
        forbidden_modules = {'marshal', 'subprocess', 'socket', 'urllib', 'requests', 'PyInstaller'}
        for node in ast.walk(tree):
            self.assertNotIsInstance(node, ast.Assert)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name): self.assertNotIn(node.func.id, forbidden)
            if isinstance(node, ast.Import):
                for alias in node.names: self.assertNotIn(alias.name.split('.')[0], forbidden_modules)
            if isinstance(node, ast.ImportFrom): self.assertNotIn((node.module or '').split('.')[0], forbidden_modules)


class ArchiveNegativeTests(unittest.TestCase):
    def test_alias_depth_and_expanded_work_do_not_bypass_caps(self):
        shared = ((1,),)
        value = [shared, [[[shared]]]]
        raw = marshal.dumps(value)
        with mock.patch.object(p, 'MAX_DEPTH', 4), self.assertRaises(p.Invalid): p.TocData(raw).parse()
        shared = tuple(range(20))
        raw = marshal.dumps([shared] * 20)
        with mock.patch.object(p, 'MAX_NODES', 100), self.assertRaises(p.Invalid): p.TocData(raw).parse()
        with mock.patch.object(p, 'MAX_NODES', 421):
            self.assertEqual(p.TocData(raw).parse(), [shared] * 20)
        with mock.patch.object(p, 'MAX_NODES', 420), self.assertRaises(p.Invalid): p.TocData(raw).parse()

    def test_carchive_padding_overlap_header_and_cookie_budget(self):
        raw = carchive()
        cookie_at = len(raw) - p.COOKIE.size
        _, length, offset, _, _, _ = p.COOKIE.unpack(raw[cookie_at:])
        toc_at = len(raw) - length + offset
        # First record overlaps the following body, has bad padding or terminator.
        for field, value in [(0, 17), (4, 0xffffffff), (8, 0xffffffff), (12, 0xffffffff)]:
            changed = bytearray(raw); changed[toc_at + field:toc_at + field + 4] = struct.pack('!I', value)
            with self.subTest(field=field), self.assertRaises(p.Invalid): p.archive_inventory(bytes(changed), MAGIC)
        changed = bytearray(raw); size = struct.unpack('!I', changed[toc_at:toc_at + 4])[0]
        changed[toc_at + size - 1] = 65
        with self.assertRaises(p.Invalid): p.archive_inventory(bytes(changed), MAGIC)
        with mock.patch.object(p, 'MAX_ENTRIES', 3):
            self.assertEqual(p.archive_inventory(raw, MAGIC)[2], pyz())
            with self.assertRaises(p.Invalid): p.archive_inventory(raw[:2] + p.COOKIE_MAGIC * 3 + raw[2:], MAGIC)
        for index in (4, 12, 16):
            bad = bytearray(pyz()); bad[index] ^= 1
            with self.subTest(index=index), self.assertRaises(p.Invalid): p.pyz_inventory(bytes(bad), MAGIC)
    def test_overlapping_stored_ranges_rejected_before_any_payload_hash(self):
        data = pyz()
        offset = struct.unpack('!i', data[8:12])[0]
        repeated = [('module_' + str(i), (0, 17, offset - 17)) for i in range(100)]
        bad_pyz = data[:offset] + marshal.dumps(repeated)
        raw = bytearray(carchive(entries=[('one', 'm', 0, b'first-body'),
                                         ('two', 'm', 0, b'second-body'),
                                         ('PYZ.pyz', 'z', 0, pyz())]))
        _, length, offset, _, _, _ = p.COOKIE.unpack(raw[-p.COOKIE.size:])
        toc_at = len(raw) - length + offset
        second = toc_at + struct.unpack('!I', raw[toc_at:toc_at + 4])[0]
        raw[second + 4:second + 8] = struct.pack('!I', 0)
        for parser, data in [(p.pyz_inventory, bad_pyz), (p.archive_inventory, bytes(raw))]:
            with self.subTest(parser=parser.__name__), \
                    mock.patch.object(p, 'digest', side_effect=AssertionError('HASHED_BEFORE_RANGE_VALIDATION')), \
                    self.assertRaisesRegex(p.Invalid, 'overlapping_entries'):
                parser(data, MAGIC)

    def test_incidental_cookie_magic_is_not_a_second_valid_archive(self):
        raw = carchive()
        raw = raw[:2] + p.COOKIE_MAGIC + raw[2:]
        self.assertEqual(p.archive_inventory(raw, MAGIC)[2], pyz())

    def test_two_structurally_valid_cookies_and_private_options_fail(self):
        first = carchive()
        with self.assertRaises(p.Invalid): p.archive_inventory(first + carchive(), MAGIC)
        for option in ['pyi-runtime-tmpdir /private/home', 'arbitrary_secret_value']:
            raw = carchive(entries=[(option, 'o', 0, b''), ('pyz', 'z', 0, pyz())])
            with self.subTest(option=option), self.assertRaises(p.Invalid): p.archive_inventory(raw, MAGIC)

    def test_data_subset_matches_generated_marshal_without_code(self):
        for seed in range(30):
            rng = random.Random(seed)
            value = [('module_' + str(i), (rng.randrange(4), rng.randrange(1000), rng.randrange(1000)))
                     for i in range(rng.randrange(30))]
            self.assertEqual(p.TocData(marshal.dumps(value)).parse(), value)
        repeated = 'repeat_name'
        value = [(repeated, (0, 17, 10)), (repeated, (1, 27, 10))]
        self.assertEqual(p.TocData(marshal.dumps(value)).parse(), value)

    def test_marshal_forbidden_types_refs_and_trailing(self):
        cases = [marshal.dumps({}), marshal.dumps(set()), marshal.dumps(True), b'c',
                 b'r' + struct.pack('<i', 0), b'\xdb' + struct.pack('<i', 1) + b'r' + struct.pack('<i', 0),
                 b'[' + struct.pack('<i', 2**31 - 1), b'z\xffx', marshal.dumps([]) + b'x']
        for data in cases:
            with self.subTest(data=data[:20]), self.assertRaises(p.Invalid): p.TocData(data).parse()

    def test_pyz_duplicates_shapes_ranges_and_unknown_types(self):
        cases = [[('same', (0, 17, 1)), ('same', (0, 18, 1))],
                 [('module', (2, 17, 1))], [('module', (0, -1, 1))], [('module', (0, 17, 999999))],
                 [('module', (3, 17, 1))], [('module', (0, 17, 0))], [('x/y', (0, 17, 1))],
                 [('one', (0, 17, 3)), ('two', (0, 18, 3))], [('x', [0, 17, 1])]]
        for table in cases:
            with self.subTest(table=table), self.assertRaises(p.Invalid):
                p.pyz_inventory(pyz(toc=marshal.dumps(table)), MAGIC)

    def test_archive_duplicates_unsupported_types_and_multiple_pyz(self):
        data = pyz()
        cases = [[('one', 'z', 0, data), ('two', 'z', 0, data)],
                 [('one', 'z', 1, data)], [('one', 'x', 0, b'x')],
                 [('PYZ-00.pyz', 'z', 0, data)],
                 [('same', 'm', 0, b'x'), ('SAME', 'm', 0, b'y'), ('pyz', 'z', 0, data)],
                 [('../secret', 'z', 0, data)]]
        for rows in cases:
            with self.subTest(rows=rows[:1]), self.assertRaises(p.Invalid):
                p.archive_inventory(carchive(entries=rows), MAGIC)

    def test_all_prefix_truncations_fail_without_hanging(self):
        data = carchive()
        for end in range(len(data)):
            with self.subTest(end=end), self.assertRaises((p.Invalid, ValueError, struct.error)):
                p.archive_inventory(data[:end], MAGIC)

    def test_cookie_and_toc_integer_boundary_mutations(self):
        data = carchive()
        start = len(data) - p.COOKIE.size
        for field in (8, 12, 16, 20):
            for value in (0, 1, 0x7fffffff, 0xffffffff):
                changed = bytearray(data); changed[start + field:start + field + 4] = struct.pack('!I', value)
                with self.subTest(field=field, value=value), self.assertRaises((p.Invalid, struct.error)):
                    p.archive_inventory(bytes(changed), MAGIC)

    def test_caps_at_boundary_and_one_above(self):
        self.assertEqual(p.TocData(b'[' + struct.pack('<i', 0)).parse(), [])
        with mock.patch.object(p, 'MAX_META', 4), self.assertRaises(p.Invalid): p.TocData(b'12345')
        with mock.patch.object(p, 'MAX_NAME', 1):
            self.assertEqual(p.name('a'), 'a')
            with self.assertRaises(p.Invalid): p.name('ab')
        with mock.patch.object(p, 'MAX_ENTRIES', 1):
            self.assertEqual(p.TocData(marshal.dumps([1])).parse(), [1])
            with self.assertRaises(p.Invalid): p.TocData(marshal.dumps([1, 2])).parse()
        with mock.patch.object(p, 'MAX_NODES', 1):
            self.assertEqual(p.TocData(marshal.dumps([])).parse(), [])
            with self.assertRaises(p.Invalid): p.TocData(marshal.dumps([1])).parse()
        with mock.patch.object(p, 'MAX_DEPTH', 0):
            self.assertEqual(p.literals(b'[]'), [])
            with self.assertRaises(p.Invalid): p.literals(b'[[]]')
            with self.assertRaises(p.Invalid): p.TocData(marshal.dumps([[]])).parse()
        raw = carchive()
        with mock.patch.object(p, 'MAX_ARCHIVE', len(raw)):
            self.assertEqual(p.archive_inventory(raw, MAGIC)[2], pyz())
        with mock.patch.object(p, 'MAX_ARCHIVE', len(raw) - 1), self.assertRaises(p.Invalid):
            p.archive_inventory(raw, MAGIC)

    def test_file_and_payload_read_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'file'; path.write_bytes(b'1234')
            self.assertEqual(p.Reads().read(path, 4), b'1234')
            with self.assertRaises(p.Invalid): p.Reads().read(path, 3)
            with mock.patch.object(p, 'MAX_READ', 4):
                reads = p.Reads(); reads.read(path)
                with self.assertRaises(p.Invalid): reads.read(path)
            fixture = Fixture(root / 'f')
            with mock.patch.object(p, 'MAX_PAYLOAD', 1), self.assertRaises(p.Invalid): fixture.inspect()
            with mock.patch.object(p, 'MAX_FILES', 1), self.assertRaises(p.Invalid): p.candidate_files(fixture.bundle)


if __name__ == '__main__':
    unittest.main()

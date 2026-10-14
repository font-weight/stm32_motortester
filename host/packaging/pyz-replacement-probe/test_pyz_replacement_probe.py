"""Opaque data fixtures and modeled new-tail-only tests; no candidate/library execution."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import marshal
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile
import zlib

SCRIPT = Path(__file__).with_name('pyz_replacement_probe.py')
spec = importlib.util.spec_from_file_location('probe_under_test', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
TOOL_PATH = Path(os.environ.get('PYZ_PROBE_INSPECTOR',
    '/workspace/shared/provenance-inspector-critic-v4-20261012/source/host/packaging/inspect_candidate_provenance.py'))
SOURCE_ZIP = Path(os.environ.get('PYZ_PROBE_SOURCE_ZIP',
    '/workspace/shared/pr171-final-package-binding-critic-20261013/source-PR171-244b0786.zip'))
WHEEL = Path(os.environ.get('PYZ_PROBE_WHEEL',
    '/workspace/shared/run14-license-provenance-audit-20261012/wheels/' + p.WHEEL_NAME))
ORIGINAL = Path(os.environ.get('PYZ_PROBE_ORIGINAL',
    '/workspace/shared/pyz-replacement-author-evidence-20261014/retained-original-init.py')).read_bytes()
if len(ORIGINAL) != p.ORIGINAL_BYTES or p.sha(ORIGINAL) != p.ORIGINAL_SHA:
    raise RuntimeError('fixed original fixture mismatch')
t = p.load_tool(TOOL_PATH)
TRIAL = '0123456789abcdef0123456789abcdef'
MAGIC = b'\xf3\r\r\n'


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def pyz(target=b'opaque target fixture', extra=b'opaque unchanged fixture', kind=1,
        names=('PySide6', 'unchanged')):
    data = bytearray(b'PYZ\0' + MAGIC + b'\0' * 9)
    rows = []
    for label, which, raw in [(names[0], kind, target), (names[1], 0, extra)]:
        stored = zlib.compress(raw)
        rows.append((label, (which, len(data), len(stored))))
        data.extend(stored)
    offset = len(data)
    data.extend(marshal.dumps(rows))  # Data tuples only; no code objects/load.
    data[8:12] = struct.pack('!i', offset)
    return bytes(data)


def exe(pyz_bytes, bootstrap=b'opaque bootstrap fixture'):
    body, toc = bytearray(), bytearray()
    for label, typ, raw in [('bootstrap', 'm', bootstrap),
                            ('pyi-contents-directory _internal', 'o', b''), ('PYZ.pyz', 'z', pyz_bytes)]:
        label = label.encode() + b'\0'
        size = (t.ENTRY.size + len(label) + 15) // 16 * 16
        toc.extend(t.ENTRY.pack(size, len(body), len(raw), len(raw), 0, typ.encode()))
        toc.extend(label.ljust(size - t.ENTRY.size, b'\0'))
        body.extend(raw)
    cookie = t.COOKIE.pack(t.COOKIE_MAGIC, len(body) + len(toc) + t.COOKIE.size,
                           len(body), len(toc), 313, b'python313.dll'.ljust(64, b'\0'))
    return b'MZopaque-stub' + body + toc + cookie


def recording(executable):
    segments = []
    for i in range(2):
        segments.append(dict(ordinal=i, kind='capture', sequence=i + 1, sample_count=2048,
            raw_sha256='a' * 64, samples_sha256='b' * 64,
            provenance=dict(generation=3 + 3 * i, config_id=1, acquisition_mode=1, auto_timeout_ms=0,
                host_elapsed_ms=i * 100, wire_version='0.1', start_operation='arm', wire_session_nonce=123,
                config_payload_hex='01000100000000080000000000000000', begin_request_id=0,
                begin_sequence=10 + i * 20, terminal_request_id=0, terminal_sequence=20 + i * 20,
                terminal_kind='end', end_samples_crc32=123, abort_reason_code=None, unavailable_reason=None)))
    return dict(schema_version=1, status='passed', scope='frozen-windows-recording', phase='complete', error=None,
        output='frozen-recording.json', workdir='frozen-recording.json.work',
        phases=[dict(name=name, utc='2026-10-14T00:00:%02d+00:00' % i)
                for i, name in enumerate(t.RECORDING_PHASES)],
        runtime=dict(platform='win32', os_name='nt', os_release='10', os_version='10.0.fixture', machine='AMD64',
                     python='3.13.16', implementation='CPython', python_bits=64, qt='6.12.0', pyside='6.12.0',
                     qt_platform='windows', frozen=True, optimize=0, application='0.1.0'),
        imports=t.RECORDING_IMPORTS.copy(), checks=t.RECORDING_CHECKS.copy(),
        identity=dict(recording_id='00000000-0000-4000-8000-000000000001',
                      session_id='00000000-0000-0000-0000-00000000007b', origin='simulator',
                      firmware_version='fixture', protocol_version='0.1', application_version='0.1.0'),
        stop=dict(request_id=8, ack_request_id=8, wire_session_nonce=123, generation=7,
                  last_window=2, status=0, termination_reason='user_stop', evidence='stop_ack'),
        segments=segments, artifacts={}, executable={'bytes': len(executable), 'sha256': p.sha(executable)},
        cleanup=t.RECORDING_CLEANUP.copy())


class Fixture:
    """Fake native metadata/opaque payloads; no runtime claim is possible."""
    def __init__(self, parent):
        self.root = parent / 'trial'
        self.output = parent / 'result.json'
        modified, patch = p.modification(ORIGINAL, TRIAL)
        self.prep = p.preparation_manifest(TRIAL, ORIGINAL, modified, patch, p.sha(SCRIPT.read_bytes()))
        for name, data in [('original-init.py', ORIGINAL), ('modified-init.py', modified),
                           ('change.patch', patch), ('preparation.json', p.encoded(self.prep))]:
            put(self.root / 'preparation' / name, data)
        self.report = dict(schema_version=1, scope='external-a-windows-replacement-execution', trial_id=TRIAL,
            application_commit=p.APP, application_tree=p.TREE, inspector_sha256=p.INSPECTOR_SHA,
            pyinstaller_wheel_sha256=p.PYINSTALLER_SHA,
            runtime={'platform': 'win32', 'python': '3.13.16', 'machine': 'AMD64', 'free_threaded': False},
            builds={}, runs=[])
        self.exes = {}
        for j, variant in enumerate(p.VARIANTS):
            base = self.root / variant
            payload = pyz(b'opaque original fixture' if variant == 'control' else b'opaque modified fixture')
            executable = exe(payload)
            self.exes[variant] = executable
            put(base / 'bundle/Motortester.exe', executable)
            put(base / 'bundle/_internal/example.dll', b'opaque equal native fixture')
            (base / 'bundle/replacement-probe').mkdir()
            put(base / 'PYZ-00.pyz', payload)
            put(base / 'build.log', b'modeled build log; no command executed\n')
            source_table = {'PySide6': {'bytes': p.ORIGINAL_BYTES if j == 0 else len(modified),
                                        'sha256': p.ORIGINAL_SHA if j == 0 else p.sha(modified)},
                            'unchanged': {'bytes': 22, 'sha256': p.sha(b'opaque source fixture')}}
            put(base / 'source-inputs.json', p.encoded(source_table))
            self.report['builds'][variant] = dict(initializer_sha256=p.ORIGINAL_SHA if j == 0 else p.sha(modified),
                source_tree_unchanged=True, changed_inputs=[] if j == 0 else [p.MEMBER], build_succeeded=True,
                log_sha256=p.sha((base / 'build.log').read_bytes()),
                source_inputs_sha256=p.sha((base / 'source-inputs.json').read_bytes()))
            receipts = base / 'receipts'
            put(receipts / p.RECEIPTS['serial'], p.encoded(p.SERIAL))
            png = b'\x89PNG\r\n\x1a\nopaque image fixture; no decoder was run'
            put(receipts / 'frozen-window.png', png)
            screen = dict(schema_version=1, status='passed', phase='complete', error=None,
                platform='win32', frozen=True, qt_platform='windows', output='frozen-window.png',
                phases=[dict(name=name, utc='2026-10-14T00:00:%02d+00:00' % i) for i, name in enumerate(
                    ('entry', 'gui-import', 'gui-imported', 'screenshot', 'png-validation', 'complete'))],
                png=dict(bytes=len(png), sha256=p.sha(png), width=1024, height=768))
            put(receipts / p.RECEIPTS['screenshot'], p.encoded(screen))
            rec = recording(executable)
            for key, name in [('archive', 'recording.mtrec'), ('spool', 'recording-' + 'f' * 32 + '.mtspool')]:
                data = ('opaque %s fixture' % key).encode()
                put(receipts / 'frozen-recording.json.work' / name, data)
                rec['artifacts'][key] = dict(name=name, bytes=len(data), sha256=p.sha(data))
            put(receipts / p.RECEIPTS['recording'], p.encoded(rec))
            for i, kind in enumerate(p.SMOKES):
                pid = 100 + 3 * j + i
                marker_hash = None
                if j and kind != 'serial':
                    marker = dict(schema_version=1, module='PySide6', trial_id=TRIAL, pid=pid,
                                  frozen=True, loader='pyimod02_importers.PyiFrozenLoader')
                    data = p.encoded(marker)
                    put(base / 'bundle/replacement-probe' / ('marker-%s-%d.json' % (TRIAL, pid)), data)
                    marker_hash = p.sha(data)
                self.report['runs'].append(dict(variant=variant, kind=kind, pid=pid, exit_code=0,
                    elapsed_ms=1000, timed_out=False, validator_passed=True,
                    command=p.COMMANDS[kind], started_utc='2026-10-14T00:00:00+00:00',
                    ended_utc='2026-10-14T00:00:01+00:00',
                    executable_sha256_before=p.sha(executable), executable_sha256_after=p.sha(executable),
                    receipt_sha256=p.sha((receipts / p.RECEIPTS[kind]).read_bytes()), marker_sha256=marker_hash))
        self.save()

    def save(self):
        put(self.root / 'execution.json', p.encoded(self.report))

    def replace_receipt(self, variant, kind, change):
        path = self.root / variant / 'receipts' / p.RECEIPTS[kind]
        data = json.loads(path.read_bytes())
        change(data)
        put(path, p.encoded(data))
        row = next(r for r in self.report['runs'] if (r['variant'], r['kind']) == (variant, kind))
        row['receipt_sha256'] = p.sha(path.read_bytes())
        self.save()

    def verify(self):
        return p.verify(t, self.root, SOURCE_ZIP, self.output)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pyz-probe-fixtures-')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)

    def reject_fixture(self, mutate, code=None):
        f = Fixture(self.parent)
        mutate(f)
        with self.assertRaises((p.Rejected, t.Invalid, OSError, ValueError)) as caught:
            f.verify()
        if code is not None:
            self.assertEqual(str(caught.exception), code)
        self.assertFalse(f.output.exists())

    def test_generated_positive_is_only_correspondence(self):
        f = Fixture(self.parent)
        result = f.verify()
        self.assertTrue(result['marker_and_smoke_receipt_correspondence'])
        self.assertEqual(result['native_file_count'], 1)
        for key in ('pyz_recombination_observed', 'actual_windows_execution_verified',
                    'native_replacement_verified', 'native_embedded_support_verified',
                    'corresponding_source_verified', 'legal_clearance', 'distribution_clearance',
                    'source_to_bytecode_attestation', 'exe_prefix_build_correspondence_verified'):
            self.assertIs(result[key], False)
        self.assertEqual(json.loads(f.output.read_bytes()), result)

    def test_actual_wheel_preparation_without_import(self):
        out = self.parent / 'prepared'
        result = p.prepare(t, WHEEL, SOURCE_ZIP, TRIAL, out)
        self.assertEqual(set(x.name for x in out.iterdir()), set(p.PREP_FILES))
        original = (out / 'original-init.py').read_bytes()
        modified = (out / 'modified-init.py').read_bytes()
        self.assertEqual(original, ORIGINAL)
        self.assertTrue(modified.startswith(original))
        self.assertNotEqual(p.sha(modified), p.ORIGINAL_SHA)
        self.assertFalse(result['actual_windows_execution'])
        self.assertNotIn('PySide6', sys.modules)

    def test_modification_is_fixed_and_trial_bound(self):
        first, patch = p.modification(ORIGINAL, TRIAL)
        second, _ = p.modification(ORIGINAL, 'a' * 32)
        self.assertNotEqual(first, second)
        self.assertIn(b"getattr(sys, 'frozen', False) is not True", first)
        self.assertIn(b"target.open('xb')", first)
        self.assertIn(b'PyiFrozenLoader', first)
        self.assertTrue(patch.startswith(b'--- PySide6/__init__.py'))

    def test_bad_original(self):
        for value in (ORIGINAL[:-1], b'x' + ORIGINAL[1:], b''):
            with self.subTest(bytes=len(value)), self.assertRaises(p.Rejected): p.modification(value, TRIAL)

    def test_fixed_member_zip_controls(self):
        def archive(names, mode=stat.S_IFREG):
            stream = io.BytesIO()
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(stream, 'w') as z:
                    for name in names:
                        info = zipfile.ZipInfo(name); info.external_attr = (mode | 0o644) << 16
                        z.writestr(info, ORIGINAL)
            return stream.getvalue()
        self.assertEqual(p.original_member(archive([p.MEMBER])), ORIGINAL)
        for names, mode in [([p.MEMBER, p.MEMBER], stat.S_IFREG),
                            ([p.MEMBER, p.MEMBER.upper()], stat.S_IFREG),
                            ([p.MEMBER.upper()], stat.S_IFREG), (['other.py'], stat.S_IFREG),
                            ([p.MEMBER], stat.S_IFLNK), ([p.MEMBER], stat.S_IFCHR)]:
            with self.subTest(names=names, mode=mode), self.assertRaises(p.Rejected):
                p.original_member(archive(names, mode))

    def test_wrong_source_and_wheel_reject_before_output(self):
        wrong = self.parent / p.WHEEL_NAME; put(wrong, b'wrong wheel')
        out = self.parent / 'not-created'
        with self.assertRaises(p.Rejected): p.prepare(t, wrong, SOURCE_ZIP, TRIAL, out)
        self.assertFalse(out.exists())
        source = self.parent / 'source.zip'; put(source, b'wrong source')
        with self.assertRaises(p.Rejected): p.prepare(t, WHEEL, source, TRIAL, out)
        self.assertFalse(out.exists())

    def test_prepare_no_overwrite(self):
        out = self.parent / 'existing'; out.mkdir(); put(out / 'keep', b'kept')
        with self.assertRaises(p.Rejected): p.prepare(t, WHEEL, SOURCE_ZIP, TRIAL, out)
        self.assertEqual((out / 'keep').read_bytes(), b'kept')
        self.assertEqual(set(x.name for x in out.iterdir()), {'keep'})

    def test_trial_types_and_names(self):
        for value in (None, True, 12, 'a' * 31, 'A' * 32, '../' + 'a' * 29, 'a' * 33):
            with self.subTest(value=value), self.assertRaises(p.Rejected): p.marker_append(value)

    def test_tool_tamper_never_executes(self):
        bad = self.parent / 'tool.py'
        put(bad, b'raise RuntimeError("must not execute")'.ljust(p.INSPECTOR_BYTES, b' '))
        with mock.patch('builtins.exec', side_effect=AssertionError('executed bad tool')):
            with self.assertRaises(p.Rejected): p.load_tool(bad)

    def test_reject_execution_identity_and_shape(self):
        changes = [('extra', lambda x: x.update(extra=1)), ('missing', lambda x: x.pop('scope')),
                   ('bool', lambda x: x.update(schema_version=True)),
                   ('app', lambda x: x.update(application_commit='0' * 40)),
                   ('runtime', lambda x: x['runtime'].update(platform='linux')),
                   ('false build', lambda x: x['builds']['modified'].update(build_succeeded=False)),
                   ('wrong target', lambda x: x['builds']['modified'].update(initializer_sha256=p.ORIGINAL_SHA)),
                   ('extra changed', lambda x: x['builds']['modified']['changed_inputs'].append('other.py')),
                   ('changed control', lambda x: x['builds']['control'].update(changed_inputs=[p.MEMBER]))]
        f = Fixture(self.parent)
        original = copy.deepcopy(f.report)
        for label, change in changes:
            with self.subTest(label=label):
                f.report = copy.deepcopy(original); change(f.report); f.save()
                with self.assertRaises((p.Rejected, t.Invalid)): f.verify()
                self.assertFalse(f.output.exists())

    def test_run_failure_fields(self):
        f = Fixture(self.parent)
        original = copy.deepcopy(f.report)
        for field, value in [('exit_code', True), ('exit_code', 1), ('elapsed_ms', True),
                             ('elapsed_ms', 60001), ('timed_out', True), ('validator_passed', False),
                             ('executable_sha256_before', '0' * 64), ('executable_sha256_after', '0' * 64),
                             ('receipt_sha256', '0' * 64), ('command', ['unknown']),
                             ('started_utc', '2026-10-15T00:00:00+00:00'),
                             ('pid', 0), ('pid', True), ('marker_sha256', '0' * 64)]:
            with self.subTest(field=field, value=value):
                f.report = copy.deepcopy(original); f.report['runs'][4][field] = value; f.save()
                with self.assertRaises((p.Rejected, t.Invalid)): f.verify()
                self.assertFalse(f.output.exists())

    def test_duplicate_pid(self):
        self.reject_fixture(lambda f: (f.report['runs'][4].update(pid=100), f.save()), 'duplicate_pid')

    def test_original_receipt_byte_hash(self):
        self.reject_fixture(lambda f: put(f.root / 'modified/receipts/frozen-recording.json',
             (f.root / 'modified/receipts/frozen-recording.json').read_bytes() + b' '), 'receipt_hash')

    def test_unchanged_copy_is_rejected(self):
        def change(f):
            new = f.exes['control']
            put(f.root / 'modified/bundle/Motortester.exe', new)
            put(f.root / 'modified/PYZ-00.pyz', pyz(b'opaque original fixture'))
            for row in f.report['runs'][3:]:
                row['executable_sha256_before'] = row['executable_sha256_after'] = p.sha(new)
            f.replace_receipt('modified', 'recording', lambda r: r.update(executable={'bytes': len(new), 'sha256': p.sha(new)}))
        self.reject_fixture(change, 'unchanged_target')

    def test_bootstrap_mutation_rejected(self):
        def mutate(f):
            path = f.root / 'modified/bundle/Motortester.exe'
            new = exe((f.root / 'modified/PYZ-00.pyz').read_bytes(), bootstrap=b'changed bootstrap')
            put(path, new)
            for row in f.report['runs'][3:]:
                row['executable_sha256_before'] = row['executable_sha256_after'] = p.sha(new)
            f.replace_receipt('modified', 'recording', lambda r: r.update(executable={'bytes': len(new), 'sha256': p.sha(new)}))
        self.reject_fixture(mutate, 'bootstrap_changed')

    def test_native_byte_mutation_rejected(self):
        self.reject_fixture(lambda f: put(f.root / 'modified/bundle/_internal/example.dll', b'changed'), 'native_changed')

    def test_extra_executable_rejected(self):
        self.reject_fixture(lambda f: put(f.root / 'modified/bundle/_internal/other.exe', b'not allowed'), 'extra_executable')

    def test_case_collision_rejected(self):
        self.reject_fixture(lambda f: put(f.root / 'modified/bundle/_internal/EXAMPLE.DLL', b'alias'), 'candidate_case_collision')

    def test_missing_or_extra_marker(self):
        self.reject_fixture(lambda f: put(f.root / 'modified/bundle/replacement-probe/extra.json', b'{}'), 'directory_members')

    def test_marker_wrong_trial_or_loader(self):
        f = Fixture(self.parent)
        path = next((f.root / 'modified/bundle/replacement-probe').iterdir())
        original = path.read_bytes()
        row = next(r for r in f.report['runs'] if r['marker_sha256'] == p.sha(original))
        for field, value in [('trial_id', 'a' * 32), ('frozen', False), ('pid', True),
                             ('loader', 'source.loader'), ('module', 'shiboken6')]:
            with self.subTest(field=field):
                data = json.loads(original); data[field] = value
                put(path, p.encoded(data)); row['marker_sha256'] = p.sha(path.read_bytes()); f.save()
                with self.assertRaises(p.Rejected): f.verify()
                self.assertFalse(f.output.exists())

    def test_serial_bool_not_integer(self):
        self.reject_fixture(lambda f: f.replace_receipt('control', 'serial', lambda r: r.update(schema_version=True)), 'serial_receipt')

    def test_screenshot_false_native(self):
        self.reject_fixture(lambda f: f.replace_receipt('modified', 'screenshot', lambda r: r.update(frozen=False)), 'screenshot_receipt')

    def test_png_bytes(self):
        self.reject_fixture(lambda f: put(f.root / 'modified/receipts/frozen-window.png', b'wrong'), 'screenshot_hash')

    def test_recording_artifact_bytes(self):
        self.reject_fixture(lambda f: put(f.root / 'modified/receipts/frozen-recording.json.work/recording.mtrec', b'changed'), 'recording_artifact')

    def test_recording_unsafe_name(self):
        self.reject_fixture(lambda f: f.replace_receipt('modified', 'recording',
            lambda r: r['artifacts']['archive'].update(name='../elsewhere')))

    def test_recording_chronology(self):
        self.reject_fixture(lambda f: f.replace_receipt('modified', 'recording',
            lambda r: r['stop'].update(request_id=1, ack_request_id=1)), 'recording_stop')

    def test_preparation_edited(self):
        self.reject_fixture(lambda f: put(f.root / 'preparation/modified-init.py', ORIGINAL), 'preparation_bytes')

    def test_preparation_extra(self):
        self.reject_fixture(lambda f: put(f.root / 'preparation/other.py', b''), 'directory_members')

    def test_input_layout_extra(self):
        self.reject_fixture(lambda f: put(f.root / 'other.json', b'{}'), 'layout_members')

    def test_json_duplicate_nonfinite_and_depth(self):
        f = Fixture(self.parent)
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}',
                    b'[' * 20 + b'0' + b']' * 20, b'{}' + b' ' * p.MAX_JSON):
            with self.subTest(bytes=len(raw)):
                put(f.root / 'execution.json', raw)
                with self.assertRaises((p.Rejected, t.Invalid, ValueError)): f.verify()
                self.assertFalse(f.output.exists())

    def test_output_no_overwrite(self):
        f = Fixture(self.parent); put(f.output, b'original')
        with self.assertRaises(p.Rejected): f.verify()
        self.assertEqual(f.output.read_bytes(), b'original')

    def test_output_case_alias_no_overwrite(self):
        f = Fixture(self.parent); alias = f.output.with_name(f.output.name.upper()); put(alias, b'original')
        with self.assertRaises(p.Rejected): f.verify()
        self.assertEqual(alias.read_bytes(), b'original')
        self.assertFalse(f.output.exists())

    def test_new_directory_case_alias(self):
        original = self.parent / 'EXISTING'; original.mkdir()
        with self.assertRaises(p.Rejected): p.create_output_dir(self.parent / 'existing', ())
        self.assertEqual(set(self.parent.iterdir()), {original})

    def test_output_overlap(self):
        f = Fixture(self.parent); f.output = f.root / 'new-result.json'
        with self.assertRaises(p.Rejected): f.verify()
        self.assertFalse(f.output.exists())

    def test_device_and_invalid_components(self):
        for name in ('CON', 'CONOUT$', 'CONIN$', 'CLOCK$', 'COM0.json', 'COM¹.json',
                     'LPT².dll', 'bad*.json', 'bad?.json', 'bad|.json', 'name.', 'name '):
            with self.subTest(name=name), self.assertRaises(p.Rejected): p.safe_path(self.parent / name)
        for name in ('C:relative', 'C:\\absolute', '//server/share', '\\\\server\\share', 'one\\two'):
            with self.subTest(name=name), self.assertRaises(p.Rejected): p.safe_path(name)

    def test_input_symlink_rejected(self):
        def mutate(f):
            path = f.root / 'modified/bundle/_internal/example.dll'
            target = self.parent / 'outside.dll'; put(target, path.read_bytes())
            path.unlink(); path.symlink_to(target)
        self.reject_fixture(mutate, 'redirected_path')

    def test_output_ancestor_symlink_rejected(self):
        f = Fixture(self.parent)
        actual = self.parent / 'actual'; actual.mkdir()
        link = self.parent / 'redirect'; link.symlink_to(actual, target_is_directory=True)
        f.output = link / 'result.json'
        with self.assertRaises(p.Rejected): f.verify()
        self.assertFalse((actual / 'result.json').exists())

    def test_mutation_before_final_recheck(self):
        f = Fixture(self.parent)
        real = p.Inputs.recheck
        def changed(inputs):
            put(f.root / 'modified/bundle/_internal/example.dll', b'changed after initial hash')
            return real(inputs)
        with mock.patch.object(p.Inputs, 'recheck', changed), self.assertRaises(t.Invalid): f.verify()
        self.assertFalse(f.output.exists())

    def test_resource_budget_before_read(self):
        inputs = p.Inputs(t)
        path = self.parent / 'large'; put(path, b'x' * 20)
        with mock.patch.object(p, 'MAX_READ', 19), mock.patch.object(Path, 'open', side_effect=AssertionError('opened')):
            with self.assertRaises(p.Rejected): inputs.read(path)

    def test_aggregate_growth_between_stats_rejects_before_open(self):
        for index, budget in enumerate((15, p.MAX_READ)):
            with self.subTest(budget=budget):
                path = self.parent / ('growing-' + str(index)); put(path, b'a' * 10)
                inputs = p.Inputs(t)
                inputs.reads.total = budget - 15
                original_read = inputs.reads.read
                original_open = Path.open
                opened_for_read = []
                def growth(*args, **kwargs):
                    put(path, b'b' * 20)
                    return original_read(*args, **kwargs)
                def observed_open(item, mode='r', *args, **kwargs):
                    if item == path and mode == 'rb': opened_for_read.append(item)
                    return original_open(item, mode, *args, **kwargs)
                with mock.patch.object(p, 'MAX_READ', budget), \
                     mock.patch.object(inputs.reads, 'read', growth), \
                     mock.patch.object(Path, 'open', observed_open):
                    with self.assertRaises(t.Invalid) as error: inputs.read(path, 30)
                self.assertEqual(str(error.exception), 'file_limit_or_type')
                self.assertEqual(opened_for_read, [])
                self.assertEqual(inputs.reads.total, budget - 15)

    def test_aggregate_exact_limit_and_second_hash_charge(self):
        path = self.parent / 'budget-boundary'; put(path, b'a' * 10)
        inputs = p.Inputs(t)
        with mock.patch.object(p, 'MAX_READ', 20):
            self.assertEqual(inputs.read(path, 30), b'a' * 10)
            inputs.recheck()
            self.assertEqual(inputs.reads.total, 20)
            with mock.patch.object(Path, 'open', side_effect=AssertionError('opened')):
                with self.assertRaises(p.Rejected) as error: inputs.read(path, 30)
            self.assertEqual(str(error.exception), 'read_budget')

    def test_aggregate_growth_to_exact_remaining_limit(self):
        path = self.parent / 'growth-boundary'; put(path, b'a' * 10)
        inputs = p.Inputs(t)
        original_read = inputs.reads.read
        def growth(*args, **kwargs):
            put(path, b'b' * 15)
            return original_read(*args, **kwargs)
        with mock.patch.object(p, 'MAX_READ', 15), mock.patch.object(inputs.reads, 'read', growth):
            self.assertEqual(inputs.read(path, 30), b'b' * 15)
        self.assertEqual(inputs.reads.total, 15)

    def test_original_smoke_deadline_boundaries(self):
        f = Fixture(self.parent)
        for index, row in enumerate(f.report['runs']):
            limit = 30000 if row['kind'] == 'serial' else 60000
            with self.subTest(variant=row['variant'], kind=row['kind']):
                report = copy.deepcopy(f.report)
                report['runs'][index]['elapsed_ms'] = limit
                p.execution_structure(t, report, f.prep)
                report['runs'][index]['elapsed_ms'] = limit + 1
                with self.assertRaises(p.Rejected) as error:
                    p.execution_structure(t, report, f.prep)
                self.assertEqual(str(error.exception), 'integer')
        f.report['runs'][0]['elapsed_ms'] = 45000
        f.save()
        with self.assertRaises(p.Rejected) as error: f.verify()
        self.assertEqual(str(error.exception), 'integer')
        self.assertFalse(f.output.exists())

    def test_oversized_file_admission(self):
        inputs = p.Inputs(t); path = self.parent / 'large'; put(path, b'x' * 100)
        with mock.patch.object(Path, 'open', side_effect=AssertionError('opened')):
            with self.assertRaises(t.Invalid): inputs.read(path, 99)

    def test_bad_work_pyz_and_cookie(self):
        self.reject_fixture(lambda f: put(f.root / 'modified/PYZ-00.pyz', b'invalid'), 'python_magic')

    def test_work_pyz_difference_is_qualified_not_authoritative(self):
        f = Fixture(self.parent)
        put(f.root / 'modified/PYZ-00.pyz', pyz(b'different retained build artifact'))
        result = f.verify()
        self.assertIs(result['binaries']['modified']['work_pyz_matches_embedded'], False)
        self.assertIs(result['binaries']['control']['work_pyz_matches_embedded'], True)
        self.assertIs(result['source_to_bytecode_attestation'], False)
        self.assertIs(result['pyz_recombination_observed'], False)

    def test_missing_target_package(self):
        f = Fixture(self.parent)
        raw = pyz(kind=0)
        put(f.root / 'modified/PYZ-00.pyz', raw)
        put(f.root / 'modified/bundle/Motortester.exe', exe(raw))
        with self.assertRaises(p.Rejected) as error: f.verify()
        self.assertEqual(str(error.exception), 'target_package')
        self.assertFalse(f.output.exists())

    def test_duplicate_case_alias_and_absent_embedded_target(self):
        cases = [(('PySide6', 'PySide6'), 'duplicate_pyz_module'),
                 (('PySide6', 'pyside6'), 'target_package'),
                 (('pyside6', 'unchanged'), 'target_package'),
                 (('unselected', 'unchanged'), 'target_package')]
        for index, (names, code) in enumerate(cases):
            with self.subTest(names=names):
                f = Fixture(self.parent / str(index))
                raw = pyz(names=names)
                put(f.root / 'modified/bundle/Motortester.exe', exe(raw))
                with self.assertRaises((p.Rejected, t.Invalid)) as error: f.verify()
                self.assertEqual(str(error.exception), code)
                self.assertFalse(f.output.exists())

    def test_bad_carchive_cookie(self):
        def change(f):
            path = f.root / 'modified/bundle/Motortester.exe'
            put(path, path.read_bytes()[:-1])
        self.reject_fixture(change, 'ambiguous_archive_cookie')

    def test_unexplained_pure_module_difference(self):
        def change(f):
            raw = pyz(b'opaque modified fixture', extra=b'changed second module')
            new = exe(raw)
            put(f.root / 'modified/PYZ-00.pyz', raw)
            put(f.root / 'modified/bundle/Motortester.exe', new)
            for row in f.report['runs'][3:]:
                row['executable_sha256_before'] = row['executable_sha256_after'] = p.sha(new)
            f.replace_receipt('modified', 'recording', lambda r: r.update(executable={'bytes': len(new), 'sha256': p.sha(new)}))
        self.reject_fixture(change, 'unexplained_module_change')

    def test_pyz_overlap_rejected_before_payload_hashes(self):
        raw = bytearray(pyz())
        offset = struct.unpack('!i', raw[8:12])[0]
        body = raw[17:offset]
        rows = [('PySide6', (1, 17, len(body))), ('duplicate_span', (0, 17, len(body)))]
        bad = bytes(raw[:offset]) + marshal.dumps(rows)
        with mock.patch.object(t, 'digest', side_effect=AssertionError('hashed overlapping payload')):
            with self.assertRaises(t.Invalid) as error: t.pyz_inventory(bad, MAGIC)
        self.assertEqual(str(error.exception), 'overlapping_entries')

    def test_native_count_bound(self):
        f = Fixture(self.parent)
        with mock.patch.object(p, 'MAX_NATIVE_FILES', 0), self.assertRaises(p.Rejected): f.verify()
        self.assertFalse(f.output.exists())

    def test_missing_marker_and_extra_recording_artifact(self):
        f = Fixture(self.parent)
        next((f.root / 'modified/bundle/replacement-probe').iterdir()).unlink()
        with self.assertRaises(p.Rejected): f.verify()
        self.assertFalse(f.output.exists())

    def test_extra_recording_artifact(self):
        self.reject_fixture(lambda f: put(f.root / 'control/receipts/frozen-recording.json.work/extra', b'x'),
                            'recording_extra_artifact')

    def test_recording_directory_changed_after_read(self):
        f = Fixture(self.parent)
        real = p.Inputs.recheck
        def changed(inputs):
            real(inputs)
            put(f.root / 'modified/receipts/frozen-recording.json.work/extra', b'x')
        with mock.patch.object(p.Inputs, 'recheck', changed), self.assertRaises(t.Invalid) as error:
            f.verify()
        self.assertEqual(str(error.exception), 'recording_extra_artifact')
        self.assertFalse(f.output.exists())

    def test_same_size_mutation_with_restored_mtime(self):
        f = Fixture(self.parent)
        real = p.Inputs.recheck
        def changed(inputs):
            path = f.root / 'modified/bundle/_internal/example.dll'
            info = path.stat(); put(path, b'x' * info.st_size)
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            return real(inputs)
        with mock.patch.object(p.Inputs, 'recheck', changed), self.assertRaises(t.Invalid): f.verify()
        self.assertFalse(f.output.exists())

    def test_bootstrap_path_handle_ctime_semantics(self):
        path = TOOL_PATH
        real_fstat = p.os.fstat
        def different_ctime(fd):
            value = real_fstat(fd)
            class Info:
                def __getattr__(self, name):
                    if name == 'st_ctime_ns': return value.st_ctime_ns + 1000000
                    return getattr(value, name)
            return Info()
        with mock.patch.object(p.os, 'fstat', different_ctime):
            self.assertEqual(p.load_tool(path).MAX_ARCHIVE, 64 * p.MIB)

    def test_output_bytes_cap(self):
        path = self.parent / 'result'
        with self.assertRaises(p.Rejected): p.write_exclusive(path, b'x' * (p.MAX_REPORT + 1))
        self.assertFalse(path.exists())

    def test_source_table_changes_rejected(self):
        def change(f):
            path = f.root / 'modified/source-inputs.json'
            table = json.loads(path.read_bytes()); table['unchanged']['sha256'] = 'f' * 64
            put(path, p.encoded(table))
            f.report['builds']['modified']['source_inputs_sha256'] = p.sha(path.read_bytes()); f.save()
        self.reject_fixture(change, 'unexplained_source_change')

    def test_source_table_unknown_and_boolean(self):
        f = Fixture(self.parent)
        path = f.root / 'modified/source-inputs.json'
        original = json.loads(path.read_bytes())
        for key, value in [('bytes', True), ('sha256', 'bad'), ('extra', 1)]:
            with self.subTest(field=key):
                table = copy.deepcopy(original); table['PySide6'][key] = value
                put(path, p.encoded(table))
                f.report['builds']['modified']['source_inputs_sha256'] = p.sha(path.read_bytes()); f.save()
                with self.assertRaises(p.Rejected): f.verify()
                self.assertFalse(f.output.exists())

    def tail_namespace(self):
        # Execute ONLY our generated appended operation, never the upstream prefix.
        loader_class = type('PyiFrozenLoader', (), {'__module__': 'pyimod02_importers'})
        return {'__loader__': loader_class()}

    def run_tail_fixture(self, executable, namespace=None):
        namespace = self.tail_namespace() if namespace is None else namespace
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'platform', 'win32'), mock.patch.object(sys, 'executable', str(executable)):
            exec(p.marker_append(TRIAL), namespace)
        return namespace

    def test_generated_tail_nonfrozen_is_inert(self):
        namespace = {}
        with mock.patch.object(sys, 'frozen', False, create=True), \
                mock.patch.object(Path, 'lstat', side_effect=AssertionError('filesystem touched')), \
                mock.patch.object(Path, 'open', side_effect=AssertionError('filesystem touched')):
            exec(p.marker_append(TRIAL), namespace)
        self.assertEqual(set(namespace), {'__builtins__'})
        self.assertNotIn('PySide6', sys.modules)

    def test_generated_tail_modeled_frozen_marker(self):
        executable = self.parent / 'bundle/Motortester.exe'
        put(executable, b'not executed')
        root = executable.parent / 'replacement-probe'; root.mkdir()
        namespace = self.run_tail_fixture(executable)
        paths = list(root.iterdir()); self.assertEqual(len(paths), 1)
        report = json.loads(paths[0].read_bytes())
        self.assertEqual(report, dict(schema_version=1, module='PySide6', trial_id=TRIAL,
            pid=os.getpid(), frozen=True, loader='pyimod02_importers.PyiFrozenLoader'))
        self.assertEqual(namespace['__motortester_replacement_probe__'], TRIAL)
        self.assertNotIn(str(self.parent), paths[0].read_text())
        original = paths[0].read_bytes()
        with self.assertRaisesRegex(RuntimeError, '^replacement_marker_failed$'):
            self.run_tail_fixture(executable)
        self.assertEqual(paths[0].read_bytes(), original)

    def test_generated_tail_missing_unsafe_unwritable_marker(self):
        executable = self.parent / 'bundle/Motortester.exe'; put(executable, b'not executed')
        with self.assertRaisesRegex(RuntimeError, '^replacement_marker_failed$'):
            self.run_tail_fixture(executable)
        target = self.parent / 'outside'; target.mkdir()
        root = executable.parent / 'replacement-probe'; root.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, '^replacement_marker_failed$'):
            self.run_tail_fixture(executable)
        root.unlink(); root.mkdir()
        with mock.patch.object(Path, 'open', side_effect=PermissionError('private path not propagated')):
            with self.assertRaisesRegex(RuntimeError, '^replacement_marker_failed$'):
                self.run_tail_fixture(executable)
        self.assertEqual(list(root.iterdir()), [])

    def test_generated_tail_rejects_source_loader(self):
        executable = self.parent / 'bundle/Motortester.exe'; put(executable, b'not executed')
        (executable.parent / 'replacement-probe').mkdir()
        with self.assertRaisesRegex(RuntimeError, '^replacement_marker_failed$'):
            self.run_tail_fixture(executable, {'__loader__': object()})

    def test_generated_tail_reparse_model(self):
        executable = self.parent / 'bundle/Motortester.exe'; put(executable, b'not executed')
        root = executable.parent / 'replacement-probe'; root.mkdir()
        real = Path.lstat
        def modeled(path):
            value = real(path)
            if path != root: return value
            class Info:
                st_file_attributes = 0x400
                def __getattr__(self, name): return getattr(value, name)
            return Info()
        with mock.patch.object(Path, 'lstat', modeled):
            with self.assertRaisesRegex(RuntimeError, '^replacement_marker_failed$'):
                self.run_tail_fixture(executable)

    def test_input_redirect_after_initial_read(self):
        f = Fixture(self.parent)
        real = p.Inputs.recheck
        def change(inputs):
            parent = f.root / 'modified/bundle/_internal'
            actual = self.parent / 'relocated'; parent.rename(actual); parent.symlink_to(actual, target_is_directory=True)
            return real(inputs)
        with mock.patch.object(p.Inputs, 'recheck', change), self.assertRaises(t.Invalid): f.verify()
        self.assertFalse(f.output.exists())

    def test_bootstrap_handle_mutation_rejected(self):
        real = p.os.fstat
        calls = 0
        def changing(fd):
            nonlocal calls
            value = real(fd); calls += 1
            observed = calls
            class Info:
                def __getattr__(self, name):
                    if name == 'st_mtime_ns': return value.st_mtime_ns + (1 if observed > 1 else 0)
                    return getattr(value, name)
            return Info()
        with mock.patch.object(p.os, 'fstat', changing), self.assertRaises(p.Rejected): p.load_tool(TOOL_PATH)

    def test_cli_full_fixture_remains_qualified(self):
        f = Fixture(self.parent)
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), 'verify',
            '--inspector', str(TOOL_PATH), '--trial-root', str(f.root), '--source-zip', str(SOURCE_ZIP),
            '--output', str(f.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(json.loads(f.output.read_bytes())['pyz_recombination_observed'], False)
        self.assertNotIn(str(self.parent), result.stdout + result.stderr)

    def test_cli_privacy(self):
        private = str(self.parent / 'PRIVATE-NEVER-PRINT')
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), 'prepare',
                                 '--inspector', private, '--wheel', private, '--source-zip', private,
                                 '--trial-id', TRIAL, '--output', private], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(private, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertRegex(result.stderr, '^REPLACEMENT_PROBE_FAILED code=[a-z_]+\\n$')

    def test_cli_isolation(self):
        result = subprocess.run([sys.executable, '-B', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('isolation_required', result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)

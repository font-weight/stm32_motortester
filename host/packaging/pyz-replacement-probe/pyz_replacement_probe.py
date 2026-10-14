"""Fixed PySide6 initializer preparation and receipt correspondence; no execution."""
import argparse
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import sys
import types
import zipfile

MIB = 1024 * 1024
APP = '244b0786a18fed19a9e708bb815150475ddd193f'
TREE = '0c0e61fb8aadd4a4380e2d7e0b8818e4c9da0d5e'
INSPECTOR_SHA = 'fa017e8ab7a348d45c74c86bb877968bac9aef77f963642cc7af396a5f2c8c53'
INSPECTOR_BYTES = 53390
SOURCE_SHA = 'e263fbb3b1734d8ca6d57d1db1493f2c6ce219979e2ffd581af56b4646c48d7f'
SOURCE_BYTES = 1411889
WHEEL_NAME = 'pyside6_essentials-6.12.0-cp310-abi3-win_amd64.whl'
WHEEL_SHA = 'c9a95102aa23c1f86516a30d5a9be37552d324eee7e567eb7508973f1218cb42'
WHEEL_BYTES = 82153795
MEMBER = 'PySide6/__init__.py'
ORIGINAL_SHA = '3b662ee91f12feb02507dfaff907b7306bf5c851f1374563f70fae19e13711fc'
ORIGINAL_BYTES = 6418
PYINSTALLER_SHA = '500bd58c7bf7e584a8435adccbd763a0b918d5c12b08d74ff50fd79b2915458b'
MAX_READ = 1024 * MIB  # Includes the final complete second hash pass.
MAX_JSON = 65536
MAX_REPORT = 65536
MAX_NATIVE_FILES = 256
VARIANTS = ('control', 'modified')
SMOKES = ('serial', 'screenshot', 'recording')
SMOKE_LIMIT_MS = {'serial': 30000, 'screenshot': 60000, 'recording': 60000}
RECEIPTS = {'serial': 'frozen-serial-import.json',
            'screenshot': 'frozen-window.png.smoke.json', 'recording': 'frozen-recording.json'}
COMMANDS = {kind: ['bundle/Motortester.exe', flag, 'receipts/' + output] for kind, flag, output in
            [('serial', '--serial-import-smoke', RECEIPTS['serial']),
             ('screenshot', '--screenshot', 'frozen-window.png'),
             ('recording', '--recording-smoke', RECEIPTS['recording'])]}
PREP_FILES = ('original-init.py', 'modified-init.py', 'change.patch', 'preparation.json')
SERIAL = {'schema_version': 1, 'scope': 'frozen-windows-serial-import-only',
          'status': 'passed', 'platform': 'win32', 'os_name': 'nt', 'frozen': True,
          'pyserial_version': '3.5',
          'imports': ['serial', 'serial.serialutil', 'serial.serialwin32', 'serial.win32',
                      'motortester.transport'],
          'serial_backend': 'serial.serialwin32.Serial',
          'transport_class': 'motortester.transport.SerialTransport', 'device_io_performed': False}


class Rejected(Exception):
    """Only fixed, sanitized machine codes may escape the CLI."""


def require(condition, code):
    if not condition:
        raise Rejected(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2,
                       allow_nan=False) + '\n').encode('ascii')


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, 'integer')
    return value


def hexadecimal(value, length=64):
    require(type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None,
            'identity')
    return value


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), 'schema')


def safe_path(raw):
    """Small bootstrap guard, before the pinned trusted reader can be loaded."""
    value = os.fspath(raw)
    require(type(value) is str and 0 < len(value.encode('utf-8')) <= 1024
            and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'path')
    require(not value.startswith(('//', '\\\\')) and '..' not in Path(value).parts, 'path')
    drive, tail = ntpath.splitdrive(value)
    require((not drive and ':' not in value) or
            (os.name == 'nt' and re.fullmatch('[A-Za-z]:', drive) is not None
             and tail.startswith(('\\', '/')) and ':' not in tail), 'path')
    path = Path(os.path.abspath(value))
    require(len(path.parts) <= 64, 'path')
    for part in path.parts[1:]:
        require(part not in ('', '.', '..') and not part.endswith((' ', '.'))
                and not any(c in '<>"|?*:\\' for c in part), 'path')
        stem = part.split('.')[0].rstrip(' ').upper()
        require(stem not in ('CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$', 'CLOCK$')
                and re.fullmatch(r'(COM|LPT)[0-9¹²³]', stem) is None, 'path')
    return path


def ancestors(path):
    path = safe_path(path)
    for item in reversed((path,) + tuple(path.parents)):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not (getattr(info, 'st_file_attributes', 0) & 0x400), 'redirect')
        if item != path:
            require(stat.S_ISDIR(info.st_mode), 'ancestor_type')
    return path


def shared(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stat.S_IFMT(info.st_mode)


def fingerprint(info):
    return shared(info) + (info.st_ctime_ns, getattr(info, 'st_birthtime_ns', None))


def load_tool(path):
    path = ancestors(path)
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_size == INSPECTOR_BYTES, 'tool_size')
    with path.open('rb') as stream:
        handle = os.fstat(stream.fileno())
        require(shared(handle) == shared(before), 'tool_changed')
        data = stream.read(INSPECTOR_BYTES + 1)
        require(fingerprint(os.fstat(stream.fileno())) == fingerprint(handle), 'tool_changed')
    require(fingerprint(ancestors(path).stat()) == fingerprint(before)
            and len(data) == INSPECTOR_BYTES and sha(data) == INSPECTOR_SHA, 'tool_hash')
    # The ONLY executed external body is this exact already-reviewed tool hash.
    # Candidate code, extracted modules and marshal code objects are never loaded.
    tool = types.ModuleType('_pinned_provenance_reader')
    exec(compile(data, '<pinned-provenance-reader>', 'exec'), tool.__dict__)
    return tool


class Inputs:
    def __init__(self, tool):
        self.tool = tool
        self.reads = tool.Reads()
        self.caps = {}

    def read(self, path, cap=MAX_JSON, keep=True):
        path = self.tool.no_redirect(safe_path(path))
        size = path.stat().st_size
        remaining = MAX_READ - self.reads.total
        require(0 <= size <= remaining, 'read_budget')
        # The pinned reader takes its own initial stat. Bound that admission,
        # rather than trusting the potentially stale preliminary size above.
        value = self.reads.read(path, min(cap, remaining), keep=keep)
        require(self.reads.total <= MAX_READ, 'read_budget')
        self.caps[path] = min(cap, self.caps.get(path, cap))
        return value

    def json(self, path):
        return self.tool.manifest_json(self.read(path))

    def recheck(self):
        self.reads.unchanged()
        for path, cap in tuple(self.caps.items()):
            self.read(path, cap, keep=False)
        self.reads.unchanged()


def exact_directory(tool, root, expected):
    root = tool.no_redirect(root)
    require(stat.S_ISDIR(root.stat().st_mode), 'directory_type')
    found = set()
    with os.scandir(root) as entries:
        for entry in entries:
            require(len(found) < len(expected) and entry.name in expected
                    and entry.name not in found, 'directory_members')
            found.add(entry.name)
            require(stat.S_ISREG(tool.child(root, entry.name).stat().st_mode), 'file_type')
    require(found == set(expected), 'directory_members')


def fixed_layout(tool, root, files, directories):
    """One directory only, with a fixed small name/type allowlist."""
    expected = set(files) | set(directories)
    found = set()
    with os.scandir(tool.no_redirect(root)) as entries:
        for entry in entries:
            require(len(found) < len(expected) and entry.name in expected
                    and entry.name not in found, 'layout_members')
            found.add(entry.name)
            mode = tool.child(root, entry.name).stat().st_mode
            require(stat.S_ISDIR(mode) if entry.name in directories else stat.S_ISREG(mode), 'layout_type')
    require(found == expected, 'layout_members')


def marker_append(trial):
    hexadecimal(trial, 32)
    # Data emitted for A's separately authorized experiment; never executed here.
    return ("\n# Local replacement experiment; original upstream bytes above are unchanged.\n"
            "# This appended probe is not part of the upstream PySide6 release.\n"
            "def _motortester_replacement_marker():\n"
            "    import sys\n"
            "    if getattr(sys, 'frozen', False) is not True:\n"
            "        return\n"
            "    import os, json, stat\n"
            "    from pathlib import Path\n"
            "    global __motortester_replacement_probe__\n"
            "    __motortester_replacement_probe__ = '" + trial + "'\n"
            "    if sys.platform != 'win32' or Path(sys.executable).name != 'Motortester.exe':\n"
            "        raise RuntimeError('replacement_runtime')\n"
            "    root = Path(sys.executable).parent / 'replacement-probe'\n"
            "    observed = []\n"
            "    for part in reversed((root,) + tuple(root.parents)):\n"
            "        info = part.lstat()\n"
            "        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & 0x400):\n"
            "            raise RuntimeError('replacement_directory')\n"
            "        observed.append((part, info.st_dev, info.st_ino))\n"
            "    loader = type(__loader__).__module__ + '.' + type(__loader__).__name__\n"
            "    if loader != 'pyimod02_importers.PyiFrozenLoader':\n"
            "        raise RuntimeError('replacement_loader')\n"
            "    pid = os.getpid()\n"
            "    value = {'schema_version': 1, 'module': 'PySide6', 'trial_id': __motortester_replacement_probe__, 'pid': pid, 'frozen': True, 'loader': loader}\n"
            "    target = root / ('marker-' + __motortester_replacement_probe__ + '-' + str(pid) + '.json')\n"
            "    data = (json.dumps(value, sort_keys=True) + '\\n').encode('ascii')\n"
            "    if len(data) > 1024:\n"
            "        raise RuntimeError('replacement_marker_limit')\n"
            "    with target.open('xb') as stream:\n"
            "        stream.write(data)\n"
            "        stream.flush()\n"
            "        handle = os.fstat(stream.fileno())\n"
            "    for part, device, inode in observed:\n"
            "        info = part.lstat()\n"
            "        if (info.st_dev, info.st_ino) != (device, inode) or not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & 0x400):\n"
            "            raise RuntimeError('replacement_directory_changed')\n"
            "    info = target.lstat()\n"
            "    if not stat.S_ISREG(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & 0x400) or (info.st_dev, info.st_ino, info.st_size) != (handle.st_dev, handle.st_ino, len(data)):\n"
            "        raise RuntimeError('replacement_marker_changed')\n"
            "try:\n"
            "    _motortester_replacement_marker()\n"
            "except Exception:\n"
            "    raise RuntimeError('replacement_marker_failed') from None\n"
            "del _motortester_replacement_marker\n").encode('ascii')


def modification(original, trial):
    require(len(original) == ORIGINAL_BYTES and sha(original) == ORIGINAL_SHA, 'original_hash')
    added = marker_append(trial)
    require(original.endswith(b'\n') and len(added) < 4096, 'modification_bound')
    modified = original + added
    patch = b'--- PySide6/__init__.py (original)\n+++ PySide6/__init__.py (local trial)\n'
    patch += ('@@ -%d,0 +%d,%d @@\n' %
              (len(original.splitlines()), len(original.splitlines()) + 1,
               len(added.splitlines()))).encode('ascii')
    patch += b''.join(b'+' + line + b'\n' for line in added.splitlines())
    return modified, patch


def preparation_manifest(trial, original, modified, patch, probe_sha256):
    return {'schema_version': 1, 'scope': 'fixed-pyside-initializer-preparation',
            'application_commit': APP, 'application_tree': TREE, 'trial_id': trial,
            'wheel': {'name': WHEEL_NAME, 'bytes': WHEEL_BYTES, 'sha256': WHEEL_SHA},
            'source_zip': {'bytes': SOURCE_BYTES, 'sha256': SOURCE_SHA},
            'member': MEMBER, 'inspector_sha256': INSPECTOR_SHA,
            'probe_sha256': hexadecimal(probe_sha256),
            'pyinstaller_wheel_sha256': PYINSTALLER_SHA,
            'files': {name: {'bytes': len(data), 'sha256': sha(data)} for name, data in
                      [('original-init.py', original), ('modified-init.py', modified), ('change.patch', patch)]},
            'actual_windows_execution': False, 'legal_clearance': False,
            'corresponding_source_verified': False, 'native_replacement_verified': False}


def require_new_name(parent, name):
    require(not os.path.lexists(parent / name), 'output_exists')
    count = 0
    with os.scandir(parent) as entries:
        for entry in entries:
            count += 1
            require(count <= 4096, 'output_parent_limit')
            require(entry.name.casefold() != name.casefold(), 'output_exists')


def create_output_dir(path, forbidden):
    path = safe_path(path)
    parent = ancestors(path.parent)
    require(stat.S_ISDIR(parent.stat().st_mode), 'output_parent_type')
    require_new_name(parent, path.name)
    for root in forbidden:
        root = safe_path(root)
        require(path != root and path not in root.parents and root not in path.parents, 'output_overlap')
    identity = shared(parent.stat())[:2]
    path.mkdir()
    require(shared(ancestors(parent).stat())[:2] == identity, 'output_parent_changed')
    return path


def write_exclusive(path, data):
    require(len(data) <= MAX_REPORT, 'output_limit')
    parent = ancestors(path.parent)
    identity = shared(parent.stat())[:2]
    require_new_name(parent, path.name)
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        handle = os.fstat(stream.fileno())
        require(stat.S_ISREG(handle.st_mode) and handle.st_size == len(data), 'output_type')
    require(shared(ancestors(parent).stat())[:2] == identity
            and shared(ancestors(path).stat()) == shared(handle), 'output_parent_changed')


def source_identity(inputs, path):
    size, hashed = inputs.read(path, SOURCE_BYTES, keep=False)
    require((size, hashed) == (SOURCE_BYTES, SOURCE_SHA), 'source_zip_hash')


def original_member(data):
    # Parse only an already hash-authenticated fixed official wheel.
    require(len(data) <= WHEEL_BYTES, 'wheel_limit')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        require(len(infos) <= 4096, 'wheel_entries')
        matches = [entry for entry in infos if entry.filename.casefold() == MEMBER.casefold()]
        require(len(matches) == 1 and matches[0].filename == MEMBER, 'wheel_member_ambiguity')
        entry = matches[0]
        require(entry.file_size == ORIGINAL_BYTES and not entry.is_dir()
                and not entry.flag_bits & 1 and entry.compress_type in (0, 8)
                and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG), 'wheel_member_type')
        with archive.open(entry) as stream:
            original = stream.read(ORIGINAL_BYTES + 1)
    require(len(original) == ORIGINAL_BYTES and sha(original) == ORIGINAL_SHA, 'original_hash')
    return original


def prepare(tool, wheel, source_zip, trial, output):
    hexadecimal(trial, 32)
    require(Path(wheel).name == WHEEL_NAME, 'wheel_name')
    inputs = Inputs(tool)
    probe_sha = sha(inputs.read(Path(__file__), 128 * 1024))
    source_identity(inputs, source_zip)
    data = inputs.read(wheel, WHEEL_BYTES)
    require(len(data) == WHEEL_BYTES and sha(data) == WHEEL_SHA, 'wheel_hash')
    original = original_member(data)
    modified, patch = modification(original, trial)
    result = preparation_manifest(trial, original, modified, patch, probe_sha)
    inputs.recheck()
    root = create_output_dir(output, (wheel, source_zip))
    for name, body in [('original-init.py', original), ('modified-init.py', modified), ('change.patch', patch)]:
        write_exclusive(root / name, body)
    inputs.reads.unchanged()
    write_exclusive(root / 'preparation.json', encoded(result))
    return result


def read_preparation(inputs, root):
    tool = inputs.tool
    exact_directory(tool, root, PREP_FILES)
    saved = inputs.json(root / 'preparation.json')
    require(type(saved) is dict and type(saved.get('trial_id')) is str, 'preparation_schema')
    trial = hexadecimal(saved['trial_id'], 32)
    original = inputs.read(root / 'original-init.py', ORIGINAL_BYTES)
    modified, patch = modification(original, trial)
    require(inputs.read(root / 'modified-init.py', 16384) == modified
            and inputs.read(root / 'change.patch', 8192) == patch, 'preparation_bytes')
    expected = preparation_manifest(trial, original, modified, patch,
                                    sha(inputs.read(Path(__file__), 128 * 1024)))
    require(tool.same_json(saved, expected), 'preparation_schema')
    return expected


def execution_structure(tool, report, prep):
    keys(report, ('schema_version', 'scope', 'trial_id', 'application_commit', 'application_tree',
                  'inspector_sha256', 'pyinstaller_wheel_sha256', 'runtime', 'builds', 'runs'))
    for key, value in {'schema_version': 1, 'scope': 'external-a-windows-replacement-execution',
                       'trial_id': prep['trial_id'], 'application_commit': APP,
                       'application_tree': TREE, 'inspector_sha256': INSPECTOR_SHA,
                       'pyinstaller_wheel_sha256': PYINSTALLER_SHA}.items():
        require(tool.same_json(report[key], value), 'execution_identity')
    require(tool.same_json(report['runtime'], {'platform': 'win32', 'python': '3.13.16',
                'machine': 'AMD64', 'free_threaded': False}), 'execution_runtime')
    keys(report['builds'], VARIANTS)
    for variant, target in (('control', ORIGINAL_SHA), ('modified', prep['files']['modified-init.py']['sha256'])):
        value = report['builds'][variant]
        keys(value, ('initializer_sha256', 'source_tree_unchanged', 'changed_inputs',
                     'build_succeeded', 'log_sha256', 'source_inputs_sha256'))
        require(value['initializer_sha256'] == target, 'build_input')
        require(tool.same_json(value['changed_inputs'], [] if variant == 'control' else [MEMBER]),
                'changed_inputs')
        for key in ('source_tree_unchanged', 'build_succeeded'):
            require(value[key] is True, 'build_claim')
        hexadecimal(value['log_sha256'])
        hexadecimal(value['source_inputs_sha256'])
    require(type(report['runs']) is list and len(report['runs']) == 6, 'run_count')
    pids = set()
    for row, expected in zip(report['runs'], [(v, s) for v in VARIANTS for s in SMOKES]):
        keys(row, ('variant', 'kind', 'pid', 'exit_code', 'elapsed_ms', 'timed_out', 'command',
                   'started_utc', 'ended_utc', 'validator_passed', 'executable_sha256_before',
                   'executable_sha256_after', 'receipt_sha256', 'marker_sha256'))
        require((row['variant'], row['kind']) == expected, 'run_order')
        integer(row['pid'], 1, 0x7fffffff)
        require(row['pid'] not in pids, 'duplicate_pid')
        pids.add(row['pid'])
        require(type(row['exit_code']) is int and row['exit_code'] == 0
                and row['timed_out'] is False and row['validator_passed'] is True, 'run_failed')
        integer(row['elapsed_ms'], 0, SMOKE_LIMIT_MS[row['kind']])
        require(tool.same_json(row['command'], COMMANDS[row['kind']]), 'run_command')
        stamps = []
        for label in ('started_utc', 'ended_utc'):
            require(type(row[label]) is str and len(row[label]) <= 64, 'run_time')
            stamp = tool.datetime.fromisoformat(row[label])
            require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0, 'run_time')
            stamps.append(stamp)
        require(stamps[1] >= stamps[0], 'run_time')
        hexadecimal(row['executable_sha256_before']); hexadecimal(row['executable_sha256_after'])
        hexadecimal(row['receipt_sha256'])
        if row['variant'] == 'modified' and row['kind'] != 'serial':
            hexadecimal(row['marker_sha256'])
        else:
            require(row['marker_sha256'] is None, 'unexpected_marker')


def screenshot(inputs, root, report):
    tool = inputs.tool
    keys(report, ('schema_version', 'status', 'phase', 'phases', 'platform', 'frozen',
                  'qt_platform', 'output', 'png', 'error'))
    for key, value in {'schema_version': 1, 'status': 'passed', 'phase': 'complete',
                       'platform': 'win32', 'frozen': True, 'qt_platform': 'windows',
                       'output': 'frozen-window.png', 'error': None}.items():
        require(tool.same_json(report[key], value), 'screenshot_receipt')
    phases = ('entry', 'gui-import', 'gui-imported', 'screenshot', 'png-validation', 'complete')
    require(type(report['phases']) is list and len(report['phases']) == len(phases), 'screenshot_phases')
    previous = None
    for row, name in zip(report['phases'], phases):
        keys(row, ('name', 'utc'))
        require(row['name'] == name and type(row['utc']) is str and len(row['utc']) <= 64, 'screenshot_phases')
        stamp = tool.datetime.fromisoformat(row['utc'])
        require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0
                and (previous is None or stamp >= previous), 'screenshot_time')
        previous = stamp
    info = report['png']
    keys(info, ('bytes', 'sha256', 'width', 'height'))
    integer(info['bytes'], 9, 32 * MIB); hexadecimal(info['sha256'])
    integer(info['width'], 1, 8192); integer(info['height'], 1, 8192)
    require(info['width'] * info['height'] <= 16000000, 'screenshot_pixels')
    data = inputs.read(root / 'frozen-window.png', 32 * MIB)
    require(data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) == info['bytes']
            and sha(data) == info['sha256'], 'screenshot_hash')
    # Pixel decoding and GUI execution are producer-validator assertions.


def recording(inputs, root, report, exe):
    tool = inputs.tool
    tool.recording_structure(report)
    require(report['executable'] == {'bytes': len(exe), 'sha256': sha(exe)}, 'recording_exe')
    work = tool.child(root, 'frozen-recording.json.work')
    expected = {item['name'] for item in report['artifacts'].values()}
    tool.recording_work_files(work, expected)
    for item in report['artifacts'].values():
        size, hashed = inputs.read(tool.child(work, item['name']), 2 * MIB, keep=False)
        require((size, hashed) == (item['bytes'], item['sha256']), 'recording_artifact')
    return expected


def native_inventory(inputs, root):
    tool = inputs.tool
    files = tool.candidate_files(root)
    native = {}
    for name in files:
        require(Path(name).suffix.lower() != '.exe' or name == 'Motortester.exe', 'extra_executable')
        if Path(name).suffix.lower() in ('.dll', '.pyd'):
            require(name.startswith('_internal/') and len(native) < MAX_NATIVE_FILES, 'native_path_or_count')
            size, hashed = inputs.read(tool.child(root, name), 64 * MIB, keep=False)
            native[name] = {'bytes': size, 'sha256': hashed}
    require(native, 'native_empty')
    return native, files


def source_input_table(inputs, path, modules, target_hash, target_size, expected_hash):
    raw = inputs.read(path, 2 * MIB)
    require(sha(raw) == expected_hash, 'source_inputs_hash')
    table = inputs.tool.manifest_json(raw)
    require(type(table) is dict and set(table) == set(modules), 'source_inputs_set')
    for name, info in table.items():
        if modules[name][0] == 'namespace':
            require(info is None, 'namespace_source')
        else:
            keys(info, ('bytes', 'sha256'))
            integer(info['bytes'], 0, 2 * MIB)
            hexadecimal(info['sha256'])
    require(table['PySide6'] == {'bytes': target_size, 'sha256': target_hash}, 'target_source_input')
    return table


def marker_receipts(inputs, bundle, report, variant):
    tool = inputs.tool
    rows = [r for r in report['runs'] if r['variant'] == variant and r['kind'] != 'serial']
    expected = set() if variant == 'control' else {
        'marker-%s-%d.json' % (report['trial_id'], r['pid']) for r in rows}
    root = tool.child(bundle, 'replacement-probe')
    exact_directory(tool, root, expected)
    if variant == 'modified':
        for row in rows:
            path = root / ('marker-%s-%d.json' % (report['trial_id'], row['pid']))
            raw = inputs.read(path, 1024)
            expected_value = {'schema_version': 1, 'module': 'PySide6', 'trial_id': report['trial_id'],
                              'pid': row['pid'], 'frozen': True,
                              'loader': 'pyimod02_importers.PyiFrozenLoader'}
            require(sha(raw) == row['marker_sha256']
                    and tool.same_json(tool.manifest_json(raw), expected_value), 'marker_mismatch')


def verify(tool, root, source_zip, output):
    root = tool.no_redirect(safe_path(root))
    inputs = Inputs(tool)
    fixed_layout(tool, root, ('execution.json',), ('preparation',) + VARIANTS)
    source_identity(inputs, source_zip)
    prep = read_preparation(inputs, tool.child(root, 'preparation'))
    execution = inputs.json(tool.child(root, 'execution.json'))
    execution_structure(tool, execution, prep)
    inventories, packages, binaries, directory_sets, bootstraps, sources = {}, {}, {}, {}, {}, {}
    recording_sets = {}
    for variant in VARIANTS:
        base = tool.child(root, variant)
        fixed_layout(tool, base, ('PYZ-00.pyz', 'build.log', 'source-inputs.json'), ('bundle', 'receipts'))
        bundle = tool.child(base, 'bundle')
        exe = inputs.read(tool.child(bundle, 'Motortester.exe'), 64 * MIB)
        # Pinned CPython3.13 magic; unknown variants must fail closed.
        work_pyz = inputs.read(tool.child(base, 'PYZ-00.pyz'), 64 * MIB)
        require(len(work_pyz) >= 17 and work_pyz[4:8] == b'\xf3\r\r\n', 'python_magic')
        archive, options, pyz, modules = tool.archive_inventory(exe, b'\xf3\r\r\n')
        work_modules = tool.pyz_inventory(work_pyz, b'\xf3\r\r\n')
        target = [r for r in modules if r['name'].casefold() == 'pyside6']
        require(len(target) == 1 and target[0]['name'] == 'PySide6'
                and target[0]['kind'] == 'package', 'target_package')
        packages[variant] = {r['name']: (r['kind'], r['stored_sha256']) for r in modules}
        target_source = prep['files']['original-init.py' if variant == 'control' else 'modified-init.py']
        sources[variant] = source_input_table(inputs, tool.child(base, 'source-inputs.json'), packages[variant],
                                              target_source['sha256'], target_source['bytes'],
                                              execution['builds'][variant]['source_inputs_sha256'])
        bootstraps[variant] = (options, {r['name']: (r['type'], r['compressed'], r['stored_sha256'])
                                       for r in archive if r['name'] != 'PYZ.pyz'})
        package_size = tool.COOKIE.unpack(exe[-tool.COOKIE.size:])[1]
        binaries[variant] = {'bytes': len(exe), 'sha256': sha(exe), 'pyz_sha256': sha(pyz),
                             'target_stored_sha256': target[0]['stored_sha256'],
                             'exe_prefix_sha256': sha(exe[:-package_size]),
                             'work_pyz_sha256': sha(work_pyz),
                             'work_pyz_matches_embedded': work_pyz == pyz,
                             'work_pyz_module_count': len(work_modules),
                             'authoritative_module_table': 'actual embedded PYZ.pyz'}
        inventories[variant], directory_sets[variant] = native_inventory(inputs, bundle)
        log_size, log_hash = inputs.read(tool.child(base, 'build.log'), 2 * MIB, keep=False)
        require(log_size > 0 and log_hash == execution['builds'][variant]['log_sha256'], 'build_log')
        receipts = tool.child(base, 'receipts')
        fixed_layout(tool, receipts, tuple(RECEIPTS.values()) + ('frozen-window.png',),
                     ('frozen-recording.json.work',))
        for row in (r for r in execution['runs'] if r['variant'] == variant):
            require(row['executable_sha256_before'] == row['executable_sha256_after'] == sha(exe), 'run_executable')
            raw = inputs.read(tool.child(receipts, RECEIPTS[row['kind']]))
            require(sha(raw) == row['receipt_sha256'], 'receipt_hash')
            parsed = tool.manifest_json(raw)
            if row['kind'] == 'serial':
                require(tool.same_json(parsed, SERIAL), 'serial_receipt')
            elif row['kind'] == 'screenshot':
                screenshot(inputs, receipts, parsed)
            else:
                recording_sets[variant] = recording(inputs, receipts, parsed, exe)
        marker_receipts(inputs, bundle, execution, variant)
    require(inventories['control'] == inventories['modified'], 'native_changed')
    require(bootstraps['control'] == bootstraps['modified'], 'bootstrap_changed')
    require(set(packages['control']) == set(packages['modified']), 'module_set_changed')
    require(packages['control']['PySide6'][1] != packages['modified']['PySide6'][1], 'unchanged_target')
    require(all(packages['control'][n] == packages['modified'][n]
                for n in packages['control'] if n != 'PySide6'), 'unexplained_module_change')
    require(all(sources['control'][n] == sources['modified'][n]
                for n in sources['control'] if n != 'PySide6'), 'unexplained_source_change')
    require(binaries['control']['sha256'] != binaries['modified']['sha256'], 'unchanged_exe')
    inputs.recheck()
    for variant in VARIANTS:
        bundle = tool.child(root, variant + '/bundle')
        require(tool.candidate_files(bundle) == directory_sets[variant], 'bundle_set_changed')
        marker_receipts(inputs, bundle, execution, variant)
        fixed_layout(tool, tool.child(root, variant), ('PYZ-00.pyz', 'build.log', 'source-inputs.json'), ('bundle', 'receipts'))
        fixed_layout(tool, tool.child(root, variant + '/receipts'),
                     tuple(RECEIPTS.values()) + ('frozen-window.png',), ('frozen-recording.json.work',))
        tool.recording_work_files(tool.child(root, variant + '/receipts/frozen-recording.json.work'),
                                  recording_sets[variant])
    fixed_layout(tool, root, ('execution.json',), ('preparation',) + VARIANTS)
    exact_directory(tool, tool.child(root, 'preparation'), PREP_FILES)
    result = {'schema_version': 1, 'scope': 'pyz-recombination-receipt-correspondence-only',
              'application_commit': APP, 'application_tree': TREE, 'trial_id': prep['trial_id'],
              'source_zip_sha256': SOURCE_SHA, 'inspector_sha256': INSPECTOR_SHA,
              'probe_sha256': prep['probe_sha256'],
              'target': 'PySide6', 'target_source': MEMBER, 'binaries': binaries,
              'initializer_sources': {'control': prep['files']['original-init.py'],
                                      'modified': prep['files']['modified-init.py']},
              'native_file_count': len(inventories['control']),
              'unchanged_native_inventory_sha256': sha(encoded(inventories['control'])),
              'module_count': len(packages['control']), 'only_target_stored_module_changed': True,
              'marker_and_smoke_receipt_correspondence': True,
              'execution_claim_source': 'external A receipt; verifier did not launch children',
              'runtime_source_input_table_binding': 'external A source-file hash observations, compared without opening those declared origins',
              'source_to_bytecode_attestation': False, 'actual_windows_execution_verified': False,
              'exe_prefix_build_correspondence_verified': False,
              'pyz_recombination_observed': False, 'native_replacement_verified': False,
              'native_embedded_support_verified': False, 'corresponding_source_verified': False,
              'legal_clearance': False, 'distribution_clearance': False,
              'qualification': 'Independent actual Windows execution review is required; modeled fixtures can satisfy this data schema. PNG decoding and recording-member/sample checks are producer assertions.'}
    out = safe_path(output)
    require(out != root and root not in out.parents and out not in root.parents, 'output_overlap')
    inputs.reads.unchanged()
    write_exclusive(out, encoded(result))
    return result


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise Rejected('arguments')
    parser = Parser(prog='pyz_replacement_probe', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    for flag in ('inspector', 'wheel', 'source-zip', 'trial-id', 'output'):
        prep.add_argument('--' + flag, required=True)
    ver = sub.add_parser('verify')
    for flag in ('inspector', 'trial-root', 'source-zip', 'output'):
        ver.add_argument('--' + flag, required=True)
    try:
        require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode, 'isolation_required')
        args = parser.parse_args(argv)
        tool = load_tool(args.inspector)
        if args.command == 'prepare':
            prepare(tool, Path(args.wheel), Path(args.source_zip), args.trial_id, Path(args.output))
        else:
            verify(tool, Path(args.trial_root), Path(args.source_zip), Path(args.output))
    except Exception as error:
        code = str(error) if isinstance(error, Rejected) else 'input_or_io_error'
        if re.fullmatch('[a-z_]{1,64}', code) is None:
            code = 'invalid_input'
        print('REPLACEMENT_PROBE_FAILED code=' + code, file=sys.stderr)
        return 1
    print('Data-only result written; actual execution and release gates remain separate.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Read-only, version-pinned inventory companion; never release/legal approval."""
import argparse
import ast
import csv
import email.parser
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
import sysconfig
import tokenize

MIB = 1024 * 1024
MAX_FILES = 4096
MAX_FILE = 256 * MIB
MAX_PAYLOAD = 1024 * MIB
MAX_READ = 2048 * MIB
MAX_ARCHIVE = 64 * MIB
MAX_META = 2 * MIB
MAX_ENTRIES = 16384
MAX_NODES = 100000
MAX_DEPTH = 8
MAX_PATH_PARTS = 16  # Actual run14 notice paths have nine directory components.
MAX_NAME = 1024
MAX_REPORT = 8 * MIB
PINNED = {
    'altgraph': '0.17.5', 'colorama': '0.4.6', 'numpy': '2.5.3',
    'packaging': '26.3', 'pefile': '2024.8.26', 'pip': '26.2.1',
    'pyinstaller': '6.22.3', 'pyinstaller-hooks-contrib': '2026.8',
    'pyqtgraph': '0.14.0', 'pyserial': '3.5', 'pyside6-essentials': '6.12.0',
    'pywin32-ctypes': '0.2.3', 'setuptools': '84.0.0', 'shiboken6': '6.12.0',
}
READER_HASHES = {
    'PyInstaller/archive/readers.py': 'e5cf8a33bc68ed2c94d52da68780094b5c359f4c38cf35af004edc90016a3613',
    'PyInstaller/archive/writers.py': 'cc82056869995c1012b08c8f0bf7eecef6b5ec8bc6e28e9e8b548e40b0d92e61',
    'PyInstaller/loader/pyimod01_archive.py': '38d2c8b801b6c2165d44bc40f47bc1e230110272003da67d4d4eaa59af4ff397',
}
COOKIE_MAGIC = b'MEI\014\013\012\013\016'
COOKIE = struct.Struct('!8sIIII64s')
ENTRY = struct.Struct('!IIIIBc')
NATIVE = ('.dll', '.pyd', '.exe')


class Invalid(ValueError):
    """Fixed public code only: do not embed input paths/content in errors."""


def require(condition, code):
    if not condition:
        raise Invalid(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


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


def module_name(value):
    name(value)
    require(all(p.isidentifier() for p in value.split('.')), 'invalid_module_name')
    return value


def norm_dist(value):
    name(value)
    return re.sub('[-_.]+', '-', value).lower()


def absolute_path(path):
    raw = os.fspath(path)
    name(raw)
    require(not raw.startswith(('//', '\\\\')) and '..' not in Path(raw).parts, 'unsafe_root')
    drive, tail = ntpath.splitdrive(raw)
    require((not drive and ':' not in raw) or
            (os.name == 'nt' and re.fullmatch('[A-Za-z]:', drive) is not None
             and tail.startswith(('\\', '/')) and ':' not in tail), 'unsafe_root')
    absolute = Path(os.path.abspath(path))
    require(len(absolute.parts) <= 64, 'ancestor_limit')
    for component in absolute.parts[1:]:
        relative(component)
    return absolute


def no_redirect(path):
    """Check each existing path component without following links/reparse points."""
    path = absolute_path(path)
    for part in reversed((path,) + tuple(path.parents)):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not (getattr(info, 'st_file_attributes', 0) & 0x400), 'redirected_path')
    return path


def child(root, rel):
    return no_redirect(root / relative(rel))


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Reads:
    def __init__(self):
        self.total = 0
        self.seen = {}

    def read(self, path, cap=MAX_META, keep=True):
        path = no_redirect(path)
        before = path.stat()
        require(stat.S_ISREG(before.st_mode) and before.st_size <= cap, 'file_limit_or_type')
        self.total += before.st_size
        require(self.total <= MAX_READ, 'read_budget')
        h = hashlib.sha256()
        chunks = []
        size = 0
        with path.open('rb') as stream:
            require(fingerprint(os.fstat(stream.fileno())) == fingerprint(before), 'input_changed')
            while True:
                data = stream.read(min(MIB, cap - size + 1))
                if not data:
                    break
                size += len(data)
                require(size <= cap and size <= before.st_size, 'input_changed')
                h.update(data)
                if keep:
                    chunks.append(data)
            require(fingerprint(os.fstat(stream.fileno())) == fingerprint(before), 'input_changed')
        require(size == before.st_size and fingerprint(path.stat()) == fingerprint(before), 'input_changed')
        identity = (fingerprint(before), h.hexdigest())
        require(path not in self.seen or self.seen[path] == identity, 'input_changed')
        self.seen[path] = identity
        return b''.join(chunks) if keep else (size, h.hexdigest())

    def unchanged(self):
        for path, (old, _) in self.seen.items():
            require(fingerprint(no_redirect(path).stat()) == old, 'input_changed')


def json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def manifest_json(data):
    """Bound JSON structure before the generic decoder allocates its tree."""
    require(len(data) <= MAX_META, 'metadata_limit')
    text = data.decode('utf-8')
    quoted, escaped, width, depth, nodes, scalar = False, False, 0, 0, 0, 0
    for char in text:
        if quoted:
            width += 1
            require(width <= MAX_NAME * 12 + 1, 'invalid_name')
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '\"':
                quoted = False
        elif char == '\"':
            quoted, width, scalar = True, 0, 0
            nodes += 1
        elif char in '{}[],:':
            scalar = 0
            nodes += 1
            if char in '{[':
                depth += 1
            elif char in '}]':
                depth -= 1
            require(0 <= depth <= MAX_DEPTH, 'metadata_complexity')
        elif char.isspace():
            scalar = 0
        else:
            scalar += 1
            require(scalar <= 64, 'json_scalar_limit')
        require(nodes <= MAX_NODES, 'metadata_complexity')
    require(not quoted and depth == 0, 'truncated_json')
    def invalid_constant(value):
        raise Invalid('json_constant')
    return json.loads(text, object_pairs_hook=json_object, parse_constant=invalid_constant)


def literals(data):
    """Only the small literal grammar used by COLLECT/PYZ guts; never eval."""
    require(len(data) <= MAX_META, 'metadata_limit')
    text = data.decode('utf-8')
    # pprint's repr strings are single-line quoted literals. Reject multiline
    # strings before tokenize can accumulate an adversarial continued string.
    quote, escaped, width = None, False, 0
    for index, character in enumerate(text):
        if quote is None:
            if character in ('\"', "'"):
                require(not text.startswith(character * 3, index), 'nonliteral_toc')
                quote, width = character, 0
        else:
            width += 1
            require(width <= MAX_NAME * 12 + 1 and character not in ('\n', '\r'), 'invalid_name')
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == quote:
                quote = None
    require(quote is None, 'nonliteral_toc')
    # Bound the token count and bracket depth *before* allocating an AST.
    depth, tokens = 0, 0
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        tokens += 1
        require(tokens <= MAX_NODES, 'metadata_complexity')
        if token.type == tokenize.OP:
            require(token.string in ('(', ')', '[', ']', ','), 'nonliteral_toc')
            if token.string in ('(', '['):
                depth += 1
                require(depth <= MAX_DEPTH + 1, 'metadata_complexity')
            elif token.string in (')', ']'):
                depth -= 1
                require(depth >= 0, 'nonliteral_toc')
        elif token.type == tokenize.STRING:
            require(token.string.startswith(('\"', "'")) and len(token.string) <= MAX_NAME * 12 + 2,
                    'invalid_name')
        elif token.type == tokenize.NAME:
            require(token.string == 'None', 'nonliteral_toc')
        else:
            require(token.type in (tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER), 'nonliteral_toc')
    require(depth == 0, 'nonliteral_toc')
    tree = ast.parse(text, mode='eval')
    count = 0

    def visit(node, depth):
        nonlocal count
        count += 1
        require(count <= MAX_NODES and depth <= MAX_DEPTH, 'metadata_complexity')
        if isinstance(node, (ast.Tuple, ast.List)):
            require(len(node.elts) <= MAX_ENTRIES, 'entry_limit')
            values = [visit(e, depth + 1) for e in node.elts]
            return tuple(values) if isinstance(node, ast.Tuple) else values
        require(isinstance(node, ast.Constant) and (type(node.value) is str or node.value is None),
                'nonliteral_toc')
        if node.value is not None:
            name(node.value)
        return node.value

    return visit(tree.body, 0)


class TocData:
    """Restricted marshal data reader for the pinned writer's TOC, not code."""
    def __init__(self, data):
        require(len(data) <= MAX_META, 'metadata_limit')
        self.data, self.pos, self.nodes, self.refs = data, 0, 0, []

    def take(self, size):
        require(0 <= size <= len(self.data) - self.pos, 'truncated_toc')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def integer(self):
        return struct.unpack('<i', self.take(4))[0]

    def object(self, depth=0):
        self.nodes += 1
        require(self.nodes <= MAX_NODES and depth <= MAX_DEPTH, 'metadata_complexity')
        tag = self.take(1)[0]
        flagged, code = bool(tag & 128), chr(tag & 127)
        if code == 'r':
            require(not flagged, 'invalid_reference')
            index = self.integer()
            require(0 <= index < len(self.refs) and self.refs[index] is not None, 'invalid_reference')
            value, height, cost = self.refs[index]
            self.nodes += cost - 1
            require(self.nodes <= MAX_NODES and depth + height <= MAX_DEPTH, 'metadata_complexity')
            return value, height, cost
        slot = len(self.refs) if flagged else None
        if flagged:
            self.refs.append(None)  # Incomplete objects cannot be referenced.
        if code == 'i':
            value = self.integer()
            height, cost = 0, 1
        elif code in ('u', 'a', 'A', 't', 'z', 'Z'):
            size = self.take(1)[0] if code in ('z', 'Z') else self.integer()
            require(0 < size <= MAX_NAME, 'invalid_name')
            value = self.take(size).decode('utf-8' if code == 'u' else 'ascii')
            name(value)
            height, cost = 0, 1
        elif code in ('[', '(', ')'):
            size = self.take(1)[0] if code == ')' else self.integer()
            require(0 <= size <= MAX_ENTRIES, 'entry_limit')
            children = [self.object(depth + 1) for _ in range(size)]
            values = [item[0] for item in children]
            value = values if code == '[' else tuple(values)
            height = 1 + max((item[1] for item in children), default=-1)
            cost = 1 + sum(item[2] for item in children)
        else:
            raise Invalid('unsupported_marshal_type')
        if slot is not None:
            self.refs[slot] = (value, height, cost)
        return value, height, cost

    def parse(self):
        value, _, _ = self.object()
        require(self.pos == len(self.data), 'trailing_toc_bytes')
        return value


def intervals(rows):
    end = 0
    for start, length in sorted(rows):
        if length:
            require(start >= end, 'overlapping_entries')
            end = start + length


def pyz_inventory(data, python_magic):
    require(17 <= len(data) <= MAX_ARCHIVE and data[:4] == b'PYZ\0', 'pyz_header')
    require(data[4:8] == python_magic and data[12:17] == b'\0' * 5, 'pyz_version')
    offset = struct.unpack('!i', data[8:12])[0]
    require(17 <= offset < len(data), 'pyz_toc_bounds')
    table = TocData(data[offset:]).parse()
    require(type(table) is list and 0 < len(table) <= MAX_ENTRIES, 'pyz_table_shape')
    result, names, spans = [], set(), []
    for row in table:
        require(type(row) is tuple and len(row) == 2, 'pyz_row_shape')
        label, fields = row
        module_name(label)
        require(label not in names, 'duplicate_pyz_module')
        names.add(label)
        require(type(fields) is tuple and len(fields) == 3 and all(type(x) is int for x in fields),
                'pyz_row_shape')
        kind, start, length = fields
        require(kind in (0, 1, 3), 'unsupported_pyz_kind')
        require(17 <= start <= offset and 0 <= length <= offset - start, 'pyz_entry_bounds')
        require((kind == 3 and length == 0) or (kind != 3 and length > 0), 'pyz_entry_length')
        spans.append((start, length))
        result.append(dict(name=label, kind={0: 'module', 1: 'package', 3: 'namespace'}[kind],
                           offset=start, stored_bytes=length))
    intervals(spans)
    # Hash only after all ranges are valid and nonoverlapping; otherwise repeated
    # adversarial spans could amplify work far beyond the bounded input size.
    for row in result:
        start, length = row['offset'], row['stored_bytes']
        row['stored_sha256'] = digest(data[start:start + length]) if length else None
    return result


def archive_inventory(exe, python_magic):
    require(len(exe) <= MAX_ARCHIVE and exe[:2] == b'MZ', 'exe_format_or_limit')
    # Ignore incidental magic bytes, but never choose between structurally valid
    # cookies. This pinned unsigned format must end at the selected cookie.
    candidates, search, hits = [], 0, 0
    while True:
        position = exe.find(COOKIE_MAGIC, search)
        if position < 0:
            break
        search, hits = position + 1, hits + 1
        require(hits <= MAX_ENTRIES, 'archive_cookie_limit')
        if position + COOKIE.size <= len(exe):
            _, size, offset, count, version, library = COOKIE.unpack(exe[position:position + COOKIE.size])
            if (version == 313 and library == b'python313.dll'.ljust(64, b'\0')
                    and 0 < count <= MAX_META and offset + count + COOKIE.size == size
                    and position + COOKIE.size - size >= 2):
                candidates.append(position)
    require(len(candidates) == 1, 'ambiguous_archive_cookie')
    cookie_at = candidates[0]
    require(cookie_at + COOKIE.size == len(exe), 'unsupported_archive_trailer')
    magic, length, toc_at, toc_len, pyvers, pylib = COOKIE.unpack(exe[cookie_at:])
    start = len(exe) - length
    require(start >= 2 and pyvers == 313 and pylib == b'python313.dll'.ljust(64, b'\0'), 'archive_version')
    require(0 < toc_len <= MAX_META and toc_at + toc_len + COOKIE.size == length, 'archive_toc_bounds')
    require(0 <= toc_at and start + toc_at <= cookie_at, 'archive_toc_bounds')
    toc = exe[start + toc_at:cookie_at]
    pos, rows, options, names, spans = 0, [], [], set(), []
    while pos < len(toc):
        require(len(rows) + len(options) < MAX_ENTRIES and len(toc) - pos >= ENTRY.size, 'archive_entry_limit')
        size, off, clen, ulen, compressed, typ = ENTRY.unpack(toc[pos:pos + ENTRY.size])
        require(size >= ENTRY.size + 1 and size % 16 == 0 and size <= len(toc) - pos, 'archive_row_size')
        raw_name = toc[pos + ENTRY.size:pos + size]
        require(b'\0' in raw_name, 'archive_name_termination')
        label, padding = raw_name.split(b'\0', 1)
        require(not padding.strip(b'\0') and len(padding) <= 15, 'archive_padding')
        label = label.decode('utf-8').replace('\\', '/')
        relative(label)
        typ = typ.decode('ascii')
        require(typ in ('z', 's', 'm', 'M', 'o') and compressed in (0, 1), 'unsupported_archive_kind')
        require(off <= toc_at and clen <= toc_at - off and ulen <= MAX_ARCHIVE, 'archive_entry_bounds')
        require(compressed or clen == ulen, 'archive_entry_length')
        if typ == 'o':
            require(clen == ulen == 0 and not compressed, 'archive_option_payload')
            require(label == 'pyi-contents-directory _internal', 'unsupported_archive_option')
            options.append(label)
        else:
            key = label.casefold()
            require(key not in names and clen > 0, 'duplicate_archive_name')
            names.add(key)
            spans.append((off, clen))
            rows.append(dict(name=label, type=typ, offset=off, stored_bytes=clen,
                             declared_uncompressed_bytes=ulen, compressed=bool(compressed)))
        pos += size
    intervals(spans)
    pyzs = [r for r in rows if r['type'] == 'z']
    require(len(pyzs) == 1 and pyzs[0]['name'] == 'PYZ.pyz' and not pyzs[0]['compressed'],
            'unsupported_pyz_count_or_compression')
    for row in rows:
        off, size = row['offset'], row['stored_bytes']
        row['stored_sha256'] = digest(exe[start + off:start + off + size])
    pyz = pyzs[0]
    data = exe[start + pyz['offset']:start + pyz['offset'] + pyz['stored_bytes']]
    return rows, options, data, pyz_inventory(data, python_magic)


def triples(table):
    require(type(table) is list and len(table) <= MAX_ENTRIES, 'build_table_shape')
    for row in table:
        require(type(row) is tuple and len(row) == 3 and type(row[0]) is str
                and (row[1] is None or type(row[1]) is str) and type(row[2]) is str, 'build_row_shape')
    return table


def collect_map(data):
    guts = literals(data)
    require(type(guts) is tuple and len(guts) == 1, 'collect_shape')
    result, normalized, final_names = {}, set(), set()
    for dest, source, typ in triples(guts[0]):
        dest = relative(dest.replace('\\', '/'))
        key = dest.casefold()
        require(key not in normalized, 'duplicate_collect_destination')
        normalized.add(key)
        require(typ in ('BINARY', 'EXTENSION', 'EXECUTABLE', 'DATA'), 'unsupported_collect_type')
        require(source is not None, 'missing_collect_source')
        final = relative(dest if typ == 'EXECUTABLE' else '_internal/' + dest)
        require(final.casefold() not in final_names, 'duplicate_collect_destination')
        final_names.add(final.casefold())
        result[final] = (source, typ)
    return result


def pure_map(data, work_pyz):
    guts = literals(data)
    require(type(guts) is tuple and len(guts) == 2 and type(guts[0]) is str, 'pure_shape')
    require(os.path.normcase(os.path.abspath(guts[0])) == os.path.normcase(str(work_pyz)), 'wrong_work_pyz_path')
    result = {}
    for label, source, typ in triples(guts[1]):
        module_name(label)
        require(label not in result and typ == 'PYMODULE', 'duplicate_or_unsupported_pure')
        result[label] = source
    return result


def candidate_files(root):
    result, stack = {}, [root]
    visited = 0
    while stack:
        parent = stack.pop()
        with os.scandir(no_redirect(parent)) as entries:
            for entry in entries:
                visited += 1
                require(visited <= MAX_FILES * 2, 'candidate_tree_limit')
                path = no_redirect(Path(entry.path))
                rel = relative(path.relative_to(root).as_posix())
                info = path.stat()
                if stat.S_ISDIR(info.st_mode):
                    require(len(path.relative_to(root).parts) <= MAX_PATH_PARTS, 'candidate_tree_depth')
                    stack.append(path)
                else:
                    require(stat.S_ISREG(info.st_mode) and len(result) < MAX_FILES, 'candidate_tree_limit')
                    key = rel.casefold()
                    require(key not in result, 'candidate_case_collision')
                    result[key] = rel
    return set(result.values())


def package_versions(rows):
    require(type(rows) is list and len(rows) == len(PINNED), 'package_set')
    result = {}
    for row in rows:
        require(type(row) is dict and set(row) == {'name', 'version'}, 'package_shape')
        key = norm_dist(row['name'])
        require(key not in result and type(row['version']) is str, 'duplicate_distribution')
        result[key] = row['version']
    require(result == PINNED, 'dependency_version')


def distributions(site, reads):
    owners, versions, nodes, count = {}, {}, 0, 0
    with os.scandir(site) as entries:
        for entry in entries:
            nodes += 1
            require(nodes <= MAX_FILES, 'environment_entry_limit')
            if not entry.name.lower().endswith('.dist-info'):
                continue
            folder = child(site, entry.name)
            meta = email.parser.BytesParser().parsebytes(reads.read(child(folder, 'METADATA')), headersonly=True)
            require(len(meta.get_all('Name', [])) == len(meta.get_all('Version', [])) == 1, 'distribution_metadata')
            key, version = norm_dist(meta['Name']), meta['Version']
            require(key not in versions, 'duplicate_distribution')
            versions[key] = version
            record = reads.read(child(folder, 'RECORD')).decode('utf-8')
            for row in csv.reader(io.StringIO(record)):
                count += 1
                require(count <= MAX_ENTRIES and len(row) == 3, 'record_shape')
                # RECORD allows absolute/out-of-site paths. They are metadata,
                # never authority to read outside the fixed site-packages root.
                member = row[0].replace('\\', '/')
                name(member)
                if '..' in member.split('/'):
                    continue
                if ntpath.isabs(member):
                    if not Path(member).is_absolute():
                        continue  # Foreign-drive metadata in a fixture, never read.
                    inside = within(Path(member), site)
                    if inside is None:
                        continue
                    member = inside.as_posix()
                relative(member)
                lowered = member.casefold()
                require(lowered not in owners, 'ambiguous_record_owner')
                owners[lowered] = dict(distribution=key, version=version)
    require(versions == PINNED, 'dependency_version')
    for member, expected in READER_HASHES.items():
        require(digest(reads.read(child(site, member))) == expected, 'pyinstaller_source_hash')
    return owners


def within(path, root):
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def origin(source, *, site, interpreter, work, reads, owners, native=False, expected=None):
    if source in (None, '-'):
        return dict(status='namespace_or_no_file')
    # Do not resolve/stat a supplied path before establishing a lexical allowlist.
    require(type(source) is str and Path(source).is_absolute(), 'origin_not_absolute')
    require('..' not in Path(source).parts, 'unsafe_origin_path')
    path = absolute_path(source)
    require(within(path, work.parent.parent / 'dist/Motortester') is None, 'self_referential_origin')
    site_rel, python_rel, work_rel = within(path, site), within(path, interpreter), within(path, work)
    if site_rel is not None:
        rel = relative(site_rel.as_posix())
        owner = owners.get(rel.casefold())
        require(owner is not None, 'unowned_environment_file')
        kind, root = 'environment', site
    elif python_rel is not None:
        rel = relative(python_rel.as_posix())
        # Native runtime files only in interpreter root or DLLs; pure modules only Lib.
        allowed = ((len(python_rel.parts) == 1 or (len(python_rel.parts) == 2 and python_rel.parts[0] == 'DLLs'))
                   if native else python_rel.parts[0] == 'Lib')
        if not allowed:
            return dict(status='outside_allowed_origins')
        owner, kind, root = None, 'interpreter', interpreter
    elif native and work_rel is not None and work_rel.as_posix() == 'Motortester.exe':
        rel, owner, kind, root = 'Motortester.exe', None, 'constructed', work
    else:
        return dict(status='outside_allowed_origins')
    require(path.suffix.lower() in (NATIVE if native else ('.py', '.pyc')), 'origin_suffix')
    size, hashed = reads.read(child(root, rel), MAX_FILE, keep=False)
    status = 'hash_correspondence' if expected is not None and hashed == expected else 'declared_input'
    if expected is not None:
        require(hashed == expected, 'origin_hash_mismatch')
    result = dict(status=status, origin_kind=kind, relative_path=rel, bytes=size, sha256=hashed)
    if owner:
        result.update(owner)
    return result


def runtime_guard(build):
    require(sys.platform == 'win32' and sys.implementation.name == 'cpython'
            and sys.version_info[:3] == (3, 13, 16) and sys.maxsize > 2**32,
            'runtime_version')
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode, 'unsafe_startup_flags')
    require(getattr(sys, '_is_gil_enabled', lambda: False)(), 'runtime_gil')
    require(not sysconfig.get_config_var('Py_GIL_DISABLED'), 'runtime_gil')
    require('64 BIT (AMD64)' in sys.version.upper(), 'runtime_architecture')
    require(no_redirect(sys.executable) == child(build, 'venv/Scripts/python.exe'), 'wrong_environment_python')


def inspect_candidate(build, source_sha, manifest_sha, inspector_commit, inspector_sha):
    hex_id(source_sha, 40); hex_id(manifest_sha); hex_id(inspector_commit, 40); hex_id(inspector_sha)
    build = no_redirect(build)
    runtime_guard(build)
    interpreter = no_redirect(Path(sys.base_prefix))
    bundle, work = child(build, 'dist/Motortester'), child(build, 'pyinstaller/motortester')
    site = child(build, 'venv/Lib/site-packages')
    reads = Reads()
    require(digest(reads.read(Path(__file__))) == inspector_sha, 'inspector_hash')
    manifest_path = child(bundle, 'build-manifest.json')
    raw_manifest = reads.read(manifest_path)
    require(digest(raw_manifest) == manifest_sha, 'manifest_hash')
    manifest = manifest_json(raw_manifest)
    require(type(manifest) is dict and manifest.get('source_commit') == source_sha, 'source_identity')
    require(set(manifest) == {'status', 'source_commit', 'build_time_utc', 'python', 'platform',
                             'machine', 'packages', 'windows_10_manual_acceptance', 'usb_verified',
                             'frozen_gui_smoke', 'serial_import_smoke', 'license_review', 'files'}, 'manifest_shape')
    require(type(manifest['build_time_utc']) is str and type(manifest['windows_10_manual_acceptance']) is str
            and manifest['usb_verified'] is False and type(manifest['frozen_gui_smoke']) is dict
            and type(manifest['serial_import_smoke']) is dict
            and manifest['license_review'] == 'required; see LICENSE-REVIEW.md', 'manifest_shape')
    require(manifest.get('status') == 'candidate-not-release-approved'
            and manifest.get('python') == '3.13.16' and manifest.get('machine') == 'AMD64'
            and type(manifest.get('platform')) is str and manifest['platform'].startswith('Windows-'), 'manifest_platform')
    package_versions(manifest.get('packages'))
    files = manifest.get('files')
    require(type(files) is dict and 0 < len(files) < MAX_FILES, 'manifest_files')
    keys = set()
    for path, hashed in files.items():
        relative(path); hex_id(hashed)
        require(path.casefold() not in keys and path != 'build-manifest.json', 'duplicate_manifest_path')
        keys.add(path.casefold())
    require('Motortester.exe' in files, 'missing_exe')
    require(candidate_files(bundle) == set(files) | {'build-manifest.json'}, 'candidate_file_set')
    total, sizes = 0, {}
    for path, expected in sorted(files.items()):
        size, hashed = reads.read(child(bundle, path), MAX_FILE, keep=False)
        require(hashed == expected, 'payload_hash')
        total += size
        require(total <= MAX_PAYLOAD, 'payload_budget')
        sizes[path] = size
    owners = distributions(site, reads)
    collect_path, pure_path = child(work, 'COLLECT-00.toc'), child(work, 'PYZ-00.toc')
    raw_collect, raw_pure = reads.read(collect_path), reads.read(pure_path)
    collected = collect_map(raw_collect)
    require('Motortester.exe' in collected and collected['Motortester.exe'][1] == 'EXECUTABLE',
            'constructed_exe_binding')
    require(absolute_path(collected['Motortester.exe'][0]) == child(work, 'Motortester.exe'),
            'constructed_exe_binding')
    exe_path, pyz_path = child(bundle, 'Motortester.exe'), child(work, 'PYZ-00.pyz')
    exe = reads.read(exe_path, MAX_ARCHIVE)
    # This stdlib constant is read without importing any package/candidate module.
    from importlib.util import MAGIC_NUMBER
    archive, options, pyz, modules = archive_inventory(exe, MAGIC_NUMBER)
    require(reads.read(pyz_path, MAX_ARCHIVE) == pyz, 'work_pyz_mismatch')
    pure = pure_map(raw_pure, pyz_path)
    embedded_names = {row['name'] for row in modules}
    require(embedded_names <= set(pure), 'pure_module_set')
    native = []
    origin_args = dict(site=site, interpreter=interpreter, work=work, reads=reads, owners=owners)
    for path, expected in sorted(files.items()):
        if not path.lower().endswith(NATIVE):
            continue
        info = dict(status='no_collect_entry')
        if path in collected:
            src, typ = collected[path]
            require(typ in ('BINARY', 'EXTENSION', 'EXECUTABLE'), 'native_collect_type')
            info = origin(src, native=True, expected=expected, **origin_args)
        native.append(dict(path=path, bytes=sizes[path], sha256=expected, origin=info))
    require(native, 'empty_native_inventory')
    for row in modules:
        src = pure[row['name']]
        require((row['kind'] == 'namespace') == (src in (None, '-')), 'namespace_source')
        row['source_association'] = origin(src, **origin_args)
    declared_only = [dict(name=label, status='declared_not_embedded',
                          source_association=origin(pure[label], **origin_args))
                     for label in sorted(set(pure) - embedded_names)]
    unknowns = sum(row['origin']['status'] not in ('hash_correspondence',) for row in native)
    report = dict(schema_version=1, scope='read-only-candidate-provenance-inventory',
                  inventory_status='complete_with_unknown_origins' if unknowns else 'complete',
                  source_commit=source_sha, source_binding='manifest-recorded; external source guard required',
                  inspector_commit=inspector_commit, inspector_sha256=inspector_sha,
                  inspector_commit_binding='externally asserted; actual script bytes verified',
                  build_manifest_sha256=manifest_sha, executable_sha256=digest(exe), executable_bytes=len(exe),
                  payload_count=len(files), payload_bytes=total, native_count=len(native),
                  native_unknown_count=unknowns, native_files=native,
                  pyinstaller_version='6.22.3', carchive_entries=archive, carchive_options=options,
                  pyz_sha256=digest(pyz), pyz_bytes=len(pyz), pyz_modules=modules,
                  declared_only_modules=declared_only,
                  collect_toc_sha256=digest(raw_collect), pure_toc_sha256=digest(raw_pure),
                  legal_clearance=False, corresponding_source_verified=False, replacement_verified=False,
                  limitations=['Stored module bytes are not decompressed, unmarshaled or executed.',
                               'Build-table source associations are not source-to-bytecode proof.',
                               'Origin hashes do not establish upstream build/source provenance or rights.',
                               'Inventory does not test recipient replacement, source access or delivery.'])
    # Detect replacement/mutation before output, including critical byte rechecks.
    for path, cap in ((manifest_path, MAX_META), (exe_path, MAX_ARCHIVE),
                      (collect_path, MAX_META), (pure_path, MAX_META), (pyz_path, MAX_ARCHIVE)):
        reads.read(path, cap, keep=False)
    reads.unchanged()
    require(candidate_files(bundle) == set(files) | {'build-manifest.json'}, 'candidate_file_set')
    return report


def write_report(output, report, build):
    output = absolute_path(output)
    parent = no_redirect(output.parent)
    build = no_redirect(build)
    interpreter = no_redirect(Path(sys.base_prefix))
    require(within(output, build) is None and within(output, interpreter) is None
            and output != Path(__file__).absolute(), 'output_overlaps_inputs')
    relative(output.name)
    encoded = io.BytesIO()
    encoder = json.JSONEncoder(ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
    for fragment in encoder.iterencode(report):
        data = fragment.encode('ascii')
        require(encoded.tell() + len(data) + 1 <= MAX_REPORT, 'report_limit')
        encoded.write(data)
    encoded.write(b'\n')
    data = encoded.getvalue()
    target = parent / output.name
    created = None
    try:
        with target.open('xb') as stream:
            created = os.fstat(stream.fileno())
            require(stream.write(data) == len(data), 'output_short_write')
    except BaseException:
        # Remove only our own incomplete output; never an existing/replaced file.
        if created is not None:
            try:
                current = target.lstat()
                if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                    target.unlink()
            except OSError:
                pass
        raise


def main(argv=None):
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            raise Invalid('invalid_arguments')

    parser = SafeParser(description=__doc__, prog='inspect_candidate_provenance')
    for flag in ('build-root', 'expected-source-sha', 'expected-manifest-sha256',
                 'inspector-commit', 'expected-inspector-sha256', 'output'):
        parser.add_argument('--' + flag, required=True)
    try:
        args = parser.parse_args(argv)
        report = inspect_candidate(Path(args.build_root), args.expected_source_sha,
                                   args.expected_manifest_sha256, args.inspector_commit,
                                   args.expected_inspector_sha256)
        write_report(Path(args.output), report, Path(args.build_root))
    except Invalid as error:
        # Only our fixed machine codes can be printed; no raw standard exception.
        code = str(error)
        if re.fullmatch('[a-z_]{1,64}', code) is None:
            code = 'invalid_input'
        print('PROVENANCE_INSPECTION_FAILED code=' + code, file=sys.stderr)
        return 1
    except Exception:
        # Library-specific parse/I/O errors must not escape as private tracebacks.
        print('PROVENANCE_INSPECTION_FAILED code=read_write_or_metadata_error', file=sys.stderr)
        return 1
    print('Provenance inventory written; legal/source/replacement review remains open.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

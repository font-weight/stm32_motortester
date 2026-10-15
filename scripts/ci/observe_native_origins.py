"""Bounded read-only CI supplement. File metadata never grants distribution rights.

This module does not load DLLs, evaluate build metadata, or search source roots.
Only main() performs filesystem observations; pure parsers accept inert bytes.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import struct
import sys

MIB = 1024 * 1024
# The fixed retainer, two script checks and two Git extracts fit below 5 MiB.
# Reserve them inside, not in addition to, the all-observer 128 MiB ceiling.
TOTAL_READ_LIMIT = 128 * MIB
AUX_READ_RESERVE = 5 * MIB
INPUT_READ_LIMIT = TOTAL_READ_LIMIT - AUX_READ_RESERVE
SOURCE = "244b0786a18fed19a9e708bb815150475ddd193f"
INSPECTOR = "54d63be3fdd8eba858bafafbf6cec33c4ed989b8"
INSPECTOR_HASH = "fa017e8ab7a348d45c74c86bb877968bac9aef77f963642cc7af396a5f2c8c53"
BASELINE_HASH = "fb864aa52ccf4db813478a41ba256b9f6bc64c711403884d9ef0b8603926a8df"
NOTICE_HASH = "62bec384df47b0328307db41455ff6ea2559e5546b394ac69148561b21703120"
PYTHON_ROOT = PureWindowsPath(r"C:\hostedtoolcache\windows\Python\3.13.16\x64")
OPENSSL_ROOT = PureWindowsPath(r"C:\Program Files\OpenSSL\bin")
OPENSSL_NAMES = ("libcrypto-3-x64.dll", "libssl-3-x64.dll")


class Invalid(ValueError):
    """Incomplete or inconsistent observation, never a legal verdict."""


def need(condition, message):
    if not condition:
        raise Invalid(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    out = {}
    for key, value in pairs:
        need(key not in out, "Duplicate JSON key")
        out[key] = value
    return out


def decode(raw):
    def invalid_constant(value):
        raise Invalid("Nonfinite JSON constant")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=invalid_constant)


def relative(value):
    need(type(value) is str and 0 < len(value) <= 4096, "Bad relative path type/length")
    need(not any(ord(c) < 32 for c in value) and "\\" not in value and ":" not in value,
         "Unsafe relative path")
    path = PurePosixPath(value)
    need(not path.is_absolute() and str(path) == value and
         all(x not in (".", "..", "") and not x.endswith((" ", ".")) for x in path.parts),
         "Noncanonical relative path")
    return value


def declared_windows_path(value):
    """Validate a declaration lexically. This function never stats or opens it."""
    need(type(value) is str and 0 < len(value) <= 4096, "Bad declared source")
    need(not any(ord(c) < 32 for c in value), "Control character in source")
    path = PureWindowsPath(value)
    need(path.is_absolute() and re.fullmatch(r"[A-Za-z]:", path.drive) is not None,
         "Device, UNC or relative source")
    need(all(x not in (".", "..", "") and ":" not in x and not x.endswith((" ", "."))
             for x in path.parts[1:]), "Unsafe source components")
    need(all(re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", x) is None
             for x in path.parts[1:]), "Reserved device component")
    # Normalization must not hide a traversal or an empty component.
    raw_parts = value.replace("/", "\\").split("\\")[1:]
    need(all(x not in ("", ".", "..") for x in raw_parts), "Aliased declared source")
    return path


def parse_collect(raw):
    """Only PyInstaller 6.22.3's one-tuple/list/triple/string grammar is accepted."""
    need(0 < len(raw) <= 2 * MIB, "COLLECT size")
    root = ast.parse(raw.decode("utf-8"), mode="eval")
    for number, _ in enumerate(ast.walk(root), 1):
        need(number <= 100000, "COLLECT AST node count")
    count = 0
    def convert(node, depth=0):
        nonlocal count
        count += 1
        need(count <= 100000 and depth <= 32, "COLLECT parser budget")
        if isinstance(node, ast.Constant):
            need(type(node.value) is str and len(node.value) <= 4096, "COLLECT scalar")
            return node.value
        need(isinstance(node, (ast.List, ast.Tuple)), "COLLECT executable/unsupported syntax")
        values = [convert(x, depth + 1) for x in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    value = convert(root.body)
    need(type(value) is tuple and len(value) == 1 and type(value[0]) is list,
         "COLLECT outer schema")
    need(len(value[0]) <= 10000, "COLLECT entry count")
    result = {}
    for entry in value[0]:
        need(type(entry) is tuple and len(entry) == 3 and all(type(x) is str for x in entry),
             "COLLECT triple schema")
        destination, source, kind = entry
        destination = relative(destination.replace("\\", "/"))
        folded = destination.casefold()
        need(folded not in result, "Duplicate COLLECT destination")
        result[folded] = (destination, source, kind)
    return result


class PE:
    """Minimal bounded PE32+ version-resource reader; no loader or dependency import."""
    def __init__(self, raw):
        need(0 < len(raw) <= 16 * MIB, "PE size")
        self.raw = raw
        self.sections = []
        need(self.bytes(0, 2) == b"MZ", "DOS signature")
        pe = self.u32(60)
        need(pe >= 64 and self.bytes(pe, 4) == b"PE\0\0", "PE signature")
        self.machine = self.u16(pe + 4)
        need(self.machine == 0x8664, "Expected AMD64 PE")
        count, optional_size = self.u16(pe + 6), self.u16(pe + 20)
        need(0 < count <= 96 and optional_size >= 136, "PE headers/count")
        optional = pe + 24
        self.bytes(optional, optional_size)
        need(self.u16(optional) == 0x20B, "Expected PE32+")
        directories = self.u32(optional + 108)
        need(directories <= 16 and optional_size >= 112 + 8 * directories,
             "PE directory extent")
        self.header_size = self.u32(optional + 60)
        table = optional + optional_size
        need(table + 40 * count <= self.header_size <= len(raw), "PE header extent")
        for index in range(count):
            offset = table + 40 * index
            virtual_size, rva, size, file_offset = struct.unpack("<IIII", self.bytes(offset + 8, 16))
            extent = max(virtual_size, size)
            need(extent > 0 and rva >= self.header_size and rva + extent <= 2**32,
                 "PE virtual section extent")
            if size:
                need(file_offset >= self.header_size, "PE section/header overlap")
                self.bytes(file_offset, size)
            for old_rva, old_extent, old_file, old_size in self.sections:
                need(rva + extent <= old_rva or old_rva + old_extent <= rva,
                     "Overlapping PE virtual sections")
                if size and old_size:
                    need(file_offset + size <= old_file or old_file + old_size <= file_offset,
                         "Overlapping PE raw sections")
            self.sections.append((rva, extent, file_offset, size))
        self.resource = (self.u32(optional + 128), self.u32(optional + 132)) if directories > 2 else (0, 0)

    def bytes(self, offset, size):
        need(type(offset) is int and type(size) is int and offset >= 0 and size >= 0
             and offset + size <= len(self.raw), "PE range outside file")
        return self.raw[offset:offset + size]

    def u16(self, offset):
        return struct.unpack("<H", self.bytes(offset, 2))[0]

    def u32(self, offset):
        return struct.unpack("<I", self.bytes(offset, 4))[0]

    def mapped(self, rva, size):
        need(size >= 0 and rva >= 0 and rva + size <= 2**32, "PE RVA overflow")
        matches = [(file + rva - start) for start, extent, file, raw_size in self.sections
                   if start <= rva and rva + size <= start + raw_size]
        need(len(matches) == 1, "Unmapped or ambiguous PE RVA")
        self.bytes(matches[0], size)
        return matches[0]

    def versions(self):
        rva, size = self.resource
        if (rva, size) == (0, 0):
            return []
        need(rva > 0 and 16 <= size <= 4 * MIB, "Resource directory extent")
        base = self.mapped(rva, size)
        seen = set()
        occupied = []
        version_cache = {}
        count = 0
        def reserve(offset, length, kind="structure", alias=False):
            need(0 <= offset and offset + length <= size, "Resource relative range")
            if alias and (offset, length, kind) in occupied:
                return base + offset
            need(all(offset + length <= old or old + n <= offset for old, n, _ in occupied),
                 "Resource object overlap")
            occupied.append((offset, length, kind))
            return base + offset
        def name(value):
            if not value & 0x80000000:
                return value
            offset = value & 0x7fffffff
            need(offset + 2 <= size, "Resource name range")
            length = self.u16(base + offset)
            need(length <= 256, "Resource name bound")
            need(offset % 2 == 0, "Resource name alignment")
            at = reserve(offset, 2 + 2 * length, "name", alias=True)
            return self.bytes(at + 2, 2 * length).decode("utf-16-le")
        def directory(offset, depth, selected):
            nonlocal count
            need(depth <= 8 and offset not in seen, "Resource depth/cycle")
            seen.add(offset)
            need(0 <= offset and offset + 16 <= size, "Resource directory header")
            at = base + offset
            named = self.u16(at + 12)
            entries = named + self.u16(at + 14)
            count += entries + 1
            need(count <= 4096, "Resource node count")
            reserve(offset, 16 + entries * 8)
            outputs = []
            keys = set()
            previous_named = None
            previous_id = None
            for i in range(entries):
                encoded_name = self.u32(at + 16 + i * 8)
                need(bool(encoded_name & 0x80000000) == (i < named), "Resource named/ID partition")
                key = name(encoded_name)
                need(key not in keys, "Duplicate resource name")
                keys.add(key)
                if i < named:
                    sort_key = key.encode("utf-16-be")
                    need(previous_named is None or previous_named < sort_key, "Resource name order")
                    previous_named = sort_key
                else:
                    need(previous_id is None or previous_id < key, "Resource ID order")
                    previous_id = key
                target = self.u32(at + 20 + i * 8)
                choose = key == 16 if depth == 0 else selected
                if not choose:
                    continue
                if target & 0x80000000:
                    outputs.extend(directory(target & 0x7fffffff, depth + 1, True))
                else:
                    data_at = reserve(target, 16, "descriptor", alias=True)
                    data_rva, length, codepage, reserved = struct.unpack("<IIII", self.bytes(data_at, 16))
                    need(reserved == 0 and 0 < length <= MIB, "Version resource descriptor")
                    data_offset = self.mapped(data_rva, length)
                    # Version data may follow directories inside the same resource section.
                    if base <= data_offset < base + size:
                        reserve(data_offset - base, length, "version_payload", alias=True)
                    cache_key = (data_offset, length)
                    if cache_key not in version_cache:
                        version_cache[cache_key] = parse_version(self.bytes(data_offset, length))
                    outputs.append({"language": key, "codepage": codepage,
                                    "value": version_cache[cache_key]})
            return outputs
        return directory(0, 0, False)


def parse_version(raw):
    """Parse the nested VS_VERSIONINFO tree, retaining bounded self-described text."""
    nodes = 0
    def word(at):
        need(0 <= at and at + 2 <= len(raw), "Version word extent")
        return struct.unpack_from("<H", raw, at)[0]
    def block(at, limit, depth):
        nonlocal nodes
        nodes += 1
        need(nodes <= 4096 and depth <= 8 and at + 6 <= limit, "Version tree bound")
        length, value_length, kind = word(at), word(at + 2), word(at + 4)
        end = at + length
        need(length >= 8 and end <= limit and kind in (0, 1), "Version block extent/type")
        cursor = at + 6
        start = cursor
        while cursor + 2 <= end and word(cursor):
            cursor += 2
            need(cursor - start <= 512, "Version key bound")
        need(cursor + 2 <= end, "Unterminated version key")
        key = raw[start:cursor].decode("utf-16-le")
        cursor = (cursor + 2 + 3) & ~3
        value_size = value_length * 2 if kind == 1 else value_length
        need(cursor + value_size <= end, "Version value extent")
        value = raw[cursor:cursor + value_size]
        if depth == 0:
            need(key == "VS_VERSION_INFO" and kind == 0 and value_size in (0, 52),
                 "Version root/fixed-size")
            def split(ms, ls):
                return [ms >> 16, ms & 65535, ls >> 16, ls & 65535]
            if value_size:
                fixed = struct.unpack("<13I", value)
                need(fixed[0] == 0xFEEF04BD and fixed[1] == 0x10000, "Fixed version signature")
                decoded = {"file_version": split(fixed[2], fixed[3]),
                           "product_version": split(fixed[4], fixed[5])}
            else:
                decoded = {"fixed_value": None, "status": "optional_fixed_value_absent"}
        elif kind == 1:
            need(value_size <= 8192, "Version string bound")
            decoded = value.decode("utf-16-le").rstrip("\0")
        else:
            need(value_size <= 8192, "Version binary value bound")
            decoded = {"base64": base64.b64encode(value).decode("ascii")}
        cursor = (cursor + value_size + 3) & ~3
        children = []
        while cursor < end:
            if end - cursor < 8:
                need(not any(raw[cursor:end]), "Nonpadding version tail")
                break
            child, next_at = block(cursor, end, depth + 1)
            children.append(child)
            cursor = (next_at + 3) & ~3
        return {"key": key, "value": decoded, "children": children}, end
    result, end = block(0, len(raw), 0)
    need(not any(raw[end:]), "Nonpadding data after version root")
    return result


def pe_metadata(raw):
    parsed = PE(raw)
    return {"machine": "AMD64", "format": "PE32+", "versions": parsed.versions(),
            "interpretation": "self-described file metadata; not loaded or authenticated"}


def checked_info(path, missing=False):
    """Root-first path checks. lstat times are compared only with other lstats."""
    need(path.is_absolute(), "Observation path must be absolute")
    for ancestor in reversed(path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            if missing:
                return None
            raise
        need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
             and not getattr(info, "st_file_attributes", 0) & 0x400,
             "Redirected/non-directory ancestor")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return None
        raise
    need(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
         and not getattr(info, "st_file_attributes", 0) & 0x400,
         "Redirected/nonregular leaf")
    return info


def identity(info):
    return info.st_dev, info.st_ino, info.st_size


def regular(info):
    return stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400


class Reader:
    """Finite explicit leaf allowlist, bounded reads and final byte rechecks."""
    def __init__(self, allowed):
        need(len(allowed) <= 63, "Read allowlist count")
        self.allowed = {str(path).casefold(): (path, cap) for path, cap in allowed.items()}
        need(len(self.allowed) == len(allowed), "Read allowlist alias")
        self.used = {}
        self.total = 0

    def read(self, path, missing=False, remember=True):
        key = str(path).casefold()
        need(key in self.allowed and path == self.allowed[key][0], "Nonallowlisted read")
        cap = self.allowed[key][1]
        before = checked_info(path, missing=missing)
        if before is None:
            if remember:
                self.used[key] = None
            return None
        need(0 < before.st_size <= cap, "Read file size bound")
        need(0 <= self.total <= INPUT_READ_LIMIT
             and before.st_size <= INPUT_READ_LIMIT - self.total, "Total read budget")
        chunks = []
        count = 0
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            need(regular(opened) and identity(opened) == identity(before),
                 "Opened file identity mismatch")
            # Read the observed regular-file extent; final metadata detects growth.
            # An exact-budget file requires no speculative extra EOF byte.
            while count < before.st_size:
                remaining = INPUT_READ_LIMIT - self.total
                need(remaining > 0, "Total read budget")
                chunk = stream.read(min(65536, before.st_size - count, remaining))
                self.total += len(chunk)
                need(self.total <= INPUT_READ_LIMIT, "Total read budget")
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
            after = os.fstat(stream.fileno())
        final = checked_info(path)
        raw = b"".join(chunks)
        need(0 < len(raw) <= cap and len(raw) == before.st_size,
             "Incomplete/oversized read")
        need(all(regular(x) for x in (before, opened, after, final))
             and identity(opened) == identity(after) == identity(final)
             and opened.st_mtime_ns == after.st_mtime_ns
             and before.st_mtime_ns == final.st_mtime_ns,
             "File identity changed during observation")
        if remember:
            previous = self.used.get(key)
            current = (identity(final), final.st_mtime_ns, len(raw), digest(raw))
            need(key not in self.used or previous == current, "Repeated input differs")
            self.used[key] = current
        return raw

    def recheck(self):
        for key, previous in list(self.used.items()):
            path = self.allowed[key][0]
            raw = self.read(path, missing=previous is None, remember=False)
            if previous is None:
                need(raw is None, "Previously absent source appeared")
            else:
                need(raw is not None, "Previously read input disappeared")
                info = checked_info(path)
                need((identity(info), info.st_mtime_ns, len(raw), digest(raw)) == previous,
                     "Final input differs")


def native_rows(rows, max_bytes=16 * MIB):
    need(type(rows) is list and len(rows) <= 1000, "Native rows schema")
    indexed = {}
    for row in rows:
        need(type(row) is dict, "Native row schema")
        path = relative(row.get("path"))
        need(path.casefold() not in indexed, "Duplicate/aliased native destination")
        need(type(row.get("bytes")) is int and 0 < row["bytes"] <= max_bytes
             and type(row.get("sha256")) is str
             and re.fullmatch("[0-9a-f]{64}", row["sha256"]) is not None,
             "Native size/hash schema")
        indexed[path.casefold()] = row
    return indexed


def bind_inputs(manifest_raw, provenance_raw, archive_raw, baseline_raw, toc_raw):
    need(digest(baseline_raw) == BASELINE_HASH, "Historical baseline bytes differ")
    baseline = native_rows(decode(baseline_raw))
    need(len(baseline) == 46, "Historical target count")
    manifest, report, archive = map(decode, (manifest_raw, provenance_raw, archive_raw))
    need(all(type(x) is dict for x in (manifest, report, archive)), "Receipt object schema")
    mh = digest(manifest_raw)
    need(manifest.get("source_commit") == SOURCE and manifest.get("python") == "3.13.16"
         and manifest.get("status") == "candidate-not-release-approved", "Manifest identity")
    need(report.get("source_commit") == SOURCE and report.get("inspector_commit") == INSPECTOR
         and report.get("inspector_sha256") == INSPECTOR_HASH
         and report.get("build_manifest_sha256") == mh
         and report.get("collect_toc_sha256") == digest(toc_raw)
         and report.get("pyinstaller_version") == "6.22.3"
         and type(report.get("schema_version")) is int and report["schema_version"] == 1,
         "Provenance identity/hash binding")
    need(all(report.get(key) is False for key in
             ("legal_clearance", "corresponding_source_verified", "replacement_verified")),
         "Provenance qualifications differ")
    need(archive.get("status") == "PASS" and archive.get("source_sha") == SOURCE
         and archive.get("build_manifest_sha256") == mh and archive.get("recording_required") is True
         and type(archive.get("recording_source_zip_hashes_verified")) is int
         and archive["recording_source_zip_hashes_verified"] == 23,
         "Archive/recording binding")
    all_rows = native_rows(report.get("native_files"), max_bytes=512 * MIB)
    need(type(report.get("native_count")) is int and report["native_count"] == len(all_rows)
         and report.get("inventory_status") == "complete_with_unknown_origins",
         "Provenance inventory completeness/count")
    for row in all_rows.values():
        origin = row.get("origin")
        need(type(origin) is dict and origin.get("status") in
             ("hash_correspondence", "outside_allowed_origins"), "Unsupported native origin classification")
        if origin["status"] == "outside_allowed_origins":
            need(origin == {"status": "outside_allowed_origins"}, "Outside-origin schema")
    unknown_count = sum(row["origin"]["status"] != "hash_correspondence" for row in all_rows.values())
    targets = {key: value for key, value in all_rows.items()
               if value.get("origin") == {"status": "outside_allowed_origins"}}
    need(set(targets) == set(baseline) and type(report.get("native_unknown_count")) is int
         and report["native_unknown_count"] == unknown_count == 46, "Fresh outside-origin set differs")
    files = manifest.get("files")
    need(type(files) is dict and len(files) <= 10000, "Manifest file schema")
    folded = set()
    for name, sha in files.items():
        relative(name)
        need(name.casefold() not in folded and type(sha) is str
             and re.fullmatch("[0-9a-f]{64}", sha) is not None, "Manifest alias/hash")
        folded.add(name.casefold())
    for key, row in targets.items():
        need(row["path"] == baseline[key]["path"]
             and row["bytes"] <= 16 * MIB
             and files.get(row["path"]) == row["sha256"], "Native manifest join")
    toc = parse_collect(toc_raw)
    declarations = {}
    for key, row in targets.items():
        # This exact selected build uses onedir _internal destinations for BINARY entries.
        need(row["path"].startswith("_internal/"), "Unexpected target bundle mapping")
        toc_key = row["path"][len("_internal/"):].casefold()
        need(toc_key in toc, "Missing COLLECT target")
        destination, source, kind = toc[toc_key]
        need(kind == "BINARY" and "_internal/" + destination == row["path"],
             "COLLECT target type/mapping")
        declared_windows_path(source)
        declarations[key] = source
    return manifest, report, archive, baseline, targets, declarations


def observe(reader, paths, context, baseline_raw, retain_notice):
    result = {"schema_version": 1, "status": "incomplete", "context": context,
              "scope": "fresh bounded metadata observation; not historical source attribution",
              "legal_clearance": False, "corresponding_source_verified": False,
              "replacement_verified": False, "package_membership_verified": False,
              "native_rows": [], "errors": []}
    # Preserve obtainable originals independently, even if the other side fails.
    notices, originals = {}, {}
    for side, key in (("source", "source_notice"), ("copy", "copy_notice")):
        entry = {"status": "unavailable"}
        try:
            raw = reader.read(paths[key])
            originals[side] = raw
            entry.update(status="read_not_retained", bytes=len(raw), sha256=digest(raw))
            entry["retention"] = retain_notice(side, raw)
            entry["status"] = "retained_original"
        except (OSError, ValueError) as exc:
            entry["error"] = {"type": type(exc).__name__, "message": str(exc)[:512]}
            result["errors"].append({"phase": "notice_" + side, **entry["error"]})
        notices[side] = entry
    source, copy = notices["source"], notices["copy"]
    notices["source_copy_equal"] = (source.get("status") == copy.get("status") == "retained_original"
                                    and originals["source"] == originals["copy"])
    notices["matches_run17_hash"] = copy.get("sha256") == NOTICE_HASH
    result["cpython_notice"] = notices
    try:
        raws = [reader.read(paths[key]) for key in ("manifest", "provenance", "archive", "toc")]
        manifest, report, archive, baseline, targets, declarations = bind_inputs(
            raws[0], raws[1], raws[2], baseline_raw, raws[3])
        result["input_hashes"] = {key: digest(raw) for key, raw in
                                  zip(("manifest", "provenance", "archive", "toc"), raws)}
        result["baseline_sha256"] = digest(baseline_raw)
        notice = result.get("cpython_notice")
        need(type(notice) is dict and notice["source_copy_equal"] and
             manifest["files"].get("licenses/CPython-LICENSE.txt") == notice["copy"]["sha256"],
             "CPython notice source/copy/manifest mismatch")
        drift = not notice["matches_run17_hash"]
        for key, target in targets.items():
            raw = reader.read(paths["bundle"] / target["path"])
            need(len(raw) == target["bytes"] and digest(raw) == target["sha256"],
                 "Actual native bytes differ from fresh receipts")
            original = baseline[key]
            row = {"path": target["path"], "bytes": len(raw), "sha256": digest(raw),
                   "run17_same_bytes": (len(raw), digest(raw)) == (original["bytes"], original["sha256"]),
                   "declared_source": declarations[key], "pe": pe_metadata(raw),
                   "source_copy": {"status": "outside_observer_allowlist"},
                   "supplying_package": None}
            drift |= not row["run17_same_bytes"]
            declared = declared_windows_path(declarations[key])
            name = PurePosixPath(target["path"]).name
            if name in OPENSSL_NAMES and declared == OPENSSL_ROOT / name:
                candidate = paths["external"][name]
                source = reader.read(candidate, missing=True)
                row["source_copy"] = {"status": "allowed_candidate_absent"} if source is None else {
                    "status": "fresh_input_copy_hash_match" if source == raw else "allowed_candidate_hash_mismatch",
                    "path": str(candidate), "bytes": len(source), "sha256": digest(source)}
            result["native_rows"].append(row)
        result["run17_baseline_matches"] = not drift
        result["status"] = "observed_with_unresolved_origins" if not drift else "baseline_drift_requires_review"
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, RecursionError, struct.error) as exc:
        result["errors"].append({"type": type(exc).__name__, "message": str(exc)[:512]})
    try:
        reader.recheck()
        result["final_inputs_unchanged"] = True
    except (OSError, ValueError) as exc:
        result["status"] = "incomplete"
        result["final_inputs_unchanged"] = False
        result["errors"].append({"type": type(exc).__name__, "message": str(exc)[:512]})
    result["read_bytes"] = reader.total
    return result


def encoded_report(result):
    chunks, size = [], 0
    encoder = json.JSONEncoder(ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":"))
    for text in encoder.iterencode(result):
        raw = text.encode("ascii")
        size += len(raw)
        need(size + 1 <= 2 * MIB, "Supplemental report size")
        chunks.append(raw)
    return b"".join(chunks) + b"\n"


def bounded_report(result):
    try:
        return encoded_report(result)
    except (ValueError, TypeError, RecursionError) as exc:
        # Original notice sidecars were already independently written and checked.
        fallback = {"schema_version": 1, "status": "incomplete",
                    "scope": "bounded failure receipt; native metadata omitted",
                    "context": result.get("context"), "cpython_notice": result.get("cpython_notice"),
                    "final_inputs_unchanged": result.get("final_inputs_unchanged", False),
                    "legal_clearance": False, "corresponding_source_verified": False,
                    "replacement_verified": False, "package_membership_verified": False,
                    "omitted_native_rows": len(result.get("native_rows", [])),
                    "errors": [{"phase": "serialization", "type": type(exc).__name__, "message": str(exc)[:256]}]}
        result["status"] = "incomplete"
        return encoded_report(fallback)


def write_exclusive(path, raw, cap):
    need(type(raw) is bytes and 0 < len(raw) <= cap, "Output size bound")
    need(checked_info(path, missing=True) is None, "Output collision")
    with path.open("xb") as stream:
        opened = os.fstat(stream.fileno())
        need(regular(opened), "Nonregular output handle")
        stream.write(raw)
        stream.flush()
        finished = os.fstat(stream.fileno())
    final = checked_info(path)
    need(all(regular(x) for x in (opened, finished, final))
         and (opened.st_dev, opened.st_ino) == (finished.st_dev, finished.st_ino)
         and identity(finished) == identity(final) and final.st_size == len(raw),
         "Output identity/size differs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    need(os.name == "nt" and sys.version_info[:3] == (3, 13, 16)
         and struct.calcsize("P") == 8 and PureWindowsPath(sys.base_prefix) == PYTHON_ROOT,
         "Expected regular trusted Windows CPython base")
    run, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    need(re.fullmatch("[0-9]{1,20}", run) is not None and re.fullmatch("[0-9]{1,4}", attempt) is not None,
         "CI run identity")
    temp = Path(os.environ["RUNNER_TEMP"])
    declared_windows_path(str(temp))
    build = temp / ("motortester-" + run + "-" + attempt)
    output = temp / ("native-observation-" + run + "-" + attempt + ".json")
    baseline_path = temp / ("native-baseline-" + run + "-" + attempt + ".json")
    notice_outputs = {side: temp / ("native-notice-" + side + "-" + run + "-" + attempt + ".bin")
                      for side in ("source", "copy")}
    need(Path(args.build_root) == build and Path(args.output) == output
         and Path(args.baseline) == baseline_path, "Fixed CI path contract")
    bundle = build / "dist" / "Motortester"
    paths = {"bundle": bundle, "manifest": bundle / "build-manifest.json",
             "provenance": temp / ("provenance-" + run + "-" + attempt + ".json"),
             "archive": build / "archive-verification.json",
             "toc": build / "pyinstaller" / "motortester" / "COLLECT-00.toc",
             "copy_notice": bundle / "licenses" / "CPython-LICENSE.txt",
             "source_notice": Path(str(PYTHON_ROOT / "LICENSE.txt")),
             "external": {name: Path(str(OPENSSL_ROOT / name)) for name in OPENSSL_NAMES}}
    first = Reader({baseline_path: 65536})
    baseline_raw = first.read(baseline_path)
    need(digest(baseline_raw) == BASELINE_HASH, "Baseline source binding")
    baseline = native_rows(decode(baseline_raw))
    need(len(baseline) == 46, "Baseline set")
    allowed = {baseline_path: 65536, paths["manifest"]: 2 * MIB,
               paths["provenance"]: 8 * MIB, paths["archive"]: MIB,
               paths["toc"]: 2 * MIB, paths["copy_notice"]: MIB, paths["source_notice"]: MIB}
    allowed.update({bundle / row["path"]: 16 * MIB for row in baseline.values()})
    allowed.update({path: 16 * MIB for path in paths["external"].values()})
    allowed.update({path: MIB for path in notice_outputs.values()})
    reader = Reader(allowed)
    # Charge and seal the initial baseline read in the same total input budget.
    reader.total = first.total
    reader.used.update(first.used)
    need(str(output).casefold() not in reader.allowed and checked_info(output, missing=True) is None,
         "Output collision")
    for path in notice_outputs.values():
        need(checked_info(path, missing=True) is None, "Notice output already exists")
    workflow = os.environ["OBSERVER_WORKFLOW_SHA"]
    need(re.fullmatch("[0-9a-f]{40}", workflow) is not None, "Workflow identity")
    context = {"run_id": run, "attempt": attempt, "job": os.environ["GITHUB_JOB"],
               "workflow_sha": workflow, "source_commit": SOURCE, "inspector_commit": INSPECTOR,
               "inspector_sha256": INSPECTOR_HASH, "python_base": sys.base_prefix,
               "source_tree": "0c0e61fb8aadd4a4380e2d7e0b8818e4c9da0d5e",
               "inspector_tree": "2353f3c2680c53faa5bae08beaa5c35b1d785a64",
               "inspector_parent": "d10e83578718d49f971f034a58e0a77af10ff855",
               "recipe_blob": "be9c0f648681f60a20ee14ce19c65cab5c8a465d",
               "lock_blob": "256a8005b4f7de19651c9b9bdd70419b5b52af3d",
               "collector_blob": "1aaa934cf7a964aa853230d635f1ce8335fc175c",
               "prior_job_status": os.environ["OBSERVER_PRIOR_JOB_STATUS"],
               "image_os": os.environ["ImageOS"], "image_version": os.environ["ImageVersion"]}
    def retain_notice(side, raw):
        path = notice_outputs[side]
        write_exclusive(path, raw, MIB)
        need(reader.read(path) == raw, "Notice sidecar readback differs")
        return {"file": path.name, "bytes": len(raw), "sha256": digest(raw)}
    result = observe(reader, paths, context, baseline_raw, retain_notice)
    if context["prior_job_status"] != "success":
        result["status"] = "incomplete"
        result["errors"].append({"phase": "prior_job", "message": "Earlier required workflow step did not succeed"})
    raw = bounded_report(result)
    write_exclusive(output, raw, 2 * MIB)
    print("NATIVE_OBSERVATION", result["status"], len(raw), digest(raw))
    return 0 if result["status"] == "observed_with_unresolved_origins" else 1


if __name__ == "__main__":
    raise SystemExit(main())

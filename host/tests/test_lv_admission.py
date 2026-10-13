"""Synthetic-only LV admission data fixtures; never a real admission bundle.

Plausible schema strings exist solely to exercise syntax.  Evidence is labelled
arbitrary synthetic bytes, including board_config; its meaning is not parsed.
No test grants authority, opens a real port or invokes a physical operation.
"""
from dataclasses import FrozenInstanceError, fields, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from motortester import lv_admission as admission


_DEFAULT_ROOT = object()
PAYLOAD = b"SYNTHETIC TEST BYTES ONLY. NEVER FLASH. NOT AN AUTHORIZATION.\n"


def synthetic_index(*, aliases=False):
    """Fabricated schema fixture; no actual board, ID assignment or permission."""
    return {
        "contract": "LV-ADMISSION-1", "kind": "physical-lv-dc",
        "bundle_id": "bundle-001", "board_instance_id": "board-001",
        "issued_at": "2026-10-13T09:00:00Z", "valid_until": "2026-10-13T10:00:00Z",
        "plan": {"method": "LV-001", "warmup_attempts": 1, "main_attempts": 20,
                 "profile": 1, "mode": 0, "source": 0, "pretrigger_samples": 0,
                 "edge": 0, "level_code": 2048, "auto_timeout_ms": 1000},
        "identities": {"firmware_build": 17, "firmware_source_sha": "a" * 40,
                       "host_source_sha": "b" * 40, "host_package_sha256": "c" * 64,
                       "usb_vid": 1, "usb_pid": 0, "usb_serial": "B001",
                       "port": "COM7", "computer_id": "host-001"},
        "evidence": {role: {"file": "combined.bin" if aliases else role + ".bin",
                            "bytes": len(PAYLOAD),
                            "sha256": hashlib.sha256(PAYLOAD).hexdigest()}
                     for role in admission.ROLES}}


def encode(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def objects(value):
    return [value, value["plan"], value["identities"], value["evidence"],
            *value["evidence"].values()]


def fake_stat(info, **changes):
    result = {name: getattr(info, name) for name in (
        "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")}
    result.update(st_file_attributes=getattr(info, "st_file_attributes", 0),
                  st_reparse_tag=getattr(info, "st_reparse_tag", 0))
    result.update(changes)
    return SimpleNamespace(**result)


class IndexTests(unittest.TestCase):
    def reject(self, value):
        with self.assertRaises(admission.AdmissionDataError) as caught:
            admission.parse_index(value if type(value) is bytes else encode(value))
        self.assertLess(len(str(caught.exception)), 64)
        return str(caught.exception)

    def test_original_bytes_and_order_are_preserved(self):
        data = synthetic_index()
        variants = [encode(data), json.dumps(data, indent=2).encode(),
                    b" \n" + json.dumps(data, sort_keys=True).encode() + b"\t\r\n"]
        parsed = [admission.parse_index(raw) for raw in variants]
        self.assertEqual(len({item.sha256 for item in parsed}), 3)
        for raw, item in zip(variants, parsed):
            self.assertEqual(item.original_bytes, raw)
            self.assertEqual(item.sha256, hashlib.sha256(raw).hexdigest())
            self.assertEqual(item.plan, parsed[0].plan)
            self.assertEqual(tuple(role.role for role in item.roles), admission.ROLES)
            self.assertEqual(len(item.files), 10)
            self.assertEqual(item.unique_bytes, len(PAYLOAD) * 10)

    def test_deep_immutable_detached_data(self):
        data = synthetic_index()
        parsed = admission.parse_index(encode(data))
        data["plan"]["main_attempts"] = 999
        data["evidence"].clear()
        self.assertEqual(parsed.plan.main_attempts, 20)
        self.assertEqual(len(parsed.files), 10)
        def check(value):
            if is_dataclass(value):
                for field in fields(value):
                    with self.assertRaises((FrozenInstanceError, AttributeError)):
                        setattr(value, field.name, None)
                    check(getattr(value, field.name))
            elif type(value) is tuple:
                for item in value:
                    check(item)
            else:
                self.assertIn(type(value), (str, int, bytes))
        check(parsed)
        for name in ("approved", "authorized", "authority", "grant", "run_device", "valid_now"):
            self.assertFalse(hasattr(parsed, name))

    def test_exact_bytes_type_without_mutable_or_custom_buffers(self):
        raw = encode(synthetic_index())
        class Bytes(bytes):
            pass
        for value in (bytearray(raw), memoryview(raw), Bytes(raw), raw.decode(), None, 1):
            with self.subTest(type=type(value)), self.assertRaises(admission.AdmissionDataError):
                admission.parse_index(value)

    def test_legal_json_escapes_and_unicode_strings(self):
        data = synthetic_index()
        data["board_instance_id"] = "плата-001"
        data["identities"]["computer_id"] = "Компьютер-001"
        data["identities"]["port"] = "P" + "😀" * 119
        parsed = admission.parse_index(encode(data))
        self.assertEqual(parsed.identities.port, data["identities"]["port"])
        self.assertEqual(admission.parse_index(encode(data).replace(b"LV-001", b"LV-00\\u0031")).plan,
                         parsed.plan)

    def test_duplicates_every_object_level(self):
        for index in range(14):
            data = synthetic_index()
            obj = objects(data)[index]
            key, value = next(iter(obj.items()))
            needle = encode(obj)
            duplicate = b"{" + encode(key) + b":" + encode(value) + b"," + needle[1:]
            raw = encode(data).replace(needle, duplicate, 1)
            with self.subTest(object=index):
                self.reject(raw)
        self.reject(encode(synthetic_index()).replace(b'"contract":', b'"contr\\u0061ct":"LV-ADMISSION-1","contract":', 1))

    def test_unknown_and_missing_keys_every_object_level(self):
        for index in range(14):
            for extra in (True, False):
                data = synthetic_index()
                obj = objects(data)[index]
                if extra:
                    obj["approved"] = True
                else:
                    obj.pop(next(iter(obj)))
                with self.subTest(object=index, extra=extra):
                    self.reject(data)
        for role in admission.ROLES:
            data = synthetic_index()
            data["evidence"].pop(role)
            self.reject(data)

    def test_objects_cannot_be_arrays_scalars_or_null(self):
        for key in ("plan", "identities", "evidence"):
            for bad in ([], None, True, 0, "x"):
                data = synthetic_index()
                data[key] = bad
                self.reject(data)
        for bad in ([], None, False, 0, "x"):
            data = synthetic_index()
            data["evidence"]["board_config"] = bad
            self.reject(data)
        for raw in (b"[]", b"null", b"true", b"1", b'"x"'):
            self.reject(raw)

    def test_fixed_strings_exact(self):
        for key, bads in (("contract", ("lv-admission-1", "LV-ADMISSION-2", 1, True)),
                          ("kind", ("synthetic", "physical-lv", "physical-lv-dc ", False))):
            for bad in bads:
                data = synthetic_index()
                data[key] = bad
                self.reject(data)
        for bad in ("LV-002", "lv-001", "LV-001 ", 1, True):
            data = synthetic_index()
            data["plan"]["method"] = bad
            self.reject(data)

    def test_integer_fields_exact_types_and_range_edges(self):
        bounds = {"plan": {"warmup_attempts": (1, 1), "main_attempts": (20, 20),
                           "profile": (1, 1), "mode": (0, 0), "source": (0, 0),
                           "pretrigger_samples": (0, 0), "edge": (0, 1),
                           "level_code": (1, 4094), "auto_timeout_ms": (100, 60000)},
                  "identities": {"firmware_build": (0, 2**32 - 1),
                                 "usb_vid": (1, 65534), "usb_pid": (0, 65535)}}
        for section, names in bounds.items():
            for name, (low, high) in names.items():
                for good in (low, high):
                    data = synthetic_index()
                    data[section][name] = good
                    admission.parse_index(encode(data))
                for bad in (low - 1, high + 1, True, False, float(low), str(low), None, [], {}):
                    data = synthetic_index()
                    data[section][name] = bad
                    with self.subTest(section=section, field=name, value=bad):
                        self.reject(data)

    def test_hash_lengths_cases_types(self):
        for name, length in (("firmware_source_sha", 40), ("host_source_sha", 40),
                             ("host_package_sha256", 64)):
            for bad in ("a" * (length - 1), "a" * (length + 1), "A" * length,
                        "g" * length, "0x" + "a" * (length - 2), 123, None):
                data = synthetic_index()
                data["identities"][name] = bad
                self.reject(data)
        for bad in ("A" * 64, "f" * 63, "g" * 64, 64, True, None):
            data = synthetic_index()
            data["evidence"]["board_config"]["sha256"] = bad
            self.reject(data)

    def test_string_bounds_types_controls_and_placeholders(self):
        targets = ((None, "bundle_id", 80), (None, "board_instance_id", 80),
                   ("identities", "port", 120), ("identities", "computer_id", 80),
                   ("identities", "usb_serial", 31))
        for section, name, maximum in targets:
            for good in ("A", "A" * maximum):
                data = synthetic_index()
                (data if section is None else data[section])[name] = good
                admission.parse_index(encode(data))
            for bad in ("", "A" * (maximum + 1), " A", "A ", "A\tB", "A\nB", "\0",
                        "A\x7f", "A\x85", "A\ud800", "A\udfff", "A\u202e",
                        "TBD", "todo", "UNKNOWN", "PLACEHOLDER", "SYNTHETIC1",
                        "TEST-01", "NEVER_FLASH", "never-flash", 1, True, None):
                data = synthetic_index()
                (data if section is None else data[section])[name] = bad
                with self.subTest(field=name, value=repr(bad)):
                    self.reject(data)
        data = synthetic_index()
        data["bundle_id"] = "плата"
        self.reject(data)
        for bad in ("A-B", "A_B", "A.B", "A B", "Плата"):
            data = synthetic_index()
            data["identities"]["usb_serial"] = bad
            self.reject(data)
        data = synthetic_index()
        data["bundle_id"] = "motortester-latest"
        admission.parse_index(encode(data))

    def test_timestamps_syntax_calendar_and_order_not_current_eligibility(self):
        for bad in ("2026-10-13T09:00:00+00:00", "2026-10-13t09:00:00z",
                    "2026-10-13T09:00:00.0Z", "2026-10-13 09:00:00Z",
                    "2026-2-03T09:00:00Z", "2026-02-29T09:00:00Z",
                    "2026-10-13T24:00:00Z", "2026-10-13T09:60:00Z",
                    "2016-12-31T23:59:60Z", "0000-01-01T00:00:00Z", "", None, 1):
            for field in ("issued_at", "valid_until"):
                data = synthetic_index()
                data[field] = bad
                self.reject(data)
        data = synthetic_index()
        for end in (data["issued_at"], "2026-10-13T08:59:59Z"):
            data["valid_until"] = end
            self.reject(data)
        data["issued_at"], data["valid_until"] = "2000-02-29T00:00:00Z", "2000-03-01T00:00:00Z"
        self.assertEqual(admission.parse_index(encode(data)).issued_at, data["issued_at"])

    def test_filename_safe_ascii_and_reserved_names(self):
        for good in ("a", "file-_.a", "a" * 100, "COM10.txt", "flash_readback.txt"):
            data = synthetic_index()
            data["evidence"]["board_passport"]["file"] = good
            admission.parse_index(encode(data))
        bads = ("", "a" * 101, ".hidden", "_private", "../a", "a..b", "a.", "a ",
                "a/b", "a\\b", "a:b", "https://a", "file://a", "\\\\server\\a",
                "a?b", "a*b", "a\0b", "é.txt", "CON", "con.txt", "PRN.bin", "AUX",
                "NUL.bin", "CLOCK$", "COM1", "com9.txt", "LPT1", "lpt9.x", "COM0",
                "LPT0", "COM¹", "admission.json", "ADMISSION.JSON", "TEST.bin",
                "SYNTHETIC.bin", "NEVER_FLASH.bin", None, True, 1)
        for bad in bads:
            data = synthetic_index()
            data["evidence"]["board_passport"]["file"] = bad
            with self.subTest(name=bad):
                self.reject(data)

    def test_aliases_exact_only_and_unique_accounting(self):
        data = synthetic_index(aliases=True)
        parsed = admission.parse_index(encode(data))
        self.assertEqual(len(parsed.roles), 10)
        self.assertEqual(len(parsed.files), 1)
        self.assertEqual(parsed.unique_bytes, len(PAYLOAD))
        self.assertTrue(all(role.member is parsed.files[0] for role in parsed.roles))
        for key, value in (("bytes", len(PAYLOAD) + 1), ("sha256", "a" * 64),
                           ("file", "Combined.bin")):
            bad = synthetic_index(aliases=True)
            bad["evidence"]["measurements"][key] = value
            self.reject(bad)

    def test_index_size_exact_limit_and_plus_one_before_decoder(self):
        raw = encode(synthetic_index())
        maximum = raw + b" " * (admission.MAX_INDEX_BYTES - len(raw))
        self.assertEqual(admission.parse_index(maximum).original_bytes, maximum)
        with patch.object(admission.json, "loads", side_effect=AssertionError("decoder reached")):
            self.reject(maximum + b" ")
            self.reject(b"")

    def test_evidence_and_total_declaration_bounds_without_giant_allocations(self):
        for bad in (0, -1, admission.MAX_FILE_BYTES + 1, True, 1.0, "1", None):
            data = synthetic_index()
            data["evidence"]["board_passport"]["bytes"] = bad
            self.reject(data)
        data = synthetic_index(aliases=True)
        for record in data["evidence"].values():
            record["bytes"] = admission.MAX_FILE_BYTES
        self.assertEqual(admission.parse_index(encode(data)).unique_bytes, admission.MAX_FILE_BYTES)
        data["evidence"]["host_package"]["file"] = "second.bin"
        self.assertEqual(admission.parse_index(encode(data)).unique_bytes, admission.MAX_TOTAL_BYTES)
        data["evidence"]["measurements"]["file"] = "third.bin"
        data["evidence"]["measurements"]["bytes"] = 1
        self.reject(data)

    def test_malformed_bounded_before_generic_allocation(self):
        malformed = [b'"' + b"x" * 2000 + b'"', b"{" * 4000 + b"}" * 4000,
                     b"[" * 4000 + b"]" * 4000, b'{"n":' + b"9" * 10000 + b"}",
                     b'{"n":1e9000}', b'{"n":NaN}', b'{"n":Infinity}', b'{"n":-Infinity}',
                     b'{"n":1.0}', b'{"n":true}', b'{"n":false}', b'{"n":null}',
                     b'{"n":[]}', b'{"n":+1}', b"{" * 4 + b"}" * 4]
        with patch.object(admission.json, "loads", side_effect=AssertionError("decoder reached")):
            for raw in malformed:
                with self.subTest(raw=raw[:20]):
                    self.reject(raw)
        for raw in (b"\xef\xbb\xbf{}", b"\xff", b'"\xed\xa0\x80"', b"{", b"}",
                    b'{"x":"bad\\"}', b'{"x":"bad\\q"}', b'{"x":01}', b'{"x":--1}',
                    b'{"x":"x\ny"}', encode(synthetic_index()) + b"{}"):
            self.reject(raw)
        tracemalloc.start()
        try:
            for raw in malformed:
                self.reject(raw)
            self.assertLess(tracemalloc.get_traced_memory()[1], 1024 * 1024)
        finally:
            tracemalloc.stop()


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lv-admission-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = synthetic_index()
        self.write_bundle()

    def write_bundle(self):
        (self.root / admission.INDEX_NAME).write_bytes(encode(self.data))
        for member in self.data["evidence"].values():
            (self.root / member["file"]).write_bytes(PAYLOAD)

    def reject(self, root=_DEFAULT_ROOT):
        with self.assertRaises(admission.AdmissionDataError) as caught:
            admission.verify_bundle(self.root if root is _DEFAULT_ROOT else root)
        self.assertLess(len(str(caught.exception)), 64)
        self.assertNotIn("private", str(caught.exception))
        return str(caught.exception)

    def test_success_checks_all_opaque_roles_and_retains_no_evidence(self):
        result = admission.verify_bundle(self.root)
        self.assertEqual(type(result), admission.InventoryCheck)
        self.assertEqual(len(result.files), 10)
        self.assertEqual(result.index.original_bytes, (self.root / admission.INDEX_NAME).read_bytes())
        for member in result.files:
            self.assertEqual(member.bytes, len(PAYLOAD))
            self.assertEqual(member.sha256, hashlib.sha256(PAYLOAD).hexdigest())
        with self.assertRaises(FrozenInstanceError):
            result.files = ()
        self.assertEqual(admission.verify_bundle(str(self.root)), result)
        self.assertFalse(hasattr(result, "approved"))

    def test_alias_file_read_once_and_unreferenced_files_not_scanned(self):
        self.data = synthetic_index(aliases=True)
        self.write_bundle()
        (self.root / "not-evidence.py").write_text("raise SystemExit('never execute')")
        # Existing unrelated files, including case variants, are not enumerated.
        if os.name != "nt":
            (self.root / "Combined.bin").write_bytes(b"unreferenced")
        with patch.object(admission.os, "open", wraps=os.open) as opened, \
                patch.object(admission.os, "scandir", side_effect=AssertionError("scan")), \
                patch.object(admission.os, "listdir", side_effect=AssertionError("scan")):
            result = admission.verify_bundle(self.root)
        self.assertEqual(len(result.files), 1)
        self.assertEqual(opened.call_count, 2)

    def test_exact_limits_streaming_and_peak_memory(self):
        self.data = synthetic_index(aliases=True)
        a = self.data["evidence"]["board_passport"]
        a["bytes"] = admission.MAX_FILE_BYTES
        chunk = b"\0" * admission.READ_CHUNK_BYTES
        digest = hashlib.sha256()
        with (self.root / a["file"]).open("wb") as out:
            for _ in range(admission.MAX_FILE_BYTES // len(chunk)):
                out.write(chunk)
                digest.update(chunk)
        a["sha256"] = digest.hexdigest()
        for role in admission.ROLES:
            self.data["evidence"][role] = dict(a)
        second = self.data["evidence"]["host_package"]
        second["file"] = "second.bin"
        with (self.root / second["file"]).open("wb") as out:
            for _ in range(admission.MAX_FILE_BYTES // len(chunk)):
                out.write(chunk)
        raw = encode(self.data)
        raw += b" " * (admission.MAX_INDEX_BYTES - len(raw))
        (self.root / admission.INDEX_NAME).write_bytes(raw)
        real_read = os.read
        sizes = []
        def read(fd, size):
            sizes.append(size)
            return real_read(fd, size)
        tracemalloc.start()
        try:
            with patch.object(admission.os, "read", side_effect=read):
                result = admission.verify_bundle(self.root)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(result.index.unique_bytes, admission.MAX_TOTAL_BYTES)
        self.assertEqual(len(result.files), 2)
        self.assertLessEqual(max(sizes), admission.READ_CHUNK_BYTES)
        self.assertLess(peak, 3 * 1024 * 1024)
        with (self.root / second["file"]).open("ab") as out:
            out.write(b"x")
        self.reject()
        (self.root / admission.INDEX_NAME).write_bytes(raw + b" ")
        self.reject()

    def test_declared_total_checked_before_evidence_open(self):
        self.data = synthetic_index(aliases=True)
        for member in self.data["evidence"].values():
            member["bytes"] = admission.MAX_FILE_BYTES
        self.data["evidence"]["firmware_image"]["file"] = "second.bin"
        self.data["evidence"]["measurements"].update(file="third.bin", bytes=1)
        (self.root / admission.INDEX_NAME).write_bytes(encode(self.data))
        with patch.object(admission.os, "open", wraps=os.open) as opened:
            self.reject()
        self.assertEqual(opened.call_count, 1)

    def test_short_long_same_size_corrupt_or_empty_member(self):
        path = self.root / "measurements.bin"
        for payload in (PAYLOAD[:-1], PAYLOAD + b"x", b"X" * len(PAYLOAD), b""):
            path.write_bytes(payload)
            with self.subTest(size=len(payload)):
                self.reject()

    def test_missing_last_member_never_returns_partial_success(self):
        (self.root / "host_package.bin").unlink()
        result = None
        with self.assertRaises(admission.AdmissionDataError):
            result = admission.verify_bundle(self.root)
        self.assertIsNone(result)

    def test_index_missing_empty_or_nonregular(self):
        path = self.root / admission.INDEX_NAME
        path.unlink()
        self.reject()
        path.write_bytes(b"")
        self.reject()
        path.unlink()
        path.mkdir()
        self.reject()

    def test_nonregular_directory_and_fifo_rejected_without_open(self):
        path = self.root / "board_passport.bin"
        path.unlink()
        path.mkdir()
        with patch.object(admission.os, "open", wraps=os.open) as opened:
            self.reject()
        self.assertEqual(opened.call_count, 1)
        path.rmdir()
        if hasattr(os, "mkfifo"):
            os.mkfifo(path)
            self.reject()

    @unittest.skipIf(os.name == "nt", "symlink privileges depend on Windows host")
    def test_symlink_member_index_root_and_ancestor(self):
        path = self.root / "board_passport.bin"
        path.unlink()
        path.symlink_to(self.root / "measurements.bin")
        self.reject()
        path.unlink()
        self.write_bundle()
        index = self.root / admission.INDEX_NAME
        index.unlink()
        index.symlink_to(self.root / "measurements.bin")
        self.reject()
        index.unlink()
        self.write_bundle()
        link = self.root / "redirect"
        link.symlink_to(self.root, target_is_directory=True)
        self.reject(link)
        sub = self.root / "inner"
        sub.mkdir()
        self.reject(link / "inner")

    def test_reparse_file_root_or_ancestor_rejected(self):
        original = Path.lstat
        for target in (self.root / "board_passport.bin", self.root, self.root.parent):
            for change in ({"st_file_attributes": 0x400}, {"st_reparse_tag": 0xa0000003}):
                def lstat(path, *, target=target, change=change):
                    result = original(path)
                    return fake_stat(result, **change) if path == target else result
                with patch.object(Path, "lstat", lstat):
                    self.reject()

    def test_root_lexical_path_and_type_rejections_before_open(self):
        bads = ("", ".", "relative", str(self.root) + "/..", str(self.root) + "/.",
                str(self.root) + "/", str(self.root) + "//inner", "//server/share",
                "\\\\server\\share", "\\\\?\\C:\\bundle", "C:bundle",
                "/tmp/a:b", "/tmp/a\\b", "/tmp/a\nb", "/" + "x" * 1024,
                "/" + "/".join(["x"] * 64), None, 0, b"/tmp", object())
        with patch.object(admission.os, "open", side_effect=AssertionError("opened")):
            for bad in bads:
                with self.subTest(root=repr(bad)):
                    self.reject(bad)

    def test_permission_read_stat_and_close_errors_are_bounded(self):
        for operation in ("open", "read", "fstat"):
            with patch.object(admission.os, operation, side_effect=PermissionError("private backend path")):
                self.reject()
        with patch.object(Path, "lstat", side_effect=OSError("private metadata")):
            self.reject()
        original_close = os.close
        closed = []
        def close(fd):
            closed.append(fd)
            original_close(fd)
            raise OSError("private close detail")
        with patch.object(admission.os, "close", side_effect=close):
            self.assertEqual(self.reject(), "filesystem-close")
        self.assertEqual(len(closed), 11)

    def test_all_descriptors_closed_after_each_error_stage(self):
        real_open, real_close, real_read = os.open, os.close, os.read
        for fail_read in (1, 2, 4, 11, 20):
            live = set()
            calls = 0
            def open_(path, flags):
                fd = real_open(path, flags)
                live.add(fd)
                return fd
            def close(fd):
                live.remove(fd)
                return real_close(fd)
            def read(fd, size):
                nonlocal calls
                calls += 1
                if calls == fail_read:
                    raise OSError("private")
                return real_read(fd, size)
            with patch.object(admission.os, "open", side_effect=open_), \
                    patch.object(admission.os, "close", side_effect=close), \
                    patch.object(admission.os, "read", side_effect=read):
                self.reject()
            self.assertEqual(live, set())
        (self.root / admission.INDEX_NAME).write_bytes(b'{"bad":true}')
        with patch.object(admission.os, "close", wraps=os.close) as closed:
            self.reject()
        self.assertEqual(closed.call_count, 1)

    def test_truncated_read_and_extra_byte_probe(self):
        original = os.read
        count = 0
        def short(fd, size):
            nonlocal count
            count += 1
            if count == 1:
                return original(fd, 3)
            if count >= 2:
                return b""
            return original(fd, size)
        with patch.object(admission.os, "read", side_effect=short):
            self.assertEqual(self.reject(), "filesystem-size")
        self.data = synthetic_index(aliases=True)
        self.write_bundle()
        count = 0
        def extra(fd, size):
            nonlocal count
            count += 1
            # Index normally needs data+EOF+probe; member data then probe.
            if count == 5:
                return b"x"
            return original(fd, size)
        with patch.object(admission.os, "read", side_effect=extra):
            self.assertEqual(self.reject(), "filesystem-size")

    def test_short_reads_can_accumulate_without_unbounded_buffering(self):
        original = os.read
        with patch.object(admission.os, "read", side_effect=lambda fd, size: original(fd, min(size, 7))):
            result = admission.verify_bundle(self.root)
        self.assertEqual(len(result.files), 10)

    def test_changed_metadata_during_read_is_rejected(self):
        original = os.read
        changed = False
        def read(fd, size):
            nonlocal changed
            chunk = original(fd, size)
            if not changed:
                changed = True
                index = self.root / admission.INDEX_NAME
                old = index.stat()
                os.utime(index, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000_000))
            return chunk
        with patch.object(admission.os, "read", side_effect=read):
            self.assertEqual(self.reject(), "filesystem-changed")

    def test_replaced_path_after_open_and_before_final_pass(self):
        original = os.read
        reads = 0
        def read(fd, size):
            nonlocal reads
            reads += 1
            chunk = original(fd, size)
            if reads == 1:
                index = self.root / admission.INDEX_NAME
                replacement = self.root / "replacement"
                replacement.write_bytes(index.read_bytes())
                os.replace(replacement, index)
            return chunk
        with patch.object(admission.os, "read", side_effect=read):
            self.assertEqual(self.reject(), "filesystem-changed")
        self.write_bundle()
        original_stable = admission._stable_file
        changed = False
        def stable(opened):
            nonlocal changed
            original_stable(opened)
            if not changed and opened.path.name == "host_package.bin":
                changed = True
                first = self.root / "board_passport.bin"
                old = first.stat()
                os.utime(first, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000_000))
        with patch.object(admission, "_stable_file", side_effect=stable):
            self.assertEqual(self.reject(), "filesystem-changed")

    def test_root_or_parent_replacement_fingerprint(self):
        original = Path.lstat
        for target in (self.root, self.root.parent):
            count = 0
            def lstat(path):
                nonlocal count
                result = original(path)
                if path == target:
                    count += 1
                    if count > 1:
                        return fake_stat(result, st_ino=result.st_ino + 1)
                return result
            with patch.object(Path, "lstat", lstat):
                self.assertEqual(self.reject(), "filesystem-changed")

    def test_separate_path_handle_fingerprints_not_cross_stat_equality(self):
        original = os.fstat
        def fstat(fd):
            result = original(fd)
            return fake_stat(result, st_ino=result.st_ino + 123456,
                             st_ctime_ns=result.st_ctime_ns + 1000,
                             st_file_attributes=0x20)
        with patch.object(admission.os, "fstat", side_effect=fstat):
            self.assertEqual(len(admission.verify_bundle(self.root).files), 10)
        count = 0
        def changing(fd):
            nonlocal count
            count += 1
            result = original(fd)
            return fake_stat(result, st_ino=result.st_ino + (1 if count > 1 else 0))
        with patch.object(admission.os, "fstat", side_effect=changing):
            self.assertEqual(self.reject(), "filesystem-changed")

    def test_hardlink_alias_or_unavailable_identity_rejected(self):
        first = self.root / "board_passport.bin"
        second = self.root / "hookup_power_review.bin"
        second.unlink()
        os.link(first, second)
        self.assertEqual(self.reject(), "filesystem-file-alias")
        second.unlink()
        second.write_bytes(PAYLOAD)
        original = os.fstat
        with patch.object(admission.os, "fstat", side_effect=lambda fd: fake_stat(original(fd), st_ino=0)):
            self.assertEqual(self.reject(), "filesystem-file-identity")

    def test_windows_drive_type_metadata_is_read_only_and_fail_closed(self):
        calls = []
        class DriveType:
            value = 3
            def __call__(self, anchor):
                calls.append(anchor)
                return self.value
        drive_type = DriveType()
        def dll(name, **kwargs):
            self.assertEqual(name, "kernel32")
            self.assertEqual(kwargs, {"use_last_error": True})
            return SimpleNamespace(GetDriveTypeW=drive_type)
        fake_ctypes = SimpleNamespace(WinDLL=dll, c_wchar_p=str, c_uint=int)
        with patch.dict(sys.modules, {"ctypes": fake_ctypes}):
            for kind in (2, 3, 5, 6):
                drive_type.value = kind
                admission._windows_local_drive("C:\\")
            for kind in (0, 1, 4):
                drive_type.value = kind
                with self.assertRaises(admission.AdmissionDataError):
                    admission._windows_local_drive("C:\\")
        self.assertEqual(calls, ["C:\\"] * 7)
        self.assertEqual(drive_type.argtypes, (str,))
        self.assertEqual(drive_type.restype, int)

    def test_final_validation_error_closes_every_retained_descriptor(self):
        real_stable = admission._stable_file
        calls = 0
        def stable(opened):
            nonlocal calls
            calls += 1
            # Two checks per file, then the final whole-inventory pass.
            if calls == 23:
                raise admission.AdmissionDataError("filesystem-changed")
            real_stable(opened)
        with patch.object(admission, "_stable_file", side_effect=stable), \
                patch.object(admission.os, "close", wraps=os.close) as close:
            self.assertEqual(self.reject(), "filesystem-changed")
        self.assertEqual(close.call_count, 11)

    def test_opened_handle_reparse_type_and_size_rejected_and_closed(self):
        real_fstat = os.fstat
        for changes in ({"st_file_attributes": 0x400}, {"st_mode": stat.S_IFIFO},
                        {"st_size": 0}, {"st_size": admission.MAX_INDEX_BYTES + 1}):
            with patch.object(admission.os, "fstat", side_effect=lambda fd: fake_stat(real_fstat(fd), **changes)), \
                    patch.object(admission.os, "close", wraps=os.close) as close:
                self.reject()
            self.assertEqual(close.call_count, 1)

    def test_no_writes_process_network_or_operational_imports(self):
        # A fresh interpreter audits import+parse+inventory together.  Tests may
        # write fixture files; the library is audited after that preparation.
        script = r'''
import os, sys
root = sys.argv[1]
def audit(event, args):
    if event == "open":
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            raise RuntimeError("write forbidden")
    if event.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.remove", "os.rename", "os.mkdir", "os.chmod", "os.utime")):
        raise RuntimeError("side effect forbidden")
sys.addaudithook(audit)
from motortester.lv_admission import parse_index, verify_bundle, AdmissionDataError
for raw in (b"{}", b"[" * 8000):
    try:
        parse_index(raw)
    except AdmissionDataError:
        pass
    else:
        raise RuntimeError("invalid accepted")
try:
    verify_bundle(os.path.join(root, "missing-bundle"))
except AdmissionDataError:
    pass
else:
    raise RuntimeError("missing accepted")
result = verify_bundle(root)
if len(result.files) != 10:
    raise RuntimeError("missing files")
for module in ("serial", "motortester.lv_runner", "motortester.transport", "motortester.runtime", "motortester.lv_collector"):
    if module in sys.modules:
        raise RuntimeError("operational dependency imported")
for module in (() if os.name == "nt" else ("ctypes",)):
    if module in sys.modules:
        raise RuntimeError("operational dependency imported")
print("read-only data inspection")
'''
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        completed = subprocess.run([sys.executable, "-B", "-c", script, str(self.root)],
                                   env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("read-only data inspection", completed.stdout)

    def test_caller_metadata_and_data_result_cannot_enable_device_seam(self):
        from motortester import lv_runner
        result = admission.verify_bundle(self.root)
        with patch.object(lv_runner, "SerialTransport", side_effect=AssertionError("device constructed")):
            for metadata in ({"approved": True}, result, result.index, result.index.identities):
                with self.assertRaises(lv_runner.AdmissionError):
                    lv_runner.run_device(admission=metadata, port="COM7")
        self.data["approved"] = True
        (self.root / admission.INDEX_NAME).write_bytes(encode(self.data))
        self.reject()


if __name__ == "__main__":
    unittest.main()

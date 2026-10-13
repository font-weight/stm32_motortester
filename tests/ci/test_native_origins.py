"""Inert metadata/parser and mocked-path controls; no DLL loading or CI execution."""
import ast
import importlib.util
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).parents[2] / "scripts/ci/observe_native_origins.py"
spec = importlib.util.spec_from_file_location("native_observer", SCRIPT)
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


def inert_pe(version=True):
    """A byte-only parser input with no instructions, entry point or executable fixture."""
    data = bytearray(2048)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, 0x8664, 1)
    struct.pack_into("<H", data, 148, 240)
    optional = 152
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<I", data, optional + 60, 512)
    struct.pack_into("<I", data, optional + 108, 16)
    table = optional + 240
    data[table:table + 8] = b".rsrc\0\0\0"
    struct.pack_into("<IIII", data, table + 8, 1536, 4096, 1536, 512)
    if version:
        struct.pack_into("<II", data, optional + 128, 4096, 1536)
        for offset, key, target in [(0, 16, 0x80000020), (32, 1, 0x80000040), (64, 1033, 96)]:
            struct.pack_into("<H", data, 512 + offset + 14, 1)
            struct.pack_into("<II", data, 512 + offset + 16, key, target)
        key = "VS_VERSION_INFO\0".encode("utf-16-le")
        block = bytearray(struct.pack("<HHH", 92, 52, 0) + key)
        block.extend(b"\0" * ((-len(block)) % 4))
        block.extend(struct.pack("<13I", 0xFEEF04BD, 0x10000, 0x10002, 0x30004,
                                 0x50006, 0x70008, 0, 0, 0, 0, 0, 0, 0))
        if len(block) != 92:
            raise RuntimeError("Inert fixture size")
        struct.pack_into("<IIII", data, 608, 4096 + 128, len(block), 1200, 0)
        data[640:640 + len(block)] = block
    return bytes(data)


class Parsers(unittest.TestCase):
    def test_inert_pe_version(self):
        result = o.pe_metadata(inert_pe())
        self.assertEqual(result["machine"], "AMD64")
        self.assertEqual(result["versions"][0]["value"]["value"]["file_version"], [1, 2, 3, 4])

    def test_optional_resource_absent(self):
        self.assertEqual(o.pe_metadata(inert_pe(False))["versions"], [])

    def test_optional_fixed_value_absent(self):
        raw = bytearray(inert_pe())
        struct.pack_into("<H", raw, 640, 40)
        struct.pack_into("<H", raw, 642, 0)
        struct.pack_into("<I", raw, 612, 40)
        value = o.pe_metadata(bytes(raw))["versions"][0]["value"]["value"]
        self.assertEqual(value, {"fixed_value": None, "status": "optional_fixed_value_absent"})

    def test_resource_partition_and_order(self):
        raw = bytearray(inert_pe())
        struct.pack_into("<HH", raw, 524, 1, 0)  # Declared named entry is numeric.
        with self.assertRaisesRegex(o.Invalid, "partition"):
            o.pe_metadata(bytes(raw))
        raw = bytearray(inert_pe())
        struct.pack_into("<H", raw, 590, 2)
        struct.pack_into("<II", raw, 600, 1032, 96)
        with self.assertRaisesRegex(o.Invalid, "order"):
            o.pe_metadata(bytes(raw))

    def test_exact_shared_version_payload(self):
        raw = bytearray(inert_pe())
        struct.pack_into("<H", raw, 590, 2)
        struct.pack_into("<II", raw, 600, 1034, 112)
        raw[624:640] = raw[608:624]
        versions = o.pe_metadata(bytes(raw))["versions"]
        self.assertEqual([x["language"] for x in versions], [1033, 1034])
        self.assertEqual(versions[0]["value"], versions[1]["value"])
        struct.pack_into("<I", raw, 624, 4096 + 132)
        with self.assertRaisesRegex(o.Invalid, "overlap"):
            o.pe_metadata(bytes(raw))

    def test_pe_malformed_ranges_and_cycles(self):
        cases = [b"", b"MZ", inert_pe()[:500]]
        for offset, fmt, value in [(60, "I", 2048), (132, "H", 0x14C),
                                    (134, "H", 97), (152, "H", 0x10B),
                                    (212, "I", 20), (260, "I", 17),
                                    (280, "I", 0xffffffff), (284, "I", 4 * o.MIB + 1),
                                    (408, "I", 4096), (412, "I", 0),
                                    (532, "I", 0x80000000), (576 + 20, "I", 0x80000020),
                                    (612, "I", 0xffffffff), (620, "I", 1),
                                    (680, "I", 0), (640, "H", 5), (642, "H", 51)]:
            raw = bytearray(inert_pe())
            struct.pack_into("<" + fmt, raw, offset, value)
            cases.append(bytes(raw))
        for number, raw in enumerate(cases):
            with self.subTest(number=number), self.assertRaises((ValueError, UnicodeError)):
                o.pe_metadata(raw)

    def test_pe_section_overlap(self):
        raw = bytearray(inert_pe())
        struct.pack_into("<H", raw, 134, 2)
        raw[432:472] = raw[392:432]
        with self.assertRaises(o.Invalid):
            o.pe_metadata(bytes(raw))

    def test_pe_oversize(self):
        with self.assertRaises(o.Invalid):
            o.pe_metadata(b"x" * (16 * o.MIB + 1))

    def test_collect_positive(self):
        raw = repr(([("libcrypto-3-x64.dll", r"C:\Program Files\OpenSSL\bin\libcrypto-3-x64.dll", "BINARY")],)).encode()
        self.assertEqual(o.parse_collect(raw)["libcrypto-3-x64.dll"][2], "BINARY")

    def test_collect_rejections(self):
        cases = [b"", b"[]", b"([], [])", b"([('a', 'b')],)",
                 b"([('a','b',True)],)", b"(__import__('os').system('never'),)",
                 b"([('a','b','BINARY'),('A','c','BINARY')],)",
                 b"([('../a','b','BINARY')],)", b"([('a:b','b','BINARY')],)",
                 repr(([("x", "a" * 4097, "BINARY")],)).encode(),
                 ("(" + "[" * 34 + "''" + "]" * 34 + ",)").encode(),
                 b" " * (2 * o.MIB + 1)]
        for raw in cases:
            with self.subTest(prefix=raw[:60]), self.assertRaises((ValueError, SyntaxError)):
                o.parse_collect(raw)

    def test_collect_count(self):
        rows = [(str(x), "C:\\a", "DATA") for x in range(10001)]
        with self.assertRaises(o.Invalid):
            o.parse_collect(repr((rows,)).encode())

    def test_windows_lexical_paths(self):
        self.assertEqual(str(o.declared_windows_path(r"C:\Program Files\OpenSSL\bin\a.dll")),
                         r"C:\Program Files\OpenSSL\bin\a.dll")
        for value in [r"\\host\share\a", r"\\?\C:\a", "C:a", r"C:\a\..\b",
                      r"C:\a:stream", "C:\\a\n", "C:\\a\\", "C:\\a.\\b", "C:\\\\a"]:
            with self.subTest(value=value), self.assertRaises(o.Invalid):
                o.declared_windows_path(value)

    def test_relative_aliases(self):
        for value in ["/a", "../a", "a/../b", "a//b", "a/./b", "a\\b", "a:b", "a.", "a ", True, ""]:
            with self.subTest(value=value), self.assertRaises(o.Invalid):
                o.relative(value)

    def test_json_rejects_duplicate_nonfinite(self):
        for raw in [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}']:
            with self.assertRaises(ValueError):
                o.decode(raw)

    def test_json_bool_size_rejected(self):
        with self.assertRaises(o.Invalid):
            o.native_rows([{"path": "a", "bytes": True, "sha256": "0" * 64}])


class FileGuards(unittest.TestCase):
    def test_absent_optional_ancestor(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "absent" / "candidate.txt"
            reader = o.Reader({path: 4})
            self.assertIsNone(reader.read(path, missing=True))
            reader.recheck()
            with self.assertRaises(FileNotFoundError):
                reader.read(path)
            path.parent.write_bytes(b"not a directory")
            with self.assertRaises(o.Invalid):
                reader.read(path, missing=True)

    def test_nonregular_handle_snapshots(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.txt"
            path.write_bytes(b"data")
            actual = path.stat()
            bad = SimpleNamespace(**{k: getattr(actual, k) for k in
                                      ("st_dev", "st_ino", "st_size", "st_mtime_ns")},
                                  st_mode=stat.S_IFDIR, st_file_attributes=0)
            for index in (0, 1):
                snapshots = [actual, actual]
                snapshots[index] = bad
                with self.subTest(index=index), patch.object(o.os, "fstat", side_effect=snapshots):
                    with self.assertRaises(o.Invalid):
                        o.Reader({path: 4}).read(path)

    def test_exclusive_sidecar_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "notice.bin"
            o.write_exclusive(path, b"original", 8)
            self.assertEqual(path.read_bytes(), b"original")
            with self.assertRaises(o.Invalid):
                o.write_exclusive(path, b"changed!", 8)
            with self.assertRaises(o.Invalid):
                o.write_exclusive(Path(folder) / "too-large.bin", b"x" * 9, 8)

    def test_exact_cap_and_recheck(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plain-input.txt"
            path.write_bytes(b"data")
            reader = o.Reader({path: 4})
            self.assertEqual(reader.read(path), b"data")
            reader.recheck()
            self.assertEqual(reader.total, 8)

    def test_first_over_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plain-input.txt"
            path.write_bytes(b"12345")
            with self.assertRaises(o.Invalid):
                o.Reader({path: 4}).read(path)

    def test_forbidden_path_never_opened(self):
        with patch.object(Path, "open", side_effect=AssertionError("must not open")):
            with self.assertRaises(o.Invalid):
                o.Reader({Path("/permitted"): 10}).read(Path("/elsewhere"))

    def test_missing_candidate_stays_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "absent.txt"
            reader = o.Reader({path: 4})
            self.assertIsNone(reader.read(path, missing=True))
            reader.recheck()
            path.write_bytes(b"x")
            with self.assertRaises(o.Invalid):
                reader.recheck()

    def test_same_length_change(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plain-input.txt"
            path.write_bytes(b"data")
            reader = o.Reader({path: 4})
            reader.read(path)
            path.write_bytes(b"DATA")
            with self.assertRaises(o.Invalid):
                reader.recheck()

    def test_mock_redirect_rejected_without_open(self):
        class Info:
            st_mode = stat.S_IFDIR | 0o755
            st_file_attributes = 0x400
        with patch.object(Path, "lstat", return_value=Info()), patch.object(Path, "open", side_effect=AssertionError("opened")):
            with self.assertRaises(o.Invalid):
                o.Reader({Path("/input.txt"): 4}).read(Path("/input.txt"))

    def test_path_and_total_caps(self):
        with self.assertRaises(o.Invalid):
            o.Reader({Path("/x" + str(n)): 1 for n in range(64)})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plain-input.txt"
            path.write_bytes(b"xx")
            reader = o.Reader({path: 2})
            reader.total = o.INPUT_READ_LIMIT - 1
            with patch.object(Path, "open", side_effect=AssertionError("over-budget file opened")):
                with self.assertRaises(o.Invalid):
                    reader.read(path)
            self.assertEqual(reader.total, o.INPUT_READ_LIMIT - 1)

    def test_exact_aggregate_boundary_and_auxiliary_reserve(self):
        fixed_auxiliary_max = (4 * o.MIB + 3) + 2 * 65537 + 2 * 65536
        self.assertLess(fixed_auxiliary_max, o.AUX_READ_RESERVE)
        self.assertEqual(o.INPUT_READ_LIMIT + o.AUX_READ_RESERVE, 128 * o.MIB)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.txt"
            raw = b"x" * 65537
            path.write_bytes(raw)
            reader = o.Reader({path: len(raw)})
            reader.total = o.INPUT_READ_LIMIT - len(raw)
            self.assertEqual(reader.read(path), raw)
            self.assertEqual(reader.total, o.INPUT_READ_LIMIT)
            with patch.object(Path, "open", side_effect=AssertionError("exhausted budget opened file")):
                with self.assertRaises(o.Invalid):
                    reader.recheck()
            self.assertEqual(reader.total, o.INPUT_READ_LIMIT)


class Receipts(unittest.TestCase):
    def setUp(self):
        self.raw_pe = inert_pe(False)
        self.baseline = [{"path": "_internal/" + (o.OPENSSL_NAMES[i] if i < 2 else f"api-{i}.dll"),
                          "bytes": len(self.raw_pe), "sha256": o.digest(self.raw_pe),
                          "origin": {"status": "outside_allowed_origins"}} for i in range(46)]
        self.baseline_raw = json.dumps(self.baseline).encode()
        self.notice = b"Original notice\r\n"
        files = {x["path"]: x["sha256"] for x in self.baseline}
        files["licenses/CPython-LICENSE.txt"] = o.digest(self.notice)
        self.manifest = {"source_commit": o.SOURCE, "python": "3.13.16", "status": "candidate-not-release-approved", "files": files}
        self.manifest_raw = json.dumps(self.manifest).encode()
        triples = [(x["path"].split("/", 1)[1], str(o.OPENSSL_ROOT / x["path"].split("/", 1)[1]), "BINARY") for x in self.baseline]
        self.toc = repr((triples,)).encode()
        self.provenance = {"source_commit": o.SOURCE, "inspector_commit": o.INSPECTOR,
                           "inspector_sha256": o.INSPECTOR_HASH, "build_manifest_sha256": o.digest(self.manifest_raw),
                           "collect_toc_sha256": o.digest(self.toc), "pyinstaller_version": "6.22.3", "schema_version": 1,
                           "legal_clearance": False, "corresponding_source_verified": False, "replacement_verified": False,
                           "native_files": self.baseline, "native_unknown_count": 46,
                           "native_count": 46, "inventory_status": "complete_with_unknown_origins"}
        self.archive = {"status": "PASS", "source_sha": o.SOURCE, "build_manifest_sha256": o.digest(self.manifest_raw),
                        "recording_required": True, "recording_source_zip_hashes_verified": 23}
        self.paths = {"bundle": Path("/build/dist/Motortester"),
                      "manifest": Path("/manifest.json"), "provenance": Path("/provenance.json"),
                      "archive": Path("/archive.json"), "toc": Path("/COLLECT-00.toc"),
                      "source_notice": Path("/source-LICENSE.txt"), "copy_notice": Path("/copy-LICENSE.txt"),
                      "external": {name: Path("/allowed-" + name) for name in o.OPENSSL_NAMES}}
        self.retained = {}
        self.patcher = patch.object(o, "BASELINE_HASH", o.digest(self.baseline_raw))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def retain(self, side, raw):
        self.retained[side] = raw
        return {"file": "native-notice-" + side + ".bin", "bytes": len(raw), "sha256": o.digest(raw)}

    def bind(self):
        return o.bind_inputs(self.manifest_raw, json.dumps(self.provenance).encode(),
                             json.dumps(self.archive).encode(), self.baseline_raw, self.toc)

    def reader(self):
        data = {self.paths["manifest"]: self.manifest_raw,
                self.paths["provenance"]: json.dumps(self.provenance).encode(),
                self.paths["archive"]: json.dumps(self.archive).encode(), self.paths["toc"]: self.toc,
                self.paths["source_notice"]: self.notice, self.paths["copy_notice"]: self.notice}
        data.update({self.paths["bundle"] / x["path"]: self.raw_pe for x in self.baseline})
        data.update({p: self.raw_pe for p in self.paths["external"].values()})
        class MockReader:
            total = 0
            def __init__(self):
                self.data = data
                self.opened = []
            def read(self, path, missing=False):
                self.opened.append(path)
                if path not in self.data and missing:
                    return None
                raw = self.data[path]
                self.total += len(raw)
                return raw
            def recheck(self):
                pass
        return MockReader()

    def test_valid_join(self):
        self.assertEqual(len(self.bind()[4]), 46)

    def test_wrong_identity_and_qualification(self):
        for key, bad in [("source_commit", "0" * 40), ("inspector_commit", "0" * 40),
                         ("inspector_sha256", "0" * 64), ("collect_toc_sha256", "0" * 64),
                         ("build_manifest_sha256", "0" * 64), ("schema_version", True),
                         ("legal_clearance", True), ("native_unknown_count", True)]:
            original = self.provenance[key]
            self.provenance[key] = bad
            with self.subTest(key=key), self.assertRaises(o.Invalid):
                self.bind()
            self.provenance[key] = original

    def test_wrong_recording_join(self):
        self.archive["recording_source_zip_hashes_verified"] = True
        with self.assertRaises(o.Invalid):
            self.bind()

    def test_no_target_omission_or_duplicate(self):
        for rows in [self.baseline[:-1], self.baseline + self.baseline[:1]]:
            self.provenance["native_files"] = rows
            with self.assertRaises(o.Invalid):
                self.bind()

    def test_unrelated_large_native_does_not_expand_target_reads(self):
        self.provenance["native_files"] = self.baseline + [{"path": "large.dll", "bytes": 20 * o.MIB,
            "sha256": "0" * 64, "origin": {"status": "hash_correspondence"}}]
        self.provenance["native_count"] = 47
        self.assertEqual(len(self.bind()[4]), 46)

    def test_extra_unknown_classification_is_rejected(self):
        for status in ("no_collect_entry", "outside_allowed_origins", "unknown"):
            self.provenance["native_files"] = self.baseline + [{"path": "extra.dll", "bytes": 10,
                "sha256": "0" * 64, "origin": {"status": status}}]
            self.provenance["native_count"] = 47
            with self.subTest(status=status), self.assertRaises(o.Invalid):
                self.bind()

    def test_each_obtainable_notice_survives_other_read_failure(self):
        for failed in ("source", "copy"):
            reader = self.reader()
            original_read = reader.read
            def read(path, missing=False):
                if path == self.paths[failed + "_notice"]:
                    raise OSError("unavailable notice")
                return original_read(path, missing)
            self.retained.clear()
            with patch.object(reader, "read", side_effect=read):
                result = o.observe(reader, self.paths, {}, self.baseline_raw, self.retain)
            other = "copy" if failed == "source" else "source"
            self.assertEqual(self.retained, {other: self.notice})
            self.assertEqual(result["cpython_notice"][failed]["status"], "unavailable")
            self.assertEqual(result["status"], "incomplete")

    def test_notice_retention_failure_does_not_skip_other(self):
        def retain(side, raw):
            if side == "source":
                raise OSError("sidecar unavailable")
            return self.retain(side, raw)
        result = o.observe(self.reader(), self.paths, {}, self.baseline_raw, retain)
        self.assertEqual(self.retained, {"copy": self.notice})
        self.assertEqual(result["cpython_notice"]["source"]["status"], "read_not_retained")
        self.assertEqual(result["status"], "incomplete")

    def test_both_maximum_different_notices_and_overflow_receipt(self):
        reader = self.reader()
        originals = {"source": b"s" * o.MIB, "copy": b"c" * o.MIB}
        for side, raw in originals.items():
            reader.data[self.paths[side + "_notice"]] = raw
        with tempfile.TemporaryDirectory() as folder:
            def retain(side, raw):
                path = Path(folder) / (side + ".bin")
                o.write_exclusive(path, raw, o.MIB)
                return {"file": path.name, "bytes": len(raw), "sha256": o.digest(raw)}
            result = o.observe(reader, self.paths, {}, self.baseline_raw, retain)
            self.assertFalse(result["cpython_notice"]["source_copy_equal"])
            result["native_rows"] = [{"oversized": "x" * (2 * o.MIB)}]
            raw = o.bounded_report(result)
            fallback = json.loads(raw)
            self.assertLessEqual(len(raw), 2 * o.MIB)
            self.assertEqual(fallback["status"], "incomplete")
            self.assertEqual(fallback["omitted_native_rows"], 1)
            for side, original in originals.items():
                self.assertEqual((Path(folder) / (side + ".bin")).read_bytes(), original)
                self.assertEqual(fallback["cpython_notice"][side]["sha256"], o.digest(original))

    def test_actual_observation_mock(self):
        reader = self.reader()
        with patch.object(o, "NOTICE_HASH", o.digest(self.notice)):
            result = o.observe(reader, self.paths, {}, self.baseline_raw, self.retain)
        self.assertEqual(result["status"], "observed_with_unresolved_origins")
        self.assertEqual(len(result["native_rows"]), 46)
        self.assertEqual(result["native_rows"][0]["source_copy"]["status"], "fresh_input_copy_hash_match")
        self.assertEqual(sum(r["source_copy"]["status"] == "outside_observer_allowlist" for r in result["native_rows"]), 44)
        self.assertFalse(result["legal_clearance"])
        self.assertEqual(self.retained, {"source": self.notice, "copy": self.notice})
        self.assertEqual(result["cpython_notice"]["copy"]["retention"]["sha256"], o.digest(self.notice))
        self.assertTrue(result["final_inputs_unchanged"])

    def test_source_missing_and_nonmatch_are_unknown(self):
        reader = self.reader()
        del reader.data[self.paths["external"][o.OPENSSL_NAMES[0]]]
        reader.data[self.paths["external"][o.OPENSSL_NAMES[1]]] = b"different"
        with patch.object(o, "NOTICE_HASH", o.digest(self.notice)):
            result = o.observe(reader, self.paths, {}, self.baseline_raw, self.retain)
        self.assertEqual(result["status"], "observed_with_unresolved_origins")
        self.assertEqual(result["native_rows"][0]["source_copy"]["status"], "allowed_candidate_absent")
        self.assertEqual(result["native_rows"][1]["source_copy"]["status"], "allowed_candidate_hash_mismatch")

    def test_notice_retained_after_provenance_failure(self):
        reader = self.reader()
        reader.data[self.paths["provenance"]] = b"bad json"
        result = o.observe(reader, self.paths, {}, self.baseline_raw, self.retain)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(self.retained, {"source": self.notice, "copy": self.notice})
        self.assertEqual(result["cpython_notice"]["copy"]["retention"]["sha256"], o.digest(self.notice))

    def test_notice_mismatch_and_historical_drift(self):
        reader = self.reader()
        result = o.observe(reader, self.paths, {}, self.baseline_raw, self.retain)
        self.assertEqual(result["status"], "baseline_drift_requires_review")
        reader.data[self.paths["source_notice"]] = b"other"
        self.assertEqual(o.observe(reader, self.paths, {}, self.baseline_raw, self.retain)["status"], "incomplete")

    def test_final_recheck_failure(self):
        reader = self.reader()
        with patch.object(reader, "recheck", side_effect=o.Invalid("late change")):
            result = o.observe(reader, self.paths, {}, self.baseline_raw, self.retain)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["final_inputs_unchanged"])

    def test_output_is_escaped_and_bounded(self):
        encoded = o.encoded_report({"path": "\n::warning::untrusted\r\n"})
        self.assertEqual(encoded.count(b"\n"), 1)
        with self.assertRaises(o.Invalid):
            o.encoded_report({"x": "x" * (2 * o.MIB)})


if __name__ == "__main__":
    unittest.main()

"""Offline supplement/source-fake checks; no Windows or redistribution approval."""
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
ROOT = PACKAGING / "licenses" / "dependency-6.12.0"
spec = importlib.util.spec_from_file_location("dependency_licenses_under_test", PACKAGING / "collect_licenses.py")
licenses = importlib.util.module_from_spec(spec)
spec.loader.exec_module(licenses)

# Independent evidence pins, not derived from the collector constants.
MANIFEST_SHA256 = "dde5677a4f9cce86a3e81c20892b7266f89ddc3c609bd7d52a8d9bce4768644b"
PROPOSAL_SHA256 = "486c978af0fa92eb5b63eee9e03c30a82d40abdb841efd3e2e9e97303e20385b"
GUIDE_SHA256 = "36923624c9723995ed4b2905e198c7e0cd4d2d4ed9b60e5ca5f6602a594a3307"
MISSING_IDS = {"md4", "md5", "sha1", "siphash", "tika-mimetypes", "tlexpected",
               "unicode-character-database", "unicode-cldr", "emoji-segmenter", "wintab", "libjpeg"}
ARCHIVES = {
    "qtbase-everywhere-src-6.12.0.tar.xz": (51220268, "a951bd163c7b80fc6b8c88d7668fb56abf91c152373e13c10666763238131307"),
    "qtsvg-everywhere-src-6.12.0.tar.xz": (2341876, "e4ab39534ec97987b1b9b60ef7f4d3253d5912a69f8c713a01753174a4029331"),
    "qtimageformats-everywhere-src-6.12.0.tar.xz": (1848620, "0943132b5db8de6b6a7f1d162682173e8763c7ed94e80fd39113292845f67762"),
    "pyside-setup-everywhere-src-6.12.0.tar.xz": (18211772, "099c1a597cf33c1b000e11cf68a0b82d216fb802bf81726efc720394f10c7751"),
}


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def distribution(name, version, root=None, files=()):
    return SimpleNamespace(metadata={"Name": name, "License": "fixture license",
                                     "License-Expression": "fixture expression"},
                           version=version, files=files, locate_file=lambda p: root / p)


class DependencyNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dependency notices тест ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        (self.runtime / "LICENSE.txt").write_bytes(b"fixture CPython notice\n")
        self.destination = self.root / "output"
        self.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    def collect(self, distributions=None):
        if distributions is None:
            distributions = [distribution("PySide6_Essentials", "6.12.0"),
                             distribution("shiboken6", "6.12.0"), distribution("pyserial", "3.5")]
        with patch.object(licenses.metadata, "distributions", return_value=iter(distributions)), \
                patch.object(licenses, "sys", SimpleNamespace(base_prefix=str(self.runtime))):
            licenses.collect(self.destination)
        return json.loads((self.destination / "inventory.json").read_text(encoding="utf-8"))

    def rejected(self):
        with self.assertRaises((RuntimeError, OSError, ValueError, TypeError)):
            self.collect()
        self.assertFalse((self.destination / "inventory.json").exists())

    @contextmanager
    def changed_manifest(self, mutation=None, transform=None):
        # Test validation below the immutable hash boundary without weakening it
        # in production. Production always uses the pinned original digest.
        vendor = self.root / "mutated"
        shutil.copytree(ROOT, vendor)
        value = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
        if mutation:
            mutation(value)
        raw = canonical(value)
        if transform:
            raw = transform(raw)
        (vendor / "manifest.json").write_bytes(raw)
        with patch.object(licenses, "DEPENDENCY_LICENSES", vendor), \
                patch.object(licenses, "DEPENDENCY_MANIFEST_BYTES", len(raw)), \
                patch.object(licenses, "DEPENDENCY_MANIFEST_SHA256", hashlib.sha256(raw).hexdigest()):
            yield
        shutil.rmtree(vendor)

    def test_exact_immutable_manifest_and_all_literal_bytes(self):
        raw = (ROOT / "manifest.json").read_bytes()
        self.assertEqual(len(raw), 182368)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), MANIFEST_SHA256)
        self.assertEqual(raw, canonical(self.manifest))
        self.assertEqual(licenses.DEPENDENCY_MANIFEST_SHA256, MANIFEST_SHA256)
        files = self.manifest["input_files"]
        self.assertEqual(len(files), 51)
        self.assertEqual(sum(row["bytes"] for row in files), 176605)
        self.assertEqual({p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file()},
                         {"manifest.json", *(r["path"] for r in files)})
        for row in files:
            data = (ROOT / row["path"]).read_bytes()
            self.assertEqual(len(data), row["bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
        guide = (PACKAGING.parent / "docs/dependency-sources.md").read_bytes()
        self.assertEqual(len(guide), 22049)
        self.assertEqual(hashlib.sha256(guide).hexdigest(), GUIDE_SHA256)

    def test_all_source_attributions_and_unresolved_rows_are_preserved(self):
        proposal = self.manifest["proposal"]
        digest = hashlib.sha256(json.dumps(proposal, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, PROPOSAL_SHA256)
        self.assertEqual(len(proposal["components"]), 55)
        missing = [i for i, c in enumerate(proposal["components"])
                   if "LicenseFile" not in c["source"]["component"]]
        self.assertEqual(len(missing), 11)
        self.assertEqual({proposal["components"][i]["source"]["component"]["Id"] for i in missing}, MISSING_IDS)
        self.assertEqual(self.manifest["unresolved_coverage"]["missing_license_file_record_indices"], missing)
        self.assertEqual(self.manifest["unresolved_coverage"]["missing_license_file_status"],
                         "historical metadata-key observation only; not proof of a legal omission")
        self.assertEqual(self.manifest["unresolved_coverage"]["remaining_text_review_record_indices"], [14])
        self.assertTrue(all(c["source"]["compiled_into_candidate"] == "unverified" for c in proposal["components"]))
        self.assertEqual([c["source"]["component"]["Id"] for c in proposal["components"]
                          if "Copyright" not in c["source"]["component"]], ["libjpeg"])
        self.assertEqual({s["name"]: (s["bytes"], s["sha256"]) for s in self.manifest["source_archives"]}, ARCHIVES)
        self.assertEqual(sum(s["bytes"] for s in self.manifest["source_archives"]), 73622536)
        llvm = self.manifest["llvm_provenance"]
        self.assertEqual(llvm["commit"], "ee46064b83e7acd5a215ea5bb1c9708d75de6164")
        self.assertEqual(llvm["sha256"], "e3bc36440fc927c62d5cc24efeefe225a14d4e34ffeb0c92e430625cce9ee444")
        self.assertIn("not recovery", llvm["qualification"])
        self.assertIn("unsigned", llvm["tag_verification"])

    def test_complete_output_preserves_inventory_api_metadata_and_other_notices(self):
        installed = self.root / "installed"
        installed.mkdir()
        (installed / "NOTICE.txt").write_bytes(b"fixture NumPy bundled notice\n")
        ds = [distribution("numpy", "2.5.3", installed, ["NOTICE.txt"]),
              distribution("pyserial", "3.5"), distribution("PySide6_Essentials", "6.12.0"),
              distribution("shiboken6", "6.12.0")]
        inventory = self.collect(ds)
        self.assertEqual(len(inventory), 4)
        fields = {"name", "version", "license_expression", "license_metadata", "copied_notices",
                  "vendored_notices", "manual_review_required"}
        for row in inventory:
            self.assertEqual(set(row), fields)
            self.assertEqual(row["license_expression"], "fixture expression")
            self.assertEqual(row["license_metadata"], "fixture license")
            self.assertIs(row["manual_review_required"], True)
        qt = [r for r in inventory if r["version"] == "6.12.0"]
        self.assertEqual(len(qt), 2)
        expected_files = {"manifest.json", "dependency-sources.md", *(r["path"] for r in self.manifest["input_files"])}
        output = self.destination / "dependency-6.12.0"
        self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}, expected_files)
        for row in qt:
            self.assertEqual(len(row["vendored_notices"]), 56)
            notices = [n for n in row["vendored_notices"] if n["path"].startswith("dependency-6.12.0/")]
            self.assertEqual(len(notices), 53)
            for n in notices:
                self.assertIn(n["path"], row["copied_notices"])
                data = (self.destination / n["path"]).read_bytes()
                self.assertEqual(len(data), n["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), n["sha256"])
                self.assertEqual(n["scope"], "shared-source-attribution-supplement-not-compiled-coverage")
        self.assertEqual((output / "manifest.json").read_bytes(), (ROOT / "manifest.json").read_bytes())
        self.assertEqual((output / "dependency-sources.md").read_bytes(), licenses.DEPENDENCY_GUIDE.read_bytes())
        self.assertEqual((self.destination / "numpy/NOTICE.txt").read_bytes(), b"fixture NumPy bundled notice\n")
        self.assertEqual((self.destination / "CPython-LICENSE.txt").read_bytes(), b"fixture CPython notice\n")

    def test_deterministic_output_and_no_network_or_execution(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("Offline collector may not access network or execute vendor code")
        with ExitStack() as stack:
            for module, name in ((socket, "socket"), (socket, "create_connection"),
                                 (urllib.request, "urlopen"), (subprocess, "Popen")):
                stack.enter_context(patch.object(module, name, side_effect=forbidden))
            stack.enter_context(patch.dict(sys.modules, {"PySide6": None, "shiboken6": None}))
            self.collect()
            first = {p.relative_to(self.destination).as_posix(): p.read_bytes()
                     for p in self.destination.rglob("*") if p.is_file()}
            self.destination = self.root / "second"
            self.collect([distribution("pyserial", "3.5"), distribution("shiboken6", "6.12.0"),
                          distribution("PySide6_Essentials", "6.12.0")])
            second = {p.relative_to(self.destination).as_posix(): p.read_bytes()
                      for p in self.destination.rglob("*") if p.is_file()}
        self.assertEqual(first, second)

    def test_each_missing_and_tampered_literal_fails_without_success_inventory(self):
        vendor = self.root / "vendor"
        shutil.copytree(ROOT, vendor)
        with patch.object(licenses, "DEPENDENCY_LICENSES", vendor):
            for i, row in enumerate(self.manifest["input_files"]):
                path = vendor / row["path"]
                original = path.read_bytes()
                for kind in ("missing", "size", "hash"):
                    self.destination = self.root / f"bad-{i}-{kind}"
                    with self.subTest(path=row["path"], kind=kind):
                        if kind == "missing":
                            path.unlink()
                        else:
                            path.write_bytes(original + b"x" if kind == "size" else bytes([original[0] ^ 1]) + original[1:])
                        self.rejected()
                        self.assertFalse((self.destination / "dependency-6.12.0").exists())
                        path.write_bytes(original)

    def test_missing_tampered_manifest_and_guide_fail(self):
        for name, constant, original in (("manifest.json", "DEPENDENCY_LICENSES", ROOT / "manifest.json"),
                                         ("guide.md", "DEPENDENCY_GUIDE", licenses.DEPENDENCY_GUIDE)):
            for kind in ("missing", "size", "hash"):
                self.destination = self.root / (name + kind)
                altered = self.root / ("input-" + name + kind)
                if name == "manifest.json":
                    altered.mkdir()
                    target = altered / name
                else:
                    target = altered
                data = original.read_bytes()
                if kind != "missing":
                    target.write_bytes(data + b"x" if kind == "size" else bytes([data[0] ^ 1]) + data[1:])
                with self.subTest(name=name, kind=kind), patch.object(licenses, constant, altered):
                    self.rejected()

    def test_extra_case_alias_directory_and_nonregular_inputs_fail(self):
        for i, kind in enumerate(("extra", "alias", "directory", "file-as-directory")):
            vendor = self.root / f"vendor-{i}"
            shutil.copytree(ROOT, vendor)
            first = vendor / self.manifest["input_files"][0]["path"]
            if kind == "extra":
                (vendor / "extra.txt").write_bytes(b"x")
            elif kind == "alias":
                alias = first.with_name(first.name.upper())
                if alias.exists():
                    # Case-insensitive filesystems cannot store this alias; a
                    # differently cased replacement still fails exact paths.
                    first.rename(first.with_name("temporary"))
                    (first.with_name("temporary")).rename(alias)
                else:
                    alias.write_bytes(first.read_bytes())
            elif kind == "directory":
                (vendor / "extra").mkdir()
            else:
                first.unlink()
                first.mkdir()
            self.destination = self.root / f"out-{i}"
            with self.subTest(kind=kind), patch.object(licenses, "DEPENDENCY_LICENSES", vendor):
                self.rejected()

    def test_linked_leaf_root_and_ancestor_rejected(self):
        vendor = self.root / "vendor"
        shutil.copytree(ROOT, vendor)
        first = vendor / self.manifest["input_files"][0]["path"]
        outside = self.root / "outside"
        outside.write_bytes(first.read_bytes())
        first.unlink()
        try:
            first.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"Host cannot create symlinks: {exc}")
        with patch.object(licenses, "DEPENDENCY_LICENSES", vendor):
            self.rejected()
        link = self.root / "linked-vendor"
        link.symlink_to(ROOT, target_is_directory=True)
        self.destination = self.root / "out-root"
        with patch.object(licenses, "DEPENDENCY_LICENSES", link):
            self.rejected()
        ancestor = self.root / "linked-ancestor"
        ancestor.symlink_to(ROOT.parent, target_is_directory=True)
        self.destination = self.root / "out-ancestor"
        with patch.object(licenses, "DEPENDENCY_LICENSES", ancestor / ROOT.name):
            self.rejected()

    def test_windows_reparse_flag_is_rejected(self):
        real_lstat = Path.lstat
        def reparse(path, *args, **kwargs):
            if path == ROOT:
                return SimpleNamespace(st_file_attributes=0x400, st_mode=real_lstat(path).st_mode)
            return real_lstat(path, *args, **kwargs)
        with patch.object(Path, "lstat", reparse):
            self.rejected()

    def test_duplicate_keys_and_noncanonical_json_fail_below_hash_boundary(self):
        transforms = [lambda raw: raw[:-2] + b', "schema_version": 1}\n',
                      lambda raw: raw + b"\n", lambda raw: b"\xef\xbb\xbf" + raw,
                      lambda raw: raw.replace(b'"qt_version": "6.12.0"', b'"qt_version":"6.12.0"')]
        for i, transform in enumerate(transforms):
            self.destination = self.root / str(i)
            with self.subTest(i=i), self.changed_manifest(transform=transform):
                self.rejected()

    def test_strict_schema_types_counts_versions_and_coverage_fail(self):
        mutations = [lambda d: d.update(schema_version=True), lambda d: d.update(schema_version=3),
                     lambda d: d.update(qt_version="6.12.1"), lambda d: d.update(extra=True),
                     lambda d: d.pop("provenance"), lambda d: d.update(input_files={}),
                     lambda d: d["input_files"].pop(),
                     lambda d: d["proposal"].update(literal_file_count=True),
                     lambda d: d["proposal"].update(literal_bytes=163527),
                     lambda d: d["proposal"]["components"].pop(),
                     lambda d: d["proposal"]["components"][0]["source"].update(compiled_into_candidate=True),
                     lambda d: d["unresolved_coverage"].update(missing_license_file_record_indices=[]),
                     lambda d: d["unresolved_coverage"].update(missing_license_file_status="complete")]
        for i, mutation in enumerate(mutations):
            self.destination = self.root / str(i)
            with self.subTest(i=i), self.changed_manifest(mutation):
                self.rejected()

    def test_unsafe_duplicate_case_alias_hash_and_size_entries_fail(self):
        cases = [("path", value) for value in ("../escape", "/tmp/escape", "C:/escape", "notices/../escape",
                                              "notices\\escape", "notices/NUL", "notices/x:stream", "notices/x.", None)]
        cases += [("bytes", value) for value in (True, 0, -1, 163527, "1")]
        cases += [("sha256", value) for value in (None, "A" * 64, "a" * 63, "x" * 64)]
        for i, (key, value) in enumerate(cases):
            self.destination = self.root / str(i)
            with self.subTest(key=key, value=value), self.changed_manifest(
                    lambda d: d["input_files"][0].update({key: value})):
                self.rejected()
        for i, mutation in enumerate((lambda d: d["input_files"].__setitem__(1, dict(d["input_files"][0])),
                                       lambda d: d["input_files"][0].update(path=d["input_files"][0]["path"].upper()),
                                       lambda d: d["input_files"].reverse(),
                                       lambda d: d["proposal"]["components"].__setitem__(1, d["proposal"]["components"][0]))):
            self.destination = self.root / f"dup-{i}"
            with self.changed_manifest(mutation):
                self.rejected()

    def test_output_collision_case_alias_and_existing_output_are_preserved(self):
        installed = self.root / "installed"
        installed.mkdir()
        (installed / "notice.txt").write_bytes(b"existing distribution notice\n")
        for i, name in enumerate(("dependency-6.12.0", "DEPENDENCY-6.12.0")):
            self.destination = self.root / str(i)
            ds = [distribution("pyserial", "3.5"), distribution("PySide6_Essentials", "6.12.0"),
                  distribution("shiboken6", "6.12.0"), distribution(name, "1", installed, ["notice.txt"])]
            with self.assertRaises(FileExistsError):
                self.collect(ds)
            self.assertFalse((self.destination / "inventory.json").exists())
            self.assertEqual((self.destination / name / "notice.txt").read_bytes(), b"existing distribution notice\n")
        self.destination = self.root / "already-there"
        self.destination.mkdir()
        sentinel = self.destination / "inventory.json"
        sentinel.write_bytes(b"original")
        with patch.object(licenses.metadata, "distributions") as read, self.assertRaises(FileExistsError):
            licenses.collect(self.destination)
        read.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"original")

    def test_late_copy_failure_cannot_produce_inventory(self):
        original_open = Path.open
        def failing_open(path, *args, **kwargs):
            if path == self.destination / "dependency-6.12.0/manifest.json":
                raise PermissionError("intentional supplement write failure")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", failing_open), self.assertRaises(PermissionError):
            self.collect()
        self.assertFalse((self.destination / "inventory.json").exists())
        self.assertTrue((self.destination / "dependency-6.12.0/dependency-sources.md").exists())

    def test_each_qt_version_mismatch_still_fails(self):
        for i, name in enumerate(("PySide6_Essentials", "shiboken6")):
            self.destination = self.root / str(i)
            ds = [distribution("pyserial", "3.5"), distribution("PySide6_Essentials", "6.12.0"),
                  distribution("shiboken6", "6.12.0")]
            next(d for d in ds if d.metadata["Name"] == name).version = "6.12.1"
            with self.assertRaisesRegex(RuntimeError, "only cover"):
                self.collect(ds)
            self.assertFalse((self.destination / "inventory.json").exists())


    def test_reconciliation_preserves_sealed_rows_and_selected_body_provenance(self):
        rec = self.manifest["reconciliation"]
        compact = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(compact(rec["rows"])).hexdigest(),
                         "378b85ddbfb30dcf3e907b34aa266b775ba9b9c78deafb30dea873756a381bd4")
        self.assertEqual(hashlib.sha256(compact(rec["additional_bodies"])).hexdigest(),
                         "c3ce22cf3dca131d26d841bbe29d67a7357e6a87ddfb9ca264b7db99f3a00c3e")
        self.assertEqual(rec["ledger"]["sha256"], "5e73b2607fc1c67d3d9712237c1de114785e433be455f9bde87ae622e27906d7")
        self.assertEqual(rec["target_commit"], "60c3d262b5597794869b32e0aa27d6981b28e38b")
        self.assertEqual(rec["target_tree"], "b276c2300a539876b0283ec3c515bd4ec4a5f398")
        self.assertEqual(len(rec["additional_bodies"]), 10)
        self.assertEqual(sum(b["bytes"] for b in rec["additional_bodies"]), 13079)
        old = {r["sha256"]: r for r in self.manifest["proposal"]["literal_files"]}
        current = {r["sha256"]: r for r in self.manifest["input_files"]}
        for sha, row in old.items():
            self.assertEqual(current[sha], {"path": "notices/" + sha, "sha256": sha,
                                           "bytes": row["bytes"], "source_path": row["path"]})
        self.assertEqual({r["record_index"] for r in rec["rows"] if r["status"] == "reuse_existing_literal"},
                         {13, 16, 21})
        for row in rec["rows"]:
            for reused in row["reuse_existing_bodies"]:
                self.assertEqual(reused, current[reused["sha256"]])
        self.assertEqual(rec["remaining_text_review"]["record_index"], 14)
        self.assertEqual(rec["remaining_text_review"]["status"], "unresolved")

    def test_plural_license_and_copyright_keys_map_without_rewriting_original(self):
        source = self.manifest["proposal"]["components"][47]["source"]
        self.assertNotIn("LicenseFile", source["component"])
        self.assertNotIn("Copyright", source["component"])
        self.assertEqual(source["component"]["LicenseFiles"], ["LICENSE", "ijg-license.txt"])
        self.assertEqual(source["component"]["CopyrightFile"], "COPYRIGHT.txt")
        paths = licenses._metadata_notice_paths(source)
        prefix = "qtbase-everywhere-src-6.12.0/src/3rdparty/libjpeg/"
        self.assertEqual(paths, [prefix + f for f in ("LICENSE", "ijg-license.txt", "COPYRIGHT.txt")])
        bodies = self.manifest["reconciliation"]["additional_bodies"]
        jpeg = [b for b in bodies if b["source"]["path"] in paths]
        self.assertEqual(len(jpeg), 3)
        self.assertEqual(sum(b["bytes"] for b in jpeg), 8498)
        self.assertTrue(all(b["selection"] == "entire original file" for b in jpeg))
        self.assertIn(47, self.manifest["unresolved_coverage"]["missing_license_file_record_indices"])
        self.assertNotIn(47, self.manifest["unresolved_coverage"]["records_without_notice_file_fields"])
        guide = licenses.DEPENDENCY_GUIDE.read_text(encoding="utf-8")
        self.assertIn("This software is based in part on the work of the Independent JPEG Group.", guide)

    def test_header_declarations_and_tika_excerpt_boundaries_are_explicit(self):
        bodies = {b["name"]: b for b in self.manifest["reconciliation"]["additional_bodies"]}
        self.assertEqual(bodies["md4-public-domain-header.txt"]["source_byte_range_half_open"], [0, 697])
        self.assertEqual(bodies["md5-public-domain-header.txt"]["source_byte_range_half_open"], [0, 909])
        self.assertEqual(bodies["wintab-copyright-permission.txt"]["source_byte_range_half_open"], [118, 473])
        self.assertEqual(bodies["tika-ASF-NOTICE.txt"]["source_byte_range_half_open"], [0, 161])
        self.assertEqual(bodies["tika-PRONOM-NOTICE.txt"]["source_byte_range_half_open"], [743, 1063])
        self.assertIn("not relabel", bodies["md4-public-domain-header.txt"]["context"])
        for name in ("tika-ASF-NOTICE.txt", "tika-PRONOM-NOTICE.txt"):
            self.assertEqual(bodies[name]["source"]["sha256"],
                             "4cd09bac8d81bd34decf6b95fb23d42fc9b3d6b653ec4be8ef44674b692c97cd")
            self.assertEqual(bodies[name]["source"]["bytes"], 80600)
        inventory = self.collect()
        record = next(r for r in inventory if r["name"] == "PySide6_Essentials")
        for name in ("tika-ASF-NOTICE.txt", "tika-PRONOM-NOTICE.txt"):
            body = bodies[name]
            notice = next(n for n in record["vendored_notices"] if n["sha256"] == body["sha256"])
            self.assertEqual(notice["source_url"], body["source"]["requested_url"])
            self.assertEqual(notice["version"], "c29724782854bb5a319f4a1940d0828a58ace3f9")
            start, end = body["source_byte_range_half_open"]
            self.assertIn(f"[{start}, {end})", notice["reason"])

    def test_metadata_key_types_duplicates_and_archive_escapes_fail(self):
        import copy
        source = self.manifest["proposal"]["components"][47]["source"]
        cases = [lambda c: c.update(LicenseFiles="LICENSE"), lambda c: c.update(LicenseFiles=[]),
                 lambda c: c.update(LicenseFiles=[True]), lambda c: c.update(CopyrightFile=["COPYRIGHT.txt"]),
                 lambda c: c.update(LicenseFile=None), lambda c: c.update(LicenseFile="LICENSE"),
                 lambda c: c.update(LicenseFiles=["LICENSE", "license"])]
        for name in ("/LICENSE", "C:/LICENSE", "a\\LICENSE", "LICENSE:stream", "a//b", "a/./b", "x.",
                     "x ", "../../../../outside", "x\x00", "x\n", "x?", ""):
            cases.append(lambda c, value=name: c.update(LicenseFiles=[value]))
        for mutate in cases:
            candidate = copy.deepcopy(source)
            mutate(candidate["component"])
            with self.subTest(component=candidate["component"]), self.assertRaises(RuntimeError):
                licenses._metadata_notice_paths(candidate)
        # A legitimate relative reference inside its archive is not an escape.
        grayraster = self.manifest["proposal"]["components"][22]["source"]
        self.assertEqual(licenses._metadata_notice_paths(grayraster),
                         ["qtbase-everywhere-src-6.12.0/src/3rdparty/freetype/LICENSE.txt"])

    def test_reconciliation_schema_duplicate_mapping_and_bounds_fail_closed(self):
        def body(d):
            return d["reconciliation"]["additional_bodies"][0]
        def row(d):
            return d["reconciliation"]["rows"][0]
        mutations = [lambda d: d.update(schema_version=1), lambda d: d.pop("reconciliation"),
                     lambda d: d["reconciliation"].update(extra=True),
                     lambda d: d["reconciliation"].update(additional_bytes=True),
                     lambda d: d["reconciliation"].update(additional_bytes=13080),
                     lambda d: d["reconciliation"]["additional_bodies"].pop(),
                     lambda d: d["reconciliation"]["rows"].pop(),
                     lambda d: d["reconciliation"]["additional_bodies"].__setitem__(1, body(d)),
                     lambda d: d["reconciliation"]["rows"].__setitem__(1, row(d)),
                     lambda d: body(d).update(bytes=True),
                     lambda d: body(d)["source"].update(bytes=True),
                     lambda d: body(d)["source"].update(sha256="x" * 64),
                     lambda d: body(d).update(selection="rewritten"),
                     lambda d: body(d).update(selection="entire original file"),
                     lambda d: body(d)["source"].update(path="../outside"),
                     lambda d: row(d).update(record_index=True),
                     lambda d: row(d).update(id="md5"),
                     lambda d: row(d).update(status="complete"),
                     lambda d: row(d).update(compiled_inclusion="proved"),
                     lambda d: row(d).update(proposed_new_bodies=[]),
                     lambda d: row(d)["proposed_new_bodies"].append(row(d)["proposed_new_bodies"][0]),
                     lambda d: d["proposal"]["components"][47]["source"]["component"].update(CopyrightFile="other.txt"),
                     lambda d: d["reconciliation"]["rows"][3]["reuse_existing_bodies"].append(
                         d["reconciliation"]["rows"][3]["reuse_existing_bodies"][0])]
        for bounds in (None, [0], [False, 697], [-1, 696], [0, 698], [1, 0], [6594, 7291], [0, "697"]):
            mutations.append(lambda d, value=bounds: body(d).update(source_byte_range_half_open=value))
        for i, mutation in enumerate(mutations):
            self.destination = self.root / str(i)
            with self.subTest(case=i), self.changed_manifest(mutation):
                self.rejected()

    def test_tika_ogl_review_cannot_be_silently_closed(self):
        mutations = [lambda d: d["unresolved_coverage"].update(remaining_text_review_record_indices=[]),
                     lambda d: d["unresolved_coverage"].update(records_without_notice_file_fields=[]),
                     lambda d: d["reconciliation"]["remaining_text_review"].update(status="complete"),
                     lambda d: d["reconciliation"]["remaining_text_review"].update(status=False),
                     lambda d: d["reconciliation"]["remaining_text_review"].update(record_index=47),
                     lambda d: d["reconciliation"]["remaining_text_review"].update(observed_read_status="verified"),
                     lambda d: d["reconciliation"]["remaining_text_review"].update(url="https://example.invalid/")]
        for i, mutation in enumerate(mutations):
            self.destination = self.root / str(i)
            with self.subTest(case=i), self.changed_manifest(mutation):
                self.rejected()

    def test_current_collector_failure_blocks_recipe_later_stages(self):
        from test_serial_packaging import build
        commands = []
        output = self.root / "recipe"
        def run(command, **kwargs):
            command = [str(part) for part in command]
            commands.append(command)
            if "--format=json" in command:
                return "[]"
            if "PyInstaller" in command:
                bundle = output / "dist/Motortester"
                bundle.mkdir(parents=True)
                (bundle / "Motortester.exe").write_bytes(b"non-executable recipe fixture")
            if any(part.endswith("collect_licenses.py") for part in command):
                self.destination = Path(command[-1])
                with patch.object(licenses, "DEPENDENCY_GUIDE", self.root / "missing-guide"):
                    try:
                        self.collect()
                    except OSError as error:
                        raise subprocess.CalledProcessError(1, command) from error
                self.fail("Actual collector accepted its missing pinned guide")
            return ""
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["build_windows.py", "--output", str(output)]))
            stack.enter_context(patch.object(build, "sys", SimpleNamespace(
                argv=["build_windows.py", "--output", str(output)], platform="win32",
                maxsize=2**63 - 1, version_info=(3, 13, 16), executable="fake-python")))
            stack.enter_context(patch.object(build.platform, "machine", return_value="AMD64"))
            stack.enter_context(patch.object(build.sysconfig, "get_config_var", return_value=None))
            stack.enter_context(patch.object(build.subprocess, "check_output", side_effect=["fixture-revision", ""]))
            stack.enter_context(patch.object(build, "run_logged", side_effect=run))
            serial_gate = stack.enter_context(patch.object(build, "run_frozen_serial_smoke", return_value={}))
            recording_gate = stack.enter_context(patch.object(build, "run_frozen_recording_smoke"))
            archive = stack.enter_context(patch.object(build.shutil, "make_archive"))
            with self.assertRaises(subprocess.CalledProcessError):
                build.main()
            serial_gate.assert_called_once()
            recording_gate.assert_not_called()
            archive.assert_not_called()
        self.assertTrue(any(any(part.endswith("collect_licenses.py") for part in command) for command in commands))
        self.assertFalse(any("--screenshot" in command for command in commands))
        self.assertFalse((self.destination / "inventory.json").exists())
        self.assertFalse((output / "SHA256SUMS.txt").exists())
        self.assertFalse((output / "dist/Motortester/build-manifest.json").exists())
        self.assertFalse((output / "dist/Motortester/CANDIDATE.txt").exists())



if __name__ == "__main__":
    unittest.main()

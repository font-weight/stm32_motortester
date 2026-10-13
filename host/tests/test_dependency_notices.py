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
MANIFEST_SHA256 = "7166c136548231e009b028caaa8b32f57967cebe05c8899e6669726e1785c084"
PROPOSAL_SHA256 = "486c978af0fa92eb5b63eee9e03c30a82d40abdb841efd3e2e9e97303e20385b"
GUIDE_SHA256 = "f0f4ef563eec85e3757e44e19099ce144377864d332eb0e3659f3948a0d4b026"
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
        self.assertEqual(len(raw), 142332)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), MANIFEST_SHA256)
        self.assertEqual(raw, canonical(self.manifest))
        self.assertEqual(licenses.DEPENDENCY_MANIFEST_SHA256, MANIFEST_SHA256)
        files = self.manifest["input_files"]
        self.assertEqual(len(files), 41)
        self.assertEqual(sum(row["bytes"] for row in files), 163526)
        self.assertEqual({p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file()},
                         {"manifest.json", *(r["path"] for r in files)})
        for row in files:
            data = (ROOT / row["path"]).read_bytes()
            self.assertEqual(len(data), row["bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
        guide = (PACKAGING.parent / "docs/dependency-sources.md").read_bytes()
        self.assertEqual(len(guide), 16721)
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
        self.assertTrue(self.manifest["unresolved_coverage"]["missing_license_file_status"].startswith("unresolved:"))
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
            self.assertEqual(len(row["vendored_notices"]), 46)
            notices = [n for n in row["vendored_notices"] if n["path"].startswith("dependency-6.12.0/")]
            self.assertEqual(len(notices), 43)
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
        mutations = [lambda d: d.update(schema_version=True), lambda d: d.update(schema_version=2),
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


if __name__ == "__main__":
    unittest.main()

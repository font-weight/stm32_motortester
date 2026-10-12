"""Pinned notice/source-fake checks; NOT a Windows or redistribution audit."""
from contextlib import ExitStack
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
spec = importlib.util.spec_from_file_location("qt_licenses_under_test", PACKAGING / "collect_licenses.py")
licenses = importlib.util.module_from_spec(spec)
spec.loader.exec_module(licenses)

# Independently copied from the immutable upstream evidence, not calculated from
# the implementation's own constants or inferred from the current local files.
EXPECTED = {
    "GPL-2.0-only.txt": (18092, "8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643",
                         "d159169d1050894d3ea3b98e1c965c4058208fe1"),
    "GPL-3.0-only.txt": (35147, "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
                         "94a9ed024d3859793618152ea559a168bbcbb5e2"),
    "LGPL-3.0-only.txt": (7651, "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768",
                          "65c5ca88a67c30becee01c5a8816d964b03862f9"),
}
SOURCE_COMMIT = "f50facb83fc3dc5f3240c854668c7a6dd93871b2"
WHEELS = {
    "pyside6-essentials": (
        "pyside6_essentials-6.12.0-cp310-abi3-win_amd64.whl",
        "c9a95102aa23c1f86516a30d5a9be37552d324eee7e567eb7508973f1218cb42",
    ),
    "shiboken6": (
        "shiboken6-6.12.0-cp310-abi3-win_amd64.whl",
        "a1906cb8116869178c64b6bab52623bbc8b4ceed7b30bfc3c9aa0f6e67f6438e",
    ),
}


def distribution(name, version, root=None, files=()):
    return SimpleNamespace(metadata={"Name": name, "License": "fixture metadata"},
                           version=version, files=files,
                           locate_file=lambda path: root / path)


def required_distributions():
    return [distribution("pyserial", "3.5"),
            distribution("PySide6_Essentials", "6.12.0"),
            distribution("shiboken6", "6.12.0")]


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="notice тест ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "python runtime"
        self.runtime.mkdir()
        (self.runtime / "LICENSE.txt").write_bytes(b"fixture CPython notice, not actual runtime evidence\n")
        self.destination = self.root / "output лицензии"

    def collect(self, distributions=None):
        if distributions is None:
            distributions = required_distributions()
        with patch.object(licenses.metadata, "distributions", return_value=iter(distributions)), \
                patch.object(licenses, "sys", SimpleNamespace(base_prefix=str(self.runtime))):
            licenses.collect(self.destination)
        return json.loads((self.destination / "inventory.json").read_text(encoding="utf-8"))

    def assert_no_inventory(self):
        self.assertFalse((self.destination / "inventory.json").exists())

    def test_exact_upstream_bytes_and_git_blobs(self):
        self.assertEqual({item[0]: item[1:] for item in licenses.QT_NOTICES}, EXPECTED)
        self.assertEqual(licenses.QT_NOTICE_SOURCE_COMMIT, SOURCE_COMMIT)
        self.assertEqual(licenses.QT_DISTRIBUTIONS, WHEELS)
        for filename, (size, sha256, git_blob) in EXPECTED.items():
            with self.subTest(filename=filename):
                data = (PACKAGING / "licenses" / "qt-6.12.0" / filename).read_bytes()
                self.assertEqual(len(data), size)
                self.assertEqual(hashlib.sha256(data).hexdigest(), sha256)
                self.assertEqual(hashlib.sha1(b"blob " + str(size).encode("ascii") + b"\0" + data).hexdigest(),
                                 git_blob)
        lock = (PACKAGING / "requirements-win-x64.lock").read_text()
        for wheel, sha256 in WHEELS.values():
            self.assertIn("# " + wheel, lock)
            self.assertIn("--hash=sha256:" + sha256, lock)

    def test_complete_supplement_has_truthful_provenance_and_retains_other_notices(self):
        source = self.root / "installed"
        source.mkdir()
        (source / "LicenseRef-Qt-Commercial.txt").write_bytes(b"fixture commercial reference\n")
        distributions = required_distributions()
        distributions[1] = distribution("PySide6_Essentials", "6.12.0", source,
                                         ["LicenseRef-Qt-Commercial.txt"])
        inventory = self.collect(distributions)
        self.assertEqual(len(inventory), 3)
        self.assertEqual(len(list((self.destination / "qt-6.12.0").iterdir())), 3)
        for record in inventory:
            self.assertIs(record["manual_review_required"], True)
            name = licenses._distribution_name(record["name"])
            if name == "pyserial":
                self.assertEqual(len(record["vendored_notices"]), 1)
                notice = record["vendored_notices"][0]
                self.assertEqual((self.destination / notice["path"]).read_bytes(),
                                 licenses.PYSERIAL_LICENSE.read_bytes())
                continue
            self.assertEqual(record["version"], "6.12.0")
            self.assertEqual(len(record["vendored_notices"]), 3)
            for notice in record["vendored_notices"]:
                filename = Path(notice["path"]).name
                size, sha256, git_blob = EXPECTED[filename]
                self.assertEqual(notice["path"], "qt-6.12.0/" + filename)
                self.assertIn(notice["path"], record["copied_notices"])
                self.assertEqual(notice["source_url"],
                                 f"https://raw.githubusercontent.com/pyside/pyside-setup/{SOURCE_COMMIT}/LICENSES/{filename}")
                self.assertEqual(notice["source_commit"], SOURCE_COMMIT)
                self.assertEqual(notice["source_git_blob"], git_blob)
                self.assertEqual(notice["sha256"], sha256)
                self.assertEqual(notice["bytes"], size)
                self.assertEqual(notice["version"], "6.12.0")
                self.assertEqual(notice["scope"], "upstream-license-text-only")
                self.assertEqual(notice["reference_wheel"], WHEELS[name][0])
                self.assertEqual(notice["reference_wheel_sha256"], WHEELS[name][1])
                self.assertEqual((self.destination / notice["path"]).read_bytes(),
                                 (PACKAGING / "licenses" / "qt-6.12.0" / filename).read_bytes())
        self.assertEqual((self.destination / "PySide6_Essentials" / "LicenseRef-Qt-Commercial.txt").read_bytes(),
                         b"fixture commercial reference\n")
        self.assertEqual((self.destination / "CPython-LICENSE.txt").read_bytes(),
                         (self.runtime / "LICENSE.txt").read_bytes())

    def test_distribution_normalization_preserves_reported_names(self):
        for index, name in enumerate(("pyside6-essentials", "PYSIDE6_ESSENTIALS", "PySide6...Essentials")):
            with self.subTest(name=name):
                self.destination = self.root / str(index)
                distributions = required_distributions()
                distributions[1].metadata["Name"] = name
                distributions[2].metadata["Name"] = "SHIBOKEN6"
                inventory = self.collect(distributions)
                self.assertEqual({r["name"] for r in inventory}, {"pyserial", name, "SHIBOKEN6"})
                self.assertTrue(all(len(r["vendored_notices"]) == 3
                                    for r in inventory if r["name"] != "pyserial"))

    def test_each_missing_qt_distribution_fails_without_inventory(self):
        for index in (1, 2):
            with self.subTest(index=index):
                self.destination = self.root / str(index)
                distributions = required_distributions()
                del distributions[index]
                with self.assertRaisesRegex(RuntimeError, "distribution is absent"):
                    self.collect(distributions)
                self.assert_no_inventory()

    def test_each_wrong_qt_version_fails_without_inventory(self):
        for index in (1, 2):
            for ordinal, version in enumerate(("6.12.1", "6.12", "6.12.0rc1", "", None, 6120)):
                with self.subTest(index=index, version=version):
                    self.destination = self.root / f"{index}-{ordinal}"
                    distributions = required_distributions()
                    distributions[index].version = version
                    with self.assertRaisesRegex(RuntimeError, "only cover"):
                        self.collect(distributions)
                    self.assert_no_inventory()

    def test_duplicate_canonical_qt_distributions_fail_without_inventory(self):
        for index, duplicate in enumerate(("PySide6_Essentials", "pyside6-essentials", "PYSIDE6..ESSENTIALS",
                                           "shiboken6", "SHIBOKEN6")):
            with self.subTest(duplicate=duplicate):
                self.destination = self.root / str(index)
                with self.assertRaisesRegex(RuntimeError, "Duplicate Qt-family distribution"):
                    self.collect(required_distributions() + [distribution(duplicate, "6.12.0")])
                self.assert_no_inventory()

    def test_each_missing_notice_fails_without_inventory(self):
        for index, filename in enumerate(EXPECTED):
            with self.subTest(filename=filename):
                vendor = self.root / f"vendor-{index}"
                shutil.copytree(licenses.QT_LICENSES, vendor)
                (vendor / filename).unlink()
                self.destination = self.root / str(index)
                with patch.object(licenses, "QT_LICENSES", vendor), self.assertRaises(OSError):
                    self.collect()
                self.assert_no_inventory()

    def test_each_modified_notice_fails_for_size_and_same_size_corruption(self):
        for index, filename in enumerate(EXPECTED):
            for kind in ("length", "same-size"):
                with self.subTest(filename=filename, corruption=kind):
                    vendor = self.root / f"vendor-{index}-{kind}"
                    shutil.copytree(licenses.QT_LICENSES, vendor)
                    target = vendor / filename
                    data = target.read_bytes()
                    target.write_bytes(data + b"changed" if kind == "length" else bytes([data[0] ^ 1]) + data[1:])
                    self.destination = self.root / f"{index}-{kind}"
                    with patch.object(licenses, "QT_LICENSES", vendor), \
                            self.assertRaisesRegex(RuntimeError, "size/SHA-256 mismatch"):
                        self.collect()
                    self.assert_no_inventory()

    def test_existing_destination_is_untouched_before_metadata_read(self):
        self.destination.mkdir()
        sentinel = self.destination / "inventory.json"
        sentinel.write_bytes(b"original")
        with patch.object(licenses.metadata, "distributions") as read, self.assertRaises(FileExistsError):
            licenses.collect(self.destination)
        read.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"original")
        self.assertEqual(list(self.destination.iterdir()), [sentinel])

    def test_existing_pinned_notice_is_never_overwritten(self):
        target = self.destination / "qt-6.12.0" / "GPL-2.0-only.txt"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"concurrent writer")
        with self.assertRaises(FileExistsError):
            licenses._qt_notices(self.destination)
        self.assertEqual(target.read_bytes(), b"concurrent writer")
        self.assert_no_inventory()

    def test_copy_error_cannot_produce_complete_inventory(self):
        original_open = Path.open
        def failing_open(path, *args, **kwargs):
            if path == self.destination / "qt-6.12.0" / "GPL-3.0-only.txt":
                raise PermissionError("intentional notice write failure")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", failing_open), self.assertRaises(PermissionError):
            self.collect()
        self.assert_no_inventory()
        self.assertTrue((self.destination / "qt-6.12.0" / "GPL-2.0-only.txt").is_file())

    def test_pyserial_guards_still_run_with_valid_qt_pair(self):
        for kind in ("absent", "wrong-version", "changed-text"):
            with self.subTest(kind=kind):
                self.destination = self.root / kind
                distributions = required_distributions()
                with ExitStack() as stack:
                    if kind == "absent":
                        del distributions[0]
                        error = "Required pyserial distribution is absent"
                    elif kind == "wrong-version":
                        distributions[0].version = "3.4"
                        error = "only covers version 3.5"
                    else:
                        altered = self.root / "changed-pyserial.txt"
                        altered.write_bytes(licenses.PYSERIAL_LICENSE.read_bytes() + b"changed")
                        stack.enter_context(patch.object(licenses, "PYSERIAL_LICENSE", altered))
                        error = "pyserial notice SHA-256 mismatch"
                    with self.assertRaisesRegex(RuntimeError, error):
                        self.collect(distributions)
                    self.assert_no_inventory()

    def test_cpython_notice_is_still_required(self):
        (self.runtime / "LICENSE.txt").unlink()
        with self.assertRaisesRegex(RuntimeError, "CPython LICENSE.txt missing"):
            self.collect()
        self.assert_no_inventory()

    def test_collection_needs_no_network_subprocess_or_qt_import(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("Collection must be local and import no Qt runtime")
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"PySide6": None, "shiboken6": None}))
            for module, name in ((socket, "socket"), (socket, "create_connection"),
                                 (urllib.request, "urlopen"), (subprocess, "Popen")):
                stack.enter_context(patch.object(module, name, side_effect=forbidden))
            self.assertEqual(len(self.collect()), 3)


if __name__ == "__main__":
    unittest.main()

"""Pure/inert TOC checks, not Windows/frozen or native execution evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
spec = importlib.util.spec_from_file_location("windows_runtime_selection", PACKAGING / "select_windows_runtime.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
KEEPERS = (
    "PySide6/Qt6Network.dll", "PySide6/QtNetwork.pyd",
    "PySide6/plugins/tls/qschannelbackend.dll", "_ssl.pyd", "_hashlib.pyd",
    "libcrypto-3.dll", "libssl-3.dll",
)


def row(name, source=None, kind=None):
    return (name, source or ("C:/rebuilt dependencies/" + name),
            kind or ("EXTENSION" if name.lower().endswith(".pyd") else "BINARY"))


def keepers():
    return [row(name) for name in KEEPERS]


class SelectionTests(unittest.TestCase):
    def test_exact_sealed_47_destinations(self):
        self.assertEqual(len(policy.EXCLUDED_DESTINATIONS), 47)
        self.assertEqual(len(set(policy.EXCLUDED_DESTINATIONS)), 47)
        # Sealed list identity only, never a hash pin on native source bytes.
        payload = ("\n".join(policy.EXCLUDED_DESTINATIONS) + "\n").encode()
        self.assertEqual(hashlib.sha256(payload).hexdigest(),
                         "7957597c771f8d8d450a8ff93a250d56fe9973e2c9429db1228d819f1be88001")
        self.assertEqual(set(policy.REQUIRED_DESTINATIONS), set(KEEPERS))

    def test_all_some_none_removed_and_exact_retention_order_input(self):
        ordinary = [row(name) for name in (
            "PySide6/plugins/tls/qcertonlybackend.dll", "MSVCP140.dll", "PySide6/MSVCP140.dll",
            "vcruntime140.dll", "PySide6/vcruntime140.dll", "numpy.libs/openblas.dll",
            "serial_backend.pyd", "api-ms-win-security-base-l1-1-0.dll",
            "unrelated-ssl-helper.dll", "каталог/модуль.dll")]
        for names in (policy.EXCLUDED_DESTINATIONS, policy.EXCLUDED_DESTINATIONS[::3], ()):
            with self.subTest(present=len(names)):
                retained = keepers() + ordinary
                inputs = retained[:3] + [row(name) for name in names] + retained[3:]
                original = deepcopy(inputs)
                selected, removed = policy.select_windows_runtime(inputs)
                self.assertEqual(selected, retained)
                self.assertEqual(removed, tuple(names))
                self.assertEqual(inputs, original)
                self.assertTrue(all(a is b for a, b in zip(selected, retained)))

    def test_mixed_case_slashes_without_rewriting_retained_rows(self):
        retained = [row(name.upper().replace("/", "\\")) for name in KEEPERS]
        omitted = [row(name.upper().replace("/", "\\"), kind="BINARY")
                   for name in policy.EXCLUDED_DESTINATIONS]
        self.assertEqual(policy.select_windows_runtime(retained + omitted),
                         (retained, policy.EXCLUDED_DESTINATIONS))

    def test_missing_each_required_destination_and_empty_toc_fail(self):
        for missing in KEEPERS:
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "Missing required"):
                policy.select_windows_runtime([item for item in keepers() if item[0] != missing])
        with self.assertRaisesRegex(ValueError, "Missing required"):
            policy.select_windows_runtime([])

    def test_required_dlls_are_binary_and_pyds_allow_both_native_types(self):
        for name in KEEPERS:
            original = keepers()
            index = KEEPERS.index(name)
            original[index] = row(name, kind="EXTENSION")
            if name.endswith(".dll"):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Required DLL"):
                    policy.select_windows_runtime(original)
            else:
                self.assertEqual(policy.select_windows_runtime(original), (original, ()))
                original[index] = row(name, kind="BINARY")
                self.assertEqual(policy.select_windows_runtime(original), (original, ()))

    def test_each_excluded_basename_at_alternate_directory_fails(self):
        for name in policy.EXCLUDED_DESTINATIONS:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unreviewed"):
                policy.select_windows_runtime(keepers() + [row("different/" + name.rsplit("/", 1)[-1])])

    def test_each_exclusion_with_extension_type_fails(self):
        for name in policy.EXCLUDED_DESTINATIONS:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unexpected type"):
                policy.select_windows_runtime(keepers() + [row(name, kind="EXTENSION")])

    def test_unknown_related_families_stop_instead_of_wildcard_deletion(self):
        names = ("api-ms-win-core-future-l1-1-0.dll", "api-ms-win-crt-future-l1-1-0.dll",
                 "ucrtbased.dll", "qopensslbackendd.dll", "libcrypto-4-x64.dll",
                 "libssl-4-x64-debug.dll")
        for name in names:
            for directory in ("", "PySide6/plugins/tls/"):
                with self.subTest(name=directory + name), self.assertRaisesRegex(ValueError, "Unreviewed"):
                    policy.select_windows_runtime(keepers() + [row(directory + name)])

    def test_unrelated_api_families_and_similar_names_stay_unchanged(self):
        names = ("api-ms-win-security-base-l1-1-0.dll", "qt_ssl_support.dll",
                 "qotheropensslbackend.dll", "my-ucrtbase.dll", "libcrypto-4.dll")
        inputs = keepers() + [row(name) for name in names]
        self.assertEqual(policy.select_windows_runtime(inputs), (inputs, ()))

    def test_full_path_collisions_even_identical_rows_fail(self):
        for name in ("libcrypto-3-x64.dll", "PySide6/extra.dll", "other.dll"):
            first = row(name)
            for second in (first, row(name.upper().replace("/", "\\"), "D:/replacement.dll")):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Colliding"):
                    policy.select_windows_runtime(keepers() + [first, second])

    def test_unsafe_destinations_fail_without_cleaning_aliases(self):
        names = ("", ".", "..", "../a.dll", "x/../a.dll", "x/./a.dll", "x//a.dll",
                 "/a.dll", "\\a.dll", "\\\\server\\share\\a.dll", "C:a.dll", "C:/a.dll",
                 "a.dll:stream", "a.dll.", "a.dll ", "x./a.dll", "x /a.dll",
                 "a\x00.dll", "a\n.dll", "a\x7f.dll", "a?.dll", "a*.dll", 'a".dll',
                 "a|.dll", "a<.dll", "a>.dll", "x/", "CON.dll", "COM1/a.dll",
                 "LPT².dll", "CLOCK$.dll", "conin$.dll", "NUL .dll", "CONOUT$.dll")
        for name in names:
            with self.subTest(name=name), self.assertRaises(ValueError):
                policy.select_windows_runtime(keepers() + [row(name)])

    def test_malformed_rows_types_and_sources_fail(self):
        malformed = (None, "other.dll", (), ("a.dll", "source"),
                     ("a.dll", "source", "BINARY", "extra"),
                     (None, "source", "BINARY"), ("a.dll", None, "BINARY"),
                     ("a.dll", "", "BINARY"), ("a.dll", "bad\x00source", "BINARY"),
                     ("a.dll", "source", "DATA"), ("a.dll", "source", "SYMLINK"),
                     ("a.dll", "source", None), ("a.dll", "source", 1))
        for item in malformed:
            with self.subTest(row=item), self.assertRaises(ValueError):
                policy.select_windows_runtime(keepers() + [item])

    def test_modified_source_paths_are_opaque_never_opened(self):
        inputs = [row(name, "Z:/reviewed modified dependencies/" + str(index))
                  for index, name in enumerate(KEEPERS)]
        with patch("builtins.open", side_effect=AssertionError("No source reads")), \
                patch.object(Path, "open", side_effect=AssertionError("No source reads")), \
                patch.object(Path, "exists", side_effect=AssertionError("No source stat")):
            self.assertEqual(policy.select_windows_runtime(inputs), (inputs, ()))

    def test_list_rows_and_generator_preserve_original_entries(self):
        inputs = [list(item) for item in keepers()]
        selected, removed = policy.select_windows_runtime(iter(inputs))
        self.assertEqual(selected, inputs)
        self.assertTrue(all(a is b for a, b in zip(selected, inputs)))
        self.assertEqual(removed, ())

    def test_failure_does_not_mutate_earlier_rows(self):
        inputs = keepers() + [row("ucrtbase.dll"), row("other.dll"), row("OTHER.dll")]
        original = deepcopy(inputs)
        with self.assertRaises(ValueError):
            policy.select_windows_runtime(inputs)
        self.assertEqual(inputs, original)


class SpecIntegrationTests(unittest.TestCase):
    def run_spec(self, binaries):
        source = (PACKAGING / "motortester.spec").read_text()
        events, state = [], {}
        analysis = SimpleNamespace(binaries=binaries, pure=object(), scripts=object(), datas=object())
        def analyze(*args, **kwargs):
            events.append("Analysis")
            return analysis
        def pyz(*args, **kwargs):
            events.append("PYZ")
            state["at_pyz"] = list(analysis.binaries)
            return object()
        def exe(*args, **kwargs):
            events.append("EXE")
            state["exe_args"] = args
            return object()
        def collect(*args, **kwargs):
            events.append("COLLECT")
            state["collect_args"] = args
            return object()
        scope = dict(SPECPATH=str(PACKAGING), Analysis=analyze, PYZ=pyz, EXE=exe, COLLECT=collect)
        output = io.StringIO()
        # Actual spec/helper, with inert PyInstaller targets and an unrelated cwd.
        with tempfile.TemporaryDirectory() as temporary, patch("sys.platform", "win32"), redirect_stdout(output):
            old_cwd = os.getcwd()
            try:
                os.chdir(temporary)
                exec(compile(source, str(PACKAGING / "motortester.spec"), "exec"), scope)
            finally:
                os.chdir(old_cwd)
        return events, state, analysis, output.getvalue()

    def test_actual_spec_filters_before_every_build_target(self):
        retained = keepers() + [row("PySide6/plugins/tls/qcertonlybackend.dll")]
        inputs = retained[:2] + [row(name) for name in policy.EXCLUDED_DESTINATIONS] + retained[2:]
        original = deepcopy(inputs)
        events, state, analysis, output = self.run_spec(inputs)
        self.assertEqual(events, ["Analysis", "PYZ", "EXE", "COLLECT"])
        self.assertEqual(state["at_pyz"], retained)
        self.assertIs(state["collect_args"][1], analysis.binaries)
        self.assertEqual(analysis.binaries, retained)
        self.assertEqual(state["exe_args"][2], [])
        self.assertEqual(inputs, original)
        self.assertIn("47 of 47 approved destinations removed", output)
        self.assertEqual(output.count("  omitted: "), 47)

    def test_actual_spec_reports_actual_subset_including_zero(self):
        for names in ((), policy.EXCLUDED_DESTINATIONS[:2]):
            _, _, _, output = self.run_spec(keepers() + [row(name) for name in names])
            self.assertIn(f"{len(names)} of 47 approved destinations removed", output)
            self.assertEqual(output.count("  omitted: "), len(names))

    def test_actual_spec_failure_prevents_all_build_targets(self):
        source = (PACKAGING / "motortester.spec").read_text()
        analysis = SimpleNamespace(binaries=keepers() + [row("ucrtbased.dll")])
        def forbidden(*args, **kwargs):
            self.fail("Build target ran before the failing TOC guard")
        with patch("sys.platform", "win32"), self.assertRaisesRegex(ValueError, "Unreviewed"):
            exec(compile(source, str(PACKAGING / "motortester.spec"), "exec"),
                 dict(SPECPATH=str(PACKAGING), Analysis=lambda *a, **k: analysis,
                      PYZ=forbidden, EXE=forbidden, COLLECT=forbidden))


if __name__ == "__main__":
    unittest.main()

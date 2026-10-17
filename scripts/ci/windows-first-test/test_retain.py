"""Inert filesystem fixtures only: no executable bytes, Windows launch or upload."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).absolute().parent
SPEC = importlib.util.spec_from_file_location("retain", HERE / "retain.py")
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)


def info(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="retention-inert-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build, self.evidence = self.root / "build", self.root / "evidence"
        self.build.mkdir()
        self.evidence.mkdir()
        self.output = self.root / "kit"
        self.plan_path = self.root / "plan.json"
        self.exe = b"INERT TEXT ONLY; NEVER EXECUTE\n"
        self.payload = {name: ("INERT FIXTURE: " + name + "\n").encode() for name in r.FIXED_PAYLOAD}
        archive = "recording.mtrec"
        spool = "recording-" + "a" * 32 + ".mtspool"
        self.payload[r.WORK + "/" + archive] = b"NOT AN MTREC ARCHIVE\n"
        self.payload[r.WORK + "/" + spool] = b"NOT A REAL SPOOL\n"
        self.receipt = {"status": "passed", "phase": "complete", "error": None,
                        "output": r.RECORDING, "workdir": r.WORK, "executable": info(self.exe),
                        "artifacts": {"archive": {"name": archive, **info(self.payload[r.WORK + "/" + archive])},
                                      "spool": {"name": spool, **info(self.payload[r.WORK + "/" + spool])}}}
        self.payload[r.RECORDING] = encoded(self.receipt)
        self.payload["SHA256SUMS.txt"] = "".join(info(data)["sha256"] + "  " + name + "\n"
                                               for name, data in self.payload.items()).encode("ascii")
        self.payload[r.BUILD_MANIFEST] = encoded({"source_commit": r.APP_COMMIT,
            "status": "candidate-not-release-approved", "usb_verified": False,
            "windows_10_manual_acceptance": "not performed by this script",
            "license_review": "required; see LICENSE-REVIEW.md", "files": {"Motortester.exe": info(self.exe)["sha256"]}})
        self.payload["build.log"] = b"INERT TEST LOG; NO BUILD WAS RUN\n"
        for name, raw in self.payload.items():
            self.put(self.build / name, raw)
        self.put(self.build / r.EXE, self.exe)
        self.put(self.evidence / "archive-verification.json", b'{"fixture":true}\n')
        self.plan = {"schema_version": 1, "mode": "accepted", "application_commit": r.APP_COMMIT,
                     "build_files": [{"path": name, **info(raw)} for name, raw in self.payload.items()],
                     "evidence_files": [{"path": "archive-verification.json", **info(b'{"fixture":true}\n')}],
                     "executable": info(self.exe)}
        self.save_plan()

    def put(self, path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def save_plan(self):
        raw = encoded(self.plan)
        self.plan_path.write_bytes(raw)
        self.plan_hash = info(raw)["sha256"]

    def invoke(self, **extra):
        args = dict(build_root=str(self.build), evidence_root=str(self.evidence), output=str(self.output),
                    expected_manifest=str(self.plan_path), expected_manifest_sha256=self.plan_hash)
        args.update(extra)
        return r.retain(**args)

    def rejected(self, match=None, **extra):
        with self.assertRaises((r.Reject, OSError)) as caught:
            self.invoke(**extra)
        if match:
            self.assertIn(match, str(caught.exception))
        return caught.exception

    def test_exact_bytes_and_original_eight_checksum_preserved(self):
        result = self.invoke()
        self.assertEqual(result["status"], "BYTES_RETAINED_ONLY")
        self.assertEqual(result["payload_files"], 12)
        for name, raw in self.payload.items():
            self.assertEqual((self.output / "build" / name).read_bytes(), raw)
        self.assertEqual((self.output / "EXPECTED_MANIFEST.json").read_bytes(), self.plan_path.read_bytes())
        outer_raw = (self.output / "OUTER_MANIFEST.json").read_bytes()
        self.assertEqual(info(outer_raw), result["outer_manifest"])
        outer = json.loads(outer_raw)
        self.assertEqual(len(self.payload["SHA256SUMS.txt"].splitlines()), 8)
        self.assertEqual(outer["external_gates"]["upload_download"], "not-performed")
        self.assertIsNone(outer["external_gates"]["transport_zip"])
        self.assertFalse((self.output / "build" / r.EXE).exists())

    def test_original_inputs_untouched(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.invoke()
        self.assertTrue(all(p.read_bytes() == raw for p, raw in before.items()))

    def test_unlisted_runner_files_not_copied(self):
        self.put(self.build / "venv/secret.txt", b"unlisted")
        self.put(self.evidence / "unlisted.log", b"unlisted")
        self.invoke()
        self.assertFalse((self.output / "build/venv").exists())
        self.assertFalse((self.output / "evidence/unlisted.log").exists())

    def test_existing_output_never_overwritten(self):
        self.output.mkdir()
        self.put(self.output / "keep", b"do not overwrite")
        self.rejected("case-alias")
        self.assertEqual((self.output / "keep").read_bytes(), b"do not overwrite")

    def test_output_case_alias_rejected(self):
        (self.root / "KIT").mkdir()
        self.rejected("case-alias")
        self.assertFalse(self.output.exists())

    def test_absolute_local_and_disjoint_roots(self):
        for value in ("relative", str(self.root) + "/../kit", str(self.root) + "//kit", "//server/share"):
            with self.subTest(value=value):
                self.rejected(output=value)
        self.rejected("overlapping-roots", output=str(self.build / "kit"))
        self.rejected("overlapping-roots", evidence_root=str(self.build))

    def test_plan_digest_mismatch(self):
        self.rejected("plan-digest-mismatch", expected_manifest_sha256="0" * 64)
        self.assertFalse(self.output.exists())

    def test_missing_and_changed_last_input_fail_before_output(self):
        path = self.evidence / "archive-verification.json"
        path.unlink()
        self.rejected()
        self.assertFalse(self.output.exists())
        path.write_bytes(b"different bytes")
        self.rejected()
        self.assertFalse(self.output.exists())

    def test_same_size_wrong_hash(self):
        path = self.build / "source.zip"
        path.write_bytes(b"x" * path.stat().st_size)
        self.rejected("input-digest-mismatch")
        self.assertFalse(self.output.exists())

    def test_invalid_last_plan_paths_reject_before_output(self):
        original = self.plan["evidence_files"][0]["path"]
        for bad in ("../outside", "/absolute", "a//b", "a/./b", "a\\b", "C:thing", "AUX.json", "NUL .txt",
                    "COM1.txt", "COM¹.txt", "CONIN$", "name.", "name ", "a\x00b", "*.json", "a?b", "x/" * 12 + "a"):
            with self.subTest(bad=bad):
                self.plan["evidence_files"][0]["path"] = bad
                self.save_plan()
                self.rejected()
                self.assertFalse(self.output.exists())
        self.plan["evidence_files"][0]["path"] = original

    def test_plan_duplicate_and_case_alias(self):
        for name in ("archive-verification.json", "ARCHIVE-verification.json"):
            self.plan["evidence_files"].append({**self.plan["evidence_files"][0], "path": name})
            self.save_plan()
            self.rejected("duplicate-or-casealias-row")
            self.plan["evidence_files"].pop()

    def test_plan_directory_file_collision(self):
        self.plan["evidence_files"].append({"path": "archive-verification.json/child", **info(b"x")})
        self.save_plan()
        self.rejected("file-directory-collision")

    def test_schema_types_and_extra_fields(self):
        for key, bad in (("schema_version", True), ("schema_version", 2), ("application_commit", "0" * 40),
                         ("executable", {"bytes": True, "sha256": "a" * 64}), ("build_files", {})):
            with self.subTest(key=key):
                plan = copy.deepcopy(self.plan)
                self.plan[key] = bad
                self.save_plan()
                self.rejected()
                self.plan = plan
        self.plan["authorization"] = True
        self.save_plan()
        self.rejected("invalid-object-fields")

    def test_duplicate_json_keys_nonfinite_and_deep_json(self):
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"x": NaN}', b'[' * 1100 + b']' * 1100):
            self.plan_path.write_bytes(raw)
            self.plan_hash = info(raw)["sha256"]
            self.rejected()
            self.assertFalse(self.output.exists())

    def test_byte_and_count_limits(self):
        row = self.plan["build_files"][0]
        for count in (0, -1, True, 201 * r.MIB):
            row["bytes"] = count
            self.save_plan()
            self.rejected("file-byte-limit")
        self.plan["build_files"] = [{"path": str(i), **info(b"x")} for i in range(65)]
        self.save_plan()
        self.rejected("file-count-limit")

    def test_total_limit(self):
        self.plan["evidence_files"] = [{"path": "file" + str(i), "bytes": 64 * r.MIB, "sha256": "a" * 64}
                                       for i in range(9)]
        self.save_plan()
        self.rejected("total-byte-limit")

    def test_missing_and_extra_original_outputs(self):
        self.plan["build_files"].pop()
        self.save_plan()
        self.rejected("wrong-build-inventory")

    def rewrite_payload(self, name, raw):
        self.put(self.build / name, raw)
        for row in self.plan["build_files"]:
            if row["path"] == name:
                row.update(info(raw))
        self.save_plan()

    def test_extra_checksum_row_rejected(self):
        self.rewrite_payload("SHA256SUMS.txt", self.payload["SHA256SUMS.txt"] + b"0" * 64 + b"  build.log\n")
        self.rejected("invalid-checksum-inventory")

    def test_checksum_mismatch_rejected(self):
        self.rewrite_payload("SHA256SUMS.txt", b"0" * 64 + self.payload["SHA256SUMS.txt"][64:])
        self.rejected("checksum-plan-mismatch")

    def test_receipt_selected_path_never_traversed(self):
        self.receipt["artifacts"]["spool"]["name"] = "../../outside"
        self.rewrite_payload(r.RECORDING, encoded(self.receipt))
        self.rejected("recording-artifact-name")
        self.assertFalse(self.output.exists())

    def test_partial_or_failed_receipt_rejected(self):
        self.receipt["status"] = "failed"
        self.rewrite_payload(r.RECORDING, encoded(self.receipt))
        self.rejected("recording-not-complete")

    def test_recording_work_extra_leaf_rejected(self):
        self.put(self.build / r.WORK / "extra.txt", b"extra")
        self.rejected("recording-work-inventory")

    def test_candidate_ready_claim_rejected(self):
        manifest = json.loads(self.payload[r.BUILD_MANIFEST])
        manifest["usb_verified"] = True
        self.rewrite_payload(r.BUILD_MANIFEST, encoded(manifest))
        self.rejected("candidate-qualification")

    def test_input_case_alias_rejected(self):
        self.put(self.build / "SOURCE.zip", b"alias")
        self.rejected("case-alias")

    def test_symlink_and_hardlink_rejected(self):
        path = self.build / "source.zip"
        original = path.read_bytes()
        path.unlink()
        outside = self.root / "outside"
        outside.write_bytes(original)
        path.symlink_to(outside)
        self.rejected("symlink-or-reparse")
        path.unlink()
        os.link(outside, path)
        self.rejected("hardlink-not-allowed")

    def test_directory_symlink_rejected(self):
        linked = self.root / "linked-build"
        linked.symlink_to(self.build, target_is_directory=True)
        self.rejected("symlink-or-reparse", build_root=str(linked))

    def test_reparse_attribute_rejected_without_windows_execution(self):
        original = Path.lstat
        target = self.build / "source.zip"
        def observed(path, *args, **kwargs):
            actual = original(path, *args, **kwargs)
            if path == target:
                values = {key: getattr(actual, key) for key in dir(actual) if key.startswith("st_")}
                return SimpleNamespace(**(values | {"st_file_attributes": 0x400}))
            return actual
        with mock.patch.object(Path, "lstat", observed):
            self.rejected("symlink-or-reparse")

    def test_cross_api_mtime_rejected_ctime_difference_allowed(self):
        original = os.fstat
        def altered(fd, field):
            actual = original(fd)
            values = {key: getattr(actual, key) for key in dir(actual) if key.startswith("st_")}
            values[field] += 1_000_000_000
            return SimpleNamespace(**values)
        with mock.patch.object(os, "fstat", lambda fd: altered(fd, "st_mtime_ns")):
            self.rejected("input-open-identity")
        with mock.patch.object(os, "fstat", lambda fd: altered(fd, "st_ctime_ns")):
            self.assertEqual(self.invoke()["status"], "BYTES_RETAINED_ONLY")

    def test_late_input_change_during_final_readback_rejected(self):
        original = r.IO.read
        def read(io, path, *args, **kwargs):
            value = original(io, path, *args, **kwargs)
            if path == self.output / "OUTER_MANIFEST.json":
                self.put(self.build / "build.log", b"late change")
            return value
        with mock.patch.object(r.IO, "read", read):
            self.rejected("terminal-input-changed")
        self.assertTrue(self.output.exists())

    def test_late_output_change_during_final_readback_rejected(self):
        original = r.IO.read
        def read(io, path, *args, **kwargs):
            value = original(io, path, *args, **kwargs)
            if path == self.output / "OUTER_MANIFEST.json":
                self.put(self.output / "build/source.zip", b"late output change")
            return value
        with mock.patch.object(r.IO, "read", read):
            self.rejected("terminal-output-changed")

    def test_late_outer_inventory_addition_rejected(self):
        original = r.IO.read
        def read(io, path, *args, **kwargs):
            value = original(io, path, *args, **kwargs)
            if path == self.output / "OUTER_MANIFEST.json":
                self.put(self.output / "extra", b"late addition")
            return value
        with mock.patch.object(r.IO, "read", read):
            self.rejected("outer-kit-inventory")

    def test_copy_failure_preserves_unaccepted_partial_no_overwrite(self):
        original = r.IO.write
        count = 0
        def write(io, stream, raw):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("inert injected write failure")
            return original(io, stream, raw)
        with mock.patch.object(r.IO, "write", write):
            self.rejected()
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / "OUTER_MANIFEST.json").exists())
        self.rejected("case-alias")

    def test_diagnostics_is_separate_and_never_copies_binary_payload(self):
        self.plan.update(mode="diagnostics", executable=None,
                         build_files=[{"path": "build.log", **info(self.payload["build.log"])}])
        self.save_plan()
        result = self.invoke(mode="diagnostics")
        self.assertEqual(result["status"], "INCOMPLETE_DIAGNOSTICS_ONLY")
        self.assertFalse((self.output / "build/source.zip").exists())
        second = self.root / "second"
        self.plan["build_files"].append({"path": "source.zip", **info(self.payload["source.zip"])})
        self.save_plan()
        self.rejected("diagnostic-file-type", mode="diagnostics", output=str(second))
        self.assertFalse(second.exists())

    def test_diagnostic_bound_and_mode_mismatch(self):
        self.rejected("mode-mismatch", mode="diagnostics")
        self.plan.update(mode="diagnostics", executable=None,
                         build_files=[{"path": "build.log", "bytes": 8 * r.MIB + 1, "sha256": "a" * 64}])
        self.save_plan()
        self.rejected("file-byte-limit", mode="diagnostics")

    def test_cli_oversized_integer_is_bounded_rejection(self):
        raw = b'{"schema_version":' + b'9' * 5000 + b'}'
        self.plan_path.write_bytes(raw)
        self.plan_hash = info(raw)["sha256"]
        args = [sys.executable, "-I", "-S", "-B", str(HERE / "retain.py"),
                "--build-root", str(self.build), "--evidence-root", str(self.evidence),
                "--output", str(self.output), "--expected-manifest", str(self.plan_path),
                "--expected-manifest-sha256", self.plan_hash]
        failed = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stdout, "")
        self.assertEqual(json.loads(failed.stderr)["code"], "invalid-json")
        self.assertNotIn("Traceback", failed.stderr)
        self.assertFalse(self.output.exists())

    def test_cli_success_and_bounded_failure(self):
        args = [sys.executable, "-I", "-S", "-B", str(HERE / "retain.py"),
                "--build-root", str(self.build), "--evidence-root", str(self.evidence),
                "--output", str(self.output), "--expected-manifest", str(self.plan_path),
                "--expected-manifest-sha256", self.plan_hash]
        passed = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(json.loads(passed.stdout)["status"], "BYTES_RETAINED_ONLY")
        failed = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stdout, "")
        self.assertEqual(json.loads(failed.stderr)["status"], "RETENTION_REJECTED")
        self.assertNotIn(str(self.root), failed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

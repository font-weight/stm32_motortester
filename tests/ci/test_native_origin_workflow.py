"""Static glue and mocked subprocess/retention controls, without a Windows build."""
import ast
import base64
import contextlib
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/host-windows.yml"


def steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["verify"]["steps"]


def embedded(name):
    step = next(x for x in steps() if x["name"] == name)
    source, = re.findall(r"@'\n(.*?)\n'@", step["run"], re.S)
    return source


class Workflow(unittest.TestCase):
    def retention_env(self, folder):
        return {"OBSERVER_REPORT": str(Path(folder) / "native-observation-1-1.json"),
                "RUNNER_TEMP": folder, "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "1"}

    def retained_parts(self, output):
        parts = {}
        for label, body in re.findall(r"BEGIN (\S+) BASE64\n(.*?)\nEND \1 BASE64", output, re.S):
            parts[label] = base64.b64decode(body)
        return parts

    def test_all_embedded_python_and_yaml(self):
        for step in steps():
            for source in re.findall(r"@'\n(.*?)\n'@", step.get("run", ""), re.S):
                ast.parse(source)

    def test_unchanged_selection_and_caps(self):
        document = yaml.safe_load(WORKFLOW.read_text())
        job = document["jobs"]["verify"]
        self.assertEqual(document["permissions"], {"contents": "read"})
        self.assertEqual(job["runs-on"], "windows-2022")
        self.assertEqual(job["timeout-minutes"], 30)
        self.assertEqual(job["env"]["SOURCE_SHA"], "244b0786a18fed19a9e708bb815150475ddd193f")
        self.assertEqual(job["env"]["INSPECTOR_SHA"], "54d63be3fdd8eba858bafafbf6cec33c4ed989b8")
        self.assertEqual(job["env"]["INSPECTOR_PARENT_SHA"], "d10e83578718d49f971f034a58e0a77af10ff855")
        script = (ROOT / "scripts/ci/observe_native_origins.py").read_bytes()
        self.assertEqual(hashlib.sha256(script).hexdigest(), job["env"]["OBSERVER_FILE_SHA256"])
        observer = next(x for x in steps() if x["name"] == "Observe bounded native metadata and original CPython notice")
        self.assertEqual(observer["timeout-minutes"], 6)
        self.assertIn("always()", observer["if"])
        self.assertNotIn("continue-on-error", WORKFLOW.read_text())
        self.assertNotIn("upload-artifact", WORKFLOW.read_text())
        self.assertNotIn("actions/cache", WORKFLOW.read_text())

    def test_delivery_tuple_is_coherent(self):
        env = yaml.safe_load(WORKFLOW.read_text())["jobs"]["verify"]["env"]
        self.assertEqual(env["SOURCE_TREE"], "0c0e61fb8aadd4a4380e2d7e0b8818e4c9da0d5e")
        checkouts = [x for x in steps() if x.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 1)
        self.assertEqual(checkouts[0]["with"]["ref"], env["SOURCE_SHA"])
        self.assertIn("$collector -cne '1aaa934cf7a964aa853230d635f1ce8335fc175c'", WORKFLOW.read_text())
        parsed = ast.parse((ROOT / "scripts/ci/observe_native_origins.py").read_text())
        assignments = [x for x in ast.walk(parsed) if isinstance(x, ast.Assign)]
        source, = [ast.literal_eval(x.value) for x in assignments
                   if any(isinstance(t, ast.Name) and t.id == "SOURCE" for t in x.targets)]
        self.assertEqual(source, env["SOURCE_SHA"])
        context, = [x.value for x in assignments
                    if any(isinstance(t, ast.Name) and t.id == "context" for t in x.targets)]
        values = {ast.literal_eval(k): v for k, v in zip(context.keys, context.values)}
        self.assertEqual(ast.literal_eval(values["source_tree"]), env["SOURCE_TREE"])
        self.assertEqual(ast.literal_eval(values["collector_blob"]), "1aaa934cf7a964aa853230d635f1ce8335fc175c")

    def test_child_errors_keep_source_guard_and_propagate(self):
        source = embedded("Observe bounded native metadata and original CPython notice")
        for failure in [subprocess.TimeoutExpired("observer", 300),
                        subprocess.CalledProcessError(1, "observer"), OSError("launch failed")]:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "observer-source.py"
                path.write_bytes(b"# inert source stand-in, not executed\n")
                env = {"OBSERVER_PATH": str(path), "OBSERVER_FILE_SHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "BUILD_DIR": folder, "OBSERVER_BASELINE": folder + "/baseline", "OBSERVER_REPORT": folder + "/report"}
                with patch.dict(os.environ, env), patch("subprocess.run", side_effect=failure) as run, patch("subprocess.check_output", return_value=b"") as guard:
                    with self.assertRaises(type(failure)):
                        exec(compile(source, "reviewed-workflow-wrapper", "exec"), {})
                    self.assertEqual(run.call_args.kwargs["timeout"], 300)
                    self.assertTrue(run.call_args.kwargs["check"])
                    guard.assert_called_once()

    def test_dirty_guard_cannot_be_hidden_by_success(self):
        source = embedded("Observe bounded native metadata and original CPython notice")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "observer-source.py"
            path.write_bytes(b"# inert source\n")
            env = {"OBSERVER_PATH": str(path), "OBSERVER_FILE_SHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "BUILD_DIR": folder, "OBSERVER_BASELINE": folder + "/baseline", "OBSERVER_REPORT": folder + "/report"}
            with patch.dict(os.environ, env), patch("subprocess.run"), patch("subprocess.check_output", return_value=b"dirty"):
                with self.assertRaisesRegex(RuntimeError, "source changed"):
                    exec(compile(source, "reviewed-workflow-wrapper", "exec"), {})

    def test_retention_complete_byte_roundtrip(self):
        source = embedded("Preserve bounded supplemental receipt independently")
        with tempfile.TemporaryDirectory() as folder:
            env = self.retention_env(folder)
            path = Path(env["OBSERVER_REPORT"])
            raw = b'{"text":"original \\r\\n bytes"}\n'
            path.write_bytes(raw)
            for side in ("source", "copy"):
                (Path(folder) / ("native-notice-" + side + "-1-1.bin")).write_bytes(side.encode() + b"\r\n")
            output = io.StringIO()
            with patch.dict(os.environ, env), contextlib.redirect_stdout(output):
                exec(compile(source, "reviewed-retention-wrapper", "exec"), {})
            lines = output.getvalue().splitlines()
            self.assertIn(hashlib.sha256(raw).hexdigest(), lines[0])
            self.assertEqual(self.retained_parts(output.getvalue()), {
                "native-observation.json": raw, "native-notice-source.bin": b"source\r\n",
                "native-notice-copy.bin": b"copy\r\n"})

    def test_missing_or_oversized_retention_is_failure(self):
        source = embedded("Preserve bounded supplemental receipt independently")
        with tempfile.TemporaryDirectory() as folder:
            env = self.retention_env(folder)
            path = Path(env["OBSERVER_REPORT"])
            with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    exec(compile(source, "reviewed-retention-wrapper", "exec"), {})
                self.assertEqual(error.exception.code, 1)
                path.write_bytes(b"x" * 2097153)
                with self.assertRaises(SystemExit) as error:
                    exec(compile(source, "reviewed-retention-wrapper", "exec"), {})
                self.assertEqual(error.exception.code, 1)

    def test_missing_report_and_notice_do_not_skip_obtainable_original(self):
        source = embedded("Preserve bounded supplemental receipt independently")
        for available in ("source", "copy"):
            with self.subTest(available=available), tempfile.TemporaryDirectory() as folder:
                raw = available.encode() * 100
                (Path(folder) / ("native-notice-" + available + "-1-1.bin")).write_bytes(raw)
                output = io.StringIO()
                with patch.dict(os.environ, self.retention_env(folder)), contextlib.redirect_stdout(output):
                    with self.assertRaises(SystemExit) as error:
                        exec(compile(source, "reviewed-retention-wrapper", "exec"), {})
                self.assertEqual(error.exception.code, 1)
                self.assertEqual(self.retained_parts(output.getvalue()), {"native-notice-" + available + ".bin": raw})

    def test_nonregular_retention_handle_not_emitted(self):
        import stat
        from types import SimpleNamespace
        source = embedded("Preserve bounded supplemental receipt independently")
        for bad_index in (0, 1):
            with tempfile.TemporaryDirectory() as folder:
                env = self.retention_env(folder)
                path = Path(env["OBSERVER_REPORT"])
                path.write_bytes(b"receipt")
                actual = path.stat()
                bad = SimpleNamespace(**{k: getattr(actual, k) for k in ("st_dev", "st_ino", "st_size", "st_mtime_ns")}, st_mode=stat.S_IFDIR)
                snapshots = [actual, actual]
                snapshots[bad_index] = bad
                output = io.StringIO()
                with patch.dict(os.environ, env), patch("os.fstat", side_effect=snapshots), contextlib.redirect_stdout(output):
                    with self.assertRaises(SystemExit):
                        exec(compile(source, "reviewed-retention-wrapper", "exec"), {})
                self.assertEqual(self.retained_parts(output.getvalue()), {})

    def test_retention_is_independent_of_original_recording(self):
        all_steps = steps()
        receipt = next(x for x in all_steps if x["name"] == "Preserve bounded supplemental receipt independently")
        original = next(x for x in all_steps if x["name"] == "Preserve original recording receipt independently")
        self.assertEqual(receipt["if"], "${{ always() }}")
        self.assertEqual(original["if"], "${{ always() }}")
        self.assertNotEqual(receipt["run"], original["run"])


if __name__ == "__main__":
    unittest.main()

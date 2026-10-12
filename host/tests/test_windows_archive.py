"""Real local Git archive regressions; other Windows build stages are fixtures."""
from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = load("archive_launcher", "launch_gui.py")
serial_smoke = load("archive_serial_smoke", "serial_import_smoke.py")
with patch.dict(sys.modules, {"launch_gui": launcher, "serial_import_smoke": serial_smoke}):
    build = load("archive_builder", "build_windows.py")


class WindowsSourceArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="archive проверка ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = self.root / "репозиторий с пробелами"
        self.repository.mkdir()
        self.host = self.repository / "host"
        self.expected = {
            "host/packaging/requirements-win-x64.lock": b"# fixture lock\r\n",
            "host/packaging/LICENSE-REVIEW.md": b"Fixture: review required\n",
            "host/source.py": b"print('tracked revision')\n",
            "host/данные с пробелами/raw.bin": bytes(range(256)),
            "host/данные с пробелами/empty": b"",
        }
        for name, data in self.expected.items():
            path = self.repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.repository / "outside.txt").write_bytes(b"must not enter the host archive")
        # All index/commit writes are confined to this disposable fixture repo.
        self.git("init", "--quiet")
        self.git("-c", "core.autocrlf=false", "add", "--all")
        self.git("-c", "user.name=Archive Test", "-c", "user.email=archive@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")
        self.revision = self.git("rev-parse", "HEAD").stdout.decode("ascii").strip()
        (self.host / "untracked secret.txt").write_bytes(b"must not leak")
        (self.host / "source.py").write_bytes(b"uncommitted bytes must not enter archive")

    def git(self, *args, cwd=None, check=True):
        # Do not inherit another checkout's Git routing or mutate global config.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT="0")
        return subprocess.run(["git", *map(str, args)], cwd=cwd or self.repository,
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=check, timeout=15)

    def assert_archive(self, path):
        names = set(self.expected)
        for name in self.expected:
            names.update(parent.as_posix() + "/" for parent in Path(name).parents
                         if parent != Path("."))
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), names)
            self.assertEqual(len(archive.namelist()), len(names))
            self.assertTrue(all(name.startswith("host/") for name in archive.namelist()))
            for name, data in self.expected.items():
                self.assertEqual(archive.read(name), data, name)
            for name in names - self.expected.keys():
                self.assertTrue(archive.getinfo(name).is_dir())
                self.assertEqual(archive.read(name), b"")
            self.assertEqual(archive.testzip(), None)

    @contextmanager
    def recipe(self, output, *, revision=None):
        commands = []
        stdout = io.StringIO()
        real_run = build.run_logged

        def run(command, **kwargs):
            command = [str(item) for item in command]
            commands.append((command, kwargs))
            self.assertEqual(kwargs["cwd"], self.host)
            if command[0] == "git":
                # The production recipe chooses arguments; this child is real Git.
                self.assertEqual(command[:4], ["git", "-C", str(self.repository), "archive"])
                return real_run(command, **kwargs)
            if "--format=json" in command:
                return "[]"
            if "PyInstaller" in command:
                bundle = output / "dist" / "Motortester"
                bundle.mkdir(parents=True)
                (bundle / "Motortester.exe").write_bytes(b"not an executable: fixture")
            if "--serial-import-smoke" in command:
                self.assertEqual(kwargs["timeout"], 30)
                Path(command[-1]).write_bytes(b"{}")
            if "--screenshot" in command:
                self.assertEqual(kwargs["timeout"], 60)
                self.assertNotIn("QT_QPA_PLATFORM", kwargs["env"])
                Path(command[-1]).write_bytes(b"not a PNG: fixture")
                launcher.receipt_path(Path(command[-1])).write_bytes(b"{}")
            return ""

        def check_output(command, **kwargs):
            self.assertEqual(kwargs, {"cwd": self.repository, "text": True})
            if command == ["git", "rev-parse", "HEAD"]:
                return (revision or self.revision) + "\n"
            self.assertEqual(command, ["git", "status", "--porcelain", "--untracked-files=all"])
            # Deliberately bypass the clean-tree prerequisite to prove that Git
            # reads committed bytes, never dirty/untracked fixture contents.
            return ""

        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["build_windows.py", "--output", str(output)]))
            stack.enter_context(patch.object(build, "HOST", self.host))
            stack.enter_context(patch.object(build, "REPOSITORY", self.repository))
            stack.enter_context(patch.object(build, "sys", SimpleNamespace(platform="win32", maxsize=2**63-1,
                version_info=(3, 13, 16), executable="fixture-python")))
            stack.enter_context(patch.object(build.platform, "machine", return_value="AMD64"))
            stack.enter_context(patch.object(build.platform, "platform", return_value="source archive fixture"))
            stack.enter_context(patch.object(build.platform, "python_version", return_value="3.13.16"))
            stack.enter_context(patch.object(build.sysconfig, "get_config_var", return_value=None))
            stack.enter_context(patch.object(build.subprocess, "check_output", side_effect=check_output))
            stack.enter_context(patch.object(build, "run_logged", side_effect=run))
            stack.enter_context(patch.object(build, "validate_report", return_value={"status": "fixture"}))
            stack.enter_context(patch.object(build, "validate_smoke_result", return_value={"status": "fixture"}))
            stack.enter_context(redirect_stdout(stdout))
            yield commands, stdout

    def test_old_host_cwd_fails_and_root_control_preserves_exact_host_tree(self):
        old_output = self.root / "старый путь.zip"
        old = self.git("archive", "--format=zip", f"--output={old_output}", self.revision,
                       "host", cwd=self.host, check=False)
        self.assertEqual(old.returncode, 128)
        self.assertIn(b"pathspec 'host'", old.stderr)
        correct = self.root / "корневой контроль.zip"
        self.git("archive", "--format=zip", f"--output={correct}", self.revision, "host")
        self.assert_archive(correct)

    def test_recipe_real_archive_from_root_host_and_unrelated_cwd(self):
        unrelated = self.root / "посторонняя папка"
        unrelated.mkdir()
        previous = Path.cwd()
        try:
            for index, cwd in enumerate((self.repository, self.host, unrelated)):
                with self.subTest(cwd=cwd):
                    os.chdir(cwd)
                    output = self.root / f"результат сборки {index}"
                    with self.recipe(output) as (commands, stdout):
                        self.assertEqual(build.main(), 0)
                    self.assert_archive(output / "source.zip")
                    self.assertIn("Candidate built:", stdout.getvalue())
                    sums = (output / "SHA256SUMS.txt").read_text(encoding="ascii")
                    self.assertIn(hashlib.sha256((output / "source.zip").read_bytes()).hexdigest()
                                  + "  source.zip\n", sums)
                    serial = next(i for i, (cmd, _) in enumerate(commands) if "--serial-import-smoke" in cmd)
                    screenshot = next(i for i, (cmd, _) in enumerate(commands) if "--screenshot" in cmd)
                    archive = next(i for i, (cmd, _) in enumerate(commands) if cmd[0] == "git")
                    self.assertLess(serial, screenshot)
                    self.assertLess(screenshot, archive)
                    # Also execute that exact production argument shape with
                    # each child cwd (the recipe itself always keeps cwd=HOST).
                    alternate = self.root / f"прямой архив {index}.zip"
                    args = [f"--output={alternate}" if arg.startswith("--output=") else arg
                            for arg in commands[archive][0][1:]]
                    self.git(*args, cwd=cwd)
                    self.assert_archive(alternate)
        finally:
            os.chdir(previous)

    def test_existing_output_directory_keeps_previous_source_archive(self):
        output = self.root / "старый результат"
        output.mkdir()
        source = output / "source.zip"
        source.write_bytes(b"old archive must remain unchanged")
        with self.recipe(output) as (commands, stdout):
            with self.assertRaises(FileExistsError):
                build.main()
        self.assertEqual(source.read_bytes(), b"old archive must remain unchanged")
        self.assertEqual(commands, [])
        self.assertNotIn("Candidate built:", stdout.getvalue())
        self.assertFalse((output / "SHA256SUMS.txt").exists())

    def test_real_archive_failure_prevents_final_checksums_and_success(self):
        output = self.root / "неудачная сборка"
        with self.recipe(output, revision="missing-source-revision") as (commands, stdout):
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                build.main()
        self.assertEqual(caught.exception.returncode, 128)
        self.assertIn("missing-source-revision", caught.exception.output)
        self.assertEqual(commands[-1][0][:4], ["git", "-C", str(self.repository), "archive"])
        self.assertFalse((output / "SHA256SUMS.txt").exists())
        self.assertNotIn("Candidate built:", stdout.getvalue())
        log = (output / "build.log").read_text(encoding="utf-8")
        self.assertIn("START git", log)
        self.assertIn("END exit_code=128", log)
        # The recipe intentionally retains earlier artifacts for diagnosis.
        self.assertTrue((output / "dist/Motortester/build-manifest.json").is_file())
        self.assertTrue((output / "Motortester-windows-x64-candidate.zip").is_file())


if __name__ == "__main__":
    unittest.main()

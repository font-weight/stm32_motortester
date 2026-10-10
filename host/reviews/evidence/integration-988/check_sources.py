"""Verify the integrated tree against each pinned source, not inherited scores."""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile

ROOT = Path("/tmp/motortester-host-app")
OUT = Path(__file__).resolve().parent
HEAD = "988f2cc094fa82c622f0b20276114e3159ae73d9"


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def require(value, message):
    if not value:
        raise RuntimeError(message)


def contents(sha, path):
    return git("show", f"{sha}:{path}")


def changed(sha):
    return git("diff", "--name-only", f"{sha}^", sha).decode().splitlines()


def compare(label, sha, paths):
    records = []
    for path in paths:
        before, after = contents(sha, path), contents(HEAD, path)
        require(before == after, f"{label}: differs from source: {path}")
        records.append({"path": path, "source_commit": sha,
                        "sha256": hashlib.sha256(after).hexdigest(), "exact_equal": True})
    return {"component": label, "files": records}


require(git("rev-parse", "HEAD").decode().strip() == HEAD, "Candidate SHA changed")
require(not git("status", "--porcelain").strip(), "Candidate tracked/worktree state not clean")
core = "2376d325d573b1aa3da6feaf8fddf05d2e1cb601"
wire = "c89c12df984390b34785eba81665abd6418658e2"
window = "21a2dd8fc53295e7228828d00e886bd28923494c"
bridge = "4e8d0a3497ee2a8203d2f3e97d8c1293e92dade4"
png = "2cf1fb83cf7d0dae98656cd6a63a5eea47c7e5c4"
session = "477057aadc9bc8e757bc8ef817125377e816d212"
layout = "3b315ac4e1184c4c7e9216ce5594cff0f9beff33"
core_paths = git("ls-tree", "-r", "--name-only", core, "host").decode().splitlines()
manual_or_later = {"host/README.md", "host/docs/format-v1.md", "host/docs/help.md",
                   "host/motortester/gui.py", "host/tests/test_gui.py"}
core_paths = [path for path in core_paths if path not in manual_or_later]
window_paths = git("ls-tree", "-r", "--name-only", window, "host/tests/fixtures/window").decode().splitlines()
window_paths += ["host/motortester/window.py", "host/docs/window-receive.md",
                 "host/tests/test_window.py", "host/tests/test_window_conformance.py"]
rows = [compare("core and unchanged packaging", core, core_paths),
        compare("wire", wire, changed(wire)),
        compare("window including optimized-safe helper", window, window_paths),
        compare("synthetic byte bridge", bridge, ["host/motortester/wire_demo.py", "host/tests/test_wire_demo.py", "host/docs/wire-demo.md"]),
        compare("PNG unchanged files", png, ["host/docs/format-v1.md", "host/docs/help.md", "host/tests/test_png_export.py"]),
        compare("pure session (all 21 added files)", session, changed(session))]
require(len(rows[-1]["files"]) == 21, "Session source count differs")

# Reconstruct the only combined production/test files from PNG + exact layout patch.
patch = git("diff", f"{layout}^", layout, "--", "host/motortester/gui.py", "host/tests/test_gui.py")
(OUT / "layout-source.patch").write_bytes(patch)
reconstructed = []
with tempfile.TemporaryDirectory(dir=OUT, prefix="source-reconstruction-") as folder:
    root = Path(folder)
    for path in ("host/motortester/gui.py", "host/tests/test_gui.py"):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents(png, path))
    result = subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(OUT / "layout-source.patch")],
                            cwd=root, capture_output=True, text=True)
    (OUT / "layout-reconstruction.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    require(result.returncode == 0, "Layout patch did not apply to PNG source")
    for path in ("host/motortester/gui.py", "host/tests/test_gui.py"):
        actual = (root / path).read_bytes()
        require(actual == contents(HEAD, path), f"Reconstructed source differs: {path}")
        reconstructed.append({"path": path, "base": png, "patch_source": layout,
                              "candidate_sha256": hashlib.sha256(actual).hexdigest(), "exact_equal": True})

all_tracked = git("ls-tree", "-r", "--name-only", HEAD, "host").decode().splitlines()
mapped = {row["path"] for group in rows for row in group["files"]}
mapped.update(row["path"] for row in reconstructed)
unmapped = sorted(set(all_tracked) - mapped)
require(unmapped == ["host/README.md", "host/docs/integration-sources.md"], f"Unexpected unmapped files: {unmapped}")
document_diffs = git("diff", bridge, HEAD, "--", *unmapped)
(OUT / "manual-integration-docs.diff").write_bytes(document_diffs)
manifest = {"reviewed_sha": HEAD, "tree": git("rev-parse", f"{HEAD}^{{tree}}").decode().strip(),
            "parent": git("rev-parse", f"{HEAD}^").decode().strip(),
            "components": rows, "reconstructed_files": reconstructed,
            "manually_reviewed_documents": unmapped,
            "all_host_tracked_files_accounted_for": len(all_tracked),
            "note": "Equality proves source composition only. Independent execution/review is separate."}
(OUT / "source-map.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"PASS: all {len(all_tracked)} tracked host files accounted for; session 21 files exact; GUI/test reconstructed exactly from PNG plus zero-fuzz layout patch.")

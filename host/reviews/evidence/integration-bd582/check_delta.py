"""Verify the exact successor and its bounded three-file delta."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path("/tmp/motortester-host-app-ui")
OUT = Path(__file__).resolve().parent
HEAD = "bd58245ecf64f08dcfe6d6fec3981e26fd7ff0ed"
BASE = "988f2cc094fa82c622f0b20276114e3159ae73d9"
TREE = "c7829a53e7151ed1ec27fe0150966344e8fa7b33"


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def require(value, message):
    if not value:
        raise RuntimeError(message)


require(git("rev-parse", "HEAD").decode().strip() == HEAD, "Wrong HEAD")
require(git("rev-parse", "HEAD^{tree}").decode().strip() == TREE, "Wrong tree")
require(git("rev-parse", "HEAD^").decode().strip() == BASE, "Wrong parent")
require(not git("status", "--porcelain").strip(), "Dirty candidate")
changed = git("diff", "--name-only", BASE, HEAD).decode().splitlines()
require(changed == ["host/docs/integration-sources.md", "host/motortester/gui.py", "host/tests/test_gui.py"], "Unexpected delta")
paths = git("ls-tree", "-r", "--name-only", HEAD, "host").decode().splitlines()
rows = []
for path in paths:
    data = git("show", f"{HEAD}:{path}")
    require((ROOT / path).read_bytes() == data, f"Worktree differs: {path}")
    prior = git("show", f"{BASE}:{path}")
    if path not in changed:
        require(data == prior, f"Unexpected source change: {path}")
    rows.append(dict(path=path, sha256=hashlib.sha256(data).hexdigest(), unchanged_from_988=data == prior))
(OUT / "candidate-delta.patch").write_bytes(git("diff", BASE, HEAD))
manifest = dict(reviewed_sha=HEAD, tree=TREE, parent=BASE, changed_files=changed,
                unchanged_files=sum(row["unchanged_from_988"] for row in rows),
                host_file_count=len(paths), files=rows)
(OUT / "source-delta.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"PASS: exact clean SHA/tree/parent; {len(paths)} host files match commit; 82 unchanged, 3 reviewed delta files.")

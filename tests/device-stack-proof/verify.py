#!/usr/bin/env python3
"""Read-only identity check; never executes the retained historical scripts."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

MANIFEST_SHA256 = "eda88182203c50775a2c9e802fc50098ac0f0cf006c9dcbb138054843d323268"
PACKAGE = "tests/device-stack-proof"


def canonical(path):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("canonical absolute path required")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError("symlink path rejected")
    return path


def read_regular(root, relative, limit):
    rel = PurePosixPath(relative)
    if rel.is_absolute() or str(rel) != relative or ".." in rel.parts:
        raise ValueError("invalid manifest-relative path")
    path = canonical(root.joinpath(*rel.parts))
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError("not a bounded regular file: " + relative)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    after = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after) or len(data) != before.st_size:
        raise ValueError("input changed during read: " + relative)
    return data


def verify(root):
    root = canonical(root)
    package = canonical(root / PACKAGE)
    raw = read_regular(package, "manifest.json", 200000)
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("manifest hash mismatch")
    manifest = json.loads(raw)
    for base, entries in ((package, manifest["files"]),
                          (root, manifest["source_dependencies"])):
        for entry in entries:
            data = read_regular(base, entry["path"], entry["bytes"])
            if (len(data) != entry["bytes"] or
                    hashlib.sha256(data).hexdigest() != entry["sha256"]):
                raise ValueError("input hash mismatch: " + entry["path"])
    expected = {entry["path"] for entry in manifest["files"]}
    actual = set()
    directories = [package / "evidence"]
    while directories:
        with os.scandir(directories.pop()) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise ValueError("symlink in evidence tree")
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    directories.append(path)
                elif entry.is_file(follow_symlinks=False):
                    actual.add(path.relative_to(package).as_posix())
                else:
                    raise ValueError("special entry in evidence tree")
    if actual != expected:
        raise ValueError("unexpected or missing evidence files")
    return {"status": "EVIDENCE_IDENTITY_PASS", "files": len(expected),
            "source_dependencies": len(manifest["source_dependencies"]),
            "source_tree": manifest["source_tree"],
            "proof_reexecuted": False, "stack_bound_transferred": False,
            "hardware_verified": False, "flash_approved": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).absolute().parent.parent.parent)
    args = parser.parse_args()
    try:
        result = verify(args.root)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print("EVIDENCE_IDENTITY_REJECTED: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

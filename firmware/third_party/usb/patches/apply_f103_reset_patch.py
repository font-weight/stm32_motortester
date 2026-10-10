#!/usr/bin/env python3
"""Apply the pinned local patch to a new file; never edit the vendor source."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="unchanged upstream dcd_stm32_fsdev.c")
    parser.add_argument("output", type=Path, help="new build-local dcd_stm32_fsdev.c")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / "f103-reset-cleanup.json").read_text())
    patch = here / "0002-f103-reset-completion-cleanup.patch"
    if digest(args.source.read_bytes()) != manifest["source_sha256"]:
        parser.error("source hash differs from the pinned upstream file")
    if digest(patch.read_bytes()) != manifest["patch_sha256"]:
        parser.error("patch hash differs from the manifest")
    if args.output.exists() or args.output.is_symlink():
        parser.error("output already exists; choose a new build path")
    with tempfile.TemporaryDirectory(prefix="usb-fsdev-reset-patch-") as temporary:
        result = Path(temporary) / "dcd_stm32_fsdev.c"
        subprocess.run([
            "patch", "--batch", "--fuzz=0", "--posix", "--output", str(result),
            str(args.source.resolve()), str(patch),
        ], check=True)
        data = result.read_bytes()
        if digest(data) != manifest["result_sha256"]:
            parser.error("patched output differs from the pinned result")
        # Exclusive creation also rejects a destination created during patching.
        with args.output.open("xb") as output:
            output.write(data)
    print(json.dumps({"source_sha256": manifest["source_sha256"],
                      "patch_sha256": manifest["patch_sha256"],
                      "result_sha256": digest(data)}, sort_keys=True))


if __name__ == "__main__":
    main()

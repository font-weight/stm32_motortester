"""Copy installed notices for review; not an automatic license-compliance audit."""
import argparse
from importlib import metadata
import hashlib
import json
from pathlib import Path
import shutil
import sys


PYSERIAL_VERSION = "3.5"
PYSERIAL_LICENSE = Path(__file__).resolve().parent / "licenses" / "pyserial-3.5-LICENSE.txt"
PYSERIAL_LICENSE_SHA256 = "f91cb9813de6a5b142b8f7f2dede630b5134160aedaeaf55f4d6a7e2593ca3f3"
PYSERIAL_LICENSE_URL = "https://raw.githubusercontent.com/pyserial/pyserial/v3.5/LICENSE.txt"
PYSERIAL_WHEEL_SHA256 = "c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0"


def _pyserial_notice(destination: Path, version: str) -> dict:
    # The hash-locked PyPI wheel lacks a full notice. Always include the complete
    # version-pinned upstream text, even if a local installation has extra files.
    # Never fetch legal material during a build or silently accept another version.
    if version != PYSERIAL_VERSION:
        raise RuntimeError("The vendored pyserial notice only covers version 3.5")
    notice = PYSERIAL_LICENSE.read_bytes()
    if hashlib.sha256(notice).hexdigest() != PYSERIAL_LICENSE_SHA256:
        raise RuntimeError("Vendored pyserial notice SHA-256 mismatch")
    target = destination / "pyserial" / PYSERIAL_LICENSE.name
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(notice)
    return {
        "path": target.relative_to(destination).as_posix(),
        "source_url": PYSERIAL_LICENSE_URL,
        "sha256": PYSERIAL_LICENSE_SHA256,
        "version": PYSERIAL_VERSION,
        "reference_wheel": "pyserial-3.5-py2.py3-none-any.whl",
        "reference_wheel_sha256": PYSERIAL_WHEEL_SHA256,
        "reason": "The pinned official wheel has no complete license text",
    }


def collect(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    inventory = []
    pyserial_seen = False
    keywords = ("license", "licence", "copying", "notice", "copyright")
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        copied = []
        for entry in dist.files or ():
            relative = Path(str(entry))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            if not any(any(word in part.lower() for word in keywords) for part in relative.parts):
                continue
            source = Path(dist.locate_file(entry))
            if not source.is_file():
                continue
            target = destination / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(target.relative_to(destination).as_posix())
        vendored = []
        if name.lower().replace("_", "-") == "pyserial":
            pyserial_seen = True
            provenance = _pyserial_notice(destination, dist.version)
            copied.append(provenance["path"])
            vendored.append(provenance)
        inventory.append({
            "name": name,
            "version": dist.version,
            "license_expression": dist.metadata.get("License-Expression"),
            "license_metadata": dist.metadata.get("License"),
            "copied_notices": copied,
            "vendored_notices": vendored,
            "manual_review_required": True,
        })
    if not pyserial_seen:
        raise RuntimeError("Required pyserial distribution is absent")
    runtime_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not runtime_license.is_file():
        raise RuntimeError(f"CPython LICENSE.txt missing: {runtime_license}")
    shutil.copy2(runtime_license, destination / "CPython-LICENSE.txt")
    (destination / "inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    collect(parser.parse_args().output)

"""Copy installed notices for review; not an automatic license-compliance audit."""
import argparse
from importlib import metadata
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys


PYSERIAL_VERSION = "3.5"
PYSERIAL_LICENSE = Path(__file__).resolve().parent / "licenses" / "pyserial-3.5-LICENSE.txt"
PYSERIAL_LICENSE_SHA256 = "f91cb9813de6a5b142b8f7f2dede630b5134160aedaeaf55f4d6a7e2593ca3f3"
PYSERIAL_LICENSE_URL = "https://raw.githubusercontent.com/pyserial/pyserial/v3.5/LICENSE.txt"
PYSERIAL_WHEEL_SHA256 = "c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0"

QT_VERSION = "6.12.0"
QT_LICENSES = Path(__file__).resolve().parent / "licenses" / "qt-6.12.0"
QT_NOTICE_SOURCE_COMMIT = "f50facb83fc3dc5f3240c854668c7a6dd93871b2"
QT_NOTICES = (
    ("GPL-2.0-only.txt", 18092,
     "8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643",
     "d159169d1050894d3ea3b98e1c965c4058208fe1"),
    ("GPL-3.0-only.txt", 35147,
     "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
     "94a9ed024d3859793618152ea559a168bbcbb5e2"),
    ("LGPL-3.0-only.txt", 7651,
     "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768",
     "65c5ca88a67c30becee01c5a8816d964b03862f9"),
)
QT_DISTRIBUTIONS = {
    "pyside6-essentials": (
        "pyside6_essentials-6.12.0-cp310-abi3-win_amd64.whl",
        "c9a95102aa23c1f86516a30d5a9be37552d324eee7e567eb7508973f1218cb42",
    ),
    "shiboken6": (
        "shiboken6-6.12.0-cp310-abi3-win_amd64.whl",
        "a1906cb8116869178c64b6bab52623bbc8b4ceed7b30bfc3c9aa0f6e67f6438e",
    ),
}


def _distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _require_qt_distributions(distributions) -> None:
    seen = set()
    for dist in distributions:
        name = _distribution_name(dist.metadata["Name"])
        if name not in QT_DISTRIBUTIONS:
            continue
        if name in seen:
            raise RuntimeError(f"Duplicate Qt-family distribution: {name}")
        if dist.version != QT_VERSION:
            raise RuntimeError(f"The vendored Qt notices only cover {name} {QT_VERSION}")
        seen.add(name)
    if seen != set(QT_DISTRIBUTIONS):
        raise RuntimeError("Required PySide6-Essentials/shiboken6 distribution is absent")


def _qt_notices(destination: Path) -> list[dict]:
    # These exact upstream GNU texts supplement both distributions. Their
    # provenance is NOT proof of library corresponding source or license choice.
    notices = []
    for filename, size, sha256, git_blob in QT_NOTICES:
        data = (QT_LICENSES / filename).read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
            raise RuntimeError(f"Vendored Qt notice size/SHA-256 mismatch: {filename}")
        notices.append((filename, data, sha256, git_blob))
    records = []
    for filename, data, sha256, git_blob in notices:
        target = destination / "qt-6.12.0" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        records.append({
            "path": target.relative_to(destination).as_posix(),
            "source_url": ("https://raw.githubusercontent.com/pyside/pyside-setup/"
                           f"{QT_NOTICE_SOURCE_COMMIT}/LICENSES/{filename}"),
            "source_commit": QT_NOTICE_SOURCE_COMMIT,
            "source_git_blob": git_blob,
            "sha256": sha256,
            "bytes": len(data),
            "version": QT_VERSION,
            "scope": "upstream-license-text-only",
            "reason": "The pinned Windows Qt-family wheels lack these complete GNU texts",
        })
    return records


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
    distributions = sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower())
    _require_qt_distributions(distributions)
    inventory = []
    pyserial_seen = False
    keywords = ("license", "licence", "copying", "notice", "copyright")
    for dist in distributions:
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
    # Copy after discovered entries, with exclusive creation, so a distribution
    # cannot silently replace these pinned texts via a colliding notice path.
    qt_notices = _qt_notices(destination)
    for record in inventory:
        name = _distribution_name(record["name"])
        if name not in QT_DISTRIBUTIONS:
            continue
        wheel, wheel_sha256 = QT_DISTRIBUTIONS[name]
        for notice in qt_notices:
            record["vendored_notices"].append(notice | {"reference_wheel": wheel,
                                                      "reference_wheel_sha256": wheel_sha256})
            record["copied_notices"].append(notice["path"])
    (destination / "inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    collect(parser.parse_args().output)

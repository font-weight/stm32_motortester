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


DEPENDENCY_LICENSES = Path(__file__).resolve().parent / "licenses" / "dependency-6.12.0"
DEPENDENCY_MANIFEST_BYTES = 142332
DEPENDENCY_MANIFEST_SHA256 = "7166c136548231e009b028caaa8b32f57967cebe05c8899e6669726e1785c084"
DEPENDENCY_GUIDE = Path(__file__).resolve().parents[1] / "docs" / "dependency-sources.md"
DEPENDENCY_GUIDE_BYTES = 16721
DEPENDENCY_GUIDE_SHA256 = "f0f4ef563eec85e3757e44e19099ce144377864d332eb0e3659f3948a0d4b026"
DEPENDENCY_DIRECTORY = "dependency-6.12.0"


def _no_links(path: Path) -> None:
    # Check ancestors too: a regular-looking leaf can escape through a junction.
    for part in (path, *path.parents):
        info = part.lstat()
        if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError(f"Linked/reparse dependency notice input: {part.name}")


def _pinned_bytes(path: Path, size: int, sha256: str) -> bytes:
    _no_links(path)
    if not path.is_file():
        raise RuntimeError(f"Non-regular dependency notice input: {path.name}")
    if path.stat().st_size != size:
        raise RuntimeError(f"Dependency notice size/SHA-256 mismatch: {path.name}")
    data = path.read_bytes()
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise RuntimeError(f"Dependency notice size/SHA-256 mismatch: {path.name}")
    return data


def _invalid_constant(value):
    raise RuntimeError(f"Non-JSON dependency manifest constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"Duplicate dependency manifest key: {key}")
        result[key] = value
    return result


def _dependency_inputs() -> list[tuple[str, bytes, dict]]:
    """Validate the entire pinned, bounded input set before copying any of it."""
    raw = _pinned_bytes(DEPENDENCY_LICENSES / "manifest.json",
                        DEPENDENCY_MANIFEST_BYTES, DEPENDENCY_MANIFEST_SHA256)
    manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    fields = {"schema_version", "qt_version", "input_files", "proposal", "source_archives",
              "mesa_source", "llvm_provenance", "unresolved_coverage", "provenance"}
    if (type(manifest) is not dict or set(manifest) != fields
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["qt_version"] != QT_VERSION):
        raise RuntimeError("Invalid dependency manifest schema/version")
    canonical = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if raw != canonical:
        raise RuntimeError("Noncanonical dependency manifest JSON")
    proposal = manifest["proposal"]
    proposal_fields = {"status", "qualification", "components", "literal_files",
                       "literal_file_count", "literal_bytes", "still_needed"}
    if (type(proposal) is not dict or set(proposal) != proposal_fields
            or type(proposal["literal_file_count"]) is not int or proposal["literal_file_count"] != 41
            or type(proposal["literal_bytes"]) is not int or proposal["literal_bytes"] != 163526
            or type(proposal["literal_files"]) is not list or len(proposal["literal_files"]) != 41
            or type(proposal["components"]) is not list or len(proposal["components"]) != 55):
        raise RuntimeError("Invalid dependency attribution/literal counts")
    files = manifest["input_files"]
    if type(files) is not list or len(files) != 41:
        raise RuntimeError("Invalid dependency input file count")
    paths, hashes = set(), set()
    for item in files:
        if (type(item) is not dict or set(item) != {"path", "bytes", "sha256", "source_path"}
                or type(item["bytes"]) is not int or not 0 < item["bytes"] <= 163526
                or type(item["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or type(item["path"]) is not str or item["path"] != "notices/" + item["sha256"]
                or type(item["source_path"]) is not str):
            raise RuntimeError("Invalid or unsafe dependency input path/hash/size")
        folded = item["path"].casefold()
        if folded in paths or item["sha256"] in hashes:
            raise RuntimeError("Duplicate/case-alias dependency input")
        paths.add(folded)
        hashes.add(item["sha256"])
    if files != sorted(files, key=lambda item: item["path"]) or sum(f["bytes"] for f in files) != 163526:
        raise RuntimeError("Invalid dependency input ordering/total")
    by_hash = {item["sha256"]: item for item in files}
    literals = proposal["literal_files"]
    if (any(type(item) is not dict or item.get("sha256") not in by_hash
            or item.get("bytes") != by_hash[item["sha256"]]["bytes"]
            or item.get("path") != by_hash[item["sha256"]]["source_path"] for item in literals)
            or len({item["sha256"] for item in literals}) != 41):
        raise RuntimeError("Dependency source/member mapping mismatch")
    components = proposal["components"]
    missing, unique_records = [], set()
    for index, record in enumerate(components):
        if type(record) is not dict or type(record.get("source")) is not dict:
            raise RuntimeError("Invalid dependency attribution record")
        source = record["source"]
        if (type(source.get("component")) is not dict
                or source.get("compiled_into_candidate") != "unverified"
                or type(source.get("license_file_inputs")) is not list):
            raise RuntimeError("Invalid dependency attribution qualification")
        key = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if key in unique_records:
            raise RuntimeError("Duplicate dependency attribution record")
        unique_records.add(key)
        if "LicenseFile" not in source["component"]:
            missing.append(index)
            if source["license_file_inputs"]:
                raise RuntimeError("Unresolved dependency attribution has unexpected literal input")
        for literal in source["license_file_inputs"]:
            if (type(literal) is not dict or literal.get("sha256") not in by_hash
                    or type(literal.get("bytes")) is not int
                    or literal["bytes"] != by_hash[literal["sha256"]]["bytes"]
                    or type(literal.get("path")) is not str):
                raise RuntimeError("Unknown dependency attribution literal input")
    coverage = manifest["unresolved_coverage"]
    if (type(coverage) is not dict or len(missing) != 11
            or coverage.get("missing_license_file_record_indices") != missing
            or not str(coverage.get("missing_license_file_status", "")).startswith("unresolved:")):
        raise RuntimeError("Missing unresolved dependency coverage")
    # A manifest hash is the trust anchor; the checks above also document its
    # schema. Never accept extra files, including case aliases or symlink trees.
    actual = set()
    _no_links(DEPENDENCY_LICENSES)
    for path in DEPENDENCY_LICENSES.rglob("*"):
        _no_links(path)
        relative = path.relative_to(DEPENDENCY_LICENSES).as_posix()
        if path.is_dir():
            if relative != "notices":
                raise RuntimeError("Unexpected dependency input directory")
        elif path.is_file():
            actual.add(relative)
        else:
            raise RuntimeError("Non-regular dependency notice input")
    if actual != {"manifest.json", *(item["path"] for item in files)}:
        raise RuntimeError("Dependency input file set mismatch (missing/extra/case-alias)")
    # Preserve the existing notice-record fields. Detailed source associations
    # live in the copied manifest, not in a new inventory/build-manifest schema.
    common = {"version": QT_VERSION,
              "scope": "shared-source-attribution-supplement-not-compiled-coverage",
              "reason": "Shared source/notice inputs; compiled scope, completeness and distribution require review"}
    prepared = [("manifest.json", raw, common | {"sha256": DEPENDENCY_MANIFEST_SHA256,
                                                "bytes": len(raw)})]
    guide = _pinned_bytes(DEPENDENCY_GUIDE, DEPENDENCY_GUIDE_BYTES, DEPENDENCY_GUIDE_SHA256)
    prepared.append(("dependency-sources.md", guide, common | {"sha256": DEPENDENCY_GUIDE_SHA256,
                                                             "bytes": len(guide)}))
    archives = {row["name"].removesuffix(".tar.xz"): row["requested_url"]
                for row in manifest["source_archives"]}
    for item in files:
        data = _pinned_bytes(DEPENDENCY_LICENSES / item["path"], item["bytes"], item["sha256"])
        prefix = item["source_path"].split("/", 1)[0]
        if prefix == "Mesa11.2.2":
            url, version = manifest["mesa_source"]["url"], "11.2.2"
        elif prefix == "LLVM3.6.2":
            url, version = manifest["llvm_provenance"]["source_url"], "3.6.2"
        elif prefix in archives:
            url, version = archives[prefix], QT_VERSION
        else:
            raise RuntimeError("Unknown dependency source archive")
        prepared.append((item["path"], data, common | {
            "source_url": url, "version": version, "sha256": item["sha256"], "bytes": len(data),
            "reason": "Exact upstream member: " + item["source_path"]
                      + "; shared attribution input, compiled coverage unverified; see manifest.json",
        }))
    return sorted(prepared, key=lambda item: item[0])


def _dependency_notices(destination: Path) -> list[dict]:
    prepared = _dependency_inputs()
    # Reserve a new output subtree. A distribution must not prepopulate or alias
    # it, even on a case-sensitive test host for this Windows-targeted package.
    _no_links(destination)
    if any(path.name.casefold() == DEPENDENCY_DIRECTORY.casefold() for path in destination.iterdir()):
        raise FileExistsError("Dependency notice output collision/case-alias")
    output = destination / DEPENDENCY_DIRECTORY
    output.mkdir(exist_ok=False)
    (output / "notices").mkdir(exist_ok=False)
    result = []
    for relative, data, record in prepared:
        target = output / relative
        _no_links(target.parent)
        with target.open("xb") as stream:
            stream.write(data)
        result.append(record | {"path": target.relative_to(destination).as_posix()})
    return result


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
    dependency_notices = _dependency_notices(destination)
    for record in inventory:
        name = _distribution_name(record["name"])
        if name not in QT_DISTRIBUTIONS:
            continue
        wheel, wheel_sha256 = QT_DISTRIBUTIONS[name]
        for notice in qt_notices + dependency_notices:
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

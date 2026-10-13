"""Copy installed notices for review; not an automatic license-compliance audit."""
import argparse
from importlib import metadata
import hashlib
import json
import posixpath
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
DEPENDENCY_MANIFEST_BYTES = 182368
DEPENDENCY_MANIFEST_SHA256 = "dde5677a4f9cce86a3e81c20892b7266f89ddc3c609bd7d52a8d9bce4768644b"
DEPENDENCY_GUIDE = Path(__file__).resolve().parents[1] / "docs" / "dependency-sources.md"
DEPENDENCY_GUIDE_BYTES = 22049
DEPENDENCY_GUIDE_SHA256 = "36923624c9723995ed4b2905e198c7e0cd4d2d4ed9b60e5ca5f6602a594a3307"
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


def _metadata_notice_paths(source: dict) -> list[str]:
    """Resolve declared notice keys inside their archive, without opening paths."""
    component = source["component"]
    prefix = source["archive"].removesuffix(".tar.xz") + "/"
    metadata_path = source["metadata_path"]
    if not metadata_path.startswith(prefix):
        raise RuntimeError("Attribution metadata is outside its source archive")
    result, seen = [], set()
    for key in ("LicenseFile", "LicenseFiles", "CopyrightFile"):
        if key not in component:
            continue
        value = component[key]
        if key == "LicenseFiles":
            if type(value) is not list or not value:
                raise RuntimeError("Invalid plural notice metadata")
            values = value
        else:
            values = [value]
        for name in values:
            if (type(name) is not str or not name or name.startswith("/")
                    or any(ord(c) < 32 or ord(c) == 127 or c in '\\:<>"|?*' for c in name)
                    or any(part in ("", ".") or part.endswith((" ", ".")) and part != ".."
                           for part in name.split("/"))):
                raise RuntimeError("Unsafe notice metadata path")
            path = posixpath.normpath(posixpath.join(posixpath.dirname(metadata_path), name))
            if not path.startswith(prefix):
                raise RuntimeError("Notice metadata escapes source archive")
            if path.casefold() in seen:
                raise RuntimeError("Duplicate/case-alias notice metadata path")
            seen.add(path.casefold())
            result.append(path)
    return result


def _reconciled_bodies(manifest: dict, by_hash: dict, historical_missing: list[int]) -> dict:
    """Validate the finite eleven-row text association, not legal compliance."""
    rec = manifest["reconciliation"]
    fields = {"ledger", "target_commit", "target_tree", "previous_manifest_sha256",
              "source_archive_sha256", "rows", "additional_bodies", "additional_bytes",
              "text_reuse_checks", "notice_file_metadata_keys", "remaining_text_review", "qualification"}
    if (type(rec) is not dict or set(rec) != fields
            or rec["notice_file_metadata_keys"] != ["LicenseFile", "LicenseFiles", "CopyrightFile"]
            or type(rec["additional_bytes"]) is not int or rec["additional_bytes"] != 13079
            or type(rec["additional_bodies"]) is not list or len(rec["additional_bodies"]) != 10
            or type(rec["rows"]) is not list or len(rec["rows"]) != 11):
        raise RuntimeError("Invalid reconciliation schema/counts")
    old_hashes = {item["sha256"] for item in manifest["proposal"]["literal_files"]}
    bodies = {}
    for body in rec["additional_bodies"]:
        if (type(body) is not dict or set(body) != {"name", "bytes", "sha256", "saved", "source",
                                                   "source_byte_range_half_open", "selection", "context"}
                or type(body["sha256"]) is not str or body["sha256"] not in by_hash
                or body["sha256"] in old_hashes or body["sha256"] in bodies
                or type(body["bytes"]) is not int or body["bytes"] != by_hash[body["sha256"]]["bytes"]
                or type(body["source"]) is not dict):
            raise RuntimeError("Invalid/duplicate reconciled notice body")
        source, bounds = body["source"], body["source_byte_range_half_open"]
        if (type(source.get("path")) is not str or source["path"] != by_hash[body["sha256"]]["source_path"]
                or type(source.get("bytes")) is not int or not 0 < source["bytes"] <= 1111937
                or type(source.get("sha256")) is not str or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
                or type(source.get("git_blob")) is not str or not re.fullmatch(r"[0-9a-f]{40}", source["git_blob"])
                or type(bounds) is not list or len(bounds) != 2 or any(type(n) is not int for n in bounds)
                or not 0 <= bounds[0] < bounds[1] <= source["bytes"]
                or bounds[1] - bounds[0] != body["bytes"]):
            raise RuntimeError("Invalid reconciled source/hash/byte range")
        if body["selection"] == "entire original file":
            if bounds != [0, source["bytes"]] or body["sha256"] != source["sha256"]:
                raise RuntimeError("Whole-file reconciliation is not the complete source")
        elif body["selection"] not in ("exact original contiguous byte slice; no rewriting",
                                       "exact original contiguous NOTICE paragraph(s); no rewriting"):
            raise RuntimeError("Invalid reconciliation selection")
        if not source["path"].startswith("qtbase-everywhere-src-6.12.0/"):
            if (source.get("repo") != "apache/tika" or source["path"] != "NOTICE.txt"
                    or source.get("commit") != "c29724782854bb5a319f4a1940d0828a58ace3f9"
                    or source.get("sha256") != "4cd09bac8d81bd34decf6b95fb23d42fc9b3d6b653ec4be8ef44674b692c97cd"):
                raise RuntimeError("Unexpected reconciled notice source")
        bodies[body["sha256"]] = body
    if set(bodies) != set(by_hash) - old_hashes or sum(b["bytes"] for b in bodies.values()) != 13079:
        raise RuntimeError("Reconciled notice body set/total mismatch")
    seen, referenced = [], set()
    components = manifest["proposal"]["components"]
    row_fields = {"record_index", "id", "status", "finding", "metadata_path", "metadata_sha256",
                  "sbom_package", "source_inputs", "proposed_new_bodies", "reuse_existing_bodies", "compiled_inclusion"}
    statuses = {7: "add_source_declaration", 8: "add_source_declaration", 10: "add_exact_retained_text",
                13: "reuse_existing_literal", 14: "partially_resolved_remaining_OGL_review",
                16: "reuse_existing_literal", 17: "add_exact_retained_text", 18: "add_exact_retained_text",
                21: "reuse_existing_literal", 40: "add_source_declaration", 47: "add_explicit_plural_license_inputs"}
    row_bodies = {}
    for row in rec["rows"]:
        if (type(row) is not dict or set(row) != row_fields or type(row["record_index"]) is not int
                or row["record_index"] not in historical_missing or row["record_index"] in seen):
            raise RuntimeError("Invalid/duplicate reconciliation row")
        index = row["record_index"]
        original = components[index]
        source = original["source"]
        if (row["id"] != source["component"]["Id"] or row["metadata_path"] != source["metadata_path"]
                or row["metadata_sha256"] != source["metadata_sha256"]
                or row["sbom_package"] != original["sbom_package"] or row["status"] != statuses[index]
                or row["compiled_inclusion"] != "unchanged, not proved by this text review"
                or type(row["proposed_new_bodies"]) is not list or type(row["reuse_existing_bodies"]) is not list
                or any(type(h) is not str or h not in bodies for h in row["proposed_new_bodies"])
                or len(set(row["proposed_new_bodies"])) != len(row["proposed_new_bodies"])):
            raise RuntimeError("Reconciliation source/qualification/body mismatch")
        reuse = row["reuse_existing_bodies"]
        if any(type(item) is not dict or type(item.get("sha256")) is not str
               or item["sha256"] not in old_hashes or item != by_hash[item["sha256"]] for item in reuse):
            raise RuntimeError("Invalid existing notice body reuse")
        if len({item["sha256"] for item in reuse}) != len(reuse):
            raise RuntimeError("Duplicate existing notice body reuse")
        if not row["proposed_new_bodies"] and not reuse:
            raise RuntimeError("Empty reconciliation body mapping")
        seen.append(index)
        referenced.update(row["proposed_new_bodies"])
        row_bodies[index] = row["proposed_new_bodies"]
    if seen != historical_missing or referenced != set(bodies):
        raise RuntimeError("Reconciliation row/body coverage mismatch")
    # Original metadata is immutable. Add associations separately rather than
    # inventing a singular LicenseFile for the three libjpeg plural/copyright files.
    absent_fields = []
    for index, original in enumerate(components):
        source = original["source"]
        declared = _metadata_notice_paths(source)
        if not declared:
            absent_fields.append(index)
        associated = {item["path"] for item in source["license_file_inputs"]}
        associated.update(bodies[h]["source"]["path"] for h in row_bodies.get(index, ()))
        if not set(declared).issubset(associated):
            raise RuntimeError("Declared notice metadata file is not mapped to a literal body")
    coverage = manifest["unresolved_coverage"]
    remaining = rec["remaining_text_review"]
    if (coverage.get("records_without_notice_file_fields") != absent_fields
            or coverage.get("remaining_text_review_record_indices") != [14]
            or type(remaining) is not dict or remaining.get("record_index") != 14
            or remaining.get("id") != "tika-mimetypes" or remaining.get("status") != "unresolved"
            or remaining.get("observed_read_status") != "403 Forbidden"
            or remaining.get("url") != "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"):
        raise RuntimeError("Missing unresolved Tika/OGL3 text-review qualification")
    return bodies


def _dependency_inputs() -> list[tuple[str, bytes, dict]]:
    """Validate the entire pinned, bounded input set before copying any of it."""
    raw = _pinned_bytes(DEPENDENCY_LICENSES / "manifest.json",
                        DEPENDENCY_MANIFEST_BYTES, DEPENDENCY_MANIFEST_SHA256)
    manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    fields = {"schema_version", "qt_version", "input_files", "proposal", "source_archives",
              "mesa_source", "llvm_provenance", "unresolved_coverage", "provenance", "reconciliation"}
    if (type(manifest) is not dict or set(manifest) != fields
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 2
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
    if type(files) is not list or len(files) != 51:
        raise RuntimeError("Invalid dependency input file count")
    paths, hashes = set(), set()
    for item in files:
        if (type(item) is not dict or set(item) != {"path", "bytes", "sha256", "source_path"}
                or type(item["bytes"]) is not int or not 0 < item["bytes"] <= 176605
                or type(item["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or type(item["path"]) is not str or item["path"] != "notices/" + item["sha256"]
                or type(item["source_path"]) is not str):
            raise RuntimeError("Invalid or unsafe dependency input path/hash/size")
        folded = item["path"].casefold()
        if folded in paths or item["sha256"] in hashes:
            raise RuntimeError("Duplicate/case-alias dependency input")
        paths.add(folded)
        hashes.add(item["sha256"])
    if files != sorted(files, key=lambda item: item["path"]) or sum(f["bytes"] for f in files) != 176605:
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
            or coverage.get("missing_license_file_status")
            != "historical metadata-key observation only; not proof of a legal omission"):
        raise RuntimeError("Missing historical dependency coverage qualification")
    reconciled = _reconciled_bodies(manifest, by_hash, missing)
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
        if item["sha256"] in reconciled:
            body = reconciled[item["sha256"]]
            source = body["source"]
            url = source.get("requested_url", archives["qtbase-everywhere-src-6.12.0"])
            version = source.get("commit", QT_VERSION)
            start, end = body["source_byte_range_half_open"]
            prepared.append((item["path"], data, common | {
                "source_url": url, "version": version, "sha256": item["sha256"], "bytes": len(data),
                "reason": f"Exact retained notice bytes [{start}, {end}) from {source['path']}; "
                          "whole-source hash and qualification in manifest.json; not compiled/legal closure",
            }))
            continue
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

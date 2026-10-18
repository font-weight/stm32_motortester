"""Bounded local byte retention for the reviewed appe38a3600 ordinary candidate.

No application imports, archive extraction, process launch or network operation.
A caller-supplied expected digest establishes byte identity, not authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

APP_COMMIT = "e38a36004befe453923f3e86165b7fd6d83076d9"
MIB = 1024 * 1024
MAX_PLAN = 64 * 1024
MAX_FILES = 64
MAX_TOTAL = 512 * MIB
MAX_DIAGNOSTICS_TOTAL = 16 * MIB
MAX_DIRECTORY_ENTRIES = 4096
MAX_RELATIVE = 180
CHUNK = MIB
RECORDING = "frozen-recording.json"
WORK = RECORDING + ".work"
EXE = "dist/Motortester/Motortester.exe"
BUILD_MANIFEST = "dist/Motortester/build-manifest.json"
FIXED_PAYLOAD = frozenset({"Motortester-windows-x64-candidate.zip", "source.zip",
                           "frozen-window.png", "frozen-window.png.smoke.json",
                           "frozen-serial-import.json", RECORDING})
BUILD_METADATA = frozenset({"SHA256SUMS.txt", "build.log", BUILD_MANIFEST})
RESERVED = re.compile(r"(?i:CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[0-9¹²³]|LPT[0-9¹²³])")
HEX = re.compile(r"[0-9a-f]{64}")


class Reject(ValueError):
    """A bounded, non-path-bearing error suitable for a caller's diagnostics."""


def require(condition, code):
    if not condition:
        raise Reject(code)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate-json-key")
        result[key] = value
    return result


def parse_json(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: require(False, "nonfinite-json"))
    except Reject:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise Reject("invalid-json") from error


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), "invalid-object-fields")


def component(name):
    require(type(name) is str and 0 < len(name) <= MAX_RELATIVE, "invalid-component")
    require(name not in (".", "..") and not name.endswith((".", " "))
            and not any(ord(c) < 32 or ord(c) > 126 or c in '<>:"/\\|?*' for c in name)
            and RESERVED.fullmatch(name.split(".", 1)[0].rstrip(" ")) is None,
            "unsafe-component")


def relative(name):
    require(type(name) is str and 0 < len(name) <= MAX_RELATIVE, "invalid-relative-path")
    parts = name.split("/")
    require(len(parts) <= 12, "relative-depth-limit")
    for part in parts:
        component(part)
    return parts


def absolute(raw):
    raw = os.fspath(raw)
    require(type(raw) is str and 0 < len(raw) <= 512, "invalid-absolute-path")
    path = Path(raw)
    require(path.is_absolute() and not raw.startswith(("//", "\\\\")), "absolute-local-path-required")
    # Path() normalizes dot/repeated separators; reject rather than silently normalize.
    require(str(path) == raw, "noncanonical-absolute-path")
    require(len(path.parts) <= 32, "absolute-depth-limit")
    for part in path.parts[1:]:
        component(part)
    return path


def redirected(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def shared(info):
    # Windows path-stat and handle-stat ctime need not be comparable.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            stat.S_IFMT(info.st_mode), getattr(info, "st_file_attributes", 0))


def stable(info):
    return shared(info) + (info.st_ctime_ns, info.st_nlink)


def members(parent):
    result = []
    with os.scandir(parent) as entries:
        for item in entries:
            require(len(result) < MAX_DIRECTORY_ENTRIES, "directory-entry-limit")
            result.append(item.name)
    return result


def no_alias(parent, name, *, absent=False):
    matches = [item for item in members(parent) if item.casefold() == name.casefold()]
    require(not matches if absent else matches == [name], "case-alias-or-existing-path")


def checked(path, kind):
    ancestors = list(reversed(path.parents))
    for index, part in enumerate(ancestors + [path]):
        info = part.lstat()
        require(not redirected(info), "symlink-or-reparse")
        wanted = kind if part == path else "directory"
        require(stat.S_ISDIR(info.st_mode) if wanted == "directory" else stat.S_ISREG(info.st_mode),
                "unexpected-file-type")
        if wanted == "file":
            require(info.st_nlink == 1, "hardlink-not-allowed")
        if index:
            no_alias(part.parent, part.name)
    return info


def disjoint(paths):
    parts = [tuple(part.casefold() for part in path.parts) for path in paths]
    for index, left in enumerate(parts):
        for right in parts[index + 1:]:
            require(left != right and left != right[:len(left)] and right != left[:len(right)],
                    "overlapping-roots")


def info_value(value, maximum):
    keys(value, ("bytes", "sha256"))
    require(type(value["bytes"]) is int and 0 < value["bytes"] <= maximum, "file-byte-limit")
    require(type(value["sha256"]) is str and HEX.fullmatch(value["sha256"]) is not None,
            "invalid-sha256")
    return value


def build_cap(name):
    if name == "Motortester-windows-x64-candidate.zip":
        return 200 * MIB
    if name == "source.zip":
        return 20 * MIB
    if name == RECORDING:
        return 65536
    if name == "frozen-window.png" or name.startswith(WORK + "/"):
        return 2 * MIB
    if name == BUILD_MANIFEST:
        return 4 * MIB
    if name == "build.log":
        return 16 * MIB
    return MIB


class IO:
    """All managed file bytes, including repeated validation, have finite caps."""
    def __init__(self):
        self.read_bytes = 0
        self.written_bytes = 0
        self.read_limit = 5 * MAX_TOTAL + 3 * 64 * MIB + 8 * MAX_PLAN
        self.write_limit = MAX_TOTAL + 3 * MAX_PLAN

    def read(self, path, expected=None, maximum=None, *, collect=False, sink=None):
        before = checked(path, "file")
        cap = expected["bytes"] if expected else maximum
        require(0 < before.st_size <= cap, "input-size-mismatch")
        if expected:
            require(before.st_size == expected["bytes"], "input-size-mismatch")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        chunks, total, digest = [], 0, hashlib.sha256()
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            require(shared(before) == shared(opened) and opened.st_nlink == 1
                    and not redirected(opened), "input-open-identity")
            while True:
                block = stream.read(min(CHUNK, cap - total + 1))
                self.read_bytes += len(block)
                require(self.read_bytes <= self.read_limit, "managed-read-limit")
                if not block:
                    break
                total += len(block)
                require(total <= cap, "input-grew")
                digest.update(block)
                if collect:
                    chunks.append(block)
                if sink is not None:
                    self.write(sink, block)
            after = os.fstat(stream.fileno())
        final = checked(path, "file")
        require(total == before.st_size and stable(opened) == stable(after)
                and stable(before) == stable(final) and shared(after) == shared(final), "input-changed")
        actual = {"bytes": total, "sha256": digest.hexdigest()}
        require(expected is None or actual == expected, "input-digest-mismatch")
        return actual, b"".join(chunks) if collect else None, stable(final)

    def write(self, stream, raw):
        self.written_bytes += len(raw)
        require(self.written_bytes <= self.write_limit, "managed-write-limit")
        require(stream.write(raw) == len(raw), "short-write")


def new_file(path, io, data=None, source=None, expected=None):
    checked(path.parent, "directory")
    no_alias(path.parent, path.name, absent=True)
    with path.open("xb") as stream:
        if source is not None:
            io.read(source, expected, sink=stream)
        else:
            io.write(stream, data)
        stream.flush()
        os.fsync(stream.fileno())
    checked(path, "file")


def mkdirs(root, relative_parent):
    path = root
    for name in relative_parent:
        child = path / name
        if not child.exists():
            no_alias(path, name, absent=True)
            child.mkdir()
        checked(child, "directory")
        path = child


def load_plan(raw, mode):
    plan = parse_json(raw)
    keys(plan, ("schema_version", "mode", "application_commit", "build_files", "evidence_files", "executable"))
    require(type(plan["schema_version"]) is int and plan["schema_version"] == 1,
            "unsupported-schema")
    require(plan["mode"] == mode and mode in ("accepted", "diagnostics"), "mode-mismatch")
    require(plan["application_commit"] == APP_COMMIT, "application-pin-mismatch")
    rows, total = [], 0
    for source in ("build", "evidence"):
        entries = plan[source + "_files"]
        require(type(entries) is list and len(entries) <= MAX_FILES, "file-count-limit")
        for row in entries:
            keys(row, ("path", "bytes", "sha256"))
            relative(row["path"])
            maximum = build_cap(row["path"]) if source == "build" else 64 * MIB
            if mode == "diagnostics":
                maximum = 8 * MIB
                require(Path(row["path"]).suffix in (".json", ".txt", ".log", ".stdout", ".stderr"),
                        "diagnostic-file-type")
            relative(source + "/" + row["path"])
            info = info_value({"bytes": row["bytes"], "sha256": row["sha256"]}, maximum)
            rows.append({"source": source, "path": row["path"], "info": info,
                         "destination": source + "/" + row["path"]})
            total += info["bytes"]
    require(0 < len(rows) <= MAX_FILES, "file-count-limit")
    require(total <= (MAX_TOTAL if mode == "accepted" else MAX_DIAGNOSTICS_TOTAL), "total-byte-limit")
    folded = [row["destination"].casefold() for row in rows]
    require(len(set(folded)) == len(folded), "duplicate-or-casealias-row")
    for name in folded:
        require(not any(other.startswith(name + "/") for other in folded), "file-directory-collision")
    if mode == "accepted":
        info_value(plan["executable"], 64 * MIB)
        build_names = {row["path"] for row in rows if row["source"] == "build"}
        require(FIXED_PAYLOAD | BUILD_METADATA <= build_names and len(build_names) == 11,
                "wrong-build-inventory")
        require(any(row["source"] == "evidence" for row in rows), "named-evidence-required")
    else:
        require(plan["executable"] is None, "diagnostics-executable-forbidden")
    return plan, rows


def joins(build, rows, buffers, executable):
    expected = {row["path"]: row["info"] for row in rows if row["source"] == "build"}
    receipt = parse_json(buffers[RECORDING])
    require(type(receipt) is dict and receipt.get("status") == "passed"
            and receipt.get("phase") == "complete" and receipt.get("error") is None
            and receipt.get("output") == RECORDING and receipt.get("workdir") == WORK,
            "recording-not-complete")
    artifacts = receipt.get("artifacts")
    keys(artifacts, ("archive", "spool"))
    names = set(FIXED_PAYLOAD)
    for kind, entry in artifacts.items():
        keys(entry, ("name", "bytes", "sha256"))
        name = entry["name"]
        require(type(name) is str and (name == "recording.mtrec" if kind == "archive" else
                re.fullmatch(r"recording-[0-9a-f]{32}\.mtspool", name) is not None), "recording-artifact-name")
        relative(name)
        path = WORK + "/" + name
        names.add(path)
        identity = info_value({"bytes": entry["bytes"], "sha256": entry["sha256"]}, 2 * MIB)
        require(expected.get(path) == identity, "recording-artifact-identity")
    require(set(expected) == names | BUILD_METADATA, "wrong-build-inventory")
    require(set(members(build / WORK)) == {artifacts[k]["name"] for k in artifacts}, "recording-work-inventory")
    sums = {}
    try:
        lines = buffers["SHA256SUMS.txt"].decode("ascii").splitlines()
    except UnicodeError as error:
        raise Reject("invalid-checksum-ascii") from error
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, "invalid-checksum-row")
        name = match[2]
        relative(name)
        require(name in names and name not in sums, "invalid-checksum-inventory")
        sums[name] = match[1]
    require(set(sums) == names and len(sums) == 8, "invalid-checksum-inventory")
    require(all(sums[name] == expected[name]["sha256"] for name in sums), "checksum-plan-mismatch")
    require(receipt.get("executable") == executable, "recording-executable-identity")
    manifest = parse_json(buffers[BUILD_MANIFEST])
    require(type(manifest) is dict and manifest.get("source_commit") == APP_COMMIT
            and manifest.get("status") == "candidate-not-release-approved"
            and manifest.get("usb_verified") is False
            and manifest.get("windows_10_manual_acceptance") == "not performed by this script"
            and manifest.get("license_review") == "required; see LICENSE-REVIEW.md",
            "candidate-qualification")
    require(type(manifest.get("files")) is dict
            and manifest["files"].get("Motortester.exe") == executable["sha256"], "manifest-executable-identity")


def inventory(root, rows):
    wanted = {row["destination"] for row in rows} | {"EXPECTED_MANIFEST.json", "OUTER_MANIFEST.json"}
    directories = {""}
    for name in wanted:
        parts = name.split("/")
        directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
    for name in directories:
        path = root.joinpath(*name.split("/")) if name else root
        checked(path, "directory")
        expected = {p[len(name) + 1:].split("/")[0] if name else p.split("/")[0]
                    for p in wanted if not name or p.startswith(name + "/")}
        require(set(members(path)) == expected, "outer-kit-inventory")


def retain(*, build_root, evidence_root, output, expected_manifest, expected_manifest_sha256, mode="accepted"):
    build, evidence, output, manifest_path = map(absolute, (build_root, evidence_root, output, expected_manifest))
    disjoint((build, evidence, output))
    require(output not in manifest_path.parents and output != manifest_path, "plan-inside-output")
    checked(build, "directory")
    checked(evidence, "directory")
    checked(output.parent, "directory")
    no_alias(output.parent, output.name, absent=True)
    require(type(expected_manifest_sha256) is str and HEX.fullmatch(expected_manifest_sha256) is not None,
            "invalid-plan-sha256")
    io = IO()
    actual_plan, raw, manifest_stat = io.read(manifest_path, maximum=MAX_PLAN, collect=True)
    require(actual_plan["sha256"] == expected_manifest_sha256, "plan-digest-mismatch")
    plan, rows = load_plan(raw, mode)
    roots = {"build": build, "evidence": evidence}
    snapshots, buffers = {manifest_path: manifest_stat}, {}
    # Entire plan and every source are admitted before creating the kit.
    for row in rows:
        path = roots[row["source"]].joinpath(*relative(row["path"]))
        collect = mode == "accepted" and row["source"] == "build" and row["path"] in {
            RECORDING, "SHA256SUMS.txt", BUILD_MANIFEST}
        _, data, snapshot = io.read(path, row["info"], collect=collect)
        snapshots[path] = snapshot
        if collect:
            buffers[row["path"]] = data
    if mode == "accepted":
        _, _, snapshots[build / EXE] = io.read(build / EXE, plan["executable"])
        joins(build, rows, buffers, plan["executable"])
    no_alias(output.parent, output.name, absent=True)
    output.mkdir()
    output_identity = shared(checked(output, "directory"))[:2]
    for row in rows:
        parts = relative(row["destination"])
        mkdirs(output, parts[:-1])
        new_file(output.joinpath(*parts), io, source=roots[row["source"]].joinpath(*relative(row["path"])),
                 expected=row["info"])
    new_file(output / "EXPECTED_MANIFEST.json", io, data=raw)
    result = {
        "schema_version": 1,
        "status": "BYTES_RETAINED_ONLY" if mode == "accepted" else "INCOMPLETE_DIAGNOSTICS_ONLY",
        "mode": mode, "application_commit": APP_COMMIT,
        "expected_manifest": actual_plan,
        "files": [{"path": row["destination"], **row["info"], "status": "bytes-match"} for row in rows],
        "executable": plan["executable"],
        "scope": "local copy and identity checks only; caller must require terminal exit 0",
        "external_gates": {"prior_build_and_validators": "not-established-by-this-helper",
                           "source_notice_completeness": "not-established-by-this-helper",
                           "independent_reviews": "not-established-by-this-helper",
                           "legal_distribution_permission": "not-established-by-this-helper",
                           "windows_10": "not-tested-by-this-helper", "physical_board": "not-tested-by-this-helper",
                           "upload_download": "not-performed", "transport_zip": None, "outer_kit_zip": None},
    }
    report = (json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("ascii")
    require(len(report) <= MAX_PLAN, "outer-manifest-limit")
    new_file(output / "OUTER_MANIFEST.json", io, data=report)
    report_info = {"bytes": len(report), "sha256": hashlib.sha256(report).hexdigest()}
    output_snapshots = {}
    for row in rows:
        destination = output.joinpath(*relative(row["destination"]))
        _, _, output_snapshots[destination] = io.read(destination, row["info"])
    _, _, output_snapshots[output / "EXPECTED_MANIFEST.json"] = io.read(output / "EXPECTED_MANIFEST.json", actual_plan)
    _, _, output_snapshots[output / "OUTER_MANIFEST.json"] = io.read(output / "OUTER_MANIFEST.json", report_info)
    # Same-API ctime/mtime checks retain late mutations and readback failures.
    for path, snapshot in snapshots.items():
        require(stable(checked(path, "file")) == snapshot, "terminal-input-changed")
    if mode == "accepted":
        joins(build, rows, buffers, plan["executable"])
    inventory(output, rows)
    for path, snapshot in output_snapshots.items():
        require(stable(checked(path, "file")) == snapshot, "terminal-output-changed")
    require(shared(checked(output, "directory"))[:2] == output_identity, "output-root-replaced")
    return {"status": result["status"], "outer_manifest": report_info,
            "payload_files": len(rows), "payload_bytes": sum(row["info"]["bytes"] for row in rows),
            "managed_read_bytes": io.read_bytes, "managed_written_bytes": io.written_bytes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("build-root", "evidence-root", "output", "expected-manifest", "expected-manifest-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--mode", choices=("accepted", "diagnostics"), default="accepted")
    args = parser.parse_args()
    try:
        result = retain(**vars(args))
    except (Reject, OSError, UnicodeError, RecursionError) as error:
        # Do not leak untrusted path/body strings or turn a partial kit into success.
        code = str(error) if isinstance(error, Reject) else type(error).__name__
        print(json.dumps({"status": "RETENTION_REJECTED", "code": code,
                          "partial_output_must_not_be_consumed": True}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

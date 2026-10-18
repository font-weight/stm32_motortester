"""Local evaluated-workflow caller. Byte retention only; never upload or run the app.

The reviewed workflow, not this program, establishes source/notice sufficiency
and permission. Support paths come only from its hash-pinned explicit inventory.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import subprocess
import struct
import sys
import types

APP_COMMIT = "e38a36004befe453923f3e86165b7fd6d83076d9"
APP_TREE = "ffab972586afff8d0a4e13a1e73d56af49190292"
INSPECTOR_COMMIT = "54d63be3fdd8eba858bafafbf6cec33c4ed989b8"
INSPECTOR_HASH = "fa017e8ab7a348d45c74c86bb877968bac9aef77f963642cc7af396a5f2c8c53"
RETENTION_COMMIT = "70d7f0fa661cb16d77c3b325e6fd4e5616b1eab7"
HELPERS = {
    "retain.py": (20066, "dac7433a4cc964f71081f0dad0639f907c1df1408c10e4d32109684f5c405b6a"),
    "zip_kit.py": (8419, "7051ecc513414685d0fdca73d01ac191925179fddd11a5b2e4152ee22aace057"),
}
# Independently reviewed Windows fresh-stat successor; all original caps retained.
PAYLOAD_CHECKER = (24668, "0924338f5ce4bd13ca20d4db42a052464ba4c9805bdc0bff44c395bbc3ca60a6")
SOURCE_PINS = {
    "host/packaging/select_windows_runtime.py": {"bytes": 5686, "sha256": "f4144a5318f4607a207531559c03365d2351473b40b5431ddfb4f7c3d381712b"},
    "host/packaging/motortester.spec": {"bytes": 1867, "sha256": "b754e84f96be7c8d8c8284a29457c83567f02c53344a99235857a60386d3e492"},
}
EXCLUSIONS_SHA256 = "7957597c771f8d8d450a8ff93a250d56fe9973e2c9429db1228d819f1be88001"
DEPENDENCY_MANIFEST = {"bytes": 182368, "sha256": "dde5677a4f9cce86a3e81c20892b7266f89ddc3c609bd7d52a8d9bce4768644b"}
DEPENDENCY_GUIDE = {"bytes": 22049, "sha256": "36923624c9723995ed4b2905e198c7e0cd4d2d4ed9b60e5ca5f6602a594a3307"}
NOTICE_HASH = "62bec384df47b0328307db41455ff6ea2559e5546b394ac69148561b21703120"
PYTHON_ROOT = PureWindowsPath(r"C:\hostedtoolcache\windows\Python\3.13.16\x64")
TERMS_FILES = {'launcher/Start-Motortester.ps1': {'bytes': 7069, 'sha256': '07f49b0867123f7c2e09edf674a7a219d4436aef86a4c49cd851a94c4e214a9f'}, 'launcher/terms/COMPONENT-TERMS.txt': {'bytes': 2881, 'sha256': '9f3c6158a504e61df2c6f35eb48727f96dc637edabfb6680b2211208339ca0b0'}, 'launcher/terms/MICROSOFT-RUNTIME-NOTICE.txt': {'bytes': 1965, 'sha256': '70af32c535680c16680fdecdc7cea0bd946cce0525ee5f182bf02743a2736ed0'}, 'launcher/terms/MICROSOFT-VISUAL-C++-V14-RUNTIME-LICENSE.txt': {'bytes': 11893, 'sha256': '3dfd057b91025067c08bd73c3cb0e6f712f76b6198b54413e14365d529f17ea6'}, 'launcher/terms/MICROSOFT-VISUAL-C++-V14-RUNTIME-LICENSE.docx': {'bytes': 39553, 'sha256': '08651651a7602fc7c0e2763de0fde1ff9f868df2780597cd1775ee9d6441c783'}}
# Exact supplier-wheel member bytes; historical reference is attribution, not fresh proof.
RUNTIME_REFERENCE_SHA256 = 'e30bc507ab0ce4d1794efd15ff2bfb8286588ab5df8b46db1dde9c4be82d1b5d'
WHEEL_RUNTIME_PINS = {'_internal/PySide6/MSVCP140.dll': {'bytes': 549176, 'sha256': 'f6f1d55bfd64027820ddce48f9cd8cf46d53bfa34047e6acf4ef348d492a05cb'}, '_internal/PySide6/MSVCP140_1.dll': {'bytes': 27448, 'sha256': '2be87527c41e1ba8aff48e326e0adcd4743516e0bbcc9304986c726e27de6aca'}, '_internal/PySide6/MSVCP140_2.dll': {'bytes': 271672, 'sha256': '6a72ed8a87eae1671640bd1ca98eccb989f1772ae46ab84594e385bf6d4ac4fb'}, '_internal/PySide6/VCRUNTIME140.dll': {'bytes': 116024, 'sha256': 'a36e0cb185eb89533ed96d4568138f86a1315756a8b2644c5963a8ce23e32101'}, '_internal/PySide6/VCRUNTIME140_1.dll': {'bytes': 41272, 'sha256': '302b1e160e858602490a55e32ae2e29ddfc0bec2a188e19550cb655cdaa58f9c'}, '_internal/numpy.libs/msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll': {'bytes': 575056, 'sha256': 'a4c2229bdc2a2a630acdc095b4d86008e5c3e3bc7773174354f3da4f5beb9cde'}, '_internal/shiboken6/MSVCP140.dll': {'bytes': 549176, 'sha256': '1ba4d7c0a95d375da138a057a57593b33510ad5bda6c5e2686afd68faa5b4231'}, '_internal/shiboken6/VCRUNTIME140.dll': {'bytes': 116024, 'sha256': 'e7be9112ee9a5b9f44b44bff24cb761cca9b1ed7550b612f77b70edfcf5fdabf'}, '_internal/shiboken6/VCRUNTIME140_1.dll': {'bytes': 41272, 'sha256': 'f02cd4a862b85a9a3153d27e15e5a213f5df8e3d77004ed2e87b8a7e8873d97c'}}
CPYTHON_RUNTIME_NAMES = ("VCRUNTIME140.dll", "VCRUNTIME140_1.dll")
REQUIRED_STEPS = (
    "verify_source", "verify_raw_blobs", "bind_inspector", "bind_retention_caller",
    "test_inert_launcher", "prepare_required_support", "build_candidate", "verify_archives", "inspect_candidate",
    "verify_reduced_payload", "verify_runtime_identity", "verify_cpython_notices", "retain_notice_logs",
    "retain_job_logs", "retain_recording_logs",
)
MIB = 1024 * 1024
# Actual original report names are selected from verified run/attempt, never globs.
DIAGNOSTIC_BUILD = (
    "build.log", "frozen-serial-import.json", "frozen-window.png.smoke.json",
    "frozen-recording.json", "SHA256SUMS.txt", "dist/Motortester/build-manifest.json",
    "archive-verification.json", "dist/Motortester/licenses/inventory.json",
)
MAX_SUPPORT_FILES = 16
MAX_SUPPORT_TOTAL = 128 * MIB

# Hash and execute exactly these source bytes; no importlib/pyc path is involved.
# Used both in this process and in isolated helper child processes.
BOOTSTRAP = r'''
import hashlib, os, stat
from pathlib import Path
def bound_raw(path, size, digest):
    path = Path(path)
    def checked():
        for part in (*reversed(path.parents), path):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('helper-redirected')
            if part != path and not stat.S_ISDIR(info.st_mode):
                raise ValueError('helper-ancestor')
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size:
            raise ValueError('helper-type-size')
        return info
    before = checked()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(size + 1)
        after = os.fstat(stream.fileno())
    final = checked()
    shared = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, stat.S_IFMT(x.st_mode))
    stable = lambda x: shared(x) + (x.st_ctime_ns, x.st_nlink)
    if (len(raw) != size or hashlib.sha256(raw).hexdigest() != digest
            or shared(before) != shared(opened) or shared(after) != shared(final)
            or stable(before) != stable(final) or stable(opened) != stable(after)
            or opened.st_nlink != 1 or getattr(opened, 'st_file_attributes', 0) & 0x400):
        raise ValueError('helper-body-identity')
    return raw
'''
exec(compile(BOOTSTRAP, "<bound-bootstrap>", "exec", dont_inherit=True))
LAUNCHER = BOOTSTRAP + r'''
import sys
path, size, digest = sys.argv[1:4]
raw = bound_raw(path, int(size), digest)
sys.argv = [path] + sys.argv[4:]
exec(compile(raw, path, 'exec', dont_inherit=True),
     {'__name__': '__main__', '__file__': path, '__package__': None})
'''


def helpers(root):
    root = Path(root)
    loaded = {}
    for name, (size, digest) in HELPERS.items():
        raw = bound_raw(root / name, size, digest)
        module = types.ModuleType("_bound_" + name[:-3])
        module.__file__ = str(root / name)
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
        loaded[name] = module
    h = loaded["retain.py"]
    h.checked(h.absolute(root), "directory")
    h.require(h.APP_COMMIT == APP_COMMIT, "retainer-application-pin")
    return h


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def context(h, raw, mode, review_reference):
    value = h.parse_json(raw)
    h.keys(value, ("schema_version", "repository", "run_id", "attempt", "workflow_sha",
                   "application_commit", "application_tree", "inspector_commit",
                   "inspector_sha256", "payload_checker_sha256", "source_notice_review", "steps"))
    h.require(type(value["schema_version"]) is int and value["schema_version"] == 1,
              "context-schema")
    h.require(value["repository"] == "font-weight/stm32_motortester"
              and value["application_commit"] == APP_COMMIT and value["application_tree"] == APP_TREE
              and value["inspector_commit"] == INSPECTOR_COMMIT
              and value["inspector_sha256"] == INSPECTOR_HASH
              and value["payload_checker_sha256"] == PAYLOAD_CHECKER[1], "context-pins")
    for key, pattern in (("run_id", r"[0-9]{1,20}"), ("attempt", r"[0-9]{1,4}"),
                         ("workflow_sha", r"[0-9a-f]{40}")):
        h.require(type(value[key]) is str and re.fullmatch(pattern, value[key]), "context-identity")
    h.keys(value["steps"], REQUIRED_STEPS)
    h.require(all(v in ("success", "failure", "cancelled", "skipped")
                  for v in value["steps"].values()), "context-outcome")
    if mode == "accepted":
        h.require(all(v == "success" for v in value["steps"].values()), "original-checks-incomplete")
        # This is a reference to external review, not an approval inferred from JSON.
        h.require(type(review_reference) is str and 0 < len(review_reference) <= 500
                  and review_reference.startswith("https://github.com/font-weight/stm32_motortester/")
                  and value["source_notice_review"] == review_reference, "external-review-binding")
    return value


def support_inventory(h, raw):
    value = h.parse_json(raw)
    h.require(type(value) is list and 1 <= len(value) <= MAX_SUPPORT_FILES, "support-count")
    seen, total = set(), 0
    for row in value:
        h.keys(row, ("path", "bytes", "sha256"))
        h.relative("support/" + row["path"])
        key = row["path"].casefold()
        h.require(key not in seen and not any(key.startswith(p + "/") or p.startswith(key + "/")
                                             for p in seen), "support-alias")
        seen.add(key)
        h.info_value({"bytes": row["bytes"], "sha256": row["sha256"]}, 64 * MIB)
        total += row["bytes"]
    h.require(total <= MAX_SUPPORT_TOTAL, "support-total")
    return value


def invoke(h, io, helper_root, name, arguments, work):
    size, digest = HELPERS[name]
    bound_raw(helper_root / name, size, digest)
    stdout, stderr = work / (name + ".stdout"), work / (name + ".stderr")
    h.no_alias(work, stdout.name, absent=True)
    h.no_alias(work, stderr.name, absent=True)
    command = [sys.executable, "-I", "-S", "-B", "-c", LAUNCHER,
               str(helper_root / name), str(size), digest, *arguments]
    with stdout.open("xb") as out, stderr.open("xb") as err:
        completed = subprocess.run(command, stdout=out, stderr=err, timeout=180, check=False)
    h.new_file(work / (name + ".exit.json"), io, data=encoded({"exit_code": completed.returncode}))
    h.require(completed.returncode == 0, "helper-terminal-nonzero")
    h.require(h.checked(stderr, "file").st_size == 0, "helper-unexpected-stderr")
    _, raw, _ = io.read(stdout, maximum=h.MAX_PLAN, collect=True)
    result = h.parse_json(raw)
    h.require(type(result) is dict, "helper-terminal-object")
    return result


def selection_observation(h, raw, approved):
    """Observe the spec's actual removal subset; final absence proves no removal count."""
    h.require(len(raw) <= 16 * MIB, "selection-log-bound")
    h.require(len(approved) == 47 and hashlib.sha256(("\n".join(approved) + "\n").encode("ascii")).hexdigest()
              == EXCLUSIONS_SHA256, "selection-policy-pin")
    prefix = b"Windows 10 1809+ x64 runtime selection: "
    header = re.compile(rb"Windows 10 1809\+ x64 runtime selection: ([0-9]{1,2}) of 47 approved destinations removed")
    count, removed, in_block = None, [], False
    for line in io.BytesIO(raw):
        line = line.rstrip(b"\r\n")
        if line.startswith(prefix):
            match = header.fullmatch(line)
            h.require(count is None and match is not None, "selection-header")
            count = int(match[1])
            h.require(count <= 47, "selection-count")
            in_block = True
        elif line.startswith(b"  omitted:"):
            h.require(in_block and line.startswith(b"  omitted: "), "selection-orphan-entry")
            name = line[len(b"  omitted: "):].decode("ascii")
            h.require(name in approved and name not in removed, "selection-subset")
            removed.append(name)
        else:
            in_block = False
    h.require(count is not None and count == len(removed), "selection-count")
    return {"schema_version": 1, "scope": "observed-build-log-selector-subset",
            "build_log": {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            "approved_exclusions_sha256": EXCLUSIONS_SHA256, "approved_count": 47,
            "actual_removed_count": count, "actual_removed": removed,
            "final_absence_used_to_infer_removal": False, "native_origin_authenticated": False}


def launcher_manifest(h, executable, support_rows):
    """Recipient launch identities only; never consent or distribution clearance."""
    h.info_value(executable, 64 * MIB)
    index = {row["path"]: {key: row[key] for key in ("bytes", "sha256")} for row in support_rows}
    h.require(all(index.get(path) == identity for path, identity in TERMS_FILES.items()), "launcher-input-pins")
    roles = {"componentTerms": "terms/COMPONENT-TERMS.txt",
             "runtimeNotice": "terms/MICROSOFT-RUNTIME-NOTICE.txt",
             "runtimeText": "terms/MICROSOFT-VISUAL-C++-V14-RUNTIME-LICENSE.txt",
             "runtimeOriginal": "terms/MICROSOFT-VISUAL-C++-V14-RUNTIME-LICENSE.docx"}
    documents = {role: {"path": path, "sha256": TERMS_FILES["launcher/" + path]["sha256"]}
                 for role, path in roles.items()}
    # Reuse the actual original notice validated before this function is called.
    # The recipient manually extracts the unchanged inner ZIP under launcher/app/.
    documents["pythonLicense"] = {"path": "app/Motortester/licenses/CPython-LICENSE.txt", "sha256": NOTICE_HASH}
    return {"application": {"path": "app/Motortester/Motortester.exe", "sha256": executable["sha256"]},
            "documents": documents}


def notice_coverage(h, reader, build, support, manifest, snapshots):
    """Join actual packaged notices and original collector inventory to pinned inputs."""
    _, raw, _ = reader.read(support / "host/packaging/licenses/dependency-6.12.0/manifest.json",
                            DEPENDENCY_MANIFEST, MIB, collect=True)
    declared = h.parse_json(raw)
    rows = declared.get("input_files")
    h.require(type(rows) is list and len(rows) == 51, "notice-literal-count")
    expected = {"manifest.json": DEPENDENCY_MANIFEST, "dependency-sources.md": DEPENDENCY_GUIDE}
    total = 0
    for row in rows:
        h.keys(row, ("path", "bytes", "sha256", "source_path"))
        identity = h.info_value({k: row[k] for k in ("bytes", "sha256")}, MIB)
        h.require(row["path"] == "notices/" + identity["sha256"] and row["path"] not in expected,
                  "notice-literal-path")
        expected[row["path"]] = identity
        total += identity["bytes"]
    h.require(total == 176605, "notice-literal-total")
    prefix = "licenses/dependency-6.12.0/"
    h.require({name for name in manifest["files"] if name.startswith(prefix)} ==
              {prefix + name for name in expected}, "notice-manifest-membership")
    for relative, identity in expected.items():
        name = prefix + relative
        h.require(manifest["files"][name] == identity["sha256"], "notice-manifest-body-hash")
        path = build / "dist/Motortester" / name
        _, _, snapshot = reader.read(path, identity, MIB)
        snapshots[path] = snapshot
    guidance = (("host/docs/project-license.md", "_internal/docs/project-license.md"),
                ("host/docs/dependency-sources.md", "_internal/docs/dependency-sources.md"),
                ("host/packaging/LICENSE-REVIEW.md", "LICENSE-REVIEW.md"))
    for source_name, bundle_name in guidance:
        identity, _, snapshot = reader.read(support.joinpath(*h.relative(source_name)), maximum=MIB)
        snapshots[support / source_name] = snapshot
        h.require(manifest["files"].get(bundle_name) == identity["sha256"], "notice-guidance-manifest")
        path = build / "dist/Motortester" / bundle_name
        _, _, snapshot = reader.read(path, identity, MIB)
        snapshots[path] = snapshot
    inventory_path = build / "dist/Motortester/licenses/inventory.json"
    inventory_info, inventory_raw, snapshot = reader.read(inventory_path, maximum=4 * MIB, collect=True)
    snapshots[inventory_path] = snapshot
    h.require(manifest["files"].get("licenses/inventory.json") == inventory_info["sha256"], "notice-inventory-manifest")
    inventory = h.parse_json(inventory_raw)
    h.require(type(inventory) is list and len(inventory) <= 32, "notice-inventory")
    seen = set()
    for record in inventory:
        h.require(type(record) is dict and type(record.get("name")) is str, "notice-inventory-row")
        name = re.sub(r"[-_.]+", "-", record["name"]).lower()
        if name not in ("pyside6-essentials", "shiboken6"):
            continue
        h.require(name not in seen, "notice-inventory-duplicate")
        seen.add(name)
        vendored, copied = record.get("vendored_notices"), record.get("copied_notices")
        h.require(type(vendored) is list and len(vendored) <= 256
                  and type(copied) is list and len(copied) <= 256, "notice-inventory-notices")
        prefix = "dependency-6.12.0/"
        subset = [row for row in vendored if type(row) is dict and type(row.get("path")) is str
                  and row["path"].startswith(prefix)]
        h.require(len(subset) == 53 and {row["path"] for row in subset} == {prefix + key for key in expected},
                  "notice-inventory-membership")
        for row in subset:
            h.require(type(row.get("bytes")) is int and {k: row.get(k) for k in ("bytes", "sha256")} ==
                      expected[row["path"][len(prefix):]], "notice-inventory-body-hash")
        subset = [name for name in copied if type(name) is str and name.startswith(prefix)]
        h.require(len(subset) == 53 and set(subset) == {prefix + key for key in expected}, "notice-copied-membership")
    h.require(seen == {"pyside6-essentials", "shiboken6"}, "notice-inventory-distributions")
    return {"schema_version": 1, "scope": "pinned-literal-notice-byte-correspondence",
            "literal_files": 51, "literal_bytes": total, "packaged_dependency_files": 53,
            "dependency_manifest": DEPENDENCY_MANIFEST, "dependency_guide": DEPENDENCY_GUIDE,
            "collector_inventory": inventory_info, "inventory_distributions": sorted(seen),
            "packaged_guidance": [name for _, name in guidance],
            "candidate_zip_correspondence": "mandatory original archive validator and bound reduced-payload checker",
            "source_zip_correspondence": "mandatory original validator verifies all 308 Git member bodies",
            "legal_clearance": False, "native_source_completeness": False, "replacement_verified": False}, inventory_info


def _capture_notice_inputs(h, *, build, reports, source_notice, run_id, attempt, workflow_sha):
    """Bounded byte-only core. Public wrapper additionally checks the actual Windows runtime."""
    build, reports, source_notice = map(h.absolute, (build, reports, source_notice))
    h.checked(build, "directory")
    h.checked(reports, "directory")
    h.require(reports != build and build not in reports.parents, "notice-output-overlap")
    for value, pattern in ((run_id, r"[0-9]{1,20}"), (attempt, r"[0-9]{1,4}"),
                           (workflow_sha, r"[0-9a-f]{40}")):
        h.require(type(value) is str and re.fullmatch(pattern, value), "notice-context")
    suffix = run_id + "-" + attempt
    output = reports / ("cpython-notice-" + suffix + ".json")
    sides = {side: reports / ("native-notice-" + side + "-" + suffix + ".bin") for side in ("source", "copy")}
    for path in (output, *sides.values()):
        h.no_alias(reports, path.name, absent=True)
    reader = h.IO()
    reader.read_limit, reader.write_limit = 32 * MIB, 4 * MIB
    inputs, snapshots = {}, {}
    for key, path, cap in (
        ("manifest", build / h.BUILD_MANIFEST, 4 * MIB),
        ("archive", build / "archive-verification.json", MIB),
        ("provenance", reports / ("provenance-" + suffix + ".json"), 8 * MIB),
        ("toc", build / "pyinstaller/motortester/COLLECT-00.toc", 2 * MIB),
        ("source", source_notice, MIB),
        ("copy", build / "dist/Motortester/licenses/CPython-LICENSE.txt", MIB)):
        identity, raw, snapshot = reader.read(path, maximum=cap, collect=True)
        inputs[key] = (identity, raw)
        snapshots[path] = snapshot
    manifest, archive, provenance = (h.parse_json(inputs[key][1]) for key in ("manifest", "archive", "provenance"))
    h.require(all(type(v) is dict for v in (manifest, archive, provenance)), "notice-report-object")
    h.require(manifest.get("python") == "3.13.16" and manifest.get("source_commit") == APP_COMMIT
              and type(manifest.get("files")) is dict, "notice-manifest")
    h.require(archive.get("status") == "PASS" and archive.get("source_sha") == APP_COMMIT
              and archive.get("build_manifest_sha256") == inputs["manifest"][0]["sha256"], "notice-archive")
    h.require(provenance.get("source_commit") == APP_COMMIT
              and provenance.get("inspector_commit") == INSPECTOR_COMMIT
              and provenance.get("inspector_sha256") == INSPECTOR_HASH
              and provenance.get("build_manifest_sha256") == inputs["manifest"][0]["sha256"]
              and provenance.get("collect_toc_sha256") == inputs["toc"][0]["sha256"]
              and provenance.get("pyinstaller_version") == "6.22.3", "notice-provenance")
    h.require(inputs["source"][1] == inputs["copy"][1]
              and inputs["copy"][0]["sha256"] == NOTICE_HASH
              and manifest["files"].get("licenses/CPython-LICENSE.txt") == NOTICE_HASH,
              "notice-source-copy-manifest")
    for side, path in sides.items():
        h.new_file(path, reader, data=inputs[side][1])
        reader.read(path, inputs[side][0])
    for path, snapshot in snapshots.items():
        h.require(h.stable(h.checked(path, "file")) == snapshot, "notice-source-changed")
    result = {"schema_version": 1, "status": "PASS", "scope": "original-cpython-notice-byte-correspondence",
              "context": {"run_id": run_id, "attempt": attempt, "workflow_sha": workflow_sha,
                          "source_commit": APP_COMMIT, "source_tree": APP_TREE},
              "input_hashes": {key: inputs[key][0]["sha256"] for key in ("manifest", "archive", "provenance", "toc")},
              "cpython_notice": {"source_copy_equal": True, "matches_run17_hash": True,
                  **{side: {"status": "retained_original", **inputs[side][0]} for side in sides}},
              "native_origins_observed": False, "legal_clearance": False, "distribution_authorized": False}
    h.new_file(output, reader, data=encoded(result))
    return result


def capture_cpython_notices(*, helper_root, build_root, reports_root, run_id, attempt, workflow_sha):
    h = helpers(helper_root)
    h.require(os.name == "nt" and sys.version_info[:3] == (3, 13, 16) and struct.calcsize("P") == 8
              and PureWindowsPath(sys.base_prefix) == PYTHON_ROOT, "notice-runtime")
    return _capture_notice_inputs(h, build=build_root, reports=reports_root,
        source_notice=str(PYTHON_ROOT / "LICENSE.txt"), run_id=run_id, attempt=attempt, workflow_sha=workflow_sha)



def runtime_payload_join(h, payload, manifest, rows):
    inventory = payload.get("actual_onedir")
    h.require(type(inventory) is dict and type(inventory.get("files")) is list,
              "runtime-payload-inventory")
    for row in rows:
        matches = [entry for entry in inventory["files"] if type(entry) is dict and entry.get("path") == row["path"]]
        h.require(matches == [row] and manifest["files"].get(row["path"]) == row["sha256"],
                  "runtime-payload-member-join")


def cpython_runtime_sources(h, python_root):
    """Choose only two fixed leaves under the trusted root; preserve actual case."""
    h.checked(python_root, "directory")
    names = h.members(python_root)  # Existing 4096-entry bound; no recursion/stat cache.
    sources = {}
    for fixed in CPYTHON_RUNTIME_NAMES:
        matches = [name for name in names if name.casefold() == fixed.casefold()]
        h.require(len(matches) == 1, "runtime-source-name-match")
        sources[fixed] = python_root / matches[0]
    return sources


def _capture_runtime_inputs(h, *, build, reports, python_root, run_id, attempt, workflow_sha):
    """Read exactly nine wheel members and two interpreter pairs; never load code."""
    build, reports, python_root = map(h.absolute, (build, reports, python_root))
    for root in (build, reports, python_root):
        h.checked(root, "directory")
    h.require(reports != build and build not in reports.parents, "runtime-output-overlap")
    for value, pattern in ((run_id, r"[0-9]{1,20}"), (attempt, r"[0-9]{1,4}"),
                           (workflow_sha, r"[0-9a-f]{40}")):
        h.require(type(value) is str and re.fullmatch(pattern, value), "runtime-context")
    suffix = run_id + "-" + attempt
    output = reports / ("runtime-identity-" + suffix + ".json")
    h.no_alias(reports, output.name, absent=True)
    reader = h.IO()
    reader.read_limit, reader.write_limit = 32 * MIB, MIB
    inputs, snapshots = {}, {}
    for key, path, cap in (("manifest", build / h.BUILD_MANIFEST, 4 * MIB),
                          ("archive", build / "archive-verification.json", MIB),
                          ("payload", reports / ("reduced-payload-" + suffix + ".json"), 4 * MIB)):
        identity, raw, snapshot = reader.read(path, maximum=cap, collect=True)
        inputs[key] = (identity, raw)
        snapshots[path] = snapshot
    manifest, archive, payload = (h.parse_json(inputs[key][1]) for key in ("manifest", "archive", "payload"))
    h.require(all(type(value) is dict for value in (manifest, archive, payload)), "runtime-report-object")
    h.require(manifest.get("source_commit") == APP_COMMIT and manifest.get("python") == "3.13.16"
              and type(manifest.get("files")) is dict, "runtime-manifest")
    h.require(archive.get("status") == "PASS" and archive.get("source_sha") == APP_COMMIT
              and archive.get("build_manifest_sha256") == inputs["manifest"][0]["sha256"], "runtime-archive")
    h.require(payload.get("status") == "PASS" and payload.get("application_commit") == APP_COMMIT
              and payload.get("application_tree") == APP_TREE
              and payload.get("actual_checker") == {"bytes": PAYLOAD_CHECKER[0], "sha256": PAYLOAD_CHECKER[1]}
              and payload.get("actual_build_manifest") == inputs["manifest"][0]
              and payload.get("actual_archive_verification_report") == inputs["archive"][0], "runtime-payload")
    wheel_rows, python_rows = [], []
    for relative, expected in WHEEL_RUNTIME_PINS.items():
        path = build / "dist/Motortester" / relative
        identity, _, snapshot = reader.read(path, expected, MIB)
        snapshots[path] = snapshot
        wheel_rows.append({"path": relative, **identity})
    source_paths = cpython_runtime_sources(h, python_root)
    for name in CPYTHON_RUNTIME_NAMES:
        source = source_paths[name]
        bundle_relative = "_internal/" + name
        bundle = build / "dist/Motortester" / bundle_relative
        identity, _, snapshot = reader.read(source, maximum=MIB)
        h.require(identity["bytes"] > 0, "runtime-empty-interpreter")
        snapshots[source] = snapshot
        _, _, snapshot = reader.read(bundle, identity, MIB)
        snapshots[bundle] = snapshot
        python_rows.append({"source_path": str(PYTHON_ROOT / source.name), "path": bundle_relative, **identity})
    runtime_payload_join(h, payload, manifest, wheel_rows +
                         [{k: row[k] for k in ("path", "bytes", "sha256")} for row in python_rows])
    for path, snapshot in snapshots.items():
        h.require(h.stable(h.checked(path, "file")) == snapshot, "runtime-source-changed")
    result = {"schema_version": 1, "status": "BYTE_IDENTITY_MATCH",
              "scope": "nine-pinned-wheel-members-and-two-cpython-source-bundle-pairs",
              "context": {"run_id": run_id, "attempt": attempt, "workflow_sha": workflow_sha,
                          "source_commit": APP_COMMIT, "source_tree": APP_TREE},
              "reference_sha256": RUNTIME_REFERENCE_SHA256,
              "input_hashes": {key: inputs[key][0]["sha256"] for key in inputs},
              "wheel_members": wheel_rows, "cpython_pairs": python_rows,
              "signature_metadata": "not-collected", "version_metadata": "not-collected"}
    raw = encoded(result)
    h.require(len(raw) <= h.MAX_PLAN, "runtime-receipt-limit")
    h.new_file(output, reader, data=raw)
    return result


def capture_runtime_identity(*, helper_root, build_root, reports_root, run_id, attempt, workflow_sha):
    h = helpers(helper_root)
    h.require(os.name == "nt" and sys.version_info[:3] == (3, 13, 16) and struct.calcsize("P") == 8
              and PureWindowsPath(sys.base_prefix) == PYTHON_ROOT, "runtime-interpreter")
    return _capture_runtime_inputs(h, build=build_root, reports=reports_root,
        python_root=str(PYTHON_ROOT), run_id=run_id, attempt=attempt, workflow_sha=workflow_sha)


def validate_runtime_receipt(h, reader, build, report, run, manifest, payload, input_hashes, snapshots):
    h.keys(report, ("schema_version", "status", "scope", "context", "reference_sha256", "input_hashes",
                    "wheel_members", "cpython_pairs", "signature_metadata", "version_metadata"))
    h.require(type(report["schema_version"]) is int and report["schema_version"] == 1
              and report["status"] == "BYTE_IDENTITY_MATCH"
              and report["scope"] == "nine-pinned-wheel-members-and-two-cpython-source-bundle-pairs"
              and report["reference_sha256"] == RUNTIME_REFERENCE_SHA256
              and report["signature_metadata"] == report["version_metadata"] == "not-collected", "runtime-receipt")
    h.require(report["context"] == {"run_id": run["run_id"], "attempt": run["attempt"],
              "workflow_sha": run["workflow_sha"], "source_commit": APP_COMMIT, "source_tree": APP_TREE}
              and report["input_hashes"] == input_hashes, "runtime-receipt-joins")
    expected_wheel = [{"path": path, **identity} for path, identity in WHEEL_RUNTIME_PINS.items()]
    h.require(encoded(report["wheel_members"]) == encoded(expected_wheel), "runtime-wheel-pins")
    h.require(type(report["cpython_pairs"]) is list and len(report["cpython_pairs"]) == 2,
              "runtime-interpreter-pairs")
    rows = list(expected_wheel)
    for name, row in zip(CPYTHON_RUNTIME_NAMES, report["cpython_pairs"]):
        h.keys(row, ("source_path", "path", "bytes", "sha256"))
        source_prefix = str(PYTHON_ROOT) + "\\"
        source = row["source_path"]
        h.require(type(source) is str and source.startswith(source_prefix), "runtime-interpreter-pair")
        actual_name = source[len(source_prefix):]
        h.require(actual_name.casefold() == name.casefold() and source == str(PYTHON_ROOT / actual_name)
                  and row["path"] == "_internal/" + name
                  and type(row["bytes"]) is int and row["bytes"] > 0, "runtime-interpreter-pair")
        h.info_value({k: row[k] for k in ("bytes", "sha256")}, MIB)
        rows.append({k: row[k] for k in ("path", "bytes", "sha256")})
    runtime_payload_join(h, payload, manifest, rows)
    for row in rows:
        path = build / "dist/Motortester" / row["path"]
        _, _, snapshot = reader.read(path, {k: row[k] for k in ("bytes", "sha256")}, MIB)
        snapshots[path] = snapshot


def stage(*, helper_root, build_root, reports_root, support_root, output_root, workflow_run,
          mode, support_manifest_sha256=None, source_notice_review=None):
    h = helpers(helper_root)
    helper_root, build, reports, support, work, workflow = map(h.absolute,
        (helper_root, build_root, reports_root, support_root, output_root, workflow_run))
    h.disjoint((build, support, work, helper_root))
    h.require(work not in reports.parents and reports != work and work not in workflow.parents,
              "input-inside-output")
    h.checked(work.parent, "directory")
    h.checked(reports, "directory")
    h.no_alias(work.parent, work.name, absent=True)
    h.require(mode in ("accepted", "diagnostics"), "invalid-mode")
    io = h.IO()
    # Covers stage input scans/copies plus final ZIP readback; helpers have separate budgets.
    io.read_limit = 3 * h.MAX_TOTAL + 520 * MIB
    io.write_limit = MAX_SUPPORT_TOTAL + 32 * MIB
    context_info, context_raw, _ = io.read(workflow, maximum=h.MAX_PLAN, collect=True)
    run = context(h, context_raw, mode, source_notice_review)
    if mode == "accepted":
        h.checked(build, "directory")
        h.checked(support, "directory")
        h.require(type(support_manifest_sha256) is str and h.HEX.fullmatch(support_manifest_sha256),
                  "support-manifest-pin-required")
        support_info, support_raw, _ = io.read(support / "support-manifest.json", maximum=h.MAX_PLAN, collect=True)
        h.require(support_info["sha256"] == support_manifest_sha256, "support-manifest-pin")
        support_rows = support_inventory(h, support_raw)
    work.mkdir()
    evidence = work / "evidence"
    evidence.mkdir()
    snapshots, evidence_rows = {}, []

    def copy(source, target, cap, expected=None):
        identity, _, snapshot = io.read(source, expected, cap)
        h.mkdirs(evidence, h.relative(target)[:-1])
        destination = evidence.joinpath(*h.relative(target))
        h.new_file(destination, io, source=source, expected=identity)
        io.read(destination, identity)
        snapshots[source] = snapshot
        evidence_rows.append({"path": target, **identity})
        return identity

    copy(workflow, "workflow-run.json", h.MAX_PLAN, context_info)
    suffix = run["run_id"] + "-" + run["attempt"]
    original_reports = (
        (reports / ("provenance-" + suffix + ".json"), "provenance.json", 8 * MIB),
        (reports / ("reduced-payload-" + suffix + ".json"), "reduced-payload.json", 4 * MIB),
        (reports / ("cpython-notice-" + suffix + ".json"), "cpython-notice.json", 2 * MIB),
        (reports / ("runtime-identity-" + suffix + ".json"), "runtime-identity.json", MIB),
    )
    if mode == "accepted":
        copy(helper_root.parent / "evaluated-workflow.yml", "evaluated-workflow.yml", 128 * 1024)
        # This source is deliberately BUILD_DIR, not the evidence directory.
        copy(build / "archive-verification.json", "archive-verification.json", MIB)
        for source, target, cap in original_reports:
            copy(source, target, cap)
        for side in ("source", "copy"):
            copy(reports / ("native-notice-" + side + "-" + suffix + ".bin"),
                 "native-notice-" + side + ".bin", MIB)
        copy(support / "support-manifest.json", "support-manifest.json", h.MAX_PLAN, support_info)
        for row in support_rows:
            copy(support.joinpath(*h.relative(row["path"])), "support/" + row["path"], 64 * MIB,
                 {"bytes": row["bytes"], "sha256": row["sha256"]})
        reports_json = {}
        for name, cap in (("archive-verification.json", MIB), ("provenance.json", 8 * MIB),
                          ("reduced-payload.json", 4 * MIB), ("cpython-notice.json", 2 * MIB),
                          ("runtime-identity.json", MIB)):
            _, raw, _ = io.read(evidence / name, maximum=cap, collect=True)
            reports_json[name] = h.parse_json(raw)
        h.require(all(type(v) is dict for v in reports_json.values()), "original-report-object")
        archive, provenance, payload, notice = (reports_json[k] for k in
            ("archive-verification.json", "provenance.json", "reduced-payload.json", "cpython-notice.json"))
        h.require(archive.get("status") == "PASS" and archive.get("source_sha") == APP_COMMIT
                  and archive.get("recording_required") is True
                  and type(archive.get("recording_source_zip_hashes_verified")) is int
                  and archive["recording_source_zip_hashes_verified"] == 23
                  and type(archive.get("source_files")) is int and archive["source_files"] == 308,
                  "original-archive-incomplete")
        h.require(provenance.get("source_commit") == APP_COMMIT
                  and provenance.get("inspector_commit") == INSPECTOR_COMMIT
                  and provenance.get("inspector_sha256") == INSPECTOR_HASH
                  and provenance.get("inventory_status") in ("complete", "complete_with_unknown_origins")
                  and all(provenance.get(k) is False for k in
                          ("legal_clearance", "corresponding_source_verified", "replacement_verified")),
                  "provenance-identity")
        h.require(type(archive.get("expected_inspector_recording")) is dict
                  and encoded(archive["expected_inspector_recording"]) == encoded(provenance.get("recording_smoke")),
                  "original-recording-inspector-join")
        h.require(type(notice.get("schema_version")) is int and notice["schema_version"] == 1
                  and notice.get("status") == "PASS"
                  and notice.get("scope") == "original-cpython-notice-byte-correspondence"
                  and type(notice.get("context")) is dict, "notice-incomplete")
        for key, expected in {"run_id": run["run_id"], "attempt": run["attempt"],
                              "workflow_sha": run["workflow_sha"], "source_commit": APP_COMMIT,
                              "source_tree": APP_TREE}.items():
            h.require(notice["context"].get(key) == expected, "notice-context")
        h.require(type(payload.get("schema_version")) is int and payload["schema_version"] == 1
                  and payload.get("status") == "PASS"
                  and payload.get("scope") == "data-only-final-reduced-payload-correspondence"
                  and payload.get("application_commit") == APP_COMMIT
                  and payload.get("application_tree") == APP_TREE
                  and payload.get("actual_checker") == {"bytes": PAYLOAD_CHECKER[0], "sha256": PAYLOAD_CHECKER[1]}
                  and payload.get("actual_source_inputs") == SOURCE_PINS, "payload-identity")
        h.require(all(payload.get(key) is False for key in
                      ("native_code_executed", "candidate_python_executed", "payload_modified",
                       "authenticated_origins", "tls_capability_verified", "hardware_verified",
                       "physical_stop_verified", "windows_10_verified", "legal_clearance", "bytes_delivered"))
                  and payload.get("archive_validator_required") is True
                  and payload.get("archive_validator_pass_report_bound") is True, "payload-qualifications")
        for view in ("onedir_policy", "zip_policy"):
            policy = payload.get(view)
            h.require(type(policy) is dict and type(policy.get("excluded_absent_count")) is int
                      and policy["excluded_absent_count"] == 47 and type(policy.get("required_present_count")) is int
                      and policy["required_present_count"] == 7 and type(policy.get("excluded_absent")) is list
                      and len(policy["excluded_absent"]) == 47
                      and type(policy.get("required_present")) is list and len(policy["required_present"]) == 7,
                      "payload-policy")
            h.require(hashlib.sha256(("\n".join(policy["excluded_absent"]) + "\n").encode("ascii")).hexdigest()
                      == EXCLUSIONS_SHA256, "payload-exclusion-list")
            for keeper in policy["required_present"]:
                h.keys(keeper, ("destination", "actual_path", "bytes", "sha256"))
                h.require(type(keeper["bytes"]) is int and keeper["bytes"] > 0, "payload-empty-keeper")
                h.info_value({k: keeper[k] for k in ("bytes", "sha256")}, 256 * MIB)
        h.require(encoded(payload["onedir_policy"]) == encoded(payload["zip_policy"]), "payload-policy-join")
        _, raw, _ = io.read(build / h.RECORDING, maximum=65536, collect=True)
        receipt = h.parse_json(raw)
        h.keys(receipt.get("artifacts"), ("archive", "spool"))
        names = set(h.FIXED_PAYLOAD | h.BUILD_METADATA)
        for kind, entry in receipt["artifacts"].items():
            h.keys(entry, ("name", "bytes", "sha256"))
            name = entry["name"]
            h.require(type(name) is str and (name == "recording.mtrec" if kind == "archive" else
                      re.fullmatch(r"recording-[0-9a-f]{32}\.mtspool", name)), "recording-artifact-name")
            names.add(h.WORK + "/" + name)
        h.require(len(names) == 11, "build-inventory")
        build_rows = []
        for name in sorted(names):
            identity, _, snapshot = io.read(build.joinpath(*h.relative(name)), maximum=h.build_cap(name))
            snapshots[build / name] = snapshot
            build_rows.append({"path": name, **identity})
        executable, _, snapshot = io.read(build / h.EXE, maximum=64 * MIB)
        snapshots[build / h.EXE] = snapshot
        manifest_identity = next(row for row in build_rows if row["path"] == h.BUILD_MANIFEST)
        h.require(archive.get("build_manifest_sha256") == manifest_identity["sha256"]
                  and provenance.get("build_manifest_sha256") == manifest_identity["sha256"], "manifest-join")
        h.require(provenance.get("executable_sha256") == executable["sha256"]
                  and type(provenance.get("executable_bytes")) is int
                  and provenance["executable_bytes"] == executable["bytes"], "provenance-executable-join")
        build_index = {row["path"]: row for row in build_rows}
        payload_names = set(names) - h.BUILD_METADATA
        h.require(archive.get("artifacts_sha256") == {k: build_index[k]["sha256"] for k in payload_names}
                  and encoded(archive.get("artifact_bytes")) == encoded({k: build_index[k]["bytes"] for k in payload_names})
                  and archive.get("checksum_file_sha256") == build_index["SHA256SUMS.txt"]["sha256"],
                  "archive-build-join")
        evidence_index = {row["path"]: row for row in evidence_rows}
        h.require(type(notice.get("input_hashes")) is dict, "notice-input-joins")
        for key, expected in {"manifest": manifest_identity["sha256"],
                              "archive": evidence_index["archive-verification.json"]["sha256"],
                              "provenance": evidence_index["provenance.json"]["sha256"]}.items():
            h.require(notice["input_hashes"].get(key) == expected, "notice-input-joins")
        notices = notice.get("cpython_notice")
        h.require(type(notices) is dict and notices.get("source_copy_equal") is True
                  and notices.get("matches_run17_hash") is True, "notice-byte-join")
        h.require(evidence_index["native-notice-source.bin"]["sha256"] ==
                  evidence_index["native-notice-copy.bin"]["sha256"] == NOTICE_HASH, "notice-copy-equality")
        for side in ("source", "copy"):
            entry = notices.get(side)
            expected = evidence_index["native-notice-" + side + ".bin"]
            h.require(type(entry) is dict and entry.get("status") == "retained_original"
                      and type(entry.get("bytes")) is int and entry["bytes"] == expected["bytes"]
                      and entry.get("sha256") == expected["sha256"], "notice-byte-join")
        _, manifest_raw, _ = io.read(build / h.BUILD_MANIFEST, maximum=4 * MIB, collect=True)
        manifest = h.parse_json(manifest_raw)
        h.require(manifest.get("python") == "3.13.16"
                  and type(manifest.get("files")) is dict
                  and manifest["files"].get("licenses/CPython-LICENSE.txt") == NOTICE_HASH, "notice-manifest-join")
        # Keep the unrelated original COLLECT hash association; the pinned
        # inspector remains the actual TOC parser and inventory validator.
        toc_info, _, toc_snapshot = io.read(build / "pyinstaller/motortester/COLLECT-00.toc", maximum=2 * MIB)
        snapshots[build / "pyinstaller/motortester/COLLECT-00.toc"] = toc_snapshot
        h.require(provenance.get("collect_toc_sha256") == toc_info["sha256"]
                  and notice["input_hashes"].get("toc") == toc_info["sha256"]
                  and provenance.get("pyinstaller_version") == "6.22.3", "collect-toc-join")
        for key, name in (("actual_source_zip", "source.zip"),
                          ("actual_candidate_zip", "Motortester-windows-x64-candidate.zip"),
                          ("actual_build_manifest", h.BUILD_MANIFEST), ("actual_checksums", "SHA256SUMS.txt")):
            expected = {k: build_index[name][k] for k in ("bytes", "sha256")}
            h.require(payload.get(key) == expected, "payload-build-join")
        h.require(payload.get("actual_archive_verification_report") ==
                  {k: evidence_index["archive-verification.json"][k] for k in ("bytes", "sha256")}
                  and payload.get("actual_artifacts") ==
                  {name: {k: build_index[name][k] for k in ("bytes", "sha256")} for name in payload_names},
                  "payload-archive-join")
        validate_runtime_receipt(h, io, build, reports_json["runtime-identity.json"], run, manifest, payload,
            {"manifest": manifest_identity["sha256"],
             "archive": evidence_index["archive-verification.json"]["sha256"],
             "payload": evidence_index["reduced-payload.json"]["sha256"]}, snapshots)
        launch_raw = encoded(launcher_manifest(h, executable, support_rows))
        h.require(len(launch_raw) <= h.MAX_PLAN, "launcher-manifest-limit")
        launch_relative = "support/launcher/launch-manifest.json"
        h.mkdirs(evidence, h.relative(launch_relative)[:-1])
        h.new_file(evidence.joinpath(*h.relative(launch_relative)), io, data=launch_raw)
        evidence_rows.append({"path": launch_relative, "bytes": len(launch_raw),
                              "sha256": hashlib.sha256(launch_raw).hexdigest()})
        coverage, inventory_info = notice_coverage(h, io, build, support, manifest, snapshots)
        copy(build / "dist/Motortester/licenses/inventory.json", "license-inventory.json", 4 * MIB, inventory_info)
        raw = encoded(coverage)
        h.new_file(evidence / "notice-coverage.json", io, data=raw)
        evidence_rows.append({"path": "notice-coverage.json", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        _, log_raw, _ = io.read(build / "build.log", maximum=16 * MIB, collect=True)
        selection = selection_observation(h, log_raw, payload["onedir_policy"]["excluded_absent"])
        h.require(selection["build_log"] == {k: build_index["build.log"][k] for k in ("bytes", "sha256")},
                  "selection-log-join")
        observation = encoded(selection)
        h.new_file(evidence / "runtime-selection.json", io, data=observation)
        evidence_rows.append({"path": "runtime-selection.json", "bytes": len(observation),
                              "sha256": hashlib.sha256(observation).hexdigest()})
    else:
        # Text diagnostics are a separate, always-incomplete branch. No archive,
        # EXE, PNG, notice .bin, recording payload, support body or wildcard.
        attempts = []
        candidates = [(build / name, "build-diagnostics/" + name, MIB) for name in DIAGNOSTIC_BUILD]
        candidates += [(source, target, MIB) for source, target, _ in original_reports]
        for source, target, cap in candidates:
            outcome = {"path": target}
            try:
                identity, raw, _ = io.read(source, maximum=cap, collect=True)
                text = raw.decode("utf-8")
                h.require(not any(ord(c) < 32 and c not in "\r\n\t" for c in text), "diagnostic-nontext")
                if source.suffix == ".json":
                    h.parse_json(raw)
                copy(source, target, cap, identity)
                outcome["status"] = "retained-original-text"
            except (OSError, ValueError, UnicodeError) as error:
                outcome.update(status="unavailable", reason=type(error).__name__)
            attempts.append(outcome)
        diagnostic = encoded({"status": "INCOMPLETE_DIAGNOSTICS_ONLY", "original_steps": run["steps"],
                              "attempted_explicit_files": attempts, "binary_files_retained": False,
                              "source_notice_acceptance_established": False})
        h.require(len(diagnostic) <= h.MAX_PLAN, "diagnostic-outcome-limit")
        h.new_file(evidence / "diagnostic-outcome.json", io, data=diagnostic)
        evidence_rows.append({"path": "diagnostic-outcome.json", "bytes": len(diagnostic),
                              "sha256": hashlib.sha256(diagnostic).hexdigest()})
        build_rows, executable = [], None
        build = work / "empty-build"
        build.mkdir()
    plan = encoded({"schema_version": 1, "mode": mode, "application_commit": APP_COMMIT,
                    "build_files": build_rows, "evidence_files": evidence_rows, "executable": executable})
    h.require(len(plan) <= h.MAX_PLAN, "expected-manifest-limit")
    h.load_plan(plan, mode)
    plan_path, kit, archive_path = work / "expected-manifest.json", work / "kit", work / "kit.zip"
    h.new_file(plan_path, io, data=plan)
    terminal = invoke(h, io, helper_root, "retain.py", [
        "--build-root", str(build), "--evidence-root", str(evidence), "--output", str(kit),
        "--expected-manifest", str(plan_path), "--expected-manifest-sha256", hashlib.sha256(plan).hexdigest(),
        "--mode", mode], work)
    wanted = "BYTES_RETAINED_ONLY" if mode == "accepted" else "INCOMPLETE_DIAGNOSTICS_ONLY"
    h.require(terminal.get("status") == wanted, "retention-terminal-status")
    outer = h.info_value(terminal.get("outer_manifest"), h.MAX_PLAN)
    io.read(kit / "OUTER_MANIFEST.json", outer)
    zipped = invoke(h, io, helper_root, "zip_kit.py", [
        "--kit", str(kit), "--output", str(archive_path), "--outer-manifest-sha256", outer["sha256"],
        "--mode", mode], work)
    h.require(zipped.get("status") == "KIT_ZIP_BYTES_VERIFIED_ONLY" and zipped.get("mode") == mode
              and zipped.get("outer_manifest") == outer and zipped.get("upload_download") == "not-performed"
              and zipped.get("distribution_authorized_by_this_helper") is False, "zip-terminal-status")
    zip_info = h.info_value(zipped.get("zip"), 514 * MIB)
    io.read(archive_path, zip_info)
    for path, snapshot in snapshots.items():
        h.require(h.stable(h.checked(path, "file")) == snapshot, "caller-source-changed")
    result = {"status": "LOCAL_KIT_BYTES_VERIFIED_ONLY" if mode == "accepted" else wanted,
              "mode": mode, "retention_exit_code": 0, "zip_exit_code": 0, "zip": zip_info,
              "outer_manifest": outer, "application_commit": APP_COMMIT, "retention_commit": RETENTION_COMMIT,
              "source_notice_review": run["source_notice_review"], "original_steps": run["steps"],
              "upload_download": "not-performed", "public_binary_publication_authorized": False,
              "windows_10_verified": False, "physical_verified": False}
    h.new_file(work / "caller-terminal.json", io, data=encoded(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("helper-root", "build-root", "reports-root", "support-root", "output-root", "workflow-run"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--mode", choices=("accepted", "diagnostics"), required=True)
    parser.add_argument("--support-manifest-sha256")
    parser.add_argument("--source-notice-review")
    args = vars(parser.parse_args())
    try:
        result = stage(**args)
    except Exception as error:
        print(json.dumps({"status": "CALLER_REJECTED", "error_type": type(error).__name__,
                          "partial_output_must_not_be_consumed": True}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    # A successful diagnostics copy must never turn the failed job green.
    return 0 if args["mode"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())

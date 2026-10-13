"""Bounded, synthetic GUI Record/Stop/Save/Open check, never device acceptance.

The validator is stdlib-only. Source identity is bound by build_windows' clean
checkout manifest, not guessed from an embedded Git revision or environment.
"""
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import stat
import struct
import sys
import time
from unittest.mock import patch
from uuid import UUID
import zlib
import zipfile

MAX_RECEIPT_BYTES = 65536
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 128 * 1024 * 1024
MAX_SEGMENTS = 16
PHASE_TIMEOUT = 12.0
TOTAL_TIMEOUT = 45.0
CLEANUP_TIMEOUT = 8.0
PHASES = ("entry", "gui", "connect", "record", "stop", "save", "open", "navigation", "shutdown", "complete")
REQUIRED_IMPORTS = ("motortester.gui", "motortester.gui_acquisition", "motortester.acquisition",
    "motortester.recording_bridge", "motortester.recording_controller", "motortester.session_archive",
    "motortester.device_emulator", "motortester.runtime", "motortester.session", "motortester.wire",
    "motortester.window", "motortester.recording", "motortester.model")
CHECKS = {"synthetic": True, "device_io_performed": False, "physical_stop_verified": False,
    "coverage": "segmented", "intersegment_device_gap": "unknown",
    "time_basis": "per_segment_nominal_unverified", "source_binding": "external-clean-build-manifest",
    "observed_bytes_and_provenance_match": True, "independent_pattern_match": True,
    "fresh_gui_open_and_selection": True}
CLEANUP = {"acquisition_native_joined": True, "writer_stopped": True,
           "windows_closed": True, "pending_io": False, "finalization_invalidated": False}


class SmokeFailure(RuntimeError):
    """Only fixed, public codes enter the receipt; never backend exception text."""


def _require(condition, code="invalid-evidence"):
    if not condition:
        raise SmokeFailure(code)


def work_path(output):
    return output.with_name(output.name + ".work")


def validate_output_path(raw):
    # Reuse the serial gate's Windows namespace/ADS/device-name guards. Native
    # POSIX absolute paths are allowed only for explicitly qualified development.
    from serial_import_smoke import validate_output_path as windows_path
    _require(type(raw) is str and 0 < len(raw) <= 180, "invalid-output-path")
    spelling = raw
    if os.name != "nt" and raw.startswith("/") and not raw.startswith("//"):
        spelling = raw[1:]
    windows_path(spelling)
    path = Path(raw).absolute()
    _require(len(str(path)) <= 180, "invalid-output-path")
    _parents(path)
    return path


def _directory(path):
    status = path.lstat()
    _require(stat.S_ISDIR(status.st_mode) and not (getattr(status, "st_file_attributes", 0)
             & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)), "redirected-directory")


def _parents(path):
    # Walk root-first so a linked/junction ancestor is rejected before any child
    # below it is opened. Windows directory junctions need not be is_symlink().
    for parent in reversed(path.absolute().parents):
        _directory(parent)


def _regular(path):
    _parents(path)
    status = path.lstat()
    _require(stat.S_ISREG(status.st_mode) and not (getattr(status, "st_file_attributes", 0)
             & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)), "nonregular-artifact")
    return status


def file_info(path, maximum=MAX_ARTIFACT_BYTES):
    expected = _regular(path)
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        actual = os.fstat(stream.fileno())
        _require(stat.S_ISREG(actual.st_mode) and (actual.st_dev, actual.st_ino) ==
                 (expected.st_dev, expected.st_ino), "replaced-artifact")
        while True:
            data = stream.read(min(65536, maximum + 1 - count))
            if not data:
                break
            digest.update(data)
            count += len(data)
            _require(count <= maximum, "artifact-too-large")
    _require(count > 0, "empty-artifact")
    return {"bytes": count, "sha256": digest.hexdigest()}


def _object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "duplicate-json-key")
        value[key] = item
    return value


def _keys(value, keys):
    _require(type(value) is dict and value.keys() == set(keys), "invalid-schema")


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high, "invalid-integer")


def _text(value, maximum=128):
    _require(type(value) is str and 0 < len(value) <= maximum
             and not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value), "invalid-text")


def _digest(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "invalid-hash")


def _info(value, maximum):
    _keys(value, ("bytes", "sha256"))
    _integer(value["bytes"], 1, maximum)
    _digest(value["sha256"])


def validate_report(output, *, executable, require_frozen_windows=True):
    """Validate fresh caller-owned receipt and its actual artifacts, no Qt import."""
    output = Path(output).absolute()
    expected = _regular(output)
    with output.open("rb") as stream:
        actual = os.fstat(stream.fileno())
        _require(stat.S_ISREG(actual.st_mode) and (actual.st_dev, actual.st_ino) ==
                 (expected.st_dev, expected.st_ino), "replaced-receipt")
        data = stream.read(MAX_RECEIPT_BYTES + 1)
    _require(0 < len(data) <= MAX_RECEIPT_BYTES, "receipt-size")
    report = json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                        parse_constant=lambda value: _require(False, "nonfinite-json"))
    _keys(report, ("schema_version", "status", "scope", "phase", "phases", "output", "workdir",
                   "runtime", "imports", "checks", "identity", "stop", "segments", "artifacts", "executable", "cleanup", "error"))
    _require(type(report["schema_version"]) is int and report["schema_version"] == 1
             and report["status"] == "passed" and report["phase"] == "complete" and report["error"] is None,
             "recording-smoke-not-passed")
    _require(report["output"] == output.name and report["workdir"] == work_path(output).name, "output-mismatch")
    _require(type(report["imports"]) is list and report["imports"] == list(REQUIRED_IMPORTS), "missing-module")
    for key, expected in (("checks", CHECKS), ("cleanup", CLEANUP)):
        _keys(report[key], expected)
        _require(all(type(report[key][k]) is type(v) and report[key][k] == v for k, v in expected.items()), "unverified-check")
    runtime = report["runtime"]
    _keys(runtime, ("platform", "os_name", "os_release", "os_version", "machine", "python", "implementation",
                    "python_bits", "qt", "pyside", "qt_platform", "frozen", "optimize", "application"))
    for key in set(runtime) - {"frozen", "python_bits", "optimize"}:
        _text(runtime[key], 256)
    _require(type(runtime["frozen"]) is bool, "invalid-frozen")
    _integer(runtime["python_bits"], 32, 64)
    _require(runtime["python_bits"] in (32, 64), "invalid-python-bits")
    _integer(runtime["optimize"], 0, 2)
    native = (runtime["platform"] == "win32" and runtime["os_name"] == "nt"
              and runtime["frozen"] is True and runtime["qt_platform"] == "windows")
    _require(report["scope"] == ("frozen-windows-recording" if native else "source-or-nonnative-development"), "scope-mismatch")
    if require_frozen_windows:
        _require(native and runtime["python"] == "3.13.16" and runtime["implementation"] == "CPython"
                 and runtime["python_bits"] == 64 and runtime["machine"].upper() in ("AMD64", "X86_64")
                 and runtime["qt"] == runtime["pyside"] == "6.12.0" and runtime["optimize"] == 0,
                 "native-frozen-windows-required")
    phases = report["phases"]
    _require(type(phases) is list and len(phases) == len(PHASES), "phase-history")
    previous = None
    for item, name in zip(phases, PHASES):
        _keys(item, ("name", "utc"))
        _require(item["name"] == name, "phase-history")
        _text(item["utc"], 64)
        stamp = datetime.fromisoformat(item["utc"])
        _require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0
                 and (previous is None or stamp >= previous), "phase-timestamp")
        previous = stamp
    identity = report["identity"]
    _keys(identity, ("recording_id", "session_id", "origin", "firmware_version", "protocol_version", "application_version"))
    for key in ("recording_id", "session_id"):
        _text(identity[key], 36)
        _require(str(UUID(identity[key])) == identity[key], "identity-uuid")
    _require(identity["origin"] == "simulator" and identity["application_version"] == runtime["application"], "identity-mismatch")
    for key in ("firmware_version", "protocol_version"):
        _text(identity[key])
    stop = report["stop"]
    _keys(stop, ("request_id", "ack_request_id", "wire_session_nonce", "generation", "last_window",
                 "status", "termination_reason", "evidence"))
    for key in ("request_id", "ack_request_id", "last_window"):
        _integer(stop[key], 1, 0xFFFFFFFE)
    _integer(stop["generation"], 2, 0xFFFFFFFE)
    _integer(stop["wire_session_nonce"], 1, 0xFFFFFFFFFFFFFFFF)
    _require(UUID(identity["session_id"]).int == stop["wire_session_nonce"], "session-nonce-mismatch")
    _require(type(stop["status"]) is int and stop["status"] == 0
             and stop["request_id"] == stop["ack_request_id"] and stop["request_id"] > stop["generation"]
             and stop["termination_reason"] == "user_stop" and stop["evidence"] == "stop_ack", "stop-mismatch")
    segments = report["segments"]
    _require(type(segments) is list and 2 <= len(segments) <= MAX_SEGMENTS, "segment-count")
    full = 0
    previous_sequence = previous_generation = 0
    for ordinal, entry in enumerate(segments):
        _keys(entry, ("ordinal", "kind", "sequence", "sample_count", "raw_sha256", "samples_sha256", "provenance"))
        _integer(entry["ordinal"], 0, MAX_SEGMENTS - 1)
        _require(entry["ordinal"] == ordinal and entry["kind"] in ("capture", "partial", "abort"), "segment-order")
        _integer(entry["sequence"], 1, 0xFFFFFFFE)
        _integer(entry["sample_count"], 0, 2048)
        _digest(entry["raw_sha256"]); _digest(entry["samples_sha256"])
        provenance = entry["provenance"]
        _keys(provenance, ("wire_session_nonce", "generation", "config_id", "acquisition_mode", "auto_timeout_ms",
            "host_elapsed_ms", "wire_version", "start_operation", "config_payload_hex", "begin_request_id",
            "begin_sequence", "terminal_kind", "terminal_request_id", "terminal_sequence", "end_samples_crc32",
            "abort_reason_code", "unavailable_reason"))
        _integer(provenance["generation"], 2, 0xFFFFFFFE)
        _integer(provenance["config_id"], 1, 0xFFFFFFFE)
        _require(entry["sequence"] > previous_sequence
                 and previous_generation < provenance["generation"] <= stop["generation"], "segment-chronology")
        previous_sequence, previous_generation = entry["sequence"], provenance["generation"]
        _integer(provenance["host_elapsed_ms"], 0, int(TOTAL_TIMEOUT * 1000))
        _require(type(provenance["wire_session_nonce"]) is int and provenance["wire_session_nonce"] == stop["wire_session_nonce"]
                 and type(provenance["acquisition_mode"]) is int and provenance["acquisition_mode"] == 1
                 and type(provenance["auto_timeout_ms"]) is int and provenance["auto_timeout_ms"] == 0
                 and provenance["wire_version"] == "0.1" and provenance["start_operation"] == "arm"
                 and provenance["config_payload_hex"] == struct.pack("<HBBBBHHHI", 1, 1, 0, 0, 0, 2048, 0, 0, 0).hex(), "provenance-mismatch")
        _require(type(provenance["terminal_request_id"]) is int and provenance["terminal_request_id"] == 0, "provenance-request")
        _integer(provenance["terminal_sequence"], 1, 0xFFFFFFFE)
        if entry["kind"] == "abort":
            _require(provenance["begin_request_id"] is None and provenance["begin_sequence"] is None
                     and provenance["unavailable_reason"] == "no_begin" and entry["sample_count"] == 0, "abort-provenance")
        else:
            _require(type(provenance["begin_request_id"]) is int and provenance["begin_request_id"] == 0, "provenance-request")
            _integer(provenance["begin_sequence"], 1, 0xFFFFFFFE)
            _require(provenance["terminal_sequence"] > provenance["begin_sequence"], "provenance-order")
        if entry["kind"] == "capture":
            full += 1
            _require(entry["sample_count"] == 2048 and provenance["terminal_kind"] == "end"
                     and provenance["abort_reason_code"] is None and provenance["unavailable_reason"] is None, "full-segment-mismatch")
            _integer(provenance["end_samples_crc32"], 0, 0xFFFFFFFF)
            expected = struct.pack("<2048H", *_expected_samples(entry["sequence"], provenance["generation"]))
            _require(entry["samples_sha256"] == hashlib.sha256(expected).hexdigest()
                     and provenance["end_samples_crc32"] == zlib.crc32(expected), "expected-pattern-mismatch")
        else:
            _require(provenance["terminal_kind"] == "abort" and provenance["end_samples_crc32"] is None, "partial-terminal")
            _integer(provenance["abort_reason_code"], 1, 6)
            if entry["kind"] == "partial":
                _require(provenance["unavailable_reason"] is None, "partial-provenance")
    _require(full >= 2, "too-few-full-segments")
    _require(stop["last_window"] == previous_sequence, "stop-window-mismatch")
    _keys(report["artifacts"], ("archive", "spool"))
    work = work_path(output)
    _directory(work)
    for key in ("archive", "spool"):
        item = report["artifacts"][key]
        _keys(item, ("name", "bytes", "sha256"))
        name = item["name"]
        _require(type(name) is str and (name == "recording.mtrec" if key == "archive"
                 else re.fullmatch(r"recording-[0-9a-f]{32}\.mtspool", name) is not None), "artifact-path")
        info = {k: item[k] for k in ("bytes", "sha256")}
        _info(info, MAX_ARTIFACT_BYTES)
        _require(file_info(work / name) == info, "artifact-hash-mismatch")
    # Bind each claimed segment to bytes in the hashed archive too. The GUI's
    # strict session_archive.load already checked the complete format; this
    # independent, bounded stdlib pass checks receipt/artifact consistency.
    with zipfile.ZipFile(work / "recording.mtrec") as archive:
        names = ["manifest.json"] + [f"segments/{e['ordinal']:08d}." +
            {"capture": "mtcap", "partial": "u16le", "abort": "json"}[e["kind"]] for e in segments] + ["diagnostics.json"]
        _require(archive.namelist() == names and all(info.compress_type == zipfile.ZIP_STORED
                 and info.file_size <= MAX_ARTIFACT_BYTES for info in archive.infolist()), "archive-members")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"), object_pairs_hook=_object,
            parse_constant=lambda value: _require(False, "nonfinite-json"))
        _require(type(manifest) is dict and manifest.get("identity") == identity
                 and manifest.get("termination") == {"reason": "user_stop", "stop_evidence": "stop_ack"}
                 and type(manifest.get("segment_count")) is int and manifest["segment_count"] == len(segments)
                 and type(manifest.get("segments")) is list and len(manifest["segments"]) == len(segments), "archive-manifest")
        for item, name, stored in zip(segments, names[1:-1], manifest["segments"]):
            raw = archive.read(name)
            _require(hashlib.sha256(raw).hexdigest() == item["raw_sha256"]
                     and type(stored) is dict and stored.get("provenance") == item["provenance"]
                     and stored.get("sha256") == item["raw_sha256"], "archive-segment-mismatch")
            if item["kind"] == "capture":
                _require(len(raw) >= 48, "capture-envelope")
                magic, size, count = struct.unpack_from("<8sII", raw)
                _require(magic == b"MTCAP\0\1\0" and count == 2048 and 0 < size <= 16384
                         and len(raw) == 16 + size + count * 2 + 32
                         and hashlib.sha256(raw[:-32]).digest() == raw[-32:], "capture-envelope")
                samples = raw[16 + size:-32]
            elif item["kind"] == "partial":
                samples = raw
            else:
                samples = b""
            _require(len(samples) == item["sample_count"] * 2
                     and hashlib.sha256(samples).hexdigest() == item["samples_sha256"], "archive-samples-mismatch")
    _info(report["executable"], MAX_EXECUTABLE_BYTES)
    _require(file_info(Path(executable) if require_frozen_windows else Path(executable).resolve(), MAX_EXECUTABLE_BYTES) == report["executable"], "executable-hash-mismatch")
    return report


def _pump(QtCore):
    QtCore.QCoreApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 5)
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    time.sleep(0.005)  # Release the GIL for Python worker slots/native cleanup.


def _wait(predicate, QtCore, deadline, failed=lambda: False):
    while time.monotonic() < deadline:
        _require(not failed(), "callback-failed")
        if predicate():
            return
        _pump(QtCore)
    raise SmokeFailure("deadline-exceeded")


def _settled(state):
    current = state.recording
    return bool(current is not None and current.snapshot is not None and current.ready_to_save
        and not current.error and not current.retired and not current.writer_busy and not current.saving
        and not current.snapshot.writer_pending and not current.snapshot.source_retention_required
        and not current.snapshot.finalization_invalidated and not current.snapshot.fault)


def _expected_samples(sequence, generation):
    # Independent oracle for the existing byte peer, not its sample buffer or a
    # second call to its implementation. Explicit boundary/rail probes matter.
    values = [(73 * i + 19 * sequence + 7 * generation) % 4096 for i in range(2048)]
    values[0], values[511], values[1023] = 2048, 0, 4095
    return tuple(values)


def _verify_segment(window, archive, ordinal, observed, recording, sa):
    entry, (before, before_provenance, source_item) = archive.entries[ordinal], observed
    raw = archive.read_segment(ordinal)
    _require(raw == before and entry.provenance == before_provenance
             and source_item.provenance == before_provenance, "stored-input-mismatch")
    p = entry.provenance
    expected = _expected_samples(entry.sequence, p.generation) if entry.kind != "abort" else ()
    if entry.kind == "capture":
        capture = recording.decode(raw)
        _require(type(source_item) is sa.FullSegment and raw == recording.encode(source_item.capture)
                 and capture == source_item.capture and capture.samples == expected
                 and capture.origin == "simulator" and capture.nominal_fs_hz == 50000
                 and capture.trigger_kind == "signal" and capture.trigger_index == 0
                 and window.capture == capture, "capture-mismatch")
        xs, ys = window.curve.getData()
        _require(tuple(ys) == expected and len(xs) == 2048
                 and all(abs(float(x) - i * 0.02) < 1e-9 for i, x in enumerate(xs)), "plot-mismatch")
        samples = struct.pack("<2048H", *expected)
        _require(p.end_samples_crc32 == zlib.crc32(samples), "sample-crc-mismatch")
    elif entry.kind == "partial":
        _require(type(source_item) is sa.PartialSegment and window.capture is None, "partial-mismatch")
        codes = tuple(value for fragment in source_item.fragments for value in fragment.samples)
        selected = tuple(expected[f.offset + i] for f in source_item.fragments for i in range(len(f.samples)))
        samples = struct.pack("<" + "H" * len(codes), *codes)
        _require(raw == samples and codes == selected and entry.partial == source_item.details(), "partial-input-mismatch")
        xs, ys = window.curve.getData()
        actual = () if ys is None else tuple(y for y in ys if math.isfinite(y))
        _require(actual == codes, "partial-plot-mismatch")
        positions = tuple((f.offset + i) * 0.02 for f in source_item.fragments for i in range(len(f.samples)))
        actual_x = () if ys is None else tuple(float(x) for x, y in zip(xs, ys) if math.isfinite(y))
        _require(len(actual_x) == len(positions) and all(abs(a - b) < 1e-9 for a, b in zip(actual_x, positions))
                 and (ys is None or sum(not math.isfinite(y) for y in ys) == len(source_item.fragments)), "partial-time-gaps-mismatch")
    else:
        _require(type(source_item) is sa.AbortEvent and window.capture is None, "abort-mismatch")
        samples = b""
        _, ys = window.curve.getData()
        _require(ys is None or len(ys) == 0, "abort-plot-mismatch")
    _require("wire nonce" in window.details_label.toolTip()
             and "неизвестны" in window.archive_label.text(), "viewer-provenance-missing")
    return {"ordinal": ordinal, "kind": entry.kind, "sequence": entry.sequence,
        "sample_count": len(samples) // 2, "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "samples_sha256": hashlib.sha256(samples).hexdigest(), "provenance": asdict(p)}


def run(raw_output):
    report = {"schema_version": 1, "status": "running", "scope": "source-or-nonnative-development",
        "phase": "entry", "phases": [], "output": None, "workdir": None, "runtime": None,
        "imports": [], "checks": None, "identity": None, "stop": None, "segments": [], "artifacts": None,
        "executable": None, "cleanup": None, "error": None}
    stream = window = viewer = bridge = adapter = QtCore = None
    previous_hook = sys.excepthook
    callback_failed = False
    total_deadline = time.monotonic() + TOTAL_TIMEOUT
    stack = ExitStack()

    def phase(name):
        report["phase"] = name
        report["phases"].append({"name": name, "utc": datetime.now(timezone.utc).isoformat()})
        save()

    def save():
        if stream is not None:
            data = (json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("ascii")
            _require(len(data) <= MAX_RECEIPT_BYTES, "receipt-too-large")
            stream.seek(0); stream.write(data); stream.truncate(); stream.flush()

    def failed_callback(*unused):
        nonlocal callback_failed
        callback_failed = True

    def wait(predicate):
        _wait(predicate, QtCore, min(total_deadline, time.monotonic() + PHASE_TIMEOUT), lambda: callback_failed)

    try:
        output = validate_output_path(raw_output)
        work = work_path(output)
        _require(not os.path.lexists(work), "existing-workdir")
        stream = output.open("x+b")
        report["output"], report["workdir"] = output.name, work.name
        phase("entry")
        work.mkdir(mode=0o700, exist_ok=False)
        sys.excepthook = failed_callback
        from PySide6 import QtCore, QtWidgets, __version__ as pyside_version
        from motortester import __version__, acquisition, recording, session_archive as sa
        from motortester import gui_acquisition
        from motortester.gui import MainWindow
        from motortester.runtime import AcquisitionRuntime
        from motortester.session import EventKind
        from motortester.transport import SerialTransport
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        app.setQuitOnLastWindowClosed(False)
        report["runtime"] = dict(platform=sys.platform, os_name=os.name, os_release=platform.release(),
            os_version=platform.version(), machine=platform.machine(), python=platform.python_version(),
            implementation=platform.python_implementation(), python_bits=struct.calcsize("P") * 8,
            qt=QtCore.qVersion(), pyside=pyside_version, qt_platform=app.platformName(),
            frozen=getattr(sys, "frozen", False) is True, optimize=sys.flags.optimize, application=__version__)
        if sys.platform == "win32" and os.name == "nt" and report["runtime"]["frozen"] and app.platformName() == "windows":
            report["scope"] = "frozen-windows-recording"
        _require(all(name in sys.modules for name in REQUIRED_IMPORTS), "required-module-missing")
        report["imports"] = list(REQUIRED_IMPORTS)
        report["executable"] = file_info(Path(sys.executable).resolve(), MAX_EXECUTABLE_BYTES)
        observed, stops, stop_acks = [], [], []
        original_append, original_stop, original_events = sa.SessionSpool._append, AcquisitionRuntime.stop, acquisition._AcquisitionWorker._events

        def append(spool, entry, data, *, source_item=None):
            _require(len(observed) < MAX_SEGMENTS, "too-many-segments")
            observed.append((bytes(data), entry.provenance, source_item))
            return original_append(spool, entry, data, source_item=source_item)

        def stop(runtime):
            result = original_stop(runtime)
            snapshot = runtime.snapshot()
            _require(len(stops) < MAX_SEGMENTS, "too-many-stop-requests")
            stops.append((snapshot.pending_stop_id, snapshot.session, snapshot.generation))
            return result

        def events(worker, values):
            for event in values:
                if event.kind is EventKind.STOPPED:
                    _require(len(stop_acks) < MAX_SEGMENTS, "too-many-stop-acks")
                    stop_acks.append(event)
            return original_events(worker, values)

        def forbid_device(*unused, **kwargs):
            raise SmokeFailure("device-access-forbidden")

        # Instrument only this smoke process; no application feature or global
        # user-data directory changes. All original methods run unchanged.
        stack.enter_context(patch.object(gui_acquisition, "recovery_directory", return_value=work))
        stack.enter_context(patch.object(sa.SessionSpool, "_append", append))
        stack.enter_context(patch.object(AcquisitionRuntime, "stop", stop))
        stack.enter_context(patch.object(acquisition._AcquisitionWorker, "_events", events))
        stack.enter_context(patch.object(SerialTransport, "__init__", forbid_device))
        window = MainWindow(); window.show(); window.show_acquisition()
        bridge, panel = window._acquisition, window._acquisition_dialog
        phase("gui")
        panel.source.setCurrentIndex(panel.source.findData("emulator"))
        _require(panel.source.currentData() == "emulator", "wrong-source")
        panel.connect_button.click()
        wait(panel.record_button.isEnabled)
        _require(bridge.state.source == "emulator" and not bridge._worker.device_used, "wrong-source")
        phase("connect")
        panel.mode.setCurrentIndex(panel.mode.findData(1)); panel.edge.setCurrentIndex(panel.edge.findData(0))
        panel.level.setValue(2048); panel.simulate.setChecked(True)
        panel.record_button.click()
        wait(lambda: bridge.state.recording is not None and bridge.state.recording.snapshot is not None
             and bridge.state.recording.snapshot.committed_segments >= 2)
        adapter = bridge._worker.recording
        phase("record")
        panel.stop_button.click()
        wait(lambda: _settled(bridge.state) and panel.save_recording_button.isEnabled())
        snapshot = bridge.state.recording.snapshot
        _require(snapshot.termination_reason == "user_stop" and snapshot.stop_evidence == "stop_ack"
                 and snapshot.user_stop_requested and stops and stop_acks, "stop-unconfirmed")
        request, nonce, generation = stops[-1]
        event = stop_acks[-1]
        _require(event.frame is not None and event.request_id == request and event.generation == generation
                 and event.frame.session == nonce and event.frame.request_id == request
                 and len(event.frame.payload) == 12, "stop-identity-mismatch")
        ack_generation, last_window, status = struct.unpack("<IIB3x", event.frame.payload)
        _require(ack_generation == generation and last_window == event.window_id and status == 0, "stop-payload-mismatch")
        report["stop"] = dict(request_id=request, ack_request_id=event.request_id, wire_session_nonce=nonce,
            generation=generation, last_window=last_window, status=status, termination_reason="user_stop", evidence="stop_ack")
        phase("stop")
        spool = Path(bridge.state.recording.spool_path)
        _require(spool.parent == work and re.fullmatch(r"recording-[0-9a-f]{32}\.mtspool", spool.name) is not None, "spool-outside-workdir")
        archive_path = work / "recording.mtrec"
        _require(window.save_recording_path(archive_path) is True, "save-not-admitted")
        wait(lambda: _settled(bridge.state) and bridge.state.recording.saved_path == str(archive_path))
        phase("save")
        archive = sa.load(archive_path)
        _require(not archive.issues and len(archive.entries) == len(observed)
                 and sum(entry.kind == "capture" for entry in archive.entries) >= 2
                 and archive.outcome.reason == "user_stop" and archive.outcome.stop_evidence == "stop_ack", "archive-mismatch")
        # A fresh MainWindow cannot display the acquisition's last in-memory Capture.
        viewer = MainWindow(); viewer.show(); viewer.open_path(archive_path)
        wait(lambda: not viewer.busy)
        _require(viewer._archive is not None and viewer.opened_from_disk
                 and viewer.segment_selector.count() == len(observed)
                 and viewer._archive.identity == archive.identity and viewer._archive.entries == archive.entries, "open-failed")
        phase("open")
        for ordinal in range(len(observed)):
            viewer.segment_selector.setCurrentIndex(ordinal)
            wait(lambda: not viewer.busy and viewer._archive_ordinal == ordinal)
            report["segments"].append(_verify_segment(viewer, archive, ordinal, observed[ordinal], recording, sa))
        phase("navigation")
        _require(_settled(bridge.state), "unsettled-writer")
        report["checks"] = dict(CHECKS)
        report["identity"] = asdict(archive.identity)
        report["artifacts"] = {"archive": {"name": "recording.mtrec", **file_info(archive_path)},
                               "spool": {"name": spool.name, **file_info(spool)}}
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = str(error) if type(error) is SmokeFailure else "operation-failed"
    finally:
        cleanup_ok = True
        try:
            if QtCore is not None:
                if bridge is not None:
                    if adapter is None and bridge._worker is not None:
                        adapter = bridge._worker.recording
                    bridge.cancel_recording_save()
                    bridge.shutdown()
                    _wait(lambda: not bridge.running and bridge._closed
                          and (adapter is None or not adapter._writer.running), QtCore,
                          time.monotonic() + CLEANUP_TIMEOUT)
                    _require(not bridge.state.close_failed, "cleanup-failed")
                for current in (viewer, window):
                    if current is not None:
                        # Smoke has already retained all failure evidence; never
                        # open a modal operator-confirmation dialog on teardown.
                        with patch.object(current, "confirm_recording_exit", return_value=True):
                            current.close()
                        _require(not current.isVisible() and not current.busy, "cleanup-pending-io")
                _pump(QtCore)
                final = adapter.state if adapter is not None else None
                snapshot = final.snapshot if final is not None else None
                pending = bool(final is not None and (final.writer_busy or final.saving
                    or (snapshot is not None and (snapshot.writer_pending or snapshot.source_retention_required))))
                invalidated = bool(snapshot is not None and snapshot.finalization_invalidated)
                report["cleanup"] = {
                    "acquisition_native_joined": bridge is not None and not bridge.running and bridge._closed,
                    "writer_stopped": adapter is not None and not adapter._writer.running,
                    "windows_closed": all(current is not None and not current.isVisible() for current in (window, viewer)),
                    "pending_io": pending or any(current is not None and current.busy for current in (window, viewer)),
                    "finalization_invalidated": invalidated,
                }
                if report["status"] != "failed":
                    _require(report["cleanup"] == CLEANUP and final is not None and final.retired
                             and snapshot is not None and snapshot.phase.value == "finished"
                             and snapshot.fault is None and final.error is None, "cleanup-state-unconfirmed")
        except BaseException:
            cleanup_ok = False
            report["status"], report["error"] = "failed", "cleanup-unconfirmed"
        stack.close()
        sys.excepthook = previous_hook
        if callback_failed:
            report["status"], report["error"] = "failed", "callback-failed"
        try:
            if report["status"] != "failed" and cleanup_ok:
                _require(report["cleanup"] == CLEANUP, "cleanup-unconfirmed")
                phase("shutdown")
                report["status"] = "passed"
                phase("complete")
                # Run exactly the build validator; development cannot pass its
                # default native Windows requirement.
                validate_report(output, executable=sys.executable, require_frozen_windows=False)
            else:
                save()
        except BaseException:
            report["status"], report["error"] = "failed", "receipt-validation-failed"
            try:
                save()
            except BaseException:
                pass
        if stream is not None:
            try:
                stream.close()
            except OSError:
                report["status"] = "failed"
    return 0 if report["status"] == "passed" else 1

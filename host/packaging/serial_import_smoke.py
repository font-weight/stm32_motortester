"""Import-only frozen Windows gate. Never instantiate or enumerate a device.

Source/non-Windows execution is a negative check, never frozen-build evidence.
The report is small, strict and exclusively created; an existing output is kept.
"""
import importlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys
from types import ModuleType

MAX_REPORT_BYTES = 4096
REQUIRED_IMPORTS = ("serial", "serial.serialutil", "serial.serialwin32", "serial.win32",
                    "motortester.transport")
SUCCESS_REPORT = {
    "schema_version": 1,
    "scope": "frozen-windows-serial-import-only",
    "status": "passed",
    "platform": "win32",
    "os_name": "nt",
    "frozen": True,
    "pyserial_version": "3.5",
    "imports": list(REQUIRED_IMPORTS),
    "serial_backend": "serial.serialwin32.Serial",
    "transport_class": "motortester.transport.SerialTransport",
    "device_io_performed": False,
}


def validate_output_path(raw: str) -> Path:
    """Accept ordinary local Windows paths, rejecting device names and ADS.

    Check spelling before Path normalization, on every OS, so tests can exercise
    Windows hazards without passing them to an operating-system file API.
    """
    if type(raw) is not str or not raw or len(raw) > 240:
        raise ValueError("Output must be a short ordinary .json file path")
    normalized = raw.replace("\\", "/")
    # No UNC, device namespaces, root-relative paths or drive-relative paths.
    if normalized.startswith("/"):
        raise ValueError("Output must be local, relative or drive-absolute")
    tail = normalized
    if len(tail) >= 2 and tail[1] == ":":
        if not re.match(r"^[A-Za-z]:/", tail):
            raise ValueError("Drive-relative output is unsupported")
        tail = tail[3:]
    for part in tail.split("/"):
        if (not part or part in (".", "..") or part[-1:] in (" ", ".")
                or any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in part)):
            raise ValueError("Unsafe output component")
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if (stem in {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"}
                or re.fullmatch(r"(?:COM|LPT)[0-9¹²³]", stem)):
            raise ValueError("Windows device output is forbidden")
    if PureWindowsPath(raw).suffix.lower() != ".json":
        raise ValueError("Output must have a .json suffix")
    return Path(raw)


def _check_imports() -> dict:
    if sys.platform != "win32" or os.name != "nt" or getattr(sys, "frozen", False) is not True:
        return {"schema_version": 1, "scope": "source-or-non-windows",
                "status": "failed", "error": "requires-frozen-windows"}
    failure = {"schema_version": 1, "scope": "frozen-windows-serial-import-only",
               "status": "failed"}
    try:
        modules = {name: importlib.import_module(name) for name in REQUIRED_IMPORTS}
    except Exception:
        return failure | {"error": "required-import-failed"}
    if any(not isinstance(module, ModuleType) or module.__name__ != name
           for name, module in modules.items()):
        return failure | {"error": "unexpected-import-module"}
    serial = modules["serial"]
    if getattr(serial, "VERSION", None) != "3.5" or getattr(serial, "__version__", None) != "3.5":
        return failure | {"error": "unexpected-pyserial-version"}
    backend = getattr(modules["serial.serialwin32"], "Serial", None)
    if (not isinstance(backend, type) or getattr(serial, "Serial", None) is not backend
            or backend.__module__ != "serial.serialwin32" or backend.__qualname__ != "Serial"):
        return failure | {"error": "unexpected-serial-backend"}
    transport = getattr(modules["motortester.transport"], "SerialTransport", None)
    if (not isinstance(transport, type) or transport.__module__ != "motortester.transport"
            or transport.__qualname__ != "SerialTransport"):
        return failure | {"error": "unexpected-transport-class"}
    return dict(SUCCESS_REPORT)


def run(output: str) -> int:
    try:
        path = validate_output_path(output)
        # Exclusive create is the no-clobber decision, not a racy exists() check.
        # Parent directories must already exist. Never remove failed evidence.
        with path.open("xb") as stream:
            report = _check_imports()
            data = (json.dumps(report, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
            if len(data) > MAX_REPORT_BYTES:
                raise ValueError("Import report exceeded its bound")
            stream.write(data)
        return 0 if report["status"] == "passed" else 1
    except (OSError, ValueError):
        return 1


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate report key")
        result[key] = value
    return result


def validate_report(path: Path) -> dict:
    """Fail closed on absent, oversized, malformed or non-gate reports."""
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Import report is not a regular file")
        data = stream.read(MAX_REPORT_BYTES + 1)
    if not data or len(data) > MAX_REPORT_BYTES:
        raise ValueError("Import report size is invalid")
    report = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    if (type(report) is not dict or report.keys() != SUCCESS_REPORT.keys()
            or any(type(report[key]) is not type(expected) or report[key] != expected
                   for key, expected in SUCCESS_REPORT.items())):
        raise ValueError("Frozen Windows serial import gate did not pass")
    return report

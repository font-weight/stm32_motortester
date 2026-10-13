"""PyInstaller entry point with a noninteractive, observable screenshot smoke."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

MAX_RECEIPT_BYTES = 65536
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SMOKE_PHASES = ("entry", "gui-import", "gui-imported", "screenshot", "png-validation", "complete")


def receipt_path(output: Path) -> Path:
    return output.with_name(output.name + ".smoke.json")


def _png_bytes(output: Path) -> bytes:
    with output.open("rb") as stream:
        data = stream.read(MAX_PNG_BYTES + 1)
    if not 8 < len(data) <= MAX_PNG_BYTES or not data.startswith(PNG_SIGNATURE):
        raise ValueError("Smoke output is not a bounded PNG")
    return data


def _inspect_png(output: Path) -> dict:
    # The source GUI has already been imported inside the exception boundary.
    # Decode the actual result; a signature or mere file existence is insufficient.
    from PySide6 import QtCore, QtGui
    data = _png_bytes(output)
    buffer = QtCore.QBuffer()
    buffer.setData(data)
    if not buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
        raise ValueError("Cannot inspect smoke PNG")
    reader = QtGui.QImageReader(buffer, b"PNG")
    size = reader.size()
    if (not size.isValid() or size.width() > 8192 or size.height() > 8192
            or size.width() * size.height() > MAX_IMAGE_PIXELS):
        raise ValueError("Smoke PNG dimensions are invalid")
    decoded = reader.read()
    if decoded.isNull() or decoded.size() != size:
        raise ValueError("Smoke PNG cannot be decoded")
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "width": size.width(), "height": size.height()}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate smoke receipt key")
        result[key] = value
    return result


def validate_smoke_result(output: Path, *, require_frozen_windows=True) -> dict:
    """Build-side validation without importing Qt into the bootstrap Python."""
    with receipt_path(output).open("rb") as stream:
        data = stream.read(MAX_RECEIPT_BYTES + 1)
    if not data or len(data) > MAX_RECEIPT_BYTES:
        raise ValueError("Invalid smoke receipt size")
    report = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    keys = {"schema_version", "status", "phase", "phases", "platform", "frozen",
            "qt_platform", "output", "png", "error"}
    if (type(report) is not dict or report.keys() != keys
            or type(report["schema_version"]) is not int or report["schema_version"] != 1
            or report["status"] != "passed" or report["phase"] != "complete"
            or report["error"] is not None or report["output"] != output.name
            or type(report["frozen"]) is not bool or type(report["platform"]) is not str
            or type(report["qt_platform"]) is not str):
        raise ValueError("Screenshot smoke did not pass")
    if require_frozen_windows and (report["platform"] != "win32" or report["frozen"] is not True
                                  or report["qt_platform"] != "windows"):
        raise ValueError("Native frozen Windows screenshot evidence is required")
    phases = report["phases"]
    if type(phases) is not list or len(phases) != len(SMOKE_PHASES):
        raise ValueError("Incomplete smoke phase history")
    for item, expected in zip(phases, SMOKE_PHASES):
        if (type(item) is not dict or item.keys() != {"name", "utc"}
                or item["name"] != expected or type(item["utc"]) is not str):
            raise ValueError("Invalid smoke phase history")
        if datetime.fromisoformat(item["utc"]).utcoffset() is None:
            raise ValueError("Smoke timestamps need a timezone")
    info = report["png"]
    if (type(info) is not dict or info.keys() != {"sha256", "bytes", "width", "height"}
            or any(type(info[key]) is not int for key in ("bytes", "width", "height"))
            or not 1 <= info["width"] <= 8192 or not 1 <= info["height"] <= 8192
            or info["width"] * info["height"] > MAX_IMAGE_PIXELS):
        raise ValueError("Invalid smoke PNG description")
    png = _png_bytes(output)
    if info["bytes"] != len(png) or info["sha256"] != hashlib.sha256(png).hexdigest():
        raise ValueError("Smoke PNG differs from the decoded image")
    return report


def _screenshot_smoke(raw_output: str) -> int:
    output = Path(raw_output)
    report = {"schema_version": 1, "status": "running", "phase": "entry", "phases": [],
              "platform": sys.platform, "frozen": getattr(sys, "frozen", False) is True,
              "qt_platform": None, "output": output.name, "png": None, "error": None}
    stream = None
    previous_hook = sys.excepthook
    callback_error = None

    def save_phase(phase):
        report["phase"] = phase
        report["phases"].append({"name": phase, "utc": datetime.now(timezone.utc).isoformat()})
        encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(encoded) > MAX_RECEIPT_BYTES:
            raise ValueError("Smoke receipt exceeded its bound")
        stream.seek(0)
        stream.write(encoded)
        stream.truncate()
        stream.flush()

    def failed_callback(kind, value, tb):
        nonlocal callback_error
        # Do not delegate a Qt callback exception to a windowed error dialog.
        if callback_error is not None:
            return
        callback_error = (kind, value, tb)
        report["status"] = "failed"
        report["error"] = {"type": kind.__name__, "message": str(value)[:2048],
                           "traceback": "".join(traceback.format_exception(kind, value, tb))[-8192:]}
        try:
            save_phase("callback-failed")
        except Exception:
            pass

    try:
        if output.suffix.lower() != ".png" or len(raw_output) > 4096:
            raise ValueError("Screenshot smoke needs a .png output path")
        if os.path.lexists(output):
            raise FileExistsError("Smoke PNG output already exists")
        # Exclusive receipt creation prevents reuse of stale successful evidence.
        stream = receipt_path(output).open("x+b")
        save_phase("entry")
        # Reserve the PNG too. A concurrent creator must not be overwritten by
        # Qt's save(); this empty file now belongs to this smoke invocation.
        with output.open("xb"):
            pass
        sys.excepthook = failed_callback
        save_phase("gui-import")
        from motortester.gui import main as gui_main
        save_phase("gui-imported")
        save_phase("screenshot")
        code = gui_main(["--screenshot", str(output)])
        if callback_error is not None:
            raise RuntimeError("Qt callback failed during screenshot") from callback_error[1]
        if type(code) is not int or code != 0:
            raise RuntimeError(f"Screenshot returned nonzero or invalid exit code: {code!r}")
        save_phase("png-validation")
        report["png"] = _inspect_png(output)
        from PySide6 import QtCore
        app = QtCore.QCoreApplication.instance()
        if app is None:
            raise RuntimeError("Screenshot did not create a Qt application")
        report["qt_platform"] = str(app.platformName())[:128]
        report["status"] = "passed"
        save_phase("complete")
        return 0
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = {"type": type(error).__name__, "message": str(error)[:2048],
                           "traceback": traceback.format_exc(limit=15)[-8192:]}
        if stream is not None:
            try:
                save_phase("failed")
            except Exception:
                pass  # A damaged/unwritable receipt can never satisfy the build.
        if sys.stderr is not None:
            try:
                print("Screenshot smoke failed; see " + str(receipt_path(output)), file=sys.stderr)
            except Exception:
                pass
        return 1
    finally:
        sys.excepthook = previous_hook
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def main() -> int:
    # Keep this branch before any Qt/application import. No other CLI is changed.
    if sys.argv[1:2] == ["--serial-import-smoke"]:
        if len(sys.argv) != 3:
            if sys.stderr is not None:
                print("Usage: --serial-import-smoke OUTPUT.json", file=sys.stderr)
            return 2
        from serial_import_smoke import run
        return run(sys.argv[2])
    if sys.argv[1:2] == ["--screenshot"]:
        if len(sys.argv) != 3:
            return 2
        return _screenshot_smoke(sys.argv[2])
    if sys.argv[1:2] == ["--recording-smoke"]:
        if len(sys.argv) != 3:
            return 2
        from recording_smoke import run
        return run(sys.argv[2])
    from motortester.gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())

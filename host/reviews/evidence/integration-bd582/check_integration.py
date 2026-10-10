"""Independent Critic-B integration probe; synthetic Linux/offscreen evidence.

Run with candidate host/ on PYTHONPATH. No production encoder/generator creates
the inputs. All checks are explicit so the same probe remains effective under -O.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import struct
import time
from uuid import UUID

import numpy as np
from PySide6 import QtGui, QtTest, QtWidgets
from motortester.gui import MainWindow
from motortester.model import CaptureError
from motortester.recording import export_csv, load, save
from motortester.wire_demo import capture_from_synthetic_stream, save_synthetic_stream

SESSION = 0x53494D0000000001
SOURCE_SHA = "124b3f32e1e7f7282bce283baa4c9406d4d72950"
STAMP = "2026-10-10T00:00:00+00:00"  # Fixture time, never an observation time.


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def crc32(data):
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def cobs(data):
    result = bytearray([0])
    marker, code = 0, 1
    for value in data:
        if value == 0:
            result[marker] = code
            marker = len(result)
            result.append(0)
            code = 1
        else:
            result.append(value)
            code += 1
            if code == 255:
                result[marker] = code
                marker = len(result)
                result.append(0)
                code = 1
    result[marker] = code
    return bytes(result) + b"\0"


def frame(kind, sequence, payload):
    raw = struct.pack("<BBBBHHQII", 0, 1, kind, 0, len(payload), 0,
                      SESSION, sequence, 0) + payload
    return cobs(raw + struct.pack("<I", crc32(raw)))


def fixture(window, *, wrong_sample_crc=False):
    # Independent integer sawtooth and control prefix, not the app's sine demo.
    values = tuple([0, window, 256, 4095] +
                   [(index * 67 + window * 101) % 4096 for index in range(4, 2048)])
    raw = struct.pack("<2048H", *values)
    payloads = [(144, struct.pack("<IIIHHHHBBBBHHIHHI", 2, window, 1,
                                  1, 2048, 512, 512, 0, 0, 0, 0, 2048,
                                  2, 72000000, 71, 19, 0))]
    # Nine blocks, alternate legal partition proves no dependency on 250x8+48.
    for offset in range(0, 2048, 249):
        block = values[offset:offset + 249]
        payloads.append((145, struct.pack("<IIHH", 2, window, offset, len(block))
                         + struct.pack(f"<{len(block)}H", *block)))
    checksum = crc32(raw) ^ int(wrong_sample_crc)
    payloads.append((146, struct.pack("<IIHHI", 2, window, 2048, 0, checksum)))
    sequence = 11 * (window - 1) + 1
    packets = [frame(kind, sequence + index, payload)
               for index, (kind, payload) in enumerate(payloads)]
    require(len(packets) == 11, "Independent fixture must have eleven frames")
    return values, raw, b"".join(packets), packets, sequence


def wait_for_file(window, app):
    deadline = time.monotonic() + 10
    while window.busy and time.monotonic() < deadline:
        QtTest.QTest.qWait(5)
    require(not window.busy, "GUI file operation failed to terminate")
    app.processEvents()
    require("Не удалось" not in window.status_label.text(), window.status_label.text())


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    require(crc32(b"123456789") == 0xCBF43926, "Independent CRC reference invalid")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    app.processEvents()
    records = []
    for index in range(1, 21):
        expected, raw, data, packets, sequence = fixture(index)
        chunk = (1, 2, 31, 97, 255, 543, 4096)[(index - 1) % 7]
        name = f"synthetic-{index:02d}"
        wire_path, path = output / f"{name}.wire", output / f"{name}.mtcap"
        wire_path.write_bytes(data)
        decoded = save_synthetic_stream(path, data, first_sequence=sequence,
                                        chunk_size=chunk, received_at_utc=STAMP)
        require(decoded.samples == expected, f"Raw mismatch: window {index}")
        require(struct.pack("<2048H", *decoded.samples) == raw, "Little-endian raw bytes changed")
        require(decoded.sequence == index and decoded.session_id == str(UUID(int=SESSION)), "Identity changed")
        require(decoded.origin == "simulator" and decoded.firmware_version is None, "Invented device provenance")
        require(decoded.protocol_version == f"draft-0.1@{SOURCE_SHA}", "Draft provenance changed")
        require(decoded.nominal_fs_hz == 50000 and decoded.trigger_index == 512, "Nominal timing changed")
        require(decoded.quality_flags == ("rail_observed", "draft_protocol", "nominal_timing_unverified"), "Quality provenance changed")
        file_before = path.read_bytes()
        window.open_path(path)
        wait_for_file(window, app)
        capture = window.capture
        require(capture == decoded and capture is not decoded, "GUI did not independently reopen disk file")
        require(window.current_path == path and window.opened_from_disk, "GUI disk identity incorrect")
        x_values, y_values = window.curve.getData()
        require(np.array_equal(y_values, np.asarray(expected)), "Displayed raw points differ")
        require(np.allclose(x_values, [(i - 512) / 50 for i in range(2048)], rtol=0, atol=1e-13), "Displayed nominal time differs")
        require("СИНТЕТИЧЕСКИЕ" in window.origin_badge.text(), "Missing visible synthetic provenance")
        window.cursor_toggle.setChecked(True)
        window.cursor_lines[0].setValue(-10.24)
        window.cursor_lines[1].setValue(-10.22)
        app.processEvents()
        require(window._cursor_indices == [0, 1], "Cursors did not snap to control samples")
        window.plot.setXRange(-10.24, -2.0, padding=0)
        app.processEvents()
        view_before = window.plot.viewRange()
        png_path = output / f"{name}.png"
        window.export_png_path(png_path)
        wait_for_file(window, app)
        image = QtGui.QImage(str(png_path))
        require(not image.isNull(), "PNG cannot be reopened")
        require("синтетические" in image.text("Provenance"), "PNG provenance lost")
        require("uncalibrated" in image.text("Scale") and image.text("RawSampleCount") == "2048", "PNG scale metadata incorrect")
        require(window.capture is capture and capture.samples == expected, "PNG/cursors changed raw capture")
        require(window.current_path == path and window.opened_from_disk, "PNG changed recording identity")
        require(window.plot.viewRange() == view_before, "PNG changed visible scale")
        require(path.read_bytes() == file_before, "GUI overwrote original recording")
        # Actual GUI save-as: a second file remains byte-for-byte identical.
        copy_path = output / f"{name}-copy.mtcap"
        window.save_path(copy_path)
        wait_for_file(window, app)
        require(copy_path.read_bytes() == file_before and load(copy_path) == decoded, "GUI resave changed bytes or metadata")
        csv_path = output / f"{name}.csv"
        export_csv(csv_path, decoded)
        with csv_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        require(tuple(int(row["adc_code"]) for row in rows) == expected, "CSV samples changed")
        require(all(row["origin"] == "simulator" for row in rows), "CSV provenance changed")
        if index in (1, 20):
            require(window.grab().save(str(output / f"{name}-viewer.png")), "Viewer screenshot failed")
        records.append({"window_id": index, "first_sequence": sequence, "chunk_size": chunk,
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "wire_sha256": hashlib.sha256(data).hexdigest(),
                        "recording_sha256": hashlib.sha256(file_before).hexdigest(),
                        "png_sha256": hashlib.sha256(png_path.read_bytes()).hexdigest(),
                        "png_size": [image.width(), image.height()]})

    # Corruption never creates a successful file or replaces the current GUI capture.
    expected, raw, data, packets, sequence = fixture(1)
    cases = {"truncated_delimiter": data[:-1],
             "missing_data": b"".join(packets[:4] + packets[5:]),
             "extra_frame": data + packets[-1],
             "wrong_sample_crc": fixture(1, wrong_sample_crc=True)[2],
             "garbage_tail": data + b"\x01\x02\x03"}
    for name, corrupted in cases.items():
        path = output / f"rejected-{name}.mtcap"
        try:
            save_synthetic_stream(path, corrupted, received_at_utc=STAMP)
        except CaptureError:
            pass
        else:
            raise RuntimeError(f"Corruption accepted: {name}")
        require(not path.exists(), f"Rejected stream left success artifact: {name}")
    original_capture, original_path = window.capture, window.current_path
    damaged_file = output / "deliberately-damaged.mtcap"
    damaged_file.write_bytes(file_before[:-1])
    window.open_path(damaged_file)
    deadline = time.monotonic() + 10
    while window.busy and time.monotonic() < deadline:
        QtTest.QTest.qWait(5)
    app.processEvents()
    require(not window.busy and "Не удалось" in window.status_label.text(), "Corrupt GUI open not reported")
    require(window.capture is original_capture and window.current_path == original_path, "Corrupt GUI open replaced current capture")
    window.close()
    app.processEvents()
    manifest = {"reviewed_sha": "bd58245ecf64f08dcfe6d6fec3981e26fd7ff0ed",
                "source": "independent integer waveform, bitwise CRC, independent COBS",
                "origin": "simulator", "fixture_timestamp": STAMP,
                "scope": "Linux/offscreen byte -> Capture -> file -> GUI -> PNG; no device, Windows, or production session integration",
                "windows": records, "rejected_streams": list(cases),
                "damaged_gui_open_preserved_capture": True}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("PASS: 20 independent synthetic byte windows, 40960 raw samples, disk/GUI/PNG/CSV identity; 5 rejected streams and corrupt GUI reopen.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    run(parser.parse_args().output)

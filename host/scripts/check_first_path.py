"""Reproducible HOST-001 evidence; not a hardware measurement."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import struct
from motortester.emulator import demo_message, from_message
from motortester.model import Capture
from motortester.recording import save, load, export_csv, encode
from motortester.plotting import save_svg

parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path, help="New evidence directory")
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
session = "baf4f270-82db-4b43-83b8-a644c8272841"
fixed_stamp = "2026-10-10T00:00:00+00:00"  # Reproducible fixture, not an observation time.
for number in range(20):
    message = demo_message(number, session)
    message["received_at_utc"] = fixed_stamp
    original = from_message(message)
    target = args.output / f"synthetic-{number:02}.mtcap"
    save(target, original)
    reopened = load(target)
    assert original == reopened
    assert struct.pack("<2048H", *original.samples) == struct.pack("<2048H", *reopened.samples)
last = load(args.output / "synthetic-19.mtcap")
assert last.samples[512:538:25] == (2048, 3048)
assert abs(last.time_seconds(2047) - last.time_seconds(0) - 0.04094) < 1e-12
export_csv(args.output / "synthetic.csv", last)
save_svg(args.output / "synthetic.svg", last)
# Independent asymmetric codes expose endian/order errors that a sine can hide.
control = Capture(samples=(0, 1, 256, 4095), session_id=session, sequence=20,
                  nominal_fs_hz=50_000, received_at_utc=fixed_stamp,
                  trigger_index=1, pretrigger_samples=1)
save(args.output / "control.mtcap", control)
raw = encode(control)
meta_len = struct.unpack_from("<I", raw, 8)[0]
assert raw[16+meta_len:-32] == bytes.fromhex("000001000001ff0f")
report = dict(level="host-only/synthetic", python=platform.python_version(),
              platform=platform.platform(), windows_verified=False, usb_verified=False,
              nominal_fs_hz=50_000, samples_per_window=2048, windows_roundtripped=20,
              first_to_last_seconds=0.04094, fixture_timestamp_is_observation=False,
              files={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(args.output.iterdir())})
(args.output / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))

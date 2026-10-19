#!/usr/bin/env python3
"""Independent byte oracle: Python struct/zlib and COBS by nonzero runs.

No production encoder or production vector generator is imported. Shared JSON
bytes are asserted against this oracle before they enter the executable corpus.
"""
import argparse
import hashlib
import json
import pathlib
import struct
import zlib

parser = argparse.ArgumentParser()
parser.add_argument("--vectors-dir", type=pathlib.Path, required=True)
parser.add_argument("--output-dir", type=pathlib.Path, required=True)
args = parser.parse_args()
VECTORS = args.vectors_dir
OUTPUT = args.output_dir
OUTPUT.mkdir(parents=True, exist_ok=True)
cases = []
OK, ARGUMENT, CAPACITY, FRAMING, LENGTH, CRC, VERSION, RESERVED = range(8)


def cobs(raw):
    out = bytearray()
    for run in raw.split(b"\0"):
        while len(run) >= 254:
            out.append(255)
            out.extend(run[:254])
            run = run[254:]
        out.append(len(run) + 1)
        out.extend(run)
    return bytes(out)


def crc(raw):
    return raw + struct.pack("<I", zlib.crc32(raw))


def envelope(major, minor, payload, kind=0x7F, session=0x0807060504030201,
             sequence=0xFFFFFFFF, request=0xFEFDFCFB):
    return crc(struct.pack("<BBBBHHQII", major, minor, kind, 0, len(payload),
                           0, session, sequence, request) + payload)


def case(name, major, minor, raw, result=OK, frame=None, semantic=False):
    cases.append((name, major, minor, result,
                  (1 if result == OK else 0) | (2 if semantic else 0),
                  raw, cobs(raw) if frame is None else frame))


v1 = json.loads((VECTORS / "vectors-v0.1.json").read_text())
for item in v1["vectors"]:
    raw, framed = bytes.fromhex(item["decoded_hex"]), bytes.fromhex(item["frame_hex"])
    assert crc(raw[:-4]) == raw and cobs(raw) + b"\0" == framed
    case("shared_0.1_" + item["name"], 0, 1, raw, frame=framed[:-1])
v2 = json.loads((VECTORS / "vectors-v0.2-stream.json").read_text())
for group in ("positive_frames", "negative_frames"):
    for item in v2[group]:
        raw = bytes.fromhex(item["decoded_hex"])
        framed = bytes.fromhex(item["framed_hex"])
        assert crc(raw[:-4]) == raw and cobs(raw) + b"\0" == framed
        assert len(raw) == item["decoded_bytes"] and len(framed) == item["framed_bytes"]
        assert raw[3] == raw[6] == raw[7] == 0
        case("shared_0.2_" + item["name"], 0, 2, raw, frame=framed[:-1],
             semantic=group == "negative_frames")

# All supported payload lengths, including both sides of every 254-byte COBS
# run boundary, two revisions and four materially different byte distributions.
for minor in (1, 2):
    for size in range(513):
        seed = 0xA1B2C3D4
        random = bytearray()
        for _ in range(size):
            seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
            random.append(seed >> 24)
        for pattern, payload in (
            ("zero", bytes(size)), ("nonzero", bytes([0xA5]) * size),
            ("cycle", bytes(i & 255 for i in range(size))),
            ("lcg", bytes(random)),
        ):
            case(f"grid_0.{minor}_{size}_{pattern}", 0, minor,
                 envelope(0, minor, payload, kind=size & 255))

# Explicit callers select arbitrary envelope revisions. This does not imply
# endpoint support or negotiation for those revisions.
for major, minor in ((0, 0), (1, 0), (255, 255)):
    case(f"explicit_{major}.{minor}", major, minor,
         envelope(major, minor, b"\0\xff\x01"))

for minor in (1, 2):
    raw = envelope(0, minor, b"\x11\0\x22")
    name = f"negative_0.{minor}_"
    case(name + "wrong_minor", 0, 3 - minor, raw, VERSION)
    case(name + "wrong_major", 1, minor, raw, VERSION)
    damaged = raw[:-1] + bytes([raw[-1] ^ 0x80])
    case(name + "bad_crc", 0, minor, damaged, CRC)
    case(name + "crc_before_version", 2, minor, damaged, CRC)
    for offset in (3, 6, 7):
        changed = bytearray(raw[:-4])
        changed[offset] = 1
        case(name + f"reserved_{offset}", 0, minor, crc(changed), RESERVED)
    changed = bytearray(raw[:-4])
    changed[3] = 1
    case(name + "crc_before_reserved", 0, minor,
         bytes(changed) + raw[-4:], CRC)
    for declared in (0, 2, 4, 512, 513, 65535):
        changed = bytearray(raw[:-4])
        changed[4:6] = struct.pack("<H", declared)
        case(name + f"declared_length_{declared}", 0, minor, crc(changed), LENGTH)
    changed = bytearray(raw)
    changed[4:6] = struct.pack("<H", 4)
    case(name + "length_before_crc", 0, minor, bytes(changed), LENGTH)
    changed = bytearray(raw[:-4])
    changed[3] = 1
    case(name + "version_before_reserved", 1, minor, crc(changed), VERSION)
    case(name + "raw_short", 0, minor, bytes(27), LENGTH)
    case(name + "raw_overflow_zero_insert", 0, minor, bytes(541), LENGTH)
    # Keep COBS shorter than 544 so this reaches decoded-buffer copy bounds.
    overflow = bytes([1]) * 200 + b"\0" + bytes([1]) * 200 + b"\0" + bytes([1]) * 139
    assert len(overflow) == 541 and len(cobs(overflow)) == 542
    case(name + "raw_overflow_copy", 0, minor, overflow, LENGTH)
    case(name + "empty_frame", 0, minor, b"", LENGTH, b"")
    case(name + "zero_code", 0, minor, b"", FRAMING, b"\0")
    case(name + "zero_inside_run", 0, minor, b"", FRAMING, b"\x02\0")
    case(name + "missing_run_byte", 0, minor, b"", FRAMING, b"\x03\x01")
    case(name + "missing_254_run", 0, minor, b"", FRAMING,
         b"\xff" + bytes([1]) * 253)
    case(name + "delimiter_included", 0, minor, b"", FRAMING, cobs(raw) + b"\0")
    case(name + "concatenated_frames", 0, minor, b"", FRAMING,
         cobs(raw) + b"\0" + cobs(raw))
    case(name + "frame_limit", 0, minor, b"", LENGTH, bytes([1]) * 544)

output = bytearray(b"MTWTEST1" + struct.pack("<I", len(cases)))
for name, major, minor, result, flags, raw, frame in cases:
    label = name.encode("ascii")
    output += struct.pack("<HBBBBHH", len(label), major, minor, result, flags,
                          len(raw), len(frame)) + label + raw + frame
(OUTPUT / "corpus.bin").write_bytes(output)
manifest = {
    "cases": len(cases), "shared_positive_0_2": len(v2["positive_frames"]),
    "shared_semantic_negative_0_2_accepted_at_framing": len(v2["negative_frames"]),
    "length_grid_cases": 2 * 513 * 4,
    "corpus_sha256": hashlib.sha256(output).hexdigest(),
    "oracle": "independent Python struct/zlib and nonzero-run COBS",
}
(OUTPUT / "corpus_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest, sort_keys=True))

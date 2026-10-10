#!/usr/bin/env python3
"""Independent host-side framing/payload/sample oracle; standard library only.

No production codec or command parser is imported. This is synthetic software
verification, not a Windows application, USB completion test, or ADC measurement.
"""
import json
from pathlib import Path
import struct
import sys
import zlib


def cobs_decode(encoded):
    raw = bytearray()
    offset = 0
    while offset < len(encoded):
        code = encoded[offset]
        assert code != 0, "embedded zero in COBS frame"
        offset += 1
        end = offset + code - 1
        assert end <= len(encoded), "COBS code extends beyond frame"
        raw.extend(encoded[offset:end])
        offset = end
        if code != 255 and offset < len(encoded):
            raw.append(0)
    return bytes(raw)


def decode_frame(frame):
    raw = cobs_decode(frame)
    assert 28 <= len(raw) <= 540
    major, minor, kind, reserved, size, reserved2, nonce, seq, req = struct.unpack_from("<BBBBHHQII", raw)
    assert (major, minor, reserved, reserved2) == (0, 1, 0, 0)
    assert len(raw) == size + 28
    assert zlib.crc32(raw[:-4]) == int.from_bytes(raw[-4:], "little"), "frame CRC"
    return dict(kind=kind, nonce=nonce, sequence=seq, request=req, payload=raw[24:-4])


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "twenty-windows.cobs")
    wire = path.read_bytes()
    parts = wire.split(b"\0")
    assert parts.pop() == b"" and all(parts)
    messages = [decode_frame(p) for p in parts]
    nonce = 0x706f736974697665
    active = None
    completed = []
    released = []
    caps_checked = False
    events = []
    for index, m in enumerate(messages, 1):
        assert m["nonce"] == nonce and m["sequence"] == index, "session or sequence discontinuity"
        kind, payload = m["kind"], m["payload"]
        if kind == 129:
            assert index == 1 and m["request"] == 1
            assert struct.unpack("<BBBBHHII", payload) == (0, 1, 1, 12, 512, 2048, 0x12345678, 61)
            caps_checked = True
        elif kind == 131:
            assert m["request"] == 2
            assert struct.unpack_from("<I", payload)[0] == 1
            assert struct.unpack("<HBBBBHHHI", payload[4:]) == (1, 1, 0, 0, 0, 2048, 512, 0, 0)
        elif kind == 132:
            assert m["request"] == 3 and struct.unpack("<II", payload) == (3, 1)
        elif kind == 134:
            state, reason, reserved, gen, win, edges, busy, dropped = struct.unpack("<BBHIIIII", payload)
            assert m["request"] == 0
            assert state in (1, 2, 3, 4, 6) and reason == reserved == edges == busy == dropped == 0
            assert gen == 3 and 0 <= win <= 20
            events.append(state)
        elif kind == 144:
            assert active is None and m["request"] == 0
            gen, win, config, profile, count, pre, trigger, trigger_kind, source, edge, channel, level, quality, timer, psc, arr, external = struct.unpack("<IIIHHHHBBBBHHIHHI", payload)
            assert (gen, win, config) == (3, len(completed) + 1, 1)
            assert (profile, count, pre, trigger, trigger_kind, source, edge, channel, level) == (1, 2048, 512, 512, 0, 0, 0, 0, 2048)
            assert (timer, psc, arr, external) == (72000000, 71, 19, 0)
            active = dict(window=win, values=[], raw=bytearray(), quality=quality, blocks=0)
        elif kind == 145:
            assert active is not None and m["request"] == 0
            gen, win, offset, count = struct.unpack_from("<IIHH", payload)
            assert (gen, win, offset) == (3, active["window"], len(active["values"]))
            assert 1 <= count <= 250 and len(payload) == 12 + 2 * count
            assert offset + count <= 2048
            values = struct.unpack(f"<{count}H", payload[12:])
            assert all(0 <= code <= 4095 for code in values)
            active["values"].extend(values)
            active["raw"].extend(payload[12:])
            active["blocks"] += 1
        elif kind == 146:
            assert active is not None and m["request"] == 0
            gen, win, total, reserved, crc = struct.unpack("<IIHHI", payload)
            assert (gen, win, total, reserved) == (3, active["window"], 2048, 0)
            assert len(active["values"]) == 2048 and len(active["raw"]) == 4096 and active["blocks"] == 9
            expected = [1000] * 512 + [3000] + [(i * 17 + (win - 1) * 31) & 4095 for i in range(513, 2048)]
            assert active["values"] == expected, f"sample mismatch window {win}"
            assert crc == zlib.crc32(active["raw"]), "sample CRC"
            assert active["quality"] == (2 if any(x in (0, 4095) for x in expected) else 0)
            completed.append(dict(window=win, crc=f"{crc:08x}", samples=total))
            active = None
        elif kind == 135:
            gen, win = struct.unpack("<II", payload)
            assert gen == 3 and win == len(released) + 1 and win <= len(completed)
            assert m["request"] == win + 3
            released.append(win)
        else:
            raise AssertionError(f"unexpected type {kind}")
    assert caps_checked and active is None and len(completed) == len(released) == 20
    assert events == [1, 2, 3, 4, 6] * 20 + [1], f"unexpected state transitions {events}"
    print(json.dumps(dict(result="PASS", frames=len(messages), bytes=len(wire), full_windows=len(completed), samples=40960, completed=completed), indent=2))


if __name__ == "__main__":
    main()

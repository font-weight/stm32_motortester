"""Независимые бинарные входы по непринятому wire b6ae8025, без codec проекта."""
import json
from pathlib import Path

PIN = "b6ae8025baf1a47e8173997029d2d8b858ada74c"
NONCE = 0x8877665544332211
MAX_ID = 0xFFFFFFFE


def u16(value):
    return value.to_bytes(2, "little")


def u32(value):
    return value.to_bytes(4, "little")


def u64(value):
    return value.to_bytes(8, "little")


def crc32(data):
    crc = 0xFFFFFFFF
    for octet in data:
        crc ^= octet
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def packet(message_type, session, sequence, request_id, payload):
    raw = bytes((0, 1, message_type, 0)) + u16(len(payload)) + b"\0\0"
    raw += u64(session) + u32(sequence) + u32(request_id) + payload
    raw += u32(crc32(raw))
    # Простой независимый COBS; 0 разделяет блоки, FF не вставляет ноль.
    encoded = bytearray([0])
    code_at, code = 0, 1
    for octet in raw:
        if octet == 0:
            encoded[code_at] = code
            code_at = len(encoded)
            encoded.append(0)
            code = 1
        else:
            encoded.append(octet)
            code += 1
            if code == 255:
                encoded[code_at] = code
                code_at = len(encoded)
                encoded.append(0)
                code = 1
    encoded[code_at] = code
    return bytes(encoded) + b"\0"


def hello_ack(bits=9):
    return bytes((0, 1, 1, 12)) + u16(512) + u16(2048) + u32(0x12345678) + u32(bits)


def caps(pre=False, modes=2, sources=1, edges=3):
    return (bytes((1, 0)) + u16(1) + u32(72000000) + u16(71) + u16(19)
            + u32(12000000) + u16(143) + u16(2048)
            + bytes((3 if pre else 1, modes, sources, edges))
            + u16(0) + u16(512 if pre else 0) + u16(1024 if pre else 0))


def config(level=2048, pre=0, edge=0):
    return u16(1) + bytes((1, 0, edge, 0)) + u16(level) + u16(pre) + u16(0) + u32(0)


def begin(generation=4, window=1, config_id=1, pre=0, level=2048):
    return (u32(generation) + u32(window) + u32(config_id)
            + u16(1) + u16(2048) + u16(pre) + u16(pre)
            + bytes((0, 0, 0, 0)) + u16(level) + u16(0)
            + u32(72000000) + u16(71) + u16(19) + u32(0))


def data(generation=4, window=1, offset=0, codes=(1, 2)):
    return u32(generation) + u32(window) + u16(offset) + u16(len(codes)) + b"".join(u16(v) for v in codes)


def end(generation=4, window=1, crc=0x12345678):
    return u32(generation) + u32(window) + u16(2048) + u16(0) + u32(crc)


def abort(generation=4, window=1, reason=1):
    return u32(generation) + u32(window) + u16(reason) + u16(0)


def stop_ack(generation=4, window=0):
    return u32(generation) + u32(window) + b"\0" * 4


def status(generation=4, window=0, state=2, reason=0):
    return bytes((state, reason, 0, 0)) + u32(generation) + u32(window) + u32(0) * 3


def error(rejected_type, code, generation=0):
    return bytes((rejected_type, code, 0, 0)) + u32(generation)


def transcript_inputs():
    # Независимые фиксированные ID: handshake=1, CAPS=2, CONFIG=3, ARM=4, STOP=5.
    return (
        ("hello", "tx", 1, 0, 1, 1, u64(NONCE), "HELLO", []),
        ("hello_ack", "rx", 129, NONCE, 1, 1, hello_ack(), "CAPS", ["HELLO_ACCEPTED"]),
        ("get_caps", "tx", 2, NONCE, 2, 2, b"", "CAPS", []),
        ("caps", "rx", 130, NONCE, 2, 2, caps(), "READY", ["CAPABILITIES"]),
        ("configure", "tx", 3, NONCE, 3, 3, config(), "READY", []),
        ("config_ack", "rx", 131, NONCE, 3, 3, u32(1) + config(), "READY", ["CONFIGURED"]),
        ("arm", "tx", 4, NONCE, 4, 4, u32(1), "READY", []),
        ("arm_ack", "rx", 132, NONCE, 4, 4, u32(4) + u32(1), "ARMED", ["ARMED"]),
        ("waiting", "rx", 134, NONCE, 5, 0, status(), "ARMED", ["STATUS"]),
        ("stop", "tx", 5, NONCE, 5, 5, u32(4), "ARMED", []),
        ("stop_ack", "rx", 133, NONCE, 6, 5, stop_ack(), "READY", ["STOPPED"]),
    )


def vectors():
    return [{"name": name, "direction": direction, "message_type": kind,
             "session": session, "sequence": seq, "request_id": request,
             "payload_hex": payload.hex(), "packet_hex": packet(kind, session, seq, request, payload).hex(),
             "phase_after": phase, "events": events}
            for name, direction, kind, session, seq, request, payload, phase, events in transcript_inputs()]


if __name__ == "__main__":
    Path(__file__).with_name("transcript.json").write_text(
        json.dumps({"provisional_contract_sha": PIN, "vectors": vectors()}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

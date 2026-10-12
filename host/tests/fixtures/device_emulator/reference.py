"""Independent executable oracle, authored from the two frozen c54 documents.

No production imports. Numeric fields below come from the shared draft. The
sample recipe alone is a declared HOST-007 simulator choice, not wire semantics.
"""
from dataclasses import dataclass

PIN = "c54bd955aaf3cb863ef3df043ea237ea62e74ad4"
BASE = "477057aadc9bc8e757bc8ef817125377e816d212"
NONCE = 0x1020304050607080
MAX_ID = 2**32 - 2
MAX_TIME = 2**64 - 1


def uint(number, width):
    return number.to_bytes(width, "little")


def number(data):
    return int.from_bytes(data, "little")


def crc32(data):
    value = 0xFFFFFFFF
    for octet in data:
        value ^= octet
        for _ in range(8):
            value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
    return value ^ 0xFFFFFFFF


def cobs(data):
    # A separate run-based encoder, not the production codec's implementation.
    encoded = bytearray()
    parts = data.split(b"\0")
    for index, part in enumerate(parts):
        while len(part) >= 254:
            encoded.append(255)
            encoded.extend(part[:254])
            part = part[254:]
        encoded.append(len(part) + 1)
        encoded.extend(part)
    return bytes(encoded)


def uncobs(data):
    if not data or b"\0" in data:
        raise ValueError("oracle: invalid COBS input")
    out, offset = bytearray(), 0
    while offset < len(data):
        code = data[offset]
        stop = offset + code
        if stop > len(data):
            raise ValueError("oracle: truncated COBS run")
        out.extend(data[offset + 1:stop])
        offset = stop
        if code < 255 and offset < len(data):
            out.append(0)
    return bytes(out)


def packet(kind, session=NONCE, sequence=2, request=2, payload=b"", *,
           major=0, minor=1, reserved=0, reserved16=0, declared_length=None):
    length = len(payload) if declared_length is None else declared_length
    header = (bytes((major, minor, kind, reserved)) + uint(length, 2)
              + uint(reserved16, 2) + uint(session, 8)
              + uint(sequence, 4) + uint(request, 4))
    raw = header + payload
    return cobs(raw + uint(crc32(raw), 4)) + b"\0"


@dataclass(frozen=True)
class Frame:
    kind: int
    session: int
    sequence: int
    request: int
    payload: bytes
    packet: bytes


def frame(encoded):
    if not encoded or encoded[-1] != 0:
        raise ValueError("oracle: missing delimiter")
    raw = uncobs(encoded[:-1])
    if not 28 <= len(raw) <= 540:
        raise ValueError("oracle: decoded size")
    if raw[:2] != b"\0\1" or raw[3] or raw[6:8] != b"\0\0":
        raise ValueError("oracle: header")
    if number(raw[4:6]) != len(raw) - 28:
        raise ValueError("oracle: payload size")
    if number(raw[-4:]) != crc32(raw[:-4]):
        raise ValueError("oracle: frame CRC")
    return Frame(raw[2], number(raw[8:16]), number(raw[16:20]),
                 number(raw[20:24]), raw[24:-4], encoded)


class Collector:
    def __init__(self):
        self.pending = bytearray()
        self.frames = []

    def feed(self, chunk):
        fresh = []
        for octet in chunk:
            if octet:
                self.pending.append(octet)
                if len(self.pending) > 543:
                    raise ValueError("oracle: overlong output frame")
            elif self.pending:
                parsed = frame(bytes(self.pending) + b"\0")
                self.pending.clear()
                self.frames.append(parsed)
                fresh.append(parsed)
        return fresh


def hello(nonce=NONCE):
    return packet(1, 0, 1, 1, uint(nonce, 8))


def config(profile=1, mode=1, source=0, edge=0, level=2048, pre=0,
           timeout=0, reserved8=0, reserved16=0):
    return (uint(profile, 2) + bytes((mode, source, edge, reserved8))
            + uint(level, 2) + uint(pre, 2) + uint(reserved16, 2)
            + uint(timeout, 4))


def hello_ack():
    return bytes((0, 1, 1, 12)) + uint(512, 2) + uint(2048, 2) + uint(0, 4) + uint(9, 4)


def caps():
    return (bytes((1, 0)) + uint(1, 2) + uint(72000000, 4) + uint(71, 2)
            + uint(19, 2) + uint(12000000, 4) + uint(143, 2) + uint(2048, 2)
            + bytes((1, 2, 1, 3)) + b"\0" * 6)


def status(state, generation=0, window=0, reason=0, dropped=0):
    return (bytes((state, reason, 0, 0)) + uint(generation, 4) + uint(window, 4)
            + uint(0, 4) + uint(0, 4) + uint(dropped, 4))


def begin(generation=4, window=1, config_id=1, edge=0, level=2048):
    return (uint(generation, 4) + uint(window, 4) + uint(config_id, 4)
            + uint(1, 2) + uint(2048, 2) + uint(0, 2) + uint(0, 2)
            + bytes((0, 0, edge, 0)) + uint(level, 2) + uint(2, 2)
            + uint(72000000, 4) + uint(71, 2) + uint(19, 2) + uint(0, 4))


def samples(generation=4, window=1, edge=0, level=2048):
    # Independently materialized expected bytes, from the disclosed simulator
    # recipe. No physical threshold, ADC or sampling-time claim is made.
    result = bytearray()
    for index in range(2048):
        if index == 0:
            code = level if edge == 0 else level - 1
        elif index == 511:
            code = 0
        elif index == 1023:
            code = 4095
        else:
            code = (73 * index + 19 * window + 7 * generation) % 4096
        result.extend(uint(code, 2))
    return bytes(result)


def data(generation=4, window=1, offset=0, count=250, edge=0, level=2048):
    return (uint(generation, 4) + uint(window, 4) + uint(offset, 2) + uint(count, 2)
            + samples(generation, window, edge, level)[2 * offset:2 * (offset + count)])


def end(generation=4, window=1, edge=0, level=2048):
    return (uint(generation, 4) + uint(window, 4) + uint(2048, 2) + b"\0\0"
            + uint(crc32(samples(generation, window, edge, level)), 4))


def release(generation=4, window=1, edge=0, level=2048):
    return uint(generation, 4) + uint(window, 4) + uint(crc32(samples(generation, window, edge, level)), 4)


def abort(generation=4, window=1, reason=1):
    return uint(generation, 4) + uint(window, 4) + uint(reason, 2) + b"\0\0"


def error(kind, code, generation=0):
    return bytes((kind, code, 0, 0)) + uint(generation, 4)


def vectors():
    entries = [
        ("HELLO", 1, 0, 1, 1, uint(NONCE, 8)),
        ("HELLO_ACK", 129, NONCE, 1, 1, hello_ack()),
        ("GET_CAPS", 2, NONCE, 2, 2, b""),
        ("CAPS", 130, NONCE, 2, 2, caps()),
        ("CONFIG", 3, NONCE, 3, 3, config()),
        ("CONFIG_ACK", 131, NONCE, 3, 3, uint(1, 4) + config()),
        ("ARM", 4, NONCE, 4, 4, uint(1, 4)),
        ("ARM_ACK", 132, NONCE, 4, 4, uint(4, 4) + uint(1, 4)),
        ("STOP", 5, NONCE, 5, 5, uint(4, 4)),
        ("STOP_ACK", 133, NONCE, 5, 5, uint(4, 4) + uint(1, 4) + b"\0" * 4),
        ("GET_STATUS", 6, NONCE, 6, 6, b""),
        ("STATUS", 134, NONCE, 6, 6, status(0, 4, 1)),
        ("RELEASE_WINDOW", 7, NONCE, 7, 7, release()),
        ("RELEASE_ACK", 135, NONCE, 7, 7, uint(4, 4) + uint(1, 4)),
        ("WINDOW_BEGIN", 144, NONCE, 8, 0, begin()),
        ("WINDOW_DATA", 145, NONCE, 9, 0, data()),
        ("WINDOW_END", 146, NONCE, 10, 0, end()),
        ("WINDOW_ABORT", 147, NONCE, 11, 0, abort()),
        ("ERROR", 255, NONCE, 12, 8, error(7, 7, 4)),
    ]
    return [{"name": name, "type": kind, "session": nonce, "sequence": sequence,
             "request_id": request, "payload_hex": payload.hex(),
             "packet_hex": packet(kind, nonce, sequence, request, payload).hex()}
            for name, kind, nonce, sequence, request, payload in entries]

"""Isolated HOST codec for the unaccepted wire 0.2 stream draft.

Pinned prose: PR192 08172114403273ecfac2fb1e8e49d92f9c68756f;
SHA256 174aed3c570a204d865c1a446c7878c628a972222d00ef8f60d33ad9c92c998a.
No transport, firmware, application or storage integration is provided.
"""
from dataclasses import dataclass
from enum import IntEnum
import struct
import zlib

DRAFT_COMMIT = "08172114403273ecfac2fb1e8e49d92f9c68756f"
DRAFT_SHA256 = "174aed3c570a204d865c1a446c7878c628a972222d00ef8f60d33ad9c92c998a"
MAX_PAYLOAD, MAX_DECODED, MAX_ENCODED, MAX_FRAME = 512, 540, 543, 544
MAX_BLOCK, U32_MAX, U64_MAX = 128, 0xffffffff, 0xffffffffffffffff
COUNTER_RESERVE = 0xffffff00
HEADER = struct.Struct("<BBBBHHQII")


class Kind(IntEnum):
    HELLO = 1
    GET_CAPS = 2
    STOP = 5
    START = 8
    HELLO_ACK = 129
    CAPS = 130
    STOP_ACK = 133
    START_ACK = 136
    DATA = 152
    END = 153
    ERROR = 255


HOST_KINDS = frozenset((Kind.HELLO, Kind.GET_CAPS, Kind.STOP, Kind.START))
DEVICE_KINDS = frozenset(set(Kind) - HOST_KINDS)


class WireError(ValueError):
    """Fixed diagnostic code; never includes arbitrary bytes/transport details."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def uint(value: int, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def cobs_encode(raw: bytes) -> bytes:
    if type(raw) is not bytes or len(raw) > MAX_DECODED:
        raise WireError("framing")
    out = bytearray((0,))
    code_at, code = 0, 1
    for byte in raw:
        if byte == 0:
            out[code_at] = code
            code_at, code = len(out), 1
            out.append(0)
        else:
            out.append(byte)
            code += 1
            if code == 255:
                out[code_at] = code
                code_at, code = len(out), 1
                out.append(0)
    out[code_at] = code
    return bytes(out)


def cobs_decode(encoded: bytes) -> bytes:
    if type(encoded) is not bytes or not 1 <= len(encoded) <= MAX_ENCODED:
        raise WireError("framing")
    out, pos = bytearray(), 0
    while pos < len(encoded):
        code = encoded[pos]
        if code == 0 or pos + code > len(encoded):
            raise WireError("framing")
        chunk = encoded[pos + 1:pos + code]
        if 0 in chunk:
            raise WireError("framing")
        out.extend(chunk)
        pos += code
        if code < 255 and pos < len(encoded):
            out.append(0)
        if len(out) > MAX_DECODED:
            raise WireError("framing")
    return bytes(out)


@dataclass(frozen=True, slots=True)
class Frame:
    kind: Kind
    nonce: int
    sequence: int
    request_id: int
    payload: bytes
    encoded: bytes  # Exact bytes, including delimiter, for immediate-duplicate checks.


def decode_frame(framed: bytes, direction: str | None = None) -> Frame:
    """Validate envelope only; payload/state validation follows nonce/correlation."""
    if type(framed) is not bytes or not 2 <= len(framed) <= MAX_FRAME or framed[-1] != 0:
        raise WireError("framing")
    raw = cobs_decode(framed[:-1])
    if len(raw) < HEADER.size + 4:
        raise WireError("length")
    if zlib.crc32(raw[:-4]) != struct.unpack_from("<I", raw, len(raw) - 4)[0]:
        raise WireError("crc")
    major, minor, kind, r1, size, r2, nonce, seq, req = HEADER.unpack_from(raw)
    if (major, minor) != (0, 2):
        raise WireError("version")
    if r1 or r2:
        raise WireError("reserved")
    if size > MAX_PAYLOAD or len(raw) != HEADER.size + size + 4:
        raise WireError("length")
    try:
        kind = Kind(kind)
    except ValueError:
        raise WireError("type") from None
    if direction not in (None, "host", "device"):
        raise ValueError("direction")
    if direction == "host" and kind not in HOST_KINDS or direction == "device" and kind not in DEVICE_KINDS:
        raise WireError("direction")
    if seq == 0:
        raise WireError("sequence")
    return Frame(kind, nonce, seq, req, raw[24:-4], framed)


@dataclass(frozen=True, slots=True)
class Hello:
    nonce: int


@dataclass(frozen=True, slots=True)
class HelloAck:
    firmware_build: int


@dataclass(frozen=True, slots=True)
class Caps:
    config_id: int = 1
    profile: int = 1
    channel: int = 0
    adc_bits: int = 12
    fs_num: int = 50000
    fs_den: int = 1
    max_block: int = 128
    flags: int = 1


@dataclass(frozen=True, slots=True)
class StartAck:
    generation: int
    config_id: int
    first_index: int


@dataclass(frozen=True, slots=True)
class Data:
    generation: int
    config_id: int
    first_index: int
    samples: tuple[int, ...]

    @property
    def end_index(self) -> int:
        return self.first_index + len(self.samples)

    @property
    def raw_samples(self) -> bytes:
        return struct.pack("<" + "H" * len(self.samples), *self.samples)


@dataclass(frozen=True, slots=True)
class End:
    generation: int
    config_id: int
    prefix_end: int
    produced_end: int | None
    last_data_sequence: int
    prefix_crc32: int
    reason: int
    flags: int
    stop_request_id: int


@dataclass(frozen=True, slots=True)
class StopAck:
    generation: int
    prefix_end: int
    terminal_sequence: int
    flags: int


@dataclass(frozen=True, slots=True)
class Error:
    rejected_type: Kind
    code: int
    generation: int


def _unpack(fmt: str, payload: bytes) -> tuple:
    if len(payload) != struct.calcsize(fmt):
        raise WireError("payload_length")
    return struct.unpack(fmt, payload)


def decode_payload(frame: Frame) -> object:
    """Strict fixed-profile semantics. Cross-frame identities belong to endpoint."""
    k, p = frame.kind, frame.payload
    if k == Kind.HELLO:
        nonce, = _unpack("<Q", p)
        if not nonce or (frame.nonce, frame.sequence, frame.request_id) != (0, 1, 1):
            raise WireError("hello")
        return Hello(nonce)
    if k == Kind.GET_CAPS:
        _unpack("<", p)
        return None
    if k == Kind.START:
        profile, reserved = _unpack("<HH", p)
        if reserved:
            raise WireError("reserved")
        if profile != 1:
            raise WireError("profile")
        return profile
    if k == Kind.STOP:
        generation, = _unpack("<I", p)
        if not generation:
            raise WireError("generation")
        return generation
    if k == Kind.HELLO_ACK:
        major, minor, channels, bits, maximum, block, build, caps = _unpack("<BBBBHHII", p)
        if (major, minor) != (0, 2):
            raise WireError("version")
        if (channels, bits, maximum, block, caps) != (1, 12, 512, 128, 1):
            raise WireError("caps")
        return HelloAck(build)
    if k == Kind.CAPS:
        config, profile, channel, bits, numerator, denominator, block, reserved, flags = _unpack("<IHBBIIHHI", p)
        if reserved:
            raise WireError("reserved")
        if (config, profile, channel, bits, numerator, denominator, block, flags) != (1, 1, 0, 12, 50000, 1, 128, 1):
            raise WireError("caps")
        return Caps()
    if k == Kind.START_ACK:
        generation, config, first = _unpack("<IIQ", p)
        if not generation:
            raise WireError("generation")
        if config != 1:
            raise WireError("config")
        if first != 0:
            raise WireError("sample_gap")
        return StartAck(generation, config, first)
    if k == Kind.DATA:
        if len(p) < 20:
            raise WireError("payload_length")
        generation, config, first, count, reserved = struct.unpack_from("<IIQHH", p)
        if not 1 <= count <= MAX_BLOCK:
            raise WireError("count")
        if len(p) != 20 + 2 * count:
            raise WireError("payload_length")
        if reserved:
            raise WireError("reserved")
        if not generation:
            raise WireError("generation")
        if config != 1:
            raise WireError("config")
        if first + count > U64_MAX:
            raise WireError("overflow")
        samples = struct.unpack_from("<" + "H" * count, p, 20)
        if any(sample > 4095 for sample in samples):
            raise WireError("code")
        return Data(generation, config, first, samples)
    if k == Kind.END:
        generation, config, prefix, produced, last, crc, reason, flags, stop = _unpack("<IIQQIIHHI", p)
        if not generation:
            raise WireError("generation")
        if config != 1:
            raise WireError("config")
        if reason > 6 or flags & ~7:
            raise WireError("terminal")
        if flags & 1:
            if produced or not flags & 2:
                raise WireError("terminal")
        elif produced < prefix or produced != prefix and not flags & 2:
            raise WireError("terminal")
        if not reason and not stop:
            raise WireError("terminal")
        if not prefix and (last or crc) or prefix and not last:
            raise WireError("terminal")
        return End(generation, config, prefix, None if flags & 1 else produced, last, crc, reason, flags, stop)
    if k == Kind.STOP_ACK:
        generation, prefix, terminal, flags = _unpack("<IQII", p)
        if not generation or not terminal or flags & ~3:
            raise WireError("terminal")
        return StopAck(generation, prefix, terminal, flags)
    if k == Kind.ERROR:
        rejected, code, reserved, generation = _unpack("<BBHI", p)
        if reserved:
            raise WireError("reserved")
        if rejected not in HOST_KINDS or not 1 <= code <= 5:
            raise WireError("error")
        return Error(Kind(rejected), code, generation)
    raise WireError("type")


def encode_frame(kind: Kind, nonce: int, sequence: int, request_id: int, payload: bytes) -> bytes:
    """Encode a strictly checked message; test negatives use independent byte builders."""
    if not isinstance(kind, Kind) or type(payload) is not bytes or len(payload) > MAX_PAYLOAD:
        raise WireError("type")
    if not uint(nonce, U64_MAX) or not uint(sequence, U32_MAX) or not uint(request_id, U32_MAX) or sequence == 0:
        raise WireError("counter")
    raw = HEADER.pack(0, 2, kind, 0, len(payload), 0, nonce, sequence, request_id) + payload
    framed = cobs_encode(raw + struct.pack("<I", zlib.crc32(raw))) + b"\0"
    decode_payload(decode_frame(framed))
    return framed

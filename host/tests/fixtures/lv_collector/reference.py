"""Независимые байтовые свидетельства LV-001, без импортов production.

Числа framing/payload взяты из предложения db09c263. Peer — синтетический
источник для проверок ПК, не модель физического АЦП и не реализация firmware.
"""
from dataclasses import dataclass
import hashlib
import json


NONCE = 0x123456789ABCDEF0
BASE = "1af7c7c27e7eaa6559138b055ed40c7e3114abb1"
TREE = "2ed75f6d5d9993af47a79cbfa0eb1e6b3568b2e9"


def u(value, width=4):
    return value.to_bytes(width, "little")


def crc(data):
    value = 0xFFFFFFFF
    for octet in data:
        value ^= octet
        for _ in range(8):
            value = value >> 1 ^ (0xEDB88320 if value & 1 else 0)
    return value ^ 0xFFFFFFFF


def cobs(data):
    out = bytearray()
    for part in data.split(b"\0"):
        while len(part) >= 254:
            out.append(255)
            out.extend(part[:254])
            part = part[254:]
        out.append(len(part) + 1)
        out.extend(part)
    return bytes(out)


def uncobs(data):
    out = bytearray()
    while data:
        width = data[0]
        if not width or width > len(data):
            raise ValueError("Неверный независимый COBS")
        out.extend(data[1:width])
        data = data[width:]
        if data and width != 255:
            out.append(0)
    return bytes(out)


@dataclass(frozen=True)
class Packet:
    kind: int
    session: int
    sequence: int
    request: int
    payload: bytes


def encode(kind, sequence, request=0, payload=b"", session=NONCE):
    body = b"\0\1" + bytes((kind, 0)) + u(len(payload), 2) + b"\0\0"
    body += u(session, 8) + u(sequence) + u(request) + payload
    return cobs(body + u(crc(body))) + b"\0"


def decode(encoded):
    if not encoded.endswith(b"\0"):
        raise ValueError("Нет независимого разделителя")
    raw = uncobs(encoded[:-1])
    if (not 28 <= len(raw) <= 540 or raw[:2] != b"\0\1" or raw[3]
            or raw[6:8] != b"\0\0" or int.from_bytes(raw[4:6], "little") != len(raw) - 28
            or crc(raw[:-4]) != int.from_bytes(raw[-4:], "little")):
        raise ValueError("Неверная независимая целостность")
    return Packet(raw[2], int.from_bytes(raw[8:16], "little"),
                  int.from_bytes(raw[16:20], "little"),
                  int.from_bytes(raw[20:24], "little"), raw[24:-4])


def packets(data):
    if data and not data.endswith(b"\0"):
        raise ValueError("Незавершённый независимый поток")
    return [decode(part + b"\0") for part in data.split(b"\0") if part]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def envelope(kind, body):
    return canonical(dict(format=kind, version=1, body=body,
                          sha256=hashlib.sha256(canonical(body)).hexdigest()))


def codes(generation, window, edge=0, level=2048, timeout=True):
    # Заявленный рецепт только синтетического DeviceEmulator, не измерение.
    values = [(73 * n + 19 * window + 7 * generation) % 4096 for n in range(2048)]
    if not timeout:
        values[0] = level - edge
    values[511], values[1023] = 0, 4095
    return tuple(values)


def sample_bytes(values):
    return b"".join(u(value, 2) for value in values)


HELLO_ACK = b"\0\1\1\x0c" + u(512, 2) + u(2048, 2) + u(0) + u(29)
CAPS = (b"\1\0" + u(1, 2) + u(72000000) + u(71, 2) + u(19, 2)
        + u(12000000) + u(143, 2) + u(2048, 2) + bytes((1, 7, 1, 3)) + b"\0" * 6)


def status(state, generation, window, reason=0, observed=0, busy=0, dropped=0):
    return bytes((state, reason, 0, 0)) + u(generation) + u(window) + u(observed) + u(busy) + u(dropped)


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class BytePeer:
    """Простой управляемый поток; границы read/write задаются отдельно."""
    def __init__(self, clock):
        self.clock = clock
        self.rx = bytearray()
        self.tx_pending = bytearray()
        self.rx_log = []
        self.tx_log = []
        self.commands = []
        self.frames = []
        self.calls = []
        self.read_limit = self.write_limit = 4096
        self.read_delay = self.write_delay = 0
        self.read_failure = self.write_failure = None
        self.read_override = None
        self.unknown_prefix = None
        self.sequence = 1
        self.generation = self.window = 0
        self.config = None
        self.stop_mode = "ack"
        self.caps = CAPS
        self.hello = HELLO_ACK

    def reply(self, kind, payload, request=0):
        data = encode(kind, self.sequence, request, payload)
        self.sequence += 1
        self.frames.append(decode(data))
        self.rx.extend(data)
        return data

    def write(self, data):
        self.calls.append(("write", len(data)))
        self.clock.now += self.write_delay
        if self.write_failure:
            raise self.write_failure
        if self.unknown_prefix is not None:
            self.tx_log.append(data[:self.unknown_prefix])
            raise TimeoutError("Неизвестный принятый префикс")
        count = min(len(data), self.write_limit)
        self.tx_log.append(data[:count])
        self.tx_pending.extend(data[:count])
        while 0 in self.tx_pending:
            end = self.tx_pending.index(0) + 1
            packet = bytes(self.tx_pending[:end])
            del self.tx_pending[:end]
            if packet != b"\0":
                command = decode(packet)
                self.commands.append(command)
                self.command(command)
        return count

    def command(self, frame):
        if frame.kind == 1:
            self.reply(129, self.hello, frame.request)
        elif frame.kind == 2:
            self.reply(130, self.caps, frame.request)
        elif frame.kind == 3:
            self.config = frame.payload
            self.reply(131, u(1) + self.config, frame.request)
        elif frame.kind == 4:
            self.generation = frame.request
            self.reply(132, u(self.generation) + u(1), frame.request)
            self.reply(134, status(2, self.generation, self.window))
        elif frame.kind == 5:
            if self.stop_mode == "ack":
                self.reply(133, u(self.generation) + u(self.window) + b"\0" * 4, frame.request)
                self.reply(134, status(0, self.generation, self.window))
            elif self.stop_mode == "bad_generation":
                self.reply(133, u(self.generation + 1) + u(self.window) + b"\0" * 4, frame.request)
            elif self.stop_mode == "error":
                self.reply(255, bytes((5, 4, 0, 0)) + u(self.generation), frame.request)
        else:
            raise ValueError("Collector выдал незапланированную команду")

    def read(self, size):
        self.calls.append(("read", size))
        self.clock.now += self.read_delay
        if self.read_failure:
            raise self.read_failure
        if self.read_override is not None:
            return self.read_override
        count = min(size, self.read_limit, len(self.rx))
        data = bytes(self.rx[:count])
        del self.rx[:count]
        self.rx_log.append(data)
        return data

    def window_frames(self, *, timeout=True, omit_offset=None, duplicate_offset=None,
                      conflicting_duplicate=False, bad_sample_crc=False, last_offset=2048,
                      values_override=None):
        self.window += 1
        edge, level = self.config[4], int.from_bytes(self.config[6:8], "little")
        values = (codes(self.generation, self.window, edge, level, timeout)
                  if values_override is None else values_override)
        raw = sample_bytes(values)
        begin = (u(self.generation) + u(self.window) + u(1) + u(1, 2) + u(2048, 2)
                 + u(0, 2) + u(65535 if timeout else 0, 2)
                 + bytes((2 if timeout else 0, 0, edge, 0)) + u(level, 2)
                 + u(int(timeout) | (2 if any(v in (0, 4095) for v in values) else 0), 2)
                 + u(72000000) + u(71, 2) + u(19, 2) + u(0))
        self.reply(144, begin)
        for offset in range(0, last_offset, 250):
            count = min(250, last_offset - offset)
            payload = u(self.generation) + u(self.window) + u(offset, 2) + u(count, 2) + raw[offset * 2:(offset + count) * 2]
            if offset != omit_offset:
                self.reply(145, payload)
                if offset == duplicate_offset:
                    duplicate = payload
                    if conflicting_duplicate:
                        duplicate = payload[:12] + bytes((payload[12] ^ 1,)) + payload[13:]
                    self.reply(145, duplicate)
        if last_offset == 2048:
            self.reply(146, u(self.generation) + u(self.window) + u(2048, 2) + b"\0\0"
                       + u(crc(raw) ^ int(bad_sample_crc)))
            self.reply(134, status(6, self.generation, self.window))
        return values

    def early_abort(self, reason=2):
        self.window += 1
        return self.reply(147, u(self.generation) + u(self.window) + u(reason, 2) + b"\0\0")


class EmulatorTransport:
    """Обёртка реального production byte-emulator, никогда не открывает COM."""
    def __init__(self, device, clock):
        self.device, self.clock = device, clock
        self.calls, self.tx_log, self.rx_log = [], [], []
        self.write_limit = self.read_limit = 4096
        self.block_read = False
        self.read_delay = 0

    def write(self, data):
        self.calls.append(("write", len(data)))
        result = self.device.write(data[:self.write_limit], self.clock.now)
        if result.diagnostics:
            raise ValueError(result.diagnostics)
        self.tx_log.append(data[:result.accepted_prefix])
        return result.accepted_prefix

    def read(self, size):
        self.calls.append(("read", size))
        self.clock.now += self.read_delay
        data = b"" if self.block_read else self.device.read(min(size, self.read_limit), self.clock.now)
        self.rx_log.append(data)
        return data

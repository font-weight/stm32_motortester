"""COBS/CRC из frozen Stop R2 raw_peer, без production codec/firmware.

Для новых авторских тестов one читает целые COBS-блоки публичным read,
не забирая первый байт следующего кадра. Авторство этого набора не независимое.
"""
from dataclasses import dataclass

NONCE = 0x8877665544332211
MAX_ID = 2**32 - 2
MAX_TIME = 2**64 - 1
CONFIG = bytes.fromhex('01000100000000080000000000000000')
CAPS = bytes.fromhex('0100010000a24a0447001300001bb7008f00000801020103000000000000')


def u(value, size=4):
    return value.to_bytes(size, 'little')


def crc(data):
    accumulator = 0xffffffff
    for octet in data:
        accumulator ^= octet
        for bit in range(8):
            accumulator = (accumulator >> 1) ^ ((-(accumulator & 1)) & 0xedb88320)
    return accumulator ^ 0xffffffff


def encode(raw):
    output = bytearray((0,))
    marker, size = 0, 1
    for octet in raw:
        if octet == 0:
            output[marker] = size
            marker = len(output)
            output.append(0)
            size = 1
        else:
            output.append(octet)
            size += 1
            if size == 255:
                output[marker] = size
                marker = len(output)
                output.append(0)
                size = 1
    output[marker] = size
    return bytes(output) + b'\0'


def decode(packet):
    if not packet or packet[-1] != 0:
        raise ValueError('Нет разделителя')
    encoded, raw, position = packet[:-1], bytearray(), 0
    while position < len(encoded):
        count = encoded[position]
        if not count or position + count > len(encoded):
            raise ValueError('Неверный COBS')
        block = encoded[position + 1:position + count]
        if 0 in block:
            raise ValueError('Нулевой байт внутри блока')
        raw.extend(block)
        position += count
        if count < 255 and position < len(encoded):
            raw.append(0)
    if len(raw) < 28 or int.from_bytes(raw[-4:], 'little') != crc(raw[:-4]):
        raise ValueError('Неверный CRC/размер')
    length = int.from_bytes(raw[4:6], 'little')
    if raw[:2] != b'\0\1' or raw[3] or raw[6:8] != b'\0\0' or len(raw) != 28 + length:
        raise ValueError('Неверный заголовок')
    return Frame(raw[2], int.from_bytes(raw[8:16], 'little'),
                 int.from_bytes(raw[16:20], 'little'), int.from_bytes(raw[20:24], 'little'),
                 bytes(raw[24:-4]), packet)


def packet(kind, sequence, request, payload=b'', session=NONCE, *, version=b'\0\1', reserved=0):
    header = version + bytes((kind, reserved)) + u(len(payload), 2) + b'\0\0'
    raw = header + u(session, 8) + u(sequence) + u(request) + payload
    return encode(raw + u(crc(raw)))


def hello(nonce=NONCE):
    return packet(1, 1, 1, u(nonce, 8), 0)


def status(state, generation=3, window=0, dropped=0):
    return bytes((state, 0, 0, 0)) + u(generation) + u(window) + b'\0' * 8 + u(dropped)


@dataclass(frozen=True)
class Frame:
    kind: int
    session: int
    sequence: int
    request: int
    payload: bytes
    packet: bytes


class Peer:
    def __init__(self, case, factory):
        self.case, self.device = case, factory()
        self.now, self.sequence, self.request = 0, 2, 2
        self.pending = bytearray()
        self.frames, self.events = [], []

    def write(self, raw):
        result = self.device.write(raw, self.now)
        self.case.assertEqual(result.accepted_prefix, len(raw))
        self.events.append({'write_hex': raw.hex(), 'accepted': result.accepted_prefix,
                            'now': self.now, 'diagnostics': [(d.code, d.count) for d in result.diagnostics]})
        return result

    def command(self, kind, payload=b'', *, request_gap=0):
        self.request += request_gap
        raw = packet(kind, self.sequence, self.request, payload)
        result = self.write(raw)
        self.sequence += 1
        self.request += 1
        return raw, result

    def read(self, size):
        raw = self.device.read(size, self.now)
        self.case.assertIs(type(raw), bytes)
        self.case.assertLessEqual(len(raw), size)
        self.events.append({'read_hex': raw.hex(), 'limit': size, 'now': self.now})
        self.pending.extend(raw)
        while 0 in self.pending:
            stop = self.pending.index(0) + 1
            self.frames.append(decode(bytes(self.pending[:stop])))
            del self.pending[:stop]
        return raw

    def drain(self, size=4096):
        start, result = len(self.frames), bytearray()
        for _ in range(10000):
            part = self.read(size)
            result.extend(part)
            if not part:
                self.case.assertFalse(self.pending)
                return bytes(result), self.frames[start:]
        self.case.fail('Не завершилась ограниченная передача')

    def one(self):
        self.case.assertFalse(self.pending)
        start = len(self.frames)
        for _ in range(544):
            marker = self.read(1)
            self.case.assertTrue(marker)
            if len(self.frames) != start:
                return self.frames[-1]
            if marker[0] > 1:
                body = self.read(marker[0] - 1)
                self.case.assertEqual(len(body), marker[0] - 1)
        self.case.fail('Кадр превысил 544 байта')

    def armed(self):
        self.write(hello())
        self.case.assertEqual([f.kind for f in self.drain()[1]], [129])
        self.command(3, CONFIG)
        replies = self.drain()[1]
        self.case.assertEqual([(f.kind, f.payload) for f in replies], [(131, u(1) + CONFIG)])
        self.command(4, u(1))
        replies = self.drain()[1]
        self.case.assertEqual([(f.kind, f.request) for f in replies], [(132, 3), (134, 0)])
        self.case.assertEqual(self.device.snapshot().state, 2)
        return self

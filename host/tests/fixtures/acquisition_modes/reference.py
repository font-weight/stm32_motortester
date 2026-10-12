"""Независимый oracle: little-endian, побитовый ISO-HDLC CRC и COBS.

Нет импортов production или авторских тестов. Формула кодов — опубликованный
синтетический договор HOST-007, а не измеренный физический сигнал.
"""
import struct

NONCE = 0x14589231ABCDEF67
SESSION_PIN = 'b6ae8025baf1a47e8173997029d2d8b858ada74c'
EMULATOR_PIN = 'c54bd955aaf3cb863ef3df043ea237ea62e74ad4'
WINDOW_PIN = '124b3f32e1e7f7282bce283baa4c9406d4d72950'


def crc32(data):
    value = 0xFFFFFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
    return value ^ 0xFFFFFFFF


def packet(kind, session, sequence, request, payload=b''):
    raw = struct.pack('<BBBBHHQII', 0, 1, kind, 0, len(payload), 0,
                      session, sequence, request) + payload
    raw += struct.pack('<I', crc32(raw))
    out = bytearray()
    cursor = 0
    while cursor < len(raw):
        end = cursor
        while end < len(raw) and raw[end] != 0 and end - cursor < 254:
            end += 1
        out.append(end - cursor + 1)
        out.extend(raw[cursor:end])
        if end - cursor == 254:
            cursor = end
        elif end < len(raw):
            cursor = end + 1
            if cursor == len(raw):
                out.append(1)
        else:
            cursor = end
    return bytes(out) + b'\0'


def config(mode=1, edge=0, timeout=0, level=2048, **overrides):
    fields = dict(profile=1, mode=mode, source=0, edge=edge, reserved=0,
                  level=level, pre=0, reserved2=0, timeout=timeout)
    fields.update(overrides)
    return struct.pack('<HBBBBHHHI', *fields.values())


def hello(all_modes=True):
    return struct.pack('<BBBBHHII', 0, 1, 1, 12, 512, 2048, 0,
                       0x1D if all_modes else 9)


def caps(all_modes=True, **overrides):
    fields = dict(count=1, reserved=0, profile=1, clock=72000000, psc=71,
                  arr=19, adc_clock=12000000, cycles=143, samples=2048,
                  pre_count=1, modes=7 if all_modes else 2, sources=1,
                  edges=3, pre0=0, pre1=0, pre2=0)
    fields.update(overrides)
    return struct.pack('<BBHIHHIHHBBBBHHH', *fields.values())


def samples(generation, window, edge=0, level=2048, timeout=False):
    result = [(73 * i + 19 * window + 7 * generation) % 4096
              for i in range(2048)]
    if not timeout:
        result[0] = level - edge
    result[511], result[1023] = 0, 4095
    return tuple(result)


def sample_bytes(values):
    return struct.pack('<2048H', *values)


def begin(generation=4, window=1, edge=0, level=2048, timeout=False,
          config_id=1, **overrides):
    fields = dict(generation=generation, window=window, config_id=config_id,
                  profile=1, samples=2048, pre=0, index=65535 if timeout else 0,
                  kind=2 if timeout else 0, source=0, edge=edge, channel=0,
                  level=level, quality=3 if timeout else 2, clock=72000000,
                  psc=71, arr=19, external=0)
    fields.update(overrides)
    return struct.pack('<IIIHHHHBBBBHHIHHI', *fields.values())


def data(values, offset, generation=4, window=1):
    return struct.pack('<IIHH', generation, window, offset, len(values)) + struct.pack(
        '<' + 'H' * len(values), *values)


def end(values, generation=4, window=1, crc=None):
    if crc is None:
        crc = crc32(sample_bytes(values))
    return struct.pack('<IIHHI', generation, window, 2048, 0, crc)

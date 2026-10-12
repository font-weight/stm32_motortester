"""HOST-008 independent scalar/byte oracle from the declared draft documents.

No production imports. CRC is bitwise ISO-HDLC; COBS is emitted by a cursor,
independently of the production split-on-zero codec. Nominal synthetic waveform
is the published HOST-007 fixture contract, not a measured input.
"""
import struct

NONCE = 0x1234567890ABCDEF
BASE = 'ef2880c0655d155284c176424a12378b3b66adf4'
SESSION_PIN = 'b6ae8025baf1a47e8173997029d2d8b858ada74c'
WINDOW_PIN = '124b3f32e1e7f7282bce283baa4c9406d4d72950'
EMULATOR_PIN = 'c54bd955aaf3cb863ef3df043ea237ea62e74ad4'


def crc32(raw):
    result = 0xFFFFFFFF
    for byte in raw:
        result ^= byte
        for _ in range(8):
            result = (result >> 1) ^ (0xEDB88320 if result & 1 else 0)
    return result ^ 0xFFFFFFFF


def packet(kind, session, sequence, request, payload):
    raw = struct.pack('<BBBBHHQII', 0, 1, kind, 0, len(payload), 0,
                      session, sequence, request) + payload
    raw += crc32(raw).to_bytes(4, 'little')
    encoded, code_at, code = bytearray(b'\0'), 0, 1
    for byte in raw:
        if byte == 0:
            encoded[code_at] = code
            code_at = len(encoded)
            encoded.append(0)
            code = 1
        else:
            encoded.append(byte)
            code += 1
            if code == 255:
                encoded[code_at] = code
                code_at = len(encoded)
                encoded.append(0)
                code = 1
    encoded[code_at] = code
    encoded.append(0)
    return bytes(encoded)


def samples(generation=4, window=1, edge=0, level=2048):
    values = [(73 * i + 19 * window + 7 * generation) % 4096 for i in range(2048)]
    values[0], values[511], values[1023] = level - edge, 0, 4095
    return tuple(values)


def sample_bytes(values):
    return b''.join(value.to_bytes(2, 'little') for value in values)


def begin(generation=4, window=1, config_id=1, *, edge=0, level=2048,
          quality=2, mode=1, source=0):
    # External Auto is used only to construct the receiver's deferred outcome.
    return struct.pack('<IIIHHHHBBBBHHIHHI', generation, window, config_id,
                       1, 2048, 0, 0, source, source, edge, 0, level, quality,
                       72000000, 71, 19, 0)


def data(values, offset=0, generation=4, window=1):
    return struct.pack('<IIHH', generation, window, offset, len(values)) + sample_bytes(values)


def end(values=None, generation=4, window=1):
    values = samples(generation, window) if values is None else values
    return struct.pack('<IIHHI', generation, window, 2048, 0, crc32(sample_bytes(values)))


def release(generation, window, crc):
    return struct.pack('<III', generation, window, crc)


def ack(generation, window):
    return struct.pack('<II', generation, window)


def vectors():
    rows = []
    for window in range(1, 21):
        raw = sample_bytes(samples(4, window))
        crc = crc32(raw)
        payload = release(4, window, crc)
        rows.append({'window': window, 'generation': 4, 'sample_count': 2048,
                     'samples_crc32': crc, 'release_payload_hex': payload.hex(),
                     'release_packet_hex': packet(7, NONCE, window + 4, window + 4, payload).hex(),
                     'release_ack_payload_hex': ack(4, window).hex()})
    return rows

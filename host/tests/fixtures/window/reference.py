"""Независимые синтетические входы HOST-004, не код приложения.

Поля собраны по закреплённому wire-документу, без импорта motortester.
CRC вычисляется побитно (не zlib), COBS — индексным построителем.
"""
import hashlib
import json
from pathlib import Path


WIRE_SHA = "124b3f32e1e7f7282bce283baa4c9406d4d72950"
SESSION = 0x0807060504030201


def le(number, width):
    return number.to_bytes(width, "little")


def crc32(raw):
    value = 0xFFFFFFFF
    for byte in raw:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
    return value ^ 0xFFFFFFFF


def codes_bytes(codes):
    return b"".join(le(code, 2) for code in codes)


def cobs(raw):
    output = bytearray([0])
    code_at, code = 0, 1
    for byte in raw:
        if byte == 0:
            output[code_at] = code
            code_at = len(output)
            output.append(0)
            code = 1
        else:
            output.append(byte)
            code += 1
            if code == 255:
                output[code_at] = code
                code_at = len(output)
                output.append(0)
                code = 1
    output[code_at] = code
    return bytes(output)


def packet(kind, payload, session=SESSION, sequence=2, request_id=0):
    body = (bytes((0, 1, kind, 0)) + le(len(payload), 2) + b"\0\0"
            + le(session, 8) + le(sequence, 4) + le(request_id, 4) + payload)
    return cobs(body + le(crc32(body), 4)) + b"\0"


def begin(generation=100, window_id=1, config_id=200, profile=1,
          sample_count=2048, pretrigger_samples=0, trigger_index=0,
          trigger_kind=0, source=0, edge=0, channel=0, level_code=2048,
          quality_flags=2, timer_clock_hz=72000000, timer_psc=71,
          timer_arr=19, external_edge_id=0):
    fields = ((generation, 4), (window_id, 4), (config_id, 4), (profile, 2),
              (sample_count, 2), (pretrigger_samples, 2), (trigger_index, 2),
              (trigger_kind, 1), (source, 1), (edge, 1), (channel, 1),
              (level_code, 2), (quality_flags, 2), (timer_clock_hz, 4),
              (timer_psc, 2), (timer_arr, 2), (external_edge_id, 4))
    return b"".join(le(value, width) for value, width in fields)


def data(offset, samples, generation=100, window_id=1, count=None):
    return (le(generation, 4) + le(window_id, 4) + le(offset, 2)
            + le(len(samples) if count is None else count, 2)
            + codes_bytes(samples))


def end(checksum, generation=100, window_id=1, total=2048, reserved=0):
    return (le(generation, 4) + le(window_id, 4) + le(total, 2)
            + le(reserved, 2) + le(checksum, 4))


def abort(reason=1, generation=100, window_id=1, reserved=0):
    return (le(generation, 4) + le(window_id, 4)
            + le(reason, 2) + le(reserved, 2))


def sample_pattern(index):
    if index == 0:
        return (0,) * 2048
    if index == 1:
        return (4095,) * 2048
    if index == 2:
        return tuple(range(2048))
    if index == 3:
        return (0, 4095) * 1024
    state = index + 1
    values = [0, 4095]
    for _ in range(2046):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        values.append((state >> 16) & 4095)
    return tuple(values)


def build_fixtures():
    stream, samples_file, vectors = bytearray(), bytearray(), []
    sequence = 2
    for index in range(20):
        samples = sample_pattern(index)
        raw_samples = codes_bytes(samples)
        checksum = crc32(raw_samples)
        mode, source, edge = index % 3, (index // 3) % 2, index % 2
        pre = (0, 512, 1024)[index % 3]
        kind = 2 if mode == 0 and index % 2 == 0 else source
        edge_id = 0 if kind != 1 else (0 if mode == 0 else index + 1)
        flags = 2 | (1 if kind == 2 else 0) | (4 if kind == 1 else 0)
        metadata = dict(window_id=index + 1, pretrigger_samples=pre,
                        trigger_index=65535 if kind == 2 else pre,
                        trigger_kind=kind, source=source, edge=edge,
                        quality_flags=flags, external_edge_id=edge_id)
        context = dict(session=SESSION, generation=100, config_id=200,
                       acquisition_mode=mode, source=source, edge=edge,
                       level_code=2048, pretrigger_samples=pre, profile=1)
        payloads = [(144, begin(**metadata))]
        counts = []
        offset = 0
        pattern = ((250,), (1, 249, 73, 127), (17, 83, 250, 211))[index % 3]
        while offset < 2048:
            count = min(pattern[len(counts) % len(pattern)], 2048 - offset)
            payloads.append((145, data(offset, samples[offset:offset + count],
                                       window_id=index + 1)))
            counts.append(count)
            offset += count
        payloads.append((146, end(checksum, window_id=index + 1)))
        packets = []
        for message_type, payload in payloads:
            packets.append(packet(message_type, payload, sequence=sequence))
            sequence += 1
        wire = b"".join(packets)
        vectors.append(dict(index=index, context=context, begin=metadata,
                            stream_offset=len(stream), stream_length=len(wire),
                            samples_offset=len(samples_file),
                            samples_crc32=checksum, block_counts=counts,
                            stream_sha256=hashlib.sha256(wire).hexdigest(),
                            samples_sha256=hashlib.sha256(raw_samples).hexdigest(),
                            deferred_checks=["AUTO_EXTERNAL_EDGE_ID"]
                            if mode == 0 and kind == 1 else []))
        stream.extend(wire)
        samples_file.extend(raw_samples)
    return (bytes(stream), bytes(samples_file),
            dict(protocol_source_sha=WIRE_SHA, origin="synthetic", vectors=vectors))


if __name__ == "__main__":
    # Нет bare assert: проверки и генерация сохраняются также при python -O.
    if crc32(b"123456789") != 0xCBF43926:
        raise SystemExit("Ошибка независимого CRC-32 self-check")
    directory = Path(__file__).parent
    stream, samples, manifest = build_fixtures()
    (directory / "windows.bin").write_bytes(stream)
    (directory / "samples.bin").write_bytes(samples)
    (directory / "vectors.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Синтетика: {len(manifest['vectors'])} окон; {len(stream)} байт потока; "
          f"{len(samples)} байт исходных кодов")

"""Независимые тестовые ожидания HOST-002; не код приложения.

COBS строится разбиением по нулю и группам 254, CRC — через zlib.
Здесь нет импорта motortester.wire и копирования реализации C codec.
"""
import zlib

WIRE_SHA = "124b3f32e1e7f7282bce283baa4c9406d4d72950"
STREAM_SHA = "8d5f68c7b8b56ed3d0bd32b0468c10bed1e0e0b7"


def cobs(raw):
    blocks = []
    for run in raw.split(b"\0"):
        while len(run) >= 254:
            blocks.append(b"\xff" + run[:254])
            run = run[254:]
        blocks.append(bytes([len(run) + 1]) + run)
    return b"".join(blocks)


def checksum(body):
    return zlib.crc32(body).to_bytes(4, "little")


def raw_message(message_type, session, sequence, request_id, payload):
    return (bytes([0, 1, message_type, 0]) + len(payload).to_bytes(2, "little")
            + b"\0\0" + session.to_bytes(8, "little")
            + sequence.to_bytes(4, "little") + request_id.to_bytes(4, "little")
            + payload)


def raw_with_crc(body):
    return body + checksum(body)


def packet(body):
    return cobs(raw_with_crc(body)) + b"\0"


def fixture_inputs():
    return [
        ("opaque_type_a_reference", 127, 0x0807060504030201, 1, 2,
         bytes.fromhex("0001feff007e")),
        ("zero_empty", 0, 0, 0, 0, b""),
        ("mixed_little_endian", 129, 0xFEDCBA9876543210, 0x78563412,
         0xF0DEBC9A, bytes.fromhex("001122003344ff0080")),
        ("maximum_zero_payload", 145, 0, 0, 0, b"\0" * 512),
        ("maximum_nonzero_frame", 255, 2**64-1, 2**32-1, 2**32-1, b"\xff" * 512),
        ("all_byte_values_twice", 145, 0x0102030405060708, 0x11223344,
         0x55667788, bytes(range(256)) * 2),
        ("cobs_254_boundary", 7, 0, 0, 0, b"\x55" * 254),
        ("cobs_255_boundary", 7, 0, 0, 0, b"\x55" * 255),
        ("cobs_508_boundary", 7, 0, 0, 0, b"\x55" * 508),
    ]

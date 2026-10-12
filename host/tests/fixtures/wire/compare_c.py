"""Сверка независимых ожиданий с неизменённым C codec конкретного SHA.

Запуск из корня: python host/tests/fixtures/wire/compare_c.py
Нужны Git, Python и cc. Исходники A извлекаются только чтением во временный
каталог; ветки/index не меняются, производственный Python codec не импортируется.
"""
import ctypes as C
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tempfile

import reference as ref

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parent


class Message(C.Structure):
    _fields_ = [("type", C.c_uint8), ("session", C.c_uint64),
                ("sequence", C.c_uint32), ("request", C.c_uint32),
                ("payload_length", C.c_uint16), ("payload", C.c_uint8 * 512)]


def git_bytes(path):
    return subprocess.check_output(["git", "show", f"{ref.WIRE_SHA}:{path}"], cwd=ROOT)


def cbytes(data):
    return (C.c_uint8 * max(1, len(data)))(*data)


def message_values(message):
    return (message.type, message.session, message.sequence, message.request,
            bytes(message.payload[:message.payload_length]))


def main():
    if not __debug__:
        raise RuntimeError("Проверка требует включённых assert; не используйте python -O/-OO")
    sha = subprocess.check_output(["git", "rev-parse", ref.WIRE_SHA], cwd=ROOT, text=True).strip()
    if sha != ref.WIRE_SHA:
        raise RuntimeError("Не совпал SHA источника C")
    sources = {p: git_bytes(p) for p in (
        "firmware/protocol/mt_wire.c", "firmware/protocol/mt_wire.h",
        "protocol/vectors-v0.1.json", "protocol/wire-v0.1.md")}
    published = json.loads(sources["protocol/vectors-v0.1.json"])
    independent = json.loads((FIXTURES / "vectors.json").read_text(encoding="utf-8"))
    assert independent["vectors"][0]["raw_hex"] == published["vectors"][0]["decoded_hex"]
    assert independent["vectors"][0]["frame_hex"] == published["vectors"][0]["frame_hex"]
    vectors = ref.fixture_inputs()
    for length in range(513):
        payload = bytes((index * 73 + length) % 256 for index in range(length))
        vectors.append((f"length_{length}", 145, 0x1020304050607080,
                        length, 0xF0E0D0C0, payload))
    compile_flags = ["-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-shared", "-fPIC"]
    with tempfile.TemporaryDirectory(prefix="host002-c-reference-") as temporary:
        directory = Path(temporary)
        for path, data in sources.items():
            if path.endswith((".c", ".h")):
                target = directory / Path(path).name
                target.write_bytes(data)
                target.chmod(0o444)
        library_path = directory / "libmtwire.so"
        subprocess.run(["cc", *compile_flags, str(directory / "mt_wire.c"),
                        "-o", str(library_path)], check=True)
        library = C.CDLL(str(library_path))
        pointer = C.POINTER(C.c_uint8)
        library.mt_wire_crc32.argtypes = [pointer, C.c_size_t]
        library.mt_wire_crc32.restype = C.c_uint32
        library.mt_wire_encode.argtypes = [C.POINTER(Message), pointer, C.c_size_t,
                                           C.POINTER(C.c_size_t)]
        library.mt_wire_encode.restype = C.c_int
        library.mt_wire_decode.argtypes = [pointer, C.c_size_t, C.POINTER(Message)]
        library.mt_wire_decode.restype = C.c_int
        assert library.mt_wire_crc32(cbytes(b"123456789"), 9) == 0xCBF43926
        assert library.mt_wire_crc32(cbytes(b""), 0) == 0
        for name, *values in vectors:
            body = ref.raw_message(*values)
            wanted = ref.packet(body)
            message = Message()
            message.type, message.session, message.sequence, message.request = values[:4]
            message.payload_length = len(values[4])
            message.payload[:len(values[4])] = values[4]
            encoded = (C.c_uint8 * 544)(*([0xA5] * 544))
            written = C.c_size_t(9876)
            assert library.mt_wire_encode(C.byref(message), encoded, 544, C.byref(written)) == 0, name
            assert written.value == len(wanted), name
            assert bytes(encoded[:written.value]) == wanted, name
            assert bytes(encoded[written.value:]) == b"\xa5" * (544-written.value), name
            decoded = Message()
            C.memset(C.byref(decoded), 0xA5, C.sizeof(decoded))
            assert library.mt_wire_decode(cbytes(wanted[:-1]), len(wanted)-1, C.byref(decoded)) == 0, name
            assert message_values(decoded) == tuple(values), name
        body = ref.raw_message(*ref.fixture_inputs()[0][1:])
        raw = ref.raw_with_crc(body)
        # Номера кодов — enum mt_wire_result из закреплённого заголовка A.
        negatives = [("empty", b"", 4), ("zero", b"\0", 3),
                     ("truncated_cobs", b"\xff", 3),
                     ("cobs_zero_data", b"\x02\0", 3),
                     ("raw_underflow", ref.cobs(b"\0"*27), 4),
                     ("raw_overflow", ref.cobs(b"\0"*541), 4),
                     ("frame_overflow", b"\x01"*544, 4),
                     ("delimiter_supplied", ref.packet(body), 3)]
        for offset, value, code in ((0, 1, 6), (1, 2, 6), (3, 1, 7),
                                    (6, 1, 7), (7, 1, 7), (4, 5, 4), (5, 3, 4)):
            changed = bytearray(body)
            changed[offset] = value
            negatives.append((f"field_{offset}", ref.packet(bytes(changed))[:-1], code))
        for index in range(len(raw)):
            changed = bytearray(raw)
            changed[index] ^= 1
            code = 4 if index in (4, 5) else 5
            negatives.append((f"bit_corruption_{index}", ref.cobs(bytes(changed)), code))
        for name, data, code in negatives:
            decoded = Message()
            C.memset(C.byref(decoded), 0xA5, C.sizeof(decoded))
            before = bytes(decoded)
            result = library.mt_wire_decode(cbytes(data), len(data), C.byref(decoded))
            assert result == code, (name, result, code)
            assert bytes(decoded) == before, name
        for payload_length, capacity, code in ((513, 544, 4), (0, 543, 2)):
            message = Message()
            message.payload_length = payload_length
            encoded = (C.c_uint8 * 544)(*([0xA5] * 544))
            written = C.c_size_t(9876)
            assert library.mt_wire_encode(C.byref(message), encoded, capacity, C.byref(written)) == code
            assert bytes(encoded) == b"\xa5" * 544
            assert written.value == 9876
    report = dict(
        status="PASS: синтетическая Linux-сверка; не приёмка общего wire-контракта",
        wire_source_sha=sha,
        source_sha256={p: hashlib.sha256(data).hexdigest() for p, data in sources.items()},
        fixture_sha256=hashlib.sha256((FIXTURES/"vectors.json").read_bytes()).hexdigest(),
        compiler=subprocess.check_output(["cc", "--version"], text=True).splitlines()[0],
        compile_flags=compile_flags,
        python=platform.python_version(), platform=platform.platform(),
        positive_encode_decode_vectors=len(vectors),
        negative_decode_vectors=len(negatives),
        negative_encode_checks=2, crc_checks=2, published_a_vectors_matched=1,
        limitations=["Native Linux C ABI, не ARM/STM32", "C stream PR7 не запускался",
                     "Нет USB, Windows, устройства, сеанса и payload-диспетчера",
                     "Не заключение Critic-A/Critic-B и не оценка качества"],
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

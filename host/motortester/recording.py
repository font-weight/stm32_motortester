"""Bounded uncompressed raw recording; SHA-256 detects corruption, not forgery."""
import csv
import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
import tempfile
from dataclasses import asdict
from .model import Capture, CaptureError, MAX_SAMPLES

MAGIC = b"MTCAP\x00\x01\x00"
HEADER = struct.Struct("<8sII")
MAX_METADATA = 16384
MAX_FILE = HEADER.size + MAX_METADATA + MAX_SAMPLES * 2 + 32


def encode(capture: Capture) -> bytes:
    values = asdict(capture)
    samples = values.pop("samples")
    values.update(format_version=1, channel_count=1, adc_bits=12,
                  sample_encoding="uint16-le", time_basis="nominal_unverified",
                  amplitude_unit="adc_code", calibration="uncalibrated",
                  timestamp_origin="host_receive", sample_count=len(samples))
    metadata = json.dumps(values, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(metadata) > MAX_METADATA:
        raise CaptureError("Метаданные превышают допустимый размер")
    payload = HEADER.pack(MAGIC, len(metadata), len(samples)) + metadata
    payload += struct.pack(f"<{len(samples)}H", *samples)
    return payload + hashlib.sha256(payload).digest()


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise CaptureError("Повторяющееся поле метаданных")
        obj[key] = value
    return obj


def _invalid_number(value):
    raise CaptureError("Недопустимое число JSON")


def decode(data: bytes) -> Capture:
    if not isinstance(data, bytes) or not HEADER.size + 32 <= len(data) <= MAX_FILE:
        raise CaptureError("Недопустимый размер файла")
    magic, meta_len, count = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise CaptureError("Неизвестный формат или неподдерживаемая версия файла")
    if not 1 <= meta_len <= MAX_METADATA or not 1 <= count <= MAX_SAMPLES:
        raise CaptureError("Размеры записи превышают ограничения")
    expected = HEADER.size + meta_len + count * 2 + 32
    if len(data) != expected:
        raise CaptureError("Файл обрезан или содержит лишние данные")
    if not hmac.compare_digest(hashlib.sha256(data[:-32]).digest(), data[-32:]):
        raise CaptureError("Нарушена целостность записи (SHA-256)")
    try:
        metadata_text = data[HEADER.size:HEADER.size + meta_len].decode("utf-8")
        meta = json.loads(metadata_text, object_pairs_hook=_object,
                          parse_constant=_invalid_number)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise CaptureError("Некорректные метаданные JSON") from exc
    if type(meta) is not dict:
        raise CaptureError("Метаданные должны быть объектом")
    constants = dict(format_version=1, channel_count=1, adc_bits=12,
                     sample_encoding="uint16-le", time_basis="nominal_unverified",
                     amplitude_unit="adc_code", calibration="uncalibrated",
                     timestamp_origin="host_receive", sample_count=count)
    for key, value in constants.items():
        actual = meta.pop(key, None)
        if type(actual) is not type(value) or actual != value:
            raise CaptureError(f"Неподдерживаемое поле записи: {key}")
    required = set(Capture.__dataclass_fields__) - {"samples"}
    if set(meta) != required or type(meta.get("quality_flags")) is not list:
        raise CaptureError("Неизвестные или отсутствующие поля записи")
    meta["quality_flags"] = tuple(meta["quality_flags"])
    samples = struct.unpack_from(f"<{count}H", data, HEADER.size + meta_len)
    try:
        return Capture(samples=samples, **meta)
    except (TypeError, ValueError) as exc:
        raise CaptureError(f"Некорректная запись: {exc}") from exc


def load(path: str | Path) -> Capture:
    with open(path, "rb") as stream:
        data = stream.read(MAX_FILE + 1)
    return decode(data)


def atomic_write(path: str | Path, data: bytes, *, overwrite: bool = False):
    """Leave an existing recording untouched on serialization/write failure."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".motortester-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Hard-link creation is atomic and fails if the destination exists.
            # Both paths are on the same filesystem. Windows NTFS supports it.
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path: str | Path, capture: Capture, *, overwrite: bool = False):
    atomic_write(path, encode(capture), overwrite=overwrite)


def export_csv(path: str | Path, capture: Capture, *, overwrite: bool = False):
    import io
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("sample_index", "time_nominal_s", "adc_code", "origin", "session_id", "window_sequence"))
    for i, code in enumerate(capture.samples):
        writer.writerow((i, format(capture.time_seconds(i), ".17g"), code,
                         capture.origin, capture.session_id, capture.sequence))
    atomic_write(path, stream.getvalue().encode("utf-8"), overwrite=overwrite)

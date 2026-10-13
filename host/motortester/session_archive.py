"""Bounded segmented storage, independent of transport and GUI.

A commit is a storage receipt, never permission to RELEASE a device window.
MTCAP v1 bytes are preserved verbatim. Device gaps and physical Stop are unknown.
See docs/session-recording-format.md for the deliberately narrow ZIP profile.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import tempfile
from uuid import UUID
import zipfile
import zlib

from .model import Capture, CaptureError, MAX_SAMPLES
from .recording import MAX_FILE as MAX_CAPTURE_BYTES, decode, encode

MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_SEGMENTS = 32768  # Includes complete windows, partial windows and abort events.
MAX_EVENTS = 4096
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 16384
MAX_INDEX_BYTES = 65536
MAX_FRAGMENTS = 1024
MAX_TEXT = 65536
MAX_JSON_NODES = 3 * 1024 * 1024
MAX_JSON_OBJECT_FIELDS = 32
MAX_JSON_KEY_CHARS = 64
MAX_JSON_NUMBER_BYTES = 64
# Enough for outcome JSON and an error report even when the payload quota is full.
TERMINAL_RESERVE = MAX_DIAGNOSTIC_BYTES + MAX_HEADER_BYTES
MAX_ENTRIES = MAX_SEGMENTS + 2
MAX_NAME_BYTES = 32
MAX_DIRECTORY_BYTES = MAX_ENTRIES * (46 + MAX_NAME_BYTES)
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + MAX_ENTRIES * (76 + 2 * MAX_NAME_BYTES) + 22
SPOOL_MAGIC = b"MTSP\x00\x01\x00\x00"
FRAME = struct.Struct("<4sI")
EOCD = struct.Struct("<4s4H2IH")
LOCAL = struct.Struct("<4s5H3I2H")
CENTRAL = struct.Struct("<4s6H3I5H2I")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REASONS = ("user_stop", "single_complete", "error", "capacity", "recovered")
_EVIDENCE = ("none", "stop_ack", "single_release_ack", "unknown")
_CONSTANTS = dict(format="motortester-session", format_version=1,
                  capture_format="mtcap-v1", coverage="segmented",
                  time_basis="per_segment_nominal_unverified",
                  intersegment_device_gap="unknown")


class SessionArchiveError(CaptureError):
    """Invalid session data or exhausted bounded storage."""


class CapacityError(SessionArchiveError):
    """Admission would consume a quota or reserved terminal space."""


class SaveCancelled(SessionArchiveError):
    """Cancellation observed before atomic publication."""


def _require(test, message="Некорректный формат сеанса"):
    if not test:
        raise SessionArchiveError(message)


def _uint(value, maximum, minimum=0):
    _require(type(value) is int and minimum <= value <= maximum,
             "Целое значение вне допустимого диапазона")


def _text(value, maximum, *, empty=False):
    _require(type(value) is str and (0 if empty else 1) <= len(value) <= maximum,
             "Недопустимая длина текста")
    _require(not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF
                     or ord(c) in (0xFFFE, 0xFFFF) for c in value),
             "Недопустимый символ в тексте")


def _stamp(value):
    _text(value, 64)
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0)
    except ValueError as exc:
        raise SessionArchiveError("Ожидается метка времени UTC") from exc


def _uuid(value):
    _text(value, 45)
    try:
        UUID(value)
    except ValueError as exc:
        raise SessionArchiveError("Некорректный UUID") from exc


def _exact(obj, fields):
    _require(type(obj) is dict and set(obj) == set(fields),
             "Неизвестные или отсутствующие поля сеанса")


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Повторяющееся поле JSON")
        result[key] = value
    return result


def _bad_number(_):
    raise SessionArchiveError("Недопустимое число JSON")


class _JSONGuard:
    """Bounded structural pass without a decoded value tree.

    The general decoder runs only after every independent resource budget passed.
    This does not replace semantic/schema validation or duplicate-field detection.
    """
    def __init__(self, data):
        self.data = data
        self.position = 0
        self.nodes = 0

    def _space(self):
        while self.position < len(self.data) and self.data[self.position] in b" \t\r\n":
            self.position += 1

    def _take(self, expected):
        self._space()
        _require(self.position < len(self.data) and self.data[self.position] == expected,
                 "Некорректная структура JSON")
        self.position += 1

    def _string(self, *, key=False):
        self._take(34)
        start = self.position - 1
        count = 0
        maximum = MAX_JSON_KEY_CHARS if key else MAX_TEXT
        while self.position < len(self.data):
            byte = self.data[self.position]
            self.position += 1
            if byte == 34:
                if key:
                    # A single bounded key is decoded, never a container/value tree.
                    token = self.data[start:self.position].decode("utf-8")
                    return json.decoder.scanstring(token, 1, True)[0]
                return None
            _require(byte >= 32, "Управляющий символ JSON")
            if byte == 92:
                _require(self.position < len(self.data))
                escape = self.data[self.position]
                self.position += 1
                _require(escape in b'"\\/bfnrtu', "Некорректное экранирование JSON")
                if escape == 117:
                    _require(self.position + 4 <= len(self.data))
                    code = self.data[self.position:self.position + 4]
                    _require(all(c in b"0123456789abcdefABCDEF" for c in code))
                    value = int(code, 16)
                    self.position += 4
                    # JSON surrogate pairs decode to one Unicode character.
                    if 0xD800 <= value <= 0xDBFF and self.data[self.position:self.position + 2] == b"\\u":
                        low = self.data[self.position + 2:self.position + 6]
                        if len(low) == 4 and all(c in b"0123456789abcdefABCDEF" for c in low):
                            if 0xDC00 <= int(low, 16) <= 0xDFFF:
                                self.position += 6
                count += 1
            elif byte & 0xC0 != 0x80:
                # Strict UTF-8 validation follows before general decoding. Counting
                # leading bytes avoids penalizing valid multibyte code points.
                count += 1
            _require(count <= maximum, "Строка JSON превышает ограничение")
            # Also bound malformed continuation bytes before UTF-8 decoding.
            _require(self.position - start <= 12 * maximum + 1,
                     "Токен строки JSON превышает ограничение")
        raise SessionArchiveError("Обрезанная строка JSON")

    def _value(self, depth, key=None):
        self._space()
        _require(self.position < len(self.data))
        self.nodes += 1
        _require(self.nodes <= MAX_JSON_NODES, "Слишком много значений JSON")
        byte = self.data[self.position]
        if byte == 123:
            _require(depth < 16, "Слишком глубокая структура JSON")
            self.position += 1
            self._space()
            if self.position < len(self.data) and self.data[self.position] == 125:
                self.position += 1
                return
            fields = 0
            while True:
                fields += 1
                _require(fields <= MAX_JSON_OBJECT_FIELDS, "Слишком много полей JSON")
                field_name = self._string(key=True)
                self._take(58)
                self._value(depth + 1, field_name)
                self._space()
                _require(self.position < len(self.data))
                separator = self.data[self.position]
                self.position += 1
                if separator == 125:
                    return
                _require(separator == 44)
        elif byte == 91:
            _require(depth < 16, "Слишком глубокая структура JSON")
            self.position += 1
            maximum = {"segments": MAX_SEGMENTS, "fragments": MAX_FRAGMENTS,
                       "missing_ranges": MAX_FRAGMENTS + 1, "recovery_issues": 1,
                       "deferred_checks": 16, "metadata_issues": 16,
                       "@pair": 2}.get(key, MAX_SEGMENTS)
            child_key = "@pair" if key in ("fragments", "missing_ranges") else None
            self._space()
            if self.position < len(self.data) and self.data[self.position] == 93:
                self.position += 1
                return
            count = 0
            while True:
                count += 1
                _require(count <= maximum, "Массив JSON превышает ограничение")
                self._value(depth + 1, child_key)
                self._space()
                _require(self.position < len(self.data))
                separator = self.data[self.position]
                self.position += 1
                if separator == 93:
                    return
                _require(separator == 44)
        elif byte == 34:
            self._string()
        elif byte in b"-0123456789":
            start = self.position
            while self.position < len(self.data) and self.data[self.position] in b"-+0123456789.eE":
                self.position += 1
                _require(self.position - start <= MAX_JSON_NUMBER_BYTES,
                         "Числовой токен JSON превышает ограничение")
            # Full number grammar and finite/range semantics are checked later.
        else:
            literal = {116: b"true", 102: b"false", 110: b"null"}.get(byte)
            _require(literal is not None and self.data[self.position:self.position + len(literal)] == literal)
            self.position += len(literal)

    def check(self):
        self._value(0)
        self._space()
        _require(self.position == len(self.data), "Лишний хвост JSON")
        return self.nodes


def _json_bytes(obj, limit):
    data = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    _require(len(data) <= limit, "Метаданные превышают ограничение")
    return data


def _json_load(data, limit):
    _require(type(data) is bytes and 1 <= len(data) <= limit)
    try:
        _JSONGuard(data).check()
        return json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=_bad_number)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise SessionArchiveError("Некорректный JSON сеанса") from exc


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    recording_id: str
    session_id: str
    origin: str
    firmware_version: str | None
    protocol_version: str | None
    application_version: str

    def __post_init__(self):
        _uuid(self.recording_id)
        _uuid(self.session_id)
        _require(type(self.origin) is str and self.origin in ("simulator", "device"))
        for value in (self.firmware_version, self.protocol_version):
            if value is not None:
                _text(value, 128)
        _text(self.application_version, 128)

    def validate_capture(self, capture):
        self.__post_init__()
        for key in ("session_id", "origin", "firmware_version", "protocol_version",
                    "application_version"):
            _require(getattr(capture, key) == getattr(self, key),
                     "Окно принадлежит другому источнику или сеансу")


@dataclass(frozen=True, slots=True)
class SegmentProvenance:
    generation: int | None = None
    config_id: int | None = None
    acquisition_mode: int | None = None
    auto_timeout_ms: int | None = None
    host_elapsed_ms: int | None = None
    wire_version: str | None = None
    start_operation: str | None = None
    wire_session_nonce: int | None = None
    config_payload_hex: str | None = None
    begin_request_id: int | None = None
    begin_sequence: int | None = None
    terminal_kind: str | None = None
    terminal_request_id: int | None = None
    terminal_sequence: int | None = None
    end_samples_crc32: int | None = None
    abort_reason_code: int | None = None
    unavailable_reason: str | None = "not_supplied"

    def __post_init__(self):
        for value, maximum, minimum in ((self.generation, 0xFFFFFFFE, 2),
                                        (self.config_id, 0xFFFFFFFE, 1),
                                        (self.host_elapsed_ms, 0xFFFFFFFFFFFFFFFF, 0)):
            if value is not None:
                _uint(value, maximum, minimum)
        if self.acquisition_mode is None:
            _require(self.auto_timeout_ms is None)
        else:
            _uint(self.acquisition_mode, 2)
            _uint(self.auto_timeout_ms, 60000)
            _require(100 <= self.auto_timeout_ms <= 60000 if self.acquisition_mode == 0
                     else self.auto_timeout_ms == 0)
        _require(self.wire_version is None or (type(self.wire_version) is str
                                              and self.wire_version == "0.1"))
        _require(self.start_operation is None or (type(self.start_operation) is str
                                                 and self.start_operation == "arm"))
        if self.wire_session_nonce is not None:
            _uint(self.wire_session_nonce, 0xFFFFFFFFFFFFFFFF, 1)
        for value in (self.begin_sequence, self.terminal_sequence):
            if value is not None:
                _uint(value, 0xFFFFFFFE, 1)
        for value in (self.begin_request_id, self.terminal_request_id):
            if value is not None:
                _uint(value, 0)  # Unsolicited BEGIN/END/ABORT in wire 0.1.
        _require(self.terminal_kind is None or (type(self.terminal_kind) is str
                 and self.terminal_kind in ("end", "abort")))
        if self.end_samples_crc32 is not None:
            _uint(self.end_samples_crc32, 0xFFFFFFFF)
            _require(self.terminal_kind == "end")
        if self.abort_reason_code is not None:
            _uint(self.abort_reason_code, 6, 1)
            _require(self.terminal_kind == "abort")
        if self.begin_sequence is not None and self.terminal_sequence is not None:
            _require(self.terminal_sequence > self.begin_sequence)
        if self.config_payload_hex is not None:
            _require(type(self.config_payload_hex) is str and
                     re.fullmatch(r"[0-9a-f]{32}", self.config_payload_hex) is not None)
            profile, mode, source, edge, reserved8, level, pre, reserved16, timeout = (
                struct.unpack("<HBBBBHHHI", bytes.fromhex(self.config_payload_hex)))
            _require(profile == 1 and mode in (0, 1, 2) and source in (0, 1)
                     and edge in (0, 1) and reserved8 == reserved16 == 0
                     and 1 <= level <= 4094 and pre in (0, 512, 1024))
            _require(self.wire_version == "0.1" and mode == self.acquisition_mode
                     and timeout == self.auto_timeout_ms)
        inapplicable = {"unavailable_reason"}
        if self.terminal_kind == "end":
            inapplicable.add("abort_reason_code")
        elif self.terminal_kind == "abort":
            inapplicable.add("end_samples_crc32")
        unknown = any(getattr(self, key) is None for key in self.__dataclass_fields__
                      if key not in inapplicable)
        if unknown:
            _text(self.unavailable_reason, 1024)
        else:
            _require(self.unavailable_reason is None)



@dataclass(frozen=True, slots=True)
class FullSegment:
    """Keep the exact runtime Capture object; serialization never substitutes it."""
    capture: Capture
    provenance: SegmentProvenance = SegmentProvenance()

    def __post_init__(self):
        _require(type(self.capture) is Capture)
        self.capture.__post_init__()
        _provenance(self.provenance)


@dataclass(frozen=True, slots=True)
class SampleFragment:
    offset: int
    samples: tuple[int, ...]

    def __post_init__(self):
        _uint(self.offset, MAX_SAMPLES - 1)
        _require(type(self.samples) is tuple and 1 <= len(self.samples) <= MAX_SAMPLES)
        for value in self.samples:
            _uint(value, 4095)


@dataclass(frozen=True, slots=True)
class PartialDetails:
    expected_samples: int
    fragments: tuple[tuple[int, int], ...]  # (original offset, count), storage order
    missing_ranges: tuple[tuple[int, int], ...]  # half-open [start, end)
    reason: str
    nominal_fs_hz: float | None = None
    received_at_utc: str | None = None
    deferred_checks: tuple[str, ...] = ()
    metadata_issues: tuple[str, ...] = ()

    def __post_init__(self):
        _uint(self.expected_samples, MAX_SAMPLES, 1)
        _text(self.reason, 1024)
        _require(type(self.fragments) is tuple and len(self.fragments) <= MAX_FRAGMENTS)
        missing, end = [], 0
        for pair in self.fragments:
            _require(type(pair) is tuple and len(pair) == 2)
            offset, count = pair
            _uint(offset, self.expected_samples - 1)
            _uint(count, self.expected_samples, 1)
            _require(offset >= end and offset + count <= self.expected_samples)
            if offset > end:
                missing.append((end, offset))
            end = offset + count
        if end < self.expected_samples:
            missing.append((end, self.expected_samples))
        _require(type(self.missing_ranges) is tuple and len(self.missing_ranges) <= MAX_FRAGMENTS + 1)
        for pair in self.missing_ranges:
            _require(type(pair) is tuple and len(pair) == 2)
            _uint(pair[0], self.expected_samples)
            _uint(pair[1], self.expected_samples)
        _require(self.missing_ranges == tuple(missing),
                 "Пропуски не совпадают с границами принятых фрагментов")
        # A complete array with failed integrity/terminal checks may still be partial.
        if self.nominal_fs_hz is not None:
            _require(type(self.nominal_fs_hz) in (int, float)
                     and 1 <= self.nominal_fs_hz <= 1_000_000
                     and math.isfinite(self.nominal_fs_hz))
        if self.received_at_utc is not None:
            _stamp(self.received_at_utc)
        for annotations in (self.deferred_checks, self.metadata_issues):
            _require(type(annotations) is tuple and len(annotations) <= 16)
            for annotation in annotations:
                _text(annotation, 128)


@dataclass(frozen=True, slots=True)
class PartialSegment:
    sequence: int
    expected_samples: int
    fragments: tuple[SampleFragment, ...]
    missing_ranges: tuple[tuple[int, int], ...]
    reason: str
    provenance: SegmentProvenance = SegmentProvenance()
    nominal_fs_hz: float | None = None
    received_at_utc: str | None = None
    deferred_checks: tuple[str, ...] = ()
    metadata_issues: tuple[str, ...] = ()

    def __post_init__(self):
        _uint(self.sequence, 0xFFFFFFFF)
        _provenance(self.provenance)
        _require(type(self.fragments) is tuple and len(self.fragments) <= MAX_FRAGMENTS)
        for fragment in self.fragments:
            _require(type(fragment) is SampleFragment)
        self.details()  # Validate aggregate ranges before traversing or packing samples.
        for fragment in self.fragments:
            fragment.__post_init__()

    def details(self):
        return PartialDetails(self.expected_samples,
                              tuple((f.offset, len(f.samples)) for f in self.fragments),
                              self.missing_ranges, self.reason, self.nominal_fs_hz,
                              self.received_at_utc, self.deferred_checks, self.metadata_issues)


@dataclass(frozen=True, slots=True)
class AbortEvent:
    """A no-BEGIN failure: no invented expected count or empty Capture."""
    reason: str
    sequence: int | None = None
    provenance: SegmentProvenance = SegmentProvenance()

    def __post_init__(self):
        _text(self.reason, 1024)
        if self.sequence is not None:
            _uint(self.sequence, 0xFFFFFFFF)
        _provenance(self.provenance)


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    reason: str
    stop_evidence: str = "none"
    diagnostic: str = ""

    def __post_init__(self):
        _require(type(self.reason) is str and self.reason in _REASONS)
        _require(type(self.stop_evidence) is str and self.stop_evidence in _EVIDENCE)
        _require(self.stop_evidence != "single_release_ack" or self.reason == "single_complete")
        _require(self.reason != "recovered" or self.stop_evidence == "unknown")
        _text(self.diagnostic, MAX_TEXT, empty=True)


@dataclass(frozen=True, slots=True)
class SegmentCommit:
    ordinal: int
    kind: str
    byte_count: int
    sha256: str
    sequence: int | None
    sample_count: int | None
    provenance: SegmentProvenance
    partial: PartialDetails | None = None
    abort_reason: str | None = None

    @property
    def member(self):
        extension = {"capture": "mtcap", "partial": "u16le", "abort": "json"}[self.kind]
        return f"segments/{self.ordinal:08d}.{extension}"


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    identity: SessionIdentity
    entry: SegmentCommit
    capacity_after: "Capacity"
    issuer: object = field(repr=False, compare=False)
    source_item: FullSegment | PartialSegment | AbortEvent | None = field(default=None, repr=False, compare=False)
    _verified: tuple | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def source_capture(self):
        return self.source_item.capture if type(self.source_item) is FullSegment else None

    def matches(self, identity, ordinal, *, issuer, item, sha256):
        """Match exact issuer/item plus session, ordinal and serialized bytes.

        Rejects ordinary public construction/copies. This is an in-process misuse
        guard, not protection against Python reflection. The controller must also
        match its own session/operation token before permitting an ACK.
        """
        if type(self._verified) is not tuple or len(self._verified) != 5:
            return False
        if (type(self.identity) is not SessionIdentity or type(self.entry) is not SegmentCommit
                or type(self.capacity_after) is not Capacity):
            return False
        try:
            self.identity.__post_init__()
            self.capacity_after.__post_init__()
            encoded_entry = _json_bytes(_entry_dict(self.entry), MAX_INDEX_BYTES)
            _entry_from_dict(_json_load(encoded_entry, MAX_INDEX_BYTES))
            proof = (_header(self.identity), encoded_entry,
                     tuple(asdict(self.capacity_after).values()), id(self.issuer), id(self.source_item))
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError):
            return False
        return (self._verified == proof and self.identity == identity
                and type(ordinal) is int and self.entry.ordinal == ordinal
                and self.entry.sha256 == sha256 and self.issuer is issuer
                and self.source_item is item
                and (item is None or self.entry.provenance == item.provenance))


@dataclass(frozen=True, slots=True)
class RecoveryIssue:
    kind: str
    offset: int
    byte_count: int


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    path: Path
    identity: SessionIdentity
    entries: tuple[SegmentCommit, ...]
    outcome: SessionOutcome
    issues: tuple[RecoveryIssue, ...]
    offsets: tuple[int, ...]
    source_bytes: int


@dataclass(frozen=True, slots=True)
class Capacity:
    entries_remaining: int
    events_remaining: int
    manifest_bytes_remaining: int
    logical_bytes_remaining: int
    spool_bytes_remaining: int

    def __post_init__(self):
        for value, maximum in ((self.entries_remaining, MAX_SEGMENTS),
                               (self.events_remaining, MAX_EVENTS),
                               (self.manifest_bytes_remaining, MAX_MANIFEST_BYTES),
                               (self.logical_bytes_remaining, MAX_TOTAL_BYTES),
                               (self.spool_bytes_remaining, MAX_TOTAL_BYTES)):
            _uint(value, maximum)

    def can_reserve_windows(self, count=1):
        """Pure worst-case admission for full/partial windows or no-BEGIN events.

        Includes index/frame overhead in addition to the permanent terminal reserve.
        The controller may reserve two slots before ARM; this performs no I/O.
        """
        _uint(count, MAX_SEGMENTS, 1)
        return (self.entries_remaining >= count and self.events_remaining >= count
                and self.manifest_bytes_remaining >= count * (MAX_INDEX_BYTES + 1)
                and self.logical_bytes_remaining >= count * (MAX_CAPTURE_BYTES + MAX_INDEX_BYTES + 1)
                and self.spool_bytes_remaining >= count * (MAX_CAPTURE_BYTES + MAX_INDEX_BYTES + 48))


@dataclass(frozen=True, slots=True)
class SessionArchive:
    path: Path
    identity: SessionIdentity
    entries: tuple[SegmentCommit, ...]
    outcome: SessionOutcome
    issues: tuple[RecoveryIssue, ...]

    def read_segment(self, ordinal):
        """Read just one bounded member, rechecking its bytes against this index."""
        _uint(ordinal, len(self.entries) - 1)
        entry = self.entries[ordinal]
        with _checked_zip(self.path) as archive:
            _require(entry.member in archive.namelist())
            data = _zip_read(archive, archive.getinfo(entry.member), _entry_limit(entry.kind))
            _validate_payload(entry, data, self.identity)
            return data


def _provenance(value):
    _require(type(value) is SegmentProvenance)
    value.__post_init__()


def _from_dataclass(cls, obj):
    _exact(obj, cls.__dataclass_fields__)
    return cls(**obj)


def _entry_dict(entry):
    return asdict(entry)


def _entry_from_dict(obj):
    _exact(obj, SegmentCommit.__dataclass_fields__)
    values = obj.copy()
    values["provenance"] = _from_dataclass(SegmentProvenance, values["provenance"])
    if values["partial"] is not None:
        part = values["partial"]
        _exact(part, PartialDetails.__dataclass_fields__)
        part = part.copy()
        for key in ("fragments", "missing_ranges"):
            _require(type(part[key]) is list and len(part[key]) <= MAX_FRAGMENTS + 1)
            _require(all(type(pair) is list and len(pair) == 2 for pair in part[key]))
            part[key] = tuple(tuple(pair) for pair in part[key])
        for key in ("deferred_checks", "metadata_issues"):
            _require(type(part[key]) is list and len(part[key]) <= 16)
            part[key] = tuple(part[key])
        values["partial"] = PartialDetails(**part)
    entry = SegmentCommit(**values)
    _uint(entry.ordinal, MAX_SEGMENTS - 1)
    _require(type(entry.kind) is str and entry.kind in ("capture", "partial", "abort"))
    _uint(entry.byte_count, _entry_limit(entry.kind))
    _require(type(entry.sha256) is str and _HEX.fullmatch(entry.sha256) is not None)
    if entry.sequence is not None:
        _uint(entry.sequence, 0xFFFFFFFF)
    if entry.kind == "abort":
        _require(entry.sample_count is None and entry.partial is None)
        _text(entry.abort_reason, 1024)
    else:
        _uint(entry.sequence, 0xFFFFFFFF)
        _uint(entry.sample_count, MAX_SAMPLES, 1 if entry.kind == "capture" else 0)
        _require(entry.abort_reason is None)
        if entry.kind == "capture":
            _require(entry.partial is None)
        else:
            _require(type(entry.partial) is PartialDetails)
            _require(entry.sample_count == sum(count for _, count in entry.partial.fragments))
            _require(entry.byte_count == entry.sample_count * 2)
    return entry


def _entry_limit(kind):
    return {"capture": MAX_CAPTURE_BYTES, "partial": MAX_SAMPLES * 2,
            "abort": MAX_HEADER_BYTES}[kind]


def _validate_payload(entry, data, identity):
    if entry.provenance.wire_session_nonce is not None:
        _require(UUID(identity.session_id).int == entry.provenance.wire_session_nonce,
                 "Wire nonce не совпадает с исходным сеансом")
    if entry.provenance.wire_version == "0.1" and entry.sequence is not None:
        _uint(entry.sequence, 0xFFFFFFFE, 1)
    _require(len(data) == entry.byte_count and hmac.compare_digest(
        hashlib.sha256(data).hexdigest(), entry.sha256), "Размер или SHA-256 сегмента не совпадает")
    if entry.kind == "capture":
        capture = decode(data)
        identity.validate_capture(capture)
        _require(capture.sequence == entry.sequence and len(capture.samples) == entry.sample_count)
        _require(capture.trigger_kind != "auto_timeout" or
                 entry.provenance.acquisition_mode in (None, 0))
        provenance = entry.provenance
        _require(provenance.terminal_kind in (None, "end"))
        if provenance.wire_session_nonce is not None:
            _require(UUID(capture.session_id).int == provenance.wire_session_nonce)
        if provenance.config_payload_hex is not None:
            _require(len(capture.samples) == 2048 and capture.nominal_fs_hz == 50000,
                     "Окно не соответствует объявленному wire 0.1 profile1")
            _, _, source, edge, _, level, pre, _, _ = struct.unpack(
                "<HBBBBHHHI", bytes.fromhex(provenance.config_payload_hex))
            _require(capture.trigger_edge == ("rising" if edge == 0 else "falling")
                     and capture.trigger_level_code == level and capture.pretrigger_samples == pre)
            _require(capture.trigger_kind == "auto_timeout" or
                     capture.trigger_kind == ("signal" if source == 0 else "external"))
        if provenance.end_samples_crc32 is not None:
            raw = struct.pack(f"<{len(capture.samples)}H", *capture.samples)
            _require(zlib.crc32(raw) & 0xFFFFFFFF == provenance.end_samples_crc32)

    elif entry.kind == "partial":
        if entry.provenance.config_payload_hex is not None:
            _require(entry.partial.expected_samples == 2048
                     and entry.partial.nominal_fs_hz in (None, 50000))
        _require(all(code <= 4095 for (code,) in struct.iter_unpack("<H", data)))
    else:
        _require(entry.provenance.begin_request_id is None and entry.provenance.begin_sequence is None
                 and entry.provenance.terminal_kind in (None, "abort"))
        obj = _json_load(data, MAX_DIAGNOSTIC_BYTES)
        _require(obj == dict(kind="no_begin_abort", reason=entry.abort_reason,
                             sequence=entry.sequence, provenance=asdict(entry.provenance)))
        # Equality alone would allow JSON true in place of integer 1.
        _exact(obj, ("kind", "reason", "sequence", "provenance"))
        _from_dataclass(SegmentProvenance, obj["provenance"])
        if obj["sequence"] is not None:
            _uint(obj["sequence"], 0xFFFFFFFF)


class _EntryState:
    """O(1) running bounds/order, without retaining Capture arrays."""
    def __init__(self):
        self.count = self.events = self.payload_bytes = self.index_bytes = 0
        self.sequence = self.elapsed = None

    def check(self, entry):
        _require(entry.ordinal == self.count)
        if entry.kind != "abort":
            _require(self.sequence is None or entry.sequence > self.sequence,
                     "Номер окна повторяется или идёт назад")
        elapsed = entry.provenance.host_elapsed_ms
        _require(elapsed is None or self.elapsed is None or elapsed >= self.elapsed,
                 "Монотонное время хоста идёт назад")

    def add(self, entry, index_length):
        self.count += 1
        self.events += entry.kind == "abort"
        self.payload_bytes += entry.byte_count
        self.index_bytes += index_length + 1
        if entry.kind != "abort":
            self.sequence = entry.sequence
        if entry.provenance.host_elapsed_ms is not None:
            self.elapsed = entry.provenance.host_elapsed_ms

    def capacity(self, spool_bytes, entry=None, index_length=0):
        count = self.count + (entry is not None)
        events = self.events + (entry is not None and entry.kind == "abort")
        payload = self.payload_bytes + (entry.byte_count if entry else 0)
        index = self.index_bytes + (index_length + 1 if entry else 0)
        values = (MAX_SEGMENTS - count, MAX_EVENTS - events,
                  MAX_MANIFEST_BYTES - MAX_HEADER_BYTES - index,
                  MAX_TOTAL_BYTES - payload - index - TERMINAL_RESERVE,
                  MAX_TOTAL_BYTES - spool_bytes - TERMINAL_RESERVE)
        if min(values) < 0:
            raise CapacityError("Достигнуто ограничение сеанса; резерв завершения сохранён")
        return Capacity(*values)


def _validate_outcome(entries, outcome):
    if outcome.reason == "single_complete":
        _require(len(entries) == 1 and entries[0].kind == "capture",
                 "Single должен содержать ровно одно полное окно")


def _serialize_item(item):
    if type(item) is FullSegment:
        item.__post_init__()
        return encode(item.capture)
    if type(item) is PartialSegment:
        item.__post_init__()
        return b"".join(struct.pack(f"<{len(f.samples)}H", *f.samples) for f in item.fragments)
    if type(item) is AbortEvent:
        item.__post_init__()
        return _json_bytes(dict(kind="no_begin_abort", reason=item.reason, sequence=item.sequence,
                                provenance=asdict(item.provenance)), MAX_HEADER_BYTES)
    raise SessionArchiveError("Неизвестный тип сегмента")


def segment_digest(item):
    """Pure bounded serialization/hash shared with the writer; never performs I/O."""
    return hashlib.sha256(_serialize_item(item)).hexdigest()


def _header(identity):
    return _json_bytes(dict(format="motortester-spool", format_version=1,
                            identity=asdict(identity)), MAX_HEADER_BYTES)


def _write_all(stream, data):
    view = memoryview(data)
    while view:
        count = stream.write(view)
        if type(count) is not int or not 1 <= count <= len(view):
            raise OSError("Не удалось записать все байты сеанса")
        view = view[count:]


def _sync(stream):
    stream.flush()
    os.fsync(stream.fileno())


def _frame(stream, kind, payload):
    _write_all(stream, FRAME.pack(kind, len(payload)))
    _write_all(stream, payload)
    if kind != b"MEM1":
        _write_all(stream, hashlib.sha256(payload).digest())


class SessionSpool:
    """Single-owner synchronous writer. Run off the GUI/transport thread.

    There is intentionally no reopen-for-append operation. After any I/O failure,
    stop using this writer and recover its verified prefix read-only.
    """
    def __init__(self):
        raise TypeError("Используйте SessionSpool.create")

    @classmethod
    def create(cls, path, identity):
        _require(type(identity) is SessionIdentity)
        identity.__post_init__()
        header = _header(identity)
        path = Path(path).absolute()
        stream = open(path, "x+b", buffering=0)
        try:
            _write_all(stream, SPOOL_MAGIC)
            _frame(stream, b"HDR1", header)
            _sync(stream)
        except BaseException:
            stream.close()  # Preserve even a failed create for diagnosis.
            raise
        self = object.__new__(cls)
        self.path, self.identity, self._stream = path, identity, stream
        self._entries = []
        self._state = _EntryState()
        self._issuer = object()
        return self

    @property
    def receipt_issuer(self):
        return self._issuer

    @property
    def capacity(self):
        self._open()
        return self._state.capacity(self._stream.tell())

    def _open(self):
        _require(self._stream is not None, "Запись spool закрыта")

    def _capture(self, data, provenance):
        self._open()
        _provenance(provenance)
        capture = decode(data)
        self.identity.validate_capture(capture)
        entry = SegmentCommit(len(self._entries), "capture", len(data),
                              hashlib.sha256(data).hexdigest(), capture.sequence,
                              len(capture.samples), provenance)
        _validate_payload(entry, data, self.identity)
        return entry, data

    def _prepare(self, entry, data):
        self._open()
        self._state.check(entry)
        _validate_payload(entry, data, self.identity)
        index = _json_bytes(_entry_dict(entry), MAX_INDEX_BYTES)
        projected = self._state.capacity(
            self._stream.tell() + 2 * FRAME.size + len(data) + len(index) + 32, entry, len(index))
        return index, projected

    def preflight_capture(self, data, provenance=SegmentProvenance()):
        """Exact admission check; does not reserve space or perform disk I/O."""
        entry, data = self._capture(data, provenance)
        return self._prepare(entry, data)[1]

    def _append(self, entry, data, *, source_item=None):
        index, _ = self._prepare(entry, data)
        start = self._stream.tell()
        try:
            _frame(self._stream, b"MEM1", data)
            _sync(self._stream)
            _frame(self._stream, b"IDX1", index)
            _sync(self._stream)
            end = self._stream.tell()
            self._stream.seek(start)
            readback = _read_frame(self._stream, b"MEM1", _entry_limit(entry.kind), digest=False)
            _validate_payload(entry, readback, self.identity)
            _require(_read_frame(self._stream, b"IDX1", MAX_INDEX_BYTES) == index,
                     "Индекс не совпадает после записи")
            _require(self._stream.tell() == end)
            self._stream.seek(end)
        except BaseException:
            self.close()
            raise
        self._entries.append(entry)
        self._state.add(entry, len(index))
        receipt = CommitReceipt(self.identity, entry, self.capacity, self._issuer, source_item)
        object.__setattr__(receipt, "_verified",
                           (_header(receipt.identity), _json_bytes(_entry_dict(entry), MAX_INDEX_BYTES),
                            tuple(asdict(receipt.capacity_after).values()), id(self._issuer), id(source_item)))
        return receipt

    def append_capture(self, data, provenance=SegmentProvenance()):
        """Preserve the supplied validated v1 bytes, not a re-encoding of them."""
        return self._append(*self._capture(data, provenance))

    def append_full(self, full):
        _require(type(full) is FullSegment)
        full.__post_init__()
        entry, data = self._capture(_serialize_item(full), full.provenance)
        return self._append(entry, data, source_item=full)

    def preflight_full(self, full):
        _require(type(full) is FullSegment)
        full.__post_init__()
        return self.preflight_capture(_serialize_item(full), full.provenance)

    def _partial(self, partial):
        self._open()
        _require(type(partial) is PartialSegment)
        partial.__post_init__()
        data = _serialize_item(partial)
        entry = SegmentCommit(len(self._entries), "partial", len(data),
                              hashlib.sha256(data).hexdigest(), partial.sequence,
                              len(data) // 2, partial.provenance, partial.details())
        return entry, data

    def append_partial(self, partial):
        return self._append(*self._partial(partial), source_item=partial)

    def preflight_partial(self, partial):
        return self._prepare(*self._partial(partial))[1]

    def _abort(self, event):
        self._open()
        _require(type(event) is AbortEvent)
        event.__post_init__()
        data = _serialize_item(event)
        entry = SegmentCommit(len(self._entries), "abort", len(data),
                              hashlib.sha256(data).hexdigest(), event.sequence, None,
                              event.provenance, abort_reason=event.reason)
        return entry, data

    def append_abort(self, event):
        return self._append(*self._abort(event), source_item=event)

    def preflight_abort(self, event):
        return self._prepare(*self._abort(event))[1]

    def finish(self, reason, stop_evidence="none", diagnostic=""):
        self._open()
        outcome = SessionOutcome(reason, stop_evidence, diagnostic)
        _validate_outcome(self._entries, outcome)
        data = _json_bytes(asdict(outcome), MAX_DIAGNOSTIC_BYTES)
        try:
            _frame(self._stream, b"END1", data)
            _sync(self._stream)
        finally:
            self.close()
        return recover(self.path)

    def close(self):
        if self._stream is not None:
            stream, self._stream = self._stream, None
            stream.close()

    def finalize(self, destination, *, overwrite=False, cancelled=None):
        _require(self._stream is None, "Сначала завершите или закройте spool")
        return finalize(recover(self.path), destination, overwrite=overwrite, cancelled=cancelled)


def _read_frame(stream, kind, maximum, *, digest=True):
    header = stream.read(FRAME.size)
    _require(len(header) == FRAME.size, "Обрезанный заголовок spool")
    actual, length = FRAME.unpack(header)
    _require(actual == kind and length <= maximum, "Неверный тип или размер записи spool")
    payload = stream.read(length)
    _require(len(payload) == length, "Обрезанная запись spool")
    if digest:
        expected = stream.read(32)
        _require(len(expected) == 32 and hmac.compare_digest(hashlib.sha256(payload).digest(), expected),
                 "Повреждена запись spool")
    return payload


def recover(path):
    """Verify only the committed prefix. Never change, truncate or resume a spool."""
    path = Path(path).absolute()
    with open(path, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        _require(size <= MAX_TOTAL_BYTES, "Spool превышает ограничение")
        _require(stream.read(len(SPOOL_MAGIC)) == SPOOL_MAGIC, "Неизвестная версия spool")
        header = _json_load(_read_frame(stream, b"HDR1", MAX_HEADER_BYTES), MAX_HEADER_BYTES)
        _exact(header, ("format", "format_version", "identity"))
        _require(header["format"] == "motortester-spool" and type(header["format_version"]) is int
                 and header["format_version"] == 1)
        identity = _from_dataclass(SessionIdentity, header["identity"])
        entries, offsets, issues = [], [], []
        outcome = SessionOutcome("recovered", "unknown")
        state = _EntryState()
        finished = False
        while stream.tell() < size:
            start = stream.tell()
            tag = stream.read(4)
            stream.seek(start)
            try:
                if tag == b"END1":
                    outcome = _from_dataclass(SessionOutcome, _json_load(
                        _read_frame(stream, b"END1", MAX_DIAGNOSTIC_BYTES), MAX_DIAGNOSTIC_BYTES))
                    _require(stream.tell() == size, "Лишний хвост после завершения")
                    _validate_outcome(entries, outcome)
                    finished = True
                    break
                _require(len(entries) < MAX_SEGMENTS)
                data = _read_frame(stream, b"MEM1", MAX_DIAGNOSTIC_BYTES, digest=False)
                payload_offset = start + FRAME.size
                index = _read_frame(stream, b"IDX1", MAX_INDEX_BYTES)
                entry = _entry_from_dict(_json_load(index, MAX_INDEX_BYTES))
                state.check(entry)
                _validate_payload(entry, data, identity)
                state.capacity(stream.tell(), entry, len(index))
            except (CaptureError, struct.error):
                # No raw tail becomes a sample, even when a complete orphan member is readable.
                kind = "uncommitted_tail" if tag == b"MEM1" else "invalid_terminal_or_tail"
                issues.append(RecoveryIssue(kind, start, size - start))
                outcome = SessionOutcome("recovered", "unknown")
                break
            entries.append(entry)
            offsets.append(payload_offset)
            state.add(entry, len(index))
        if not finished and not issues:
            issues.append(RecoveryIssue("missing_finish", size, 0))
        return RecoveryReport(path, identity, tuple(entries), outcome, tuple(issues),
                              tuple(offsets), size)


def _diagnostics(outcome, issues):
    return _json_bytes(dict(format_version=1, message=outcome.diagnostic,
                            recovery_issues=[asdict(issue) for issue in issues]), MAX_DIAGNOSTIC_BYTES)


def _manifest(identity, entries, outcome, diagnostics):
    return _json_bytes(dict(_CONSTANTS, identity=asdict(identity),
                            termination=dict(reason=outcome.reason, stop_evidence=outcome.stop_evidence),
                            segment_count=len(entries), segments=[_entry_dict(e) for e in entries],
                            diagnostics=dict(byte_count=len(diagnostics),
                                             sha256=hashlib.sha256(diagnostics).hexdigest())),
                       MAX_MANIFEST_BYTES)


def _zip_info(name):
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def _cancel(cancelled):
    if cancelled is not None and cancelled():
        raise SaveCancelled("Сохранение отменено; spool сохранён")


def finalize(report, destination, *, overwrite=False, cancelled=None):
    """Validate, stage, verify and atomically publish; always retain the spool.

    Cancellation is honored up to publication. No fallible durability claim is
    made after replacement: successful file fsync is not universal power-loss proof.
    """
    _require(type(report) is RecoveryReport)
    _require(type(overwrite) is bool)
    _require(cancelled is None or callable(cancelled))
    _cancel(cancelled)
    current = recover(report.path)
    _require(current == report, "Spool изменился после восстановления")
    destination = Path(destination).absolute()
    _require(destination != report.path, "Нельзя заменить spool архивом")
    if destination.exists():
        _require(not os.path.samefile(destination, report.path), "Назначение является spool")
    diagnostics = _diagnostics(report.outcome, report.issues)
    manifest = _manifest(report.identity, report.entries, report.outcome, diagnostics)
    _require(sum(e.byte_count for e in report.entries) + len(diagnostics) + len(manifest)
             <= MAX_TOTAL_BYTES)
    fd, temporary = tempfile.mkstemp(prefix=".motortester-session-", dir=destination.parent)
    temporary = Path(temporary)
    published = False
    try:
        with os.fdopen(fd, "w+b") as output, open(report.path, "rb") as source:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
                archive.writestr(_zip_info("manifest.json"), manifest)
                for entry, offset in zip(report.entries, report.offsets):
                    _cancel(cancelled)
                    source.seek(offset)
                    data = source.read(entry.byte_count)
                    _validate_payload(entry, data, report.identity)
                    archive.writestr(_zip_info(entry.member), data)
                archive.writestr(_zip_info("diagnostics.json"), diagnostics)
            _sync(output)
        _cancel(cancelled)
        verified = load(temporary)
        _require((verified.identity, verified.entries, verified.outcome, verified.issues) ==
                 (report.identity, report.entries, report.outcome, report.issues))
        _cancel(cancelled)
        if overwrite:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)
        published = True
        return SessionArchive(destination, verified.identity, verified.entries,
                              verified.outcome, verified.issues)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            if not published:
                raise
            # Publication succeeded; failed temporary unlink must not claim an old file survived.


def _preflight_directory(stream, offset, size, declared_count):
    """Scan actual records and bounded names before ZipFile creates ZipInfo objects."""
    stream.seek(offset)
    end = offset + size
    count = total = 0
    names = set()
    while stream.tell() < end:
        _require(count < declared_count and count < MAX_ENTRIES,
                 "Число записей каталога ZIP превышает ограничение")
        _require(end - stream.tell() >= CENTRAL.size)
        raw = stream.read(CENTRAL.size)
        _require(len(raw) == CENTRAL.size)
        (signature, made_by, needed, flags, compression, _, _, _, compressed,
         uncompressed, name_length, extra_length, comment_length, disk, internal,
         external, _) = CENTRAL.unpack(raw)
        _require(signature == b"PK\x01\x02" and made_by == 0x0314 and needed == 20)
        _require(flags == compression == extra_length == comment_length == disk == internal == 0)
        _require(external == (stat.S_IFREG | 0o600) << 16)
        _require(1 <= name_length <= MAX_NAME_BYTES and name_length <= end - stream.tell())
        name = stream.read(name_length)
        _require(len(name) == name_length and name not in names)
        _require(name in (b"manifest.json", b"diagnostics.json") or
                 re.fullmatch(rb"segments/[0-9]{8}\.(mtcap|u16le|json)", name) is not None)
        names.add(name)  # At most MAX_ENTRIES small bytes objects.
        maximum = (MAX_MANIFEST_BYTES if name == b"manifest.json" else
                   MAX_DIAGNOSTIC_BYTES if name == b"diagnostics.json" else
                   MAX_CAPTURE_BYTES if name.endswith(b".mtcap") else
                   MAX_SAMPLES * 2 if name.endswith(b".u16le") else MAX_HEADER_BYTES)
        _require(compressed == uncompressed and uncompressed <= maximum)
        total += uncompressed
        _require(total <= MAX_TOTAL_BYTES)
        count += 1
    _require(stream.tell() == end and count == declared_count,
             "Объявленное число записей ZIP не совпадает с каталогом")


@contextmanager
def _checked_zip(path):
    """Bound the directory before ZipFile allocates it; disallow ZIP extensions."""
    with open(path, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        _require(EOCD.size <= size <= MAX_ARCHIVE_BYTES, "Недопустимый размер архива")
        stream.seek(size - EOCD.size)
        end = EOCD.unpack(stream.read(EOCD.size))
        signature, disk, cd_disk, on_disk, count, directory_size, directory_offset, comment = end
        _require(signature == b"PK\x05\x06" and disk == cd_disk == comment == 0)
        _require(2 <= count <= MAX_ENTRIES and on_disk == count)
        _require(directory_size <= MAX_DIRECTORY_BYTES and directory_offset + directory_size
                 == size - EOCD.size, "Недопустимый центральный каталог ZIP")
        _preflight_directory(stream, directory_offset, directory_size, count)
        stream.seek(0)
        try:
            with zipfile.ZipFile(stream, "r", allowZip64=False) as archive:
                infos = archive.infolist()
                _require(len(infos) == count and archive.start_dir == directory_offset)
                names = set()
                next_offset = total = 0
                for info in infos:
                    name = info.filename
                    _require(name == info.orig_filename and name not in names)
                    names.add(name)
                    _require(name in ("manifest.json", "diagnostics.json") or re.fullmatch(
                        r"segments/[0-9]{8}\.(mtcap|u16le|json)", name) is not None)
                    encoded = name.encode("ascii")
                    _require(len(encoded) <= MAX_NAME_BYTES)
                    _require(info.compress_type == zipfile.ZIP_STORED and info.flag_bits == 0)
                    _require(not info.extra and not info.comment and info.create_system == 3)
                    _require(info.external_attr == (stat.S_IFREG | 0o600) << 16)
                    _require(info.extract_version == 20 and info.create_version == 20 and info.reserved == 0 and info.volume == 0)
                    _require(info.file_size == info.compress_size and info.header_offset == next_offset)
                    maximum = MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_DIAGNOSTIC_BYTES
                    _require(0 <= info.file_size <= maximum)
                    total += info.file_size
                    _require(total <= MAX_TOTAL_BYTES)
                    stream.seek(info.header_offset)
                    raw = stream.read(LOCAL.size)
                    _require(len(raw) == LOCAL.size)
                    local = LOCAL.unpack(raw)
                    _require(local[0] == b"PK\x03\x04" and local[1] == 20 and local[2] == 0
                             and local[3] == 0 and local[6] == info.CRC
                             and local[7] == local[8] == info.file_size
                             and local[9] == len(encoded) and local[10] == 0)
                    _require(stream.read(len(encoded)) == encoded)
                    next_offset += LOCAL.size + len(encoded) + info.file_size
                    _require(next_offset <= directory_offset)
                _require(next_offset == directory_offset)
                yield archive
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError, struct.error) as exc:
            raise SessionArchiveError("Повреждённый или неподдерживаемый ZIP") from exc


def _zip_read(archive, info, limit):
    _require(info.file_size <= limit)
    with archive.open(info) as member:
        data = member.read(limit + 1)
    _require(len(data) == info.file_size and len(data) <= limit)
    return data


def load(path):
    """Validate all members, retaining only the bounded immutable index in memory."""
    path = Path(path).absolute()
    with _checked_zip(path) as archive:
        infos = archive.infolist()
        _require(infos[0].filename == "manifest.json" and infos[-1].filename == "diagnostics.json")
        obj = _json_load(_zip_read(archive, infos[0], MAX_MANIFEST_BYTES), MAX_MANIFEST_BYTES)
        _exact(obj, (*_CONSTANTS, "identity", "termination", "segment_count", "segments", "diagnostics"))
        for key, value in _CONSTANTS.items():
            _require(type(obj[key]) is type(value) and obj[key] == value)
        identity = _from_dataclass(SessionIdentity, obj["identity"])
        _uint(obj["segment_count"], MAX_SEGMENTS)
        _require(type(obj["segments"]) is list and len(obj["segments"]) == obj["segment_count"])
        _require(len(infos) == obj["segment_count"] + 2)
        entries = []
        state = _EntryState()
        for raw, info in zip(obj["segments"], infos[1:-1]):
            entry = _entry_from_dict(raw)
            state.check(entry)
            _require(info.filename == entry.member)
            _validate_payload(entry, _zip_read(archive, info, _entry_limit(entry.kind)), identity)
            entries.append(entry)
            state.add(entry, 0)
        _require(sum(e.kind == "abort" for e in entries) <= MAX_EVENTS)
        data = _zip_read(archive, infos[-1], MAX_DIAGNOSTIC_BYTES)
        _exact(obj["diagnostics"], ("byte_count", "sha256"))
        _uint(obj["diagnostics"]["byte_count"], MAX_DIAGNOSTIC_BYTES)
        _require(type(obj["diagnostics"]["sha256"]) is str
                 and _HEX.fullmatch(obj["diagnostics"]["sha256"]) is not None
                 and obj["diagnostics"]["byte_count"] == len(data)
                 and hmac.compare_digest(obj["diagnostics"]["sha256"], hashlib.sha256(data).hexdigest()))
        diagnostic = _json_load(data, MAX_DIAGNOSTIC_BYTES)
        _exact(diagnostic, ("format_version", "message", "recovery_issues"))
        _require(type(diagnostic["format_version"]) is int and diagnostic["format_version"] == 1)
        _require(type(diagnostic["recovery_issues"]) is list and len(diagnostic["recovery_issues"]) <= 1)
        issues = []
        for raw in diagnostic["recovery_issues"]:
            issue = _from_dataclass(RecoveryIssue, raw)
            _require(type(issue.kind) is str and issue.kind in
                     ("missing_finish", "uncommitted_tail", "invalid_terminal_or_tail"))
            _uint(issue.offset, MAX_TOTAL_BYTES)
            _uint(issue.byte_count, MAX_TOTAL_BYTES - issue.offset)
            _require(issue.kind != "missing_finish" or issue.byte_count == 0)
            issues.append(issue)
        _exact(obj["termination"], ("reason", "stop_evidence"))
        outcome = SessionOutcome(**obj["termination"], diagnostic=diagnostic["message"])
        _require(not issues or outcome.reason == "recovered")
        _validate_outcome(entries, outcome)
        return SessionArchive(path, identity, tuple(entries), outcome, tuple(issues))

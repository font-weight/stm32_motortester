"""Bounded append-only host journal with conservative checkpoint recovery.

No transport, background thread, implicit resume, or legacy-format conversion.
SHA-256 detects accidental corruption, not forged provenance. See stream-recording.md.
"""
from bisect import bisect_right
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import struct
from uuid import uuid4

from .stream_model import (MAX_BLOCK_SAMPLES, MAX_READ_SAMPLES, StreamBlock,
                           StreamError, StreamIdentity, StreamStart,
                           StreamTerminal, FailureEvidence, StreamPreparation,
                           PreparedReadyToken, UnboundOutcome, require, uint)

MAX_RAW_BYTES = 64 * 1024 * 1024
MAX_BLOCKS = 65536
MAX_METADATA_BYTES = 4096
CHECKPOINT_BYTES = 1024 * 1024
CHECKPOINT_BLOCKS = 128
MAGIC = b"MTSTRM\x01\x00"
ATTEMPT_MAGIC = b"MTSTRM\x02\x00"
_HEADER = struct.Struct("<8sI")
_FRAME = struct.Struct("<cI")
_BLOCK = struct.Struct("<QI")
_CHECKPOINT = struct.Struct("<QQ")
_MAX_FRAMES = 2 * MAX_BLOCKS + 1
TERMINAL_RESERVE = _FRAME.size + MAX_METADATA_BYTES + 32
# At most one checkpoint per raw frame. Includes worst-case header and terminal.
MAX_FILE_BYTES = (_HEADER.size + MAX_METADATA_BYTES + 32 + MAX_RAW_BYTES +
                  MAX_BLOCKS * (2 * _FRAME.size + _BLOCK.size + _CHECKPOINT.size + 64) +
                  TERMINAL_RESERVE)
# Version 2 direct-start attempts may contain one extra bounded S frame.
MAX_ATTEMPT_FILE_BYTES = MAX_FILE_BYTES + TERMINAL_RESERVE


@dataclass(frozen=True, slots=True)
class StreamLimits:
    max_raw_bytes: int = MAX_RAW_BYTES
    max_blocks: int = MAX_BLOCKS

    def __post_init__(self):
        uint(self.max_raw_bytes, MAX_RAW_BYTES)
        require(self.max_raw_bytes % 2 == 0, "Raw quota должна быть чётной")
        uint(self.max_blocks, MAX_BLOCKS)


class StreamCapacityError(StreamError):
    """A foundation limit was reached; no following sample was admitted."""


def _json(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    require(len(data) <= MAX_METADATA_BYTES, "Метаданные превышают предел")
    return data


def _object(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, "Повторное поле JSON")
        obj[key] = value
    return obj


def _decode(data):
    require(1 <= len(data) <= MAX_METADATA_BYTES, "Размер метаданных вне предела")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(StreamError("Нечисловой JSON")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise StreamError("Некорректные метаданные JSON") from exc
    require(type(value) is dict, "Ожидается объект метаданных")
    return value


def _construct(cls, obj):
    require(type(obj) is dict and set(obj) == set(cls.__dataclass_fields__),
            "Неизвестные или отсутствующие поля")
    values = dict(obj)
    if cls in (StreamStart, StreamTerminal, StreamPreparation):
        values["identity"] = _construct(StreamIdentity, values["identity"])
    if cls in (StreamTerminal, UnboundOutcome) and values["failure"] is not None:
        values["failure"] = _construct(FailureEvidence, values["failure"])
    return cls(**values)


def _write_all(stream, data):
    remaining = memoryview(data)
    while remaining:
        size = stream.write(remaining)
        if type(size) is not int or not 1 <= size <= len(remaining):
            raise OSError("Запись не продвигается")
        remaining = remaining[size:]


def _sync(stream):
    stream.flush()
    os.fsync(stream.fileno())


def _regular(stream):
    info = os.fstat(stream.fileno())
    require(stat.S_ISREG(info.st_mode), "Ожидается обычный файл")
    require(info.st_size <= getattr(stream, "_stream_size_limit", MAX_FILE_BYTES),
            "Размер файла превышает предел")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _ancestors(path):
    result = []
    for parent in reversed(path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and not _reparse(info),
                "Недопустимый каталог в пути")
        result.append((info.st_dev, info.st_ino))
    return tuple(result)


def _reparse(info):
    return bool(getattr(info, "st_file_attributes", 0) &
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _path_fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _observe(stream, path):
    handle = _regular(stream)
    info = path.lstat()
    leaf = _path_fingerprint(info)
    require(stat.S_ISREG(info.st_mode) and not _reparse(info) and
            leaf[:4] == handle[:4], "Путь и открытый файл различаются")
    # Path ctime and handle ctime deliberately stay in separate observations.
    return handle, leaf, _ancestors(path)


def _unchanged(stream, path, observation):
    require(_observe(stream, path) == observation,
            "Файл или его путь изменился во время операции")


def _open_file(path, *, create=False, maximum=MAX_FILE_BYTES):
    # Cooperative-directory checks, not exclusion of arbitrary hostile OS races.
    path = Path(path).absolute()
    ancestors = _ancestors(path)
    prior_leaf = None
    if not create:
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and not _reparse(info),
                "Ожидается обычный файл без перенаправления")
        prior_leaf = _path_fingerprint(info)
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY)
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        stream = os.fdopen(fd, "wb" if create else "rb", buffering=0)
    except BaseException:
        os.close(fd)
        raise
    try:
        stream._stream_size_limit = maximum
        observation = _observe(stream, path)
        require(observation[2] == ancestors and
                (prior_leaf is None or observation[1] == prior_leaf),
                "Путь изменился при открытии")
        # FileIO owns this private snapshot; wrappers forward it with fileno/read.
        stream._stream_observation = observation
    except BaseException:
        stream.close()
        raise
    return stream


def _open_read(path):
    return _open_file(path)


def _open_attempt_read(path):
    return _open_file(path, maximum=MAX_ATTEMPT_FILE_BYTES)


class StreamWriter:
    """Single-owner synchronous writer. append is acceptance, not a durable receipt."""

    @classmethod
    def create(cls, path, start, limits=StreamLimits()):
        require(type(start) is StreamStart, "Ожидается StreamStart")
        start.__post_init__()
        require(type(limits) is StreamLimits, "Ожидается StreamLimits")
        limits.__post_init__()
        metadata = _json(dict(start=asdict(start), limits=asdict(limits)))
        return cls._create(path, start, limits, MAGIC, metadata, MAX_FILE_BYTES)

    @classmethod
    def _create(cls, path, start, limits, magic, metadata, maximum):
        prefix = _HEADER.pack(magic, len(metadata)) + metadata
        writer = cls()
        writer.path = Path(path).absolute()
        writer.start = start
        writer.limits = limits
        writer.accepted_end_sample_index = None if start is None else start.first_sample_index
        writer.checkpointed_end_sample_index = writer.accepted_end_sample_index
        writer._blocks = writer._raw_bytes = 0
        writer._pending_blocks = writer._pending_bytes = 0
        writer._digest = hashlib.sha256(prefix).digest()
        writer._file_bytes = 0
        writer._file_limit = maximum
        writer._buffer = bytearray()
        writer.first_failure = None
        writer._stream = _open_file(writer.path, create=True, maximum=maximum)
        writer._observation = writer._stream._stream_observation
        try:
            writer._check_file()
            _write_all(writer._stream, prefix + writer._digest)
            writer._file_bytes = len(prefix) + 32
            writer._after_write()
            writer._sync_file()
        except BaseException:
            try:
                writer.close()
            except OSError:
                pass
            raise
        return writer

    @property
    def closed(self):
        return self._stream.closed

    @property
    def raw_bytes_remaining(self):
        return self.limits.max_raw_bytes - self._raw_bytes

    @property
    def archive_frames_remaining(self):
        return self.limits.max_blocks - self._blocks - bool(self._buffer)

    def _active(self):
        require(not self.closed, "Writer уже закрыт; продолжение запрещено")

    def _check_file(self):
        _unchanged(self._stream, self.path, self._observation)
        require(self._observation[0][2] == self._file_bytes and
                self._stream.tell() == self._file_bytes,
                "Размер или позиция writer изменились")

    def _after_write(self):
        current = _observe(self._stream, self.path)
        require(current[0][:2] == self._observation[0][:2] and
                current[1][:2] == self._observation[1][:2] and
                current[2] == self._observation[2] and
                current[0][2] == self._file_bytes and self._stream.tell() == self._file_bytes,
                "Файл изменился при записи")
        # Only our completed write may advance size/mtime/ctime snapshots.
        self._observation = current

    def _sync_file(self):
        self._check_file()
        _sync(self._stream)
        self._check_file()

    def _frame(self, kind, payload):
        self._check_file()
        header = _FRAME.pack(kind, len(payload))
        digest = hashlib.sha256(self._digest + header + payload).digest()
        reserve = 0 if kind in (b"T", b"U") else TERMINAL_RESERVE
        require(self._file_bytes + len(header) + len(payload) + 32 + reserve <= self._file_limit,
                "Физический предел файла; резерв завершения")
        _write_all(self._stream, header + payload + digest)
        self._file_bytes += len(header) + len(payload) + 32
        self._after_write()
        self._digest = digest

    def _flush_buffer(self):
        if not self._buffer:
            return
        first = self.accepted_end_sample_index - len(self._buffer) // 2
        self._frame(b"B", _BLOCK.pack(first, len(self._buffer) // 2) + self._buffer)
        self._blocks += 1
        self._pending_blocks += 1
        self._buffer.clear()

    def _io_failure(self):
        if self.first_failure is None:
            self.first_failure = FailureEvidence("host_io", "storage operation failed")
        try:
            self.close()
        except OSError:
            pass  # Preserve the original I/O error; no automatic retry.

    def append(self, block):
        self._active()
        code = "invalid_input"
        try:
            require(type(block) is StreamBlock, "Ожидается StreamBlock")
            block.__post_init__()
            code = "input_identity"
            require(block.identity == self.start.identity, "Изменилась identity потока")
            code = "input_order"
            require(block.first_sample_index == self.accepted_end_sample_index,
                    "Пропуск, повтор или перестановка отсчётов")
            code = "capacity_raw"
            if self._raw_bytes + len(block.raw_u16le) > self.limits.max_raw_bytes:
                raise StreamCapacityError("Достигнут предел raw записи")
            # Reserve every new archive frame needed, including a partial tail.
            projected = (len(self._buffer) + len(block.raw_u16le) + 2 * MAX_BLOCK_SAMPLES - 1) // (2 * MAX_BLOCK_SAMPLES)
            code = "capacity_blocks"
            if self._blocks + projected > self.limits.max_blocks:
                raise StreamCapacityError("Достигнут предел индекса записи")
        except StreamError as exc:
            try:
                self._reject("capacity" if isinstance(exc, StreamCapacityError) else "host_failure", code)
            except (OSError, StreamError) as persistence_error:
                raise exc from persistence_error
            raise
        try:
            self._check_file()
            # Pending accounting includes both on-disk frames and RAM tail.
            if self._pending_bytes + len(block.raw_u16le) > CHECKPOINT_BYTES:
                self.checkpoint()
                # An explicit short frame can consume one extra entry.
                if self._blocks + (len(block.raw_u16le) + 2 * MAX_BLOCK_SAMPLES - 1) // (2 * MAX_BLOCK_SAMPLES) > self.limits.max_blocks:
                    self._reject("capacity", "capacity_blocks")
                    raise StreamCapacityError("Достигнут предел индекса записи")
            position = 0
            while position < len(block.raw_u16le):
                count = min(2 * MAX_BLOCK_SAMPLES - len(self._buffer), len(block.raw_u16le) - position)
                self._buffer.extend(block.raw_u16le[position:position + count])
                self.accepted_end_sample_index += count // 2
                position += count
                if len(self._buffer) == 2 * MAX_BLOCK_SAMPLES:
                    self._flush_buffer()
            self._raw_bytes += len(block.raw_u16le)
            self._pending_bytes += len(block.raw_u16le)
            if (self._pending_blocks >= CHECKPOINT_BLOCKS or
                    self._pending_bytes >= CHECKPOINT_BYTES):
                self.checkpoint()
        except BaseException:
            self._io_failure()
            raise

    def checkpoint(self):
        self._active()
        try:
            self._check_file()
            if not self._pending_bytes:
                return
            self._flush_buffer()
            self._frame(b"C", _CHECKPOINT.pack(self.accepted_end_sample_index, self._blocks))
            self._sync_file()
            self.checkpointed_end_sample_index = self.accepted_end_sample_index
            self._pending_blocks = self._pending_bytes = 0
        except BaseException:
            self._io_failure()
            raise

    def _reject(self, reason, code):
        # The first rejection closes the interval. No later stop can overwrite it.
        if self.first_failure is None:
            self.first_failure = FailureEvidence(code, "input admission rejected")
        self.finish(StreamTerminal(self.start.identity, self.accepted_end_sample_index,
                                   reason, False, boundary_evidence="host_rejected_input",
                                   failure=self.first_failure))

    def finish(self, terminal):
        self._active()
        try:
            require(type(terminal) is StreamTerminal, "Ожидается StreamTerminal")
            terminal.__post_init__()
            require(terminal.identity == self.start.identity and
                    terminal.end_sample_index == self.accepted_end_sample_index,
                    "Завершение не соответствует принятому префиксу")
            payload = _json(asdict(terminal))
        except StreamError as exc:
            try:
                self._reject("host_failure", "invalid_terminal")
            except (OSError, StreamError) as persistence_error:
                raise exc from persistence_error
            raise
        if self.first_failure is None:
            self.first_failure = terminal.failure
        try:
            self._flush_buffer()
            self._frame(b"T", payload)
            self._sync_file()
            self.checkpointed_end_sample_index = self.accepted_end_sample_index
        except BaseException:
            self._io_failure()
            raise
        finally:
            self.close()

    def close(self):
        """Close only; does not checkpoint a tail, report Stop, or authorize resume."""
        self._stream.close()


@dataclass(frozen=True, slots=True)
class _Entry:
    first: int
    count: int
    offset: int
    previous_digest: bytes
    digest: bytes


@dataclass(frozen=True, slots=True)
class StreamRecording:
    path: Path
    start: StreamStart
    limits: StreamLimits
    end_sample_index: int
    terminal: StreamTerminal | None
    recovery_issue: str | None
    discarded_tail_bytes: int
    _entries: tuple[_Entry, ...]
    _starts: tuple[int, ...]
    _fingerprint: tuple
    _path_fingerprint: tuple
    _ancestor_fingerprint: tuple
    _attempt_format: bool = False

    @property
    def identity(self):
        return self.start.identity

    @property
    def first_sample_index(self):
        return self.start.first_sample_index

    @property
    def clean_stop_reported(self):
        return (self.recovery_issue is None and self.terminal is not None and
                self.terminal.clean_stop_reported)

    def read_raw(self, start_sample_index, count):
        uint(start_sample_index)
        uint(count, MAX_READ_SAMPLES)
        require(self.first_sample_index <= start_sample_index <= self.end_sample_index and
                start_sample_index + count <= self.end_sample_index,
                "Запрошены недоступные отсчёты")
        result = bytearray()
        stop = start_sample_index + count
        opener = _open_attempt_read if self._attempt_format else _open_read
        with opener(self.path) as stream:
            observation = (self._fingerprint, self._path_fingerprint, self._ancestor_fingerprint)
            _unchanged(stream, self.path, observation)
            ordinal = max(0, bisect_right(self._starts, start_sample_index) - 1)
            while start_sample_index < stop:
                entry = self._entries[ordinal]
                stream.seek(entry.offset)
                header = stream.read(_FRAME.size)
                payload = stream.read(_BLOCK.size + entry.count * 2)
                digest = stream.read(32)
                require(header == _FRAME.pack(b"B", len(payload)) and
                        len(payload) == _BLOCK.size + entry.count * 2 and
                        payload[:_BLOCK.size] == _BLOCK.pack(entry.first, entry.count) and
                        hmac.compare_digest(digest, entry.digest) and
                        hmac.compare_digest(hashlib.sha256(entry.previous_digest + header + payload).digest(), digest),
                        "Изменилась целостность блока")
                end = min(stop, entry.first + entry.count)
                begin_byte = _BLOCK.size + 2 * (start_sample_index - entry.first)
                result.extend(payload[begin_byte:begin_byte + 2 * (end - start_sample_index)])
                start_sample_index = end
                ordinal += 1
            _unchanged(stream, self.path, observation)
        return bytes(result)

    def read_samples(self, start_sample_index, count):
        raw = self.read_raw(start_sample_index, count)
        return struct.unpack(f"<{count}H", raw)

    def iter_raw(self, start_sample_index=None, end_sample_index=None, chunk_samples=MAX_BLOCK_SAMPLES):
        first = self.first_sample_index if start_sample_index is None else start_sample_index
        end = self.end_sample_index if end_sample_index is None else end_sample_index
        uint(first)
        uint(end)
        uint(chunk_samples, MAX_BLOCK_SAMPLES, minimum=1)
        require(self.first_sample_index <= first <= end <= self.end_sample_index,
                "Запрошены недоступные отсчёты")
        while first < end:
            count = min(chunk_samples, end - first)
            yield StreamBlock(self.identity, first, self.read_raw(first, count))
            first += count


class PreparedStreamWriter:
    """One descriptor, one readiness token, serialized selection owned by caller."""
    @classmethod
    def prepare(cls, path, preparation, limits=StreamLimits()):
        require(type(preparation) is StreamPreparation, "Ожидается StreamPreparation")
        preparation.__post_init__()
        require(type(limits) is StreamLimits, "Ожидается StreamLimits")
        limits.__post_init__()
        metadata = _json(dict(preparation=asdict(preparation), limits=asdict(limits)))
        prepared = cls()
        prepared._preparation = preparation
        prepared._writer = StreamWriter._create(path, None, limits, ATTEMPT_MAGIC, metadata,
                                                MAX_ATTEMPT_FILE_BYTES)
        try:
            prepared._token = PreparedReadyToken(uuid4().hex, preparation)
            prepared._state = "prepared"
        except BaseException:
            prepared._writer.close()
            raise
        return prepared

    @property
    def preparation(self):
        return self._preparation

    @property
    def readiness_token(self):
        return self._token

    @property
    def closed(self):
        return self._state != "prepared"

    @property
    def first_failure(self):
        return self._writer.first_failure

    def _available(self, token):
        require(self._state == "prepared" and token is self.readiness_token,
                "Чужой, использованный или закрытый readiness token")

    def _reject(self, code, error):
        failure = FailureEvidence(code, "selection admission rejected")
        try:
            self.finish_unbound(self.readiness_token, UnboundOutcome("failed_unbound", failure))
        except (OSError, StreamError) as persistence_error:
            raise error from persistence_error
        raise error

    def _bind(self, start):
        # The caller already linearized its DATA/END or START_ACK outcome.
        self._state = "selected"
        writer = self._writer
        writer.start = start
        writer.accepted_end_sample_index = start.first_sample_index
        writer.checkpointed_end_sample_index = start.first_sample_index
        return writer

    def bind_first_block(self, token, block):
        self._available(token)
        code = "invalid_input"
        try:
            require(self.preparation.start_policy == "next_block", "Неверный способ выбора начала")
            require(type(block) is StreamBlock, "Ожидается StreamBlock")
            block.__post_init__()
            code = "input_identity"
            require(block.identity == self.preparation.identity, "Изменилась identity потока")
            code = "capacity_raw"
            if len(block.raw_u16le) > self._writer.limits.max_raw_bytes:
                raise StreamCapacityError("Недостаточная raw quota для первого блока")
            code = "capacity_blocks"
            if self._writer.limits.max_blocks == 0:
                raise StreamCapacityError("Недостаточная quota индекса для первого блока")
        except StreamError as exc:
            self._reject(code, exc)
        writer = self._bind(StreamStart(self.preparation.identity, block.first_sample_index,
                                       self.preparation.recording_id, "caller_next_whole_validated_block"))
        try:
            writer.append(block)
            # One bounded first B carries the actual R; later input still coalesces.
            writer._flush_buffer()
            return writer
        except BaseException:
            writer._io_failure()
            raise

    def bind_start(self, token, start):
        self._available(token)
        try:
            require(self.preparation.start_policy == "source_start", "Неверный способ выбора начала")
            require(type(start) is StreamStart, "Ожидается StreamStart")
            start.__post_init__()
            require(start.identity == self.preparation.identity and
                    start.recording_id == self.preparation.recording_id,
                    "Начало не соответствует preparation")
            payload = _json(asdict(start))
        except StreamError as exc:
            self._reject("invalid_input", exc)
        writer = self._bind(start)
        try:
            writer._frame(b"S", payload)
            return writer
        except BaseException:
            writer._io_failure()
            raise

    def finish_unbound(self, token, outcome):
        self._available(token)
        try:
            require(type(outcome) is UnboundOutcome, "Ожидается UnboundOutcome")
            outcome.__post_init__()
            payload = _json(asdict(outcome))
        except StreamError as exc:
            self._reject("invalid_terminal", exc)
        self._state = "terminal"
        writer = self._writer
        writer.first_failure = outcome.failure
        try:
            writer._frame(b"U", payload)
            writer._sync_file()
        except BaseException:
            writer._io_failure()
            raise
        finally:
            writer.close()

    def close(self):
        # Once selected, ownership belongs only to the returned active writer.
        if self._state == "prepared":
            self._state = "closed"
            self._writer.close()


@dataclass(frozen=True, slots=True)
class AttemptReport:
    path: Path
    preparation: StreamPreparation
    limits: StreamLimits
    selected_start: StreamStart | None
    recording: StreamRecording | None
    unbound_outcome: UnboundOutcome | None
    recovery_issue: str | None
    discarded_tail_bytes: int


def recover(path):
    """Version 1 only: read-only verified prefix; never a retroactive fsync receipt."""
    return _recover(path, attempt=False)


def recover_attempt(path):
    """Version 2 only: readiness is never recreated; unset R remains explicit."""
    return _recover(path, attempt=True)


def _recover(path, *, attempt):
    path = Path(path).absolute()
    opener = _open_attempt_read if attempt else _open_read
    with opener(path) as stream:
        observation = stream._stream_observation
        _unchanged(stream, path, observation)
        fingerprint = observation[0]
        size = fingerprint[2]
        prefix = stream.read(_HEADER.size)
        require(len(prefix) == _HEADER.size, "Обрезан заголовок")
        magic, length = _HEADER.unpack(prefix)
        require(magic == (ATTEMPT_MAGIC if attempt else MAGIC) and
                1 <= length <= MAX_METADATA_BYTES, "Неверный заголовок")
        metadata, digest = stream.read(length), stream.read(32)
        require(len(metadata) == length and len(digest) == 32 and
                hmac.compare_digest(hashlib.sha256(prefix + metadata).digest(), digest),
                "Нарушена целостность заголовка")
        obj = _decode(metadata)
        root = "preparation" if attempt else "start"
        require(set(obj) == {root, "limits"}, "Неизвестные поля заголовка")
        preparation = _construct(StreamPreparation, obj[root]) if attempt else None
        start = None if attempt else _construct(StreamStart, obj[root])
        limits = _construct(StreamLimits, obj["limits"])
        source_identity = preparation.identity if attempt else start.identity
        entries = []
        next_index = committed_end = None if start is None else start.first_sample_index
        committed_count = raw_bytes = pending_bytes = pending_count = 0
        boundary_offset = stream.tell()
        terminal = unbound = None
        issue = "missing_selection_or_terminal" if attempt else "missing_terminal"
        frames = 0
        frame_limit = _MAX_FRAMES + bool(attempt and preparation.start_policy == "source_start")
        while stream.tell() < size:
            offset = stream.tell()
            try:
                frames += 1
                require(frames <= frame_limit, "Слишком много кадров")
                header = stream.read(_FRAME.size)
                require(len(header) == _FRAME.size, "Обрезан кадр")
                kind, length = _FRAME.unpack(header)
                maxima = {b"B": _BLOCK.size + 2 * MAX_BLOCK_SAMPLES,
                          b"C": _CHECKPOINT.size, b"T": MAX_METADATA_BYTES}
                if attempt:
                    maxima.update({b"S": MAX_METADATA_BYTES, b"U": MAX_METADATA_BYTES})
                maximum = maxima.get(kind)
                require(maximum is not None and 1 <= length <= maximum,
                        "Неизвестный или слишком большой кадр")
                payload, next_digest = stream.read(length), stream.read(32)
                require(len(payload) == length and len(next_digest) == 32 and
                        hmac.compare_digest(hashlib.sha256(digest + header + payload).digest(), next_digest),
                        "Повреждён кадр")
                if kind == b"B":
                    require(length >= _BLOCK.size, "Обрезан блок")
                    first, count = _BLOCK.unpack_from(payload)
                    block = StreamBlock(source_identity, first, payload[_BLOCK.size:])
                    require(count == block.sample_count, "Неверное число отсчётов")
                    require(len(entries) < limits.max_blocks and raw_bytes + count * 2 <= limits.max_raw_bytes,
                            "Превышен предел записи")
                    require(pending_count + 1 <= CHECKPOINT_BLOCKS and
                            pending_bytes + count * 2 <= CHECKPOINT_BYTES,
                            "Превышен предел неподтверждённой пачки")
                    if start is None:
                        require(attempt and preparation.start_policy == "next_block", "Нет объявленного source start")
                        start = StreamStart(source_identity, first, preparation.recording_id,
                                            "caller_next_whole_validated_block")
                        next_index = committed_end = first
                    require(first == next_index, "Пропуск, повтор или перестановка")
                    pending_count += 1
                    pending_bytes += count * 2
                    entries.append(_Entry(first, count, offset, digest, next_digest))
                    raw_bytes += count * 2
                    next_index = block.end_sample_index
                elif kind == b"S":
                    require(start is None and frames == 1 and preparation.start_policy == "source_start",
                            "Повторное или недопустимое объявление начала")
                    candidate = _construct(StreamStart, _decode(payload))
                    require(candidate.identity == source_identity and
                            candidate.recording_id == preparation.recording_id, "Чужое объявление начала")
                    start = candidate
                    next_index = committed_end = start.first_sample_index
                elif kind == b"C":
                    require(start is not None and length == _CHECKPOINT.size and pending_count > 0 and
                            _CHECKPOINT.unpack(payload) == (next_index, len(entries)),
                            "Неверная контрольная точка")
                    committed_count, committed_end = len(entries), next_index
                    boundary_offset = stream.tell()
                    pending_bytes = pending_count = 0
                elif kind == b"T":
                    candidate = _construct(StreamTerminal, _decode(payload))
                    require(start is not None and candidate.identity == source_identity and
                            candidate.end_sample_index == next_index, "Неверное завершение")
                    require(stream.tell() == size, "Данные после завершения")
                    terminal = candidate
                    committed_count, committed_end = len(entries), next_index
                    boundary_offset = stream.tell()
                    issue = None
                else:
                    require(start is None and frames == 1 and stream.tell() == size,
                            "Недопустимый unbound terminal")
                    unbound = _construct(UnboundOutcome, _decode(payload))
                    boundary_offset = stream.tell()
                    issue = None
                digest = next_digest
            except StreamError:
                issue = "invalid_tail"
                break
        _unchanged(stream, path, observation)
    if terminal is None and unbound is None and issue != "invalid_tail":
        if start is not None:
            if size > boundary_offset:
                issue = "selection_without_commit" if not entries and attempt else "uncheckpointed_tail"
            else:
                issue = "missing_terminal"
    committed = tuple(entries[:committed_count])
    result = None
    if not attempt or committed_count or (terminal is not None and preparation.start_policy == "source_start"):
        result = StreamRecording(path, start, limits, committed_end, terminal, issue,
                                 size - boundary_offset, committed, tuple(entry.first for entry in committed),
                                 fingerprint, observation[1], observation[2], attempt)
    if not attempt:
        return result
    return AttemptReport(path, preparation, limits, start, result, unbound, issue, size - boundary_offset)

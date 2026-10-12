"""HOST-007: ограниченный синтетический byte-peer, не прошивка и не USB.

Основание — непринятый draft SOURCE_SHA; отдельная миграция барьера Stop
привязана к STOP_BARRIER_POLICY_SHA. Срок удержания окна PROVISIONAL:
5000 мс от последнего принятого байта END, без второго terminal outcome.
"""
from dataclasses import dataclass
from enum import Enum
import struct
import zlib

from .wire import Frame, StreamDecoder, encode_frame

SOURCE_SHA = "c54bd955aaf3cb863ef3df043ea237ea62e74ad4"
STOP_BARRIER_POLICY_SHA = "2017576dbe104f4c43d8d9feac28436589191fd1"
MAX_CHUNK_SIZE = 4096
MAX_CONTROL_DESCRIPTORS = 8
SAMPLE_COUNT = 2048
SAMPLE_BUFFER_BYTES = 4096
MAX_ID = (1 << 32) - 2
MAX_COUNTER = (1 << 32) - 1
MAX_TIME_MS = (1 << 64) - 1
HOLD_TIMEOUT_MS = 5000  # PROVISIONAL: см. Provenance.
FIRMWARE_BUILD = 0
CAPABILITY_BITS = 9
ALL_ANALOG_CAPABILITY_BITS = 0x1D
STOPPED, PREPARING, WAITING, CAPTURING, TRANSFERRING, ERROR, WAIT_RELEASE = range(7)
_DEVICE_TYPES = frozenset((129, 130, 131, 132, 133, 134, 135, 144, 145, 146, 147, 255))


class ErrorCode(str, Enum):
    ARGUMENT = "ARGUMENT"
    RANGE = "RANGE"
    CHUNK_SIZE = "CHUNK_SIZE"
    CLOCK_ROLLBACK = "CLOCK_ROLLBACK"


class DeviceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__({
            ErrorCode.ARGUMENT: "Неверный тип аргумента эмулятора",
            ErrorCode.RANGE: "Значение вне допустимого диапазона",
            ErrorCode.CHUNK_SIZE: "Порция данных превышает 4096 байт",
            ErrorCode.CLOCK_ROLLBACK: "Монотонное время не может уменьшаться",
        }[code])


@dataclass(frozen=True, slots=True)
class Provenance:
    simulator: bool = True
    draft: bool = True
    nominal_timing: bool = True
    synthetic_waveform: bool = True
    source_sha: str = SOURCE_SHA
    timeout_provisional: bool = True
    timeout_interpretation: str = (
        "PROVISIONAL: 5000 мс от последнего принятого байта END; "
        "ERROR/TRANSFER_TIMEOUT сохраняет END и tombstone без второго терминала")
    stop_barrier_policy_sha: str = STOP_BARRIER_POLICY_SHA


PROVENANCE = Provenance()


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    count: int


@dataclass(frozen=True, slots=True)
class WriteResult:
    accepted_prefix: int
    diagnostics: tuple[Diagnostic, ...]
    provenance: Provenance = PROVENANCE


@dataclass(frozen=True, slots=True)
class Terminal:
    outcome: str
    generation: int
    window_id: int
    sample_crc32: int | None
    reason: int = 0


@dataclass(frozen=True, slots=True)
class Snapshot:
    state: int
    reason: int
    session: int
    negotiated: bool
    generation: int
    window_id: int
    config_id: int
    config: bytes | None
    next_rx_sequence: int
    next_tx_sequence: int
    last_request_id: int
    stop_barrier: bool
    held_samples: bool
    terminal: Terminal | None
    tombstone: Terminal | None
    control_descriptors: int
    rx_used: int
    tx_offset: int | None
    tx_size: int
    now_ms: int | None
    hold_started_ms: int | None
    dropped_windows: int
    data_offset: int
    continuation: bool
    diagnostics: tuple[Diagnostic, ...]
    sample_buffer_bytes: int = SAMPLE_BUFFER_BYTES
    provenance: Provenance = PROVENANCE


@dataclass(frozen=True, slots=True)
class _Descriptor:
    kind: int
    payload: bytes
    request: int
    session: int
    epoch: object
    completion: str = ""
    count: int = 0


@dataclass(slots=True)
class _Tx:
    descriptor: _Descriptor
    encoded: bytes
    offset: int = 0


def _uint(value, maximum):
    if type(value) is not int:
        raise DeviceError(ErrorCode.ARGUMENT)
    if not 0 <= value <= maximum:
        raise DeviceError(ErrorCode.RANGE)


class DeviceEmulator:
    """Один владелец; внешнего ввода-вывода, потоков и истории нет.

    read означает принятие байтов виртуальным транспортом. Не принятый
    write суффикс остаётся у вызывающего кода. Часы задаёт вызывающий код.
    """
    __slots__ = (
        "_decoder", "_samples", "_queue", "_tx", "_epoch", "_now",
        "_session", "_negotiated", "_next_rx", "_next_tx", "_last_request",
        "_last_rx", "_state", "_reason", "_config", "_config_id",
        "_generation", "_window_id", "_dropped", "_active", "_held",
        "_terminal", "_tombstone", "_stage", "_offset", "_crc",
        "_continuation", "_release_pending", "_abort_pending", "_stop_acks",
        "_hold_started", "_diagnostics", "_all_analog_modes",
        "_wait_started", "_trigger_kind")

    def __init__(self, *, all_analog_modes=False):
        if type(all_analog_modes) is not bool:
            raise DeviceError(ErrorCode.ARGUMENT)
        self._all_analog_modes = all_analog_modes
        self._decoder = StreamDecoder()
        self._samples = bytearray(SAMPLE_BUFFER_BYTES)
        self._queue = []
        self._tx = None
        self._epoch = object()
        self._now = None
        self._session = 0
        self._diagnostics = ()
        self._reset_session()

    def _reset_session(self):
        self._negotiated = False
        self._next_rx = self._next_tx = 1
        self._last_request = 0
        self._last_rx = None
        self._state, self._reason = STOPPED, 0
        self._config = None
        self._config_id = self._generation = self._window_id = self._dropped = 0
        self._active = self._held = self._continuation = False
        self._release_pending = self._abort_pending = False
        self._stop_acks = 0
        self._terminal = self._tombstone = None
        self._stage = ""
        self._offset = self._crc = 0
        self._hold_started = self._wait_started = None
        self._trigger_kind = 0

    def snapshot(self):
        return Snapshot(
            self._state, self._reason, self._session, self._negotiated,
            self._generation, self._window_id, self._config_id, self._config,
            self._next_rx, self._next_tx, self._last_request, bool(self._stop_acks),
            self._held, self._terminal, self._tombstone, len(self._queue),
            self._decoder._used, self._tx.offset if self._tx else None,
            len(self._tx.encoded) if self._tx else 0, self._now,
            self._hold_started, self._dropped, self._offset, self._continuation,
            self._diagnostics)

    def _time(self, now_ms):
        _uint(now_ms, MAX_TIME_MS)
        if self._now is not None and now_ms < self._now:
            raise DeviceError(ErrorCode.CLOCK_ROLLBACK)

    @staticmethod
    def _add(counts, code, count=1):
        counts[code] = counts.get(code, 0) + count

    def _remember(self, counts):
        self._diagnostics = tuple(Diagnostic(code, n) for code, n in counts.items())
        return self._diagnostics

    def _advance(self, now_ms):
        self._now = now_ms
        counts = {}
        for item in self._decoder.tick(now_ms).diagnostics:
            self._add(counts, item.code.value, item.count)
        # Candidate interpretation, explicitly NOT an accepted wire clarification.
        if (self._hold_started is not None and self._held
                and self._state == WAIT_RELEASE
                and now_ms - self._hold_started >= HOLD_TIMEOUT_MS):
            self._continuation = False
            self._hold_started = None
            self._set_state(ERROR, 3)
        if (self._wait_started is not None and self._state == WAITING
                and self._continuation
                and now_ms - self._wait_started >= struct.unpack_from("<I", self._config, 12)[0]):
            # Политика симулятора: срок проверяется до ввода этого вызова.
            # Одно удерживаемое окно, без накопления пропущенных периодов.
            self._capture(2)
        return counts

    def tick(self, now_ms):
        self._time(now_ms)
        return self._remember(self._advance(now_ms))

    def _descriptor(self, kind, payload, request=0, completion="", count=0):
        return _Descriptor(kind, payload, request, self._session, self._epoch,
                           completion, count)

    def _enqueue(self, kind, payload, request=0, completion=""):
        # Admission reserves three slots before consuming another RX byte.
        if len(self._queue) >= MAX_CONTROL_DESCRIPTORS:
            raise RuntimeError("Нарушен внутренний предел очереди эмулятора")
        self._queue.append(self._descriptor(kind, payload, request, completion))

    def _remove_status(self):
        self._queue[:] = [d for d in self._queue if not (d.kind == 134 and d.request == 0)]

    def _status_payload(self):
        return struct.pack("<BBHIIIII", self._state, self._reason, 0,
                           self._generation, self._window_id, 0, 0, self._dropped)

    def _set_state(self, state, reason=0):
        self._state, self._reason = state, reason
        self._remove_status()
        self._enqueue(134, self._status_payload())

    def _recover(self, reason=4):
        # A partially accepted frame remains intact; its old callback is inert.
        self._epoch = object()
        self._negotiated = False
        self._queue.clear()
        self._stage = ""
        self._active = self._held = self._continuation = False
        self._release_pending = self._abort_pending = False
        self._stop_acks = 0
        self._hold_started = self._wait_started = None
        self._state, self._reason = ERROR, reason
        if reason == 6 and self._session and self._next_tx <= MAX_ID:
            self._enqueue(134, self._status_payload())

    def disconnect(self, now_ms):
        self._time(now_ms)
        self._now = now_ms
        self._epoch = object()
        self._queue.clear()
        self._tx = None  # A physical disconnect may truncate the current frame.
        self._decoder.reset()
        self._session = 0
        self._reset_session()
        self._diagnostics = ()

    def write(self, data, now_ms):
        if type(data) is not bytes:
            raise DeviceError(ErrorCode.ARGUMENT)
        if len(data) > MAX_CHUNK_SIZE:
            raise DeviceError(ErrorCode.CHUNK_SIZE)
        self._time(now_ms)
        counts = self._advance(now_ms)
        accepted = 0
        for byte in data:
            if len(self._queue) > MAX_CONTROL_DESCRIPTORS - 3:
                self._add(counts, "BACKPRESSURE")
                break
            result = self._decoder.feed(bytes((byte,)), now_ms)
            accepted += 1
            for item in result.diagnostics:
                self._add(counts, item.code.value, item.count)
            for frame in result.frames:
                self._receive(frame, counts)
        return WriteResult(accepted, self._remember(counts))

    def _receive(self, frame, counts):
        if frame.message_type in _DEVICE_TYPES:
            self._recover()
            self._add(counts, "PROTOCOL_RECOVERY")
            return
        if frame.message_type == 1:
            if (frame.session != 0 or frame.sequence != 1 or frame.request_id != 1
                    or len(frame.payload) != 8 or not int.from_bytes(frame.payload, "little")):
                self._add(counts, "INVALID_HELLO")
                return
            nonce = int.from_bytes(frame.payload, "little")
            if nonce == self._session:
                self._add(counts, "DUPLICATE")
                return
            self._epoch = object()
            self._queue.clear()
            self._reset_session()
            self._session, self._negotiated = nonce, True
            self._next_rx, self._last_request = 2, 1
            self._last_rx = frame
            self._enqueue(129, struct.pack("<BBBBHHII", 0, 1, 1, 12, 512,
                                          SAMPLE_COUNT, FIRMWARE_BUILD,
                                          ALL_ANALOG_CAPABILITY_BITS if self._all_analog_modes
                                          else CAPABILITY_BITS), 1)
            return
        if not self._negotiated:
            self._add(counts, "NEED_HELLO")
            return
        if frame.session != self._session:
            self._add(counts, "WRONG_SESSION")
            return
        if frame == self._last_rx:
            self._add(counts, "DUPLICATE")
            return
        if (not 2 <= frame.sequence <= MAX_ID or not 2 <= frame.request_id <= MAX_ID):
            self._recover(6)
            self._add(counts, "COUNTER_SATURATED")
            return
        if frame.sequence != self._next_rx or frame.request_id <= self._last_request:
            self._recover()
            self._add(counts, "PROTOCOL_RECOVERY")
            return
        self._next_rx = frame.sequence + 1
        self._last_request = frame.request_id
        self._last_rx = frame
        # Барьер относится к ещё не допереданному STOP_ACK, не ко всему
        # TRANSFERRING. Восстановление приоритетнее ошибок тела команды.
        if self._stop_acks:
            self._recover()
            self._add(counts, "PROTOCOL_RECOVERY")
            return
        self._command(frame)

    def _error(self, frame, code):
        self._enqueue(255, struct.pack("<BBHI", frame.message_type, code, 0,
                                       self._generation), frame.request_id)

    def _command(self, frame):
        kind, payload, request = frame.message_type, frame.payload, frame.request_id
        length = {2: 0, 3: 16, 4: 4, 5: 4, 6: 0, 7: 12}.get(kind)
        if length is None:
            self._error(frame, 5)
            return
        if len(payload) != length:
            self._error(frame, 6)
            return
        if kind == 2:
            self._enqueue(130, struct.pack("<BBHIHHIHHBBBBHHH", 1, 0, 1, 72000000,
                                          71, 19, 12000000, 143, 2048,
                                          1, 7 if self._all_analog_modes else 2, 1, 3, 0, 0, 0), request)
        elif kind == 3:
            profile, mode, source, edge, reserved, level, pre, reserved2, timeout = (
                struct.unpack("<HBBBBHHHI", payload))
            if reserved or reserved2:
                self._error(frame, 6)
            elif (profile != 1 or mode not in ((0, 1, 2) if self._all_analog_modes else (1,))
                  or source != 0 or edge not in (0, 1)
                  or not 1 <= level <= 4094 or pre != 0
                  or (not 100 <= timeout <= 60000 if mode == 0 else timeout != 0)):
                self._error(frame, 1)
            elif self._state != STOPPED or self._held or self._stop_acks:
                self._error(frame, 2)
            elif self._config_id >= MAX_COUNTER:
                self._recover(6)
            else:
                self._config_id += 1
                self._config = payload
                self._enqueue(131, struct.pack("<I", self._config_id) + payload, request)
        elif kind == 4:
            config_id = struct.unpack("<I", payload)[0]
            if self._state != STOPPED or self._held or self._stop_acks:
                self._error(frame, 2)
            elif self._config is None or config_id != self._config_id:
                self._error(frame, 3)
            else:
                self._generation = request
                self._continuation = True
                self._enqueue(132, struct.pack("<II", request, config_id), request)
                self._wait()
        elif kind == 5:
            if struct.unpack("<I", payload)[0] != self._generation:
                self._error(frame, 4)
            else:
                self._stop(request)
        elif kind == 6:
            self._enqueue(134, self._status_payload(), request)
        else:
            self._release(frame)

    def trigger(self, now_ms):
        self._time(now_ms)
        self._remember(self._advance(now_ms))
        if not self._negotiated or self._state != WAITING or not self._continuation:
            return False
        return self._capture(0)

    def _wait(self):
        # Pre=0 готова немедленно; ARM_ACK не является началом ожидания.
        self._wait_started = self._now if self._config[2] == 0 else None
        self._set_state(WAITING)

    def _capture(self, trigger_kind):
        if self._window_id >= MAX_COUNTER:
            self._recover(6)
            return False
        self._wait_started = None
        self._trigger_kind = trigger_kind
        self._window_id += 1
        self._active = self._held = True
        self._offset = 0
        self._stage = "begin"
        edge = self._config[4]
        level = struct.unpack_from("<H", self._config, 6)[0]
        for i in range(SAMPLE_COUNT):
            code = (73 * i + 19 * self._window_id + 7 * self._generation) & 4095
            if i == 0 and trigger_kind == 0:
                code = level if edge == 0 else level - 1
            elif i == 511:
                code = 0
            elif i == 1023:
                code = 4095
            struct.pack_into("<H", self._samples, 2 * i, code)
        self._crc = zlib.crc32(self._samples)
        self._set_state(TRANSFERRING)
        return True

    def _stop(self, request):
        self._continuation = False
        self._hold_started = self._wait_started = None
        self._stage = ""
        self._remove_status()
        if self._active and not self._abort_pending:
            if self._dropped == MAX_COUNTER:
                self._recover(6)
                return
            self._dropped += 1
            self._abort_pending = True
            self._enqueue(147, struct.pack("<IIHH", self._generation, self._window_id,
                                          1, 0), completion="abort")
        # END already committed (or no allocated window): never invent ABORT.
        if not self._active and not (self._tx and self._tx.descriptor.completion == "end"):
            self._held = False
        self._stop_acks += 1
        self._state, self._reason = TRANSFERRING, 0
        self._enqueue(133, struct.pack("<IIB3x", self._generation, self._window_id, 0),
                      request, "stop")

    def _release(self, frame):
        generation, window, crc = struct.unpack("<III", frame.payload)
        t = self._tombstone
        if t is None or (generation, window, crc) != (t.generation, t.window_id, t.sample_crc32):
            self._error(frame, 7)
            return
        # Узкая defensive parity с A PR79 (66fce97b): committed END ещё
        # не разрешает RELEASE. Источники и границы: early-release-parity.md.
        if (self._tx is not None and self._tx.descriptor.completion == "end"
                and self._tx.descriptor.epoch is self._epoch
                and (generation, window) == (self._generation, self._window_id)):
            self._error(frame, 2)
            return
        first = (self._held and not self._active and self._terminal is t
                 and (self._generation, self._window_id) == (generation, window)
                 and not self._release_pending)
        completion = "release" if first else ""
        if first:
            self._held = False
            self._hold_started = None
            self._release_pending = True
        self._enqueue(135, struct.pack("<II", generation, window), frame.request_id, completion)

    def _window_descriptor(self):
        if self._stage == "begin":
            edge = self._config[4]
            level = struct.unpack_from("<H", self._config, 6)[0]
            return self._descriptor(144, struct.pack(
                "<IIIHHHHBBBBHHIHHI", self._generation, self._window_id,
                self._config_id, 1, SAMPLE_COUNT, 0,
                0xFFFF if self._trigger_kind == 2 else 0, self._trigger_kind, 0, edge, 0,
                level, 3 if self._trigger_kind == 2 else 2, 72000000, 71, 19, 0), completion="begin")
        if self._stage == "data":
            count = min(250, SAMPLE_COUNT - self._offset)
            payload = struct.pack("<IIHH", self._generation, self._window_id,
                                  self._offset, count)
            payload += self._samples[2 * self._offset:2 * (self._offset + count)]
            return self._descriptor(145, payload, completion="data", count=count)
        if self._stage == "end":
            return self._descriptor(146, struct.pack("<IIHHI", self._generation,
                self._window_id, SAMPLE_COUNT, 0, self._crc), completion="end")
        return None

    def _start_tx(self):
        if self._next_tx > MAX_ID:
            self._recover(6)
            return False
        descriptor = self._queue.pop(0) if self._queue else self._window_descriptor()
        if descriptor is None:
            return False
        encoded = encode_frame(Frame(descriptor.kind, descriptor.session,
                                     self._next_tx, descriptor.request, descriptor.payload))
        self._tx = _Tx(descriptor, encoded)
        self._next_tx += 1  # Called only when read has positive remaining capacity.
        if descriptor.completion == "end":
            self._terminal = Terminal("END", self._generation, self._window_id, self._crc)
            self._tombstone = self._terminal
            self._active = False
        elif descriptor.completion == "abort":
            self._terminal = Terminal("ABORT", self._generation, self._window_id, None, 1)
            self._active = False
        return True

    def _finished(self, descriptor):
        if descriptor.epoch is not self._epoch:
            return
        action = descriptor.completion
        if action == "begin" and self._stage:
            self._stage = "data"
        elif action == "data" and self._stage:
            self._offset += descriptor.count
            self._stage = "end" if self._offset == SAMPLE_COUNT else "data"
        elif action == "end":
            self._stage = ""
            if self._stop_acks or not self._continuation:
                self._held = False
            elif not self._release_pending:
                self._hold_started = self._now
                self._set_state(WAIT_RELEASE)
        elif action == "abort":
            self._held = self._abort_pending = False
        elif action == "stop":
            self._stop_acks -= 1
            if self._stop_acks == 0:
                self._set_state(STOPPED)
        elif action == "release":
            self._release_pending = False
            if self._continuation and not self._stop_acks:
                if self._config[2] == 2:
                    self._continuation = False
                    self._set_state(STOPPED)
                else:
                    self._wait()

    def read(self, max_bytes, now_ms):
        _uint(max_bytes, MAX_CHUNK_SIZE)
        self._time(now_ms)
        self._remember(self._advance(now_ms))
        result = bytearray()
        while len(result) < max_bytes:
            if self._tx is None and not self._start_tx():
                break
            tx = self._tx
            n = min(max_bytes - len(result), len(tx.encoded) - tx.offset)
            result.extend(tx.encoded[tx.offset:tx.offset + n])
            tx.offset += n
            if tx.offset == len(tx.encoded):
                self._tx = None
                self._finished(tx.descriptor)
        return bytes(result)

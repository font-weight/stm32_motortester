"""HOST-006/008: bounded draft control and verified-window release.

Only this module adopts shared PR14 at SOURCE_SHA. Existing wire.py keeps its
own older provenance. WINDOW_END_ADMITTED never proves a complete measurement.
"""
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
import struct
import zlib

from .wire import Frame, WireError, encode_frame
from .window import (
    BeginMetadata, ExpectedContext, MAX_DATA_SAMPLES, MAX_WINDOW_SAMPLES,
    PROTOCOL_SOURCE_SHA, SampleBlock, WindowCode, WindowError, WindowResult,
)

SOURCE_SHA = "b6ae8025baf1a47e8173997029d2d8b858ada74c"
VALIDATION_SCOPE = "draft_analog_control"
MAX_TIME_MS = (1 << 64) - 1
MAX_ID = (1 << 32) - 2
RESPONSE_TIMEOUT_MS = 1000


class ErrorCode(str, Enum):
    ARGUMENT = "ARGUMENT"
    RANGE = "RANGE"
    CLOCK_ROLLBACK = "CLOCK_ROLLBACK"
    STATE = "STATE"
    UNSUPPORTED = "UNSUPPORTED"
    STALE_TICKET = "STALE_TICKET"


_MESSAGES = {
    ErrorCode.ARGUMENT: "Неверный тип аргумента контроллера сеанса",
    ErrorCode.RANGE: "Значение вне допустимого диапазона",
    ErrorCode.CLOCK_ROLLBACK: "Монотонное время не может уменьшаться",
    ErrorCode.STATE: "Команда недоступна в текущем состоянии",
    ErrorCode.UNSUPPORTED: "Этот путь управления пока не поддерживается",
    ErrorCode.STALE_TICKET: "Билет записи устарел или выдан другим контроллером",
}


class SessionError(ValueError):
    def __init__(self, code: ErrorCode):
        self.code = code
        super().__init__(_MESSAGES[code])


class Phase(str, Enum):
    IDLE = "IDLE"
    HELLO = "HELLO"
    CAPS = "CAPS"
    READY = "READY"
    ARMED = "ARMED"
    RECOVERY = "RECOVERY"


class DeviceState(IntEnum):
    """State codes reported by wire STATUS, separate from host control Phase."""

    STOPPED = 0
    PREPARING = 1
    WAITING = 2
    CAPTURING = 3
    TRANSFERRING = 4
    ERROR = 5
    WAIT_RELEASE = 6


class EventKind(str, Enum):
    HELLO_ACCEPTED = "HELLO_ACCEPTED"
    CAPABILITIES = "CAPABILITIES"
    CONFIGURED = "CONFIGURED"
    ARMED = "ARMED"
    STOPPED = "STOPPED"
    COMMAND_ERROR = "COMMAND_ERROR"
    STATUS = "STATUS"
    WINDOW_BEGIN = "WINDOW_BEGIN"
    WINDOW_DATA = "WINDOW_DATA"
    WINDOW_END_ADMITTED = "WINDOW_END_ADMITTED"
    WINDOW_RELEASED = "WINDOW_RELEASED"
    WINDOW_ABORT = "WINDOW_ABORT"
    RECOVERY = "RECOVERY"


class RecoveryReason(str, Enum):
    DIRECTION = "DIRECTION"
    TYPE = "TYPE"
    SEQUENCE = "SEQUENCE"
    REPLY = "REPLY"
    PAYLOAD = "PAYLOAD"
    CAPABILITIES = "CAPABILITIES"
    WINDOW = "WINDOW"
    TIMEOUT = "TIMEOUT"
    TRANSPORT_LOST = "TRANSPORT_LOST"
    UNKNOWN_WRITE = "UNKNOWN_WRITE"
    EXHAUSTED = "EXHAUSTED"
    DEVICE_ERROR = "DEVICE_ERROR"
    PROTOCOL_FAULT = "PROTOCOL_FAULT"


class FaultCode(str, Enum):
    VERSION = "VERSION"
    DECODE = "DECODE"
    DOWNSTREAM = "DOWNSTREAM"


def _uint(value, maximum, minimum=0):
    if type(value) is not int:
        raise SessionError(ErrorCode.ARGUMENT)
    if not minimum <= value <= maximum:
        raise SessionError(ErrorCode.RANGE)


def _analog_trigger(mode, pre, index, kind, quality):
    # Без события Auto не имеет индекса запуска. Bit2 пока допускается старым
    # pinned wire и не означает, что внешний путь реализован этим контроллером.
    if quality & ~7 or bool(quality & 1) != (kind == 2):
        return False
    return ((kind == 0 and index == pre)
            or (mode == 0 and kind == 2 and index == 0xFFFF))


@dataclass(frozen=True, slots=True)
class Config:
    profile: int = 1
    mode: int = 1
    source: int = 0
    edge: int = 0
    level_code: int = 2048
    pretrigger_samples: int = 0
    auto_timeout_ms: int = 0

    def __post_init__(self):
        for value, maximum in ((self.profile, 65535), (self.mode, 2),
                               (self.source, 1), (self.edge, 1),
                               (self.pretrigger_samples, 65535),
                               (self.auto_timeout_ms, 60000)):
            _uint(value, maximum)
        _uint(self.level_code, 4094, 1)
        if ((self.mode == 0 and self.auto_timeout_ms < 100)
                or (self.mode != 0 and self.auto_timeout_ms != 0)):
            raise SessionError(ErrorCode.RANGE)

    def payload(self):
        self.__post_init__()
        return struct.pack("<HBBBBHHHI", self.profile, self.mode, self.source,
                           self.edge, 0, self.level_code,
                           self.pretrigger_samples, 0, self.auto_timeout_ms)


@dataclass(frozen=True, slots=True)
class WindowReceipt:
    """Локальный допуск RELEASE, не доказательство происхождения данных.

    Только from_window создаёт пригодный допуск. Вызывающая сторона обязана
    передать тот же доверенный context, с которым работал WindowReceiver.
    """

    session: int
    generation: int
    window_id: int
    sample_count: int
    samples_crc32: int
    _validated: tuple[int, ...] | None = field(default=None, init=False,
                                             repr=False, compare=False)

    def __post_init__(self):
        _uint(self.session, MAX_TIME_MS, 1)
        _uint(self.generation, MAX_ID, 2)
        _uint(self.window_id, MAX_ID, 1)
        _uint(self.sample_count, MAX_WINDOW_SAMPLES, MAX_WINDOW_SAMPLES)
        _uint(self.samples_crc32, 0xFFFFFFFF)

    def _values(self):
        return (self.session, self.generation, self.window_id,
                self.sample_count, self.samples_crc32)

    @classmethod
    def from_window(cls, context, result):
        if (cls is not WindowReceipt or type(context) is not ExpectedContext
                or type(result) is not WindowResult):
            raise SessionError(ErrorCode.ARGUMENT)
        try:
            return cls._from_window(context, result)
        except AttributeError:
            raise SessionError(ErrorCode.ARGUMENT) from None
        except WindowError as exc:
            code = ErrorCode.ARGUMENT if exc.code is WindowCode.ARGUMENT else ErrorCode.RANGE
            raise SessionError(code) from exc

    @classmethod
    def _from_window(cls, context, result):
        context.__post_init__()
        _uint(context.generation, MAX_ID, 2)
        _uint(context.config_id, MAX_ID, 1)
        if context.source != 0:
            raise SessionError(ErrorCode.UNSUPPORTED)
        if (type(result.complete) is not bool or type(result.reason) is not WindowCode
                or type(result.rail_observed) is not bool
                or type(result.protocol_source_sha) is not str
                or type(result.validation_scope) is not str
                or any(type(value) is not tuple for value in (
                    result.fragments, result.missing_ranges,
                    result.deferred_checks, result.metadata_issues))):
            raise SessionError(ErrorCode.ARGUMENT)
        if len(result.fragments) > MAX_WINDOW_SAMPLES:
            raise SessionError(ErrorCode.RANGE)
        if (not result.complete or result.reason is not WindowCode.COMPLETE
                or result.missing_ranges or result.metadata_issues
                or result.abort_reason is not None
                or result.protocol_source_sha != PROTOCOL_SOURCE_SHA
                or result.validation_scope != "draft_window_receive"):
            raise SessionError(ErrorCode.STATE)
        if result.deferred_checks:
            raise SessionError(ErrorCode.UNSUPPORTED)
        if type(result.begin) is not BeginMetadata or type(result.samples) is not tuple:
            raise SessionError(ErrorCode.ARGUMENT)
        if len(result.samples) != MAX_WINDOW_SAMPLES:
            raise SessionError(ErrorCode.RANGE)
        _uint(result.samples_crc32, 0xFFFFFFFF)
        b = result.begin
        # Точные типы и пределы проверяются до обхода/упаковки массива.
        for name in BeginMetadata.__dataclass_fields__:
            _uint(getattr(b, name), 0xFFFFFFFF)
        _uint(b.window_id, MAX_ID, 1)
        if ((b.generation, b.config_id, b.profile, b.sample_count,
             b.pretrigger_samples, b.source, b.edge, b.channel, b.level_code,
             b.timer_clock_hz, b.timer_psc, b.timer_arr, b.external_edge_id) !=
                (context.generation, context.config_id, context.profile,
                 MAX_WINDOW_SAMPLES, context.pretrigger_samples,
                 context.source, context.edge, 0, context.level_code,
                 72000000, 71, 19, 0)
                or not _analog_trigger(context.acquisition_mode,
                                       context.pretrigger_samples, b.trigger_index,
                                       b.trigger_kind, b.quality_flags)):
            raise SessionError(ErrorCode.STATE)
        offset = 0
        for block in result.fragments:
            if type(block) is not SampleBlock or type(block.samples) is not tuple:
                raise SessionError(ErrorCode.ARGUMENT)
            _uint(block.offset, MAX_WINDOW_SAMPLES - 1)
            count = len(block.samples)
            if not 1 <= count <= MAX_DATA_SAMPLES or offset + count > MAX_WINDOW_SAMPLES:
                raise SessionError(ErrorCode.RANGE)
            if block.offset != offset:
                raise SessionError(ErrorCode.STATE)
            offset += count
        if offset != MAX_WINDOW_SAMPLES:
            raise SessionError(ErrorCode.STATE)
        crc, rail = 0, False
        for block in result.fragments:
            for index, code in enumerate(block.samples, block.offset):
                _uint(code, 4095)
                _uint(result.samples[index], 4095)
                if code != result.samples[index]:
                    raise SessionError(ErrorCode.STATE)
                rail = rail or code in (0, 4095)
            crc = zlib.crc32(struct.pack(f"<{len(block.samples)}H", *block.samples), crc)
        if (crc != result.samples_crc32 or rail != result.rail_observed
                or bool(b.quality_flags & 2) != rail):
            raise SessionError(ErrorCode.STATE)
        receipt = cls(context.session, b.generation, b.window_id, b.sample_count, crc)
        # Фиксированная привязка защищает от случайного прямого вызова/replace,
        # но не аутентифицирует объект против обхода Python через object.__setattr__.
        object.__setattr__(receipt, "_validated", receipt._values())
        return receipt


@dataclass(frozen=True, slots=True)
class Capabilities:
    firmware_build: int
    capability_bits: int
    profile: int
    timer_clock_hz: int
    timer_psc: int
    timer_arr: int
    adc_clock_hz: int
    adc_sample_cycles_x2: int
    window_samples: int
    pretrigger_samples: tuple[int, ...]
    mode_bits: int
    source_bits: int
    edge_bits: int


@dataclass(frozen=True, slots=True)
class TxTicket:
    frame: Frame
    encoded: bytes
    offset: int = 0


@dataclass(frozen=True, slots=True)
class TerminalWindow:
    kind: EventKind
    generation: int
    window_id: int
    reason: int = 0
    samples_crc32: int | None = None


@dataclass(frozen=True, slots=True)
class RecoveryDiagnostic:
    """First recovery's bounded control context, captured before cancellation.

    command_type/request_id identify a known triggering request only (deadline
    expiry or uncertain write). Pending lanes are context, not a claim that a
    particular request caused a read/protocol failure. No payload/backend text.
    """
    reason: RecoveryReason
    fault: FaultCode | None = None
    command_type: int = 0
    request_id: int = 0
    pending_command_type: int = 0
    pending_request_id: int = 0
    pending_stop_id: int = 0

    def __post_init__(self):
        if (type(self.reason) is not RecoveryReason
                or (self.fault is not None and type(self.fault) is not FaultCode)):
            raise SessionError(ErrorCode.ARGUMENT)
        for kind, request in ((self.command_type, self.request_id),
                              (self.pending_command_type, self.pending_request_id)):
            _uint(kind, 7)
            _uint(request, MAX_ID)
            if kind not in (0, 1, 2, 3, 4, 5, 7) or bool(kind) != bool(request):
                raise SessionError(ErrorCode.ARGUMENT)
        _uint(self.pending_stop_id, MAX_ID)

    def text(self):
        self.__post_init__()
        commands = {0: "none", 1: "HELLO", 2: "GET_CAPS", 3: "CONFIG",
                    4: "ARM", 5: "STOP", 7: "RELEASE"}
        return (f"RECOVERY reason={self.reason.value}; "
                f"fault={self.fault.value if self.fault else 'none'}; "
                f"command={commands[self.command_type]}; request_id={self.request_id}; "
                f"pending={commands[self.pending_command_type]}/{self.pending_request_id}; "
                f"stop_request_id={self.pending_stop_id}")


@dataclass(frozen=True, slots=True)
class Event:
    kind: EventKind
    request_id: int = 0
    generation: int = 0
    window_id: int = 0
    reason: int | RecoveryReason = 0
    frame: Frame | None = None
    preserve_fragments: bool = False
    reset_transport: bool = False
    fault: FaultCode | None = None
    source_sha: str = SOURCE_SHA
    validation_scope: str = VALIDATION_SCOPE
    recovery: RecoveryDiagnostic | None = None


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    """One validated STATUS observation, never a physical-state guarantee.

    received_ms is the host monotonic receipt time, not a device timestamp.
    The counters retain their wire meaning; no external trigger is enabled.
    """

    state: DeviceState
    reason: int
    generation: int
    window_id: int
    observed_external_edges: int
    busy_external_edges: int
    dropped_windows: int
    received_ms: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Host control state and the latest device report are independent.

    device_status=None means no validated STATUS in this session. A stale
    report is retained evidence, invalidated by control/window boundaries or
    recovery. False does not promise physical liveness: STATUS has no heartbeat
    deadline, and connection state belongs to the outer transport/bridge.
    """

    phase: Phase
    session: int
    next_tx_sequence: int
    next_request_id: int
    next_rx_sequence: int
    pending_request_id: int
    pending_stop_id: int
    config: Config | None
    config_id: int
    generation: int
    stop_barrier: bool
    window_barrier: bool
    window_id: int
    terminal: TerminalWindow | None
    capabilities: Capabilities | None
    now_ms: int | None
    tx_offset: int | None
    source_sha: str = SOURCE_SHA
    validation_scope: str = VALIDATION_SCOPE
    device_status: DeviceStatus | None = None
    device_status_stale: bool = False
    recovery: RecoveryDiagnostic | None = None


@dataclass(frozen=True, slots=True)
class _Pending:
    kind: int
    request_id: int
    payload: bytes
    settings: Config | None = None
    generation: int = 0
    started: bool = False
    sent_ms: int | None = None


_DEVICE_TYPES = frozenset((129, 130, 131, 132, 133, 134, 135, 144, 145, 146, 147, 255))
_REPLY_TYPES = {1: 129, 2: 130, 3: 131, 4: 132, 5: 133, 7: 135}


class SessionController:
    """Single owner; caller supplies monotonic time and truthful write progress.

    start after recovery asserts that the outer transport has been reset.
    No callback, outbound queue, sample array, background task or I/O is owned.
    """
    __slots__ = ("_phase", "_session", "_now", "_next_tx", "_next_rx",
                 "_next_request", "_ordinary", "_stop", "_ticket", "_last_rx",
                 "_hello", "_caps", "_config", "_config_id", "_generation",
                 "_window_id", "_active_window", "_window_barrier", "_terminal",
                 "_released_window", "_device_status", "_device_status_stale",
                 "_recovery_diagnostic")

    def __init__(self):
        self._now = None
        self._session = 0
        self._reset()
        self._phase = Phase.IDLE

    def _reset(self):
        self._next_tx = self._next_rx = self._next_request = 1
        self._ordinary = self._stop = self._ticket = self._last_rx = None
        self._hello = self._caps = self._config = self._terminal = None
        self._config_id = self._generation = self._window_id = self._active_window = 0
        self._released_window = 0
        self._window_barrier = False
        self._device_status = None
        self._device_status_stale = False
        self._recovery_diagnostic = None

    def snapshot(self):
        return Snapshot(self._phase, self._session, self._next_tx,
                        self._next_request, self._next_rx,
                        self._ordinary.request_id if self._ordinary else 0,
                        self._stop.request_id if self._stop else 0,
                        self._config, self._config_id, self._generation,
                        self._stop is not None, self._window_barrier,
                        self._window_id, self._terminal, self._caps, self._now,
                        self._ticket.offset if self._ticket else None,
                        device_status=self._device_status,
                        device_status_stale=self._device_status_stale,
                        recovery=self._recovery_diagnostic)

    def _stale_device_status(self):
        # Keep the last report for diagnostics without relabelling it as a new
        # state. Only another validated STATUS can refresh it; reset clears it.
        if self._device_status is not None:
            self._device_status_stale = True

    def _time(self, now_ms):
        _uint(now_ms, MAX_TIME_MS)
        if self._now is not None and now_ms < self._now:
            raise SessionError(ErrorCode.CLOCK_ROLLBACK)

    def _recover(self, reason, fault=None, *, request=None):
        if self._phase is Phase.RECOVERY:
            return ()
        self._recovery_diagnostic = RecoveryDiagnostic(reason, fault,
            request.kind if request else 0, request.request_id if request else 0,
            self._ordinary.kind if self._ordinary else 0,
            self._ordinary.request_id if self._ordinary else 0,
            self._stop.request_id if self._stop else 0)
        self._stale_device_status()
        self._phase = Phase.RECOVERY
        self._ordinary = self._stop = self._ticket = None
        return (Event(EventKind.RECOVERY, request_id=self._recovery_diagnostic.request_id,
                      generation=self._generation,
                      window_id=self._window_id, reason=reason,
                      preserve_fragments=True, reset_transport=True, fault=fault,
                      recovery=self._recovery_diagnostic),)

    def _advance(self, now_ms):
        self._now = now_ms
        for request in (self._ordinary, self._stop):
            if (request is not None and request.sent_ms is not None
                    and now_ms - request.sent_ms >= RESPONSE_TIMEOUT_MS):
                return self._recover(RecoveryReason.TIMEOUT, request=request)
        return ()

    def _capacity(self):
        unstarted = sum(p is not None and not p.started
                        for p in (self._ordinary, self._stop))
        return self._next_request <= MAX_ID and self._next_tx + unstarted <= MAX_ID

    def start(self, nonce, now_ms):
        _uint(nonce, MAX_TIME_MS, 1)
        self._time(now_ms)
        if self._phase not in (Phase.IDLE, Phase.RECOVERY) or nonce == self._session:
            raise SessionError(ErrorCode.STATE)
        self._reset()
        self._session, self._now, self._phase = nonce, now_ms, Phase.HELLO
        self._ordinary = _Pending(1, 1, struct.pack("<Q", nonce))
        self._next_request = 2
        return ()

    def _available(self):
        if (self._phase is not Phase.READY or self._ordinary is not None
                or self._stop is not None or self._window_barrier):
            raise SessionError(ErrorCode.STATE)

    def _valid_config(self, settings):
        if type(settings) is not Config:
            raise SessionError(ErrorCode.ARGUMENT)
        settings.__post_init__()
        if settings.source != 0:
            raise SessionError(ErrorCode.UNSUPPORTED)
        if self._caps is not None and (
                settings.profile != self._caps.profile
                or not (self._caps.mode_bits & (1 << settings.mode))
                or not (self._caps.source_bits & (1 << settings.source))
                or not (self._caps.edge_bits & (1 << settings.edge))
                or settings.pretrigger_samples not in self._caps.pretrigger_samples):
            raise SessionError(ErrorCode.UNSUPPORTED)

    def _queue(self, kind, payload, now_ms, settings=None, generation=0):
        events = self._advance(now_ms)
        if events:
            return events
        if not self._capacity():
            return self._recover(RecoveryReason.EXHAUSTED)
        if kind in (3, 4, 5, 7):
            self._stale_device_status()
        pending = _Pending(kind, self._next_request, payload, settings, generation)
        self._next_request += 1
        if kind == 5:
            self._stop = pending
        else:
            self._ordinary = pending
        return ()

    def configure(self, settings, now_ms):
        self._time(now_ms)
        self._valid_config(settings)
        self._available()
        return self._queue(3, settings.payload(), now_ms, settings=settings)

    def arm(self, now_ms):
        self._time(now_ms)
        self._available()
        if self._config is None:
            raise SessionError(ErrorCode.STATE)
        return self._queue(4, struct.pack("<I", self._config_id), now_ms)

    def stop(self, now_ms):
        self._time(now_ms)
        if self._phase not in (Phase.CAPS, Phase.READY, Phase.ARMED) or self._stop:
            raise SessionError(ErrorCode.STATE)
        generation = (self._ordinary.request_id
                      if self._ordinary and self._ordinary.kind == 4 else self._generation)
        return self._queue(5, struct.pack("<I", generation), now_ms, generation=generation)

    def release(self, receipt, now_ms):
        self._time(now_ms)
        if type(receipt) is not WindowReceipt:
            raise SessionError(ErrorCode.ARGUMENT)
        try:
            receipt.__post_init__()
            if type(receipt._validated) is not tuple or receipt._validated != receipt._values():
                raise SessionError(ErrorCode.STATE)
        except AttributeError:
            raise SessionError(ErrorCode.ARGUMENT) from None
        terminal = self._terminal
        if (self._phase not in (Phase.ARMED, Phase.READY)
                or self._ordinary is not None or self._stop is not None
                or self._active_window or terminal is None
                or terminal.kind is not EventKind.WINDOW_END_ADMITTED
                or receipt.session != self._session
                or (receipt.generation, receipt.window_id, receipt.samples_crc32) !=
                    (terminal.generation, terminal.window_id, terminal.samples_crc32)
                or receipt.generation != self._generation
                or receipt.window_id != self._window_id
                or receipt.window_id == self._released_window):
            raise SessionError(ErrorCode.STATE)
        return self._queue(7, struct.pack("<III", receipt.generation, receipt.window_id,
                                         receipt.samples_crc32), now_ms,
                           generation=receipt.generation)

    def prepare_tx(self):
        if self._ticket is not None:
            return self._ticket
        if self._phase in (Phase.IDLE, Phase.RECOVERY):
            return None
        pending = next((p for p in (self._ordinary, self._stop)
                        if p is not None and p.sent_ms is None), None)
        if pending is None:
            return None
        frame = Frame(pending.kind, 0 if pending.kind == 1 else self._session,
                      self._next_tx, pending.request_id, pending.payload)
        self._ticket = TxTicket(frame, encode_frame(frame))
        return self._ticket

    def written(self, ticket, accepted_count, now_ms):
        self._time(now_ms)
        _uint(accepted_count, 544)
        if type(ticket) is not TxTicket:
            raise SessionError(ErrorCode.ARGUMENT)
        if ticket is not self._ticket:
            raise SessionError(ErrorCode.STALE_TICKET)
        if accepted_count > len(ticket.encoded) - ticket.offset:
            raise SessionError(ErrorCode.RANGE)
        events = self._advance(now_ms)
        if events or accepted_count == 0:
            return events
        attr = "_stop" if ticket.frame.message_type == 5 else "_ordinary"
        pending = getattr(self, attr)
        if ticket.offset == 0:
            self._next_tx += 1
            pending = replace(pending, started=True)
        offset = ticket.offset + accepted_count
        if offset == len(ticket.encoded):
            pending = replace(pending, sent_ms=now_ms)
            self._ticket = None
        else:
            self._ticket = replace(ticket, offset=offset)
        setattr(self, attr, pending)
        return ()

    def tick(self, now_ms):
        self._time(now_ms)
        return self._advance(now_ms)

    def transport_lost(self, now_ms):
        self._time(now_ms)
        self._now = now_ms
        return self._recover(RecoveryReason.TRANSPORT_LOST)

    def unknown_write(self, now_ms):
        self._time(now_ms)
        self._now = now_ms
        request = None
        if self._ticket is not None:
            request = (self._stop if self._ticket.frame.message_type == 5
                       else self._ordinary)
        return self._recover(RecoveryReason.UNKNOWN_WRITE, request=request)

    def protocol_fault(self, code, now_ms):
        self._time(now_ms)
        if type(code) is not FaultCode:
            raise SessionError(ErrorCode.ARGUMENT)
        self._now = now_ms
        return self._recover(RecoveryReason.PROTOCOL_FAULT, code)

    def receive(self, frame, now_ms):
        self._time(now_ms)
        if type(frame) is not Frame:
            raise SessionError(ErrorCode.ARGUMENT)
        try:
            frame.__post_init__()
        except WireError as exc:
            raise SessionError(ErrorCode.ARGUMENT) from exc
        if 1 <= frame.message_type <= 7:
            self._now = now_ms
            return self._recover(RecoveryReason.DIRECTION)
        # Old-session traffic must not advance time, deadlines or replay state.
        if frame.session != self._session:
            return ()
        if self._phase in (Phase.IDLE, Phase.RECOVERY):
            return ()
        events = self._advance(now_ms)
        if events:
            return events
        if frame == self._last_rx:
            return ()
        if frame.message_type not in _DEVICE_TYPES:
            return self._recover(RecoveryReason.TYPE)
        if not 1 <= frame.sequence <= MAX_ID:
            return self._recover(RecoveryReason.EXHAUSTED)
        if frame.sequence != self._next_rx:
            return self._recover(RecoveryReason.SEQUENCE)
        if frame.request_id > MAX_ID:
            return self._recover(RecoveryReason.REPLY)
        self._last_rx, self._next_rx = frame, frame.sequence + 1
        if self._phase is Phase.HELLO and frame.message_type != 129:
            return self._recover(RecoveryReason.REPLY)
        if frame.message_type in (134, 144, 145, 146, 147) and frame.request_id == 0:
            return self._event(frame, now_ms)
        pending = (self._stop if self._stop and frame.request_id == self._stop.request_id
                   else self._ordinary)
        if (pending is None or frame.request_id != pending.request_id
                or pending.sent_ms is None
                or (pending.kind == 5 and self._ordinary is not None)
                or frame.message_type not in (_REPLY_TYPES[pending.kind], 255)):
            return self._recover(RecoveryReason.REPLY)
        return self._reply(frame, pending, now_ms)

    def _reply(self, frame, pending, now_ms):
        data, kind = frame.payload, frame.message_type
        if kind == 255:
            if len(data) != 8:
                return self._recover(RecoveryReason.PAYLOAD)
            rejected, code, reserved, generation = struct.unpack("<BBHI", data)
            if (rejected != pending.kind or not 1 <= code <= 8 or reserved
                    or generation != self._generation):
                return self._recover(RecoveryReason.PAYLOAD)
            event = Event(EventKind.COMMAND_ERROR, pending.request_id,
                          self._generation, reason=code, frame=frame)
            if pending.kind == 5:
                self._stop = None
                return (event,) + self._recover(RecoveryReason.DEVICE_ERROR)
            self._ordinary = None
            if pending.kind in (1, 2, 7):
                return (event,) + self._recover(RecoveryReason.DEVICE_ERROR)
            return (event,)
        if kind == 129:
            if len(data) != 16:
                return self._recover(RecoveryReason.PAYLOAD)
            major, minor, channels, bits, max_payload, max_window, build, caps = (
                struct.unpack("<BBBBHHII", data))
            if ((major, minor, channels, bits, max_payload, max_window) !=
                    (0, 1, 1, 12, 512, 2048) or caps & ~63):
                return self._recover(RecoveryReason.CAPABILITIES)
            self._hello = (build, caps)
            self._ordinary = None
            self._phase = Phase.CAPS
            self._queue(2, b"", now_ms)
            return (Event(EventKind.HELLO_ACCEPTED, 1, frame=frame),)
        if kind == 130:
            caps = self._parse_caps(data)
            if caps is None:
                return self._recover(RecoveryReason.CAPABILITIES)
            self._caps, self._ordinary, self._phase = caps, None, Phase.READY
            return (Event(EventKind.CAPABILITIES, pending.request_id, frame=frame),)
        if kind == 131:
            if (len(data) != 20 or data[4:] != pending.payload
                    or struct.unpack_from("<I", data)[0] != self._config_id + 1
                    or self._config_id + 1 > MAX_ID):
                return self._recover(RecoveryReason.PAYLOAD)
            self._config, self._config_id = pending.settings, self._config_id + 1
            self._ordinary = None
            self._stale_device_status()
            return (Event(EventKind.CONFIGURED, pending.request_id, frame=frame),)
        if kind == 132:
            if (len(data) != 8 or struct.unpack("<II", data) !=
                    (pending.request_id, self._config_id)):
                return self._recover(RecoveryReason.PAYLOAD)
            self._generation, self._ordinary = pending.request_id, None
            self._phase = Phase.ARMED
            self._stale_device_status()
            if self._stop is not None:
                return ()
            return (Event(EventKind.ARMED, pending.request_id,
                          self._generation, frame=frame),)
        if kind == 133:
            if len(data) != 12:
                return self._recover(RecoveryReason.PAYLOAD)
            generation, last_window, status = struct.unpack("<IIB", data[:9])
            if (generation != pending.generation or generation != self._generation
                    or last_window != self._window_id or status or data[9:] != b"\0" * 3
                    or self._active_window):
                return self._recover(RecoveryReason.WINDOW)
            self._stop, self._phase = None, Phase.READY
            self._window_barrier = False
            self._stale_device_status()
            return (Event(EventKind.STOPPED, pending.request_id,
                          generation, last_window, frame=frame),)
        if kind == 135:
            if len(data) != 8 or data != pending.payload[:8]:
                return self._recover(RecoveryReason.PAYLOAD)
            generation, window = struct.unpack("<II", data)
            self._ordinary = None
            self._released_window = window
            self._window_barrier = False
            self._stale_device_status()
            # Single останавливается лишь после полного подтверждения RELEASE.
            # Ожидающий STOP сохраняет собственный барьер даже при READY.
            if self._config.mode == 2:
                self._phase = Phase.READY
            # READY после Stop остаётся READY, поздний ACK не вооружает сеанс.
            return (Event(EventKind.WINDOW_RELEASED, pending.request_id,
                          generation, window, frame=frame),)
        return self._recover(RecoveryReason.REPLY)

    def _parse_caps(self, data):
        if len(data) != 30 or self._hello is None:
            return None
        (count, reserved, profile, clock, psc, arr, adc_clock, cycles, window,
         pre_count, modes, sources, edges, pre0, pre1, pre2) = struct.unpack(
             "<BBHIHHIHHBBBBHHH", data)
        build, bits = self._hello
        expected_pre = (3, 0, 512, 1024) if bits & 32 else (1, 0, 0, 0)
        if ((count, reserved, profile, clock, psc, arr, adc_clock, cycles, window)
                != (1, 0, 1, 72000000, 71, 19, 12000000, 143, 2048)
                or modes & ~7 or sources & ~3 or edges & ~3
                or modes != ((bits >> 2) & 7) or sources != (bits & 3)
                or (pre_count, pre0, pre1, pre2) != expected_pre):
            return None
        return Capabilities(build, bits, profile, clock, psc, arr, adc_clock,
                            cycles, window, (pre0, pre1, pre2)[:pre_count],
                            modes, sources, edges)

    def _event(self, frame, now_ms):
        kind, data = frame.message_type, frame.payload
        if kind == 134:
            if len(data) != 24:
                return self._recover(RecoveryReason.PAYLOAD)
            state, reason, reserved, generation, window, observed, busy, dropped = (
                struct.unpack("<BBHIIIII", data))
            if (state > 6 or reason > 6 or reserved or generation != self._generation
                    or window > MAX_ID or busy > observed
                    or (state != 5 and reason != 0) or (state == 5 and reason == 0)):
                return self._recover(RecoveryReason.PAYLOAD)
            self._device_status = DeviceStatus(DeviceState(state), reason,
                generation, window, observed, busy, dropped, now_ms)
            # STATUS is transition-based, with no promised heartbeat interval.
            # An in-flight state-changing command, an older window, or a host
            # ACK inconsistent with the report prevents treating it as current.
            # These checks annotate telemetry only; existing wire/control guards
            # and the reported value itself remain unchanged.
            self._device_status_stale = bool(
                self._stop is not None
                or (self._ordinary is not None and self._ordinary.kind in (3, 4, 7))
                or window < self._window_id
                or (state == DeviceState.STOPPED and self._phase is Phase.ARMED)
                or (state not in (DeviceState.STOPPED, DeviceState.ERROR)
                    and self._phase is not Phase.ARMED))
            if state == 5:
                return self._recover(RecoveryReason.DEVICE_ERROR)
            return (Event(EventKind.STATUS, generation=generation,
                          window_id=window, reason=reason, frame=frame),)
        if len(data) < 8:
            return self._recover(RecoveryReason.PAYLOAD)
        generation, window = struct.unpack_from("<II", data)
        if (self._phase is not Phase.ARMED or generation != self._generation
                or not 1 <= generation <= MAX_ID or not 1 <= window <= MAX_ID):
            return self._recover(RecoveryReason.WINDOW)
        if kind == 144:
            if (len(data) != 40 or self._window_barrier
                    or window != self._window_id + 1):
                return self._recover(RecoveryReason.WINDOW)
            values = struct.unpack("<IIIHHHHBBBBHHIHHI", data)
            (_, _, config_id, profile, samples, pre, index, trigger, source, edge,
             channel, level, quality, clock, psc, arr, external) = values
            c = self._config
            if ((config_id, profile, samples, pre, source, edge,
                 channel, level, clock, psc, arr, external) !=
                    (self._config_id, c.profile, 2048, c.pretrigger_samples,
                     c.source, c.edge, 0, c.level_code, 72000000, 71, 19, 0)
                    or not _analog_trigger(c.mode, pre, index, trigger, quality)):
                return self._recover(RecoveryReason.WINDOW)
            self._window_id = self._active_window = window
            self._window_barrier = True
            self._stale_device_status()
            return (Event(EventKind.WINDOW_BEGIN, generation=generation,
                          window_id=window, frame=frame),)
        if kind == 147:
            if len(data) != 12:
                return self._recover(RecoveryReason.PAYLOAD)
            reason, reserved = struct.unpack_from("<HH", data, 8)
            if (reserved or not 1 <= reason <= 6
                    or (self._active_window != window and
                        (self._window_barrier or window != self._window_id + 1))):
                return self._recover(RecoveryReason.WINDOW)
            self._window_id = window
            self._terminal = TerminalWindow(EventKind.WINDOW_ABORT, generation, window, reason)
            self._active_window, self._window_barrier = 0, True
            self._stale_device_status()
            return (Event(EventKind.WINDOW_ABORT, generation=generation,
                          window_id=window, reason=reason, frame=frame),)
        if window != self._active_window:
            return self._recover(RecoveryReason.WINDOW)
        if kind == 145:
            if len(data) < 14:
                return self._recover(RecoveryReason.PAYLOAD)
            offset, count = struct.unpack_from("<HH", data, 8)
            if (not 1 <= count <= 250 or len(data) != 12 + 2 * count
                    or offset + count > 2048
                    or any(code > 4095 for (code,) in struct.iter_unpack("<H", data[12:]))):
                return self._recover(RecoveryReason.WINDOW)
            return (Event(EventKind.WINDOW_DATA, generation=generation,
                          window_id=window, frame=frame),)
        if kind == 146:
            if len(data) != 16:
                return self._recover(RecoveryReason.PAYLOAD)
            samples, reserved, crc = struct.unpack_from("<HHI", data, 8)
            if samples != 2048 or reserved:
                return self._recover(RecoveryReason.WINDOW)
            self._terminal = TerminalWindow(EventKind.WINDOW_END_ADMITTED,
                                            generation, window, samples_crc32=crc)
            self._active_window = 0
            self._stale_device_status()
            return (Event(EventKind.WINDOW_END_ADMITTED, generation=generation,
                          window_id=window, frame=frame),)
        return self._recover(RecoveryReason.REPLY)

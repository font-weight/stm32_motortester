"""Ограниченная приёмка одного окна непринятого wire-draft, без управления USB.

Вход — Frame из HOST-002 после проверки сеанса/порядка внешним диспетчером.
Локальные проверки не подтверждают общий контракт, аппаратный захват
или шкалу времени.
"""
from dataclasses import dataclass, field
from enum import Enum
import struct
import zlib

from .wire import Frame, MAX_PAYLOAD_SIZE, MAX_TIME_MS


PROTOCOL_SOURCE_SHA = "124b3f32e1e7f7282bce283baa4c9406d4d72950"
MAX_WINDOW_SAMPLES = 2048
MAX_DATA_SAMPLES = 250
PROGRESS_TIMEOUT_MS = 3000
_BEGIN = struct.Struct("<IIIHHHHBBBBHHIHHI")
_DATA = struct.Struct("<IIHH")
_END = struct.Struct("<IIHHI")
_ABORT = struct.Struct("<IIHH")


class WindowCode(str, Enum):
    ARGUMENT = "ARGUMENT"
    RANGE = "RANGE"
    PAYLOAD_SIZE = "PAYLOAD_SIZE"
    CLOCK_ROLLBACK = "CLOCK_ROLLBACK"
    COMPLETE = "COMPLETE"
    TYPE = "TYPE"
    REQUEST = "REQUEST"
    SESSION = "SESSION"
    CONTEXT = "CONTEXT"
    IDENTITY = "IDENTITY"
    LENGTH = "LENGTH"
    ENUM = "ENUM"
    RESERVED = "RESERVED"
    BOUNDS = "BOUNDS"
    ADC = "ADC"
    ORDER = "ORDER"
    OVERLAP = "OVERLAP"
    COUNT = "COUNT"
    CRC = "CRC"
    QUALITY = "QUALITY"
    ABORT = "ABORT"
    TIMEOUT = "TIMEOUT"
    DISCONNECT = "DISCONNECT"
    RESET = "RESET"


_ERROR_MESSAGES = {
    WindowCode.ARGUMENT: "Неверный тип аргумента приёмника окна",
    WindowCode.RANGE: "Число вне допустимого диапазона приёмника окна",
    WindowCode.PAYLOAD_SIZE: "Полезная нагрузка превышает 512 байт",
    WindowCode.CLOCK_ROLLBACK: "Монотонное время не может уменьшаться",
}


class WindowError(ValueError):
    """Ошибка вызова API: состояние и временная база остаются неизменными."""

    def __init__(self, code: WindowCode):
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


def _uint(value: int, maximum: int, minimum: int = 0) -> None:
    if type(value) is not int:
        raise WindowError(WindowCode.ARGUMENT)
    if not minimum <= value <= maximum:
        raise WindowError(WindowCode.RANGE)


@dataclass(frozen=True, slots=True)
class ExpectedContext:
    """Принятые снаружи настройки; согласование CONFIG/CAPS здесь не делается."""

    session: int
    generation: int
    config_id: int
    acquisition_mode: int
    source: int
    edge: int
    level_code: int
    pretrigger_samples: int
    profile: int = 1

    def __post_init__(self):
        _uint(self.session, MAX_TIME_MS, 1)
        _uint(self.generation, 0xFFFFFFFF, 2)
        _uint(self.config_id, 0xFFFFFFFF, 1)
        _uint(self.acquisition_mode, 2)
        _uint(self.source, 1)
        _uint(self.edge, 1)
        _uint(self.level_code, 4094, 1)
        _uint(self.pretrigger_samples, 1024)
        _uint(self.profile, 1, 1)
        if self.pretrigger_samples not in (0, 512, 1024):
            raise WindowError(WindowCode.RANGE)


@dataclass(frozen=True, slots=True)
class BeginMetadata:
    generation: int
    window_id: int
    config_id: int
    profile: int
    sample_count: int
    pretrigger_samples: int
    trigger_index: int
    trigger_kind: int
    source: int
    edge: int
    channel: int
    level_code: int
    quality_flags: int
    timer_clock_hz: int
    timer_psc: int
    timer_arr: int
    external_edge_id: int


@dataclass(frozen=True, slots=True)
class SampleBlock:
    offset: int
    samples: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WindowResult:
    """Один терминальный результат; неполный массив не создаётся и не дополняется."""

    complete: bool
    reason: WindowCode
    begin: BeginMetadata | None
    fragments: tuple[SampleBlock, ...]
    missing_ranges: tuple[tuple[int, int], ...]
    samples: tuple[int, ...] | None
    samples_crc32: int | None
    abort_reason: int | None
    deferred_checks: tuple[str, ...]
    rail_observed: bool
    metadata_issues: tuple[str, ...]
    protocol_source_sha: str = field(default=PROTOCOL_SOURCE_SHA, init=False)
    validation_scope: str = field(default="draft_window_receive", init=False)

    @property
    def semantic_checks_complete(self) -> bool:
        """Только локальные проверки draft; не приёмка протокола или прибора."""
        return self.complete and not self.deferred_checks and not self.metadata_issues


class WindowReceiver:
    """Один владелец и одно окно до явного reset; без очереди и callback."""

    __slots__ = ("_context", "_begin", "_blocks", "_received", "_crc",
                 "_last_progress_ms", "_now_ms", "_terminal")

    def __init__(self, context: ExpectedContext):
        if type(context) is not ExpectedContext:
            raise WindowError(WindowCode.ARGUMENT)
        context.__post_init__()
        self._context = context
        self._clear()

    def _clear(self) -> None:
        self._begin = None
        self._blocks = []
        self._received = 0
        self._crc = 0
        self._last_progress_ms = None
        self._now_ms = None
        self._terminal = False

    def _validate_time(self, now_ms: int) -> None:
        _uint(now_ms, MAX_TIME_MS)
        if self._now_ms is not None and now_ms < self._now_ms:
            raise WindowError(WindowCode.CLOCK_ROLLBACK)

    @staticmethod
    def _validate_frame(frame: Frame) -> None:
        # Повторная проверка защищает границу API даже при обходе frozen через
        # object.__setattr__. До неё нет копирования payload или смены времени.
        if type(frame) is not Frame:
            raise WindowError(WindowCode.ARGUMENT)
        try:
            _uint(frame.message_type, 0xFF)
            _uint(frame.session, MAX_TIME_MS)
            _uint(frame.sequence, 0xFFFFFFFF)
            _uint(frame.request_id, 0xFFFFFFFF)
            if type(frame.payload) is not bytes:
                raise WindowError(WindowCode.ARGUMENT)
            if len(frame.payload) > MAX_PAYLOAD_SIZE:
                raise WindowError(WindowCode.PAYLOAD_SIZE)
        except AttributeError:
            raise WindowError(WindowCode.ARGUMENT) from None

    def _finish(self, reason: WindowCode,
                abort_reason: int | None = None) -> WindowResult:
        complete = reason is WindowCode.COMPLETE
        fragments = tuple(self._blocks)
        rail_observed = any(code in (0, 4095)
                            for block in fragments for code in block.samples)
        deferred = []
        if (self._begin is not None and self._begin.trigger_kind == 1
                and self._context.acquisition_mode == 0):
            deferred.append("AUTO_EXTERNAL_EDGE_ID")
        metadata_issues = ()
        if complete and bool(self._begin.quality_flags & 2) != rail_observed:
            metadata_issues = ("RAIL_FLAG_MISMATCH",)
        result = WindowResult(
            complete=complete, reason=reason, begin=self._begin,
            fragments=fragments,
            missing_ranges=((self._received, MAX_WINDOW_SAMPLES),)
            if self._received < MAX_WINDOW_SAMPLES else (),
            samples=tuple(code for block in fragments for code in block.samples)
            if complete else None,
            samples_crc32=self._crc if complete else None,
            abort_reason=abort_reason, deferred_checks=tuple(deferred),
            rail_observed=rail_observed, metadata_issues=metadata_issues,
        )
        # Результат владеет неизменяемыми фрагментами; приёмник их не удерживает.
        self._blocks = []
        self._begin = None
        self._received = 0
        self._crc = 0
        self._last_progress_ms = None
        self._terminal = True
        return result

    def _expire(self, now_ms: int) -> WindowResult | None:
        if (self._begin is not None
                and now_ms - self._last_progress_ms >= PROGRESS_TIMEOUT_MS):
            return self._finish(WindowCode.TIMEOUT)
        return None

    def tick(self, now_ms: int) -> WindowResult | None:
        """Проверить срок без прогресса. До BEGIN ожидание не ограничивается."""
        self._validate_time(now_ms)
        self._now_ms = now_ms
        return self._expire(now_ms)

    def disconnect(self, now_ms: int) -> WindowResult | None:
        """Сохранить неполное активное окно; истёкший срок имеет приоритет."""
        result = self.tick(now_ms)
        if result is not None:
            return result
        return self._finish(WindowCode.DISCONNECT) if self._begin else None

    def reset(self) -> WindowResult | None:
        """Выдать неполное активное окно и разрешить следующий BEGIN, забыв время."""
        result = self._finish(WindowCode.RESET) if self._begin else None
        self._clear()
        return result

    def feed(self, frame: Frame, now_ms: int) -> WindowResult | None:
        """Принять один допущенный снаружи Frame; конец возвращается ровно раз."""
        self._validate_frame(frame)
        self._validate_time(now_ms)
        self._now_ms = now_ms
        result = self._expire(now_ms)
        if result is not None or self._terminal:
            return result
        if frame.message_type not in (144, 145, 146, 147):
            return self._finish(WindowCode.TYPE)
        if frame.session != self._context.session:
            return self._finish(WindowCode.SESSION)
        if frame.request_id != 0:
            return self._finish(WindowCode.REQUEST)
        if frame.message_type == 144:
            if self._begin is not None:
                return self._finish(WindowCode.ORDER)
            return self._accept_begin(frame.payload, now_ms)
        if self._begin is None:
            return self._finish(WindowCode.ORDER)
        if frame.message_type == 145:
            return self._accept_data(frame.payload, now_ms)
        if frame.message_type == 146:
            return self._accept_end(frame.payload)
        return self._accept_abort(frame.payload)

    def _accept_begin(self, payload: bytes, now_ms: int) -> WindowResult | None:
        if len(payload) != _BEGIN.size:
            return self._finish(WindowCode.LENGTH)
        begin = BeginMetadata(*_BEGIN.unpack(payload))
        expected = self._context
        if begin.generation != expected.generation:
            return self._finish(WindowCode.IDENTITY)
        if begin.window_id == 0:
            return self._finish(WindowCode.BOUNDS)
        if (begin.trigger_kind not in (0, 1, 2) or begin.source not in (0, 1)
                or begin.edge not in (0, 1)):
            return self._finish(WindowCode.ENUM)
        if begin.quality_flags & ~7:
            return self._finish(WindowCode.RESERVED)
        if begin.channel != 0 or not 1 <= begin.level_code <= 4094:
            return self._finish(WindowCode.BOUNDS)
        if begin.sample_count != MAX_WINDOW_SAMPLES:
            return self._finish(WindowCode.COUNT)
        if (begin.config_id != expected.config_id or begin.profile != expected.profile
                or begin.pretrigger_samples != expected.pretrigger_samples
                or begin.source != expected.source or begin.edge != expected.edge
                or begin.level_code != expected.level_code
                or (begin.timer_clock_hz, begin.timer_psc, begin.timer_arr)
                != (72000000, 71, 19)):
            return self._finish(WindowCode.CONTEXT)
        if bool(begin.quality_flags & 1) != (begin.trigger_kind == 2):
            return self._finish(WindowCode.QUALITY)
        if begin.trigger_kind == 2:
            if expected.acquisition_mode != 0 or begin.external_edge_id != 0:
                return self._finish(WindowCode.CONTEXT)
            if begin.trigger_index != 0xFFFF:
                return self._finish(WindowCode.BOUNDS)
        else:
            if begin.trigger_index != begin.pretrigger_samples:
                return self._finish(WindowCode.BOUNDS)
            if begin.source != begin.trigger_kind:
                return self._finish(WindowCode.CONTEXT)
            if begin.trigger_kind == 0 and begin.external_edge_id != 0:
                return self._finish(WindowCode.CONTEXT)
            if (begin.trigger_kind == 1 and expected.acquisition_mode != 0
                    and begin.external_edge_id == 0):
                return self._finish(WindowCode.CONTEXT)
        self._begin = begin
        self._last_progress_ms = now_ms
        return None

    def _identity_matches(self, generation: int, window_id: int) -> bool:
        return (generation, window_id) == (self._begin.generation, self._begin.window_id)

    def _accept_data(self, payload: bytes, now_ms: int) -> WindowResult | None:
        if len(payload) < _DATA.size:
            return self._finish(WindowCode.LENGTH)
        generation, window_id, offset, count = _DATA.unpack_from(payload)
        if not self._identity_matches(generation, window_id):
            return self._finish(WindowCode.IDENTITY)
        if not 1 <= count <= MAX_DATA_SAMPLES or offset + count > MAX_WINDOW_SAMPLES:
            return self._finish(WindowCode.BOUNDS)
        if len(payload) != _DATA.size + 2 * count:
            return self._finish(WindowCode.LENGTH)
        # Диапазоны и точная длина проверены прежде выделения tuple кодов.
        samples = struct.unpack_from(f"<{count}H", payload, _DATA.size)
        if any(code > 4095 for code in samples):
            return self._finish(WindowCode.ADC)
        if offset < self._received:
            for block in self._blocks:
                if offset == block.offset and samples == block.samples:
                    return None  # Дубль не меняет CRC, фрагменты или прогресс.
            return self._finish(WindowCode.OVERLAP)
        if offset != self._received:
            return self._finish(WindowCode.ORDER)
        self._blocks.append(SampleBlock(offset, samples))
        self._received += count
        self._crc = zlib.crc32(memoryview(payload)[_DATA.size:], self._crc)
        self._last_progress_ms = now_ms
        return None

    def _accept_end(self, payload: bytes) -> WindowResult | None:
        if len(payload) != _END.size:
            return self._finish(WindowCode.LENGTH)
        generation, window_id, count, reserved, crc = _END.unpack(payload)
        if not self._identity_matches(generation, window_id):
            return self._finish(WindowCode.IDENTITY)
        if reserved:
            return self._finish(WindowCode.RESERVED)
        if count != MAX_WINDOW_SAMPLES or self._received != MAX_WINDOW_SAMPLES:
            return self._finish(WindowCode.COUNT)
        if crc != self._crc:
            return self._finish(WindowCode.CRC)
        return self._finish(WindowCode.COMPLETE)

    def _accept_abort(self, payload: bytes) -> WindowResult | None:
        if len(payload) != _ABORT.size:
            return self._finish(WindowCode.LENGTH)
        generation, window_id, reason, reserved = _ABORT.unpack(payload)
        if not self._identity_matches(generation, window_id):
            return self._finish(WindowCode.IDENTITY)
        if reserved:
            return self._finish(WindowCode.RESERVED)
        if reason not in (1, 2, 3, 4, 5, 6):
            return self._finish(WindowCode.ENUM)
        return self._finish(WindowCode.ABORT, reason)

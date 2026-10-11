"""Immutable raw capture. Nominal time is not calibrated physical time."""
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from uuid import UUID

MAX_SAMPLES = 65536  # File/host allocation limit, not an MCU capability.


class CaptureError(ValueError):
    """Invalid or unsupported capture, safe to present to the operator."""


@dataclass(frozen=True)
class Capture:
    samples: tuple[int, ...]
    session_id: str
    sequence: int
    nominal_fs_hz: float
    received_at_utc: str
    origin: str = "simulator"
    trigger_kind: str = "signal"
    trigger_index: int | None = None
    trigger_edge: str = "rising"
    trigger_level_code: int = 2048
    pretrigger_samples: int = 0
    firmware_version: str | None = None
    protocol_version: str | None = None
    sensor: str = "unknown"
    quality_flags: tuple[str, ...] = ()
    application_version: str = "0.1.0"
    complete: bool = True

    def __post_init__(self):
        if type(self.samples) is not tuple or not 1 <= len(self.samples) <= MAX_SAMPLES:
            raise CaptureError("Ожидается неизменяемый массив от 1 до 65536 отсчётов")
        if any(type(x) is not int or not 0 <= x <= 4095 for x in self.samples):
            raise CaptureError("Отсчёты должны быть целыми кодами 12-битного АЦП (0…4095)")
        if type(self.session_id) is not str:
            raise CaptureError("Некорректный идентификатор сеанса")
        try:
            UUID(self.session_id)
        except (ValueError, AttributeError) as exc:
            raise CaptureError("Некорректный UUID сеанса") from exc
        if type(self.sequence) is not int or not 0 <= self.sequence <= 0xFFFFFFFF:
            raise CaptureError("Некорректный номер окна")
        if type(self.nominal_fs_hz) not in (int, float) or not 1 <= self.nominal_fs_hz <= 1_000_000 or not isfinite(self.nominal_fs_hz):
            raise CaptureError("Некорректная номинальная частота выборки")
        if type(self.received_at_utc) is not str or not 1 <= len(self.received_at_utc) <= 64:
            raise CaptureError("Метка UTC должна содержать не более 64 символов")
        try:
            stamp = datetime.fromisoformat(self.received_at_utc.replace("Z", "+00:00"))
            if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
                raise ValueError()
        except (AttributeError, TypeError, ValueError) as exc:
            raise CaptureError("Метка приёма должна содержать дату/время UTC") from exc
        if self.origin not in ("simulator", "device"):
            raise CaptureError("Неизвестное происхождение записи")
        if self.trigger_kind not in ("signal", "external", "auto_timeout"):
            raise CaptureError("Неизвестный источник запуска")
        if self.trigger_edge not in ("rising", "falling"):
            raise CaptureError("Неизвестный фронт запуска")
        if type(self.trigger_level_code) is not int or not 0 <= self.trigger_level_code <= 4095:
            raise CaptureError("Некорректный уровень запуска")
        if type(self.pretrigger_samples) is not int or not 0 <= self.pretrigger_samples < len(self.samples):
            raise CaptureError("Некорректный размер предыстории")
        if self.trigger_kind == "auto_timeout":
            if self.trigger_index is not None:
                raise CaptureError("Запуск по тайм-ауту не имеет индекса реального события")
        elif type(self.trigger_index) is not int or not 0 <= self.trigger_index < len(self.samples):
            raise CaptureError("Для события запуска нужен индекс внутри массива")
        elif self.trigger_index != self.pretrigger_samples:
            raise CaptureError("Индекс запуска должен совпадать с фактической предысторией")
        if self.complete is not True:
            raise CaptureError("Неполное окно не поддерживается: отсчёты нельзя достраивать")
        if type(self.application_version) is not str or not 1 <= len(self.application_version) <= 128:
            raise CaptureError("Некорректная версия приложения")
        for value in (self.firmware_version, self.protocol_version):
            if value is not None and (type(value) is not str or not 1 <= len(value) <= 128):
                raise CaptureError("Некорректная версия")
        if type(self.sensor) is not str or not 1 <= len(self.sensor) <= 256:
            raise CaptureError("Некорректное описание датчика")
        if type(self.quality_flags) is not tuple or len(self.quality_flags) > 16 or any(type(x) is not str or not 1 <= len(x) <= 64 for x in self.quality_flags):
            raise CaptureError("Некорректные флаги качества")
        if any(x in ("incomplete", "missing_samples") for x in self.quality_flags):
            raise CaptureError("Неполное окно не поддерживается")
        strings = (self.session_id, self.received_at_utc, self.origin, self.trigger_kind,
                   self.trigger_edge, self.sensor, self.application_version,
                   self.firmware_version, self.protocol_version, *self.quality_flags)
        for text in strings:
            if text is not None and any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in text):
                raise CaptureError("Текст содержит недопустимые управляющие символы")

    def time_seconds(self, index: int) -> float:
        if type(index) is not int or not 0 <= index < len(self.samples):
            raise CaptureError("Индекс вне массива")
        zero = self.trigger_index if self.trigger_index is not None else 0
        return (index - zero) / self.nominal_fs_hz

    @property
    def rail_warning(self) -> bool:
        return 0 in self.samples or 4095 in self.samples

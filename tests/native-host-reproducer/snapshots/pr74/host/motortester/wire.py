"""Экспериментальный byte codec и ограниченный накопитель wire v0.1.

Основание: непринятый draft A 124b3f32e1e7f7282bce283baa4c9406d4d72950.
Модуль не проверяет команды, сеанс или состояние прибора и не открывает USB.
"""
from dataclasses import dataclass
from enum import Enum
import struct
import zlib


MAX_PAYLOAD_SIZE = 512
MAX_RAW_SIZE = 540
MAX_ENCODED_SIZE = 543  # Без нулевого разделителя.
MAX_CHUNK_SIZE = 4096
MAX_TIME_MS = 2**64 - 1
INTERBYTE_TIMEOUT_MS = 1000
FRAME_TIMEOUT_MS = 3000

_HEADER = struct.Struct("<BBBBHHQII")
_CRC = struct.Struct("<I")
_MAX_RETURNED_FRAMES = 137  # Один накопленный кадр + 4095 // 30 новых.


class DiagnosticCode(str, Enum):
    ARGUMENT = "ARGUMENT"
    RANGE = "RANGE"
    PAYLOAD_SIZE = "PAYLOAD_SIZE"
    FRAME_SIZE = "FRAME_SIZE"
    FRAMING = "FRAMING"
    LENGTH = "LENGTH"
    CRC = "CRC"
    VERSION = "VERSION"
    RESERVED = "RESERVED"
    CHUNK_SIZE = "CHUNK_SIZE"
    CLOCK_ROLLBACK = "CLOCK_ROLLBACK"
    TIMEOUT = "TIMEOUT"
    OVERFLOW = "OVERFLOW"


_MESSAGES = {
    DiagnosticCode.ARGUMENT: "Неверный тип аргумента проводного протокола",
    DiagnosticCode.RANGE: "Целое значение вне допустимого диапазона",
    DiagnosticCode.PAYLOAD_SIZE: "Полезная нагрузка превышает 512 байт",
    DiagnosticCode.FRAME_SIZE: "Кодированный кадр превышает 543 байта",
    DiagnosticCode.FRAMING: "Повреждён COBS-кадр или передан разделитель",
    DiagnosticCode.LENGTH: "Длина кадра не соответствует заголовку или пределу",
    DiagnosticCode.CRC: "Контрольная сумма кадра не совпадает",
    DiagnosticCode.VERSION: "Версия целостного кадра отличается от 0.1",
    DiagnosticCode.RESERVED: "Зарезервированные поля заголовка ненулевые",
    DiagnosticCode.CHUNK_SIZE: "Порция входных данных превышает 4096 байт",
    DiagnosticCode.CLOCK_ROLLBACK: "Монотонное время не может уменьшаться",
    DiagnosticCode.TIMEOUT: "Истёк срок приёма кадра; ожидается разделитель",
    DiagnosticCode.OVERFLOW: "Накопитель переполнен; ожидается разделитель",
}


class WireError(ValueError):
    """Ограниченный код и фиксированный русский текст без входных данных."""

    def __init__(self, code: DiagnosticCode):
        self.code = code
        super().__init__(_MESSAGES[code])


def _uint(value: int, maximum: int) -> None:
    if type(value) is not int:
        raise WireError(DiagnosticCode.ARGUMENT)
    if not 0 <= value <= maximum:
        raise WireError(DiagnosticCode.RANGE)


@dataclass(frozen=True, slots=True)
class Frame:
    """Неизменяемый заголовок и непрозрачный payload, без семантики команды."""

    message_type: int
    session: int
    sequence: int
    request_id: int
    payload: bytes

    def __post_init__(self):
        _uint(self.message_type, 0xFF)
        _uint(self.session, 0xFFFFFFFFFFFFFFFF)
        _uint(self.sequence, 0xFFFFFFFF)
        _uint(self.request_id, 0xFFFFFFFF)
        if type(self.payload) is not bytes:
            raise WireError(DiagnosticCode.ARGUMENT)
        if len(self.payload) > MAX_PAYLOAD_SIZE:
            raise WireError(DiagnosticCode.PAYLOAD_SIZE)


def encode_frame(frame: Frame) -> bytes:
    """Вернуть один COBS-кадр вместе с завершающим нулём."""
    if type(frame) is not Frame:
        raise WireError(DiagnosticCode.ARGUMENT)
    # Повторная проверка до упаковки исключает неявное приведение чисел/bytes.
    frame.__post_init__()
    raw = _HEADER.pack(0, 1, frame.message_type, 0, len(frame.payload), 0,
                       frame.session, frame.sequence, frame.request_id)
    raw += frame.payload
    raw += _CRC.pack(zlib.crc32(raw))

    encoded = bytearray()
    # Нулевые байты задают границы блоков. Длинный ненулевой блок делится
    # на отрезки по 254 байта, после которых COBS не восстанавливает ноль.
    for run in raw.split(b"\x00"):
        while len(run) >= 254:
            encoded.append(255)
            encoded.extend(run[:254])
            run = run[254:]
        encoded.append(len(run) + 1)
        encoded.extend(run)
    encoded.append(0)
    return bytes(encoded)


def decode_frame(encoded: bytes) -> Frame:
    """Проверить один кадр без разделителя; при отказе вернуть WireError."""
    if type(encoded) is not bytes:
        raise WireError(DiagnosticCode.ARGUMENT)
    if len(encoded) > MAX_ENCODED_SIZE:
        raise WireError(DiagnosticCode.FRAME_SIZE)
    if not encoded:
        raise WireError(DiagnosticCode.FRAMING)

    raw = bytearray()
    offset = 0
    while offset < len(encoded):
        code = encoded[offset]
        if code == 0:
            raise WireError(DiagnosticCode.FRAMING)
        end = offset + code
        if end > len(encoded):
            raise WireError(DiagnosticCode.FRAMING)
        if len(raw) + code - 1 > MAX_RAW_SIZE:
            raise WireError(DiagnosticCode.LENGTH)
        run = encoded[offset + 1:end]
        if 0 in run:
            raise WireError(DiagnosticCode.FRAMING)
        raw.extend(run)
        offset = end
        if code != 255 and offset < len(encoded):
            if len(raw) == MAX_RAW_SIZE:
                raise WireError(DiagnosticCode.LENGTH)
            raw.append(0)

    if len(raw) < _HEADER.size + _CRC.size:
        raise WireError(DiagnosticCode.LENGTH)
    major, minor, kind, reserved, size, reserved2, session, seq, request = (
        _HEADER.unpack_from(raw)
    )
    if size > MAX_PAYLOAD_SIZE or len(raw) != _HEADER.size + size + _CRC.size:
        raise WireError(DiagnosticCode.LENGTH)
    if zlib.crc32(raw[:-_CRC.size]) != _CRC.unpack_from(raw, len(raw) - 4)[0]:
        raise WireError(DiagnosticCode.CRC)
    if (major, minor) != (0, 1):
        raise WireError(DiagnosticCode.VERSION)
    if reserved or reserved2:
        raise WireError(DiagnosticCode.RESERVED)
    return Frame(kind, session, seq, request, bytes(raw[_HEADER.size:-4]))


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: DiagnosticCode
    count: int

    def __post_init__(self):
        if type(self.code) is not DiagnosticCode or type(self.count) is not int:
            raise WireError(DiagnosticCode.ARGUMENT)
        if not 1 <= self.count <= MAX_CHUNK_SIZE + 1:
            raise WireError(DiagnosticCode.RANGE)


@dataclass(frozen=True, slots=True)
class StreamResult:
    """Кадры по порядку и счётчики ошибок только текущего вызова.

    Чередование кадров и ошибок не сохраняется. Потребитель обязан изучить
    diagnostics до признания порции успешной; политика сеанса здесь не задана.
    """

    frames: tuple[Frame, ...]
    diagnostics: tuple[Diagnostic, ...]

    def __post_init__(self):
        if type(self.frames) is not tuple or type(self.diagnostics) is not tuple:
            raise WireError(DiagnosticCode.ARGUMENT)
        if (len(self.frames) > _MAX_RETURNED_FRAMES
                or len(self.diagnostics) > len(DiagnosticCode)):
            raise WireError(DiagnosticCode.RANGE)
        if (any(type(frame) is not Frame for frame in self.frames)
                or any(type(item) is not Diagnostic for item in self.diagnostics)):
            raise WireError(DiagnosticCode.ARGUMENT)


class StreamDecoder:
    """Один владелец, без параллельных вызовов; история результатов не хранится."""

    __slots__ = ("_buffer", "_used", "_first_ms", "_last_byte_ms",
                 "_now_ms", "_discarding")

    def __init__(self):
        self._buffer = bytearray(MAX_ENCODED_SIZE)
        self.reset()

    def reset(self) -> None:
        """Забыть незавершённый кадр, отбрасывание и базу времени при reconnect."""
        self._used = 0
        self._first_ms = 0
        self._last_byte_ms = 0
        self._now_ms = None
        self._discarding = False

    def _validate_time(self, now_ms: int) -> None:
        _uint(now_ms, MAX_TIME_MS)
        if self._now_ms is not None and now_ms < self._now_ms:
            raise WireError(DiagnosticCode.CLOCK_ROLLBACK)

    def _expire(self, now_ms: int) -> bool:
        if (self._used and not self._discarding
                and (now_ms - self._last_byte_ms >= INTERBYTE_TIMEOUT_MS
                     or now_ms - self._first_ms >= FRAME_TIMEOUT_MS)):
            self._used = 0
            self._discarding = True
            return True
        return False

    def tick(self, now_ms: int) -> StreamResult:
        """Проверить сроки при пустом транспорте, не обновляя время байта."""
        return self.feed(b"", now_ms)

    def feed(self, data: bytes, now_ms: int) -> StreamResult:
        """Принять до 4096 байт с одной монотонной меткой времени в мс.

        Все аргументы проверяются до изменения состояния. Для точности сроков
        транспорт вызывает tick регулярно, а feed — сразу после чтения.
        """
        if type(data) is not bytes:
            raise WireError(DiagnosticCode.ARGUMENT)
        if len(data) > MAX_CHUNK_SIZE:
            raise WireError(DiagnosticCode.CHUNK_SIZE)
        self._validate_time(now_ms)
        self._now_ms = now_ms

        frames = []
        counts = {}
        if self._expire(now_ms):
            counts[DiagnosticCode.TIMEOUT] = 1
        for byte in data:
            if self._discarding:
                if byte == 0:
                    self._discarding = False
                continue
            if byte == 0:
                if self._used:
                    encoded = bytes(memoryview(self._buffer)[:self._used])
                    self._used = 0
                    try:
                        frames.append(decode_frame(encoded))
                    except WireError as error:
                        counts[error.code] = counts.get(error.code, 0) + 1
                continue
            if self._used == MAX_ENCODED_SIZE:
                self._used = 0
                self._discarding = True
                code = DiagnosticCode.OVERFLOW
                counts[code] = counts.get(code, 0) + 1
                continue
            if self._used == 0:
                self._first_ms = now_ms
            self._buffer[self._used] = byte
            self._used += 1
            self._last_byte_ms = now_ms
        return StreamResult(tuple(frames), tuple(
            Diagnostic(code, count) for code, count in counts.items()
        ))

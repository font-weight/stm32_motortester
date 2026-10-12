"""HOST-009: bounded, single-owner serial I/O; no controller or GUI policy.

Importing this module never imports pyserial or opens a port. All operations
belong to one acquisition worker; this class is not thread-safe.
"""
from enum import Enum


MAX_IO_BYTES = 4096
MAX_PORT_CHARS = 1024
MAX_BAUDRATE = (1 << 32) - 1
MAX_TIMEOUT_SECONDS = 1.0


class TransportState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    RECOVERY = "RECOVERY"


class TransportError(Exception):
    """Base error at the transport boundary."""


class TransportArgumentError(TransportError):
    """Invalid argument; no backend operation or state change took place."""


class TransportStateError(TransportError):
    """Operation is unavailable in the current state."""


class TransportOpenError(TransportError):
    """Open failed; cleanup_error reports an additional close failure."""

    def __init__(self, message, *, cleanup_error=None):
        self.cleanup_error = cleanup_error
        super().__init__(message)


class TransportReadError(TransportError):
    """Read failed or returned invalid data; the session requires recovery."""


class TransportWriteError(TransportError):
    """Acceptance is unknown, including on timeout. Never resend the payload."""

    accepted_bytes = None


class TransportCloseError(TransportError):
    """Closure is unconfirmed; retain the handle and explicitly retry close."""


def _uint(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise TransportArgumentError("Недопустимый целочисленный аргумент транспорта")


def _timeout(value, *, allow_zero):
    if (type(value) not in (int, float)
            or not 0 <= value <= MAX_TIMEOUT_SECONDS
            or (not allow_zero and value == 0)):
        raise TransportArgumentError("Недопустимый тайм-аут транспорта")
    return float(value)


def _settings(port, baudrate, read_timeout, write_timeout):
    if (type(port) is not str or not 1 <= len(port) <= MAX_PORT_CHARS
            or port != port.strip() or "://" in port
            or any(ord(char) < 32 or ord(char) == 127 for char in port)):
        raise TransportArgumentError("Нужно явно выбрать имя последовательного порта")
    _uint(baudrate, 1, MAX_BAUDRATE)
    return (port, baudrate, _timeout(read_timeout, allow_zero=True),
            _timeout(write_timeout, allow_zero=False))


def _payload(data):
    if type(data) in (bytes, bytearray):
        size = len(data)
    elif type(data) is memoryview:
        try:
            if data.ndim != 1 or data.itemsize != 1 or not data.c_contiguous:
                raise TransportArgumentError("Нужен непрерывный одномерный буфер байтов")
            size = data.nbytes
        except ValueError as exc:
            raise TransportArgumentError("Буфер байтов уже освобождён") from exc
    else:
        raise TransportArgumentError("Ожидаются bytes, bytearray или memoryview")
    _uint(size, 0, MAX_IO_BYTES)
    # Validate the byte count before copying; never retain a mutable caller buffer.
    return bytes(data)


def _serial_factory(**kwargs):
    # Optional until the first explicit open; fake-backed tests need no pyserial.
    try:
        from serial import Serial
    except ImportError as exc:
        raise TransportOpenError("Для открытия порта требуется пакет pyserial") from exc
    return Serial(**kwargs)


class SerialTransport:
    """One bounded backend call per read/write, without retry or reconnect.

    A supplied factory must create a fresh, closed pyserial-compatible object
    with port/is_open and open/read/write/close. It receives port=None. The
    backend must not be shared or accessed outside this owner.
    """

    __slots__ = ("_factory", "_backend", "_settings", "_state")

    def __init__(self, *, backend_factory=None):
        if backend_factory is not None and not callable(backend_factory):
            raise TransportArgumentError("Фабрика транспорта должна быть вызываемой")
        self._factory = _serial_factory if backend_factory is None else backend_factory
        self._backend = None
        self._settings = None
        self._state = TransportState.CLOSED

    @property
    def state(self):
        return self._state

    @property
    def port(self):
        return self._settings[0] if self._settings is not None else None

    def open(self, port, *, baudrate=115200, read_timeout=0.05, write_timeout=0.1):
        """Explicit open. An identical open is a no-op, not a health check."""
        settings = _settings(port, baudrate, read_timeout, write_timeout)
        if self._state is TransportState.OPEN and settings == self._settings:
            return
        if self._state is not TransportState.CLOSED:
            raise TransportStateError("Сначала требуется успешное закрытие транспорта")
        self._settings = settings
        self._state = TransportState.RECOVERY
        try:
            self._backend = self._factory(
                port=None, baudrate=settings[1], bytesize=8, parity="N", stopbits=1,
                timeout=settings[2], write_timeout=settings[3],
                xonxoff=False, rtscts=False, dsrdtr=False,
            )
            if self._backend.is_open is not False:
                raise ValueError("Factory must return a closed backend")
            self._backend.port = settings[0]
            self._backend.open()
            if self._backend.is_open is not True:
                raise ValueError("Backend did not confirm open")
        except Exception as exc:
            cleanup_error = None
            try:
                self.close()
            except TransportCloseError as close_exc:
                cleanup_error = close_exc
            if isinstance(exc, TransportOpenError) and cleanup_error is None:
                raise
            raise TransportOpenError(
                "Не удалось открыть последовательный порт",
                cleanup_error=cleanup_error,
            ) from exc
        self._state = TransportState.OPEN

    def _require_open(self):
        if self._state is not TransportState.OPEN:
            raise TransportStateError("Транспорт закрыт или требует восстановления сеанса")

    def read(self, size=MAX_IO_BYTES):
        """Return up to size bytes; b'' is normal timeout/no data, not EOF."""
        _uint(size, 0, MAX_IO_BYTES)
        self._require_open()
        if size == 0:
            return b""
        # An interrupted call must also remain unusable until explicit recovery.
        self._state = TransportState.RECOVERY
        try:
            data = self._backend.read(size)
        except Exception as exc:
            raise TransportReadError("Ошибка чтения; требуется восстановление сеанса") from exc
        if type(data) is not bytes or len(data) > size:
            raise TransportReadError("Некорректный результат чтения; требуется восстановление")
        self._state = TransportState.OPEN
        return data

    def write(self, data):
        """Return the backend's known accepted prefix count, never an ACK.

        A short/zero count is returned unchanged. Exceptions or invalid counts
        have unknown acceptance, force RECOVERY and never trigger another write.
        """
        payload = _payload(data)
        self._require_open()
        if not payload:
            return 0
        self._state = TransportState.RECOVERY
        try:
            count = self._backend.write(payload)
        except Exception as exc:
            raise TransportWriteError("Объём записи неизвестен; требуется восстановление сеанса") from exc
        if type(count) is not int or not 0 <= count <= len(payload):
            raise TransportWriteError("Некорректный объём записи; требуется восстановление сеанса")
        self._state = TransportState.OPEN
        return count

    def close(self):
        """Close once. On failure, keep the handle for an explicit close retry."""
        if self._backend is not None:
            self._state = TransportState.RECOVERY
            try:
                self._backend.close()
                if self._backend.is_open is not False:
                    raise ValueError("Backend did not confirm close")
            except Exception as exc:
                raise TransportCloseError("Закрытие порта не подтверждено; повторите закрытие") from exc
        self._backend = None
        self._settings = None
        self._state = TransportState.CLOSED

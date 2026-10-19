"""Explicit receiver-owned serial peer; import/construction never opens a port.

OS timeout settings are requests to the backend, not callback-duration promises.
A successful close settles the handle only. It proves neither device quiescence
nor isolation of bytes/writes buffered outside that handle.
"""
from dataclasses import dataclass, replace
import threading

from .transport import (SerialTransport, TransportArgumentError, TransportState,
                        _settings as validate_serial_settings)


@dataclass(frozen=True, slots=True)
class SerialSettings:
    port: str
    baudrate: int = 115200
    read_timeout: float = 0.0
    write_timeout: float = 0.05

    def __post_init__(self):
        # The accepted transport's pure validator owns literal-port, bool/type,
        # scalar/range and timeout validation; no transport is created here.
        validate_serial_settings(self.port, self.baudrate, self.read_timeout, self.write_timeout)
        if self.read_timeout != 0:
            raise TransportArgumentError("Streaming reads require timeout=0")


@dataclass(frozen=True, slots=True)
class SerialPeerStatus:
    port: str
    generation: int = 0
    state: str = "closed"
    handle_settled: bool = True
    first_error: str | None = None
    close_error: str | None = None


class SerialStreamPeer:
    """One selected port, one handle owner, one backend call per operation.

    backend_factory is the existing SerialTransport test seam. The factory and
    transport are invoked/created only by the receiver's explicit open action.
    There is no scan, retry, reconnect, flush, reset or recovery-proof API.
    """
    def __init__(self, settings: SerialSettings, *, backend_factory=None):
        if type(settings) is not SerialSettings:
            raise TransportArgumentError("Exact immutable SerialSettings required")
        settings.__post_init__()
        if backend_factory is not None and not callable(backend_factory):
            raise TransportArgumentError("Backend factory must be callable")
        self._settings = settings
        self.initial_status = SerialPeerStatus(settings.port)
        self._status = self.initial_status
        self._factory = backend_factory
        self._transport = None
        self._owner_id = None

    @property
    def settings(self):
        return self._settings

    def _owner(self):
        current = threading.get_ident()
        if self._owner_id is None:
            self._owner_id = current
        if current != self._owner_id:
            raise RuntimeError("Serial lifecycle belongs to one receiver")

    def snapshot(self):
        self._owner()
        return self._status

    def open(self):
        self._owner()
        if self._transport is not None or not self._status.handle_settled:
            raise RuntimeError("Prior serial handle has not settled")
        self._status = SerialPeerStatus(self.settings.port, self._status.generation + 1,
                                        "opening", False)
        self._transport = SerialTransport(backend_factory=self._factory)
        try:
            self._transport.open(self.settings.port, baudrate=self.settings.baudrate,
                read_timeout=self.settings.read_timeout, write_timeout=self.settings.write_timeout)
        except Exception as exc:
            settled = self._transport.state is TransportState.CLOSED
            self._status = replace(self._status, state="closed" if settled else "close_failed",
                handle_settled=settled, first_error=type(exc).__name__,
                close_error=None if settled else "Open cleanup did not confirm closure")
            if settled:
                self._transport = None
            raise
        self._status = replace(self._status, state="open")

    def _io(self, kind, value):
        self._owner()
        if self._transport is None or self._status.state != "open":
            raise RuntimeError("Serial peer is not open")
        try:
            return getattr(self._transport, kind)(value)
        except Exception as exc:
            self._status = replace(self._status, state="recovery",
                first_error=self._status.first_error or type(exc).__name__)
            raise

    def read(self, maximum):
        if type(maximum) is not int or not 0 <= maximum <= 544:
            raise TransportArgumentError("Streaming read exceeds its bounded byte offer")
        return self._io("read", maximum)

    def write(self, data):
        # SerialTransport validates/copies the bounded payload and preserves
        # known short/zero counts; exceptions keep accepted_bytes unknown.
        return self._io("write", data)

    def close(self):
        self._owner()
        if self._transport is None:
            return
        try:
            self._transport.close()
        except Exception as exc:
            self._status = replace(self._status, state="close_failed", handle_settled=False,
                first_error=self._status.first_error or type(exc).__name__,
                close_error=type(exc).__name__)
            raise
        self._transport = None
        self._status = replace(self._status, state="closed", handle_settled=True, close_error=None)

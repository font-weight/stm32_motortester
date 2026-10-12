"""HOST-010: single-owner, bounded acquisition runtime, without Qt or file I/O.

The caller owns transport open/close and must establish a real reset boundary
before restarting a failed session. An open COM handle is not reset evidence.
"""
from datetime import datetime, timezone
from uuid import UUID

from .model import Capture
from .session import (SessionController, WindowReceipt, Phase, EventKind,
                      FaultCode, MAX_TIME_MS)
from .wire import StreamDecoder
from .window import ExpectedContext, WindowReceiver, WindowCode


WRITE_STALL_MS = 1000
MAX_READ_BYTES = 4096


class RuntimeError(ValueError):
    """Invalid caller action, safe to display without backend exception text."""


class AcquisitionRuntime:
    """One complete capture and one incomplete result at most; no hidden queue.

    Call pump from a single acquisition worker. Each call performs at most one
    bounded write and one bounded read. clock_ms is sampled after each blocking
    operation too, so backend latency cannot silently extend response deadlines.
    acknowledge requires the exact immutable capture exposed by pending_capture.
    """

    def __init__(self, transport, *, origin, clock_ms, utc_now=None):
        if origin not in ("simulator", "device"):
            raise RuntimeError("Нужно явно указать источник данных")
        if (not callable(clock_ms) or not callable(getattr(transport, "read", None))
                or not callable(getattr(transport, "write", None))
                or (utc_now is not None and not callable(utc_now))):
            raise RuntimeError("Некорректный транспорт или часы")
        self._transport, self._origin, self._clock = transport, origin, clock_ms
        self._utc = utc_now or (lambda: datetime.now(timezone.utc).isoformat())
        self._controller, self._decoder = SessionController(), StreamDecoder()
        self._receiver = self._context = None
        self._capture = self._receipt = self._incomplete = None
        self._acknowledged = False
        self._now = None
        self._write_key = self._write_progress = None

    def snapshot(self):
        return self._controller.snapshot()

    @property
    def pending_capture(self):
        return self._capture

    @property
    def incomplete_result(self):
        return self._incomplete

    def take_incomplete(self):
        """Transfer the immutable fragments to the caller; never pad samples."""
        result, self._incomplete = self._incomplete, None
        return result

    def _time(self, *, write_completed=False):
        try:
            now = self._clock()
        except Exception:
            now = None
        if (type(now) is not int or not 0 <= now <= MAX_TIME_MS
                or (self._now is not None and now < self._now)):
            if self._now is not None:
                fail = (self._controller.unknown_write if write_completed
                        else self._controller.transport_lost)
                self._dispatch(fail(self._now))
            raise RuntimeError("Часы должны быть монотонными целыми миллисекундами")
        self._now = now
        return now

    def start(self, nonce, *, reset_confirmed=False):
        """Explicit handshake; reset_confirmed is caller responsibility, not proof."""
        if type(reset_confirmed) is not bool:
            raise RuntimeError("Неверный признак сброса")
        if self._capture is not None or self._incomplete is not None:
            raise RuntimeError("Сначала сохраните и примите оставшиеся результаты")
        if self.snapshot().phase is Phase.RECOVERY and not reset_confirmed:
            raise RuntimeError("Нужно подтверждение внешнего сброса транспорта")
        events = self._controller.start(nonce, self._time())
        self._decoder.reset()
        self._receiver = self._context = None
        self._write_key = self._write_progress = None
        return events

    def configure(self, settings):
        if self._capture is not None or self._incomplete is not None:
            raise RuntimeError("Предыдущий результат ещё не принят")
        return self._dispatch(self._controller.configure(settings, self._time()))

    def arm(self):
        if self._capture is not None or self._incomplete is not None:
            raise RuntimeError("Предыдущий результат ещё не принят")
        return self._dispatch(self._controller.arm(self._time()))

    def stop(self):
        return self._dispatch(self._controller.stop(self._time()))

    def disconnect(self):
        return self._dispatch(self._controller.transport_lost(self._time()))

    def acknowledge(self, capture):
        """Caller has retained/saved this exact capture; no file operation here."""
        if (capture is not self._capture or capture is None
                or (self._acknowledged and self.snapshot().phase is not Phase.RECOVERY)):
            raise RuntimeError("Запись устарела, не принадлежит сеансу или уже принята")
        s = self.snapshot()
        if s.stop_barrier:
            raise RuntimeError("Дождитесь подтверждения Stop")
        if s.phase is Phase.RECOVERY or (s.phase is Phase.READY and not s.window_barrier):
            self._capture = self._receipt = None
            self._acknowledged = False
            return ()
        events = self._controller.release(self._receipt, self._time())
        self._acknowledged = True
        return self._dispatch(events)

    def _preserve(self):
        if self._receiver is not None:
            result = self._receiver.disconnect(self._now)
            if result is not None:
                self._incomplete = result
            self._receiver = None

    def _dispatch(self, events):
        output = list(events)
        for event in events:
            if event.kind is EventKind.RECOVERY:
                self._preserve()
            elif event.kind is EventKind.WINDOW_RELEASED:
                self._capture = self._receipt = None
                self._acknowledged = False
                self._receiver = self._context = None
            elif event.kind is EventKind.WINDOW_BEGIN:
                if self._capture is not None or self._incomplete is not None:
                    output.extend(self._dispatch(self._controller.protocol_fault(FaultCode.DOWNSTREAM, self._now)))
                    break
                s = self.snapshot()
                c = s.config
                self._context = ExpectedContext(s.session, s.generation, s.config_id,
                    c.mode, c.source, c.edge, c.level_code, c.pretrigger_samples, c.profile)
                self._receiver = WindowReceiver(self._context)
            if event.kind in (EventKind.WINDOW_BEGIN, EventKind.WINDOW_DATA,
                              EventKind.WINDOW_END_ADMITTED, EventKind.WINDOW_ABORT):
                if self._receiver is not None:
                    result = self._receiver.feed(event.frame, self._now)
                    if result is not None:
                        output.extend(self._terminal(result))
        return tuple(output)

    def _terminal(self, result):
        self._receiver = None
        if not result.semantic_checks_complete:
            self._incomplete = result
            # A genuine terminal ABORT may be followed by STOP_ACK; let it drain.
            if result.reason is WindowCode.ABORT:
                return ()
            return self._dispatch(self._controller.protocol_fault(FaultCode.DOWNSTREAM, self._now))
        try:
            receipt = WindowReceipt.from_window(self._context, result)
            b = result.begin
            flags = ["draft_protocol", "nominal_timing_unverified"]
            if result.rail_observed:
                flags.append("rail_observed")
            if b.quality_flags & 4:
                flags.append("wire_quality_bit2")
            capture = Capture(samples=result.samples,
                session_id=str(UUID(int=self._context.session)), sequence=b.window_id,
                nominal_fs_hz=b.timer_clock_hz / ((b.timer_psc + 1) * (b.timer_arr + 1)),
                received_at_utc=self._utc(), origin=self._origin,
                trigger_kind="signal", trigger_index=b.trigger_index,
                trigger_edge="rising" if b.edge == 0 else "falling",
                trigger_level_code=b.level_code, pretrigger_samples=b.pretrigger_samples,
                firmware_version=str(self.snapshot().capabilities.firmware_build),
                protocol_version=f"draft-0.1@{self.snapshot().source_sha}",
                sensor=("Синтетический byte-peer; прибор не подключён" if self._origin == "simulator"
                        else "Датчик и физический масштаб не подтверждены"),
                quality_flags=tuple(flags))
        except Exception:
            self._incomplete = result
            return self._dispatch(self._controller.protocol_fault(FaultCode.DOWNSTREAM, self._now))
        self._capture, self._receipt = capture, receipt
        self._acknowledged = False
        return ()

    def _tick(self):
        output = list(self._dispatch(self._controller.tick(self._now)))
        if self.snapshot().phase is Phase.RECOVERY:
            return tuple(output)
        if (self._write_progress is not None and self.snapshot().tx_offset is not None
                and self._now - self._write_progress >= WRITE_STALL_MS):
            output.extend(self._dispatch(self._controller.transport_lost(self._now)))
            return tuple(output)
        if self._decoder.tick(self._now).diagnostics:
            output.extend(self._dispatch(self._controller.protocol_fault(FaultCode.DECODE, self._now)))
        if self._receiver is not None:
            result = self._receiver.tick(self._now)
            if result is not None:
                output.extend(self._terminal(result))
        return tuple(output)

    def pump(self):
        """One bounded iteration; no retries, auto-ARM, reconnect or sleep."""
        self._time()
        output = list(self._tick())
        if self.snapshot().phase in (Phase.IDLE, Phase.RECOVERY):
            return tuple(output)
        ticket = self._controller.prepare_tx()
        if ticket is not None:
            key = (ticket.frame.sequence, ticket.frame.request_id)
            if key != self._write_key:
                self._write_key, self._write_progress = key, self._now
            if self._now - self._write_progress >= WRITE_STALL_MS:
                output.extend(self._dispatch(self._controller.transport_lost(self._now)))
                return tuple(output)
            payload = ticket.encoded[ticket.offset:]
            try:
                count = self._transport.write(payload)
            except Exception:
                self._time(write_completed=True)
                output.extend(self._dispatch(self._controller.unknown_write(self._now)))
                return tuple(output)
            self._time(write_completed=True)
            if type(count) is not int or not 0 <= count <= len(payload):
                output.extend(self._dispatch(self._controller.unknown_write(self._now)))
                return tuple(output)
            output.extend(self._dispatch(self._controller.written(ticket, count, self._now)))
            if count:
                self._write_progress = self._now
            output.extend(self._tick())
        else:
            self._write_key = self._write_progress = None
        if self.snapshot().phase is Phase.RECOVERY:
            return tuple(output)
        try:
            data = self._transport.read(MAX_READ_BYTES)
        except Exception:
            self._time()
            output.extend(self._dispatch(self._controller.transport_lost(self._now)))
            return tuple(output)
        self._time()
        output.extend(self._tick())
        if self.snapshot().phase is Phase.RECOVERY:
            return tuple(output)
        if type(data) is not bytes or len(data) > MAX_READ_BYTES:
            output.extend(self._dispatch(self._controller.transport_lost(self._now)))
            return tuple(output)
        batch = self._decoder.feed(data, self._now)
        if batch.diagnostics:
            output.extend(self._dispatch(self._controller.protocol_fault(FaultCode.DECODE, self._now)))
            return tuple(output)
        for frame in batch.frames:
            output.extend(self._dispatch(self._controller.receive(frame, self._now)))
            if self.snapshot().phase is Phase.RECOVERY:
                break
        return tuple(output)

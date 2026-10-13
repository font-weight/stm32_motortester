"""Bounded Qt acquisition bridge. Serial and runtime belong to one worker.

Synthetic/offscreen evidence does not establish Windows, USB or hardware safety.
No QObject signal is used as an unbounded command or per-pump status queue.
"""
from dataclasses import dataclass, replace
import secrets
import threading
import time
from uuid import UUID, uuid4

from PySide6 import QtCore

from .device_emulator import DeviceEmulator, WAITING
from . import __version__
from .recording_bridge import RecordingBridgeState, RecordingSessionAdapter, SaveCompletion
from .session_archive import SessionIdentity, SessionSpool
from .runtime import AcquisitionRuntime
from .session import Config, EventKind, Phase, Snapshot
from .transport import SerialTransport, TransportState


PUMP_INTERVAL_MS = 10
SIMULATED_EDGE_DELAY_MS = 150


@dataclass(frozen=True, slots=True)
class BridgeState:
    source: str | None = None
    connected: bool = False
    snapshot: Snapshot | None = None
    busy: bool = False
    start_pending: bool = False
    closing: bool = False
    reset_required: bool = False
    close_failed: bool = False
    has_capture: bool = False
    has_incomplete: bool = False
    recording: RecordingBridgeState | None = None


class _Mailbox:
    """One command, one ACK of each kind, and coalesced priority flags.

    Both directions have fixed slots. The worker posts at most one outstanding
    Qt notification; a blocked GUI cannot accumulate a per-tick event backlog.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.command = None
        self.executing = False  # Diagnostic: any pump, including idle reads.
        self.control_executing = False
        self.stop = self.disconnect = self.shutdown = False
        self.cancel_save = None  # Exact Save operation, never a global cancellation flag.
        self.capture_ack = self.incomplete_ack = None
        self.state = BridgeState()
        self.capture = self.incomplete = self.error = self.saved = None
        self.notified = False

    def barrier(self):
        return self.stop or self.disconnect or self.shutdown

    def cancel_command(self, command=None):
        """Caller holds lock; settle a queued Save's exact admission too."""
        if command is None:
            command, self.command = self.command, None
        if command and command[0] == "save_recording":
            self.saved = SaveCompletion(command[1][1], error="save_cancelled")
            self.error = "Сохранение отменено; исходный spool оставлен"


class _EmulatorTransport:
    def __init__(self, clock):
        self.peer = DeviceEmulator(all_analog_modes=True, pretrigger=True)
        self.clock = clock

    def read(self, size):
        return self.peer.read(size, self.clock())

    def write(self, data):
        return self.peer.write(data, self.clock()).accepted_prefix

    def close(self):
        self.peer.disconnect(self.clock())


class _AcquisitionWorker(QtCore.QObject):
    notification = QtCore.Signal()
    finished = QtCore.Signal()

    def __init__(self, mailbox, serial_factory, clock, interval_ms, edge_delay_ms,
                 spool_factory=SessionSpool.create):
        super().__init__()
        self.box, self.serial_factory, self.clock = mailbox, serial_factory, clock
        self.interval_ms, self.edge_delay_ms = interval_ms, edge_delay_ms
        self.runtime = self.transport = self.timer = None
        self.source = None
        self.connected = self.device_used = self.closing = self.close_failed = False
        self.staged_start = self.arm_ready = self.simulate_edge = False
        self.wait_since = None
        self.ack_capture = self.consumer_capture = None
        self.sent_capture = self.sent_incomplete = None
        self.last_state = None
        self.last_nonce = 0
        self.recording = None
        self.spool_factory = spool_factory
        self.last_save_completion = self.last_recording_error = None

    @QtCore.Slot()
    def initialize(self):
        # Construct the timer after moveToThread; its owner and callbacks agree.
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self.pump)
        self.timer.start()
        self._publish()

    def _error(self, message):
        with self.box.lock:
            self.box.error = message
        self._publish(force=True)

    def _publish(self, *, force=False, complete_tick=False):
        snapshot = self.runtime.snapshot() if self.runtime else None
        capture = self.runtime.pending_capture if self.runtime else None
        incomplete = self.runtime.incomplete_result if self.runtime else None
        with self.box.lock:
            if complete_tick:
                # Publish the resulting state in the same critical section as
                # releasing command ownership; a stale GUI notification cannot
                # expose READY between those two operations.
                self.box.executing = self.box.control_executing = False
            state = BridgeState(self.source, self.connected, snapshot,
                bool(self.box.command or self.box.control_executing or self.box.barrier()
                     or self.staged_start or (self.recording and self.recording.owns_runtime) or (snapshot and
                     (snapshot.pending_request_id or snapshot.pending_stop_id))),
                self.staged_start, self.closing, self.device_used,
                self.close_failed, capture is not None, incomplete is not None,
                self.recording.state if self.recording else None)
            # The monotonic observation timestamp alone is not a UI update.
            comparison = replace(state, snapshot=replace(snapshot, now_ms=None)
                                 if snapshot else None)
            changed = comparison != self.last_state
            if changed:
                self.box.state = state
                self.last_state = comparison
            if capture is None:
                self.sent_capture = None
            if incomplete is None:
                self.sent_incomplete = None
            if capture is not None and capture is not self.sent_capture:
                self.box.capture = capture
                self.sent_capture = capture
                changed = True
            if incomplete is not None and incomplete is not self.sent_incomplete:
                self.box.incomplete = incomplete
                self.sent_incomplete = incomplete
                changed = True
            if state.recording is not None:
                completion = self.recording.save_completion
                if completion is not self.last_save_completion:
                    self.last_save_completion = completion
                    if completion is not None:
                        self.box.saved = completion
                        if completion.error is not None:
                            self.box.error = self._recording_error_message(completion.error)
                        changed = True
                if state.recording.error != self.last_recording_error:
                    self.last_recording_error = state.recording.error
                    if self.last_recording_error is not None:
                        self.box.error = self._recording_error_message(self.last_recording_error)
                        changed = True
            notify = (changed or force or self.box.saved is not None
                      or self.box.error is not None) and not self.box.notified
            if notify:
                self.box.notified = True
        if notify:
            self.notification.emit()

    def _close(self):
        self.close_failed = False
        if self.transport is not None:
            try:
                self.transport.close()
            except Exception:
                self.close_failed = True
                self._error("Закрытие порта не подтверждено. Повторите отключение или закрытие.")
                return False
        self.connected = False
        self.transport = None
        return True

    def _disconnect(self):
        self.staged_start = self.arm_ready = self.simulate_edge = False
        self.wait_since = None
        if self.recording and self.recording.owns_runtime:
            with self.box.lock:
                self.recording.stop()
        if self.runtime and self.runtime.snapshot().phase is not Phase.RECOVERY:
            self._events(self.runtime.disconnect())
        if self.runtime and not (self.recording and self.recording.owns_runtime):
            capture = self.runtime.pending_capture
            if capture is not None and (capture is self.ack_capture or capture is self.consumer_capture):
                self.runtime.acknowledge(capture)  # Recovery transfers ownership without RELEASE.
        self.ack_capture = self.consumer_capture = None
        return self._close()

    def _connect(self, source, port, reset_confirmed):
        if self.connected or self.transport is not None:
            raise ValueError("Сначала отключите текущий источник")
        if self.runtime and (self.runtime.pending_capture is not None
                             or self.runtime.incomplete_result is not None):
            raise ValueError("Сначала сохраните и примите оставшиеся результаты")
        if source == "device" and self.device_used and not reset_confirmed:
            raise ValueError("Повторное подключение требует подтверждения физического сброса прибора")
        if self.recording is not None:
            state = self.recording.state
            if self.recording.owns_runtime or (state.ready_to_save and state.saved_path is None) or state.saving:
                raise ValueError("Сначала завершите и сохраните текущий сеанс записи")
            self.recording.retire()
            self.recording = None
        self.source = source
        self.close_failed = False
        self.staged_start = self.arm_ready = self.simulate_edge = False
        self.consumer_capture = self.ack_capture = None
        self.sent_capture = self.sent_incomplete = None
        if source == "emulator":
            self.transport = _EmulatorTransport(self.clock)
        else:
            # Even a failed open may have affected the physical device.
            self.device_used = True
            self.transport = self.serial_factory()
            try:
                self.transport.open(port, read_timeout=0, write_timeout=0.1)
            except Exception:
                # SerialTransport.open already attempts close. Retain a handle
                # whose cleanup failed so an explicit disconnect can retry it.
                if self.transport.state is TransportState.CLOSED:
                    self.transport = None
                else:
                    self.close_failed = True
                raise
        self.connected = True
        self.runtime = AcquisitionRuntime(self.transport,
            origin="simulator" if source == "emulator" else "device", clock_ms=self.clock)
        nonce = secrets.randbits(64) or 1
        if nonce == self.last_nonce:
            nonce = 1 if nonce == (1 << 64) - 1 else nonce + 1
        self.last_nonce = nonce
        self.runtime.start(nonce)

    @staticmethod
    def _recording_error_message(code):
        return ("Сохранение отменено; исходный spool оставлен" if code == "save_cancelled" else
                "Ошибка записи сеанса; автоматического повтора нет [" + code + "]")

    def _recording_step(self, events=()):
        if self.recording is None:
            return
        with self.box.lock:
            # A GUI request racing this tick wins before any completion or ARM.
            if self.box.barrier():
                self.recording.stop()
            cancellation, self.box.cancel_save = self.box.cancel_save, None
            if cancellation is not None:
                self.recording.cancel_save(cancellation)
            self.recording.observe(events)

    def _events(self, events):
        command_error = None
        for event in events:
            if event.kind is EventKind.CONFIGURED and self.staged_start:
                self.arm_ready = True
            elif event.kind is EventKind.ARMED:
                self.staged_start = False
            elif event.kind in (EventKind.STOPPED, EventKind.COMMAND_ERROR, EventKind.RECOVERY):
                self.staged_start = self.arm_ready = False
                if event.kind is EventKind.COMMAND_ERROR:
                    command_error = f"Прибор отклонил команду (код {event.reason}); проверьте поддерживаемые настройки"
                    self._error(command_error)
                elif event.kind is EventKind.RECOVERY:
                    self.simulate_edge = False
                    reason = getattr(event.reason, "value", str(event.reason))
                    fault = getattr(event.fault, "value", None)
                    messages = {
                        "DIRECTION": "неверное направление кадра",
                        "TYPE": "неподдерживаемый тип сообщения",
                        "SEQUENCE": "нарушен порядок кадров",
                        "REPLY": "неожиданный ответ",
                        "PAYLOAD": "некорректные данные сообщения",
                        "CAPABILITIES": "несовместимые возможности прибора",
                        "WINDOW": "нарушен порядок данных окна",
                        "TIMEOUT": "истёк срок ожидания",
                        "TRANSPORT_LOST": "соединение потеряно",
                        "UNKNOWN_WRITE": "неизвестен результат записи в порт",
                        "EXHAUSTED": "исчерпаны идентификаторы сеанса",
                        "DEVICE_ERROR": "прибор сообщил об ошибке",
                        "PROTOCOL_FAULT": "ошибка протокола",
                    }
                    faults = {"VERSION": "несовместимая версия протокола",
                              "DECODE": "повреждённый кадр",
                              "DOWNSTREAM": "ошибка обработки данных"}
                    detail = messages.get(reason, reason) + f" [{reason}]"
                    if fault:
                        detail += f" / {faults.get(fault, fault)} [{fault}]"
                    message = f"Связь требует восстановления ({detail}). Записи сохранены; автоматического запуска нет."
                    if command_error is not None:
                        message += "\n" + command_error
                    self._error(message)
        self._recording_step(events)

    def _accept_capture(self):
        if self.runtime is None or (self.recording and self.recording.owns_runtime):
            return
        capture = self.runtime.pending_capture
        if capture is None:
            self.ack_capture = self.consumer_capture = None
            return
        snapshot = self.runtime.snapshot()
        # A lost RELEASE_ACK retains the capture in runtime. The earlier exact
        # consumer ACK still proves retention, and recovery sends no new bytes.
        if snapshot.phase is Phase.RECOVERY and capture is self.consumer_capture:
            self.runtime.acknowledge(capture)
            self.consumer_capture = None
        elif self.ack_capture is capture and not snapshot.stop_barrier:
            with self.box.lock:
                if self.box.barrier() or self.closing:
                    return
                events = self.runtime.acknowledge(capture)
                self.consumer_capture = capture
                self.ack_capture = None
            self._events(events)

    def _edge(self):
        if not (self.connected and self.source == "emulator" and self.simulate_edge):
            return
        peer = self.transport.peer
        if peer.snapshot().state != WAITING:
            self.wait_since = None
            return
        now = self.clock()
        if self.wait_since is None:
            self.wait_since = now
        elif now - self.wait_since >= self.edge_delay_ms:
            peer.trigger(now)
            self.wait_since = None

    @QtCore.Slot()
    def pump(self):
        with self.box.lock:
            shutdown, disconnect, stop = self.box.shutdown, self.box.disconnect, self.box.stop
            cancel_save, self.box.cancel_save = self.box.cancel_save, None
            self.box.shutdown = self.box.disconnect = self.box.stop = False
            command, self.box.command = self.box.command, None
            if shutdown or disconnect:
                self.box.cancel_command(command)
            ack, self.box.capture_ack = self.box.capture_ack, None
            incomplete_ack, self.box.incomplete_ack = self.box.incomplete_ack, None
            self.box.executing = True
            self.box.control_executing = bool(command or shutdown or disconnect or stop
                                               or ack is not None or incomplete_ack is not None)
        try:
            if cancel_save is not None and self.recording:
                with self.box.lock:
                    self.recording.cancel_save(cancel_save)
            if shutdown:
                self.closing = True
                if self._disconnect():
                    if self.recording:
                        with self.box.lock:
                            self.recording.retire()
                    self.timer.stop()
                    self._publish(force=True, complete_tick=True)
                    self.finished.emit()
                    return
            elif disconnect:
                if ack is not None:
                    self.ack_capture = ack
                self._disconnect()
                if (incomplete_ack is not None and self.runtime is not None
                        and not (self.recording and self.recording.owns_runtime)
                        and incomplete_ack is self.runtime.incomplete_result):
                    self.runtime.take_incomplete()
            elif not self.closing:
                if stop:
                    self.staged_start = self.arm_ready = self.simulate_edge = False
                    self.wait_since = None
                    if self.recording and self.recording.owns_runtime:
                        with self.box.lock:
                            self.recording.stop()
                    elif self.runtime and self.connected:
                        s = self.runtime.snapshot()
                        if s.phase in (Phase.CAPS, Phase.READY, Phase.ARMED) and not s.stop_barrier:
                            self._events(self.runtime.stop())
                    if command and command[0] in ("start", "record"):
                        command = None
                if command:
                    kind, args = command
                    if kind == "connect":
                        self._connect(*args)
                    elif kind == "start":
                        if self.recording is not None:
                            state = self.recording.state
                            if self.recording.owns_runtime or state.saved_path is None or state.saving:
                                raise ValueError("Сначала завершите и сохраните сеанс записи")
                            self.recording.retire()
                            self.recording = None
                        self._events(self.runtime.configure(args[0]))
                        self.staged_start = True
                        self.simulate_edge = args[1] and self.source == "emulator"
                        self.wait_since = None
                    elif kind == "record":
                        settings, path, simulate_edge = args
                        with self.box.lock:
                            if self.box.barrier():
                                command = None
                            else:
                                if self.recording is None:
                                    self.recording = RecordingSessionAdapter(self.runtime,
                                        clock_ms=self.clock, spool_factory=self.spool_factory)
                                snapshot = self.runtime.snapshot()
                                identity = SessionIdentity(str(uuid4()), str(UUID(int=snapshot.session)),
                                    "simulator" if self.source == "emulator" else "device",
                                    str(snapshot.capabilities.firmware_build),
                                    f"draft-0.1@{snapshot.source_sha}", __version__)
                                if not self.recording.record(identity, settings, path):
                                    raise ValueError("Предыдущий сеанс записи ещё не сохранён")
                                self.simulate_edge = simulate_edge and self.source == "emulator"
                                self.wait_since = None
                    elif kind == "save_recording":
                        with self.box.lock:
                            if self.box.shutdown or self.box.disconnect:
                                self.box.cancel_command(command)
                            elif self.recording is None or not self.recording.save(args[0], operation=args[1]):
                                self.box.saved = SaveCompletion(args[1], error="save_rejected")
                                self.box.error = "Сеанс ещё не готов к сохранению"
                            else:
                                if cancel_save is args[1] or self.box.cancel_save is args[1]:
                                    self.recording.cancel_save(args[1])
                                if self.box.cancel_save is args[1]:
                                    self.box.cancel_save = None
                if self.recording and self.recording.owns_runtime:
                    # Display consumers never own the recorder's durable ACK.
                    ack = incomplete_ack = None
                if ack is not None:
                    if self.runtime is None or ack is not self.runtime.pending_capture:
                        raise ValueError("Запись больше не принадлежит текущему захвату")
                    self.ack_capture = ack
                if incomplete_ack is not None:
                    if self.runtime is None or incomplete_ack is not self.runtime.incomplete_result:
                        raise ValueError("Неполное окно больше не принадлежит текущему захвату")
                    self.runtime.take_incomplete()
                if self.runtime:
                    # Lock only nonblocking control transitions, never backend I/O.
                    with self.box.lock:
                        if self.arm_ready and not self.box.barrier():
                            self.arm_ready = False
                            events = self.runtime.arm()
                        else:
                            events = ()
                    self._events(events)
                    self._accept_capture()
                    self._recording_step()
                    with self.box.lock:
                        barrier = self.box.barrier()
                    if self.connected and not barrier:
                        self._edge()
                        self._events(self.runtime.pump())
                    if self.runtime.snapshot().phase is Phase.RECOVERY:
                        self.staged_start = self.arm_ready = self.simulate_edge = False
                        self._accept_capture()
                        if self.connected and not self.close_failed:
                            self._close()
        except Exception as exc:
            # Domain errors are bounded, human-readable messages. Never expose
            # arbitrary backend exception text, credentials, or device bytes.
            from .runtime import RuntimeError as AcquisitionError
            from .session import SessionError
            from .transport import TransportError
            if isinstance(exc, (AcquisitionError, SessionError, TransportError)):
                message = str(exc)
            elif type(exc) is ValueError:
                message = str(exc)
            else:
                message = "Ошибка рабочего потока захвата; требуется переподключение"
                if self.runtime:
                    self._disconnect()
            self.staged_start = self.arm_ready = False
            self._error(message)
        finally:
            self._publish(complete_tick=True)


class AcquisitionBridge(QtCore.QObject):
    """GUI-thread owner. Retain/display a capture BEFORE acknowledge(capture).

    Public methods are GUI-thread-only and nonblocking. False means the request
    was rejected locally; true means queued, not that the device acknowledged it.
    Keep this QObject and its QThread alive until finished. Never terminate a
    thread or block the GUI waiting for a driver. close_failed requires an
    explicit disconnect_source()/shutdown() retry.
    """
    state_changed = QtCore.Signal(object)
    capture_ready = QtCore.Signal(object)
    incomplete_ready = QtCore.Signal(object)
    error = QtCore.Signal(str)
    finished = QtCore.Signal()
    recording_changed = QtCore.Signal(object)
    recording_saved = QtCore.Signal(str)

    def __init__(self, parent=None, *, serial_factory=SerialTransport,
                 clock_ms=None, interval_ms=PUMP_INTERVAL_MS,
                 edge_delay_ms=SIMULATED_EDGE_DELAY_MS, spool_factory=SessionSpool.create):
        super().__init__(parent)
        if type(interval_ms) is not int or not 1 <= interval_ms <= 1000:
            raise ValueError("Invalid pump interval")
        if type(edge_delay_ms) is not int or not 1 <= edge_delay_ms <= 60000:
            raise ValueError("Invalid simulated-edge delay")
        self._box = _Mailbox()
        self._factory, self._clock = serial_factory, clock_ms or (lambda: time.monotonic_ns() // 1000000)
        self._interval, self._edge_delay = interval_ms, edge_delay_ms
        self._spool_factory = spool_factory
        self._thread = self._worker = None
        self._native_joined = True
        self._state = BridgeState()
        self._capture = self._incomplete = None
        self._capture_ack_sent = self._incomplete_ack_sent = False
        self._closed = False
        self._save_requested = None

    @property
    def state(self):
        return self._state

    @property
    def running(self):
        # QThread.finished/isRunning(False) can precede native thread cleanup.
        # Keep the owner live until a nonblocking join verifies that boundary.
        return self._thread is not None and not self._native_joined

    @property
    def active(self):
        with self._box.lock:
            return self._state.connected or bool(self._box.command or self._box.control_executing)

    @property
    def pending_capture(self):
        return self._capture

    @property
    def pending_incomplete(self):
        return self._incomplete

    def _owner(self):
        if QtCore.QThread.currentThread() != self.thread():
            raise RuntimeError("AcquisitionBridge methods belong to the GUI thread")

    def _launch(self):
        if self._thread is not None:
            return
        self._thread = QtCore.QThread(self)
        self._native_joined = False
        self._worker = _AcquisitionWorker(self._box, self._factory, self._clock,
                                         self._interval, self._edge_delay, self._spool_factory)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.initialize)
        self._worker.notification.connect(self._drain)
        self._worker.finished.connect(self._thread.quit, QtCore.Qt.ConnectionType.DirectConnection)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._finished)
        self._thread.start()

    def _command(self, kind, args):
        self._owner()
        with self._box.lock:
            if (self._closed or self._state.closing or self._box.command
                    or self._box.control_executing or self._box.barrier()):
                return False
            self._box.command = (kind, args)
        self._state = replace(self._state, busy=True,
                              start_pending=kind in ("start", "record"))
        self.state_changed.emit(self._state)
        self._launch()
        return True

    def connect_source(self, source, *, port="", reset_confirmed=False):
        self._owner()
        if source not in ("emulator", "device") or type(reset_confirmed) is not bool:
            return False
        if (self._state.connected or self._state.busy or self._state.close_failed
                or self._state.has_capture or self._state.has_incomplete):
            return False
        if source == "device" and (not isinstance(port, str) or not port.strip()):
            return False
        if source == "device" and self._state.reset_required and not reset_confirmed:
            self.error.emit("Подтвердите физический сброс прибора перед повторным подключением")
            return False
        return self._command("connect", (source, port, reset_confirmed))

    def start(self, settings, *, simulate_edge=True):
        self._owner()
        s = self._state.snapshot
        if (type(settings) is not Config or type(simulate_edge) is not bool
                or not self._state.connected or self._state.busy or s is None
                or s.phase is not Phase.READY or s.window_barrier
                or self._state.has_capture or self._state.has_incomplete
                or (self._state.recording is not None and
                    (self._state.recording.saved_path is None or self._state.recording.saving))):
            return False
        return self._command("start", (settings, simulate_edge))

    def record(self, settings, spool_path, *, simulate_edge=True):
        """Queue exclusive spool preparation before CONFIG/ARM; no disk I/O here."""
        self._owner()
        state = self._state
        s, recording = state.snapshot, state.recording
        if (type(settings) is not Config or type(simulate_edge) is not bool
                or not isinstance(spool_path, str) or not spool_path
                or not state.connected or state.busy or s is None
                or s.phase is not Phase.READY or s.window_barrier
                or state.has_capture or state.has_incomplete
                or (recording is not None and recording.saved_path is None)):
            return False
        return self._command("record", (settings, spool_path, simulate_edge))

    def save_recording(self, destination):
        """Save only the exact accepted terminal report; never overwrite a file."""
        self._owner()
        recording = self._state.recording
        if (recording is None or not recording.ready_to_save or self._state.busy
                or self._save_requested is not None
                or not isinstance(destination, str) or not destination):
            return False
        operation = object()
        self._save_requested = operation
        accepted = self._command("save_recording", (destination, operation))
        if not accepted and self._save_requested is operation:
            self._save_requested = None
        return accepted

    def cancel_recording_save(self):
        self._owner()
        operation = self._save_requested
        if operation is None or self._closed:
            return False
        queued = False
        with self._box.lock:
            command = self._box.command
            if command and command[0] == "save_recording" and command[1][1] is operation:
                self._box.cancel_command()
                queued = True
            else:
                self._box.cancel_save = operation
        if queued:
            # No worker state change would otherwise clear optimistic GUI busy.
            self._drain()
        return True

    def stop(self):
        self._owner()
        with self._box.lock:
            if self._closed or self._state.closing or not self.running or self._box.barrier():
                return False
            if (self._state.snapshot and self._state.snapshot.stop_barrier
                    and self._state.recording is None):
                return False
            self._box.stop = True
            if self._box.command and self._box.command[0] in ("start", "record"):
                self._box.command = None
        self._state = replace(self._state, busy=True, start_pending=False)
        self.state_changed.emit(self._state)
        return True

    def disconnect_source(self):
        self._owner()
        with self._box.lock:
            if self._closed or self._state.closing or not self.running or self._box.disconnect:
                return False
            self._box.disconnect = True
            self._box.cancel_command()
        self._state = replace(self._state, busy=True, start_pending=False)
        self.state_changed.emit(self._state)
        return True

    def acknowledge(self, capture):
        self._owner()
        if (self._state.recording is not None or capture is None
                or capture is not self._capture or self._capture_ack_sent):
            return False
        with self._box.lock:
            if self._closed or self._state.closing or self._box.shutdown or self._box.disconnect:
                return False
            self._box.capture_ack = capture
        self._capture_ack_sent = True
        return True

    def acknowledge_incomplete(self, result):
        self._owner()
        if (self._state.recording is not None or result is None
                or result is not self._incomplete or self._incomplete_ack_sent):
            return False
        with self._box.lock:
            if self._closed or self._state.closing or self._box.shutdown or self._box.disconnect:
                return False
            self._box.incomplete_ack = result
        self._incomplete_ack_sent = True
        return True

    def shutdown(self):
        self._owner()
        if self._closed:
            return False
        with self._box.lock:
            if self._box.shutdown or (self._state.closing and not self._state.close_failed):
                return False
            self._box.shutdown = True
            self._box.cancel_command()
            self._box.capture_ack = self._box.incomplete_ack = None
            # The old attempt failed; a newly accepted retry has no outcome yet.
            # Also update the coalesced snapshot so an older notification cannot
            # turn the new close request back into the previous failure.
            self._box.state = replace(self._box.state, closing=True, busy=True,
                                      start_pending=False, close_failed=False)
        self._state = replace(self._state, closing=True, busy=True, start_pending=False,
                              close_failed=False)
        self.state_changed.emit(self._state)
        if self._thread is None:
            # Bind callback lifetime to this GUI QObject, including the lazy
            # no-worker path. Never use a context-free bound Signal.emit here.
            QtCore.QTimer.singleShot(0, self, self._finished)
        return True

    @QtCore.Slot()
    def _drain(self):
        with self._box.lock:
            state = self._box.state
            capture, self._box.capture = self._box.capture, None
            incomplete, self._box.incomplete = self._box.incomplete, None
            error, self._box.error = self._box.error, None
            saved, self._box.saved = self._box.saved, None
            self._box.notified = False
            # Keep locally queued intent visible if this notification preceded it.
            if self._box.shutdown:
                state = replace(state, closing=True, busy=True, start_pending=False,
                                close_failed=False)
            elif self._box.barrier() or self._box.command or self._box.control_executing:
                state = replace(state, busy=True)
        if not state.has_capture and capture is None:
            self._capture = None
            self._capture_ack_sent = False
        if not state.has_incomplete and incomplete is None:
            self._incomplete = None
            self._incomplete_ack_sent = False
        previous_recording = self._state.recording
        self._state = state
        self.state_changed.emit(state)
        if state.recording != previous_recording:
            self.recording_changed.emit(state.recording)
        if saved is not None:
            if saved.operation is self._save_requested:
                self._save_requested = None
            if saved.path is not None:
                self.recording_saved.emit(saved.path)
        if capture is not None:
            self._capture, self._capture_ack_sent = capture, False
            self.capture_ready.emit(capture)
        if incomplete is not None:
            self._incomplete, self._incomplete_ack_sent = incomplete, False
            self.incomplete_ready.emit(incomplete)
        if error is not None:
            self.error.emit(error)

    @QtCore.Slot()
    def _finished(self):
        if self._closed:
            return
        if self._thread is not None and not self._thread.wait(0):
            # finished is emitted before native/TLS cleanup has necessarily
            # completed. Poll without blocking or destroying a live owner.
            QtCore.QTimer.singleShot(1, self, self._finished)
            return
        self._native_joined = True
        self._drain()
        self._closed = True
        self._state = replace(self._state, connected=False, busy=False, closing=True)
        self.state_changed.emit(self._state)
        self.finished.emit()

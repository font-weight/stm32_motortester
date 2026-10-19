"""Isolated streaming ownership, using an explicitly supplied bounded byte peer.

No COM, device discovery, executable entry point or synthetic source lives here.
The receiver is the only bridge owner. Disk and display workers never tick it.
Manual service/execute methods are the same roles used by the real threads, for
finite tests with explicit clocks; they are not a second acquisition path.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import secrets
import threading
import time
import uuid

from . import stream_endpoint as wire
from .stream_bridge import BridgeSnapshot, StreamBridge
from .stream_model import StreamBlock, StreamError, StreamIdentity
from .stream_recording import StreamLimits
from .stream_provenance import (ProvenanceError, admit_profile, admit_fake_diagnostic_declaration,
                                validate_physical_context)
from .stream_session import SessionLimits
from .stream_serial import SerialPeerStatus, SerialStreamPeer
from .stream_view import (DetectorStatus, Envelope, EventConfig, EventDetector,
                          EventEntry, Viewport, minmax_envelope)

RECENT_SAMPLES = 65536
EVENT_CAPACITY = 4096
POINT_BUDGET = 2048
DISPLAY_INTERVAL_MS = 100
# This is a poll target, not a Windows/USB real-time guarantee.
OWNER_POLL_SECONDS = 0.001


@dataclass(frozen=True, slots=True)
class ControllerStatus:
    revision: int = 0
    phase: str = "idle"
    path: str | None = None
    bridge: BridgeSnapshot | None = None
    config: EventConfig | None = None
    config_revision: int = 0
    command_pending: bool = False
    stop_pending: bool = False
    stop_requested: bool = False
    close_requested: bool = False
    worker_unresolved: bool = False
    can_preview: bool = True
    can_record: bool = True
    can_open: bool = True
    error: str | None = None
    display_error: str | None = None
    receiver_steps: int = 0
    max_service_gap_ms: int = 0
    validated_blocks: int = 0
    validated_samples: int = 0
    raw_available_start: int = 0
    ring_samples: int = 0
    detector_status: DetectorStatus | None = None
    display_pending: bool = False
    display_running: bool = False
    displays_built: int = 0
    displays_replaced: int = 0
    peak_raw_items: int = 0
    peak_raw_bytes: int = 0
    raw_item_limit: int = 256
    raw_byte_limit: int = 256 * 1024
    storage_raw_limit: int = 64 * 1024 * 1024
    serial: SerialPeerStatus | None = None
    serial_call: str | None = None
    serial_operation_id: int = 0
    serial_operation_generation: int = 0
    serial_unresolved: bool = False
    can_connect: bool = False
    can_disconnect: bool = False
    can_retry_close: bool = False


@dataclass(frozen=True, slots=True)
class _Command:
    kind: str
    path: str | None
    config: EventConfig


@dataclass(frozen=True, slots=True)
class _View:
    revision: int
    start: int | None = None
    stop: int | None = None
    follow: bool = True


@dataclass(frozen=True, slots=True)
class DisplayInput:
    """One bounded, immutable receiver copy; expensive work runs elsewhere."""
    revision: int
    sequence: int
    identity: StreamIdentity
    config: EventConfig
    config_revision: int
    first: int
    end: int
    raw_first: int
    raw: bytes
    events: tuple[EventEntry, ...]
    detector: DetectorStatus
    view: _View
    loss_samples: int | None
    tail_state: str
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DisplayResult:
    source: DisplayInput
    envelope: Envelope


@dataclass(frozen=True, slots=True)
class _RecentReader:
    identity: StreamIdentity
    first_sample_index: int
    end_sample_index: int
    raw: bytes

    def iter_raw(self, start_sample_index, end_sample_index, chunk_samples=8192):
        for at in range(start_sample_index, end_sample_index, chunk_samples):
            stop = min(at + chunk_samples, end_sample_index)
            lo, hi = at - self.first_sample_index, stop - self.first_sample_index
            yield StreamBlock(self.identity, at, self.raw[2 * lo:2 * hi])


def render_display(source: DisplayInput) -> DisplayResult:
    """Display-role only: bounded envelope; never a journal reader."""
    view = source.view
    width = RECENT_SAMPLES if view.start is None else view.stop - view.start
    if view.follow:
        stop = source.end
        start = max(source.raw_first, stop - width)
    else:
        start, stop = view.start, view.stop
    # Empty sources have status, no artificial [0, 1) waveform.
    if source.end <= source.first:
        raise ValueError("Empty source has no display")
    reader = _RecentReader(source.identity, source.raw_first, source.end, source.raw)
    envelope = minmax_envelope(reader, Viewport(start, stop), POINT_BUDGET)
    return DisplayResult(source, envelope)


class StreamController:
    """Reusable controller for one supplied, nonblocking byte-transport boundary.

    peer.read(maximum) returns at most maximum bytes; peer.write(bytes) returns
    its actual accepted prefix (or None for an unknown count). Both callbacks
    run ONLY on the receiver, must be bounded/nonblocking, and cannot call back
    into bridge ownership. This component cannot prove a physical FIFO/reset.

    Clean completed attempts may start a fresh HELLO with a new nonce. Any fault
    permanently disables restart on this controller. A separate isolated adapter
    and settled old workers are prerequisites outside this component for retry.
    """
    def __init__(self, peer, *, event_config: EventConfig, threaded=True,
                 session_limits=SessionLimits(), storage_limits=StreamLimits(),
                 clock=None, managed_peer=False, producer_profile=None, fake_diagnostic_declaration=None,
                 physical_diagnostic_context=None):
        if type(event_config) is not EventConfig:
            raise ValueError("Explicit HOST display EventConfig required")
        event_config.__post_init__()
        session_limits.__post_init__(); storage_limits.__post_init__()
        if type(managed_peer) is not bool or (managed_peer and type(peer) is not SerialStreamPeer):
            raise ValueError("Managed mode requires an explicit SerialStreamPeer")
        self._fake_diagnostic_declaration = (None if fake_diagnostic_declaration is None else
                                            admit_fake_diagnostic_declaration(fake_diagnostic_declaration))
        self._physical_diagnostic_context = (None if physical_diagnostic_context is None else
                                             validate_physical_context(physical_diagnostic_context))
        if self._physical_diagnostic_context is not None and (producer_profile is not None or
                fake_diagnostic_declaration is not None or not managed_peer or
                type(peer) is not SerialStreamPeer or peer.fake_backend_selected or peer._factory is None):
            raise ProvenanceError("Operational STR1 requires its exclusive managed backend seam")
        if self._fake_diagnostic_declaration is not None and (producer_profile is not None or
                not managed_peer or type(peer) is not SerialStreamPeer or not peer.fake_backend_selected):
            raise ProvenanceError("str1 требует только явный managed fake backend; physical activation запрещён")
        self._producer_profile = None if producer_profile is None else admit_profile(producer_profile)
        if self._producer_profile is not None and (managed_peer or type(peer) is SerialStreamPeer):
            raise ProvenanceError("Синтетический producer profile не разрешает serial/device activation")
        self._managed_peer = managed_peer
        self._serial_status = peer.initial_status if managed_peer else None
        self._serial_call = None
        self._serial_operation_id = self._serial_operation_generation = self._serial_fence_sequence = 0
        self._serial_fenced = managed_peer
        self._serial_close_pending = False
        self._serial_detach_notified = False
        self._peer = peer
        self._config = event_config
        self._limits, self._storage_limits = session_limits, storage_limits
        self._clock = clock or (lambda: time.monotonic_ns() // 1_000_000)
        self._iteration_now = self._observed_now = None
        self._threaded = bool(threaded)
        self._lock = threading.Lock()
        self._wake, self._disk_wake, self._display_wake = (threading.Event() for _ in range(3))
        self._finished = threading.Event()
        self._command = None
        self._ordinary_inflight = False
        self._stop_requested = self._close_requested = False
        self._intent_busy = False
        self._disk = None
        self._display_input = self._sink = None
        self._display_running = False
        self._display_error = None
        self._displays_built = self._displays_replaced = 0
        self._view = _View(0)
        self._status = ControllerStatus(config=event_config,
            raw_item_limit=session_limits.max_raw_items, raw_byte_limit=session_limits.max_raw_bytes,
            storage_raw_limit=storage_limits.max_raw_bytes, serial=self._serial_status,
            can_connect=managed_peer, can_preview=not managed_peer, can_record=not managed_peer)
        # Receiver-owned fields below. No other role mutates them.
        self._owner_id = None
        self._bridge = None
        self._pending_action = None
        self._negotiation = None
        self._revision = self._config_revision = 0
        self._path = self._error = None
        self._faulted = False
        self._stop_issued = False
        self._detector = None
        self._ring = bytearray(2 * RECENT_SAMPLES)
        self._ring_end = self._ring_count = self._source_first = 0
        self._sequence = self._blocks = self._samples = self._steps = 0
        self._max_gap = self._peak_items = self._peak_bytes = 0
        self._previous_now = None
        self._last_display_ms = -DISPLAY_INTERVAL_MS
        self._dirty = False
        self._last_view = self._last_sink = None
        self._threads = ()
        if self._threaded:
            self._threads = tuple(threading.Thread(target=target, name=name, daemon=True)
                for target, name in ((self._receive_loop, "stream-receiver"),
                                     (self._disk_loop, "stream-disk"),
                                     (self._display_loop, "stream-display")))
            for thread in self._threads:
                thread.start()

    def _submit(self, kind, path, config):
        config = self._config if config is None else config
        if type(config) is not EventConfig:
            raise ValueError("Exact EventConfig required")
        config.__post_init__()
        with self._lock:
            status = self._status
            allowed = (status.can_connect if kind == "connect" else
                       status.can_preview if kind == "preview" else status.can_record)
            if (not allowed or self._ordinary_inflight or self._command is not None or self._stop_requested or
                    self._close_requested or
                    (self._managed_peer and kind != "connect" and self._serial_fenced) or
                    (kind == "connect" and self._serial_close_pending)):
                return False
            self._command = _Command(kind, path, config)
            self._ordinary_inflight = True
            self._intent_busy = True
            self._status = replace(status, can_preview=False, can_record=False, can_open=False)
        self._wake.set()
        return True

    def connect(self):
        """Explicit managed open only; never negotiates, previews or records."""
        return self._managed_peer and self._submit("connect", None, None)

    def disconnect(self):
        """Reserved immediate admission fence; serial close runs on receiver."""
        with self._lock:
            if not self._managed_peer or self._close_requested:
                return False
            if self._serial_fenced and not (self._ordinary_inflight or self._serial_call == "open"):
                return self._serial_close_pending
            self._serial_fenced = True
            self._serial_fence_sequence += 1
            self._serial_close_pending = True
            self._command = self._display_input = self._sink = None
        self._wake.set()
        return True

    def retry_close(self):
        """Cleanup-only retry remains available after permanent close()."""
        with self._lock:
            if (not self._managed_peer or self._serial_status.state != "close_failed"
                    or self._serial_call is not None or self._serial_close_pending):
                return False
            self._serial_close_pending = True
        self._wake.set()
        return True

    def preview(self, *, event_config=None):
        return self._submit("preview", None, event_config)

    def record(self, path, *, event_config=None):
        # Only the selected pathname crosses the GUI boundary. No exists/stat,
        # mkdir/open or overwrite preflight here or anywhere in the GUI.
        if type(path) is not str or not path or len(path) > 4096 or "\0" in path:
            raise ValueError("A selected pathname is required")
        return self._submit("record", path, event_config)

    def stop(self):
        with self._lock:
            if self._close_requested:
                return False
            self._stop_requested = True
        self._wake.set()
        return True

    def close(self):
        """Immediate public fence. Never joins or cancels a blocking OS call."""
        with self._lock:
            if self._managed_peer and not self._close_requested:
                self._serial_fenced = True
                self._serial_fence_sequence += 1
                # A known failed-close handle requires retry_close, not a
                # repeated shutdown call or an automatic cleanup loop.
                if self._serial_status.state != "close_failed":
                    self._serial_close_pending = True
            self._close_requested = True
            self._command = self._display_input = self._sink = None
        self._wake.set(); self._disk_wake.set(); self._display_wake.set()

    def join(self, timeout=0):
        """Non-GUI cleanup observation; False explicitly means unresolved work."""
        deadline = time.monotonic() + max(0, timeout)
        for thread in self._threads:
            thread.join(max(0, deadline - time.monotonic()))
        return self._finished.is_set() and not any(t.is_alive() for t in self._threads)

    def snapshot(self):
        with self._lock:
            return replace(self._status, command_pending=self._command is not None,
                stop_pending=self._stop_requested, close_requested=self._close_requested,
                can_preview=self._status.can_preview and not self._intent_busy and not self._close_requested and not self._serial_fenced,
                can_record=self._status.can_record and not self._ordinary_inflight and not self._close_requested and not self._serial_fenced,
                can_open=self._status.can_open and not self._intent_busy and not self._close_requested,
                display_pending=self._display_input is not None,
                display_running=self._display_running, display_error=self._display_error,
                displays_built=self._displays_built, displays_replaced=self._displays_replaced,
                serial=self._serial_status, serial_call=self._serial_call,
                serial_operation_id=self._serial_operation_id,
                serial_operation_generation=self._serial_operation_generation,
                serial_unresolved=bool(self._managed_peer and (self._serial_call is not None or
                    not self._serial_status.handle_settled)),
                can_connect=self._status.can_connect and not self._ordinary_inflight and
                    not self._close_requested and not self._serial_close_pending,
                can_disconnect=self._managed_peer and not self._serial_fenced and not self._close_requested,
                can_retry_close=self._managed_peer and self._serial_status.state == "close_failed" and
                    self._serial_call is None and not self._serial_close_pending)

    def bind_display(self, revision, sink):
        """Install a source-bound, off-GUI immutable-result consumer."""
        with self._lock:
            if self._close_requested or revision != self._status.revision:
                return False
            self._sink = (revision, sink)
            self._view = _View(revision)
        self._wake.set()
        return True

    def request_view(self, revision, start, stop, *, follow=False):
        Viewport(start, stop)  # inexpensive scalar bounds; no envelope validation
        with self._lock:
            if self._close_requested or revision != self._status.revision:
                return False
            self._view = _View(revision, start, stop, bool(follow))
        self._wake.set()
        return True

    def claim_open(self):
        """Fence live delivery before GUI delegates Open to its strict reader."""
        with self._lock:
            if (not self._status.can_open or self._intent_busy or self._command is not None
                    or self._close_requested):
                return False
            self._sink = self._display_input = None
        return True

    def _owner(self, now_ms):
        current = threading.get_ident()
        if self._owner_id is None:
            self._owner_id = current
        if current != self._owner_id:
            raise RuntimeError("Single receiver owner required")
        if type(now_ms) is not int or now_ms < 0 or (self._previous_now is not None and now_ms < self._previous_now):
            raise ValueError("Integral monotonic milliseconds required")
        if self._previous_now is not None:
            self._max_gap = max(self._max_gap, now_ms - self._previous_now)
        self._previous_now = now_ms
        self._steps += 1
        self._iteration_now = now_ms

    def _current_time(self):
        """Fresh owner observation, never a disk/display completion timestamp.

        Every mode samples the same authoritative clock, including after
        callbacks. Deterministic callers must explicitly inject their clock;
        service's entry timestamp never freezes time within an iteration.
        """
        now = self._clock()
        if (type(now) is not int or now < self._iteration_now or
                (self._observed_now is not None and now < self._observed_now)):
            raise ValueError("Current clock must be integral and monotonic")
        self._observed_now = now
        return now

    def _serial_operation(self, kind, *args):
        # Announce ownership before entering an OS callback. Snapshot can show
        # unresolved work without calling the transport or blocking on this lock.
        with self._lock:
            self._serial_call = kind
            self._serial_operation_id += 1
            self._serial_operation_generation = self._serial_status.generation + (kind == "open")
        self._current_time()
        try:
            return getattr(self._peer, kind)(*args)
        finally:
            self._current_time()
            status = self._peer.snapshot()
            with self._lock:
                self._serial_status = status
                self._serial_call = None

    def _service_disconnected(self):
        bridge = self._bridge
        if bridge is not None:
            snap = bridge.snapshot()
            if not snap.closed:
                if self._close_requested:
                    bridge.close(self._current_time())
                elif not self._serial_detach_notified:
                    self._serial_detach_notified = True
                    # Orderly completed preview has no writer and needs no
                    # invented fault. Active disconnect is a real source loss;
                    # allow the accepted bounded failure drain/journal finish.
                    if not self._restartable(snap):
                        now = self._current_time()
                        self._observe(bridge.transport_fault(now), now)
            bridge.accept_storage_completion(self._current_time())
            if not bridge.snapshot().closed:
                bridge.dispatch_storage(self._current_time())
                snap = bridge.snapshot()
                if ((snap.storage is None and snap.endpoint_state in
                        (wire.State.NEEDS_HELLO, wire.State.RESET_REQUIRED) and not self._restartable(snap)) or
                        (snap.session is not None and snap.session.finalized and snap.storage is not None
                         and snap.storage.closed and snap.storage.returned and not snap.storage.failure
                         and not snap.mailbox.result_pending)):
                    bridge.close(self._current_time())
            with self._lock:
                self._disk = bridge.disk_executor
            self._disk_wake.set()
        with self._lock:
            attempt_close = self._serial_close_pending
            self._serial_close_pending = False
        if attempt_close:
            try:
                self._serial_operation("close")
                if self._error and self._error.startswith("Serial close unconfirmed:"):
                    self._error = None
            except Exception as exc:
                self._error = "Serial close unconfirmed: " + type(exc).__name__
        self._publish(self._current_time())

    def _connect_serial(self):
        snap = None if self._bridge is None else self._bridge.snapshot()
        with self._lock:
            fence = self._serial_fence_sequence
            display_busy = self._display_running or self._display_input is not None
        disk_busy = bool(snap and snap.storage and (not snap.storage.closed or
            snap.mailbox.running or snap.mailbox.result_pending or snap.mailbox.operation_id is not None or
            (snap.mailbox.close_requested and snap.mailbox.close_acknowledged is None)))
        if (display_busy or disk_busy or not self._serial_status.handle_settled or
                (snap is not None and not snap.closed and not self._restartable(snap))):
            self._error = "Connect refused: prior ownership is still unsettled"
            self._publish(self._current_time())
            return
        self._error = None
        try:
            self._serial_operation("open")
        except Exception as exc:
            self._error = "Serial open failed: " + type(exc).__name__
            if self._serial_status.state == "close_failed":
                with self._lock:
                    self._serial_close_pending = False
        with self._lock:
            stale = self._close_requested or fence != self._serial_fence_sequence
            if not stale and self._serial_status.state == "open":
                self._serial_fenced = False
                self._serial_detach_notified = False
        if stale:
            self._service_disconnected()
        else:
            self._publish(self._current_time())

    def _retire_late_callback(self):
        if self._managed_peer and self._serial_fenced:
            self._service_disconnected()
            return True
        return False

    def _serial_io_ready(self):
        return not self._managed_peer or (not self._serial_fenced and self._serial_status.state == "open")

    def _begin(self, command, now):
        if self._bridge is not None:
            prior = self._bridge.snapshot()
            if prior.mailbox is not None and (prior.mailbox.close_acknowledged is not True or
                    prior.mailbox.result_pending or prior.mailbox.running):
                raise StreamError("Previous disk owner has not been retired")
            # Preview-only has no disk resource. Replacement is permitted only
            # after its actual END + matching ACK and no outstanding request.
        self._revision += 1
        self._config_revision += 1
        self._config = command.config
        self._path = command.path
        self._error = None
        self._stop_issued = False
        self._bridge = StreamBridge(uuid.uuid4().hex, session_limits=self._limits,
                                    storage_limits=self._storage_limits,
                                    producer_profile=self._producer_profile,
                                    fake_diagnostic_declaration=self._fake_diagnostic_declaration,
                                    physical_diagnostic_context=self._physical_diagnostic_context)
        self._pending_action = command
        self._negotiation = "hello"
        self._detector = EventDetector(self._config, EVENT_CAPACITY, retention="recent")
        self._ring_end = self._ring_count = self._source_first = 0
        self._blocks = self._samples = self._sequence = 0
        self._last_display_ms = now - DISPLAY_INTERVAL_MS
        self._dirty = False
        with self._lock:
            self._view = _View(self._revision)
            self._sink = self._display_input = None
        nonce = secrets.randbits(64) or 1
        now = self._current_time()
        self._observe(self._bridge.hello(nonce, now), now)

    def _command_action(self, command, now):
        previous = None if self._bridge is None else self._bridge.snapshot()
        if self._restartable(previous):
            self._begin(command, now)
        elif command.kind == "record" and self._path is None and command.config == self._config:
            self._bridge.record(command.path, uuid.uuid4().hex, self._current_time())
            self._path = command.path
        else:
            raise StreamError("Attempt active, configuration changed or restart blocked")

    def _observe(self, events, now):
        for event in events:
            if isinstance(event, wire.Negotiated):
                if event.capabilities is None and self._negotiation == "hello":
                    self._negotiation = "caps"
                    now = self._current_time()
                    self._observe(self._bridge.get_caps(now), now)
                elif event.capabilities is not None and self._negotiation == "caps":
                    self._negotiation = None
                    command, self._pending_action = self._pending_action, None
                    if command is not None:
                        try:
                            if command.kind == "preview":
                                self._bridge.preview(self._current_time())
                            else:
                                self._bridge.record(command.path, uuid.uuid4().hex, self._current_time())
                        except ProvenanceError as exc:
                            # No generation/writer/START was reserved. Keep the
                            # admission cause visible and fence this controller.
                            self._error = self._error or str(exc)
                            self._faulted = True
                            self._observe(self._bridge.transport_fault(self._current_time()), now)
            elif isinstance(event, wire.Started):
                self._source_first = self._ring_end = event.first_index
            elif isinstance(event, wire.ValidatedData):
                identity = self._bridge.snapshot().identity
                block = StreamBlock(identity, event.block.first_index, event.block.raw_samples)
                # Always raw first, regardless of disk/display admission or drops.
                if self._detector.status.outcome == "running":
                    self._detector.feed(block)
                self._blocks += 1
                self._samples += block.sample_count
                position = (block.first_sample_index % RECENT_SAMPLES) * 2
                raw = block.raw_u16le
                first = min(len(raw), len(self._ring) - position)
                self._ring[position:position + first] = raw[:first]
                self._ring[:len(raw) - first] = raw[first:]
                self._ring_end = block.end_sample_index
                self._ring_count = min(RECENT_SAMPLES, self._ring_count + block.sample_count)
                self._dirty = True
            elif isinstance(event, wire.ValidatedEnd):
                reason = "source_fault" if event.terminal.reason or event.terminal.flags else "stopped"
                self._detector.finish(reason)
                self._dirty = True
            elif isinstance(event, (wire.Fault, wire.RecoveryRequired, wire.ResetRequired)):
                self._faulted = True
                if isinstance(event, (wire.RecoveryRequired, wire.ResetRequired)) and self._detector is not None:
                    self._detector.finish("unknown_loss")
                    self._dirty = True

    def _copy_display(self, now, snap):
        with self._lock:
            view, sink = self._view, self._sink
        if (sink is None or sink[0] != self._revision or self._ring_count == 0 or
                now - self._last_display_ms < DISPLAY_INTERVAL_MS or
                (not self._dirty and view == self._last_view and sink == self._last_sink)):
            return
        raw_first = self._ring_end - self._ring_count
        position = (raw_first % RECENT_SAMPLES) * 2
        count = self._ring_count * 2
        # Bounded memcpy + shallow immutable metadata copy, no rendering/validation.
        raw = bytes(self._ring[position:position + count])
        if len(raw) < count:
            raw += bytes(self._ring[:count - len(raw)])
        self._sequence += 1
        terminal = snap.source_terminal
        loss = None if terminal is None or terminal.terminal.produced_end is None else terminal.terminal.produced_end - terminal.terminal.prefix_end
        flags = () if snap.endpoint_first_fault is None else (snap.endpoint_first_fault.code,)
        source = DisplayInput(self._revision, self._sequence, snap.identity, self._config,
            self._config_revision, self._source_first, self._ring_end, raw_first, raw,
            self._detector.retained_events, self._detector.status, view, loss,
            "reported_stopped" if terminal is not None else "unknown" if self._faulted else "running", flags)
        with self._lock:
            if self._close_requested or self._sink != sink:
                return
            if self._display_input is not None:
                self._displays_replaced += 1
            self._display_input = (source, sink)
        self._last_display_ms = now
        self._last_view, self._last_sink = view, sink
        self._dirty = False
        self._display_wake.set()

    def _restartable(self, snap):
        if snap is None:
            return True
        session = snap.session
        if (self._faulted or snap.endpoint_first_fault or snap.late_fault
                or snap.pending_start or snap.pending_stop):
            return False
        if session is None:
            return self._stop_issued and not self._negotiation
        if session.first_failure:
            return False
        wire_clean = bool(session.source_end and session.stop_acknowledgement and
                          session.stop_acknowledgement.quiescence_reported)
        never_started = not session.source_started and self._stop_issued and not self._negotiation
        if self._path is None:
            return wire_clean
        return bool((wire_clean or never_started) and session.finalized and
            not session.finalization_unresolved and snap.storage and snap.storage.returned and
            snap.storage.closed and not snap.storage.failure and snap.mailbox and
            snap.mailbox.close_acknowledged is True and not snap.mailbox.result_pending and
            not snap.mailbox.running)

    def _publish(self, now):
        bridge = self._bridge
        snap = None if bridge is None else bridge.snapshot()
        if snap is not None and not self._close_requested:
            self._copy_display(now, snap)
            # Even the bounded owner-side raw copy can consume time. Display
            # progress is never evidence that receive deadlines were serviced.
            if not snap.closed:
                now = self._current_time()
                self._observe(bridge.tick(now), now)
                snap = bridge.snapshot()
        session = None if snap is None else snap.session
        storage = None if snap is None else snap.storage
        mailbox = None if snap is None else snap.mailbox
        if snap is not None and (snap.endpoint_first_fault or (session and session.first_failure) or snap.late_fault):
            self._faulted = True
        unresolved = bool(mailbox and (mailbox.running or mailbox.result_pending or
            mailbox.operation_id is not None or (mailbox.close_requested and mailbox.close_acknowledged is None)))
        wire_clean = bool(session and session.source_end and session.stop_acknowledgement and
            session.stop_acknowledgement.quiescence_reported and not session.first_failure and
            not snap.endpoint_first_fault and not snap.pending_start and not snap.pending_stop)
        saved_clean = wire_clean and bool(session.clean_completion_reported and storage and storage.returned and storage.closed and not storage.failure)
        clean = bool(wire_clean if self._path is None else saved_clean)
        settled = bool(storage is None or (storage.closed and not unresolved))
        source_done = bool(snap and (snap.closed or snap.endpoint_state in
            (wire.State.NEEDS_HELLO, wire.State.RESET_REQUIRED) or
            (self._stop_issued and not self._negotiation and not snap.pending_start and
             not snap.pending_stop and (session is None or not session.source_started)) or
            (session is not None and not session.source_started and session.admission_fenced and
             snap.endpoint_state == wire.State.STOPPED and not snap.pending_start and not snap.pending_stop)))
        finished = bool(source_done and settled)
        if session:
            self._peak_items = max(self._peak_items, session.raw_items_held)
            self._peak_bytes = max(self._peak_bytes, session.raw_bytes_held)
        if self._close_requested:
            phase = "closing" if unresolved else "closed"
        elif self._error:
            phase = "error"
        elif self._faulted:
            phase = "fault; writer unresolved" if unresolved else "fault"
        elif clean and settled:
            phase = "saved" if self._path else "preview stopped"
        elif self._negotiation:
            phase = "negotiating"
        elif session is None:
            phase = "idle" if bridge is None else "stopped before START"
        elif session.stop_requested or self._stop_issued:
            phase = "draining" if not settled or not source_done else "stopped without R"
        elif self._path and session.selected_start_sample_index is not None:
            phase = "recording"
        elif self._path:
            phase = "waiting for boundary" if storage and storage.operation == "prepare" and storage.returned and not mailbox.result_pending else "preparing"
        else:
            phase = "preview" if session.source_started else "starting preview"
        restart = self._restartable(snap)
        can_record = restart or bool(not self._faulted and session and session.source_started and
            self._path is None and not session.stop_requested and not session.admission_fenced)
        can_open = bridge is None or finished or bool(session and session.finalized and settled and source_done)
        with self._lock:
            display_settled = not self._display_running and self._display_input is None
            serial_call = self._serial_call
        serial_settled = not self._managed_peer or (self._serial_status.handle_settled and serial_call is None)
        can_connect = bool(self._managed_peer and serial_settled and settled and display_settled and
            (snap is None or snap.closed or restart) and not self._close_requested)
        if self._managed_peer:
            restart = restart and self._serial_io_ready()
            can_record = can_record and self._serial_io_ready()
            if self._close_requested:
                phase = "closed" if serial_settled and not unresolved and display_settled else "closing"
            elif self._serial_fenced:
                phase = "serial close unconfirmed" if not serial_settled else "disconnected"
            elif snap is None:
                phase = "connected"
        status = ControllerStatus(self._revision, phase, self._path, snap, self._config,
            self._config_revision, stop_requested=self._stop_issued, worker_unresolved=unresolved, can_preview=restart,
            can_record=can_record, can_open=can_open, error=self._error,
            receiver_steps=self._steps, max_service_gap_ms=self._max_gap,
            validated_blocks=self._blocks, validated_samples=self._samples,
            raw_available_start=self._ring_end - self._ring_count, ring_samples=self._ring_count,
            detector_status=None if self._detector is None else self._detector.status,
            peak_raw_items=self._peak_items, peak_raw_bytes=self._peak_bytes,
            raw_item_limit=self._limits.max_raw_items, raw_byte_limit=self._limits.max_raw_bytes,
            storage_raw_limit=self._storage_limits.max_raw_bytes,
            can_connect=can_connect, serial=self._serial_status)
        with self._lock:
            self._status = status
            if self._command is None:
                self._ordinary_inflight = False
            if finished or (bridge is None and self._command is None):
                self._intent_busy = False
        if self._close_requested and not unresolved and serial_settled and display_settled:
            self._finished.set()
            self._disk_wake.set(); self._display_wake.set()

    def service(self, now_ms):
        """One receiver iteration; supplied clocks remain live within the call.

        now_ms is an entry observation in the same clock domain, not a clock
        override. Deterministic callers inject a clock even in manual mode.
        The sole receiver must call this method.
        """
        self._owner(now_ms)
        with self._lock:
            close, stop = self._close_requested, self._stop_requested
            command, self._command = self._command, None
            self._stop_requested = False
        bridge = self._bridge
        if self._managed_peer:
            if close or self._serial_close_pending or (self._serial_fenced and
                    (command is None or command.kind != "connect")):
                self._service_disconnected()
                return
            if command is not None and command.kind == "connect":
                self._connect_serial()
                return
        if close:
            if bridge is not None:
                bridge.close(self._current_time())
                bridge.accept_storage_completion(self._current_time())
                self._disk_wake.set()
            self._publish(self._current_time())
            return
        # Stop wins over an ordinary command still waiting for owner acceptance.
        if stop:
            self._pending_action = None
            if bridge is not None and bridge.snapshot().session is not None and not self._stop_issued:
                snap = bridge.snapshot()
                if not snap.closed and not snap.session.finalization_started and not snap.session.stop_requested:
                    bridge.stop(self._current_time())
            self._stop_issued = True
            command = None
        if command is not None:
            try:
                self._command_action(command, self._current_time())
            except (ValueError, RuntimeError) as exc:
                self._error = str(exc)
        bridge = self._bridge
        if bridge is None:
            self._publish(self._current_time())
            return
        if bridge.snapshot().closed:
            bridge.accept_storage_completion(self._current_time())
            self._disk_wake.set()
            self._publish(self._current_time())
            return
        now = self._current_time()
        self._observe(bridge.tick(now), now)
        bridge.accept_storage_completion(self._current_time())
        now = self._current_time()  # completion handling can itself consume time
        offers = bridge.next_write(now) if self._serial_io_ready() else ()
        self._observe(offers, now)
        for offer in offers:
            if isinstance(offer, wire.WriteOffer):
                try:
                    accepted = (self._serial_operation("write", offer.data) if self._managed_peer
                                else self._peer.write(offer.data))
                except Exception:
                    # The accepted prefix is unknown, but this exact offer's
                    # operation/request is known. Never guess read causality.
                    accepted = None
                now = self._current_time()
                self._observe(bridge.observe_write(offer.token, accepted, now), now)
                if self._retire_late_callback():
                    return
        # Tick before the next callback as well as after it: previous owner work
        # must not buy extra lifetime for a DATA/request/partial-frame deadline.
        now = self._current_time()
        self._observe(bridge.tick(now), now)
        try:
            data = ((self._serial_operation("read", wire.MAX_INPUT_BYTES) if self._managed_peer
                     else self._peer.read(wire.MAX_INPUT_BYTES)) if self._serial_io_ready() else b"")
            if type(data) is not bytes or len(data) > wire.MAX_INPUT_BYTES:
                raise ValueError("Byte peer exceeded bounded read contract")
        except Exception:
            now = self._current_time()
            self._observe(bridge.transport_fault(now), now)
            if self._retire_late_callback():
                return
        else:
            now = self._current_time()
            if self._retire_late_callback():
                return
            if data:
                self._observe(bridge.receive(data, now), now)
            else:
                self._observe(bridge.tick(now), now)
        # In particular, a detector/raw batch may have elapsed a new deadline.
        bridge.dispatch_storage(self._current_time())
        with self._lock:
            self._disk = bridge.disk_executor
        self._disk_wake.set()
        snap = bridge.snapshot()
        if (snap.session is not None and snap.session.finalized and
                snap.storage is not None and snap.storage.closed and snap.storage.returned and
                not snap.storage.failure and not snap.mailbox.result_pending):
            bridge.close(self._current_time())
        self._publish(self._current_time())

    def execute_disk_once(self):
        """Disk role: the only storage call is the real executor.execute_one."""
        with self._lock:
            disk = self._disk
        result = False if disk is None else disk.execute_one()
        if result:
            self._wake.set()
        return result

    def execute_display_once(self):
        with self._lock:
            if self._display_running or self._display_input is None or self._close_requested:
                return False
            item, self._display_input = self._display_input, None
            self._display_running = True
        source, sink = item
        try:
            result = render_display(source)
            with self._lock:
                current = not self._close_requested and self._sink == sink
            if current:
                # A sink must itself fence its source (e.g. viewer LiveHandle).
                # Never call a callback while holding a controller mutex.
                sink[1](result)
            with self._lock:
                self._displays_built += 1
        except Exception as exc:
            with self._lock:
                if self._display_error is None:
                    self._display_error = "Display failed: " + type(exc).__name__
        finally:
            with self._lock:
                self._display_running = False
            self._wake.set()
        return True

    def _receive_loop(self):
        try:
            while not self._finished.is_set():
                self._wake.clear()
                self.service(self._clock())
                self._wake.wait(OWNER_POLL_SECONDS)
        except Exception as exc:
            self._error = "Receiver failed: " + type(exc).__name__
            self.close()
            # Keep ownership alive to settle a blocked real writer; never hide it.
            while not self._finished.is_set():
                self._wake.clear()
                self.service(max(self._previous_now or 0, self._clock()))
                self._wake.wait(OWNER_POLL_SECONDS)

    def _disk_loop(self):
        while not self._finished.is_set():
            self._disk_wake.wait()
            self._disk_wake.clear()
            self.execute_disk_once()

    def _display_loop(self):
        while not self._finished.is_set():
            self._display_wake.wait()
            self._display_wake.clear()
            self.execute_display_once()

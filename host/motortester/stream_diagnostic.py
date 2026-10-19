"""Opt-in, volatile callback transcript around the accepted controller.

The legacy public DiagnosticRun stays fake-only. The explicit invocation's
separate physical/test core shares its supervisor and owned export. Injected
Python is a caller contract, not a sandbox; there is no new wire parser.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from os import fsync as _fsync
from pathlib import Path
import platform
import threading
import time
import uuid

from .stream_controller import StreamController
from .stream_recording import StreamLimits
from .stream_serial import SerialSettings, SerialStreamPeer
from .stream_view import EventConfig
from .stream_provenance import (admit_fake_diagnostic_declaration, STR1_DIAGNOSTIC_CAPS,
                                STR1_DIAGNOSTIC_DECLARATION, PhysicalDiagnosticContext,
                                validate_physical_context, physical_context_identity)

BASE_COMMIT = "db124e280ca020dd6af8356eadd2fdd45a7f9ae2"
BASE_TREE = "63a88e0b15b63da601642ba87dacdb7e612d9d66"
FORMAT = "motortester-fake-callback-trace-1"
PHYSICAL_FORMAT = "motortester-str1-physical-callback-trace-1"
MAX_CALLBACKS, MAX_RAW, MAX_SNAPSHOTS = 16384, 4 * 1024 * 1024, 128
MAX_FILE, MAX_LINE, MAX_METADATA = 32 * 1024 * 1024, 32768, 8192
MAX_NS = (1 << 63) - 1
MODULES = ("stream_diagnostic.py", "stream_controller.py", "stream_serial.py",
           "stream_bridge.py", "stream_endpoint.py", "stream_wire.py",
           "stream_session.py", "stream_recording.py", "stream_provenance.py",
           "../tests/test_stream_controller.py", "../tests/test_stream_serial.py",
           "../tests/test_stream_diagnostic.py")
INVOCATION_MODULES = MODULES + ("stream_diagnostic_invocation.py", "../tests/test_stream_diagnostic_invocation.py")
PHYSICAL_MODULES = INVOCATION_MODULES + ("transport.py",)
_open_file = open


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("bounded integer required")


def _text(value, maximum):
    if (type(value) is not str or not 1 <= len(value) <= maximum or
            any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError("bounded text required")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _error(exc):
    # Never retain arbitrary exception messages or repr of backend results.
    return type(exc).__name__[:64]


@dataclass(frozen=True, slots=True)
class DiagnosticLimits:
    max_callbacks: int = MAX_CALLBACKS
    max_raw_bytes: int = MAX_RAW
    max_snapshots: int = MAX_SNAPSHOTS
    max_file_bytes: int = MAX_FILE
    connect_ms: int = 2000
    run_ms: int = 1000
    drain_ms: int = 4500
    close_ms: int = 1000

    def __post_init__(self):
        for name, upper in (("max_callbacks", MAX_CALLBACKS), ("max_raw_bytes", MAX_RAW),
                            ("max_snapshots", MAX_SNAPSHOTS), ("max_file_bytes", MAX_FILE),
                            ("connect_ms", 2000), ("run_ms", 10000),
                            ("drain_ms", 4500), ("close_ms", 1000)):
            _integer(getattr(self, name), 1, upper)


@dataclass(frozen=True, slots=True)
class CallbackRecord:
    callback_id: int
    kind: str
    begin_ns: int | None
    return_ns: int | None = None
    offered: bytes = b""
    read_maximum: int | None = None
    received: bytes = b""
    actual_rx_length: int | None = None
    accepted_count: int | None = None
    outcome: str = "pending"
    error_type: str | None = None

    def payload(self):
        result = asdict(self)
        result["offered"] = self.offered.hex()
        result["received"] = self.received.hex()
        return result


@dataclass(frozen=True, slots=True)
class _FrozenMap:
    items: tuple


def _freeze_tree(value):
    if type(value) is dict:
        return _FrozenMap(tuple((key, _freeze_tree(item)) for key, item in value.items()))
    if type(value) in (tuple, list):
        return tuple(_freeze_tree(item) for item in value)
    return value


def _thaw(value):
    if type(value) is _FrozenMap:
        return {key: _thaw(item) for key, item in value.items}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Immutable as-of evidence; JSON accessors serialize on their caller.

    The supervisor only freezes bounded scalar copies. Export serialization and
    source-file hashing belong to the separate report owner, after capture.
    """
    run_id: str
    _metadata: _FrozenMap
    callbacks: tuple[CallbackRecord, ...]
    _snapshots: tuple[_FrozenMap, ...]
    _final: _FrozenMap

    @property
    def metadata(self):
        return _json(_thaw(self._metadata))

    @property
    def snapshots(self):
        return tuple(_json(_thaw(value)) for value in self._snapshots)

    @property
    def final(self):
        return _json(_thaw(self._final))


@dataclass(frozen=True, slots=True)
class ExportStatus:
    state: str = "not_started"
    fsync_returned: bool = False
    readback_verified: bool = False
    complete_receipt: bool = False
    error: str | None = None
    owner_settled: bool = False


@dataclass(frozen=True, slots=True)
class DiagnosticStatus:
    controller: object
    trace_error: str | None
    dropped_callbacks: int
    run_error: str | None
    collecting: bool
    export: ExportStatus


@dataclass(frozen=True, slots=True)
class ReadReport:
    """Verified records are canonical JSON; complete-file hashes use actual bytes."""
    records: tuple[bytes, ...]
    complete: bool
    error: str | None
    run_id: str | None
    summary: bytes | None = None
    file_bytes: int | None = None
    file_sha256: str | None = None


class _BackendProxy:
    def __init__(self, backend, run):
        self._backend, self._run = backend, run

    @property
    def is_open(self):
        return self._backend.is_open

    @property
    def port(self):
        return self._backend.port

    @port.setter
    def port(self, value):
        self._backend.port = value

    def open(self):
        if self._run._physical_context is not None:
            # Transport already owns this proxy. A setup exception therefore
            # retains its ordinary cleanup/handle-settlement responsibility.
            def request_dtr():
                if self._backend.is_open is not False:
                    raise ValueError("DTR request requires a closed backend")
                self._backend.dtr = True
            self._run._callback("dtr", request_dtr)
        return self._run._callback("open", self._backend.open)

    def read(self, maximum):
        return self._run._callback("read", lambda: self._backend.read(maximum), maximum=maximum)

    def write(self, data):
        return self._run._callback("write", lambda: self._backend.write(data), offered=data)

    def close(self):
        return self._run._callback("close", self._backend.close)


def _summary(status, observed_ns):
    if status is None:
        return {"observed_ns": observed_ns, "controller": None}
    bridge = status.bridge
    result = {"observed_ns": observed_ns, "phase": status.phase,
              "error": None if status.error is None else status.error[:256],
              "validated_blocks": status.validated_blocks,
              "validated_samples": status.validated_samples,
              "worker_unresolved": status.worker_unresolved,
              "serial_unresolved": status.serial_unresolved,
              "serial_call": status.serial_call,
              "serial_operation_id": status.serial_operation_id,
              "serial_operation_generation": status.serial_operation_generation,
              "serial": None if status.serial is None else asdict(status.serial),
              "bridge": None}
    if bridge is not None:
        fields = ("identity", "source_received_end", "source_prefix_crc32", "source_terminal",
                  "endpoint_first_fault", "recovery", "pending_start", "pending_stop",
                  "late_fault", "closed", "storage", "mailbox", "accepted_build_id", "accepted_caps")
        values = {}
        for name in fields:
            value = getattr(bridge, name)
            values[name] = asdict(value) if hasattr(value, "__dataclass_fields__") else value
        # Recovery may retain a bounded WriteOffer; bytes are an observation,
        # not a new input to an endpoint or proof of request acceptance.
        if values["recovery"] and values["recovery"]["outstanding_offer"]:
            offer = values["recovery"]["outstanding_offer"]
            offer["data"] = offer["data"].hex()
        values["endpoint_state"] = bridge.endpoint_state.value
        session = bridge.session
        values["session"] = None if session is None else {
            key: (asdict(value) if hasattr(value, "__dataclass_fields__") else value)
            for key, value in ((key, getattr(session, key)) for key in session.__dataclass_fields__)
            if key != "latest_display"}
        result["bridge"] = values
    return result


def _transition(status):
    if status is None:
        return None
    b = status.bridge
    s = None if b is None else b.session
    return (status.phase, status.error, None if status.serial is None else status.serial.state,
            None if b is None else b.accepted_build_id,
            None if b is None else b.accepted_caps,
            None if b is None else b.endpoint_first_fault,
            None if b is None else b.source_terminal,
            None if s is None else s.selected_start_sample_index,
            None if s is None else s.stop_acknowledgement,
            None if s is None else s.first_failure)


class _DiagnosticRun:
    """Shared collector; public construction seams choose provenance and transport.

    collect runs the supervisor on its caller; accepted controller threads own
    receiver/sample disk/display roles. write_report starts ONE separate export
    owner. Arbitrary injected Python is trusted, not authenticated or sandboxed.
    """
    def __init__(self, settings, *, backend_factory, fixture_name,
                 event_config, limits=DiagnosticLimits(), clock_ns=time.monotonic_ns,
                 fake_diagnostic_declaration=None, invocation_selection=None,
                 physical_context=None, physical_metadata=None):
        if type(settings) is not SerialSettings or type(event_config) is not EventConfig:
            raise ValueError("exact serial settings and event configuration required")
        settings.__post_init__(); event_config.__post_init__()
        if type(limits) is not DiagnosticLimits:
            raise ValueError("exact diagnostic limits required")
        limits.__post_init__()
        if not callable(backend_factory) or not callable(clock_ns):
            raise ValueError("explicit selected factory and clock required")
        _text(fixture_name, 128)
        self._physical_context = (None if physical_context is None else
                                  validate_physical_context(physical_context))
        if self._physical_context is not None and (fake_diagnostic_declaration is not None or
                                                   invocation_selection is not None):
            raise ValueError("Physical/test context cannot mix with legacy fake selection")
        self._physical_metadata = physical_metadata
        self._fake_diagnostic_declaration = (None if fake_diagnostic_declaration is None else
                                            admit_fake_diagnostic_declaration(fake_diagnostic_declaration))
        self._invocation_selection = None
        if invocation_selection is not None:
            from .stream_diagnostic_invocation import validate_selection, INVOCATION_LIMITS, FAKE_SETTINGS
            selection = validate_selection(invocation_selection)
            if (self._fake_diagnostic_declaration is None or limits != INVOCATION_LIMITS or
                    settings != FAKE_SETTINGS):
                raise ValueError("Invocation requires exact STR1 fake settings and limits")
            self._invocation_selection = selection
        self._settings, self._limits = settings, limits
        self._factory, self._clock, self._config = backend_factory, clock_ns, event_config
        self._fixture, self._run_id = fixture_name, uuid.uuid4().hex
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._controller = self._report = self._export_thread = None
        self._started = False
        self._records, self._snapshots = [], []
        self._last_transition = None
        self._raw_reserved = self._callback_sequence = self._dropped = 0
        self._trace_error = self._run_error = self._first_gap_ns = None
        self._connect_intent_ns = self._record_intent_return_ns = None
        self._stop_intent_ns = self._close_intent_ns = None
        self._export_status = ExportStatus()
        self._export_evidence = self._export_proof = None
        self._metadata = None

    @property
    def _bounded_invocation(self):
        return self._invocation_selection is not None or self._physical_context is not None

    @property
    def settings(self):
        return self._settings

    @property
    def limits(self):
        return self._limits

    @property
    def run_id(self):
        return self._run_id

    def _now(self):
        now = self._clock()
        _integer(now, 0, MAX_NS)
        return now

    def _trace_failure(self, code):
        with self._lock:
            if self._trace_error is None:
                self._trace_error = code

    def _stamp(self):
        try:
            return self._now()
        except Exception:
            self._trace_failure("diagnostic_clock")
            return None

    def _reserve(self, kind, offered, maximum):
        begin = self._stamp()
        need = len(offered) + (maximum or 0)
        with self._lock:
            self._callback_sequence += 1
            if (len(self._records) >= self.limits.max_callbacks or
                    self._raw_reserved + need > self.limits.max_raw_bytes):
                self._trace_error = self._trace_error or "callback_capacity"
                self._first_gap_ns = self._first_gap_ns if self._first_gap_ns is not None else begin
                self._dropped += 1
                return None
            record = CallbackRecord(self._callback_sequence, kind, begin,
                                    offered=offered, read_maximum=maximum)
            index = len(self._records)
            self._records.append(record)
            self._raw_reserved += need
            return index, need, record

    def _finish(self, ticket, kind, result, exc):
        end = self._stamp()
        if ticket is None:
            return
        index, reserved, record = ticket
        if end is not None and record.begin_ns is not None and end < record.begin_ns:
            self._trace_failure("diagnostic_clock")
        outcome, accepted, received, length = "returned", None, b"", None
        if exc is not None:
            outcome = "exception"
        elif kind == "write":
            if type(result) is int and 0 <= result <= len(record.offered):
                outcome, accepted = "known", result
            else:
                outcome = "unknown"
        elif kind == "read":
            if type(result) is bytes:
                length, received = len(result), result[:record.read_maximum]
                if length > record.read_maximum:
                    outcome = "invalid_rx"
                    self._trace_failure("invalid_rx_truncated")
            else:
                outcome = "invalid_rx"
        updated = replace(record, return_ns=end, accepted_count=accepted, received=received,
                          actual_rx_length=length, outcome=outcome,
                          error_type=None if exc is None else _error(exc))
        with self._lock:
            self._records[index] = updated
            self._raw_reserved += len(record.offered) + len(received) - reserved

    def _callback(self, kind, callback, *, offered=b"", maximum=None):
        # Logging must never change the original callback result or exception.
        ticket = None
        try:
            ticket = self._reserve(kind, offered, maximum)
        except Exception:
            self._trace_failure("diagnostic_reservation")
        result, error = None, None
        try:
            result = callback()
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                self._finish(ticket, kind, result, error)
            except Exception:
                self._trace_failure("diagnostic_completion")

    def _backend_factory(self, **kwargs):
        backend = self._callback("factory", lambda: self._factory(**kwargs))
        return _BackendProxy(backend, self)

    def _note_intent(self, name):
        observed = self._stamp()
        with self._lock:
            if getattr(self, name) is None:
                setattr(self, name, observed)
            return getattr(self, name)

    def request_stop(self):
        self._note_intent("_stop_intent_ns")
        self._stop.set()
        controller = self._controller
        return False if controller is None else controller.stop()

    def close(self):
        self._note_intent("_close_intent_ns")
        self._stop.set()
        controller = self._controller
        if controller is not None:
            controller.close()

    def retry_close(self):
        controller = self._controller
        return False if controller is None else controller.retry_close()

    def snapshot(self):
        controller = self._controller
        state = None if controller is None else controller.snapshot()
        with self._lock:
            owner_settled = self._export_thread is not None and not self._export_thread.is_alive()
            export = replace(self._export_status, owner_settled=owner_settled,
                complete_receipt=self._export_status.complete_receipt and owner_settled)
            return DiagnosticStatus(state, self._trace_error, self._dropped, self._run_error,
                                    self._started and not self._done.is_set(), export)

    def join(self, timeout=0):
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or not 0 <= timeout <= 10:
            raise ValueError("join observation must be between 0 and 10 seconds")
        deadline = time.monotonic() + timeout
        controller = self._controller
        settled = controller is None or controller.join(max(0, deadline - time.monotonic()))
        with self._lock:
            thread = self._export_thread
        if thread is not None:
            thread.join(max(0, deadline - time.monotonic()))
            settled = settled and not thread.is_alive()
        if self._started:
            settled = self._done.wait(max(0, deadline - time.monotonic())) and settled
        return settled

    def _observe(self):
        status = self._controller.snapshot()
        key = _transition(status)
        if key != self._last_transition:
            frozen = _freeze_tree(_summary(status, self._now()))
            if len(self._snapshots) < self.limits.max_snapshots:
                self._snapshots.append(frozen)
            else:
                self._trace_failure("snapshot_capacity")
            self._last_transition = key
        return status

    @staticmethod
    def _settled_attempt(status):
        b = status.bridge
        if b is None:
            return True
        if b.session is None:
            return status.phase == "stopped before START" or b.closed
        return bool(b.closed and not status.worker_unresolved)

    def _stop_and_drain(self):
        self.request_stop()
        origin = self._now() if self._stop_intent_ns is None else self._stop_intent_ns
        deadline = origin + self.limits.drain_ms * 1000000
        while self._now() < deadline:
            settled = self._settled_attempt(self._observe())
            if self._bounded_invocation and self._now() >= deadline:
                self._run_error = self._run_error or "drain_unresolved"
                break
            if settled:
                break
            time.sleep(.001)
        else:
            self._run_error = self._run_error or "drain_unresolved"

    def collect(self, recording_path):
        """Explicitly connect, Record once, Stop and observe bounded cleanup.

        run_ms begins when the PUBLIC Record request returns admitted, including
        handshake/preparation. It is not a START_ACK or device-time budget.
        """
        _text(recording_path, 4096)
        if self._invocation_selection is not None and recording_path != self._invocation_selection.recording_path:
            raise ValueError("Recording path differs from selected invocation")
        with self._lock:
            if self._started:
                raise RuntimeError("one collect call per DiagnosticRun")
            self._started = True
        try:
            meta = {"format": FORMAT, "run_id": self.run_id, "origin": "synthetic-fake",
                    "fixture_name": self._fixture, "base_commit": BASE_COMMIT, "base_tree": BASE_TREE,
                    "module_sha256": {}, "source_hash_observed_ns": None, "settings": asdict(self.settings), "limits": asdict(self.limits),
                    "utc_label": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
                    "platform": platform.system(), "producer_profile": None,
                    "selected_declaration": (None if self._fake_diagnostic_declaration is None else
                                             asdict(self._fake_diagnostic_declaration)),
                    "expected_negotiation": (None if self._fake_diagnostic_declaration is None else
                                             {"build_id": self._fake_diagnostic_declaration.expected_build_id,
                                              "caps": STR1_DIAGNOSTIC_CAPS}),
                    "flash_status": (None if self._fake_diagnostic_declaration is None else "NOT_FLASH_APPROVED"),
                    "rehearsal_notice": (None if self._fake_diagnostic_declaration is None else
                        "Synthetic rehearsal of an intended device declaration; NOT_FLASH_APPROVED. "
                        "Completed-DMA records exclude ADC_DR overwrites, unfinished conversions and missed triggers. "
                        "Index / nominal Fs is not calibrated physical time. Unknown produced_end/loss stay unknown. "
                        "Accepted HELLO/CAPS do not authenticate an executing image."),
                    "firmware_image": None, "usb_identity": None, "telemetry": None,
                    "clock": "injected monotonic_ns; controller uses integer ns//1000000",
                    "coverage": "raw backend callbacks plus sampled persistent controller state",
                    "source_hash_scope": "export-time HOST files and reference fake tests; not factory authentication",
                    "volatile_until_export": True}
            if self._invocation_selection is not None:
                meta["invocation"] = asdict(self._invocation_selection)
                meta["base_commit"] = self._invocation_selection.bindings.source_commit
                meta["base_tree"] = self._invocation_selection.bindings.source_tree
            if self._physical_context is not None:
                injected = self._physical_context.execution_kind == "injected-test"
                meta.update(format=FORMAT if injected else PHYSICAL_FORMAT,
                    origin="synthetic-fake" if injected else "device",
                    physical_context=asdict(self._physical_context),
                    selected_declaration=asdict(STR1_DIAGNOSTIC_DECLARATION),
                    expected_negotiation={"build_id": STR1_DIAGNOSTIC_DECLARATION.expected_build_id,
                                          "caps": STR1_DIAGNOSTIC_CAPS},
                    physical_selection=self._physical_metadata,
                    flash_status=None,
                    rehearsal_notice=("Injected synthetic test of the operational source path; "
                                      "not a physical device run." if injected else None),
                    dtr_request=1, dtr_electrical_observation=None,
                    external_authority_verified=False,
                    clock="selected monotonic_ns callback; controller uses integer ns//1000000",
                    firmware_image=None, usb_identity=None)
                if self._physical_metadata is not None:
                    host = self._physical_metadata["deployment"]["selection"]["host"]
                    meta.update(base_commit=host["commit"], base_tree=host["tree"])
                if len(_json(meta)) > MAX_METADATA:
                    raise ValueError("metadata capacity")
            self._metadata = _freeze_tree(meta)
            peer = (SerialStreamPeer(self.settings, fake_backend_factory=self._backend_factory)
                    if self._physical_context is None else
                    SerialStreamPeer(self.settings, backend_factory=self._backend_factory))
            self._controller = StreamController(peer, event_config=self._config, managed_peer=True,
                storage_limits=StreamLimits(2 * 1024 * 1024, 16384), clock=lambda: self._now() // 1000000,
                fake_diagnostic_declaration=self._fake_diagnostic_declaration,
                physical_diagnostic_context=self._physical_context)
            controller = self._controller
            connect_at = self._now()
            if not self._stop.is_set():
                self._connect_intent_ns = connect_at
                if not controller.connect():
                    raise RuntimeError("connect intent refused")
            deadline = connect_at + self.limits.connect_ms * 1000000
            connected = False
            while not self._stop.is_set() and self._now() < deadline:
                s = self._observe()
                if self._stop.is_set() or self._trace_error or s.error:
                    break
                if s.phase == "connected" and (not self._bounded_invocation or self._now() < deadline):
                    connected = True
                    break
                self._stop.wait(.001)
            if connected and not self._stop.is_set() and not self._trace_error:
                if not controller.record(recording_path):
                    raise RuntimeError("record intent refused")
                record_at = self._record_intent_return_ns = self._now()
                deadline = record_at + self.limits.run_ms * 1000000
                while self._now() < deadline:
                    s = self._observe()
                    if (self._stop.is_set() or self._trace_error or s.error or
                            (s.bridge and (s.bridge.endpoint_first_fault or
                             (s.bridge.session and s.bridge.session.first_failure)))):
                        break
                    self._stop.wait(.001)
            elif not self._stop.is_set() and not self._trace_error:
                self._run_error = "connect_not_observed"
            self._stop_and_drain()
        except (Exception, KeyboardInterrupt) as exc:
            self._run_error = self._run_error or "collector_" + _error(exc)
            if self._physical_context is not None and self._controller is not None:
                try:
                    self._stop_and_drain()  # Uses the original intent, never another budget.
                except (Exception, KeyboardInterrupt):
                    pass  # First cause remains; finally still requests bounded close.
        finally:
            controller = self._controller
            if controller is not None:
                try:
                    self.close()
                    close_origin = self._close_intent_ns
                    remaining_ns = (self.limits.close_ms * 1000000 if close_origin is None else
                        max(0, close_origin + self.limits.close_ms * 1000000 - self._now()))
                    joined = controller.join(remaining_ns / 1000000000)
                    if (not joined or (self._bounded_invocation and close_origin is not None and
                            self._now() >= close_origin + self.limits.close_ms * 1000000)):
                        self._run_error = self._run_error or "cleanup_unresolved"
                except (Exception, KeyboardInterrupt) as exc:
                    self._run_error = self._run_error or "cleanup_" + _error(exc)
            try:
                self._report = self._freeze()
            finally:
                self._done.set()
        return self._report

    def _freeze(self):
        controller = self._controller
        status = None if controller is None else controller.snapshot()
        settled = controller is None or controller.join(0)
        observed = self._stamp()
        if (self._bounded_invocation and self._close_intent_ns is not None and observed is not None and
                observed >= self._close_intent_ns + self.limits.close_ms * 1000000):
            self._run_error = self._run_error or "cleanup_unresolved"
        final = _summary(status, observed)
        with self._lock:
            callbacks = tuple(self._records)
            trace_error, dropped = self._trace_error, self._dropped
        final.update({"run_id": self.run_id, "trace_error": trace_error, "dropped_callbacks": dropped,
                      "first_gap_ns": self._first_gap_ns, "run_error": self._run_error,
                      "connect_intent_ns": self._connect_intent_ns,
                      "record_intent_return_ns": self._record_intent_return_ns,
                      "stop_intent_ns": self._stop_intent_ns, "close_intent_ns": self._close_intent_ns,
                      "controller_joined": settled,
                      "trace_complete": bool(settled and not trace_error and not dropped and
                          all(r.outcome != "pending" and r.return_ns is not None for r in callbacks)),
                      "live_durability": "none; RAM capture before explicit export",
                      "full_endpoint_event_trace": False})
        metadata = self._metadata or _freeze_tree({"format": FORMAT, "run_id": self.run_id,
            "origin": "synthetic-fake", "module_sha256": {}, "volatile_until_export": True})
        return DiagnosticReport(self.run_id, metadata, callbacks, tuple(self._snapshots), _freeze_tree(final))

    def write_report(self, path):
        """One explicit asynchronous export of the immutable collect result."""
        _text(path, 4096)
        if self._invocation_selection is not None and path != self._invocation_selection.report_path:
            raise ValueError("Report path differs from selected invocation")
        if not self._done.is_set() or self._report is None:
            raise RuntimeError("collect must produce a frozen as-of report first")
        with self._lock:
            if self._export_thread is not None:
                raise RuntimeError("one report export per run")
            self._export_status = ExportStatus("running")
            self._export_thread = threading.Thread(target=self._export, args=(path, self._report),
                                                   name="stream-diagnostic-export", daemon=True)
            self._export_thread.start()

    def _export(self, path, report):
        synced = False
        capture = report
        try:
            capture_digest = _capture_sha256(capture)
            # These are export-time file observations, not authentication of
            # executed Python. The immutable capture itself stays unchanged.
            hashes = {}
            modules = (PHYSICAL_MODULES if self._physical_context is not None else
                       INVOCATION_MODULES if self._invocation_selection is not None else MODULES)
            for name in modules:
                with _open_file(Path(__file__).parent / name, "rb") as source:
                    content = source.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ValueError("source module capacity")
                hashes[name] = hashlib.sha256(content).hexdigest()
            metadata = _thaw(report._metadata)
            metadata.update(module_sha256=hashes, source_hash_observed_ns=self._now())
            if len(_json(metadata)) > MAX_METADATA:
                raise ValueError("metadata capacity")
            report = replace(report, _metadata=_freeze_tree(metadata))
            with _open_file(path, "xb") as output:
                expected_file = _write_report(output, report, self.limits.max_file_bytes)
                output.flush()
                _fsync(output.fileno())
                synced = True
            checked = read_report(path, expected_run_id=report.run_id,
                                  expected_source_hashes=json.loads(report.metadata)["module_sha256"])
            if not checked.complete:
                raise ValueError("readback failed")
            if (checked.file_bytes, checked.file_sha256) != expected_file:
                raise ValueError("readback differs from owned capture")
            complete = bool(json.loads(report.final)["trace_complete"] and
                            not json.loads(report.final)["run_error"])
            status = ExportStatus("verified", True, True, complete)
            self._export_evidence = (capture, capture_digest, report, expected_file)
        except Exception as exc:
            status = ExportStatus("failed", synced, False, False, _error(exc))
        with self._lock:
            self._export_status = status


    def owned_export_proof(self):
        """Return runtime-owned evidence only after the export owner has settled.

        This association prevents accidental receipt/capture substitution, not
        hostile Python or post-readback filesystem changes. It cannot be rebuilt
        from a footer, run ID, caller booleans or a fresh file reader.
        """
        with self._lock:
            if (self._export_thread is None or self._export_thread.is_alive() or
                    self._export_evidence is None):
                return None
            if self._export_proof is None:
                self._export_proof = OwnedExportProof(_PROOF_SEAL, *self._export_evidence,
                    replace(self._export_status, owner_settled=True))
            return self._export_proof


class DiagnosticRun(_DiagnosticRun):
    """Legacy fake-only public seam; never falls back to a real backend."""
    def __init__(self, settings, *, fake_backend_factory, fake_origin, fixture_name,
                 event_config, limits=DiagnosticLimits(), clock_ns=time.monotonic_ns,
                 fake_diagnostic_declaration=None, invocation_selection=None):
        if fake_origin != "synthetic-fake" or type(fake_origin) is not str:
            raise ValueError("explicit synthetic-fake origin required")
        if not callable(fake_backend_factory):
            raise ValueError("explicit fake factory required")
        super().__init__(settings, backend_factory=fake_backend_factory, fixture_name=fixture_name,
            event_config=event_config, limits=limits, clock_ns=clock_ns,
            fake_diagnostic_declaration=fake_diagnostic_declaration,
            invocation_selection=invocation_selection)


class _PhysicalDiagnosticRun(_DiagnosticRun):
    """Operational core, constructed only by the explicit invocation boundary.

    Injected tests must say so; their context forces synthetic journal/report
    identity. Real construction has no caller factory override. Neither mode
    establishes an external deployment decision or owner authority.
    """
    def __init__(self, settings, *, context, event_config, backend_factory=None,
                 fixture_name="selected-physical-attempt", clock_ns=time.monotonic_ns,
                 selection_metadata=None):
        context = validate_physical_context(context)
        if context.execution_kind == "physical":
            if backend_factory is not None:
                raise ValueError("Real construction does not accept an injected factory")
            # Lazy import/call: construction performs no backend or port action.
            def factory(**kwargs):
                from .transport import _serial_factory
                return _serial_factory(**kwargs)
        else:
            if not callable(backend_factory):
                raise ValueError("Injected operational test requires its fake factory")
            factory = backend_factory
        if selection_metadata is not None:
            if type(selection_metadata) is not dict or len(_json(selection_metadata)) > 4096:
                raise ValueError("bounded physical selection metadata required")
            selection_metadata = _thaw(_freeze_tree(selection_metadata))
        super().__init__(settings, backend_factory=factory, fixture_name=fixture_name,
            event_config=event_config, limits=DiagnosticLimits(run_ms=2000), clock_ns=clock_ns,
            physical_context=context, physical_metadata=selection_metadata)


_PROOF_SEAL = object()


@dataclass(frozen=True, slots=True, init=False)
class OwnedExportProof:
    """Opaque in-process association issued by the settled export owner only."""
    _capture: DiagnosticReport
    capture_sha256: str
    _exported: DiagnosticReport
    file_bytes: int
    file_sha256: str
    status: ExportStatus
    _seal: object

    def __init__(self, seal, capture, capture_sha256, exported, expected_file, status):
        if seal is not _PROOF_SEAL:
            raise TypeError("Only the settled diagnostic export owner issues proof")
        for key, value in (("_capture", capture), ("capture_sha256", capture_sha256),
                ("_exported", exported), ("file_bytes", expected_file[0]),
                ("file_sha256", expected_file[1]), ("status", status), ("_seal", seal)):
            object.__setattr__(self, key, value)


def _capture_sha256(report):
    digest = hashlib.sha256()
    def add(value):
        digest.update(len(value).to_bytes(8, "big")); digest.update(value)
    add(report.metadata)
    for row in report.callbacks:
        add(_json(row.payload()))
    for value in report.snapshots:
        add(value)
    add(report.final)
    return digest.hexdigest()


def validate_owned_export(report, proof):
    """Pure evidence association check. No file access, waits or authority."""
    if (type(report) is not DiagnosticReport or type(proof) is not OwnedExportProof or
            proof._seal is not _PROOF_SEAL or proof._capture is not report or
            proof.capture_sha256 != _capture_sha256(report)):
        raise ValueError("No owned export for this exact frozen capture")
    status = proof.status
    if (type(status) is not ExportStatus or status.state != "verified" or
            status.fsync_returned is not True or status.readback_verified is not True or
            status.complete_receipt is not True or status.owner_settled is not True or
            status.error is not None):
        raise ValueError("Export is incomplete, failed or unsettled")
    if (proof._exported.run_id != report.run_id or proof._exported.callbacks != report.callbacks or
            proof._exported.snapshots != report.snapshots or proof._exported.final != report.final):
        raise ValueError("Export content differs from frozen capture")
    metadata, exported = json.loads(report.metadata), json.loads(proof._exported.metadata)
    for key in ("module_sha256", "source_hash_observed_ns"):
        metadata.pop(key, None); exported.pop(key, None)
    if metadata != exported:
        raise ValueError("Export metadata differs from capture")
    # Recompute the exact deterministic serialization without opening any file.
    class DigestSink:
        def write(self, data):
            return len(data)
    if _write_report(DigestSink(), proof._exported, MAX_FILE) != (proof.file_bytes, proof.file_sha256):
        raise ValueError("Export byte commitment differs from owned content")
    return proof


def _write_report(output, report, file_limit):
    sequence, written, chain = 0, 0, "0" * 64
    file_digest = hashlib.sha256()

    def emit(kind, payload):
        nonlocal sequence, written, chain
        body = {"seq": sequence, "run_id": report.run_id, "kind": kind,
                "previous": chain, "payload": payload}
        digest = hashlib.sha256(_json(body)).hexdigest()
        encoded = _json({**body, "digest": digest}) + b"\n"
        if len(encoded) > MAX_LINE or written + len(encoded) > file_limit:
            raise ValueError("report capacity")
        offset = 0
        while offset < len(encoded):
            count = output.write(encoded[offset:])
            if type(count) is not int or not 1 <= count <= len(encoded) - offset:
                raise OSError("report write did not progress")
            # Commit the exact owned serialization accepted by this write,
            # including the header, final record and every newline.
            file_digest.update(encoded[offset:offset + count])
            offset += count
        written += len(encoded); sequence += 1; chain = digest

    emit("header", json.loads(report.metadata))
    for record in report.callbacks:
        emit("callback", record.payload())
    for snapshot in report.snapshots:
        emit("snapshot", json.loads(snapshot))
    emit("final", {"callback_count": len(report.callbacks), "snapshot_count": len(report.snapshots),
                   "raw_bytes": sum(len(r.offered) + len(r.received) for r in report.callbacks),
                   "prefix_records": sequence, "prefix_bytes": written, "prefix_sha256": chain,
                   "summary": json.loads(report.final)})
    return written, file_digest.hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _hex(value, maximum, *, length=None):
    if (type(value) is not str or len(value) > maximum * 2 or len(value) % 2 or
            any(c not in "0123456789abcdef" for c in value) or
            (length is not None and len(value) != length * 2)):
        raise ValueError("bounded lowercase hex required")
    return bytes.fromhex(value)


def _report_context(header):
    """Strict format/provenance pairing; context is data, never admission."""
    if "physical_context" not in header:
        if header.get("format") != FORMAT or header.get("origin") != "synthetic-fake":
            raise ValueError("legacy fake header identity")
        return None
    raw = header["physical_context"]
    keys = ("format run_id origin fixture_name base_commit base_tree module_sha256 source_hash_observed_ns "
            "settings limits utc_label python platform producer_profile selected_declaration expected_negotiation "
            "flash_status rehearsal_notice firmware_image usb_identity telemetry clock coverage source_hash_scope "
            "volatile_until_export physical_context physical_selection dtr_request dtr_electrical_observation "
            "external_authority_verified").split()
    if set(header) != set(keys):
        raise ValueError("physical trace header schema")
    if type(raw) is not dict or set(raw) != {"execution_kind", "declaration"}:
        raise ValueError("physical context schema")
    context = validate_physical_context(PhysicalDiagnosticContext(raw["execution_kind"]))
    if _json(raw) != _json(asdict(context)):
        raise ValueError("physical context declaration")
    injected = context.execution_kind == "injected-test"
    expected = {"format": FORMAT if injected else PHYSICAL_FORMAT,
                "origin": "synthetic-fake" if injected else "device",
                "selected_declaration": asdict(context.declaration),
                "expected_negotiation": {"build_id": context.declaration.expected_build_id,
                                         "caps": list(STR1_DIAGNOSTIC_CAPS)},
                "dtr_request": 1, "dtr_electrical_observation": None,
                "external_authority_verified": False,
                "firmware_image": None, "usb_identity": None, "telemetry": None,
                "producer_profile": None, "flash_status": None,
                "limits": asdict(DiagnosticLimits(run_ms=2000))}
    if any(key not in header or _json(header[key]) != _json(value) for key, value in expected.items()):
        raise ValueError("mixed physical/test header provenance")
    selected = header["physical_selection"]
    if selected is None:
        if not injected:
            raise ValueError("physical trace requires its declared deployment")
    else:
        from .stream_diagnostic_invocation import (parse_deployment, OperatorConfirmation,
                                                  validate_deployment, _object)
        if type(selected) is not dict or set(selected) != {"deployment", "confirmation"}:
            raise ValueError("physical selection schema")
        deployment = parse_deployment(selected["deployment"])
        confirmation = _object(OperatorConfirmation, selected["confirmation"])
        validate_deployment(deployment.selection, deployment, confirmation)
        if (header["base_commit"] != deployment.selection.host.commit or
                header["base_tree"] != deployment.selection.host.tree or
                _json(header["module_sha256"]) != _json(dict(deployment.selection.host.module_sha256)) or
                _json(header["settings"]) != _json(asdict(SerialSettings(deployment.selection.association.port, 230400, 0, .025)))):
            raise ValueError("physical source/port binding")
    return context


def _summary_context(summary, context):
    if context is None:
        return
    origin, metadata = physical_context_identity(context)
    def visit(value):
        if type(value) is dict:
            for key, child in value.items():
                if key == "identity" and child is not None:
                    from .stream_model import StreamIdentity
                    if type(child) is not dict:
                        raise ValueError("physical identity schema")
                    StreamIdentity(**child)
                    if (type(child) is not dict or child.get("origin") != origin or
                            tuple(child.get(k) for k in ("protocol_revision", "firmware_version",
                                                       "application_version")) != metadata):
                        raise ValueError("mixed physical/test identity carrier")
                visit(child)
        elif type(value) is list:
            for child in value:
                visit(child)
    visit(summary)


def read_report(path, *, expected_run_id=None, expected_source_hashes=None):
    """Validate finite records; corruption exposes only the verified prefix.

    Complete means an intact exported structure, NOT a historical fsync return,
    complete callback coverage, source success or authenticated producer identity.
    file_bytes/file_sha256 describe the entire actual file only on complete
    validation; the export owner separately compares them to its own write.
    """
    records = []
    run_id, chain, total, raw_total = None, "0" * 64, 0, 0
    file_digest = hashlib.sha256()
    callbacks = snapshots = previous_callback = 0
    seen_snapshot = False
    context = None
    limits = DiagnosticLimits()
    try:
        with _open_file(path, "rb") as source:
            while True:
                line = source.readline(MAX_LINE + 1)
                if not line:
                    raise ValueError("missing final record")
                if not line.endswith(b"\n") or len(line) > MAX_LINE or total + len(line) > limits.max_file_bytes:
                    raise ValueError("truncated or oversized record")
                obj = json.loads(line, object_pairs_hook=_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
                if type(obj) is not dict or set(obj) != {"seq", "run_id", "kind", "previous", "payload", "digest"}:
                    raise ValueError("record schema")
                _integer(obj["seq"], len(records), len(records))
                _hex(obj["run_id"], 16, length=16); _hex(obj["digest"], 32, length=32)
                if obj["previous"] != chain:
                    raise ValueError("chain mismatch")
                body = {key: value for key, value in obj.items() if key != "digest"}
                if hashlib.sha256(_json(body)).hexdigest() != obj["digest"]:
                    raise ValueError("record digest")
                kind, payload = obj["kind"], obj["payload"]
                if type(payload) is not dict:
                    raise ValueError("payload schema")
                if not records:
                    if (kind != "header" or payload.get("run_id") != obj["run_id"] or
                            payload.get("volatile_until_export") is not True):
                        raise ValueError("header identity")
                    context = _report_context(payload)
                    if len(_json(payload)) > MAX_METADATA:
                        raise ValueError("header capacity")
                    run_id = obj["run_id"]
                    if expected_run_id is not None and run_id != expected_run_id:
                        raise ValueError("unexpected run")
                    hashes = payload.get("module_sha256")
                    source_modules = (PHYSICAL_MODULES if context is not None else
                                      INVOCATION_MODULES if "invocation" in payload else MODULES)
                    if type(hashes) is not dict or set(hashes) != set(source_modules):
                        raise ValueError("source hash schema")
                    for value in hashes.values():
                        _hex(value, 32, length=32)
                    if expected_source_hashes is not None and hashes != expected_source_hashes:
                        raise ValueError("unexpected sources")
                    if "limits" in payload:
                        limits = DiagnosticLimits(**payload["limits"])
                    if total + len(line) > limits.max_file_bytes:
                        raise ValueError("declared file capacity")
                elif obj["run_id"] != run_id:
                    raise ValueError("mixed run")
                elif kind == "callback":
                    if seen_snapshot or callbacks >= limits.max_callbacks or set(payload) != set(CallbackRecord.__dataclass_fields__):
                        raise ValueError("callback schema/order/capacity")
                    _integer(payload["callback_id"], previous_callback + 1, MAX_NS)
                    previous_callback = payload["callback_id"]
                    if payload["kind"] not in (("factory", "open", "read", "write", "close", "dtr")
                                               if context is not None else
                                               ("factory", "open", "read", "write", "close")):
                        raise ValueError("callback kind")
                    for name in ("begin_ns", "return_ns"):
                        if payload[name] is not None:
                            _integer(payload[name], 0, MAX_NS)
                    if (payload["begin_ns"] is not None and payload["return_ns"] is not None and
                            payload["return_ns"] < payload["begin_ns"]):
                        raise ValueError("callback time")
                    offered, received = _hex(payload["offered"], 544), _hex(payload["received"], 544)
                    if payload["kind"] != "write" and offered:
                        raise ValueError("nonwrite offer")
                    if payload["kind"] != "read" and (received or payload["read_maximum"] is not None or payload["actual_rx_length"] is not None):
                        raise ValueError("nonread data")
                    if payload["kind"] == "read":
                        _integer(payload["read_maximum"], 0, 544)
                        if len(received) > payload["read_maximum"]:
                            raise ValueError("read offer")
                        if payload["actual_rx_length"] is not None:
                            _integer(payload["actual_rx_length"], len(received), MAX_NS)
                    outcome = payload["outcome"]
                    if outcome not in ("pending", "returned", "known", "unknown", "exception", "invalid_rx"):
                        raise ValueError("callback outcome")
                    if outcome == "known":
                        if payload["kind"] != "write":
                            raise ValueError("nonwrite acceptance")
                        _integer(payload["accepted_count"], 0, len(offered))
                    elif payload["accepted_count"] is not None:
                        raise ValueError("invented accepted prefix")
                    if payload["error_type"] is not None:
                        _text(payload["error_type"], 64)
                    if outcome == "pending":
                        if (payload["return_ns"] is not None or received or
                                payload["actual_rx_length"] is not None or payload["error_type"] is not None):
                            raise ValueError("pending result")
                    elif outcome == "exception":
                        if payload["error_type"] is None or received or payload["actual_rx_length"] is not None:
                            raise ValueError("exception result")
                    elif payload["error_type"] is not None:
                        raise ValueError("unexpected exception type")
                    if outcome == "unknown" and payload["kind"] != "write":
                        raise ValueError("nonwrite unknown count")
                    if outcome == "returned":
                        if payload["kind"] == "write":
                            raise ValueError("write without acceptance")
                        if payload["kind"] == "read" and (type(payload["actual_rx_length"]) is not int or
                                payload["actual_rx_length"] != len(received)):
                            raise ValueError("read return length")
                    if outcome == "invalid_rx":
                        if payload["kind"] != "read" or (payload["actual_rx_length"] is not None and
                                payload["actual_rx_length"] <= payload["read_maximum"]) or (
                                payload["actual_rx_length"] is None and received):
                            raise ValueError("invalid read result")
                    raw_total += len(offered) + len(received)
                    if raw_total > limits.max_raw_bytes:
                        raise ValueError("raw capacity")
                    callbacks += 1
                elif kind == "snapshot":
                    _summary_context(payload, context)
                    seen_snapshot = True
                    snapshots += 1
                    if snapshots > limits.max_snapshots:
                        raise ValueError("snapshot capacity")
                elif kind == "final":
                    if set(payload) != {"callback_count", "snapshot_count", "raw_bytes", "prefix_records", "prefix_bytes", "prefix_sha256", "summary"}:
                        raise ValueError("final schema")
                    expected = (callbacks, snapshots, raw_total, len(records), total, chain)
                    actual = tuple(payload[key] for key in ("callback_count", "snapshot_count", "raw_bytes", "prefix_records", "prefix_bytes", "prefix_sha256"))
                    if actual != expected or any(type(payload[key]) is not int for key in ("callback_count", "snapshot_count", "raw_bytes", "prefix_records", "prefix_bytes")):
                        raise ValueError("final commitment")
                    summary = payload["summary"]
                    if type(summary) is not dict or summary.get("run_id") != run_id or type(summary.get("trace_complete")) is not bool:
                        raise ValueError("final run/summary")
                    _summary_context(summary, context)
                    if summary["trace_complete"]:
                        if summary.get("trace_error") is not None or summary.get("dropped_callbacks") != 0 or summary.get("controller_joined") is not True or previous_callback != callbacks:
                            raise ValueError("false trace completion")
                        for entry in records:
                            previous = json.loads(entry)
                            if previous["kind"] == "callback" and (previous["payload"]["outcome"] == "pending" or previous["payload"]["return_ns"] is None):
                                raise ValueError("pending callback completion")
                    if source.read(1):
                        raise ValueError("bytes after final")
                    records.append(_json(obj))
                    file_digest.update(line)
                    return ReadReport(tuple(records), True, None, run_id, _json(summary),
                                      total + len(line), file_digest.hexdigest())
                else:
                    raise ValueError("record order/kind")
                records.append(_json(obj))
                if len(records) > MAX_CALLBACKS + MAX_SNAPSHOTS + 1:
                    raise ValueError("record capacity")
                file_digest.update(line)
                total += len(line); chain = obj["digest"]
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        return ReadReport(tuple(records), False, _error(exc), run_id)

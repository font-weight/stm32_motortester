"""Single-owner recording adapter and bounded off-thread spool writer.

The caller serializes every public adapter method and immediate runtime control
with the same admission lock as Stop. It pumps transport outside that lock and
passes each observation here before accepting writer completions. Disk calls run
only on one daemon thread with one job/completion slot. No Qt dependency.

Retirement never joins or force-cancels an OS write. Its unresolved snapshot and
spool path must remain visible to the caller. Process exit may truncate an
uncommitted tail; recovery can trust only the verified prefix. A retired writer
cannot publish a completion or initiate an export. Cancellation cannot undo an
archive publication which has already completed.
"""
from dataclasses import dataclass
from pathlib import Path
import threading

from .model import Capture
from .recording_controller import (IntentKind, MAX_RECORDING_MS,
    RecordingController, RecordingPhase, RecordingResult, RecordingSnapshot)
from .runtime import AcquisitionRuntime
from .session import Event
from .session_archive import (AbortEvent, FullSegment, PartialSegment,
    RecoveryReport, SampleFragment, SaveCancelled, SegmentProvenance, SessionSpool, finalize)
from .window import WindowResult


@dataclass(frozen=True, slots=True)
class RecordingBridgeState:
    snapshot: RecordingSnapshot | None = None
    spool_path: str | None = None
    writer_busy: bool = False
    writer_running: bool = False
    ready_to_save: bool = False
    saving: bool = False
    saved_path: str | None = None
    error: str | None = None
    retired: bool = False


@dataclass(frozen=True, slots=True)
class SaveCompletion:
    """One exact Save admission outcome, including repeated paths or failures."""
    operation: object
    path: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _Job:
    kind: str
    token: object
    payload: object = None
    destination: str | None = None
    cancelled: threading.Event | None = None


@dataclass(frozen=True, slots=True)
class _Completion:
    job: _Job
    value: object = None
    error: str | None = None


class _DiskWriter:
    """One admitted operation, including its not-yet-consumed completion.

    The disk thread alone owns SessionSpool. submit/take/retire only take a short
    condition lock; it is never held during file operations or user callbacks.
    """
    def __init__(self, spool_factory=SessionSpool.create):
        self._factory = spool_factory
        self._condition = threading.Condition()
        self._job = self._completion = self._spool = None
        self._retired = False
        self._retained = ()
        self._thread = threading.Thread(target=self._run,
            name="motortester-recording-writer", daemon=True)
        self._thread.start()

    @property
    def running(self):
        return self._thread.is_alive()

    @property
    def busy(self):
        with self._condition:
            return self._job is not None or self._completion is not None

    def submit(self, job):
        with self._condition:
            if self._retired or self._job is not None or self._completion is not None:
                return False
            self._job = job
            self._condition.notify()
            return True

    def take(self):
        with self._condition:
            completion, self._completion = self._completion, None
            return completion

    def retire(self, retained):
        with self._condition:
            self._retired = True
            self._retained = retained
            if self._job is not None and self._job.cancelled is not None:
                self._job.cancelled.set()
            self._condition.notify()

    def _perform(self, job):
        if job.kind == "prepare":
            self._spool = self._factory(job.destination, job.payload)
            return self._spool.receipt_issuer, self._spool.capacity
        if job.kind == "persist":
            return {FullSegment: self._spool.append_full,
                    PartialSegment: self._spool.append_partial,
                    AbortEvent: self._spool.append_abort}[type(job.payload)](job.payload)
        if job.kind == "finish":
            outcome = job.payload
            return self._spool.finish(outcome.reason, outcome.stop_evidence, outcome.diagnostic)
        if job.kind == "save":
            return finalize(job.payload, job.destination, cancelled=job.cancelled.is_set)
        raise ValueError("Unknown writer operation")

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._job is not None or self._retired)
                    if self._job is None:
                        return
                    job = self._job
                try:
                    value = self._perform(job)
                    completion = _Completion(job, value)
                except BaseException as exc:
                    # Never retain traceback/backend text/credentials in a UI queue.
                    code = "save_cancelled" if isinstance(exc, SaveCancelled) else job.kind + "_failed"
                    completion = _Completion(job, error=code)
                with self._condition:
                    self._job = None
                    self._completion = completion
                    if self._retired:
                        return
                    # Do not accept another job until the owner consumes this slot.
        finally:
            if self._spool is not None:
                try:
                    self._spool.close()
                except BaseException:
                    pass  # Retirement cannot assert that close/fsync succeeded.


class RecordingSessionAdapter:
    """Acquisition-owner API. It never pumps, opens or closes the transport.

    All calls require the caller's Stop/admission lock. observe() must follow
    every runtime pump/disconnect, including while storage is blocked. Call it
    before consuming writer callbacks so recovery can revoke a terminal intent.
    Exact runtime results remain there until a valid durable receipt permits a
    local-only ACK. GUI acknowledgement is unrelated and must not reach runtime.
    """
    def __init__(self, runtime, *, clock_ms, spool_factory=SessionSpool.create,
                 max_recording_ms=MAX_RECORDING_MS):
        if type(runtime) is not AcquisitionRuntime or not callable(clock_ms):
            raise ValueError("Invalid recording runtime")
        self.runtime, self.clock = runtime, clock_ms
        self.controller = RecordingController(max_recording_ms=max_recording_ms)
        self._writer = _DiskWriter(spool_factory)
        self._session = self._report = self._job = None
        self._path = self._saved = self._error = None
        self._retired = False
        self._sources = ()
        self._started = 0
        self._save_cancel = self._save_completion = None

    @property
    def save_completion(self):
        return self._save_completion

    @property
    def owns_runtime(self):
        return self._session is not None and self.controller.snapshot().phase is not RecordingPhase.FINISHED

    @property
    def state(self):
        return RecordingBridgeState(self.controller.snapshot() if self._session else None,
            self._path, self._writer.busy, self._writer.running,
            self._report is not None and not self._retired and self._job is None,
            self._job is not None and self._job.kind == "save", self._saved,
            self._error, self._retired)

    def record(self, identity, settings, spool_path):
        if (self._retired or self._writer.busy or self._job is not None
                or self.runtime.pending_capture is not None
                or self.runtime.incomplete_result is not None
                or (self._session is not None and self._saved is None)):
            return False
        if not isinstance(spool_path, (str, Path)) or not str(spool_path):
            return False
        now = self.clock()
        session = self.controller.record(identity, settings, self.runtime.snapshot(), now_ms=now)
        self._session, self._started = session, now
        self._path = str(Path(spool_path).absolute())
        self._report = self._saved = self._error = self._save_completion = None
        self._sources = ()
        self._drive(now)
        return True

    def _results(self, now):
        values = []
        for source in (self.runtime.pending_capture, self.runtime.incomplete_result):
            if source is None:
                continue
            cached = next((result for result in self._sources if result.source is source), None)
            if cached is not None:
                values.append(cached)
                continue
            facts = self.runtime.result_provenance(source)
            if facts is None:
                raise ValueError("Missing exact runtime provenance")
            begin, terminal = facts.begin_frame, facts.terminal_frame
            fields = dict(generation=facts.generation, config_id=facts.config_id,
                acquisition_mode=facts.config.mode, auto_timeout_ms=facts.config.auto_timeout_ms,
                host_elapsed_ms=now - self._started, wire_version="0.1", start_operation="arm",
                wire_session_nonce=facts.session_nonce, config_payload_hex=facts.config.payload().hex(),
                begin_request_id=begin.request_id, begin_sequence=begin.sequence,
                unavailable_reason="no_validated_terminal")
            if terminal is not None:
                fields.update(terminal_kind="end" if terminal.message_type == 146 else "abort",
                    terminal_request_id=terminal.request_id, terminal_sequence=terminal.sequence,
                    unavailable_reason=None)
                if terminal.message_type == 146:
                    fields["end_samples_crc32"] = int.from_bytes(terminal.payload[12:16], "little")
                else:
                    fields["abort_reason_code"] = int.from_bytes(terminal.payload[8:10], "little")
            provenance = SegmentProvenance(**fields)
            if type(source) is Capture:
                item = FullSegment(source, provenance)
            else:
                b = source.begin
                item = PartialSegment(b.window_id, b.sample_count,
                    tuple(SampleFragment(f.offset, f.samples) for f in source.fragments),
                    source.missing_ranges, source.reason.value, provenance,
                    nominal_fs_hz=b.timer_clock_hz / ((b.timer_psc + 1) * (b.timer_arr + 1)),
                    deferred_checks=source.deferred_checks, metadata_issues=source.metadata_issues)
            values.append(RecordingResult(item, source))
        self._sources = tuple(values)
        return self._sources

    def observe(self, events=()):
        if self._retired or self._session is None:
            return
        now = self.clock()
        if self.owns_runtime:
            try:
                results = self._results(now)
            except (ValueError, TypeError, AttributeError):
                results = ()
                self.controller.fault("result_conversion_failed", now_ms=now,
                                      source_retention_required=True)
            self.controller.observe(self._session, self.runtime.snapshot(), events, results, now_ms=now)
        self.controller.tick(now_ms=now)
        completion = self._writer.take()
        if completion is not None:
            self._complete(completion, now)
        self._drive(now)

    def _complete(self, completion, now):
        if completion.job is not self._job or self._retired:
            return False
        job, self._job = self._job, None
        if job.kind == "save":
            self._save_cancel = None
            if completion.error:
                self._error = completion.error
            else:
                self._saved = str(completion.value.path)
                self._error = None
            self._save_completion = SaveCompletion(job.token,
                str(completion.value.path) if completion.error is None else None, completion.error)
            return True
        if completion.error:
            self._error = completion.error
            return self.controller.failed(job.token, completion.error, now_ms=now)
        if job.kind == "prepare":
            issuer, capacity = completion.value
            return self.controller.spool_prepared(job.token, issuer=issuer, capacity=capacity, now_ms=now)
        if job.kind == "persist":
            return self.controller.committed(job.token, completion.value, now_ms=now)
        if job.kind == "finish":
            report = completion.value
            snapshot = self.controller.snapshot()
            if (type(report) is not RecoveryReport or report.identity.recording_id != self._session.recording_id
                    or report.outcome != job.payload or len(report.entries) != snapshot.committed_segments
                    or sum(entry.byte_count for entry in report.entries) != snapshot.committed_bytes):
                self._error = "invalid_finalization_report"
                self.controller.failed(job.token, self._error, now_ms=now)
                return False
            accepted = self.controller.finalized(job.token, now_ms=now)
            if accepted:
                self._report = completion.value
            else:
                self._error = "finalization_unresolved"
            return accepted
        return False

    def _drive(self, now):
        if self._retired or self._session is None:
            return
        for _ in range(8):  # Bounded synchronous control transitions per pump.
            intents = self.controller.intents
            if not intents:
                return
            intent = next((i for i in intents if i.kind is IntentKind.STOP), intents[0])
            disk_kind = {IntentKind.PREPARE: "prepare", IntentKind.PERSIST: "persist",
                         IntentKind.FINALIZE_READY: "finish"}.get(intent.kind)
            if disk_kind and (self._job is not None or self._writer.busy):
                return
            if not self.controller.admit(intent, now_ms=now, runtime_state=self.runtime.snapshot()):
                return
            try:
                if disk_kind:
                    payload = intent.payload.result.item if disk_kind == "persist" else intent.payload
                    job = _Job(disk_kind, intent, payload, self._path if disk_kind == "prepare" else None)
                    if not self._writer.submit(job):
                        raise ValueError("Writer admission rejected")
                    self._job = job
                    return
                if intent.kind is IntentKind.ACK_LOCAL:
                    source = intent.payload.result.source
                    if type(source) is Capture:
                        if self.runtime.pending_capture is not source:
                            raise ValueError("Source ownership changed")
                        if self.runtime.acknowledge(source) != ():
                            raise ValueError("Local ACK unexpectedly emitted events")
                    elif type(source) is WindowResult:
                        if self.runtime.incomplete_result is not source:
                            raise ValueError("Source ownership changed")
                        if self.runtime.take_incomplete() is not source:
                            raise ValueError("Source ownership changed")
                    elif type(source) is not Event:
                        raise ValueError("Invalid ownership source")
                    self.controller.acknowledged(intent, self.runtime.snapshot(), now_ms=now)
                    self._sources = tuple(r for r in self._sources if r.source is not source)
                else:
                    events = {IntentKind.CONFIGURE: lambda: self.runtime.configure(intent.payload),
                              IntentKind.ARM: self.runtime.arm, IntentKind.STOP: self.runtime.stop}[intent.kind]()
                    self.controller.command_submitted(intent, self.runtime.snapshot(), now_ms=now)
                    self.controller.observe(self._session, self.runtime.snapshot(), events, now_ms=now)
            except (ValueError, TypeError, AttributeError):
                self._error = "recording_effect_failed"
                self.controller.failed(intent, self._error, now_ms=now)
                return

    def stop(self):
        if self._retired or self._session is None:
            return False
        now = self.clock()
        self.controller.stop(now_ms=now)
        self._drive(now)
        return True

    def save(self, destination, *, operation=None):
        if not self.state.ready_to_save or not isinstance(destination, (str, Path)) or not str(destination):
            return False
        cancel = threading.Event()
        job = _Job("save", object() if operation is None else operation,
                   self._report, str(destination), cancel)
        if not self._writer.submit(job):
            return False
        self._job, self._save_cancel = job, cancel
        self._error = None
        return True

    def cancel_save(self, operation=None):
        if (self._save_cancel is None or self._job is None or self._job.kind != "save"
                or (operation is not None and operation is not self._job.token)):
            return False
        self._save_cancel.set()
        return True

    def retire(self):
        """Bounded owner retirement; never report the daemon as joined.

        Caller first disconnects runtime and observes its actual retained results.
        The immutable state and path remain available even after QThread closure.
        """
        if self._retired:
            return
        if self.owns_runtime:
            self.controller.fault("recording_shutdown", now_ms=self.clock(),
                                  source_retention_required=True)
        self._retired = True
        self.cancel_save()
        self._writer.retire((self.runtime.pending_capture, self.runtime.incomplete_result,
                             self.controller.snapshot().pending))

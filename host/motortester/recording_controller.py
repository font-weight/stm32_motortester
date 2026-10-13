"""Deterministic, bounded ownership controller for gapped recording sessions.

No disk, Qt, transport calls, clocks, callbacks or hidden worker live here. One
adapter serializes *all* methods and each admitted transport effect under the
same lock as Stop. Disk effects run elsewhere after admission, while the adapter
keeps pumping the runtime. Intents are revocable until admit(); never execute a
cached intent without admitting it. A claimed ordinary command must be reported
with command_submitted() before releasing that lock.

Policy: validated END -> STOP -> matching STOP_ACK -> durable/read-back receipt
-> exact local-only ownership ACK -> deliberate fresh ARM. No RELEASE intent
exists. Auto/Normal repeat; Single finishes after one complete window. Gaps and
host receive time cannot establish continuous acquisition or physical cessation.

Each transport observation contains its available results before STOPPED is
processed. Omitting a retained runtime result violates the adapter contract.
False result admission always leaves ownership with the caller: never ACK it.
Before Record the adapter must check that the runtime owns no old result; its
protocol Snapshot alone does not expose pending Capture/incomplete ownership.
The normal path owns one result; a second slot reserves an extra terminal. The
writer counts as an owner. There is no acquisition while either slot is occupied.
"""
from dataclasses import dataclass, replace
from enum import Enum
import struct
from uuid import UUID

from .model import Capture
from .session import Config, Event, EventKind, Phase, Snapshot, MAX_TIME_MS
from .session_archive import (AbortEvent, Capacity, CommitReceipt, FullSegment,
    PartialSegment, SegmentProvenance, SessionIdentity, SessionOutcome, segment_digest)
from .window import WindowCode, WindowResult


MAX_RECORDING_MS = 60 * 60 * 1000
MAX_RETAINED_RESULTS = 2


class RecordingError(ValueError):
    """Invalid controller call; no device or disk effect is performed here."""


class IntentKind(str, Enum):
    PREPARE = "prepare_spool"
    CONFIGURE = "configure"
    ARM = "arm"
    STOP = "stop"
    PERSIST = "persist"
    ACK_LOCAL = "acknowledge_local_only"
    FINALIZE_READY = "finalize_ready"


class RecordingPhase(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    PERSISTING = "persisting"
    ACKNOWLEDGING = "acknowledging"
    FINALIZE_READY = "finalize_ready"
    FINISHED = "finished"
    FAILED = "failed"


def _require(condition, message="Некорректное состояние записи"):
    if not condition:
        raise RecordingError(message)


def _uint(value, maximum=MAX_TIME_MS, minimum=0):
    _require(type(value) is int and minimum <= value <= maximum,
             "Нужно целое значение в допустимых границах")


@dataclass(frozen=True, slots=True)
class SessionToken:
    """Exact object identity, not equality, identifies one Record operation."""
    epoch: int
    recording_id: str


@dataclass(frozen=True, slots=True)
class RecordingResult:
    """Archive item plus exact source retained by the runtime until local ACK."""
    item: FullSegment | PartialSegment | AbortEvent
    source: Capture | WindowResult | Event

    def __post_init__(self):
        _require(type(self.item) in (FullSegment, PartialSegment, AbortEvent))
        self.item.__post_init__()
        if type(self.item) is FullSegment:
            _require(self.source is self.item.capture)
            _require(len(self.source.samples) == 2048 and self.source.nominal_fs_hz == 50000
                     and 1 <= self.source.sequence <= 0xFFFFFFFE
                     and self.source.trigger_kind in ("signal", "auto_timeout"),
                     "Живой профиль содержит ровно 2048 отсчётов")
        elif type(self.item) is AbortEvent:
            event = self.source
            _require(type(event) is Event and event.kind is EventKind.WINDOW_ABORT
                     and event.frame is not None and event.frame.message_type == 147
                     and event.frame.request_id == 0
                     and event.frame.payload == struct.pack("<IIHH", event.generation,
                         event.window_id, event.reason, 0))
            p = self.item.provenance
            _require(self.item.reason == "ABORT" and self.item.sequence == event.window_id
                     and p.generation == event.generation
                     and p.wire_session_nonce == event.frame.session
                     and p.terminal_kind == "abort" and p.abort_reason_code == event.reason
                     and p.terminal_request_id == event.frame.request_id
                     and p.terminal_sequence == event.frame.sequence)
        else:
            source = self.source
            _require(type(source) is WindowResult and not source.semantic_checks_complete
                     and type(source.reason) is WindowCode
                     and ((source.complete is False and source.samples is None)
                          or (source.complete is True and type(source.samples) is tuple
                              and source.samples == tuple(code for f in source.fragments
                                                          for code in f.samples))))
            b, p = source.begin, self.item.provenance
            _require(b is not None and b.sample_count == 2048
                     and self.item.sequence == b.window_id
                     and self.item.expected_samples == b.sample_count
                     and self.item.reason == source.reason.value
                     and self.item.deferred_checks == source.deferred_checks
                     and self.item.metadata_issues == source.metadata_issues
                     and p.generation == b.generation and p.config_id == b.config_id
                     and self.item.missing_ranges == source.missing_ranges
                     and tuple((f.offset, f.samples) for f in self.item.fragments)
                     == tuple((f.offset, f.samples) for f in source.fragments))
            if source.abort_reason is not None:
                _require(source.reason is WindowCode.ABORT and p.terminal_kind == "abort"
                         and p.abort_reason_code == source.abort_reason)
            else:
                _require(source.reason is not WindowCode.ABORT
                         and p.abort_reason_code is None and p.terminal_kind != "abort")
            if source.samples_crc32 is not None and p.end_samples_crc32 is not None:
                _require(p.end_samples_crc32 == source.samples_crc32)
            if self.item.nominal_fs_hz is not None:
                _require(self.item.nominal_fs_hz ==
                         b.timer_clock_hz / ((b.timer_psc + 1) * (b.timer_arr + 1)))


@dataclass(frozen=True, slots=True)
class PendingRecord:
    ordinal: int
    result: RecordingResult
    sha256: str
    committed: bool = False


@dataclass(frozen=True, slots=True)
class Intent:
    session: SessionToken
    serial: int
    kind: IntentKind
    payload: SessionIdentity | Config | PendingRecord | SessionOutcome | None = None


@dataclass(frozen=True, slots=True)
class RecordingSnapshot:
    phase: RecordingPhase
    session: SessionToken | None
    mode: int | None
    elapsed_ms: int
    stop_fence: bool
    termination_reason: str | None
    stop_evidence: str
    fault: str | None
    generation: int
    config_id: int
    pending: tuple[PendingRecord, ...]
    committed_segments: int
    committed_bytes: int
    capacity: Capacity | None
    writer_pending: bool
    user_stop_requested: bool
    source_retention_required: bool
    finalization_invalidated: bool
    coverage: str = "segmented"
    intersegment_device_gap: str = "unknown"


@dataclass(frozen=True, slots=True)
class _Operation:
    intent: Intent
    claimed: bool = False
    request_id: int = 0
    generation: int = 0


class RecordingController:
    """Single-owner state machine; returned snapshots and intents are immutable.

    prepare completion supplies the archive's issuer token and capacity. A writer
    must return its actual CommitReceipt, never manufacture one from a success
    flag. A failed writer is not retried automatically. FINALIZE_READY means all
    retained results have durable receipts and local ownership has been drained;
    its outcome can still report unknown Stop. It does not mean Save succeeded.
    A changed outcome revokes an unclaimed terminal intent and may issue a new one.
    A changed outcome during a claimed finish is unresolved: stale completion is
    rejected, the spool must be retained, and no automatic replacement is issued.
    """

    def __init__(self, *, max_recording_ms=MAX_RECORDING_MS):
        _uint(max_recording_ms, MAX_RECORDING_MS, 1)
        self._limit = max_recording_ms
        self._epoch = self._serial = 0
        self._now = None
        self._session = self._identity = self._config = None
        self._ops = {}
        self._held = ()
        self._capacity = self._issuer = None
        self._started = 0
        self._ended_ms = None
        self._fence = self._fault = None
        self._user_stop = self._finished = self._prepare_failed = False
        self._settlement_failed = self._transport_failed = self._source_pending = False
        self._finalization_invalidated = False
        self._invalidated_finish = None
        self._safe = True
        self._recovery = False
        self._evidence = "none"
        self._generation = self._config_id = 0
        self._committed = self._bytes = self._admitted_count = 0
        self._last_sequence = None
        self._begun = None
        self._last_abort_key = None
        self._last_recovery_session = None

    @property
    def intents(self):
        """At most one intent of each kind; pending notification is coalescible."""
        return tuple(op.intent for op in self._ops.values() if not op.claimed)

    def snapshot(self):
        if self._session is None:
            phase = RecordingPhase.IDLE
        elif self._finished:
            phase = RecordingPhase.FINISHED
        elif IntentKind.FINALIZE_READY in self._ops:
            phase = RecordingPhase.FINALIZE_READY
        elif self._prepare_failed or self._settlement_failed or self._transport_failed or self._source_pending:
            phase = RecordingPhase.FAILED
        elif IntentKind.STOP in self._ops:
            phase = RecordingPhase.STOPPING
        elif IntentKind.PREPARE in self._ops:
            phase = RecordingPhase.PREPARING
        elif IntentKind.PERSIST in self._ops:
            phase = RecordingPhase.PERSISTING
        elif IntentKind.ACK_LOCAL in self._ops:
            phase = RecordingPhase.ACKNOWLEDGING
        elif IntentKind.CONFIGURE in self._ops or IntentKind.ARM in self._ops:
            phase = RecordingPhase.STARTING
        else:
            phase = RecordingPhase.RECORDING
        return RecordingSnapshot(phase, self._session,
            self._config.mode if self._config else None,
            0 if self._session is None else
            (self._ended_ms if self._ended_ms is not None else self._now) - self._started,
            self._fence is not None, self._fence, self._evidence, self._fault,
            self._generation, self._config_id, self._held,
            self._committed, self._bytes, self._capacity,
            (IntentKind.PERSIST in self._ops or self._invalidated_finish is not None
             or (IntentKind.FINALIZE_READY in self._ops
                 and self._ops[IntentKind.FINALIZE_READY].claimed)),
            self._user_stop, self._source_pending,
            self._finalization_invalidated)

    def _time(self, now_ms):
        _uint(now_ms)
        _require(self._now is None or now_ms >= self._now,
                 "Монотонное время не может уменьшаться")
        self._now = now_ms
        if (self._session is not None and not self._finished and self._fence is None
                and now_ms - self._started >= self._limit):
            self._end("capacity", "elapsed_time_limit")

    def _emit(self, kind, payload=None):
        if kind not in self._ops:
            self._serial += 1
            self._ops[kind] = _Operation(Intent(self._session, self._serial, kind, payload))

    def _current(self, intent, *, claimed=None):
        if type(intent) is not Intent or intent.session is not self._session:
            return None
        op = self._ops.get(intent.kind)
        if op is None or op.intent is not intent or (claimed is not None and op.claimed != claimed):
            return None
        return op

    def _cancel_unclaimed_starts(self):
        for kind in (IntentKind.CONFIGURE, IntentKind.ARM):
            op = self._ops.get(kind)
            if op is not None and not op.claimed:
                del self._ops[kind]

    def _end(self, reason, fault=None):
        if self._fence is None:
            self._fence = reason
            self._ended_ms = self._now
        if fault is not None and self._fault is None:
            self._fault = fault
        # A fault must not become a successful user/Single termination.
        if reason == "error":
            self._fence = reason
        self._cancel_unclaimed_starts()

    def _runtime_matches(self, state):
        return (type(state) is Snapshot and self._identity is not None
                and state.session == UUID(self._identity.session_id).int)

    def _locally_safe(self, state):
        return (self._runtime_matches(state) and not state.stop_barrier
                and (state.phase is Phase.RECOVERY or
                     (state.phase is Phase.READY and not state.window_barrier
                      and not state.pending_request_id and not state.pending_stop_id)))

    def record(self, identity, config, runtime_state, *, now_ms):
        _require(self._session is None or self._finished,
                 "Предыдущая запись ещё не завершена")
        _require(type(identity) is SessionIdentity and type(config) is Config)
        identity.__post_init__()
        config.__post_init__()
        _require(config.profile == 1 and config.source == 0
                 and config.pretrigger_samples in (0, 512, 1024))
        _require(type(runtime_state) is Snapshot and runtime_state.phase is Phase.READY
                 and not runtime_state.pending_request_id and not runtime_state.pending_stop_id
                 and not runtime_state.stop_barrier and not runtime_state.window_barrier)
        _require(UUID(identity.session_id).int == runtime_state.session)
        _require(identity.session_id != self._last_recovery_session,
                 "После восстановления нужен новый nonce и явная запись")
        _require(self._identity is None or identity.recording_id != self._identity.recording_id,
                 "Для новой записи нужен новый идентификатор")
        _uint(now_ms)
        _require(self._now is None or now_ms >= self._now)
        self._epoch += 1
        self._session = SessionToken(self._epoch, identity.recording_id)
        self._identity, self._config = identity, config
        self._started = self._now = now_ms
        self._ended_ms = None
        self._ops, self._held = {}, ()
        self._capacity = self._issuer = None
        self._fence = self._fault = None
        self._user_stop = self._finished = self._prepare_failed = self._settlement_failed = self._transport_failed = self._source_pending = False
        self._finalization_invalidated = False
        self._invalidated_finish = None
        self._safe, self._recovery, self._evidence = True, False, "none"
        self._generation, self._config_id = runtime_state.generation, 0
        self._committed = self._bytes = self._admitted_count = 0
        self._last_sequence = None
        self._begun = None
        self._last_abort_key = None
        self._emit(IntentKind.PREPARE, identity)
        return self._session

    def admit(self, intent, *, now_ms, runtime_state=None):
        """Claim once, serialized with Stop and the immediate transport action.

        Disk work starts outside that lock. ACK_LOCAL additionally requires the
        actual runtime snapshot whose acknowledge() cannot emit RELEASE.
        """
        op = self._current(intent, claimed=False)
        if op is None:
            return False
        self._time(now_ms)
        if self._current(intent, claimed=False) is None:
            self._drive()
            return False
        if intent.kind in (IntentKind.CONFIGURE, IntentKind.ARM):
            if (self._fence or self._recovery or not self._locally_safe(runtime_state)
                    or runtime_state.phase is not Phase.READY or self._held):
                self._end("error", "unsafe_start_admission")
                self._drive()
                return False
            self._safe, self._evidence = False, "unknown"
        elif intent.kind is IntentKind.STOP:
            if (not self._runtime_matches(runtime_state) or self._transport_failed
                    or runtime_state.phase not in (Phase.CAPS, Phase.READY, Phase.ARMED)
                    or runtime_state.stop_barrier):
                return False
        elif intent.kind is IntentKind.ACK_LOCAL:
            if not self._safe or not self._locally_safe(runtime_state):
                self._end("error", "unsafe_local_ack")
                self._safe = False
                self._drive()
                return False
        self._ops[intent.kind] = replace(op, claimed=True)
        return True

    def spool_prepared(self, intent, *, issuer, capacity, now_ms):
        if self._current(intent, claimed=True) is None or intent.kind is not IntentKind.PREPARE:
            return False
        self._time(now_ms)
        _require(issuer is not None and type(capacity) is Capacity)
        capacity.__post_init__()
        del self._ops[intent.kind]
        self._issuer, self._capacity = issuer, capacity
        self._drive()
        return True

    def failed(self, intent, reason, *, now_ms):
        """Definite or ambiguous failure ends the session; no implicit retry."""
        if self._current(intent, claimed=True) is None:
            if intent is self._invalidated_finish:
                self._invalidated_finish = None  # The old worker no longer owns I/O.
            return False
        self._time(now_ms)
        _require(type(reason) is str and 1 <= len(reason) <= 128
                 and all(ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF for c in reason))
        del self._ops[intent.kind]
        self._end("error", reason)
        if intent.kind is IntentKind.PREPARE:
            self._prepare_failed = True
        elif intent.kind in (IntentKind.PERSIST, IntentKind.ACK_LOCAL, IntentKind.FINALIZE_READY):
            self._settlement_failed = True
        elif intent.kind in (IntentKind.CONFIGURE, IntentKind.ARM, IntentKind.STOP, IntentKind.ACK_LOCAL):
            # Failure alone is not runtime recovery or confirmed physical Stop.
            self._safe = False
            self._transport_failed = True
        self._drive()
        return True

    def command_submitted(self, intent, runtime_state, *, now_ms):
        op = self._current(intent, claimed=True)
        if op is None:
            return False
        self._time(now_ms)
        _require(intent.kind in (IntentKind.CONFIGURE, IntentKind.ARM, IntentKind.STOP)
                 and self._runtime_matches(runtime_state) and not op.request_id)
        request = (runtime_state.pending_stop_id if intent.kind is IntentKind.STOP
                   else runtime_state.pending_request_id)
        _uint(request, 0xFFFFFFFE, 1)
        generation = runtime_state.generation
        if intent.kind is IntentKind.ARM:
            generation = request
        elif intent.kind is IntentKind.STOP:
            arm = self._ops.get(IntentKind.ARM)
            if arm is not None and arm.request_id:
                generation = arm.request_id
        self._ops[intent.kind] = replace(op, request_id=request, generation=generation)
        return True

    def stop(self, *, now_ms):
        self._time(now_ms)
        if self._session is not None and not self._finished:
            self._user_stop = True
            self._end("user_stop")
            self._drive()

    def fault(self, reason, *, now_ms, source_retention_required=False):
        """Fail closed if an adapter cannot safely represent an available result."""
        _require(type(reason) is str and 1 <= len(reason) <= 128
                 and all(ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF for c in reason))
        _require(type(source_retention_required) is bool)
        self._time(now_ms)
        if self._session is not None and not self._finished:
            self._source_pending |= source_retention_required
            self._end("error", reason)
            self._drive()

    def tick(self, *, now_ms):
        self._time(now_ms)
        self._drive()

    def observe(self, session, runtime_state, events=(), results=(), *, now_ms):
        """One atomic pump observation, with results offered before stop evidence.

        Returns one admission boolean per result. False keeps source ownership at
        the adapter/runtime. Stale sessions neither advance this clock nor mutate
        a new recording. Only a validated matching STOPPED event proves STOP_ACK.
        """
        _require(type(results) is tuple and len(results) <= MAX_RETAINED_RESULTS)
        if session is not self._session or self._session is None or self._finished:
            return tuple(False for _ in results)
        _require(type(events) is tuple and len(events) <= 256
                 and all(type(event) is Event for event in events))
        _require(type(results) is tuple and len(results) <= MAX_RETAINED_RESULTS
                 and all(type(result) is RecordingResult for result in results))
        _require(self._runtime_matches(runtime_state))
        self._time(now_ms)
        arm = self._ops.get(IntentKind.ARM)
        if (arm and arm.claimed and arm.request_id
                and runtime_state.generation == arm.request_id):
            # ARM_ACK may be suppressed by the runtime while Stop is pending.
            self._generation = arm.request_id
        if self._safe and runtime_state.phase is Phase.ARMED and arm is None:
            self._safe = False
            self._end("error", "unexpected_active_runtime")
        accepted = []
        for result in results:
            try:
                accepted.append(self._retain(result))
            except (ValueError, TypeError, AttributeError):
                self._end("error", "invalid_result")
                self._source_pending = True
                accepted.append(False)
        # A pre-BEGIN ABORT has no WindowResult in db86 runtime. Retain its
        # verified event itself, never manufacture an empty sample result.
        for event in events:
            if event.kind is EventKind.WINDOW_BEGIN:
                self._begun = (event.generation, event.window_id)
            elif event.kind is EventKind.WINDOW_ABORT:
                self._retain_abort_event(event)
        if runtime_state.phase is Phase.RECOVERY:
            self._recovery = self._safe = True
            self._evidence = "unknown"
            self._last_recovery_session = self._identity.session_id
            self._end("error", "transport_recovery")
            for kind in (IntentKind.CONFIGURE, IntentKind.ARM, IntentKind.STOP):
                self._ops.pop(kind, None)
        else:
            for event in events:
                if event.kind is EventKind.CONFIGURED:
                    op = self._ops.get(IntentKind.CONFIGURE)
                    if op and op.claimed and event.request_id == op.request_id and op.request_id:
                        if runtime_state.config != self._config or not runtime_state.config_id:
                            self._end("error", "configuration_mismatch")
                        else:
                            self._config_id = runtime_state.config_id
                            del self._ops[IntentKind.CONFIGURE]
                            if self._fence is None:
                                self._safe = True
                elif event.kind is EventKind.ARMED:
                    op = self._ops.get(IntentKind.ARM)
                    if op and op.claimed and event.request_id == op.request_id and op.request_id:
                        if event.generation != op.request_id or runtime_state.generation != event.generation:
                            self._end("error", "generation_mismatch")
                        else:
                            self._generation = event.generation
                            del self._ops[IntentKind.ARM]
                elif event.kind is EventKind.STOPPED:
                    op = self._ops.get(IntentKind.STOP)
                    if (op and op.claimed and op.request_id
                            and event.request_id == op.request_id
                            and event.generation == op.generation
                            and runtime_state.generation == event.generation
                            and self._locally_safe(runtime_state)):
                        self._generation = event.generation
                        self._safe, self._evidence = True, "stop_ack"
                        for kind in (IntentKind.CONFIGURE, IntentKind.ARM, IntentKind.STOP):
                            self._ops.pop(kind, None)
                elif event.kind is EventKind.COMMAND_ERROR:
                    self._end("error", "command_error")
        self._drive()
        return tuple(accepted)

    def _retain_abort_event(self, event):
        key = (event.generation, event.window_id,
               event.frame.sequence if event.frame is not None else None)
        if key == self._last_abort_key:
            return
        matching = any((p.result.item.capture.sequence if type(p.result.item) is FullSegment
                        else p.result.item.sequence) == event.window_id
                       and p.result.item.provenance.generation == event.generation
                       for p in self._held)
        if matching:
            self._last_abort_key = key
            self._begun = None
            return
        if self._begun == (event.generation, event.window_id):
            self._end("error", "missing_partial_result")
            self._source_pending = True
            return
        try:
            _require(event.frame is not None)
            provenance = SegmentProvenance(generation=event.generation,
                config_id=self._config_id, acquisition_mode=self._config.mode,
                auto_timeout_ms=self._config.auto_timeout_ms,
                host_elapsed_ms=self._now - self._started, wire_version="0.1",
                start_operation="arm", wire_session_nonce=event.frame.session,
                config_payload_hex=self._config.payload().hex(),
                terminal_kind="abort", terminal_request_id=event.frame.request_id,
                terminal_sequence=event.frame.sequence, abort_reason_code=event.reason,
                unavailable_reason="no_begin")
            result = RecordingResult(AbortEvent("ABORT", event.window_id, provenance), event)
            if self._retain(result):
                self._last_abort_key = key
                self._begun = None
        except (ValueError, TypeError, AttributeError, struct.error):
            self._end("error", "invalid_abort_event")
            self._source_pending = True

    def _retain(self, result):
        for pending in self._held:
            if pending.result.source is result.source:
                return True
        if len(self._held) >= MAX_RETAINED_RESULTS:
            self._end("error", "terminal_reserve_exhausted")
            self._source_pending = True
            return False
        final = self._ops.get(IntentKind.FINALIZE_READY)
        if final is not None:
            self._end("error", "late_terminal")
            if final.claimed:
                self._source_pending = True
                return False
            del self._ops[IntentKind.FINALIZE_READY]
        result.__post_init__()
        item = result.item
        provenance = item.provenance
        if (self._issuer is None or not self._config_id or
                provenance.generation != self._generation or
                provenance.config_id != self._config_id or
                provenance.acquisition_mode != self._config.mode or
                provenance.auto_timeout_ms != self._config.auto_timeout_ms or
                (provenance.wire_session_nonce is not None and
                 provenance.wire_session_nonce != UUID(self._identity.session_id).int) or
                (provenance.config_payload_hex is not None and
                 provenance.config_payload_hex != self._config.payload().hex()) or
                (provenance.host_elapsed_ms is not None and
                 provenance.host_elapsed_ms > self._now - self._started)):
            self._end("error", "result_identity_mismatch")
            self._source_pending = True
            return False
        sequence = item.capture.sequence if type(item) is FullSegment else item.sequence
        if sequence is not None and self._last_sequence is not None and sequence <= self._last_sequence:
            self._end("error", "result_sequence_mismatch")
            self._source_pending = True
            return False
        if type(item) is FullSegment:
            self._identity.validate_capture(item.capture)
            _require((item.capture.trigger_edge, item.capture.trigger_level_code,
                      item.capture.pretrigger_samples) ==
                     ("rising" if self._config.edge == 0 else "falling",
                      self._config.level_code, self._config.pretrigger_samples))
            if item.capture.trigger_kind == "auto_timeout" and self._config.mode != 0:
                self._end("error", "trigger_mode_mismatch")
                self._source_pending = True
                return False
        if type(item) is PartialSegment:
            b = result.source.begin
            _require((b.profile, b.pretrigger_samples, b.source, b.edge, b.level_code) ==
                     (self._config.profile, self._config.pretrigger_samples,
                      self._config.source, self._config.edge, self._config.level_code))
        digest = segment_digest(item)
        self._held += (PendingRecord(self._admitted_count, result, digest),)
        self._admitted_count += 1
        if sequence is not None:
            self._last_sequence = sequence
        if len(self._held) > 1:
            self._end("error", "extra_inflight_terminal")
        elif type(item) is not FullSegment:
            self._end("error" if self._fence is None else self._fence,
                      "incomplete_result" if self._fence is None else None)
        elif self._config.mode == 2:
            self._end("single_complete")
        return True

    def committed(self, intent, receipt, *, now_ms):
        if self._current(intent, claimed=True) is None or intent.kind is not IntentKind.PERSIST:
            return False
        self._time(now_ms)
        pending = self._held[0]
        try:
            valid = type(receipt) is CommitReceipt and receipt.matches(
                self._identity, pending.ordinal, issuer=self._issuer,
                item=pending.result.item, sha256=pending.sha256)
            if valid:
                receipt.capacity_after.__post_init__()
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            self._end("error", "invalid_commit_receipt")
            self._settlement_failed = True
            del self._ops[IntentKind.PERSIST]
            self._drive()
            return False
        self._capacity = receipt.capacity_after
        self._held = (replace(pending, committed=True),) + self._held[1:]
        self._committed += 1
        self._bytes += receipt.entry.byte_count
        del self._ops[IntentKind.PERSIST]
        self._drive()
        return True

    def acknowledged(self, intent, runtime_state, *, now_ms):
        if self._current(intent, claimed=True) is None or intent.kind is not IntentKind.ACK_LOCAL:
            return False
        self._time(now_ms)
        _require(self._held and self._held[0].committed)
        if not self._locally_safe(runtime_state):
            self._end("error", "local_ack_state_changed")
            self._safe = False
            self._drive()
            return False
        del self._ops[IntentKind.ACK_LOCAL]
        self._held = self._held[1:]
        self._drive()
        return True

    def finalized(self, intent, *, now_ms):
        """Accept the exact still-current terminal operation; Save is separate.

        False forbids publishing its report as this recording's successful
        completion. Its exact callback may clear writer_pending only; it cannot
        clear finalization_invalidated or accept the superseded outcome.
        A superseded claimed finish may already have written its old
        outcome: preserve that spool for explicit recovery, never retry its finish
        automatically. This acceptance is the session-closing linearization point.
        """
        if self._current(intent, claimed=True) is None or intent.kind is not IntentKind.FINALIZE_READY:
            if intent is self._invalidated_finish:
                self._invalidated_finish = None  # Settle I/O, never accept the old outcome.
            return False
        self._time(now_ms)
        _require(not self._held and not self._source_pending
                 and not self._ops.keys() - {IntentKind.FINALIZE_READY})
        self._ops.clear()
        self._finished = True
        return True

    def _reconcile_finalization(self):
        op = self._ops.get(IntentKind.FINALIZE_READY)
        if op is None:
            return
        outcome = SessionOutcome(self._fence, self._evidence, self._fault or "")
        if (op.intent.payload == outcome and not self._held and not self._source_pending
                and self._safe and not self._settlement_failed):
            return
        del self._ops[IntentKind.FINALIZE_READY]
        if op.claimed:
            # An off-thread finish may have committed its now-superseded payload.
            # Revocation rejects its eventual callback, but cannot undo disk I/O.
            # Keep the spool, expose this unresolved outcome, and never schedule a
            # second finish that could overwrite or disguise the first operation.
            self._finalization_invalidated = True
            self._invalidated_finish = op.intent
            self._settlement_failed = True

    def _drive(self):
        if self._session is None or self._finished:
            return
        self._reconcile_finalization()
        if self._prepare_failed:
            return
        needs_stop = self._fence is not None or bool(self._held)
        if needs_stop and not self._safe and not self._recovery and not self._transport_failed:
            self._emit(IntentKind.STOP)
        if not self._safe or self._issuer is None or self._settlement_failed:
            return
        if self._held:
            pending = self._held[0]
            if pending.committed:
                self._emit(IntentKind.ACK_LOCAL, pending)
            else:
                self._emit(IntentKind.PERSIST, pending)
            return
        if self._fence is not None:
            if not self._ops and not self._source_pending:
                outcome = SessionOutcome(self._fence, self._evidence, self._fault or "")
                self._emit(IntentKind.FINALIZE_READY, outcome)
            return
        if self._ops:
            return
        if not self._capacity.can_reserve_windows(MAX_RETAINED_RESULTS):
            self._end("capacity", "archive_capacity")
            self._drive()
        elif not self._config_id:
            self._emit(IntentKind.CONFIGURE, self._config)
        else:
            self._emit(IntentKind.ARM)

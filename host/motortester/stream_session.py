"""Bounded, serialized host decisions; normalized inputs are caller claims.

No I/O, threads, clocks, wire validation or acquisition occurs in this module.
A single owner calls methods in event order and executes returned jobs elsewhere.
"""
from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json

from .stream_model import (FailureEvidence, StreamBlock, StreamError,
                           StreamIdentity, StreamStart, StreamTerminal, UnboundOutcome,
                           U64_MAX, require, text, uint)


@dataclass(frozen=True, slots=True)
class SessionLimits:
    max_raw_bytes: int = 256 * 1024
    max_raw_items: int = 256

    def __post_init__(self):
        uint(self.max_raw_bytes, 1024 * 1024, minimum=2)
        uint(self.max_raw_items, 4096, minimum=1)


def _exact(value, kind):
    require(type(value) is kind, "Неверный тип нормализованного объекта")
    value.__post_init__()
    return deepcopy(value)


def _hex(value, length):
    require(type(value) is str and len(value) == length and
            all(c in "0123456789abcdef" for c in value), "Неверный digest/token")


@dataclass(frozen=True, slots=True)
class SourceAction:
    attempt_id: str
    action_id: int
    kind: str
    identity: StreamIdentity

    def __post_init__(self):
        text(self.attempt_id, 128)
        uint(self.action_id, minimum=1)
        require(type(self.kind) is str and self.kind in ("start", "stop"),
                "Неизвестное действие источника")
        _exact(self.identity, StreamIdentity)


@dataclass(frozen=True, slots=True)
class SourceStarted:
    attempt_id: str
    start_action_id: int
    identity: StreamIdentity
    first_sample_index: int

    def __post_init__(self):
        text(self.attempt_id, 128)
        uint(self.start_action_id, minimum=1)
        _exact(self.identity, StreamIdentity)
        uint(self.first_sample_index)


@dataclass(frozen=True, slots=True)
class SourceEnd:
    terminal_id: str
    terminal: StreamTerminal

    def __post_init__(self):
        text(self.terminal_id, 128)
        _exact(self.terminal, StreamTerminal)


@dataclass(frozen=True, slots=True)
class StopAcknowledgement:
    attempt_id: str
    stop_action_id: int
    identity: StreamIdentity
    terminal_id: str
    end_sample_index: int
    request_id: int
    quiescence_reported: bool

    def __post_init__(self):
        text(self.attempt_id, 128)
        uint(self.stop_action_id, minimum=1)
        _exact(self.identity, StreamIdentity)
        text(self.terminal_id, 128)
        uint(self.end_sample_index)
        uint(self.request_id)
        require(type(self.quiescence_reported) is bool, "Неверный quiescence_reported")


@dataclass(frozen=True, slots=True)
class StorageJob:
    attempt_id: str
    operation_id: int
    kind: str
    identity: StreamIdentity
    recording_id: str
    start_policy: str
    token_id: str | None
    start: StreamStart | None
    block: StreamBlock | None
    terminal: StreamTerminal | None
    unbound: UnboundOutcome | None
    expected_accepted_end_sample_index: int | None
    prefix_sha256: str

    @property
    def sha256(self):
        # Hash exact raw content, not Python object identity or a mutable array.
        obj = asdict(self)
        if self.block is not None:
            obj["block"]["raw_u16le"] = hashlib.sha256(self.block.raw_u16le).hexdigest()
        payload = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode("ascii")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class JobCompletion:
    """Correlated caller report, never proof of disk readback or physical I/O."""
    attempt_id: str
    operation_id: int
    kind: str
    identity: StreamIdentity
    recording_id: str
    job_sha256: str
    token_id: str
    accepted_end_sample_index: int | None
    checkpointed_end_sample_index: int | None

    def __post_init__(self):
        text(self.attempt_id, 128)
        uint(self.operation_id, minimum=1)
        require(type(self.kind) is str and self.kind in
                ("prepare", "bind_first_block", "bind_start", "append",
                 "checkpoint", "finish", "finish_unbound"), "Неизвестная операция")
        _exact(self.identity, StreamIdentity)
        text(self.recording_id, 128)
        _hex(self.job_sha256, 64)
        _hex(self.token_id, 32)
        for value in (self.accepted_end_sample_index, self.checkpointed_end_sample_index):
            if value is not None:
                uint(value)

    @classmethod
    def for_job(cls, job, *, token_id, accepted_end=None, checkpointed_end=None):
        """Call only after the exact job's storage method returned successfully."""
        require(type(job) is StorageJob, "Ожидается StorageJob")
        return cls(job.attempt_id, job.operation_id, job.kind, deepcopy(job.identity),
                   job.recording_id, job.sha256, token_id, accepted_end, checkpointed_end)


@dataclass(frozen=True, slots=True)
class JobFailure:
    """A failed prepare has no readiness token and no successful endpoints."""
    attempt_id: str
    operation_id: int
    kind: str
    identity: StreamIdentity
    recording_id: str
    job_sha256: str
    token_id: str | None
    failure: FailureEvidence

    def __post_init__(self):
        text(self.attempt_id, 128)
        uint(self.operation_id, minimum=1)
        require(type(self.kind) is str and self.kind in
                ("prepare", "bind_first_block", "bind_start", "append",
                 "checkpoint", "finish", "finish_unbound"), "Неизвестная операция")
        _exact(self.identity, StreamIdentity)
        text(self.recording_id, 128)
        _hex(self.job_sha256, 64)
        if self.kind == "prepare":
            require(self.token_id is None, "Failed prepare не выдаёт token")
        else:
            _hex(self.token_id, 32)
        _exact(self.failure, FailureEvidence)

    @classmethod
    def for_job(cls, job, failure):
        require(type(job) is StorageJob, "Ожидается StorageJob")
        return cls(job.attempt_id, job.operation_id, job.kind, deepcopy(job.identity),
                   job.recording_id, job.sha256, job.token_id, deepcopy(failure))


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    identity: StreamIdentity
    source_started: bool
    source_first_sample_index: int | None
    received_end_sample_index: int | None
    selected_start_sample_index: int | None
    queued_end_sample_index: int | None
    accepted_end_sample_index: int | None
    checkpointed_end_sample_index: int | None
    finalized_end_sample_index: int | None
    raw_bytes_held: int
    raw_items_held: int
    pending_operation_id: int | None
    source_end: SourceEnd | None
    stop_acknowledgement: StopAcknowledgement | None
    first_failure: FailureEvidence | None
    stop_requested: bool
    admission_fenced: bool
    writer_fenced: bool
    finalized: bool
    finalization_started: bool
    finalization_unresolved: bool
    clean_completion_reported: bool
    unbound_reason: str | None
    latest_display: StreamBlock | None


class StreamSession:
    """One explicit attempt and source identity. There is no reset/retry method."""

    def __init__(self, identity, attempt_id, limits=SessionLimits()):
        self._identity = _exact(identity, StreamIdentity)
        text(attempt_id, 128)
        self._attempt_id = attempt_id
        self._limits = _exact(limits, SessionLimits)
        self._control = self._start_action = self._stop_action = None
        self._start_dispatched = self._stop_dispatched = False
        self._source_first = self._received_end = None
        self._end = self._ack = self._latest = None
        self._recording_id = self._policy = self._token = None
        self._start = None
        self._prepared = self._bound = self._finalized = False
        self._finalization_started = False
        self._cancelled = self._selection_cancelled = False
        self._fenced = self._writer_fenced = self._drain_closed = False
        self._first_failure = None
        self._queue = deque()
        self._raw_bytes = 0
        self._queued_end = self._accepted_end = self._checkpointed_end = None
        self._finalized_end = self._unbound_reason = None
        self._accepted_hash = hashlib.sha256()
        self._pending = None
        self._operation_id = 0
        self._checkpoint_requested = False

    def _reject(self, code, detail, *, writer=False):
        self._fail(FailureEvidence(code, detail), writer=writer)
        raise StreamError(detail)

    def _check(self, condition, code, detail, *, writer=False):
        if not condition:
            self._reject(code, detail, writer=writer)

    def _input(self, value, kind, code="invalid_input", *, writer=False):
        try:
            return _exact(value, kind)
        except (StreamError, AttributeError, TypeError, ValueError):
            self._reject(code, "Некорректный вход " + kind.__name__, writer=writer)

    def _fail(self, evidence, *, writer=False):
        if self._first_failure is None:
            self._first_failure = deepcopy(evidence)
        self._fenced = True
        self._selection_cancelled = self._start is None
        self._writer_fenced |= writer
        self._request_stop()

    def _observations_open(self):
        # Finish freezes source/cancel decisions. Later unrelated events cannot
        # rewrite that artifact; only its own storage result can settle it.
        if self._finalization_started:
            raise StreamError("Исход уже передан на завершение; наблюдение вне его границ")

    def _request_start(self):
        self._start_action = SourceAction(self._attempt_id, 1, "start", self._identity)
        self._control = self._start_action

    def _request_stop(self):
        if self._stop_action is not None:
            return
        if not self._start_dispatched:
            # A not-yet-dispatched Start intent is cancelled, never executed later.
            self._control = None
            self._drain_closed = True
            return
        self._stop_action = SourceAction(self._attempt_id, 2, "stop", self._identity)
        self._control = self._stop_action

    def take_control_action(self):
        """One reserved slot, independent of raw/storage saturation; no I/O."""
        action, self._control = self._control, None
        if action is not None:
            if action.kind == "start":
                self._start_dispatched = True
            else:
                self._stop_dispatched = True
        return deepcopy(action)

    def preview(self):
        self._observations_open()
        self._check(self._start_action is None and self._recording_id is None and
                    not self._drain_closed and not self._fenced,
                    "invalid_input", "Повторный/поздний Preview запрещён")
        self._request_start()

    def record(self, recording_id):
        self._observations_open()
        try:
            text(recording_id, 128)
        except StreamError:
            self._reject("invalid_input", "Некорректный recording_id")
        self._check(self._recording_id is None and not self._drain_closed and
                    self._end is None and self._stop_action is None and not self._fenced,
                    "invalid_input", "Повторный/поздний Record запрещён")
        self._recording_id = recording_id
        self._policy = "source_start" if self._start_action is None else "next_block"

    def source_started(self, observation):
        self._observations_open()
        observation = self._input(observation, SourceStarted)
        self._check(self._start_dispatched and self._source_first is None and
                    self._end is None and not self._drain_closed and
                    observation.attempt_id == self._attempt_id and
                    observation.start_action_id == self._start_action.action_id and
                    observation.identity == self._identity,
                    "input_identity", "START не соответствует единственному действию")
        self._source_first = self._received_end = observation.first_sample_index
        if self._policy == "source_start" and not self._selection_cancelled and not self._fenced:
            self._check(self._prepared, "invalid_input", "START до готовности storage")
            self._select(observation.first_sample_index, "caller_validated_source_start")

    def _select(self, first, evidence):
        self._start = StreamStart(self._identity, first, self._recording_id, evidence)
        self._queued_end = first

    def receive(self, block):
        self._observations_open()
        block = self._input(block, StreamBlock)
        self._check(block.identity == self._identity, "input_identity", "Изменилась identity")
        self._check(self._source_first is not None and self._end is None and
                    not self._drain_closed and not self._fenced,
                    "input_order", "DATA вне открытого нормализованного потока")
        self._check(block.first_sample_index == self._received_end,
                    "input_order", "Пропуск, повтор или перестановка DATA")
        self._received_end = block.end_sample_index
        self._latest = block
        selected = self._start is not None
        selecting = (self._prepared and self._policy == "next_block" and
                     not selected and not self._selection_cancelled)
        if not (selected or selecting):
            return
        # Capacity is checked BEFORE selecting R. In-flight raw stays in this deque.
        self._check(self._raw_bytes + len(block.raw_u16le) <= self._limits.max_raw_bytes,
                    "capacity_raw", "Исчерпана очередь raw bytes")
        self._check(len(self._queue) < self._limits.max_raw_items,
                    "capacity_blocks", "Исчерпана очередь raw items")
        if selecting:
            self._select(block.first_sample_index, "caller_next_whole_validated_block")
        self._queue.append(block)
        self._raw_bytes += len(block.raw_u16le)
        self._queued_end = block.end_sample_index

    def stop(self):
        self._observations_open()
        self._check(not self._finalized and self._stop_action is None and
                    not self._drain_closed and
                    (self._start_action is not None or self._recording_id is not None),
                    "invalid_input", "Повторный/неактивный Stop")
        if self._start is None:
            self._selection_cancelled = True
        self._request_stop()

    def cancel(self):
        self._observations_open()
        self._check(not self._cancelled and not self._finalized and
                    (self._recording_id is not None or self._start_action is not None),
                    "invalid_input", "Повторная/неактивная отмена")
        self._cancelled = self._selection_cancelled = True
        if self._start is not None:
            self._fail(FailureEvidence("unknown", "recording cancelled after selection"))
        else:
            self._request_stop()

    def fault(self, failure):
        self._observations_open()
        self._fail(self._input(failure, FailureEvidence))

    def drain_failed(self, failure):
        """Adapter's bounded drain/timeout failure; no timer constant is invented."""
        self.fault(failure)
        self._drain_closed = True

    def source_end(self, observation):
        self._observations_open()
        observation = self._input(observation, SourceEnd, "invalid_terminal")
        terminal = observation.terminal
        self._check(self._end is None and self._source_first is not None and
                    not self._drain_closed and terminal.identity == self._identity,
                    "invalid_terminal", "END вне соответствующего потока")
        self._check(terminal.terminal_observed and
                    ((not self._fenced and terminal.end_sample_index == self._received_end) or
                     (self._fenced and terminal.end_sample_index >= self._received_end)),
                    "invalid_terminal", "END не соответствует received-префиксу")
        self._check(terminal.reason != "stopped" or
                    (terminal.stop_requested and terminal.stop_request_id is not None),
                    "invalid_terminal", "Stopped END без идентификатора Stop")
        self._end = observation
        self._selection_cancelled |= self._start is None
        if terminal.failure is not None:
            self._fail(terminal.failure)
        elif self._stop_action is None:
            self._fail(FailureEvidence("invalid_terminal", "clean END without requested Stop"))
        self._match_ack()

    def stop_acknowledged(self, observation):
        self._observations_open()
        observation = self._input(observation, StopAcknowledgement, "invalid_terminal")
        self._check(self._ack is None and self._stop_dispatched and
                    observation.attempt_id == self._attempt_id and
                    observation.identity == self._identity and
                    observation.stop_action_id == self._stop_action.action_id,
                    "invalid_terminal", "ACK не соответствует единственному Stop")
        self._ack = observation
        if not observation.quiescence_reported:
            self._fail(FailureEvidence("source_error", "quiescence not reported"))
        self._match_ack()

    def _match_ack(self):
        if self._ack is None or self._end is None:
            return
        terminal = self._end.terminal
        self._check(self._ack.terminal_id == self._end.terminal_id and
                    self._ack.end_sample_index == terminal.end_sample_index and
                    (terminal.stop_request_id is None or
                     terminal.stop_request_id == self._ack.request_id),
                    "invalid_terminal", "ACK/END не согласованы")

    def request_checkpoint(self):
        self._observations_open()
        self._check(self._bound and not self._writer_fenced and not self._finalized and
                    not self._checkpoint_requested,
                    "invalid_input", "Checkpoint вне активной записи или уже ожидается")
        self._checkpoint_requested = True

    def _can_finish(self):
        return (self._drain_closed or
                (self._end is not None and
                 (self._first_failure is not None or self._cancelled or
                  (self._ack is not None and self._ack.quiescence_reported))))

    def _terminal(self):
        source = self._end.terminal if self._end is not None else None
        failure = self._first_failure
        reason = ("capacity" if failure is not None and failure.code.startswith("capacity_")
                  else "host_failure" if failure is not None else "stopped")
        if source is not None and failure == source.failure and failure is not None:
            reason = source.reason
        return StreamTerminal(
            self._identity, self._accepted_end, reason, source is not None,
            None if source is None else source.device_generated_end_sample_index,
            None if source is None else source.device_transmitted_end_sample_index,
            None if source is None else source.loss_samples,
            "host_selected_prefix" if source is None or failure is not None else source.boundary_evidence,
            failure, self._stop_action is not None,
            None if self._ack is None else self._ack.request_id)

    def _unbound(self):
        source = self._end.terminal if self._end is not None else None
        reason = ("failed_unbound" if self._first_failure is not None else
                  "cancelled_unbound" if self._cancelled or self._selection_cancelled and source is None
                  else "ended_unbound")
        return UnboundOutcome(
            reason, self._first_failure, source is not None,
            None if source is None else source.device_generated_end_sample_index,
            None if source is None else source.device_transmitted_end_sample_index,
            None if source is None else source.loss_samples,
            "no_established_recording_start", self._stop_action is not None,
            None if self._ack is None else self._ack.request_id)

    def next_storage_job(self):
        """Dispatch once. A pending job is never returned again or retried."""
        if (self._recording_id is None or self._pending is not None or
                self._writer_fenced or self._finalized):
            return None
        kind = None
        block = terminal = unbound = None
        expected = self._accepted_end
        digest = self._accepted_hash.copy()
        if not self._prepared:
            kind = "prepare"
        elif not self._bound and self._start is not None:
            kind = "bind_start" if self._policy == "source_start" else "bind_first_block"
            expected = self._start.first_sample_index
            if kind == "bind_first_block":
                block = self._queue[0]
        elif self._checkpoint_requested and self._bound:
            kind = "checkpoint"
        elif self._queue:
            kind, block = "append", self._queue[0]
        elif self._can_finish():
            if self._bound:
                kind, terminal = "finish", self._terminal()
            else:
                kind, unbound = "finish_unbound", self._unbound()
        if kind is None:
            return None
        if block is not None:
            expected = block.end_sample_index
            digest.update(block.raw_u16le)
        self._check(self._operation_id < U64_MAX, "capacity_blocks", "Исчерпан счётчик операций",
                    writer=True)
        self._operation_id += 1
        self._finalization_started |= kind in ("finish", "finish_unbound")
        self._pending = StorageJob(
            self._attempt_id, self._operation_id, kind, self._identity,
            self._recording_id, self._policy, self._token,
            self._start if kind in ("bind_start", "bind_first_block") else None,
            block, terminal, unbound, expected, digest.hexdigest())
        return deepcopy(self._pending)

    def storage_completed(self, completion):
        if self._finalized:
            raise StreamError("Повторный callback после завершения")
        completion = self._input(completion, JobCompletion, "host_io", writer=True)
        job = self._pending
        self._check(job is not None and
                    completion.attempt_id == self._attempt_id and
                    completion.operation_id == job.operation_id and completion.kind == job.kind and
                    completion.identity == self._identity and completion.recording_id == self._recording_id and
                    completion.job_sha256 == job.sha256,
                    "host_io", "Устаревшее, повторное или чужое завершение job", writer=True)
        self._check(completion.accepted_end_sample_index == job.expected_accepted_end_sample_index,
                    "host_io", "Неверная accepted-граница", writer=True)
        if job.kind == "prepare":
            self._check(completion.checkpointed_end_sample_index is None,
                        "host_io", "R не установлен при prepare", writer=True)
        else:
            self._check(completion.token_id == self._token, "host_io", "Неверный token", writer=True)
            end = job.expected_accepted_end_sample_index
            checkpoint = completion.checkpointed_end_sample_index
            if end is None:
                self._check(checkpoint is None, "host_io", "R не установлен", writer=True)
            elif job.kind in ("checkpoint", "finish"):
                self._check(checkpoint == end, "host_io", "Checkpoint/finalize не завершён", writer=True)
            else:
                first = self._start.first_sample_index
                allowed = {first if self._checkpointed_end is None else self._checkpointed_end,
                           first if self._accepted_end is None else self._accepted_end, end}
                self._check(checkpoint in allowed and type(checkpoint) is int and
                            first <= checkpoint <= end,
                            "host_io", "Неверная checkpointed-граница", writer=True)
        self._pending = None
        if self._writer_fenced:
            # This exact late callback settles ownership, not failed state.
            raise StreamError("Job завершился после fence; границы не продвигаются")
        if job.kind == "prepare":
            self._token = completion.token_id
            self._prepared = True
            if self._policy == "source_start" and not self._selection_cancelled and not self._fenced:
                self._request_start()
        else:
            self._accepted_end = completion.accepted_end_sample_index
            self._checkpointed_end = completion.checkpointed_end_sample_index
            if job.kind in ("bind_start", "bind_first_block"):
                self._bound = True
            if job.block is not None:
                block = self._queue.popleft()
                self._raw_bytes -= len(block.raw_u16le)
                self._accepted_hash.update(block.raw_u16le)
            if job.kind == "checkpoint":
                self._checkpoint_requested = False
            if job.kind in ("finish", "finish_unbound"):
                self._finalized = True
                self._finalized_end = self._accepted_end
                self._unbound_reason = None if job.unbound is None else job.unbound.reason

    def storage_failed(self, completion):
        """Correlate the failed operation, but never advance either endpoint."""
        if self._finalized:
            raise StreamError("Повторный callback после завершения")
        completion = self._input(completion, JobFailure, "host_io", writer=True)
        job = self._pending
        self._check(job is not None and
                    completion.attempt_id == self._attempt_id and
                    completion.operation_id == job.operation_id and completion.kind == job.kind and
                    completion.identity == self._identity and completion.recording_id == self._recording_id and
                    completion.job_sha256 == job.sha256 and
                    (job.kind == "prepare" or completion.token_id == self._token),
                    "host_io", "Отказ чужого или завершённого job", writer=True)
        self._pending = None
        self._fail(completion.failure, writer=True)

    def snapshot(self):
        clean = (self._finalized and self._finalized_end is not None and
                 self._first_failure is None and self._end is not None and
                 self._ack is not None and self._ack.quiescence_reported and
                 self._terminal().clean_stop_reported)
        return deepcopy(SessionSnapshot(
            self._identity, self._source_first is not None, self._source_first,
            self._received_end, None if self._start is None else self._start.first_sample_index,
            self._queued_end, self._accepted_end, self._checkpointed_end, self._finalized_end,
            self._raw_bytes, len(self._queue), None if self._pending is None else self._pending.operation_id,
            self._end, self._ack, self._first_failure, self._stop_action is not None,
            self._fenced, self._writer_fenced, self._finalized, self._finalization_started,
            self._finalization_started and self._writer_fenced and not self._finalized, clean,
            self._unbound_reason, self._latest))

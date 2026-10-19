"""Isolated byte -> normalized session -> real journal join, with explicit clocks.

One receiver owner calls controller methods. Only the separate executor touches
storage. No thread, transport, scheduler, reconnect or application is created.
"""
from dataclasses import dataclass, replace
import hashlib
from threading import Lock, get_ident

from . import stream_endpoint as wire
from .stream_model import (FailureEvidence, StreamBlock, StreamError,
                           StreamIdentity, StreamPreparation, StreamTerminal,
                           require, text)
from .stream_recording import PreparedStreamWriter, StreamLimits
from .stream_provenance import (ProvenanceError, admit_profile, validate_negotiation,
                               admit_fake_diagnostic_declaration,
                               validate_fake_diagnostic_negotiation, rehearsal_identity_metadata)
from .stream_session import (JobCompletion, JobFailure, SessionLimits,
                             SourceEnd, SourceStarted, StopAcknowledgement,
                             StreamSession)
from .stream_wire import Kind


@dataclass(frozen=True, slots=True)
class StorageStatus:
    operation_id: int | None
    operation: str | None
    returned: bool
    closed: bool
    failure: FailureEvidence | None
    accepted_end: int | None
    checkpointed_end: int | None
    selected_sha256: str


@dataclass(frozen=True, slots=True)
class MailboxState:
    operation_id: int | None
    running: bool
    result_pending: bool
    close_requested: bool
    close_acknowledged: bool | None


@dataclass(frozen=True, slots=True)
class _StorageResult:
    job: object
    completion: object
    status: StorageStatus


class StorageExecutor:
    """Single writer owner. Private original capability never leaves this object.

    execute_one is the sole disk entrypoint. A short mailbox lock protects only
    ownership, never I/O. No recovery reader can manufacture a successful return.
    No completion is accepted from a caller; the receiver consumes the owned slot.
    """
    def __init__(self, path, limits, attempt_id, identity):
        self._path, self._limits = path, limits
        self._prepared = self._writer = self._token = None
        self._digest = hashlib.sha256()
        self._last_operation = 0
        self._closed = False
        self._close_result = None
        self._status = StorageStatus(None, None, False, False, None, None, None,
                                     self._digest.hexdigest())
        self._published_status = self._status
        self._attempt_id, self._identity = attempt_id, identity
        self._lock = Lock()
        self._work = self._running_work = self._result = self._issued_result = None
        self._running = self._close_requested = False
        self._close_acknowledged = None

    def __copy__(self):
        raise StreamError("Storage owner cannot be copied")

    def __deepcopy__(self, memo):
        raise StreamError("Storage owner cannot be copied")

    @property
    def status(self):
        with self._lock:
            return self._published_status

    def snapshot(self):
        with self._lock:
            job = self._work or self._running_work
            return MailboxState(None if job is None else job.operation_id, self._running,
                                self._result is not None, self._close_requested,
                                self._close_acknowledged)

    def _submit(self, job):
        with self._lock:
            require(not self._close_requested and not self._running and self._work is None and
                    self._result is None and not self._published_status.closed,
                    "Storage mailbox occupied or closed")
            require(job.attempt_id == self._attempt_id and job.identity == self._identity,
                    "Storage mailbox attempt identity")
            self._work = job

    def _request_close(self):
        with self._lock:
            self._close_requested = True

    def _take_result(self):
        with self._lock:
            if self._result is None and self._issued_result is None:
                return None
            result = self._result
            owned = result is self._issued_result and type(result) is _StorageResult
            self._result = self._issued_result = None
            return result, owned

    def execute_one(self):
        """Claim one work/close request and publish one result; no owner callback.

        May block in OS I/O. Concurrent invocations never execute a second job.
        A close request cannot interrupt a running call; it is checked on return.
        """
        with self._lock:
            if self._running or self._result is not None:
                return False
            if self._work is None and (not self._close_requested or self._close_acknowledged is not None):
                return False
            job, self._work = self._work, None
            self._running, self._running_work = True, job
            abandon = self._close_requested
        completion = None
        try:
            if job is not None:
                if abandon:
                    failure = FailureEvidence("host_io", "reserved storage job abandoned", operation=job.kind)
                    completion = JobFailure.for_job(job, failure)
                    self._status = replace(self._status, operation_id=job.operation_id,
                                           operation=job.kind, returned=False, failure=failure)
                else:
                    completion = self._execute(job)
        except Exception as exc:
            failure = FailureEvidence("host_io", "storage executor failed: " + type(exc).__name__,
                                      operation=None if job is None else job.kind)
            completion = None if job is None else JobFailure.for_job(job, failure)
            self._status = replace(self._status, returned=False, failure=failure)
            self._close()
        # Never retain the mailbox lock while closing or flushing a file.
        with self._lock:
            close_requested = self._close_requested
        close_ack = self._close() if close_requested else None
        result = _StorageResult(job, completion, self._status)
        with self._lock:
            self._running, self._running_work = False, None
            self._published_status = self._status
            self._result = self._issued_result = result
            if close_requested:
                self._close_acknowledged = close_ack
        return True

    def _execute(self, job):
        require(not self._closed and job.operation_id > self._last_operation,
                "Storage job already consumed or writer closed")
        self._last_operation = job.operation_id
        accepted = checkpointed = None
        try:
            if job.kind == "prepare":
                require(self._prepared is None and job.token_id is None,
                        "Duplicate prepare")
                preparation = StreamPreparation(job.identity, job.recording_id, job.start_policy)
                self._prepared = PreparedStreamWriter.prepare(self._path, preparation, self._limits)
                self._token = self._prepared.readiness_token
                require(self._token.preparation == preparation, "Preparation mismatch")
            else:
                require(self._token is not None and job.token_id == self._token.token_id,
                        "Readiness correlation")
                if job.kind == "bind_start":
                    self._writer = self._prepared.bind_start(self._token, job.start)
                elif job.kind == "bind_first_block":
                    self._writer = self._prepared.bind_first_block(self._token, job.block)
                elif job.kind == "append":
                    self._writer.append(job.block)
                elif job.kind == "checkpoint":
                    self._writer.checkpoint()
                elif job.kind == "finish":
                    self._writer.finish(job.terminal)
                elif job.kind == "finish_unbound":
                    self._prepared.finish_unbound(self._token, job.unbound)
                else:
                    raise StreamError("Unknown storage operation")
                if self._writer is not None:
                    accepted = self._writer.accepted_end_sample_index
                    checkpointed = self._writer.checkpointed_end_sample_index
            require(accepted == job.expected_accepted_end_sample_index,
                    "Actual writer accepted endpoint differs from job")
            if job.kind in ("checkpoint", "finish"):
                require(checkpointed == accepted, "Actual writer checkpoint endpoint differs")
            digest = self._digest.copy()
            if job.block is not None:
                digest.update(job.block.raw_u16le)
            require(digest.hexdigest() == job.prefix_sha256, "Selected prefix digest differs")
            self._digest = digest
            if job.kind in ("finish", "finish_unbound"):
                require((self._writer if self._writer is not None else self._prepared).closed,
                        "Final writer did not close")
                self._closed = True
                self._close_result = True
            # Only here, after the actual call and its endpoint checks returned.
            completion = JobCompletion.for_job(job, token_id=self._token.token_id,
                                               accepted_end=accepted, checkpointed_end=checkpointed)
            self._status = StorageStatus(job.operation_id, job.kind, True, self._closed, None,
                                         accepted, checkpointed, self._digest.hexdigest())
            return completion
        except (OSError, ValueError) as exc:
            owner = self._writer if self._writer is not None else self._prepared
            original = None if owner is None else owner.first_failure
            failure = (replace(original, operation=job.kind) if original is not None else
                       FailureEvidence("host_io", "storage " + job.kind + " failed: " + type(exc).__name__,
                                       operation=job.kind))
            # Keep endpoints from the last confirmed return, not the failed call.
            previous = self._status
            closed = self._close()
            self._status = StorageStatus(job.operation_id, job.kind, False, closed, failure,
                                         previous.accepted_end, previous.checkpointed_end,
                                         self._digest.hexdigest())
            return JobFailure.for_job(job, failure)

    def _close(self):
        """Disk owner only. Abandon RAM tail, never imply checkpoint/terminal or source quiescence."""
        if self._close_result is not None:
            return self._close_result
        self._closed = True
        try:
            if self._writer is not None:
                self._writer.close()
            elif self._prepared is not None:
                self._prepared.close()
        except OSError:
            # An earlier operation failure remains primary. Closure is attempted
            # once; no hidden retry of a potentially blocking OS operation.
            self._close_result = False
            self._status = replace(self._status, closed=False, failure=self._status.failure or
                                   FailureEvidence("host_io", "writer close failed", operation="close"))
            return False
        self._close_result = True
        self._status = replace(self._status, closed=True)
        return True


@dataclass(frozen=True, slots=True)
class BridgeSnapshot:
    endpoint_state: wire.State
    identity: StreamIdentity | None
    session: object
    storage: StorageStatus | None
    source_received_end: int
    source_prefix_crc32: int
    source_terminal: wire.ValidatedEnd | None
    endpoint_first_fault: wire.FirstFault | None
    recovery: wire.RecoverySnapshot | None
    pending_start: wire.Pending | None
    pending_stop: wire.Pending | None
    storage_job_id: int | None
    late_fault: FailureEvidence | None
    closed: bool
    mailbox: MailboxState | None
    accepted_build_id: int | None = None
    accepted_caps: tuple[int, ...] | None = None


class StreamBridge:
    """One attempt, one planned generation and at most one recording pathname.

    Normalized observations and storage completions are not public inputs. Bytes
    reach the session only through this owned endpoint. Snapshots are observations,
    not mutable control handles. The caller supplies integral monotonic ms.
    fake_diagnostic_declaration selects only synthetic rehearsal interpretation;
    this transport-free component cannot authenticate a caller's byte source.
    """
    def __init__(self, attempt_id, *, session_limits=SessionLimits(), storage_limits=StreamLimits(),
                 producer_profile=None, fake_diagnostic_declaration=None):
        text(attempt_id, 128)
        require(type(session_limits) is SessionLimits and type(storage_limits) is StreamLimits,
                "Exact limits required")
        session_limits.__post_init__()
        storage_limits.__post_init__()
        self._fake_diagnostic_declaration = (None if fake_diagnostic_declaration is None else
                                            admit_fake_diagnostic_declaration(fake_diagnostic_declaration))
        if self._fake_diagnostic_declaration is not None and producer_profile is not None:
            raise ProvenanceError("Нельзя смешивать producer profile и fake-rehearsal declaration")
        self._producer_profile = None if producer_profile is None else admit_profile(producer_profile)
        self._owner_thread = get_ident()
        self._attempt_id, self._limits, self._storage_limits = attempt_id, session_limits, storage_limits
        self._endpoint = wire.Endpoint()
        self._session = self._identity = self._plan = self._build = self._caps = None
        self._storage = self._job = self._late_fault = None
        self._closed = False
        self._now = 0
        self._start_request = self._stop_request = None
        self._active_offer = self._write_trigger = None

    def __copy__(self):
        raise StreamError("Receiver owner cannot be copied")

    def __deepcopy__(self, memo):
        raise StreamError("Receiver owner cannot be copied")

    def _owner(self):
        require(get_ident() == self._owner_thread, "Receiver owner thread required")

    def _clock(self, now_ms):
        self._owner()
        if type(now_ms) is not int or not self._now <= now_ms <= wire.MAX_TIME_MS:
            raise ValueError("monotonic_ms")
        self._now = now_ms

    @property
    def disk_executor(self):
        """Disk-role handle. It has no source/session or completion callback API."""
        return self._storage

    @property
    def next_deadline_ms(self):
        self._owner()
        return self._endpoint.next_deadline_ms

    def _live(self):
        require(not self._closed, "Bridge closed")

    def _failure(self, code, *, source_code=None):
        first = self._endpoint.first_fault
        ordinary = first.ordinary if first is not None else self._endpoint.ordinary
        stop = first.stop if first is not None else self._endpoint.stop_pending
        # Both request lanes are context, not an arbitrary causal priority.
        pending = None
        if first is not None and first.code == code and first.causal_request is not None:
            pending = first.causal_request
        elif (code.startswith("device_error_") and self._endpoint.last_recovery is not None and
              self._endpoint.last_recovery.causal_request is not None):
            pending = self._endpoint.last_recovery.causal_request
        elif code == "request_timeout":
            pending = ordinary
        elif code == "stop_timeout":
            pending = stop
        elif code == "write_count_unknown" and self._write_trigger is not None:
            pending = self._write_trigger.request
        category = ("host_io" if code == "storage" else "capacity_raw" if code == "capacity"
                    else "source_error" if code.startswith("device_") else "transport_error")
        detail = "wire " + code + "; ordinary=" + str(None if ordinary is None else ordinary.request_id)
        detail += "; stop=" + str(None if stop is None else stop.request_id)
        return FailureEvidence(category, detail, None if pending is None else pending.kind.name,
                               None if pending is None else pending.request_id, source_code)

    def _fault(self, failure, *, drain=False):
        if self._session is None:
            return
        if self._session.snapshot().finalization_started:
            if self._late_fault is None:
                self._late_fault = failure
            return
        if drain:
            self._session.drain_failed(failure)
        else:
            self._session.fault(failure)

    def _terminal_id(self, event):
        return "end:" + str(event.nonce) + ":" + str(event.sequence)

    def _events(self, events):
        for event in events:
            if (self._session is not None and self._session.snapshot().finalization_started and
                    isinstance(event, (wire.Started, wire.ValidatedData, wire.ValidatedEnd, wire.StopAcknowledged))):
                continue  # The already-issued terminal job is immutable.
            if isinstance(event, wire.Pending) and event.kind == Kind.HELLO:
                # Clear only for an actually admitted new handshake. A refused
                # HELLO cannot erase accepted evidence from the current attempt.
                self._build = self._caps = None
            elif isinstance(event, wire.WriteOffer):
                self._active_offer = event
            elif isinstance(event, wire.Negotiated):
                self._build = event.firmware_build
                self._caps = event.capabilities
            elif isinstance(event, wire.Started):
                require(self._identity is not None and event.nonce == self._plan.nonce and
                        event.generation == self._identity.generation and
                        event.config_id == self._identity.config_id and
                        event.capabilities == self._plan.capabilities and
                        event.request.request_id == self._plan.generation and
                        event.request.action_id == 1, "START reservation mismatch")
                self._session.source_started(SourceStarted(self._attempt_id, event.request.action_id,
                                                          self._identity, event.first_index))
            elif isinstance(event, wire.ValidatedData):
                snap = self._session.snapshot()
                if snap.finalization_started or snap.admission_fenced:
                    continue  # Endpoint still validates source counters and CRC.
                block = StreamBlock(self._identity, event.block.first_index, event.block.raw_samples)
                try:
                    self._session.receive(block)
                except StreamError:
                    # The accepted session advances received_end for a complete
                    # validated block before rejecting its raw-queue admission.
                    failure = self._session.snapshot().first_failure
                    if failure is not None and failure.code.startswith("capacity_"):
                        self._events(self._endpoint.adapter_fault("capacity", self._now))
                    else:
                        self._events(self._endpoint.transport_fault(self._now))
            elif isinstance(event, wire.ValidatedEnd):
                terminal = event.terminal
                failure = None
                if terminal.reason or terminal.flags:
                    failure = self._failure("device_" + str(terminal.reason), source_code=terminal.reason)
                loss = None if terminal.produced_end is None else terminal.produced_end - terminal.prefix_end
                normalized = StreamTerminal(self._identity, terminal.prefix_end,
                    "stopped" if failure is None else "source_failure", True,
                    terminal.produced_end, terminal.prefix_end, loss,
                    "validated_wire_full_prefix_crc32", failure,
                    bool(terminal.stop_request_id), terminal.stop_request_id or None)
                self._session.source_end(SourceEnd(self._terminal_id(event), normalized))
            elif isinstance(event, wire.StopAcknowledged):
                self._session.stop_acknowledged(StopAcknowledgement(self._attempt_id,
                    event.request.action_id, self._identity, self._terminal_id(event.terminal),
                    event.acknowledgement.prefix_end, event.request.request_id, event.quiescence_reported))
            elif isinstance(event, wire.Fault):
                self._fault(self._failure(event.first.code))
            elif isinstance(event, (wire.RecoveryRequired, wire.ResetRequired)):
                self._fault(self._failure(event.trigger if isinstance(event, wire.RecoveryRequired) else event.code), drain=True)
            elif isinstance(event, wire.StopRequired):
                if self._session is not None and not self._session.snapshot().stop_requested:
                    self._session.stop()

    def _controls(self):
        if self._session is None or self._closed:
            return
        # The session has exactly one START and one STOP; never an unbounded pump.
        for _ in range(2):
            action = self._session.take_control_action()
            if action is None:
                break
            if action.kind == "start":
                events = self._endpoint.start(self._now, action.action_id, plan=self._plan)
            else:
                events = self._endpoint.stop(self._now, action.action_id)
            pending = next((x for x in events if isinstance(x, wire.Pending)), None)
            if pending is not None:
                if action.kind == "start":
                    self._start_request = pending
                else:
                    self._stop_request = pending
            self._events(events)
            if pending is None:
                self._fault(self._failure("control_" + action.kind + "_not_admitted"), drain=True)

    def _dispatch(self, method, now_ms, *args):
        self._live()
        self._clock(now_ms)
        events = method(*args, now_ms)
        self._events(events)
        self._controls()
        return events

    def hello(self, nonce, now_ms):
        return self._dispatch(self._endpoint.hello, now_ms, nonce)

    def get_caps(self, now_ms):
        return self._dispatch(self._endpoint.get_caps, now_ms)

    def receive(self, data, now_ms):
        return self._dispatch(self._endpoint.receive, now_ms, data)

    def tick(self, now_ms):
        return self._dispatch(self._endpoint.tick, now_ms)

    def next_write(self, now_ms):
        return self._dispatch(self._endpoint.next_write, now_ms)

    def observe_write(self, token, accepted, now_ms):
        self._owner()
        self._write_trigger = (self._active_offer if self._active_offer is not None and
                               type(token) is int and token == self._active_offer.token else None)
        try:
            result = self._dispatch(self._endpoint.observe_write, now_ms, token, accepted)
            self._active_offer = None
            return result
        finally:
            self._write_trigger = None

    def transport_fault(self, now_ms):
        return self._dispatch(self._endpoint.transport_fault, now_ms)

    def recover(self, now_ms):
        """Request explicit isolation; does not reconnect or start another attempt."""
        return self._dispatch(self._endpoint.recover, now_ms)

    def _reserve(self):
        require(self._session is None, "Attempt already planned")
        if self._producer_profile is not None:
            require(self._build is not None and self._caps is not None, "Validated negotiation required")
            validate_negotiation(self._producer_profile, self._build, self._caps)
        if self._fake_diagnostic_declaration is not None:
            validate_fake_diagnostic_negotiation(self._fake_diagnostic_declaration,
                                                self._build, self._caps)
        events = self._endpoint.plan_start(self._now)
        self._events(events)
        plans = [x for x in events if isinstance(x, wire.StartPlan)]
        require(len(plans) == 1 and self._build is not None, "Validated negotiation required")
        self._plan = plans[0]  # Exact capability object, never a reconstructed copy.
        caps = self._plan.capabilities
        metadata = ("0.2-draft-081721", "build:" + format(self._build, "08x"),
                    "isolated-stream-bridge") if self._producer_profile is None else self._producer_profile.identity_metadata
        if self._fake_diagnostic_declaration is not None:
            metadata = rehearsal_identity_metadata(self._fake_diagnostic_declaration)
        self._identity = StreamIdentity("wire-0.2:" + format(self._plan.nonce, "016x"),
            self._plan.generation, caps.config_id, caps.fs_num // caps.fs_den,
            "synthetic", *metadata)
        self._session = StreamSession(self._identity, self._attempt_id, self._limits)

    def preview(self, now_ms):
        self.tick(now_ms)
        self._reserve()
        self._session.preview()
        self._controls()

    def record(self, path, recording_id, now_ms):
        self.tick(now_ms)
        require(self._storage is None, "Only one pathname/recording per attempt")
        if self._session is None:
            self._reserve()
        self._session.record(recording_id)
        self._storage = StorageExecutor(path, self._storage_limits, self._attempt_id, self._identity)
        self._controls()

    def stop(self, now_ms):
        self.tick(now_ms)
        require(self._session is not None, "No attempt")
        self._session.stop()
        if self._start_request is None:
            self._endpoint.cancel_start_plan(now_ms)
        self._controls()

    def cancel(self, now_ms):
        self.tick(now_ms)
        require(self._session is not None, "No attempt")
        self._session.cancel()
        if self._start_request is None:
            self._endpoint.cancel_start_plan(now_ms)
        self._controls()

    def checkpoint(self, now_ms):
        self.tick(now_ms)
        require(self._session is not None, "No attempt")
        self._session.request_checkpoint()

    def dispatch_storage(self, now_ms):
        """Reserve one exact job; in-flight raw stays owned through acceptance."""
        self.tick(now_ms)
        if self._job is None and self._session is not None:
            job = self._session.next_storage_job()
            if job is not None:
                self._storage._submit(job)
                self._job = job
        return None if self._job is None else self._job.operation_id

    def accept_storage_completion(self, now_ms):
        """Receiver owner accepts the owned result at CURRENT observation time.

        Allowed after close only to settle ownership/evidence, never to resurrect
        an abandoned session. No externally supplied completion/result is accepted.
        """
        self._clock(now_ms)
        if not self._closed:
            self._events(self._endpoint.tick(now_ms))
            self._controls()
        if self._storage is None:
            return None
        envelope = self._storage._take_result()
        if envelope is None:
            return None
        result, owned = envelope
        if not owned:
            job, self._job = self._job, None
            failure = FailureEvidence("host_io", "copied or foreign storage result capability")
            if job is not None and not self._closed:
                self._session.storage_failed(JobFailure.for_job(job, failure))
            else:
                self._fault(failure)
            self._storage._request_close()
            if not self._closed:
                self._controls()
            return self._storage.status
        if result.job is None:
            if result.status.failure is not None:
                self._fault(result.status.failure)
            return result.status
        job, self._job = self._job, None
        if job is not result.job:
            failure = FailureEvidence("host_io", "stale or foreign owned storage result")
            if job is not None and not self._closed:
                self._session.storage_failed(JobFailure.for_job(job, failure))
            else:
                self._fault(failure)
            self._storage._request_close()
            if not self._closed:
                self._controls()
            return result.status
        if self._closed:
            return result.status  # close already failed this pending session job.
        completion = result.completion
        try:
            if isinstance(completion, JobFailure):
                self._session.storage_failed(completion)
                self._events(self._endpoint.adapter_fault("storage", now_ms))
                if self._endpoint.generation is None:
                    self._endpoint.cancel_start_plan(now_ms)
            else:
                self._session.storage_completed(completion)
        except StreamError:
            # Settle the exact owned job after the session rejects its result;
            # do not leave a ghost pending operation after consuming the slot.
            failure = self._session.snapshot().first_failure
            self._session.storage_failed(JobFailure.for_job(job, failure))
            self._storage._request_close()
            self._events(self._endpoint.adapter_fault("storage", now_ms))
        self._controls()
        return result.status

    def storage_tick(self, now_ms):
        """Synchronous replay convenience ONLY; never use as a disk-thread entry.

        Real runtime must call executor.execute_one(), then receiver-owned
        accept_storage_completion(current_now), after the disk call returns.
        """
        self.tick(now_ms)
        if self._storage is None:
            return None
        self._storage.execute_one()
        return self.accept_storage_completion(now_ms)

    def close(self, now_ms):
        """Receiver-only abandonment request. Actual close is disk-owned later."""
        self._clock(now_ms)
        if self._closed:
            return
        if self._session is None or not self._session.snapshot().finalized:
            self._events(self._endpoint.transport_fault(now_ms))
        if self._job is not None:
            self._session.storage_failed(JobFailure.for_job(self._job,
                FailureEvidence("host_io", "storage job abandoned by receiver", operation=self._job.kind)))
        if self._storage is not None:
            self._storage._request_close()
        self._closed = True

    def snapshot(self):
        self._owner()
        ep = self._endpoint
        return BridgeSnapshot(ep.state, self._identity,
            None if self._session is None else self._session.snapshot(),
            None if self._storage is None else self._storage.status,
            ep.received_end, ep.received_crc32, ep.terminal, ep.first_fault, ep.last_recovery,
            ep.ordinary, ep.stop_pending, None if self._job is None else self._job.operation_id,
            self._late_fault, self._closed,
            None if self._storage is None else self._storage.snapshot(),
            self._build, None if self._caps is None else
            tuple(getattr(self._caps, name) for name in ("config_id", "profile", "channel", "adc_bits",
                                                        "fs_num", "fs_den", "max_block", "flags")))

"""Pure-state oracle for the proposed wire0.2 request budget, not host code.

All input times are synthetic monotonic integer milliseconds. A write event is
an acceptance observed at that instant, not a real asynchronous OS completion.
The parser, OS transport, device state and cleanup proof are outside this model.
"""

from dataclasses import dataclass


BUDGET_MS = 2000
MAX_TICK = (1 << 63) - 1  # A bounded example clock; not a wire-format requirement.
KINDS = ("HELLO", "GET_CAPS", "STREAM_START", "STOP")
RESPONSES = {
    "HELLO": "HELLO_ACK",
    "GET_CAPS": "STREAM_CAPS",
    "STREAM_START": "STREAM_START_ACK",
    "STOP": "STOP_ACK",
}


@dataclass(frozen=True)
class Identity:
    kind: str
    nonce: int
    request_id: int
    intended_sequence: int


@dataclass
class Pending:
    identity: Identity
    admitted_ms: int
    deadline_ms: int
    frame_bytes: int
    recovery_prefix_bytes: int = 0
    accepted_bytes: int = 0
    accepted_count_known: bool = True
    completed: bool = False
    abandoned: bool = False
    cancelled_suffix_bytes: int | None = 0

    @property
    def total_bytes(self):
        return self.frame_bytes + self.recovery_prefix_bytes

    @property
    def sequence_committed(self):
        return self.accepted_bytes > self.recovery_prefix_bytes


@dataclass(frozen=True)
class Cause:
    name: str
    identity: Identity | None
    observed_ms: int


class RequestBudgetModel:
    """Two bounded slots and one TX owner, with no production dependencies.

    Only timing/correlation admission gates are modeled. ``validated=True`` is
    supplied by the test, not computed by a COBS/CRC or payload validator. The
    ``generation_known`` input represents an already checked START generation.
    Recovery isolation is an explicit test input, never inferred from cancel.
    """

    def __init__(self, nonce=123, generation_known=True, recovery=False,
                 verified_prefix=()):
        self.nonce = nonce
        self.generation_known = generation_known
        self.recovery = recovery
        self.old_writer_isolated = not recovery
        self.now_ms = 0
        self.requests = []
        self.needs_recovery = False
        self.first_cause = None
        self.timed_out_identity = None
        self.accepted_log = []
        self.last_rejection = None
        self.verified_prefix = tuple(verified_prefix)

    def _clock(self, now_ms):
        if type(now_ms) is not int or not self.now_ms <= now_ms <= MAX_TICK:
            raise ValueError("a non-wrapping monotonic integer clock is required")
        self.now_ms = now_ms

    def pending(self):
        return [request for request in self.requests if not request.completed]

    @property
    def next_timer_ms(self):
        """Scheduler obligation exposed without depending on I/O callbacks."""
        if self.needs_recovery:
            return None
        return min((r.deadline_ms for r in self.pending()), default=None)

    def _fault(self, name, request=None):
        identity = request.identity if request is not None else None
        if self.first_cause is None:
            self.first_cause = Cause(name, identity, self.now_ms)
        if name == "request_deadline":
            self.timed_out_identity = identity
        self.needs_recovery = True
        for pending in self.pending():
            pending.abandoned = True
            pending.cancelled_suffix_bytes = (
                pending.total_bytes - pending.accepted_bytes
                if pending.accepted_count_known else None)

    def advance(self, now_ms):
        self._clock(now_ms)
        if self.needs_recovery:
            return False
        # Stable admission order resolves identical deadlines for this oracle.
        # The contract only requires expiry before any completion at that instant.
        overdue = [r for r in self.pending() if now_ms >= r.deadline_ms]
        if overdue:
            self._fault("request_deadline", min(overdue, key=lambda r: r.deadline_ms))
            return False
        return True

    def latch_first_cause(self, now_ms, name):
        """Diagnostic input, e.g. storage fault before a permitted Stop attempt.

        This does not model the actual storage-fault stopping procedure.
        """
        self.advance(now_ms)
        if self.first_cause is None:
            self.first_cause = Cause(name, None, now_ms)

    def establish_old_writer_isolation(self, now_ms, verified):
        self._clock(now_ms)
        self.old_writer_isolated = bool(verified)

    def admit(self, now_ms, kind, request_id, intended_sequence=None,
              frame_bytes=8, recovery_prefix=False):
        self.last_rejection = None
        if not self.advance(now_ms):
            self.last_rejection = "recovery_required"
            return None
        if kind not in KINDS or not 1 <= frame_bytes <= 544:
            raise ValueError("invalid abstract request")
        if recovery_prefix and kind != "HELLO":
            raise ValueError("only recovery HELLO has an extra delimiter")
        live = self.pending()
        if kind == "STOP":
            unavailable = (not self.generation_known or
                           any(r.identity.kind in ("STOP", "HELLO") for r in live))
        else:
            unavailable = bool(live)
        if unavailable:
            self.last_rejection = "slot_or_state_unavailable"
            return None
        if kind == "HELLO" and self.recovery and not self.old_writer_isolated:
            self.last_rejection = "old_writer_not_isolated"
            return None
        if now_ms > MAX_TICK - BUDGET_MS:
            self.last_rejection = "deadline_overflow"
            return None
        if any(r.identity.request_id == request_id for r in self.requests):
            self.last_rejection = "request_id_already_used"
            return None
        if intended_sequence is None:
            intended_sequence = 1 if kind == "HELLO" else 2
        identity = Identity(kind, self.nonce, request_id, intended_sequence)
        request = Pending(identity, now_ms, now_ms + BUDGET_MS, frame_bytes,
                          int(recovery_prefix))
        self.requests.append(request)
        return request

    def write(self, now_ms, request, accepted_bytes):
        # The expiry guard is deliberately before send/acceptance processing.
        if not self.advance(now_ms):
            return False
        owner = next((r for r in self.pending()
                      if r.accepted_bytes < r.total_bytes), None)
        if request is not owner:
            return False
        if accepted_bytes is None:
            request.accepted_count_known = False
            self._fault("accepted_byte_count_unknown", request)
            return False
        remaining = request.total_bytes - request.accepted_bytes
        if type(accepted_bytes) is not int or not 0 <= accepted_bytes <= remaining:
            raise ValueError("invalid accepted-byte observation")
        request.accepted_bytes += accepted_bytes
        if accepted_bytes:
            self.accepted_log.append((now_ms, request.identity, accepted_bytes))
        return True

    def response(self, now_ms, response_kind, request_id, nonce,
                 complete=True, validated=True):
        # ``now_ms`` is when complete validation/correlation has finished.
        if not self.advance(now_ms):
            return False
        request = next((r for r in self.pending()
                        if r.identity.request_id == request_id), None)
        if request is None or not complete or not validated:
            return False
        if nonce != request.identity.nonce:
            return False
        if response_kind != RESPONSES[request.identity.kind]:
            return False
        if request.accepted_bytes != request.total_bytes:
            return False
        request.completed = True
        return True

    def matching_response(self, now_ms, request, **kwargs):
        return self.response(now_ms, RESPONSES[request.identity.kind],
                             request.identity.request_id, request.identity.nonce,
                             **kwargs)


class CleanupBudgetModel:
    """Absolute cleanup gate only; no OS cancellation is performed or proven.

    ``isolated=True`` means external evidence has already established old-write
    completion/cancellation isolation AND FIFO ordering. Cancellation progress by
    itself is insufficient. The first request/recording fault lives in the old
    RequestBudgetModel and is not overwritten by this cleanup outcome.
    """

    def __init__(self, entered_ms, old_nonce=123):
        if type(entered_ms) is not int or not 0 <= entered_ms <= MAX_TICK:
            raise ValueError("invalid recovery-entry clock")
        self.entered_ms = self.now_ms = entered_ms
        self.deadline_ms = entered_ms + BUDGET_MS
        self.old_nonce = old_nonce
        self.isolated = False
        self.requires_physical_reset = self.deadline_ms > MAX_TICK
        self.progress_events = 0

    @property
    def next_timer_ms(self):
        if self.isolated or self.requires_physical_reset:
            return None
        return self.deadline_ms

    def advance(self, now_ms):
        if type(now_ms) is not int or not self.now_ms <= now_ms <= MAX_TICK:
            raise ValueError("a non-wrapping monotonic integer clock is required")
        self.now_ms = now_ms
        if not self.isolated and now_ms >= self.deadline_ms:
            self.requires_physical_reset = True
        return not self.requires_physical_reset

    def progress(self, now_ms, isolated=False):
        if not self.advance(now_ms):
            return False
        self.progress_events += 1
        if isolated:
            self.isolated = True
        return True

    def new_session(self, now_ms, fresh_nonce):
        if not self.advance(now_ms) or not self.isolated:
            return None
        if not fresh_nonce or fresh_nonce == self.old_nonce:
            return None
        model = RequestBudgetModel(nonce=fresh_nonce, generation_known=False,
                                   recovery=True)
        model.establish_old_writer_isolation(now_ms, verified=True)
        return model

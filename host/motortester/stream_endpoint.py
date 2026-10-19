"""Deterministic provisional HOST endpoint; never performs transport or disk I/O.

All times are explicit integral monotonic milliseconds. Returned observations are
immutable and bounded. A future adapter owns scheduling, writes and proof of old
write isolation. Importing this module does not activate it in the application.
"""
from dataclasses import dataclass, replace
from enum import Enum
import struct
import zlib

from .stream_wire import (COUNTER_RESERVE, MAX_BLOCK, MAX_ENCODED, MAX_FRAME,
                          U32_MAX, U64_MAX, Caps, Data, End, Error, Frame, HelloAck,
                          Kind, StartAck, StopAck, WireError, decode_frame,
                          decode_payload, encode_frame, uint)

REQUEST_MS, DATA_MS, PARTIAL_MS, CLEANUP_MS = 2000, 1000, 1000, 2000
MAX_TIME_MS = (1 << 63) - 1
MAX_INPUT_BYTES, MAX_HANDSHAKE_BYTES, MAX_HANDSHAKE_FRAMES = 544, 4096, 64
MAX_EVENTS = 64
MAX_TAIL_DATA, MAX_TAIL_CONTROL = 32, 4


class State(str, Enum):
    NO_SESSION = "no_session"
    HELLO = "hello"
    STOPPED = "stopped"
    STARTING = "starting"
    STREAMING = "streaming"
    DRAINING = "draining"
    TERMINAL = "terminal"
    NEEDS_HELLO = "needs_hello"
    RECOVERING = "recovering"
    RESET_REQUIRED = "reset_required"


@dataclass(frozen=True, slots=True)
class Pending:
    kind: Kind
    nonce: int
    request_id: int
    sequence: int
    action_id: int
    admitted_ms: int
    deadline_ms: int
    accepted_bytes: int | None = 0  # Includes recovery delimiter, if present.
    frame_accepted_bytes: int | None = 0
    complete: bool = False


@dataclass(frozen=True, slots=True)
class WriteOffer:
    token: int
    request: Pending
    data: bytes


@dataclass(frozen=True, slots=True)
class Refused:
    code: str


@dataclass(frozen=True, slots=True)
class Negotiated:
    nonce: int
    firmware_build: int
    capabilities: Caps | None  # None until a separate GET_CAPS succeeds.


@dataclass(frozen=True, slots=True)
class StartPlan:
    nonce: int
    generation: int
    sequence: int
    capabilities: Caps


@dataclass(frozen=True, slots=True)
class Started:
    nonce: int
    generation: int
    config_id: int
    first_index: int
    request: Pending
    capabilities: Caps


@dataclass(frozen=True, slots=True)
class ValidatedData:
    nonce: int
    sequence: int
    block: Data
    full_prefix_crc32: int


@dataclass(frozen=True, slots=True)
class ValidatedEnd:
    nonce: int
    sequence: int
    terminal: End


@dataclass(frozen=True, slots=True)
class StopAcknowledged:
    nonce: int
    request: Pending
    terminal: ValidatedEnd
    acknowledgement: StopAck
    # This is a validated device report, not independent physical-stop evidence.
    quiescence_reported: bool = True


@dataclass(frozen=True, slots=True)
class FirstFault:
    code: str
    at_ms: int
    ordinary: Pending | None
    stop: Pending | None
    causal_request: Pending | None = None


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    nonce: int | None
    generation: int | None
    received_end: int
    received_crc32: int
    terminal: ValidatedEnd | None
    first_fault: FirstFault | None
    ordinary: Pending | None
    stop: Pending | None
    outstanding_offer: WriteOffer | None
    causal_request: Pending | None = None


@dataclass(frozen=True, slots=True)
class Fault:
    first: FirstFault
    trigger: str


@dataclass(frozen=True, slots=True)
class RecoveryRequired:
    deadline_ms: int
    snapshot: RecoverySnapshot
    trigger: str


@dataclass(frozen=True, slots=True)
class ResetRequired:
    code: str
    snapshot: RecoverySnapshot | None


@dataclass(frozen=True, slots=True)
class StopRequired:
    code: str


@dataclass(slots=True)
class _Request:
    identity: Pending
    wire: bytes
    prelude: int = 0


class Endpoint:
    """Single-threaded pure state machine with a two-slot, single-owner TX path.

    Call tick(next_deadline_ms) independently of traffic. For every WriteOffer,
    issue at most one nonblocking/bounded adapter write, then report its exact
    accepted prefix using observe_write. No offer means no write is authorized.
    Caller errors in time/action/nonce inputs raise ValueError without mutation;
    malformed transport observations fail the session closed with fixed codes.
    """
    def __init__(self):
        self.state = State.NO_SESSION
        self.nonce = None
        self.generation = None
        self.received_end = 0
        self.received_crc32 = 0
        self.terminal = None
        self.first_fault = None
        self.last_recovery = None
        self._now = 0
        self._ordinary = None
        self._stop = None
        self._offer = None
        self._offer_token = 0
        self._next_tx = 1
        self._next_rx = 1
        self._next_request = 2
        self._last_frame = None
        self._last_data = 0
        self._short_seen = False
        self._stop_used = False
        self._caps = None
        self._plan = None
        self._build = None
        self._data_deadline = None
        self._partial_deadline = None
        self._cleanup_deadline = None
        self._rx = bytearray()
        self._discarding = False
        self._handshake_bytes = 0
        self._handshake_frames = 0
        self._tail_data = 0
        self._tail_control = 0
        self._counter_notice = False

    @property
    def ordinary(self) -> Pending | None:
        return self._ordinary.identity if self._ordinary else None

    @property
    def stop_pending(self) -> Pending | None:
        return self._stop.identity if self._stop else None

    @property
    def buffered_bytes(self) -> int:
        return len(self._rx)

    @property
    def next_deadline_ms(self) -> int | None:
        deadlines = [x for x in (self.ordinary.deadline_ms if self.ordinary else None,
                                  self.stop_pending.deadline_ms if self.stop_pending else None,
                                  self._data_deadline, self._partial_deadline,
                                  self._cleanup_deadline) if x is not None]
        return min(deadlines) if deadlines else None

    def _check_time(self, now_ms):
        # Observation may reach the last representable deadline. Creation of
        # each new budget is checked separately; bool/floats are not clocks.
        if not uint(now_ms, MAX_TIME_MS) or now_ms < self._now:
            raise ValueError("monotonic_ms")

    @staticmethod
    def _check_action(action_id):
        if not uint(action_id, U64_MAX):
            raise ValueError("action_id")

    def _check_nonce(self, nonce):
        if not uint(nonce, U64_MAX) or not nonce or nonce == self.nonce:
            raise ValueError("fresh_nonce")

    def _latch(self, code, now_ms, causal_request=None):
        if self.first_fault is None:
            self.first_fault = FirstFault(code, now_ms, self.ordinary, self.stop_pending, causal_request)
            return [Fault(self.first_fault, code)]
        return []

    def _snapshot(self, causal_request=None):
        return RecoverySnapshot(self.nonce, self.generation, self.received_end,
                                self.received_crc32, self.terminal, self.first_fault,
                                self.ordinary, self.stop_pending, self._offer, causal_request)

    def _recover(self, code, now_ms, causal_request=None):
        events = self._latch(code, now_ms, causal_request) if code else []
        self.last_recovery = self._snapshot(causal_request)
        self.state = State.RECOVERING
        self._cleanup_deadline = None if now_ms > MAX_TIME_MS - CLEANUP_MS else now_ms + CLEANUP_MS
        self._ordinary = self._stop = self._offer = None
        self._plan = None
        self._data_deadline = self._partial_deadline = None
        self._rx.clear()
        self._discarding = False
        if self._cleanup_deadline is None:
            events.extend(self._reset_required("cleanup_clock_limit"))
        else:
            events.append(RecoveryRequired(self._cleanup_deadline, self.last_recovery, code or "new_session"))
        return events

    def _reset_required(self, code):
        self.state = State.RESET_REQUIRED
        self._cleanup_deadline = None
        return [ResetRequired(code, self.last_recovery)]

    def _advance(self, now_ms):
        self._check_time(now_ms)
        self._now = now_ms
        timers = []
        if self._cleanup_deadline is not None:
            timers.append((self._cleanup_deadline, 0, "isolation_timeout"))
        if self.ordinary:
            timers.append((self.ordinary.deadline_ms, 1, "request_timeout"))
        if self.stop_pending:
            timers.append((self.stop_pending.deadline_ms, 2, "stop_timeout"))
        if self._data_deadline is not None:
            timers.append((self._data_deadline, 3, "data_timeout"))
        if self._partial_deadline is not None:
            timers.append((self._partial_deadline, 4, "partial_timeout"))
        if timers:
            deadline, _, code = min(timers)
            if now_ms >= deadline:
                if code == "isolation_timeout":
                    return self._reset_required(code)
                return self._recover(code, now_ms)
        return []

    def tick(self, now_ms: int) -> tuple:
        return tuple(self._advance(now_ms))

    def _admit(self, kind, payload, action_id, now_ms, *, recovery=False):
        if now_ms > MAX_TIME_MS - REQUEST_MS:
            return [Refused("deadline_overflow")]
        hello = kind == Kind.HELLO
        if hello:
            req, seq, header_nonce = 1, 1, 0
        else:
            req = self._next_request
            seq = self._next_tx
            # A not-yet-started ordinary frame already owns its intended value.
            if self.ordinary and self.ordinary.frame_accepted_bytes == 0:
                seq += 1
            if req > U32_MAX or seq > U32_MAX:
                return [Refused("counter_exhausted")]
            if kind != Kind.STOP and (req >= COUNTER_RESERVE or seq >= COUNTER_RESERVE):
                return [Refused("counter_reserve")]
            header_nonce = self.nonce
        wire = encode_frame(kind, header_nonce, seq, req, payload)
        identity = Pending(kind, self.nonce, req, seq, action_id, now_ms, now_ms + REQUEST_MS)
        request = _Request(identity, (b"\0" if recovery else b"") + wire, int(recovery))
        if kind == Kind.STOP:
            self._stop = request
            self._stop_used = True
            if self.terminal is None:
                self.state = State.DRAINING
        else:
            self._ordinary = request
        if not hello:
            self._next_request += 1
        if kind == Kind.START:
            self.generation = req
            self.state = State.STARTING
        return [identity]

    def _hello(self, nonce, now_ms, recovery):
        if now_ms > MAX_TIME_MS - REQUEST_MS:
            return [Refused("deadline_overflow")]
        self.nonce = nonce
        self.generation = None
        self.received_end = self.received_crc32 = 0
        self.terminal = self.first_fault = None
        self._last_frame = None
        self._last_data = 0
        self._short_seen = self._stop_used = False
        self._caps = self._build = None
        self._plan = None
        self._next_tx = self._next_rx = 1
        self._next_request = 2
        self._cleanup_deadline = None
        self._handshake_bytes = self._handshake_frames = 0
        self._tail_data = self._tail_control = 0
        self._counter_notice = False
        self._rx.clear()
        self._partial_deadline = None
        self._discarding = False
        self.state = State.HELLO
        return self._admit(Kind.HELLO, struct.pack("<Q", nonce), 0, now_ms, recovery=recovery)

    def hello(self, nonce: int, now_ms: int) -> tuple:
        self._check_time(now_ms)
        self._check_nonce(nonce)
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state != State.NO_SESSION:
            return (Refused("needs_isolation"),)
        return tuple(self._hello(nonce, now_ms, False))

    def recover(self, now_ms: int) -> tuple:
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state in (State.RECOVERING, State.RESET_REQUIRED):
            return (Refused("recovery_in_progress"),)
        code = "session_replaced" if self.generation is not None and self.terminal is None else None
        return tuple(self._recover(code, now_ms))

    def isolation_complete(self, nonce: int, now_ms: int) -> tuple:
        """Caller attests old writes cannot append after the new delimiter (FIFO)."""
        self._check_time(now_ms)
        self._check_nonce(nonce)
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state != State.RECOVERING:
            return (Refused("isolation_not_pending"),)
        return tuple(self._hello(nonce, now_ms, True))

    def isolation_failed(self, now_ms: int) -> tuple:
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state != State.RECOVERING:
            return (Refused("isolation_not_pending"),)
        return tuple(self._reset_required("isolation_unproven"))

    def reset_confirmed(self, nonce: int, now_ms: int) -> tuple:
        """Caller attests physical reset/reconnect AND validated startup/isolation.

        A CDC reopen or cancellation call alone cannot justify this assertion.
        """
        self._check_time(now_ms)
        self._check_nonce(nonce)
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state != State.RESET_REQUIRED:
            return (Refused("reset_not_required"),)
        return tuple(self._hello(nonce, now_ms, True))

    def get_caps(self, now_ms: int, action_id: int = 0) -> tuple:
        self._check_time(now_ms)
        self._check_action(action_id)
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state != State.STOPPED or self._ordinary or self._stop or self._plan:
            return (Refused("ordinary_slot_or_state"),)
        return tuple(self._admit(Kind.GET_CAPS, b"", action_id, now_ms))

    def plan_start(self, now_ms: int) -> tuple:
        """Reserve one exact generation for storage preparation; no TX or timer.

        While held, ordinary admission is blocked. The exact returned object must
        be supplied to start(plan=...). Recovery invalidates it. Preparation may
        take any duration; admission and its deadline begin only at start().
        """
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state != State.STOPPED or self._ordinary or self._stop or self._plan:
            return (Refused("ordinary_slot_or_state"),)
        if self._caps is None:
            return (Refused("capabilities_required"),)
        if max(self._next_tx, self._next_rx, self._next_request) >= COUNTER_RESERVE:
            return (Refused("counter_reserve"),)
        self._plan = StartPlan(self.nonce, self._next_request, self._next_tx, self._caps)
        return (self._plan,)

    def cancel_start_plan(self, now_ms: int) -> tuple:
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        self._plan = None
        return ()

    def start(self, now_ms: int, action_id: int = 0, *, plan: StartPlan | None = None) -> tuple:
        """Explicit Preview/Record action only. No negotiation path calls start."""
        self._check_time(now_ms)
        self._check_action(action_id)
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state != State.STOPPED or self._ordinary or self._stop:
            return (Refused("ordinary_slot_or_state"),)
        if self._caps is None:
            return (Refused("capabilities_required"),)
        if (plan is not None or self._plan is not None) and plan is not self._plan:
            return (Refused("start_plan"),)
        if self._next_rx >= COUNTER_RESERVE:
            return (Refused("counter_reserve"),)
        events = self._admit(Kind.START, struct.pack("<HH", 1, 0), action_id, now_ms)
        if isinstance(events[0], Pending):
            self._plan = None
        return tuple(events)

    def stop(self, now_ms: int, action_id: int = 0) -> tuple:
        self._check_time(now_ms)
        self._check_action(action_id)
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        return tuple(self._stop_admission(now_ms, action_id))

    def _stop_admission(self, now_ms, action_id):
        if self._stop_used or self.generation is None or self.state not in (State.STARTING, State.STREAMING, State.DRAINING, State.TERMINAL):
            return [Refused("stop_slot_or_state")]
        return self._admit(Kind.STOP, struct.pack("<I", self.generation), action_id, now_ms)

    def next_write(self, now_ms: int) -> tuple:
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self._offer is not None:
            return ()
        request = next((r for r in (self._ordinary, self._stop) if r and not r.identity.complete), None)
        if request is None:
            return ()
        if self._offer_token == U64_MAX:
            return tuple(self._recover("offer_counter", now_ms))
        self._offer_token += 1
        self._offer = WriteOffer(self._offer_token, request.identity, request.wire[request.identity.accepted_bytes:])
        return (self._offer,)

    def observe_write(self, token: int, accepted: int | None, now_ms: int) -> tuple:
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state in (State.RECOVERING, State.RESET_REQUIRED):
            return (Refused("stale_write"),)
        if not uint(token, U64_MAX) or self._offer is None or token != self._offer.token:
            return tuple(self._recover("write_correlation", now_ms))
        request = self._stop if self._offer.request.kind == Kind.STOP else self._ordinary
        if not uint(accepted, len(self._offer.data)):
            request.identity = replace(request.identity, accepted_bytes=None, frame_accepted_bytes=None)
            return tuple(self._recover("write_count_unknown", now_ms))
        old = request.identity
        total = old.accepted_bytes + accepted
        frame_count = max(0, total - request.prelude)
        if old.frame_accepted_bytes == 0 and frame_count:
            if old.sequence != self._next_tx:
                return tuple(self._recover("write_sequence", now_ms))
            self._next_tx += 1  # May be U32_MAX+1 sentinel; never serialized.
        request.identity = replace(old, accepted_bytes=total, frame_accepted_bytes=frame_count,
                                   complete=total == len(request.wire))
        self._offer = None
        return ()

    def transport_fault(self, now_ms: int) -> tuple:
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state in (State.RECOVERING, State.RESET_REQUIRED):
            return (Refused("recovery_in_progress"),)
        return tuple(self._recover("transport", now_ms))

    def adapter_fault(self, code: str, now_ms: int) -> tuple:
        """Report local storage/capacity failure without claiming disk evidence.

        Keep validating the received prefix; the session/storage owner separately
        fences selection/durability. Emit a StopRequired hint, never hidden retry.
        """
        self._check_time(now_ms)
        if code not in ("storage", "capacity"):
            raise ValueError("adapter_fault")
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.generation is None or self.state not in (State.STARTING, State.STREAMING, State.DRAINING, State.TERMINAL):
            return (Refused("stream_required"),)
        events = self._latch(code, now_ms)
        if not self._stop_used:
            events.append(StopRequired(code))
        return tuple(events)

    def receive(self, data: bytes, now_ms: int) -> tuple:
        events = self._advance(now_ms)
        if events:
            return tuple(events)
        if self.state in (State.NO_SESSION, State.RECOVERING, State.RESET_REQUIRED):
            return ()
        if type(data) is not bytes or len(data) > MAX_INPUT_BYTES:
            return tuple(self._recover("input_limit", now_ms))
        for byte in data:
            if self.state == State.HELLO:
                self._handshake_bytes += 1
                if self._handshake_bytes > MAX_HANDSHAKE_BYTES:
                    events.extend(self._recover("handshake_limit", now_ms))
                    break
            if byte:
                if self._partial_deadline is None:
                    if now_ms > MAX_TIME_MS - PARTIAL_MS:
                        events.extend(self._recover("clock_limit", now_ms))
                        break
                    self._partial_deadline = now_ms + PARTIAL_MS
                if not self._discarding:
                    if len(self._rx) == MAX_ENCODED:
                        if self.state in (State.STARTING, State.STREAMING, State.DRAINING, State.TERMINAL, State.NEEDS_HELLO):
                            events.extend(self._recover("framing", now_ms))
                            break
                        self._rx.clear()
                        self._discarding = True
                    else:
                        self._rx.append(byte)
                continue
            if self.state == State.HELLO:
                self._handshake_frames += 1
                if self._handshake_frames > MAX_HANDSHAKE_FRAMES:
                    events.extend(self._recover("handshake_limit", now_ms))
                    break
            frame_bytes = bytes(self._rx) + b"\0" if self._rx and not self._discarding else None
            self._rx.clear()
            self._discarding = False
            self._partial_deadline = None
            if frame_bytes is not None:
                events.extend(self._frame(frame_bytes, now_ms))
                if self.state in (State.RECOVERING, State.RESET_REQUIRED):
                    break
        # Bounded input admits at most 18 minimum-size frames; each emits <=3.
        if len(events) > MAX_EVENTS:
            raise RuntimeError("internal_event_bound")
        return tuple(events)

    def _frame(self, raw, now_ms):
        try:
            frame = decode_frame(raw, "device")
        except WireError as exc:
            if exc.code in ("version", "direction", "type") or self.state in (State.STARTING, State.STREAMING, State.DRAINING, State.TERMINAL, State.NEEDS_HELLO):
                return self._recover(exc.code, now_ms)
            return []
        if frame.nonce != self.nonce:
            return []
        if self.state == State.HELLO:
            if (frame.kind, frame.sequence, frame.request_id) != (Kind.HELLO_ACK, 1, 1):
                return self._recover("hello_correlation", now_ms)
        elif self._last_frame == raw:
            return []
        elif frame.sequence != self._next_rx:
            return self._recover("sequence", now_ms)
        try:
            # Correlate before reading payload/state. Every reply requires full TX.
            request = None
            if frame.kind not in (Kind.DATA, Kind.END):
                request = self._stop if frame.kind == Kind.STOP_ACK else self._ordinary
                if frame.kind == Kind.ERROR and self._stop and frame.request_id == self._stop.identity.request_id:
                    request = self._stop
                if request is None or frame.request_id != request.identity.request_id or not request.identity.complete:
                    raise WireError("request")
            elif frame.request_id != 0:
                raise WireError("request")
            payload = decode_payload(frame)
            if frame.sequence >= COUNTER_RESERVE:
                if frame.kind == Kind.DATA:
                    if self._tail_data >= MAX_TAIL_DATA:
                        raise WireError("tail_limit")
                elif frame.kind in (Kind.END, Kind.STOP_ACK, Kind.ERROR, Kind.START_ACK):
                    if self._tail_control >= MAX_TAIL_CONTROL:
                        raise WireError("tail_limit")
                else:
                    raise WireError("counter_reserve")
            events = self._message(frame, payload, request, now_ms)
            if frame.sequence >= COUNTER_RESERVE:
                if frame.kind == Kind.DATA:
                    self._tail_data += 1
                else:
                    self._tail_control += 1
            if self.state not in (State.RECOVERING, State.RESET_REQUIRED):
                self._next_rx = frame.sequence + 1
                self._last_frame = raw
            return events
        except WireError as exc:
            return self._recover(exc.code, now_ms)

    def _message(self, frame, payload, request, now_ms):
        k = frame.kind
        if k == Kind.HELLO_ACK:
            if self.state != State.HELLO or request.identity.kind != Kind.HELLO or not isinstance(payload, HelloAck):
                raise WireError("state")
            self._build = payload.firmware_build
            self._ordinary = None
            self.state = State.STOPPED
            return [Negotiated(self.nonce, self._build, None)]
        if k == Kind.CAPS:
            if self.state != State.STOPPED or request.identity.kind != Kind.GET_CAPS or not isinstance(payload, Caps):
                raise WireError("state")
            self._caps = payload
            self._ordinary = None
            return [Negotiated(self.nonce, self._build, self._caps)]
        if k == Kind.START_ACK:
            if self.state not in (State.STARTING, State.DRAINING) or request.identity.kind != Kind.START or payload.generation != self.generation:
                raise WireError("generation")
            if now_ms > MAX_TIME_MS - DATA_MS:
                raise WireError("clock_limit")
            event = Started(self.nonce, payload.generation, payload.config_id, payload.first_index, request.identity, self._caps)
            self._ordinary = None
            self._data_deadline = now_ms + DATA_MS
            self.state = State.DRAINING if self._stop else State.STREAMING
            return [event]
        if k == Kind.DATA:
            if self.state not in (State.STREAMING, State.DRAINING) or self._ordinary is not None:
                raise WireError("state")
            if payload.generation != self.generation:
                raise WireError("generation")
            if self._short_seen:
                raise WireError("data_after_short")
            if payload.first_index != self.received_end:
                raise WireError("sample_gap")
            if now_ms > MAX_TIME_MS - DATA_MS:
                raise WireError("clock_limit")
            crc = zlib.crc32(payload.raw_samples, self.received_crc32)
            self.received_end, self.received_crc32 = payload.end_index, crc
            self._last_data = frame.sequence
            self._short_seen = len(payload.samples) < MAX_BLOCK
            self._data_deadline = now_ms + DATA_MS
            events = [ValidatedData(self.nonce, frame.sequence, payload, crc)]
            if frame.sequence >= COUNTER_RESERVE - 1 and not self._counter_notice:
                self._counter_notice = True
                if not self._stop_used:
                    events.append(StopRequired("counter_reserve"))
            return events
        if k == Kind.END:
            if self.state not in (State.STREAMING, State.DRAINING) or self._ordinary is not None or self.terminal is not None:
                raise WireError("state")
            if payload.generation != self.generation:
                raise WireError("generation")
            if (payload.prefix_end, payload.prefix_crc32, payload.last_data_sequence) != (self.received_end, self.received_crc32, self._last_data):
                raise WireError("terminal_prefix")
            if payload.stop_request_id and (self._stop is None or not self._stop.identity.complete or payload.stop_request_id != self._stop.identity.request_id):
                raise WireError("terminal_request")
            self.terminal = ValidatedEnd(self.nonce, frame.sequence, payload)
            self._data_deadline = None
            self.state = State.TERMINAL
            events = [self.terminal]
            if payload.reason or payload.flags:
                events.extend(self._latch("device_" + str(payload.reason), now_ms))
            if payload.flags & 4:
                events.extend(self._recover("quiescence_unconfirmed", now_ms))
            return events
        if k == Kind.STOP_ACK:
            if self.state != State.TERMINAL or self.terminal is None or request.identity.kind != Kind.STOP:
                raise WireError("state")
            end = self.terminal
            if (payload.generation, payload.prefix_end, payload.terminal_sequence, payload.flags) != (self.generation, end.terminal.prefix_end, end.sequence, end.terminal.flags):
                raise WireError("ack_terminal")
            event = StopAcknowledged(self.nonce, request.identity, end, payload)
            self._stop = None
            self.state = State.NEEDS_HELLO
            return [event]
        if k == Kind.ERROR:
            if not isinstance(payload, Error) or payload.rejected_type != request.identity.kind:
                raise WireError("error_correlation")
            expected_generation = self.generation if request.identity.kind in (Kind.START, Kind.STOP) else 0
            if payload.generation != expected_generation:
                raise WireError("generation")
            return self._recover("device_error_" + str(payload.code), now_ms,
                                 causal_request=request.identity)
        raise WireError("state")

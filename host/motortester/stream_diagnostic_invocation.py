"""Bounded STR1 policy rehearsal. Physical execution is unavailable in this release.

Selections and receipts are evidence associations, never image authentication,
owner authority, physical Stop proof, or permission to retrieve RAM.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
import threading
import time

from .stream_diagnostic import (DiagnosticLimits, DiagnosticRun, DiagnosticReport,
                                ExportStatus, FORMAT, validate_owned_export)
from .stream_provenance import (STR1_DIAGNOSTIC_DECLARATION, STR1_DIAGNOSTIC_CAPS,
                                ProducerProfile, admit_fake_diagnostic_declaration,
                                rehearsal_identity_metadata)
from .stream_serial import SerialSettings

VERSION = "str1-host-invocation-1"
STAGE = "NOT_FLASH_APPROVED"
EXPORT_OBSERVATION_MS = 1000
MAX_SELECTION_BYTES = 2048
INVOCATION_LIMITS = DiagnosticLimits(connect_ms=2000, run_ms=2000, drain_ms=4500, close_ms=1000)
# This literal is never the caller's prospective COM target. The mandatory
# injected factory remains a trusted fake contract, not a Python sandbox.
FAKE_SETTINGS = SerialSettings("FAKE-STR1", 230400, 0, .025)


@dataclass(frozen=True, slots=True)
class Bindings:
    version: str = VERSION
    source_commit: str = "4a7ddfc074ce4685f7a15536680a6777a06697ff"
    source_tree: str = "3ce428067727165e4f5a98418d265443d548bd45"
    policy_comment: str = "https://github.com/font-weight/stm32_motortester/issues/191#issuecomment-6274335866"
    policy_sha256: str = "1859e24afe0efd6f8e98c0d3b4ca598b3124d831c3b5a84b56184d2178f8da80"
    review_sha256: str = "d3fb75fb6135f9dc7d0cd8e01bcf8a7e30dc01a8f2f084ad957600f593fa824e"
    declaration: ProducerProfile = STR1_DIAGNOSTIC_DECLARATION


@dataclass(frozen=True, slots=True)
class Association:
    """Caller declarations only; no enumeration or observed hardware facts."""
    port: str
    usb_serial: str
    board_instance: str
    windows_pc: str
    vid: int = 0x1209
    pid: int = 0x0001
    bcd_device: int = 0x0100
    require_dtr: int = 1


@dataclass(frozen=True, slots=True)
class Selection:
    invocation_id: str
    association: Association
    recording_path: str
    report_path: str
    bindings: Bindings = Bindings()
    stage: str = STAGE
    export_observation_ms: int = EXPORT_OBSERVATION_MS


def _text(value, maximum=128):
    if (type(value) is not str or not 1 <= len(value) <= maximum or value != value.strip() or
            any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError("bounded literal text required")


def _exact_tree(actual, expected, path="evidence"):
    """All named keys are required; constants are exact, including their types."""
    if type(expected) is dict:
        if type(actual) is not dict or set(actual) != set(expected):
            raise ValueError(path + ":keys")
        for key, value in expected.items():
            _exact_tree(actual[key], value, path + "." + key)
    elif type(expected) in (list, tuple):
        if type(actual) is not type(expected) or len(actual) != len(expected):
            raise ValueError(path + ":sequence")
        for index, value in enumerate(expected):
            _exact_tree(actual[index], value, path + "." + str(index))
    elif expected in (int, str, bool):
        if type(actual) is not expected:
            raise ValueError(path + ":type")
        if expected is int and not 0 <= actual <= (1 << 64) - 1:
            raise ValueError(path + ":range")
        if expected is str:
            _text(actual, 4096)
    elif type(actual) is not type(expected) or actual != expected:
        raise ValueError(path + ":value")


def validate_selection(selection):
    """Pure bounded selection validation. A valid return never grants admission."""
    if type(selection) is not Selection or type(selection.bindings) is not Bindings:
        raise ValueError("exact selection and bindings required")
    _exact_tree(asdict(selection.bindings), asdict(Bindings()), "bindings")
    admit_fake_diagnostic_declaration(selection.bindings.declaration)
    if type(selection.association) is not Association:
        raise ValueError("explicit board/PC/COM association required")
    a = selection.association
    if type(a.port) is not str or not re.fullmatch(r"COM[1-9][0-9]{0,4}", a.port):
        raise ValueError("explicit native COM literal required")
    for name, expected in (("vid", 0x1209), ("pid", 1), ("bcd_device", 0x100), ("require_dtr", 1),
                           ("usb_serial", "AH20261017001")):
        _exact_tree(getattr(a, name), expected, "association." + name)
    _text(a.board_instance); _text(a.windows_pc)
    if type(selection.invocation_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", selection.invocation_id):
        raise ValueError("explicit invocation ID required")
    _exact_tree(selection.stage, STAGE, "stage")
    _exact_tree(selection.export_observation_ms, EXPORT_OBSERVATION_MS, "export_observation_ms")
    paths = []
    for value in (selection.recording_path, selection.report_path):
        _text(value, 4096)
        path = PureWindowsPath(value) if PureWindowsPath(value).drive else PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("explicit absolute output path without parent traversal required")
        paths.append(str(path).casefold())
    if paths[0] == paths[1]:
        raise ValueError("distinct sample and report outputs required")
    if len(json.dumps(asdict(selection), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")) > MAX_SELECTION_BYTES:
        raise ValueError("selection metadata capacity")
    return selection


@dataclass(frozen=True, slots=True)
class Admission:
    admitted: bool = False
    stage: str = STAGE
    reason: str = "reviewed physical deployment route unavailable"


def physical_admission(selection):
    validate_selection(selection)
    return Admission()


def collect_physical(selection):
    """Explicit current refusal, before a factory, worker, transport or file exists.

    No approval argument, file, Boolean, factory or attestation can unlock this
    route. A later operational route is a separate reviewed change.
    """
    physical_admission(selection)
    raise RuntimeError(STAGE + ": physical collection unavailable")


_IDENTITY = dict(session_id=str, generation=int, config_id=1, nominal_fs_hz=50000,
                 origin="synthetic", protocol_revision=str, firmware_version=str, application_version=str)
_FAILURE = dict(code="source_error", detail=str, operation=None, request_id=None, source_code=0)
_PENDING_STOP = dict(accepted_bytes=int, action_id=int, admitted_ms=int, complete=True,
                     deadline_ms=int, frame_accepted_bytes=int, kind=5, nonce=int,
                     request_id=int, sequence=int)
_TERMINAL = dict(config_id=1, flags=3, generation=int, last_data_sequence=int,
                 prefix_crc32=int, prefix_end=int, produced_end=None, reason=0, stop_request_id=int)
_NORMALIZED = dict(identity=_IDENTITY, end_sample_index=int, reason="source_failure",
                   terminal_observed=True, device_generated_end_sample_index=None,
                   device_transmitted_end_sample_index=int, loss_samples=None,
                   boundary_evidence="validated_wire_full_prefix_crc32", failure=_FAILURE,
                   stop_requested=True, stop_request_id=int)
_SESSION = dict(identity=_IDENTITY, source_started=True, source_first_sample_index=int,
    received_end_sample_index=int, selected_start_sample_index=int, queued_end_sample_index=int,
    accepted_end_sample_index=int, checkpointed_end_sample_index=int, finalized_end_sample_index=int,
    raw_bytes_held=0, raw_items_held=0, pending_operation_id=None,
    source_end=dict(terminal_id=str, terminal=_NORMALIZED),
    stop_acknowledgement=dict(attempt_id=str, stop_action_id=int, identity=_IDENTITY,
        terminal_id=str, end_sample_index=int, request_id=int, quiescence_reported=True),
    first_failure=_FAILURE, stop_requested=True, admission_fenced=True, writer_fenced=False,
    finalized=True, finalization_started=True, finalization_unresolved=False,
    clean_completion_reported=False, unbound_reason=None)
_FINAL = dict(observed_ns=int, phase="closed", error=None, validated_blocks=int, validated_samples=int,
    worker_unresolved=False, serial_unresolved=False, serial_call=None, serial_operation_id=int,
    serial_operation_generation=int,
    serial=dict(port=FAKE_SETTINGS.port, generation=1, state="closed", handle_settled=True,
                first_error=None, close_error=None),
    bridge=dict(identity=_IDENTITY, source_received_end=int, source_prefix_crc32=int,
        source_terminal=dict(nonce=int, sequence=int, terminal=_TERMINAL),
        endpoint_first_fault=dict(code="device_0", at_ms=int, ordinary=None,
                                  stop=_PENDING_STOP, causal_request=None),
        recovery=None, pending_start=None, pending_stop=None, late_fault=None, closed=True,
        storage=dict(operation_id=int, operation="finish", returned=True, closed=True, failure=None,
                     accepted_end=int, checkpointed_end=int, selected_sha256=str),
        mailbox=dict(operation_id=None, running=False, result_pending=False,
                     close_requested=True, close_acknowledged=True),
        accepted_build_id=0x53545231, accepted_caps=list(STR1_DIAGNOSTIC_CAPS),
        endpoint_state="needs_hello", session=_SESSION),
    run_id=str, trace_error=None, dropped_callbacks=0, first_gap_ns=None, run_error=None,
    connect_intent_ns=int, record_intent_return_ns=int, stop_intent_ns=int, close_intent_ns=int,
    controller_joined=True, trace_complete=True,
    live_durability="none; RAM capture before explicit export", full_endpoint_event_trace=False)


def _validate_final(final):
    _exact_tree(final, _FINAL)
    b, s = final["bridge"], final["bridge"]["session"]
    end, ack = b["source_terminal"], s["stop_acknowledgement"]
    t, n, fault = end["terminal"], s["source_end"]["terminal"], b["endpoint_first_fault"]
    identity, stop = b["identity"], fault["stop"]
    expected_meta = rehearsal_identity_metadata(STR1_DIAGNOSTIC_DECLARATION)
    _exact_tree(tuple(identity[key] for key in ("protocol_revision", "firmware_version", "application_version")),
                expected_meta, "identity declaration")
    if not (identity == s["identity"] == n["identity"] == ack["identity"]):
        raise ValueError("identity correlation")
    if (end["nonce"] == 0 or identity["session_id"] != "wire-0.2:" + format(end["nonce"], "016x") or
            identity["generation"] != t["generation"] or not 1 <= t["generation"] <= 0xffffffff or
            stop["nonce"] != end["nonce"]):
        raise ValueError("nonce/generation correlation")
    terminal_id = "end:" + str(end["nonce"]) + ":" + str(end["sequence"])
    if s["source_end"]["terminal_id"] != terminal_id or ack["terminal_id"] != terminal_id:
        raise ValueError("END/ACK terminal correlation")
    if (not 1 <= t["stop_request_id"] <= 0xffffffff or not 1 <= end["sequence"] <= 0xffffffff or
            not 0 <= t["last_data_sequence"] < end["sequence"] or
            not (t["stop_request_id"] == n["stop_request_id"] == ack["request_id"] == stop["request_id"]) or
            not 1 <= stop["action_id"] == ack["stop_action_id"] or
            not 0 < stop["accepted_bytes"] == stop["frame_accepted_bytes"] <= 544 or
            stop["deadline_ms"] <= stop["admitted_ms"] or
            not stop["admitted_ms"] <= fault["at_ms"] < stop["deadline_ms"] or
            not re.fullmatch(r"[0-9a-f]{32}", ack["attempt_id"])):
        raise ValueError("Stop request correlation")
    prefix = t["prefix_end"]
    if (not 0 <= t["prefix_crc32"] <= 0xffffffff or b["source_prefix_crc32"] != t["prefix_crc32"] or
            any(value != prefix for value in (b["source_received_end"], n["end_sample_index"],
                n["device_transmitted_end_sample_index"], ack["end_sample_index"], b["storage"]["accepted_end"],
                b["storage"]["checkpointed_end"], *(s[key] for key in ("received_end_sample_index",
                "queued_end_sample_index", "accepted_end_sample_index", "checkpointed_end_sample_index",
                "finalized_end_sample_index")))) or
            not s["source_first_sample_index"] <= s["selected_start_sample_index"] <= prefix or
            final["validated_samples"] != prefix - s["source_first_sample_index"] or
            not re.fullmatch(r"[0-9a-f]{64}", b["storage"]["selected_sha256"])):
        raise ValueError("prefix/finalization correlation")
    failure = dict(code="source_error", detail="wire device_0; ordinary=None; stop=" + str(stop["request_id"]),
                   operation=None, request_id=None, source_code=0)
    _exact_tree(n["failure"], failure, "normalized terminal exception")
    _exact_tree(s["first_failure"], failure, "first exception")
    if not (final["connect_intent_ns"] <= final["record_intent_return_ns"] <= final["stop_intent_ns"] <=
            final["close_intent_ns"] <= final["observed_ns"]):
        raise ValueError("intent order")


def _validate_callbacks(callbacks):
    if type(callbacks) is not tuple or not callbacks:
        raise ValueError("callback evidence absent")
    raw = 0
    for index, row in enumerate(callbacks, 1):
        from .stream_diagnostic import CallbackRecord
        if type(row) is not CallbackRecord:
            raise ValueError("callback type")
        for name in ("callback_id", "begin_ns", "return_ns"):
            if type(getattr(row, name)) is not int:
                raise ValueError("callback integer")
        if row.callback_id != index or not 0 <= row.begin_ns <= row.return_ns <= (1 << 63) - 1:
            raise ValueError("callback sequence/time")
        if row.error_type is not None or row.outcome not in ("known", "returned"):
            raise ValueError("callback unresolved/error")
        if type(row.offered) is not bytes or type(row.received) is not bytes:
            raise ValueError("callback byte types")
        if row.kind == "write":
            if (row.outcome != "known" or type(row.accepted_count) is not int or
                    not 0 <= row.accepted_count <= len(row.offered) <= 544 or row.received or
                    row.read_maximum is not None or row.actual_rx_length is not None):
                raise ValueError("write evidence")
        elif row.kind in ("factory", "open", "read", "close"):
            if row.outcome != "returned" or row.accepted_count is not None or row.offered:
                raise ValueError("nonwrite evidence")
            if row.kind == "read":
                if (type(row.read_maximum) is not int or type(row.actual_rx_length) is not int or
                        not 0 <= row.actual_rx_length == len(row.received) <= row.read_maximum <= 544):
                    raise ValueError("read evidence")
            elif row.received or row.read_maximum is not None or row.actual_rx_length is not None:
                raise ValueError("nonread evidence")
        else:
            raise ValueError("callback kind")
        raw += len(row.offered) + len(row.received)
    if len(callbacks) > INVOCATION_LIMITS.max_callbacks or raw > INVOCATION_LIMITS.max_raw_bytes:
        raise ValueError("callback quota")
    if [r.kind for r in callbacks].count("factory") != 1 or [r.kind for r in callbacks].count("open") != 1 or \
            [r.kind for r in callbacks].count("close") != 1:
        raise ValueError("one lifecycle required")


@dataclass(frozen=True, slots=True)
class EvidenceDecision:
    prospective_eligible: bool
    outcome: str
    reason: str
    scope: str = "synthetic-rehearsal"
    physical_admitted: bool = False
    clean_completion: bool = False


def evaluate_evidence(report, proof, selection):
    """Pure, fail-closed policy predicate on owned as-of HOST evidence."""
    try:
        validate_selection(selection)
        if type(report) is not DiagnosticReport:
            raise ValueError("supported fake report required")
        validate_owned_export(report, proof)
        metadata, final = json.loads(report.metadata), json.loads(report.final)
        _exact_tree(metadata["invocation"], asdict(selection), "invocation")
        for key, expected in (("format", FORMAT), ("origin", "synthetic-fake"), ("run_id", report.run_id),
                ("base_commit", selection.bindings.source_commit), ("base_tree", selection.bindings.source_tree),
                ("limits", asdict(INVOCATION_LIMITS)), ("settings", asdict(FAKE_SETTINGS)),
                ("selected_declaration", asdict(STR1_DIAGNOSTIC_DECLARATION)), ("flash_status", STAGE),
                ("expected_negotiation", dict(build_id=0x53545231, caps=list(STR1_DIAGNOSTIC_CAPS))),
                ("producer_profile", None), ("firmware_image", None), ("usb_identity", None), ("telemetry", None)):
            _exact_tree(metadata[key], expected, "metadata." + key)
        if final["run_id"] != report.run_id or not re.fullmatch(r"[0-9a-f]{32}", report.run_id):
            raise ValueError("report run identity")
        _validate_final(final)
        _validate_callbacks(report.callbacks)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return EvidenceDecision(False, "ineligible", str(exc)[:192])
    return EvidenceDecision(True, "quiescence-qualified-incomplete", "prospective predicate satisfied by synthetic evidence")


@dataclass(frozen=True, slots=True)
class InvocationResult:
    report: DiagnosticReport
    proof: object
    evidence: EvidenceDecision
    export: ExportStatus
    outcome: str


class RehearsalInvocation:
    """One fake attempt, fixed policy budgets, separate finite export observation."""
    def __init__(self, selection, *, fake_backend_factory, fixture_name, event_config,
                 clock_ns=time.monotonic_ns):
        self._selection = validate_selection(selection)
        self._run = DiagnosticRun(FAKE_SETTINGS, fake_backend_factory=fake_backend_factory,
            fake_origin="synthetic-fake", fixture_name=fixture_name, event_config=event_config,
            clock_ns=clock_ns, limits=INVOCATION_LIMITS,
            fake_diagnostic_declaration=STR1_DIAGNOSTIC_DECLARATION, invocation_selection=selection)
        self._lock = threading.Lock()
        self._used = False

    @property
    def selection(self):
        return self._selection

    def request_stop(self):
        return self._run.request_stop()

    def close(self):
        return self._run.close()

    def snapshot(self):
        return self._run.snapshot()

    def join(self, timeout=0):
        return self._run.join(timeout)

    def collect(self):
        with self._lock:
            if self._used:
                raise RuntimeError("one invocation only")
            self._used = True
        report = self._run.collect(self.selection.recording_path)
        deadline = time.monotonic_ns() + EXPORT_OBSERVATION_MS * 1000000
        self._run.write_report(self.selection.report_path)
        observed = time.monotonic_ns()
        expired = observed >= deadline
        self._run.join(max(0, deadline - observed) / 1000000000)
        expired = time.monotonic_ns() >= deadline or expired
        proof = self._run.owned_export_proof()
        expired = time.monotonic_ns() >= deadline or expired
        status = self._run.snapshot().export
        expired = time.monotonic_ns() >= deadline or expired
        # Initiation and owner observations can resume late. Keep their actual
        # receipt facts without granting another budget or retroactive success.
        # Subsequent pure validation cost is not report-owner observation time.
        decision = (EvidenceDecision(False, "ineligible", "export observation deadline expired")
                    if expired else evaluate_evidence(report, proof, self.selection))
        outcome = ("export-unresolved" if expired or not status.owner_settled else
                   "export-failed" if status.error is not None else decision.outcome)
        return InvocationResult(report, proof, decision, status, outcome)

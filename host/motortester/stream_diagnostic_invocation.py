"""Explicit one-attempt STR1 source boundary; imports and defaults are inactive.

Selections and receipts are evidence associations, never image authentication,
owner authority, physical Stop proof, or permission to retrieve RAM.
"""
from dataclasses import asdict, dataclass
from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import threading
import time

from .stream_diagnostic import (DiagnosticLimits, DiagnosticRun, DiagnosticReport,
                                ExportStatus, FORMAT, PHYSICAL_FORMAT, PHYSICAL_MODULES,
                                _PhysicalDiagnosticRun, validate_owned_export)
from .stream_provenance import (STR1_DIAGNOSTIC_DECLARATION, STR1_DIAGNOSTIC_CAPS,
                                ProducerProfile, admit_fake_diagnostic_declaration,
                                rehearsal_identity_metadata, PhysicalDiagnosticContext,
                                physical_context_identity)
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


def physical_admission(selection, *, deployment=None, confirmation=None):
    if type(selection) is Selection:
        validate_selection(selection)
        return Admission()
    validate_deployment(selection, deployment, confirmation)
    return Admission(True, DEPLOYED_STAGE,
                     "declared prerequisites consistent; external authority is not authenticated")


def collect_physical(selection, *, deployment=None, confirmation=None, event_config=None):
    """Refuse the owner-losing convenience route, even for consistent inputs."""
    stage = STAGE + ": " if type(selection) is Selection else ""
    raise RuntimeError(stage + "collect_physical is disabled; retain a PhysicalInvocation instance, "
                       "call its collect(), and keep it for cleanup-only retry_close()/join()")


DEPLOYED_STAGE = "APPROVED_FOR_ONE_STR1_DIAGNOSTIC_ATTEMPT"
PHYSICAL_VERSION = "str1-physical-selection-1"
MAX_DEPLOYMENT_BYTES = 12288


@dataclass(frozen=True, slots=True)
class HostFreeze:
    commit: str
    tree: str
    module_sha256: tuple  # Immutable sorted (fixed source name, SHA256) pairs.


@dataclass(frozen=True, slots=True)
class ReadbackIdentity:
    """Externally declared bytes/coverage; HOST did not observe flashing."""
    sha256: str
    coverage: str
    artifact_identity: str


@dataclass(frozen=True, slots=True)
class PhysicalSelection:
    invocation_id: str
    association: Association
    recording_path: str
    report_path: str
    host: HostFreeze
    package_sha256: str
    exe_sha256: str
    readback: ReadbackIdentity
    version: str = PHYSICAL_VERSION
    declaration: ProducerProfile = STR1_DIAGNOSTIC_DECLARATION
    policy_comment: str = Bindings().policy_comment
    policy_sha256: str = Bindings().policy_sha256
    review_sha256: str = Bindings().review_sha256


@dataclass(frozen=True, slots=True)
class DecisionReference:
    """Private operator evidence reference, not a credential or fetched proof."""
    reference: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class DeploymentRecord:
    selection: PhysicalSelection
    selection_sha256: str
    a_final_disposition: DecisionReference
    owner_authorization: DecisionReference
    disposition: str = STAGE
    version: str = "str1-final-disposition-reference-1"


@dataclass(frozen=True, slots=True)
class OperatorConfirmation:
    operator: str
    invocation_id: str
    selection_sha256: str
    action: str = "collect-one-str1-attempt"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _digest(value, length=64):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is None:
        raise ValueError("exact lowercase digest required")


def _declared_text(value):
    _text(value, 192)
    if value.casefold() in ("unknown", "tbd", "todo", "pending", "placeholder", "none"):
        raise ValueError("placeholder declaration refused")


def physical_selection_payload(selection):
    value = asdict(selection)
    value["host"]["module_sha256"] = dict(selection.host.module_sha256)
    return value


def selection_digest(selection):
    validate_physical_selection(selection)
    return hashlib.sha256(_canonical(physical_selection_payload(selection))).hexdigest()


def validate_physical_selection(selection):
    """Only selection/consistency; no image, USB, code or authority authentication."""
    if type(selection) is not PhysicalSelection:
        raise ValueError("exact physical selection required")
    # Reuse the accepted literal target/path validation, without changing its
    # current-stage fake policy meaning or allowing any physical factory there.
    validate_selection(Selection(selection.invocation_id, selection.association,
                                 selection.recording_path, selection.report_path))
    _declared_text(selection.association.board_instance)
    _declared_text(selection.association.windows_pc)
    _exact_tree(selection.version, PHYSICAL_VERSION, "physical version")
    admit_fake_diagnostic_declaration(selection.declaration)
    for name in ("policy_comment", "policy_sha256", "review_sha256"):
        _exact_tree(getattr(selection, name), getattr(Bindings(), name), name)
    if type(selection.host) is not HostFreeze or type(selection.host.module_sha256) is not tuple:
        raise ValueError("exact HOST freeze required")
    _digest(selection.host.commit, 40); _digest(selection.host.tree, 40)
    names = []
    for pair in selection.host.module_sha256:
        if type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str:
            raise ValueError("immutable source map required")
        names.append(pair[0]); _digest(pair[1])
    if names != sorted(PHYSICAL_MODULES):
        raise ValueError("exact fixed source map required")
    _digest(selection.package_sha256); _digest(selection.exe_sha256)
    if type(selection.readback) is not ReadbackIdentity:
        raise ValueError("separate externally declared readback identity required")
    _digest(selection.readback.sha256)
    _declared_text(selection.readback.coverage); _declared_text(selection.readback.artifact_identity)
    if len(_canonical(physical_selection_payload(selection))) > 4096:
        raise ValueError("physical selection capacity")
    return selection


def deployment_payload(deployment):
    value = asdict(deployment)
    value["selection"] = physical_selection_payload(deployment.selection)
    return value


def _validate_deployment_record(selection, deployment):
    validate_physical_selection(selection)
    if type(deployment) is not DeploymentRecord:
        raise ValueError("explicit deployment required")
    validate_physical_selection(deployment.selection)
    _exact_tree(deployment.version, "str1-final-disposition-reference-1", "deployment version")
    if deployment.disposition == STAGE:
        raise ValueError(STAGE + ": current deployment is not admitted")
    _exact_tree(deployment.disposition, DEPLOYED_STAGE, "disposition")
    digest = selection_digest(selection)
    if (deployment.selection != selection or deployment.selection_sha256 != digest or
            type(deployment.selection_sha256) is not str):
        raise ValueError("deployment selection mismatch")
    for reference in (deployment.a_final_disposition, deployment.owner_authorization):
        if type(reference) is not DecisionReference:
            raise ValueError("explicit external decision reference required")
        _declared_text(reference.reference); _digest(reference.content_sha256)
    if len(_canonical(deployment_payload(deployment))) > MAX_DEPLOYMENT_BYTES:
        raise ValueError("deployment capacity")
    return digest


def validate_deployment(selection, deployment, confirmation):
    digest = _validate_deployment_record(selection, deployment)
    if type(confirmation) is not OperatorConfirmation:
        raise ValueError("explicit operator confirmation required")
    _declared_text(confirmation.operator)
    _exact_tree(confirmation.invocation_id, selection.invocation_id, "confirmed invocation")
    _exact_tree(confirmation.selection_sha256, digest, "confirmed selection")
    _exact_tree(confirmation.action, "collect-one-str1-attempt", "confirmed action")
    return deployment


def observed_source_hashes():
    """Bounded source-file observations, never an executed-code attestation."""
    from . import stream_diagnostic
    hashes = []
    for name in sorted(PHYSICAL_MODULES):
        with open(Path(stream_diagnostic.__file__).parent / name, "rb") as source:
            content = source.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("source file capacity")
        hashes.append((name, hashlib.sha256(content).hexdigest()))
    return tuple(hashes)


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


def _validate_final(final, *, context=None, port=None):
    template = _FINAL
    if context is not None:
        template = deepcopy(_FINAL)
        origin, _ = physical_context_identity(context)
        # deepcopy preserves the shared identity-schema references.
        template["bridge"]["identity"]["origin"] = origin
        template["serial"]["port"] = port
    _exact_tree(final, template)
    b, s = final["bridge"], final["bridge"]["session"]
    end, ack = b["source_terminal"], s["stop_acknowledgement"]
    t, n, fault = end["terminal"], s["source_end"]["terminal"], b["endpoint_first_fault"]
    identity, stop = b["identity"], fault["stop"]
    expected_meta = (rehearsal_identity_metadata(STR1_DIAGNOSTIC_DECLARATION) if context is None
                     else physical_context_identity(context)[1])
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


def _validate_callbacks(callbacks, *, allow_dtr=False):
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
        elif row.kind in (("factory", "open", "read", "close", "dtr") if allow_dtr else
                         ("factory", "open", "read", "close")):
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
    if allow_dtr:
        kinds = [r.kind for r in callbacks]
        if kinds.count("dtr") != 1 or not kinds.index("factory") < kinds.index("dtr") < kinds.index("open"):
            raise ValueError("one owned pre-open DTR request required")


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


def _collect_and_export(run, selection, evaluator, *, scope="synthetic-rehearsal"):
    report = run.collect(selection.recording_path)
    deadline = time.monotonic_ns() + EXPORT_OBSERVATION_MS * 1000000
    export_error = None
    try:
        run.write_report(selection.report_path)
    except (Exception, KeyboardInterrupt) as exc:
        export_error = "export initiation " + type(exc).__name__
    observed = time.monotonic_ns()
    expired = observed >= deadline
    try:
        run.join(max(0, deadline - observed) / 1000000000)
    except (Exception, KeyboardInterrupt) as exc:
        export_error = export_error or "export observation " + type(exc).__name__
    expired = time.monotonic_ns() >= deadline or expired
    proof = run.owned_export_proof()
    expired = time.monotonic_ns() >= deadline or expired
    status = run.snapshot().export
    expired = time.monotonic_ns() >= deadline or expired
    # Actual late receipt facts remain visible, never retroactively timely.
    # Subsequent pure validation cost is not report-owner observation time.
    decision = (EvidenceDecision(False, "ineligible", "export observation deadline expired", scope)
                if expired else EvidenceDecision(False, "ineligible", export_error, scope)
                if export_error else evaluator(report, proof))
    outcome = ("export-unresolved" if expired or not status.owner_settled else
               "export-failed" if export_error or status.error is not None else decision.outcome)
    return InvocationResult(report, proof, decision, status, outcome)


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
        return _collect_and_export(self._run, self.selection,
                                   lambda report, proof: evaluate_evidence(report, proof, self.selection))


def _evaluate_operational(report, proof, selection, deployment, confirmation, kind):
    scope = "physical-device" if kind == "physical" else "physical-path-synthetic-rehearsal"
    try:
        validate_deployment(selection, deployment, confirmation)
        validate_owned_export(report, proof)
        context = PhysicalDiagnosticContext(kind)
        metadata, final = json.loads(report.metadata), json.loads(report.final)
        expected = {"format": PHYSICAL_FORMAT if kind == "physical" else FORMAT,
            "origin": "device" if kind == "physical" else "synthetic-fake",
            "run_id": report.run_id, "base_commit": selection.host.commit, "base_tree": selection.host.tree,
            "physical_context": asdict(context),
            "physical_selection": {"deployment": deployment_payload(deployment), "confirmation": asdict(confirmation)},
            "settings": asdict(SerialSettings(selection.association.port, 230400, 0, .025)),
            "limits": asdict(INVOCATION_LIMITS), "selected_declaration": asdict(STR1_DIAGNOSTIC_DECLARATION),
            "expected_negotiation": {"build_id": 0x53545231, "caps": list(STR1_DIAGNOSTIC_CAPS)},
            "dtr_request": 1, "dtr_electrical_observation": None, "external_authority_verified": False,
            "firmware_image": None, "usb_identity": None, "telemetry": None}
        for key, value in expected.items():
            _exact_tree(metadata[key], value, "metadata." + key)
        _exact_tree(json.loads(proof._exported.metadata)["module_sha256"], dict(selection.host.module_sha256),
                    "observed source files")
        if final["run_id"] != report.run_id or not re.fullmatch(r"[0-9a-f]{32}", report.run_id):
            raise ValueError("report run identity")
        _validate_final(final, context=context, port=selection.association.port)
        _validate_callbacks(report.callbacks, allow_dtr=True)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return EvidenceDecision(False, "ineligible", str(exc)[:192], scope)
    return EvidenceDecision(True, "quiescence-qualified-incomplete",
        "exact owned HOST predicate satisfied; external physical prerequisites remain separate", scope,
        physical_admitted=(kind == "physical"))


def evaluate_physical_evidence(report, proof, selection, deployment, confirmation):
    """Device-only evidence check; injected full-path output always fails here."""
    return _evaluate_operational(report, proof, selection, deployment, confirmation, "physical")


class PhysicalInvocation(RehearsalInvocation):
    """Inactive construction; one explicit action after externally checked inputs.

    The local record is not authenticated authority. Actual private references
    stay in local evidence. Test construction cannot produce device provenance.
    """
    def __init__(self, selection, *, deployment, confirmation, event_config,
                 clock_ns=time.monotonic_ns):
        self._initialize(selection, deployment, confirmation, event_config, clock_ns,
                         "physical", None, "selected-physical-attempt")

    @classmethod
    def for_rehearsal(cls, selection, *, deployment, confirmation, event_config,
                      fake_backend_factory, fixture_name, clock_ns=time.monotonic_ns):
        if not callable(fake_backend_factory):
            raise ValueError("explicit injected operational fake required")
        instance = cls.__new__(cls)
        instance._initialize(selection, deployment, confirmation, event_config, clock_ns,
                             "injected-test", fake_backend_factory, fixture_name)
        return instance

    def _initialize(self, selection, deployment, confirmation, event_config, clock_ns,
                    kind, factory, fixture_name):
        validate_deployment(selection, deployment, confirmation)
        self._selection, self._deployment, self._confirmation = selection, deployment, confirmation
        self._kind = kind
        self._run = _PhysicalDiagnosticRun(SerialSettings(selection.association.port, 230400, 0, .025),
            context=PhysicalDiagnosticContext(kind), backend_factory=factory, fixture_name=fixture_name,
            event_config=event_config, clock_ns=clock_ns,
            selection_metadata={"deployment": deployment_payload(deployment), "confirmation": asdict(confirmation)})
        self._lock, self._used = threading.Lock(), False

    def retry_close(self):
        """Explicit cleanup-only retry on the retained owner; no new attempt."""
        return self._run.retry_close()

    def collect(self):
        with self._lock:
            if self._used:
                raise RuntimeError("one invocation only")
            self._used = True
        validate_deployment(self.selection, self._deployment, self._confirmation)
        if observed_source_hashes() != self.selection.host.module_sha256:
            raise ValueError("selected source-file identities differ from observations")
        if any(os.path.lexists(path) for path in (self.selection.recording_path, self.selection.report_path)):
            raise ValueError("fresh output paths required")
        return _collect_and_export(self._run, self.selection,
            lambda report, proof: _evaluate_operational(report, proof, self.selection, self._deployment,
                                                       self._confirmation, self._kind),
            scope="physical-device" if self._kind == "physical" else "physical-path-synthetic-rehearsal")


def _object(cls, value, **converted):
    if type(value) is not dict or set(value) != set(cls.__dataclass_fields__):
        raise ValueError("exact " + cls.__name__ + " JSON schema required")
    return cls(**{**value, **converted})


def parse_deployment(value):
    """Bounded data conversion only. No reference fetch or backend admission."""
    if type(value) is not dict or len(_canonical(value)) > MAX_DEPLOYMENT_BYTES:
        raise ValueError("bounded deployment JSON required")
    try:
        raw = value["selection"]
        host = raw["host"]
        if type(host["module_sha256"]) is not dict:
            raise ValueError("fixed source map required")
        selected = _object(PhysicalSelection, raw,
            association=_object(Association, raw["association"]),
            host=_object(HostFreeze, host, module_sha256=tuple(sorted(host["module_sha256"].items()))),
            readback=_object(ReadbackIdentity, raw["readback"]),
            declaration=_object(ProducerProfile, raw["declaration"]))
        result = _object(DeploymentRecord, value, selection=selected,
            a_final_disposition=_object(DecisionReference, value["a_final_disposition"]),
            owner_authorization=_object(DecisionReference, value["owner_authorization"]))
        validate_physical_selection(selected)
        return result
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("deployment JSON structure") from exc


def _read_deployment(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate deployment key")
            result[key] = value
        return result
    with open(path, "rb") as source:
        data = source.read(MAX_DEPLOYMENT_BYTES + 1)
    if len(data) > MAX_DEPLOYMENT_BYTES:
        raise ValueError("deployment file capacity")
    return parse_deployment(json.loads(data, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite deployment JSON"))))


_CLI_OWNER = None


def retained_cli_invocation():
    """Cleanup-only observation handle; no new acquisition or automatic retry."""
    return _CLI_OWNER


def main(argv=None, *, fake_backend_factory=None):
    """Source-review CLI. Explicit test injection always forces synthetic output.

    It is not an alternative distribution to the separately gated package.
    Private references/paths/board-PC labels are never printed automatically.
    """
    global _CLI_OWNER
    parser = argparse.ArgumentParser(description="Inactive-by-default one-attempt STR1 source diagnostic")
    parser.add_argument("--deployment", help="local/private bounded deployment JSON")
    parser.add_argument("--execute-one-attempt", action="store_true", help="explicit operation request, not approval")
    parser.add_argument("--operator")
    parser.add_argument("--invocation-id")
    parser.add_argument("--confirm-selection", help="exact displayed canonical selection SHA256")
    args = parser.parse_args(argv)
    if args.deployment is None:
        print("Inactive: no deployment selected; no backend or output created.")
        return 2
    try:
        deployed = _read_deployment(args.deployment)
        selected = deployed.selection
        digest = _validate_deployment_record(selected, deployed)
        if not args.execute_one_attempt:
            print("Inactive: declaration consistency only; selection_sha256=" + digest)
            print("External A/owner decisions are not authenticated; no operation requested.")
            return 0
        confirmed = OperatorConfirmation(args.operator, args.invocation_id, args.confirm_selection)
        if _CLI_OWNER is not None and not _CLI_OWNER.join(0):
            raise RuntimeError("previous CLI owner remains unresolved")
        from .stream_view import EventConfig
        # Bounded HOST display detector only; no wire/ADC configuration change.
        config = EventConfig(1000, 500, "above", 8, 12, 3, 0, 256, 0, 4095)
        if fake_backend_factory is None:
            run = PhysicalInvocation(selected, deployment=deployed, confirmation=confirmed, event_config=config)
        else:
            run = PhysicalInvocation.for_rehearsal(selected, deployment=deployed, confirmation=confirmed,
                event_config=config, fake_backend_factory=fake_backend_factory,
                fixture_name="fabricated CLI injected operational source test")
        _CLI_OWNER = run
        result = run.collect()
        print(result.outcome + "; scope=" + result.evidence.scope + "; clean_completion=false")
        print("No RAM action; external physical prerequisites remain separate.")
        return 0 if result.evidence.prospective_eligible else 1
    except (ValueError, TypeError, OSError, RuntimeError, RecursionError) as exc:
        # Exception messages may contain private paths or reference content.
        print("Ineligible: input/preflight refused (" + type(exc).__name__ + "). No automatic retry.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

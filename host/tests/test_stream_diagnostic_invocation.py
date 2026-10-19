"""Source-only encoded fake flows and explicitly fabricated policy/schema controls.

All executing backends are mandatory injected FakeFactory instances. Physical
carrier dictionaries below are fabricated schemas, never a physical report.
"""
from copy import deepcopy
from dataclasses import asdict, fields, replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from motortester import stream_diagnostic as diagnostic
from motortester import stream_diagnostic_invocation as invocation
from motortester import stream_provenance as provenance
from motortester.stream_diagnostic_invocation import (Association, Bindings, Selection,
    RehearsalInvocation, collect_physical, evaluate_evidence, physical_admission, validate_selection)
from motortester.stream_model import StreamIdentity
from test_stream_controller import CONFIG, wait_for
from test_stream_diagnostic import str1_factory


def selection(root, suffix="sample"):
    return Selection("1" * 32, Association("COM7", "AH20261017001", "fabricated-board-1", "fabricated-PC-1"),
                     str(root / (suffix + ".mtstream")), str(root / (suffix + ".jsonl")))


def factory(*, reason=0, data=True, configure=None):
    def setup(backend):
        peer = backend.peer
        original = peer._end
        peer._end = lambda **kwargs: original(reason=reason, flags=3, produced=0, **kwargs)
        if data:
            read, emitted = peer.read, [False]
            def reading(maximum):
                result = read(maximum)
                if peer.generation and peer.start_waiting is None and not emitted[0]:
                    emitted[0] = True
                    peer.data()
                return result
            peer.read = reading
        if configure:
            configure(backend)
    return str1_factory(unknown_end=False, configure=setup)


def leaves(tree, prefix=()):
    for key, value in tree.items():
        if type(value) is dict:
            yield from leaves(value, prefix + (key,))
        else:
            yield prefix + (key,), value


def parent(tree, path):
    for key in path[:-1]:
        tree = tree[key]
    return tree


def operational_inputs(root, name="sample"):
    """Conspicuously fabricated approval/identity values, never real authority."""
    old = selection(root, name)
    digest = lambda text: hashlib.sha256(text.encode("ascii")).hexdigest()
    chosen = invocation.PhysicalSelection(old.invocation_id, old.association,
        old.recording_path, old.report_path,
        invocation.HostFreeze("1" * 40, "2" * 40, invocation.observed_source_hashes()),
        digest("fabricated package"), digest("fabricated EXE"),
        invocation.ReadbackIdentity(digest("fabricated separate readback"),
            "fabricated byte extent 0..4095; not observed hardware", "fabricated-readback-fixture"))
    selected = invocation.selection_digest(chosen)
    deployed = invocation.DeploymentRecord(chosen, selected,
        invocation.DecisionReference("fabricated:A-final-test-only", digest("fabricated A decision")),
        invocation.DecisionReference("fabricated:owner-test-only", digest("fabricated owner decision")),
        invocation.DEPLOYED_STAGE)
    confirmed = invocation.OperatorConfirmation("fabricated-test-operator", chosen.invocation_id, selected)
    return chosen, deployed, confirmed


class InvocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="str1-invocation-")
        cls.root = Path(cls.temp.name)
        cls.guard = patch("motortester.transport._serial_factory", side_effect=AssertionError("Real serial forbidden"))
        cls.guard.start()
        cls.runs = []
        cls.good, cls.good_factory, cls.good_result = cls.capture("positive")
        cls.bad, cls.bad_factory, cls.bad_result = cls.capture("reason2", reason=2)

    @classmethod
    def tearDownClass(cls):
        for run in cls.runs:
            run.close()
            if not run.join(3):
                raise AssertionError("fake owners did not settle")
        cls.guard.stop()
        cls.temp.cleanup()

    @classmethod
    def capture(cls, name, *, reason=0, data=True, configure=None, natural=False):
        f = factory(reason=reason, data=data, configure=configure)
        run = RehearsalInvocation(selection(cls.root, name), fake_backend_factory=f,
                                  fixture_name="inspected encoded synthetic STR1 fixture", event_config=CONFIG)
        cls.runs.append(run)
        if natural:
            return run, f, run.collect()
        results, errors = [], []
        def collect():
            try:
                results.append(run.collect())
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=collect)
        thread.start()
        try:
            def ready():
                status = run.snapshot().controller
                if errors:
                    raise errors[0]
                if not thread.is_alive():
                    raise AssertionError("collector ended before synthetic Record: " + repr(results))
                return (status is not None and status.phase == "recording" and status.bridge.session.source_started and
                        status.bridge.session.accepted_end_sample_index == (128 if data else 0))
            wait_for(ready, timeout=4)
        finally:
            run.request_stop()
            thread.join(7)
        if thread.is_alive() or errors or len(results) != 1:
            raise AssertionError((thread.is_alive(), errors, results))
        return run, f, results[0]

    def test_encoded_reason0_and_reason2_have_complete_exports_but_distinct_eligibility(self):
        for run, result, eligible in ((self.good, self.good_result, True), (self.bad, self.bad_result, False)):
            self.assertTrue(diagnostic.read_report(run.selection.report_path).complete)
            self.assertTrue(result.export.complete_receipt)
            self.assertTrue(result.export.fsync_returned)
            self.assertTrue(result.export.owner_settled)
            self.assertEqual(result.evidence.prospective_eligible, eligible, result.evidence)
            self.assertEqual(result.evidence.scope, "synthetic-rehearsal")
            self.assertFalse(result.evidence.physical_admitted)
            self.assertFalse(result.evidence.clean_completion)
            final = json.loads(result.report.final)
            self.assertIsNone(final["bridge"]["source_terminal"]["terminal"]["produced_end"])
            self.assertFalse(final["bridge"]["session"]["clean_completion_reported"])
        self.assertEqual(self.good_result.outcome, "quiescence-qualified-incomplete")

    def test_fixed_settings_limits_and_export_source_policy_bindings(self):
        report = self.good_result.report
        metadata = json.loads(report.metadata)
        self.assertEqual(metadata["limits"], asdict(invocation.INVOCATION_LIMITS))
        self.assertEqual(metadata["settings"], asdict(invocation.FAKE_SETTINGS))
        self.assertEqual(metadata["invocation"], asdict(self.good.selection))
        rows = diagnostic.read_report(self.good.selection.report_path).records
        exported = json.loads(rows[0])["payload"]
        self.assertEqual(set(exported["module_sha256"]), set(diagnostic.INVOCATION_MODULES))
        self.assertIsNone(metadata["source_hash_observed_ns"])
        self.assertIs(type(exported["source_hash_observed_ns"]), int)
        for name, expected in exported["module_sha256"].items():
            self.assertEqual(hashlib.sha256((Path(diagnostic.__file__).parent / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(exported["invocation"]["bindings"]["policy_comment"], Bindings().policy_comment)

    def test_construction_selection_and_physical_refusal_are_inert(self):
        f = factory()
        selected = selection(self.root, "inert")
        with patch("threading.Thread") as workers, patch.object(diagnostic, "_open_file") as files:
            run = RehearsalInvocation(selected, fake_backend_factory=f, fixture_name="inert fake", event_config=CONFIG)
            self.assertIs(validate_selection(selected), selected)
            self.assertFalse(physical_admission(selected).admitted)
            with self.assertRaisesRegex(RuntimeError, "NOT_FLASH_APPROVED"):
                collect_physical(selected)
            with self.assertRaises(TypeError):
                collect_physical(selected, approved=True)
            with self.assertRaises(TypeError):
                collect_physical(selected, fake_backend_factory=f)
            self.assertIsNone(run.snapshot().controller)
            workers.assert_not_called(); files.assert_not_called()
        self.assertEqual(f.calls, [])
        self.assertFalse(Path(selected.recording_path).exists())
        self.assertFalse(Path(selected.report_path).exists())
        with self.assertRaises(AttributeError):
            run.selection = selected

    def test_selection_every_binding_value_type_and_association_rejected_before_factory(self):
        f = factory()
        good = selection(self.root, "invalid")
        cases = [None, {}, replace(good, stage="FLASH_APPROVED"), replace(good, stage=True),
                 replace(good, invocation_id=""), replace(good, invocation_id="x" * 32),
                 replace(good, export_observation_ms=True), replace(good, export_observation_ms=1001),
                 replace(good, recording_path="relative.mtstream"), replace(good, report_path=good.recording_path),
                 replace(good, report_path=str(self.root / ".." / "bad")), replace(good, association=None),
                 replace(good, report_path="/" + "x" * 2500)]
        for field in fields(good.bindings):
            old = getattr(good.bindings, field.name)
            if type(old) is str:
                cases.extend(replace(good, bindings=replace(good.bindings, **{field.name: value}))
                             for value in (old + "x", None, True))
        for field in fields(good.bindings.declaration):
            old = getattr(good.bindings.declaration, field.name)
            for value in ((old + "x", None) if type(old) is str else (old + 1, True, float(old))):
                cases.append(replace(good, bindings=replace(good.bindings,
                    declaration=replace(good.bindings.declaration, **{field.name: value}))))
        for field in fields(good.association):
            old = getattr(good.association, field.name)
            bad = ("", None, True) if type(old) is str else (old + 1, True, float(old))
            for value in bad:
                cases.append(replace(good, association=replace(good.association, **{field.name: value})))
        cases += [replace(good, association=replace(good.association, port=value)) for value in
                  ("COM0", "com7", " COM7", "COM7 ", "COM0007", "COM100000", "1209:0001", "loop://", "\\\\.\\COM7")]
        with patch("threading.Thread") as workers, patch.object(diagnostic, "_open_file") as files:
            for bad in cases:
                with self.subTest(selection=repr(bad)[:100]), self.assertRaises(ValueError):
                    RehearsalInvocation(bad, fake_backend_factory=f, fixture_name="invalid fake", event_config=CONFIG)
            workers.assert_not_called(); files.assert_not_called()
        self.assertEqual(f.calls, [])

    def test_required_final_field_deletions_and_exact_scalar_types_fail_closed(self):
        # Explicitly fabricated mutations of a genuine captured summary; these
        # dictionaries are not given an owned export or presented as hardware.
        final = json.loads(self.good_result.report.final)
        invocation._validate_final(final)
        count = 0
        for path, value in leaves(final):
            missing = deepcopy(final)
            del parent(missing, path)[path[-1]]
            with self.subTest(missing=path), self.assertRaises(ValueError):
                invocation._validate_final(missing)
            wrong = deepcopy(final)
            parent(wrong, path)[path[-1]] = (not value if type(value) is bool else
                                           bool(value) if type(value) is int else
                                           False if value is None else None)
            with self.subTest(type=path), self.assertRaises(ValueError):
                invocation._validate_final(wrong)
            count += 1
        self.assertGreater(count, 100)

    def test_exact_terminal_ack_identity_prefix_fault_and_settlement_mutations(self):
        final = json.loads(self.good_result.report.final)
        cases = {
            ("bridge", "source_terminal", "terminal", "reason"): 2,
            ("bridge", "source_terminal", "terminal", "flags"): 0,
            ("bridge", "source_terminal", "terminal", "produced_end"): 128,
            ("bridge", "source_terminal", "nonce"): 1,
            ("bridge", "source_terminal", "sequence"): 1,
            ("bridge", "source_terminal", "terminal", "generation"): 1,
            ("bridge", "source_terminal", "terminal", "stop_request_id"): 999,
            ("bridge", "source_prefix_crc32"): 1,
            ("bridge", "session", "source_end", "terminal_id"): "stale-end",
            ("bridge", "session", "stop_acknowledgement", "terminal_id"): "stale-ack",
            ("bridge", "session", "stop_acknowledgement", "request_id"): 999,
            ("bridge", "session", "stop_acknowledgement", "stop_action_id"): 999,
            ("bridge", "session", "stop_acknowledgement", "end_sample_index"): 999,
            ("bridge", "session", "stop_acknowledgement", "quiescence_reported"): False,
            ("bridge", "session", "source_end", "terminal", "loss_samples"): 0,
            ("bridge", "session", "first_failure", "detail"): "contains device_0 but unrelated",
            ("bridge", "endpoint_first_fault", "code"): "device_2",
            ("bridge", "endpoint_first_fault", "stop", "nonce"): 1,
            ("bridge", "endpoint_first_fault", "stop", "complete"): False,
            ("bridge", "session", "clean_completion_reported"): True,
            ("bridge", "session", "source_started"): False,
            ("bridge", "session", "finalized"): False,
            ("bridge", "session", "finalization_unresolved"): True,
            ("bridge", "session", "selected_start_sample_index"): None,
            ("bridge", "storage", "returned"): False,
            ("bridge", "mailbox", "result_pending"): True,
            ("bridge", "mailbox", "close_acknowledged"): False,
            ("serial", "handle_settled"): False,
            ("serial", "state"): "open",
            ("controller_joined",): False,
            ("worker_unresolved",): True,
            ("serial_unresolved",): True,
            ("trace_complete",): False,
            ("dropped_callbacks",): 1,
        }
        for key in ("recovery", "late_fault", "pending_start", "pending_stop"):
            cases[("bridge", key)] = {}
        for key in ("error", "run_error", "trace_error", "serial_call", "first_gap_ns"):
            cases[(key,)] = "bad"
        for path, value in cases.items():
            bad = deepcopy(final); parent(bad, path)[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                invocation._validate_final(bad)
        for i in range(8):
            bad = deepcopy(final); bad["bridge"]["accepted_caps"][i] = True
            with self.subTest(caps=i), self.assertRaises(ValueError):
                invocation._validate_final(bad)

    def test_cross_run_stale_and_equal_copy_cannot_borrow_owned_export(self):
        report, proof = self.good_result.report, self.good_result.proof
        self.assertTrue(evaluate_evidence(report, proof, self.good.selection).prospective_eligible)
        for other_report, other_proof in ((report, self.bad_result.proof), (self.bad_result.report, proof),
                (replace(report), proof), (report, self.good_result.export), (report, None)):
            self.assertFalse(evaluate_evidence(other_report, other_proof, self.good.selection).prospective_eligible)
        final = json.loads(report.final); final["validated_samples"] += 1
        altered = replace(report, _final=diagnostic._freeze_tree(final))
        self.assertFalse(evaluate_evidence(altered, proof, self.good.selection).prospective_eligible)
        with self.assertRaises(TypeError):
            diagnostic.OwnedExportProof(None, report, "0" * 64, report, (1, "0" * 64), self.good_result.export)
        with self.assertRaises(TypeError):
            replace(proof, file_sha256="0" * 64)

    def test_unknown_pending_invalid_exception_and_dropped_callbacks_rejected(self):
        report = self.good_result.report
        invocation._validate_callbacks(report.callbacks)
        for outcome in ("unknown", "pending", "exception", "invalid_rx"):
            callbacks = (replace(report.callbacks[0], outcome=outcome),) + report.callbacks[1:]
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                invocation._validate_callbacks(callbacks)
        for callbacks in (report.callbacks[1:], (),
                          (replace(report.callbacks[0], callback_id=True),) + report.callbacks[1:],
                          (replace(report.callbacks[0], return_ns=None),) + report.callbacks[1:]):
            with self.assertRaises(ValueError):
                invocation._validate_callbacks(callbacks)
        writes = [i for i, row in enumerate(report.callbacks) if row.kind == "write"]
        for count in (0, 1):
            changed = list(report.callbacks); changed[writes[0]] = replace(changed[writes[0]], accepted_count=count)
            invocation._validate_callbacks(tuple(changed))

    def test_started_empty_interval_passes_but_cancel_before_connect_has_no_R(self):
        run, f, result = self.capture("empty", data=False)
        self.assertTrue(result.evidence.prospective_eligible, result.evidence)
        self.assertEqual(json.loads(result.report.final)["bridge"]["session"]["finalized_end_sample_index"], 0)
        f = factory()
        run = RehearsalInvocation(selection(self.root, "pre-cancel"), fake_backend_factory=f,
                                  fixture_name="cancelled fake", event_config=CONFIG)
        self.runs.append(run)
        run.request_stop(); run.close()
        result = run.collect()
        self.assertFalse(result.evidence.prospective_eligible)
        final = json.loads(result.report.final)
        self.assertIsNone(final["connect_intent_ns"])
        self.assertIsNone(final["record_intent_return_ns"])
        self.assertEqual(f.calls, [])
        self.assertFalse(Path(run.selection.recording_path).exists())
        with self.assertRaises(RuntimeError):
            run.collect()

    def test_original_stop_close_intents_never_restart(self):
        f, now = factory(), [100]
        run = RehearsalInvocation(selection(self.root, "origins"), fake_backend_factory=f,
            fixture_name="intent fake", event_config=CONFIG, clock_ns=lambda: now[0])
        run.request_stop(); now[0] = 200; run.request_stop()
        run.close(); now[0] = 999; run.close()
        self.assertEqual(run._run._stop_intent_ns, 100)
        self.assertEqual(run._run._close_intent_ns, 200)
        self.assertEqual(f.calls, [])

    def test_export_observation_expires_without_rewriting_frozen_capture(self):
        entered, release = threading.Event(), threading.Event()
        original = diagnostic._fsync
        def hold(fd):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("finite export barrier")
            original(fd)
        with patch.object(diagnostic, "_fsync", side_effect=hold):
            started = time.monotonic()
            run, f, result = self.capture("blocked-export")
            elapsed = time.monotonic() - started
            self.assertTrue(entered.is_set())
            self.assertEqual(result.outcome, "export-unresolved")
            self.assertFalse(result.export.owner_settled)
            self.assertFalse(result.evidence.prospective_eligible)
            self.assertLess(elapsed, 3)
            before = result.report.final
            release.set()
            self.assertTrue(run.join(3))
        self.assertEqual(result.report.final, before)
        self.assertIsNone(result.proof)
        self.assertIsNotNone(run._run.owned_export_proof())
        self.assertEqual(result.outcome, "export-unresolved")

    def test_export_original_deadline_rechecked_after_each_delayed_observation(self):
        # Actual owner writes/fsyncs/reads back; only caller observation time is
        # injected. Late facts stay true while this invocation stays unresolved.
        for seam in ("write_report", "join", "owned_export_proof", "snapshot", "evaluate_evidence"):
            for milliseconds in (999, 1000, 1001):
                with self.subTest(seam=seam, milliseconds=milliseconds):
                    now, seen = [0], []
                    owner = invocation if seam == "evaluate_evidence" else diagnostic.DiagnosticRun
                    original = getattr(owner, seam)
                    def delayed(*args, **kwargs):
                        value = original(*args, **kwargs)
                        if seam == "write_report":
                            self.assertTrue(args[0].join(3))
                            complete = True
                        elif seam == "snapshot":
                            complete = value.export.owner_settled
                        elif seam == "join":
                            complete = value is True and args[0].snapshot().export.owner_settled
                        else:
                            complete = value is not None
                        if complete:
                            now[0] = milliseconds * 1000000
                            seen.append(milliseconds)
                        return value
                    with patch.object(invocation, "time", SimpleNamespace(monotonic_ns=lambda: now[0])), \
                            patch.object(owner, seam, delayed):
                        run, f, result = self.capture("deadline-observed-" + seam + str(milliseconds))
                    self.assertTrue(seen)
                    self.assertTrue(result.export.fsync_returned)
                    self.assertTrue(result.export.readback_verified)
                    self.assertTrue(result.export.owner_settled)
                    self.assertTrue(result.export.complete_receipt)
                    self.assertIsNotNone(result.proof)
                    # Once the owner/proof/status were observed on time,
                    # subsequent pure validation cost does not redate them.
                    eligible = milliseconds < 1000 or seam == "evaluate_evidence"
                    self.assertEqual(result.evidence.prospective_eligible, eligible)
                    self.assertEqual(result.outcome, "quiescence-qualified-incomplete" if eligible else "export-unresolved")
                    if not eligible:
                        before = (result.outcome, result.evidence, result.export, result.proof, result.report.final)
                        self.assertTrue(run.join(0))
                        self.assertEqual((result.outcome, result.evidence, result.export, result.proof, result.report.final), before)
                        self.assertEqual(result.evidence.reason, "export observation deadline expired")

    def test_exclusive_report_output_preserves_original_bytes_and_first_error(self):
        selected = selection(self.root, "existing-output")
        Path(selected.report_path).write_bytes(b"preserve original")
        f = factory()
        run = RehearsalInvocation(selected, fake_backend_factory=f, fixture_name="existing fake", event_config=CONFIG)
        self.runs.append(run)
        run.request_stop()
        result = run.collect()
        self.assertEqual(Path(selected.report_path).read_bytes(), b"preserve original")
        self.assertEqual(result.export.error, "FileExistsError")
        self.assertEqual(result.outcome, "export-failed")
        self.assertFalse(result.evidence.prospective_eligible)

    def test_physical_schema_and_carrier_are_distinct_fabricated_read_only_data(self):
        carrier = dict(format=provenance.PHYSICAL_DIAGNOSTIC_FORMAT, mode="physical-declaration", origin="device",
            selected_declaration=asdict(provenance.STR1_DIAGNOSTIC_DECLARATION),
            expected_negotiation=dict(build_id=0x53545231, caps=list(provenance.STR1_DIAGNOSTIC_CAPS)),
            observed_negotiation=None)
        self.assertIs(provenance.validate_physical_carrier(carrier), provenance.STR1_DIAGNOSTIC_DECLARATION)
        known = deepcopy(carrier); known["observed_negotiation"] = deepcopy(known["expected_negotiation"])
        provenance.validate_physical_carrier(known)
        for key, value in (("format", diagnostic.FORMAT), ("origin", "synthetic-fake"),
                           ("mode", "fake"), ("observed_negotiation", True)):
            bad = deepcopy(carrier); bad[key] = value
            with self.assertRaises(ValueError):
                provenance.validate_physical_carrier(bad)
        for path, value in leaves(carrier):
            bad = deepcopy(carrier); del parent(bad, path)[path[-1]]
            with self.subTest(missing=path), self.assertRaises(ValueError):
                provenance.validate_physical_carrier(bad)
        identity = StreamIdentity("fabricated-schema-only", 1, 1, 50000, "device",
                         *provenance.physical_identity_metadata(provenance.STR1_DIAGNOSTIC_DECLARATION))
        self.assertIs(provenance.profile_for_identity(identity), provenance.STR1_DIAGNOSTIC_DECLARATION)
        self.assertIn("Физическая ДЕКЛАРАЦИЯ", provenance.provenance_text(identity))
        from motortester.stream_model import StreamStart, StreamTerminal, FailureEvidence
        from motortester.stream_recording import StreamWriter
        fabricated_path = self.root / "fabricated-physical-carrier-schema.mtstream"
        writer = StreamWriter.create(fabricated_path, StreamStart(identity, 0, "fabricated schema only"))
        writer.finish(StreamTerminal(identity, 0, "source_failure", True, None, 0, None,
            "fabricated schema only", FailureEvidence("source_error", "fabricated reason0", source_code=0), True, 1))
        script = "from motortester.stream_recording import recover; from motortester.stream_provenance import provenance_text; import sys; r=recover(sys.argv[1]); print(provenance_text(r.identity)); print(r.terminal.device_generated_end_sample_index)"
        command = [sys.executable] + (["-O"] if sys.flags.optimize else [])
        reopened = subprocess.run(command + ["-c", script, str(fabricated_path)], capture_output=True, text=True, timeout=5)
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        self.assertIn("Физическая ДЕКЛАРАЦИЯ", reopened.stdout)
        self.assertTrue(reopened.stdout.endswith("None\n"))
        for name, value in (("origin", "synthetic"), ("application_version", "physical:unknown"),
                            ("firmware_version", "0" * 64), ("config_id", True), ("nominal_fs_hz", True)):
            with self.subTest(field=name), self.assertRaises(ValueError):
                provenance.profile_for_identity(replace(identity, **{name: value}))
        # Fresh-process validation: only a fabricated identity declaration, no
        # writer, serial object or physical report is created.
        code = "from motortester.stream_model import StreamIdentity; from motortester.stream_provenance import profile_for_identity; import json,sys; i=StreamIdentity(**json.loads(sys.argv[1])); print(profile_for_identity(i).profile_id)"
        args = [sys.executable] + (["-O"] if sys.flags.optimize else [])
        proc = subprocess.run(args + ["-c", code, json.dumps(asdict(identity))], capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "str1")

    def test_before_equality_after_observation_deadlines_and_record_return_origin(self):
        # A finite fake controller isolates supervisor clocks. Genuine encoded
        # flows above separately cover accepted endpoint/session behavior.
        from motortester.stream_controller import ControllerStatus
        for boundary, before, equal, after in (("connect", 1999, 2000, 2001),
                ("run", 1999, 2000, 2001), ("drain", 4499, 4500, 4501), ("close", 999, 1000, 1001)):
            for value in (before, equal, after):
                with self.subTest(boundary=boundary, milliseconds=value):
                    now, calls, state = [0], [], {"phase": "idle", "observations": 0, "joined": False}
                    selected = selection(self.root, "deadline-" + boundary + str(value))
                    run = RehearsalInvocation(selected, fake_backend_factory=factory(),
                        fixture_name="fabricated finite controller clock fixture", event_config=CONFIG,
                        clock_ns=lambda: now[0])
                    class FakeController:
                        def __init__(self, *args, **kwargs): pass
                        def connect(self):
                            state["phase"] = "connected"; return True
                        def record(self, path):
                            calls.append("record")
                            now[0] += 17_000_000  # Public return after admission work.
                            state["record_return"] = now[0]
                            state["phase"] = "recording"; return True
                        def snapshot(self):
                            if state["phase"] == "connected":
                                now[0] = (value if boundary == "connect" else 1) * 1000000
                            elif state["phase"] == "recording":
                                state["observations"] += 1
                                offset = value if boundary == "run" and state["observations"] == 1 else 2000
                                now[0] = state["record_return"] + offset * 1000000
                            return ControllerStatus(phase=state["phase"])
                        def stop(self):
                            calls.append("stop"); state["phase"] = "stopped before START"
                            now[0] += (value if boundary == "drain" else 1) * 1000000
                            return True
                        def close(self):
                            calls.append("close"); state["phase"] = "closed"
                        def join(self, timeout=0):
                            if not state["joined"]:
                                now[0] += (value if boundary == "close" else 1) * 1000000
                                state["joined"] = True
                            return True
                    with patch.object(diagnostic, "StreamController", FakeController):
                        report = run._run.collect(selected.recording_path)
                    final = json.loads(report.final)
                    if boundary == "connect" and value >= equal:
                        self.assertNotIn("record", calls)
                        self.assertIsNone(final["record_intent_return_ns"])
                        self.assertEqual(final["run_error"], "connect_not_observed")
                    else:
                        self.assertEqual(calls.count("record"), 1)
                        self.assertEqual(final["record_intent_return_ns"], state["record_return"])
                        expected = max(2000, value) if boundary == "run" else 2000
                        self.assertEqual(final["stop_intent_ns"] - final["record_intent_return_ns"], expected * 1000000)
                        if boundary in ("drain", "close") and value >= equal:
                            self.assertEqual(final["run_error"], "drain_unresolved" if boundary == "drain" else "cleanup_unresolved")
                        else:
                            self.assertIsNone(final["run_error"])
                    self.assertEqual(calls.count("stop"), 1)
                    self.assertEqual(calls.count("close"), 1)

    def test_natural_fixed_two_second_fake_run_is_eligible_without_retry(self):
        def periodic(backend):
            original, last = backend.peer.read, [0.0]
            def read(maximum):
                result = original(maximum)
                now = time.monotonic()
                if (backend.peer.generation and backend.peer.start_waiting is None and
                        not backend.peer.stop_request and now - last[0] >= .05 and len(backend.peer.raw) < 128 * 256):
                    backend.peer.data(); last[0] = now
                return result
            backend.peer.read = read
        run, f, result = self.capture("natural-budget", configure=periodic, natural=True)
        final = json.loads(result.report.final)
        self.assertGreaterEqual(final["stop_intent_ns"] - final["record_intent_return_ns"], 2_000_000_000)
        self.assertTrue(result.evidence.prospective_eligible, result.evidence)
        self.assertEqual(sum(call[0] == "factory" for call in f.calls), 1)
        self.assertEqual(sum(command[0] == 8 for command in f.backends[0].peer.commands), 1)

    def test_selected_output_mismatch_refuses_before_collect_or_export(self):
        f = factory()
        run = RehearsalInvocation(selection(self.root, "path-binding"), fake_backend_factory=f,
            fixture_name="path binding fake", event_config=CONFIG)
        with self.assertRaises(ValueError):
            run._run.collect(str(self.root / "other.mtstream"))
        self.assertEqual(f.calls, [])
        run.request_stop()
        run._run.collect(run.selection.recording_path)
        with self.assertRaises(ValueError):
            run._run.write_report(str(self.root / "other.jsonl"))
        self.assertFalse(Path(self.root / "other.jsonl").exists())
        self.assertTrue(run.join(1))

    def test_fresh_import_starts_no_worker_or_real_serial_module(self):
        script = "import threading,sys; threading.Thread.start=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('worker forbidden')); import motortester.stream_diagnostic_invocation; print('serial' in sys.modules); print('PySide6' in sys.modules)"
        command = [sys.executable] + (["-O"] if sys.flags.optimize else [])
        proc = subprocess.run(command + ["-c", script], capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "False\nFalse\n")

    def test_real_factory_fallback_and_generic_rehearsal_mixing_are_rejected(self):
        selected = selection(self.root, "mixing")
        with self.assertRaises(TypeError):
            RehearsalInvocation(selected, fixture_name="missing", event_config=CONFIG)
        for value in (None, True, "serial"):
            with self.assertRaises(ValueError):
                RehearsalInvocation(selected, fake_backend_factory=value, fixture_name="bad", event_config=CONFIG)
        with self.assertRaises(ValueError):
            diagnostic.DiagnosticRun(invocation.FAKE_SETTINGS, fake_backend_factory=factory(),
                fake_origin="synthetic-fake", fixture_name="generic", event_config=CONFIG,
                limits=invocation.INVOCATION_LIMITS, invocation_selection=selected)


class OperationalSeamTests(unittest.TestCase):
    """Only inspected injected bytes; the real factory is always a tripwire."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="str1-operational-fake-")
        self.root = Path(self.temp.name)
        self.runs = []
        self.guard = patch("motortester.transport._serial_factory",
                           side_effect=AssertionError("Real serial forbidden"))
        self.real_factory = self.guard.start()

    def tearDown(self):
        for run, fake in self.runs:
            for backend in fake.backends:
                backend.close_fail = False
            run.close()
            deadline = time.monotonic() + 3
            while not run.join(.01) and time.monotonic() < deadline:
                run.retry_close()  # Test cleanup only; never another acquisition.
            self.assertTrue(run.join(0), "retained fake owner did not settle")
        self.real_factory.assert_not_called()
        self.guard.stop()
        self.temp.cleanup()

    def make(self, *, configure=None, reason=0, data=True):
        from test_stream_serial import FakeBackend
        class ObservedDtrBackend(FakeBackend):
            @property
            def dtr(self):
                return getattr(self, "requested_dtr", None)
            @dtr.setter
            def dtr(self, value):
                self._call("dtr", (value, self.is_open, self.port))
                if getattr(self, "dtr_fail", False):
                    raise OSError("fabricated DTR setup failure")
                self.requested_dtr = value
        def setup(backend):
            backend.__class__ = ObservedDtrBackend
            if configure:
                configure(backend)
        fake = factory(reason=reason, data=data, configure=setup)
        run = diagnostic._PhysicalDiagnosticRun(invocation.SerialSettings("COM7", 230400, 0, .025),
            context=provenance.PhysicalDiagnosticContext("injected-test"),
            backend_factory=fake, fixture_name="fabricated operational source fixture", event_config=CONFIG)
        self.runs.append((run, fake))
        return run, fake

    def capture(self, run, name="sample", expected=128):
        results, errors = [], []
        def collect():
            try:
                results.append(run.collect() if isinstance(run, invocation.PhysicalInvocation) else
                               run.collect(str(self.root / (name + ".mtstream"))))
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=collect)
        thread.start()
        try:
            def ready():
                if errors or not thread.is_alive():
                    raise AssertionError((errors, results))
                status = run.snapshot().controller
                return (status is not None and status.phase == "recording" and
                        status.bridge.session.source_started and
                        status.bridge.session.accepted_end_sample_index == expected)
            wait_for(ready, timeout=4)
        finally:
            run.request_stop()
            thread.join(7)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        return results[0]

    def export(self, run, name="trace"):
        path = self.root / (name + ".jsonl")
        run.write_report(str(path))
        self.assertTrue(run.join(3))
        checked = diagnostic.read_report(path)
        self.assertTrue(checked.complete, checked.error)
        return checked

    def test_inert_real_and_explicit_test_construction_and_controller_guards(self):
        from motortester.stream_controller import StreamController
        from motortester.stream_serial import SerialStreamPeer
        settings = invocation.SerialSettings("COM7", 230400, 0, .025)
        context = provenance.PhysicalDiagnosticContext("physical")
        with patch("threading.Thread") as workers, patch.object(diagnostic, "_open_file") as files:
            real = diagnostic._PhysicalDiagnosticRun(settings, context=context, event_config=CONFIG)
            self.assertIsNone(real.snapshot().controller)
            with self.assertRaises(ValueError):
                diagnostic._PhysicalDiagnosticRun(settings, context=context,
                    backend_factory=factory(), event_config=CONFIG)
            with self.assertRaises(ValueError):
                diagnostic._PhysicalDiagnosticRun(settings,
                    context=provenance.PhysicalDiagnosticContext("injected-test"), event_config=CONFIG)
            for peer, options in ((SerialStreamPeer(settings, fake_backend_factory=factory()), {}),
                    (SerialStreamPeer(settings), {}),
                    (SerialStreamPeer(settings, backend_factory=factory()),
                     {"fake_diagnostic_declaration": provenance.STR1_DIAGNOSTIC_DECLARATION})):
                with self.assertRaises(ValueError):
                    StreamController(peer, event_config=CONFIG, managed_peer=True,
                        physical_diagnostic_context=context, **options)
            workers.assert_not_called(); files.assert_not_called()

    def test_full_nonfake_seam_dtr_owned_export_and_synthetic_reopen(self):
        from motortester.stream_recording import recover_attempt
        run, fake = self.make()
        report = self.capture(run)
        checked = self.export(run)
        self.assertIs(diagnostic.validate_owned_export(report, run.owned_export_proof()), run.owned_export_proof())
        self.assertFalse(run._controller._peer.fake_backend_selected)
        metadata, final = json.loads(report.metadata), json.loads(report.final)
        self.assertEqual(metadata["format"], diagnostic.FORMAT)
        self.assertEqual(metadata["origin"], "synthetic-fake")
        self.assertEqual(metadata["physical_context"]["execution_kind"], "injected-test")
        self.assertFalse(metadata["external_authority_verified"])
        self.assertIsNone(metadata["usb_identity"])
        self.assertIsNone(metadata["dtr_electrical_observation"])
        identity = final["bridge"]["identity"]
        self.assertEqual(identity["origin"], "synthetic")
        self.assertTrue(identity["application_version"].startswith("rehearsal:"))
        self.assertFalse(final["bridge"]["session"]["clean_completion_reported"])
        self.assertIsNone(final["bridge"]["source_terminal"]["terminal"]["produced_end"])
        reader = recover_attempt(self.root / "sample.mtstream")
        self.assertEqual(reader.preparation.identity.origin, "synthetic")
        kinds = [call[0] for call in fake.calls]
        self.assertEqual(kinds.count("factory"), 1)
        self.assertLess(kinds.index("dtr"), kinds.index("open"))
        dtr = next(call for call in fake.calls if call[0] == "dtr")
        self.assertEqual(dtr[2], (True, False, "COM7"))
        self.assertEqual(len({call[1] for call in fake.calls}), 1)
        self.assertIn("dtr", [row.kind for row in report.callbacks])
        header = json.loads(checked.records[0])["payload"]
        self.assertEqual(set(header["module_sha256"]), set(diagnostic.PHYSICAL_MODULES))

    def test_dtr_setup_failure_is_recorded_and_closed_without_open(self):
        run, fake = self.make(configure=lambda backend: setattr(backend, "dtr_fail", True))
        report = run.collect(str(self.root / "never-started.mtstream"))
        checked = self.export(run)
        self.assertTrue(checked.complete)
        self.assertNotIn("open", [call[0] for call in fake.calls])
        self.assertIn("close", [call[0] for call in fake.calls])
        self.assertFalse(fake.backends[0].is_open)
        dtr = next(row for row in report.callbacks if row.kind == "dtr")
        self.assertEqual((dtr.outcome, dtr.error_type), ("exception", "OSError"))
        final = json.loads(report.final)
        self.assertIsNotNone(final["serial"]["first_error"])
        self.assertFalse(run.snapshot().export.complete_receipt)
        self.assertFalse((self.root / "never-started.mtstream").exists())

    def test_already_open_factory_object_is_closed_before_dtr_or_open(self):
        run, fake = self.make(configure=lambda backend: setattr(backend, "is_open", True))
        report = run.collect(str(self.root / "already-open.mtstream"))
        self.export(run)
        kinds = [call[0] for call in fake.calls]
        self.assertNotIn("dtr", kinds); self.assertNotIn("open", kinds)
        self.assertIn("close", kinds)
        self.assertIsNotNone(json.loads(report.final)["serial"]["first_error"])

    def test_dtr_and_cleanup_failure_preserve_both_and_retained_owner(self):
        def configure(backend):
            backend.dtr_fail = backend.close_fail = True
        run, fake = self.make(configure=configure)
        report = run.collect(str(self.root / "failed-close.mtstream"))
        final = json.loads(report.final)
        self.assertIsNotNone(final["serial"]["first_error"])
        self.assertIsNotNone(final["serial"]["close_error"])
        self.assertFalse(final["serial"]["handle_settled"])
        self.assertTrue(final["serial_unresolved"])
        self.assertEqual({row.kind for row in report.callbacks if row.outcome == "exception"}, {"dtr", "close"})
        self.assertNotIn("open", [call[0] for call in fake.calls])
        self.assertIsNotNone(run._controller)

    def test_strict_physical_test_header_and_carrier_pairing(self):
        run, _ = self.make()
        report = self.capture(run)
        self.export(run)
        header, final = json.loads(report.metadata), json.loads(report.final)
        context = diagnostic._report_context(header)
        diagnostic._summary_context(final, context)
        for field, value in (("format", diagnostic.PHYSICAL_FORMAT), ("origin", "device"),
                             ("dtr_request", True), ("external_authority_verified", True)):
            mutated = deepcopy(header); mutated[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                diagnostic._report_context(mutated)
        fabricated = deepcopy(header)
        fabricated["format"], fabricated["origin"] = diagnostic.PHYSICAL_FORMAT, "device"
        fabricated["physical_context"]["execution_kind"] = "physical"
        selected, deployed, confirmed = operational_inputs(self.root, "fabricated-schema")
        fabricated["physical_selection"] = {"deployment": invocation.deployment_payload(deployed),
                                            "confirmation": asdict(confirmed)}
        fabricated["base_commit"], fabricated["base_tree"] = selected.host.commit, selected.host.tree
        fabricated["module_sha256"] = dict(selected.host.module_sha256)
        physical = diagnostic._report_context(fabricated)
        with self.assertRaises(ValueError):
            diagnostic._summary_context(final, physical)
        fabricated.pop("physical_context")
        with self.assertRaises(ValueError):
            diagnostic._report_context(fabricated)

    def make_invocation(self, name="sample", *, reason=0, configure=None, data=True):
        selected, deployed, confirmed = operational_inputs(self.root, name)
        # Reuse the inspected DTR/encoded-byte fake, with one new shared owner.
        core, fake = self.make(reason=reason, configure=configure, data=data)
        self.runs.remove((core, fake))
        run = invocation.PhysicalInvocation.for_rehearsal(selected, deployment=deployed,
            confirmation=confirmed, event_config=CONFIG, fake_backend_factory=fake,
            fixture_name="fabricated complete operational API fixture")
        self.runs.append((run._run, fake))
        return run, fake, deployed, confirmed

    def test_complete_operational_api_is_synthetic_and_device_evaluator_rejects_it(self):
        run, fake, deployed, confirmed = self.make_invocation()
        result = self.capture(run)
        self.assertTrue(result.evidence.prospective_eligible, result.evidence)
        self.assertEqual(result.evidence.scope, "physical-path-synthetic-rehearsal")
        self.assertFalse(result.evidence.physical_admitted)
        self.assertFalse(result.evidence.clean_completion)
        self.assertTrue(result.export.complete_receipt)
        self.assertTrue(diagnostic.read_report(run.selection.report_path).complete)
        self.assertFalse(invocation.evaluate_physical_evidence(result.report, result.proof,
            run.selection, deployed, confirmed).prospective_eligible)
        with self.assertRaises(RuntimeError):
            run.collect()
        self.assertEqual(sum(call[0] == "factory" for call in fake.calls), 1)

    def test_operational_reason2_complete_export_is_still_ineligible(self):
        run, _, _, _ = self.make_invocation(reason=2)
        result = self.capture(run)
        self.assertTrue(result.export.complete_receipt)
        self.assertFalse(result.evidence.prospective_eligible)

    def test_physical_inputs_current_stage_missing_mismatched_and_mutated_are_inert(self):
        selected, deployed, confirmed = operational_inputs(self.root)
        cases = [(selected, None, confirmed), (selected, deployed, None),
                 (selected, replace(deployed, disposition=invocation.STAGE), confirmed),
                 (selected, replace(deployed, disposition=True), confirmed),
                 (selected, replace(deployed, selection_sha256="0" * 64), confirmed),
                 (selected, deployed, replace(confirmed, invocation_id="2" * 32)),
                 (selected, deployed, replace(confirmed, selection_sha256="0" * 64)),
                 (selected, deployed, replace(confirmed, action="approve")),
                 (selected, replace(deployed, owner_authorization=None), confirmed)]
        for name, value in (("package_sha256", None), ("exe_sha256", True),
                            ("readback", None), ("version", "unknown")):
            cases.append((replace(selected, **{name: value}), deployed, confirmed))
        cases.extend((replace(selected, association=replace(selected.association, **{name: value})), deployed, confirmed)
            for name, value in (("port", "COM8"), ("usb_serial", "OTHER"), ("require_dtr", True)))
        with patch("threading.Thread") as workers, patch.object(diagnostic, "_open_file") as files:
            for chosen, deployment, confirmation in cases:
                with self.subTest(chosen=chosen.version), self.assertRaises(ValueError):
                    invocation.PhysicalInvocation(chosen, deployment=deployment, confirmation=confirmation,
                                                   event_config=CONFIG)
            workers.assert_not_called(); files.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_source_mismatch_or_existing_output_refuses_before_workers_and_factory(self):
        for existing in (False, True):
            run, fake, _, _ = self.make_invocation("preflight" + str(existing))
            if existing:
                Path(run.selection.report_path).write_text("preserve existing", encoding="ascii")
            with patch("threading.Thread") as workers:
                if existing:
                    with self.assertRaises(ValueError):
                        run.collect()
                else:
                    with patch.object(invocation, "observed_source_hashes", return_value=()):
                        with self.assertRaises(ValueError):
                            run.collect()
                workers.assert_not_called()
            self.assertEqual(fake.calls, [])
            self.assertFalse(Path(run.selection.recording_path).exists())
            if existing:
                self.assertEqual(Path(run.selection.report_path).read_text(), "preserve existing")

    def test_every_deployment_json_field_missing_or_wrong_type_fails_closed(self):
        selected, deployed, confirmed = operational_inputs(self.root)
        payload = invocation.deployment_payload(deployed)
        restored = invocation.parse_deployment(payload)
        self.assertEqual(restored, deployed)
        invocation.validate_deployment(selected, restored, confirmed)
        for path, value in leaves(payload):
            for replacement in ("delete", None, True, 1.0):
                mutated = deepcopy(payload)
                if replacement == "delete":
                    del parent(mutated, path)[path[-1]]
                else:
                    parent(mutated, path)[path[-1]] = replacement
                with self.subTest(path=path, replacement=replacement), self.assertRaises(ValueError):
                    parsed = invocation.parse_deployment(mutated)
                    invocation.validate_deployment(selected, parsed, confirmed)
        mutated = deepcopy(payload); mutated["approval"] = True
        with self.assertRaises(ValueError):
            invocation.parse_deployment(mutated)

    def test_every_encoded_build_caps_mismatch_precedes_start_writer_generation_and_R(self):
        from test_stream_diagnostic import STR1_BUILD, STR1_CAPS
        mismatches = [("build", {"build": STR1_BUILD + 1})]
        for index in range(8):
            caps = list(STR1_CAPS); caps[index] += 1
            mismatches.append(("caps" + str(index), {"caps": tuple(caps)}))
        for name, change in mismatches:
            fake = str1_factory(**change)
            run = diagnostic._PhysicalDiagnosticRun(invocation.SerialSettings("COM7", 230400, 0, .025),
                context=provenance.PhysicalDiagnosticContext("injected-test"), backend_factory=fake,
                fixture_name="fabricated mismatched negotiation", event_config=CONFIG)
            self.runs.append((run, fake))
            path = self.root / (name + ".mtstream")
            with self.subTest(name=name), patch("motortester.stream_endpoint.Endpoint.plan_start") as plan, \
                 patch("motortester.stream_bridge.StreamSession") as session, \
                 patch("motortester.stream_bridge.StorageExecutor") as storage:
                report = run.collect(str(path))
                plan.assert_not_called(); session.assert_not_called(); storage.assert_not_called()
            final = json.loads(report.final)
            self.assertIsNone(final["bridge"]["identity"])
            self.assertIsNone(final["bridge"]["session"])
            self.assertEqual(fake.backends[0].peer.generation, 0)
            self.assertNotIn(8, [command[0] for command in fake.backends[0].peer.commands])
            self.assertFalse(path.exists())
            self.assertEqual(final["bridge"]["accepted_build_id"], change.get("build", STR1_BUILD))

    @staticmethod
    def periodic(backend):
        original, last = backend.peer.read, [0.0]
        def read(maximum):
            result = original(maximum)
            now = time.monotonic()
            if (backend.peer.generation and backend.peer.start_waiting is None and
                    not backend.peer.stop_request and now - last[0] >= .05 and len(backend.peer.raw) < 128 * 256):
                backend.peer.data(); last[0] = now
            return result
        backend.peer.read = read

    def test_natural_operational_two_second_policy_uses_one_attempt(self):
        run, fake, _, _ = self.make_invocation(configure=self.periodic)
        result = run.collect()
        final = json.loads(result.report.final)
        self.assertTrue(result.evidence.prospective_eligible, result.evidence)
        self.assertGreaterEqual(final["stop_intent_ns"] - final["record_intent_return_ns"], 2_000_000_000)
        self.assertEqual(sum(call[0] == "factory" for call in fake.calls), 1)
        self.assertEqual(sum(command[0] == 8 for command in fake.backends[0].peer.commands), 1)

    def test_started_empty_is_distinct_from_no_R(self):
        empty, _, _, _ = self.make_invocation("empty", data=False)
        result = self.capture(empty, expected=0)
        final = json.loads(result.report.final)
        self.assertTrue(final["bridge"]["session"]["source_started"])
        self.assertTrue(result.evidence.prospective_eligible, result.evidence)
        no_R, _, _, _ = self.make_invocation("no-R", data=False,
            configure=lambda backend: setattr(backend.peer, "auto_start", False))
        result = no_R.collect()
        self.assertFalse(result.evidence.prospective_eligible)
        self.assertFalse(json.loads(result.report.final)["bridge"]["session"]["source_started"])

    def test_known_partial_writes_preserve_prefixes_and_unknown_counts_are_ineligible(self):
        partial, _, _, _ = self.make_invocation("partial",
            configure=lambda backend: setattr(backend.peer, "accepted_prefix", 7))
        result = self.capture(partial)
        self.assertTrue(result.evidence.prospective_eligible, result.evidence)
        self.assertTrue(any(row.kind == "write" and 0 < row.accepted_count < len(row.offered)
                            for row in result.report.callbacks))
        unknown, _, _, _ = self.make_invocation("unknown",
            configure=lambda backend: setattr(backend.peer, "unknown_write", True))
        result = unknown.collect()
        self.assertFalse(result.evidence.prospective_eligible)
        self.assertTrue(any(row.outcome == "unknown" for row in result.report.callbacks))

    def test_operator_interrupt_preserves_first_cause_stop_close_and_available_export(self):
        run, fake, _, _ = self.make_invocation()
        original, interrupted = run._run._observe, [False]
        def observe():
            status = original()
            if (not interrupted[0] and status.phase == "recording" and
                    status.bridge.session.accepted_end_sample_index == 128):
                interrupted[0] = True
                raise KeyboardInterrupt()
            return status
        with patch.object(run._run, "_observe", side_effect=observe):
            result = run.collect()
        self.assertTrue(interrupted[0])
        final = json.loads(result.report.final)
        self.assertEqual(final["run_error"], "collector_KeyboardInterrupt")
        self.assertIsNotNone(final["stop_intent_ns"])
        self.assertIsNotNone(final["close_intent_ns"])
        self.assertTrue(final["controller_joined"])
        self.assertTrue(result.export.fsync_returned)
        self.assertTrue(result.export.readback_verified)
        self.assertFalse(result.evidence.prospective_eligible)
        self.assertEqual(sum(call[0] == "factory" for call in fake.calls), 1)

    def test_late_operational_export_receipt_never_rewrites_expired_result(self):
        run, _, _, _ = self.make_invocation()
        entered, release = threading.Event(), threading.Event()
        original = diagnostic._fsync
        def fsync(fd):
            entered.set()
            if not release.wait(3):
                raise OSError("finite fabricated fsync gate")
            return original(fd)
        try:
            with patch.object(diagnostic, "_fsync", side_effect=fsync):
                result = self.capture(run)
                self.assertTrue(entered.is_set())
                self.assertEqual(result.outcome, "export-unresolved")
                self.assertFalse(result.evidence.prospective_eligible)
                self.assertFalse(result.export.owner_settled)
                release.set()
                self.assertTrue(run.join(3))
        finally:
            release.set()
        self.assertTrue(run.snapshot().export.fsync_returned)
        self.assertTrue(run.snapshot().export.readback_verified)
        self.assertTrue(run.snapshot().export.owner_settled)
        self.assertFalse(result.evidence.prospective_eligible)
        self.assertFalse(result.export.owner_settled)

    def test_fabricated_physical_trace_reopens_but_cannot_borrow_test_owned_proof(self):
        run, _, deployed, confirmed = self.make_invocation()
        result = self.capture(run)
        original = result.proof._exported
        header = json.loads(original.metadata)
        header.update(format=diagnostic.PHYSICAL_FORMAT, origin="device", rehearsal_notice=None,
                      fixture_name="EXPLICITLY FABRICATED PHYSICAL SCHEMA ONLY")
        header["physical_context"]["execution_kind"] = "physical"
        def fabricate(value):
            if type(value) is dict:
                for key, child in value.items():
                    if key == "identity" and type(child) is dict:
                        child["origin"] = "device"
                        for name, item in zip(("protocol_revision", "firmware_version", "application_version"),
                                provenance.physical_identity_metadata(provenance.STR1_DIAGNOSTIC_DECLARATION)):
                            child[name] = item
                    fabricate(child)
            elif type(value) is list:
                for child in value:
                    fabricate(child)
            return value
        fabricated = replace(original, _metadata=diagnostic._freeze_tree(header),
            _final=diagnostic._freeze_tree(fabricate(json.loads(original.final))),
            _snapshots=tuple(diagnostic._freeze_tree(fabricate(json.loads(item))) for item in original.snapshots))
        path = self.root / "EXPLICITLY-FABRICATED-PHYSICAL-SCHEMA.jsonl"
        with open(path, "xb") as output:
            diagnostic._write_report(output, fabricated, diagnostic.MAX_FILE)
        self.assertTrue(diagnostic.read_report(path).complete)
        command = [sys.executable] + (["-O"] if sys.flags.optimize else [])
        script = "from motortester.stream_diagnostic import read_report; import sys; print(read_report(sys.argv[1]).complete)"
        checked = subprocess.run(command + ["-c", script, str(path)], capture_output=True, text=True, timeout=5)
        self.assertEqual((checked.returncode, checked.stdout), (0, "True\n"), checked.stderr)
        self.assertFalse(invocation.evaluate_physical_evidence(fabricated, result.proof,
            run.selection, deployed, confirmed).prospective_eligible)
        for key, value in (("origin", "synthetic-fake"), ("format", diagnostic.FORMAT),
                           ("unexpected", "field"), ("limits", {**header["limits"], "run_ms": 1000})):
            changed = deepcopy(header); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                diagnostic._report_context(changed)

    def test_cli_is_inactive_by_default_and_executes_only_the_explicit_fake_seam(self):
        selected, deployed, confirmed = operational_inputs(self.root)
        path = self.root / "fabricated-local-deployment.json"
        path.write_bytes(invocation._canonical(invocation.deployment_payload(deployed)))
        output = io.StringIO()
        with patch("sys.stdout", output), patch("threading.Thread") as workers:
            self.assertEqual(invocation.main([]), 2)
            self.assertEqual(invocation.main(["--deployment", str(path)]), 0)
            self.assertEqual(invocation.main(["--deployment", str(path), "--execute-one-attempt"]), 2)
            workers.assert_not_called()
        self.assertIn("Inactive", output.getvalue())
        for private in (deployed.owner_authorization.reference, selected.association.board_instance,
                        selected.recording_path):
            self.assertNotIn(private, output.getvalue())
        core, fake = self.make(configure=self.periodic)
        self.runs.remove((core, fake))
        with patch("sys.stdout", output):
            code = invocation.main(["--deployment", str(path), "--execute-one-attempt",
                "--operator", confirmed.operator, "--invocation-id", confirmed.invocation_id,
                "--confirm-selection", confirmed.selection_sha256], fake_backend_factory=fake)
        retained = invocation.retained_cli_invocation()
        self.runs.append((retained._run, fake))
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn("physical-path-synthetic-rehearsal", output.getvalue())
        self.assertIn("clean_completion=false", output.getvalue())
        self.assertNotIn(deployed.owner_authorization.reference, output.getvalue())
        self.assertTrue(diagnostic.read_report(selected.report_path).complete)

    def test_fresh_cli_help_and_default_have_no_backend_or_worker(self):
        script = ("import sys,threading; threading.Thread.start=lambda *a,**k: "
                  "(_ for _ in ()).throw(AssertionError('worker forbidden')); "
                  "from motortester import transport; transport._serial_factory=lambda **k: "
                  "(_ for _ in ()).throw(AssertionError('real factory forbidden')); "
                  "from motortester.stream_diagnostic_invocation import main; "
                  "raise SystemExit(main(sys.argv[1:]))")
        command = [sys.executable] + (["-O"] if sys.flags.optimize else [])
        for args, expected in (([], 2), (["--help"], 0)):
            result = subprocess.run(command + ["-c", script] + args,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, expected, result.stderr)

    def test_physical_carrier_kind_cannot_use_implicit_serial_factory(self):
        from motortester.stream_controller import StreamController
        from motortester.stream_serial import SerialStreamPeer
        settings = invocation.SerialSettings("COM7", 230400, 0, .025)
        with patch("threading.Thread") as workers:
            for kind in ("physical", "injected-test"):
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    StreamController(SerialStreamPeer(settings), managed_peer=True, event_config=CONFIG,
                        physical_diagnostic_context=provenance.PhysicalDiagnosticContext(kind))
            workers.assert_not_called()

    def test_physical_target_placeholders_refuse_without_changing_legacy_selection(self):
        selected, deployed, confirmed = operational_inputs(self.root)
        for field in ("board_instance", "windows_pc"):
            for value in ("unknown", "TBD", "TODO", "pending", "placeholder", "none", "UnKnOwN"):
                with self.subTest(field=field, value=value):
                    association = replace(selected.association, **{field: value})
                    legacy = replace(selection(self.root), association=association)
                    self.assertIs(validate_selection(legacy), legacy)
                    chosen = replace(selected, association=association)
                    fake = factory()
                    with patch("threading.Thread") as workers, patch.object(diagnostic, "_open_file") as files:
                        with self.assertRaises(ValueError):
                            invocation.selection_digest(chosen)
                        with self.assertRaises(ValueError):
                            invocation.physical_admission(chosen, deployment=deployed, confirmation=confirmed)
                        with self.assertRaises(ValueError):
                            invocation.PhysicalInvocation.for_rehearsal(chosen, deployment=deployed,
                                confirmation=confirmed, event_config=CONFIG, fake_backend_factory=fake,
                                fixture_name="fabricated placeholder refusal")
                        workers.assert_not_called(); files.assert_not_called()
                    self.assertEqual(fake.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIs(invocation.validate_deployment(selected, deployed, confirmed), deployed)

    def test_public_cleanup_retry_keeps_same_owner_budgets_and_immutable_failure(self):
        run, fake, _, _ = self.make_invocation(configure=lambda backend: setattr(backend, "close_fail", True))
        self.assertFalse(run.retry_close())
        self.assertEqual(fake.calls, [])
        result = self.capture(run)
        frozen = (result.outcome, result.evidence, result.report.final, result.export, result.proof)
        origins = (run._run._connect_intent_ns, run._run._record_intent_return_ns,
                   run._run._stop_intent_ns, run._run._close_intent_ns)
        first_error = run.snapshot().controller.serial.first_error
        self.assertTrue(run.snapshot().controller.can_retry_close)
        self.assertFalse(result.evidence.prospective_eligible)
        for backend in fake.backends:
            backend.close_fail = False
        run.close(); time.sleep(.02)
        self.assertFalse(run.join(0))
        self.assertTrue(run.retry_close())
        self.assertTrue(run.join(3))
        self.assertFalse(run.retry_close())
        self.assertEqual(run.snapshot().controller.serial.first_error, first_error)
        self.assertEqual(origins, (run._run._connect_intent_ns, run._run._record_intent_return_ns,
                                  run._run._stop_intent_ns, run._run._close_intent_ns))
        self.assertEqual(frozen, (result.outcome, result.evidence, result.report.final, result.export, result.proof))
        self.assertEqual(sum(call[0] == "factory" for call in fake.calls), 1)
        self.assertEqual(sum(call[0] == "open" for call in fake.calls), 1)
        self.assertEqual(sum(call[0] == 8 for call in fake.backends[0].peer.commands), 1)
        with self.assertRaises(RuntimeError):
            run.collect()

    def test_retained_cli_handle_exposes_only_explicit_same_owner_cleanup_retry(self):
        selected, deployed, confirmed = operational_inputs(self.root)
        path = self.root / "fabricated-cleanup-deployment.json"
        path.write_bytes(invocation._canonical(invocation.deployment_payload(deployed)))
        def configure(backend):
            backend.dtr_fail = backend.close_fail = True
        core, fake = self.make(configure=configure)
        self.runs.remove((core, fake))
        args = ["--deployment", str(path), "--execute-one-attempt", "--operator", confirmed.operator,
                "--invocation-id", confirmed.invocation_id, "--confirm-selection", confirmed.selection_sha256]
        with patch.object(invocation, "_CLI_OWNER", None), patch("sys.stdout", io.StringIO()):
            self.assertEqual(invocation.main(args, fake_backend_factory=fake), 1)
            retained = invocation.retained_cli_invocation()
            self.runs.append((retained._run, fake))
            frozen = retained._run._report.final
            origin = retained._run._close_intent_ns
            self.assertFalse(retained.join(0))
            self.assertEqual(invocation.main(args, fake_backend_factory=fake), 2)
            self.assertIs(invocation.retained_cli_invocation(), retained)
            for backend in fake.backends:
                backend.close_fail = False
            retained.close(); time.sleep(.02)
            self.assertFalse(retained.join(0))
            self.assertTrue(retained.retry_close())
            self.assertTrue(retained.join(3))
            self.assertFalse(retained.retry_close())
            self.assertEqual(retained._run._report.final, frozen)
            self.assertEqual(retained._run._close_intent_ns, origin)
            self.assertEqual(sum(call[0] == "factory" for call in fake.calls), 1)
            self.assertNotIn("open", [call[0] for call in fake.calls])
            self.assertNotIn(8, [call[0] for call in fake.backends[0].peer.commands])


if __name__ == "__main__":
    unittest.main()

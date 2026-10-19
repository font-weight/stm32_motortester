"""Source-only encoded fake flows and explicitly fabricated policy/schema controls.

All executing backends are mandatory injected FakeFactory instances. Physical
carrier dictionaries below are fabricated schemas, never a physical report.
"""
from copy import deepcopy
from dataclasses import asdict, fields, replace
import hashlib
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


if __name__ == "__main__":
    unittest.main()

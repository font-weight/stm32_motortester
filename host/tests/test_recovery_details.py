"""Bounded recovery context through real runtime, spool, Save and fresh reopen.

Synthetic byte peers and injected host failures only; no board diagnosis.
"""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import UUID
import zipfile

from motortester import session_archive as archive
from motortester.recording import encode
from motortester.recording_controller import IntentKind, RecordingError
from motortester.session import (EventKind, FaultCode, MAX_ID,
    Phase, RecoveryDiagnostic, RecoveryReason, SessionError)
from test_recording_bridge import Harness
from test_recording_controller import Rig
from test_session import Harness as SessionHarness


class RecoveryContextTests(unittest.TestCase):
    def test_timeout_retains_actual_expired_ordinary_with_stop_also_pending(self):
        h = SessionHarness().configured()
        h.c.arm(0)
        ordinary = h.tx()
        h.now = 10
        stop = h.stop()
        self.assertEqual(h.c.tick(999), ())
        event, = h.c.tick(1000)
        detail = event.recovery
        self.assertEqual((event.request_id, detail.command_type, detail.request_id),
                         (ordinary.request_id, 4, ordinary.request_id))
        self.assertEqual((detail.pending_command_type, detail.pending_request_id,
                          detail.pending_stop_id), (4, ordinary.request_id, stop.request_id))
        self.assertEqual((h.c.snapshot().pending_request_id, h.c.snapshot().pending_stop_id), (0, 0))
        self.assertIs(h.c.snapshot().recovery, detail)
        self.assertIsNone(h.c.prepare_tx())
        self.assertEqual(h.c.transport_lost(1001), ())
        self.assertIs(h.c.snapshot().recovery, detail)
        with self.assertRaises(FrozenInstanceError):
            detail.request_id = 9
        h.c.start(101, 1001)
        self.assertIsNone(h.c.snapshot().recovery)

    def test_stop_timeout_after_ordinary_ack_identifies_stop(self):
        h = SessionHarness().configured()
        h.c.arm(0)
        ordinary = h.tx()
        stop = h.stop()
        import struct
        h.receive(132, struct.pack('<II', ordinary.request_id, 1), ordinary.request_id)
        event, = h.c.tick(1000)
        self.assertEqual((event.request_id, event.recovery.command_type), (stop.request_id, 5))
        self.assertEqual(event.recovery.pending_request_id, 0)
        self.assertEqual(event.recovery.pending_stop_id, stop.request_id)

    def test_unknown_write_uses_active_ticket_not_latest_pending_lane(self):
        for stop_ticket in (False, True):
            with self.subTest(stop_ticket=stop_ticket):
                h = SessionHarness().configured()
                h.c.arm(0)
                if stop_ticket:
                    h.tx()
                h.c.stop(0)
                before = h.c.snapshot()
                ticket = h.c.prepare_tx()
                h.c.written(ticket, 1, 0)
                event, = h.c.unknown_write(1)
                self.assertEqual((event.request_id, event.recovery.command_type),
                                 (ticket.frame.request_id, ticket.frame.message_type))
                self.assertEqual(event.recovery.pending_request_id, before.pending_request_id)
                self.assertEqual(event.recovery.pending_stop_id, before.pending_stop_id)

    def test_read_protocol_and_no_ticket_failures_do_not_invent_culprit(self):
        for fail in ('transport', 'protocol', 'unknown_without_ticket'):
            with self.subTest(fail=fail):
                h = SessionHarness().configured()
                h.c.arm(0)
                h.tx()
                h.c.stop(0)
                before = h.c.snapshot()
                if fail == 'transport':
                    event, = h.c.transport_lost(0)
                elif fail == 'protocol':
                    event, = h.c.protocol_fault(FaultCode.VERSION, 0)
                else:
                    event, = h.c.unknown_write(0)
                self.assertEqual((event.request_id, event.recovery.command_type), (0, 0))
                self.assertEqual(event.recovery.pending_request_id, before.pending_request_id)
                self.assertEqual(event.recovery.pending_stop_id, before.pending_stop_id)

    def test_diagnostic_accepts_only_bounded_enums_and_exact_numbers(self):
        detail = RecoveryDiagnostic(RecoveryReason.PROTOCOL_FAULT, FaultCode.DOWNSTREAM,
            2, MAX_ID, 2, MAX_ID, MAX_ID)
        self.assertLess(len(detail.text()), 256)
        for field, value in (('reason', 'COM7 secret'), ('fault', 'private backend'),
                             ('request_id', True), ('request_id', MAX_ID + 1),
                             ('command_type', 6), ('pending_command_type', 'CONFIG'),
                             ('pending_stop_id', -1), ('pending_request_id', 0)):
            with self.subTest(field=field), self.assertRaises(SessionError):
                replace(detail, **{field: value})


class RecoveryPersistenceTests(unittest.TestCase):
    def recover(self, reason, stage='before_begin'):
        h = Harness(self)
        original = None
        if reason == 'ordinary_timeout':
            h.begin()
            h.until(lambda: h.runtime.snapshot().pending_request_id == 3)
        else:
            h.start()
            if stage == 'partial':
                h.link.read_limit = 64
                self.assertTrue(h.link.peer.trigger(h.link.now))
                h.until(lambda: any(e.kind is EventKind.WINDOW_DATA for e in h.events))
            elif stage == 'full':
                original = h.capture()
        if reason in ('ordinary_timeout', 'stop_timeout'):
            if reason == 'stop_timeout':
                h.adapter.stop()
            h.link.hold_read = True
            h.pump()
            h.link.now += 1000
            h.pump()
        elif reason == 'unknown_write':
            h.adapter.stop()
            h.link.write_limit = 1
            h.pump()
            with patch.object(h.link, 'write', side_effect=OSError('COM9 /private/token=SECRET')):
                h.pump()
        elif reason == 'transport_lost':
            with patch.object(h.link, 'read', side_effect=OSError('COM9 /private/token=SECRET')):
                h.pump()
        elif reason == 'protocol_fault':
            with patch.object(h.link, 'read', return_value=b'\x01\x00'):
                h.pump()
        self.assertIs(h.runtime.snapshot().phase, Phase.RECOVERY)
        event = next(e for e in h.events if e.kind is EventKind.RECOVERY)
        diagnostic = event.recovery.text()
        self.assertIn(diagnostic, h.adapter.state.snapshot.recovery_diagnostic)
        h.until(lambda: h.adapter.state.ready_to_save)
        self.assertNotIn(7, [frame.message_type for frame in h.link.tx])
        before_tx = tuple(h.link.tx)
        h.adapter.stop()
        h.adapter.observe(h.runtime.disconnect())
        for _ in range(5):
            h.pump()
        self.assertEqual(tuple(h.link.tx), before_tx)
        self.assertEqual(h.adapter.state.snapshot.recovery_diagnostic, diagnostic)
        report = archive.recover(h.path)
        self.assertIn(diagnostic, report.outcome.diagnostic)
        self.assertEqual((report.outcome.reason, report.outcome.stop_evidence), ('error', 'unknown'))
        self.assertTrue(all(secret not in report.outcome.diagnostic for secret in ('COM9', '/private', 'SECRET')))
        if stage == 'before_begin':
            self.assertEqual(report.entries, ())
        elif stage == 'partial':
            self.assertEqual(len(report.entries), 1)
            self.assertEqual(report.entries[0].kind, 'partial')
            self.assertGreater(report.entries[0].sample_count, 0)
            self.assertLess(report.entries[0].sample_count, 2048)
        else:
            self.assertEqual(report.entries[0].kind, 'capture')
        opened = h.save()
        self.assertEqual(opened.outcome, report.outcome)
        self.assertIsNone(h.adapter.state.error)
        self.assertIsNotNone(h.adapter.state.saved_path)
        if original is not None:
            self.assertEqual(opened.read_segment(0), encode(original))
        fresh = subprocess.check_output([sys.executable, '-c',
            'from motortester.session_archive import load; import json,sys; '
            'a=load(sys.argv[1]); print(json.dumps([a.outcome.diagnostic,a.outcome.reason,a.outcome.stop_evidence]))',
            str(opened.path)], text=True)
        self.assertEqual(json.loads(fresh), [report.outcome.diagnostic, 'error', 'unknown'])
        with zipfile.ZipFile(opened.path) as z:
            self.assertEqual(set(json.loads(z.read('diagnostics.json'))),
                             {'format_version', 'message', 'recovery_issues'})
        return h, diagnostic

    def test_record_recovery_save_fresh_reopen_all_codes_without_begin(self):
        for reason in ('ordinary_timeout', 'stop_timeout', 'unknown_write',
                       'transport_lost', 'protocol_fault'):
            with self.subTest(reason=reason):
                self.recover(reason)

    def test_partial_and_full_sources_survive_every_recovery_family(self):
        for stage in ('partial', 'full'):
            for reason in ('stop_timeout', 'unknown_write', 'transport_lost', 'protocol_fault'):
                with self.subTest(stage=stage, reason=reason):
                    self.recover(reason, stage)

    def test_save_failure_and_success_do_not_replace_acquisition_cause(self):
        h, diagnostic = self.recover('protocol_fault')
        saved = h.adapter.state.saved_path
        destination = Path(h.temp.name) / 'already-exists.mtrec'
        destination.write_bytes(b'previous data')
        self.assertTrue(h.adapter.save(destination))
        h.until(lambda: not h.adapter.state.saving)
        self.assertEqual(h.adapter.state.error, 'save_failed')
        self.assertEqual(h.adapter.state.saved_path, saved)
        self.assertEqual(h.adapter.state.snapshot.recovery_diagnostic, diagnostic)
        self.assertEqual(destination.read_bytes(), b'previous data')
        opened = h.save('explicit-retry.mtrec')
        self.assertIn(diagnostic, opened.outcome.diagnostic)
        self.assertIsNone(h.adapter.state.error)

    def test_prior_fault_is_separate_and_late_context_invalidates_claimed_finish(self):
        for claimed in (False, True):
            with self.subTest(claimed=claimed):
                rig = Rig(self)
                rig.prepare()
                rig.controller.fault('prior_storage_fault', now_ms=0)
                old = rig.intent(IntentKind.FINALIZE_READY)
                if claimed:
                    rig.claim(old)
                rig.runtime.disconnect()
                # No event delivery is required: the exact runtime snapshot is
                # persistent evidence even after an exceptional pump return.
                rig.observe()
                state = rig.controller.snapshot()
                self.assertEqual(state.fault, 'prior_storage_fault')
                self.assertIn('TRANSPORT_LOST', state.recovery_diagnostic)
                if claimed:
                    self.assertTrue(state.finalization_invalidated)
                    self.assertFalse(rig.controller.finalized(old, now_ms=0))
                    self.assertFalse(rig.controller.intents)
                else:
                    fresh = rig.intent(IntentKind.FINALIZE_READY)
                    self.assertIsNot(fresh, old)
                    self.assertIn('prior_storage_fault', fresh.payload.diagnostic)
                    self.assertIn('TRANSPORT_LOST', fresh.payload.diagnostic)
                    self.assertFalse(rig.controller.admit(old, now_ms=0,
                        runtime_state=rig.runtime.snapshot()))

    def test_arbitrary_recovery_object_cannot_supply_backend_text(self):
        rig = Rig(self)
        rig.prepare()
        rig.runtime.disconnect()
        class BackendText:
            def text(self):
                raise AssertionError('An arbitrary formatter must never be called')
        with self.assertRaises(RecordingError):
            rig.controller.observe(rig.token,
                replace(rig.runtime.snapshot(), recovery=BackendText()), now_ms=0)
        self.assertEqual(rig.controller.snapshot().recovery_diagnostic, '')

    def test_fresh_record_and_stale_event_or_callback_do_not_reuse_old_cause(self):
        h, diagnostic = self.recover('stop_timeout')
        old_token = h.adapter.state.snapshot.session
        old_snapshot = h.runtime.snapshot()
        old_event = next(e for e in h.events if e.kind is EventKind.RECOVERY)
        from motortester.device_emulator import DeviceEmulator
        h.link.peer = DeviceEmulator(all_analog_modes=True)
        h.link.hold_read = False
        h.runtime.start(102, reset_confirmed=True)
        h.until(lambda: h.runtime.snapshot().phase is Phase.READY)
        identity = replace(h.identity, recording_id=str(UUID(int=203)), session_id=str(UUID(int=102)))
        self.assertTrue(h.adapter.record(identity, h.settings, Path(h.temp.name) / 'fresh.spool'))
        state = h.adapter.state.snapshot
        self.assertEqual(state.recovery_diagnostic, '')
        self.assertEqual(h.adapter.controller.observe(old_token, old_snapshot, (old_event,), now_ms=h.link.now), ())
        self.assertEqual(h.adapter.controller.snapshot(), state)
        # Even a stale event presented with the new token cannot replace the
        # current runtime's recovery context with a previous session's cause.
        h.adapter.observe((old_event,))
        self.assertEqual(h.adapter.state.snapshot.recovery_diagnostic, '')
        h.until(lambda: h.runtime.snapshot().phase is Phase.ARMED)
        h.finish()
        opened = h.save('fresh.mtrec')
        self.assertEqual(opened.outcome.diagnostic, '')

    def test_legacy_diagnostic_string_remains_readable_without_new_fields(self):
        h = Harness(self)
        spool = archive.SessionSpool.create(Path(h.temp.name) / 'legacy.spool', h.identity)
        report = spool.finish('error', 'unknown', 'transport_recovery')
        dest = Path(h.temp.name) / 'legacy.mtrec'
        archive.finalize(report, dest)
        self.assertEqual(archive.load(dest).outcome.diagnostic, 'transport_recovery')


if __name__ == '__main__':
    unittest.main()

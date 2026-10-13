"""Bounded STATUS observation checks; synthetic bytes are not hardware proof."""
from dataclasses import FrozenInstanceError
import struct
import unittest

from motortester.session import (
    Config, DeviceState, DeviceStatus, EventKind, FaultCode, MAX_ID, Phase,
    RecoveryReason, RESPONSE_TIMEOUT_MS, SessionController, SessionError,
)
from motortester.wire import Frame
from test_session import Harness
from test_session_release import BytePeer


def payload(state=DeviceState.WAITING, *, reason=0, generation=4, window=0,
            observed=0, busy=0, dropped=0, reserved=0):
    return struct.pack("<BBHIIIII", state, reason, reserved, generation,
                       window, observed, busy, dropped)


def status(h, state=DeviceState.WAITING, **kwargs):
    kwargs.setdefault("generation", h.c.snapshot().generation)
    return h.receive(134, payload(state, **kwargs))


class DeviceStatusTests(unittest.TestCase):
    def test_valid_waiting_status_reaches_snapshot_regression(self):
        # Base 41cdc0a emits STATUS but loses its state before publication.
        h = Harness().armed()
        events = status(h)
        self.assertEqual([e.kind for e in events], [EventKind.STATUS])
        self.assertIsNotNone(getattr(h.c.snapshot(), "device_status", None))
        self.assertIs(h.c.snapshot().device_status.state, DeviceState.WAITING)
        self.assertFalse(h.c.snapshot().device_status_stale)
        self.assertIs(h.c.snapshot().phase, Phase.ARMED)

    def test_missing_status_remains_unknown_despite_handshake_and_arm(self):
        for h in (Harness(), Harness().ready(), Harness().configured(), Harness().armed()):
            with self.subTest(phase=h.c.snapshot().phase):
                self.assertIsNone(h.c.snapshot().device_status)
                self.assertFalse(h.c.snapshot().device_status_stale)
        h = Harness().armed()
        h.stopped(h.stop())
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        self.assertIsNone(h.c.snapshot().device_status)

    def test_all_seven_exact_state_codes_are_distinct_observations(self):
        self.assertEqual([int(s) for s in DeviceState], list(range(7)))
        for state in DeviceState:
            with self.subTest(state=state):
                h = Harness().configured() if state is DeviceState.STOPPED else Harness().armed()
                h.now = 17
                events = status(h, state, reason=3 if state is DeviceState.ERROR else 0,
                                observed=12, busy=7, dropped=2)
                report = h.c.snapshot().device_status
                self.assertIs(report.state, state)
                self.assertEqual((report.observed_external_edges, report.busy_external_edges,
                                  report.dropped_windows, report.received_ms), (12, 7, 2, 17))
                self.assertEqual((report.generation, report.window_id), (h.c.snapshot().generation, 0))
                if state is DeviceState.ERROR:
                    self.assertEqual([e.kind for e in events], [EventKind.RECOVERY])
                    self.assertIs(events[0].reason, RecoveryReason.DEVICE_ERROR)
                    self.assertTrue(h.c.snapshot().device_status_stale)
                else:
                    self.assertEqual([e.kind for e in events], [EventKind.STATUS])
                    self.assertFalse(h.c.snapshot().device_status_stale)

    def test_every_error_reason_retained_without_changing_recovery_contract(self):
        for reason in range(1, 7):
            with self.subTest(reason=reason):
                h = Harness().armed()
                events = status(h, DeviceState.ERROR, reason=reason)
                self.assertIs(h.c.snapshot().phase, Phase.RECOVERY)
                self.assertEqual(h.c.snapshot().device_status.reason, reason)
                self.assertTrue(events[0].reset_transport)
                self.assertTrue(events[0].preserve_fragments)
                self.assertTrue(h.c.snapshot().device_status_stale)
                previous = h.c.snapshot().device_status
                self.assertEqual(status(h, DeviceState.STOPPED), ())
                self.assertIs(h.c.snapshot().device_status, previous)

    def test_report_and_snapshot_are_immutable_fixed_size_values(self):
        h = Harness().armed()
        status(h)
        report = h.c.snapshot().device_status
        self.assertIsInstance(report, DeviceStatus)
        for obj, field, value in ((report, "state", DeviceState.STOPPED),
                                  (h.c.snapshot(), "device_status_stale", True)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)
            self.assertFalse(hasattr(obj, "__dict__"))
        self.assertFalse(hasattr(h.c, "__dict__"))
        status(h, DeviceState.CAPTURING)
        self.assertIs(report.state, DeviceState.WAITING)
        self.assertIsNot(h.c.snapshot().device_status, report)

    def test_idle_and_prehello_traffic_cannot_invent_observations(self):
        c = SessionController()
        self.assertEqual(c.receive(Frame(134, 0, 1, 0, payload(generation=0)), 0), ())
        self.assertIsNone(c.snapshot().device_status)
        h = Harness()
        h.tx()
        events = status(h, DeviceState.STOPPED)
        self.assertIs(events[0].reason, RecoveryReason.REPLY)
        self.assertIsNone(h.c.snapshot().device_status)

    def test_malformed_status_cannot_replace_last_valid_observation(self):
        invalid = [b"", bytes(23), bytes(25), payload(state=7), payload(reason=1),
                   payload(state=5), payload(state=5, reason=7), payload(reserved=1),
                   payload(generation=3), payload(window=MAX_ID + 1), payload(busy=1)]
        for data in invalid:
            with self.subTest(payload=data):
                h = Harness().armed()
                status(h)
                before = h.c.snapshot().device_status
                events = h.receive(134, data)
                self.assertIs(events[0].reason, RecoveryReason.PAYLOAD)
                self.assertIs(h.c.snapshot().device_status, before)
                self.assertTrue(h.c.snapshot().device_status_stale)

    def test_old_session_does_not_advance_time_or_deadlines_or_replace_status(self):
        h = Harness().armed()
        status(h)
        h.c.stop(0)
        h.tx()
        before = h.c.snapshot()
        foreign = Frame(134, before.session + 1, MAX_ID, 0, payload(state=5, reason=3))
        self.assertEqual(h.c.receive(foreign, RESPONSE_TIMEOUT_MS * 10), ())
        self.assertEqual(h.c.snapshot(), before)

    def test_exact_replay_does_not_refresh_receipt_time(self):
        h = Harness().armed()
        status(h)
        before = h.c.snapshot().device_status
        self.assertEqual(h.c.receive(h.c._last_rx, 10), ())
        self.assertIs(h.c.snapshot().device_status, before)
        self.assertEqual(before.received_ms, 0)
        self.assertEqual(h.c.snapshot().now_ms, 10)

    def test_sequence_reply_and_direction_guards_preserve_last_report(self):
        for kind, sequence, request in ((134, 99, 0), (134, 6, 99), (3, 6, 0)):
            with self.subTest(kind=kind, sequence=sequence, request=request):
                h = Harness().armed()
                status(h)
                before = h.c.snapshot().device_status
                events = h.c.receive(Frame(kind, 100, sequence, request, payload()), 0)
                self.assertIs(events[0].kind, EventKind.RECOVERY)
                self.assertIs(h.c.snapshot().device_status, before)
                self.assertTrue(h.c.snapshot().device_status_stale)

    def test_config_and_arm_do_not_synthesize_reported_states(self):
        h = Harness().ready()
        status(h, DeviceState.STOPPED)
        original = h.c.snapshot().device_status
        h.c.configure(Config(), 0)
        self.assertTrue(h.c.snapshot().device_status_stale)
        status(h, DeviceState.STOPPED)
        self.assertTrue(h.c.snapshot().device_status_stale)
        request = h.tx()
        h.receive(131, struct.pack("<I", 1) + request.payload, request.request_id)
        self.assertTrue(h.c.snapshot().device_status_stale)
        status(h, DeviceState.STOPPED)
        self.assertFalse(h.c.snapshot().device_status_stale)
        h.c.arm(0)
        self.assertTrue(h.c.snapshot().device_status_stale)
        request = h.tx()
        h.receive(132, struct.pack("<II", request.request_id, 1), request.request_id)
        self.assertTrue(h.c.snapshot().device_status_stale)
        self.assertIs(h.c.snapshot().device_status.state, DeviceState.STOPPED)
        self.assertEqual(h.c.snapshot().device_status.generation, original.generation)
        status(h, DeviceState.PREPARING)
        self.assertFalse(h.c.snapshot().device_status_stale)
        self.assertEqual(h.c.snapshot().device_status.generation, request.request_id)

    def test_rejected_command_does_not_invent_an_error_status(self):
        h = Harness().ready()
        status(h, DeviceState.STOPPED)
        original = h.c.snapshot().device_status
        h.c.configure(Config(), 0)
        request = h.tx()
        events = h.receive(255, struct.pack("<BBHI", 3, 1, 0, 0), request.request_id)
        self.assertEqual([e.kind for e in events], [EventKind.COMMAND_ERROR])
        self.assertIs(h.c.snapshot().device_status, original)
        self.assertTrue(h.c.snapshot().device_status_stale)
        self.assertIs(h.c.snapshot().phase, Phase.READY)

    def test_stop_pending_and_ack_keep_last_report_until_new_status(self):
        h = Harness().armed()
        status(h)
        h.c.stop(0)
        request = h.tx()
        status(h, DeviceState.TRANSFERRING)
        self.assertTrue(h.c.snapshot().device_status_stale)
        prior = h.c.snapshot().device_status
        h.stopped(request)
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        self.assertIs(h.c.snapshot().device_status, prior)
        self.assertTrue(h.c.snapshot().device_status_stale)
        status(h, DeviceState.STOPPED)
        self.assertIs(h.c.snapshot().device_status.state, DeviceState.STOPPED)
        self.assertFalse(h.c.snapshot().device_status_stale)

    def test_late_status_never_cancels_a_new_arm_or_ack(self):
        h = Harness().armed()
        status(h)
        h.stopped(h.stop())
        h.c.arm(0)
        request = h.tx()
        status(h, DeviceState.STOPPED)
        self.assertEqual(h.c.snapshot().pending_request_id, request.request_id)
        self.assertTrue(h.c.snapshot().device_status_stale)
        h.receive(132, struct.pack("<II", request.request_id, 1), request.request_id)
        self.assertIs(h.c.snapshot().phase, Phase.ARMED)
        self.assertTrue(h.c.snapshot().device_status_stale)
        status(h, DeviceState.WAITING)
        self.assertFalse(h.c.snapshot().device_status_stale)

    def test_state_conflicting_with_host_ack_is_retained_as_stale_not_control(self):
        h = Harness().armed()
        status(h, DeviceState.STOPPED)
        self.assertIs(h.c.snapshot().phase, Phase.ARMED)
        self.assertTrue(h.c.snapshot().device_status_stale)
        h.stopped(h.stop())
        status(h, DeviceState.WAITING)
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        self.assertTrue(h.c.snapshot().device_status_stale)

    def test_window_boundaries_make_prior_report_stale_without_inventing_states(self):
        for terminal in ("end", "abort"):
            with self.subTest(terminal=terminal):
                h = Harness().armed()
                status(h, DeviceState.CAPTURING, window=1)
                h.begin()
                self.assertTrue(h.c.snapshot().device_status_stale)
                status(h, DeviceState.TRANSFERRING, window=1)
                self.assertFalse(h.c.snapshot().device_status_stale)
                getattr(h, terminal)()
                self.assertTrue(h.c.snapshot().device_status_stale)
                self.assertIs(h.c.snapshot().device_status.state, DeviceState.TRANSFERRING)
        h = Harness().armed()
        status(h)
        h.abort()
        self.assertTrue(h.c.snapshot().device_status_stale)

    def test_older_window_status_is_retained_as_stale(self):
        h = Harness().armed()
        h.begin()
        status(h, DeviceState.TRANSFERRING, window=0)
        self.assertTrue(h.c.snapshot().device_status_stale)
        self.assertEqual(h.c.snapshot().window_id, 1)
        self.assertEqual(h.c.snapshot().device_status.window_id, 0)

    def test_release_queue_and_ack_wait_for_new_status_to_refresh(self):
        h = BytePeer()
        receipt = h.window()
        self.assertIs(h.c.snapshot().device_status.state, DeviceState.WAIT_RELEASE)
        self.assertFalse(h.c.snapshot().device_status_stale)
        h.c.release(receipt, h.now)
        self.assertTrue(h.c.snapshot().device_status_stale)
        h.write()
        frame = h.read_one()
        self.assertEqual(frame.message_type, 135)
        self.assertTrue(h.c.snapshot().device_status_stale)
        self.assertIs(h.c.snapshot().device_status.state, DeviceState.WAIT_RELEASE)
        h.drain()
        self.assertIs(h.c.snapshot().device_status.state, DeviceState.WAITING)
        self.assertFalse(h.c.snapshot().device_status_stale)

    def test_response_timeout_and_transport_faults_preserve_stale_observation(self):
        for fault in ("transport_lost", "unknown_write", "protocol_fault", "timeout"):
            with self.subTest(fault=fault):
                h = Harness().armed()
                status(h)
                report = h.c.snapshot().device_status
                if fault == "timeout":
                    h.stop()
                    events = h.c.tick(RESPONSE_TIMEOUT_MS)
                elif fault == "protocol_fault":
                    events = h.c.protocol_fault(FaultCode.DECODE, 1)
                else:
                    events = getattr(h.c, fault)(1)
                self.assertIs(events[0].kind, EventKind.RECOVERY)
                self.assertIs(h.c.snapshot().device_status, report)
                self.assertTrue(h.c.snapshot().device_status_stale)

    def test_no_fabricated_heartbeat_timeout_during_normal_wait(self):
        h = Harness().armed()
        status(h)
        report = h.c.snapshot().device_status
        self.assertEqual(h.c.tick(86400000), ())
        self.assertIs(h.c.snapshot().phase, Phase.ARMED)
        self.assertIs(h.c.snapshot().device_status, report)
        self.assertFalse(h.c.snapshot().device_status_stale)

    def test_status_at_response_deadline_cannot_replace_timeout_evidence(self):
        h = Harness().armed()
        status(h)
        report = h.c.snapshot().device_status
        h.stop()
        h.now = RESPONSE_TIMEOUT_MS
        events = status(h, DeviceState.ERROR, reason=3)
        self.assertIs(events[0].reason, RecoveryReason.TIMEOUT)
        self.assertIs(h.c.snapshot().device_status, report)
        self.assertTrue(h.c.snapshot().device_status_stale)

    def test_new_session_clears_observation_and_ignores_previous_session_status(self):
        h = Harness().armed()
        status(h)
        old_frame = h.c._last_rx
        h.c.transport_lost(1)
        h.c.start(101, 2)
        before = h.c.snapshot()
        self.assertIsNone(before.device_status)
        self.assertFalse(before.device_status_stale)
        self.assertEqual(h.c.receive(old_frame, 50000), ())
        self.assertEqual(h.c.snapshot(), before)

    def test_invalid_api_call_does_not_mutate_status_or_freshness(self):
        h = Harness().armed()
        status(h)
        before = h.c.snapshot()
        with self.assertRaises(SessionError):
            h.c.configure(Config(), 10000)
        self.assertEqual(h.c.snapshot(), before)

    def test_many_observations_keep_one_value_no_status_history(self):
        h = Harness().armed()
        for number in range(2000):
            h.now = number
            status(h, DeviceState.WAITING, observed=number, busy=number)
        self.assertEqual(h.c.snapshot().device_status.received_ms, 1999)
        self.assertEqual(h.c.snapshot().device_status.observed_external_edges, 1999)
        self.assertIsNone(h.c.snapshot().terminal)
        self.assertFalse(hasattr(h.c, "__dict__"))


try:
    from motortester.acquisition import _AcquisitionWorker, _Mailbox
except ModuleNotFoundError:
    _AcquisitionWorker = _Mailbox = None


@unittest.skipIf(_AcquisitionWorker is None, "Qt dependencies unavailable")
class DeviceStatusPublicationTests(unittest.TestCase):
    """Direct worker/mailbox checks create no thread, timer, or external I/O."""

    def make(self):
        h = Harness().armed()

        class Runtime:
            pending_capture = incomplete_result = None

            def snapshot(self):
                return h.c.snapshot()

            def disconnect(self):
                return h.c.transport_lost(h.now)

        box = _Mailbox()
        worker = _AcquisitionWorker(box, None, lambda: h.now, 10, 150)
        worker.runtime, worker.connected, worker.source = Runtime(), True, "emulator"
        notices = []
        worker.notification.connect(lambda: notices.append(True))
        worker._publish()
        return h, box, worker, notices

    def test_status_reaches_bounded_publication_without_status_event_queue(self):
        h, box, worker, notices = self.make()
        self.assertIsNone(box.state.snapshot.device_status)
        for state in (DeviceState.PREPARING, DeviceState.WAITING, DeviceState.CAPTURING,
                      DeviceState.TRANSFERRING, DeviceState.WAIT_RELEASE):
            worker._events(status(h, state))
            worker._publish()
            self.assertIs(box.state.snapshot.device_status.state, state)
            self.assertIs(box.state.snapshot.phase, Phase.ARMED)
        self.assertEqual(len(notices), 1)  # A blocked GUI cannot grow a status queue.
        self.assertIsNone(box.capture)
        self.assertIsNone(box.incomplete)
        box.notified = False
        worker._events(status(h))
        worker._publish()
        self.assertEqual(len(notices), 2)

    def test_host_clock_alone_does_not_generate_publication(self):
        h, box, worker, notices = self.make()
        worker._events(status(h))
        worker._publish()
        box.notified = False
        h.c.tick(1000)
        worker._publish()
        self.assertEqual(len(notices), 1)

    def test_disconnect_keeps_last_report_explicitly_stale_and_disconnected(self):
        h, box, worker, notices = self.make()
        worker._events(status(h))
        worker._publish()
        self.assertTrue(worker._disconnect())
        worker._publish()
        self.assertFalse(box.state.connected)
        self.assertIs(box.state.snapshot.phase, Phase.RECOVERY)
        self.assertIs(box.state.snapshot.device_status.state, DeviceState.WAITING)
        self.assertTrue(box.state.snapshot.device_status_stale)

    def test_error_report_survives_immediate_recovery_publication(self):
        h, box, worker, notices = self.make()
        worker._events(status(h, DeviceState.ERROR, reason=3))
        worker._publish()
        self.assertIs(box.state.snapshot.device_status.state, DeviceState.ERROR)
        self.assertEqual(box.state.snapshot.device_status.reason, 3)
        self.assertTrue(box.state.snapshot.device_status_stale)
        self.assertIn("DEVICE_ERROR", box.error)


if __name__ == "__main__":
    unittest.main()

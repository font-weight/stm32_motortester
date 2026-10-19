"""Finite fake-backed serial ownership; never imports pyserial or opens a COM port."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from motortester.stream_serial import SerialSettings, SerialStreamPeer
from motortester.stream_controller import StreamController
from motortester import stream_recording as storage
from motortester.transport import TransportArgumentError
from test_stream_controller import EncodedPeer, CONFIG, wait_for


class FakeBackend:
    def __init__(self, calls, kwargs):
        self.calls, self.kwargs = calls, kwargs
        self.port = None
        self.is_open = False
        self.peer = EncodedPeer()
        self.open_fail = self.close_fail = False
        self.hooks = {}

    def _call(self, kind, arg=None):
        self.calls.append((kind, threading.get_ident(), arg))
        hook = self.hooks.get(kind)
        if hook:
            return hook(arg)

    def open(self):
        self._call("open", self.port)
        if self.open_fail:
            raise OSError("fake open failure")
        self.is_open = True

    def read(self, maximum):
        if "read" in self.hooks:
            return self._call("read", maximum)
        self._call("read", maximum)
        return self.peer.read(maximum)

    def write(self, data):
        if "write" in self.hooks:
            return self._call("write", data)
        self._call("write", data)
        return self.peer.write(data)

    def close(self):
        self._call("close")
        if self.close_fail:
            raise OSError("fake close failure")
        self.is_open = False


class FakeFactory:
    def __init__(self):
        self.calls, self.backends = [], []
        self.configure = None

    def __call__(self, **kwargs):
        self.calls.append(("factory", threading.get_ident(), kwargs))
        backend = FakeBackend(self.calls, kwargs)
        self.backends.append(backend)
        if self.configure:
            self.configure(backend)
        return backend


class SerialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stream-serial-")
        self.path = str(Path(self.temp.name) / "record.mtstream")
        self.controllers, self.releases = [], []
        self.guard = patch("motortester.transport._serial_factory",
                           side_effect=AssertionError("Real serial factory is forbidden in this suite"))
        self.guard.start()

    def tearDown(self):
        for release in self.releases:
            release.set()
        for c, factory, clock in self.controllers:
            for backend in factory.backends:
                backend.close_fail = False
                backend.hooks.pop("close", None)
            c.close()
            if c._threaded:
                deadline = time.monotonic() + 4
                while not c.join(.01) and time.monotonic() < deadline:
                    c.retry_close()
                self.assertTrue(c.join(0), "retained cleanup owner did not settle after release")
            else:
                for _ in range(50):
                    c.retry_close(); c.service(clock[0]); c.execute_disk_once()
                    if c.join(0):
                        break
                self.assertTrue(c.join(0))
        self.guard.stop()
        self.temp.cleanup()

    def make(self, *, threaded=False, configure=None):
        clock = [0]
        factory = FakeFactory(); factory.configure = configure
        peer = SerialStreamPeer(SerialSettings("FAKE-SELECTED-PORT", 230400, 0, .025), backend_factory=factory)
        c = StreamController(peer, event_config=CONFIG, managed_peer=True,
                             threaded=threaded, clock=lambda: clock[0])
        self.controllers.append((c, factory, clock))
        return c, factory, clock

    def step(self, c, *, disk=True):
        c.service(c._clock())
        if disk:
            c.execute_disk_once()

    def until(self, c, predicate):
        if c._threaded:
            wait_for(lambda: predicate(c.snapshot()))
        else:
            for _ in range(200):
                if predicate(c.snapshot()):
                    return
                self.step(c)
            self.fail("finite lifecycle did not settle: " + repr(c.snapshot()))

    def connected(self, **kwargs):
        c, f, clock = self.make(**kwargs)
        self.assertTrue(c.connect())
        self.until(c, lambda s: s.phase == "connected")
        return c, f, clock

    def recording(self, **kwargs):
        c, f, clock = self.connected(**kwargs)
        self.assertTrue(c.record(self.path))
        self.until(c, lambda s: s.phase == "recording" and
                   s.bridge.session.accepted_end_sample_index == 0 and not s.worker_unresolved)
        return c, f, clock

    def hold(self, action=None):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def callback(arg):
            result = action(arg) if action else None
            entered.set()
            if not release.wait(4):
                raise RuntimeError("finite fake callback barrier expired")
            return result
        return callback, entered, release

    def test_explicit_settings_legacy_validation_and_immutable_aliases(self):
        valid = SerialSettings("FAKE")
        with self.assertRaises(FrozenInstanceError):
            valid.port = "OTHER"
        for args in (("",), (" FAKE",), ("loop://",), ("bad\nport",), ([],),
                     ("FAKE", True), ("FAKE", 0), ("FAKE", 115200, True),
                     ("FAKE", 115200, .01), ("FAKE", 115200, 0, True),
                     ("FAKE", 115200, 0, 0), ("FAKE", 115200, 0, float("nan")),
                     ("FAKE", 115200, 0, float("inf"))):
            with self.subTest(args=args), self.assertRaises(TransportArgumentError):
                SerialSettings(*args)
        peer = SerialStreamPeer(valid, backend_factory=FakeFactory())
        with self.assertRaises(AttributeError):
            peer.settings = SerialSettings("OTHER")

    def test_connect_only_opens_on_receiver_without_negotiation_or_arming(self):
        c, f, clock = self.make(threaded=True)
        self.assertEqual(f.calls, [])
        self.assertFalse(c.preview()); self.assertFalse(c.record(self.path))
        self.assertTrue(c.connect())
        self.until(c, lambda s: s.phase == "connected")
        time.sleep(.015)
        self.assertEqual([x[0] for x in f.calls], ["factory", "open"])
        self.assertIsNone(c.snapshot().bridge)
        self.assertEqual(len({x[1] for x in f.calls}), 1)
        self.assertNotEqual(f.calls[0][1], threading.get_ident())
        self.assertEqual(f.calls[0][2], dict(port=None, baudrate=230400, bytesize=8,
            parity="N", stopbits=1, timeout=0.0, write_timeout=.025,
            xonxoff=False, rtscts=False, dsrdtr=False))
        self.assertEqual(f.calls[1][2], "FAKE-SELECTED-PORT")

    def test_connect_slot_saturation_and_reserved_disconnect_before_acceptance(self):
        c, f, clock = self.make()
        self.assertTrue(c.connect())
        for _ in range(10):
            self.assertFalse(c.connect()); self.assertFalse(c.preview())
        self.assertTrue(c.disconnect())
        self.step(c)
        self.assertEqual(f.calls, [])
        self.assertEqual(c.snapshot().phase, "disconnected")
        self.assertTrue(c.connect()); self.step(c)
        self.assertEqual(c.snapshot().serial.generation, 1)
        self.assertTrue(c.disconnect())
        # Check actions directly before owner publication, not only snapshot/UI.
        self.assertFalse(c.preview()); self.assertFalse(c.record(self.path)); self.assertFalse(c.connect())

    def test_real_encoded_peer_journal_and_clean_fresh_connection_generation(self):
        c, f, clock = self.recording()
        old = f.backends[0]; old_bytes = old.peer.data()
        self.until(c, lambda s: s.bridge.session.accepted_end_sample_index == 128)
        old_identity = c.snapshot().bridge.identity
        c.stop(); self.until(c, lambda s: s.phase == "saved" and s.can_open)
        fresh = storage.recover_attempt(self.path).recording
        self.assertEqual(fresh.read_raw(0, 128), old.peer.raw)
        self.assertTrue(fresh.clean_stop_reported)
        self.assertTrue(c.disconnect())
        self.until(c, lambda s: s.phase == "disconnected" and s.can_connect)
        self.assertTrue(c.connect()); self.until(c, lambda s: s.serial.generation == 2 and s.can_preview)
        self.assertEqual([x[0] for x in f.calls].count("close"), 1)
        self.assertFalse(f.backends[0].is_open)
        self.assertEqual(f.backends[1].peer.commands, [])
        self.assertTrue(c.record(self.path + "new"))
        self.until(c, lambda s: s.phase == "recording" and s.revision == 2)
        self.assertNotEqual(c.snapshot().bridge.identity, old_identity)
        f.backends[1].peer.enqueue(old_bytes)
        for _ in range(5):
            self.step(c)
        self.assertEqual(c.snapshot().validated_samples, 0)
        self.assertTrue(all(x[2] <= 544 for x in f.calls if x[0] == "read"))
        self.assertEqual(len({x[1] for x in f.calls}), 1)

    def test_clean_preview_disconnect_does_not_invent_transport_fault(self):
        c, f, clock = self.connected()
        c.preview(); self.until(c, lambda s: s.phase == "preview")
        c.stop(); self.until(c, lambda s: s.phase == "preview stopped")
        c.disconnect(); self.until(c, lambda s: s.can_connect)
        self.assertIsNone(c.snapshot().bridge.endpoint_first_fault)
        c.connect(); self.until(c, lambda s: s.can_preview)
        self.assertTrue(c.preview())
        self.until(c, lambda s: s.phase == "preview" and s.revision == 2)

    def test_shutdown_close_failure_keeps_owner_alive_for_cleanup_only_retry(self):
        c, f, clock = self.connected(threaded=True)
        backend = f.backends[0]; backend.close_fail = True
        c.close()
        self.until(c, lambda s: s.can_retry_close)
        before = len(f.calls)
        time.sleep(.025)
        self.assertEqual(len(f.calls), before)
        self.assertFalse(c.join(0))
        self.assertTrue(c.snapshot().serial_unresolved)
        self.assertFalse(c.connect()); self.assertFalse(c.preview()); self.assertFalse(c.record(self.path))
        self.assertTrue(c.retry_close())
        self.until(c, lambda s: s.can_retry_close and [x[0] for x in f.calls].count("close") == 2)
        self.assertIs(c._peer._transport._backend, backend)
        backend.close_fail = False
        self.assertTrue(c.retry_close())
        self.assertTrue(c.join(4))
        self.assertEqual([x[0] for x in f.calls].count("close"), 3)
        self.assertEqual(len(f.backends), 1)
        self.assertIsNone(c.snapshot().error)
        self.assertEqual(c.snapshot().serial.first_error, "TransportCloseError")
        self.assertIsNone(c.snapshot().serial.close_error)

    def test_failed_open_cleanup_retains_exact_handle_until_explicit_retry(self):
        def configure(b):
            b.open_fail = b.close_fail = True
        c, f, clock = self.make(configure=configure)
        c.connect(); self.step(c)
        self.assertEqual(c.snapshot().serial.state, "close_failed")
        before = len(f.calls)
        for _ in range(10):
            self.step(c)
        self.assertEqual(len(f.calls), before)
        self.assertFalse(c.connect())
        c.close(); self.step(c)
        self.assertEqual(len(f.calls), before)
        self.assertFalse(c.join(0))
        f.backends[0].close_fail = False
        self.assertTrue(c.retry_close()); self.step(c)
        self.assertTrue(c.join(0))
        self.assertEqual(len(f.backends), 1)

    def test_late_open_success_after_disconnect_never_revives_or_starts(self):
        callback, entered, release = self.hold()
        c, f, clock = self.make(threaded=True, configure=lambda b: b.hooks.update(open=callback))
        c.connect(); self.assertTrue(entered.wait(4))
        self.assertEqual(c.snapshot().serial_call, "open")
        self.assertEqual(c.snapshot().serial_operation_generation, 1)
        self.assertEqual(c.snapshot().serial_operation_id, 1)
        self.assertTrue(c.disconnect())
        self.assertFalse(c.preview()); self.assertFalse(c.connect())
        clock[0] = 1001; release.set()
        self.until(c, lambda s: s.phase == "disconnected" and s.can_connect)
        self.assertEqual([x[0] for x in f.calls], ["factory", "open", "close"])
        self.assertIsNone(c.snapshot().bridge)
        self.assertFalse(f.backends[0].is_open)

    def test_late_read_bytes_after_disconnect_are_not_admitted_or_saved(self):
        c, f, clock = self.recording(threaded=True)
        backend = f.backends[0]
        def encoded_data(maximum):
            backend.peer.data()
            return backend.peer.read(maximum)
        callback, entered, release = self.hold(encoded_data)
        backend.hooks["read"] = callback
        self.assertTrue(entered.wait(4))
        c.disconnect()
        self.assertFalse(c.record(self.path + "other"))
        release.set()
        self.until(c, lambda s: s.serial.handle_settled)
        clock[0] = 4001
        self.until(c, lambda s: s.can_open and not s.worker_unresolved)
        self.assertEqual(c.snapshot().validated_samples, 0)
        report = storage.recover_attempt(self.path).recording
        self.assertEqual(report.end_sample_index, 0)
        self.assertFalse(report.clean_stop_reported)
        self.assertFalse(c.snapshot().can_preview)

    def test_late_write_preserves_exact_offer_count_but_does_not_read_ACK(self):
        c, f, clock = self.recording(threaded=True)
        backend = f.backends[0]
        callback, entered, release = self.hold(backend.peer.write)
        backend.hooks["write"] = callback
        c.stop(); self.assertTrue(entered.wait(4))
        reads = sum(x[0] == "read" for x in f.calls)
        c.disconnect(); release.set()
        self.until(c, lambda s: s.serial.handle_settled)
        clock[0] = 4001
        self.until(c, lambda s: s.can_open and not s.worker_unresolved)
        self.assertEqual(sum(x[0] == "read" for x in f.calls), reads)
        pending = c.snapshot().bridge.endpoint_first_fault.stop
        self.assertTrue(pending.complete)
        self.assertEqual(pending.request_id, 4)
        self.assertIsNone(c.snapshot().bridge.session.stop_acknowledgement)
        self.assertFalse(storage.recover_attempt(self.path).recording.clean_stop_reported)

    def test_unknown_write_keeps_fault_fenced_after_explicit_reconnect(self):
        c, f, clock = self.recording()
        backend = f.backends[0]
        backend.hooks["write"] = lambda _: None  # invalid count becomes TransportWriteError
        c.stop(); self.step(c)
        self.assertEqual(c.snapshot().bridge.session.first_failure.operation, "STOP")
        self.assertEqual(c.snapshot().bridge.session.first_failure.request_id, 4)
        clock[0] = 4001
        self.until(c, lambda s: s.bridge.session.finalized and not s.worker_unresolved)
        failure = storage.recover_attempt(self.path).recording.terminal.failure
        self.assertEqual((failure.operation, failure.request_id), ("STOP", 4))
        c.disconnect(); self.until(c, lambda s: s.can_connect)
        c.connect(); self.until(c, lambda s: s.serial.state == "open" and s.serial.generation == 2)
        self.assertFalse(c.preview()); self.assertFalse(c.record(self.path + "retry"))
        self.assertEqual(f.backends[1].peer.commands, [])

    def test_serial_default_short_zero_and_deadline_equality_observations(self):
        c, f, clock = self.recording()
        backend = f.backends[0]
        backend.peer.accepted_prefix = 1
        c.stop(); self.step(c)
        self.assertEqual(c.snapshot().bridge.pending_stop.accepted_bytes, 1)
        backend.hooks["write"] = lambda _: 0
        self.step(c)
        self.assertEqual(c.snapshot().bridge.pending_stop.accepted_bytes, 1)
        def elapsed(_):
            clock[0] = 1000
            return 0
        backend.hooks["write"] = elapsed
        clock[0] = 999; self.step(c)
        self.assertEqual(c.snapshot().bridge.endpoint_first_fault.code, "data_timeout")
        clock[0] = 5000
        self.until(c, lambda s: s.bridge.session.finalized and not s.worker_unresolved)
        self.assertIn("wire data_timeout;", storage.recover_attempt(self.path).recording.terminal.failure.detail)

    def test_close_callback_unresolved_is_not_force_cancelled_or_retried(self):
        c, f, clock = self.connected(threaded=True)
        callback, entered, release = self.hold()
        f.backends[0].hooks["close"] = callback
        c.close(); self.assertTrue(entered.wait(4))
        self.assertEqual(c.snapshot().serial_call, "close")
        self.assertFalse(c.join(0)); self.assertFalse(c.retry_close()); self.assertFalse(c.connect())
        release.set(); self.assertTrue(c.join(4))
        self.assertEqual(sum(x[0] == "close" for x in f.calls), 1)


    def test_failed_open_with_settled_cleanup_needs_another_explicit_connect(self):
        c, f, clock = self.make(configure=lambda b: setattr(b, "open_fail", True))
        c.connect(); self.step(c)
        self.assertTrue(c.snapshot().serial.handle_settled)
        self.assertEqual(c.snapshot().serial.first_error, "TransportOpenError")
        for _ in range(10):
            self.step(c)
        self.assertEqual(len(f.backends), 1)
        f.configure = None
        self.assertTrue(c.connect()); self.step(c)
        self.assertEqual(c.snapshot().serial.generation, 2)
        self.assertTrue(c.snapshot().can_preview)
        self.assertEqual(f.backends[1].peer.commands, [])

    def test_foreign_thread_cannot_operate_handle_and_all_IO_has_one_owner(self):
        c, f, clock = self.recording(threaded=True)
        before = len(f.calls)
        for method, args in (("snapshot", ()), ("read", (1,)), ("write", (b"x",)), ("close", ())):
            with self.subTest(method=method), self.assertRaises(RuntimeError):
                getattr(c._peer, method)(*args)
        f.backends[0].peer.data()
        self.until(c, lambda s: s.validated_samples == 128)
        c.stop(); self.until(c, lambda s: s.phase == "saved")
        c.close(); self.assertTrue(c.join(4))
        self.assertEqual(set(x[0] for x in f.calls), {"factory", "open", "read", "write", "close"})
        self.assertEqual(len({x[1] for x in f.calls}), 1)
        self.assertNotEqual(f.calls[0][1], threading.get_ident())

    def test_permanent_close_during_open_retains_failed_cleanup_for_retry(self):
        callback, entered, release = self.hold()
        def configure(backend):
            backend.hooks["open"] = callback
            backend.close_fail = True
        c, f, clock = self.make(threaded=True, configure=configure)
        c.connect(); self.assertTrue(entered.wait(4))
        c.close(); self.assertFalse(c.join(0))
        clock[0] = 2001; release.set()
        self.until(c, lambda s: s.can_retry_close)
        self.assertIsNone(c.snapshot().bridge)
        self.assertEqual([x[0] for x in f.calls], ["factory", "open", "close"])
        self.assertFalse(c.preview()); self.assertFalse(c.connect())
        f.backends[0].close_fail = False
        self.assertTrue(c.retry_close()); self.assertTrue(c.join(4))
        self.assertEqual(len(f.backends), 1)

    def test_disconnect_during_real_prepare_preserves_unbound_attempt_and_settlement(self):
        c, f, clock = self.connected(threaded=True)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = storage.PreparedStreamWriter.prepare
        def held(*args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("prepare barrier expired")
            return original(*args, **kwargs)
        with patch.object(storage.PreparedStreamWriter, "prepare", held):
            c.record(self.path); self.assertTrue(entered.wait(4))
            self.assertTrue(c.disconnect())
            self.until(c, lambda s: s.serial.handle_settled)
            self.assertFalse(c.connect()); self.assertFalse(c.snapshot().can_open)
            self.assertIsNone(c.snapshot().bridge.session.selected_start_sample_index)
            release.set(); clock[0] = 4001
            self.until(c, lambda s: s.can_open and s.can_connect)
        report = storage.recover_attempt(self.path)
        self.assertIsNone(report.recording)
        self.assertIsNone(report.selected_start)
        self.assertIsNotNone(report.unbound_outcome.failure)
        self.assertNotIn(8, [command[0] for command in f.backends[0].peer.commands])

    def test_disconnect_during_real_append_keeps_writer_owned_and_failure_durable(self):
        c, f, clock = self.recording(threaded=True)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = storage.StreamWriter.append
        def held(*args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("append barrier expired")
            return original(*args, **kwargs)
        with patch.object(storage.StreamWriter, "append", held):
            f.backends[0].peer.data(); self.assertTrue(entered.wait(4))
            c.disconnect(); self.until(c, lambda s: s.serial.handle_settled)
            self.assertTrue(c.snapshot().worker_unresolved)
            self.assertFalse(c.connect()); self.assertFalse(c.snapshot().can_open)
            self.assertEqual(c.snapshot().bridge.session.accepted_end_sample_index, 0)
            clock[0] = 4001; release.set()
            self.until(c, lambda s: s.can_open and s.can_connect)
        report = storage.recover_attempt(self.path).recording
        self.assertEqual(report.read_raw(0, 128), f.backends[0].peer.raw)
        self.assertFalse(report.clean_stop_reported)
        self.assertEqual(report.terminal.failure, c.snapshot().bridge.session.first_failure)

    def test_shutdown_during_real_finish_does_not_claim_returned_writer_success(self):
        c, f, clock = self.recording(threaded=True)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = storage.StreamWriter.finish
        def held(*args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("finish barrier expired")
            return original(*args, **kwargs)
        with patch.object(storage.StreamWriter, "finish", held):
            c.stop(); self.assertTrue(entered.wait(4))
            c.close(); self.until(c, lambda s: s.serial.handle_settled)
            self.assertFalse(c.join(0)); self.assertTrue(c.snapshot().worker_unresolved)
            release.set(); self.assertTrue(c.join(4))
        self.assertTrue(c.snapshot().bridge.session.finalization_unresolved)
        self.assertFalse(c.snapshot().bridge.session.clean_completion_reported)
        # Bytes that the actual writer finished remain distinguishable from the
        # completion abandoned by the receiver; never call this observed success.
        self.assertTrue(storage.recover_attempt(self.path).recording.clean_stop_reported)

    def test_held_display_prevents_reconnect_and_shutdown_join_until_return(self):
        from motortester import stream_controller as module
        c, f, clock = self.connected(threaded=True)
        c.preview(); self.until(c, lambda s: s.phase == "preview")
        c.bind_display(c.snapshot().revision, lambda _: None)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = module.render_display
        def held(source):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("display barrier expired")
            return original(source)
        with patch.object(module, "render_display", held):
            f.backends[0].peer.data(); self.assertTrue(entered.wait(4))
            c.stop(); self.until(c, lambda s: s.phase == "preview stopped")
            c.disconnect(); self.until(c, lambda s: s.serial.handle_settled)
            self.assertFalse(c.connect()); self.assertFalse(c.snapshot().can_connect)
            c.close(); self.assertFalse(c.join(0))
            release.set(); self.assertTrue(c.join(4))

    def test_actual_serial_errors_use_current_time_and_preserve_known_or_ambiguous_cause(self):
        for mode in ("read_error", "read_empty", "write_error", "write_invalid"):
            with self.subTest(mode=mode):
                c, f, clock = self.connected()
                path = self.path + mode
                c.record(path)
                self.until(c, lambda s: s.phase == "recording" and s.bridge.session.accepted_end_sample_index == 0)
                backend = f.backends[0]
                def elapsed(arg):
                    clock[0] = 1000
                    if mode.endswith("error"):
                        raise OSError("fake callback elapsed at deadline")
                    return True if mode == "write_invalid" else b""
                kind = "write" if mode.startswith("write") else "read"
                backend.hooks[kind] = elapsed
                if kind == "write":
                    c.stop()
                clock[0] = 999; self.step(c)
                self.assertEqual(c.snapshot().bridge.endpoint_first_fault.code, "data_timeout")
                backend.hooks.pop(kind)
                clock[0] = 5000
                self.until(c, lambda s: s.bridge.session.finalized and not s.worker_unresolved)
                failure = storage.recover_attempt(path).recording.terminal.failure
                self.assertIn("wire data_timeout;", failure.detail)
                self.assertIsNone(failure.operation); self.assertIsNone(failure.request_id)


    def test_preview_transport_loss_settles_without_fabricating_restart_isolation(self):
        c, f, clock = self.connected()
        c.preview(); self.until(c, lambda s: s.phase == "preview")
        c.disconnect(); self.step(c)
        clock[0] = 4001
        self.until(c, lambda s: s.can_connect)
        self.assertIsNone(c.snapshot().bridge.storage)
        c.connect(); self.until(c, lambda s: s.serial.generation == 2 and s.serial.state == "open")
        self.assertFalse(c.preview()); self.assertFalse(c.record(self.path))
        self.assertEqual(f.backends[1].peer.commands, [])

    def test_shutdown_during_held_read_exception_observes_deadline_before_close(self):
        c, f, clock = self.recording(threaded=True)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def failed(maximum):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("read error barrier expired")
            raise OSError("late read failure")
        f.backends[0].hooks["read"] = failed
        self.assertTrue(entered.wait(4))
        c.close(); self.assertFalse(c.join(0))
        clock[0] = 1000; release.set()
        self.assertTrue(c.join(4))
        self.assertEqual(c.snapshot().bridge.endpoint_first_fault.code, "data_timeout")
        self.assertIsNone(c.snapshot().bridge.session.first_failure.operation)
        self.assertFalse(c.snapshot().bridge.session.clean_completion_reported)

    def test_shutdown_during_held_write_error_keeps_exact_old_request(self):
        c, f, clock = self.recording(threaded=True)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def failed(data):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("write error barrier expired")
            raise OSError("unknown old write prefix")
        f.backends[0].hooks["write"] = failed
        c.stop(); self.assertTrue(entered.wait(4))
        c.close(); release.set(); self.assertTrue(c.join(4))
        failure = c.snapshot().bridge.session.first_failure
        self.assertEqual((failure.operation, failure.request_id), ("STOP", 4))
        self.assertIn("write_count_unknown", failure.detail)
        self.assertEqual(len(f.backends), 1)


if __name__ == "__main__":
    unittest.main()

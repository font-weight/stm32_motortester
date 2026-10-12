"""Author Qt/byte-peer checks; fake serial is not Windows/hardware evidence."""
from dataclasses import FrozenInstanceError, replace
import gc
import struct
import threading
import time
import unittest
import zlib

try:
    from PySide6 import QtCore, QtTest, QtWidgets
    from motortester.acquisition import AcquisitionBridge, _AcquisitionWorker, _Mailbox
    from motortester.gui_acquisition import AcquisitionDialog
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"Qt dependencies unavailable: {exc}") from exc

from motortester.device_emulator import DeviceEmulator, WAITING
from motortester.session import Config, Event, EventKind, FaultCode, Phase, RecoveryReason
from motortester.transport import SerialTransport
from motortester.wire import StreamDecoder


class DiagnosticPeer(DeviceEmulator):
    """Inject framed replies in the fake byte peer, never a physical port."""
    def __init__(self, controls):
        super().__init__(all_analog_modes=True)
        self.controls = controls

    def _enqueue(self, kind, payload, request=0, completion=""):
        payload = self.controls.get("reply_payloads", {}).get(kind, payload)
        return super()._enqueue(kind, payload, request, completion)

    def _command(self, frame):
        rejection = self.controls.get("reject_commands", {}).get(frame.message_type)
        if rejection:
            self._error(frame, rejection)
        elif frame.message_type == 3 and self.controls.get("reject_config"):
            self._error(frame, self.controls["reject_config"])
        else:
            super()._command(frame)


class SerialPeer:
    """Backend methods, byte protocol and emulator execute in the worker only."""
    def __init__(self, *, calls, controls, **kwargs):
        self.calls, self.controls = calls, controls
        self.is_open, self.port = False, None
        self.peer = DiagnosticPeer(controls)
        self.decoder = StreamDecoder()
        self.kinds = []
        self.saw_config = False
        self.record("create")
        controls["backend"] = self
        controls["settings"] = kwargs

    def record(self, operation):
        self.calls.append((operation, threading.get_ident()))

    def now(self):
        return time.monotonic_ns() // 1000000

    def open(self):
        self.record("open")
        entered = self.controls.get("open_entered")
        if entered:
            entered.set()
            if not self.controls["open_gate"].wait(3):
                raise OSError("test gate timed out")
        self.is_open = True
        if self.controls.get("open_fail"):
            raise OSError("private backend detail")

    def write(self, data):
        self.record("write")
        frames = self.decoder.feed(data, self.now()).frames
        self.kinds.extend(f.message_type for f in frames)
        if any(f.message_type == 3 for f in frames):
            self.saw_config = True
        if self.controls.get("fail_release") and any(f.message_type == 7 for f in frames):
            raise OSError("unknown acceptance")
        return self.peer.write(data, self.now()).accepted_prefix

    def read(self, size):
        self.record("read")
        if self.controls.pop("gate_idle_read", False):
            self.controls["read_entered"].set()
            if not self.controls["read_gate"].wait(3):
                raise OSError("test idle-read gate timed out")
        if self.controls.get("read_fail"):
            raise OSError("private backend detail")
        injected = self.controls.pop("read_bytes_once", None)
        if injected is not None:
            return injected
        if self.saw_config and self.controls.get("hold_config"):
            return b""
        if self.controls.get("trigger") and self.peer.snapshot().state == WAITING:
            self.peer.trigger(self.now())
        return self.peer.read(min(size, self.controls.get("read_limit", size)), self.now())

    def close(self):
        self.record("close")
        if self.controls.get("close_fail"):
            raise OSError("private backend detail")
        self.is_open = False
        self.peer.disconnect(self.now())


class AcquisitionBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.bridges = []
        self.dialog_viewers = []
        self.controls = {}
        self.calls = []

    def tearDown(self):
        self.controls["close_fail"] = False
        for gate_name in ("open_gate", "read_gate"):
            gate = self.controls.get(gate_name)
            if gate:
                gate.set()
        for bridge in self.bridges:
            if not bridge._closed:
                bridge.shutdown()
            self.wait(lambda: bridge._closed and not bridge.running)
            bridge.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(bridge, QtCore.QEvent.Type.DeferredDelete)
        self.bridges.clear()
        for viewer in self.dialog_viewers:
            viewer.close()
            viewer.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(viewer, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def make(self, **kwargs):
        bridge = AcquisitionBridge(**kwargs)
        bridge.captures, bridge.incompletes, bridge.errors, bridge.states = [], [], [], []
        bridge.capture_ready.connect(bridge.captures.append)
        bridge.incomplete_ready.connect(bridge.incompletes.append)
        bridge.error.connect(bridge.errors.append)
        bridge.state_changed.connect(bridge.states.append)
        self.bridges.append(bridge)
        return bridge

    def serial(self):
        def factory(**kwargs):
            return SerialPeer(calls=self.calls, controls=self.controls, **kwargs)
        return self.make(serial_factory=lambda: SerialTransport(backend_factory=factory),
                         edge_delay_ms=30)

    def dialog(self, bridge):
        """Real signal wiring without MainWindow's capture-ACK side effects."""
        viewer = QtWidgets.QWidget()
        viewer.busy = viewer._acq_requested = False
        viewer._set_actions = lambda: None
        panel = AcquisitionDialog(viewer, bridge)
        self.dialog_viewers.append(viewer)
        panel.update_state(bridge.state)
        return panel

    def wait(self, predicate, timeout=3000):
        start = time.monotonic()
        while not predicate():
            if (time.monotonic() - start) * 1000 > timeout:
                states = [(b.state, b.errors) for b in self.bridges]
                self.fail(f"Qt deadline; current states: {states}")
            QtTest.QTest.qWait(5)
        self.app.processEvents()

    def ready(self, bridge):
        return (bridge.state.connected and bridge.state.snapshot is not None
                and bridge.state.snapshot.phase is Phase.READY and not bridge.state.busy)

    def connect(self, bridge, source="emulator"):
        self.assertTrue(bridge.connect_source(source, port="COM_TEST" if source == "device" else ""))
        self.wait(lambda: self.ready(bridge))

    def capture(self, bridge, config=Config(), *, simulate_edge=True):
        before = len(bridge.captures)
        self.assertTrue(bridge.start(config, simulate_edge=simulate_edge))
        self.wait(lambda: len(bridge.captures) > before)
        return bridge.captures[-1]

    def test_lazy_lifecycle_and_frozen_state(self):
        bridge = self.make()
        self.assertFalse(bridge.running)
        self.assertFalse(bridge.active)
        self.assertFalse(bridge.start(Config()))
        self.assertFalse(bridge.stop())
        with self.assertRaises(FrozenInstanceError):
            bridge.state.connected = True
        finished = []
        bridge.finished.connect(lambda: finished.append(True))
        self.assertTrue(bridge.shutdown())
        self.assertFalse(bridge.shutdown())
        self.wait(lambda: bool(finished))

    def test_connect_only_handshake_no_automatic_arm_and_explicit_source(self):
        bridge = self.serial()
        self.assertFalse(bridge.connect_source("default"))
        self.assertFalse(bridge.connect_source("device", port=""))
        self.connect(bridge, "device")
        QtTest.QTest.qWait(100)
        self.assertEqual(self.controls["backend"].kinds, [1, 2])
        self.assertEqual(bridge.state.snapshot.generation, 0)
        self.assertIsNone(bridge.state.snapshot.config)
        self.assertEqual(bridge.captures, [])
        self.assertEqual(self.controls["settings"]["timeout"], 0)
        self.assertEqual(self.controls["settings"]["write_timeout"], 0.1)

    def test_negotiated_emulator_caps_reach_gui_without_opening_serial_or_arming(self):
        def forbidden():
            raise AssertionError("Serial factory called while displaying emulator CAPS")
        bridge = self.make(serial_factory=forbidden)
        panel = self.dialog(bridge)
        self.assertIn("CAPS не подтверждены", panel.device_details.text())
        self.connect(bridge)
        caps = bridge.state.snapshot.capabilities
        self.assertIsNotNone(caps)
        self.assertEqual((caps.firmware_build, caps.capability_bits, caps.mode_bits,
                          caps.source_bits, caps.edge_bits), (0, 0x1D, 7, 1, 3))
        text = panel.device_details.text()
        for expected in ("Эмулятор · синтетические данные", "0 (0x00000000)",
                         "wire 0.1 (draft)", "0x1d/0x7/0x1/0x3",
                         "Auto, Normal, Single", "предыстория: 0"):
            self.assertIn(expected, text)
        QtTest.QTest.qWait(80)
        self.assertIsNone(bridge.state.snapshot.config)
        self.assertEqual(bridge.state.snapshot.generation, 0)
        self.assertEqual(bridge.captures, [])

    def test_nondefault_negotiated_masks_build_and_pretrigger_reach_gui(self):
        # These alternate advertisements satisfy the existing session parser;
        # displaying them does not enable new GUI acquisition profiles.
        self.controls["reply_payloads"] = {
            129: struct.pack("<BBBBHHII", 0, 1, 1, 12, 512, 2048, 0xFEDCBA98, 0x37),
            130: struct.pack("<BBHIHHIHHBBBBHHH", 1, 0, 1, 72000000, 71, 19,
                             12000000, 143, 2048, 3, 5, 3, 2, 0, 512, 1024),
        }
        bridge = self.serial()
        panel = self.dialog(bridge)
        self.connect(bridge, "device")
        caps = bridge.state.snapshot.capabilities
        self.assertEqual((caps.firmware_build, caps.capability_bits, caps.mode_bits,
                          caps.source_bits, caps.edge_bits, caps.pretrigger_samples),
                         (0xFEDCBA98, 0x37, 5, 3, 2, (0, 512, 1024)))
        text = panel.device_details.text()
        for expected in ("Прибор · выбранный COM-порт", "4275878552 (0xfedcba98)",
                         "0x37/0x5/0x3/0x2", "Auto, Single", "фронты: спадающий",
                         "предыстория: 0, 512, 1024"):
            self.assertIn(expected, text)
        self.assertNotIn("Normal", text)
        self.assertNotIn("синтетические данные", text)
        self.assertEqual(self.controls["backend"].kinds, [1, 2])

    def test_real_recovery_clears_caps_but_keeps_diagnostic_through_state_polls(self):
        bridge = self.serial()
        panel = self.dialog(bridge)
        self.connect(bridge, "device")
        self.assertIn("CAPS приняты", panel.device_details.text())
        self.controls["read_fail"] = True
        self.wait(lambda: not bridge.state.connected and bool(bridge.errors))
        self.assertIs(bridge.state.snapshot.phase, Phase.RECOVERY)
        self.assertIsNotNone(bridge.state.snapshot.capabilities)
        # Session retains its old CAPS as diagnostics; the GUI must not.
        self.assertIn("CAPS не подтверждены", panel.device_details.text())
        self.assertNotIn("0x00000000", panel.device_details.text())
        for _ in range(3):
            bridge._drain()
            self.assertIn("соединение потеряно [TRANSPORT_LOST]", panel.status.text())
        self.assertNotIn("private backend detail", panel.status.text())
        self.assertFalse(panel.start_button.isEnabled())
        self.assertEqual(self.controls["backend"].kinds, [1, 2])
        self.assertEqual(sum(name == "open" for name, _ in self.calls), 1)

    def test_bad_hello_payload_and_incompatible_caps_report_actual_recovery_reason(self):
        cases = (
            ({129: b"bad"}, "PAYLOAD", "некорректные данные сообщения", [1]),
            ({130: bytes(30)}, "CAPABILITIES", "несовместимые возможности прибора", [1, 2]),
            ({129: struct.pack("<BBBBHHII", 0, 2, 1, 12, 512, 2048, 0, 0x1D)},
             "CAPABILITIES", "несовместимые возможности прибора", [1]),
        )
        for payloads, reason, explanation, commands in cases:
            with self.subTest(reason=reason, payloads=payloads):
                self.controls["reply_payloads"] = payloads
                bridge = self.serial()
                panel = self.dialog(bridge)
                self.assertTrue(bridge.connect_source("device", port="COM_TEST"))
                self.wait(lambda: bridge.state.snapshot is not None
                          and bridge.state.snapshot.phase is Phase.RECOVERY
                          and not bridge.state.connected and bool(bridge.errors))
                self.assertIn(f"{explanation} [{reason}]", panel.status.text())
                self.assertIn("CAPS не подтверждены", panel.device_details.text())
                self.assertNotIn("CAPS приняты", panel.device_details.text())
                self.assertEqual(self.controls["backend"].kinds, commands)
                self.assertEqual(bridge.captures, [])
                bridge.shutdown()
                self.wait(lambda: bridge._closed)

    def test_wire_version_diagnostic_currently_reaches_gui_as_decode_fault(self):
        bridge = self.serial()
        panel = self.dialog(bridge)
        self.connect(bridge, "device")
        # A valid CRC/COBS frame with unsupported wire 0.2, not random damage.
        raw = struct.pack("<BBBBHHQII", 0, 2, 134, 0, 0, 0,
                          bridge.state.snapshot.session, 3, 0)
        raw += struct.pack("<I", zlib.crc32(raw))
        encoded = b"".join(bytes((len(run) + 1,)) + run for run in raw.split(b"\0")) + b"\0"
        self.controls["read_bytes_once"] = encoded
        self.wait(lambda: not bridge.state.connected and bool(bridge.errors))
        self.assertIn("ошибка протокола [PROTOCOL_FAULT]", panel.status.text())
        self.assertIn("повреждённый кадр [DECODE]", panel.status.text())
        self.assertNotIn("[VERSION]", panel.status.text())
        self.assertIn("CAPS не подтверждены", panel.device_details.text())
        self.assertEqual(self.controls["backend"].kinds, [1, 2])

    def test_rejected_config_shows_numeric_reason_and_never_arms(self):
        self.controls["reject_config"] = 1
        bridge = self.serial()
        panel = self.dialog(bridge)
        self.connect(bridge, "device")
        self.assertTrue(bridge.start(Config()))
        self.wait(lambda: bool(bridge.errors) and self.ready(bridge))
        self.assertIn("Прибор отклонил команду (код 1)", panel.status.text())
        for _ in range(3):
            bridge._drain()
            self.assertIn("Прибор отклонил команду (код 1)", panel.status.text())
        self.assertIn("CAPS приняты", panel.device_details.text())
        self.assertEqual(self.controls["backend"].kinds, [1, 2, 3])
        self.assertEqual(bridge.captures, [])

    def test_terminal_command_rejection_keeps_numeric_reason_and_recovery_together(self):
        # GET_CAPS, STOP and RELEASE generate COMMAND_ERROR then RECOVERY in
        # one runtime event batch. The one-slot mailbox must retain both facts.
        for command, code in ((2, 5), (5, 8), (7, 4)):
            with self.subTest(command=command, code=code):
                self.controls["reject_commands"] = {command: code}
                self.controls["trigger"] = command == 7
                bridge = self.serial()
                panel = self.dialog(bridge)
                capture = None
                if command == 2:
                    self.assertTrue(bridge.connect_source("device", port="COM_TEST"))
                    expected_commands = [1, 2]
                else:
                    self.connect(bridge, "device")
                    if command == 5:
                        self.assertTrue(bridge.stop())
                        expected_commands = [1, 2, 5]
                    else:
                        capture = self.capture(bridge)
                        self.assertTrue(bridge.acknowledge(capture))
                        expected_commands = [1, 2, 3, 4, 7]
                self.wait(lambda: bridge.state.snapshot is not None
                          and bridge.state.snapshot.phase is Phase.RECOVERY
                          and not bridge.state.connected and bool(bridge.errors))
                for _ in range(3):
                    bridge._drain()
                    self.assertIn(f"Прибор отклонил команду (код {code})", panel.status.text())
                    self.assertIn("прибор сообщил об ошибке [DEVICE_ERROR]", panel.status.text())
                self.assertIn("CAPS не подтверждены", panel.device_details.text())
                self.assertFalse(panel.start_button.isEnabled())
                QtTest.QTest.qWait(60)
                self.assertEqual(self.controls["backend"].kinds, expected_commands)
                if capture is not None:
                    self.assertIs(bridge.captures[-1], capture)
                else:
                    self.assertEqual(bridge.captures, [])
                bridge.shutdown()
                self.wait(lambda: bridge._closed)

    def test_typed_recovery_event_preserves_version_fault_at_worker_boundary(self):
        # Formatting unit fixture only: existing runtime collapses byte decoder
        # diagnostics to DECODE, separately demonstrated by the test above.
        mailbox = _Mailbox()
        worker = _AcquisitionWorker(mailbox, lambda: None, lambda: 0, 10, 20)
        worker.staged_start = worker.arm_ready = worker.simulate_edge = True
        worker._events((Event(EventKind.RECOVERY, reason=RecoveryReason.PROTOCOL_FAULT,
                              fault=FaultCode.VERSION),))
        self.assertIn("ошибка протокола [PROTOCOL_FAULT]", mailbox.error)
        self.assertIn("несовместимая версия протокола [VERSION]", mailbox.error)
        self.assertIn("автоматического запуска нет", mailbox.error)
        self.assertFalse(worker.staged_start)
        self.assertFalse(worker.arm_ready)
        self.assertFalse(worker.simulate_edge)

    def test_finished_and_running_wait_for_nonblocking_native_join(self):
        bridge = self.make()
        self.connect(bridge)
        native_wait = bridge._thread.wait
        held = [True]
        attempts = []
        finished = []

        def delayed_native_join(timeout):
            self.assertEqual(timeout, 0)
            attempts.append(timeout)
            return False if held[0] else native_wait(timeout)

        bridge._thread.wait = delayed_native_join
        bridge.finished.connect(lambda: finished.append(True))
        self.assertTrue(bridge.shutdown())
        self.wait(lambda: len(attempts) >= 2)
        # Even though Qt has emitted QThread.finished, the public lifetime
        # contract does not permit deleting the owner before its native join.
        self.assertFalse(bridge._thread.isRunning())
        self.assertTrue(bridge.running)
        self.assertFalse(bridge._closed)
        self.assertEqual(finished, [])
        ticks = []
        QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
        self.wait(lambda: bool(ticks))
        held[0] = False
        self.wait(lambda: bool(finished))
        self.assertTrue(bridge._closed)
        self.assertFalse(bridge.running)
        self.assertTrue(native_wait(0))

    def test_lazy_shutdown_under_gc_and_deferred_delete_pressure(self):
        thresholds = gc.get_threshold()
        try:
            gc.set_threshold(5, 1, 1)
            for _ in range(30):
                bridge = self.make()
                finished = []
                bridge.finished.connect(lambda: finished.append(True))
                self.assertTrue(bridge.shutdown())
                self.assertFalse(bridge._closed)
                gc.collect()
                self.wait(lambda: bool(finished))
                self.assertTrue(bridge._closed)
                bridge.deleteLater()
                QtCore.QCoreApplication.sendPostedEvents(bridge, QtCore.QEvent.Type.DeferredDelete)
                self.bridges.remove(bridge)
        finally:
            gc.set_threshold(*thresholds)

    def test_normal_waits_without_edge_and_stop_does_not_resume(self):
        bridge = self.make(edge_delay_ms=30)
        self.connect(bridge)
        self.assertTrue(bridge.start(Config(), simulate_edge=False))
        self.wait(lambda: bridge.state.snapshot.phase is Phase.ARMED)
        QtTest.QTest.qWait(200)
        self.assertEqual(bridge.captures, [])
        self.assertTrue(bridge.stop())
        self.wait(lambda: self.ready(bridge))
        QtTest.QTest.qWait(100)
        self.assertEqual(bridge.captures, [])
        self.assertIs(bridge.state.snapshot.phase, Phase.READY)

    def test_auto_without_edge_emits_truthful_immutable_capture(self):
        bridge = self.make()
        self.connect(bridge)
        capture = self.capture(bridge, Config(mode=0, auto_timeout_ms=100), simulate_edge=False)
        self.assertEqual(capture.origin, "simulator")
        self.assertEqual(capture.trigger_kind, "auto_timeout")
        self.assertIsNone(capture.trigger_index)
        self.assertEqual(len(capture.samples), 2048)
        with self.assertRaises(FrozenInstanceError):
            capture.sequence = 999
        self.assertFalse(bridge.acknowledge(replace(capture)))
        QtTest.QTest.qWait(200)
        self.assertEqual(len(bridge.captures), 1)
        self.assertIs(bridge.pending_capture, capture)
        self.assertTrue(bridge.acknowledge(capture))
        self.assertFalse(bridge.acknowledge(capture))
        self.wait(lambda: len(bridge.captures) == 2)
        self.assertEqual(bridge.captures[-1].trigger_kind, "auto_timeout")

    def test_single_requires_another_start_and_new_generation(self):
        bridge = self.make(edge_delay_ms=30)
        self.connect(bridge)
        capture = self.capture(bridge, Config(mode=2, edge=1, level_code=1234))
        self.assertEqual(capture.samples[0], 1233)
        self.assertEqual(capture.trigger_edge, "falling")
        first_generation = bridge.state.snapshot.generation
        self.assertTrue(bridge.acknowledge(capture))
        self.wait(lambda: self.ready(bridge) and not bridge.state.has_capture)
        QtTest.QTest.qWait(150)
        self.assertEqual(len(bridge.captures), 1)
        self.capture(bridge, Config(mode=2))
        self.assertGreater(bridge.state.snapshot.generation, first_generation)

    def test_normal_continues_only_after_exact_ack_and_keeps_generation(self):
        bridge = self.make(edge_delay_ms=20)
        self.connect(bridge)
        capture = self.capture(bridge)
        generation = bridge.state.snapshot.generation
        for count in range(1, 4):
            QtTest.QTest.qWait(40)
            self.assertEqual(len(bridge.captures), count)
            self.assertEqual(bridge.state.snapshot.generation, generation)
            expected = [(73 * i + 19 * count + 7 * generation) & 4095 for i in range(2048)]
            expected[0], expected[511], expected[1023] = 2048, 0, 4095
            self.assertEqual(capture.samples, tuple(expected))
            if count < 3:
                self.assertTrue(bridge.acknowledge(capture))
                self.wait(lambda: len(bridge.captures) == count + 1)
                capture = bridge.captures[-1]

    def gate_idle_read(self, bridge):
        self.controls.update(read_entered=threading.Event(), read_gate=threading.Event(),
                             gate_idle_read=True)
        self.wait(self.controls["read_entered"].is_set)
        self.assertTrue(self.ready(bridge))
        with bridge._box.lock:
            self.assertTrue(bridge._box.executing)
            self.assertFalse(bridge._box.control_executing)

    def test_ready_start_queues_once_during_idle_backend_read(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        self.gate_idle_read(bridge)
        try:
            self.controls["trigger"] = True
            self.assertTrue(bridge.start(Config(mode=2)))
            queued = bridge._box.command
            bridge._drain()  # An older idle snapshot cannot hide queued intent.
            self.assertTrue(bridge.state.busy)
            for _ in range(100):
                self.assertFalse(bridge.start(Config(mode=2)))
                self.assertIs(bridge._box.command, queued)
            ticks = []
            QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
            self.wait(lambda: bool(ticks))
            self.assertTrue(bridge.state.busy)
            self.assertEqual(bridge.captures, [])
        finally:
            self.controls["read_gate"].set()
        self.wait(lambda: len(bridge.captures) == 1)
        self.assertTrue(bridge.acknowledge(bridge.captures[0]))
        self.wait(lambda: self.ready(bridge))
        kinds = self.controls["backend"].kinds
        self.assertEqual(kinds.count(3), 1)
        self.assertEqual(kinds.count(4), 1)
        QtTest.QTest.qWait(60)
        self.assertEqual(len(bridge.captures), 1)

    def test_priority_actions_cancel_start_queued_during_idle_read(self):
        for action in ("stop", "disconnect_source", "shutdown"):
            with self.subTest(action=action):
                bridge = self.serial()
                self.connect(bridge, "device")
                self.gate_idle_read(bridge)
                try:
                    self.assertTrue(bridge.start(Config(mode=2)))
                    self.assertTrue(getattr(bridge, action)())
                    self.assertIsNone(bridge._box.command)
                    self.assertFalse(bridge.start(Config(mode=2)))
                finally:
                    self.controls["read_gate"].set()
                if action == "stop":
                    self.wait(lambda: self.ready(bridge))
                elif action == "disconnect_source":
                    self.wait(lambda: not bridge.state.connected and not bridge.state.busy)
                else:
                    self.wait(lambda: bridge._closed and not bridge.running)
                kinds = self.controls["backend"].kinds
                self.assertEqual(kinds, [1, 2, 5] if action == "stop" else [1, 2])
                self.assertEqual(bridge.captures, [])
                if not bridge._closed:
                    bridge.shutdown()
                    self.wait(lambda: bridge._closed)

    def test_stop_cancels_queued_start_without_arm(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        self.assertTrue(bridge.start(Config()))
        self.assertTrue(bridge.stop())
        self.assertFalse(bridge.start(Config()))
        self.wait(lambda: self.ready(bridge))
        self.assertNotIn(4, self.controls["backend"].kinds)
        self.assertEqual(bridge.captures, [])

    def test_stop_cancels_config_ack_staged_start(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        self.controls["hold_config"] = True
        self.assertTrue(bridge.start(Config()))
        self.wait(lambda: 3 in self.controls["backend"].kinds)
        self.assertTrue(bridge.stop())
        self.controls["hold_config"] = False
        self.wait(lambda: self.ready(bridge))
        self.assertIn(5, self.controls["backend"].kinds)
        self.assertNotIn(4, self.controls["backend"].kinds)

    def test_stop_priority_defers_ack_then_never_sends_release(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        self.controls["trigger"] = True
        capture = self.capture(bridge)
        self.assertTrue(bridge.acknowledge(capture))
        self.assertTrue(bridge.stop())
        self.wait(lambda: self.ready(bridge) and not bridge.state.has_capture)
        kinds = self.controls["backend"].kinds
        self.assertIn(5, kinds)
        self.assertNotIn(7, kinds)
        QtTest.QTest.qWait(100)
        self.assertEqual(len(bridge.captures), 1)

    def test_slow_open_gui_responsive_commands_bounded_and_shutdown_async(self):
        self.controls["open_entered"] = threading.Event()
        self.controls["open_gate"] = threading.Event()
        bridge = self.serial()
        previous_idle_state = bridge.state
        self.assertTrue(bridge.connect_source("device", port="COM_TEST"))
        self.wait(self.controls["open_entered"].is_set)
        self.assertTrue(bridge._box.control_executing)
        with bridge._box.lock:
            bridge._box.state = previous_idle_state
        # Draining an older notification must not expose an in-flight control
        # as idle after its command has been removed from the mailbox.
        bridge._drain()
        self.assertTrue(bridge.state.busy)
        ticks = []
        QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
        self.wait(lambda: bool(ticks))
        for _ in range(1000):
            self.assertFalse(bridge.connect_source("device", port="COM_OTHER"))
            self.assertFalse(bridge.start(Config()))
        before = time.monotonic()
        self.assertTrue(bridge.shutdown())
        self.assertLess(time.monotonic() - before, 0.1)
        self.assertTrue(bridge.running)
        self.controls["open_gate"].set()
        self.wait(lambda: not bridge.running)
        self.assertEqual(self.controls["backend"].kinds, [])
        methods = {name for name, _ in self.calls}
        self.assertTrue({"create", "open", "close"} <= methods)
        self.assertEqual(len({ident for _, ident in self.calls}), 1)
        self.assertNotEqual(self.calls[0][1], threading.get_ident())

    def test_serial_full_io_and_signal_affinity(self):
        bridge = self.serial()
        received_threads = []
        bridge.capture_ready.connect(lambda _: received_threads.append(threading.get_ident()))
        self.connect(bridge, "device")
        self.controls["trigger"] = True
        capture = self.capture(bridge, Config(mode=2))
        self.assertEqual(capture.origin, "device")
        self.assertTrue(bridge.acknowledge(capture))
        self.wait(lambda: self.ready(bridge))
        bridge.shutdown()
        self.wait(lambda: not bridge.running)
        self.assertTrue({"create", "open", "read", "write", "close"} <= {name for name, _ in self.calls})
        self.assertEqual(len({ident for _, ident in self.calls}), 1)
        self.assertNotEqual(self.calls[0][1], threading.get_ident())
        self.assertEqual(received_threads, [threading.get_ident()])

    def test_blocked_gui_has_one_pending_capture_and_notification(self):
        bridge = self.make(edge_delay_ms=20)
        self.connect(bridge)
        self.assertTrue(bridge.start(Config()))
        time.sleep(0.25)  # Deliberately stall the consumer, not the worker.
        with bridge._box.lock:
            self.assertTrue(bridge._box.notified)
            self.assertIsNotNone(bridge._box.capture)
            self.assertIsNone(bridge._box.command)
            pending = bridge._box.capture
        time.sleep(0.1)
        with bridge._box.lock:
            self.assertIs(bridge._box.capture, pending)
        self.wait(lambda: len(bridge.captures) == 1)
        self.assertIs(bridge.captures[0], pending)

    def test_shutdown_discards_queued_ack_without_release_and_retains_capture(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        self.controls["trigger"] = True
        capture = self.capture(bridge)
        self.assertTrue(bridge.acknowledge(capture))
        self.assertTrue(bridge.shutdown())
        self.wait(lambda: not bridge.running)
        self.assertNotIn(7, self.controls["backend"].kinds)
        self.assertIs(bridge.pending_capture, capture)
        self.assertEqual(capture.samples[511], 0)

    def test_disconnect_retains_capture_blocks_reconnect_until_exact_ack(self):
        bridge = self.make(edge_delay_ms=20)
        self.connect(bridge)
        capture = self.capture(bridge)
        self.assertTrue(bridge.disconnect_source())
        self.wait(lambda: not bridge.state.connected and not bridge.state.busy)
        self.assertIs(bridge.pending_capture, capture)
        self.assertFalse(bridge.connect_source("emulator"))
        self.assertTrue(bridge.acknowledge(capture))
        self.wait(lambda: not bridge.state.has_capture)
        self.assertTrue(bridge.connect_source("emulator"))
        self.wait(lambda: self.ready(bridge))
        self.assertIsNone(bridge.state.snapshot.config)
        self.assertEqual(bridge.state.snapshot.generation, 0)

    def test_device_recovery_needs_physical_reset_assertion_and_never_autostarts(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        first_session = bridge.state.snapshot.session
        self.controls["read_fail"] = True
        self.wait(lambda: bridge.state.snapshot.phase is Phase.RECOVERY and not bridge.state.connected)
        self.assertTrue(bridge.state.reset_required)
        self.assertFalse(bridge.connect_source("device", port="COM_TEST"))
        self.controls["read_fail"] = False
        self.assertTrue(bridge.connect_source("device", port="COM_TEST", reset_confirmed=True))
        self.wait(lambda: self.ready(bridge))
        self.assertNotEqual(bridge.state.snapshot.session, first_session)
        self.assertEqual(self.controls["backend"].kinds, [1, 2])
        self.assertFalse(any("private backend" in error for error in bridge.errors))

    def test_unknown_release_write_preserves_retained_capture_without_retry(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        self.controls["trigger"] = True
        capture = self.capture(bridge)
        self.controls["fail_release"] = True
        self.assertTrue(bridge.acknowledge(capture))
        self.wait(lambda: bridge.state.snapshot.phase is Phase.RECOVERY and not bridge.state.connected)
        self.assertEqual(self.controls["backend"].kinds.count(7), 1)
        self.assertIs(bridge.captures[-1], capture)
        self.assertFalse(bridge.state.has_capture)  # Consumer retains the original.
        self.assertEqual(capture.samples[1023], 4095)

    def test_partial_disconnect_delivers_immutable_diagnostic_without_padding(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        self.controls.update(trigger=True, read_limit=1200)
        self.assertTrue(bridge.start(Config()))
        self.wait(lambda: bridge.state.snapshot.window_barrier)
        self.controls["read_fail"] = True
        self.wait(lambda: bool(bridge.incompletes))
        result = bridge.incompletes[-1]
        self.assertIsNone(result.samples)
        self.assertTrue(result.fragments)
        self.assertFalse(bridge.acknowledge_incomplete(replace(result)))
        self.assertTrue(bridge.acknowledge_incomplete(result))
        self.wait(lambda: not bridge.state.has_incomplete)

    def test_close_failure_keeps_worker_handle_for_explicit_retry(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        self.controls["close_fail"] = True
        finished = []
        bridge.finished.connect(lambda: finished.append(True))
        self.assertTrue(bridge.shutdown())
        self.wait(lambda: bridge.state.close_failed)
        self.assertTrue(bridge.running)
        self.assertEqual(finished, [])
        before = sum(name == "close" for name, _ in self.calls)
        QtTest.QTest.qWait(100)
        self.assertEqual(sum(name == "close" for name, _ in self.calls), before)
        self.controls["close_fail"] = False
        retry_states = []
        bridge.state_changed.connect(retry_states.append)
        self.assertTrue(bridge.shutdown())
        self.assertFalse(bridge.state.close_failed)
        self.assertFalse(retry_states[-1].close_failed)
        self.wait(lambda: bool(finished))
        self.assertFalse(bridge.running)
        self.assertFalse(bridge.state.close_failed)
        self.assertFalse(any(state.close_failed for state in retry_states))

    def test_emulator_never_constructs_serial_transport(self):
        def forbidden():
            raise AssertionError("Serial backend touched by emulator")
        bridge = self.make(serial_factory=forbidden, edge_delay_ms=20)
        self.connect(bridge)
        self.capture(bridge, Config(mode=2))
        self.assertEqual(bridge.errors, [])

    def test_ack_and_disconnect_same_turn_transfers_without_release(self):
        bridge = self.serial()
        self.connect(bridge, "device")
        self.controls["trigger"] = True
        capture = self.capture(bridge)
        self.assertTrue(bridge.acknowledge(capture))
        self.assertTrue(bridge.disconnect_source())
        self.wait(lambda: not bridge.state.connected and not bridge.state.busy)
        self.assertFalse(bridge.state.has_capture)
        self.assertNotIn(7, self.controls["backend"].kinds)
        self.assertIs(bridge.captures[-1], capture)

    def test_unacknowledged_hold_timeout_preserves_capture_until_consumer_accepts(self):
        now = [0]
        bridge = self.make(clock_ms=lambda: now[0], edge_delay_ms=20)
        self.connect(bridge)
        self.assertTrue(bridge.start(Config()))
        self.wait(lambda: bridge.state.snapshot.phase is Phase.ARMED)
        QtTest.QTest.qWait(30)
        now[0] = 50
        self.wait(lambda: bool(bridge.captures))
        capture = bridge.captures[-1]
        now[0] = 6000
        self.wait(lambda: bridge.state.snapshot.phase is Phase.RECOVERY and not bridge.state.connected)
        self.assertIs(bridge.pending_capture, capture)
        self.assertTrue(bridge.state.has_capture)
        self.assertEqual(len(bridge.captures), 1)
        self.assertTrue(bridge.acknowledge(capture))
        self.wait(lambda: not bridge.state.has_capture)
        self.assertIs(bridge.captures[-1], capture)

    def test_open_failure_cleanup_failure_can_be_explicitly_disconnected(self):
        self.controls.update(open_fail=True, close_fail=True)
        bridge = self.serial()
        self.assertTrue(bridge.connect_source("device", port="COM_TEST"))
        self.wait(lambda: bridge.state.close_failed and not bridge.state.busy)
        self.assertFalse(bridge.state.connected)
        self.assertFalse(bridge.connect_source("emulator"))
        self.controls["close_fail"] = False
        self.assertTrue(bridge.disconnect_source())
        self.wait(lambda: not bridge.state.close_failed and not bridge.state.busy)
        self.assertTrue(bridge.connect_source("emulator"))
        self.wait(lambda: self.ready(bridge))


if __name__ == "__main__":
    unittest.main()

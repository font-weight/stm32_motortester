"""Author GUI boundary checks with an explicit fake acquisition bridge."""
from dataclasses import replace
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets
from motortester.gui import MainWindow
from motortester.emulator import demo_message, from_message
from motortester.session import SessionController, Phase


class FakeBridge(QtCore.QObject):
    state_changed = QtCore.Signal(object)
    capture_ready = QtCore.Signal(object)
    incomplete_ready = QtCore.Signal(object)
    error = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.calls = []
        self.viewer = parent
        self.accept = True

    def connect_source(self, source, **kwargs):
        self.calls.append(("connect", source, kwargs))
        return self.accept

    def start(self, settings, **kwargs):
        self.calls.append(("start", settings, kwargs))
        return self.accept

    def stop(self):
        self.calls.append(("stop",))
        return self.accept

    def disconnect_source(self):
        self.calls.append(("disconnect",))
        return self.accept

    def acknowledge(self, capture):
        if self.viewer.capture is not capture:
            raise AssertionError("GUI must retain the exact capture before ACK")
        self.calls.append(("ack", capture))
        return self.accept

    def acknowledge_incomplete(self, result):
        if self.viewer.incomplete_capture is not result:
            raise AssertionError("GUI must retain incomplete result before ACK")
        self.calls.append(("incomplete_ack", result))
        return self.accept

    def shutdown(self):
        self.calls.append(("shutdown",))
        return True

    def emit_state(self, phase, **kwargs):
        snapshot = replace(SessionController().snapshot(), phase=phase)
        state = SimpleNamespace(snapshot=snapshot, connected=phase is not Phase.IDLE,
                                source="emulator", busy=False, start_pending=False,
                                closing=False, reset_required=False, close_failed=False)
        for key, value in kwargs.items():
            setattr(state, key, value)
        self.state_changed.emit(state)


class AcquisitionGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.fake_module = patch.dict(sys.modules, {"motortester.acquisition": SimpleNamespace(AcquisitionBridge=FakeBridge)})
        self.fake_module.start()
        self.window = MainWindow()
        self.window.show()
        self.window.show_acquisition()
        self.bridge = self.window._acquisition
        self.dialog = self.window._acquisition_dialog
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.bridge.finished.emit()
        self.app.processEvents()
        self.window.deleteLater()
        self.app.processEvents()
        self.fake_module.stop()

    def test_connect_never_arms_and_repeated_connect_is_disabled(self):
        self.dialog.connect_button.click()
        self.assertEqual([c[0] for c in self.bridge.calls], ["connect"])
        self.assertFalse(self.dialog.connect_button.isEnabled())
        self.assertFalse(self.window.open_button.isEnabled())
        self.dialog.connect_button.click()
        self.assertEqual(len(self.bridge.calls), 1)

    def test_explicit_modes_settings_and_no_physical_reset_claim(self):
        self.bridge.emit_state(Phase.READY)
        self.dialog.mode.setCurrentIndex(0)
        self.dialog.edge.setCurrentIndex(1)
        self.dialog.level.setValue(1234)
        self.dialog.timeout.setValue(321)
        self.dialog.simulate.setChecked(False)
        self.dialog.start_button.click()
        call = self.bridge.calls[-1]
        self.assertEqual(call[0], "start")
        self.assertEqual((call[1].mode, call[1].edge, call[1].level_code, call[1].auto_timeout_ms), (0, 1, 1234, 321))
        self.assertFalse(call[2]["simulate_edge"])
        self.assertFalse(self.dialog.start_button.isEnabled())

    def test_capture_retained_before_ack_then_stop_enables_save(self):
        self.bridge.emit_state(Phase.ARMED)
        capture = from_message(demo_message())
        self.bridge.capture_ready.emit(capture)
        self.assertIs(self.window.capture, capture)
        self.assertEqual(self.bridge.calls[-1], ("ack", capture))
        self.assertFalse(self.window.save_button.isEnabled())
        self.dialog.stop_button.click()
        self.assertEqual(self.bridge.calls[-1][0], "stop")
        self.bridge.emit_state(Phase.READY)
        self.assertTrue(self.window.save_button.isEnabled())

    def test_plot_failure_does_not_ack(self):
        self.bridge.emit_state(Phase.ARMED)
        capture = from_message(demo_message())
        with patch.object(self.window, "show_capture", side_effect=ValueError("plot failed")):
            self.bridge.capture_ready.emit(capture)
        self.assertNotIn("ack", [c[0] for c in self.bridge.calls])
        self.assertEqual(self.bridge.calls[-1], ("stop",))

    def test_incomplete_does_not_replace_current_full_capture(self):
        full = from_message(demo_message())
        self.window.show_capture(full)
        incomplete = object()
        self.bridge.incomplete_ready.emit(incomplete)
        self.assertIs(self.window.capture, full)
        self.assertIs(self.window.incomplete_capture, incomplete)
        self.assertEqual(self.bridge.calls[-1], ("incomplete_ack", incomplete))

    def test_incomplete_ack_retries_after_disconnect_barrier_only(self):
        full = from_message(demo_message())
        self.window.show_capture(full)
        incomplete = object()
        self.bridge.accept = False
        self.bridge.incomplete_ready.emit(incomplete)
        self.assertIs(self.window._incomplete_waiting_for_safe_ack, incomplete)
        self.bridge.emit_state(Phase.RECOVERY, busy=True)
        self.assertEqual(sum(c[0] == "incomplete_ack" for c in self.bridge.calls), 1)
        self.bridge.accept = True
        self.bridge.emit_state(Phase.RECOVERY, connected=False)
        self.assertEqual(sum(c[0] == "incomplete_ack" for c in self.bridge.calls), 2)
        self.assertIsNone(self.window._incomplete_waiting_for_safe_ack)
        self.assertIs(self.window.incomplete_capture, incomplete)
        self.assertIs(self.window.capture, full)
        self.assertNotIn("connect", [c[0] for c in self.bridge.calls])
        self.assertNotIn("start", [c[0] for c in self.bridge.calls])

    def test_hide_panel_keeps_worker_and_main_close_waits_for_exit(self):
        self.dialog.close()
        self.assertEqual(self.bridge.calls, [])
        self.window.close()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.bridge.calls[-1], ("shutdown",))
        self.bridge.finished.emit()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_recovery_retains_capture_and_cannot_start(self):
        capture = from_message(demo_message())
        self.window.show_capture(capture)
        self.bridge.emit_state(Phase.RECOVERY, reset_required=True)
        self.assertIs(self.window.capture, capture)
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertTrue(self.window.save_button.isEnabled())
        self.assertIn("сброс", self.dialog.status.text())

    def test_missing_explicit_port_never_opens_device(self):
        self.dialog.source.setCurrentIndex(1)
        self.dialog.connect_button.click()
        self.assertEqual(self.bridge.calls, [])
        self.assertIn("COM", self.dialog.status.text())

    def test_file_action_calls_are_guarded_while_acquiring(self):
        capture = from_message(demo_message())
        self.window.show_capture(capture)
        self.bridge.emit_state(Phase.ARMED)
        self.window.generate_demo()
        self.window.open_path("must-not-open.mtcap")
        self.window.save_path("must-not-save.mtcap")
        self.assertIs(self.window.capture, capture)
        self.assertFalse(self.window.busy)

"""Author GUI boundary checks with an explicit fake acquisition bridge."""
from dataclasses import replace
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets
from motortester.gui import MainWindow
from motortester.emulator import demo_message, from_message
from motortester.session import Capabilities, SessionController, Phase


def advertised_caps(**changes):
    """Presentation fixture; session-parser admission is checked separately."""
    return replace(Capabilities(
        firmware_build=0x1234ABCD, capability_bits=0x3F, profile=1,
        timer_clock_hz=72000000, timer_psc=71, timer_arr=19,
        adc_clock_hz=12000000, adc_sample_cycles_x2=143,
        window_samples=2048, pretrigger_samples=(0, 512, 1024),
        mode_bits=7, source_bits=3, edge_bits=3), **changes)


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

    def emit_state(self, phase, *, capabilities=None, **kwargs):
        snapshot = replace(SessionController().snapshot(), phase=phase,
                           capabilities=capabilities)
        state = SimpleNamespace(snapshot=snapshot, connected=phase is not Phase.IDLE,
                                source="emulator", busy=False, start_pending=False,
                                closing=False, reset_required=False, close_failed=False)
        for key, value in kwargs.items():
            setattr(state, key, value)
        self.state_changed.emit(state)
        return state


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

    def test_current_caps_show_identity_limits_raw_masks_and_nominal_timing(self):
        caps = advertised_caps()
        self.bridge.emit_state(Phase.READY, capabilities=caps)
        text = self.dialog.device_details.text()
        for expected in (
            "Эмулятор · синтетические данные", "CAPS приняты в этом сеансе",
            "wire 0.1 (draft)", "305441741 (0x1234abcd)",
            "это не версия SemVer и не SHA", "Серийный номер",
            "не передаются этим контрактом", "Профиль 1", "2048 отсчётов",
            "предыстория: 0, 512, 1024", "Auto, Normal, Single",
            "нарастающий, спадающий", "0x3f/0x7/0x3/0x3",
            "72000000 Гц", "PSC=71", "ARR=19", "12000000 Гц",
            "sample_cycles_x2=143", "точность и безопасность не подтверждены",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertEqual(self.bridge.calls, [])

    def test_caps_renderer_uses_advertised_fields_without_inventing_modes(self):
        caps = advertised_caps(
            firmware_build=0xFFFFFFFF, capability_bits=0x16, profile=9,
            timer_clock_hz=36000000, timer_psc=35, timer_arr=39,
            adc_clock_hz=6000000, adc_sample_cycles_x2=111,
            window_samples=1024, pretrigger_samples=(123,),
            mode_bits=5, source_bits=2, edge_bits=2)
        self.bridge.emit_state(Phase.ARMED, capabilities=caps, source="device")
        text = self.dialog.device_details.text()
        for expected in (
            "Прибор · выбранный COM-порт", "4294967295 (0xffffffff)",
            "Профиль 9", "1024 отсчётов", "предыстория: 123",
            "Режимы: Auto, Single; фронты: спадающий", "0x16/0x5/0x2/0x2",
            "36000000 Гц", "PSC=35", "ARR=39", "6000000 Гц",
            "sample_cycles_x2=111",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertNotIn("Normal", text)
        self.assertNotIn("синтетические данные", text)
        self.assertNotIn("нарастающий", text)
        self.assertNotIn("72000000", text)
        self.assertEqual(self.bridge.calls, [])

    def test_empty_mode_and_edge_masks_do_not_claim_support(self):
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps(
            firmware_build=0, capability_bits=0, mode_bits=0,
            source_bits=0, edge_bits=0))
        text = self.dialog.device_details.text()
        self.assertIn("0 (0x00000000)", text)
        self.assertIn("Режимы: нет; фронты: нет", text)
        self.assertIn("0x0/0x0/0x0/0x0", text)

    def test_details_use_connected_source_not_unsubmitted_dropdown(self):
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps(), source="device")
        self.assertEqual(self.dialog.source.currentData(), "emulator")
        self.assertIn("Прибор · выбранный COM-порт", self.dialog.device_details.text())
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps(), source="emulator")
        self.dialog.source.setCurrentIndex(1)
        self.assertIn("Эмулятор · синтетические данные", self.dialog.device_details.text())
        self.assertEqual(self.bridge.calls, [])

    def test_unknown_source_and_missing_snapshot_remain_explicitly_unknown(self):
        for source in (None, "unknown", "<b>untrusted</b>"):
            with self.subTest(source=source):
                self.bridge.emit_state(Phase.IDLE, source=source, snapshot=None)
                text = self.dialog.device_details.text()
                self.assertIn("Источник не выбран", text)
                self.assertIn("CAPS не подтверждены", text)
                self.assertIn("Ожидаемый формат: wire 0.1 (draft)", text)
                self.assertIn("пока неизвестны", text)
                self.assertNotIn("untrusted", text)

    def test_stale_caps_are_cleared_in_every_noncurrent_connection_state(self):
        caps = advertised_caps()
        cases = [
            (phase, {"connected": True, "capabilities": caps})
            for phase in (Phase.IDLE, Phase.HELLO, Phase.CAPS, Phase.RECOVERY)
        ] + [
            (phase, {"connected": False, "capabilities": caps})
            for phase in (Phase.READY, Phase.ARMED, Phase.RECOVERY)
        ] + [
            (Phase.READY, {"connected": True, "capabilities": None}),
            (Phase.READY, {"connected": True, "snapshot": None}),
        ]
        for phase, changes in cases:
            with self.subTest(phase=phase, changes=changes):
                self.bridge.emit_state(Phase.READY, capabilities=caps)
                self.assertIn("0x1234abcd", self.dialog.device_details.text())
                self.bridge.emit_state(phase, **changes)
                text = self.dialog.device_details.text()
                self.assertIn("CAPS не подтверждены", text)
                for stale in ("0x1234abcd", "Профиль 1", "72000000", "Auto, Normal, Single"):
                    self.assertNotIn(stale, text)
        self.assertEqual(self.bridge.calls, [])

    def test_new_session_replaces_old_device_build_and_capabilities(self):
        self.bridge.emit_state(Phase.READY, source="device", capabilities=advertised_caps())
        self.bridge.emit_state(Phase.HELLO, source="emulator")
        self.assertNotIn("0x1234abcd", self.dialog.device_details.text())
        self.bridge.emit_state(Phase.READY, source="emulator", capabilities=advertised_caps(
            firmware_build=0, capability_bits=9, mode_bits=2, source_bits=1))
        text = self.dialog.device_details.text()
        self.assertIn("Эмулятор · синтетические данные", text)
        self.assertIn("0 (0x00000000)", text)
        self.assertIn("Режимы: Normal", text)
        self.assertNotIn("0x1234abcd", text)
        self.assertNotIn("Прибор · выбранный COM-порт", text)

    def test_bridge_error_is_plain_text_and_survives_recovery_state_polls(self):
        diagnostic = "Несовместимая версия [VERSION] <b>literal</b> & detail"
        self.bridge.error.emit(diagnostic)
        self.assertEqual(self.dialog.status.textFormat(), QtCore.Qt.TextFormat.PlainText)
        self.assertEqual(self.dialog.device_details.textFormat(), QtCore.Qt.TextFormat.PlainText)
        self.assertTrue(self.dialog.device_details.textInteractionFlags()
                        & QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        for connected in (True, False, False):
            self.bridge.emit_state(Phase.RECOVERY, connected=connected, reset_required=True)
            text = self.dialog.status.text()
            self.assertIn(diagnostic, text)
            self.assertEqual(text.count(diagnostic), 1)
            self.assertIn("физический сброс", text)
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertEqual(self.bridge.calls, [])

    def test_later_error_replaces_previous_without_accumulating_history(self):
        self.bridge.error.emit("First diagnostic")
        self.bridge.error.emit("Latest diagnostic")
        self.bridge.emit_state(Phase.READY)
        self.assertNotIn("First diagnostic", self.dialog.status.text())
        self.assertIn("Latest diagnostic", self.dialog.status.text())

    def test_error_resets_only_on_accepted_request_or_new_hello(self):
        self.bridge.emit_state(Phase.READY)
        self.bridge.error.emit("Old diagnostic")
        self.dialog.stop_button.click()
        self.assertEqual(self.bridge.calls[-1], ("stop",))
        self.bridge.emit_state(Phase.READY)
        self.assertNotIn("Old diagnostic", self.dialog.status.text())
        self.bridge.error.emit("Before reconnect")
        self.bridge.emit_state(Phase.HELLO)
        self.assertNotIn("Before reconnect", self.dialog.status.text())
        self.bridge.error.emit("During handshake")
        self.bridge.emit_state(Phase.HELLO)
        self.bridge.emit_state(Phase.CAPS)
        self.assertIn("During handshake", self.dialog.status.text())

    def test_rejected_request_keeps_its_explanation_after_state_poll(self):
        self.bridge.emit_state(Phase.READY)
        self.bridge.accept = False
        self.dialog.start_button.click()
        self.bridge.emit_state(Phase.READY)
        self.assertIn("Команда не принята", self.dialog.status.text())
        self.assertEqual([call[0] for call in self.bridge.calls], ["start"])

    def test_caps_details_and_actions_remain_reachable_at_small_viewport(self):
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps())
        self.dialog.resize(420, 360)
        self.app.processEvents()
        scroll = self.dialog.findChild(QtWidgets.QScrollArea)
        self.assertIsNotNone(scroll)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        for widget in (self.dialog.device_details, self.dialog.connect_button,
                       self.dialog.start_button, self.dialog.stop_button,
                       self.dialog.disconnect_button, self.dialog.status):
            with self.subTest(widget=widget.objectName() or widget.text()):
                scroll.ensureWidgetVisible(widget, 0, 0)
                self.app.processEvents()
                center = widget.mapTo(scroll.viewport(), widget.rect().center())
                self.assertTrue(scroll.viewport().rect().contains(center))
        self.assertEqual(self.bridge.calls, [])

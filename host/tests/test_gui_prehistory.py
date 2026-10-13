"""Prehistory choices and reported STATUS presentation on synthetic Qt state."""
from dataclasses import replace
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from PySide6 import QtWidgets
from motortester.gui import MainWindow
from motortester.gui_acquisition import reported_device_status_text
from motortester.session import Config, Phase, SessionController
from test_gui_acquisition import FakeBridge, advertised_caps


class RecordFakeBridge(FakeBridge):
    def record(self, settings, path, **kwargs):
        self.calls.append(("record", settings, path, kwargs))
        return self.accept


class PrehistoryGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.fake_module = patch.dict(sys.modules, {
            "motortester.acquisition": SimpleNamespace(AcquisitionBridge=RecordFakeBridge)})
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

    def choices(self):
        return tuple(self.dialog.pretrigger.itemData(i)
                     for i in range(self.dialog.pretrigger.count()))

    def test_disconnected_unknown_and_unsupported_disable_nonzero_choices(self):
        self.assertEqual(self.choices(), (0,))
        self.assertFalse(self.dialog.pretrigger.isEnabled())
        for phase in (Phase.HELLO, Phase.CAPS, Phase.RECOVERY, Phase.IDLE):
            self.bridge.emit_state(phase, capabilities=advertised_caps())
            self.assertEqual(self.choices(), (0,))
            self.assertFalse(self.dialog.pretrigger.isEnabled())
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps(
            capability_bits=0x1D, pretrigger_samples=(0,)))
        self.assertEqual(self.choices(), (0,))
        self.assertFalse(self.dialog.pretrigger.isEnabled())
        self.assertIn("только нулевую", self.dialog.pretrigger_status.text())
        self.assertEqual(self.bridge.calls, [])

    def test_capability_and_config_dispatch_for_start_and_record(self):
        for action in ("start", "record"):
            for count in (0, 512, 1024):
                with self.subTest(action=action, count=count):
                    self.bridge.emit_state(Phase.READY, capabilities=advertised_caps())
                    self.assertEqual(self.choices(), (0, 512, 1024))
                    self.assertTrue(self.dialog.pretrigger.isEnabled())
                    self.dialog.pretrigger.setCurrentIndex(self.dialog.pretrigger.findData(count))
                    getattr(self.dialog, action + "_button").click()
                    self.assertEqual(self.bridge.calls[-1][0], action)
                    self.assertEqual(self.bridge.calls[-1][1].pretrigger_samples, count)
                    self.assertFalse(self.dialog.pretrigger.isEnabled())
                    self.assertTrue(self.dialog.stop_button.isEnabled())

    def test_active_and_pending_lock_choice_without_losing_value(self):
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps())
        self.dialog.pretrigger.setCurrentIndex(2)
        for phase, changes in ((Phase.ARMED, {}), (Phase.READY, {"busy": True}),
                               (Phase.READY, {"start_pending": True}),
                               (Phase.READY, {"closing": True})):
            self.bridge.emit_state(phase, capabilities=advertised_caps(), **changes)
            self.assertFalse(self.dialog.pretrigger.isEnabled())
            self.assertEqual(self.dialog.pretrigger.currentData(), 1024)
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps())
        self.assertTrue(self.dialog.pretrigger.isEnabled())
        self.assertEqual(self.dialog.pretrigger.currentData(), 1024)
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps(), connected=False)
        self.assertEqual(self.choices(), (0,))
        self.assertFalse(self.dialog.pretrigger.isEnabled())

    def test_reconnect_drops_unsupported_selection_and_ack_is_separate(self):
        caps = advertised_caps()
        self.bridge.emit_state(Phase.READY, capabilities=caps)
        self.dialog.pretrigger.setCurrentIndex(1)
        snapshot = replace(SessionController().snapshot(), phase=Phase.READY,
                           capabilities=caps, config=Config(pretrigger_samples=1024))
        self.bridge.emit_state(Phase.READY, snapshot=snapshot)
        self.assertEqual(self.dialog.pretrigger.currentData(), 512)
        self.assertIn("Принято по CONFIG_ACK: 1024 отсчётов (50%)",
                      self.dialog.pretrigger_status.text())
        self.bridge.emit_state(Phase.HELLO)
        self.assertNotIn("Принято", self.dialog.pretrigger_status.text())
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps(
            capability_bits=0x1D, pretrigger_samples=(0,)))
        self.assertEqual(self.dialog._settings().pretrigger_samples, 0)

    def test_missing_bit_does_not_enable_unadvertised_prehistory(self):
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps(capability_bits=0x1D))
        self.assertEqual(self.choices(), (0,))
        self.assertFalse(self.dialog.pretrigger.isEnabled())

    def test_reported_device_state_never_inferred_from_host_phase(self):
        for phase in Phase:
            self.bridge.emit_state(phase)
            self.assertIn("ещё не сообщено", self.dialog.device_status.text())
        names = ("остановлен", "подготовка", "ожидание запуска", "захват",
                 "передача", "ошибка", "ожидание подтверждения окна")
        for value, name in enumerate(names):
            snapshot = SimpleNamespace(device_status=SimpleNamespace(state=value, reason=0),
                                       device_status_stale=False)
            state = SimpleNamespace(snapshot=snapshot, connected=True)
            text = reported_device_status_text(state)
            self.assertIn(name, text)
            self.assertIn(f"код {value}", text)
            self.assertIn("не независимая проверка", text)
            snapshot.device_status_stale = True
            self.assertIn("устарело", reported_device_status_text(state))
            state.connected = False
            self.assertIn("Соединение закрыто", reported_device_status_text(state))
        snapshot.device_status = SimpleNamespace(state=5, reason=3)
        self.assertIn("ошибка (код 5); причина 3", reported_device_status_text(state))


if __name__ == "__main__":
    unittest.main()

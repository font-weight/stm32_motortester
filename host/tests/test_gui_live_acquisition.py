"""Real Qt + bounded worker + byte-emulator author integration checks."""
import tempfile
import time
import unittest
from pathlib import Path
from PySide6 import QtCore, QtWidgets
from motortester.gui import MainWindow
from motortester import recording


class LiveAcquisitionGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.window = MainWindow()
        self.window.show()
        self.window.show_acquisition()
        self.dialog = self.window._acquisition_dialog
        self.bridge = self.window._acquisition
        self.captures = []
        self.bridge.capture_ready.connect(self.captures.append)
        self.app.processEvents()

    def wait(self, condition, seconds=6):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                return
            time.sleep(.003)
        self.fail(f"Timed out: {self.dialog.status.text()} / {self.bridge.state}")

    def settle(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.003)

    def connect(self):
        self.dialog.connect_button.click()
        self.wait(self.dialog.start_button.isEnabled)
        self.assertIsNone(self.window.capture)

    def tearDown(self):
        self.window.close()
        self.wait(lambda: not self.bridge.running)
        self.app.processEvents()
        self.window.deleteLater()
        self.app.processEvents()

    def test_single_requires_new_start_and_saves_reopens_exactly(self):
        self.connect()
        self.dialog.mode.setCurrentIndex(2)
        self.dialog.start_button.click()
        self.wait(lambda: len(self.captures) == 1)
        self.wait(self.dialog.start_button.isEnabled)
        self.settle(.3)
        self.assertEqual(len(self.captures), 1)
        first = self.window.capture
        self.assertEqual(len(first.samples), 2048)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "реальный-байтовый-путь.mtcap"
            self.window.save_path(path)
            self.wait(lambda: not self.window.busy)
            self.assertEqual(recording.load(path), first)
            self.window.open_path(path)
            self.wait(lambda: not self.window.busy)
            self.assertEqual(self.window.capture, first)
            self.assertIsNot(self.window.capture, first)
        self.dialog.edge.setCurrentIndex(1)
        self.dialog.start_button.click()
        self.wait(lambda: len(self.captures) == 2)
        self.wait(self.dialog.start_button.isEnabled)
        self.assertEqual(self.window.capture.trigger_edge, "falling")

    def test_normal_without_edge_stops_without_fabricated_capture(self):
        self.connect()
        self.dialog.simulate.setChecked(False)
        self.dialog.start_button.click()
        self.wait(lambda: self.bridge.state.snapshot.phase.value == "ARMED")
        self.settle(.3)
        self.assertEqual(self.captures, [])
        self.dialog.stop_button.click()
        self.wait(self.dialog.start_button.isEnabled)
        self.assertIsNone(self.window.capture)

    def test_auto_timeout_has_no_trigger_marker_and_stop_retains_window(self):
        self.connect()
        self.dialog.mode.setCurrentIndex(0)
        self.dialog.timeout.setValue(100)
        self.dialog.simulate.setChecked(False)
        self.dialog.start_button.click()
        self.wait(lambda: bool(self.captures))
        self.dialog.stop_button.click()
        self.wait(self.dialog.start_button.isEnabled)
        capture = self.window.capture
        self.assertEqual(capture.trigger_kind, "auto_timeout")
        self.assertIsNone(capture.trigger_index)
        self.assertFalse(self.window.trigger_line.isVisible())
        self.assertTrue(self.window.save_button.isEnabled())
        count = len(self.captures)
        self.settle(.2)
        self.assertEqual(len(self.captures), count)
        self.assertIs(self.window.capture, capture)

    def test_hide_panel_does_not_disconnect_and_close_main_exits_worker(self):
        self.connect()
        self.dialog.close()
        self.app.processEvents()
        self.assertTrue(self.bridge.active)
        self.window.show_acquisition()
        self.assertIs(self.window._acquisition, self.bridge)
        self.dialog.start_button.click()
        self.window.close()
        self.wait(lambda: not self.bridge.running)
        self.wait(lambda: not self.window.isVisible())
        self.assertTrue(self.window._acq_shutdown_complete)

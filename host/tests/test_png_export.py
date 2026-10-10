"""Real PNG rendering and failure paths; no hardware/Windows claim."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
try:
    from PySide6 import QtCore, QtGui, QtTest, QtWidgets
    from motortester.gui import MainWindow
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI dependencies unavailable: {exc}") from exc
from motortester.model import CaptureError


class PngExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def settle(self):
        end = time.monotonic() + 5
        while self.window.busy and time.monotonic() < end:
            QtTest.QTest.qWait(10)
        self.assertFalse(self.window.busy)
        self.app.processEvents()

    def tearDown(self):
        self.settle()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.tmp.cleanup()

    def test_no_recording_does_not_create_image(self):
        self.assertFalse(self.window.png_button.isEnabled())
        with self.assertRaises(CaptureError):
            self.window.png_image()
        self.window.export_png_path(self.folder / "empty.png")
        self.assertFalse((self.folder / "empty.png").exists())

    def test_png_from_current_view_keeps_raw_and_file_identity(self):
        self.window.generate_demo()
        original = self.window.capture
        self.window.cursor_toggle.setChecked(True)
        self.window.plot.setXRange(-2, 5, padding=0)
        self.app.processEvents()
        before_range = self.window.plot.viewRange()
        path = self.folder / "осциллограмма.png"
        self.window.export_png_path(path)
        self.settle()
        image = QtGui.QImage(str(path))
        self.assertFalse(image.isNull())
        self.assertGreater(image.height(), self.window.plot.grab().height())
        self.assertEqual(image.width(), self.window.plot.grab().width())
        self.assertIn("синтетические", image.text("Provenance"))
        self.assertIn("uncalibrated", image.text("Scale"))
        self.assertEqual(image.text("RawSampleCount"), "2048")
        self.assertIn(".mtcap", image.text("Purpose"))
        self.assertIs(self.window.capture, original)
        self.assertIsNone(self.window.current_path)
        self.assertEqual(self.window.plot.viewRange(), before_range)
        self.assertIn("иллюстрация", self.window.status_label.text())

    def test_overwrite_requires_explicit_permission_and_handles_failure(self):
        self.window.generate_demo()
        path = self.folder / "existing.png"
        path.write_bytes(b"original content")
        self.window.export_png_path(path)
        self.settle()
        self.assertEqual(path.read_bytes(), b"original content")
        self.assertIn("Не удалось", self.window.status_label.text())
        with patch("motortester.recording.os.replace", side_effect=OSError("read-only")):
            self.window.export_png_path(path, overwrite=True)
            self.settle()
        self.assertEqual(path.read_bytes(), b"original content")
        self.window.export_png_path(path, overwrite=True)
        self.settle()
        self.assertFalse(QtGui.QImage(str(path)).isNull())

    def test_cancel_and_rejected_overwrite_leave_state_unchanged(self):
        self.window.generate_demo()
        original = self.window.capture
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.window.png_dialog()
        self.assertIn("отменено", self.window.status_label.text())
        path = self.folder / "existing.png"
        path.write_bytes(b"original")
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(path), "")), patch.object(self.window, "_confirm_overwrite", return_value=False):
            self.window.png_dialog()
        self.assertEqual(path.read_bytes(), b"original")
        self.assertIs(self.window.capture, original)
        self.assertFalse(self.window.busy)

    def test_wrong_extension_and_missing_directory_are_controlled_errors(self):
        self.window.generate_demo()
        original = self.window.capture
        self.window.export_png_path(self.folder / "recording.mtcap")
        self.assertIn("расширением .png", self.window.status_label.text())
        self.assertFalse((self.folder / "recording.mtcap").exists())
        self.window.export_png_path(self.folder / "missing" / "image.png")
        self.settle()
        self.assertIn("Не удалось", self.window.status_label.text())
        self.assertIs(self.window.capture, original)

    def test_long_unbroken_quality_flags_fit_actual_png_text_rectangles(self):
        self.window.generate_demo()
        capture = replace(self.window.capture,
                          quality_flags=tuple(f"flag{i}_" + "Ж" * 57 for i in range(16)))
        self.window.show_capture(capture)
        self.window.resize(self.window.minimumSize())
        self.app.processEvents()
        text_bounds = []
        draw_text = QtGui.QPainter.drawText

        def record_text(painter, *args):
            if len(args) == 3 and isinstance(args[0], QtCore.QRect) and isinstance(args[2], str):
                rect, flags, text = args
                bounds = painter.fontMetrics().boundingRect(rect, flags, text)
                text_bounds.append((text, rect, bounds))
            return draw_text(painter, *args)

        with patch.object(QtGui.QPainter, "drawText", new=record_text):
            image = self.window.png_image()
        self.assertFalse(image.isNull())
        footer = next(item for item in text_bounds if "flag15_" in item[0])
        self.assertLessEqual(footer[2].width(), footer[1].width())
        self.assertLessEqual(footer[2].height(), footer[1].height())
        self.assertIs(self.window.capture, capture)

    def test_repeated_export_device_provenance_and_quality(self):
        self.window.generate_demo()
        c = replace(self.window.capture, origin="device", samples=(0, 4095),
                    trigger_index=0, pretrigger_samples=0, quality_flags=("timing_unverified",))
        self.window.show_capture(c)
        self.app.processEvents()
        image = self.window.png_image()
        self.assertIn("происхождение не проверено", image.text("Provenance"))
        self.assertNotIn("синтетические", image.text("Provenance"))
        for number in range(3):
            path = self.folder / f"view{number}.png"
            self.window.export_png_path(path)
            self.settle()
            self.assertFalse(QtGui.QImage(str(path)).isNull())
        self.assertEqual(self.window.capture.samples, (0, 4095))


if __name__ == "__main__":
    unittest.main()

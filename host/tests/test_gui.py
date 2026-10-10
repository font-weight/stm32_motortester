"""Real Qt widget checks; synthetic data on the OS running this suite only.

Run with QT_QPA_PLATFORM=offscreen for a headless host. This is not a Windows
package test. File dialogs are substituted; buttons, events, threads and the
plot are real Qt objects.
"""
import csv
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    import numpy as np
    from PySide6 import QtCore, QtGui, QtTest, QtWidgets
    from motortester.gui import MainWindow, screenshot
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI dependencies unavailable: {exc}") from exc

from motortester.emulator import demo_message, from_message
from motortester.model import MAX_SAMPLES
from motortester import recording


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="motortester-qt-")
        self.folder = Path(self.temp.name)
        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.wait_until(lambda: not self.window.busy)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def click(self, widget):
        QtTest.QTest.mouseClick(widget, QtCore.Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def wait_until(self, predicate, timeout=5000):
        start = time.monotonic()
        while not predicate():
            if (time.monotonic() - start) * 1000 > timeout:
                self.fail("Qt operation did not complete before test timeout")
            QtTest.QTest.qWait(10)
        self.app.processEvents()

    def settle(self):
        self.wait_until(lambda: not self.window.busy)

    def generate(self):
        self.click(self.window.generate_button)
        self.assertIsNotNone(self.window.capture)
        return self.window.capture

    def test_initial_state_has_no_fabricated_capture(self):
        self.assertIsNone(self.window.capture)
        self.assertFalse(self.window.save_button.isEnabled())
        self.assertFalse(self.window.csv_button.isEnabled())
        self.assertFalse(self.window.trigger_line.isVisible())
        self.assertFalse(self.window.cursor_toggle.isEnabled())
        self.assertIn("НЕТ ЗАПИСИ", self.window.origin_badge.text())
        self.assertIn("USB", self.window.status_label.text())

    def test_generate_save_open_plot_from_independent_disk_load(self):
        original = self.generate()
        path = self.folder / "русская запись.mtcap"
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(path), "")):
            self.click(self.window.save_button)
            self.settle()
        self.assertTrue(path.is_file())
        self.assertIn("Сохранено", self.window.status_label.text())
        # Replace the on-screen array before reopening so a stale plot cannot
        # satisfy the scenario, even if equality happens to pass for the demo.
        self.window.show_capture(replace(original, samples=(1, 2, 3), trigger_kind="auto_timeout",
                                         trigger_index=None, pretrigger_samples=0))
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            self.click(self.window.open_button)
            self.settle()
        reopened = self.window.capture
        self.assertIsNot(reopened, original)
        self.assertEqual(reopened, original)
        self.assertTrue(self.window.opened_from_disk)
        self.assertEqual(self.window.current_path, path)
        self.assertIn("Открыто с диска", self.window.file_label.text())
        self.assertIn("ДЕМО", self.window.origin_badge.text())
        x, y = self.window.curve.getData()
        self.assertEqual(len(y), 2048)
        for index, expected in ((512, 2048), (537, 3048), (562, 2048), (587, 1048)):
            self.assertEqual(int(y[index]), expected)
        self.assertAlmostEqual(x[0], -10.24, places=12)
        self.assertAlmostEqual(x[512], 0, places=12)
        self.assertAlmostEqual(x[-1], 30.70, places=12)
        self.assertAlmostEqual(x[-1] - x[0], 40.94, places=12)
        self.assertIn("40,94", self.window.span_value.text())
        self.assertTrue(self.window.trigger_line.isVisible())
        self.assertEqual(self.window.trigger_line.value(), 0)
        self.assertIn("номинальная", self.window.plot.getAxis("bottom").labelText)
        self.assertIn("Код АЦП", self.window.plot.getAxis("left").labelText)

    def test_invalid_open_preserves_last_good_plot_and_recovers(self):
        original = self.generate()
        good = self.folder / "исправная.mtcap"
        recording.save(good, original)
        self.window.open_path(good)
        self.settle()
        previous = self.window.capture
        previous_curve = tuple(self.window.curve.getData()[1])
        bad = self.folder / "ошибка.mtcap"
        bad.write_bytes(b"broken")
        self.window.open_path(bad)
        self.settle()
        self.assertIs(self.window.capture, previous)
        self.assertEqual(self.window.current_path, good)
        self.assertEqual(tuple(self.window.curve.getData()[1]), previous_curve)
        self.assertIn("Не удалось открыть", self.window.status_label.text())
        self.assertIn("ошибка.mtcap", self.window.status_label.text())
        self.assertNotIn("ошибка.mtcap", self.window.file_label.text())
        self.assertTrue(self.window.open_button.isEnabled())
        self.window.open_path(good)
        self.settle()
        self.assertIn("Открыто с диска", self.window.status_label.text())

    def test_cancel_open_save_export_preserves_data_and_file_identity(self):
        original = self.generate()
        for button, method in ((self.window.open_button, "getOpenFileName"),
                               (self.window.save_button, "getSaveFileName"),
                               (self.window.csv_button, "getSaveFileName")):
            with self.subTest(method=method, button=button.text()):
                with patch.object(QtWidgets.QFileDialog, method, return_value=("", "")):
                    self.click(button)
                self.assertIs(self.window.capture, original)
                self.assertIsNone(self.window.current_path)
                self.assertIn("отменено", self.window.status_label.text().lower())
                self.assertFalse(self.window.busy)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_extension_applied_before_overwrite_confirmation_and_decline(self):
        original = self.generate()
        target = self.folder / "существующая.mtcap"
        target.write_bytes(b"keep this existing file")
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(target.with_suffix("")), "")), \
             patch.object(self.window, "_confirm_overwrite", return_value=False) as question:
            self.click(self.window.save_button)
        question.assert_called_once()
        self.assertEqual(question.call_args.args[0], target)
        self.assertEqual(target.read_bytes(), b"keep this existing file")
        self.assertIs(self.window.capture, original)
        self.assertIn("Замена файла отменена", self.window.status_label.text())
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(target.with_suffix("")), "")), \
             patch.object(self.window, "_confirm_overwrite", return_value=True):
            self.click(self.window.save_button)
            self.settle()
        self.assertEqual(recording.load(target), original)

    def test_real_overwrite_dialog_has_russian_buttons_and_safe_default(self):
        target = self.folder / "существующая.mtcap"
        for action in ("enter", "escape", "close", "replace"):
            with self.subTest(action=action):
                observed = {}

                def inspect_and_dismiss():
                    dialog = self.app.activeModalWidget()
                    observed["is_message_box"] = isinstance(dialog, QtWidgets.QMessageBox)
                    if not observed["is_message_box"]:
                        self.app.closeAllWindows()
                        return
                    observed["labels"] = {button.text() for button in dialog.buttons()}
                    default = dialog.defaultButton()
                    escape = dialog.escapeButton()
                    observed["default"] = default.text() if default is not None else None
                    observed["escape"] = escape.text() if escape is not None else None
                    observed["message"] = dialog.text()
                    observed["format"] = dialog.textFormat()
                    if action == "enter":
                        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key.Key_Return)
                    elif action == "escape":
                        QtTest.QTest.keyClick(dialog, QtCore.Qt.Key.Key_Escape)
                    elif action == "close":
                        dialog.close()
                    else:
                        button = next((button for button in dialog.buttons() if button.text() == "Заменить"), None)
                        if button is None:
                            dialog.close()
                        else:
                            QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)

                QtCore.QTimer.singleShot(0, inspect_and_dismiss)
                confirmed = self.window._confirm_overwrite(target)
                self.assertTrue(observed["is_message_box"])
                self.assertEqual(observed["labels"], {"Заменить", "Отмена"})
                self.assertEqual(observed["default"], "Отмена")
                self.assertEqual(observed["escape"], "Отмена")
                self.assertIn(target.name, observed["message"])
                self.assertEqual(observed["format"], QtCore.Qt.TextFormat.PlainText)
                self.assertEqual(confirmed, action == "replace")

    def test_write_failure_is_not_success_and_does_not_change_display(self):
        original = self.generate()
        target = self.folder / "защищённая.mtcap"
        target.write_bytes(b"original file")
        with patch.object(recording, "save", side_effect=PermissionError("denied")):
            self.window.save_path(target, overwrite=True)
            self.settle()
        self.assertEqual(target.read_bytes(), b"original file")
        self.assertIs(self.window.capture, original)
        self.assertIsNone(self.window.current_path)
        self.assertIn("Не удалось сохранить", self.window.status_label.text())
        self.assertIn("Нет прав", self.window.status_label.text())
        self.assertTrue(self.window.save_button.isEnabled())
        self.window.save_path(self.folder / "повтор.mtcap")
        self.settle()
        self.assertIn("Сохранено", self.window.status_label.text())

    def test_csv_dialog_exports_raw_codes_nominal_seconds_and_origin(self):
        original = self.generate()
        target = self.folder / "экспорт.csv"
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(target), "")):
            self.click(self.window.csv_button)
            self.settle()
        with target.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2048)
        self.assertEqual(rows[537]["adc_code"], "3048")
        self.assertAlmostEqual(float(rows[537]["time_nominal_s"]), 0.0005, places=12)
        self.assertEqual(rows[537]["origin"], "simulator")
        self.assertIs(self.window.capture, original)
        self.assertIsNone(self.window.current_path)
        self.assertIn("CSV экспортирован", self.window.status_label.text())

    def test_missing_trigger_rails_quality_and_plain_text_are_visible(self):
        self.generate()
        capture = replace(self.window.capture, samples=(0, 1, 2048, 4095), nominal_fs_hz=1000,
                          trigger_kind="auto_timeout", trigger_index=None, pretrigger_samples=0,
                          quality_flags=("<b>флаг источника</b>",))
        self.window.show_capture(capture)
        x, y = self.window.curve.getData()
        self.assertEqual(tuple(x), (0.0, 1.0, 2.0, 3.0))
        self.assertEqual(tuple(y), (0, 1, 2048, 4095))
        self.assertFalse(self.window.trigger_line.isVisible())
        self.assertIn("Событие запуска отсутствует", self.window.trigger_label.text())
        self.assertIn("Есть крайние коды", self.window.quality_label.text())
        self.assertIn("<b>флаг источника</b>", self.window.quality_label.text())
        self.assertEqual(self.window.quality_label.textFormat(), QtCore.Qt.TextFormat.PlainText)

    def test_device_file_is_not_labeled_as_demo(self):
        capture = replace(self.generate(), origin="device")
        path = self.folder / "прибор.mtcap"
        recording.save(path, capture)
        self.window.open_path(path)
        self.settle()
        self.assertNotIn("ДЕМО", self.window.origin_badge.text())
        self.assertIn("ЗАПИСЬ ПРИБОРА", self.window.origin_badge.text())
        self.assertIn("Запуск из метаданных", self.window.trigger_label.text())
        self.assertIn("Открыто с диска", self.window.file_label.text())

    def test_minimum_window_with_cursors_has_no_overlapping_plot_labels(self):
        self.generate()
        self.window.cursor_toggle.setChecked(True)
        self.window.resize(1000, 710)
        QtTest.QTest.qWait(100)
        self.assertLess(self.window.plot.geometry().bottom(), self.window.trigger_label.geometry().top())
        self.assertLess(self.window.trigger_label.geometry().bottom(), self.window.cursor_label.geometry().top())

    def test_minimum_window_scrolls_annotations_with_larger_font_metrics(self):
        self.generate()
        self.window.cursor_toggle.setChecked(True)
        original_style = self.window.styleSheet()
        for pixels in (18, 22):
            with self.subTest(font_pixels=pixels):
                self.window.setStyleSheet(original_style.replace("font-size: 13px", f"font-size: {pixels}px"))
                self.window.resize(1000, 710)
                QtTest.QTest.qWait(100)
                self.assertEqual(self.window.size(), QtCore.QSize(1000, 710))
                self.assertGreaterEqual(self.window.plot.height(), 100)
                self.assertLess(self.window.plot.geometry().bottom(), self.window.trigger_label.geometry().top())
                self.assertLess(self.window.trigger_label.geometry().bottom(), self.window.cursor_label.geometry().top())
                scroll = self.window.plot_scroll
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                scroll.ensureWidgetVisible(self.window.cursor_label)
                self.app.processEvents()
                cursor_bottom = self.window.cursor_label.mapTo(scroll.viewport(), self.window.cursor_label.rect().bottomLeft())
                self.assertLess(cursor_bottom.y(), scroll.viewport().height())
        self.window.setStyleSheet(original_style)
        self.window.resize(1240, 900)
        QtTest.QTest.qWait(100)
        self.assertEqual(self.window.plot_scroll.verticalScrollBar().maximum(), 0)

    def test_sidebar_keeps_actions_and_wrapped_warnings_reachable(self):
        self.generate()
        original_style = self.window.styleSheet()
        for pixels in (13, 18, 22):
            with self.subTest(font_pixels=pixels):
                self.window.setStyleSheet(original_style.replace("font-size: 13px", f"font-size: {pixels}px"))
                self.window.resize(1000, 710)
                QtTest.QTest.qWait(100)
                sidebar = self.window.findChild(QtWidgets.QFrame, "sidebar")
                for label in sidebar.findChildren(QtWidgets.QLabel):
                    self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()), label.text())
                for button in sidebar.findChildren(QtWidgets.QPushButton):
                    self.assertGreaterEqual(button.height(), button.minimumSizeHint().height(), button.text())
                    self.assertGreaterEqual(button.width(), button.minimumSizeHint().width(), button.text())
                scroll = self.window.sidebar_scroll
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                for button in (self.window.generate_button, self.window.png_button, self.window.help_button):
                    scroll.ensureWidgetVisible(button)
                    self.app.processEvents()
                    center = button.mapTo(scroll.viewport(), button.rect().center())
                    self.assertTrue(scroll.viewport().rect().contains(center), button.text())
                self.assertEqual(self.window.size(), QtCore.QSize(1000, 710))
        self.window.setStyleSheet(original_style)
        self.window.resize(1240, 900)
        QtTest.QTest.qWait(100)
        self.assertEqual(self.window.sidebar_scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.window.sidebar_scroll.horizontalScrollBar().maximum(), 0)

    def test_long_quality_flags_are_scrollable_and_do_not_resize_window(self):
        capture = replace(self.generate(), quality_flags=tuple(f"флаг_{i}_" + "я" * 55 for i in range(16)))
        original_height = self.window.height()
        self.window.show_capture(capture)
        self.app.processEvents()
        self.assertEqual(self.window.height(), original_height)
        self.assertGreater(self.window.quality_scroll.verticalScrollBar().maximum(), 0)
        self.assertIn(capture.quality_flags[-1], self.window.quality_label.text())

    def test_help_is_local_reopens_and_missing_file_has_clear_error(self):
        help_file = self.folder / "help.md"
        help_file.write_text("# Справка просмотрщика\n\nСинтетические данные. Без сети.\n", encoding="utf-8")
        with patch("motortester.gui.HELP_PATH", help_file), \
             patch("socket.create_connection", side_effect=AssertionError("network is not permitted")):
            self.click(self.window.help_button)
            dialog = self.window._help_dialog
            self.assertTrue(dialog.isVisible())
            browser = dialog.findChild(QtWidgets.QTextBrowser, "helpText")
            self.assertIn("Без сети", browser.toPlainText())
            self.assertFalse(browser.openExternalLinks())
            self.assertFalse(browser.openLinks())
            dialog.close()
            self.assertFalse(dialog.isVisible())
            self.click(self.window.help_button)
            self.assertIs(self.window._help_dialog, dialog)
            self.assertTrue(dialog.isVisible())
            self.window.close()
            self.assertFalse(dialog.isVisible())
        other = MainWindow()
        try:
            with patch("motortester.gui.HELP_PATH", self.folder / "missing.md"):
                other.show_help()
                self.assertIsNone(other._help_dialog)
                self.assertIn("Не удалось открыть офлайн-справку", other.status_label.text())
        finally:
            other.close()
            other.deleteLater()

    def test_zoom_pan_reset_preserve_original_samples(self):
        original = self.generate()
        before = tuple(original.samples)
        view = self.window.plot.getViewBox()
        initial = view.viewRange()
        viewport = self.window.plot.viewport()
        position = QtCore.QPointF(viewport.width() / 2, viewport.height() / 2)
        wheel = QtGui.QWheelEvent(position, viewport.mapToGlobal(position.toPoint()), QtCore.QPoint(),
                                 QtCore.QPoint(0, 120), QtCore.Qt.MouseButton.NoButton,
                                 QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(viewport, wheel)
        self.app.processEvents()
        after_zoom = view.viewRange()
        self.assertLess(after_zoom[0][1] - after_zoom[0][0], initial[0][1] - initial[0][0])
        view.translateBy(x=2, y=50)
        self.app.processEvents()
        self.assertNotEqual(view.viewRange(), after_zoom)
        self.click(self.window.reset_button)
        np.testing.assert_allclose(view.viewRange(), initial)
        self.assertIs(self.window.capture, original)
        self.assertEqual(original.samples, before)
        self.assertEqual(tuple(self.window.curve.getData()[1]), before)

    def test_two_cursors_snap_to_raw_samples_and_report_nominal_differences(self):
        original = self.generate()
        self.click(self.window.cursor_toggle)
        self.assertTrue(all(line.isVisible() for line in self.window.cursor_lines))
        self.window.cursor_lines[0].setValue(0.001)
        self.window.cursor_lines[1].setValue(0.501)
        self.assertEqual(self.window._cursor_indices, [512, 537])
        self.assertEqual(self.window.cursor_lines[0].value(), 0)
        self.assertAlmostEqual(self.window.cursor_lines[1].value(), 0.5)
        text = self.window.cursor_label.text()
        self.assertIn("Δt (B−A): 0,500 мс", text)
        self.assertIn("Δкод: +1000", text)
        self.assertIn("2048 код", text)
        self.assertIn("3048 код", text)
        self.assertIs(self.window.capture, original)
        self.click(self.window.cursor_toggle)
        self.assertFalse(any(line.isVisible() for line in self.window.cursor_lines))
        self.assertFalse(self.window.cursor_label.isVisible())

    def test_single_sample_and_maximum_window_are_supported(self):
        original = self.generate()
        for count in (1, MAX_SAMPLES):
            with self.subTest(count=count):
                capture = replace(original, samples=tuple(i % 4096 for i in range(count)),
                                  trigger_kind="auto_timeout", trigger_index=None, pretrigger_samples=0)
                path = self.folder / f"размер-{count}.mtcap"
                recording.save(path, capture)
                self.window.open_path(path)
                self.settle()
                self.assertEqual(len(self.window.curve.getData()[0]), count)
                self.assertEqual(tuple(self.window.curve.getData()[1]), capture.samples)
                self.window.cursor_toggle.setChecked(True)
                self.assertTrue(self.window.cursor_label.isVisible())
                self.assertTrue(all(0 <= i < count for i in self.window._cursor_indices))
                bounds = self.window.plot.getViewBox().viewRange()[0]
                self.assertLess(bounds[0], bounds[1])

    def test_slow_io_keeps_event_loop_responsive_blocks_repeat_and_close_is_safe(self):
        original = self.generate()
        release = threading.Event()
        entered = threading.Event()
        ticks = []

        def slow_load(_):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test release timed out")
            return original

        with patch.object(recording, "load", side_effect=slow_load) as loader:
            try:
                self.window.open_path(self.folder / "медленная.mtcap")
                self.wait_until(entered.is_set)
                QtCore.QTimer.singleShot(0, lambda: ticks.append(True))
                self.wait_until(lambda: bool(ticks))
                self.assertTrue(self.window.busy)
                self.assertFalse(self.window.generate_button.isEnabled())
                self.assertFalse(self.window.open_button.isEnabled())
                self.assertTrue(self.window.reset_button.isEnabled())
                self.window.open_path(self.folder / "повтор.mtcap")
                self.assertEqual(loader.call_count, 1)
                self.assertIs(self.window.capture, original)
                self.assertIn("предыдущая запись", self.window.status_label.text())
                self.window.close()
                self.assertFalse(self.window.isVisible())
            finally:
                release.set()
            self.settle()
        self.assertIs(self.window.capture, original)
        self.assertIsNone(self.window.current_path)

    def test_repeated_interactions_and_new_window_after_close(self):
        for index in range(3):
            capture = self.generate()
            self.assertEqual(capture.sequence, index)
            self.window.cursor_toggle.setChecked(index % 2 == 0)
            path = self.folder / f"повтор-{index}.mtcap"
            self.window.save_path(path)
            self.settle()
            self.window.open_path(path)
            self.settle()
            self.assertEqual(self.window.capture, capture)
        self.window.close()
        other = MainWindow()
        other.show()
        self.app.processEvents()
        try:
            self.assertIsNone(other.capture)
            self.click(other.generate_button)
            self.assertEqual(other.capture.sequence, 0)
        finally:
            other.close()
            other.deleteLater()
            self.app.processEvents()

    def test_screenshot_helper_creates_actual_qt_window_image(self):
        target = self.folder / "снимок.png"
        self.assertEqual(screenshot(target), 0)
        data = target.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        image = QtGui.QImage(str(target))
        self.assertGreaterEqual(image.width(), 1000)
        self.assertGreaterEqual(image.height(), 710)


if __name__ == "__main__":
    unittest.main()

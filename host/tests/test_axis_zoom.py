"""Bounded real-Qt axis gestures; synthetic software evidence, not Windows/USB.

Wheel and held-button mouse events enter the PlotWidget viewport and are routed
by the real graphics scene. Calling ViewBox.scaleBy alone cannot satisfy these
checks of bottom-axis, left-axis and plot-area gesture dispatch.
"""
from dataclasses import replace
import math
from pathlib import Path
import tempfile
import time
import unittest

try:
    import numpy as np
    from PySide6 import QtCore, QtGui, QtTest, QtWidgets
    from motortester.gui import MainWindow
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI dependencies unavailable: {exc}") from exc

from motortester import recording
from motortester.emulator import demo_message, from_message
from motortester.model import MAX_SAMPLES


class AxisZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()
        self.plot = self.window.plot
        self.view = self.plot.getViewBox()
        self.viewport = self.plot.viewport()
        self.original = from_message(demo_message())

    def tearDown(self):
        self.wait_until(lambda: not self.window.busy)
        self.window.close()
        if self.window._acquisition is not None:
            self.wait_until(lambda: not self.window._acquisition.running)
        self.window.deleteLater()
        self.app.processEvents()

    def wait_until(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Qt operation did not complete")
            QtTest.QTest.qWait(5)
        self.app.processEvents()

    def show_capture(self, capture=None):
        self.window.show_capture(capture or self.original)
        self.app.processEvents()

    def point(self, area):
        item = self.view if area == "plot" else self.plot.getAxis(area)
        point = self.plot.mapFromScene(item.mapToScene(item.rect().center()))
        self.assertTrue(self.viewport.rect().contains(point), (area, point))
        if area != "plot":
            self.assertFalse(self.view.sceneBoundingRect().contains(self.plot.mapToScene(point)))
        return point

    def wheel(self, area, delta=120):
        point = self.point(area)
        event = QtGui.QWheelEvent(
            QtCore.QPointF(point), QtCore.QPointF(self.viewport.mapToGlobal(point)),
            QtCore.QPoint(), QtCore.QPoint(0, delta), QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(self.viewport, event)
        self.app.processEvents()

    def drag_from(self, point, dx, dy):
        QtTest.QTest.mouseMove(self.viewport, point)
        QtTest.QTest.mousePress(self.viewport, QtCore.Qt.MouseButton.LeftButton, pos=point)
        # QTest.mouseMove does not retain button state on every Qt platform.
        # Explicit real QMouseEvents exercise Qt/GraphicsScene drag recognition.
        for fraction in (.25, .5, .75, 1):
            target = point + QtCore.QPoint(round(dx * fraction), round(dy * fraction))
            event = QtGui.QMouseEvent(
                QtCore.QEvent.Type.MouseMove, QtCore.QPointF(target),
                QtCore.QPointF(self.viewport.mapToGlobal(target)),
                QtCore.Qt.MouseButton.NoButton, QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(self.viewport, event)
            QtTest.QTest.qWait(20)
        QtTest.QTest.mouseRelease(self.viewport, QtCore.Qt.MouseButton.LeftButton,
                                 pos=point + QtCore.QPoint(dx, dy))
        self.app.processEvents()

    def drag(self, area, dx, dy):
        self.drag_from(self.point(area), dx, dy)

    def expected_bounds(self, capture):
        if capture is None:
            first, last, period = 0., 1., 1.
        else:
            period = 1000 / capture.nominal_fs_hz
            zero = capture.trigger_index or 0
            first = -zero * period
            last = (len(capture.samples) - 1 - zero) * period
            if len(capture.samples) == 1:
                first, last = first - period / 2, last + period / 2
        margin = (last - first) * .025
        return ((first - margin, last + margin), (-143.325, 4238.325)), (period, 1.)

    def assert_bounded(self, capture):
        bounds, minimum = self.expected_bounds(capture)
        ranges = self.view.viewRange()
        limits = self.view.state["limits"]
        for axis, key in enumerate(("x", "y")):
            low, high = ranges[axis]
            self.assertTrue(math.isfinite(low) and math.isfinite(high), ranges)
            self.assertGreater(high, low)
            tolerance = max(1e-12, (bounds[axis][1] - bounds[axis][0]) * 1e-10)
            self.assertGreaterEqual(low, bounds[axis][0] - tolerance)
            self.assertLessEqual(high, bounds[axis][1] + tolerance)
            self.assertGreaterEqual(high - low, minimum[axis] - tolerance)
            self.assertLessEqual(high - low, bounds[axis][1] - bounds[axis][0] + tolerance)
            np.testing.assert_allclose(limits[f"{key}Limits"], bounds[axis], rtol=1e-12, atol=1e-12)
            np.testing.assert_allclose(limits[f"{key}Range"],
                                       (minimum[axis], bounds[axis][1] - bounds[axis][0]),
                                       rtol=1e-12, atol=1e-12)

    def assert_full_view(self, capture):
        self.assert_bounded(capture)
        expected, _ = self.expected_bounds(capture)
        np.testing.assert_allclose(self.view.viewRange(), expected, rtol=1e-12, atol=1e-12)

    def test_wheel_events_on_axes_change_only_the_requested_axis(self):
        self.show_capture()
        for area, axis in (("bottom", 0), ("left", 1)):
            for delta in (120, -120):
                with self.subTest(area=area, delta=delta):
                    self.window.reset_view()
                    self.wheel("plot")
                    self.wheel("plot")
                    before = np.array(self.view.viewRange())
                    self.wheel(area, delta)
                    after = np.array(self.view.viewRange())
                    np.testing.assert_array_equal(after[1 - axis], before[1 - axis])
                    old_span, new_span = np.diff(before[axis])[0], np.diff(after[axis])[0]
                    self.assertLess(new_span, old_span) if delta > 0 else self.assertGreater(new_span, old_span)
                    self.assert_bounded(self.original)

    def test_left_drag_events_on_axes_pan_only_the_requested_axis(self):
        self.show_capture()
        for area, axis in (("bottom", 0), ("left", 1)):
            with self.subTest(area=area):
                self.window.reset_view()
                for _ in range(3):
                    self.wheel("plot")
                before = np.array(self.view.viewRange())
                self.drag(area, 40, 30)
                after = np.array(self.view.viewRange())
                np.testing.assert_array_equal(after[1 - axis], before[1 - axis])
                self.assertFalse(np.allclose(after[axis], before[axis]))
                np.testing.assert_allclose(np.diff(after, axis=1), np.diff(before, axis=1), rtol=1e-12)
                self.assert_bounded(self.original)

    def test_plot_area_still_zooms_and_pans_both_axes(self):
        self.show_capture()
        before = np.array(self.view.viewRange())
        self.wheel("plot")
        zoomed = np.array(self.view.viewRange())
        self.assertTrue(np.all(np.diff(zoomed, axis=1) < np.diff(before, axis=1)))
        self.drag("plot", 40, 30)
        moved = np.array(self.view.viewRange())
        self.assertTrue(np.all(np.abs(moved[:, 0] - zoomed[:, 0]) > 0))
        self.assert_bounded(self.original)

    def test_repeated_zoom_events_stop_at_finite_resolution_and_full_view(self):
        captures = [None, self.original]
        for count, rate, level, trigger in (
            (1, 1, 0, 0), (1, 1_000_000, 4095, None),
            (2, 1_000_000, 2048, 1), (3, 1, 2048, None),
            (MAX_SAMPLES, 1, 4095, MAX_SAMPLES - 1),
            (MAX_SAMPLES, 1_000_000, 0, MAX_SAMPLES // 2),
        ):
            captures.append(replace(
                self.original, samples=(level,) * count, nominal_fs_hz=rate,
                trigger_kind="signal" if trigger is not None else "auto_timeout",
                trigger_index=trigger, pretrigger_samples=trigger or 0))
        for capture in captures:
            with self.subTest(count=None if capture is None else len(capture.samples),
                              rate=None if capture is None else capture.nominal_fs_hz):
                if capture is not None:
                    self.show_capture(capture)
                for area, axis in (("bottom", 0), ("left", 1)):
                    for delta in (120, -120):
                        for _ in range(80):
                            self.wheel(area, delta)
                            self.assert_bounded(capture)
                        if delta > 0:
                            minimum = self.expected_bounds(capture)[1][axis]
                            self.assertAlmostEqual(np.diff(self.view.viewRange()[axis])[0] / minimum, 1., places=6)
                        else:
                            self.assert_full_view(capture)
                self.window.reset_view()
                self.assert_full_view(capture)

    def test_repeated_axis_pan_stops_at_both_overview_edges(self):
        self.show_capture()
        bounds, _ = self.expected_bounds(self.original)
        for area, axis in (("bottom", 0), ("left", 1)):
            self.window.reset_view()
            for _ in range(3):
                self.wheel(area)
            other = self.view.viewRange()[1 - axis][:]
            for direction in (-1, 1):
                for _ in range(18):
                    self.drag(area, direction * 90 if axis == 0 else 0,
                              direction * 70 if axis == 1 else 0)
                    self.assert_bounded(self.original)
                    np.testing.assert_array_equal(self.view.viewRange()[1 - axis], other)
                low, high = self.view.viewRange()[axis]
                edge = (high if direction < 0 else low) if axis == 0 else (low if direction < 0 else high)
                expected = (bounds[axis][1] if direction < 0 else bounds[axis][0]) if axis == 0 else (bounds[axis][0] if direction < 0 else bounds[axis][1])
                self.assertAlmostEqual(edge, expected, places=8)

    def test_home_button_and_new_capture_restore_original_fit(self):
        self.show_capture()
        for reset in (lambda: self.window.reset_button.click(),
                      lambda: QtTest.QTest.keyClick(self.window, QtCore.Qt.Key.Key_Home)):
            for _ in range(4):
                self.wheel("plot")
            self.drag("plot", 30, -25)
            reset()
            # QPushButton's keyboard shortcut uses animateClick: allow its
            # delayed release, rather than asserting before the slot runs.
            self.wait_until(lambda: np.allclose(self.view.viewRange(), self.expected_bounds(self.original)[0]))
            self.assert_full_view(self.original)
        # Replacing a long capture by a short one must replace old limits too.
        for count, rate in ((MAX_SAMPLES, 1), (1, 1_000_000), (2, 1), (2048, 50_000)):
            self.wheel("plot")
            capture = replace(self.original, samples=(2048,) * count, nominal_fs_hz=rate,
                              trigger_kind="auto_timeout", trigger_index=None, pretrigger_samples=0)
            self.show_capture(capture)
            self.assert_full_view(capture)
        self.window.generate_demo()
        self.app.processEvents()
        self.assert_full_view(self.window.capture)

    def test_no_data_gestures_remain_finite_without_fabricating_samples(self):
        self.assert_full_view(None)
        self.wheel("plot")
        self.drag("plot", 40, 20)
        self.assert_bounded(None)
        self.assertIsNone(self.window.capture)
        self.assertFalse(self.window.reset_button.isEnabled())
        self.assertFalse(self.window.cursor_toggle.isEnabled())
        self.assertFalse(self.window.save_button.isEnabled())
        self.assertIsNone(self.window.curve.getData()[0])

    def test_navigation_preserves_samples_encoded_bytes_and_cursor_mapping(self):
        self.show_capture()
        self.window.cursor_toggle.setChecked(True)
        encoded = recording.encode(self.original)
        x, y = (a.copy() for a in self.window.curve.getData())
        indices = self.window._cursor_indices[:]
        positions = [line.value() for line in self.window.cursor_lines]
        label = self.window.cursor_label.text()
        for _ in range(3):
            for area in ("bottom", "left", "plot"):
                self.wheel(area)
                self.drag(area, 35, 25)
                self.window.reset_view()
        self.assertIs(self.window.capture, self.original)
        self.assertEqual(recording.encode(self.window.capture), encoded)
        np.testing.assert_array_equal(self.window.curve.getData()[0], x)
        np.testing.assert_array_equal(self.window.curve.getData()[1], y)
        self.assertEqual(self.window._cursor_indices, indices)
        self.assertEqual([line.value() for line in self.window.cursor_lines], positions)
        self.assertEqual(self.window.cursor_label.text(), label)
        # Drag a real cursor after zooming. Its position must still be an exact
        # original sample time, and the displayed values use that sample code.
        self.wheel("bottom")
        self.wheel("left")
        before = self.window._cursor_indices[0]
        line = self.window.cursor_lines[0]
        point = self.plot.mapFromScene(self.view.mapViewToScene(
            QtCore.QPointF(line.value(), sum(self.view.viewRange()[1]) / 2)))
        self.drag_from(point, 50, 0)
        self.assertNotEqual(self.window._cursor_indices[0], before)
        for index, cursor in zip(self.window._cursor_indices, self.window.cursor_lines):
            self.assertAlmostEqual(cursor.value(), self.original.time_seconds(index) * 1000, places=10)
            self.assertIn(f"{self.original.samples[index]} код", self.window.cursor_label.text())
        self.assertEqual(recording.encode(self.window.capture), encoded)

    def test_reopen_updates_bounds_and_invalid_open_preserves_view(self):
        capture = replace(self.original, origin="device", samples=(0, 4095), nominal_fs_hz=1,
                          trigger_index=1, pretrigger_samples=1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "масштаб.mtcap"
            recording.save(path, capture)
            encoded = path.read_bytes()
            self.show_capture()
            self.wheel("plot")
            self.window.open_path(path)
            self.wait_until(lambda: not self.window.busy)
            self.assertEqual(self.window.capture, capture)
            self.assertTrue(self.window.opened_from_disk)
            self.assert_full_view(capture)
            self.wheel("left")
            self.wheel("bottom")
            previous = np.array(self.view.viewRange())
            self.window.open_path(Path(folder) / "missing.mtcap")
            self.wait_until(lambda: not self.window.busy)
            np.testing.assert_array_equal(self.view.viewRange(), previous)
            self.assertEqual(path.read_bytes(), encoded)
            self.assertEqual(recording.encode(self.window.capture), encoded)

    def test_byte_emulator_acquired_windows_reset_view_and_keep_recording(self):
        self.window.show_acquisition()
        dialog = self.window._acquisition_dialog
        dialog.connect_button.click()
        self.wait_until(dialog.start_button.isEnabled)
        dialog.mode.setCurrentIndex(2)  # Single: no implicit extra acquisitions.
        dialog.start_button.click()
        self.wait_until(lambda: self.window.capture is not None and dialog.start_button.isEnabled())
        self.assert_full_view(self.window.capture)
        first = self.window.capture
        before = recording.encode(first)
        self.wheel("bottom")
        self.wheel("left")
        self.drag("bottom", 40, 0)
        self.assertEqual(recording.encode(self.window.capture), before)
        dialog.start_button.click()
        self.wait_until(lambda: self.window.capture is not first and dialog.start_button.isEnabled())
        second = self.window.capture
        self.assert_full_view(second)
        self.assertEqual(recording.encode(first), before)
        self.assertEqual(len(second.samples), 2048)
        self.assertEqual(second.origin, "simulator")

    def test_wrapped_guidance_reachable_at_narrow_width_and_large_fonts(self):
        self.show_capture()
        self.window.cursor_toggle.setChecked(True)
        style = self.window.styleSheet()
        for pixels in (13, 18, 22):
            with self.subTest(font_pixels=pixels):
                self.window.setStyleSheet(style.replace("font-size: 13px", f"font-size: {pixels}px"))
                self.window.resize(1000, 710)
                QtTest.QTest.qWait(60)
                label = self.window.zoom_help
                self.assertTrue(label.wordWrap())
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                self.assertLess(self.window.trigger_label.geometry().bottom(), label.geometry().top())
                self.assertLess(label.geometry().bottom(), self.window.cursor_label.geometry().top())
                self.assertGreaterEqual(self.window.plot.height(), 100)
                self.assertEqual(self.window.size(), QtCore.QSize(1000, 710))
                scroll = self.window.plot_scroll
                for point in (label.rect().topLeft(), label.rect().bottomRight()):
                    target = label.mapTo(scroll.widget(), point)
                    scroll.ensureVisible(target.x(), target.y(), 1, 1)
                    self.app.processEvents()
                    self.assertTrue(scroll.viewport().rect().contains(label.mapTo(scroll.viewport(), point)))
                for text in ("Нижняя ось", "левая ось", "обе оси", "Home", "сбрасывает"):
                    self.assertIn(text, label.text())
        self.window.show_help()
        help_text = self.window._help_dialog.findChild(QtWidgets.QTextBrowser, "helpText").toPlainText()
        for text in ("нижняя ось", "левая ось", "одним номинальным интервалом", "одним кодом", "очередное окно захвата"):
            self.assertIn(text, help_text)


if __name__ == "__main__":
    unittest.main()

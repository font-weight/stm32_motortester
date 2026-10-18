"""Real Qt wheel/selection events; a fake bridge records outgoing commands.

These are running-platform GUI checks, not Windows desktop or hardware proof.
"""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from motortester.emulator import demo_message, from_message
from motortester.gui import MainWindow
from motortester.session import Config, Phase
from test_gui_acquisition import advertised_caps
from test_gui_prehistory import RecordFakeBridge


class WheelSettingsTests(unittest.TestCase):
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
        self.scroll = self.dialog.findChild(QtWidgets.QScrollArea)
        self.dialog.resize(560, 300)
        self.dialog.activateWindow()
        self.app.processEvents()

    def tearDown(self):
        for combo in (self.dialog.source, self.dialog.mode,
                      self.dialog.edge, self.dialog.pretrigger):
            combo.hidePopup()
        self.window.close()
        self.bridge.finished.emit()
        self.app.processEvents()
        self.window.deleteLater()
        self.app.processEvents()
        self.fake_module.stop()

    def prepare(self, name, focused=True):
        self.bridge.emit_state(Phase.IDLE if name == "source" else Phase.READY,
                               capabilities=advertised_caps())
        self.dialog.mode.setCurrentIndex(0)
        widget = getattr(self.dialog, name)
        self.assertTrue(widget.isEnabled())
        self.dialog.activateWindow()
        (widget if focused else self.dialog).setFocus()
        self.scroll.ensureWidgetVisible(widget)
        self.app.processEvents()
        self.assertEqual(widget.hasFocus(), focused)
        return widget

    def wheel(self, widget, angle=0, pixel=0, *, native=True):
        position = widget.rect().center()
        if native and (angle or pixel):
            # Window-system dispatch hit-tests the control and propagates its
            # ignored wheel event to the real enclosing QScrollArea.
            window = widget.window()
            QtTest.QTest.wheelEvent(window.windowHandle(), widget.mapTo(window, position),
                                   QtCore.QPoint(0, angle), QtCore.QPoint(0, pixel))
        else:
            # Popup view delivery needs no ancestor propagation; QTest's
            # window helper rejects zero deltas, so deliver those directly too.
            event = QtGui.QWheelEvent(QtCore.QPointF(position),
                QtCore.QPointF(widget.mapToGlobal(position)), QtCore.QPoint(0, pixel),
                QtCore.QPoint(0, angle), QtCore.Qt.MouseButton.NoButton,
                QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(widget, event)
        self.app.processEvents()

    def test_all_settings_ignore_focused_unfocused_and_high_resolution_wheels(self):
        for name in ("source", "mode", "edge", "pretrigger", "level", "timeout"):
            for focused in (False, True):
                for angle, pixel in ((-120, 0), (120, 0), (-15, 0), (15, 0),
                                     (0, -25), (0, 25), (0, 0)):
                    with self.subTest(name=name, focused=focused, angle=angle, pixel=pixel):
                        widget = self.prepare(name, focused)
                        if isinstance(widget, QtWidgets.QComboBox):
                            widget.setCurrentIndex(1 if angle > 0 or pixel > 0 else 0)
                            value = widget.currentData
                            changes = QtTest.QSignalSpy(widget.currentIndexChanged)
                        else:
                            widget.setValue(1234)
                            value = widget.value
                            changes = QtTest.QSignalSpy(widget.valueChanged)
                        before = value()
                        self.wheel(widget, angle, pixel)
                        self.assertEqual(value(), before)
                        self.assertEqual(changes.count(), 0)
        self.assertEqual(self.bridge.calls, [])

    def test_ignored_wheels_scroll_panel_in_both_directions(self):
        for name in ("source", "mode", "edge", "pretrigger", "level", "timeout"):
            for focused in (False, True):
                for delta in (-120, 120):
                    with self.subTest(name=name, focused=focused, delta=delta):
                        widget = self.prepare(name, focused)
                        bar = self.scroll.verticalScrollBar()
                        center = widget.mapTo(self.scroll.widget(), widget.rect().center()).y()
                        bar.setValue(max(1, center - self.scroll.viewport().height() // 2))
                        self.app.processEvents()
                        before = bar.value()
                        self.assertGreater(before, bar.minimum())
                        self.assertLess(before, bar.maximum())
                        self.wheel(widget, delta)
                        self.assertGreater((bar.value() - before) * -delta, 0)

    def test_spin_editor_wheel_and_explicit_numeric_input(self):
        for name in ("level", "timeout"):
            with self.subTest(name=name):
                spin = self.prepare(name)
                spin.setValue(1234)
                for delta in (-120, 120, 0):
                    self.scroll.ensureWidgetVisible(spin)
                    self.wheel(spin.lineEdit(), delta)
                    self.assertEqual(spin.value(), 1234)
                spin.selectAll()
                QtTest.QTest.keyClicks(spin, "2345")
                QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Tab)
                self.assertEqual(spin.value(), 2345)
                spin.setFocus()
                QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Up)
                self.assertEqual(spin.value(), 2346)
                QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Down)
                self.assertEqual(spin.value(), 2345)
                self.scroll.ensureWidgetVisible(spin)
                option = QtWidgets.QStyleOptionSpinBox()
                spin.initStyleOption(option)
                for control, expected in ((QtWidgets.QStyle.SubControl.SC_SpinBoxUp, 2346),
                                          (QtWidgets.QStyle.SubControl.SC_SpinBoxDown, 2345)):
                    rect = spin.style().subControlRect(QtWidgets.QStyle.ComplexControl.CC_SpinBox,
                                                      option, control, spin)
                    self.assertFalse(rect.isEmpty())
                    QtTest.QTest.mouseClick(spin, QtCore.Qt.MouseButton.LeftButton, pos=rect.center())
                    self.assertEqual(spin.value(), expected)

    def test_open_popups_do_not_commit_wheels_and_keep_click_keyboard_selection(self):
        for name in ("source", "mode", "edge", "pretrigger"):
            with self.subTest(name=name):
                combo = self.prepare(name)
                combo.setCurrentIndex(0)
                QtTest.QTest.mouseClick(combo, QtCore.Qt.MouseButton.LeftButton)
                self.app.processEvents()
                view = combo.view()
                self.assertTrue(view.isVisible())
                changes = QtTest.QSignalSpy(combo.currentIndexChanged)
                for angle, pixel in ((-120, 0), (120, 0), (0, -25), (0, 25), (0, 0)):
                    self.wheel(view.viewport(), angle, pixel, native=False)
                    self.assertEqual(combo.currentIndex(), 0)
                    self.assertEqual(changes.count(), 0)
                    self.assertTrue(view.isVisible())
                QtTest.QTest.keyClick(view, QtCore.Qt.Key.Key_Escape)
                self.assertEqual(combo.currentIndex(), 0)
                combo.showPopup()
                self.app.processEvents()
                item = combo.model().index(1, 0)
                QtTest.QTest.mouseMove(view.viewport(), view.visualRect(combo.model().index(0, 0)).center())
                QtTest.QTest.mouseMove(view.viewport(), view.visualRect(item).center())
                QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                       pos=view.visualRect(item).center())
                self.assertEqual(combo.currentIndex(), 1)
                self.assertFalse(view.isVisible())
                QtTest.QTest.keyClick(combo, QtCore.Qt.Key.Key_Up)
                self.assertEqual(combo.currentIndex(), 0)
                QtTest.QTest.keyClick(combo, QtCore.Qt.Key.Key_Down)
                self.assertEqual(combo.currentIndex(), 1)
                combo.setCurrentIndex(0)
                QtTest.QTest.keyClick(combo, QtCore.Qt.Key.Key_Down,
                                     QtCore.Qt.KeyboardModifier.AltModifier)
                self.app.processEvents()
                self.assertTrue(view.isVisible())
                QtTest.QTest.keyClick(view, QtCore.Qt.Key.Key_Down)
                self.assertEqual(combo.currentIndex(), 0)
                QtTest.QTest.keyClick(view, QtCore.Qt.Key.Key_Return)
                self.assertEqual(combo.currentIndex(), 1)
                self.assertFalse(view.isVisible())

    def test_scrollable_popup_list_keeps_selection_until_explicit_click(self):
        combo = self.prepare("mode")
        # The real settings lists are short. Extra presentation-only entries
        # exercise the same popup view when scrolling is actually available.
        for index in range(30):
            combo.addItem("Presentation fixture " + str(index), index + 10)
        combo.view().setFixedHeight(90)
        combo.setCurrentIndex(0)
        combo.showPopup()
        self.app.processEvents()
        view = combo.view()
        bar = view.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.wheel(view.viewport(), -120, native=False)
        self.assertGreater(bar.value(), 0)
        self.assertEqual(combo.currentIndex(), 0)
        before = bar.value()
        self.wheel(view.viewport(), 120, native=False)
        self.assertLess(bar.value(), before)
        self.assertEqual(combo.currentIndex(), 0)
        self.wheel(view.viewport(), -120, native=False)
        item = view.indexAt(view.viewport().rect().center())
        self.assertTrue(item.isValid())
        self.assertGreater(item.row(), 0)
        QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                               pos=view.visualRect(item).center())
        self.assertEqual(combo.currentIndex(), item.row())

    def test_tab_navigation_reaches_every_enabled_setting(self):
        source = self.prepare("source")
        QtTest.QTest.keyClick(source, QtCore.Qt.Key.Key_Tab)
        self.assertTrue(self.dialog.mode.hasFocus())
        self.prepare("mode")
        visited = set()
        for _ in range(30):
            for name in ("mode", "edge", "level", "pretrigger", "timeout"):
                if getattr(self.dialog, name).hasFocus():
                    visited.add(name)
            QtTest.QTest.keyClick(self.app.focusWidget(), QtCore.Qt.Key.Key_Tab)
        self.assertEqual(visited, {"mode", "edge", "level", "pretrigger", "timeout"})

    def test_record_receives_deliberate_settings_after_scrolling(self):
        self.prepare("mode")
        self.dialog.mode.setCurrentIndex(0)
        self.dialog.edge.setCurrentIndex(1)
        self.dialog.level.setValue(2345)
        self.dialog.pretrigger.setCurrentIndex(2)
        self.dialog.timeout.setValue(5432)
        expected = Config(mode=0, edge=1, level_code=2345,
                          pretrigger_samples=1024, auto_timeout_ms=5432)
        for name in ("mode", "edge", "level", "pretrigger", "timeout"):
            widget = getattr(self.dialog, name)
            widget.setFocus()
            for angle in (-120, 120, 0):
                self.scroll.ensureWidgetVisible(widget)
                self.wheel(widget, angle)
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.dialog._settings(), expected)
        with patch("motortester.gui_acquisition.recovery_directory", return_value=Path("recovery")):
            self.dialog.record_button.click()
        self.assertEqual(len(self.bridge.calls), 1)
        self.assertEqual(self.bridge.calls[0][0:2], ("record", expected))
        self.assertTrue(self.bridge.calls[0][3]["simulate_edge"])

    def test_waveform_wheel_zoom_is_unchanged(self):
        capture = from_message(demo_message())
        self.window.show_capture(capture)
        self.dialog.hide()
        self.window.activateWindow()
        self.app.processEvents()
        view = self.window.plot.getViewBox()
        before = view.viewRange()
        self.wheel(self.window.plot.viewport(), 120)
        after = view.viewRange()
        self.assertLess(after[0][1] - after[0][0], before[0][1] - before[0][0])
        self.assertIs(self.window.capture, capture)
        self.assertEqual(tuple(self.window.curve.getData()[1]), capture.samples)

    def test_archive_segment_navigation_keeps_ordinary_wheel_behavior(self):
        selector = self.window.segment_selector
        selector.addItems(["First segment", "Second segment", "Third segment"])
        selector.show()
        self.dialog.hide()
        self.window.activateWindow()
        selector.setFocus()
        self.app.processEvents()
        selector.setCurrentIndex(1)
        self.wheel(selector, -120)
        self.assertEqual(selector.currentIndex(), 2)
        self.wheel(selector, 120)
        self.assertEqual(selector.currentIndex(), 1)


if __name__ == "__main__":
    unittest.main()

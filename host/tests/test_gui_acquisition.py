"""Author GUI boundary checks with an explicit fake acquisition bridge."""
from dataclasses import replace
import json
import os
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from motortester.gui import MainWindow
from motortester.gui_acquisition import AcquisitionDialog
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


def button_text_geometry(widget):
    option = QtWidgets.QStyleOptionButton()
    widget.initStyleOption(option)
    element = (QtWidgets.QStyle.SubElement.SE_CheckBoxContents
               if isinstance(widget, QtWidgets.QCheckBox)
               else QtWidgets.QStyle.SubElement.SE_PushButtonContents)
    contents = widget.style().subElementRect(element, option, widget)
    # Both the checkbox style and the application's button stylesheet align
    # text left/vertically centered. Do not use TextSingleLine for native labels.
    flags = int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
                | QtCore.Qt.TextFlag.TextShowMnemonic)
    # Measure the complete glyph layout. itemTextRect adds a legacy Windows
    # disabled-text shadow outside this rectangle (SH_EtchDisabledText).
    text = widget.fontMetrics().boundingRect(contents, flags, widget.text())
    return contents, text


def apply_run11_horizontal_metrics(dialog):
    """Synthetic width model, not Windows font/height/rendering emulation.

    Run11 measured 13px per character on these three labels. Use actual Qt
    text metrics and spacing, never forced minimum widths or custom size hints.
    """
    for widget in (dialog.connect_button, dialog.reset, dialog.simulate):
        widget.setStyleSheet("font-family: 'DejaVu Sans Mono';")
        widget.ensurePolished()
        font = widget.font()
        font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 0)
        font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing,
                              13 - QtGui.QFontMetricsF(font).horizontalAdvance("M"))
        widget.setFont(font)


def panel_geometry(app, dialog, scroll):
    """Bounded, ASCII-safe evidence for native-platform layout failures."""
    def size(value):
        return [value.width(), value.height()]

    def rect(value):
        return [value.x(), value.y(), value.width(), value.height()]

    def font(widget):
        actual = QtGui.QFontInfo(widget.font())
        return {"requested": widget.font().toString()[:160],
                "actual_family": actual.family()[:80],
                "actual_point_size": actual.pointSizeF(),
                "actual_pixel_size": actual.pixelSize()}

    def geometry(widget):
        return {"class": type(widget).__name__, "name": widget.objectName()[:80],
                "rect": rect(widget.geometry()), "minimum": size(widget.minimumSize()),
                "maximum": size(widget.maximumSize()),
                "minimum_hint": size(widget.minimumSizeHint()),
                "size_hint": size(widget.sizeHint()), "font": font(widget),
                "font_height": widget.fontMetrics().height(),
                "policy": [widget.sizePolicy().horizontalPolicy().name,
                           widget.sizePolicy().verticalPolicy().name]}

    screen = dialog.screen()
    content = scroll.widget()
    widgets = []
    for widget in content.findChildren(QtWidgets.QWidget):
        if not isinstance(widget, (QtWidgets.QLabel, QtWidgets.QAbstractButton,
                                   QtWidgets.QComboBox, QtWidgets.QLineEdit,
                                   QtWidgets.QAbstractSpinBox)):
            continue
        entry = geometry(widget)
        text = widget.currentText() if isinstance(widget, QtWidgets.QComboBox) else widget.text()
        entry["text"] = text[:100]
        if isinstance(widget, QtWidgets.QLabel):
            entry["word_wrap"] = widget.wordWrap()
            entry["height_for_width"] = widget.heightForWidth(widget.width())
        if isinstance(widget, (QtWidgets.QPushButton, QtWidgets.QCheckBox)):
            contents, text = button_text_geometry(widget)
            entry["text_contents"] = rect(contents)
            entry["text_rect"] = rect(text)
        if isinstance(widget, QtWidgets.QComboBox):
            entry["native_minimum_hint"] = size(QtWidgets.QComboBox.minimumSizeHint(widget))
            option = QtWidgets.QStyleOptionComboBox()
            widget.initStyleOption(option)
            entry["edit_rect"] = rect(widget.style().subControlRect(
                QtWidgets.QStyle.ComplexControl.CC_ComboBox, option,
                QtWidgets.QStyle.SubControl.SC_ComboBoxEditField, widget))
            entry["widest_item_text"] = max(widget.fontMetrics().horizontalAdvance(widget.itemText(i))
                                           for i in range(widget.count()))
        widgets.append(entry)
        if len(widgets) == 48:
            break
    layouts = []
    for layout in content.findChildren(QtWidgets.QLayout)[:12]:
        margins = layout.contentsMargins()
        entry = {"class": type(layout).__name__, "rect": rect(layout.geometry()),
                 "minimum": size(layout.minimumSize()), "size_hint": size(layout.sizeHint()),
                 "spacing": layout.spacing(),
                 "margins": [margins.left(), margins.top(), margins.right(), margins.bottom()],
                 "constraint": layout.sizeConstraint().name}
        if isinstance(layout, QtWidgets.QFormLayout):
            entry["row_wrap"] = layout.rowWrapPolicy().name
            entry["field_growth"] = layout.fieldGrowthPolicy().name
        layouts.append(entry)
    report = {"platform": sys.platform, "python_version": sys.version[:160],
              "qt_version": QtCore.qVersion(),
              "qt_platform": app.platformName(), "style": app.style().objectName(),
              "style_class": app.style().metaObject().className(), "app_font": font(app),
              "logical_dpi": screen.logicalDotsPerInch(),
              "physical_dpi": screen.physicalDotsPerInch(), "dpr": dialog.devicePixelRatioF(),
              "qt_scale_env": {key: os.environ.get(key) for key in
                               ("QT_SCALE_FACTOR", "QT_FONT_DPI", "QT_SCREEN_SCALE_FACTORS")},
              "dialog": geometry(dialog), "viewport": geometry(scroll.viewport()),
              "scroll": geometry(scroll),
              "dialog_style": dialog.style().metaObject().className(),
              "scroll_style": scroll.style().metaObject().className(),
              "content": geometry(content),
              "horizontal_range": [scroll.horizontalScrollBar().minimum(),
                                   scroll.horizontalScrollBar().maximum()],
              "vertical_range": [scroll.verticalScrollBar().minimum(),
                                 scroll.verticalScrollBar().maximum()],
              "layouts": layouts, "widgets": widgets}
    return "ACQUISITION_LAYOUT=" + json.dumps(report, ensure_ascii=True, separators=(",", ":"))


class _PanelDiagnostic:
    """Do not query/cache Qt size hints until an assertion has already failed."""
    def __init__(self, app, dialog, scroll):
        self.app, self.dialog, self.scroll = app, dialog, scroll

    def __str__(self):
        return panel_geometry(self.app, self.dialog, self.scroll)


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
        self.assert_panel_reachable(self.dialog)
        self.assertEqual(self.bridge.calls, [])

    def test_layout_failure_includes_bounded_native_geometry_diagnostics(self):
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps())
        self.dialog.port.setMinimumWidth(600)
        self.dialog.resize(420, 360)
        for _ in range(3):
            self.app.processEvents()
        scroll = self.dialog.findChild(QtWidgets.QScrollArea)
        before = (scroll.horizontalScrollBar().maximum(), scroll.widget().size(),
                  scroll.viewport().size(), self.dialog.port.geometry())
        with self.assertRaises(AssertionError) as failure:
            self.assert_panel_reachable(self.dialog)
        text = str(failure.exception).split("ACQUISITION_LAYOUT=", 1)[1]
        self.assertTrue(text.isascii())
        self.assertLess(len(text), 32768)
        report = json.loads(text)
        self.assertGreater(report["horizontal_range"][1], 0)
        self.assertLessEqual(len(report["widgets"]), 48)
        self.assertLessEqual(len(report["layouts"]), 12)
        self.assertTrue(any(item["class"] == "QLineEdit" and item["minimum"][0] == 600
                            for item in report["widgets"]))
        self.assertTrue(any(item.get("edit_rect") and item.get("widest_item_text")
                            for item in report["widgets"]))
        self.assertIn("actual_family", report["app_font"])
        self.assertIn("logical_dpi", report)
        for _ in range(3):
            self.app.processEvents()
        self.assertEqual(before, (scroll.horizontalScrollBar().maximum(), scroll.widget().size(),
                                  scroll.viewport().size(), self.dialog.port.geometry()))
        self.assertEqual(self.bridge.calls, [])

    def test_successful_layout_does_not_collect_or_cache_failure_diagnostics(self):
        self.bridge.emit_state(Phase.READY, capabilities=advertised_caps())
        with patch(__name__ + ".panel_geometry", side_effect=AssertionError("Unexpected hint probe")) as probe:
            # A wide positive control tests instrumentation independently of
            # the still-failing Windows small-viewport acceptance cases.
            self.assert_panel_reachable(self.dialog, QtCore.QSize(1024, 360))
        probe.assert_not_called()
        self.assertEqual(self.bridge.calls, [])

    def assert_panel_reachable(self, dialog, size=QtCore.QSize(420, 360)):
        dialog.resize(size)
        # LayoutRequest can follow an initial resize/style event on native Qt.
        for _ in range(3):
            self.app.processEvents()
        scroll = dialog.findChild(QtWidgets.QScrollArea)
        self.assertIsNotNone(scroll)
        diagnostic = _PanelDiagnostic(self.app, dialog, scroll)
        self.assertEqual(dialog.size(), size, diagnostic)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0, diagnostic)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0, diagnostic)
        self.assertLessEqual(scroll.widget().width(), scroll.viewport().width(), diagnostic)
        controls = (dialog.source, dialog.port, dialog.mode, dialog.edge,
                    dialog.level, dialog.timeout, dialog.reset, dialog.simulate,
                    dialog.connect_button, dialog.start_button,
                    dialog.stop_button, dialog.disconnect_button)
        labels = tuple(scroll.widget().findChildren(QtWidgets.QLabel))
        for widget in controls + labels:
            with self.subTest(widget=widget.objectName() or type(widget).__name__):
                self.assertGreaterEqual(widget.width(), widget.minimumSizeHint().width(), diagnostic)
                if isinstance(widget, QtWidgets.QLabel) and widget.wordWrap():
                    self.assertGreaterEqual(widget.height(), widget.heightForWidth(widget.width()), diagnostic)
                else:
                    self.assertGreaterEqual(widget.height(), widget.minimumSizeHint().height(), diagnostic)
                if isinstance(widget, (QtWidgets.QPushButton, QtWidgets.QCheckBox)):
                    contents, text = button_text_geometry(widget)
                    self.assertTrue(widget.rect().contains(contents), diagnostic)
                    self.assertTrue(contents.contains(text), diagnostic)
                    self.assertGreaterEqual(text.height(), widget.fontMetrics().height(), diagnostic)
                if isinstance(widget, QtWidgets.QComboBox):
                    option = QtWidgets.QStyleOptionComboBox()
                    widget.initStyleOption(option)
                    edit = widget.style().subControlRect(
                        QtWidgets.QStyle.ComplexControl.CC_ComboBox, option,
                        QtWidgets.QStyle.SubControl.SC_ComboBoxEditField, widget)
                    widest_item = max(widget.fontMetrics().horizontalAdvance(widget.itemText(i))
                                      for i in range(widget.count()))
                    self.assertGreaterEqual(edit.width(), widest_item, diagnostic)
                    self.assertGreaterEqual(edit.height(), widget.fontMetrics().height(), diagnostic)
                # A long CAPS/error label can be taller than the viewport. Each
                # edge must remain reachable, not just its middle or the button.
                for point in (widget.rect().topLeft(), widget.rect().topRight(),
                              widget.rect().bottomLeft(), widget.rect().bottomRight()):
                    target = widget.mapTo(scroll.widget(), point)
                    scroll.ensureVisible(target.x(), target.y(), 1, 1)
                    self.app.processEvents()
                    self.assertTrue(scroll.viewport().rect().contains(
                        widget.mapTo(scroll.viewport(), point)), diagnostic)
        for button in dialog.findChildren(QtWidgets.QPushButton,
                                          options=QtCore.Qt.FindChildOption.FindDirectChildrenOnly):
            self.assertGreaterEqual(button.width(), button.minimumSizeHint().width(), diagnostic)
            self.assertGreaterEqual(button.height(), button.minimumSizeHint().height(), diagnostic)
            self.assertTrue(dialog.rect().contains(button.geometry()), diagnostic)

    def test_small_panel_handles_larger_native_font_before_controls_are_created(self):
        original_font = QtGui.QFont(self.app.font())
        original_style = self.app.style().objectName()
        try:
            # Source combo native minimumSizeHint can reflect this base font
            # even though the inherited GUI stylesheet renders its text at 13px.
            # Old two-column rows overflow by 131px with DejaVu Sans 22pt/Linux;
            # the real Windows run9 showed 129px with unrecorded native metrics.
            for style in ("Fusion", "Windows"):
                if style not in QtWidgets.QStyleFactory.keys():
                    continue
                for points in (9, 14, 18, 22):
                    with self.subTest(style=style, native_font_points=points):
                        self.app.setStyle(style)
                        self.app.setFont(QtGui.QFont("DejaVu Sans", points))
                        bridge = FakeBridge(self.window)
                        dialog = AcquisitionDialog(self.window, bridge)
                        try:
                            dialog.show()
                            bridge.emit_state(Phase.READY, capabilities=advertised_caps())
                            for size in (QtCore.QSize(420, 360), QtCore.QSize(360, 300)):
                                with self.subTest(size=size):
                                    self.assert_panel_reachable(dialog, size)
                            self.assertEqual(bridge.calls, [])
                        finally:
                            dialog.close()
                            dialog.deleteLater()
                            bridge.deleteLater()
                            self.app.processEvents()
        finally:
            self.app.setFont(original_font)
            self.app.setStyle(original_style)

    def test_small_panel_fits_run11_horizontal_text_metrics(self):
        original_style = self.app.style().objectName()
        try:
            for style in ("Fusion", "Windows"):
                if style not in QtWidgets.QStyleFactory.keys():
                    continue
                self.app.setStyle(style)
                apply_run11_horizontal_metrics(self.dialog)
                self.bridge.emit_state(Phase.READY, capabilities=advertised_caps())
                for size in (QtCore.QSize(420, 360), QtCore.QSize(360, 300)):
                    with self.subTest(style=style, size=size):
                        self.assert_panel_reachable(self.dialog, size)
                self.assertEqual(self.bridge.calls, [])
        finally:
            self.app.setStyle(original_style)

    def test_run11_metric_model_rejects_each_original_single_line_constraint(self):
        original_style = self.app.style().objectName()
        try:
            for style in ("Fusion", "Windows"):
                if style not in QtWidgets.QStyleFactory.keys():
                    continue
                self.app.setStyle(style)
                apply_run11_horizontal_metrics(self.dialog)
                self.bridge.emit_state(Phase.READY, capabilities=advertised_caps())
                for widget, measured_width in ((self.dialog.connect_button, 442),
                                               (self.dialog.reset, 390),
                                               (self.dialog.simulate, 377)):
                    original = widget.text()
                    try:
                        widget.setText(original.replace("\n", " "))
                        # Windows style adds one pixel to the checkbox. Allow
                        # that native-style difference, not arbitrary pressure.
                        self.assertAlmostEqual(widget.sizeHint().width(), measured_width, delta=1)
                        for size in (QtCore.QSize(420, 360), QtCore.QSize(360, 300)):
                            with self.subTest(style=style, text=widget.text(), size=size):
                                with self.assertRaises(AssertionError):
                                    self.assert_panel_reachable(self.dialog, size)
                                scroll = self.dialog.findChild(QtWidgets.QScrollArea)
                                self.assertGreater(scroll.horizontalScrollBar().maximum(), 0)
                    finally:
                        widget.setText(original)
                self.assertEqual(self.bridge.calls, [])
        finally:
            self.app.setStyle(original_style)

    def test_multiline_controls_keep_native_click_keyboard_and_accessible_meaning(self):
        original_style = self.app.style().objectName()
        try:
            for style in ("Fusion", "Windows"):
                if style not in QtWidgets.QStyleFactory.keys():
                    continue
                self.app.setStyle(style)
                for metric_model in (False, True):
                    for size in (QtCore.QSize(420, 360), QtCore.QSize(360, 300)):
                        with self.subTest(style=style, metric_model=metric_model, size=size):
                            bridge = FakeBridge(self.window)
                            dialog = AcquisitionDialog(self.window, bridge)
                            try:
                                dialog.show()
                                dialog.activateWindow()
                                if metric_model:
                                    apply_run11_horizontal_metrics(dialog)
                                self.assert_panel_reachable(dialog, size)
                                scroll = dialog.findChild(QtWidgets.QScrollArea)
                                for widget, sentence, source in (
                                        (dialog.reset, "Подтверждаю физический сброс", "device"),
                                        (dialog.simulate, "Событие запуска в эмуляторе", "emulator")):
                                    self.assertIs(type(widget), QtWidgets.QCheckBox)
                                    self.assertEqual(" ".join(widget.text().split()), sentence)
                                    self.assertEqual(len(widget.text().splitlines()), 2)
                                    interface = QtGui.QAccessible.queryAccessibleInterface(widget)
                                    self.assertIsNotNone(interface)
                                    self.assertEqual(interface.role(), QtGui.QAccessible.Role.CheckBox)
                                    self.assertEqual(" ".join(interface.text(QtGui.QAccessible.Text.Name).split()), sentence)
                                    dialog.source.setCurrentIndex(dialog.source.findData(source))
                                    self.assertTrue(widget.isEnabled())
                                    widget.setChecked(False)
                                    toggles = QtTest.QSignalSpy(widget.toggled)
                                    scroll.ensureWidgetVisible(widget)
                                    self.app.processEvents()
                                    _, text = button_text_geometry(widget)
                                    for line, words in enumerate(widget.text().splitlines()):
                                        point = QtCore.QPoint(
                                            text.left() + widget.fontMetrics().horizontalAdvance(words) // 2,
                                            text.top() + (2 * line + 1) * text.height() // 4)
                                        self.assertTrue(text.contains(point))
                                        self.assertTrue(scroll.viewport().rect().contains(
                                            widget.mapTo(scroll.viewport(), point)))
                                        QtTest.QTest.mouseClick(widget, QtCore.Qt.MouseButton.LeftButton, pos=point)
                                        self.assertEqual(toggles.count(), line + 1)
                                        self.assertEqual(widget.isChecked(), line == 0)
                                    self.assertNotEqual(widget.focusPolicy(), QtCore.Qt.FocusPolicy.NoFocus)
                                    widget.setFocus()
                                    self.app.processEvents()
                                    self.assertTrue(widget.hasFocus())
                                    QtTest.QTest.keyClick(widget, QtCore.Qt.Key.Key_Space)
                                    self.assertEqual(toggles.count(), 3)
                                    self.assertTrue(widget.isChecked())
                                self.assertEqual(bridge.calls, [])
                                button = dialog.connect_button
                                self.assertIs(type(button), QtWidgets.QPushButton)
                                sentence = "Подключить · без запуска захвата"
                                self.assertEqual(" ".join(button.text().split()), sentence)
                                interface = QtGui.QAccessible.queryAccessibleInterface(button)
                                self.assertEqual(interface.role(), QtGui.QAccessible.Role.Button)
                                self.assertEqual(" ".join(interface.text(QtGui.QAccessible.Text.Name).split()), sentence)
                                dialog.source.setCurrentIndex(dialog.source.findData("device"))
                                dialog.port.setText("COM7")
                                scroll.ensureWidgetVisible(button)
                                self.app.processEvents()
                                _, text = button_text_geometry(button)
                                point = QtCore.QPoint(text.center().x(), text.top() + 3 * text.height() // 4)
                                self.assertTrue(scroll.viewport().rect().contains(
                                    button.mapTo(scroll.viewport(), point)))
                                QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton, pos=point)
                                self.assertEqual(bridge.calls, [("connect", "device", {"port": "COM7", "reset_confirmed": True})])
                                self.assertFalse(dialog.reset.isChecked())
                                self.assertFalse(button.isEnabled())
                            finally:
                                self.window._acq_requested = False
                                dialog.close()
                                dialog.deleteLater()
                                bridge.deleteLater()
                                self.app.processEvents()
        finally:
            self.app.setStyle(original_style)

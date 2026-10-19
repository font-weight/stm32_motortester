"""Actual buttons + real encoded peer/journal, Qt offscreen (not Windows)."""
from pathlib import Path
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from motortester.gui_stream_controls import StreamControls
from motortester.stream_controller import StreamController
from motortester import stream_recording as storage
from motortester import gui_stream
from qt_test_support import pump_events
from test_stream_controller import EncodedPeer, CONFIG, waveform


class StreamControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stream-controls-")
        self.path = str(Path(self.temp.name) / "buttons.mtstream")
        self.evidence = Path(os.environ.get("STREAM_FLOW_EVIDENCE_DIR", self.temp.name))
        self.peer = EncodedPeer()
        self.controller = StreamController(self.peer, event_config=CONFIG)
        self.panel = StreamControls(self.controller)
        self.panel.resize(1100, 900)
        self.panel.show()
        self.releases = []
        self.app.processEvents()

    def tearDown(self):
        for event in self.releases:
            event.set()
        self.panel.shutdown()
        self.assertTrue(self.controller.join(5))
        deadline = time.monotonic() + 5
        while self.panel.viewer._mailbox.running and time.monotonic() < deadline:
            pump_events(5)
        self.panel.close()
        self.panel.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Qt/worker timeout: " + repr(self.controller.snapshot()))
            pump_events(5)
        self.app.processEvents()

    def click(self, button):
        self.panel.viewer.scroll_area.ensureWidgetVisible(button)
        QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def choose_record(self):
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(self.path, "")):
            self.click(self.panel.record_button)

    def feed(self, count=1):
        before = self.controller.snapshot().validated_blocks
        for _ in range(count):
            self.peer.data()
        self.wait(lambda: self.controller.snapshot().validated_blocks == before + count)

    def stop(self):
        self.click(self.panel.stop_button)
        self.wait(lambda: self.controller.snapshot().phase == "saved" and self.controller.snapshot().can_open
                  and self.panel.viewer.open_button.isEnabled())

    def test_buttons_preview_record_stop_fresh_open_navigation_and_zoom(self):
        self.click(self.panel.preview_button)
        self.wait(lambda: self.controller.snapshot().phase == "preview")
        self.feed(2)
        self.wait(lambda: self.panel.viewer._last_live is not None)
        self.choose_record()
        self.wait(lambda: self.controller.snapshot().phase == "waiting for boundary")
        self.feed(6)
        self.wait(lambda: self.panel.viewer._events and len(self.panel.viewer._events) >= 8)
        self.assertEqual(self.controller.snapshot().bridge.session.selected_start_sample_index, 256)
        self.click(self.panel.viewer.previous_button)
        self.wait(lambda: self.panel.viewer._event is not None)
        event = self.panel.viewer._event
        self.wait(lambda: self.panel.viewer.selection.start == event.start_index and
                         self.panel.viewer._last_live.envelope.viewport.start == event.start_index)
        self.assertEqual(self.panel.viewer.selection.stop, event.end_index)
        old_y = self.panel.viewer.plot.getViewBox().viewRange()[1]
        self.panel.viewer.plot.setYRange(50, 2300, padding=0)
        self.feed(2)
        self.wait(lambda: self.panel.viewer._last_live.end_sample_index == 1280)
        self.assertEqual(self.panel.viewer.plot.getViewBox().viewRange()[1], [50, 2300])
        self.assertNotEqual(old_y, [50, 2300])
        self.stop()
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(self.path, "")):
            self.click(self.panel.viewer.open_button)
        self.wait(lambda: self.panel.viewer._recording is not None and not self.panel.viewer.busy)
        recording = self.panel.viewer._recording
        self.assertEqual((recording.first_sample_index, recording.end_sample_index), (256, 1280))
        self.assertEqual(b"".join(b.raw_u16le for b in recording.iter_raw()), self.peer.raw[512:])
        self.assertEqual(len(self.panel.viewer._events), 8)
        self.click(self.panel.viewer.whole_button)
        self.wait(lambda: not self.panel.viewer.busy)
        self.assertEqual((self.panel.viewer.selection.start, self.panel.viewer.selection.stop), (256, 1280))
        self.click(self.panel.viewer.next_button)
        self.wait(lambda: not self.panel.viewer.busy)
        self.assertIsNotNone(self.panel.viewer._event)

    def test_record_chooser_cancel_admits_nothing_and_direct_empty_open(self):
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.click(self.panel.record_button)
        pump_events(40)
        self.assertEqual(self.peer.commands, [])
        self.assertIsNone(self.controller.snapshot().bridge)
        self.choose_record()
        self.wait(lambda: self.controller.snapshot().phase == "recording")
        self.stop()
        self.assertTrue(self.panel.open_path(self.path))
        self.wait(lambda: self.panel.viewer._recording is not None and not self.panel.viewer.busy)
        self.assertEqual(self.panel.viewer._recording.first_sample_index, self.panel.viewer._recording.end_sample_index)
        self.assertIsNone(self.panel.viewer.selection)
        self.assertFalse(self.panel.viewer.next_button.isEnabled())

    def test_real_prepare_block_keeps_gui_heartbeat_and_reserved_stop(self):
        entered, release, beat = threading.Event(), threading.Event(), threading.Event()
        self.releases.append(release)
        original = storage.PreparedStreamWriter.prepare
        def held(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test prepare barrier timeout")
            return original(*args, **kwargs)
        timer = QtCore.QTimer(self.panel)
        timer.setInterval(5)
        timer.timeout.connect(beat.set)
        with patch.object(storage.PreparedStreamWriter, "prepare", held):
            self.choose_record()
            self.wait(entered.is_set)
            beat.clear(); timer.start()
            self.click(self.panel.stop_button)
            self.wait(lambda: beat.is_set() and not self.controller.snapshot().stop_pending)
            self.assertTrue(self.controller.snapshot().worker_unresolved)
            self.assertFalse(self.panel.open_path(self.path))
            self.assertFalse(self.panel.record_button.isEnabled())
            release.set()
            self.wait(lambda: self.controller.snapshot().bridge.session.finalized and self.controller.snapshot().can_open)
        timer.stop()
        self.assertFalse(any(command[0] == 8 for command in self.peer.commands))
        self.assertTrue(self.panel.open_path(self.path))
        self.wait(lambda: not self.panel.viewer.busy)
        self.assertIsNone(self.panel.viewer._recording)
        self.assertIn("R не установлена", self.panel.viewer.storage_label.text())

    def test_settings_wheel_protection_keyboard_edits_and_narrow_scroll(self):
        for pixels in (13, 22):
            with self.subTest(pixels=pixels):
                self.panel.viewer.setStyleSheet(self.panel.viewer.styleSheet() + f"\nQWidget {{ font-size: {pixels}px; }}")
                self.panel.resize(420, 420)
                pump_events(40)
                spin = self.panel.fields["threshold_code"]
                self.panel.viewer.scroll_area.ensureWidgetVisible(spin)
                self.app.processEvents()
                spin.setFocus()
                before = spin.value()
                pos = QtCore.QPointF(spin.rect().center())
                event = QtGui.QWheelEvent(pos, spin.mapToGlobal(pos.toPoint()), QtCore.QPoint(), QtCore.QPoint(0, 120),
                    QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
                    QtCore.Qt.ScrollPhase.NoScrollPhase, False)
                self.app.sendEvent(spin, event)
                self.assertEqual(spin.value(), before)
                QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Up)
                self.assertEqual(spin.value(), before + 1)
                spin.setValue(before)
                bar = self.panel.viewer.scroll_area.verticalScrollBar()
                self.assertGreater(bar.maximum(), 0)
                self.panel.viewer.scroll_area.ensureWidgetVisible(self.panel.viewer.status_label)
                self.app.processEvents()
                rect = self.panel.viewer.status_label.rect()
                center = self.panel.viewer.status_label.mapTo(self.panel.viewer.scroll_area.viewport(), rect.center())
                self.assertTrue(self.panel.viewer.scroll_area.viewport().rect().contains(center))
                output = self.evidence
                self.assertTrue(self.panel.grab().save(str(output / f"controls-{pixels}px-bottom.png")))
                for name, widget in (("preview", self.panel.preview_button), ("record", self.panel.record_button),
                                     ("stop", self.panel.stop_button), ("polarity", self.panel.polarity),
                                     ("rail", self.panel.fields["rail_high"]), ("open", self.panel.viewer.open_button)):
                    self.panel.viewer.scroll_area.ensureWidgetVisible(widget)
                    self.app.processEvents()
                    location = widget.mapTo(self.panel.viewer.scroll_area.viewport(), widget.rect().center())
                    self.assertTrue(self.panel.viewer.scroll_area.viewport().rect().contains(location), name)
                    self.assertTrue(self.panel.grab().save(str(output / f"controls-{pixels}px-{name}.png")))


    def test_live_event_leaving_ring_keeps_original_context_and_explicit_unavailable(self):
        self.click(self.panel.preview_button)
        self.wait(lambda: self.controller.snapshot().phase == "preview")
        self.feed()
        self.wait(lambda: len(self.panel.viewer._events) == 1)
        self.click(self.panel.viewer.previous_button)
        self.wait(lambda: self.panel.viewer._event is not None)
        event = self.panel.viewer._event
        # Preview-only removes disk pacing from this finite retention exercise.
        self.feed(600)
        self.wait(lambda: self.panel.viewer._last_live.end_sample_index == 601 * 128)
        self.assertEqual(self.panel.viewer._event, event)
        self.assertEqual((self.panel.viewer.selection.start, self.panel.viewer.selection.stop),
                         (event.start_index, event.end_index))
        self.assertIn("недоступен", self.panel.viewer.status_label.text())
        self.assertIn("не свидетельство потери raw", self.panel.viewer.status_label.text())
        raw_x = self.panel.viewer.curve.getData()[0]
        self.assertTrue(raw_x is None or len(raw_x) == 0)
        self.assertEqual(self.controller.snapshot().ring_samples, 65536)

    def test_plot_wheel_zoom_and_below_polarity_config_are_live(self):
        self.panel.polarity.setCurrentIndex(1)
        self.panel.fields["threshold_code"].setValue(500)
        self.panel.fields["release_code"].setValue(1000)
        self.click(self.panel.preview_button)
        self.wait(lambda: self.controller.snapshot().phase == "preview")
        self.assertFalse(self.panel.settings.isEnabled())
        import struct
        self.peer.data(struct.pack("<128H", *([2000] * 20 + [100] * 8 + [2000] * 100)))
        self.wait(lambda: self.panel.viewer._last_live is not None and bool(self.panel.viewer._events))
        self.assertEqual(self.panel.viewer._last_live.event_index.config.polarity, "below")
        self.assertIn("мс", self.panel.context_label.text())
        self.assertIn("синтетический", self.panel.settings.title())
        self.panel.viewer.scroll_area.ensureWidgetVisible(self.panel.viewer.plot)
        self.app.processEvents()
        old = self.panel.viewer.plot.getViewBox().viewRange()[0]
        viewport = self.panel.viewer.plot.viewport()
        point = QtCore.QPointF(viewport.rect().center())
        event = QtGui.QWheelEvent(point, viewport.mapToGlobal(point.toPoint()), QtCore.QPoint(), QtCore.QPoint(0, 120),
            QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
            QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(viewport, event)
        self.app.processEvents()
        self.assertNotEqual(self.panel.viewer.plot.getViewBox().viewRange()[0], old)
        self.assertFalse(self.panel.viewer.follow_tail)
        self.click(self.panel.viewer.follow_button)
        self.assertTrue(self.panel.viewer.follow_tail)

    def test_held_fresh_Open_keeps_GUI_heartbeat_and_old_read_cannot_replace_new_live_source(self):
        self.choose_record()
        self.wait(lambda: self.controller.snapshot().phase == "recording")
        self.feed(2); self.stop()
        old_identity = self.controller.snapshot().bridge.identity
        entered, release, beat = threading.Event(), threading.Event(), threading.Event()
        self.releases.append(release)
        original = gui_stream._open_archive
        def held(path):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("read barrier timeout")
            return original(path)
        timer = QtCore.QTimer(self.panel)
        timer.setInterval(5); timer.timeout.connect(beat.set)
        with patch.object(gui_stream, "_open_archive", held):
            self.assertTrue(self.panel.open_path(self.path))
            self.wait(entered.is_set)
            timer.start(); self.wait(beat.is_set)
            self.click(self.panel.preview_button)
            self.wait(lambda: self.controller.snapshot().phase == "preview" and
                      self.controller.snapshot().bridge.identity != old_identity)
            self.feed(2)
            self.wait(lambda: self.panel.viewer._identity == gui_stream.identity_key(self.controller.snapshot().bridge.identity))
            release.set()
            self.wait(lambda: self.panel.viewer._last_live is not None and not self.panel.viewer.busy)
        timer.stop()
        self.assertIsNone(self.panel.viewer._recording)
        self.assertNotEqual(self.panel.viewer._identity, gui_stream.identity_key(old_identity))


    def test_actual_Record_Stop_Open_controls_at_13_and_22px_narrow_viewport(self):
        for pixels in (13, 22):
            with self.subTest(pixels=pixels):
                self.path = str(Path(self.temp.name) / f"narrow-{pixels}.mtstream")
                self.panel.viewer.setStyleSheet(self.panel.viewer.styleSheet() + f"\nQWidget {{ font-size: {pixels}px; }}")
                self.panel.resize(420, 420)
                self.wait(lambda: self.panel.record_button.isEnabled())
                self.choose_record()
                self.wait(lambda: self.controller.snapshot().phase == "recording")
                self.feed(2)
                self.stop()
                with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(self.path, "")):
                    self.click(self.panel.viewer.open_button)
                self.wait(lambda: self.panel.viewer._recording is not None and not self.panel.viewer.busy)
                self.assertEqual(self.panel.viewer._recording.end_sample_index, 256)
                self.click(self.panel.viewer.previous_button)
                self.wait(lambda: self.panel.viewer._event is not None and not self.panel.viewer.busy)
                self.panel.viewer.scroll_area.ensureWidgetVisible(self.panel.viewer.plot)
                self.app.processEvents()
                self.assertTrue(self.panel.grab().save(str(self.evidence / f"narrow-flow-{pixels}px.png")))

    def test_actual_append_and_finish_barriers_keep_GUI_alive_and_Stop_separate(self):
        for method in ("append", "finish"):
            with self.subTest(method=method):
                self.path = str(Path(self.temp.name) / f"held-{method}.mtstream")
                self.wait(lambda: self.panel.record_button.isEnabled())
                self.choose_record()
                self.wait(lambda: self.controller.snapshot().phase == "recording")
                entered, release, beat = threading.Event(), threading.Event(), threading.Event()
                self.releases.append(release)
                original = getattr(storage.StreamWriter, method)
                def held(writer, *args, **kwargs):
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("GUI real storage barrier timeout")
                    return original(writer, *args, **kwargs)
                timer = QtCore.QTimer(self.panel)
                timer.setInterval(5); timer.timeout.connect(beat.set)
                with patch.object(storage.StreamWriter, method, held):
                    self.peer.data()
                    self.wait(lambda: self.controller.snapshot().validated_blocks == 1)
                    if method == "finish":
                        self.click(self.panel.stop_button)
                    self.wait(entered.is_set)
                    beat.clear(); timer.start()
                    self.click(self.panel.stop_button)
                    self.wait(lambda: beat.is_set() and self.controller.snapshot().bridge.session.stop_acknowledgement is not None)
                    self.assertFalse(self.controller.snapshot().bridge.session.clean_completion_reported)
                    self.assertFalse(self.panel.open_path(self.path))
                    self.assertFalse(self.panel.record_button.isEnabled())
                    release.set()
                    self.wait(lambda: self.controller.snapshot().phase == "saved" and self.panel.record_button.isEnabled())
                timer.stop()
                self.assertEqual(storage.recover_attempt(self.path).recording.end_sample_index, 128)

    def test_finite_nominal_cadence_load_records_exact_raw_with_coalesced_full_event_index(self):
        import json
        import statistics
        import struct
        for name, value in (("pre_samples", 2), ("post_samples", 2),
                            ("baseline_samples", 1), ("max_event_samples", 16)):
            self.panel.fields[name].setValue(value)
        self.choose_record()
        self.wait(lambda: self.controller.snapshot().phase == "recording")
        raw = struct.pack("<128H", *(([100] * 12 + [2000] * 2 + [100] * 2) * 8))
        done, cancel = threading.Event(), threading.Event()
        offered_times, producer_errors = [], []
        self.releases.append(cancel)
        def feed_at_nominal_cadence():
            start = time.monotonic()
            try:
                for n in range(640):
                    if cancel.wait(max(0, start + n * .00256 - time.monotonic())):
                        return
                    self.peer.data(raw)
                    offered_times.append(time.monotonic())
            except Exception as exc:
                producer_errors.append(type(exc).__name__)
            finally:
                done.set()
        producer = threading.Thread(target=feed_at_nominal_cadence, daemon=True)
        producer.start()
        try:
            self.wait(lambda: done.is_set() and self.controller.snapshot().validated_blocks == 640, timeout=10)
        finally:
            cancel.set(); producer.join(3)
        self.assertFalse(producer.is_alive())
        self.assertEqual(producer_errors, [])
        self.stop()
        self.wait(lambda: self.panel.viewer._last_live is not None and
                  self.panel.viewer._last_live.end_sample_index == 640 * 128)
        status = self.controller.snapshot()
        self.assertIsNone(status.bridge.session.first_failure)
        self.assertEqual(status.detector_status.next_ordinal, 5120)
        self.assertEqual(status.detector_status.indexed_events, 4096)
        self.assertEqual(status.detector_status.evicted_events, 1024)
        self.assertLessEqual(status.peak_raw_items, status.raw_item_limit)
        self.assertEqual(b"".join(b.raw_u16le for b in storage.recover_attempt(self.path).recording.iter_raw()), raw * 640)
        intervals = [(b - a) * 1000 for a, b in zip(offered_times, offered_times[1:])]
        measurement = {"kind": "independent Python producer thread, finite Linux offscreen 640x128, attempted wall-clock 2.56ms pacing",
            "offered_blocks": len(offered_times), "offered_samples": len(self.peer.raw) // 2,
            "producer_duration_s": offered_times[-1] - offered_times[0],
            "producer_interval_p50_ms": statistics.median(intervals),
            "producer_interval_p95_ms": sorted(intervals)[int(len(intervals) * .95)],
            "producer_interval_max_ms": max(intervals),
            "received_blocks": status.validated_blocks, "received_samples": status.validated_samples,
            "selected_R": status.bridge.session.selected_start_sample_index,
            "queued_end": status.bridge.session.queued_end_sample_index,
            "writer_accepted_end": status.bridge.session.accepted_end_sample_index,
            "checkpointed_end": status.bridge.session.checkpointed_end_sample_index,
            "finalized_end": status.bridge.session.finalized_end_sample_index,
            "raw_queue_remaining": status.bridge.session.raw_items_held,
            "writer_clean_completion_reported": status.bridge.session.clean_completion_reported,
            "receiver_steps": status.receiver_steps, "max_service_gap_ms": status.max_service_gap_ms,
            "peak_raw_items_including_inflight": status.peak_raw_items, "peak_raw_bytes": status.peak_raw_bytes,
            "displays_built": status.displays_built, "displays_replaced": status.displays_replaced,
            "event_count": status.detector_status.next_ordinal, "retained_events": status.detector_status.indexed_events,
            "physical_usb_or_windows_guarantee": False}
        print("FINITE_JOINED_LOAD " + json.dumps(measurement, sort_keys=True))


    def test_numeric_fields_enabled_disabled_focus_contrast_at_13_and_22px(self):
        def luminance(color):
            components = [getattr(color, channel)() / 255 for channel in ("red", "green", "blue")]
            linear = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in components]
            return sum(x * weight for x, weight in zip(linear, (.2126, .7152, .0722)))
        for pixels in (13, 22):
            self.panel.viewer.setStyleSheet(self.panel.viewer.styleSheet() +
                                            "\nQWidget { font-size: %dpx; }" % pixels)
            self.panel.resize(420, 420)
            for state in ("enabled", "disabled", "focus"):
                for name, widget in self.panel.fields.items():
                    with self.subTest(pixels=pixels, state=state, field=name):
                        widget.setEnabled(state != "disabled")
                        self.panel.viewer.scroll_area.ensureWidgetVisible(widget)
                        if state == "focus":
                            widget.setFocus()
                            widget.lineEdit().deselect()
                        else:
                            widget.clearFocus()
                        self.app.processEvents()
                        palette = widget.palette()
                        group = (QtGui.QPalette.ColorGroup.Disabled if state == "disabled"
                                 else QtGui.QPalette.ColorGroup.Active)
                        fg = palette.color(group, QtGui.QPalette.ColorRole.Text)
                        bg = palette.color(group, QtGui.QPalette.ColorRole.Base)
                        low, high = sorted((luminance(fg), luminance(bg)))
                        self.assertGreaterEqual((high + .05) / (low + .05), 4.5)
                        capture = widget.grab()
                        image = capture.toImage()
                        colors = [image.pixelColor(x, y).name() for y in range(image.height())
                                  for x in range(image.width())]
                        # Inspect actual field pixels, not only declared CSS.
                        self.assertGreater(colors.count(bg.name()), 10)
                        # A one-digit 13 px glyph may have only one fully solid
                        # foreground pixel; count its near-solid antialiased ink.
                        ink = sum(max(abs(QtGui.QColor(color).red() - fg.red()),
                                      abs(QtGui.QColor(color).green() - fg.green()),
                                      abs(QtGui.QColor(color).blue() - fg.blue())) <= 15
                                  for color in colors)
                        self.assertGreater(ink, 2)
                        if state == "focus":
                            self.assertTrue(widget.hasFocus())
                            self.assertGreater(colors.count("#d7ebff"), 10)
                        self.assertTrue(capture.save(str(self.evidence /
                            f"numeric-{pixels}-{state}-{name}.png")))
                        widget.setEnabled(True)
            # Native step arrow remains clickable after the local palette fix.
            widget = self.panel.fields["threshold_code"]
            self.panel.viewer.scroll_area.ensureWidgetVisible(widget)
            before = widget.value()
            option = QtWidgets.QStyleOptionSpinBox()
            widget.initStyleOption(option)
            rect = widget.style().subControlRect(QtWidgets.QStyle.ComplexControl.CC_SpinBox,
                option, QtWidgets.QStyle.SubControl.SC_SpinBoxUp, widget)
            QtTest.QTest.mouseClick(widget, QtCore.Qt.MouseButton.LeftButton, pos=rect.center())
            self.assertEqual(widget.value(), before + 1)


if __name__ == "__main__":
    unittest.main()

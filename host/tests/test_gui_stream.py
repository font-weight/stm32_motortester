"""Synthetic real-Qt tests for the isolated reusable stream panel (not Windows)."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from motortester.gui_stream import (DETAIL_SAMPLES, POINT_BUDGET, SCAN_SAMPLES,
                                   SUMMARY_BINS, LiveSnapshot, LiveEventIndex, Selection,
                                   StreamViewerPanel, _Scan)
from motortester.stream_model import (FailureEvidence, StreamBlock, StreamError, StreamIdentity,
                                     StreamPreparation, StreamStart, StreamTerminal, UnboundOutcome)
from motortester.stream_recording import (PreparedStreamWriter, StreamRecording, StreamWriter,
                                         recover, recover_attempt)
from motortester.stream_session import SessionLimits, SourceEnd, StreamSession
from motortester.stream_bridge import StreamBridge
from motortester.stream_provenance import DMA_DOMAIN, SYNTHETIC_DMA_PROFILE, provenance_text
from motortester.stream_view import EventConfig, EventDetector, Viewport, minmax_envelope
from qt_test_support import pump_events
from test_stream_session import (IDENTITY, _RealStorageProbe, ack, block, end,
                                 started, stopping)
import test_stream_bridge as bridge_fixture


def config():
    return EventConfig(1000, 500, "above", 8, 12, 3, 0, 256, 0, 65535)


class StreamPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stream-panel-")
        self.folder = Path(self.temp.name)
        self.panel = StreamViewerPanel(event_config=config())
        self.panel.resize(1300, 850)
        self.panel.show()
        self.app.processEvents()
        self.releases = []

    def tearDown(self):
        for release in self.releases:
            release.set()
        self.panel.shutdown()
        deadline = time.monotonic() + 5
        while self.panel._mailbox.running and time.monotonic() < deadline:
            pump_events(5)
        self.panel.close()
        self.panel.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Qt/worker timeout")
            pump_events(5)
        self.app.processEvents()

    def settle(self):
        self.wait(lambda: not self.panel.busy)

    def test_declared_dma_live_bound_empty_no_r_and_legacy_reopen(self):
        profile = SYNTHETIC_DMA_PROFILE
        for name, empty, no_r, unknown in (("clean", False, False, False),
                ("unknown", False, False, True), ("empty", True, False, False),
                ("no-r", False, True, False), ("no-r-unknown", False, True, True)):
            with self.subTest(name=name):
                path = self.folder / (name + ".mtstream")
                bridge = StreamBridge(name, producer_profile=profile)
                self.addCleanup(lambda b=bridge: bridge_fixture.BridgeTests.shutdown(self, b))
                peer = bridge_fixture.Peer(bridge); peer.negotiate()
                disk = lambda: bridge_fixture.BridgeTests.disk(self, bridge)
                if no_r:
                    bridge.preview(0); peer.started(); peer.data()
                    bridge.record(path, name, 0); bridge.dispatch_storage(0)
                else:
                    bridge.record(path, name, 0); disk(); peer.started(); disk()
                    if not empty:
                        peer.data()
                identity = bridge.snapshot().identity
                self.panel.begin_live(identity)
                self.assertIn(DMA_DOMAIN, self.panel.source_label.text())
                self.assertIn("не калиброванное физическое время", self.panel.source_label.text())
                peer.stop(); peer.end(reason=int(unknown), flags=3 if unknown else 0); peer.ack()
                if no_r:
                    bridge.storage_tick(0)
                disk()
                bridge_fixture.BridgeTests.shutdown(self, bridge)
                # The panel reads the saved file through its real open worker.
                self.panel.open_path(path); self.settle()
                source, status = self.panel.source_label.text(), self.panel.storage_label.text()
                self.assertIn(provenance_text(identity), source)
                self.assertIn("ADC_DR overwrite", source)
                self.assertIn("пропущенные триггеры", source)
                self.assertIn("не аутентификация", source)
                self.assertIn(DMA_DOMAIN, status)
                self.assertIn("completed DMA end", status)
                self.assertIn("номинальная Fs", self.panel.plot.getAxis("bottom").labelText)
                if no_r:
                    self.assertIsNone(self.panel.selection)
                    self.assertIn("R не установлена", status)
                if unknown:
                    self.assertIn("потери, заявлено: неизвестны", status)
                    self.assertNotIn("Чистое завершение", status)
                elif not no_r:
                    self.assertIn("Чистое завершение заявлено только в домене", status)
                self.assertIn("прежнего fsync", status)
                if name == "clean":
                    stylesheet = self.panel.styleSheet()
                    for font in (13, 22):
                        self.panel.setStyleSheet(stylesheet + f"\nQWidget {{font-size: {font}px;}}")
                        self.panel.resize(900, 600); pump_events(100)
                        for label in (self.panel.source_label, self.panel.storage_label):
                            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                            self.panel.scroll_area.ensureWidgetVisible(label, 0, 0); pump_events(20)
                            area = self.panel.scroll_area
                            bottom = label.mapTo(area.viewport(), QtCore.QPoint(0, label.height())).y()
                            self.assertLessEqual(bottom, area.viewport().height())
                    self.panel.setStyleSheet(stylesheet); self.panel.resize(1300, 850)
        legacy = self.recording([1, 2, 3], name="legacy-after-dma.mtstream")
        self.panel.open_path(legacy.path); self.settle()
        self.assertNotIn(DMA_DOMAIN, self.panel.source_label.text())
        self.assertNotIn(DMA_DOMAIN, self.panel.storage_label.text())
        self.assertIn("Новый DMA-домен не заявлен", self.panel.source_label.text())
        # A hash-consistent but unknown tagged producer must not reopen under
        # the legacy interpretation or retain the previous clean display.
        saved = path.read_bytes()
        magic, size = struct.unpack("<8sI", saved[:12])
        header = json.loads(saved[12:12 + size])
        header["preparation"]["identity"]["application_version"] = "producer:unknown"
        metadata = json.dumps(header, separators=(",", ":")).encode()
        prefix = struct.pack("<8sI", magic, len(metadata))
        bad_path = self.folder / "unknown-producer.mtstream"
        bad_path.write_bytes(prefix + metadata + hashlib.sha256(prefix + metadata).digest())
        self.panel.open_path(bad_path); self.settle()
        self.assertIsNone(self.panel._recording)
        self.assertIn("Ошибка open", self.panel.status_label.text())
        self.assertNotIn("Чистое завершение", self.panel.storage_label.text())

    def recording(self, samples, *, name="synthetic.mtstream", first=0, generation=0,
                  terminal=True, clean=True):
        identity = StreamIdentity("synthetic-session", generation, 7, 50000, "synthetic")
        path = self.folder / name
        writer = StreamWriter.create(path, StreamStart(identity, first, name))
        for at in range(0, len(samples), 8192):
            part = samples[at:at + 8192]
            writer.append(StreamBlock(identity, first + at, struct.pack(f"<{len(part)}H", *part)))
        if terminal:
            end = first + len(samples)
            writer.finish(StreamTerminal(identity, end, "stopped", clean,
                                         end if clean else None, end if clean else None,
                                         0 if clean else None, stop_requested=clean))
        else:
            writer.checkpoint()
            writer.close()
        return recover(path)

    def load(self, recording):
        self.panel.load_recording(recording)
        self.settle()

    def wheel(self, delta, modifiers=QtCore.Qt.KeyboardModifier.NoModifier):
        viewport = self.panel.plot.viewport()
        point = QtCore.QPointF(viewport.rect().center())
        event = QtGui.QWheelEvent(point, viewport.mapToGlobal(point.toPoint()),
                                 QtCore.QPoint(0, 0), QtCore.QPoint(0, delta),
                                 QtCore.Qt.MouseButton.NoButton, modifiers,
                                 QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(viewport, event)
        self.app.processEvents()

    def test_saved_synthetic_open_exact_indexes_dc_and_last_sample(self):
        first = (1 << 53) + 101
        recording = self.recording([32768] * 100 + [65535], first=first)
        self.panel.open_path(recording.path)
        self.settle()
        self.assertEqual(self.panel.selection, Selection(first, first + 101))
        self.assertIn("СИНТЕТИЧЕСКИЙ", self.panel.origin_label.text())
        x, y = self.panel.curve.getData()
        self.assertEqual(x[0], 0)
        self.assertAlmostEqual(x[-1], 100 / 50000)
        self.assertEqual(y[-1], 65535)
        self.assertIn(str(first), self.panel.range_label.text())
        self.assertIn("Fs ном.", self.panel.range_label.text())
        self.assertIn("0 отсчётов", self.panel.storage_label.text())
        self.assertEqual(self.panel._summary.points[0].sample_index, first)
        self.assertEqual(self.panel._summary.points[-1].sample_index, first + 100)

    def test_event_previous_next_full_context_and_explicit_truncation(self):
        samples = [100] * 32 + [2000] * 20 + [100] * 80 + [3000] * 15 + [100] * 70 + [4000] * 10
        recording = self.recording(samples)
        self.load(recording)
        self.assertEqual(len(self.panel._events), 3)
        QtTest.QTest.mouseClick(self.panel.next_button, QtCore.Qt.MouseButton.LeftButton)
        self.settle()
        event = self.panel._events[0]
        self.assertEqual(self.panel.selection, Selection(event.start_index, event.end_index))
        self.assertLess(event.start_index, event.trigger_index)
        self.assertGreater(event.end_index, 52)
        QtTest.QTest.mouseClick(self.panel.next_button, QtCore.Qt.MouseButton.LeftButton)
        self.settle()
        self.assertEqual(self.panel._event.ordinal, 1)
        QtTest.QTest.mouseClick(self.panel.previous_button, QtCore.Qt.MouseButton.LeftButton)
        self.settle()
        self.assertEqual(self.panel._event.ordinal, 0)
        self.panel.next_event(); self.panel.next_event(); self.settle()
        self.assertIn("right_truncated", self.panel.event_label.text())
        self.assertIn("tail_ambiguous", self.panel.event_label.text())
        self.assertIn("не подтверждение", self.panel.event_label.text())

    def test_summary_bounded_full_range_extrema_and_raw_limit(self):
        count = DETAIL_SAMPLES * 3 + 35
        samples = [100] * count
        for at in (1, 70001, count - 2):
            samples[at] = 65535
        recording = self.recording(samples)
        reads = []
        original = StreamRecording.iter_raw
        def traced(reader, start=None, end=None, chunk_samples=8192):
            reads.append((start, end, threading.current_thread().ident))
            yield from original(reader, start, end, chunk_samples)
        with patch.object(StreamRecording, "iter_raw", traced):
            self.load(recording)
            self.panel.show_whole_recording(); self.settle()
            self.assertTrue(self.panel._summary.complete)
            self.assertLessEqual(len(self.panel._summary.points), SUMMARY_BINS * 4)
            self.assertEqual(self.panel._summary.covered_until, count)
            self.assertIn("ОБЗОР", self.panel.range_label.text())
            self.assertEqual(max(self.panel.curve.getData()[1]), 65535)
            before = len(reads)
            self.panel.select_range(69990, 70020); self.settle()
            self.assertIn("RAW DETAIL", self.panel.range_label.text())
            self.assertEqual(len(reads), before + 1)
        self.assertTrue(all(end - start <= SCAN_SAMPLES for start, end, _ in reads))
        self.assertTrue(all(worker != threading.current_thread().ident for _, _, worker in reads))
        self.assertEqual(self.panel.selection, Selection(69990, 70020))

    def test_summary_progress_is_incomplete_until_final_batch(self):
        recording = self.recording([123] * (SCAN_SAMPLES + 10))
        scan = _Scan(recording, config(), 2)
        first = scan.step(threading.Event())
        self.assertFalse(first.summary.complete)
        self.assertEqual(first.summary.covered_until, SCAN_SAMPLES)
        second = scan.step(threading.Event())
        self.assertTrue(second.summary.complete)
        self.assertEqual(second.summary.covered_until, SCAN_SAMPLES + 10)
        cancelled = threading.Event(); cancelled.set()
        scan = _Scan(recording, config(), 2)
        self.assertIsNone(scan.step(cancelled))

    def test_event_capacity_does_not_stop_overview_or_claim_whole_index(self):
        self.panel.shutdown(); self.panel.close()
        self.panel = StreamViewerPanel(event_config=config(), event_capacity=1)
        self.panel.show()
        recording = self.recording(([100] * 30 + [2000] * 10 + [100] * 30) * 5)
        self.load(recording)
        self.assertTrue(self.panel._summary.complete)
        self.assertEqual(len(self.panel._events), 1)
        self.assertIn("event_capacity", self.panel.index_label.text())
        self.assertIn("Незавершённое", self.panel.index_label.text())

    def test_unknown_terminal_separate_from_complete_envelope(self):
        recording = self.recording([100] * 200, terminal=False)
        self.load(recording)
        self.assertIn("complete", self.panel.range_label.text())
        self.assertIn("missing_terminal", self.panel.storage_label.text())
        self.assertIn("хвост неизвестен", self.panel.storage_label.text())
        self.assertIn("потери, заявлено: неизвестны", self.panel.storage_label.text())
        self.assertIn("unknown_loss", self.panel.index_label.text())

    def test_rapid_navigation_coalesces_and_rejects_stale_render(self):
        recording = self.recording(list(range(2000)))
        self.load(recording)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = StreamRecording.iter_raw
        calls = []
        def blocked(reader, start=None, end=None, chunk_samples=8192):
            calls.append((start, end))
            if start == 10:
                entered.set(); release.wait(5)
            yield from original(reader, start, end, chunk_samples)
        with patch.object(StreamRecording, "iter_raw", blocked):
            self.panel.select_range(10, 110)
            self.wait(entered.is_set)
            for start in range(100, 1000):
                self.panel.select_range(start, start + 100)
            self.assertIsNotNone(self.panel._pending)
            release.set()
            self.settle()
        self.assertEqual(calls, [(10, 110), (999, 1099)])
        self.assertEqual(self.panel.selection, Selection(999, 1099))
        self.assertEqual(self.panel.curve.getData()[1][0], 999)

    def test_source_swap_same_identity_rejects_old_file(self):
        old = self.recording([111] * 200, name="old.mtstream")
        new = self.recording([222] * 200, name="new.mtstream")
        self.load(old)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = StreamRecording.iter_raw
        def blocked(reader, start=None, end=None, chunk_samples=8192):
            if reader is old:
                entered.set(); release.wait(5)
            yield from original(reader, start, end, chunk_samples)
        with patch.object(StreamRecording, "iter_raw", blocked):
            self.panel.select_range(1, 100)
            self.wait(entered.is_set)
            self.panel.load_recording(new)
            release.set(); self.settle()
        self.assertIs(self.panel._recording, new)
        self.assertEqual(set(self.panel.curve.getData()[1]), {222})
        self.assertIn("new.mtstream", self.panel.source_label.text())

    def test_close_while_reader_blocked_does_not_wait_or_revive(self):
        recording = self.recording([111] * 200)
        self.load(recording)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = StreamRecording.iter_raw
        def blocked(reader, start=None, end=None, chunk_samples=8192):
            entered.set(); release.wait(5)
            yield from original(reader, start, end, chunk_samples)
        with patch.object(StreamRecording, "iter_raw", blocked):
            self.panel.select_range(10, 100)
            self.wait(entered.is_set)
            started = time.monotonic()
            self.panel.close()
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertTrue(self.panel._closed)
            release.set()
            self.wait(lambda: not self.panel._mailbox.running)
        self.assertIsNone(self.panel._mailbox.result)
        self.assertIn("отменён", self.panel.index_label.text())

    def test_delete_without_close_worker_has_no_qobject_callback(self):
        recording = self.recording([111] * 200)
        self.load(recording)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = StreamRecording.iter_raw
        def blocked(reader, start=None, end=None, chunk_samples=8192):
            entered.set(); release.wait(5)
            yield from original(reader, start, end, chunk_samples)
        with patch.object(StreamRecording, "iter_raw", blocked):
            self.panel.select_range(10, 100)
            self.wait(entered.is_set)
            mailbox = self.panel._mailbox
            self.panel.deleteLater()
            self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
            self.assertTrue(mailbox.closed)
            release.set(); self.wait(lambda: not mailbox.running)
            self.assertIsNone(mailbox.result)
        self.panel = StreamViewerPanel()

    def live_snapshot(self, recording, sequence, start, stop, **kw):
        env = minmax_envelope(recording, Viewport(start, stop), POINT_BUDGET)
        return LiveSnapshot(recording.identity, sequence, recording.first_sample_index,
                            stop, env, **kw)

    def test_live_latest_only_sequence_generation_and_follow_pause(self):
        recording = self.recording(list(range(1000)))
        handle = self.panel.begin_live(recording.identity)
        for sequence in range(1, 51):
            snapshot = self.live_snapshot(recording, sequence, sequence, sequence + 100)
            self.assertTrue(self.panel.post_live_snapshot(handle, snapshot))
        self.settle()
        self.assertEqual(self.panel.selection, Selection(50, 150))
        self.assertEqual(self.panel._latest_live_sequence, 50)
        self.assertFalse(self.panel.post_live_snapshot(handle, snapshot))
        self.panel.select_range(60, 100)
        self.assertFalse(self.panel.follow_tail)
        self.panel.post_live_snapshot(handle, self.live_snapshot(recording, 51, 60, 160))
        self.settle()
        self.assertEqual(self.panel.selection, Selection(60, 100))
        self.assertIn("удерживается", self.panel.status_label.text())
        other = replace(recording.identity, generation=1)
        new_handle = self.panel.begin_live(other)
        self.assertNotEqual(new_handle, handle)
        self.assertFalse(self.panel.post_live_snapshot(handle, self.live_snapshot(recording, 52, 60, 160)))
        self.assertFalse(self.panel.post_live_snapshot(new_handle, snapshot))

    def test_live_vertical_range_survives_tail_updates_and_manual_zoom(self):
        recording = self.recording(list(range(2000)))
        handle = self.panel.begin_live(recording.identity)
        self.panel.post_live_snapshot(handle, self.live_snapshot(recording, 1, 0, 1000))
        self.settle()
        self.panel.plot.setYRange(100, 500, padding=0)
        self.panel.post_live_snapshot(handle, self.live_snapshot(recording, 2, 10, 1010))
        self.settle()
        self.assertEqual(self.panel.plot.getViewBox().viewRange()[1], [100, 500])
        self.assertEqual(self.panel.selection, Selection(10, 1010))
        old_width = self.panel.selection.width
        self.wheel(120)
        self.assertLess(self.panel.selection.width, old_width)
        self.assertFalse(self.panel.follow_tail)

    def test_actual_drag_and_wheel_change_selection_without_resetting_y(self):
        recording = self.recording(list(range(5000)))
        self.load(recording)
        self.panel.select_range(1000, 3000); self.settle()
        self.panel.plot.setYRange(100, 1000, padding=0)
        self.wheel(120); self.settle()
        self.assertLess(self.panel.selection.width, 2000)
        viewport = self.panel.plot.viewport()
        center = viewport.rect().center()
        before = self.panel.selection
        QtTest.QTest.mousePress(viewport, QtCore.Qt.MouseButton.LeftButton, pos=center)
        QtTest.QTest.mouseMove(viewport, center + QtCore.QPoint(80, 0), delay=30)
        QtTest.QTest.mouseRelease(viewport, QtCore.Qt.MouseButton.LeftButton, pos=center + QtCore.QPoint(80, 0))
        self.settle()
        self.assertNotEqual(self.panel.selection, before)
        y_before = self.panel.plot.getViewBox().viewRange()[1]
        self.panel.select_range(2000, 3000); self.settle()
        self.assertEqual(self.panel.plot.getViewBox().viewRange()[1], y_before)

    def test_dc_raw_extent_and_live_empty_complete_rejected(self):
        recording = self.recording([32000] * 101)
        self.load(recording)
        x, y = self.panel.curve.getData()
        self.assertGreater(len(x), 1)
        self.assertEqual(x[0], 0)
        self.assertEqual(x[-1], 100 / 50000)
        self.assertEqual(set(y), {32000})
        snapshot = self.live_snapshot(recording, 1, 0, 101)
        with self.assertRaises(ValueError):
            replace(snapshot, envelope=replace(snapshot.envelope, segments=()))
        with self.assertRaises(ValueError):
            replace(snapshot, envelope=replace(snapshot.envelope,
                segments=(snapshot.envelope.segments[0][:-1],)))

    def test_live_partial_range_keeps_both_unavailable_boundaries(self):
        recording = self.recording([100] * 100, first=10)
        env = minmax_envelope(recording, Viewport(0, 120), POINT_BUDGET)
        self.assertEqual(len(env.discontinuities), 2)
        snapshot = LiveSnapshot(recording.identity, 1, 10, 110, env)
        self.assertEqual(snapshot.envelope.outcome, "partial_range")
        handle = self.panel.begin_live(recording.identity)
        self.panel.follow_tail = False
        self.panel.post_live_snapshot(handle, snapshot)
        self.settle()
        self.assertIn("partial_range", self.panel.range_label.text())
        self.assertIn("unavailable_prefix", self.panel.range_label.text())
        self.assertIn("unavailable_suffix", self.panel.range_label.text())

    def test_live_maximum_wheel_zoom_out_is_legal_and_response_applies(self):
        recording = self.recording([100] * 200000)
        handle = self.panel.begin_live(recording.identity)
        initial = self.live_snapshot(recording, 1, 200000 - DETAIL_SAMPLES, 200000)
        self.panel.post_live_snapshot(handle, initial); self.settle()
        requests = []
        self.panel.view_requested.connect(requests.append)
        self.wheel(-120)
        self.assertTrue(requests)
        requested = requests[-1]
        self.assertEqual(requested, self.panel.selection)
        self.assertLessEqual(requested.width, DETAIL_SAMPLES)
        self.assertIn("ограничен", self.panel.status_label.text())
        self.assertIn(str(DETAIL_SAMPLES), self.panel.status_label.text())
        x_range = self.panel.plot.getViewBox().viewRange()[0]
        self.assertAlmostEqual(x_range[1] - x_range[0], requested.width / 50000)
        response = LiveSnapshot(recording.identity, 2, 0, 200000,
            minmax_envelope(recording, Viewport(requested.start, requested.stop), POINT_BUDGET))
        self.assertTrue(self.panel.post_live_snapshot(handle, response)); self.settle()
        self.assertEqual(self.panel.selection, requested)
        self.assertIn("complete", self.panel.range_label.text())
        self.assertEqual(self.panel.curve.getData()[0][0], 0)
        self.assertIn("ограничен", self.panel.status_label.text())
        # A smaller legal selection can still be requested normally.
        self.panel.select_range(requested.start, requested.start + 100)
        self.assertEqual(requests[-1].width, 100)
        self.assertNotIn("ограничен", self.panel.status_label.text())

    def test_known_source_failure_survives_cached_navigation_until_new_source(self):
        recording = self.recording([100] * 100000)
        self.load(recording)
        self.panel.show_whole_recording(); self.settle()
        with recording.path.open("ab") as stream:
            stream.write(b"X")
        self.panel.select_range(0, 100); self.settle()
        failure = self.panel.status_label.text()
        self.assertIn("Ошибка plot", failure)
        self.assertIn("измен", failure.lower())
        for _ in range(2):
            self.panel.show_whole_recording(); self.settle()
            self.assertIn(failure, self.panel.status_label.text())
            self.assertIn("ИСТОЧНИК ОТКЛОНЁН", self.panel.storage_label.text())
            self.assertIn("ИСТОЧНИК ОТКЛОНЁН", self.panel.origin_label.text())
            self.assertIn("кеш", self.panel.status_label.text())
        replacement = self.recording([222] * 1000, name="replacement.mtstream")
        self.panel.open_path(replacement.path); self.settle()
        self.assertIsNone(self.panel._source_failure)
        self.assertNotIn("ОТКЛОНЁН", self.panel.storage_label.text())
        self.assertNotIn(failure, self.panel.status_label.text())
        self.assertEqual(set(self.panel.curve.getData()[1]), {222})

    def test_late_failure_of_same_source_survives_newer_cached_selection(self):
        recording = self.recording([100] * 100000)
        self.load(recording)
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original = StreamRecording.iter_raw
        def blocked(reader, start=None, end=None, chunk_samples=8192):
            entered.set(); release.wait(5)
            yield from original(reader, start, end, chunk_samples)
        with patch.object(StreamRecording, "iter_raw", blocked):
            self.panel.select_range(10, 100)
            self.wait(entered.is_set)
            with recording.path.open("ab") as stream:
                stream.write(b"X")
            # This invalidates only the old plot request, not its source.
            self.panel.show_whole_recording()
            release.set(); self.settle()
        self.assertIn("Ошибка plot", self.panel.status_label.text())
        self.assertIn("ИСТОЧНИК ОТКЛОНЁН", self.panel.storage_label.text())
        self.assertIn("ОБЗОР", self.panel.range_label.text())

    def test_scroll_content_keeps_labels_readable_at_small_size_and_large_font(self):
        recording = self.recording([100] * 70000, first=(1 << 60) + 123)
        self.load(recording)
        stylesheet = self.panel.styleSheet()
        for font in (13, 22):
            self.panel.setStyleSheet(stylesheet + f"\nQWidget {{font-size: {font}px;}}")
            for width, height in ((1000, 710), (800, 600), (600, 400)):
                with self.subTest(font=font, width=width, height=height):
                    self.panel.resize(width, height)
                    pump_events(250)
                    self.assertEqual((self.panel.width(), self.panel.height()), (width, height))
                    self.assertGreaterEqual(self.panel.range_label.y(),
                                            self.panel.plot.y() + self.panel.plot.height())
                    for name in ("source_label", "range_label", "storage_label", "index_label",
                                 "event_label", "status_label"):
                        label = getattr(self.panel, name)
                        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()), name)
                        self.assertLessEqual(label.y() + label.height(), getattr(self.panel, "content", self.panel).height())
                    # The lowest status remains reachable by the ordinary scroll
                    # container, even when content is taller than the viewport.
                    if hasattr(self.panel, "scroll_area"):
                        area = self.panel.scroll_area
                        area.ensureWidgetVisible(self.panel.status_label, 0, 0)
                        pump_events(25)
                        bottom = self.panel.status_label.mapTo(area.viewport(),
                            QtCore.QPoint(0, self.panel.status_label.height())).y()
                        self.assertLessEqual(bottom, area.viewport().height())
                        area.verticalScrollBar().setValue(0)
        if hasattr(self.panel, "scroll_area"):
            self.assertGreater(self.panel.scroll_area.verticalScrollBar().maximum(), 0)

    def test_bad_open_empty_and_malformed_live_do_not_fabricate_data(self):
        bad = self.folder / "bad.mtstream"; bad.write_bytes(b"bad")
        self.panel.open_path(bad); self.settle()
        self.assertIsNone(self.panel.selection)
        self.assertIn("Ошибка open", self.panel.status_label.text())
        empty = self.recording([])
        self.load(empty)
        self.assertIsNone(self.panel.selection)
        self.assertIn("Пустая", self.panel.status_label.text())
        good = self.recording([100] * 100, name="good.mtstream")
        snapshot = self.live_snapshot(good, 1, 0, 100)
        with self.assertRaises(ValueError):
            replace(snapshot, envelope=replace(snapshot.envelope, samples_read=99))
        with self.assertRaises(ValueError):
            replace(snapshot, flags=["mutable"])

    def assert_no_waveform(self):
        self.assertIsNone(self.panel.selection)
        x, y = self.panel.curve.getData()
        self.assertTrue(x is None or len(x) == 0)
        self.assertTrue(y is None or len(y) == 0)
        self.assertFalse(self.panel.whole_button.isEnabled())
        self.assertFalse(self.panel.follow_button.isEnabled())
        self.assertFalse(self.panel.previous_button.isEnabled())
        self.assertFalse(self.panel.next_button.isEnabled())
        self.assertEqual(self.panel._events, ())

    def attempt_recording(self, samples, *, name="attempt.mtstream", first=0):
        prepared = PreparedStreamWriter.prepare(self.folder / name,
            StreamPreparation(IDENTITY, name, "next_block"))
        self.addCleanup(prepared.close)
        writer = prepared.bind_first_block(prepared.readiness_token,
            StreamBlock(IDENTITY, first, struct.pack(f"<{min(len(samples), 8192)}H", *samples[:8192])))
        self.addCleanup(writer.close)
        for at in range(8192, len(samples), 8192):
            part = samples[at:at + 8192]
            writer.append(StreamBlock(IDENTITY, first + at, struct.pack(f"<{len(part)}H", *part)))
        last = first + len(samples)
        writer.finish(StreamTerminal(IDENTITY, last, "stopped", True,
                                     last, last, 0, stop_requested=True))
        return recover_attempt(writer.path)

    def test_v2_real_session_preview_selected_start_reopens_exact_raw(self):
        first = (1 << 60) + 123
        session = StreamSession(IDENTITY, "preview-attempt")
        probe = _RealStorageProbe(session, self.folder / "session-preview.mtstream")
        session.preview(); started(session, first)
        session.receive(block(first, 3))  # Preview is deliberately excluded.
        session.record("recording-1"); probe.step()
        session.receive(block(first + 3, 5)); probe.step()
        session.receive(block(first + 8, 4))
        stop = stopping(session); end(session, first + 12); ack(session, stop, first + 12)
        probe.drain()
        self.assertTrue(session.snapshot().clean_completion_reported)
        self.panel.open_path(probe.path); self.settle()
        recording = self.panel._recording
        self.assertEqual(self.panel.selection, Selection(first + 3, first + 12))
        self.assertEqual(recording.read_raw(first + 3, 9), block(first + 3, 9).raw_u16le)
        self.assertEqual(recording.terminal, probe.jobs[-1].terminal)
        self.assertIn(f"R = {first + 3}", self.panel.storage_label.text())
        self.assertIn("caller_next_whole_validated_block", self.panel.storage_label.text())
        self.assertEqual(self.panel._summary.points[-1].sample_index, first + 11)
        self.assertAlmostEqual(self.panel.curve.getData()[0][-1], 8 / 50000)

    def test_v2_real_session_direct_start_empty_is_established_interval(self):
        first = (1 << 60) + 321
        session = StreamSession(IDENTITY, "empty-attempt")
        probe = _RealStorageProbe(session, self.folder / "session-empty.mtstream")
        session.record("empty-recording"); probe.step(); started(session, first); probe.step()
        stop = stopping(session); end(session, first); ack(session, stop, first); probe.drain()
        self.panel.open_path(probe.path); self.settle()
        self.assert_no_waveform()
        self.assertIsNotNone(self.panel._recording)
        self.assertEqual((self.panel._first, self.panel._end), (first, first))
        self.assertTrue(self.panel._recording.clean_stop_reported)
        self.assertIn(f"[{first}, {first})", self.panel.storage_label.text())
        self.assertIn("caller_validated_source_start", self.panel.storage_label.text())
        self.assertIn("Терминал: stopped", self.panel.storage_label.text())
        self.assertIn("Пустая запись", self.panel.status_label.text())

    def test_v2_real_session_no_r_outcomes_are_status_only(self):
        for state in ("ended_unbound", "cancelled_unbound", "failed_unbound", "prepared"):
            with self.subTest(state=state):
                session = StreamSession(IDENTITY, "unbound-attempt")
                probe = _RealStorageProbe(session, self.folder / f"{state}.mtstream")
                if state == "ended_unbound":
                    session.preview(); started(session, 12)
                session.record(state); probe.step()
                if state == "ended_unbound":
                    stop = stopping(session); end(session, 12); ack(session, stop, 12)
                elif state == "cancelled_unbound":
                    session.cancel()
                elif state == "failed_unbound":
                    session.fault(FailureEvidence("source_error", "START failed", "START", 8, 4))
                if state == "prepared":
                    probe.prepared.close()
                else:
                    probe.drain()
                self.panel.open_path(probe.path); self.settle()
                self.assert_no_waveform()
                self.assertIsNone(self.panel._recording)
                self.assertIsNone(self.panel._first)
                self.assertIsNone(self.panel._end)
                text = self.panel.storage_label.text()
                self.assertIn("R не установлена", text)
                self.assertNotIn("raw-префикс [", text)
                self.assertIn("fsync", text)
                if state == "prepared":
                    self.assertIn("терминальный исход неизвестен", text)
                    self.assertIn("не доказывает, что END опередил DATA", text)
                    self.assertIn("missing_selection_or_terminal", text)
                else:
                    self.assertIn(state, text)
                if state == "failed_unbound":
                    self.assertIn("Первый отказ: source_error: START failed", text)
                    self.assertIn("операция: START; request id: 8; source code: 4", text)
                if state == "ended_unbound":
                    self.assertIn("generated end: 12; transmitted end: 12", text)

    def test_v2_session_source_counts_and_first_cause_remain_separate(self):
        for local_first in (False, True):
            for source_loss in (7, 0, None):
                with self.subTest(local_first=local_first, loss=source_loss):
                    session = StreamSession(IDENTITY, "loss-attempt", SessionLimits(10, 1))
                    probe = _RealStorageProbe(session,
                        self.folder / f"loss-{local_first}-{source_loss}.mtstream")
                    session.record("loss-recording"); probe.step(); started(session); probe.step()
                    session.receive(block(0, 5)); probe.step()
                    if local_first:
                        session.receive(block(5, 5))
                        with self.assertRaises(StreamError):
                            session.receive(block(10, 1))
                    source_end = 11 if local_first else 5
                    source_failure = FailureEvidence("source_overrun", "reported source loss", source_code=3)
                    terminal = StreamTerminal(IDENTITY, source_end, "source_failure", True,
                        None if source_loss is None else source_end + source_loss,
                        source_end, source_loss, "source_counter_only", source_failure)
                    session.source_end(SourceEnd("loss-end", terminal))
                    stop = session.take_control_action()
                    ack(session, stop, source_end, terminal_id="loss-end"); probe.drain()
                    self.panel.open_path(probe.path); self.settle()
                    recording = self.panel._recording
                    self.assertEqual(recording.end_sample_index, 10 if local_first else 5)
                    self.assertEqual(recording.terminal.loss_samples, source_loss)
                    self.assertFalse(recording.clean_stop_reported)
                    text = self.panel.storage_label.text()
                    expected_loss = "неизвестны" if source_loss is None else str(source_loss)
                    self.assertIn(f"потери, заявлено: {expected_loss}", text)
                    self.assertIn("не сумма потерь файла", text)
                    self.assertIn(f"transmitted end: {source_end}", text)
                    self.assertIn("Первый отказ: " + ("capacity_raw" if local_first else "source_overrun"), text)
                    self.assertIn("Полнота хвоста не подтверждена", text)

    def test_v2_readable_prepare_or_terminal_is_not_prior_fsync_success(self):
        for stage in ("prepare", "finish"):
            with self.subTest(stage=stage):
                session = StreamSession(IDENTITY, "io-attempt")
                probe = _RealStorageProbe(session, self.folder / f"fsync-{stage}.mtstream")
                session.record("io-recording")
                if stage == "finish":
                    probe.step(); started(session); probe.step()
                    session.receive(block(0, 3)); probe.step()
                    stop = stopping(session); end(session, 3); ack(session, stop, 3)
                with patch("motortester.stream_recording._sync", side_effect=OSError("synthetic fsync failure")):
                    with self.assertRaises(OSError):
                        probe.step()
                self.assertFalse(session.snapshot().finalized)
                self.panel.open_path(probe.path); self.settle()
                self.assertIn("не доказывают успешный возврат прежнего fsync", self.panel.storage_label.text())
                if stage == "prepare":
                    self.assert_no_waveform()
                    self.assertIn("терминальный исход неизвестен", self.panel.storage_label.text())
                else:
                    self.assertTrue(session.snapshot().finalization_unresolved)
                    self.assertTrue(self.panel._recording.clean_stop_reported)
                    self.assertEqual(self.panel._recording.read_raw(0, 3), block(0, 3).raw_u16le)

    def test_v2_incomplete_header_selection_blocks_and_terminal_keep_recovery_meaning(self):
        first = (1 << 60) + 17
        for policy in ("next_block", "source_start"):
            path = self.folder / f"source-{policy}.mtstream"
            prep = PreparedStreamWriter.prepare(path, StreamPreparation(IDENTITY, policy, policy))
            header = path.read_bytes()
            if policy == "source_start":
                writer = prep.bind_start(prep.readiness_token, StreamStart(IDENTITY, first, policy))
            else:
                writer = prep.bind_first_block(prep.readiness_token, block(first, 4))
            selected = path.read_bytes()
            writer.checkpoint()
            checkpoint = path.read_bytes()
            last = first if policy == "source_start" else first + 4
            writer.finish(StreamTerminal(IDENTITY, last, "stopped", True,
                                         last, last, 0, stop_requested=True))
            finished = path.read_bytes()
            cases = (("header-torn", header[:-1], None, False, "Ошибка open"),
                     ("header-only", header, None, False, "missing_selection_or_terminal"),
                     ("selection-torn", selected[:-1], None, False, "invalid_tail"),
                     ("selection-only", selected, first, False,
                      "selection_without_commit" if policy == "source_start" else "uncheckpointed_tail"),
                     ("terminal-torn", finished[:-1], first, policy == "next_block", "invalid_tail"))
            for name, data, selected_r, has_recording, expected in cases:
                with self.subTest(policy=policy, stage=name):
                    fixture = self.folder / f"{policy}-{name}.mtstream"; fixture.write_bytes(data)
                    self.panel.open_path(fixture); self.settle()
                    self.assertEqual(self.panel._recording is not None, has_recording)
                    text = self.panel.storage_label.text() + self.panel.status_label.text()
                    self.assertIn(expected, text)
                    if not has_recording:
                        self.assert_no_waveform()
                    if selected_r is not None:
                        self.assertIn(f"R = {first}", text)
                    elif name != "header-torn":
                        self.assertIn("R не установлена", text)
                    if has_recording:
                        self.assertEqual(self.panel._recording.read_raw(first, 4), block(first, 4).raw_u16le)
                        self.assertIsNone(self.panel._recording.terminal)
            self.assertGreaterEqual(len(checkpoint), len(selected))

    def test_v2_strict_dispatch_rejects_bad_header_symlink_and_open_mutation(self):
        report = self.attempt_recording([10] * 20)
        from motortester import gui_stream
        with patch.object(gui_stream, "recover", wraps=recover) as old_reader, \
                patch.object(gui_stream, "recover_attempt", wraps=recover_attempt) as new_reader:
            self.panel.open_path(report.path); self.settle()
            old_reader.assert_not_called(); new_reader.assert_called_once()
        damaged = self.folder / "bad-v2.mtstream"
        data = bytearray(report.path.read_bytes()); data[20] ^= 1; damaged.write_bytes(data)
        with patch.object(gui_stream, "recover", wraps=recover) as old_reader:
            self.panel.open_path(damaged); self.settle()
            self.assert_no_waveform(); self.assertIn("Ошибка open", self.panel.status_label.text())
            old_reader.assert_not_called()
        link = self.folder / "redirect.mtstream"; link.symlink_to(report.path)
        self.panel.open_path(link); self.settle()
        self.assert_no_waveform(); self.assertIn("Ошибка open", self.panel.status_label.text())
        original = recover_attempt
        def mutate_after_recovery(path):
            result = original(path)
            with Path(path).open("ab") as stream:
                stream.write(b"X")
            return result
        with patch.object(gui_stream, "recover_attempt", mutate_after_recovery):
            self.panel.open_path(report.path); self.settle()
        self.assert_no_waveform(); self.assertIn("измен", self.panel.status_label.text())

    def test_v2_torn_unbound_terminal_does_not_claim_its_outcome(self):
        path = self.folder / "unbound-terminal.mtstream"
        prepared = PreparedStreamWriter.prepare(path,
            StreamPreparation(IDENTITY, "unbound-terminal", "next_block"))
        prepared.finish_unbound(prepared.readiness_token, UnboundOutcome(
            "failed_unbound", FailureEvidence("source_error", "source failed before selection")))
        path.write_bytes(path.read_bytes()[:-1])
        self.panel.open_path(path); self.settle()
        self.assert_no_waveform()
        text = self.panel.storage_label.text()
        self.assertIn("R не установлена", text)
        self.assertIn("invalid_tail", text)
        self.assertIn("терминальный исход неизвестен", text)
        self.assertNotIn("failed_unbound", text)

    def test_v2_mutated_source_failure_stays_through_cached_overview(self):
        report = self.attempt_recording([100] * (DETAIL_SAMPLES + 100))
        self.panel.open_path(report.path); self.settle()
        self.panel.show_whole_recording(); self.settle()
        with report.path.open("ab") as stream:
            stream.write(b"X")
        self.panel.select_range(0, 100); self.settle()
        failure = self.panel._source_failure
        self.assertIsNotNone(failure)
        self.panel.show_whole_recording(); self.settle()
        self.assertEqual(self.panel._source_failure, failure)
        self.assertIn("ОТКЛОНЁН", self.panel.storage_label.text())
        self.assertIn("ОБЗОР", self.panel.range_label.text())

    def test_v2_stale_status_only_open_cannot_replace_new_source_or_survive_close(self):
        path = self.folder / "status-only.mtstream"
        prepared = PreparedStreamWriter.prepare(path, StreamPreparation(IDENTITY, "pending", "next_block"))
        prepared.close()
        replacement = self.attempt_recording([222] * 50, name="replacement-v2.mtstream")
        for close in (False, True):
            with self.subTest(close=close):
                entered, release = threading.Event(), threading.Event()
                self.releases.append(release)
                def blocked(source):
                    result = recover_attempt(source)
                    entered.set(); release.wait(5)
                    return result
                with patch("motortester.gui_stream.recover_attempt", blocked):
                    self.panel.open_path(path); self.wait(entered.is_set)
                    if close:
                        self.panel.shutdown()
                    else:
                        self.panel.open_path(replacement.path)
                    release.set()
                    self.wait(lambda: not self.panel._mailbox.running)
                    if not close:
                        self.settle()
                        self.assertEqual(set(self.panel.curve.getData()[1]), {222})
                        self.assertNotIn("pending", self.panel.source_label.text())
                    else:
                        self.assertIsNone(self.panel._mailbox.result)
                        self.assertIsNone(self.panel._recording)


    def event_snapshot(self, recording, handle, detector, sequence, *, available=None,
                       start=None, stop=None, revision=7):
        end = detector.status.processed_until
        available = recording.first_sample_index if available is None else available
        start = available if start is None else start
        stop = end if stop is None else stop
        class Ring:
            identity = recording.identity
            first_sample_index = available
            end_sample_index = end
            iter_raw = staticmethod(recording.iter_raw)
        env = minmax_envelope(Ring(), Viewport(start, stop), POINT_BUDGET)
        index = LiveEventIndex(handle, sequence, revision, detector.config,
                               detector.retained_events, detector.status, detector.event_capacity)
        return LiveSnapshot(recording.identity, sequence, recording.first_sample_index,
                            end, env, loss_samples=0, tail_state="running",
                            raw_available_start=available, event_index=index)

    def feed_recording(self, detector, recording, start=None, stop=None, chunk=7):
        start = recording.first_sample_index if start is None else start
        stop = recording.end_sample_index if stop is None else stop
        for part in recording.iter_raw(start, stop, chunk_samples=chunk):
            detector.feed(part)

    def test_recent_live_global_ordinal_navigation_and_exact_large_ranges(self):
        first = 2**60
        recording = self.recording(([100]*32+[2000]*5+[100]*40)*3, first=first)
        detector = EventDetector(config(), 2, retention="recent")
        self.feed_recording(detector, recording)
        handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
        snapshot = self.event_snapshot(recording, handle, detector, 1)
        self.assertTrue(self.panel.post_live_snapshot(handle, snapshot)); self.settle()
        self.assertEqual([e.ordinal for e in self.panel._events], [1, 2])
        self.assertIn("вытеснено метаданных: 1", self.panel.index_label.text())
        self.assertIn("Config revision 7", self.panel.index_label.text())
        self.assertTrue(self.panel.next_button.isEnabled())
        requests = []
        self.panel.view_requested.connect(requests.append)
        self.panel.next_event()
        event = detector.event(1)
        self.assertEqual(requests, [Selection(event.start_index, event.end_index)])
        self.assertEqual(self.panel._event.ordinal, 1)
        self.assertFalse(self.panel.follow_tail)
        response = self.event_snapshot(recording, handle, detector, 2,
                                       start=event.start_index, stop=event.end_index)
        self.assertTrue(self.panel.post_live_snapshot(handle, response)); self.settle()
        self.assertEqual(self.panel.curve.getData()[0][0], 0)
        self.assertAlmostEqual(self.panel.curve.getData()[0][-1], (event.end_index-event.start_index-1)/50000)
        self.panel.next_event(); self.assertEqual(self.panel._event.ordinal, 2)
        self.panel.previous_event(); self.assertEqual(self.panel._event.ordinal, 1)

    def test_recent_live_missing_prehistory_never_clamps_or_requests_other_samples(self):
        recording = self.recording([100]*32+[2000]*5+[100]*40, first=1000)
        detector = EventDetector(config(), 2, retention="recent")
        self.feed_recording(detector, recording)
        event = detector.event(0)
        available = event.start_index+1
        handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
        snapshot = self.event_snapshot(recording, handle, detector, 1, available=available)
        self.panel.post_live_snapshot(handle, snapshot); self.settle()
        requests = []
        self.panel.view_requested.connect(requests.append)
        self.panel.next_event()
        self.assertEqual(requests, [])
        self.assertEqual(self.panel.selection, Selection(event.start_index, event.end_index))
        self.assertEqual(self.panel._event, event)
        self.assertIn("недоступен в live-памяти", self.panel.status_label.text())
        self.assertIn("не свидетельство потери raw", self.panel.status_label.text())
        self.assertIn("unavailable", self.panel.range_label.text())
        self.assertEqual(len(self.panel.curve.getData()[0] or []), 0)
        self.panel.post_live_snapshot(handle, self.event_snapshot(recording, handle, detector, 2, available=available))
        self.settle()
        self.assertEqual(requests, [])
        self.assertEqual(self.panel._event, event)
        self.assertIn("потери, заявлено: 0", self.panel.storage_label.text())

    def test_recent_live_selected_event_eviction_requires_deliberate_choice(self):
        recording = self.recording(([100]*32+[2000]*5+[100]*40)*3)
        detector = EventDetector(config(), 2, retention="recent")
        self.feed_recording(detector, recording, stop=77)
        handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
        self.panel.post_live_snapshot(handle, self.event_snapshot(recording, handle, detector, 1)); self.settle()
        self.panel.next_event()
        old = self.panel._event
        self.panel.post_live_snapshot(handle, self.event_snapshot(recording, handle, detector, 2,
            start=old.start_index, stop=old.end_index)); self.settle()
        self.feed_recording(detector, recording, start=77)
        requests = []
        self.panel.view_requested.connect(requests.append)
        snapshot = self.event_snapshot(recording, handle, detector, 3, available=131)
        self.panel.post_live_snapshot(handle, snapshot); self.settle()
        self.assertEqual(self.panel._event, old)
        self.assertEqual(self.panel.selection, Selection(old.start_index, old.end_index))
        self.assertEqual(requests, [])
        self.assertIn("вытеснен", self.panel.status_label.text())
        self.panel._set_status("Выбор удерживается")
        self.assertIn("вытеснен", self.panel.status_label.text())
        self.panel.next_event()
        self.assertEqual(self.panel._event.ordinal, 1)
        self.assertEqual(requests, [])  # Its prehistory also left the ring.
        self.panel.next_event()
        self.assertEqual(self.panel._event.ordinal, 2)
        self.assertEqual(requests, [Selection(detector.event(2).start_index, detector.event(2).end_index)])
        self.assertIsNone(self.panel._event_notice)

    def test_recent_live_payload_immutability_coherence_and_source_bounds(self):
        recording = self.recording(([100]*32+[2000]*5+[100]*40)*3)
        detector = EventDetector(config(), 2, retention="recent")
        self.feed_recording(detector, recording)
        handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
        snapshot = self.event_snapshot(recording, handle, detector, 1)
        index = snapshot.event_index
        mutations = [dict(events=list(index.events)), dict(capacity=4097),
            dict(handle=replace(handle, identity=("wrong",))),
            dict(handle=replace(handle, identity=(["mutable"],)+handle.identity[1:])) ,
            dict(status=replace(index.status, evicted_events=0)),
            dict(status=replace(index.status, next_ordinal=99)),
            dict(status=replace(index.status, first_retained_ordinal=0)),
            dict(status=replace(index.status, pending=index.events[-1])),
            dict(events=(replace(index.events[0], flags=["mutable"]), index.events[1])),
            dict(events=(replace(index.events[0], end_index=10000), index.events[1])),
            dict(events=(index.events[1], index.events[0])),
            dict(events=(replace(index.events[0], ordinal=100), index.events[1]))]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                replace(index, **mutation)
        for mutation in (dict(raw_available_start=None), dict(raw_available_start=-1),
                dict(raw_available_start=recording.end_sample_index+1),
                dict(event_index=replace(index, sequence=2)),
                dict(event_index=replace(index, status=replace(index.status, processed_until=recording.end_sample_index-1)))):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                replace(snapshot, **mutation)
        with self.assertRaises(ValueError):
            replace(snapshot, raw_available_start=1)  # Existing pixels claim an evicted sample.
        empty = EventDetector(config(), 2, retention="recent")
        empty_index = LiveEventIndex(handle, 1, 7, config(), (), empty.status, 2)
        self.assertEqual(empty_index.status.next_ordinal, 0)
        with self.assertRaises(ValueError):
            replace(empty_index, status=replace(empty.status, next_ordinal=1))

    def test_recent_live_latest_only_stale_config_source_and_regressions(self):
        recording = self.recording(([100]*32+[2000]*5+[100]*40)*3)
        detector = EventDetector(config(), 2, retention="recent")
        self.feed_recording(detector, recording)
        handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
        for sequence in range(1, 51):
            snapshot = self.event_snapshot(recording, handle, detector, sequence)
            self.assertTrue(self.panel.post_live_snapshot(handle, snapshot))
        self.settle()
        self.assertEqual(self.panel._latest_live_sequence, 50)
        self.assertEqual(self.panel._events, detector.retained_events)
        self.assertFalse(self.panel.post_live_snapshot(handle, snapshot))
        wrong_config = replace(snapshot, sequence=51,
            event_index=replace(snapshot.event_index, sequence=51, config_revision=6))
        self.assertFalse(self.panel.post_live_snapshot(handle, wrong_config))
        wrong_settings = replace(wrong_config, event_index=replace(wrong_config.event_index,
            config_revision=7, config=replace(config(), threshold_code=1001)))
        self.assertFalse(self.panel.post_live_snapshot(handle, wrong_settings))
        missing = replace(snapshot, sequence=51, event_index=None)
        self.assertFalse(self.panel.post_live_snapshot(handle, missing))
        newer = self.event_snapshot(recording, handle, detector, 51, available=10)
        self.assertTrue(self.panel.post_live_snapshot(handle, newer))
        regressed = self.event_snapshot(recording, handle, detector, 52)
        self.assertFalse(self.panel.post_live_snapshot(handle, regressed))
        new_handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=8)
        self.assertFalse(self.panel.post_live_snapshot(handle, newer))
        self.assertFalse(self.panel.post_live_snapshot(new_handle, newer))
        fresh = self.event_snapshot(recording, new_handle, detector, 1, revision=8)
        self.assertTrue(self.panel.post_live_snapshot(new_handle, fresh)); self.settle()
        self.assertEqual(self.panel._latest_live_sequence, 1)
        self.panel.shutdown()
        self.assertFalse(self.panel.post_live_snapshot(new_handle, replace(fresh, sequence=2,
            event_index=replace(fresh.event_index, sequence=2))))
        self.assertIsNone(self.panel._mailbox.live)

    def test_recent_live_active_context_remains_visible_across_ring_boundary(self):
        samples = ([100]*32+[2000]*5+[100]*40)*3 + [100]*10 + [2000]*100
        recording = self.recording(samples)
        detector = EventDetector(config(), 2, retention="recent")
        self.feed_recording(detector, recording, stop=260)
        handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
        pending = detector.status.pending
        self.assertIsNotNone(pending)
        snapshot = self.event_snapshot(recording, handle, detector, 1, available=pending.start_index+1)
        self.panel.post_live_snapshot(handle, snapshot); self.settle()
        self.assertEqual(len(self.panel._events), 2)
        self.assertIn("Незавершённый ordinal 3", self.panel.index_label.text())
        self.assertIn("активного контекста уже недоступна", self.panel.index_label.text())
        self.feed_recording(detector, recording, start=260)
        self.assertEqual(detector.status.pending.start_index, pending.start_index)
        detector.finish("unknown_loss")
        self.assertEqual(detector.event(3).start_index, pending.start_index)
        snapshot = self.event_snapshot(recording, handle, detector, 2, available=250)
        self.panel.post_live_snapshot(handle, snapshot); self.settle()
        self.assertIn("unknown_loss", self.panel.index_label.text())
        self.panel._select_event(detector.event(3))
        self.assertIn("right_truncated", self.panel.event_label.text())
        self.assertIn("недоступен", self.panel.status_label.text())

    def test_recent_live_extended_labels_reachable_at_default_and_large_font(self):
        recording = self.recording(([100]*32+[2000]*5+[100]*40)*3)
        detector = EventDetector(config(), 2, retention="recent")
        self.feed_recording(detector, recording)
        handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
        self.panel.post_live_snapshot(handle, self.event_snapshot(recording, handle, detector, 1, available=131)); self.settle()
        self.panel._select_event(detector.event(1))
        stylesheet = self.panel.styleSheet()
        for font in (13, 22):
            self.panel.setStyleSheet(stylesheet + f"\nQWidget {{font-size: {font}px;}}")
            self.panel.resize(900, 600); pump_events(200)
            for name in ("index_label", "event_label", "status_label"):
                label = getattr(self.panel, name)
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                self.panel.scroll_area.ensureWidgetVisible(label, 0, 0); pump_events(20)
                area = self.panel.scroll_area
                bottom = label.mapTo(area.viewport(), QtCore.QPoint(0, label.height())).y()
                self.assertLessEqual(bottom, area.viewport().height())
        self.assertGreater(self.panel.scroll_area.verticalScrollBar().maximum(), 0)



    def test_recent_validation_is_outside_lock_and_source_rechecked_under_lock(self):
        recording = self.recording([100]*32+[2000]*5+[100]*40)
        detector = EventDetector(config(), 2, retention="recent")
        self.feed_recording(detector, recording)
        for action in ("source", "close"):
            handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
            snapshot = self.event_snapshot(recording, handle, detector, 1)
            entered, release = threading.Event(), threading.Event()
            self.releases.append(release)
            results, errors = [], []
            original = LiveSnapshot.__post_init__
            def validate(value):
                original(value)
                acquired = self.panel._mailbox.lock.acquire(blocking=False)
                if not acquired:
                    raise AssertionError("Nested snapshot validation holds mailbox lock")
                self.panel._mailbox.lock.release()
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Validation race timed out")
            def producer():
                try:
                    results.append(self.panel.post_live_snapshot(handle, snapshot))
                except Exception as exc:
                    errors.append(exc)
            with patch.object(LiveSnapshot, "__post_init__", validate):
                thread = threading.Thread(target=producer)
                thread.start()
                self.assertTrue(entered.wait(5))
                if action == "source":
                    self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
                else:
                    self.panel.shutdown()
                release.set(); thread.join(5)
                self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results, [False])
            self.assertIsNone(self.panel._mailbox.live)

    def test_recent_terminal_detector_fault_cannot_be_erased_by_newer_snapshot(self):
        recording = self.recording([100]*32+[2000]*5+[100]*40)
        detector = EventDetector(config(), 2, retention="recent")
        self.feed_recording(detector, recording)
        detector.finish("unknown_loss")
        handle = self.panel.begin_live(recording.identity, event_config=config(), event_config_revision=7)
        snapshot = self.event_snapshot(recording, handle, detector, 1)
        self.assertTrue(self.panel.post_live_snapshot(handle, snapshot)); self.settle()
        status = replace(detector.status, outcome="running", discontinuity=None)
        resurrected = replace(snapshot, sequence=2, event_index=replace(snapshot.event_index,
            sequence=2, status=status))
        self.assertFalse(self.panel.post_live_snapshot(handle, resurrected))
        self.assertIn("unknown_loss", self.panel.index_label.text())

    def test_recent_eviction_does_not_limit_saved_raw_reopen_or_hide_offline_cap(self):
        cfg = EventConfig(1000, 500, "above", 1, 0, 1, 0, 3, 0, 65535)
        values = [2000, 100]*5000
        recording = self.recording(values)
        detector = EventDetector(cfg, 2, retention="recent")
        self.feed_recording(detector, recording, chunk=8192)
        handle = self.panel.begin_live(recording.identity, event_config=cfg, event_config_revision=7)
        snapshot = self.event_snapshot(recording, handle, detector, 1, available=len(values)-64)
        self.panel.post_live_snapshot(handle, snapshot); self.settle()
        self.assertEqual(detector.status.evicted_events, 4998)
        self.panel.shutdown(); self.panel.close(); self.panel.deleteLater()
        self.panel = StreamViewerPanel(event_config=cfg)
        self.panel.show()
        self.panel.open_path(recording.path); self.settle()
        self.assertEqual((self.panel._first, self.panel._end), (0, len(values)))
        self.assertEqual(len(self.panel._events), 4096)
        self.assertIn("event_capacity", self.panel.index_label.text())
        self.assertTrue(self.panel._summary.complete)
        self.panel.show_whole_recording(); self.settle()
        self.assertEqual(self.panel.selection, Selection(0, len(values)))
        raw = b"".join(part.raw_u16le for part in self.panel._recording.iter_raw(0, len(values)))
        self.assertEqual(raw, struct.pack(f"<{len(values)}H", *values))


if __name__ == "__main__":
    unittest.main()

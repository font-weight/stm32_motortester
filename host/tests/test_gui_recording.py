"""Real Qt recording/viewer checks on the running OS, with synthetic byte peers.

Dialogs are selected/cancelled programmatically; no Windows or hardware claim.
The adapter files are separately owned. These tests require their composed pin.
"""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from motortester import recording, session_archive as sa
from motortester.acquisition import AcquisitionBridge
from motortester.emulator import demo_message, from_message
from motortester.gui import MainWindow
from motortester.gui_acquisition import AcquisitionDialog, recording_status_text
from motortester.recording_controller import RecordingPhase
from motortester.session import Phase
from qt_test_support import pump_events


class RecordingGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="motortester-recording-gui-")
        self.root = Path(self.temp.name)
        self.gates = []
        self.window = MainWindow()
        self.window.show()
        self.bridges = []
        self.records = []
        self.saved = []
        self.spool_folder = patch("motortester.gui_acquisition.recovery_directory", return_value=self.root / "восстановление")
        self.spool_folder.start()
        self.addCleanup(self.spool_folder.stop)
        pump_events(1)

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.wait(lambda: not self.window.busy)
        adapters = [bridge._worker.recording for bridge in self.bridges if bridge._worker]
        with patch.object(self.window, "confirm_recording_exit", return_value=True):
            self.window.close()
        for bridge in self.bridges:
            self.wait(lambda: not bridge.running and bridge._closed)
        for adapter in adapters:
            if adapter is not None:
                self.wait(lambda: not adapter._writer.running)
        self.window.deleteLater()
        pump_events(5)
        self.temp.cleanup()

    def wait(self, condition, seconds=6):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                pump_events(1)
                return
            pump_events(5)
        self.fail("Qt operation did not settle: " + self.window.status_label.text())

    def connect(self):
        def create(parent):
            bridge = AcquisitionBridge(parent, interval_ms=1, edge_delay_ms=15)
            self.bridges.append(bridge)
            bridge.recording_saved.connect(self.saved.append)
            return bridge
        with patch("motortester.acquisition.AcquisitionBridge", side_effect=create):
            self.window.show_acquisition()
        self.panel = self.window._acquisition_dialog
        self.bridge = self.window._acquisition
        self.panel.connect_button.click()
        self.wait(self.panel.record_button.isEnabled)

    def record(self, mode=1, *, edge=True):
        self.panel.mode.setCurrentIndex(mode)
        self.panel.simulate.setChecked(edge)
        self.panel.timeout.setValue(100)
        self.panel.record_button.click()
        self.wait(lambda: self.bridge.state.recording is not None)

    def finish(self):
        self.panel.stop_button.click()
        self.wait(lambda: self.bridge.state.recording.ready_to_save)
        self.wait(self.panel.save_recording_button.isEnabled)

    def save(self, name="русский сеанс.mtrec"):
        path = self.root / name
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(path), "")):
            self.panel.save_recording_button.click()
        self.wait(lambda: str(path) in self.saved)
        self.assertTrue(path.is_file())
        return path

    def open(self, path):
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            self.window.open_button.click()
        self.wait(lambda: not self.window.busy)

    def test_real_record_four_windows_stop_save_open_every_bit_and_provenance(self):
        self.connect()
        original = sa.SessionSpool._append
        written = []
        def append(spool, entry, data, *, source_item=None):
            receipt = original(spool, entry, data, source_item=source_item)
            written.append((bytes(data), entry.provenance, source_item))
            return receipt
        with patch.object(sa.SessionSpool, "_append", append), patch.object(self.bridge, "acknowledge", wraps=self.bridge.acknowledge) as ack:
            self.record()
            self.wait(lambda: self.bridge.state.recording.snapshot.committed_segments >= 4)
            self.finish()
            self.assertEqual(ack.call_count, 0, "GUI paint must not consume recording ownership")
        saved_spool = Path(self.bridge.state.recording.spool_path)
        self.assertTrue(saved_spool.is_file())
        self.assertFalse(self.panel.start_button.isEnabled())
        self.assertFalse(self.panel.record_button.isEnabled())
        path = self.save()
        self.assertIn("Сеанс сохранён", self.window.status_label.text())
        loaded = sa.load(path)
        self.assertGreaterEqual(sum(entry.kind == "capture" for entry in loaded.entries), 4)
        self.assertEqual(len(loaded.entries), len(written))
        self.open(path)
        self.assertEqual(self.window.segment_selector.count(), len(written))
        self.assertIn("неизвестны", self.window.archive_label.text())
        for ordinal, (raw, provenance, source_item) in enumerate(written):
            self.assertEqual(loaded.read_segment(ordinal), raw)
            self.assertEqual(loaded.entries[ordinal].provenance, provenance)
            self.assertEqual(provenance, source_item.provenance)
            self.window.segment_selector.setCurrentIndex(ordinal)
            self.wait(lambda: not self.window.busy)
            if type(source_item) is sa.FullSegment:
                self.assertEqual(raw, recording.encode(source_item.capture))
                self.assertEqual(self.window.capture, source_item.capture)
                self.assertEqual(tuple(self.window.curve.getData()[1]), source_item.capture.samples)
            elif type(source_item) is sa.PartialSegment:
                # Stop is allowed to retain an in-flight partial terminal.
                expected = tuple(value for fragment in source_item.fragments for value in fragment.samples)
                self.assertEqual(raw, np.asarray(expected, dtype="<u2").tobytes())
                self.assertIsNone(self.window.capture)
                curve = self.window.curve.getData()[1]
                actual = () if curve is None else tuple(curve[np.isfinite(curve)])
                self.assertEqual(actual, expected)
            else:
                self.assertIs(type(source_item), sa.AbortEvent)
                self.assertIsNone(self.window.capture)
            self.assertIn("wire nonce", self.window.details_label.toolTip())
        self.assertTrue(saved_spool.exists(), "Save/Open must not delete recovery spool")
        # Ordinary Single still consumes its display ACK and supports .mtcap.
        self.panel.mode.setCurrentIndex(2)
        self.panel.start_button.click()
        self.wait(lambda: self.bridge.state.recording is None and self.panel.start_button.isEnabled())
        self.assertIsNone(self.window._archive)
        ordinary = self.root / "обычное.mtcap"
        self.window.save_path(ordinary)
        self.wait(lambda: not self.window.busy)
        self.assertEqual(recording.load(ordinary), self.window.capture)

    def test_prehistory_record_stop_save_open_preserves_every_full_segment(self):
        self.connect()
        self.assertTrue(self.panel.pretrigger.isEnabled())
        self.panel.pretrigger.setCurrentIndex(self.panel.pretrigger.findData(1024))
        self.record()
        self.wait(lambda: self.bridge.state.recording.snapshot.committed_segments >= 2)
        self.finish()
        stopped = self.window.capture
        self.assertEqual(stopped.pretrigger_samples, 1024)
        self.assertEqual(stopped.trigger_index, 1024)
        self.assertFalse(self.panel.pretrigger.isEnabled())
        path = self.save("предыстория.mtrec")
        archive = sa.load(path)
        full = 0
        self.open(path)
        for ordinal, entry in enumerate(archive.entries):
            self.window.segment_selector.setCurrentIndex(ordinal)
            self.wait(lambda: not self.window.busy)
            if entry.kind == "capture":
                full += 1
                capture = recording.decode(archive.read_segment(ordinal))
                self.assertEqual(self.window.capture, capture)
                self.assertEqual((capture.pretrigger_samples, capture.trigger_index), (1024, 1024))
                self.assertEqual(capture.origin, "simulator")
                self.assertEqual(capture.time_seconds(0), -1024 / 50000)
                self.assertEqual(capture.time_seconds(1024), 0)
                self.assertEqual(tuple(self.window.curve.getData()[1]), capture.samples)
        self.assertGreaterEqual(full, 2)
        self.assertTrue(Path(self.bridge.state.recording.spool_path).exists())

    def test_single_records_exactly_one_and_can_start_another_saved_recording(self):
        self.connect()
        self.record(mode=2)
        self.wait(lambda: self.bridge.state.recording.ready_to_save)
        pump_events(100)
        self.assertEqual(self.bridge.state.recording.snapshot.committed_segments, 1)
        first_spool = self.bridge.state.recording.spool_path
        self.save()
        self.wait(self.panel.record_button.isEnabled)
        self.record(mode=2)
        self.wait(lambda: self.bridge.state.recording.spool_path != first_spool
                  and self.bridge.state.recording.ready_to_save)
        self.assertNotEqual(self.bridge.state.recording.spool_path, first_spool)
        self.assertTrue(Path(first_spool).exists())
        self.assertEqual(self.bridge.state.recording.snapshot.committed_segments, 1)

    def test_missing_normal_trigger_produces_empty_archive_without_capture(self):
        self.connect()
        self.record(edge=False)
        self.wait(lambda: self.bridge.state.snapshot.phase is Phase.ARMED)
        pump_events(110)
        self.assertIsNone(self.window.capture)
        self.assertEqual(self.bridge.state.recording.snapshot.committed_segments, 0)
        self.assertIn("ожидание запуска", self.panel.recording_status.text())
        self.finish()
        path = self.save("пустой.mtrec")
        self.open(path)
        self.assertIsNone(self.window.capture)
        self.assertEqual(self.window.segment_selector.count(), 0)
        self.assertIn("без сегментов", self.window.trigger_label.text())
        self.assertIsNone(self.window._archive_ordinal)
        self.assertFalse(self.window.plot.getAxis("bottom").style["showValues"])
        self.assertIn("не определена", self.window.plot.getAxis("bottom").labelText)
        self.assertEqual(self.window.details_label.toolTip(), "")
        self.assertFalse(self.window.save_button.isEnabled())

    def test_auto_timeout_records_no_trigger_marker(self):
        self.connect()
        self.record(mode=0, edge=False)
        self.wait(lambda: self.bridge.state.recording.snapshot.committed_segments >= 3)
        self.finish()
        self.open(self.save())
        self.assertEqual(self.window.capture.trigger_kind, "auto_timeout")
        self.assertIsNone(self.window.capture.trigger_index)
        self.assertFalse(self.window.trigger_line.isVisible())

    def fixture_archive(self, *, fs=50000):
        capture = from_message(demo_message())
        identity = sa.SessionIdentity(str(uuid4()), capture.session_id, capture.origin,
                                     capture.firmware_version, capture.protocol_version, capture.application_version)
        path = self.root / "полные и неполные.mtrec"
        spool = sa.SessionSpool.create(self.root / "fixture.spool", identity)
        try:
            spool.append_capture(recording.encode(capture))
            spool.append_partial(sa.PartialSegment(1, 12,
                (sa.SampleFragment(1, (0, 4095)), sa.SampleFragment(8, (255, 256, 17))),
                ((0, 1), (3, 8), (11, 12)), "STOP", nominal_fs_hz=fs))
            spool.append_partial(sa.PartialSegment(2, 12, (), ((0, 12),), "Нет DATA"))
            spool.append_abort(sa.AbortEvent("ABORT до BEGIN"))
            sa.finalize(spool.finish("user_stop", "stop_ack"), path)
        finally:
            spool.close()
        return path, capture

    def test_open_ordered_partial_fragments_gaps_empty_partial_and_no_begin(self):
        path, capture = self.fixture_archive()
        self.open(path)
        self.assertEqual(self.window.capture, capture)
        archive = self.window._archive
        original = sa.SessionArchive.read_segment
        calls = []
        def selected(current, ordinal):
            calls.append(ordinal)
            return original(current, ordinal)
        with patch.object(sa.SessionArchive, "read_segment", selected):
            self.window.segment_selector.setCurrentIndex(1)
            self.wait(lambda: not self.window.busy)
        self.assertEqual(calls, [1])
        self.assertIsNone(self.window.capture, "Partial must never become a fabricated Capture")
        xs, ys = self.window.curve.getData()
        finite = np.isfinite(ys)
        self.assertEqual(tuple(ys[finite]), (0, 4095, 255, 256, 17))
        self.assertTrue(np.allclose(xs[finite], np.array([1, 2, 8, 9, 10]) * .02))
        self.assertEqual(sum(~finite), 2)
        self.assertFalse(self.window.save_button.isEnabled())
        self.assertFalse(self.window.cursor_toggle.isEnabled())
        self.assertIn("Неполное", self.window.quality_label.text())
        for ordinal in (2, 3):
            self.window.segment_selector.setCurrentIndex(ordinal)
            self.wait(lambda: not self.window.busy)
            self.assertIsNone(self.window.capture)
            data = self.window.curve.getData()[1]
            self.assertTrue(data is None or len(data) == 0)
        self.assertIn("без BEGIN", self.window.trigger_label.text())
        self.assertFalse(self.window.plot.getAxis("bottom").style["showValues"])
        self.assertIn("не определена", self.window.plot.getAxis("bottom").labelText)
        self.window.segment_selector.setCurrentIndex(0)
        self.wait(lambda: not self.window.busy)
        self.assertEqual(self.window.capture, capture)
        self.assertEqual(self.window._archive, archive)
        self.assertTrue(self.window.plot.getAxis("bottom").style["showValues"])
        self.assertIn("номинальная Fs", self.window.plot.getAxis("bottom").labelText)

    def test_partial_without_sample_rate_uses_original_indices(self):
        path, _ = self.fixture_archive(fs=None)
        self.open(path)
        self.window.segment_selector.setCurrentIndex(1)
        self.wait(lambda: not self.window.busy)
        xs, ys = self.window.curve.getData()
        self.assertEqual(tuple(xs[np.isfinite(ys)]), (1, 2, 8, 9, 10))
        self.assertIn("Индекс", self.window.plot.getAxis("bottom").labelText)
        self.assertIn("неизвестна", self.window.fs_value.text())

    def test_failed_open_and_selection_do_not_replace_good_data(self):
        path, capture = self.fixture_archive()
        self.open(path)
        archive = self.window._archive
        bad = self.root / "broken.mtrec"
        bad.write_bytes(b"not an archive")
        self.open(bad)
        self.assertIs(self.window._archive, archive)
        self.assertEqual(self.window.capture, capture)
        path.unlink()
        self.window.segment_selector.setCurrentIndex(1)
        self.wait(lambda: not self.window.busy)
        self.assertEqual(self.window.segment_selector.currentIndex(), 0)
        self.assertEqual(self.window.capture, capture)
        self.assertIn("Не удалось прочитать", self.window.status_label.text())

    def test_cancel_dialog_existing_path_and_explicit_nonoverwriting_save(self):
        self.connect()
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        spool = Path(self.bridge.state.recording.spool_path)
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=("", "")):
            self.panel.save_recording_button.click()
        self.assertIn("отменено", self.window.status_label.text())
        path = self.root / "exists.mtrec"
        path.write_bytes(b"keep me")
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(path), "")), patch.object(self.bridge, "save_recording", wraps=self.bridge.save_recording) as save:
            self.panel.save_recording_button.click()
            self.assertEqual(save.call_count, 1)
        self.wait(lambda: self.bridge.state.recording.error == "save_failed")
        self.wait(self.panel.save_recording_button.isEnabled)
        self.assertEqual(path.read_bytes(), b"keep me")
        self.assertEqual(self.saved, [])
        self.assertIsNone(self.window._pending_recording_save)
        self.assertIn("новое имя", self.window.status_label.text())
        self.assertTrue(spool.exists())
        self.save()

    def test_save_disk_error_is_not_success_and_can_retry(self):
        self.connect()
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        path = self.root / "disk-error.mtrec"
        with patch.object(sa, "_sync", side_effect=OSError("private disk detail")):
            self.assertTrue(self.window.save_recording_path(path))
            self.wait(lambda: self.bridge.state.recording.error is not None)
            self.wait(self.panel.save_recording_button.isEnabled)
        self.assertEqual(self.saved, [])
        self.assertFalse(path.exists())
        self.assertNotIn("private disk detail", self.window.status_label.text())
        self.save("retry.mtrec")
        self.assertNotIn("Ошибка", self.panel.status.text())

    @unittest.skipIf(os.name == "nt", "POSIX chmod evidence only; Windows permissions need native testing")
    def test_actual_read_only_destination(self):
        self.connect()
        forbidden = self.root / "read-only"
        forbidden.mkdir()
        forbidden.chmod(0o500)
        self.addCleanup(lambda: forbidden.chmod(0o700) if forbidden.exists() else None)
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        self.assertTrue(self.window.save_recording_path(forbidden / "record.mtrec"))
        self.wait(lambda: self.bridge.state.recording.error is not None)
        self.assertEqual(self.saved, [])
        self.assertFalse((forbidden / "record.mtrec").exists())
        self.assertTrue(Path(self.bridge.state.recording.spool_path).exists())

    @unittest.skipIf(os.name == "nt", "POSIX chmod evidence only; Windows permissions need native testing")
    def test_actual_read_only_recovery_parent_fails_asynchronously_before_arm(self):
        self.connect()
        forbidden = self.root / "read-only-parent"
        forbidden.mkdir()
        forbidden.chmod(0o500)
        self.addCleanup(lambda: forbidden.chmod(0o700) if forbidden.exists() else None)
        runtime = self.bridge._worker.runtime
        with patch("motortester.gui_acquisition.recovery_directory", return_value=forbidden / "recovery"), patch.object(runtime, "configure", wraps=runtime.configure) as configure, patch.object(runtime, "arm", wraps=runtime.arm) as arm:
            self.panel.record_button.click()
            self.wait(lambda: self.bridge.state.recording is not None and self.bridge.state.recording.error == "prepare_failed")
            self.assertEqual((configure.call_count, arm.call_count), (0, 0))
        self.assertFalse(Path(self.bridge.state.recording.spool_path).exists())
        self.assertFalse(self.panel.record_button.isEnabled())
        self.assertFalse(self.panel.start_button.isEnabled())
        self.assertFalse(self.panel.save_recording_button.isEnabled())
        self.assertIn("файл мог не создаться", self.panel.recording_status.text())

    def _filesystem_heartbeat(self):
        beats = []
        timer = QtCore.QTimer()
        timer.setInterval(1)
        timer.timeout.connect(lambda: beats.append(time.monotonic()))
        timer.start()
        self.addCleanup(timer.stop)
        return beats

    def _assert_filesystem_heartbeat(self, beats, entered, release, *, seconds=.5):
        # A timer callback after this baseline proves GUI dispatch while the
        # filesystem worker is still gated. Qt may coalesce timer expirations;
        # an arbitrary callback count within one 50 ms pump is not a guarantee.
        started = time.monotonic()
        deadline = started + seconds
        before = len(beats)
        self.assertTrue(entered.is_set())
        self.assertFalse(release.is_set())
        while time.monotonic() < deadline:
            pump_events(5)
            self.assertTrue(entered.is_set())
            self.assertFalse(release.is_set())
            now = time.monotonic()
            if now <= deadline and any(started < stamp <= now for stamp in beats[before:]):
                return
        self.fail("No post-baseline GUI heartbeat before deadline while filesystem gate stayed closed")

    def test_filesystem_heartbeat_accepts_one_prompt_callback_while_gate_closed(self):
        entered, release = threading.Event(), threading.Event()
        entered.set()
        beats = []
        QtCore.QTimer.singleShot(31, lambda: beats.append(time.monotonic()))
        self._assert_filesystem_heartbeat(beats, entered, release)
        self.assertEqual(len(beats), 1)
        self.assertFalse(release.is_set())

    def test_filesystem_heartbeat_rejects_blocked_gui_even_with_late_callback(self):
        entered, release = threading.Event(), threading.Event()
        entered.set()
        beats = self._filesystem_heartbeat()
        # This is deliberately on the GUI thread: a timer becoming overdue
        # during a blocking callback must not satisfy the bounded handshake.
        QtCore.QTimer.singleShot(0, lambda: time.sleep(.6))
        started = time.monotonic()
        with self.assertRaisesRegex(AssertionError, "No post-baseline GUI heartbeat"):
            self._assert_filesystem_heartbeat(beats, entered, release)
        self.assertGreaterEqual(time.monotonic() - started, .6)
        self.assertFalse(release.is_set())

    def _blocked_recovery_preparation(self, *, close):
        self.connect()
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)
        observed = []
        owner = threading.get_ident()
        folder = self.root / "восстановление"
        original = Path.mkdir
        def blocked(path, *args, **kwargs):
            if path == folder:
                observed.append(threading.get_ident())
                entered.set()
                release.wait(10)
            return original(path, *args, **kwargs)
        runtime = self.bridge._worker.runtime
        beats = self._filesystem_heartbeat()
        with patch.object(Path, "mkdir", blocked), patch.object(runtime, "configure", wraps=runtime.configure) as configure, patch.object(runtime, "arm", wraps=runtime.arm) as arm:
            started = time.monotonic()
            self.panel.record_button.click()
            self.assertLess(time.monotonic() - started, .1)
            self.wait(entered.is_set)
            self.wait(lambda: self.bridge.state.recording is not None)
            self._assert_filesystem_heartbeat(beats, entered, release)
            self.assertTrue(self.panel.stop_button.isEnabled())
            self.assertTrue(self.panel.disconnect_button.isEnabled())
            self.assertEqual(observed, [self.bridge._worker.recording._writer._thread.ident])
            self.assertNotEqual(observed[0], owner)
            self.assertIn("создание файла может быть не завершено", self.panel.recovery_path.toolTip())
            spool = Path(self.bridge.state.recording.spool_path)
            self.assertFalse(spool.exists())
            started = time.monotonic()
            self.panel.stop_button.click()
            self.assertLess(time.monotonic() - started, .1)
            self.wait(lambda: self.bridge.state.recording.snapshot.user_stop_requested)
            self.assertFalse(self.panel.save_recording_button.isEnabled())
            if close:
                seen = []
                self._answer_warning(True, seen)
                self.window.close()
                self.wait(lambda: not self.bridge.running and self.bridge._closed)
                self.assertLess(time.monotonic() - started, 3)
                self.assertIn(str(spool), seen[0])
                self.assertIn("если создан", seen[0])
                self.assertTrue(self.bridge.state.recording.retired)
                self.assertTrue(self.bridge.state.recording.writer_running)
            release.set()
            if close:
                self.wait(lambda: not self.bridge._worker.recording._writer.running)
                self.assertFalse(spool.exists(), "retirement during mkdir must fence exclusive create")
                self.assertFalse(self.bridge.state.recording.ready_to_save)
            else:
                self.wait(self.panel.save_recording_button.isEnabled)
                self.assertTrue(spool.exists())
                self.assertEqual(sa.recover(spool).entries, ())
                self.assertEqual(self.bridge.state.recording.snapshot.termination_reason, "user_stop")
            self.assertEqual((configure.call_count, arm.call_count), (0, 0))
            self.assertEqual(self.saved, [])

    def test_record_click_stalled_mkdir_keeps_heartbeat_and_stop_before_arm(self):
        self._blocked_recovery_preparation(close=False)

    def test_record_click_stalled_mkdir_close_fences_late_create_and_arm(self):
        self._blocked_recovery_preparation(close=True)

    def test_save_click_stalled_destination_stat_keeps_heartbeat_stop_and_close(self):
        self.connect()
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        path = self.root / "blocked-stat.mtrec"
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)
        observed = []
        original = Path.exists
        def blocked(selected):
            if selected == path:
                observed.append(threading.get_ident())
                entered.set()
                release.wait(10)
            return original(selected)
        beats = self._filesystem_heartbeat()
        with patch.object(Path, "exists", blocked), patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(path), "")):
            started = time.monotonic()
            self.panel.save_recording_button.click()
            self.assertLess(time.monotonic() - started, .1)
            self.wait(entered.is_set)
            self.wait(self.panel.cancel_recording_button.isEnabled)
            self._assert_filesystem_heartbeat(beats, entered, release)
            self.assertEqual(observed, [self.bridge._worker.recording._writer._thread.ident])
            self.assertNotEqual(observed[0], threading.get_ident())
            self.assertTrue(self.panel.stop_button.isEnabled())
            self.assertTrue(self.panel.disconnect_button.isEnabled())
            started = time.monotonic()
            self.panel.stop_button.click()
            self.assertLess(time.monotonic() - started, .1)
            seen = []
            self._answer_warning(True, seen)
            self.window.close()
            self.wait(lambda: not self.bridge.running and self.bridge._closed)
            self.assertLess(time.monotonic() - started, 3)
            self.assertIn(self.bridge.state.recording.spool_path, seen[0])
            self.assertTrue(self.bridge.state.recording.retired)
            self.assertTrue(self.bridge.state.recording.writer_running)
            self.assertEqual(self.saved, [])
            release.set()
            self.wait(lambda: not self.bridge._worker.recording._writer.running)
        self.assertFalse(path.exists())
        self.assertEqual(self.saved, [])
        self.assertIsNone(self.window._pending_recording_save)
        self.assertFalse(self.bridge.state.recording.ready_to_save)

    def test_long_recovery_path_is_rejected_before_record(self):
        self.connect()
        with patch("motortester.gui_acquisition.recovery_directory", return_value=self.root / ("x" * 241)), patch.object(self.bridge, "record", wraps=self.bridge.record) as record:
            self.panel.record_button.click()
        self.assertEqual(record.call_count, 0)
        self.assertIsNone(self.bridge.state.recording)

    def test_retired_or_wrong_session_save_signal_cannot_advertise_success(self):
        self.connect()
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        current = self.window._recording
        path = str(self.root / "stale.mtrec")
        self.window._pending_recording_save = (path, current.snapshot.session)
        self.window._recording = replace(current, saved_path=path, retired=True)
        self.window._status("unchanged")
        self.bridge.recording_saved.emit(path)
        self.assertEqual(self.window.status_label.text(), "unchanged")
        self.window._recording = replace(current, saved_path=path)
        self.window._pending_recording_save = (path, object())
        self.bridge.recording_saved.emit(path)
        self.assertEqual(self.window.status_label.text(), "unchanged")
        self.window._recording = current

    def test_unaccepted_finalization_capacity_and_fault_status_are_explicit(self):
        self.connect()
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        current = self.window._recording
        snapshot = replace(current.snapshot, phase=RecordingPhase.FINALIZE_READY,
            finalization_invalidated=True, source_retention_required=True,
            termination_reason="capacity", stop_evidence="unknown", fault="writer_failed")
        unresolved = replace(current, snapshot=snapshot, ready_to_save=False)
        state = replace(self.bridge.state, recording=unresolved)
        self.window._acquisition_state(state)
        self.panel.update_state(state)
        self.assertFalse(self.panel.save_recording_button.isEnabled())
        with patch.object(self.bridge, "save_recording", wraps=self.bridge.save_recording) as save:
            self.assertFalse(self.window.save_recording_path(self.root / "forbidden.mtrec"))
        self.assertEqual(save.call_count, 0)
        text = self.panel.recording_status.text()
        for required in ("предел", "не подтверждено", "неизвестно", "удерживаемые", "writer_failed"):
            self.assertIn(required, text)
        self.window._acquisition_state(self.bridge.state)

    def _answer_warning(self, proceed, seen):
        def answer():
            box = next((widget for widget in QtWidgets.QApplication.topLevelWidgets()
                        if isinstance(widget, QtWidgets.QMessageBox) and widget.isVisible()), None)
            if box is None:
                QtCore.QTimer.singleShot(5, answer)
                return
            seen.append(box.text())
            role = QtWidgets.QMessageBox.ButtonRole.AcceptRole if proceed else QtWidgets.QMessageBox.ButtonRole.RejectRole
            next(button for button in box.buttons() if box.buttonRole(button) == role).click()
        QtCore.QTimer.singleShot(0, answer)

    def test_unsaved_close_cancel_preserves_session_and_confirmation_names_spool(self):
        self.connect()
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        seen = []
        self._answer_warning(False, seen)
        self.window.close()
        self.assertFalse(self.window._closing)
        self.assertTrue(self.window.isVisible())
        self.assertIn(self.bridge.state.recording.spool_path, seen[0])
        self.assertIn("хвост", seen[0])
        self.assertIn("физическую остановку", seen[0])

    def test_blocked_writer_disconnect_and_close_remain_bounded(self):
        self.connect()
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)
        original = sa.SessionSpool.append_full
        def blocked(spool, full):
            entered.set()
            release.wait(10)
            return original(spool, full)
        with patch.object(sa.SessionSpool, "append_full", blocked):
            self.record()
            self.wait(entered.is_set)
            self.assertTrue(self.panel.stop_button.isEnabled())
            started = time.monotonic()
            self.panel.stop_button.click()
            self.assertLess(time.monotonic() - started, .2)
            self.wait(lambda: self.bridge.state.recording.snapshot.user_stop_requested)
            self.assertFalse(self.panel.save_recording_button.isEnabled())
            seen = []
            self._answer_warning(True, seen)
            self.panel.disconnect_button.click()
            self.wait(lambda: not self.bridge.state.connected)
            self.assertIn("Файл восстановления", seen[0])
            self._answer_warning(True, seen)
            self.window.close()
            self.wait(lambda: not self.bridge.running and self.bridge._closed)
            self.assertLess(time.monotonic() - started, 3)
            current = self.bridge.state.recording
            self.assertTrue(current.retired)
            self.assertTrue(current.writer_running)
            self.assertFalse(current.ready_to_save)
            self.assertEqual(self.saved, [])
            release.set()
        self.wait(lambda: not self.bridge._worker.recording._writer.running)
        self.assertEqual(self.saved, [])

    def test_cancel_running_save_retains_spool_and_retry_is_explicit(self):
        self.connect()
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)
        original = sa._sync
        def blocked(stream):
            entered.set()
            release.wait(10)
            return original(stream)
        path = self.root / "cancel.mtrec"
        with patch.object(sa, "_sync", blocked):
            self.assertTrue(self.window.save_recording_path(path))
            self.wait(entered.is_set)
            self.wait(self.panel.cancel_recording_button.isEnabled)
            self.panel.cancel_recording_button.click()
            self.assertIn("отмена", self.window.status_label.text())
            # Queued cancellation is best effort. Hold publication until the
            # acquisition owner actually applies it, rather than racing it.
            self.wait(lambda: self.bridge._worker.recording._save_cancel.is_set())
            release.set()
            self.wait(self.panel.save_recording_button.isEnabled)
        self.assertFalse(path.exists())
        self.assertEqual(self.saved, [])
        self.assertTrue(Path(self.bridge.state.recording.spool_path).exists())
        self.save("retry-after-cancel.mtrec")

    def test_repeated_save_same_path_after_rename_clears_each_pending_completion(self):
        self.connect()
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        path = self.save("same.mtrec")
        original = path.read_bytes()
        path.rename(self.root / "original.mtrec")
        self.assertTrue(self.window.save_recording_path(path))
        self.wait(lambda: path.exists() and not self.bridge.state.recording.saving and not self.bridge.state.busy)
        self.assertEqual(self.saved, [str(path), str(path)])
        self.assertIsNone(self.window._pending_recording_save)
        self.assertEqual(sa.load(path).entries, sa.load(self.root / "original.mtrec").entries)
        self.assertEqual((self.root / "original.mtrec").read_bytes(), original)

    def test_later_save_failure_must_not_headline_old_saved_path(self):
        self.connect()
        self.record(mode=2)
        self.wait(self.panel.save_recording_button.isEnabled)
        first = self.save("first.mtrec")
        with patch.object(sa, "_sync", side_effect=OSError("private disk detail")):
            self.assertTrue(self.window.save_recording_path(self.root / "second.mtrec"))
            self.wait(lambda: self.bridge.state.recording.error is not None)
        self.assertTrue(first.exists())
        self.assertIsNone(self.window._pending_recording_save)
        self.assertNotIn("Архив .mtrec сохранён", recording_status_text(self.window._recording).split("\n")[0])
        self.assertIn("Ошибка", self.panel.recording_status.text().split("\n")[0])
        # An obsolete success for the prior path cannot supersede this error.
        self.window._status("current failure")
        self.bridge.recording_saved.emit(str(first))
        self.assertEqual(self.window.status_label.text(), "current failure")

    def test_minimal_embedder_keeps_ordinary_controls_and_gates_recording(self):
        self.connect()
        self.assertTrue(self.panel.record_button.isEnabled(), "Full viewer must retain recording support")
        minimal = QtWidgets.QWidget(self.window)
        minimal.busy = False
        minimal._acq_requested = False
        minimal._set_actions = lambda: None
        panel = AcquisitionDialog(minimal, self.bridge)
        try:
            panel.update_state(self.bridge.state)
            self.assertTrue(panel.start_button.isEnabled())
            self.assertTrue(panel.stop_button.isEnabled())
            self.assertTrue(panel.disconnect_button.isEnabled())
            self.assertFalse(panel.record_button.isEnabled())
            self.assertFalse(panel.save_recording_button.isEnabled())
            self.assertFalse(panel.cancel_recording_button.isEnabled())
            with patch.object(self.bridge, "start", return_value=True) as start:
                panel.start_button.click()
            self.assertEqual(start.call_count, 1)
            minimal._acq_requested = False
            panel.update_state(self.bridge.state)
            with patch.object(self.bridge, "disconnect_source", return_value=True) as disconnect:
                panel.disconnect_button.click()
            self.assertEqual(disconnect.call_count, 1)
        finally:
            panel.close()
            panel.deleteLater()
            minimal.deleteLater()
            pump_events(1)

    def test_archive_selector_uses_readable_dark_palette(self):
        path, _ = self.fixture_archive()
        self.open(path)
        selector = self.window.segment_selector
        selector.ensurePolished()
        palette = selector.palette()
        background = palette.color(QtGui.QPalette.ColorRole.Button)
        foreground = palette.color(QtGui.QPalette.ColorRole.ButtonText)
        self.assertLess(background.lightness(), 80)
        self.assertGreater(foreground.lightness(), 180)
        self.assertTrue(selector.isEnabled())
        self.assertEqual(selector.currentIndex(), 0)

    def test_failed_spool_prepare_blocks_new_start_without_fabricated_data(self):
        self.connect()
        with patch.object(sa, "_sync", side_effect=OSError("private preparation detail")):
            self.record()
            self.wait(lambda: self.bridge.state.recording.error is not None)
        self.assertIsNone(self.window.capture)
        self.assertFalse(self.panel.record_button.isEnabled())
        self.assertFalse(self.panel.start_button.isEnabled())
        self.assertFalse(self.panel.save_recording_button.isEnabled())
        self.assertIsNotNone(self.bridge.state.recording.spool_path)
        self.assertNotIn("private preparation detail", self.panel.recording_status.text())

    def test_real_invalidated_terminal_never_enables_save(self):
        self.connect()
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)
        original = sa.SessionSpool.finish
        def blocked(spool, *args, **kwargs):
            entered.set()
            release.wait(10)
            return original(spool, *args, **kwargs)
        with patch.object(sa.SessionSpool, "finish", blocked):
            self.record(mode=2)
            self.wait(entered.is_set)
            self.assertFalse(self.panel.save_recording_button.isEnabled())
            seen = []
            self._answer_warning(True, seen)
            self.panel.disconnect_button.click()
            self.wait(lambda: self.bridge.state.recording.snapshot.finalization_invalidated)
            release.set()
            self.wait(lambda: not self.bridge.state.recording.writer_busy)
        self.assertFalse(self.bridge.state.recording.ready_to_save)
        self.assertFalse(self.panel.save_recording_button.isEnabled())
        self.assertIn("не подтверждено", self.panel.recording_status.text())
        self.assertFalse(self.window.save_recording_path(self.root / "invalid.mtrec"))
        self.assertEqual(self.saved, [])
        self.assertTrue(Path(self.bridge.state.recording.spool_path).exists())

    def test_archive_open_cancel_and_ordinary_demo_clear_selection_safely(self):
        path, capture = self.fixture_archive()
        self.open(path)
        archive = self.window._archive
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=("", "")):
            self.window.open_button.click()
        self.assertIs(self.window._archive, archive)
        self.assertEqual(self.window.capture, capture)
        self.assertEqual(self.window.segment_selector.currentIndex(), 0)
        self.window.generate_button.click()
        self.assertIsNone(self.window._archive)
        self.assertFalse(self.window.segment_selector.isVisible())
        self.assertTrue(self.window.save_button.isEnabled())

    def test_active_recording_recovery_path_stays_copyable_and_within_panel(self):
        self.connect()
        self.record(edge=False)
        self.wait(lambda: self.bridge.state.snapshot.phase is Phase.ARMED)
        self.panel.resize(360, 300)
        pump_events(10)
        scroll = self.panel.findChild(QtWidgets.QScrollArea)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(self.panel.recovery_path.text(), self.bridge.state.recording.spool_path)
        self.panel.recovery_path.selectAll()
        self.assertEqual(self.panel.recovery_path.selectedText(), self.bridge.state.recording.spool_path)
        for widget in (self.panel.recording_status, self.panel.recovery_path):
            for point in (widget.rect().topLeft(), widget.rect().bottomRight()):
                target = widget.mapTo(scroll.widget(), point)
                scroll.ensureVisible(target.x(), target.y(), 1, 1)
                pump_events(1)
                self.assertTrue(scroll.viewport().rect().contains(widget.mapTo(scroll.viewport(), point)))
        self.finish()

    def test_header_source_warning_and_subtitle_fit_normal_and_large_fonts(self):
        path, _ = self.fixture_archive()
        self.open(path)
        original = self.window.styleSheet()
        subtitle = self.window.findChild(QtWidgets.QLabel, "subtitle")
        try:
            for pixels in (13, 18, 22):
                self.window.setStyleSheet(original.replace("font-size: 13px", f"font-size: {pixels}px"))
                for ordinal in (0, 1, 3):
                    with self.subTest(font_pixels=pixels, segment=ordinal):
                        self.window.segment_selector.setCurrentIndex(ordinal)
                        self.wait(lambda: not self.window.busy)
                        self.window.resize(1000, 710)
                        pump_events(10)
                        self.assertEqual(self.window.size(), QtCore.QSize(1000, 710))
                        for label in (subtitle, self.window.origin_badge):
                            contents = label.contentsRect()
                            flags = int(label.alignment() | (QtCore.Qt.TextFlag.TextWordWrap
                                if label.wordWrap() else QtCore.Qt.TextFlag.TextSingleLine))
                            text = label.fontMetrics().boundingRect(contents, flags, label.text())
                            self.assertTrue(contents.contains(text), (label.text(), contents, text))
                            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                            self.assertTrue(label.parentWidget().rect().contains(label.geometry()))
        finally:
            self.window.setStyleSheet(original)

    def test_new_controls_fit_previous_wide_glyph_measurement_model(self):
        self.connect()
        controls = (self.panel.record_button, self.panel.save_recording_button,
                    self.panel.cancel_recording_button)
        for widget in controls:
            # Synthetic width model from prior Windows label measurements;
            # this does not emulate Windows font rendering or prove Windows QA.
            widget.setStyleSheet("font-family: 'DejaVu Sans Mono';")
            widget.ensurePolished()
            font = widget.font()
            font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 0)
            font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing,
                                  13 - QtGui.QFontMetricsF(font).horizontalAdvance("M"))
            widget.setFont(font)
        for width in (420, 360):
            self.panel.resize(width, 300)
            pump_events(10)
            scroll = self.panel.findChild(QtWidgets.QScrollArea)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            for widget in controls:
                option = QtWidgets.QStyleOptionButton()
                widget.initStyleOption(option)
                content = widget.style().subElementRect(QtWidgets.QStyle.SubElement.SE_PushButtonContents, option, widget)
                text = widget.fontMetrics().boundingRect(content, int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter), widget.text())
                self.assertTrue(content.contains(text), (widget.text(), content, text))
                scroll.ensureWidgetVisible(widget)
                pump_events(1)
                self.assertTrue(scroll.viewport().rect().contains(widget.mapTo(scroll.viewport(), widget.rect().center())))

    def test_recording_controls_reachable_at_small_panel_and_large_native_fonts(self):
        self.connect()
        original_font = QtGui.QFont(self.app.font())
        try:
            for points in (9, 14, 22):
                self.app.setFont(QtGui.QFont("DejaVu Sans", points))
                self.window.resize(1000, 710)
                for width, height in ((420, 360), (360, 300)):
                    with self.subTest(points=points, width=width):
                        self.panel.resize(width, height)
                        pump_events(10)
                        scroll = self.panel.findChild(QtWidgets.QScrollArea)
                        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                        for widget in (self.panel.record_button, self.panel.stop_button,
                                       self.panel.save_recording_button, self.panel.cancel_recording_button,
                                       self.panel.disconnect_button):
                            scroll.ensureWidgetVisible(widget)
                            pump_events(1)
                            self.assertTrue(scroll.viewport().rect().contains(widget.mapTo(scroll.viewport(), widget.rect().center())))
                            option = QtWidgets.QStyleOptionButton()
                            widget.initStyleOption(option)
                            content = widget.style().subElementRect(QtWidgets.QStyle.SubElement.SE_PushButtonContents, option, widget)
                            text = widget.fontMetrics().boundingRect(content, int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter), widget.text())
                            self.assertTrue(content.contains(text), (widget.text(), content, text))
        finally:
            self.app.setFont(original_font)


if __name__ == "__main__":
    unittest.main()

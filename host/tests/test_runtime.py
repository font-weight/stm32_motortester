"""Author tests for the bounded real-byte Normal acquisition path."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from motortester.device_emulator import DeviceEmulator
from motortester.recording import save, load
from motortester.runtime import AcquisitionRuntime, RuntimeError
from motortester.session import Config, Phase, EventKind, RecoveryReason


class Link:
    def __init__(self):
        self.now = 0
        self.device = DeviceEmulator()
        self.write_limit = 4096
        self.read_limit = 4096
        self.writes = []
        self.fail_write = False
        self.fail_read = False

    def write(self, data):
        self.writes.append(data)
        if self.fail_write:
            raise OSError("unknown acceptance")
        return self.device.write(data[:self.write_limit], self.now).accepted_prefix

    def read(self, size):
        if self.fail_read:
            raise OSError("disconnected")
        return self.device.read(min(size, self.read_limit), self.now)


class RuntimeTests(unittest.TestCase):
    def make(self, *, armed=True):
        link = Link()
        runtime = AcquisitionRuntime(link, origin="simulator", clock_ms=lambda: link.now,
                                     utc_now=lambda: "2026-10-10T19:00:00Z")
        runtime.start(101)
        self.until(runtime, lambda: runtime.snapshot().phase is Phase.READY)
        if armed:
            runtime.configure(Config())
            self.until(runtime, lambda: runtime.snapshot().pending_request_id == 0)
            runtime.arm()
            self.until(runtime, lambda: runtime.snapshot().phase is Phase.ARMED)
        return runtime, link

    def until(self, runtime, predicate):
        for _ in range(20000):
            if predicate():
                return
            runtime.pump()
            self.assertIsNot(runtime.snapshot().phase, Phase.RECOVERY)
        self.fail("bounded test pump did not reach expected state")

    def window(self, runtime, link):
        self.assertTrue(link.device.trigger(link.now))
        self.until(runtime, lambda: runtime.pending_capture is not None)
        return runtime.pending_capture

    def test_twenty_windows_save_reopen_before_ack_one_generation(self):
        runtime, link = self.make()
        generation = runtime.snapshot().generation
        with tempfile.TemporaryDirectory() as folder:
            for window in range(1, 21):
                capture = self.window(runtime, link)
                expected = [(73 * i + 19 * window + 7 * generation) & 4095 for i in range(2048)]
                expected[0], expected[511], expected[1023] = 2048, 0, 4095
                self.assertEqual(capture.samples, tuple(expected))
                self.assertEqual(capture.origin, "simulator")
                self.assertEqual(capture.nominal_fs_hz, 50000)
                before = len(link.writes)
                for _ in range(5):
                    runtime.pump()
                self.assertEqual(len(link.writes), before)
                path = Path(folder) / f"{window}.mtcap"
                save(path, capture)
                self.assertEqual(load(path), capture)
                runtime.acknowledge(capture)
                self.until(runtime, lambda: runtime.pending_capture is None)
                self.assertEqual(runtime.snapshot().generation, generation)
                self.assertEqual(runtime.snapshot().phase, Phase.ARMED)
        from motortester.wire import StreamDecoder
        frames = StreamDecoder().feed(b"".join(link.writes), 0).frames
        self.assertEqual(sum(f.message_type == 4 for f in frames), 1)
        self.assertEqual(sum(f.message_type == 7 for f in frames), 20)

    def test_capture_identity_and_double_ack(self):
        runtime, link = self.make()
        capture = self.window(runtime, link)
        with self.assertRaises(RuntimeError):
            runtime.acknowledge(replace(capture))
        runtime.acknowledge(capture)
        with self.assertRaises(RuntimeError):
            runtime.acknowledge(capture)

    def test_partial_byte_transport(self):
        runtime, link = self.make(armed=False)
        link.write_limit, link.read_limit = 1, 1
        runtime.configure(Config(edge=1))
        self.until(runtime, lambda: runtime.snapshot().pending_request_id == 0)
        runtime.arm()
        self.until(runtime, lambda: runtime.snapshot().phase is Phase.ARMED)
        capture = self.window(runtime, link)
        self.assertEqual(capture.trigger_edge, "falling")
        runtime.acknowledge(capture)
        self.until(runtime, lambda: runtime.pending_capture is None)

    def test_stop_after_complete_preserves_capture_without_release(self):
        runtime, link = self.make()
        capture = self.window(runtime, link)
        runtime.stop()
        with self.assertRaises(RuntimeError):
            runtime.acknowledge(capture)
        self.until(runtime, lambda: runtime.snapshot().phase is Phase.READY)
        self.assertIs(runtime.pending_capture, capture)
        before = len(link.writes)
        runtime.acknowledge(capture)
        runtime.pump()
        self.assertEqual(len(link.writes), before)

    def test_partial_disconnect_keeps_fragments_and_requires_explicit_reset(self):
        runtime, link = self.make()
        self.assertTrue(link.device.trigger(link.now))
        link.read_limit = 700
        runtime.pump()
        runtime.disconnect()
        result = runtime.incomplete_result
        self.assertIsNotNone(result)
        self.assertFalse(result.complete)
        self.assertTrue(result.fragments)
        self.assertIsNone(result.samples)
        with self.assertRaises(RuntimeError):
            runtime.start(102, reset_confirmed=True)
        self.assertIs(runtime.take_incomplete(), result)
        with self.assertRaises(RuntimeError):
            runtime.start(102)
        # Emulator reset is an actual fresh transport peer in this test.
        link.device = DeviceEmulator()
        runtime.start(102, reset_confirmed=True)
        self.until(runtime, lambda: runtime.snapshot().phase is Phase.READY)

    def test_unknown_release_write_never_retries_and_capture_can_be_retained(self):
        runtime, link = self.make()
        capture = self.window(runtime, link)
        runtime.acknowledge(capture)
        link.fail_write = True
        events = runtime.pump()
        self.assertTrue(any(e.reason is RecoveryReason.UNKNOWN_WRITE for e in events))
        before = len(link.writes)
        runtime.pump()
        self.assertEqual(len(link.writes), before)
        self.assertIs(runtime.pending_capture, capture)
        runtime.acknowledge(capture)
        self.assertIsNone(runtime.pending_capture)

    def test_zero_write_stall_is_bounded_but_trigger_wait_is_not(self):
        runtime, link = self.make(armed=False)
        runtime.configure(Config())
        link.write_limit = 0
        runtime.pump()
        link.now = 1000
        before = len(link.writes)
        runtime.pump()
        self.assertEqual(runtime.snapshot().phase, Phase.RECOVERY)
        self.assertEqual(len(link.writes), before)
        runtime, link = self.make()
        link.now = 600000
        runtime.pump()
        self.assertEqual(runtime.snapshot().phase, Phase.ARMED)

    def test_clock_rollback_after_write_never_resends(self):
        runtime, link = self.make(armed=False)
        link.now = 10
        runtime.configure(Config())
        original = link.write
        def write(data):
            count = original(data)
            link.now = 9
            return count
        link.write = write
        with self.assertRaises(RuntimeError):
            runtime.pump()
        self.assertEqual(runtime.snapshot().phase, Phase.RECOVERY)
        before = len(link.writes)
        link.now = 11
        runtime.pump()
        self.assertEqual(len(link.writes), before)


if __name__ == "__main__":
    unittest.main()

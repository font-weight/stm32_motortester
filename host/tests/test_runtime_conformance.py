"""Independent HOST-010 byte-stream and consumer-boundary conformance.

Expected bytes use the existing separately authored HOST-007 reference, never
runtime/session/wire encoders. The real DeviceEmulator and SerialTransport are
subjects of explicitly named integration tests, not expected-output oracles.
No bare asserts: the same checks and setup execute under python -O.
"""
import gc
import weakref
import importlib.util
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from uuid import UUID

from motortester.device_emulator import DeviceEmulator
from motortester.recording import load, save
from motortester.runtime import AcquisitionRuntime, RuntimeError as RuntimeActionError
from motortester.session import Config, Phase, EventKind, SessionError
from motortester.transport import SerialTransport, TransportState
from motortester.window import WindowCode

_ORACLE = Path(__file__).parent / "fixtures" / "device_emulator" / "reference.py"
_spec = importlib.util.spec_from_file_location("host010_independent_reference", _ORACLE)
ref = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ref
_spec.loader.exec_module(ref)
NONCE = 0x1020304050607080
UTC = "2026-10-10T12:00:00+00:00"


class Clock:
    def __init__(self):
        self.now = 0
        self.error = None

    def __call__(self):
        if self.error is not None:
            raise self.error
        return self.now


class ScriptTransport:
    """Observable single-operation transport with explicit injected outcomes."""
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.input = []
        self.writes = []
        self.accepted = bytearray()
        self.collector = ref.Collector()
        self.write_limit = None
        self.read_limit = 4096
        self.write_delay = 0
        self.read_delay = 0
        self.write_error = self.read_error = None
        self.write_result = None
        self.read_result = None
        self.is_open = False
        self.port = None

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def write(self, payload):
        self.calls.append(("write", payload))
        self.writes.append(payload)
        self.clock.now += self.write_delay
        if self.write_error is not None:
            raise self.write_error
        if self.write_result is not None:
            return self.write_result
        count = len(payload) if self.write_limit is None else min(self.write_limit, len(payload))
        self.accepted.extend(payload[:count])
        self.collector.feed(payload[:count])
        return count

    def read(self, size):
        self.calls.append(("read", size))
        self.clock.now += self.read_delay
        if self.read_error is not None:
            raise self.read_error
        if self.read_result is not None:
            return self.read_result
        if not self.input:
            return b""
        data = self.input[0]
        count = min(size, self.read_limit, len(data))
        result = data[:count]
        self.input[0] = data[count:]
        if not self.input[0]:
            self.input.pop(0)
        return result


class DeviceTransport(ScriptTransport):
    def __init__(self, clock):
        super().__init__(clock)
        self.device = DeviceEmulator()
        self.rx_collector = ref.Collector()

    def write(self, payload):
        self.calls.append(("write", payload))
        self.writes.append(payload)
        self.clock.now += self.write_delay
        if self.write_error is not None:
            raise self.write_error
        amount = len(payload) if self.write_limit is None else min(self.write_limit, len(payload))
        result = self.device.write(payload[:amount], self.clock.now)
        self.accepted.extend(payload[:result.accepted_prefix])
        self.collector.feed(payload[:result.accepted_prefix])
        return result.accepted_prefix

    def read(self, size):
        self.calls.append(("read", size))
        self.clock.now += self.read_delay
        if self.read_error is not None:
            raise self.read_error
        data = self.device.read(min(size, self.read_limit), self.clock.now)
        self.rx_collector.feed(data)
        return data


class Checks(unittest.TestCase):
    def make(self, *, device=False, serial=False, origin="simulator", utc_now=None):
        clock = Clock()
        backend = DeviceTransport(clock) if device else ScriptTransport(clock)
        transport = backend
        if serial:
            transport = SerialTransport(backend_factory=lambda **kwargs: backend)
            transport.open("COM_TEST_ONLY")
        runtime = AcquisitionRuntime(transport, origin=origin, clock_ms=clock,
                                     utc_now=utc_now or (lambda: UTC))
        return runtime, backend, clock, transport

    def pump(self, runtime, backend):
        first = len(backend.calls)
        events = runtime.pump()
        calls = backend.calls[first:]
        self.assertLessEqual(sum(c[0] == "read" for c in calls), 1)
        self.assertLessEqual(sum(c[0] == "write" for c in calls), 1)
        for kind, value in calls:
            if kind == "read":
                self.assertIs(type(value), int)
                self.assertLessEqual(value, 4096)
                self.assertGreater(value, 0)
            else:
                self.assertIs(type(value), bytes)
                self.assertGreater(len(value), 0)
                self.assertLessEqual(len(value), 4096)
        return events

    def until(self, runtime, backend, condition, limit=12000):
        for _ in range(limit):
            if condition():
                return
            self.pump(runtime, backend)
            self.assertIsNot(runtime.snapshot().phase, Phase.RECOVERY,
                             "unexpected recovery while driving valid byte stream")
        self.fail("bounded harness exhausted before expected result")

    def ready(self, *, device=False, serial=False, edge=0, level=2048, origin="simulator", utc_now=None):
        runtime, backend, clock, transport = self.make(device=device, serial=serial, origin=origin, utc_now=utc_now)
        runtime.start(NONCE)
        if not device:
            backend.input.append(ref.packet(129, sequence=1, request=1, payload=ref.hello_ack()))
        self.until(runtime, backend, lambda: runtime.snapshot().phase is Phase.CAPS)
        if not device:
            backend.input.append(ref.packet(130, sequence=2, request=2, payload=ref.caps()))
        self.until(runtime, backend, lambda: runtime.snapshot().phase is Phase.READY)
        runtime.configure(Config(edge=edge, level_code=level))
        if not device:
            backend.input.append(ref.packet(131, sequence=3, request=3,
                payload=ref.uint(1, 4) + ref.config(edge=edge, level=level)))
        self.until(runtime, backend, lambda: runtime.snapshot().config_id == 1)
        runtime.arm()
        if not device:
            backend.input.append(ref.packet(132, sequence=4, request=4,
                payload=ref.uint(4, 4) + ref.uint(1, 4)))
        self.until(runtime, backend, lambda: runtime.snapshot().phase is Phase.ARMED)
        return runtime, backend, clock, transport

    def prefix(self, runtime, backend, blocks=1):
        payloads = [(144, ref.begin())]
        payloads += [(145, ref.data(offset=250 * index)) for index in range(blocks)]
        backend.input.append(b"".join(ref.packet(kind, sequence=5 + index, request=0, payload=data)
                                      for index, (kind, data) in enumerate(payloads)))
        self.pump(runtime, backend)
        self.assertIsNone(runtime.pending_capture)
        self.assertIsNone(runtime.incomplete_result)

    def full_window(self, runtime, backend):
        payloads = [(144, ref.begin())]
        payloads += [(145, ref.data(offset=i, count=min(250, 2048 - i)))
                     for i in range(0, 2048, 250)]
        payloads += [(146, ref.end())]
        backend.input.append(b"".join(ref.packet(kind, sequence=5 + index, request=0, payload=data)
                                      for index, (kind, data) in enumerate(payloads)))
        self.until(runtime, backend, lambda: runtime.pending_capture is not None)
        return runtime.pending_capture

    def assert_fragments(self, runtime, count, reason):
        result = runtime.incomplete_result
        self.assertIsNotNone(result)
        self.assertFalse(result.complete)
        self.assertIs(result.reason, reason)
        self.assertIsNone(result.samples)
        self.assertIsNone(result.samples_crc32)
        actual = b"".join(ref.uint(code, 2) for block in result.fragments for code in block.samples)
        self.assertEqual(actual, ref.samples()[:2 * count])
        self.assertEqual(result.missing_ranges, ((count, 2048),))
        self.assertIsNone(runtime.pending_capture)
        return result

    def assert_quiet_recovery(self, runtime, backend):
        self.assertIs(runtime.snapshot().phase, Phase.RECOVERY)
        before = list(backend.calls)
        for _ in range(4):
            self.assertEqual(self.pump(runtime, backend), ())
        self.assertEqual(backend.calls, before)


class BytePeerAndConsumer(Checks):
    def test_twenty_saved_reopened_windows_one_arm_generation_through_serial(self):
        for edge, level in ((0, 2048), (1, 1)):
            with self.subTest(edge=edge, level=level), tempfile.TemporaryDirectory() as directory:
                runtime, backend, clock, transport = self.ready(device=True, serial=True, edge=edge, level=level)
                for window_id in range(1, 21):
                    self.assertTrue(backend.device.trigger(clock.now))
                    self.until(runtime, backend, lambda: runtime.pending_capture is not None)
                    capture = runtime.pending_capture
                    raw = b"".join(ref.uint(code, 2) for code in capture.samples)
                    self.assertEqual(raw, ref.samples(4, window_id, edge, level))
                    self.assertEqual(capture.session_id, str(UUID(int=NONCE)))
                    self.assertEqual(capture.sequence, window_id)
                    self.assertEqual(capture.nominal_fs_hz, 50000)
                    self.assertEqual(capture.origin, "simulator")
                    self.assertEqual(capture.received_at_utc, UTC)
                    self.assertIn("draft_protocol", capture.quality_flags)
                    self.assertIn("nominal_timing_unverified", capture.quality_flags)
                    self.assertIn("rail_observed", capture.quality_flags)
                    self.assertEqual(runtime.snapshot().generation, 4)
                    release_count = len([f for f in backend.collector.frames if f.kind == 7])
                    self.assertEqual(release_count, window_id - 1)
                    for _ in range(3):
                        self.pump(runtime, backend)
                        self.assertIs(runtime.pending_capture, capture)
                        self.assertTrue(backend.device.snapshot().held_samples)
                        self.assertFalse(backend.device.trigger(clock.now))
                    with self.assertRaises((RuntimeActionError, SessionError)):
                        runtime.arm()
                    with self.assertRaises((RuntimeActionError, SessionError)):
                        runtime.configure(Config())
                    path = Path(directory) / f"window-{window_id}.mtcap"
                    save(path, capture)
                    reopened = load(path)
                    self.assertEqual(reopened, capture)
                    self.assertEqual(b"".join(ref.uint(x, 2) for x in reopened.samples), raw)
                    with self.assertRaises(RuntimeActionError):
                        runtime.acknowledge(reopened)
                    self.assertEqual(len([f for f in backend.collector.frames if f.kind == 7]), release_count)
                    runtime.acknowledge(capture)
                    self.assertIs(runtime.pending_capture, capture)
                    self.until(runtime, backend, lambda: runtime.pending_capture is None)
                    release = [f for f in backend.collector.frames if f.kind == 7][-1]
                    self.assertEqual(release.payload, ref.release(4, window_id, edge, level))
                    self.assertIsNone(runtime.incomplete_result)
                    clock.now += 1
                self.assertEqual(len([f for f in backend.collector.frames if f.kind == 4]), 1)
                self.assertEqual(len([f for f in backend.collector.frames if f.kind == 7]), 20)
                self.assertEqual(len([f for f in backend.rx_collector.frames if f.kind == 146]), 20)
                self.assertIs(transport.state, TransportState.OPEN)
                transport.close()

    def test_short_writes_send_only_unaccepted_suffix_without_new_ids(self):
        runtime, backend, clock, _ = self.make(device=True)
        backend.write_limit, backend.read_limit = 1, 1
        runtime.start(NONCE)
        self.until(runtime, backend, lambda: runtime.snapshot().phase is Phase.READY)
        self.assertEqual(bytes(backend.accepted), ref.hello() + ref.packet(2))
        expected = ref.hello()
        for index in range(len(expected)):
            self.assertEqual(backend.writes[index], expected[index:])
        self.assertEqual([(f.kind, f.sequence, f.request) for f in backend.collector.frames],
                         [(1, 1, 1), (2, 2, 2)])

    def test_split_release_ack_keeps_capture_until_final_byte(self):
        runtime, backend, clock, _ = self.ready()
        capture = self.full_window(runtime, backend)
        runtime.acknowledge(capture)
        ack = ref.packet(135, sequence=16, request=5, payload=ref.uint(4, 4) + ref.uint(1, 4))
        backend.input.append(ack[:-1])
        self.pump(runtime, backend)
        self.assertIs(runtime.pending_capture, capture)
        self.assertTrue(runtime.snapshot().window_barrier)
        with self.assertRaises(RuntimeActionError):
            runtime.acknowledge(capture)
        backend.input.append(ack[-1:])
        self.pump(runtime, backend)
        self.assertIsNone(runtime.pending_capture)
        self.assertFalse(runtime.snapshot().window_barrier)
        with self.assertRaises(RuntimeActionError):
            runtime.acknowledge(capture)

    def test_release_ack_and_next_window_in_one_read_preserve_new_owner(self):
        runtime, backend, clock, _ = self.ready()
        previous = self.full_window(runtime, backend)
        runtime.acknowledge(previous)
        payloads = [(135, ref.uint(4, 4) + ref.uint(1, 4)), (144, ref.begin(window=2))]
        payloads += [(145, ref.data(window=2, offset=i, count=min(250, 2048 - i)))
                     for i in range(0, 2048, 250)]
        payloads += [(146, ref.end(window=2))]
        backend.input.append(b"".join(ref.packet(kind, sequence=16 + index,
            request=5 if index == 0 else 0, payload=data)
            for index, (kind, data) in enumerate(payloads)))
        self.pump(runtime, backend)
        self.assertIsNone(runtime.pending_capture)
        self.assertTrue(runtime.snapshot().window_barrier)
        self.until(runtime, backend, lambda: runtime.pending_capture is not None)
        current = runtime.pending_capture
        self.assertIsNot(current, previous)
        self.assertEqual(current.sequence, 2)
        self.assertEqual(b"".join(ref.uint(x, 2) for x in current.samples), ref.samples(window=2))
        self.assertEqual(runtime.snapshot().generation, 4)
        with self.assertRaises(RuntimeActionError):
            runtime.acknowledge(previous)
        self.assertIs(runtime.pending_capture, current)
        self.assertEqual(len([f for f in backend.collector.frames if f.kind == 4]), 1)
        self.assertEqual(len([f for f in backend.collector.frames if f.kind == 7]), 1)

    def test_waiting_normal_does_not_manufacture_capture_or_timeout(self):
        runtime, backend, clock, _ = self.ready(device=True)
        clock.now = 10**9
        self.pump(runtime, backend)
        self.assertIs(runtime.snapshot().phase, Phase.ARMED)
        self.assertIsNone(runtime.pending_capture)
        self.assertIsNone(runtime.incomplete_result)
        self.assertFalse(any(f.kind == 7 for f in backend.collector.frames))

    def test_save_failure_does_not_acknowledge_or_lose_capture(self):
        runtime, backend, clock, _ = self.ready(device=True)
        self.assertTrue(backend.device.trigger(0))
        self.until(runtime, backend, lambda: runtime.pending_capture is not None)
        capture = runtime.pending_capture
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                save(Path(directory) / "absent" / "capture.mtcap", capture)
        self.pump(runtime, backend)
        self.assertIs(runtime.pending_capture, capture)
        self.assertFalse(any(f.kind == 7 for f in backend.collector.frames))
        self.assertTrue(backend.device.snapshot().held_samples)

    def test_released_capture_is_not_retained_by_hidden_queue(self):
        runtime, backend, clock, _ = self.ready(device=True)
        self.assertTrue(backend.device.trigger(0))
        self.until(runtime, backend, lambda: runtime.pending_capture is not None)
        capture = runtime.pending_capture
        previous = weakref.ref(capture)
        runtime.acknowledge(capture)
        self.until(runtime, backend, lambda: runtime.pending_capture is None)
        del capture
        gc.collect()
        self.assertIsNone(previous())
        self.assertTrue(backend.device.trigger(0))
        self.until(runtime, backend, lambda: runtime.pending_capture is not None)
        self.assertEqual(runtime.pending_capture.sequence, 2)

    def test_device_origin_is_explicit_and_samples_remain_raw(self):
        runtime, backend, clock, _ = self.ready(origin="device")
        capture = self.full_window(runtime, backend)
        self.assertEqual(capture.origin, "device")
        self.assertEqual(capture.firmware_version, "0")
        self.assertEqual(capture.trigger_kind, "signal")
        self.assertEqual(capture.trigger_index, 0)
        self.assertEqual(capture.pretrigger_samples, 0)
        self.assertEqual(capture.samples[511], 0)
        self.assertEqual(capture.samples[1023], 4095)


class StopAndRetention(Checks):
    def test_stop_mid_window_keeps_exact_fragments_and_no_release(self):
        runtime, backend, clock, _ = self.ready()
        self.prefix(runtime, backend, blocks=2)
        runtime.stop()
        backend.input.append(ref.packet(147, sequence=8, request=0, payload=ref.abort())
            + ref.packet(133, sequence=9, request=5,
                         payload=ref.uint(4, 4) + ref.uint(1, 4) + b"\0" * 4))
        events = self.pump(runtime, backend)
        self.assertTrue(any(e.kind is EventKind.STOPPED for e in events))
        result = self.assert_fragments(runtime, 500, WindowCode.ABORT)
        self.assertEqual(result.abort_reason, 1)
        self.assertIs(runtime.snapshot().phase, Phase.READY)
        self.assertFalse(any(f.kind == 7 for f in backend.collector.frames))
        with self.assertRaises(RuntimeActionError):
            runtime.arm()
        self.assertIs(runtime.take_incomplete(), result)
        self.assertIsNone(runtime.take_incomplete())
        runtime.arm()

    def test_device_stop_inside_data_frame_preserves_every_completed_fragment(self):
        runtime, backend, clock, _ = self.ready(device=True)
        backend.read_limit = 900
        self.assertTrue(backend.device.trigger(0))
        self.pump(runtime, backend)
        self.assertIsNotNone(backend.device.snapshot().tx_offset)
        self.assertIsNone(runtime.pending_capture)
        runtime.stop()
        self.until(runtime, backend, lambda: runtime.snapshot().phase is Phase.READY)
        result = runtime.incomplete_result
        self.assertIsNotNone(result)
        self.assertIs(result.reason, WindowCode.ABORT)
        received = [f for f in backend.rx_collector.frames if f.kind == 145]
        actual = b"".join(ref.uint(code, 2) for block in result.fragments for code in block.samples)
        expected = b"".join(f.payload[12:] for f in received)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ref.samples()[:len(actual)])
        self.assertLess(len(actual), 4096)
        self.assertIsNone(result.samples)
        self.assertEqual(result.missing_ranges, ((len(actual) // 2, 2048),))
        self.assertEqual(len([f for f in backend.rx_collector.frames if f.kind == 147]), 1)
        self.assertFalse(any(f.kind in (7,) for f in backend.collector.frames))
        self.assertFalse(any(f.kind == 146 for f in backend.rx_collector.frames))

    def test_stop_complete_capture_retained_until_consumer_ack(self):
        runtime, backend, clock, _ = self.ready(device=True)
        self.assertTrue(backend.device.trigger(0))
        self.until(runtime, backend, lambda: runtime.pending_capture is not None)
        capture = runtime.pending_capture
        runtime.stop()
        with self.assertRaises(RuntimeActionError):
            runtime.acknowledge(capture)
        self.until(runtime, backend, lambda: runtime.snapshot().phase is Phase.READY)
        self.assertIs(runtime.pending_capture, capture)
        self.assertFalse(backend.device.trigger(0))
        runtime.acknowledge(capture)
        self.assertIsNone(runtime.pending_capture)
        self.pump(runtime, backend)
        self.assertFalse(any(f.kind == 7 for f in backend.collector.frames))

    def test_stop_during_partial_arm_finishes_frame_before_stop(self):
        runtime, backend, clock, _ = self.make(device=True)
        runtime.start(NONCE)
        self.until(runtime, backend, lambda: runtime.snapshot().phase is Phase.READY)
        runtime.configure(Config())
        self.until(runtime, backend, lambda: runtime.snapshot().config_id == 1)
        backend.write_limit = 2
        runtime.arm()
        self.pump(runtime, backend)
        runtime.stop()
        self.until(runtime, backend, lambda: not runtime.snapshot().stop_barrier)
        self.assertIs(runtime.snapshot().phase, Phase.READY)
        commands = backend.collector.frames
        self.assertEqual([f.kind for f in commands], [1, 2, 3, 4, 5])
        self.assertEqual(commands[-1].payload, ref.uint(4, 4))
        self.assertEqual([f.sequence for f in commands], [1, 2, 3, 4, 5])
        self.assertIsNone(runtime.pending_capture)

    def test_disconnect_requires_retention_and_explicit_fresh_reset(self):
        runtime, backend, clock, _ = self.ready()
        self.prefix(runtime, backend)
        runtime.disconnect()
        result = self.assert_fragments(runtime, 250, WindowCode.DISCONNECT)
        with self.assertRaises(RuntimeActionError):
            runtime.start(NONCE + 1, reset_confirmed=True)
        self.assertIs(runtime.take_incomplete(), result)
        with self.assertRaises(RuntimeActionError):
            runtime.start(NONCE + 1)
        with self.assertRaises(SessionError):
            runtime.start(NONCE, reset_confirmed=True)
        before = len(backend.calls)
        runtime.start(NONCE + 1, reset_confirmed=True)
        self.assertEqual(len(backend.calls), before)
        self.assertIs(runtime.snapshot().phase, Phase.HELLO)
        self.assertIsNone(runtime.incomplete_result)
        self.pump(runtime, backend)
        self.assertEqual(backend.collector.frames[-1].packet, ref.hello(NONCE + 1))
        self.assertEqual(result.missing_ranges, ((250, 2048),))

    def test_failed_release_acknowledgement_can_be_cleared_in_recovery(self):
        for failure in ("unknown_write", "response_timeout", "disconnect"):
            with self.subTest(failure=failure):
                runtime, backend, clock, _ = self.ready()
                capture = self.full_window(runtime, backend)
                runtime.acknowledge(capture)
                if failure == "unknown_write":
                    backend.write_error = OSError("prefix outcome unknown")
                    self.pump(runtime, backend)
                elif failure == "response_timeout":
                    self.pump(runtime, backend)
                    clock.now = 1000
                    self.pump(runtime, backend)
                else:
                    runtime.disconnect()
                self.assert_quiet_recovery(runtime, backend)
                self.assertIs(runtime.pending_capture, capture)
                with self.assertRaises(RuntimeActionError):
                    runtime.start(NONCE + 1, reset_confirmed=True)
                runtime.acknowledge(capture)
                self.assertIsNone(runtime.pending_capture)
                with self.assertRaises(RuntimeActionError):
                    runtime.acknowledge(capture)
                runtime.start(NONCE + 1, reset_confirmed=True)
                self.assertIs(runtime.snapshot().phase, Phase.HELLO)


class DeadlinesAndTransportFailures(Checks):
    def test_zero_write_no_progress_deadline_999_then_1000(self):
        runtime, backend, clock, _ = self.make()
        backend.write_limit = 0
        runtime.start(NONCE)
        self.pump(runtime, backend)
        self.assertEqual(runtime.snapshot().next_tx_sequence, 1)
        clock.now = 999
        self.pump(runtime, backend)
        self.assertIs(runtime.snapshot().phase, Phase.HELLO)
        before = len(backend.calls)
        clock.now = 1000
        self.pump(runtime, backend)
        self.assertIs(runtime.snapshot().phase, Phase.RECOVERY)
        self.assertEqual(len(backend.calls), before)
        self.assertEqual(bytes(backend.accepted), b"")
        self.assert_quiet_recovery(runtime, backend)

    def test_post_io_zero_write_stall_expires_before_read(self):
        runtime, backend, clock, _ = self.make()
        backend.write_limit, backend.write_delay = 0, 1000
        runtime.start(NONCE)
        self.pump(runtime, backend)
        self.assertIs(runtime.snapshot().phase, Phase.RECOVERY)
        self.assertEqual([c[0] for c in backend.calls], ["write"])

    def test_post_read_write_stall_expires_at_observed_deadline(self):
        for count in (0, 1):
            for elapsed, phase in ((999, Phase.HELLO), (1000, Phase.RECOVERY)):
                with self.subTest(count=count, elapsed=elapsed):
                    runtime, backend, clock, _ = self.make()
                    backend.write_limit, backend.read_delay = count, elapsed
                    runtime.start(NONCE)
                    self.pump(runtime, backend)
                    self.assertIs(runtime.snapshot().phase, phase)
                    self.assertEqual([c[0] for c in backend.calls], ["write", "read"])
                    self.assertEqual(bytes(backend.accepted), ref.hello()[:count])
                    if phase is Phase.RECOVERY:
                        self.assert_quiet_recovery(runtime, backend)

    def test_positive_prefix_resets_write_stall_but_never_response_timeout(self):
        runtime, backend, clock, _ = self.make()
        backend.write_limit = 1
        runtime.start(NONCE)
        self.pump(runtime, backend)
        clock.now = 999
        self.pump(runtime, backend)
        backend.write_limit = 0
        clock.now = 1998
        self.pump(runtime, backend)
        self.assertIs(runtime.snapshot().phase, Phase.HELLO)
        self.assertEqual(bytes(backend.accepted), ref.hello()[:2])
        self.assertEqual(backend.writes[-1], ref.hello()[2:])
        clock.now = 1999
        self.pump(runtime, backend)
        self.assert_quiet_recovery(runtime, backend)

    def test_post_read_response_deadline_uses_last_write_return_time(self):
        for elapsed, expected in ((999, Phase.CAPS), (1000, Phase.RECOVERY)):
            with self.subTest(elapsed=elapsed):
                runtime, backend, clock, _ = self.make()
                backend.write_delay, backend.read_delay = 700, elapsed
                backend.input.append(ref.packet(129, sequence=1, request=1, payload=ref.hello_ack()))
                runtime.start(NONCE)
                self.pump(runtime, backend)
                self.assertEqual(clock.now, 700 + elapsed)
                self.assertIs(runtime.snapshot().phase, expected)

    def test_response_timeout_without_bytes_does_not_repeat_command(self):
        runtime, backend, clock, _ = self.make()
        runtime.start(NONCE)
        self.pump(runtime, backend)
        clock.now = 999
        self.pump(runtime, backend)
        self.assertIs(runtime.snapshot().phase, Phase.HELLO)
        clock.now = 1000
        self.pump(runtime, backend)
        self.assert_quiet_recovery(runtime, backend)
        self.assertEqual(backend.writes, [ref.hello()])

    def test_progress_timeout_exact_boundary_preserves_only_received_samples(self):
        runtime, backend, clock, _ = self.ready()
        self.prefix(runtime, backend)
        clock.now = 2999
        self.pump(runtime, backend)
        self.assertIsNone(runtime.incomplete_result)
        clock.now = 3000
        self.pump(runtime, backend)
        self.assert_fragments(runtime, 250, WindowCode.TIMEOUT)
        self.assert_quiet_recovery(runtime, backend)

    def test_post_read_progress_deadline_rejects_late_data(self):
        runtime, backend, clock, _ = self.ready()
        self.prefix(runtime, backend)
        backend.read_delay = 3000
        backend.input.append(ref.packet(145, sequence=7, request=0, payload=ref.data(offset=250)))
        self.pump(runtime, backend)
        self.assert_fragments(runtime, 250, WindowCode.TIMEOUT)
        self.assert_quiet_recovery(runtime, backend)

    def test_unknown_write_and_invalid_counts_never_retry(self):
        for result in (True, -1, 9999, 0.5, "1", OSError("unknown prefix")):
            with self.subTest(result=repr(result)):
                runtime, backend, clock, _ = self.make()
                if isinstance(result, Exception):
                    backend.write_error = result
                else:
                    backend.write_result = result
                runtime.start(NONCE)
                events = self.pump(runtime, backend)
                self.assertEqual([c[0] for c in backend.calls], ["write"])
                self.assertEqual([e.reason.value for e in events if e.kind is EventKind.RECOVERY], ["UNKNOWN_WRITE"])
                self.assert_quiet_recovery(runtime, backend)

    def test_read_faults_preserve_fragments_without_backend_text(self):
        for failure in (OSError("sensitive backend details"), bytearray(b"x"), b"x" * 4097, "x"):
            with self.subTest(failure=type(failure).__name__):
                runtime, backend, clock, _ = self.ready()
                self.prefix(runtime, backend)
                if isinstance(failure, Exception):
                    backend.read_error = failure
                else:
                    backend.read_result = failure
                events = self.pump(runtime, backend)
                self.assert_fragments(runtime, 250, WindowCode.DISCONNECT)
                self.assert_quiet_recovery(runtime, backend)
                self.assertNotIn("sensitive backend details", repr(events))

    def test_real_transport_unknown_write_preserves_runtime_fragments(self):
        runtime, backend, clock, transport = self.ready(device=True, serial=True)
        backend.read_limit = 900
        self.assertTrue(backend.device.trigger(0))
        self.pump(runtime, backend)
        runtime.stop()
        backend.write_error = TimeoutError("driver accepted unknown bytes")
        self.pump(runtime, backend)
        self.assertIs(transport.state, TransportState.RECOVERY)
        result = runtime.incomplete_result
        self.assertIsNotNone(result)
        self.assertIsNone(result.samples)
        self.assertEqual(result.fragments[0].offset, 0)
        self.assertEqual(len(result.fragments[0].samples), 250)
        self.assert_quiet_recovery(runtime, backend)


class MalformedByteStreams(Checks):
    def test_mixed_valid_corrupt_coalesced_read_is_rejected_as_a_batch(self):
        # StreamDecoder deliberately reports aggregate errors without order.
        # Therefore no frame in a corrupt batch may publish a capture; already
        # admitted fragments from earlier reads must still survive recovery.
        runtime, backend, clock, _ = self.ready()
        self.prefix(runtime, backend)
        valid = ref.packet(145, sequence=7, request=0, payload=ref.data(offset=250))
        corrupted = bytearray(ref.uncobs(ref.packet(145, sequence=8, request=0,
            payload=ref.data(offset=500))[:-1]))
        corrupted[-1] ^= 1
        backend.input.append(valid + ref.cobs(bytes(corrupted)) + b"\0"
                             + ref.packet(146, sequence=9, request=0, payload=ref.end()))
        self.pump(runtime, backend)
        self.assert_fragments(runtime, 250, WindowCode.DISCONNECT)
        self.assert_quiet_recovery(runtime, backend)
        self.assertFalse(any(f.kind == 7 for f in backend.collector.frames))

    def test_corrupt_crc_after_good_fragment_fails_closed(self):
        runtime, backend, clock, _ = self.ready()
        self.prefix(runtime, backend)
        packet = ref.packet(145, sequence=7, request=0, payload=ref.data(offset=250))
        raw = bytearray(ref.uncobs(packet[:-1]))
        raw[-1] ^= 1
        backend.input.append(ref.cobs(bytes(raw)) + b"\0")
        self.pump(runtime, backend)
        self.assert_fragments(runtime, 250, WindowCode.DISCONNECT)
        self.assert_quiet_recovery(runtime, backend)

    def test_gap_adc_and_wrong_window_crc_never_publish_capture(self):
        for failure in ("gap", "adc", "crc"):
            with self.subTest(failure=failure):
                runtime, backend, clock, _ = self.ready()
                self.prefix(runtime, backend)
                if failure == "gap":
                    payload = ref.data(offset=500)
                    backend.input.append(ref.packet(145, sequence=7, request=0, payload=payload))
                    reason = WindowCode.ORDER
                elif failure == "adc":
                    payload = ref.data(offset=250)
                    payload = payload[:12] + ref.uint(4096, 2) + payload[14:]
                    backend.input.append(ref.packet(145, sequence=7, request=0, payload=payload))
                    reason = WindowCode.DISCONNECT  # Control admission rejects invalid ADC first.
                else:
                    blocks = [(145, ref.data(offset=i, count=min(250, 2048 - i)))
                              for i in range(250, 2048, 250)]
                    end = ref.end()
                    blocks.append((146, end[:-4] + ref.uint(ref.number(end[-4:]) ^ 1, 4)))
                    backend.input.append(b"".join(ref.packet(kind, sequence=7 + index, request=0, payload=data)
                        for index, (kind, data) in enumerate(blocks)))
                    reason = WindowCode.CRC
                for _ in range(3):
                    self.pump(runtime, backend)
                    if runtime.snapshot().phase is Phase.RECOVERY:
                        break
                result = runtime.incomplete_result
                self.assertIsNotNone(result)
                self.assertIs(result.reason, reason)
                count = 2048 if failure == "crc" else 250
                actual = b"".join(ref.uint(code, 2) for block in result.fragments for code in block.samples)
                self.assertEqual(actual, ref.samples()[:2 * count])
                self.assertEqual(result.missing_ranges, () if count == 2048 else ((250, 2048),))
                self.assertIsNone(result.samples)
                self.assertIsNone(runtime.pending_capture)
                self.assertFalse(any(f.kind == 7 for f in backend.collector.frames))

    def test_duplicate_data_new_sequence_does_not_refresh_progress(self):
        runtime, backend, clock, _ = self.ready()
        self.prefix(runtime, backend)
        clock.now = 2999
        backend.input.append(ref.packet(145, sequence=7, request=0, payload=ref.data()))
        self.pump(runtime, backend)
        clock.now = 3000
        self.pump(runtime, backend)
        self.assert_fragments(runtime, 250, WindowCode.TIMEOUT)
        self.assert_quiet_recovery(runtime, backend)

    def test_stale_release_ack_cannot_clear_current_capture(self):
        for wrong in ("request", "window", "session"):
            with self.subTest(wrong=wrong):
                runtime, backend, clock, _ = self.ready()
                capture = self.full_window(runtime, backend)
                runtime.acknowledge(capture)
                backend.input.append(ref.packet(135, session=NONCE - 1 if wrong == "session" else NONCE,
                    sequence=16, request=99 if wrong == "request" else 5,
                    payload=ref.uint(4, 4) + ref.uint(2 if wrong == "window" else 1, 4)))
                self.pump(runtime, backend)
                self.assertIs(runtime.pending_capture, capture)
                if wrong == "session":
                    self.assertIs(runtime.snapshot().phase, Phase.ARMED)
                    clock.now = 1000
                    self.pump(runtime, backend)
                self.assert_quiet_recovery(runtime, backend)

    def test_unreleased_second_begin_never_replaces_first_capture(self):
        runtime, backend, clock, _ = self.ready()
        capture = self.full_window(runtime, backend)
        backend.input.append(ref.packet(144, sequence=16, request=0, payload=ref.begin(window=2)))
        self.pump(runtime, backend)
        self.assertIs(runtime.pending_capture, capture)
        self.assert_quiet_recovery(runtime, backend)
        self.assertFalse(any(f.kind == 7 for f in backend.collector.frames))

    def test_truncated_wire_frame_times_out_without_padding(self):
        runtime, backend, clock, _ = self.ready()
        self.prefix(runtime, backend)
        backend.input.append(ref.packet(145, sequence=7, request=0, payload=ref.data(offset=250))[:10])
        self.pump(runtime, backend)
        clock.now = 1000
        self.pump(runtime, backend)
        self.assert_fragments(runtime, 250, WindowCode.DISCONNECT)
        self.assert_quiet_recovery(runtime, backend)


class CallbackFailuresAndArguments(Checks):
    def test_import_and_idle_runtime_need_no_qt_or_serial_import(self):
        source = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'pyqtgraph', 'serial'):
        raise ImportError('forbidden optional runtime dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from motortester.runtime import AcquisitionRuntime
class NoIO:
    def read(self, size):
        raise RuntimeError('idle runtime performed read')
    def write(self, payload):
        raise RuntimeError('idle runtime performed write')
runtime = AcquisitionRuntime(NoIO(), origin='simulator', clock_ms=lambda: 0)
if runtime.pump() != ():
    raise RuntimeError('idle runtime produced unexpected events')
print('independent import and idle check passed')
"""
        result = subprocess.run([sys.executable, "-B", "-c", source],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("independent import and idle check passed", result.stdout)

    def test_clock_invalid_or_rollback_after_write_recovers_without_retry(self):
        for value in (99, -1, True, 0.25, 2**64, OSError("clock failed")):
            with self.subTest(value=repr(value)):
                runtime, backend, clock, _ = self.make()
                clock.now = 100
                runtime.start(NONCE)
                original = backend.write

                def write(payload):
                    count = original(payload)
                    if isinstance(value, Exception):
                        clock.error = value
                    else:
                        clock.now = value
                    return count

                backend.write = write
                with self.assertRaises(RuntimeActionError):
                    self.pump(runtime, backend)
                self.assertIs(runtime.snapshot().phase, Phase.RECOVERY)
                self.assertEqual([c[0] for c in backend.calls], ["write"])
                self.assertEqual(bytes(backend.accepted), ref.hello())
                clock.error, clock.now = None, 101
                self.assert_quiet_recovery(runtime, backend)

    def test_clock_failure_after_read_keeps_previously_admitted_fragments(self):
        runtime, backend, clock, _ = self.ready()
        self.prefix(runtime, backend)
        original = backend.read

        def read(size):
            data = original(size)
            clock.error = OSError("clock failed")
            return data

        backend.read = read
        with self.assertRaises(RuntimeActionError):
            self.pump(runtime, backend)
        self.assert_fragments(runtime, 250, WindowCode.DISCONNECT)
        clock.error = None
        self.assert_quiet_recovery(runtime, backend)

    def test_utc_callback_failure_retains_complete_raw_result_for_manual_recovery(self):
        for outcome in ("bad date", "2026-10-10T12:00:00+05:00", OSError("UTC unavailable")):
            with self.subTest(outcome=repr(outcome)):
                def utc_now():
                    if isinstance(outcome, Exception):
                        raise outcome
                    return outcome

                fresh, backend, clock, _ = self.ready(utc_now=utc_now)
                payloads = [(144, ref.begin())]
                payloads += [(145, ref.data(offset=i, count=min(250, 2048 - i)))
                             for i in range(0, 2048, 250)]
                payloads += [(146, ref.end())]
                backend.input.append(b"".join(ref.packet(kind, sequence=5 + index, request=0, payload=data)
                    for index, (kind, data) in enumerate(payloads)))
                for _ in range(3):
                    self.pump(fresh, backend)
                self.assert_quiet_recovery(fresh, backend)
                self.assertIsNone(fresh.pending_capture)
                result = fresh.incomplete_result
                self.assertIsNotNone(result)
                self.assertTrue(result.complete)
                self.assertEqual(b"".join(ref.uint(x, 2) for x in result.samples), ref.samples())
                self.assertFalse(any(f.kind == 7 for f in backend.collector.frames))
                with self.assertRaises(RuntimeActionError):
                    fresh.start(NONCE + 1, reset_confirmed=True)
                self.assertIs(fresh.take_incomplete(), result)
                fresh.start(NONCE + 1, reset_confirmed=True)

    def test_invalid_constructor_inputs_and_reset_token_fail_before_io(self):
        clock = Clock()
        backend = ScriptTransport(clock)
        for kwargs in ({"origin": "unknown", "clock_ms": clock},
                       {"origin": "simulator", "clock_ms": None},
                       {"origin": "simulator", "clock_ms": clock, "utc_now": 7}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeActionError):
                AcquisitionRuntime(backend, **kwargs)
        runtime = AcquisitionRuntime(backend, origin="simulator", clock_ms=clock)
        for flag in (None, 1, "yes"):
            with self.subTest(flag=flag), self.assertRaises(RuntimeActionError):
                runtime.start(NONCE, reset_confirmed=flag)
        self.assertEqual(backend.calls, [])
        self.assertIs(runtime.snapshot().phase, Phase.IDLE)


if __name__ == "__main__":
    unittest.main()

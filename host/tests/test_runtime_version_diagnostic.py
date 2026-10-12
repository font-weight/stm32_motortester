"""Real-byte regression checks for the bounded runtime VERSION diagnostic.

Byte fixtures use the existing independent reference, not production encoders.
These are software checks; the scripted transport never opens a physical port.
"""
import importlib.util
from pathlib import Path
import sys
import unittest

from motortester.runtime import AcquisitionRuntime, RuntimeError
from motortester.session import Config, EventKind, FaultCode, Phase, RecoveryReason
from motortester.window import WindowCode
from motortester.wire import DiagnosticCode, StreamDecoder


_PATH = Path(__file__).parent / "fixtures" / "device_emulator" / "reference.py"
_SPEC = importlib.util.spec_from_file_location("runtime_version_reference", _PATH)
ref = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ref
_SPEC.loader.exec_module(ref)


def hello_ack(**kwargs):
    return ref.packet(129, sequence=1, request=1, payload=ref.hello_ack(), **kwargs)


def corrupt_crc(packet):
    raw = ref.uncobs(packet[:-1])
    return ref.cobs(raw[:-1] + bytes((raw[-1] ^ 1,))) + b"\0"


class ScriptTransport:
    def __init__(self):
        self.now = 0
        self.input = []
        self.calls = []
        self.commands = ref.Collector()

    def write(self, data):
        self.calls.append(("write", data))
        self.commands.feed(data)
        return len(data)

    def read(self, size):
        self.calls.append(("read", size))
        if not self.input:
            return b""
        data = self.input[0][:size]
        self.input[0] = self.input[0][size:]
        if not self.input[0]:
            self.input.pop(0)
        return data


class RuntimeVersionDiagnosticTests(unittest.TestCase):
    def make(self):
        link = ScriptTransport()
        runtime = AcquisitionRuntime(link, origin="simulator", clock_ms=lambda: link.now,
                                     utc_now=lambda: "2026-10-12T00:00:00Z")
        runtime.start(ref.NONCE)
        return runtime, link

    def feed(self, runtime, link, data):
        link.input.append(data)
        return runtime.pump()

    def ready(self, *, armed=False):
        runtime, link = self.make()
        self.feed(runtime, link, hello_ack())
        self.assertIs(runtime.snapshot().phase, Phase.CAPS)
        self.feed(runtime, link, ref.packet(130, payload=ref.caps()))
        self.assertIs(runtime.snapshot().phase, Phase.READY)
        if armed:
            runtime.configure(Config())
            self.feed(runtime, link, ref.packet(131, sequence=3, request=3,
                payload=ref.uint(1, 4) + ref.config()))
            runtime.arm()
            self.feed(runtime, link, ref.packet(132, sequence=4, request=4,
                payload=ref.uint(4, 4) + ref.uint(1, 4)))
            self.assertIs(runtime.snapshot().phase, Phase.ARMED)
        return runtime, link

    def assert_fault(self, runtime, events, fault):
        self.assertIs(runtime.snapshot().phase, Phase.RECOVERY)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIs(event.kind, EventKind.RECOVERY)
        self.assertIs(event.reason, RecoveryReason.PROTOCOL_FAULT)
        self.assertIs(event.fault, fault)
        self.assertTrue(event.preserve_fragments)
        self.assertTrue(event.reset_transport)

    def assert_quiet(self, runtime, link, expected_commands):
        before = list(link.calls)
        for _ in range(4):
            self.assertEqual(runtime.pump(), ())
        self.assertEqual(link.calls, before)
        self.assertEqual([frame.kind for frame in link.commands.frames], expected_commands)
        with self.assertRaises(RuntimeError):
            runtime.start(ref.NONCE + 1)
        self.assertEqual(link.calls, before)

    def assert_decoder(self, data, *codes):
        batch = StreamDecoder().feed(data, 0)
        self.assertEqual(tuple(item.code for item in batch.diagnostics), codes)
        return batch

    def test_supported_tuple_still_connects_without_automatic_arm(self):
        runtime, link = self.ready()
        self.assertEqual([f.kind for f in link.commands.frames], [1, 2])
        for _ in range(4):
            self.assertEqual(runtime.pump(), ())
        self.assertIs(runtime.snapshot().phase, Phase.READY)
        self.assertIsNone(runtime.pending_capture)
        self.assertEqual([f.kind for f in link.commands.frames], [1, 2])

    def test_complete_unsupported_major_and_minor_report_version_and_fail_closed(self):
        for major, minor in ((1, 1), (0, 0), (0, 2), (255, 255)):
            with self.subTest(major=major, minor=minor):
                packet = hello_ack(major=major, minor=minor)
                batch = self.assert_decoder(packet, DiagnosticCode.VERSION)
                self.assertEqual(batch.frames, ())
                runtime, link = self.make()
                self.assert_fault(runtime, self.feed(runtime, link, packet), FaultCode.VERSION)
                self.assertIsNone(runtime.snapshot().capabilities)
                self.assertIsNone(runtime.pending_capture)
                self.assertIsNone(runtime.incomplete_result)
                self.assert_quiet(runtime, link, [1])

    def test_corrupt_and_ambiguous_frames_keep_decoder_precedence_and_decode_fault(self):
        packets = (
            (corrupt_crc(hello_ack(major=1)), DiagnosticCode.CRC),
            (hello_ack(major=1, declared_length=1), DiagnosticCode.LENGTH),
            (b"\xff\x02\0", DiagnosticCode.FRAMING),
            (hello_ack(reserved=1), DiagnosticCode.RESERVED),
            (hello_ack(reserved16=1), DiagnosticCode.RESERVED),
            (b"\x01" * 544 + b"\0", DiagnosticCode.OVERFLOW),
        )
        for packet, code in packets:
            with self.subTest(code=code):
                self.assert_decoder(packet, code)
                runtime, link = self.make()
                self.assert_fault(runtime, self.feed(runtime, link, packet), FaultCode.DECODE)
                self.assertIsNone(runtime.pending_capture)
                self.assert_quiet(runtime, link, [1])

    def test_version_precedes_reserved_only_when_crc_and_length_are_valid(self):
        packet = hello_ack(major=1, reserved=1, reserved16=1)
        self.assert_decoder(packet, DiagnosticCode.VERSION)
        runtime, link = self.make()
        self.assert_fault(runtime, self.feed(runtime, link, packet), FaultCode.VERSION)

    def test_mixed_corruption_stays_decode_regardless_of_byte_order(self):
        version = hello_ack(major=1)
        crc = corrupt_crc(version)
        for data in (version + crc, crc + version):
            with self.subTest(data=data.hex()):
                batch = StreamDecoder().feed(data, 0)
                self.assertEqual({d.code for d in batch.diagnostics},
                                 {DiagnosticCode.CRC, DiagnosticCode.VERSION})
                runtime, link = self.make()
                self.assert_fault(runtime, self.feed(runtime, link, data), FaultCode.DECODE)
                self.assert_quiet(runtime, link, [1])

    def test_repeated_versions_and_valid_neighbor_frames_do_not_admit_handshake(self):
        version = hello_ack(minor=2)
        for data in (version * 2, hello_ack() + version, version + hello_ack()):
            with self.subTest(data=data.hex()):
                batch = self.assert_decoder(data, DiagnosticCode.VERSION)
                self.assertEqual(batch.diagnostics[0].count, 2 if data == version * 2 else 1)
                runtime, link = self.make()
                self.assert_fault(runtime, self.feed(runtime, link, data), FaultCode.VERSION)
                self.assertIsNone(runtime.snapshot().capabilities)
                self.assert_quiet(runtime, link, [1])

    def test_every_fragment_boundary_waits_for_delimiter_before_diagnosis(self):
        version = hello_ack(major=1)
        for packet, fault in ((version, FaultCode.VERSION),
                              (corrupt_crc(version), FaultCode.DECODE)):
            for split in range(1, len(packet)):
                with self.subTest(fault=fault, split=split):
                    runtime, link = self.make()
                    self.assertEqual(self.feed(runtime, link, packet[:split]), ())
                    self.assertIs(runtime.snapshot().phase, Phase.HELLO)
                    self.assertIsNone(runtime.pending_capture)
                    self.assertEqual(runtime.pump(), ())
                    self.assert_fault(runtime, self.feed(runtime, link, packet[split:]), fault)
                    self.assert_quiet(runtime, link, [1])

    def test_partial_version_frame_timeout_remains_decode_without_admission(self):
        runtime, link = self.ready()
        self.assertEqual(self.feed(runtime, link, hello_ack(major=1)[:-1]), ())
        link.now = 999
        self.assertEqual(runtime.pump(), ())
        link.now = 1000
        self.assert_fault(runtime, runtime.pump(), FaultCode.DECODE)
        self.assertIsNone(runtime.pending_capture)
        self.assert_quiet(runtime, link, [1, 2])

    def test_controller_deadline_still_precedes_unread_version_frame(self):
        runtime, link = self.make()
        runtime.pump()
        link.input.append(hello_ack(major=1))
        before = list(link.calls)
        link.now = 1000
        events = runtime.pump()
        self.assertEqual(len(events), 1)
        self.assertIs(events[0].reason, RecoveryReason.TIMEOUT)
        self.assertIsNone(events[0].fault)
        self.assertIs(runtime.snapshot().phase, Phase.RECOVERY)
        self.assertEqual(link.calls, before)
        self.assert_quiet(runtime, link, [1])

    def prefix(self, runtime, link, blocks):
        packets = [ref.packet(144, sequence=5, request=0, payload=ref.begin())]
        packets += [ref.packet(145, sequence=6 + index, request=0,
                    payload=ref.data(offset=250 * index)) for index in range(blocks)]
        link.input.append(b"".join(packets))
        while link.input:
            runtime.pump()
            self.assertIs(runtime.snapshot().phase, Phase.ARMED)
        self.assertIsNone(runtime.pending_capture)

    def test_version_fault_retains_exact_partial_samples_without_release_or_restart(self):
        runtime, link = self.ready(armed=True)
        self.prefix(runtime, link, 1)
        bad_data = ref.packet(145, sequence=7, request=0, payload=ref.data(offset=250), major=1)
        self.assert_fault(runtime, self.feed(runtime, link, bad_data), FaultCode.VERSION)
        result = runtime.incomplete_result
        self.assertIsNotNone(result)
        self.assertFalse(result.complete)
        self.assertIs(result.reason, WindowCode.DISCONNECT)
        self.assertIsNone(result.samples)
        self.assertEqual(result.missing_ranges, ((250, 2048),))
        actual = b"".join(ref.uint(sample, 2) for block in result.fragments for sample in block.samples)
        self.assertEqual(actual, ref.samples()[:500])
        self.assertIsNone(runtime.pending_capture)
        self.assert_quiet(runtime, link, [1, 2, 3, 4])
        with self.assertRaises(RuntimeError):
            runtime.start(ref.NONCE + 1, reset_confirmed=True)
        self.assertIs(runtime.take_incomplete(), result)
        with self.assertRaises(RuntimeError):
            runtime.start(ref.NONCE + 1)
        runtime.start(ref.NONCE + 1, reset_confirmed=True)
        self.assertIs(runtime.snapshot().phase, Phase.HELLO)
        self.assertIsNone(runtime.pending_capture)

    def test_valid_end_in_faulted_batch_never_promotes_capture(self):
        final = ref.packet(145, sequence=14, request=0, payload=ref.data(offset=2000, count=48))
        final += ref.packet(146, sequence=15, request=0, payload=ref.end())
        version = hello_ack(major=1)
        for data in (final + version, version + final):
            with self.subTest(version_first=data.startswith(version)):
                runtime, link = self.ready(armed=True)
                self.prefix(runtime, link, 8)
                self.assert_fault(runtime, self.feed(runtime, link, data), FaultCode.VERSION)
                result = runtime.incomplete_result
                self.assertFalse(result.complete)
                self.assertEqual(result.missing_ranges, ((2000, 2048),))
                self.assertIsNone(runtime.pending_capture)
                self.assert_quiet(runtime, link, [1, 2, 3, 4])

    def test_already_complete_capture_is_retained_without_release_on_version_fault(self):
        runtime, link = self.ready(armed=True)
        self.prefix(runtime, link, 8)
        final = ref.packet(145, sequence=14, request=0, payload=ref.data(offset=2000, count=48))
        final += ref.packet(146, sequence=15, request=0, payload=ref.end())
        self.feed(runtime, link, final)
        capture = runtime.pending_capture
        self.assertIsNotNone(capture)
        self.assert_fault(runtime, self.feed(runtime, link, hello_ack(major=1)), FaultCode.VERSION)
        self.assertIs(runtime.pending_capture, capture)
        self.assertIsNone(runtime.incomplete_result)
        self.assert_quiet(runtime, link, [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()

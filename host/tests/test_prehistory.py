"""Synthetic byte-peer prehistory checks; no physical timing/USB claim."""
import struct
import unittest
import zlib

from motortester.device_emulator import (
    DeviceEmulator, DeviceError, ErrorCode, PREPARING, WAITING, STOPPED,
    WAIT_RELEASE, ERROR, HOLD_TIMEOUT_MS,
)
from motortester.session import Config, Phase, SessionController, SessionError
from motortester.wire import Frame, StreamDecoder, encode_frame
from test_acquisition_modes import AnalogPeer


class PrehistoryPeer(AnalogPeer):
    def __init__(self, pre=512, mode=1, edge=0, level=2048, *, enabled=True, arm=True):
        self.c = SessionController()
        self.d = DeviceEmulator(all_analog_modes=True, pretrigger=enabled)
        self.decoder = StreamDecoder()
        self.now = 0
        self.frames, self.events, self.requests, self.results = [], [], [], []
        self.receiver = self.context = None
        self.c.start(101, self.now)
        self.pump()
        self.c.configure(Config(mode=mode, edge=edge, level_code=level,
                                pretrigger_samples=pre,
                                auto_timeout_ms=100 if mode == 0 else 0), self.now)
        self.pump()
        if arm:
            self.c.arm(self.now)
            self.pump()

    def ready(self):
        self.now += (self.c.snapshot().config.pretrigger_samples + 49) // 50
        self.d.tick(self.now)
        self.drain()


class PrehistoryTests(unittest.TestCase):
    def test_explicit_capability_opt_in_and_exact_boolean(self):
        for flag in (0, 1, None, "yes", []):
            with self.assertRaises(DeviceError) as caught:
                DeviceEmulator(pretrigger=flag)
            self.assertIs(caught.exception.code, ErrorCode.ARGUMENT)
        for enabled, expected in ((False, (0,)), (True, (0, 512, 1024))):
            h = PrehistoryPeer(pre=0, enabled=enabled, arm=False)
            caps = h.c.snapshot().capabilities
            self.assertEqual(caps.pretrigger_samples, expected)
            self.assertEqual(caps.capability_bits, 0x3D if enabled else 0x1D)
            h.d.disconnect(0)
            wire = encode_frame(Frame(1, 0, 1, 1, struct.pack("<Q", 202)))
            h.d.write(wire, 0)
            hello = StreamDecoder().feed(h.d.read(4096, 0), 0).frames[0]
            self.assertEqual(struct.unpack_from("<I", hello.payload, 12)[0], caps.capability_bits)

    def test_unsupported_and_invalid_config_leave_prior_settings_intact(self):
        h = PrehistoryPeer(pre=0, enabled=False, arm=False)
        old = h.c.snapshot()
        with self.assertRaises(SessionError):
            h.c.configure(Config(pretrigger_samples=512), 0)
        self.assertEqual(h.c.snapshot(), old)
        for enabled in (False, True):
            h = PrehistoryPeer(pre=0, enabled=enabled, arm=False)
            invalid = (1, 511, 513, 1023, 1025, 2048, 65535)
            if not enabled:
                invalid += (512, 1024)
            for pre in invalid:
                old = h.d.snapshot()
                payload = struct.pack("<HBBBBHHHI", 1, 1, 0, 0, 0, 2048, pre, 0, 0)
                replies = h.raw(3, payload)
                self.assertEqual([(f.message_type, f.payload[1]) for f in replies], [(255, 1)])
                new = h.d.snapshot()
                self.assertEqual((new.config_id, new.config, new.state),
                                 (old.config_id, old.config, old.state))

    def test_all_modes_edges_levels_and_pretrigger_positions(self):
        for pre in (0, 512, 1024):
            for mode in (0, 1, 2):
                for edge in (0, 1):
                    for level in (1, 4094):
                        with self.subTest(pre=pre, mode=mode, edge=edge, level=level):
                            h = PrehistoryPeer(pre, mode, edge, level)
                            h.ready()
                            receipt = h.window()
                            result = h.results[-1]
                            b = result.begin
                            self.assertEqual((b.pretrigger_samples, b.trigger_index, b.trigger_kind,
                                              b.quality_flags, b.external_edge_id), (pre, pre, 0, 2, 0))
                            self.assertEqual((b.timer_clock_hz, b.timer_psc, b.timer_arr),
                                             (72000000, 71, 19))
                            self.assertEqual(len(result.samples), 2048)
                            self.assertEqual(result.samples[pre], level - edge)
                            if pre:
                                self.assertTrue(all(v == level - (1 - edge)
                                                    for v in result.samples[:pre]))
                            self.assertIn(0, result.samples)
                            self.assertIn(4095, result.samples)
                            raw = b"".join(f.payload[12:] for f in h.frames if f.message_type == 145)
                            self.assertEqual(struct.unpack("<2048H", raw), result.samples)
                            self.assertEqual(zlib.crc32(raw), receipt.samples_crc32)
                            self.assertEqual(h.d.snapshot().state, WAIT_RELEASE)
                            h.release(receipt)
                            self.assertEqual(h.d.snapshot().state,
                                             STOPPED if mode == 2 else PREPARING if pre else WAITING)

    def test_preparing_rejects_early_edge_and_auto_starts_after_readiness(self):
        for pre, ready in ((512, 11), (1024, 21)):
            for mode in (0, 1, 2):
                h = PrehistoryPeer(pre, mode)
                self.assertEqual(h.d.snapshot().state, PREPARING)
                self.assertFalse(h.d.trigger(ready - 1))
                self.assertEqual(h.d.snapshot().window_id, 0)
                h.now = ready
                h.d.tick(h.now)
                h.drain()
                self.assertEqual(h.d.snapshot().state, WAITING)
                self.assertEqual(h.results, [])
                h.now = ready + 99
                h.d.tick(h.now)
                h.drain()
                self.assertEqual(h.results, [])
                if mode == 0:
                    h.now += 1
                    self.assertFalse(h.d.trigger(h.now))
                    h.drain()
                    b = h.results[-1].begin
                    self.assertEqual((b.pretrigger_samples, b.trigger_kind, b.trigger_index,
                                      b.quality_flags), (pre, 2, 0xFFFF, 3))
                else:
                    self.assertTrue(h.d.trigger(h.now))
                    h.drain()
                    self.assertEqual(h.results[-1].begin.trigger_index, pre)

    def test_late_clock_poll_does_not_shift_timeout_or_allocate_backlog(self):
        h = PrehistoryPeer(1024, 0)
        h.now = 100000
        h.d.tick(h.now)
        h.drain()
        self.assertEqual(len(h.results), 1)
        self.assertEqual(h.results[0].begin.trigger_kind, 2)
        self.assertEqual(h.d.snapshot().window_id, 1)
        self.assertEqual(h.d.snapshot().state, WAIT_RELEASE)

    def test_stop_during_preparing_cancels_future_capture_without_abort(self):
        for pre in (512, 1024):
            h = PrehistoryPeer(pre, 0)
            h.now = 1
            h.c.stop(h.now)
            h.pump()
            self.assertIs(h.c.snapshot().phase, Phase.READY)
            self.assertEqual(h.d.snapshot().state, STOPPED)
            self.assertFalse(h.d.snapshot().held_samples)
            h.now = 100000
            h.d.tick(h.now)
            self.assertFalse(h.d.trigger(h.now))
            h.drain()
            self.assertEqual(h.results, [])
            self.assertFalse(any(f.message_type in (144, 145, 146, 147) for f in h.frames))

    def test_release_ack_restarts_preparation_only_after_last_byte(self):
        h = PrehistoryPeer(512, 0)
        h.ready()
        receipt = h.window()
        h.c.release(receipt, h.now)
        h.write()
        first = h.d.read(1, h.now)
        rest = h.d.read(h.d.snapshot().tx_size - 2, h.now)
        h.now += 1000
        self.assertFalse(h.d.trigger(h.now))
        self.assertEqual(h.d.snapshot().state, WAIT_RELEASE)
        last = h.d.read(1, h.now)
        self.assertEqual(StreamDecoder().feed(first + rest + last, h.now).frames[0].message_type, 135)
        self.assertEqual(h.d.snapshot().state, PREPARING)
        self.assertFalse(h.d.trigger(h.now + 10))
        self.assertTrue(h.d.trigger(h.now + 11))

    def test_stop_during_nonzero_data_keeps_partial_and_one_abort(self):
        h = PrehistoryPeer(512)
        h.ready()
        self.assertTrue(h.d.trigger(h.now))
        self.assertEqual(h.read_one().message_type, 134)
        self.assertEqual(h.read_one().message_type, 144)
        self.assertEqual(h.read_one().message_type, 145)
        h.c.stop(h.now)
        h.pump()
        self.assertEqual([f.message_type for f in h.frames if f.message_type in (146, 147)], [147])
        self.assertEqual(h.d.snapshot().state, STOPPED)
        self.assertFalse(h.d.snapshot().held_samples)
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        self.assertEqual(len([f for f in h.frames if f.message_type == 145]), 1)
        self.assertEqual(h.d.snapshot().sample_buffer_bytes, 4096)

    def test_stop_at_prepared_auto_deadline_cancels_the_allocated_window(self):
        h = PrehistoryPeer(1024, 0)
        h.now = 121
        h.c.stop(h.now)
        h.pump()
        self.assertEqual([f.message_type for f in h.frames if f.message_type in (146, 147)], [147])
        self.assertEqual(h.d.snapshot().window_id, 1)
        self.assertEqual(h.d.snapshot().state, STOPPED)
        self.assertFalse(h.d.snapshot().held_samples)
        self.assertIs(h.c.snapshot().phase, Phase.READY)

    def test_timeout_and_stop_preserve_one_terminal_and_held_window(self):
        h = PrehistoryPeer(1024)
        h.ready()
        h.window()
        terminal = h.d.snapshot().terminal
        h.now += HOLD_TIMEOUT_MS
        h.d.tick(h.now)
        self.assertEqual(h.d.snapshot().state, ERROR)
        self.assertTrue(h.d.snapshot().held_samples)
        self.assertEqual(h.d.snapshot().terminal, terminal)
        replies = h.raw(5, struct.pack("<I", terminal.generation))
        self.assertEqual([f.message_type for f in replies], [133, 134])
        self.assertEqual(h.d.snapshot().terminal, terminal)
        self.assertEqual(h.d.snapshot().state, STOPPED)


if __name__ == "__main__":
    unittest.main()

"""Авторские проверки ограниченных аналоговых режимов; только синтетика."""
from dataclasses import replace
import struct
import unittest
import zlib

from motortester.device_emulator import (
    DeviceEmulator, DeviceError, ErrorCode as DeviceCode, ERROR, MAX_TIME_MS,
    STOPPED, TRANSFERRING, WAITING, WAIT_RELEASE,
)
from motortester.session import (
    Config, ErrorCode, EventKind as K, Phase, SessionController, SessionError,
    WindowReceipt,
)
from motortester.window import ExpectedContext, WindowCode, WindowReceiver
from motortester.wire import Frame, StreamDecoder, encode_frame


class AnalogPeer:
    def __init__(self, mode=0, edge=0, timeout=100, *, enabled=True, arm=True):
        self.c = SessionController()
        self.d = DeviceEmulator(all_analog_modes=enabled)
        self.decoder = StreamDecoder()
        self.now = 0
        self.frames, self.events, self.requests, self.results = [], [], [], []
        self.receiver = self.context = None
        self.c.start(101, self.now)
        self.pump()
        self.c.configure(Config(mode=mode, edge=edge,
                                auto_timeout_ms=timeout if mode == 0 else 0), self.now)
        self.pump()
        if arm:
            self.c.arm(self.now)
            self.pump()

    def write(self, count=None):
        ticket = self.c.prepare_tx()
        if ticket is None:
            raise AssertionError("Ожидалась команда")
        data = ticket.encoded[ticket.offset:]
        if count is not None:
            data = data[:count]
        result = self.d.write(data, self.now)
        if result.accepted_prefix != len(data) or result.diagnostics:
            raise AssertionError("Неожиданная ошибка записи")
        if ticket.offset == 0 and result.accepted_prefix:
            self.requests.append(ticket.frame)
        events = self.c.written(ticket, result.accepted_prefix, self.now)
        if events:
            raise AssertionError(events)
        return ticket

    def read_byte(self):
        data = self.d.read(1, self.now)
        result = self.decoder.feed(data, self.now)
        if result.diagnostics:
            raise AssertionError(result.diagnostics)
        for frame in result.frames:
            self.frames.append(frame)
            events = self.c.receive(frame, self.now)
            self.events.extend(events)
            if any(event.kind is K.RECOVERY for event in events):
                raise AssertionError(events)
            for event in events:
                if event.kind is K.WINDOW_BEGIN:
                    s = self.c.snapshot()
                    self.context = ExpectedContext(
                        s.session, s.generation, s.config_id, s.config.mode,
                        s.config.source, s.config.edge, s.config.level_code,
                        s.config.pretrigger_samples, s.config.profile)
                    self.receiver = WindowReceiver(self.context)
                if event.kind in (K.WINDOW_BEGIN, K.WINDOW_DATA, K.WINDOW_END_ADMITTED,
                                   K.WINDOW_ABORT) and self.receiver is not None:
                    outcome = self.receiver.feed(event.frame, self.now)
                    if outcome is not None:
                        self.results.append(outcome)
        return data, result.frames

    def read_one(self):
        while True:
            data, frames = self.read_byte()
            if frames:
                return frames[0]
            if not data:
                return None

    def drain(self):
        while self.read_one() is not None:
            pass

    def pump(self):
        while self.c.prepare_tx() is not None:
            self.write()
            self.drain()

    def window(self, *, timeout=False):
        if timeout:
            self.now += self.c.snapshot().config.auto_timeout_ms
            self.d.tick(self.now)
        elif not self.d.trigger(self.now):
            raise AssertionError("Запуск отклонён")
        self.drain()
        return WindowReceipt.from_window(self.context, self.results[-1])

    def release(self, receipt):
        self.c.release(receipt, self.now)
        self.pump()

    def raw(self, kind, payload):
        s = self.d.snapshot()
        data = encode_frame(Frame(kind, s.session, s.next_rx_sequence,
                                  s.last_request_id + 1, payload))
        result = self.d.write(data, self.now)
        if result.accepted_prefix != len(data):
            raise AssertionError("Неожиданный backpressure")
        decoder, frames = StreamDecoder(), []
        while True:
            data = self.d.read(4096, self.now)
            if not data:
                return frames
            decoded = decoder.feed(data, self.now)
            if decoded.diagnostics:
                raise AssertionError(decoded.diagnostics)
            frames.extend(decoded.frames)


def expected_samples(generation, window, edge, timeout):
    samples = [(73 * i + 19 * window + 7 * generation) & 4095 for i in range(2048)]
    if not timeout:
        samples[0] = 2048 - edge
    samples[511], samples[1023] = 0, 4095
    return tuple(samples)


class AcquisitionModeTests(unittest.TestCase):
    def reject(self, code, function, *args, **kwargs):
        with self.assertRaises(SessionError) as caught:
            function(*args, **kwargs)
        self.assertIs(caught.exception.code, code)

    def test_constructor_exact_bool_keyword_only(self):
        class Integer(int):
            pass
        for flag in (0, 1, None, "yes", [], Integer(1)):
            with self.subTest(flag=flag):
                with self.assertRaises(DeviceError) as caught:
                    DeviceEmulator(all_analog_modes=flag)
                self.assertIs(caught.exception.code, DeviceCode.ARGUMENT)
        with self.assertRaises(TypeError):
            DeviceEmulator(True)

    def test_masks_default_opt_in_and_reset_policy(self):
        for enabled, bits, modes in ((False, 9, 2), (True, 0x1D, 7)):
            h = AnalogPeer(mode=1, enabled=enabled, arm=False)
            caps = h.c.snapshot().capabilities
            self.assertEqual((caps.capability_bits, caps.mode_bits, caps.source_bits,
                              caps.edge_bits, caps.pretrigger_samples),
                             (bits, modes, 1, 3, (0,)))
            self.assertEqual(h.c.snapshot().validation_scope, "draft_analog_control")
            h.d.disconnect(0)
            raw = encode_frame(Frame(1, 0, 1, 1, struct.pack("<Q", 202)))
            self.assertEqual(h.d.write(raw, 0).accepted_prefix, len(raw))
            frames = StreamDecoder().feed(h.d.read(4096, 0), 0).frames
            self.assertEqual(struct.unpack_from("<I", frames[0].payload, 12)[0], bits)

    def test_six_mode_edge_combinations_real_window(self):
        for mode in range(3):
            for edge in range(2):
                with self.subTest(mode=mode, edge=edge):
                    h = AnalogPeer(mode, edge)
                    receipt = h.window()
                    result = h.results[-1]
                    self.assertEqual(result.samples,
                                     expected_samples(receipt.generation, 1, edge, False))
                    self.assertEqual((result.begin.trigger_kind, result.begin.trigger_index,
                                      result.begin.external_edge_id, result.begin.quality_flags),
                                     (0, 0, 0, 2))
                    self.assertEqual(h.d.snapshot().state, WAIT_RELEASE)
                    self.assertTrue(h.d.snapshot().held_samples)
                    self.assertIs(h.c.snapshot().phase, Phase.ARMED)
                    h.release(receipt)
                    self.assertEqual(h.d.snapshot().state, STOPPED if mode == 2 else WAITING)
                    self.assertIs(h.c.snapshot().phase, Phase.READY if mode == 2 else Phase.ARMED)

    def test_default_peer_gates_new_modes_before_state_or_time_changes(self):
        h = AnalogPeer(mode=1, enabled=False, arm=False)
        for cfg in (Config(mode=0, auto_timeout_ms=100), Config(mode=2)):
            before = h.c.snapshot()
            self.reject(ErrorCode.UNSUPPORTED, h.c.configure, cfg, 60000)
            self.assertEqual(h.c.snapshot(), before)

    def test_config_timeout_endpoints_strict_types_and_unchanged_state(self):
        for timeout in (100, 60000):
            h = AnalogPeer(timeout=timeout, arm=False)
            self.assertEqual(h.c.snapshot().config.auto_timeout_ms, timeout)
            self.assertEqual(h.d.snapshot().config, h.c.snapshot().config.payload())
        for mode, timeout in ((0, 0), (0, 99), (0, 60001), (1, 100), (2, 100)):
            self.reject(ErrorCode.RANGE, Config, mode=mode, auto_timeout_ms=timeout)
        h = AnalogPeer(mode=1, arm=False)
        for field, value in (("mode", True), ("mode", 0.0), ("auto_timeout_ms", True),
                             ("auto_timeout_ms", 100.0), ("auto_timeout_ms", "100")):
            cfg = Config(mode=0, auto_timeout_ms=100)
            object.__setattr__(cfg, field, value)
            before = h.c.snapshot()
            self.reject(ErrorCode.ARGUMENT, h.c.configure, cfg, 60000)
            self.assertEqual(h.c.snapshot(), before)

    def test_wire_bad_config_preserves_settings_and_id(self):
        for mode, timeout, pre, source in ((0, 99, 0, 0), (0, 60001, 0, 0),
                                         (1, 100, 0, 0), (2, 1, 0, 0),
                                         (3, 0, 0, 0), (0, 100, 1, 0), (2, 0, 0, 1)):
            h = AnalogPeer(mode=1, arm=False)
            before = h.d.snapshot()
            payload = struct.pack("<HBBBBHHHI", 1, mode, source, 0, 0, 2048, pre, 0, timeout)
            frames = h.raw(3, payload)
            self.assertEqual([(f.message_type, f.payload[1]) for f in frames], [(255, 1)])
            after = h.d.snapshot()
            self.assertEqual((after.config_id, after.config, after.state),
                             (before.config_id, before.config, before.state))

    def test_auto_exact_100_and_60000_deadlines_not_transfer_timeouts(self):
        for timeout in (100, 60000):
            h = AnalogPeer(timeout=timeout)
            self.assertEqual(h.c.tick(timeout - 1), ())
            self.assertEqual(h.d.tick(timeout - 1), ())
            self.assertEqual(h.d.snapshot().window_id, 0)
            self.assertIsNone(h.receiver)
            h.now = timeout
            self.assertEqual(h.c.tick(h.now), ())
            h.d.tick(h.now)
            h.drain()
            b = h.results[-1].begin
            self.assertEqual((b.trigger_kind, b.trigger_index, b.external_edge_id,
                              b.quality_flags), (2, 0xFFFF, 0, 3))
            self.assertTrue(h.results[-1].complete)
            self.assertIs(h.c.snapshot().phase, Phase.ARMED)

    def test_auto_timer_begins_on_arm_execution_before_arm_ack(self):
        h = AnalogPeer(arm=False)
        h.c.arm(0)
        h.write()
        self.assertEqual(h.d.snapshot().state, WAITING)
        h.read_byte()
        self.assertIsNotNone(h.d.snapshot().tx_offset)
        h.d.tick(99)
        self.assertEqual(h.d.snapshot().window_id, 0)
        h.now = 100
        h.d.tick(100)
        self.assertEqual(h.d.snapshot().window_id, 1)
        h.drain()
        self.assertEqual(h.results[-1].begin.trigger_kind, 2)

    def test_normal_and_single_wait_without_deadline(self):
        for mode in (1, 2):
            h = AnalogPeer(mode)
            h.now = MAX_TIME_MS
            self.assertEqual(h.c.tick(h.now), ())
            self.assertEqual(h.d.tick(h.now), ())
            self.assertEqual((h.d.snapshot().state, h.d.snapshot().window_id), (WAITING, 0))
            receipt = h.window()
            h.release(receipt)
            self.assertIsNotNone(receipt)

    def test_ten_auto_real_timeout_windows_share_one_arm_and_exact_samples_crc(self):
        h = AnalogPeer(edge=1)
        generation = h.c.snapshot().generation
        for window in range(1, 11):
            timeout = window % 2 == 0
            receipt = h.window(timeout=timeout)
            expected = expected_samples(generation, window, 1, timeout)
            self.assertEqual(h.results[-1].samples, expected)
            self.assertEqual(receipt.samples_crc32, zlib.crc32(struct.pack("<2048H", *expected)))
            self.assertEqual(h.results[-1].begin.trigger_kind, 2 if timeout else 0)
            self.assertEqual(h.results[-1].begin.trigger_index, 0xFFFF if timeout else 0)
            h.release(receipt)
            self.assertEqual(h.c.snapshot().generation, generation)
        self.assertEqual([f.message_type for f in h.requests].count(4), 1)
        self.assertEqual([e.kind for e in h.events].count(K.WINDOW_RELEASED), 10)

    def test_ten_single_shots_require_explicit_new_generation(self):
        h = AnalogPeer(mode=2)
        generations = []
        for window in range(1, 11):
            if window > 1:
                h.c.arm(h.now)
                h.pump()
            receipt = h.window()
            generations.append(receipt.generation)
            expected = expected_samples(receipt.generation, window, 0, False)
            self.assertEqual(h.results[-1].samples, expected)
            self.assertEqual(receipt.samples_crc32, zlib.crc32(struct.pack("<2048H", *expected)))
            self.assertTrue(h.c.snapshot().window_barrier)
            self.assertFalse(h.d.trigger(h.now))
            h.release(receipt)
            self.assertEqual(h.d.snapshot().state, STOPPED)
            self.assertFalse(h.d.trigger(h.now))
        self.assertEqual(len(set(generations)), 10)
        self.assertEqual([f.message_type for f in h.requests].count(4), 10)
        self.assertEqual([e.kind for e in h.events].count(K.WINDOW_RELEASED), 10)

    def test_release_final_byte_starts_fresh_auto_deadline(self):
        h = AnalogPeer()
        receipt = h.window(timeout=True)
        h.c.release(receipt, h.now)
        h.write()
        h.read_byte()
        self.assertEqual(h.d.snapshot().state, WAIT_RELEASE)
        h.now += 250
        h.d.tick(h.now)
        self.assertEqual(h.d.snapshot().window_id, 1)
        while h.c.snapshot().pending_request_id:
            h.read_byte()
        self.assertEqual(h.d.snapshot().state, WAITING)
        h.d.tick(h.now + 99)
        self.assertEqual(h.d.snapshot().window_id, 1)
        h.now += 100
        h.d.tick(h.now)
        h.drain()
        self.assertEqual(h.results[-1].begin.window_id, 2)
        self.assertEqual(h.results[-1].begin.trigger_kind, 2)

    def test_single_release_final_byte_and_pending_stop_barrier(self):
        h = AnalogPeer(mode=2)
        receipt = h.window()
        h.c.release(receipt, 0)
        h.write()
        h.read_byte()
        self.assertIs(h.c.snapshot().phase, Phase.ARMED)
        self.assertEqual(h.d.snapshot().state, WAIT_RELEASE)
        h.c.stop(0)
        h.write()
        while h.c.snapshot().pending_request_id:
            h.read_byte()
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        self.assertTrue(h.c.snapshot().stop_barrier)
        self.reject(ErrorCode.STATE, h.c.arm, 0)
        self.reject(ErrorCode.STATE, h.c.configure, Config(mode=2), 0)
        h.drain()
        self.assertIs(h.c.snapshot().phase, Phase.READY)
        self.assertEqual(h.d.snapshot().state, STOPPED)
        self.assertFalse(h.d.snapshot().continuation)

    def test_exact_tie_expiry_precedes_trigger_and_stop_input(self):
        h = AnalogPeer()
        h.now = 100
        self.assertFalse(h.d.trigger(h.now))
        h.drain()
        self.assertEqual(h.results[-1].begin.trigger_kind, 2)
        h = AnalogPeer()
        h.now = 100
        h.c.stop(h.now)
        h.pump()
        terminals = [f.message_type for f in h.frames if f.message_type in (146, 147)]
        self.assertEqual(terminals, [147])
        self.assertEqual(h.d.snapshot().state, STOPPED)
        self.assertEqual(h.d.snapshot().window_id, 1)

    def test_large_clock_jump_has_one_window_and_no_catchup_queue(self):
        h = AnalogPeer()
        h.now = MAX_TIME_MS - 101
        h.d.tick(h.now)
        self.assertEqual(h.d.snapshot().window_id, 1)
        h.drain()
        receipt = WindowReceipt.from_window(h.context, h.results[-1])
        self.assertEqual(len(h.results), 1)
        self.assertLessEqual(h.d.snapshot().control_descriptors, 8)
        self.assertLessEqual(h.d.snapshot().tx_size, 544)
        h.release(receipt)
        h.d.tick(h.now + 99)
        self.assertEqual(h.d.snapshot().window_id, 1)
        h.now += 100
        h.d.tick(h.now)
        h.drain()
        self.assertEqual(len(h.results), 2)
        self.assertEqual(h.d.snapshot().window_id, 2)
        self.assertFalse(hasattr(h.d, "__dict__"))

    def test_factory_rechecks_new_mode_metadata_and_payload(self):
        for mode, timeout in ((0, False), (0, True), (2, False)):
            h = AnalogPeer(mode)
            h.window(timeout=timeout)
            result = h.results[-1]
            bad_begin = (
                replace(result.begin, trigger_index=0 if timeout else 0xFFFF),
                replace(result.begin, quality_flags=result.begin.quality_flags ^ 1),
                replace(result.begin, external_edge_id=1),
                replace(result.begin, quality_flags=8),
            )
            for begin in bad_begin:
                self.reject(ErrorCode.STATE, WindowReceipt.from_window, h.context,
                            replace(result, begin=begin))
            for bad in (replace(result, complete=False),
                        replace(result, samples_crc32=result.samples_crc32 ^ 1),
                        replace(result, missing_ranges=((0, 1),)),
                        replace(result, metadata_issues=("RAIL_FLAG",)),
                        replace(result, fragments=()), replace(result, rail_observed=False)):
                self.reject(ErrorCode.STATE, WindowReceipt.from_window, h.context, bad)
            self.reject(ErrorCode.UNSUPPORTED, WindowReceipt.from_window, h.context,
                        replace(result, deferred_checks=("pending",)))
            self.reject(ErrorCode.ARGUMENT, WindowReceipt.from_window, h.context,
                        replace(result, complete=1))

    def test_controller_rejects_false_timeout_or_index_in_new_modes(self):
        for mode, kind, index, quality in ((2, 2, 0xFFFF, 3), (1, 2, 0xFFFF, 3),
                                         (0, 2, 0, 3), (0, 0, 0, 3), (0, 2, 0xFFFF, 2)):
            h = AnalogPeer(mode)
            s = h.c.snapshot()
            payload = struct.pack("<IIIHHHHBBBBHHIHHI", s.generation, 1, s.config_id,
                                  1, 2048, 0, index, kind, 0, 0, 0, 2048, quality,
                                  72000000, 71, 19, 0)
            events = h.c.receive(Frame(144, s.session, s.next_rx_sequence, 0, payload), 0)
            self.assertEqual([e.kind for e in events], [K.RECOVERY])

    def test_late_release_after_stop_never_rearms_either_new_mode(self):
        for mode in (0, 2):
            h = AnalogPeer(mode)
            receipt = h.window(timeout=mode == 0)
            h.c.stop(h.now)
            h.pump()
            h.release(receipt)
            self.assertEqual(h.d.snapshot().state, STOPPED)
            self.assertIs(h.c.snapshot().phase, Phase.READY)
            self.assertFalse(h.d.snapshot().continuation)
            h.d.tick(h.now + 100000)
            self.assertEqual(h.d.snapshot().window_id, 1)
            self.assertFalse(h.d.trigger(h.now + 100000))

    def test_release_command_deadline_is_independent_of_auto_wait(self):
        h = AnalogPeer(timeout=60000)
        receipt = h.window(timeout=True)
        h.c.release(receipt, h.now)
        h.write()
        self.assertEqual(h.c.tick(h.now + 999), ())
        events = h.c.tick(h.now + 1000)
        self.assertEqual([e.kind for e in events], [K.RECOVERY])
        self.assertIs(h.c.snapshot().phase, Phase.RECOVERY)

    def test_provisional_hold_deadline_still_separate_from_acquisition_timer(self):
        for mode in (0, 2):
            h = AnalogPeer(mode)
            h.window(timeout=mode == 0)
            h.d.tick(h.now + 4999)
            self.assertEqual(h.d.snapshot().state, WAIT_RELEASE)
            h.d.tick(h.now + 5000)
            self.assertEqual(h.d.snapshot().state, ERROR)
            self.assertFalse(h.d.snapshot().continuation)
            self.assertEqual(h.d.snapshot().terminal.outcome, "END")
            self.assertTrue(h.d.snapshot().provenance.timeout_provisional)


if __name__ == "__main__":
    unittest.main()

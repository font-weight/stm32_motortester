"""Независимая HOST-006 conformance, normal/analog synthetic control.

Oracle: непринятый общий wire b6ae8025 и issue24, не код автора SessionController.
Нет оценки Critic-B, сборки окна или аппаратного подтверждения.
"""
from dataclasses import FrozenInstanceError, fields, replace
import gc
import importlib.util
import json
from pathlib import Path
import tracemalloc
import unittest

from motortester.wire import Frame, decode_frame
from motortester.session import Config, SessionController, SessionError, FaultCode

FIXTURES = Path(__file__).parent / "fixtures" / "session"
_spec = importlib.util.spec_from_file_location("session_independent_reference", FIXTURES / "reference.py")
ref = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ref)
TRANSCRIPT = json.loads((FIXTURES / "transcript.json").read_text(encoding="utf-8"))
NONCE, MAX_ID, MAX_TIME = ref.NONCE, ref.MAX_ID, 2**64 - 1


def names(events):
    return tuple(event.kind.name for event in events)


def inbound(kind, payload, seq=1, request=1, nonce=NONCE):
    return decode_frame(ref.packet(kind, nonce, seq, request, payload)[:-1])


class SessionChecks(unittest.TestCase):
    def session(self):
        return SessionController()

    def send_all(self, controller, now=0):
        ticket = controller.prepare_tx()
        self.assertIsNotNone(ticket)
        self.assertEqual(controller.written(ticket, len(ticket.encoded) - ticket.offset, now), ())
        return ticket

    def rx(self, controller, kind, payload, request=0, seq=None, nonce=NONCE, now=0):
        if seq is None:
            seq = controller.snapshot().next_rx_sequence
        return controller.receive(inbound(kind, payload, seq, request, nonce), now)

    def negotiating(self, bits=9):
        controller = self.session()
        self.assertEqual(controller.start(NONCE, 0), ())
        self.send_all(controller)
        result = self.rx(controller, 129, ref.hello_ack(bits), 1, 1)
        self.assertEqual(names(result), ("HELLO_ACCEPTED",))
        self.send_all(controller)
        return controller

    def ready(self, pre=False):
        controller = self.negotiating(41 if pre else 9)
        self.assertEqual(names(self.rx(controller, 130, ref.caps(pre), 2)), ("CAPABILITIES",))
        return controller

    def configured(self, pre=False):
        controller = self.ready(pre)
        config = Config(pretrigger_samples=512 if pre else 0)
        self.assertEqual(controller.configure(config, 0), ())
        self.send_all(controller)
        payload = ref.u32(1) + ref.config(pre=512 if pre else 0)
        self.assertEqual(names(self.rx(controller, 131, payload, 3)), ("CONFIGURED",))
        self.assertEqual(controller.snapshot().config, config)
        return controller

    def armed(self, pre=False):
        controller = self.configured(pre)
        self.assertEqual(controller.arm(0), ())
        self.send_all(controller)
        self.assertEqual(names(self.rx(controller, 132, ref.u32(4) + ref.u32(1), 4)), ("ARMED",))
        self.assertEqual(controller.snapshot().generation, 4)
        return controller

    def recovery(self, controller, events, reason=None):
        self.assertIn("RECOVERY", names(events))
        action = next(event for event in events if event.kind.name == "RECOVERY")
        if reason is not None:
            self.assertEqual(action.reason.name, reason)
        self.assertTrue(action.preserve_fragments)
        self.assertTrue(action.reset_transport)
        self.assertEqual(controller.snapshot().phase.name, "RECOVERY")
        self.assertIsNone(controller.prepare_tx())
        self.assertFalse(controller.snapshot().pending_request_id)
        self.assertFalse(controller.snapshot().pending_stop_id)
        self.assertLessEqual(len(events), 2)

    def unchanged_error(self, controller, function, *arguments, code=None):
        ticket = controller.prepare_tx()
        before = controller.snapshot()
        with self.assertRaises(SessionError) as caught:
            function(*arguments)
        if code:
            self.assertEqual(caught.exception.code.name, code)
        self.assertEqual(controller.snapshot(), before)
        self.assertIs(controller.prepare_tx(), ticket)


class IndependentTranscript(SessionChecks):
    def test_literal_fixture_rederives_and_crc_is_standard(self):
        self.assertEqual(ref.crc32(b"123456789"), 0xCBF43926)
        self.assertEqual(TRANSCRIPT["provisional_contract_sha"], ref.PIN)
        self.assertEqual(TRANSCRIPT["vectors"], ref.vectors())
        # Критичные размеры и wire bytes заданы отдельно от генератора fixture.
        self.assertEqual(ref.config().hex(), "01000100000000080000000000000000")
        self.assertEqual((len(ref.hello_ack()), len(ref.caps()), len(ref.begin())), (16, 30, 40))

    def test_fixed_handshake_config_arm_wait_stop_transcript(self):
        controller = self.session()
        for row in TRANSCRIPT["vectors"]:
            with self.subTest(frame=row["name"]):
                if row["name"] == "hello":
                    events = controller.start(NONCE, 0)
                elif row["name"] == "configure":
                    events = controller.configure(Config(), 0)
                elif row["name"] == "arm":
                    events = controller.arm(0)
                elif row["name"] == "stop":
                    events = controller.stop(0)
                else:
                    events = ()
                binary = bytes.fromhex(row["packet_hex"])
                if row["direction"] == "tx":
                    ticket = controller.prepare_tx()
                    self.assertEqual(ticket.encoded, binary)
                    self.assertEqual(ticket.offset, 0)
                    self.assertEqual(controller.written(ticket, len(binary), 0), ())
                else:
                    events = controller.receive(decode_frame(binary[:-1]), 0)
                self.assertEqual(names(events), tuple(row["events"]))
                self.assertEqual(controller.snapshot().phase.name, row["phase_after"])
        self.assertFalse(controller.snapshot().stop_barrier)
        # Никакого ожидания последующего STATUS для нового ARM.
        self.assertEqual(controller.arm(1), ())
        self.assertEqual(controller.prepare_tx().frame.request_id, 6)

    def test_objects_are_immutable_and_carry_provisional_provenance(self):
        controller = self.session()
        controller.start(NONCE, 0)
        ticket, snapshot = controller.prepare_tx(), controller.snapshot()
        self.send_all(controller)
        events = self.rx(controller, 129, ref.hello_ack(), 1, 1)
        self.assertIs(type(events), tuple)
        for obj in (Config(), ticket, ticket.frame, snapshot, events[0]):
            self.assertFalse(hasattr(obj, "__dict__"))
            with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
                setattr(obj, fields(obj)[0].name, None)
        for obj in (snapshot, events[0]):
            self.assertEqual(obj.source_sha, ref.PIN)
            self.assertEqual(obj.validation_scope, "draft_analog_control")


class HandshakeAndConfiguration(SessionChecks):
    def test_hello_exact_header_nonce_and_no_arm_before_caps(self):
        controller = self.session()
        controller.start(NONCE, 0)
        ticket = controller.prepare_tx()
        self.assertEqual((ticket.frame.message_type, ticket.frame.session, ticket.frame.sequence,
                          ticket.frame.request_id, ticket.frame.payload), (1, 0, 1, 1, ref.u64(NONCE)))
        self.unchanged_error(controller, controller.arm, 0)
        self.send_all(controller)
        self.rx(controller, 129, ref.hello_ack(), 1, 1)
        self.unchanged_error(controller, controller.arm, 0)
        self.unchanged_error(controller, controller.configure, Config(), 0)

    def test_hello_ack_field_and_response_validation(self):
        mutations = [(0, 1), (1, 2), (2, 2), (3, 10), (4, 255), (6, 255), (12, 128)]
        for offset, value in mutations:
            with self.subTest(offset=offset):
                controller = self.session()
                controller.start(NONCE, 0)
                self.send_all(controller)
                payload = bytearray(ref.hello_ack())
                payload[offset] = value
                self.recovery(controller, self.rx(controller, 129, bytes(payload), 1, 1))
        for payload, request, seq in ((ref.hello_ack()[:-1], 1, 1), (ref.hello_ack() + b"\0", 1, 1),
                                      (ref.hello_ack(), 0, 1), (ref.hello_ack(), 2, 1),
                                      (ref.hello_ack(), 1, 2)):
            with self.subTest(length=len(payload), request=request, sequence=seq):
                controller = self.session()
                controller.start(NONCE, 0)
                self.send_all(controller)
                self.recovery(controller, self.rx(controller, 129, payload, request, seq))

    def test_caps_all_fixed_fields_masks_and_pretrigger_agree(self):
        mutations = [(0, 2), (1, 1), (2, 2), (4, 1), (8, 70), (10, 20),
                     (12, 1), (16, 142), (18, 1), (20, 0), (20, 3),
                     (21, 1), (21, 0x82), (22, 2), (22, 0x81),
                     (23, 4), (24, 1), (26, 1), (28, 1)]
        for offset, value in mutations:
            with self.subTest(offset=offset, value=value):
                controller = self.negotiating()
                payload = bytearray(ref.caps())
                payload[offset] = value
                self.recovery(controller, self.rx(controller, 130, bytes(payload), 2))
        for payload in (ref.caps()[:-1], ref.caps() + b"\0", ref.caps(pre=True)):
            controller = self.negotiating()
            self.recovery(controller, self.rx(controller, 130, payload, 2))
        controller = self.negotiating(41)
        self.recovery(controller, self.rx(controller, 130, ref.caps(), 2))
        # Положительная предыстория 512 и её точные маски.
        controller = self.configured(pre=True)
        self.assertEqual(controller.snapshot().config.pretrigger_samples, 512)

    def test_consistent_capabilities_outside_control_scope_are_not_protocol_errors(self):
        for bits, modes, sources, edges in ((5, 1, 1, 3), (10, 2, 2, 3),
                                            (0, 0, 0, 0), (9, 2, 1, 0)):
            with self.subTest(bits=bits):
                controller = self.negotiating(bits)
                events = self.rx(controller, 130, ref.caps(modes=modes, sources=sources, edges=edges), 2)
                self.assertEqual(names(events), ("CAPABILITIES",))
                self.unchanged_error(controller, controller.configure, Config(), 0, code="UNSUPPORTED")

    def test_config_ack_exact_echo_and_increasing_nonzero_id(self):
        bad = [ref.u32(0) + ref.config(), ref.u32(0xFFFFFFFF) + ref.config(),
               ref.u32(1) + ref.config(level=2049), (ref.u32(1) + ref.config())[:-1],
               ref.u32(1) + ref.config() + b"\0"]
        for payload in bad:
            with self.subTest(payload=payload.hex()):
                controller = self.ready()
                controller.configure(Config(), 0)
                self.send_all(controller)
                self.recovery(controller, self.rx(controller, 131, payload, 3))
        controller = self.configured()
        controller.configure(Config(level_code=1000), 0)
        self.send_all(controller)
        self.recovery(controller, self.rx(controller, 131, ref.u32(1) + ref.config(level=1000), 4))

    def test_config_rejection_preserves_previous_settings_then_valid_update(self):
        controller = self.configured()
        old = controller.snapshot().config
        controller.configure(Config(level_code=1000), 0)
        self.send_all(controller)
        result = self.rx(controller, 255, ref.error(3, 1), 4)
        self.assertEqual(names(result), ("COMMAND_ERROR",))
        self.assertEqual((controller.snapshot().config, controller.snapshot().config_id), (old, 1))
        self.assertFalse(controller.snapshot().pending_request_id)
        controller.configure(Config(level_code=2000), 0)
        self.send_all(controller)
        self.assertEqual(names(self.rx(controller, 131, ref.u32(2) + ref.config(level=2000), 5)), ("CONFIGURED",))
        self.assertEqual(controller.snapshot().config.level_code, 2000)

    def test_only_normal_analog_config_and_advertised_pre_edge(self):
        controller = self.ready()
        for config in (Config(mode=0, auto_timeout_ms=100), Config(mode=2), Config(source=1),
                       Config(pretrigger_samples=512), Config(profile=0)):
            with self.subTest(config=config):
                self.unchanged_error(controller, controller.configure, config, 0)
        controller = self.negotiating()
        self.rx(controller, 130, ref.caps(edges=1), 2)
        self.unchanged_error(controller, controller.configure, Config(edge=1), 0)


class TransportAndDeadline(SessionChecks):
    def test_zero_partial_final_write_commit_and_serialization(self):
        controller = self.configured()
        controller.arm(0)
        ticket = controller.prepare_tx()
        before = controller.snapshot()
        self.assertEqual(controller.written(ticket, 0, 10000), ())
        self.assertIs(controller.prepare_tx(), ticket)
        self.assertEqual(controller.snapshot().next_tx_sequence, before.next_tx_sequence)
        self.assertEqual(controller.tick(20000), ())  # Нет придуманного wire write-stall timeout.
        self.assertEqual(controller.written(ticket, 1, 20000), ())
        self.assertEqual(controller.snapshot().next_tx_sequence, before.next_tx_sequence + 1)
        partial = controller.prepare_tx()
        self.assertEqual(partial.offset, 1)
        controller.stop(20000)
        self.assertIs(controller.prepare_tx(), partial)
        self.assertEqual(controller.tick(50000), ())
        self.send_all(controller, 50000)
        stop = controller.prepare_tx()
        self.assertEqual((stop.frame.message_type, stop.frame.sequence, stop.frame.request_id), (5, 5, 5))
        self.assertEqual(controller.tick(50999), ())
        self.recovery(controller, controller.tick(51000), "TIMEOUT")

    def test_last_byte_not_first_byte_starts_exact_deadline(self):
        for response_time, expires in ((5999, False), (6000, True)):
            with self.subTest(response_time=response_time):
                controller = self.session()
                controller.start(NONCE, 0)
                ticket = controller.prepare_tx()
                controller.written(ticket, 1, 100)
                self.assertEqual(controller.tick(5000), ())
                self.send_all(controller, 5000)
                events = self.rx(controller, 129, ref.hello_ack(), 1, 1, now=response_time)
                if expires:
                    self.recovery(controller, events, "TIMEOUT")
                else:
                    self.assertEqual(names(events), ("HELLO_ACCEPTED",))

    def test_uint64_time_boundary_does_not_wrap(self):
        controller = self.session()
        controller.start(NONCE, MAX_TIME - 1000)
        self.send_all(controller, MAX_TIME - 999)
        self.assertEqual(controller.tick(MAX_TIME), ())
        controller = self.session()
        controller.start(NONCE, MAX_TIME - 1000)
        self.send_all(controller, MAX_TIME - 1000)
        self.recovery(controller, controller.tick(MAX_TIME), "TIMEOUT")

    def test_unstarted_cancellation_consumes_no_sequence_and_rejects_stale_ticket(self):
        controller = self.configured()
        controller.arm(0)
        ticket = controller.prepare_tx()
        controller.written(ticket, 0, 0)
        before = controller.snapshot().next_tx_sequence
        self.recovery(controller, controller.transport_lost(0), "TRANSPORT_LOST")
        self.assertEqual(controller.snapshot().next_tx_sequence, before)
        self.unchanged_error(controller, controller.written, ticket, 1, 0)
        controller.start(NONCE + 1, 0)
        self.assertEqual(controller.prepare_tx().frame.sequence, 1)

    def test_unknown_outcome_never_replays_and_requires_fresh_nonce(self):
        controller = self.configured()
        controller.arm(0)
        old = controller.prepare_tx()
        controller.written(old, 1, 0)
        self.recovery(controller, controller.unknown_write(0), "UNKNOWN_WRITE")
        self.assertEqual(controller.unknown_write(1), ())
        self.unchanged_error(controller, controller.arm, 1)
        self.unchanged_error(controller, controller.start, NONCE, 1)
        controller.start(NONCE + 1, 1)
        self.unchanged_error(controller, controller.written, old, 1, 1)
        self.assertNotEqual(controller.prepare_tx().encoded, old.encoded)

    def test_waiting_trigger_has_no_command_or_transfer_timeout(self):
        controller = self.armed()
        self.rx(controller, 134, ref.status(state=2))
        for now in (1000, 3000, 5000, 1000000, MAX_TIME):
            self.assertEqual(controller.tick(now), ())
            self.assertEqual(controller.snapshot().phase.name, "ARMED")

    def test_stop_has_its_own_last_byte_deadline_after_prior_arm_reply(self):
        controller = self.configured()
        controller.arm(0)
        self.send_all(controller, 0)
        controller.stop(100)
        self.send_all(controller, 100)
        self.rx(controller, 132, ref.u32(4) + ref.u32(1), 4, now=999)
        self.assertEqual(controller.tick(1099), ())
        self.recovery(controller, controller.tick(1100), "TIMEOUT")

    def test_sample_progress_timeout_belongs_to_downstream_owner(self):
        controller = self.armed()
        self.rx(controller, 144, ref.begin())
        # Контроллер не может объявить progress по повторённым кускам samples.
        self.assertEqual(controller.tick(3000), ())
        self.assertEqual(names(self.rx(controller, 145, ref.data(), now=3000)), ("WINDOW_DATA",))
        self.assertEqual(controller.tick(10000), ())
        self.recovery(controller, controller.protocol_fault(FaultCode.DOWNSTREAM, 10000), "PROTOCOL_FAULT")

    def test_decode_version_or_downstream_fault_demands_recovery(self):
        for code in FaultCode:
            with self.subTest(code=code.name):
                controller = self.armed()
                result = controller.protocol_fault(code, 0)
                self.recovery(controller, result, "PROTOCOL_FAULT")
                self.assertEqual(result[-1].fault, code)


class StopAndTerminal(SessionChecks):
    def pending_arm_and_stop(self):
        controller = self.configured()
        controller.arm(0)
        self.send_all(controller)
        controller.stop(0)
        stop = self.send_all(controller)
        self.assertEqual(stop.frame.payload, ref.u32(4))
        return controller

    def test_stop_during_pending_arm_prior_ack_cannot_rearm(self):
        controller = self.pending_arm_and_stop()
        self.assertEqual(self.rx(controller, 132, ref.u32(4) + ref.u32(1), 4), ())
        self.assertTrue(controller.snapshot().stop_barrier)
        self.unchanged_error(controller, controller.arm, 0)
        self.unchanged_error(controller, controller.configure, Config(), 0)
        self.assertEqual(names(self.rx(controller, 133, ref.stop_ack(), 5)), ("STOPPED",))
        self.assertFalse(controller.snapshot().stop_barrier)
        self.assertEqual(controller.arm(0), ())

    def test_arm_error_then_stop_error_recovers_immediately(self):
        controller = self.pending_arm_and_stop()
        self.assertEqual(names(self.rx(controller, 255, ref.error(4, 2), 4)), ("COMMAND_ERROR",))
        self.assertEqual(controller.snapshot().generation, 0)
        result = self.rx(controller, 255, ref.error(5, 4), 5)
        self.recovery(controller, result, "DEVICE_ERROR")
        self.assertEqual(controller.tick(1000), ())
        self.unchanged_error(controller, controller.arm, 1000)
        self.unchanged_error(controller, controller.stop, 1000)

    def test_stop_ack_cannot_overtake_previous_reply_or_active_terminal(self):
        controller = self.pending_arm_and_stop()
        self.recovery(controller, self.rx(controller, 133, ref.stop_ack(), 5))
        controller = self.armed()
        self.rx(controller, 144, ref.begin())
        controller.stop(0)
        self.send_all(controller)
        self.recovery(controller, self.rx(controller, 133, ref.stop_ack(window=1), 5))

    def test_early_abort_is_one_cancellation_without_begin_or_samples(self):
        controller = self.armed()
        controller.stop(0)
        self.send_all(controller)
        frame = inbound(147, ref.abort(), seq=5, request=0)
        result = controller.receive(frame, 0)
        self.assertEqual(names(result), ("WINDOW_ABORT",))
        event = result[0]
        self.assertEqual((event.generation, event.window_id, event.reason), (4, 1, 1))
        self.assertEqual(event.frame, frame)
        self.assertFalse(hasattr(event, "samples"))
        self.assertEqual(controller.receive(frame, 0), ())
        self.assertEqual(names(self.rx(controller, 133, ref.stop_ack(window=1), 5)), ("STOPPED",))
        self.assertEqual(controller.snapshot().terminal.kind.name, "WINDOW_ABORT")
        self.assertIsNone(controller.snapshot().terminal.samples_crc32)

    def test_begin_data_end_stop_order_never_publishes_capture_success(self):
        controller = self.armed()
        self.assertEqual(names(self.rx(controller, 144, ref.begin())), ("WINDOW_BEGIN",))
        result = self.rx(controller, 145, ref.data())
        self.assertEqual(names(result), ("WINDOW_DATA",))
        self.assertIsInstance(result[0].frame, Frame)
        controller.stop(0)
        self.send_all(controller)
        terminal = inbound(146, ref.end(), seq=7, request=0)
        events = controller.receive(terminal, 0)
        self.assertEqual(names(events), ("WINDOW_END_ADMITTED",))
        self.assertFalse(hasattr(events[0], "samples"))
        self.assertEqual(controller.receive(terminal, 0), ())
        self.assertEqual(names(self.rx(controller, 133, ref.stop_ack(window=1), 5)), ("STOPPED",))
        self.assertEqual(controller.snapshot().terminal.kind.name, "WINDOW_END_ADMITTED")
        self.assertEqual(controller.snapshot().terminal.samples_crc32, 0x12345678)
        # Два samples намеренно НЕ составляют окно. Этот слой не принимает измерение.
        self.assertFalse(controller.snapshot().window_barrier)

    def test_data_after_stop_ack_and_conflicting_terminal_fail_closed(self):
        controller = self.armed()
        controller.stop(0)
        self.send_all(controller)
        self.rx(controller, 133, ref.stop_ack(), 5)
        self.recovery(controller, self.rx(controller, 145, ref.data()))
        for next_kind, next_payload in ((147, ref.abort()), (146, ref.end()), (144, ref.begin())):
            with self.subTest(next_kind=next_kind):
                controller = self.armed()
                self.rx(controller, 147, ref.abort())
                self.recovery(controller, self.rx(controller, next_kind, next_payload))

    def test_terminal_ids_reserved_reasons_and_old_generation_are_checked(self):
        bad = [ref.abort(window=0), ref.abort(window=0xFFFFFFFF), ref.abort(generation=3),
               ref.abort(reason=0), ref.abort(reason=7), ref.abort()[:-1], ref.abort() + b"\0",
               ref.abort()[:-2] + b"\1\0"]
        for payload in bad:
            with self.subTest(payload=payload.hex()):
                controller = self.armed()
                self.recovery(controller, self.rx(controller, 147, payload))
        controller = self.armed()
        self.recovery(controller, self.rx(controller, 146, ref.end()))  # END без BEGIN.

    def test_begin_end_fields_and_known_quality_flags(self):
        for offset, value in ((0, 3), (4, 0), (8, 2), (12, 2), (14, 1), (16, 1),
                              (18, 1), (20, 1), (21, 1), (22, 1), (23, 1),
                              (24, 1), (26, 8), (28, 1), (32, 70), (34, 20), (36, 1)):
            with self.subTest(offset=offset):
                controller = self.armed()
                payload = bytearray(ref.begin())
                payload[offset] = value
                self.recovery(controller, self.rx(controller, 144, bytes(payload)))
        controller = self.armed()
        payload = bytearray(ref.begin())
        payload[26] = 4  # Известный quality bit2 не равносилен source=external.
        self.assertEqual(names(self.rx(controller, 144, bytes(payload))), ("WINDOW_BEGIN",))
        for payload in (ref.end(generation=3), ref.end(window=2), ref.end()[:-1],
                        ref.end() + b"\0", ref.end()[:8] + ref.u16(2047) + ref.end()[10:],
                        ref.end()[:10] + ref.u16(1) + ref.end()[12:]):
            controller = self.armed()
            self.rx(controller, 144, ref.begin())
            self.recovery(controller, self.rx(controller, 146, payload))

    def test_stop_before_first_arm_and_repeat_stop_no_phantom_window(self):
        controller = self.configured()
        controller.stop(0)
        self.assertEqual(self.send_all(controller).frame.payload, ref.u32(0))
        result = self.rx(controller, 133, ref.stop_ack(generation=0), 4)
        self.assertEqual(names(result), ("STOPPED",))
        self.assertIsNone(controller.snapshot().terminal)
        controller.stop(0)
        self.assertEqual(self.send_all(controller).frame.request_id, 5)
        self.assertEqual(names(self.rx(controller, 133, ref.stop_ack(generation=0), 5)), ("STOPPED",))
        self.assertIsNone(controller.snapshot().terminal)


class OrderingAndValidation(SessionChecks):
    def test_foreign_nonce_ignored_even_if_stale_or_future_time_then_reconnect(self):
        controller = self.armed()
        old = controller.snapshot()
        for frame in (inbound(134, ref.status(), seq=999, request=0, nonce=NONCE + 1),
                      inbound(127, b"", seq=0, request=0, nonce=NONCE + 1)):
            self.assertEqual(controller.receive(frame, MAX_TIME), ())
            self.assertEqual(controller.snapshot(), old)
        controller.transport_lost(1)
        controller.start(NONCE + 1, 1)
        self.send_all(controller, 1)
        after = controller.snapshot()
        self.assertEqual(controller.receive(inbound(129, ref.hello_ack()), 2), ())
        self.assertEqual(controller.snapshot(), after)
        self.assertEqual(names(self.rx(controller, 129, ref.hello_ack(), 1, 1, NONCE + 1, 2)), ("HELLO_ACCEPTED",))
        self.assertIsNone(controller.snapshot().config)
        self.assertEqual(controller.snapshot().generation, 0)

    def test_exact_last_replay_only_conflict_gap_and_stale_fail_closed(self):
        controller = self.armed()
        frame = inbound(134, ref.status(), seq=5, request=0)
        self.assertEqual(names(controller.receive(frame, 0)), ("STATUS",))
        before = controller.snapshot()
        self.assertEqual(controller.receive(frame, 0), ())
        self.assertEqual(controller.snapshot(), before)
        altered = inbound(134, ref.status(state=1), seq=5, request=0)
        self.recovery(controller, controller.receive(altered, 0), "SEQUENCE")
        for seq in (0, 3, 6, 0xFFFFFFFF):
            controller = self.armed()
            self.recovery(controller, self.rx(controller, 134, ref.status(), seq=seq))
        controller = self.armed()
        self.rx(controller, 134, ref.status(), seq=5)
        self.rx(controller, 134, ref.status(state=1), seq=6)
        self.recovery(controller, controller.receive(frame, 0), "SEQUENCE")

    def test_direction_unknown_type_wrong_request_and_unsent_response(self):
        for kind in range(1, 8):
            controller = self.armed()
            # Даже чужой nonce не меняет приоритет проверки направления.
            self.recovery(controller, self.rx(controller, kind, b"", nonce=NONCE + 1), "DIRECTION")
        for kind in (0, 8, 127, 128, 136, 143, 148, 254):
            controller = self.armed()
            self.recovery(controller, self.rx(controller, kind, b""), "TYPE")
        for kind, payload, request in ((130, ref.caps(), 99), (132, ref.u32(4) + ref.u32(1), 4),
                                       (134, ref.status(), 5), (147, ref.abort(), 4),
                                       (135, ref.u32(4) + ref.u32(1), 0)):
            controller = self.armed()
            self.recovery(controller, self.rx(controller, kind, payload, request))
        controller = self.configured()
        controller.arm(0)
        self.recovery(controller, self.rx(controller, 132, ref.u32(4) + ref.u32(1), 4))

    def test_arm_generation_config_and_error_payloads_checked(self):
        for payload in (ref.u32(3) + ref.u32(1), ref.u32(4) + ref.u32(2), ref.u32(0) + ref.u32(1),
                        ref.u32(4), ref.u32(4) + ref.u32(1) + b"\0"):
            controller = self.configured()
            controller.arm(0)
            self.send_all(controller)
            self.recovery(controller, self.rx(controller, 132, payload, 4))
        for payload in (ref.error(3, 1), ref.error(4, 0), ref.error(4, 9),
                        bytes((4, 2, 1, 0)) + ref.u32(0), ref.error(4, 2)[:-1]):
            controller = self.configured()
            controller.arm(0)
            self.send_all(controller)
            self.recovery(controller, self.rx(controller, 255, payload, 4))

    def test_stop_ack_payload_checked(self):
        for payload in (ref.stop_ack(generation=3), ref.stop_ack()[:-1], ref.stop_ack() + b"\0",
                        ref.stop_ack()[:8] + b"\1\0\0\0", ref.stop_ack()[:9] + b"\1\0\0"):
            controller = self.armed()
            controller.stop(0)
            self.send_all(controller)
            self.recovery(controller, self.rx(controller, 133, payload, 5))

    def test_status_payload_and_reserved_checked(self):
        for payload in (ref.status()[:-1], ref.status() + b"\0", ref.status(state=7),
                        ref.status(reason=7), ref.status()[:2] + b"\1\0" + ref.status()[4:],
                        ref.status(generation=3)):
            controller = self.armed()
            self.recovery(controller, self.rx(controller, 134, payload))

    def test_maximum_identifier_is_never_used_and_last_allowed_replay_survives(self):
        # White-box setup только граничных счётчиков; каждый проверяемый переход публичный.
        controller = self.armed()
        controller._next_rx = MAX_ID
        last = inbound(134, ref.status(), MAX_ID, 0)
        self.assertEqual(names(controller.receive(last, 0)), ("STATUS",))
        self.assertEqual(controller.receive(last, 0), ())
        self.recovery(controller, self.rx(controller, 134, ref.status(), seq=0xFFFFFFFF))
        for field in ("_next_tx", "_next_request"):
            controller = self.configured()
            setattr(controller, field, MAX_ID)
            controller.arm(0)
            ticket = self.send_all(controller)
            self.assertLessEqual(ticket.frame.sequence, MAX_ID)
            self.assertLessEqual(ticket.frame.request_id, MAX_ID)
            self.rx(controller, 132, ref.u32(ticket.frame.request_id) + ref.u32(1), ticket.frame.request_id)
            # Следующий STOP не может выдать FFFFFFFF или обернуться к нулю.
            result = controller.stop(0)
            self.recovery(controller, result, "EXHAUSTED")

    def test_invalid_python_inputs_time_and_huge_forged_frame_preserve_state(self):
        controller = self.session()
        for nonce in (False, True, 1.0, "1", b"1", None, 0, -1, 2**64):
            self.unchanged_error(controller, controller.start, nonce, 0)
        controller.start(NONCE, 10)
        ticket = controller.prepare_tx()
        for now in (False, 10.0, "10", None, -1, MAX_TIME + 1, 9):
            for function, prefix in ((controller.tick, ()), (controller.written, (ticket, 0)),
                                     (controller.transport_lost, ()), (controller.unknown_write, ()),
                                     (controller.receive, (inbound(129, ref.hello_ack()),)),
                                     (controller.protocol_fault, (FaultCode.VERSION,))):
                with self.subTest(function=function.__name__, now=now):
                    self.unchanged_error(controller, function, *prefix, now)
        for value in (None, (), b"", object()):
            self.unchanged_error(controller, controller.receive, value, 10)
        forged = object.__new__(Frame)
        for name, value in (("message_type", 129), ("session", NONCE), ("sequence", 1),
                            ("request_id", 1), ("payload", b"x" * 1000000)):
            object.__setattr__(forged, name, value)
        self.unchanged_error(controller, controller.receive, forged, 10)
        for field, value in (("message_type", True), ("session", -1), ("sequence", 2**32),
                             ("request_id", 1.0), ("payload", bytearray())):
            bad = object.__new__(Frame)
            for name in ("message_type", "session", "sequence", "request_id", "payload"):
                object.__setattr__(bad, name, getattr(inbound(129, ref.hello_ack()), name))
            object.__setattr__(bad, field, value)
            self.unchanged_error(controller, controller.receive, bad, 10)
        for code in (None, "VERSION", 1):
            self.unchanged_error(controller, controller.protocol_fault, code, 10)

    def test_count_ticket_and_config_validation_preserve_state(self):
        controller = self.configured()
        for value in (None, (), b"", object()):
            self.unchanged_error(controller, controller.configure, value, 0)
        bad_config = object.__new__(Config)
        for field in fields(Config()):
            object.__setattr__(bad_config, field.name, getattr(Config(), field.name))
        object.__setattr__(bad_config, "level_code", 2**100)
        self.unchanged_error(controller, controller.configure, bad_config, 0)
        controller.arm(0)
        ticket = controller.prepare_tx()
        for count in (False, 1.0, "1", None, -1, len(ticket.encoded) + 1, 2**100):
            self.unchanged_error(controller, controller.written, ticket, count, 0)
        for wrong in (None, object(), replace(ticket), replace(ticket, encoded=b"x" * 1000000)):
            self.unchanged_error(controller, controller.written, wrong, 0, 0)
        controller.written(ticket, 1, 0)
        self.unchanged_error(controller, controller.written, ticket, 0, 0)
        current = controller.prepare_tx()
        self.unchanged_error(controller, controller.written, current, len(current.encoded), 0)
        for kwargs in ({"level_code": False}, {"level_code": 0}, {"level_code": 4095},
                       {"pretrigger_samples": -1}, {"auto_timeout_ms": 1}, {"profile": -1},
                       {"edge": 2}, {"mode": 3}, {"source": 2}):
            with self.subTest(config=kwargs), self.assertRaises(SessionError):
                Config(**kwargs)

    def test_one_ordinary_plus_stop_slots_do_not_grow(self):
        controller = self.configured()
        controller.arm(0)
        controller.stop(0)
        controller.prepare_tx()
        snapshot = controller.snapshot()
        self.assertEqual((snapshot.pending_request_id, snapshot.pending_stop_id), (4, 5))
        for _ in range(100):
            for function, prefix in ((controller.arm, ()), (controller.configure, (Config(),)),
                                     (controller.stop, ())):
                self.unchanged_error(controller, function, *prefix, 0)
        self.assertEqual(controller.snapshot(), snapshot)
        self.assertEqual(controller.prepare_tx().frame.message_type, 4)

    def test_repeated_input_keeps_only_last_rx_and_bounded_events(self):
        controller = self.armed()
        frame = inbound(134, ref.status(), seq=5, request=0)
        controller.receive(frame, 0)
        stale = inbound(134, ref.status(), seq=0, request=0, nonce=NONCE + 1)
        tracemalloc.start()
        try:
            for _ in range(100):
                controller.receive(frame, 0)
                controller.receive(stale, 0)
            gc.collect()
            before, _ = tracemalloc.get_traced_memory()
            for _ in range(3000):
                self.assertEqual(controller.receive(frame, 0), ())
                self.assertEqual(controller.receive(stale, 0), ())
                self.assertEqual(controller.tick(0), ())
            # Новые валидные события тоже не должны накапливаться внутри.
            for sequence in range(6, 1006):
                frame = inbound(134, ref.status(), seq=sequence, request=0)
                result = controller.receive(frame, 0)
                self.assertEqual(names(result), ("STATUS",))
            gc.collect()
            after, _ = tracemalloc.get_traced_memory()
            self.assertLess(after - before, 32768)
        finally:
            tracemalloc.stop()
        self.assertEqual(controller._last_rx, frame)
        self.assertIsNone(controller.prepare_tx())
        self.assertIsNone(controller.snapshot().terminal)


if __name__ == "__main__":
    unittest.main()

"""Executable HOST transitions, synthetic peer bytes, explicit fake clocks.

No firmware/CDC/GUI/disk implementation is exercised. Counter boundary tests seed
private counters to avoid billions of frames; all subsequent operations use the
public dispatch API. Original retained vectors are tested as an actual trace.
"""
from dataclasses import FrozenInstanceError, replace
import math
import struct
import unittest
import zlib

from motortester import stream_endpoint as e
from motortester import stream_wire as w
from test_stream_wire import NEGATIVES, fixture, frame

NONCE = 0x0102030405060708


def send(endpoint, now=0):
    offers = []
    for _ in range(3):
        events = endpoint.next_write(now)
        if not events:
            return offers
        offer, = events
        if not isinstance(offer, e.WriteOffer):
            raise AssertionError(events)
        offers.append(offer)
        result = endpoint.observe_write(offer.token, len(offer.data), now)
        if result:
            raise AssertionError(result)
    raise AssertionError("unbounded_tx")


def ready(started=True):
    endpoint = e.Endpoint()
    endpoint.hello(NONCE, 0)
    send(endpoint)
    endpoint.receive(fixture("hello_ack"), 0)
    endpoint.get_caps(0)
    send(endpoint)
    endpoint.receive(fixture("caps"), 0)
    if started:
        endpoint.start(0, action_id=11)
        send(endpoint)
        endpoint.receive(fixture("start_ack"), 0)
    return endpoint


def data(endpoint, *, samples=tuple(range(128)), first=None, generation=None, sequence=None, request=0, nonce=None):
    p = struct.pack("<IIQHH", endpoint.generation if generation is None else generation, 1,
                    endpoint.received_end if first is None else first, len(samples), 0)
    p += struct.pack("<" + "H" * len(samples), *samples)
    return frame(152, p, nonce=endpoint.nonce if nonce is None else nonce,
                 sequence=endpoint._next_rx if sequence is None else sequence, request=request)


def end(endpoint, *, reason=0, flags=0, produced=None, stop=None, prefix=None, crc=None,
        last=None, generation=None, sequence=None, config=1):
    prefix = endpoint.received_end if prefix is None else prefix
    if produced is None:
        produced = 0 if flags & 1 else prefix
    if stop is None:
        stop = endpoint.stop_pending.request_id if endpoint.stop_pending else 0
    p = struct.pack("<IIQQIIHHI", endpoint.generation if generation is None else generation,
                    config, prefix, produced, endpoint._last_data if last is None else last,
                    endpoint.received_crc32 if crc is None else crc, reason, flags, stop)
    return frame(153, p, nonce=endpoint.nonce, sequence=endpoint._next_rx if sequence is None else sequence)


def ack(endpoint, *, generation=None, prefix=None, terminal=None, flags=None, request=None):
    t = endpoint.terminal
    p = struct.pack("<IQII", endpoint.generation if generation is None else generation,
                    t.terminal.prefix_end if prefix is None else prefix,
                    t.sequence if terminal is None else terminal,
                    t.terminal.flags if flags is None else flags)
    return frame(133, p, nonce=endpoint.nonce, sequence=endpoint._next_rx,
                 request=endpoint.stop_pending.request_id if request is None else request)


class EndpointTests(unittest.TestCase):
    def assertFault(self, endpoint, code=None):
        self.assertEqual(endpoint.state, e.State.RECOVERING)
        self.assertIsNotNone(endpoint.first_fault)
        if code:
            self.assertEqual(endpoint.first_fault.code, code)
        self.assertLessEqual(endpoint.buffered_bytes, w.MAX_ENCODED)

    def test_actual_positive_trace(self):
        endpoint = ready()
        self.assertEqual(endpoint.state, e.State.STREAMING)
        endpoint.stop(0, action_id=22)
        offer, = endpoint.next_write(0)
        self.assertEqual(offer.data, fixture("stop"))
        endpoint.observe_write(offer.token, len(offer.data), 0)
        block, = endpoint.receive(fixture("final_data"), 1)
        self.assertEqual(block.block.samples, (0, 1, 2048, 4095))
        terminal, = endpoint.receive(fixture("end"), 2)
        stopped, = endpoint.receive(fixture("stop_ack"), 3)
        self.assertIs(stopped.terminal, terminal)
        self.assertEqual(stopped.request.action_id, 22)
        self.assertEqual(endpoint.state, e.State.NEEDS_HELLO)
        self.assertIsNone(endpoint.first_fault)
        self.assertIsInstance(endpoint.start(3)[0], e.Refused)
        self.assertIsInstance(endpoint.stop(3)[0], e.Refused)

    def test_all_retained_negative_frames_execute_endpoint(self):
        for name in NEGATIVES:
            with self.subTest(name=name):
                endpoint = ready(False) if name == "wrong_rate" else ready()
                if name == "wrong_rate":
                    # A new request uses distinct sequence/request IDs.
                    endpoint = e.Endpoint()
                    endpoint.hello(NONCE, 0)
                    send(endpoint)
                    endpoint.receive(fixture("hello_ack"), 0)
                    endpoint.get_caps(0)
                    send(endpoint)
                elif name == "false_clean_tail":
                    endpoint.stop(0)
                    send(endpoint)
                    endpoint.receive(fixture("final_data"), 0)
                endpoint.receive(fixture(name), 1)
                self.assertFault(endpoint)

    def test_no_start_or_stop_during_negotiation(self):
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        self.assertIsInstance(endpoint.start(0)[0], e.Refused)
        self.assertIsInstance(endpoint.stop(0)[0], e.Refused)
        send(endpoint)
        endpoint.receive(fixture("hello_ack"), 0)
        self.assertIsInstance(endpoint.start(0)[0], e.Refused)
        self.assertEqual(endpoint.next_write(0), ())
        self.assertEqual(endpoint.state, e.State.STOPPED)

    def test_capabilities_failure_never_falls_back(self):
        for major, minor in ((0, 1), (1, 2)):
            endpoint = e.Endpoint()
            endpoint.hello(NONCE, 0)
            send(endpoint)
            p = w.decode_frame(fixture("hello_ack")).payload
            endpoint.receive(frame(129, p, sequence=1, request=1, major=major, minor=minor), 1)
            self.assertFault(endpoint, "version")
            self.assertEqual(endpoint.next_write(1), ())

    def test_start_plan_is_not_request_admission(self):
        endpoint = ready(False)
        endpoint.get_caps(0)
        send(endpoint)
        p = w.decode_frame(fixture("caps")).payload
        endpoint.receive(frame(130, p, sequence=3, request=3), 1)
        plan, = endpoint.plan_start(2)
        self.assertEqual(plan.generation, 4)  # Never infer the common happy-path 3.
        self.assertIsNone(endpoint.ordinary)
        self.assertIsNone(endpoint.next_deadline_ms)
        self.assertEqual(endpoint.next_write(50000), ())
        self.assertIsInstance(endpoint.get_caps(50000)[0], e.Refused)
        self.assertIsInstance(endpoint.start(50000)[0], e.Refused)
        pending, = endpoint.start(50000, 99, plan=plan)
        self.assertEqual((pending.request_id, pending.admitted_ms, pending.deadline_ms), (4, 50000, 52000))
        self.assertEqual(endpoint.generation, plan.generation)
        self.assertEqual(endpoint.state, e.State.STARTING)

    def test_start_plan_cancel_copy_and_recovery_invalidation(self):
        endpoint = ready(False)
        plan, = endpoint.plan_start(0)
        self.assertIsInstance(endpoint.start(0, plan=replace(plan))[0], e.Refused)
        endpoint.cancel_start_plan(0)
        self.assertIsInstance(endpoint.start(0, plan=plan)[0], e.Refused)
        plan, = endpoint.plan_start(0)
        endpoint.recover(0)
        endpoint.isolation_complete(NONCE + 1, 0)
        self.assertIsNone(endpoint._plan)
        self.assertIsInstance(endpoint.start(0, plan=plan)[0], e.Refused)

    def test_full_blocks_short_final_empty_end(self):
        for sizes in ((), (128,), (128, 128, 3)):
            endpoint = ready()
            endpoint.stop(0)
            send(endpoint)
            all_samples = b""
            for count in sizes:
                samples = tuple(range(count))
                endpoint.receive(data(endpoint, samples=samples), 1)
                all_samples += struct.pack("<" + "H" * count, *samples)
            terminal, = endpoint.receive(end(endpoint), 2)
            self.assertEqual(terminal.terminal.prefix_end, sum(sizes))
            self.assertEqual(terminal.terminal.prefix_crc32, zlib.crc32(all_samples))
            endpoint.receive(ack(endpoint), 3)
            self.assertEqual(endpoint.state, e.State.NEEDS_HELLO)

    def test_no_data_after_short_even_if_contiguous(self):
        endpoint = ready()
        endpoint.receive(data(endpoint, samples=(1,)), 1)
        endpoint.receive(data(endpoint, samples=(2,)), 2)
        self.assertFault(endpoint, "data_after_short")
        self.assertEqual(endpoint.received_end, 1)

    def test_immediate_identical_duplicate_has_no_progress(self):
        endpoint = ready()
        encoded = data(endpoint)
        endpoint.receive(encoded, 10)
        before = (endpoint.received_end, endpoint.received_crc32, endpoint.next_deadline_ms)
        self.assertEqual(endpoint.receive(encoded, 999), ())
        self.assertEqual((endpoint.received_end, endpoint.received_crc32, endpoint.next_deadline_ms), before)
        endpoint.tick(1010)
        self.assertFault(endpoint, "data_timeout")

    def test_conflicting_older_gap_reorder_fail(self):
        for mode in ("conflict", "older", "gap", "overlap", "generation", "request"):
            endpoint = ready()
            original = data(endpoint)
            endpoint.receive(original, 1)
            if mode == "older":
                endpoint.receive(data(endpoint), 2)
                bad = original
            elif mode == "conflict":
                bad = data(endpoint, sequence=4, first=0, samples=(1,) * 128)
            elif mode == "gap":
                bad = data(endpoint, sequence=6)
            elif mode == "overlap":
                bad = data(endpoint, first=127)
            elif mode == "generation":
                bad = data(endpoint, generation=4)
            else:
                bad = data(endpoint, request=4)
            endpoint.receive(bad, 3)
            self.assertFault(endpoint)

    def test_stale_nonce_does_not_join_or_advance(self):
        endpoint = ready()
        endpoint.receive(data(endpoint, nonce=NONCE + 1), 999)
        self.assertEqual((endpoint.received_end, endpoint._next_rx), (0, 4))
        endpoint.tick(1000)
        self.assertFault(endpoint, "data_timeout")

    def test_full_prefix_crc_remains_distinct_from_late_selected_interval(self):
        endpoint = ready()
        first = tuple(range(128))
        selected = (4095,) * 128
        endpoint.receive(data(endpoint, samples=first), 1)
        # Simulate adapter writer-ready selecting R=128; endpoint is unaffected.
        endpoint.receive(data(endpoint, samples=selected), 2)
        prefix_bytes = struct.pack("<128H", *first) + struct.pack("<128H", *selected)
        self.assertEqual(endpoint.received_crc32, zlib.crc32(prefix_bytes))
        self.assertNotEqual(endpoint.received_crc32, zlib.crc32(struct.pack("<128H", *selected)))
        endpoint.stop(2)
        send(endpoint, 2)
        endpoint.receive(end(endpoint, crc=zlib.crc32(struct.pack("<128H", *selected))), 3)
        self.assertFault(endpoint, "terminal_prefix")

    def test_end_join_mismatches(self):
        for alteration in ({"prefix": 127}, {"crc": 1}, {"last": 99}, {"stop": 99},
                           {"generation": 99}, {"config": 2}, {"produced": 129}, {"flags": 8}):
            with self.subTest(alteration=alteration):
                endpoint = ready()
                endpoint.receive(data(endpoint), 1)
                endpoint.stop(1)
                send(endpoint, 1)
                endpoint.receive(end(endpoint, **alteration), 2)
                self.assertFault(endpoint)
                self.assertIsNone(endpoint.terminal)

    def test_ack_alone_or_mismatch_never_completes(self):
        endpoint = ready()
        endpoint.stop(0)
        send(endpoint)
        endpoint.receive(frame(133, struct.pack("<IQII", 3, 0, 4, 0), sequence=4, request=4), 1)
        self.assertFault(endpoint, "state")
        for alteration in ({"generation": 4}, {"prefix": 1}, {"terminal": 8}, {"flags": 2}, {"request": 5}):
            endpoint = ready()
            endpoint.stop(0)
            send(endpoint)
            endpoint.receive(end(endpoint), 1)
            immutable = endpoint.terminal
            endpoint.receive(ack(endpoint, **alteration), 2)
            self.assertFault(endpoint)
            self.assertIs(endpoint.terminal, immutable)

    def test_one_end_and_no_data_after_end(self):
        for second in ("data", "end"):
            endpoint = ready()
            endpoint.receive(end(endpoint, reason=1), 1)
            original = endpoint.terminal
            endpoint.receive(data(endpoint) if second == "data" else end(endpoint, reason=1), 2)
            self.assertEqual(endpoint.state, e.State.RECOVERING)
            self.assertIs(endpoint.terminal, original)
            self.assertEqual(endpoint.first_fault.code, "device_1")

    def test_unsolicited_end_late_stop_references_frozen_end(self):
        endpoint = ready()
        terminal, fault = endpoint.receive(end(endpoint, reason=2, flags=3), 1)
        self.assertIsNone(terminal.terminal.produced_end)
        self.assertEqual(terminal.terminal.stop_request_id, 0)
        endpoint.stop(50000, action_id=23)
        send(endpoint, 50000)
        self.assertIsInstance(endpoint.stop(50001)[0], e.Refused)
        stopped, = endpoint.receive(ack(endpoint), 51999)
        self.assertIs(stopped.terminal, terminal)
        self.assertEqual(stopped.request.action_id, 23)
        self.assertEqual(endpoint.first_fault.code, "device_2")
        self.assertEqual(endpoint.state, e.State.NEEDS_HELLO)

    def test_late_stop_timeout_retains_end(self):
        endpoint = ready()
        endpoint.receive(end(endpoint, reason=1), 1)
        terminal = endpoint.terminal
        endpoint.stop(100)
        send(endpoint, 100)
        endpoint.receive(ack(endpoint), 2100)
        self.assertFault(endpoint, "device_1")
        self.assertIs(endpoint.last_recovery.terminal, terminal)
        self.assertEqual(endpoint.last_recovery.stop.request_id, 4)

    def test_stop_during_end_fragment_accepts_immutable_unsolicited_terminal(self):
        endpoint = ready()
        wire = end(endpoint, reason=2, stop=0)
        endpoint.receive(wire[:10], 1)
        endpoint.stop(2)
        send(endpoint, 2)
        endpoint.receive(wire[10:], 3)
        self.assertEqual(endpoint.terminal.terminal.stop_request_id, 0)
        endpoint.receive(ack(endpoint), 4)
        self.assertEqual(endpoint.state, e.State.NEEDS_HELLO)
        self.assertEqual(endpoint.first_fault.code, "device_2")

    def test_stop_before_failure_end_may_join_stop_id(self):
        endpoint = ready()
        endpoint.stop(1)
        send(endpoint, 1)
        endpoint.receive(end(endpoint, reason=1, flags=2, produced=128), 2)
        endpoint.receive(ack(endpoint), 3)
        self.assertEqual(endpoint.first_fault.code, "device_1")
        self.assertEqual(endpoint.state, e.State.NEEDS_HELLO)

    def test_quiescence_failure_preserves_terminal_forbids_ack(self):
        endpoint = ready()
        endpoint.stop(0)
        send(endpoint)
        endpoint.receive(end(endpoint, reason=5, flags=7), 1)
        self.assertFault(endpoint, "device_5")
        self.assertIsNotNone(endpoint.terminal)
        self.assertIsNone(endpoint.terminal.terminal.produced_end)
        self.assertEqual(endpoint.receive(ack(endpoint, request=4), 2), ())
        self.assertEqual(endpoint.state, e.State.RECOVERING)

    def test_first_local_cause_survives_clean_end_and_ack(self):
        for code in ("storage", "capacity"):
            endpoint = ready()
            endpoint.adapter_fault(code, 1)
            endpoint.stop(1)
            send(endpoint, 1)
            endpoint.receive(data(endpoint), 2)
            endpoint.receive(end(endpoint), 3)
            endpoint.receive(ack(endpoint), 4)
            self.assertEqual(endpoint.first_fault.code, code)
            self.assertEqual(endpoint.received_end, 128)
            self.assertEqual(endpoint.state, e.State.NEEDS_HELLO)

    def test_start_stop_race_single_owner_and_two_live_deadlines(self):
        endpoint = ready(False)
        start, = endpoint.start(0, 11)
        offer, = endpoint.next_write(100)
        endpoint.observe_write(offer.token, 1, 100)
        stop, = endpoint.stop(500, 22)
        self.assertEqual((start.deadline_ms, stop.deadline_ms), (2000, 2500))
        suffix, = endpoint.next_write(600)
        self.assertEqual(suffix.request.kind, w.Kind.START)
        endpoint.observe_write(suffix.token, len(suffix.data), 600)
        stop_offer, = endpoint.next_write(700)
        self.assertEqual(stop_offer.request.kind, w.Kind.STOP)
        endpoint.observe_write(stop_offer.token, len(stop_offer.data), 700)
        started, = endpoint.receive(fixture("start_ack"), 800)
        self.assertEqual(started.first_index, 0)
        self.assertEqual(started.request.action_id, 11)
        self.assertEqual(endpoint.state, e.State.DRAINING)
        endpoint.receive(end(endpoint), 801)
        endpoint.receive(ack(endpoint), 802)
        self.assertEqual(endpoint.state, e.State.NEEDS_HELLO)

    def test_stop_does_not_cancel_or_extend_start_deadline(self):
        endpoint = ready(False)
        endpoint.start(0)
        offer, = endpoint.next_write(900)
        endpoint.observe_write(offer.token, 1, 900)
        endpoint.stop(1000)
        endpoint.tick(2000)
        self.assertFault(endpoint, "request_timeout")
        self.assertEqual(endpoint.last_recovery.ordinary.request_id, 3)
        self.assertEqual(endpoint.last_recovery.ordinary.accepted_bytes, 1)
        self.assertEqual(endpoint.last_recovery.stop.deadline_ms, 3000)
        self.assertEqual(endpoint.next_write(2000), ())

    def test_failed_start_with_stop_does_not_fabricate_generation(self):
        endpoint = ready(False)
        endpoint.start(0)
        send(endpoint)
        endpoint.stop(0)
        send(endpoint)
        error = frame(255, struct.pack("<BBHI", 8, 4, 0, 3), sequence=3, request=3)
        endpoint.receive(error, 1)
        self.assertFault(endpoint, "device_error_4")
        self.assertIsNone(endpoint.terminal)
        self.assertEqual(endpoint.last_recovery.stop.request_id, 4)
        self.assertEqual(endpoint.receive(fixture("stop_ack"), 2), ())

    def test_data_before_start_ack_fails(self):
        endpoint = ready(False)
        endpoint.start(0)
        send(endpoint)
        endpoint.receive(data(endpoint), 1)
        self.assertFault(endpoint, "state")

    def test_request_deadline_before_equal_after(self):
        for time in (1999, 2000, 2001):
            endpoint = e.Endpoint()
            endpoint.hello(NONCE, 0)
            send(endpoint)
            endpoint.receive(fixture("hello_ack"), time)
            if time < 2000:
                self.assertEqual(endpoint.state, e.State.STOPPED)
            else:
                self.assertFault(endpoint, "request_timeout")
                self.assertEqual(endpoint.last_recovery.ordinary.request_id, 1)

    def test_admission_origin_literal_counterexample(self):
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        offer, = endpoint.next_write(900)
        endpoint.observe_write(offer.token, 1, 900)
        offer, = endpoint.next_write(1500)
        endpoint.observe_write(offer.token, len(offer.data), 1500)
        endpoint.receive(fixture("hello_ack"), 2700)
        self.assertFault(endpoint, "request_timeout")
        self.assertEqual(endpoint.last_recovery.ordinary.deadline_ms, 2000)

    def test_zero_partial_trickle_and_silence_are_bounded(self):
        for mode in ("zero", "partial", "trickle", "silence"):
            endpoint = e.Endpoint()
            endpoint.hello(NONCE, 0)
            if mode == "silence":
                send(endpoint)
            else:
                for now in (0, 700, 1400, 1999):
                    offer, = endpoint.next_write(now)
                    accepted = 0 if mode == "zero" else 1
                    endpoint.observe_write(offer.token, accepted, now)
                    if mode == "partial":
                        break
            endpoint.tick(2000)
            self.assertFault(endpoint, "request_timeout")
            self.assertIsNotNone(endpoint.last_recovery.ordinary)
            self.assertEqual(endpoint.last_recovery.ordinary.deadline_ms, 2000)

    def test_completion_at_expiry_is_not_applied(self):
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        offer, = endpoint.next_write(0)
        endpoint.observe_write(offer.token, len(offer.data), 2000)
        self.assertFault(endpoint, "request_timeout")
        self.assertEqual(endpoint.last_recovery.ordinary.accepted_bytes, 0)
        self.assertIs(endpoint.last_recovery.outstanding_offer, offer)

    def test_response_requires_completed_exact_request_write(self):
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        endpoint.receive(fixture("hello_ack"), 1)
        self.assertFault(endpoint, "request")

    def test_unknown_malformed_counts_and_exact_token(self):
        for count in (None, -1, True, 1.0, "1", 99999):
            endpoint = e.Endpoint()
            endpoint.hello(NONCE, 0)
            offer, = endpoint.next_write(0)
            endpoint.observe_write(offer.token, count, 1)
            self.assertFault(endpoint, "write_count_unknown")
            self.assertIsNone(endpoint.last_recovery.ordinary.accepted_bytes)
        for token in (True, -1, 1.0, 100):
            endpoint = e.Endpoint()
            endpoint.hello(NONCE, 0)
            endpoint.next_write(0)
            endpoint.observe_write(token, 0, 1)
            self.assertFault(endpoint, "write_correlation")

    def test_no_duplicate_offer_or_replayed_completion(self):
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        offer, = endpoint.next_write(0)
        self.assertEqual(endpoint.next_write(0), ())
        endpoint.observe_write(offer.token, 1, 0)
        next_offer, = endpoint.next_write(0)
        self.assertNotEqual(next_offer.token, offer.token)
        self.assertEqual(next_offer.data, offer.data[1:])
        endpoint.observe_write(offer.token, 1, 0)
        self.assertFault(endpoint, "write_correlation")

    def test_sequence_commits_only_at_first_encoded_byte(self):
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        offer, = endpoint.next_write(0)
        endpoint.observe_write(offer.token, 0, 0)
        self.assertEqual(endpoint._next_tx, 1)
        offer, = endpoint.next_write(0)
        endpoint.observe_write(offer.token, 1, 0)
        self.assertEqual(endpoint._next_tx, 2)
        endpoint.transport_fault(1)
        endpoint.isolation_complete(NONCE + 1, 2)
        offer, = endpoint.next_write(2)
        self.assertEqual(offer.data[:1], b"\0")
        endpoint.observe_write(offer.token, 1, 2)
        self.assertEqual(endpoint._next_tx, 1)
        self.assertEqual(endpoint.ordinary.frame_accepted_bytes, 0)
        offer, = endpoint.next_write(2)
        endpoint.observe_write(offer.token, 1, 2)
        self.assertEqual(endpoint._next_tx, 2)
        self.assertEqual(endpoint.ordinary.accepted_bytes, 2)
        self.assertEqual(endpoint.ordinary.frame_accepted_bytes, 1)

    def test_first_next_data_deadline_before_equal_after(self):
        for progress in (False, True):
            for delta in (-1, 0, 1):
                endpoint = ready()
                origin = 10 if progress else 0
                if progress:
                    endpoint.receive(data(endpoint), origin)
                endpoint.receive(data(endpoint), origin + 1000 + delta)
                if delta < 0:
                    self.assertEqual(endpoint.state, e.State.STREAMING)
                else:
                    self.assertFault(endpoint, "data_timeout")
                    self.assertEqual(endpoint.received_end, 128 if progress else 0)

    def test_end_at_data_deadline_loses_even_if_stop_pending(self):
        endpoint = ready()
        endpoint.stop(0)
        send(endpoint)
        endpoint.receive(end(endpoint), 1000)
        self.assertFault(endpoint, "data_timeout")
        self.assertIsNone(endpoint.terminal)
        self.assertEqual(endpoint.last_recovery.stop.request_id, 4)

    def test_partial_frame_absolute_deadline_never_extends(self):
        for delta in (-1, 0, 1):
            endpoint = e.Endpoint()
            endpoint.hello(NONCE, 0)
            send(endpoint)
            raw = fixture("hello_ack")
            endpoint.receive(raw[:1], 100)
            endpoint.receive(raw[1:2], 800)
            endpoint.receive(raw[2:], 1100 + delta)
            if delta < 0:
                self.assertEqual(endpoint.state, e.State.STOPPED)
            else:
                self.assertFault(endpoint, "partial_timeout")

    def test_delimiters_do_not_extend_liveness(self):
        endpoint = ready()
        endpoint.receive(b"\0" * 100, 999)
        endpoint.tick(1000)
        self.assertFault(endpoint, "data_timeout")

    def test_active_malformed_nonempty_frames_fail_immediately(self):
        for raw in (b"\x03a\0", b"\x01\0", b"\x01" * 544,
                    frame(2, sequence=4, request=4), frame(251, sequence=4)):
            endpoint = ready()
            endpoint.receive(raw, 1)
            self.assertFault(endpoint)
        endpoint = ready()
        bad = bytearray(fixture("final_data"))
        bad[-2] ^= 1
        endpoint.receive(bytes(bad), 1)
        self.assertFault(endpoint)

    def test_idle_invalid_is_discarded_but_sequence_gap_is_fatal(self):
        endpoint = ready(False)
        endpoint.receive(b"\x03a\0", 1)
        self.assertEqual(endpoint.state, e.State.STOPPED)
        endpoint.get_caps(1)
        send(endpoint, 1)
        payload = w.decode_frame(fixture("caps")).payload
        endpoint.receive(frame(130, payload, sequence=4, request=3), 2)
        self.assertFault(endpoint, "sequence")

    def test_handshake_old_tail_is_bounded_and_cannot_succeed(self):
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        send(endpoint)
        endpoint.receive(b"\x03a\0", 1)
        stale = frame(129, w.decode_frame(fixture("hello_ack")).payload, nonce=NONCE + 1, sequence=1, request=1)
        endpoint.receive(stale, 2)
        self.assertEqual(endpoint.state, e.State.HELLO)
        endpoint.receive(fixture("hello_ack"), 3)
        self.assertEqual(endpoint.state, e.State.STOPPED)
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        for _ in range(65):
            endpoint.receive(b"\0", 0)
        self.assertFault(endpoint, "handshake_limit")
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        for _ in range(8):
            endpoint.receive(b"x" * 543 + b"\0", 0)
        self.assertFault(endpoint, "handshake_limit")

    def test_bounded_api_input_and_immutable_events(self):
        endpoint = ready()
        endpoint.receive(b"x" * 545, 1)
        self.assertFault(endpoint, "input_limit")
        endpoint = ready()
        event, = endpoint.receive(data(endpoint), 1)
        with self.assertRaises(FrozenInstanceError):
            event.sequence = 99
        with self.assertRaises(FrozenInstanceError):
            event.block.first_index = 99
        endpoint = ready()
        for _ in range(100):
            events = endpoint.receive(b"\0" * 544, 0)
            self.assertLessEqual(len(events), e.MAX_EVENTS)
            self.assertEqual(endpoint.buffered_bytes, 0)

    def test_recovery_isolation_just_before_equal_after_and_failure(self):
        for when in (1999, 2000, 2001):
            endpoint = ready()
            endpoint.transport_fault(0)
            self.assertEqual(endpoint.next_write(0), ())
            events = endpoint.isolation_complete(NONCE + 1, when)
            if when < 2000:
                self.assertEqual(endpoint.state, e.State.HELLO)
                self.assertEqual(endpoint.ordinary.deadline_ms, 3999)
                self.assertEqual(endpoint.next_write(when)[0].data[:1], b"\0")
            else:
                self.assertEqual(endpoint.state, e.State.RESET_REQUIRED)
                self.assertIsInstance(events[0], e.ResetRequired)
                self.assertEqual(endpoint.next_write(when), ())
        endpoint = ready()
        endpoint.transport_fault(0)
        endpoint.isolation_failed(1)
        self.assertEqual(endpoint.state, e.State.RESET_REQUIRED)
        endpoint.reset_confirmed(NONCE + 1, 2)
        self.assertEqual(endpoint.state, e.State.HELLO)

    def test_recovery_deadline_not_extended_by_calls_or_old_callbacks(self):
        endpoint = e.Endpoint()
        endpoint.hello(NONCE, 0)
        offer, = endpoint.next_write(0)
        endpoint.transport_fault(0)
        endpoint.observe_write(offer.token, len(offer.data), 1000)
        endpoint.recover(1500)
        endpoint.receive(fixture("hello_ack"), 1999)
        endpoint.tick(2000)
        self.assertEqual(endpoint.state, e.State.RESET_REQUIRED)
        self.assertEqual(endpoint.last_recovery.ordinary.accepted_bytes, 0)

    def test_recovery_hello_budget_includes_resync_delimiter(self):
        endpoint = ready()
        endpoint.transport_fault(0)
        endpoint.isolation_complete(NONCE + 1, 100)
        offer, = endpoint.next_write(100)
        endpoint.observe_write(offer.token, 1, 100)
        endpoint.tick(2100)
        self.assertFault(endpoint, "request_timeout")
        self.assertEqual(endpoint.last_recovery.ordinary.accepted_bytes, 1)
        self.assertEqual(endpoint.last_recovery.ordinary.frame_accepted_bytes, 0)

    def test_old_tail_after_timeout_never_revives_stream(self):
        endpoint = ready()
        endpoint.stop(0)
        send(endpoint)
        late = end(endpoint)
        endpoint.tick(1000)
        endpoint.receive(late, 1001)
        self.assertIsNone(endpoint.terminal)
        self.assertEqual(endpoint.state, e.State.RECOVERING)
        endpoint.isolation_complete(NONCE + 1, 1002)
        send(endpoint, 1002)
        endpoint.receive(late, 1003)
        self.assertEqual(endpoint.state, e.State.HELLO)
        ack_payload = w.decode_frame(fixture("hello_ack")).payload
        endpoint.receive(frame(129, ack_payload, nonce=NONCE + 1, sequence=1, request=1), 1004)
        self.assertEqual(endpoint.state, e.State.STOPPED)
        self.assertEqual(endpoint.received_end, 0)
        self.assertEqual(endpoint.last_recovery.first_fault.code, "data_timeout")

    def test_clock_values_rejected_consistently_without_mutation(self):
        methods = [lambda x, t: x.tick(t), lambda x, t: x.receive(b"", t),
                   lambda x, t: x.next_write(t), lambda x, t: x.observe_write(1, 0, t),
                   lambda x, t: x.start(t), lambda x, t: x.stop(t), lambda x, t: x.get_caps(t),
                   lambda x, t: x.recover(t), lambda x, t: x.transport_fault(t),
                   lambda x, t: x.adapter_fault("storage", t), lambda x, t: x.plan_start(t),
                   lambda x, t: x.cancel_start_plan(t), lambda x, t: x.hello(NONCE + 1, t),
                   lambda x, t: x.isolation_complete(NONCE + 1, t),
                   lambda x, t: x.isolation_failed(t), lambda x, t: x.reset_confirmed(NONCE + 1, t)]
        for method in methods:
            for bad in (True, 0.0, math.inf, math.nan, -1, 9, e.MAX_TIME_MS + 1, "10", None):
                endpoint = ready()
                endpoint.tick(10)
                before = (endpoint.state, endpoint._now, endpoint.ordinary, endpoint.stop_pending)
                with self.subTest(method=method, bad=bad), self.assertRaises(ValueError):
                    method(endpoint, bad)
                self.assertEqual((endpoint.state, endpoint._now, endpoint.ordinary, endpoint.stop_pending), before)

    def test_counter_reserve_and_no_wrap(self):
        for counter in ("_next_tx", "_next_rx", "_next_request"):
            endpoint = ready(False)
            setattr(endpoint, counter, w.COUNTER_RESERVE)
            self.assertIsInstance(endpoint.start(0)[0], e.Refused)
            self.assertIsInstance(endpoint.plan_start(0)[0], e.Refused)
        endpoint = ready()
        endpoint._next_tx = endpoint._next_request = w.U32_MAX
        pending, = endpoint.stop(0)
        self.assertEqual(pending.sequence, w.U32_MAX)
        send(endpoint)
        self.assertEqual(endpoint._next_tx, w.U32_MAX + 1)
        self.assertIsInstance(endpoint.stop(0)[0], e.Refused)

    def test_reserved_data_tail_is_finite_and_duplicate_does_not_consume(self):
        endpoint = ready()
        endpoint._next_rx = w.COUNTER_RESERVE
        for index in range(32):
            wire = data(endpoint)
            endpoint.receive(wire, index)
            endpoint.receive(wire, index)
        self.assertEqual(endpoint._tail_data, 32)
        self.assertEqual(endpoint.received_end, 4096)
        endpoint.receive(data(endpoint), 33)
        self.assertFault(endpoint, "tail_limit")
        self.assertEqual(endpoint.received_end, 4096)

    def test_counter_notice_and_limit_terminal_require_fresh_hello(self):
        endpoint = ready()
        endpoint._next_rx = w.COUNTER_RESERVE - 1
        events = endpoint.receive(data(endpoint), 0)
        self.assertIsInstance(events[-1], e.StopRequired)
        endpoint.stop(0)
        send(endpoint)
        endpoint.receive(end(endpoint, reason=4), 1)
        endpoint.receive(ack(endpoint), 2)
        self.assertEqual(endpoint.state, e.State.NEEDS_HELLO)
        self.assertEqual(endpoint.first_fault.code, "device_4")
        self.assertIsInstance(endpoint.start(2)[0], e.Refused)

    def test_u64_exclusive_end_no_reuse_or_wrap(self):
        endpoint = ready()
        endpoint.received_end = w.U64_MAX - 128
        endpoint.receive(data(endpoint), 0)
        self.assertEqual(endpoint.received_end, w.U64_MAX)
        endpoint.receive(data(endpoint, samples=(1,)), 1)
        self.assertFault(endpoint, "overflow")
        self.assertEqual(endpoint.received_end, w.U64_MAX)

    def test_every_request_uses_admission_deadline(self):
        for kind in ("caps", "start", "stop"):
            for time in (1999, 2000, 2001):
                endpoint = ready(False) if kind != "stop" else ready()
                if kind == "caps":
                    endpoint.get_caps(0)
                    send(endpoint)
                    reply = frame(130, w.decode_frame(fixture("caps")).payload, sequence=3, request=3)
                elif kind == "start":
                    endpoint.start(0)
                    send(endpoint)
                    reply = fixture("start_ack")
                else:
                    endpoint.stop(0)
                    send(endpoint)
                    endpoint.receive(end(endpoint), 0)
                    reply = ack(endpoint)
                endpoint.receive(reply, time)
                if time < 2000:
                    self.assertNotEqual(endpoint.state, e.State.RECOVERING)
                else:
                    self.assertFault(endpoint, "stop_timeout" if kind == "stop" else "request_timeout")

    def test_plan_cannot_stop_or_accept_start_ack_before_admission(self):
        endpoint = ready(False)
        plan, = endpoint.plan_start(0)
        self.assertIsNone(endpoint.generation)
        self.assertIsNone(endpoint.ordinary)
        self.assertIsInstance(endpoint.stop(0)[0], e.Refused)
        endpoint.receive(fixture("start_ack"), 0)
        self.assertFault(endpoint, "request")
        self.assertIsNone(endpoint.generation)
        self.assertIsNone(endpoint._plan)

    def test_slots_cannot_replace_pending_identity_or_deadline(self):
        endpoint = ready(False)
        original, = endpoint.start(0, action_id=55)
        endpoint.stop(500, action_id=66)
        stop = endpoint.stop_pending
        for action in (endpoint.start, endpoint.get_caps, endpoint.stop):
            self.assertIsInstance(action(1000)[0], e.Refused)
        self.assertIs(endpoint.ordinary, original)
        self.assertIs(endpoint.stop_pending, stop)
        self.assertEqual(endpoint.next_deadline_ms, 2000)

    def test_first_cause_and_later_timeout_trigger_are_both_retained(self):
        endpoint = ready()
        endpoint.adapter_fault("storage", 0)
        endpoint.stop(0, 456)
        send(endpoint)
        endpoint.receive(end(endpoint), 1)
        events = endpoint.tick(2000)
        recovery = next(item for item in events if isinstance(item, e.RecoveryRequired))
        self.assertEqual(recovery.trigger, "stop_timeout")
        self.assertEqual(recovery.snapshot.first_fault.code, "storage")
        self.assertEqual(recovery.snapshot.stop.action_id, 456)
        self.assertIsNotNone(recovery.snapshot.terminal)

    def test_valid_bytes_at_every_fragment_boundary(self):
        for split in range(1, len(fixture("final_data"))):
            endpoint = ready()
            raw = fixture("final_data")
            self.assertEqual(endpoint.receive(raw[:split], 1), ())
            events = endpoint.receive(raw[split:], 2)
            self.assertEqual(len(events), 1)
            self.assertIsInstance(events[0], e.ValidatedData)
            self.assertEqual(endpoint.received_end, 4)
        endpoint = ready()
        raw = data(endpoint) + data(endpoint, first=128, sequence=5)
        events = endpoint.receive(raw[:544], 1) + endpoint.receive(raw[544:], 2)
        self.assertEqual(sum(isinstance(x, e.ValidatedData) for x in events), 2)
        self.assertEqual(endpoint.received_end, 256)

    def test_offer_counter_exhaustion_does_not_wrap(self):
        endpoint = ready(False)
        endpoint.start(0)
        endpoint._offer_token = w.U64_MAX
        endpoint.next_write(0)
        self.assertFault(endpoint, "offer_counter")
        self.assertEqual(endpoint.last_recovery.ordinary.accepted_bytes, 0)

    def test_receive_u32_max_can_duplicate_but_cannot_wrap(self):
        endpoint = ready()
        endpoint._next_rx = w.U32_MAX
        raw = data(endpoint)
        endpoint.receive(raw, 0)
        self.assertEqual(endpoint._next_rx, w.U32_MAX + 1)
        self.assertEqual(endpoint.receive(raw, 1), ())
        endpoint.receive(data(endpoint, sequence=1), 2)
        self.assertFault(endpoint, "sequence")

    def test_last_representable_request_deadline_can_expire(self):
        endpoint = e.Endpoint()
        pending, = endpoint.hello(NONCE, e.MAX_TIME_MS - 2000)
        self.assertEqual(pending.deadline_ms, e.MAX_TIME_MS)
        self.assertEqual(endpoint.tick(e.MAX_TIME_MS - 1), ())
        events = endpoint.tick(e.MAX_TIME_MS)
        self.assertEqual(endpoint.state, e.State.RESET_REQUIRED)
        self.assertEqual(endpoint.first_fault.code, "request_timeout")
        self.assertEqual(endpoint.last_recovery.ordinary, pending)
        self.assertEqual(events[-1].code, "cleanup_clock_limit")

    def test_request_deadline_creation_overflow_allocates_nothing(self):
        endpoint = e.Endpoint()
        refused, = endpoint.hello(NONCE, e.MAX_TIME_MS - 1999)
        self.assertEqual(refused.code, "deadline_overflow")
        self.assertEqual(endpoint.state, e.State.NO_SESSION)
        self.assertIsNone(endpoint.nonce)
        self.assertIsNone(endpoint.ordinary)
        endpoint = ready(False)
        plan, = endpoint.plan_start(e.MAX_TIME_MS - 1999)
        refused, = endpoint.start(e.MAX_TIME_MS - 1999, plan=plan)
        self.assertEqual(refused.code, "deadline_overflow")
        self.assertIs(endpoint._plan, plan)
        self.assertIsNone(endpoint.generation)
        self.assertIsNone(endpoint.ordinary)

    def test_cleanup_last_deadline_and_overflow(self):
        endpoint = ready(False)
        endpoint.transport_fault(e.MAX_TIME_MS - 2000)
        self.assertEqual(endpoint.next_deadline_ms, e.MAX_TIME_MS)
        endpoint.tick(e.MAX_TIME_MS)
        self.assertEqual(endpoint.state, e.State.RESET_REQUIRED)
        endpoint = ready(False)
        events = endpoint.transport_fault(e.MAX_TIME_MS - 1999)
        self.assertEqual(endpoint.state, e.State.RESET_REQUIRED)
        self.assertEqual(events[-1].code, "cleanup_clock_limit")
        self.assertIsNone(endpoint.next_deadline_ms)

    def test_partial_and_data_timer_creation_overflow_fail_closed(self):
        endpoint = ready(False)
        endpoint.receive(b"x", e.MAX_TIME_MS - 999)
        self.assertEqual(endpoint.state, e.State.RESET_REQUIRED)
        self.assertEqual(endpoint.first_fault.code, "clock_limit")
        endpoint = ready(False)
        endpoint.start(e.MAX_TIME_MS - 2000)
        send(endpoint, e.MAX_TIME_MS - 2000)
        endpoint.receive(fixture("start_ack"), e.MAX_TIME_MS - 1)
        self.assertEqual(endpoint.state, e.State.RESET_REQUIRED)
        self.assertEqual(endpoint.first_fault.code, "clock_limit")

    def _two_transmitted_pending_requests(self):
        endpoint = ready(False)
        endpoint.start(0, action_id=301)
        send(endpoint)
        endpoint.stop(0, action_id=402)
        send(endpoint)
        self.assertTrue(endpoint.ordinary.complete)
        self.assertTrue(endpoint.stop_pending.complete)
        return endpoint

    def _validated_error_causality(self, kind, request_id):
        endpoint = self._two_transmitted_pending_requests()
        ordinary, stop = endpoint.ordinary, endpoint.stop_pending
        expected = ordinary if kind == 8 else stop
        raw = frame(255, struct.pack("<BBHI", kind, 1, 0, 3), sequence=3, request=request_id)
        events = ()
        for offset in range(0, len(raw), 5):
            events += endpoint.receive(raw[offset:offset + 5], 1)
        self.assertFault(endpoint, "device_error_1")
        self.assertIs(getattr(endpoint.first_fault, "causal_request", None), expected)
        self.assertIs(getattr(endpoint.last_recovery, "causal_request", None), expected)
        self.assertIs(endpoint.first_fault.ordinary, ordinary)
        self.assertIs(endpoint.first_fault.stop, stop)
        self.assertIs(endpoint.last_recovery.ordinary, ordinary)
        self.assertIs(endpoint.last_recovery.stop, stop)
        self.assertEqual(len(events), 2)
        self.assertIs(events[0].first.causal_request, expected)
        self.assertIs(events[1].snapshot.causal_request, expected)
        self.assertIsNone(endpoint.ordinary)
        self.assertIsNone(endpoint.stop_pending)
        return endpoint, expected

    def test_valid_start_error_preserves_exact_causal_request_with_both_lanes(self):
        self._validated_error_causality(8, 3)

    def test_valid_stop_error_preserves_exact_causal_request_with_both_lanes(self):
        self._validated_error_causality(5, 4)

    def test_error_causality_does_not_replace_an_earlier_first_cause(self):
        endpoint = self._two_transmitted_pending_requests()
        endpoint.adapter_fault("storage", 0)
        original = endpoint.first_fault
        rejected = endpoint.stop_pending
        events = endpoint.receive(frame(255, struct.pack("<BBHI", 5, 1, 0, 3),
                                        sequence=3, request=4), 1)
        self.assertFault(endpoint, "storage")
        self.assertIs(endpoint.first_fault, original)
        self.assertIsNone(getattr(original, "causal_request", None))
        self.assertIs(getattr(endpoint.last_recovery, "causal_request", None), rejected)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trigger, "device_error_1")
        self.assertIs(events[0].snapshot.first_fault, original)
        self.assertIs(events[0].snapshot.causal_request, rejected)

    def test_error_causal_request_survives_cleanup_reset_and_fresh_hello(self):
        endpoint, rejected = self._validated_error_causality(5, 4)
        snapshot = endpoint.last_recovery
        events = endpoint.tick(2001)
        self.assertEqual(endpoint.state, e.State.RESET_REQUIRED)
        self.assertIs(events[0].snapshot, snapshot)
        self.assertIs(events[0].snapshot.causal_request, rejected)
        endpoint.reset_confirmed(NONCE + 1, 2002)
        self.assertIsNone(endpoint.first_fault)
        self.assertIs(endpoint.last_recovery, snapshot)
        self.assertIs(snapshot.first_fault.causal_request, rejected)
        self.assertIs(snapshot.causal_request, rejected)
        with self.assertRaises(FrozenInstanceError):
            snapshot.causal_request = None
        with self.assertRaises(FrozenInstanceError):
            snapshot.first_fault.causal_request = None
        with self.assertRaises(FrozenInstanceError):
            rejected.request_id = 99

    def test_invalid_error_identity_type_generation_never_identifies_a_cause(self):
        cases = ((8, 99, 3, 3, "request"), (5, 3, 3, 3, "error_correlation"),
                 (8, 4, 3, 3, "error_correlation"), (8, 3, 4, 3, "generation"),
                 (5, 4, 4, 3, "generation"), (8, 3, 3, 4, "sequence"))
        for kind, request, generation, sequence, expected_code in cases:
            with self.subTest(kind=kind, request=request, generation=generation, sequence=sequence):
                endpoint = self._two_transmitted_pending_requests()
                raw = frame(255, struct.pack("<BBHI", kind, 1, 0, generation),
                            sequence=sequence, request=request)
                endpoint.receive(raw, 1)
                self.assertFault(endpoint, expected_code)
                self.assertIsNone(endpoint.first_fault.causal_request)
                self.assertIsNone(endpoint.last_recovery.causal_request)

    def test_incomplete_or_expired_request_error_never_identifies_a_cause(self):
        endpoint = ready(False)
        endpoint.start(0)
        send(endpoint)
        endpoint.stop(0)
        offer, = endpoint.next_write(0)
        endpoint.observe_write(offer.token, 1, 0)
        endpoint.receive(frame(255, struct.pack("<BBHI", 5, 1, 0, 3), sequence=3, request=4), 1)
        self.assertFault(endpoint, "request")
        self.assertIsNone(endpoint.first_fault.causal_request)
        self.assertIsNone(endpoint.last_recovery.causal_request)
        endpoint = self._two_transmitted_pending_requests()
        endpoint.receive(frame(255, struct.pack("<BBHI", 8, 1, 0, 3), sequence=3, request=3), 2000)
        self.assertFault(endpoint, "request_timeout")
        self.assertIsNone(endpoint.first_fault.causal_request)
        self.assertIsNone(endpoint.last_recovery.causal_request)

    def test_unknown_read_or_crc_cause_stays_ambiguous_with_two_lanes(self):
        for trigger in ("transport", "crc"):
            endpoint = self._two_transmitted_pending_requests()
            if trigger == "transport":
                endpoint.transport_fault(1)
            else:
                raw = frame(255, struct.pack("<BBHI", 8, 1, 0, 3), sequence=3, request=3)
                decoded = bytearray(w.cobs_decode(raw[:-1]))
                decoded[-1] ^= 1
                endpoint.receive(w.cobs_encode(bytes(decoded)) + b"\0", 1)
            self.assertFault(endpoint, trigger)
            self.assertIsNone(endpoint.first_fault.causal_request)
            self.assertIsNone(endpoint.last_recovery.causal_request)
            self.assertIsNotNone(endpoint.last_recovery.ordinary)
            self.assertIsNotNone(endpoint.last_recovery.stop)

    def test_old_nonce_error_is_ignored_without_causal_attribution(self):
        endpoint = self._two_transmitted_pending_requests()
        raw = frame(255, struct.pack("<BBHI", 8, 1, 0, 3), nonce=NONCE + 1,
                    sequence=3, request=3)
        self.assertEqual(endpoint.receive(raw, 1), ())
        self.assertIsNone(endpoint.first_fault)
        self.assertIsNone(endpoint.last_recovery)
        endpoint.tick(2000)
        self.assertFault(endpoint, "request_timeout")
        self.assertIsNone(endpoint.first_fault.causal_request)
        self.assertIsNone(endpoint.last_recovery.causal_request)

    def test_fixed_sanitized_diagnostics_no_transport_details(self):
        endpoint = ready()
        secret = b"private transport detail" * 30
        events = endpoint.receive(secret, 0)
        self.assertNotIn("private", repr(events))
        self.assertLess(len(repr(events)), 3000)
        self.assertEqual(endpoint.first_fault.code, "input_limit")


if __name__ == "__main__":
    unittest.main()

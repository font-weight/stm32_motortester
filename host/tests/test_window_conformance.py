"""Независимые синтетические проверки HOST-004; это не вердикт Critic-B.

Входные payload/COBS/CRC построены отдельно от производственного кода.
Проверка не доказывает сеанс, firmware, USB, Windows или аппаратный захват.
"""
from dataclasses import FrozenInstanceError
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import tracemalloc
import unittest

from motortester.wire import Frame, StreamDecoder
from motortester.window import ExpectedContext, WindowCode as C, WindowError, WindowReceiver


FIXTURES = Path(__file__).parent / "fixtures" / "window"
_spec = importlib.util.spec_from_file_location("window_independent_reference", FIXTURES / "reference.py")
ref = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ref)
VECTORS = json.loads((FIXTURES / "vectors.json").read_text(encoding="utf-8"))


def context(**changes):
    values = dict(session=ref.SESSION, generation=100, config_id=200,
                  acquisition_mode=1, source=0, edge=0, level_code=2048,
                  pretrigger_samples=0, profile=1)
    values.update(changes)
    return ExpectedContext(**values)


class Assertions(unittest.TestCase):
    def frame(self, kind, payload, **header):
        # Каждый обычный вход проходит настоящий HOST-002 parser.
        result = StreamDecoder().feed(ref.packet(kind, payload, **header), 0)
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(len(result.frames), 1)
        return result.frames[0]

    def feed(self, receiver, kind, payload, now=0, **header):
        return receiver.feed(self.frame(kind, payload, **header), now)

    def start(self, now=0, ctx=None, **metadata):
        receiver = WindowReceiver(context() if ctx is None else ctx)
        self.assertIsNone(self.feed(receiver, 144, ref.begin(**metadata), now))
        return receiver

    def incomplete(self, result, reason, accepted=(), offset=0, begin=True):
        self.assertIsNotNone(result)
        self.assertFalse(result.complete)
        self.assertIs(result.reason, reason)
        self.assertIsNone(result.samples)
        self.assertFalse(result.semantic_checks_complete)
        self.assertEqual(tuple(value for fragment in result.fragments for value in fragment.samples),
                         tuple(accepted))
        self.assertEqual(result.missing_ranges, ((offset, 2048),) if offset < 2048 else ())
        self.assertEqual(result.begin is not None, begin)
        self.assertEqual(result.protocol_source_sha, ref.WIRE_SHA)
        self.assertEqual(result.validation_scope, "draft_window_receive")
        self.assertIs(type(result.fragments), tuple)
        self.assertIs(type(result.missing_ranges), tuple)
        self.assertIs(type(result.deferred_checks), tuple)
        self.assertIs(type(result.metadata_issues), tuple)

    def reject(self, code, function, *args):
        with self.assertRaises(WindowError) as caught:
            function(*args)
        self.assertIs(caught.exception.code, code)

    def all_samples(self, receiver, samples=None, now=1):
        samples = tuple(range(2048)) if samples is None else samples
        for offset in range(0, 2048, 250):
            self.assertIsNone(self.feed(receiver, 145, ref.data(offset, samples[offset:offset + 250]), now))
        return samples

    def complete(self, receiver, samples=None, now=1):
        samples = self.all_samples(receiver, samples, now)
        checksum = ref.crc32(ref.codes_bytes(samples))
        result = self.feed(receiver, 146, ref.end(checksum), now)
        self.assertTrue(result.complete)
        self.assertIs(result.reason, C.COMPLETE)
        self.assertEqual(result.samples, samples)
        self.assertEqual(result.samples_crc32, checksum)
        self.assertEqual(result.missing_ranges, ())
        return result


class IndependentFixtureConformance(Assertions):
    def test_reference_known_crc_and_frozen_fixture_regeneration(self):
        self.assertEqual(ref.crc32(b"123456789"), 0xCBF43926)
        self.assertEqual(ref.crc32(b""), 0)
        stream, samples, manifest = ref.build_fixtures()
        self.assertEqual(stream, (FIXTURES / "windows.bin").read_bytes())
        self.assertEqual(samples, (FIXTURES / "samples.bin").read_bytes())
        self.assertEqual(manifest, VECTORS)
        self.assertEqual(len(VECTORS["vectors"]), 20)
        self.assertEqual(len(samples), 20 * 4096)

    def test_twenty_windows_through_fragmented_and_coalesced_host002(self):
        stream = (FIXTURES / "windows.bin").read_bytes()
        raw_codes = (FIXTURES / "samples.bin").read_bytes()
        retained = []
        for vector in VECTORS["vectors"]:
            with self.subTest(window=vector["index"]):
                begin = vector["stream_offset"]
                wire = stream[begin:begin + vector["stream_length"]]
                expected_raw = raw_codes[vector["samples_offset"]:vector["samples_offset"] + 4096]
                expected = tuple(int.from_bytes(expected_raw[i:i + 2], "little") for i in range(0, 4096, 2))
                self.assertEqual(hashlib.sha256(wire).hexdigest(), vector["stream_sha256"])
                self.assertEqual(hashlib.sha256(expected_raw).hexdigest(), vector["samples_sha256"])
                self.assertEqual(ref.crc32(expected_raw), vector["samples_crc32"])
                decoder = StreamDecoder()
                receiver = WindowReceiver(ExpectedContext(**vector["context"]))
                terminals, frames = [], []
                chunks = (1, 7, 4096, 13, 512, 29, 2000)
                cursor, turn = 0, 0
                while cursor < len(wire):
                    size = chunks[turn % len(chunks)]
                    batch = decoder.feed(wire[cursor:cursor + size], turn)
                    self.assertEqual(batch.diagnostics, ())
                    frames.extend(batch.frames)
                    for frame in batch.frames:
                        result = receiver.feed(frame, turn)
                        if result is not None:
                            terminals.append(result)
                    cursor += size
                    turn += 1
                self.assertEqual(len(terminals), 1)
                result = terminals[0]
                self.assertTrue(result.complete)
                self.assertEqual(result.samples, expected)
                self.assertEqual(result.samples_crc32, vector["samples_crc32"])
                self.assertEqual(tuple(len(block.samples) for block in result.fragments),
                                 tuple(vector["block_counts"]))
                self.assertEqual(result.deferred_checks, tuple(vector["deferred_checks"]))
                self.assertEqual(result.semantic_checks_complete, not vector["deferred_checks"])
                self.assertEqual(result.begin.external_edge_id, vector["begin"]["external_edge_id"])
                self.assertTrue(result.rail_observed)
                self.assertEqual(result.validation_scope, "draft_window_receive")
                self.assertEqual(result.protocol_source_sha, ref.WIRE_SHA)
                self.assertEqual(result.missing_ranges, ())
                self.assertIsNone(receiver.feed(frames[-1], turn))
                self.assertIsNone(receiver.disconnect(turn))
                self.assertIsNone(receiver.reset())
                retained.append((result, expected))
        for result, expected in retained:
            self.assertEqual(result.samples, expected)

    def test_every_byte_boundary_of_begin_and_maximum_data(self):
        for kind, payload in ((144, ref.begin()), (145, ref.data(0, tuple(range(250))))):
            wire = ref.packet(kind, payload)
            for split in range(1, len(wire)):
                with self.subTest(kind=kind, split=split):
                    decoder = StreamDecoder()
                    left = decoder.feed(wire[:split], 0)
                    right = decoder.feed(wire[split:], 1)
                    self.assertEqual(left.frames, ())
                    self.assertEqual(left.diagnostics + right.diagnostics, ())
                    self.assertEqual(len(right.frames), 1)
                    self.assertEqual(right.frames[0].payload, payload)
                    receiver = WindowReceiver(context()) if kind == 144 else self.start()
                    self.assertIsNone(receiver.feed(right.frames[0], 1))
                    result = receiver.reset()
                    self.incomplete(result, C.RESET, range(250) if kind == 145 else (),
                                    250 if kind == 145 else 0)


class PayloadConformance(Assertions):
    def test_all_other_payload_lengths_up_to_512_are_rejected(self):
        for kind, correct in ((144, ref.begin()), (145, ref.data(0, (10,))),
                              (146, ref.end(0)), (147, ref.abort())):
            for length in range(513):
                if length == len(correct):
                    continue
                with self.subTest(kind=kind, length=length):
                    payload = (correct + b"\0" * 512)[:length]
                    receiver = self.start() if kind != 144 else WindowReceiver(context())
                    self.incomplete(self.feed(receiver, kind, payload), C.LENGTH,
                                    begin=kind != 144)

    def test_exact_payload_lengths(self):
        originals = ((144, ref.begin()), (145, ref.data(0, (10,))),
                     (146, ref.end(0)), (147, ref.abort()))
        for kind, payload in originals:
            for malformed in (b"", payload[:-1], payload + b"\0"):
                with self.subTest(kind=kind, length=len(malformed)):
                    receiver = self.start() if kind != 144 else WindowReceiver(context())
                    result = self.feed(receiver, kind, malformed)
                    self.incomplete(result, C.LENGTH, begin=kind != 144)

    def test_begin_invalid_fields(self):
        cases = (("profile", 0, C.CONTEXT), ("profile", 2, C.CONTEXT),
                 ("sample_count", 0, C.COUNT), ("sample_count", 2047, C.COUNT),
                 ("sample_count", 2049, C.COUNT), ("sample_count", 65535, C.COUNT),
                 ("trigger_kind", 3, C.ENUM), ("source", 2, C.ENUM),
                 ("edge", 2, C.ENUM), ("channel", 1, C.BOUNDS),
                 ("level_code", 0, C.BOUNDS), ("level_code", 4095, C.BOUNDS),
                 ("quality_flags", 8, C.RESERVED), ("quality_flags", 65535, C.RESERVED),
                 ("trigger_index", 1, C.BOUNDS), ("trigger_index", 65535, C.BOUNDS),
                 ("pretrigger_samples", 1, C.CONTEXT),
                 ("timer_clock_hz", 0, C.CONTEXT), ("timer_clock_hz", 72000001, C.CONTEXT),
                 ("timer_psc", 70, C.CONTEXT), ("timer_arr", 18, C.CONTEXT),
                 ("external_edge_id", 1, C.CONTEXT), ("window_id", 0, C.BOUNDS))
        for field, value, reason in cases:
            with self.subTest(field=field, value=value):
                result = self.feed(WindowReceiver(context()), 144, ref.begin(**{field: value}))
                self.incomplete(result, reason, begin=False)

    def test_expected_begin_context_rejected_before_sample_allocation(self):
        changes = (dict(generation=101), dict(config_id=201), dict(pretrigger_samples=512, trigger_index=512),
                   dict(source=1, trigger_kind=1, external_edge_id=1), dict(edge=1), dict(level_code=100))
        for change in changes:
            with self.subTest(change=change):
                result = self.feed(WindowReceiver(context()), 144, ref.begin(**change))
                self.incomplete(result, C.IDENTITY if "generation" in change else C.CONTEXT, begin=False)

    def test_window_event_type_request_session(self):
        for kind, header, reason in ((129, {}, C.TYPE), (1, {}, C.TYPE), (255, {}, C.TYPE),
                                     (144, {"request_id": 1}, C.REQUEST),
                                     (144, {"session": ref.SESSION + 1}, C.SESSION)):
            with self.subTest(kind=kind, header=header):
                result = self.feed(WindowReceiver(context()), kind, ref.begin(), **header)
                self.incomplete(result, reason, begin=False)

    def test_kind_source_auto_index_and_flag_consistency(self):
        cases = ((context(), dict(trigger_kind=1), C.CONTEXT),
                 (context(), dict(trigger_kind=2, trigger_index=65535, quality_flags=3), C.CONTEXT),
                 (context(), dict(quality_flags=3), C.QUALITY),
                 (context(acquisition_mode=0), dict(trigger_kind=2, trigger_index=65535), C.QUALITY),
                 (context(acquisition_mode=0), dict(trigger_kind=2, trigger_index=0, quality_flags=3), C.BOUNDS),
                 (context(acquisition_mode=0), dict(trigger_kind=2, trigger_index=65535,
                                                   quality_flags=3, external_edge_id=1), C.CONTEXT),
                 (context(source=1), dict(source=1, trigger_kind=1, external_edge_id=0), C.CONTEXT))
        for ctx, metadata, reason in cases:
            with self.subTest(metadata=metadata, ctx=ctx):
                self.incomplete(self.feed(WindowReceiver(ctx), 144, ref.begin(**metadata)), reason, begin=False)

    def test_data_count_limits_exact_length_and_bounds(self):
        cases = ((ref.data(0, ()), C.BOUNDS), (ref.data(0, (1,), count=0), C.BOUNDS),
                 (ref.data(0, (1,), count=251), C.BOUNDS),
                 (ref.data(0, (1,), count=2), C.LENGTH),
                 (ref.data(2048, (1,)), C.BOUNDS), (ref.data(2047, (1, 2)), C.BOUNDS),
                 (ref.data(65535, (1,)), C.BOUNDS), (ref.data(0, (4096,)), C.ADC),
                 (ref.data(0, (65535,)), C.ADC))
        for payload, reason in cases:
            with self.subTest(payload=payload.hex()):
                self.incomplete(self.feed(self.start(), 145, payload), reason)

    def test_end_and_abort_reserved_enum_and_count(self):
        for payload, reason in ((ref.end(0, reserved=1), C.RESERVED),
                                 (ref.end(0, total=0), C.COUNT),
                                 (ref.end(0, total=2047), C.COUNT)):
            self.incomplete(self.feed(self.start(), 146, payload), reason)
        for payload, reason in ((ref.abort(reserved=1), C.RESERVED),
                                 (ref.abort(reason=0), C.ENUM), (ref.abort(reason=7), C.ENUM),
                                 (ref.abort(reason=65535), C.ENUM)):
            self.incomplete(self.feed(self.start(), 147, payload), reason)

    def test_changed_active_ids_preserve_prior_verified_fragments(self):
        for kind, payload in ((145, ref.data(3, (40,), generation=101)),
                              (145, ref.data(3, (40,), window_id=2)),
                              (146, ref.end(0, generation=101)),
                              (146, ref.end(0, window_id=2)),
                              (147, ref.abort(generation=101)),
                              (147, ref.abort(window_id=2))):
            receiver = self.start()
            self.feed(receiver, 145, ref.data(0, (10, 20, 30)))
            result = self.feed(receiver, kind, payload)
            self.incomplete(result, C.IDENTITY, (10, 20, 30), 3)

    def test_changed_session_during_data_preserves_fragments(self):
        receiver = self.start()
        self.feed(receiver, 145, ref.data(0, (10, 20)))
        result = self.feed(receiver, 145, ref.data(2, (30,)), session=ref.SESSION + 1)
        self.incomplete(result, C.SESSION, (10, 20), 2)

    def test_invalid_adc_in_later_block_never_partially_accepts_that_block(self):
        for samples in ((100, 4096), (65535, 100), (100, 200, 65535)):
            receiver = self.start()
            self.feed(receiver, 145, ref.data(0, (0, 4095)))
            result = self.feed(receiver, 145, ref.data(2, samples))
            self.incomplete(result, C.ADC, (0, 4095), 2)
            self.assertEqual(len(result.fragments), 1)

    def test_valid_context_identifier_and_level_extremes(self):
        ctx = context(session=2**64 - 1, generation=2**32 - 1,
                      config_id=2**32 - 1, level_code=4094)
        receiver = WindowReceiver(ctx)
        payload = ref.begin(generation=2**32 - 1, config_id=2**32 - 1,
                            window_id=2**32 - 1, level_code=4094)
        self.assertIsNone(self.feed(receiver, 144, payload, session=2**64 - 1))
        self.incomplete(receiver.reset(), C.RESET)
        receiver = self.start(ctx=context(generation=2, config_id=1, level_code=1),
                              generation=2, config_id=1, level_code=1)
        self.incomplete(receiver.reset(), C.RESET)


class AssemblyConformance(Assertions):
    def test_exact_any_prior_original_block_is_ignored(self):
        receiver = self.start()
        first = ref.data(0, (0, 10, 20))
        self.assertIsNone(self.feed(receiver, 145, first, 1))
        self.assertIsNone(self.feed(receiver, 145, ref.data(3, (30, 40)), 2))
        for sequence in range(4, 50):
            self.assertIsNone(self.feed(receiver, 145, first, 3, sequence=sequence))
        result = receiver.reset()
        self.incomplete(result, C.RESET, (0, 10, 20, 30, 40), 5)
        self.assertEqual(tuple((block.offset, block.samples) for block in result.fragments),
                         ((0, (0, 10, 20)), (3, (30, 40))))

    def test_gap_conflict_overlap_and_matching_subslice_do_not_overwrite(self):
        for payload, reason in ((ref.data(4, (50,)), C.ORDER),
                                 (ref.data(0, (1, 20, 30)), C.OVERLAP),
                                 (ref.data(1, (20, 30)), C.OVERLAP),
                                 (ref.data(2, (30, 40)), C.OVERLAP),
                                 (ref.data(0, (10, 20)), C.OVERLAP)):
            receiver = self.start()
            self.feed(receiver, 145, ref.data(0, (10, 20, 30)))
            self.incomplete(self.feed(receiver, 145, payload), reason, (10, 20, 30), 3)

    def test_new_begin_during_window_does_not_replace_original(self):
        receiver = self.start()
        self.feed(receiver, 145, ref.data(0, (5, 6)))
        result = self.feed(receiver, 144, ref.begin(window_id=2))
        self.incomplete(result, C.ORDER, (5, 6), 2)
        self.assertEqual(result.begin.window_id, 1)

    def test_data_end_abort_at_idle_receiver_boundary_have_one_incomplete_outcome(self):
        # Ранний ABORT может быть допустимым событием внешнего диспетчера.
        # Здесь проверяется только неверная маршрутизация в idle-приёмник окна.
        for kind, payload in ((145, ref.data(0, (10,))), (146, ref.end(0)), (147, ref.abort())):
            receiver = WindowReceiver(context())
            result = self.feed(receiver, kind, payload)
            self.incomplete(result, C.ORDER, begin=False)
            self.assertIsNone(self.feed(receiver, 144, ref.begin()))
            self.assertIsNone(receiver.reset())

    def test_full_samples_wait_for_end_and_crc(self):
        receiver = self.start()
        samples = self.all_samples(receiver)
        result = self.feed(receiver, 146, ref.end(ref.crc32(ref.codes_bytes(samples)) ^ 1), 1)
        self.incomplete(result, C.CRC, samples, 2048)
        self.assertIsNone(self.feed(receiver, 146, ref.end(ref.crc32(ref.codes_bytes(samples))), 1))

    def test_early_end_is_incomplete_without_zero_fill(self):
        receiver = self.start()
        self.feed(receiver, 145, ref.data(0, (0, 4095, 100)))
        result = self.feed(receiver, 146, ref.end(0))
        self.incomplete(result, C.COUNT, (0, 4095, 100), 3)
        self.assertTrue(result.rail_observed)

    def test_all_abort_reasons_and_disconnect_reset_keep_original_samples(self):
        for action in range(1, 9):
            receiver = self.start()
            self.feed(receiver, 145, ref.data(0, (123, 456)))
            if action <= 6:
                result = self.feed(receiver, 147, ref.abort(reason=action))
                reason = C.ABORT
                self.assertEqual(result.abort_reason, action)
            elif action == 7:
                result, reason = receiver.disconnect(1), C.DISCONNECT
            else:
                result, reason = receiver.reset(), C.RESET
            self.incomplete(result, reason, (123, 456), 2)
            self.assertFalse(result.rail_observed)
            self.assertIsNone(receiver.disconnect(1))
            self.assertIsNone(receiver.reset())
            self.assertIsNone(receiver.reset())
            self.assertEqual(result.fragments[0].samples, (123, 456))

    def test_complete_and_partial_results_remain_immutable_after_next_window(self):
        receiver = self.start()
        self.feed(receiver, 145, ref.data(0, (0, 100, 200)))
        partial = receiver.reset()
        self.assertIsNone(self.feed(receiver, 144, ref.begin(), 0))
        full = self.complete(receiver)
        self.assertEqual(partial.fragments[0].samples, (0, 100, 200))
        for result in (partial, full):
            for target, name, value in ((result, "complete", True),
                                         (result.begin, "window_id", 9),
                                         (result.fragments[0], "samples", ())):
                with self.assertRaises((FrozenInstanceError, AttributeError)):
                    setattr(target, name, value)
            self.assertIs(type(result.fragments), tuple)
            self.assertIs(type(result.fragments[0].samples), tuple)
        with self.assertRaises(TypeError):
            full.samples[0] = 9

    def test_success_terminal_exactly_once(self):
        receiver = self.start()
        result = self.complete(receiver)
        for kind, payload in ((146, ref.end(result.samples_crc32)),
                              (147, ref.abort()), (144, ref.begin(window_id=2)),
                              (145, ref.data(0, (0,)))):
            self.assertIsNone(self.feed(receiver, kind, payload, 2))
        self.assertIsNone(receiver.tick(10000))
        self.assertIsNone(receiver.disconnect(10000))
        self.assertIsNone(receiver.reset())

    def test_auto_external_raw_id_is_preserved_with_explicit_deferral(self):
        for edge_id in (0, 1, 0xFFFFFFFF):
            receiver = self.start(ctx=context(acquisition_mode=0, source=1),
                                  source=1, trigger_kind=1, external_edge_id=edge_id,
                                  quality_flags=6)
            result = self.complete(receiver)
            self.assertEqual(result.begin.external_edge_id, edge_id)
            self.assertIn("AUTO_EXTERNAL_EDGE_ID", result.deferred_checks)
            self.assertFalse(result.semantic_checks_complete)

    def test_rail_observation_and_flag_mismatch_are_separate_from_transfer(self):
        for rail, flagged in ((False, False), (False, True), (True, False), (True, True)):
            receiver = self.start(quality_flags=2 if flagged else 0)
            samples = ((0,) + (200,) * 2047) if rail else (200,) * 2048
            result = self.complete(receiver, samples)
            self.assertEqual(result.rail_observed, rail)
            self.assertEqual(result.begin.quality_flags, 2 if flagged else 0)
            self.assertEqual("RAIL_FLAG_MISMATCH" in result.metadata_issues, rail != flagged)
            self.assertEqual(result.deferred_checks, ())
            self.assertEqual(result.semantic_checks_complete, rail == flagged)

    def test_external_timing_flag_is_preserved_without_invented_iff_rule(self):
        for flags in (2, 6):
            receiver = self.start(ctx=context(source=1), source=1, trigger_kind=1,
                                  external_edge_id=10, quality_flags=flags)
            result = self.complete(receiver)
            self.assertEqual(result.begin.quality_flags, flags)
            self.assertTrue(result.semantic_checks_complete)


class DeadlineConformance(Assertions):
    def test_exact_progress_deadline_and_idle_no_timeout(self):
        receiver = self.start(100)
        self.assertIsNone(receiver.tick(3099))
        self.incomplete(receiver.tick(3100), C.TIMEOUT)
        self.assertIsNone(receiver.tick(99999))
        receiver.reset()
        self.assertIsNone(receiver.tick(2**64 - 1))

    def test_new_samples_advance_deadline_duplicate_and_tick_do_not(self):
        receiver = self.start()
        block = ref.data(0, (1, 2))
        self.assertIsNone(self.feed(receiver, 145, block, 2999))
        self.assertIsNone(receiver.tick(5000))
        self.assertIsNone(self.feed(receiver, 145, block, 5998, sequence=3))
        self.incomplete(receiver.tick(5999), C.TIMEOUT, (1, 2), 2)

    def test_duplicate_flood_never_prevents_deadline(self):
        receiver = self.start()
        block = self.frame(145, ref.data(0, (1,)))
        self.assertIsNone(receiver.feed(block, 1))
        for now in range(2, 3001):
            self.assertIsNone(receiver.feed(block, now))
        self.incomplete(receiver.feed(block, 3001), C.TIMEOUT, (1,), 1)

    def test_valid_event_deadline_precedes_data_end_abort_disconnect(self):
        for kind, payload in ((145, ref.data(0, (1,))), (146, ref.end(0)), (147, ref.abort())):
            self.incomplete(self.feed(self.start(), kind, payload, 3000), C.TIMEOUT)
        self.incomplete(self.start().disconnect(3000), C.TIMEOUT)
        receiver = self.start()
        samples = self.all_samples(receiver, now=1)
        checksum = ref.crc32(ref.codes_bytes(samples))
        self.incomplete(self.feed(receiver, 146, ref.end(checksum), 3001), C.TIMEOUT, samples, 2048)

    def test_well_typed_but_bad_protocol_event_does_not_bypass_expired_deadline(self):
        receiver = self.start()
        self.incomplete(self.feed(receiver, 144, b"", 3000), C.TIMEOUT)

    def test_idle_tick_and_disconnect_do_not_end_waiting_normal_or_single(self):
        for mode in (1, 2):
            receiver = WindowReceiver(context(acquisition_mode=mode))
            self.assertIsNone(receiver.tick(0))
            self.assertIsNone(receiver.tick(9000000))
            self.assertIsNone(receiver.disconnect(9000000))
            self.assertIsNone(self.feed(receiver, 144, ref.begin(), 9000000))
            self.incomplete(receiver.tick(9003000), C.TIMEOUT)

    def test_equal_uint64_max_and_explicit_reset_clock(self):
        now = 2**64 - 1
        receiver = self.start(now)
        self.assertIsNone(receiver.tick(now))
        result = self.complete(receiver, now=now)
        self.assertTrue(result.complete)
        self.reject(C.RANGE, receiver.tick, now + 1)
        self.reject(C.CLOCK_ROLLBACK, receiver.tick, 0)
        self.assertIsNone(receiver.reset())
        self.assertIsNone(receiver.tick(0))


class DefensiveConformance(Assertions):
    def test_context_exact_types_and_ranges(self):
        for field in ("session", "generation", "config_id", "acquisition_mode", "source", "edge",
                      "level_code", "pretrigger_samples", "profile"):
            for value in (True, False, 1.0, "1", None):
                with self.subTest(field=field, value=value):
                    self.reject(C.ARGUMENT, lambda: context(**{field: value}))
        invalid = dict(session=(0, -1, 2**64), generation=(0, 1, -1, 2**32),
                       config_id=(0, -1, 2**32), acquisition_mode=(-1, 3), source=(-1, 2),
                       edge=(-1, 2), level_code=(0, 4095), pretrigger_samples=(1, 2048), profile=(0, 2))
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.reject(C.RANGE, lambda: context(**{field: value}))

    def test_noncontext_and_mutated_context_rejected(self):
        for value in (None, {}, (), 1):
            self.reject(C.ARGUMENT, WindowReceiver, value)
        ctx = context()
        object.__setattr__(ctx, "pretrigger_samples", 65535)
        self.reject(C.RANGE, WindowReceiver, ctx)

    def test_invalid_api_types_leave_active_state_and_time_unchanged(self):
        class IntSubclass(int):
            pass
        class BytesSubclass(bytes):
            def __len__(self):
                raise AssertionError("Тип надо проверить до len")
        good = self.frame(145, ref.data(0, (10, 20)))
        cases = []
        for value in (None, {}, (), 1):
            cases.append((C.ARGUMENT, lambda receiver, value=value: receiver.feed(value, 99999)))
        for value in (True, False, None, "1", 1.0, IntSubclass(1)):
            for name in ("feed", "tick", "disconnect"):
                cases.append((C.ARGUMENT, lambda receiver, value=value, name=name:
                              getattr(receiver, name)(good, value) if name == "feed"
                              else getattr(receiver, name)(value)))
        for value, code in ((-1, C.RANGE), (2**64, C.RANGE), (2**4096, C.RANGE), (9, C.CLOCK_ROLLBACK)):
            for name in ("feed", "tick", "disconnect"):
                cases.append((code, lambda receiver, value=value, name=name:
                              getattr(receiver, name)(good, value) if name == "feed"
                              else getattr(receiver, name)(value)))
        for payload, code in ((bytearray(b"x"), C.ARGUMENT), (BytesSubclass(b"x"), C.ARGUMENT),
                              (b"x" * 513, C.PAYLOAD_SIZE)):
            forged = Frame(145, ref.SESSION, 2, 0, b"")
            object.__setattr__(forged, "payload", payload)
            cases.append((code, lambda receiver, forged=forged: receiver.feed(forged, 99999)))
        for field, value, code in (("session", True, C.ARGUMENT), ("sequence", -1, C.RANGE),
                                    ("request_id", 2**32, C.RANGE), ("message_type", 256, C.RANGE)):
            forged = Frame(145, ref.SESSION, 2, 0, b"")
            object.__setattr__(forged, field, value)
            cases.append((code, lambda receiver, forged=forged: receiver.feed(forged, 99999)))
        missing = object.__new__(Frame)
        cases.append((C.ARGUMENT, lambda receiver: receiver.feed(missing, 99999)))
        for index, (code, call) in enumerate(cases):
            with self.subTest(case=index):
                receiver = self.start(10)
                self.reject(code, call, receiver)
                self.assertIsNone(receiver.feed(good, 11))
                self.incomplete(receiver.reset(), C.RESET, (10, 20), 2)

    def test_huge_payload_rejected_before_copy_or_timeout(self):
        forged = Frame(145, ref.SESSION, 2, 0, b"")
        object.__setattr__(forged, "payload", b"x" * (8 * 1024 * 1024))
        receiver = self.start(10)
        tracemalloc.start()
        try:
            self.reject(C.PAYLOAD_SIZE, receiver.feed, forged, 999999)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 256 * 1024)
        self.assertIsNone(self.feed(receiver, 145, ref.data(0, (1,)), 11))
        self.incomplete(receiver.reset(), C.RESET, (1,), 1)

    def test_maximum_block_metadata_and_duplicate_flood_are_bounded(self):
        receiver = self.start(quality_flags=0)
        blocks = [self.frame(145, ref.data(i, (100,)), sequence=i + 3) for i in range(2048)]
        for block in blocks:
            self.assertIsNone(receiver.feed(block, 0))
        gc.collect()
        tracemalloc.start()
        try:
            for index in range(10000):
                self.assertIsNone(receiver.feed(blocks[index % 2048], 0))
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(current, 128 * 1024)
        self.assertLess(peak, 512 * 1024)
        result = receiver.reset()
        self.incomplete(result, C.RESET, (100,) * 2048, 2048)
        self.assertEqual(len(result.fragments), 2048)


if __name__ == "__main__":
    unittest.main()

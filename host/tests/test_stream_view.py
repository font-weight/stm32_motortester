"""Synthetic normalized blocks only; no device/wire/Qt/disk evidence."""
from dataclasses import dataclass, replace
import random
import struct
import unittest
from types import SimpleNamespace

from motortester.stream_view import (StreamViewError, Viewport, DisplayPoint,
    minmax_envelope, EventConfig, EventDetector, MAX_INDEX, MAX_VIEW_SAMPLES,
    MAX_BLOCK_SAMPLES, MAX_EVENTS, MAX_PAGE, DETECTOR_VERSION, ENVELOPE_VERSION)


@dataclass(frozen=True)
class Identity:
    session_id: str = "synthetic-session"
    generation: int = 1
    config_id: int = 1
    nominal_fs_hz: int = 50000
    origin: str = "synthetic"
    protocol_revision: str | None = None
    firmware_version: str | None = None
    application_version: str | None = None


@dataclass(frozen=True)
class Block:
    identity: object
    first_sample_index: int
    raw_u16le: bytes

    @property
    def sample_count(self):
        return len(self.raw_u16le) // 2

    @property
    def end_sample_index(self):
        return self.first_sample_index + self.sample_count


def block(values, first=0, identity=None):
    return Block(identity or Identity(), first, struct.pack(f"<{len(values)}H", *values))


def config(**kwargs):
    values = dict(threshold_code=100, release_code=40, polarity="above",
                  pre_samples=4, post_samples=3, baseline_samples=2,
                  holdoff_samples=0, max_event_samples=64, rail_low=0, rail_high=4095)
    values.update(kwargs)
    return EventConfig(**values)


class Reader:
    def __init__(self, values, first=0, chunk=8192, identity=None):
        self.identity = identity or Identity()
        self.first_sample_index = first
        self.end_sample_index = first + len(values)
        self.values = values
        self.chunk = chunk
        self.requests = []
        self.yielded = 0

    def iter_raw(self, start_sample_index, end_sample_index, chunk_samples=8192):
        self.requests.append((start_sample_index, end_sample_index, chunk_samples))
        for start in range(start_sample_index, end_sample_index, min(self.chunk, chunk_samples)):
            end = min(start + self.chunk, start + chunk_samples, end_sample_index)
            self.yielded += end - start
            yield block(self.values[start-self.first_sample_index:end-self.first_sample_index], start, self.identity)


def detect(values, sizes=None, first=0, cfg=None, capacity=256, finish=True):
    detector = EventDetector(cfg or config(), capacity)
    sizes = sizes or [MAX_BLOCK_SAMPLES]
    position = 0
    for size in sizes:
        if position == len(values) or detector.status.outcome != "running":
            break
        part = values[position:position+size]
        detector.feed(block(part, first+position))
        position += len(part)
    while position < len(values) and detector.status.outcome == "running":
        part = values[position:position+MAX_BLOCK_SAMPLES]
        detector.feed(block(part, first+position))
        position += len(part)
    if finish:
        detector.finish()
    return detector


class ViewportTests(unittest.TestCase):
    def test_manual_zoom_pan_stays_put(self):
        view = Viewport(100, 200, True).select(120, 140).pan(7)
        self.assertEqual(view, Viewport(127, 147))
        self.assertIs(view.on_append(10000), view)
        self.assertEqual(view.follow().on_append(10000), Viewport(9980, 10000, True))

    def test_rejects_oversized_or_noninteger_view(self):
        for args in [(0, 2, False, 1), (True, 4), (0, 2.0), (-1, 4), (4, 4),
                     (MAX_INDEX-1, MAX_INDEX+1), (0, 5, 1), (0, 5, False, MAX_VIEW_SAMPLES+1)]:
            with self.subTest(args=args), self.assertRaises(StreamViewError):
                Viewport(*args)

    def test_local_origin_keeps_adjacent_samples_above_float_precision(self):
        index = 2**60
        point = DisplayPoint(index + 1, 42)
        self.assertEqual(point.local_seconds(index, 50000), 1 / 50000)
        self.assertNotEqual(point.local_seconds(index, 50000), 0)


class EnvelopeTests(unittest.TestCase):
    def test_extrema_budget_and_chunk_partition_invariance(self):
        values = [1000] * 10003
        values[23] = 65535
        values[9876] = 0
        expected = None
        for size in (1, 7, 101, 8192):
            reader = Reader(values, first=2**60, chunk=size)
            env = minmax_envelope(reader, Viewport(2**60, 2**60+len(values)), 31)
            self.assertEqual(env.samples_read, len(values))
            self.assertLessEqual(env.point_count, 31)
            codes = [p.code for part in env.segments for p in part]
            self.assertIn(65535, codes); self.assertIn(0, codes)
            self.assertEqual(env.outcome, "complete")
            if expected is None:
                expected = env
            self.assertEqual(env, expected)
        self.assertEqual(values[23], 65535)

    def test_zoom_reads_only_requested_range(self):
        reader = Reader([11] * 1_000_000, first=20)
        env = minmax_envelope(reader, Viewport(5020, 5120), 10)
        self.assertEqual(reader.requests, [(5020, 5120, 8192)])
        self.assertEqual(reader.yielded, 100)
        self.assertEqual(env.samples_read, 100)

    def test_large_source_does_not_allocate_or_scan_whole_recording(self):
        class VirtualReader:
            identity = Identity()
            first_sample_index = 0
            end_sample_index = 2**63
            yielded = 0
            def iter_raw(self, start_sample_index, end_sample_index, chunk_samples):
                for start in range(start_sample_index, end_sample_index, chunk_samples):
                    count = min(chunk_samples, end_sample_index-start)
                    self.yielded += count
                    yield Block(self.identity, start, b'\x80\x80' * count)
        reader = VirtualReader()
        env = minmax_envelope(reader, Viewport(2**60, 2**60+65536), 512)
        self.assertEqual(reader.yielded, 65536)
        self.assertLessEqual(env.point_count, 512)

    def test_gap_overlap_identity_and_bad_end_stop_at_verified_prefix(self):
        for reason, second in [("loss", block([30, 40], 5)),
                               ("overlap", block([30, 40], 1)),
                               ("identity_changed", block([30, 40], 2, Identity(config_id=2))),
                               ("range_violation", block([30]*9, 2))]:
            reader = Reader([1]*10)
            reader.iter_raw = lambda *a, **kw: iter((block([10, 20]), second))
            env = minmax_envelope(reader, Viewport(0, 10), 10)
            self.assertEqual(env.outcome, reason)
            self.assertEqual(env.covered_until, 2)
            self.assertEqual(env.samples_read, 2)
            self.assertEqual(len(env.segments), 1)
            self.assertEqual(env.discontinuities[-1].reason, reason)

    def test_short_and_unavailable_ranges_explicit(self):
        reader = Reader([10]*10, first=100)
        env = minmax_envelope(reader, Viewport(90, 120), 10)
        self.assertEqual(env.outcome, "partial_range")
        self.assertEqual([f.reason for f in env.discontinuities],
                         ["unavailable_prefix", "unavailable_suffix"])
        self.assertEqual(minmax_envelope(reader, Viewport(1, 2)).outcome, "unavailable")
        reader.iter_raw = lambda *a, **kw: iter((block([10], 100),))
        self.assertEqual(minmax_envelope(reader, Viewport(100, 110)).outcome, "short_read")

    def test_actual_polyline_endpoints_plus_extrema_within_budget(self):
        for values in ([2048]*100, [7, 10, 5, 6, 8]):
            for budget in (4, 5, 10, 2048):
                for chunk in (1, 3, 8192):
                    env = minmax_envelope(Reader(values, first=2**60, chunk=chunk),
                        Viewport(2**60, 2**60+len(values)), budget)
                    points = env.segments[0]
                    self.assertEqual(points[0], DisplayPoint(2**60, values[0]))
                    self.assertEqual(points[-1], DisplayPoint(2**60+len(values)-1, values[-1]))
                    self.assertIn(min(values), [point.code for point in points])
                    self.assertIn(max(values), [point.code for point in points])
                    self.assertLessEqual(env.point_count, budget)
                    self.assertEqual([p.sample_index for p in points],
                                     sorted(set(p.sample_index for p in points)))
                    self.assertEqual(env.version, ENVELOPE_VERSION)

    def test_singleton_and_two_sample_endpoints(self):
        for values in ([11], [11, 11], [7, 13], [13, 7]):
            for budget in (4, 2048):
                env = minmax_envelope(Reader(values), Viewport(0, len(values)), budget)
                self.assertEqual(env.segments[0], tuple(DisplayPoint(i, code)
                                 for i, code in enumerate(values)))

    def test_endpoint_reserve_stops_at_first_fault_prefix(self):
        for reason, bad in [('loss', block([90], 8)),
                            ('overlap', block([90], 4)),
                            ('identity_changed', block([90], 5, Identity(config_id=2)))]:
            reader = Reader([7, 10, 5, 6, 8, 90, 90, 90, 90])
            reader.iter_raw = lambda *a, **kw: iter((block([7, 10, 5, 6, 8]), bad))
            env = minmax_envelope(reader, Viewport(0, 9), 4)
            self.assertEqual(env.outcome, reason)
            self.assertEqual(env.segments[0], (DisplayPoint(0, 7), DisplayPoint(1, 10),
                                             DisplayPoint(2, 5), DisplayPoint(4, 8)))
            self.assertEqual(env.covered_until, 5)
            self.assertEqual(env.samples_read, 5)

    def test_minimal_budget_and_all_uint16_bits(self):
        reader = Reader([65535, 32768, 1])
        result = minmax_envelope(reader, Viewport(0, 3), 4)
        self.assertEqual(result.segments[0], (DisplayPoint(0, 65535), DisplayPoint(2, 1)))
        for bad in (True, 1, 2, 3, 65537):
            with self.assertRaises(StreamViewError):
                minmax_envelope(reader, Viewport(0, 3), bad)


class EventTests(unittest.TestCase):
    def test_exact_event_context_and_navigation(self):
        values = [20]*8 + [101, 110, 80] + [20]*9 + [200] + [20]*9
        detector = detect(values, first=2**60)
        entries = detector.page(0)
        self.assertEqual(len(entries), 2)
        one = entries[0]
        self.assertEqual((one.trigger_index, one.start_index, one.end_index),
                         (2**60+8, 2**60+4, 2**60+16))
        self.assertEqual((one.baseline_start, one.baseline_end, one.settled_index),
                         (2**60+6, 2**60+8, 2**60+13))
        self.assertEqual(one.flags, ())
        self.assertEqual(detector.next_after(one.trigger_index), entries[1])
        self.assertEqual(detector.previous_before(entries[1].trigger_index), one)
        self.assertIsNone(detector.previous_before(one.trigger_index))
        self.assertIsNone(detector.next_after(entries[-1].trigger_index))
        self.assertEqual(detector.version, DETECTOR_VERSION)

    def test_every_two_way_split_and_one_sample_chunks(self):
        signals = ([20]*6 + [110, 200, 80, 20, 20, 70, 110, 20, 20, 20, 20, 20],
                   [20]*6 + [200]*100 + [20]*9,
                   [20, 20, 100, 39, 100, 39, 100, 39, 39, 20, 20, 20],
                   [200]*30, [20]*30, [0, 20, 200, 4095, 20, 20])
        for values in signals:
            expected = detect(values)
            for split in range(1, len(values)):
                actual = detect(values, [split, len(values)-split])
                self.assertEqual(actual.page(0), expected.page(0), (values, split))
                self.assertEqual(actual.status, expected.status)
            actual = detect(values, [1]*len(values))
            self.assertEqual(actual.page(0), expected.page(0))
            self.assertEqual(actual.status, expected.status)

    def test_random_partitions_preserve_event_index(self):
        rng = random.Random(191)
        values = [rng.choice((20, 20, 20, 35, 60, 100, 120)) for _ in range(4000)]
        expected = detect(values, capacity=256)
        for _ in range(12):
            sizes = [rng.randint(1, 83) for _ in range(300)]
            actual = detect(values, sizes, capacity=256)
            self.assertEqual(actual.page(0, 256), expected.page(0, 256))
            self.assertEqual(actual.status, expected.status)

    def test_below_polarity_is_symmetric(self):
        values = [20]*8 + [110]*3 + [20]*9
        above = detect(values).page(0)
        below = detect([4095-v for v in values], cfg=config(
            polarity="below", threshold_code=3995, release_code=4055)).page(0)
        self.assertEqual(above, below)

    def test_no_event_and_dc_do_not_claim_physical_absence(self):
        self.assertEqual(detect([20]*1000).page(0), ())
        dc = detect([200]*1000)
        self.assertEqual(len(dc.page(0)), 1)
        self.assertIn("context_limit", dc.event(0).flags)
        self.assertIn("left_truncated", dc.event(0).flags)
        self.assertLessEqual(dc.event(0).end_index-dc.event(0).start_index, 64)

    def test_pre_context_clipping_and_tail_flags(self):
        entries = detect([20]*5+[0, 20, 200, 4095, 20, 20, 70, 20, 20]).page(0)
        self.assertIn("clipping", entries[0].flags)
        self.assertIn("tail_ambiguous", entries[0].flags)
        truncated = detect([200, 200]).event(0)
        self.assertEqual(truncated.end_index, 2)
        self.assertTrue({"left_truncated", "baseline_unsettled", "right_truncated",
                         "tail_ambiguous"}.issubset(truncated.flags))

    def test_close_crossings_merged_with_overlap_evidence(self):
        detector = detect([20]*6+[200, 20, 200, 20, 20, 200]+[20]*8)
        self.assertEqual(len(detector.page(0)), 1)
        self.assertIn("overlapping_activity", detector.event(0).flags)

    def test_holdoff_and_suppressed_crossings_explicit(self):
        values = [20]*4 + [200] + [20]*6 + [200] + [20]*6 + [200] + [20]*6
        detector = detect(values, cfg=config(holdoff_samples=12))
        self.assertEqual([e.trigger_index for e in detector.page(0)], [4, 18])
        self.assertEqual(detector.status.suppressed_crossings, 1)

    def test_loss_overlap_and_identity_first_fault(self):
        for reason, next_block in [("loss", block([200], 10)),
                                   ("overlap", block([200], 5)),
                                   ("identity_changed", block([200], 7, Identity(generation=2)))]:
            detector = EventDetector(config(), 4)
            detector.feed(block([20]*5 + [200, 200]))
            status = detector.feed(next_block)
            self.assertEqual(status.outcome, reason)
            self.assertEqual(detector.event(0).end_index, 7)
            self.assertIn(reason, detector.event(0).flags)
            self.assertEqual(detector.finish().outcome, reason)
            with self.assertRaises(StreamViewError):
                detector.feed(block([20], 7))

    def test_stop_and_loss_on_exact_context_boundary(self):
        values = [20]*4+[200]+[20]*5
        complete = detect(values)
        self.assertEqual(complete.event(0).end_index, 10)
        self.assertNotIn("right_truncated", complete.event(0).flags)
        shorter = detect(values[:-1])
        self.assertIn("right_truncated", shorter.event(0).flags)
        detector = detect(values, finish=False)
        detector.feed(block([20], 12))
        self.assertEqual(detector.status.outcome, "loss")
        self.assertNotIn("loss", detector.event(0).flags)
        self.assertEqual(detector.status.discontinuity.expected_index, 10)

    def test_event_capacity_preserves_extra_incomplete_context(self):
        values = ([20]*5 + [200] + [20]*6)*5
        detector = detect(values, capacity=2)
        self.assertEqual(detector.status.outcome, "event_capacity")
        self.assertEqual(detector.status.indexed_events, 2)
        self.assertEqual(detector.status.pending.ordinal, 2)
        self.assertEqual(detector.status.pending.trigger_index, 29)
        self.assertEqual(detector.status.processed_until, 30)
        self.assertIn("event_capacity", detector.status.pending.flags)
        self.assertEqual(len(detector.page(0)), 2)
        self.assertEqual(len(detector._rail_ring), config().pre_samples)
        self.assertEqual(detector.finish("unknown_loss").outcome, "event_capacity")

    def test_capacity_partition_invariance(self):
        values = ([20]*5+[200]+[20]*6)*5
        one = detect(values, capacity=2)
        many = detect(values, [1]*len(values), capacity=2)
        self.assertEqual(one.status, many.status)
        self.assertEqual(one.page(0), many.page(0))

    def test_lazy_raw_selection_is_exact_and_identity_bound(self):
        values = [20]*8 + [101, 65535, 70] + [20]*9
        first = 2**60
        detector = detect(values, first=first)
        reader = Reader(values, first=first, chunk=3)
        iterator = detector.iter_event_raw(reader, 0)
        self.assertEqual(reader.requests, [])
        raw = b''.join(b.raw_u16le for b in iterator)
        entry = detector.event(0)
        expected = values[entry.start_index-first:entry.end_index-first]
        self.assertEqual(raw, struct.pack(f'<{len(expected)}H', *expected))
        self.assertEqual(reader.requests, [(entry.start_index, entry.end_index, 8192)])
        stale = Reader(values, first=first, identity=Identity(config_id=2))
        with self.assertRaises(StreamViewError):
            list(detector.iter_event_raw(stale, 0))
        reader.iter_raw = lambda *a, **kw: iter((block([20], entry.start_index+1),))
        with self.assertRaises(StreamViewError):
            list(detector.iter_event_raw(reader, 0))

    def test_mutable_identity_is_copied_and_mutable_raw_rejected(self):
        identity = SimpleNamespace(**Identity().__dict__)
        detector = EventDetector(config(), 2)
        detector.feed(block([20], identity=identity))
        identity.generation = 2
        self.assertEqual(detector.feed(block([20], 1, identity)).outcome, "identity_changed")
        for raw in (bytearray(b'\x00\x00'), memoryview(b'\x00\x00')):
            with self.assertRaises(StreamViewError):
                EventDetector(config(), 2).feed(Block(Identity(), 0, raw))

    def test_provenance_change_is_explicit_identity_boundary(self):
        for field in ('firmware_version', 'application_version'):
            detector = EventDetector(config(), 2)
            detector.feed(block([20]))
            identity = replace(Identity(), **{field: 'next-version'})
            self.assertEqual(detector.feed(block([20], 1, identity)).outcome,
                             'identity_changed')

    def test_bad_blocks_do_not_mutate_detector(self):
        for bad in (block([1], True), block([1], MAX_INDEX), block([], 0),
                    block([1]*(MAX_BLOCK_SAMPLES+1)), Block(Identity(), 0, b'\x00'),
                    block([1], identity=Identity(generation=True))):
            detector = EventDetector(config(), 2)
            before = detector.status
            with self.assertRaises(StreamViewError):
                detector.feed(bad)
            self.assertEqual(detector.status, before)

    def test_invalid_configuration_and_navigation(self):
        for kwargs in (dict(threshold_code=True), dict(release_code=100),
                       dict(polarity="both"), dict(pre_samples=1), dict(baseline_samples=0),
                       dict(post_samples=-1), dict(rail_high=65536), dict(max_event_samples=3)):
            with self.assertRaises(StreamViewError):
                config(**kwargs)
        for capacity in (True, 0, MAX_EVENTS+1):
            with self.assertRaises(StreamViewError):
                EventDetector(config(), capacity)
        detector = detect([20]*8+[200]+[20]*8)
        for args in ((True, 1), (0, MAX_PAGE+1), (0, True)):
            with self.assertRaises(StreamViewError):
                detector.page(*args)
        with self.assertRaises(StreamViewError):
            detector.event(True)
        with self.assertRaises(StreamViewError):
            detector.next_after(1.0)


    def test_near_u64_end_retains_exact_context(self):
        values = [20]*8+[200]+[20]*7
        first = MAX_INDEX-len(values)
        detector = detect(values, first=first)
        self.assertEqual(detector.event(0).trigger_index, first+8)
        self.assertEqual(detector.status.processed_until, MAX_INDEX)
        self.assertEqual(detector.event(0).end_index, first+14)

    def test_quiet_long_input_retains_only_fixed_state(self):
        detector = EventDetector(config(), 2)
        raw = struct.pack('<H', 20)*8192
        for index in range(0, 8192*128, 8192):
            detector.feed(Block(Identity(), index, raw))
        self.assertEqual(detector.status.processed_until, 1048576)
        self.assertEqual(detector.page(0), ())
        self.assertEqual(len(detector._rail_ring), 4)
        self.assertIsNone(detector.status.pending)

    def test_clipping_precontext_expires_and_no_raw_alias(self):
        values = [0]+[20]*10+[200]+[20]*8
        source = block(values)
        detector = EventDetector(config(), 2)
        detector.feed(source)
        detector.finish()
        self.assertNotIn('clipping', detector.event(0).flags)
        self.assertEqual(source.raw_u16le, struct.pack(f'<{len(values)}H', *values))

    def test_empty_finish_and_unknown_tail(self):
        detector = EventDetector(config(), 1)
        self.assertEqual(detector.finish().processed_until, None)
        detector = EventDetector(config(), 1)
        status = detector.finish("unknown_loss")
        self.assertEqual(status.outcome, "unknown_loss")
        self.assertIsNone(status.discontinuity.expected_index)
        self.assertIsNone(status.discontinuity.observed_index)



class RecentEventTests(unittest.TestCase):
    def rolling(self, values, *, capacity=2, chunk=8192, first=0, cfg=None, finish=True):
        detector = EventDetector(cfg or config(), capacity, retention="recent")
        for at in range(0, len(values), chunk):
            detector.feed(block(values[at:at + chunk], first + at))
        if finish:
            detector.finish()
        return detector

    def test_recent_eviction_keeps_global_identity_and_full_progress(self):
        values = ([20]*5 + [200] + [20]*6)*20
        detector = self.rolling(values, capacity=2)
        fixed = detect(values)
        self.assertEqual(detector.retained_events, fixed.retained_events[-2:])
        status = detector.status
        self.assertEqual((status.retention, status.indexed_events, status.first_retained_ordinal,
                          status.next_ordinal, status.evicted_events, status.processed_until),
                         ("recent", 2, 18, 20, 18, len(values)))
        self.assertEqual(detector.event(18), fixed.event(18))
        self.assertEqual(detector.page(1), (fixed.event(19),))
        self.assertEqual(detector.next_after(fixed.event(18).trigger_index), fixed.event(19))
        self.assertEqual(detector.previous_before(fixed.event(19).trigger_index), fixed.event(18))
        with self.assertRaises(StreamViewError):
            detector.event(0)
        self.assertEqual(len(detector._rail_ring), 4)

    def test_small_recent_matches_large_fixed_across_every_split(self):
        signals = [([20]*6+[200, 0, 20, 20, 20, 20, 20])*5 + [20, 200, 20],
                   ([20]*6+[200, 20, 200, 20, 20, 70, 200]+[20]*9)*4,
                   ([20]*6+[200]*70+[20]*10)*4]
        for cfg in (config(), config(holdoff_samples=22)):
            for values in signals:
                fixed = detect(values, cfg=cfg)
                for split in range(1, len(values)):
                    detector = EventDetector(cfg, 2, retention="recent")
                    detector.feed(block(values[:split]))
                    detector.feed(block(values[split:], split))
                    detector.finish()
                    self.assertEqual(detector.retained_events, fixed.retained_events[-2:])
                    self.assertEqual(detector.status.next_ordinal, fixed.status.next_ordinal)
                    self.assertEqual(detector.status.suppressed_crossings, fixed.status.suppressed_crossings)
                    self.assertEqual(detector.status.processed_until, len(values))
                self.assertEqual(self.rolling(values, cfg=cfg, chunk=1).retained_events,
                                 fixed.retained_events[-2:])

    def test_active_cross_block_context_and_prehistory_survive_metadata_eviction(self):
        prefix = ([20]*5+[200]+[20]*6)*5 + [0, 20, 200, 200]
        recent = self.rolling(prefix, chunk=7, finish=False)
        fixed = detect(prefix, finish=False)
        self.assertEqual(recent.status.pending, fixed.status.pending)
        original = recent.status.pending
        self.assertEqual(original.ordinal, 5)
        self.assertIn("clipping", original.flags)
        self.assertEqual(recent.status.evicted_events, 3)
        for part in ([200, 20], [20, 70, 200], [20]*7):
            at = recent.status.processed_until
            recent.feed(block(part, at)); fixed.feed(block(part, at))
            self.assertEqual(recent.status.pending, fixed.status.pending)
        self.assertEqual(recent.retained_events, fixed.retained_events[-2:])
        self.assertEqual(recent.event(5).start_index, original.start_index)
        self.assertIn("overlapping_activity", recent.event(5).flags)

    def test_recent_first_fault_and_terminal_do_not_join_gaps(self):
        prefix = ([20]*5+[200]+[20]*6)*4 + [20, 200]
        for reason, observed, identity in (("loss", len(prefix)+1, Identity()),
                ("overlap", len(prefix)-1, Identity()),
                ("identity_changed", len(prefix), Identity(config_id=2))):
            recent = self.rolling(prefix, finish=False)
            fixed = detect(prefix, finish=False)
            for detector in (recent, fixed):
                detector.feed(block([20], observed, identity))
                detector.finish()
            self.assertEqual(recent.status.outcome, reason)
            self.assertEqual(recent.status.processed_until, len(prefix))
            self.assertEqual(recent.retained_events, fixed.retained_events[-2:])
            self.assertEqual(recent.status.discontinuity, fixed.status.discontinuity)
            with self.assertRaises(StreamViewError):
                recent.feed(block([20], len(prefix)))

    def test_recent_large_integer_and_exact_raw_or_explicit_unavailability(self):
        values = ([20]*5+[200]+[20]*6)*8
        first = MAX_INDEX-len(values)
        detector = self.rolling(values, first=first, chunk=1)
        fixed = detect(values, first=first)
        self.assertEqual(detector.retained_events, fixed.retained_events[-2:])
        self.assertEqual(detector.status.processed_until, MAX_INDEX)
        entry = detector.event(7)
        reader = Reader(values, first=first)
        result = b"".join(b.raw_u16le for b in detector.iter_event_raw(reader, 7))
        expected = values[entry.start_index-first:entry.end_index-first]
        self.assertEqual(result, struct.pack(f"<{len(expected)}H", *expected))
        reader.first_sample_index = entry.start_index+1
        with self.assertRaises(StreamViewError):
            list(detector.iter_event_raw(reader, 7))

    def test_high_noise_fastest_crossings_bounded_without_raw_loss(self):
        cfg = config(pre_samples=1, baseline_samples=1, post_samples=0, max_event_samples=3)
        values = [200, 20]*16000
        fixed = detect(values, cfg=cfg, capacity=20000)
        for chunk in (7, 8192):
            recent = self.rolling(values, cfg=cfg, capacity=3, chunk=chunk)
            self.assertEqual(recent.status.processed_until, len(values))
            self.assertEqual(recent.status.next_ordinal, 16000)
            self.assertEqual(recent.status.evicted_events, 15997)
            self.assertEqual(recent.retained_events, fixed.retained_events[-3:])
            self.assertEqual(len(recent._events), 3)
            self.assertEqual(len(recent._rail_ring), 1)

    def test_recent_empty_and_bad_retention_parameters(self):
        detector = EventDetector(config(), 2, retention="recent")
        self.assertEqual(detector.retained_events, ())
        self.assertEqual((detector.status.first_retained_ordinal, detector.status.next_ordinal,
                          detector.status.evicted_events), (0, 0, 0))
        self.assertEqual(detector.finish().outcome, "stopped")
        for retention, capacity in (("rolling", 2), (True, 2), ("recent", 4097)):
            with self.assertRaises(StreamViewError):
                EventDetector(config(), capacity, retention=retention)

    def test_counter_exhaustion_is_explicit_and_does_not_consume_crossing(self):
        detector = EventDetector(config(), 1, retention="recent")
        detector.feed(block([20]*5))
        # Finite counter boundary cannot be reached with an allocated test stream.
        # Inject the exact otherwise-inaccessible state, then exercise real feed.
        detector._next_ordinal = detector._evicted = MAX_INDEX
        result = detector.feed(block([200, 20], 5))
        self.assertEqual(result.outcome, "ordinal_limit")
        self.assertEqual(result.processed_until, 5)
        self.assertEqual(result.next_ordinal, MAX_INDEX)
        self.assertIsNone(result.pending)
        self.assertEqual(result.discontinuity.observed_index, 5)
        self.assertEqual(detector.finish().outcome, "ordinal_limit")


if __name__ == '__main__':
    unittest.main()

"""Wire-neutral bounded views and threshold-event metadata; no Qt or disk I/O.

Raw uint16-le input is never changed. Indices are absolute integers and all
ranges are half-open. These are derived navigation events, not identified
physical discharges. A caller must serialize access and feed ALL raw blocks.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass, replace
from itertools import islice
import struct
from typing import Protocol, Iterator

MAX_INDEX = (1 << 64) - 1
MAX_BLOCK_SAMPLES = 8192
MAX_VIEW_SAMPLES = 1 << 20
MAX_POINTS = 65536
MAX_EVENTS = 65536
MAX_RECENT_EVENTS = 4096
MAX_CONTEXT_SAMPLES = 65536
MAX_PAGE = 256
DETECTOR_VERSION = "threshold-context-v1"
ENVELOPE_VERSION = "endpoint-minmax-v2"


class StreamViewError(ValueError):
    """Invalid input or requested work outside the declared bounded scope."""


def _require(condition, message):
    if not condition:
        raise StreamViewError(message)


def _int(value, low=0, high=MAX_INDEX):
    _require(type(value) is int and low <= value <= high,
             "Ожидается целое число в допустимых границах")


def _identity(identity):
    """Copy values, not the caller's identity object, across ownership boundary."""
    try:
        key = tuple(getattr(identity, name) for name in
                    ("session_id", "generation", "config_id", "nominal_fs_hz",
                     "origin", "protocol_revision"))
    except AttributeError as exc:
        raise StreamViewError("Нет идентификатора нормализованного потока") from exc
    key += tuple(getattr(identity, name, None) for name in
                 ("firmware_version", "application_version"))
    for position, text in ((0, key[0]), (5, key[5]), (6, key[6]), (7, key[7])):
        if text is not None:
            _require(type(text) is str and 1 <= len(text) <= (128 if position == 0 else 64),
                     "Некорректная строка идентификатора")
            _require(not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF
                             or ord(c) in (0xFFFE, 0xFFFF) for c in text),
                     "Недопустимый символ идентификатора")
    _require(type(key[0]) is str, "Нет идентификатора сеанса")
    _int(key[1]); _int(key[2]); _int(key[3], 1, 1_000_000)
    _require(key[4] in ("synthetic", "device") and type(key[4]) is str,
             "Неизвестное происхождение потока")
    return key


class RawBlock(Protocol):
    identity: object
    first_sample_index: int
    raw_u16le: bytes
    sample_count: int
    end_sample_index: int


class RangeReader(Protocol):
    identity: object
    first_sample_index: int
    end_sample_index: int

    def iter_raw(self, start_sample_index: int, end_sample_index: int,
                 chunk_samples: int = MAX_BLOCK_SAMPLES) -> Iterator[RawBlock]: ...


def _block(block):
    try:
        start, raw = block.first_sample_index, block.raw_u16le
        count, end = block.sample_count, block.end_sample_index
        key = _identity(block.identity)
    except AttributeError as exc:
        raise StreamViewError("Нужен нормализованный блок потока") from exc
    _int(start); _int(count, 1, MAX_BLOCK_SAMPLES); _int(end)
    _require(type(raw) is bytes and len(raw) == count * 2,
             "Нужны неизменяемые uint16-le отсчёты ограниченного размера")
    _require(start + count == end, "Несогласованный диапазон блока")
    return key, start, end, raw


def _reader(reader):
    key = _identity(reader.identity)
    first, end = reader.first_sample_index, reader.end_sample_index
    _int(first); _int(end)
    _require(first <= end, "Некорректный диапазон источника")
    return key, first, end


@dataclass(frozen=True, slots=True)
class Viewport:
    start: int
    stop: int
    follow_tail: bool = False
    max_samples: int = 65536

    def __post_init__(self):
        _int(self.start); _int(self.stop); _int(self.max_samples, 1, MAX_VIEW_SAMPLES)
        _require(type(self.follow_tail) is bool, "Некорректный режим слежения")
        _require(0 < self.stop - self.start <= self.max_samples,
                 "Масштаб превышает ограничение чтения; нужен отдельный обзорный индекс")

    def select(self, start: int, stop: int) -> Viewport:
        """An explicit zoom/selection pauses follow without changing its width later."""
        return Viewport(start, stop, False, self.max_samples)

    def pan(self, delta: int) -> Viewport:
        _int(delta, -MAX_INDEX, MAX_INDEX)
        return self.select(self.start + delta, self.stop + delta)

    def follow(self, enabled: bool = True) -> Viewport:
        return replace(self, follow_tail=enabled)

    def on_append(self, end_sample_index: int) -> Viewport:
        _int(end_sample_index)
        if not self.follow_tail or end_sample_index <= self.stop:
            return self
        return replace(self, start=end_sample_index - (self.stop - self.start),
                       stop=end_sample_index)


@dataclass(frozen=True, slots=True)
class DisplayPoint:
    sample_index: int
    code: int

    def local_seconds(self, origin: int, nominal_fs_hz: int) -> float:
        _int(origin); _int(nominal_fs_hz, 1, 1_000_000)
        # Subtract as integers BEFORE float conversion, including above 2**53.
        return (self.sample_index - origin) / nominal_fs_hz


@dataclass(frozen=True, slots=True)
class Discontinuity:
    expected_index: int | None
    observed_index: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class Envelope:
    version = ENVELOPE_VERSION

    identity: tuple
    viewport: Viewport
    segments: tuple[tuple[DisplayPoint, ...], ...]
    discontinuities: tuple[Discontinuity, ...]
    samples_read: int
    covered_until: int
    outcome: str

    @property
    def point_count(self):
        return sum(map(len, self.segments))


def minmax_envelope(reader: RangeReader, viewport: Viewport,
                    point_budget: int = 2048) -> Envelope:
    """Read a bounded viewport; retain actual endpoints and both bin extrema.

    Bins are tied to viewport coordinates, never transport chunks. On the first
    gap/overlap/identity fault, return the verified prefix and explicit evidence;
    never draw a bridge or silently accept the disputed block. No global/full-
    recording overview is implied. The caller must run this off the GUI thread.
    """
    _require(type(viewport) is Viewport, "Нужен ограниченный диапазон просмотра")
    _int(point_budget, 4, MAX_POINTS)
    key, first, end = _reader(reader)
    start, stop = max(first, viewport.start), min(end, viewport.stop)
    points, faults = [], []
    expected, read_count, outcome = start, 0, "complete"
    if viewport.start < first:
        faults.append(Discontinuity(viewport.start, first, "unavailable_prefix"))
    if start >= stop:
        return Envelope(key, viewport, (),
                        (Discontinuity(viewport.start, None, "unavailable_range"),),
                        0, viewport.start, "unavailable")
    # Two points are reserved for the actual accepted time endpoints. Without
    # them a constant bin collapses to a single point and a final extremum may
    # precede the last raw sample. Budgets below four cannot promise both
    # arbitrary interior extrema and endpoints, so they are rejected above.
    bins = (point_budget - 2) // 2
    width = (viewport.stop - viewport.start + bins - 1) // bins
    current_bin, minimum, maximum = None, None, None
    first_point = last_point = None

    def flush():
        if minimum is None:
            return
        selected = (minimum,) if minimum == maximum else sorted(
            (minimum, maximum), key=lambda point: point.sample_index)
        points.extend(selected)

    for block in reader.iter_raw(start, stop, chunk_samples=MAX_BLOCK_SAMPLES):
        block_key, block_start, block_end, raw = _block(block)
        reason = ("identity_changed" if block_key != key else
                  "loss" if block_start > expected else
                  "overlap" if block_start < expected else
                  "range_violation" if block_end > stop else None)
        if reason:
            outcome = reason
            faults.append(Discontinuity(expected, block_start, reason))
            break
        for offset, (code,) in enumerate(struct.iter_unpack("<H", raw)):
            index = block_start + offset
            bucket = (index - viewport.start) // width
            point = DisplayPoint(index, code)
            if first_point is None:
                first_point = point
            last_point = point
            if bucket != current_bin:
                flush()
                current_bin, minimum, maximum = bucket, point, point
            else:
                if code < minimum.code:
                    minimum = point
                if code > maximum.code:
                    maximum = point
            read_count += 1
        expected = block_end
    flush()
    if points:
        if points[0].sample_index != first_point.sample_index:
            points.insert(0, first_point)
        if points[-1].sample_index != last_point.sample_index:
            points.append(last_point)
    if outcome == "complete" and expected != stop:
        outcome = "short_read"
        faults.append(Discontinuity(expected, None, outcome))
    if viewport.stop > end:
        faults.append(Discontinuity(end, None, "unavailable_suffix"))
        if outcome == "complete":
            outcome = "partial_range"
    if viewport.start < first and outcome == "complete":
        outcome = "partial_range"
    _require(len(points) <= point_budget, "Нарушен бюджет отображения")
    return Envelope(key, viewport, (tuple(points),) if points else (), tuple(faults),
                    read_count, expected, outcome)


@dataclass(frozen=True, slots=True)
class EventConfig:
    threshold_code: int
    release_code: int
    polarity: str
    pre_samples: int
    post_samples: int
    baseline_samples: int
    holdoff_samples: int
    max_event_samples: int
    rail_low: int
    rail_high: int

    def __post_init__(self):
        _int(self.threshold_code, 0, 65535); _int(self.release_code, 0, 65535)
        _int(self.rail_low, 0, 65535); _int(self.rail_high, 0, 65535)
        _require(self.rail_low < self.rail_high, "Некорректные коды ограничения")
        _require(type(self.polarity) is str and self.polarity in ("above", "below"),
                 "Нужна явно заданная полярность")
        _require((self.release_code < self.threshold_code if self.polarity == "above"
                  else self.release_code > self.threshold_code), "Нужен гистерезис")
        for value in (self.pre_samples, self.post_samples, self.baseline_samples):
            _int(value, 0, MAX_CONTEXT_SAMPLES)
        _int(self.holdoff_samples, 0, MAX_VIEW_SAMPLES)
        _int(self.max_event_samples, 1, MAX_VIEW_SAMPLES)
        _require(self.baseline_samples >= 1 and self.pre_samples >= self.baseline_samples,
                 "Предыстория должна вмещать базовый интервал")
        _require(self.max_event_samples >=
                 self.pre_samples + self.baseline_samples + self.post_samples + 1,
                 "Лимит события не вмещает заданный контекст")

    def high(self, code):
        return code >= self.threshold_code if self.polarity == "above" else code <= self.threshold_code

    def quiet(self, code):
        return code <= self.release_code if self.polarity == "above" else code >= self.release_code


@dataclass(frozen=True, slots=True)
class EventEntry:
    ordinal: int
    trigger_index: int
    start_index: int
    end_index: int
    baseline_start: int
    baseline_end: int
    settled_index: int | None
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DetectorStatus:
    outcome: str
    indexed_events: int
    processed_until: int | None
    pending: EventEntry | None
    discontinuity: Discontinuity | None
    suppressed_crossings: int
    retention: str = "fixed"
    first_retained_ordinal: int = 0
    next_ordinal: int = 0
    evicted_events: int = 0


class EventDetector:
    """Bounded metadata index, scalar detector state and a bounded rail bit ring.

    A high crossing starts an event. baseline_samples consecutive quiet samples
    settle it; post_samples more samples finish context. Another high crossing
    before context completion merges with explicit overlapping_activity evidence.
    A context cap closes an ambiguous/truncated event and waits for rearming.
    Default fixed retention stops at metadata capacity; explicit recent retention
    evicts only the oldest closed metadata and never restarts detector state.
    """
    version = DETECTOR_VERSION

    def __init__(self, config: EventConfig, event_capacity: int, *, retention="fixed"):
        _require(type(config) is EventConfig, "Нужна явная конфигурация детектора")
        config.__post_init__()
        _require(type(retention) is str and retention in ("fixed", "recent"),
                 "Неизвестный режим хранения событий")
        _int(event_capacity, 1, MAX_RECENT_EVENTS if retention == "recent" else MAX_EVENTS)
        self.config, self.event_capacity = config, event_capacity
        self.retention = retention
        self._next_ordinal = self._evicted = 0
        self.identity = None
        self._first = self._end = None
        self._events = deque() if retention == "recent" else []
        self._active = None
        self._flags = set()
        self._quiet_run = 0
        self._settled = None
        self._rearmed = True
        self._holdoff_until = 0
        self._rail_ring = bytearray(config.pre_samples)
        self._rail_count = self._ring_position = 0
        self._outcome = "running"
        self._fault = None
        self._capacity_pending = None
        self._suppressed = 0

    def _entry(self, flags=()):
        if self._active is None:
            return None
        trigger, start, baseline_start = self._active
        return EventEntry(self._next_ordinal, trigger, start, self._end,
                          baseline_start, trigger, self._settled,
                          tuple(sorted(self._flags | set(flags))))

    @property
    def status(self):
        return DetectorStatus(self._outcome, len(self._events), self._end,
                              self._capacity_pending or self._entry(("pending",)),
                              self._fault, self._suppressed, self.retention,
                              self._evicted, self._next_ordinal, self._evicted)

    def _close(self, *flags):
        entry = self._entry(flags)
        if entry is not None:
            if self.retention == "recent" and len(self._events) == self.event_capacity:
                self._events.popleft()
                self._evicted += 1
            self._events.append(entry)
            self._next_ordinal += 1
            self._active = None
            self._flags = set()
            self._settled = None

    def _terminate(self, reason, observed=None):
        self._fault = Discontinuity(self._end, observed, reason)
        self._close(reason, "right_truncated", "tail_ambiguous")
        self._outcome = reason
        return self.status

    def feed(self, block: RawBlock) -> DetectorStatus:
        _require(self._outcome == "running", "Индексирование уже завершено")
        key, start, end, raw = _block(block)
        if self.identity is None:
            self.identity, self._first, self._end = key, start, start
        if key != self.identity:
            return self._terminate("identity_changed", start)
        if start != self._end:
            return self._terminate("loss" if start > self._end else "overlap", start)
        config = self.config
        for index, (code,) in enumerate(struct.iter_unpack("<H", raw), start):
            high, quiet = config.high(code), config.quiet(code)
            clipped = code <= config.rail_low or code >= config.rail_high
            crossing = high and self._rearmed
            if self._active is not None and crossing:
                self._flags.add("overlapping_activity")
            if self._active is None and crossing:
                self._rearmed = False
                if index < self._holdoff_until:
                    self._suppressed += 1
                else:
                    if self._next_ordinal == MAX_INDEX:
                        # No ordinal can be reserved without overflowing the
                        # completed count. Leave this crossing unprocessed.
                        return self._terminate("ordinal_limit", index)
                    self._active = (index, max(self._first, index - config.pre_samples),
                                    index - min(self._quiet_run, config.baseline_samples))
                    self._flags = set()
                    self._settled = None
                    if index - self._first < config.pre_samples:
                        self._flags.add("left_truncated")
                    if self._quiet_run < config.baseline_samples:
                        self._flags.add("baseline_unsettled")
                    if self._rail_count:
                        self._flags.add("clipping")
                    self._holdoff_until = min(MAX_INDEX, index + config.holdoff_samples)
                    if self.retention == "fixed" and len(self._events) == self.event_capacity:
                        self._end = index + 1
                        if clipped:
                            self._flags.add("clipping")
                        self._capacity_pending = self._entry(
                            ("event_capacity", "right_truncated", "tail_ambiguous"))
                        self._active = None
                        self._outcome = "event_capacity"
                        return self.status
            self._quiet_run = min(config.baseline_samples, self._quiet_run + 1) if quiet else 0
            if quiet:
                self._rearmed = True
            elif high:
                self._rearmed = False
            self._end = index + 1
            if self._active is not None:
                if clipped:
                    self._flags.add("clipping")
                if self._settled is not None and not quiet:
                    self._flags.add("tail_ambiguous")
                if high and self._settled is not None:
                    self._flags.add("overlapping_activity")
                    self._settled = None
                    self._rearmed = False
                if self._settled is None and self._quiet_run == config.baseline_samples:
                    self._settled = self._end
                if self._settled is not None and self._end >= self._settled + config.post_samples:
                    self._close()
                elif self._end - self._active[1] >= config.max_event_samples:
                    self._close("context_limit", "right_truncated", "tail_ambiguous")
                    self._rearmed = False
            # The ring stores only rail evidence for bounded prehistory, no raw
            # data and no Python object per sample retained across blocks.
            self._rail_count -= self._rail_ring[self._ring_position]
            self._rail_ring[self._ring_position] = int(clipped)
            self._rail_count += int(clipped)
            self._ring_position = (self._ring_position + 1) % len(self._rail_ring)
        return self.status

    def finish(self, reason: str = "stopped") -> DetectorStatus:
        _require(reason in ("stopped", "source_fault", "unknown_loss"),
                 "Неизвестное основание окончания входа")
        if self._outcome != "running":
            return self.status  # A later clean Stop cannot erase the first fault.
        if reason != "stopped":
            return self._terminate(reason)
        self._close("right_truncated", "tail_ambiguous")
        self._outcome = reason
        return self.status

    def event(self, ordinal: int) -> EventEntry:
        _int(ordinal, self._evicted, self._next_ordinal - 1)
        return self._events[ordinal - self._evicted]

    def page(self, offset: int, limit: int = 64) -> tuple[EventEntry, ...]:
        _int(offset, 0, len(self._events)); _int(limit, 1, MAX_PAGE)
        if self.retention == "fixed":
            return tuple(self._events[offset:offset + limit])
        return tuple(islice(self._events, offset, offset + limit))

    @property
    def retained_events(self) -> tuple[EventEntry, ...]:
        """Copy only at publication time, never once per feed/raw block."""
        return tuple(self._events)

    def next_after(self, sample_index: int) -> EventEntry | None:
        _int(sample_index)
        at = bisect_right(self._events, sample_index, key=lambda entry: entry.trigger_index)
        return self._events[at] if at < len(self._events) else None

    def previous_before(self, sample_index: int) -> EventEntry | None:
        _int(sample_index)
        at = bisect_left(self._events, sample_index, key=lambda entry: entry.trigger_index)
        return self._events[at - 1] if at else None

    def iter_event_raw(self, reader: RangeReader, ordinal: int) -> Iterator[RawBlock]:
        """Lazy exact raw event context; identity and each range are rechecked.

        Generator results belong to this detector/reader identity. A future UI
        must additionally reject superseded render requests; no async UI exists
        in this foundation.
        """
        entry = self.event(ordinal)
        key, first, end = _reader(reader)
        _require(key == self.identity, "Другой поток записи")
        _require(first <= entry.start_index <= entry.end_index <= end,
                 "Контекст события недоступен")
        expected = entry.start_index
        for block in reader.iter_raw(entry.start_index, entry.end_index,
                                     chunk_samples=MAX_BLOCK_SAMPLES):
            key, start, end, _ = _block(block)
            _require(key == self.identity and start == expected and end <= entry.end_index,
                     "Разрыв или смена записи при чтении события")
            yield block
            expected = end
        _require(expected == entry.end_index, "Контекст события обрезан")

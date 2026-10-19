"""Reusable stream viewer panel; no transport, acquisition or executable app.

Only a bounded plot payload crosses from the single worker to Qt. Saved files
are immutable lazy readers. Live input is an explicitly selected source and a
latest-only precomputed envelope, never a queue of transport blocks.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, fields
import math
from pathlib import Path
import struct
import threading
from typing import Callable

from PySide6 import QtCore, QtWidgets
import pyqtgraph as pg

from .gui import STYLE, _label
from .stream_model import StreamError, StreamIdentity, uint
from .stream_provenance import profile_for_identity, provenance_text
from . import stream_recording as storage
from .stream_recording import AttemptReport, StreamRecording, recover, recover_attempt
from .stream_view import (DetectorStatus, Discontinuity, DisplayPoint, Envelope, EventConfig,
                          EventDetector, EventEntry, Viewport, minmax_envelope)

DETAIL_SAMPLES = 65536
POINT_BUDGET = 2048
SCAN_SAMPLES = 65536
SUMMARY_BINS = 4096
EVENT_CAPACITY = 4096


def identity_key(identity):
    if type(identity) is not StreamIdentity:
        raise ValueError("Нужна неизменяемая нормализованная identity")
    identity.__post_init__()
    return tuple(getattr(identity, field.name) for field in fields(StreamIdentity))


@dataclass(frozen=True, slots=True)
class Selection:
    """Exact raw-index selection, independent of the bounded raw Viewport."""
    start: int
    stop: int

    def __post_init__(self):
        uint(self.start); uint(self.stop)
        if self.start >= self.stop:
            raise ValueError("Нужен непустой диапазон [start, stop)")

    @property
    def width(self):
        return self.stop - self.start


@dataclass(frozen=True, slots=True)
class LiveHandle:
    source_revision: int
    identity: tuple


@dataclass(frozen=True, slots=True)
class LiveEventIndex:
    """Bounded recent metadata; producer constructs it outside receiver locks."""
    handle: LiveHandle
    sequence: int
    config_revision: int
    config: EventConfig
    events: tuple[EventEntry, ...]
    status: DetectorStatus
    capacity: int = EVENT_CAPACITY

    def __post_init__(self):
        if type(self.handle) is not LiveHandle or type(self.handle.identity) is not tuple:
            raise ValueError("Нужен immutable live handle")
        uint(self.handle.source_revision); uint(self.sequence); uint(self.config_revision)
        if len(self.handle.identity) != len(fields(StreamIdentity)):
            raise ValueError("Некорректный immutable идентификатор handle")
        StreamIdentity(*self.handle.identity)  # Reject mutable/nonstrict nested values.
        if type(self.config) is not EventConfig or type(self.status) is not DetectorStatus:
            raise ValueError("Нужны immutable настройки и статус детектора")
        self.config.__post_init__()
        uint(self.capacity, EVENT_CAPACITY, minimum=1)
        status = self.status
        for value in (status.indexed_events, status.first_retained_ordinal,
                      status.next_ordinal, status.evicted_events, status.suppressed_crossings):
            uint(value)
        if (type(self.events) is not tuple or len(self.events) > self.capacity or
                type(status.retention) is not str or status.retention != "recent" or status.indexed_events != len(self.events) or
                status.first_retained_ordinal != status.evicted_events or
                status.next_ordinal != status.evicted_events + len(self.events)):
            raise ValueError("Несогласованный ограниченный индекс событий")
        if type(status.outcome) is not str or status.outcome not in ("running", "stopped", "source_fault", "unknown_loss",
                                   "loss", "overlap", "identity_changed", "ordinal_limit"):
            raise ValueError("Неизвестный исход live детектора")
        if status.processed_until is not None:
            uint(status.processed_until)
        if status.discontinuity is not None:
            fault = status.discontinuity
            if type(fault) is not Discontinuity or type(fault.reason) is not str or fault.reason != status.outcome:
                raise ValueError("Несогласованная граница детектора")
            for index in (fault.expected_index, fault.observed_index):
                if index is not None:
                    uint(index)
            if fault.expected_index != status.processed_until:
                raise ValueError("Граница отличается от обработанного префикса")
        elif status.outcome not in ("running", "stopped"):
            raise ValueError("Нет границы отказа детектора")
        previous_trigger = -1
        entries = self.events + ((status.pending,) if status.pending is not None else ())
        for offset, entry in enumerate(entries):
            if type(entry) is not EventEntry:
                raise ValueError("Нужны immutable события")
            for value in (entry.ordinal, entry.trigger_index, entry.start_index,
                          entry.end_index, entry.baseline_start, entry.baseline_end):
                uint(value)
            if (status.processed_until is None or
                    entry.ordinal != status.first_retained_ordinal + offset or
                    not previous_trigger < entry.trigger_index or
                    not entry.start_index <= entry.baseline_start <= entry.baseline_end == entry.trigger_index < entry.end_index <= status.processed_until or
                    entry.end_index - entry.start_index > self.config.max_event_samples):
                raise ValueError("Несогласованные исходные границы события")
            if entry.settled_index is not None:
                uint(entry.settled_index)
                if not entry.trigger_index < entry.settled_index <= entry.end_index:
                    raise ValueError("Несогласованная граница возвращения")
            if (type(entry.flags) is not tuple or len(entry.flags) > 16 or
                    any(type(flag) is not str or not 1 <= len(flag) <= 64 for flag in entry.flags) or
                    len(set(entry.flags)) != len(entry.flags)):
                raise ValueError("Нужны ограниченные immutable флаги события")
            pending = offset == len(self.events)
            if pending != ("pending" in entry.flags):
                raise ValueError("Незавершённое событие смешано с закрытыми")
            if pending and (status.outcome != "running" or entry.end_index != status.processed_until):
                raise ValueError("Несогласованный активный контекст")
            previous_trigger = entry.trigger_index


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    """Producer-owned immutable bounded display, not a durable recording receipt.

    Publish from any producer thread via post_live_snapshot. Envelope selection
    must match the panel's requested range, or end at the latest tail while
    following. Sequence is monotonic within the explicitly opened LiveHandle.
    """
    identity: StreamIdentity
    sequence: int
    first_sample_index: int
    end_sample_index: int
    envelope: Envelope
    loss_samples: int | None = None
    tail_state: str = "unknown"
    flags: tuple[str, ...] = ()
    raw_available_start: int | None = None
    event_index: LiveEventIndex | None = None

    def __post_init__(self):
        key = identity_key(self.identity)
        uint(self.sequence); uint(self.first_sample_index); uint(self.end_sample_index)
        if self.first_sample_index > self.end_sample_index:
            raise ValueError("Некорректный диапазон live snapshot")
        available = self.first_sample_index if self.raw_available_start is None else self.raw_available_start
        uint(available)
        if not self.first_sample_index <= available <= self.end_sample_index:
            raise ValueError("Некорректная граница live raw")
        if self.raw_available_start is not None and self.end_sample_index - available > DETAIL_SAMPLES:
            raise ValueError("Live raw превышает бюджет кольца")
        index = self.event_index
        if index is not None:
            if type(index) is not LiveEventIndex or self.raw_available_start is None:
                raise ValueError("Индексу событий нужна явная граница live raw")
            index.__post_init__()
            if index.handle.identity != key or index.sequence != self.sequence:
                raise ValueError("Метаданные событий относятся к другому snapshot")
            processed = index.status.processed_until
            if processed is None:
                if self.first_sample_index != self.end_sample_index:
                    raise ValueError("Нет обработанного префикса событий")
            elif not self.first_sample_index <= processed <= self.end_sample_index:
                raise ValueError("Индекс выходит за границы источника")
            if index.status.outcome == "running" and processed is not None and processed != self.end_sample_index:
                raise ValueError("Детектор не обработал каждый блок до snapshot")
            entries = index.events + ((index.status.pending,) if index.status.pending else ())
            if any(entry.start_index < self.first_sample_index for entry in entries):
                raise ValueError("Контекст предшествует началу источника")
        if self.loss_samples is not None:
            uint(self.loss_samples)
        if self.tail_state not in ("running", "unknown", "reported_stopped"):
            raise ValueError("Неизвестное состояние хвоста")
        if (type(self.flags) is not tuple or len(self.flags) > 16 or
                any(type(flag) is not str or not 1 <= len(flag) <= 128 for flag in self.flags)):
            raise ValueError("Флаги должны быть ограниченным неизменяемым кортежем")
        env = self.envelope
        if type(env) is not Envelope or env.identity != key or type(env.viewport) is not Viewport:
            raise ValueError("Envelope относится к другому потоку")
        env.viewport.__post_init__()
        if env.viewport.stop - env.viewport.start > DETAIL_SAMPLES:
            raise ValueError("Live snapshot превышает бюджет диапазона")
        if (type(env.segments) is not tuple or len(env.segments) > 1 or
                any(type(segment) is not tuple for segment in env.segments) or
                env.point_count > POINT_BUDGET or type(env.discontinuities) is not tuple or
                len(env.discontinuities) > 3):
            raise ValueError("Envelope превышает бюджет отображения")
        uint(env.samples_read, DETAIL_SAMPLES); uint(env.covered_until)
        if not env.viewport.start <= env.covered_until <= env.viewport.stop:
            raise ValueError("Несогласованное покрытие envelope")
        if env.outcome not in ("complete", "unavailable", "partial_range", "short_read", "loss",
                               "overlap", "range_violation", "identity_changed"):
            raise ValueError("Неизвестный результат envelope")
        for fault in env.discontinuities:
            if type(fault) is not Discontinuity or type(fault.reason) is not str or not 1 <= len(fault.reason) <= 64:
                raise ValueError("Некорректная граница envelope")
            for index in (fault.expected_index, fault.observed_index):
                if index is not None:
                    uint(index)
        previous = -1
        for segment in env.segments:
            for point in segment:
                if type(point) is not DisplayPoint:
                    raise ValueError("Нужны immutable исходные точки")
                uint(point.sample_index); uint(point.code, 65535)
                if not (previous < point.sample_index < env.covered_until and
                        env.viewport.start <= point.sample_index and
                        available <= point.sample_index < self.end_sample_index):
                    raise ValueError("Несогласованные индексы точек")
                previous = point.sample_index
        if env.samples_read:
            accepted_start = max(env.viewport.start, available)
            if (not env.segments or not env.segments[0] or
                    env.segments[0][0].sample_index != accepted_start or
                    env.segments[0][-1].sample_index != env.covered_until - 1 or
                    env.samples_read != env.covered_until - accepted_start):
                raise ValueError("Envelope не сохраняет реальные края принятого префикса")
        elif env.point_count:
            raise ValueError("Точки без принятых отсчётов")
        if env.outcome == "complete" and (env.covered_until != env.viewport.stop or
                env.samples_read != env.viewport.stop - env.viewport.start or env.discontinuities):
            raise ValueError("Неполное покрытие помечено complete")


@dataclass(frozen=True, slots=True)
class Summary:
    points: tuple[DisplayPoint, ...]
    covered_until: int
    bin_samples: int
    complete: bool


@dataclass(frozen=True, slots=True)
class ScanProgress:
    summary: Summary
    events: tuple[EventEntry, ...]
    detector_status: DetectorStatus | None


@dataclass(frozen=True, slots=True)
class PlotPayload:
    selection: Selection
    segments: tuple[tuple[tuple[float, ...], tuple[int, ...]], ...]
    covered_until: int
    outcome: str
    overview: bool = False
    bin_samples: int = 1


def _payload(selection, segments, fs, covered_until, outcome, *, overview=False, bin_samples=1):
    # Integer subtraction happens before float conversion, including >2**53.
    arrays = tuple((tuple((p.sample_index - selection.start) / fs for p in segment),
                    tuple(p.code for p in segment)) for segment in segments if segment)
    return PlotPayload(selection, arrays, covered_until, outcome, overview, bin_samples)


def _detail(recording, selection, cancelled):
    if cancelled.is_set():
        return None
    env = minmax_envelope(recording, Viewport(selection.start, selection.stop), POINT_BUDGET)
    if cancelled.is_set():
        return None
    reason = env.outcome
    if env.discontinuities:
        reason += "; " + "; ".join(d.reason for d in env.discontinuities)
    return _payload(selection, env.segments, recording.identity.nominal_fs_hz,
                    env.covered_until, reason)


def _overview(summary, selection, fs):
    points = tuple(p for p in summary.points if selection.start <= p.sample_index < selection.stop)
    return _payload(selection, (points,), fs, min(selection.stop, summary.covered_until),
                    "complete" if summary.complete else "building", overview=True,
                    bin_samples=summary.bin_samples)


class _Scan:
    """Worker-only mutable state; GUI receives immutable bounded snapshots."""
    def __init__(self, recording, config, capacity):
        self.recording = recording
        self.position = recording.first_sample_index
        self.bin_samples = max(1, (recording.end_sample_index - self.position + SUMMARY_BINS - 1) // SUMMARY_BINS)
        self.detector = EventDetector(config, capacity) if config is not None else None
        self.finished = False
        self._points = []
        self._bin = None
        self._first = self._low = self._high = self._last = None

    def _flush_bin(self):
        if self._first is not None:
            # At most four original samples per bin; never averaged/fabricated.
            unique = {p.sample_index: p for p in (self._first, self._low, self._high, self._last)}
            self._points.extend(unique[index] for index in sorted(unique))
            self._first = None

    def step(self, cancelled):
        recording = self.recording
        stop = min(recording.end_sample_index, self.position + SCAN_SAMPLES)
        expected_identity = identity_key(recording.identity)
        for block in recording.iter_raw(self.position, stop, chunk_samples=8192):
            if cancelled.is_set():
                return None
            if (identity_key(block.identity) != expected_identity or
                    block.first_sample_index != self.position or block.end_sample_index > stop):
                raise ValueError("Разрыв исходной записи при индексировании")
            if self.detector is not None and self.detector.status.outcome == "running":
                self.detector.feed(block)
            for index, (code,) in enumerate(struct.iter_unpack("<H", block.raw_u16le), self.position):
                bin_number = (index - recording.first_sample_index) // self.bin_samples
                if bin_number != self._bin:
                    self._flush_bin()
                    self._bin = bin_number
                point = DisplayPoint(index, code)
                if self._first is None:
                    self._first = self._low = self._high = point
                if code < self._low.code:
                    self._low = point
                if code > self._high.code:
                    self._high = point
                self._last = point
            self.position = block.end_sample_index
        if self.position != stop:
            raise ValueError("Неполное чтение исходной записи при индексировании")
        self.finished = self.position == recording.end_sample_index
        if self.finished:
            self._flush_bin()
            if self.detector is not None:
                terminal = recording.terminal
                reason = "stopped" if recording.clean_stop_reported else (
                    "source_fault" if terminal and terminal.failure else "unknown_loss")
                self.detector.finish(reason)
        points = tuple(self._points)
        if self._first is not None:
            unique = {p.sample_index: p for p in (self._first, self._low, self._high, self._last)}
            points += tuple(unique[index] for index in sorted(unique))
        status = self.detector.status if self.detector else None
        entries = () if self.detector is None else tuple(
            event for offset in range(0, status.indexed_events, 256)
            for event in self.detector.page(offset, 256))
        return ScanProgress(Summary(points, self.position, self.bin_samples, self.finished), entries, status)


@dataclass(frozen=True, slots=True)
class _Request:
    source: int
    revision: int
    kind: str
    recording: object
    operation: Callable


class _Mailbox:
    """One active job/result and one latest live input. No QObject references."""
    def __init__(self):
        self.lock = threading.Lock()
        self.result = None
        self.running = False
        self.closed = False
        self.live_handle = None
        self.live = None
        self.live_sequence = -1
        self.live_config = None
        self.live_config_revision = 0
        self.live_progress = None
        self.live_terminal = None
        self.cancelled = None

    def start(self, request):
        with self.lock:
            if self.closed or self.running:
                raise RuntimeError("Worker mailbox is busy or closed")
            self.running = True

        def run():
            try:
                result, error = request.operation(), None
            except Exception as exc:
                result, error = None, f"{type(exc).__name__}: {exc}"
            with self.lock:
                if not self.closed:
                    self.result = (request, result, error)
                self.running = False
        # No QWidget, Qt signal or Qt thread object survives shutdown in this job.
        threading.Thread(target=run, name="stream-viewer", daemon=True).start()

    def close(self):
        with self.lock:
            self.closed = True
            if self.cancelled is not None:
                self.cancelled.set()
            self.live = self.result = self.live_handle = None


def _open_archive(path):
    # Dispatch only the fixed magic, under the storage module's existing path,
    # size and mutation guards. The chosen strict reader owns all parsing; an
    # invalid v2 file must never fall back to the v1 reader. Keep this observation
    # open across recovery so a replacement between the two opens also rejects.
    path = Path(path).absolute()
    with storage._open_attempt_read(path) as stream:
        observation = stream._stream_observation
        storage._unchanged(stream, path, observation)
        magic = stream.read(len(storage.MAGIC))
        if magic == storage.MAGIC:
            reader = recover
        elif magic == storage.ATTEMPT_MAGIC:
            reader = recover_attempt
        else:
            raise StreamError("Неизвестная или обрезанная версия .mtstream")
        storage._unchanged(stream, path, observation)
        result = reader(path)
        storage._unchanged(stream, path, observation)
        return result


def _terminal_status(terminal, identity):
    loss = "неизвестны" if terminal.loss_samples is None else str(terminal.loss_samples)
    profile = profile_for_identity(identity)
    generated = "generated end" if profile is None else "completed DMA end"
    scope = "" if profile is None else f" Домен счётчиков: {profile.accounting_domain}."
    text = (f"Терминал: {terminal.reason}; наблюдался: {terminal.terminal_observed}; "
            f"{generated}: {terminal.device_generated_end_sample_index}; transmitted end: {terminal.device_transmitted_end_sample_index}; "
            f"потери, заявлено: {loss} отсчётов. Это счётчик источника, не сумма потерь файла. "
            f"Stop запрошен: {terminal.stop_requested}; request id: {terminal.stop_request_id}; "
            f"основание: {terminal.boundary_evidence}.{scope}")
    if terminal.failure:
        failure = terminal.failure
        text += (f" Первый отказ: {failure.code}: {failure.detail}; операция: {failure.operation}; "
                 f"request id: {failure.request_id}; source code: {failure.source_code}.")
    return text


def _recovery_status(source):
    if source.recovery_issue:
        return (f" Восстановление: {source.recovery_issue}; отброшено {source.discarded_tail_bytes} байт, "
                "это не число потерянных отсчётов.")
    return ""


_ARCHIVE_CAVEAT = (" Читаемые байты не доказывают успешный возврат прежнего fsync. "
                   "Заявления файла не доказывают физическую остановку или окончание разряда.")


def _storage_status(recording):
    terminal = recording.terminal
    text = (f"R = {recording.first_sample_index}; основание: {recording.start.boundary_evidence}. "
            f"Проверенный raw-префикс [{recording.first_sample_index}, {recording.end_sample_index}).")
    text += _recovery_status(recording)
    if terminal is None:
        text += " Терминал отсутствует; хвост неизвестен; потери, заявлено: неизвестны."
    else:
        text += " " + _terminal_status(terminal, recording.identity)
        if not recording.clean_stop_reported:
            text += " Полнота хвоста не подтверждена."
        elif profile_for_identity(recording.identity) is not None:
            text += " Чистое завершение заявлено только в домене completed-dma-v1."
    return text + _ARCHIVE_CAVEAT


def _attempt_status(attempt):
    # A selected S/B without C/T is evidence of R, but is not a recoverable
    # (even empty) recording. A preparation header alone establishes neither.
    if attempt.selected_start is None:
        text = "R не установлена в файле; интервал записи не определён."
    else:
        start = attempt.selected_start
        text = (f"В файле есть свидетельство R = {start.first_sample_index}; "
                f"основание: {start.boundary_evidence}. Подтверждённого raw-префикса нет.")
    if attempt.unbound_outcome is None:
        text += " Сохранена подготовка; терминальный исход неизвестен. Заголовок подготовки не доказывает, что END опередил DATA."
    else:
        text += " " + _terminal_status(attempt.unbound_outcome, attempt.preparation.identity)
    return text + _recovery_status(attempt) + _ARCHIVE_CAVEAT


class StreamViewerPanel(QtWidgets.QWidget):
    """Embed in the existing application. All methods except post_live are GUI-only.

    Closing is final. A blocked OS read cannot be force-cancelled; its detached
    bounded worker may finish later without touching Qt or reviving the source.
    """
    view_requested = QtCore.Signal(object)  # User intent only, not per-block traffic.

    def __init__(self, parent=None, *, event_config=None, event_capacity=EVENT_CAPACITY):
        super().__init__(parent)
        if event_config is not None and type(event_config) is not EventConfig:
            raise ValueError("Нужна явная конфигурация порогового индекса")
        if event_config is not None:
            event_config.__post_init__()
        uint(event_capacity, EVENT_CAPACITY, minimum=1)
        self._config, self._capacity = event_config, event_capacity
        self._mailbox = _Mailbox()
        self._cancel = threading.Event()
        self._mailbox.cancelled = self._cancel
        self._source = self._revision = 0
        self._pending = None
        self._closed = False
        self._recording = self._identity = self._scan = self._summary = None
        self._events = ()
        self._event = None
        self._first = self._end = None
        self.selection = None
        self.follow_tail = False
        self._updating_plot = False
        self._display_origin = 0
        self._last_live = None
        self._latest_live_sequence = -1
        self._source_failure = None
        self._live_limit_notice = None
        self._event_notice = None
        self._raw_first = None
        self.setObjectName("root")
        self.setStyleSheet(STYLE)
        self._build_ui()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        # Deleting a panel without close still invalidates the thread-safe sink.
        mailbox = self._mailbox
        self.destroyed.connect(lambda: mailbox.close())
        self._actions()

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # The embeddable panel may be smaller than its readable content. Keep
        # plot/status geometries intact and make that content reachable instead
        # of squeezing height-for-width labels over the axis.
        self.scroll_area = QtWidgets.QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content = QtWidgets.QWidget(objectName="root")
        layout = QtWidgets.QVBoxLayout(self.content)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        self.scroll_area.setWidget(self.content)
        outer.addWidget(self.scroll_area)
        self.origin_label = _label("НЕТ ПОТОКОВОЙ ЗАПИСИ", "badge", wrap=True)
        layout.addWidget(self.origin_label)
        self.source_label = _label("Компонент просмотра; подключение прибора здесь не реализовано.", "muted", wrap=True)
        layout.addWidget(self.source_label)
        row = QtWidgets.QHBoxLayout()
        for name, text, slot in (("open_button", "Открыть .mtstream…", self.open_dialog),
                                  ("whole_button", "Вся запись", self.show_whole_recording),
                                  ("previous_button", "Предыдущее событие", self.previous_event),
                                  ("next_button", "Следующее событие", self.next_event),
                                  ("follow_button", "К хвосту", self.follow)):
            button = QtWidgets.QPushButton(text)
            setattr(self, name, button)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        self.plot = pg.PlotWidget(background="#111923")
        self.plot.setObjectName("streamPlot")
        self.plot.setMinimumHeight(220)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("left", "Исходный код uint16 · без калибровки")
        self.plot.setLabel("bottom", "Время от локального начала, с · номинальная Fs")
        self.plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.disableAutoRange()
        self.plot.setYRange(0, 65535, padding=0)
        for name in ("left", "bottom"):
            axis = self.plot.getAxis(name)
            axis.setPen(pg.mkPen("#63738b"))
            axis.setTextPen(pg.mkPen("#c1cfe0"))
            axis.enableAutoSIPrefix(False)
        self.curve = self.plot.plot([], [], pen=pg.mkPen("#79dcc8", width=1.7))
        self.plot.getViewBox().sigRangeChangedManually.connect(self._manual_range)
        layout.addWidget(self.plot, 1)
        self.range_label = _label("Диапазон не выбран", "muted", wrap=True)
        self.storage_label = _label("Терминал и потери неизвестны", "quality", wrap=True)
        self.index_label = _label("Обзор и индекс не построены", "muted", wrap=True)
        self.event_label = _label("Пороговые события служат навигации; горение, затухание и цилиндр не определяются.", "muted", wrap=True)
        self.status_label = _label("Готово к открытию синтетической записи", "muted", wrap=True)
        for label in (self.range_label, self.storage_label, self.index_label, self.event_label, self.status_label):
            label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)

    def _set_status(self, text):
        if self._source_failure:
            continuation = "" if text == self._source_failure else " " + text
            text = f"{self._source_failure}\nИсточник отклонён; кеш относится к прежним проверенным байтам.{continuation}"
        elif self._event_notice:
            text = f"{self._event_notice} {text}"
        elif self._live_limit_notice:
            text = f"{self._live_limit_notice} {text}"
        self.status_label.setText(text)

    def _latch_source_failure(self, message):
        if self._source_failure is None:
            self._source_failure = message
            self.origin_label.setText(self.origin_label.text() + " · ИСТОЧНИК ОТКЛОНЁН · КЕШ")
            self.storage_label.setText(self.storage_label.text() +
                "\nИСТОЧНИК ОТКЛОНЁН: " + message +
                ". Кеш показывает ранее проверенные байты; повторно откройте действительный источник.")
        self._scan = None

    @property
    def busy(self):
        with self._mailbox.lock:
            return bool(self._mailbox.running or self._mailbox.result or self._mailbox.live or
                        self._pending or self._scan is not None)

    def _actions(self):
        loaded = self._identity is not None and self.selection is not None and not self._closed
        self.whole_button.setEnabled(loaded and self._recording is not None)
        self.follow_button.setEnabled(loaded)
        self.previous_button.setEnabled(bool(self._events) and not self._closed)
        self.next_button.setEnabled(bool(self._events) and not self._closed)
        self.open_button.setEnabled(not self._closed)

    def _reset_source(self):
        if self._closed:
            raise RuntimeError("Панель закрыта")
        self._cancel.set()
        self._cancel = threading.Event()
        self._source += 1
        self._revision += 1
        self._pending = None
        self._recording = self._identity = self._scan = self._summary = None
        self._events = ()
        self._event = None
        self._first = self._end = self.selection = None
        self._last_live = None
        self._latest_live_sequence = -1
        self._source_failure = None
        self._live_limit_notice = None
        self._event_notice = None
        self._raw_first = None
        self.follow_tail = False
        with self._mailbox.lock:
            self._mailbox.cancelled = self._cancel
            self._mailbox.live_handle = self._mailbox.live = None
            self._mailbox.live_sequence = -1
            self._mailbox.live_config = None
            self._mailbox.live_config_revision = 0
            self._mailbox.live_progress = None
            self._mailbox.live_terminal = None
        self.curve.setData([], [])
        self.origin_label.setText("НЕТ ПОТОКОВОЙ ЗАПИСИ")
        self.range_label.setText("Диапазон не выбран")
        self.storage_label.setText("Терминал и потери неизвестны")
        self.index_label.setText("Обзор и индекс не построены")
        self.event_label.setText("Пороговые события служат навигации; полный физический разряд не гарантируется.")
        self._actions()

    def open_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Открыть потоковую запись", "", "Потоковая запись (*.mtstream);;Все файлы (*)")
        if path:
            self.open_path(path)

    def open_path(self, path):
        self._reset_source()
        path = Path(path)
        self.source_label.setText(f"Открытие {path.name}; проверка файла вне GUI")
        self._set_status("Проверка bounded journal…")
        self._submit(_Request(self._source, self._revision, "open", None, lambda: _open_archive(path)))

    def load_recording(self, recording):
        if type(recording) is not StreamRecording:
            raise ValueError("Нужен immutable StreamRecording после recover() или recover_attempt()")
        self._reset_source()
        self._accept_recording(recording)

    def _accept_archive(self, archive):
        if type(archive) is not AttemptReport:
            self._accept_recording(archive)
        elif archive.recording is not None:
            self._accept_recording(archive.recording)
        else:
            preparation = archive.preparation
            self._identity = identity_key(preparation.identity)
            self._origin(preparation.identity.origin)
            self.source_label.setText(f"{archive.path.name} · попытка {preparation.recording_id} · сеанс {preparation.identity.session_id} · generation {preparation.identity.generation} · правило {preparation.start_policy}")
            self.source_label.setText(self.source_label.text() + "\n" + provenance_text(preparation.identity))
            self.storage_label.setText(_attempt_status(archive))
            self.range_label.setText("Диапазон просмотра отсутствует: восстановленная запись не установлена")
            self.index_label.setText("Обзор и события не строятся: восстановленного raw-префикса нет")
            self._set_status("Нет восстановленной записи; график отсутствует. Отсутствие отсчётов не доказывает отсутствие разряда")
            self._actions()

    def _accept_recording(self, recording):
        self._recording = recording
        self._identity = identity_key(recording.identity)
        self._first, self._end = recording.first_sample_index, recording.end_sample_index
        self.source_label.setText(f"{recording.path.name} · запись {recording.start.recording_id} · сеанс {recording.identity.session_id} · generation {recording.identity.generation} · Fs ном. {recording.identity.nominal_fs_hz} Гц")
        self.source_label.setText(self.source_label.text() + "\n" + provenance_text(recording.identity))
        self._origin(recording.identity.origin)
        self.storage_label.setText(_storage_status(recording))
        self._scan = _Scan(recording, self._config, self._capacity)
        self.index_label.setText("Обзор строится вне GUI; индекс событий: " + ("строится" if self._config else "параметры не заданы"))
        if self._end > self._first:
            self.select_range(self._first, min(self._end, self._first + DETAIL_SAMPLES))
        else:
            self._set_status("Пустая запись; отсутствие отсчётов не доказывает отсутствие разряда")
        self._actions()

    def _origin(self, origin):
        self.origin_label.setText("СИНТЕТИЧЕСКИЙ ИСТОЧНИК · НЕ ИЗМЕРЕНИЕ" if origin == "synthetic" else "ЗАЯВЛЕН ИСТОЧНИК DEVICE · ПРОИСХОЖДЕНИЕ НЕ ПРОВЕРЕНО")

    def begin_live(self, identity, *, event_config=None, event_config_revision=0):
        key = identity_key(identity)
        uint(event_config_revision)
        if event_config is not None:
            if type(event_config) is not EventConfig:
                raise ValueError("Нужна immutable конфигурация live событий")
            event_config.__post_init__()
        self._reset_source()
        self._identity = key
        self.follow_tail = True
        handle = LiveHandle(self._source, key)
        with self._mailbox.lock:
            self._mailbox.live_handle = handle
            self._mailbox.live_config = event_config
            self._mailbox.live_config_revision = event_config_revision
        self._origin(identity.origin)
        self.source_label.setText(f"Предвычисленный live display · {identity.session_id} · generation {identity.generation}; live ≤{DETAIL_SAMPLES} отсчётов; transport здесь отсутствует")
        self.source_label.setText(self.source_label.text() + "\n" + provenance_text(identity))
        self.index_label.setText(f"Live: диапазон не более {DETAIL_SAMPLES} отсчётов; только bounded envelope. Обзор/события всего сеанса здесь не индексируются")
        self._set_status("Ожидание первого bounded snapshot")
        return handle

    def post_live_snapshot(self, handle, snapshot):
        """Thread-safe latest-only ingress; no signal per block, no Qt access."""
        if type(handle) is not LiveHandle or type(snapshot) is not LiveSnapshot:
            raise ValueError("Нужны LiveHandle и immutable LiveSnapshot")
        # Bounded nested validation must not hold the producer/consumer lock.
        snapshot.__post_init__()
        index = snapshot.event_index
        progress = None
        if index is not None:
            status = index.status
            progress = (snapshot.first_sample_index, snapshot.end_sample_index,
                        snapshot.raw_available_start, status.next_ordinal,
                        status.evicted_events, status.processed_until)
        with self._mailbox.lock:
            if ((index is None) != (self._mailbox.live_config is None) or
                    (index is not None and (index.handle != handle or
                     index.config != self._mailbox.live_config or
                     index.config_revision != self._mailbox.live_config_revision))):
                return False
            terminal = self._mailbox.live_terminal
            if index is not None and terminal is not None and terminal != (status.outcome, status.discontinuity):
                return False
            previous = self._mailbox.live_progress
            if previous is not None and progress is not None:
                if (progress[0] != previous[0] or
                        any(new < old for new, old in zip(progress[1:], previous[1:])
                            if new is not None and old is not None)):
                    return False
            if (self._mailbox.closed or handle != self._mailbox.live_handle or
                    handle.identity != identity_key(snapshot.identity) or
                    snapshot.sequence <= self._mailbox.live_sequence):
                return False
            self._mailbox.live_sequence = snapshot.sequence
            self._mailbox.live_progress = progress
            if index is not None and status.outcome != "running":
                self._mailbox.live_terminal = (status.outcome, status.discontinuity)
            self._mailbox.live = snapshot
            return True

    def _consume_live(self, snapshot):
        self._latest_live_sequence = snapshot.sequence
        self._first, self._end = snapshot.first_sample_index, snapshot.end_sample_index
        self._last_live = snapshot
        self._raw_first = (snapshot.first_sample_index if snapshot.raw_available_start is None
                           else snapshot.raw_available_start)
        index = snapshot.event_index
        if index is not None:
            self._events = index.events
            status, cfg = index.status, index.config
            text = (f"Recent live raw [{self._raw_first}, {self._end}); источник [{self._first}, {self._end}). "
                    f"События {status.indexed_events}/{index.capacity}; ordinal [{status.first_retained_ordinal}, {status.next_ordinal}); "
                    f"вытеснено метаданных: {status.evicted_events}; {status.outcome}; processed_until {status.processed_until}. "
                    f"Config revision {index.config_revision}: порог {cfg.threshold_code}/{cfg.release_code}, {cfg.polarity}; "
                    f"pre {cfg.pre_samples}, post {cfg.post_samples}, baseline {cfg.baseline_samples}, "
                    f"holdoff {cfg.holdoff_samples}, max context {cfg.max_event_samples}; rails {cfg.rail_low}/{cfg.rail_high}. "
                    f"Подавлено пересечений: {status.suppressed_crossings}. Это не потери архива.")
            if status.pending:
                pending = status.pending
                text += f" Незавершённый ordinal {pending.ordinal}: [{pending.start_index}, {pending.end_index}); {', '.join(pending.flags)}."
                if pending.start_index < self._raw_first:
                    text += " Часть активного контекста уже недоступна в live-памяти."
            if status.discontinuity:
                text += f" Граница детектора: {status.discontinuity.reason} ({status.discontinuity.expected_index}, {status.discontinuity.observed_index})."
            self.index_label.setText(text)
        loss = "неизвестны" if snapshot.loss_samples is None else str(snapshot.loss_samples)
        profile = profile_for_identity(snapshot.identity)
        scope = "" if profile is None else f" Домен счётчиков: {profile.accounting_domain}."
        self.storage_label.setText(f"Live snapshot #{snapshot.sequence}; потери, заявлено: {loss}; хвост: {snapshot.tail_state}; "
                                   f"флаги: {', '.join(snapshot.flags) or 'не сообщены'}.{scope} Отображение не подтверждает сохранение на диск.")
        self._actions()
        if self._event is not None and not self._live_event_available(self._event):
            self._actions()
            return
        env = snapshot.envelope
        incoming = Selection(env.viewport.start, env.viewport.stop)
        if self.selection is None:
            self.selection = incoming
        if self.follow_tail:
            width = self.selection.width
            if incoming.stop != self._end or incoming.width != min(width, self._end - self._raw_first):
                self._set_status("Ожидается envelope хвоста с выбранной шириной")
                return
            self.selection = incoming
        if self.selection != incoming:
            self._set_status("Выбор удерживается; snapshot другого диапазона не заменяет график")
            return
        self._revision += 1
        selection, fs, cancelled = self.selection, snapshot.identity.nominal_fs_hz, self._cancel
        def operation():
            if cancelled.is_set():
                return None
            snapshot.__post_init__()
            outcome = env.outcome
            if env.discontinuities:
                outcome += "; " + "; ".join(d.reason for d in env.discontinuities)
            return _payload(selection, env.segments, fs, env.covered_until, outcome)
        self._submit(_Request(self._source, self._revision, "live", snapshot, operation))
        self._actions()

    def select_range(self, start, stop):
        if self._identity is None or self._first is None or self._first == self._end:
            return
        uint(start); uint(stop)
        if start >= stop:
            raise ValueError("Пустой диапазон просмотра")
        requested_width = stop - start
        first = self._first if self._recording is not None else self._raw_first
        width = min(requested_width, self._end - first)
        if width <= 0:
            self._set_status("В live-памяти нет доступных отсчётов")
            return
        if self._recording is None:
            self._event = None
            self._event_notice = None
        self._live_limit_notice = None
        if self._recording is None and width > DETAIL_SAMPLES:
            # Saved-file summary can exceed the raw detail budget. Live has no
            # such producer API, so never emit an unrepresentable Selection.
            width = DETAIL_SAMPLES
            start += (requested_width - width) // 2
            self._live_limit_notice = f"Диапазон live ограничен {DETAIL_SAMPLES} отсчётами; более широкий live-обзор недоступен."
        start = max(first, min(start, self._end - width))
        self.selection = Selection(start, start + width)
        self.follow_tail = False
        self._revision += 1
        if self._live_limit_notice:
            # Undo the unsupported wheel scale immediately, before its legal
            # producer response arrives; cached points keep their old origin.
            self._updating_plot = True
            try:
                fs = self._identity[3]
                self.plot.setXRange((self.selection.start - self._display_origin) / fs,
                                    (self.selection.stop - self._display_origin) / fs, padding=0)
            finally:
                self._updating_plot = False
        self._request_plot()
        self._actions()

    def _request_plot(self):
        if self.selection is None:
            return
        selection, recording = self.selection, self._recording
        self.range_label.setText(f"Выбран raw [{selection.start}, {selection.stop}); {selection.width} отсчётов. Ожидание отображения…")
        if recording is None:
            self.view_requested.emit(selection)
            self._set_status("Ожидание precomputed envelope выбранного диапазона")
            return
        if selection.width <= DETAIL_SAMPLES:
            cancelled = self._cancel
            operation = lambda: _detail(recording, selection, cancelled)
        elif self._summary is not None:
            summary, fs = self._summary, recording.identity.nominal_fs_hz
            operation = lambda: _overview(summary, selection, fs)
        else:
            self._set_status("Широкий диапазон: ожидается ограниченный обзорный индекс")
            return
        self._submit(_Request(self._source, self._revision, "plot", recording, operation))

    def show_whole_recording(self):
        if self._recording is not None and self._end > self._first:
            self.select_range(self._first, self._end)

    def follow(self):
        if self.selection is None:
            return
        width = min(self.selection.width, DETAIL_SAMPLES, self._end - (self._raw_first if self._recording is None else self._first))
        if width <= 0:
            self._set_status("В live-памяти нет доступных отсчётов")
            return
        self.select_range(self._end - width, self._end)
        self.follow_tail = True
        if self._last_live is not None:
            self._consume_live(self._last_live)

    def _manual_range(self, axes):
        if self._updating_plot or self.selection is None:
            return
        # Vertical-only interaction must not pause tail or change raw selection.
        if not axes[0]:
            return
        bounds = self.plot.getViewBox().viewRange()[0]
        fs = self._identity[3]
        if not all(math.isfinite(value) for value in bounds):
            return
        start = self._display_origin + math.floor(bounds[0] * fs)
        stop = self._display_origin + math.ceil(bounds[1] * fs)
        start = max(0, min((1 << 64) - 2, start))
        stop = max(start + 1, min((1 << 64) - 1, stop))
        self.select_range(start, stop)

    def _live_event_available(self, event):
        index = self._last_live.event_index if self._last_live is not None else None
        reason = None
        if index is not None and event.ordinal < index.status.first_retained_ordinal:
            reason = f"Выбранный ordinal {event.ordinal} вытеснен из live-индекса; выберите событие заново."
        elif not self._raw_first <= event.start_index < event.end_index <= self._end:
            reason = f"Полный исходный контекст ordinal {event.ordinal} недоступен в live-памяти."
        if reason is None:
            return True
        self._event_notice = reason + " Это не свидетельство потери raw в архиве."
        self.selection = Selection(event.start_index, event.end_index)
        self.follow_tail = False
        self._revision += 1
        self._pending = None
        self.curve.setData([], [])
        self.range_label.setText(f"Исходный контекст [{event.start_index}, {event.end_index}); unavailable live raw [{self._raw_first}, {self._end}). Диапазон не подменён.")
        self._set_status("После Stop откройте завершённый файл для полного raw-доступа.")
        return False

    def _select_event(self, event):
        self._event_notice = None
        self._event = event
        self.event_label.setText(f"Пороговое событие #{event.ordinal + 1} (ordinal {event.ordinal}): trigger {event.trigger_index}; исходный контекст [{event.start_index}, {event.end_index}); "
                                f"baseline [{event.baseline_start}, {event.baseline_end}); settled {event.settled_index}; "
                                f"флаги: {', '.join(event.flags) or 'не выявлены правилом'}. Это не подтверждение полного физического разряда.")
        if self._recording is None and not self._live_event_available(event):
            return
        self.select_range(event.start_index, event.end_index)
        self._event = event

    def next_event(self):
        if not self._events:
            return
        if self._event is not None:
            at = bisect_left(self._events, self._event.ordinal + 1, key=lambda entry: entry.ordinal)
        else:
            at = bisect_left(self._events, self.selection.start, key=lambda entry: entry.trigger_index)
        if at < len(self._events):
            self._select_event(self._events[at])
        else:
            self._set_status("Следующего события в обработанном префиксе нет")

    def previous_event(self):
        if not self._events:
            return
        if self._event is not None:
            at = bisect_left(self._events, self._event.ordinal, key=lambda entry: entry.ordinal) - 1
        else:
            at = bisect_left(self._events, self.selection.stop, key=lambda entry: entry.trigger_index) - 1
        if at >= 0:
            self._select_event(self._events[at])
        else:
            self._set_status("Предыдущего события в обработанном префиксе нет")

    def _submit(self, request):
        if self._closed:
            return
        with self._mailbox.lock:
            occupied = self._mailbox.running or self._mailbox.result is not None
        if occupied:
            self._pending = request  # Replace, never accumulate mouse/live jobs.
        else:
            self._pending = None
            self._mailbox.start(request)

    def _poll(self):
        if self._closed:
            return
        with self._mailbox.lock:
            completed, live = self._mailbox.result, self._mailbox.live
            self._mailbox.result = self._mailbox.live = None
        if live is not None:
            self._consume_live(live)
        if completed is not None:
            request, result, error = completed
            source_matches = request.source == self._source
            if request.kind in ("plot", "scan"):
                source_matches = source_matches and request.recording is self._recording
            if request.kind == "live":
                source_matches = source_matches and request.recording.sequence == self._latest_live_sequence
            current = source_matches and (request.kind == "scan" or request.revision == self._revision)
            # A stale selection cannot revive pixels, but a read failure still
            # belongs to this exact current source. Do not hide that evidence
            # merely because the user navigated before the worker returned.
            if source_matches and error and request.kind in ("plot", "scan"):
                message = f"Ошибка {request.kind}: {error}"
                self._latch_source_failure(message)
                self._set_status(message)
            if current:
                if error:
                    message = f"Ошибка {request.kind}: {error}"
                    self._set_status(message)
                    if request.kind == "scan":
                        self._scan = None
                        self.index_label.setText("Обзор/индекс неполны: ошибка чтения; сохранён только обработанный префикс")
                elif result is not None:
                    if request.kind == "open":
                        self._accept_archive(result)
                    elif request.kind == "scan":
                        self._accept_scan(result)
                    else:
                        self._draw(result)
        with self._mailbox.lock:
            idle = not self._mailbox.running and self._mailbox.result is None
        if idle and self._pending is not None:
            pending, self._pending = self._pending, None
            self._mailbox.start(pending)
        elif idle and self._scan is not None:
            scan, cancelled = self._scan, self._cancel
            self._mailbox.start(_Request(self._source, 0, "scan", self._recording,
                                         lambda: scan.step(cancelled)))

    def _accept_scan(self, progress):
        self._summary, self._events = progress.summary, progress.events
        status = progress.detector_status
        state = "готов" if progress.summary.complete else "строится; неполный"
        text = f"Обзор {state}: проверен [{self._first}, {progress.summary.covered_until}); bin≤{progress.summary.bin_samples} отсчётов, ≤{SUMMARY_BINS} bins."
        if status is not None:
            text += (f" Порог {self._config.threshold_code}/{self._config.release_code}, {self._config.polarity}; "
                     f"pre {self._config.pre_samples}, post {self._config.post_samples}, max context {self._config.max_event_samples} отсчётов.")
            text += f" Индекс событий: {status.indexed_events}/{self._capacity}; {status.outcome}; processed_until {status.processed_until}."
            if status.pending:
                text += f" Незавершённое событие: [{status.pending.start_index}, {status.pending.end_index}); {', '.join(status.pending.flags)}."
            if status.discontinuity:
                text += f" Граница индекса: {status.discontinuity.reason}."
        else:
            text += " Пороговые параметры не заданы; события не индексируются."
        self.index_label.setText(text)
        if progress.summary.complete:
            self._scan = None
        if self.selection is not None and self.selection.width > DETAIL_SAMPLES:
            self._request_plot()
        self._actions()

    def _draw(self, payload):
        if payload.selection != self.selection:
            return
        # Qt paint is necessarily on GUI; read, scan, min/max and float conversion
        # have already finished in the worker. NaN separators never bridge gaps.
        x, y = [], []
        for xs, ys in payload.segments:
            if x:
                x.append(float("nan")); y.append(float("nan"))
            x.extend(xs); y.extend(ys)
        self._updating_plot = True
        try:
            self.curve.setData(x, y, connect="finite", symbol="o" if len(x) == 1 else None,
                               symbolSize=4, symbolBrush="#79dcc8")
            self._display_origin = payload.selection.start
            fs = self._identity[3]
            # Half-open view includes the final sample and preserves DC extent.
            self.plot.setXRange(0, payload.selection.width / fs, padding=0)
        finally:
            self._updating_plot = False
        kind = f"ОБЗОР · bin≤{payload.bin_samples}; границы частичных bins приблизительны" if payload.overview else "RAW DETAIL · first/min/max/last исходные точки"
        self.range_label.setText(f"{kind}\nRaw [{payload.selection.start}, {payload.selection.stop}); локальный ноль = {payload.selection.start}; "
                                f"покрытие до {payload.covered_until}; {payload.outcome}; Fs ном. {fs} Гц.")
        self._set_status("К хвосту" if self.follow_tail else "Выбранный диапазон удерживается")

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._source += 1
        self._cancel.set()
        self._pending = self._scan = None
        self._timer.stop()
        self._mailbox.close()
        self.index_label.setText("Просмотр закрыт; незавершённый обзор/индекс отменён")
        self._actions()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

"""Dark viewer with explicit bounded acquisition through a worker thread.

Run ``python -m motortester.gui``. ``--screenshot PATH`` exercises a disk
round-trip and captures this actual Qt window; it is synthetic evidence only.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
from typing import Callable

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from .emulator import demo_message, from_message
from .model import Capture, CaptureError
from . import recording


STYLE = """
QMainWindow, QWidget#root { background: #10151f; color: #e5edf7; }
QWidget { color: #e5edf7; font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 13px; }
QFrame#sidebar, QFrame#plotCard, QFrame#metric { background: #171f2c; border: 1px solid #293346; border-radius: 12px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 26px; font-weight: 650; }
QLabel#subtitle, QLabel#muted { color: #a8b7cd; }
QLabel#section { color: #a8b7cd; font-size: 11px; font-weight: 650; }
QLabel#badge { color: #f3bf6d; background: #30291f; border: 1px solid #625037; border-radius: 6px; padding: 7px 10px; font-weight: 650; }
QLabel#metricValue { font-size: 23px; font-weight: 650; }
QLabel#quality { color: #f3bf6d; background: #232a35; border-radius: 6px; padding: 10px; }
QPushButton { background: #242f42; border: 1px solid #3a4960; border-radius: 7px; padding: 10px 12px; text-align: left; }
QPushButton:hover { background: #30405a; border-color: #6a86a8; }
QPushButton:pressed { background: #1d2838; }
QPushButton:focus { border: 2px solid #75dbca; }
QPushButton:disabled { color: #67778c; background: #1c2533; border-color: #2b3546; }
QPushButton#primary { color: #102623; background: #79dcc8; border-color: #79dcc8; font-weight: 650; }
QPushButton#primary:hover { background: #9becdb; }
QPushButton#primary:disabled { color: #607b79; background: #2b4947; border-color: #345955; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #68819f; border-radius: 3px; background: #202c40; }
QCheckBox::indicator:checked { background: #79dcc8; border-color: #b4f6e9; }
QStatusBar { background: #171f2c; color: #bdcce0; border-top: 1px solid #293346; }
QStatusBar::item { border: none; }
QMessageBox, QFileDialog, QDialog { background: #171f2c; }
QTextBrowser { background: #111923; border: 1px solid #3a4960; padding: 14px; }
QScrollArea { border: none; background: #232a35; }
QScrollBar:vertical { background: #171f2c; width: 12px; }
QScrollBar::handle:vertical { background: #61718c; min-height: 20px; }
QLineEdit, QTreeView, QListView { background: #10151f; color: #e5edf7; selection-background-color: #365772; }
QToolTip { color: #e5edf7; background: #263348; border: 1px solid #61718c; }
"""


HELP_PATH = Path(__file__).resolve().parent.parent / "docs" / "help.md"

def _label(text: str = "", name: str = "", *, wrap: bool = False) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _number(value: float, decimals: int = 3) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


class _JobSignals(QtCore.QObject):
    finished = QtCore.Signal(object, object)


class _FileJob(QtCore.QRunnable):
    """Only files/models cross the thread boundary; Qt widgets never do."""

    def __init__(self, operation: Callable):
        super().__init__()
        self.operation = operation
        self.signals = _JobSignals()

    def run(self):
        try:
            result = self.operation()
        except FileNotFoundError:
            self.signals.finished.emit(None, "Файл или папка не найдены")
        except PermissionError:
            self.signals.finished.emit(None, "Нет прав на чтение или запись")
        except FileExistsError:
            self.signals.finished.emit(None, "Файл уже существует; требуется подтверждение замены")
        except IsADirectoryError:
            self.signals.finished.emit(None, "Вместо файла выбрана папка")
        except (OSError, CaptureError, ValueError) as exc:
            self.signals.finished.emit(None, str(exc))
        except Exception:
            # A failed worker must always release the UI's busy state. Unknown
            # exceptions are logged for development, not disguised as success.
            import traceback
            traceback.print_exc()
            self.signals.finished.emit(None, "Непредвиденная ошибка файловой операции")
        else:
            self.signals.finished.emit(result, None)


class _WaveformPlot(pg.PlotWidget):
    def sizeHint(self):
        return QtCore.QSize(640, 100)

    def minimumSizeHint(self):
        return QtCore.QSize(100, 100)


class MainWindow(QtWidgets.QMainWindow):
    """One immutable current capture; failed/cancelled opens cannot replace it."""

    operation_finished = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.capture: Capture | None = None
        self.current_path: Path | None = None
        self.opened_from_disk = False
        self.busy = False
        self._closing = False
        self._job: _FileJob | None = None
        self._completion: Callable | None = None
        self._failure_label = ""
        self._sequence = 0
        self._moving_cursor = False
        self._help_dialog = None
        self._acquisition_dialog = None
        self._acquisition = None
        self._acq_state = None
        self._acq_requested = False
        self._acq_shutdown_complete = False
        self.incomplete_capture = None
        self._capture_waiting_for_safe_ack = None
        self._incomplete_waiting_for_safe_ack = None
        self._capture_live_device = False
        self.setWindowTitle("Мотортестер · запись и просмотр")
        self.resize(1240, 800)
        self.setMinimumSize(1000, 710)
        self.setStyleSheet(STYLE)
        self._build_ui()
        self.reset_view()
        self._set_actions()
        self._status("Создайте демо, откройте .mtcap или выберите «Подключение и захват» для эмулятора / USB.")

    def _build_ui(self):
        root = QtWidgets.QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(24, 22, 24, 18)
        outer.setSpacing(18)
        top = QtWidgets.QHBoxLayout()
        titles = QtWidgets.QVBoxLayout()
        titles.addWidget(_label("Мотортестер", "title"))
        titles.addWidget(_label("Просмотр исходных записей  /  один канал", "subtitle"))
        top.addLayout(titles)
        top.addStretch()
        self.acquisition_button = QtWidgets.QPushButton("Подключение и захват…")
        self.acquisition_button.clicked.connect(self.show_acquisition)
        top.addWidget(self.acquisition_button)
        self.origin_badge = _label("НЕТ ЗАПИСИ", "badge")
        top.addWidget(self.origin_badge)
        outer.addLayout(top)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(18)
        sidebar = QtWidgets.QFrame(objectName="sidebar")
        side = QtWidgets.QVBoxLayout(sidebar)
        side.setContentsMargins(18, 20, 18, 20)
        side.setSpacing(12)
        side.addWidget(_label("ДЕМО БЕЗ ПРИБОРА", "section"))
        side.addWidget(_label("Внутренний эмулятор", wrap=True))
        side.addWidget(_label("Синтетическая синусоида.\nПрибор и датчик не используются.", "muted", wrap=True))
        self.generate_button = self._button("Создать демо", "generate", self.generate_demo, "Ctrl+D")
        self.generate_button.setObjectName("primary")
        side.addWidget(self.generate_button)
        side.addSpacing(10)
        side.addWidget(_label("ЗАПИСЬ И ФАЙЛЫ", "section"))
        self.open_button = self._button("Открыть запись…", "open", self.open_dialog, "Ctrl+O")
        self.save_button = self._button("Сохранить .mtcap…", "save", self.save_dialog, "Ctrl+S")
        self.csv_button = self._button("Экспортировать CSV…", "csv", self.csv_dialog, "Ctrl+E")
        self.png_button = self._button("Экспортировать PNG…", "png", self.png_dialog, "Ctrl+Shift+E")
        for button in (self.open_button, self.save_button, self.csv_button, self.png_button):
            side.addWidget(button)
        self.file_label = _label("Файл не открыт", "muted", wrap=True)
        self.file_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        side.addWidget(self.file_label)
        side.addStretch(1)
        side.addWidget(_label("ГРАНИЦЫ ПРОТОТИПА", "section"))
        side.addWidget(_label("Эмулятор и USB · Auto / Normal / Single.\nWindows-пакет и аппаратные измерения требуют отдельной проверки.", "muted", wrap=True))
        self.help_button = self._button("Справка", "help", self.show_help, "F1")
        side.addWidget(self.help_button)
        # Keep every action and warning reachable on small windows and with
        # larger platform fonts. The content keeps its minimum layout size;
        # the viewport, rather than its child labels/buttons, absorbs pressure.
        side.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        self.sidebar_scroll = QtWidgets.QScrollArea()
        self.sidebar_scroll.setFixedWidth(262)
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setWidget(sidebar)
        body.addWidget(self.sidebar_scroll)

        content = QtWidgets.QVBoxLayout()
        content.setSpacing(14)
        metrics = QtWidgets.QHBoxLayout()
        metrics.setSpacing(12)
        self.count_value = self._metric(metrics, "ОТСЧЁТЫ · КАНАЛ 1", "—")
        self.fs_value = self._metric(metrics, "ЧАСТОТА · НОМИНАЛЬНАЯ", "—")
        self.span_value = self._metric(metrics, "МЕЖДУ КРАЙНИМИ ТОЧКАМИ", "—")
        content.addLayout(metrics)

        card = QtWidgets.QFrame(objectName="plotCard")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 12)
        card_layout.setSpacing(9)
        chart_top = QtWidgets.QHBoxLayout()
        chart_top.addWidget(_label("Исходные коды АЦП"))
        chart_top.addStretch()
        self.cursor_toggle = QtWidgets.QCheckBox("Два курсора")
        self.cursor_toggle.setObjectName("cursorToggle")
        self.cursor_toggle.toggled.connect(self._toggle_cursors)
        chart_top.addWidget(self.cursor_toggle)
        self.reset_button = self._button("Всё окно", "reset", self.reset_view, "Home")
        self.reset_button.setToolTip("Вернуть весь диапазон времени и коды 0…4095 (Home)")
        chart_top.addWidget(self.reset_button)
        card_layout.addLayout(chart_top)
        self.plot = _WaveformPlot(background="#111923")
        self.plot.setObjectName("waveformPlot")
        self.plot.setMinimumHeight(100)
        self.plot.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                QtWidgets.QSizePolicy.Policy.Expanding)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self.plot.setLabel("left", "Код АЦП · 12 бит")
        self.plot.setLabel("bottom", "Время, мс · номинальная Fs")
        self.plot.getAxis("bottom").setToolTip("Колесо: масштаб времени (X). Левая кнопка + перетаскивание: сдвиг по времени.")
        self.plot.getAxis("left").setToolTip("Колесо: масштаб кодов АЦП (Y). Левая кнопка + перетаскивание: сдвиг по кодам.")
        for name in ("left", "bottom"):
            axis = self.plot.getAxis(name)
            axis.setPen(pg.mkPen("#63738b"))
            axis.setTextPen(pg.mkPen("#c1cfe0"))
            axis.enableAutoSIPrefix(False)
        self.curve = self.plot.plot([], [], pen=pg.mkPen("#79dcc8", width=1.7))
        self.trigger_line = pg.InfiniteLine(pos=0, angle=90, movable=False,
                                            pen=pg.mkPen("#f3bf6d", width=1.2, style=QtCore.Qt.PenStyle.DashLine))
        self.plot.addItem(self.trigger_line)
        self.trigger_line.hide()
        self.cursor_lines = []
        for index, color in enumerate(("#77b6ff", "#d0a0ef")):
            line = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(color, width=1.4),
                                   hoverPen=pg.mkPen(color, width=3), label="A" if index == 0 else "B")
            line.sigPositionChanged.connect(self._cursor_moved)
            self.plot.addItem(line)
            line.hide()
            self.cursor_lines.append(line)
        self._cursor_indices = [0, 0]
        card_layout.addWidget(self.plot, 1)
        self.trigger_label = _label("Нет данных. Создайте демо или откройте запись.", "muted", wrap=True)
        card_layout.addWidget(self.trigger_label)
        self.zoom_help = _label(
            "Колесо: масштаб · левая кнопка + перетаскивание: сдвиг. "
            "Нижняя ось: только время (X); левая ось: только код (Y); поле графика: обе оси. "
            "«Всё окно» / Home: обзор. Новая запись сбрасывает масштаб и сдвиг.",
            "muted", wrap=True)
        card_layout.addWidget(self.zoom_help)
        self.cursor_label = _label("Курсоры привязаны к исходным отсчётам; время номинальное.", "muted", wrap=True)
        self.cursor_label.hide()
        card_layout.addWidget(self.cursor_label)
        # Keep the plot and its annotations at their layout minimums even
        # when platform font metrics cannot fit the minimum window height.
        # A scroll viewport absorbs the shortage instead of letting Qt place
        # the following labels underneath the plot's hard minimum height.
        card_layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        self.plot_scroll = QtWidgets.QScrollArea()
        self.plot_scroll.setWidgetResizable(True)
        self.plot_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.plot_scroll.setWidget(card)
        content.addWidget(self.plot_scroll, 1)
        self.quality_label = _label("Качество записи: данные ещё не загружены.", "quality", wrap=True)
        self.quality_scroll = QtWidgets.QScrollArea()
        self.quality_scroll.setWidgetResizable(True)
        self.quality_scroll.setFixedHeight(82)
        self.quality_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.quality_scroll.setWidget(self.quality_label)
        content.addWidget(self.quality_scroll)
        self.details_label = _label("Шкалы не калиброваны. Коды АЦП не переведены в напряжение.", "muted", wrap=True)
        content.addWidget(self.details_label)
        body.addLayout(content, 1)
        outer.addLayout(body, 1)
        self.status_label = _label("", wrap=True)
        self.status_label.setMinimumHeight(35)
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(_label("HOST-001 · прототип", "muted"))

    def _button(self, text, name, slot, shortcut):
        button = QtWidgets.QPushButton(text)
        button.setProperty("action", name)
        button.setAccessibleName(text)
        button.setShortcut(QtGui.QKeySequence(shortcut))
        button.setToolTip(f"{text} ({shortcut})")
        button.clicked.connect(slot)
        return button

    @staticmethod
    def _metric(layout, title, value):
        frame = QtWidgets.QFrame(objectName="metric")
        column = QtWidgets.QVBoxLayout(frame)
        column.setContentsMargins(14, 12, 14, 12)
        column.addWidget(_label(title, "section", wrap=True))
        number = _label(value, "metricValue")
        column.addWidget(number)
        layout.addWidget(frame, 1)
        return number

    def _status(self, text: str, *, error=False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #ffac9c;" if error else "color: #bdcce0;")

    def _acquisition_busy(self):
        state = self._acq_state
        if self._acq_requested:
            return True
        if state is None:
            return False
        phase = state.snapshot.phase.value if state.snapshot is not None else "IDLE"
        return state.busy or state.start_pending or state.closing or phase in ("HELLO", "CAPS", "ARMED")

    def _set_actions(self):
        acquiring = self._acquisition_busy()
        for button in (self.generate_button, self.open_button):
            button.setEnabled(not self.busy and not acquiring)
        for button in (self.save_button, self.csv_button, self.png_button):
            button.setEnabled(not self.busy and not acquiring and self.capture is not None)
        self.acquisition_button.setEnabled(not self.busy and not self._closing)
        if self._acquisition_dialog is not None:
            self._acquisition_dialog.refresh()
        self.reset_button.setEnabled(self.capture is not None)
        self.cursor_toggle.setEnabled(self.capture is not None)

    def _submit(self, operation: Callable, complete: Callable, status: str, failure: str):
        if self.busy or self._closing or self._acquisition_busy():
            return
        self.busy = True
        self._completion = complete
        self._failure_label = failure
        self._status(status)
        self._set_actions()
        self._job = _FileJob(operation)
        self._job.signals.finished.connect(self._file_finished)
        QtCore.QThreadPool.globalInstance().start(self._job)

    @QtCore.Slot(object, object)
    def _file_finished(self, result, error):
        complete = self._completion
        self.busy = False
        self._job = None
        self._completion = None
        if self._closing:
            return
        self._set_actions()
        if error is not None:
            self._status(f"{self._failure_label}: {error}. Текущая запись не изменена.", error=True)
        elif complete is not None:
            complete(result)
        self.operation_finished.emit()

    def generate_demo(self):
        if self.busy or self._acquisition_busy():
            return
        capture = from_message(demo_message(sequence=self._sequence))
        self._sequence += 1
        self.show_capture(capture)
        self._status("Создано демо: синтетические данные, ещё не сохранены. Физических измерений нет.")

    def show_capture(self, capture: Capture, path: Path | None = None, *, opened=False, live=False):
        """Display validated raw data, without modifying or filtering the tuple."""
        self.capture = capture
        self.current_path = path
        self.opened_from_disk = opened
        self._capture_live_device = bool(live and capture.origin == "device")
        zero = capture.trigger_index if capture.trigger_index is not None else 0
        times_ms = (np.arange(len(capture.samples), dtype=np.float64) - zero) * (1000 / capture.nominal_fs_hz)
        codes = np.asarray(capture.samples, dtype=np.uint16)
        self.curve.setData(times_ms, codes, symbol="o" if len(codes) == 1 else None,
                           symbolSize=6, symbolBrush="#79dcc8")
        self.origin_badge.setText("ДЕМО · СИНТЕТИЧЕСКИЕ ДАННЫЕ" if capture.origin == "simulator" else ("ДАННЫЕ ПРИБОРА · USB" if self._capture_live_device else "ЗАПИСЬ ПРИБОРА · ИЗ ФАЙЛА"))
        self.count_value.setText(f"{len(codes):,}".replace(",", " "))
        self.fs_value.setText(f"{capture.nominal_fs_hz / 1000:g} кГц".replace(".", ","))
        self.span_value.setText(f"{_number((len(codes) - 1) * 1000 / capture.nominal_fs_hz, 2)} мс")
        self.trigger_line.setVisible(capture.trigger_index is not None)
        if capture.trigger_index is not None:
            prefix = "Синтетический запуск" if capture.origin == "simulator" else "Запуск из метаданных"
            self.trigger_label.setText(f"{prefix}: индекс {capture.trigger_index} · t = 0 мс · предыстория {capture.pretrigger_samples} отсчётов")
        else:
            self.trigger_label.setText("Событие запуска отсутствует (auto_timeout). t = 0 у первого отсчёта; маркера запуска нет.")
        flags = "; ".join(capture.quality_flags) if capture.quality_flags else "не заданы"
        rail = "Есть крайние коды 0 / 4095; возможное ограничение." if capture.rail_warning else "Крайние коды 0 / 4095 отсутствуют."
        self.quality_label.setText(f"Флаги качества: {flags}. {rail}\nЭто не доказывает точность шкал, отсутствие аналогового ограничения или состояние катушки.")
        stamp = capture.received_at_utc
        visible_stamp = stamp if len(stamp) <= 64 else stamp[:61] + "…"
        self.details_label.setText(f"Окно {capture.sequence} · шкалы не калиброваны · время рассчитано по номинальной Fs\nМетка приёма ПК (UTC): {visible_stamp}")
        self.details_label.setToolTip(stamp)
        self._update_file_label()
        self._moving_cursor = True
        self._cursor_indices = [(len(codes) - 1) // 3, 2 * (len(codes) - 1) // 3]
        for line, index in zip(self.cursor_lines, self._cursor_indices):
            line.setBounds((float(times_ms[0]), float(times_ms[-1])))
            line.setValue(float(times_ms[index]))
        self._moving_cursor = False
        self._toggle_cursors(self.cursor_toggle.isChecked())
        self.reset_view()
        self._set_actions()

    def _update_file_label(self):
        if self.current_path is None:
            self.file_label.setText("В памяти · не сохранено")
            self.file_label.setToolTip("")
        else:
            action = "Открыто с диска" if self.opened_from_disk else "Сохранено"
            self.file_label.setText(f"{action}:\n{self.current_path.name}")
            self.file_label.setToolTip(str(self.current_path))

    def reset_view(self):
        """Fit the whole capture and constrain navigation to meaningful scales.

        Display limits never modify samples, nominal Fs or cursor positions.
        Every newly displayed capture (including a live window) resets the view;
        limits from a previous, possibly much longer recording cannot leak in.
        """
        if self.capture is None:
            # A finite empty viewport is not a capture or a sample-rate claim.
            first, last, sample_ms = 0.0, 1.0, 1.0
        else:
            first = self.capture.time_seconds(0) * 1000
            last = self.capture.time_seconds(len(self.capture.samples) - 1) * 1000
            sample_ms = 1000 / self.capture.nominal_fs_hz
        if first == last:
            first, last = first - sample_ms / 2, last + sample_ms / 2
        # Retain the existing fit margins. Navigation stays inside this finite
        # overview; zooming further than one sample interval / one ADC code
        # adds no source resolution. Y uses the ADC scale, not signal extrema,
        # so a constant trace and rail codes remain inspectable and honest.
        margin_x, margin_y = (last - first) * 0.025, 4095 * 0.035
        x_range = (first - margin_x, last + margin_x)
        y_range = (-margin_y, 4095 + margin_y)
        view = self.plot.getViewBox()
        view.setLimits(xMin=x_range[0], xMax=x_range[1],
                       yMin=y_range[0], yMax=y_range[1],
                       minXRange=sample_ms, maxXRange=x_range[1] - x_range[0],
                       minYRange=1, maxYRange=y_range[1] - y_range[0])
        view.setRange(xRange=x_range, yRange=y_range, padding=0)

    def _toggle_cursors(self, checked):
        visible = checked and self.capture is not None
        for line in self.cursor_lines:
            line.setVisible(visible)
        self.cursor_label.setVisible(visible)
        if visible:
            self._update_cursor_label()

    def _cursor_moved(self):
        if self._moving_cursor or self.capture is None:
            return
        self._moving_cursor = True
        try:
            zero = self.capture.trigger_index if self.capture.trigger_index is not None else 0
            for j, line in enumerate(self.cursor_lines):
                index = round(line.value() * self.capture.nominal_fs_hz / 1000 + zero)
                index = max(0, min(index, len(self.capture.samples) - 1))
                self._cursor_indices[j] = index
                line.setValue(self.capture.time_seconds(index) * 1000)
            self._update_cursor_label()
        finally:
            self._moving_cursor = False

    def _update_cursor_label(self):
        a, b = self._cursor_indices
        ta, tb = (self.capture.time_seconds(i) * 1000 for i in (a, b))
        ya, yb = (self.capture.samples[i] for i in (a, b))
        self.cursor_label.setText(f"A: {_number(ta)} мс / {ya} код · B: {_number(tb)} мс / {yb} код\nΔt (B−A): {_number(tb - ta)} мс · Δкод: {yb - ya:+d} · по отсчётам, время номинальное")

    def open_dialog(self):
        if self.busy or self._acquisition_busy():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Открыть запись", "", "Записи мотортестера (*.mtcap);;Все файлы (*)")
        if not path:
            self._status("Открытие отменено. Текущая запись не изменена.")
            return
        self.open_path(path)

    def open_path(self, path: str | Path):
        path = Path(path)

        def complete(capture):
            self.show_capture(capture, path, opened=True)
            self._status(f"Открыто с диска: {path.name}. График построен из загруженной записи.")

        self._submit(lambda: recording.load(path), complete,
                     "Открытие файла… До завершения показана предыдущая запись.", f"Не удалось открыть «{path.name}»")

    def _choose_save_path(self, caption, suffix, file_filter):
        proposed = self.current_path.stem if self.current_path else "демо"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, caption, f"{proposed}{suffix}", file_filter,
            options=QtWidgets.QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            self._status(f"{caption}: отменено. Текущая запись не изменена.")
            return None
        selected = Path(path)
        if not selected.suffix:
            selected = selected.with_suffix(suffix)
        overwrite = selected.exists()
        if overwrite:
            if not self._confirm_overwrite(selected):
                self._status("Замена файла отменена. Текущая запись и файл не изменены.")
                return None
        return selected, overwrite

    def _confirm_overwrite(self, path: Path) -> bool:
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Заменить файл?")
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Question)
        dialog.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        dialog.setText(f"Файл «{path.name}» уже существует. Заменить его?")
        replace_button = dialog.addButton("Заменить", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        cancel_button = dialog.addButton("Отмена", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        confirmed = dialog.clickedButton() is replace_button
        dialog.deleteLater()
        return confirmed

    def save_dialog(self):
        if self.capture is None or self.busy or self._acquisition_busy():
            return
        choice = self._choose_save_path("Сохранить запись", ".mtcap", "Запись мотортестера (*.mtcap)")
        if choice is not None:
            self.save_path(choice[0], overwrite=choice[1])

    def save_path(self, path: str | Path, *, overwrite=False):
        if self.capture is None:
            return
        path, capture = Path(path), self.capture

        def complete(_):
            self.current_path = path
            self.opened_from_disk = False
            self._update_file_label()
            self._status(f"Сохранено: {path.name}. Исходные отсчёты и метаданные записаны в .mtcap.")

        self._submit(lambda: recording.save(path, capture, overwrite=overwrite), complete,
                     "Сохранение записи…", "Не удалось сохранить запись")

    def csv_dialog(self):
        if self.capture is None or self.busy or self._acquisition_busy():
            return
        choice = self._choose_save_path("Экспортировать CSV", ".csv", "Численный экспорт (*.csv)")
        if choice is not None:
            self.export_path(choice[0], overwrite=choice[1])

    def export_path(self, path: str | Path, *, overwrite=False):
        if self.capture is None:
            return
        path, capture = Path(path), self.capture
        self._submit(lambda: recording.export_csv(path, capture, overwrite=overwrite),
                     lambda _: self._status(f"CSV экспортирован: {path.name}. Время в секундах, номинальное; исходная запись в .mtcap."),
                     "Экспорт CSV…", "Не удалось экспортировать CSV")

    def png_image(self) -> QtGui.QImage:
        """Render the current view with provenance; never replace raw samples."""
        if self.capture is None:
            raise CaptureError("Нет записи для экспорта PNG")
        chart = self.plot.grab().toImage()
        # PNG is an illustration of the visible viewport. Limit image resources
        # independently of the capture size, including high-DPI displays.
        width = chart.width()
        if width < 1 or chart.height() < 1 or width > 4096 or chart.height() > 4096:
            raise CaptureError("Размер графика для PNG должен быть не более 4096 × 4096 пикселей")
        chart.setDevicePixelRatio(1)
        capture = self.capture
        origin = "ДЕМОНСТРАЦИЯ · синтетические данные" if capture.origin == "simulator" else ("Данные прибора · USB · шкалы не калиброваны" if self._capture_live_device else "Данные из файла прибора · происхождение не проверено")
        header = f"Мотортестер · PNG-иллюстрация\n{origin}"
        footer = (f"{len(capture.samples)} исходных отсчётов · Fs ном. {capture.nominal_fs_hz:g} Гц · шкалы не калиброваны\n"
                  f"{self.trigger_label.text()}\n{self.quality_label.text()}\n")
        if self.cursor_toggle.isChecked():
            footer += self.cursor_label.text() + "\n"
        footer += "Изображение не заменяет исходную запись .mtcap. Сохранён текущий масштаб графика."
        font = QtGui.QFont("DejaVu Sans", 10)
        metrics = QtGui.QFontMetrics(font)
        flags = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.TextFlag.TextWrapAnywhere
        inner_width = max(1, width - 32)
        header_h = metrics.boundingRect(QtCore.QRect(0, 0, inner_width, 10000), int(flags), header).height() + 24
        footer_h = metrics.boundingRect(QtCore.QRect(0, 0, inner_width, 10000), int(flags), footer).height() + 24
        height = header_h + chart.height() + footer_h
        if width * height > 16_000_000:
            raise CaptureError("Изображение PNG превышает допустимые 16 миллионов пикселей")
        result = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32)
        if result.isNull():
            raise CaptureError("Недостаточно памяти для PNG")
        result.fill(QtGui.QColor("#10151f"))
        painter = QtGui.QPainter(result)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#e5edf7"))
        painter.drawText(QtCore.QRect(16, 12, inner_width, header_h - 12), int(flags), header)
        painter.drawImage(0, header_h, chart)
        painter.drawText(QtCore.QRect(16, header_h + chart.height() + 12, inner_width, footer_h - 12), int(flags), footer)
        painter.end()
        result.setText("Provenance", origin)
        result.setText("Scale", "adc_code; time from nominal Fs; uncalibrated")
        result.setText("RawSampleCount", str(len(capture.samples)))
        result.setText("Purpose", "Illustration only; preserve original .mtcap")
        return result

    def png_dialog(self):
        if self.capture is None or self.busy or self._acquisition_busy():
            return
        choice = self._choose_save_path("Экспортировать иллюстрацию PNG", ".png", "Изображение PNG (*.png)")
        if choice is not None:
            self.export_png_path(choice[0], overwrite=choice[1])

    def export_png_path(self, path: str | Path, *, overwrite=False):
        if self.capture is None or self.busy or self._acquisition_busy():
            return
        path = Path(path)
        try:
            if path.suffix.lower() != ".png":
                raise CaptureError("Для иллюстрации выберите имя с расширением .png")
            image = self.png_image()
            buffer = QtCore.QBuffer()
            if not buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
                raise CaptureError("Не удалось закодировать изображение PNG")
            data = bytes(buffer.data())
            buffer.close()
        except (CaptureError, OSError) as exc:
            self._status(f"Не удалось подготовить PNG: {exc}. Запись не изменена.", error=True)
            return
        self._submit(lambda: recording.atomic_write(path, data, overwrite=overwrite),
                     lambda _: self._status(f"PNG сохранён: {path.name}. Это иллюстрация; исходные данные сохраняйте в .mtcap."),
                     "Экспорт PNG…", "Не удалось сохранить PNG")

    def show_help(self):
        if self._help_dialog is not None:
            self._help_dialog.show()
            self._help_dialog.raise_()
            return
        try:
            text = HELP_PATH.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            self._status("Не удалось открыть офлайн-справку. Проверьте наличие docs/help.md в комплекте приложения.", error=True)
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Справка · просмотрщик Мотортестер")
        dialog.resize(760, 640)
        layout = QtWidgets.QVBoxLayout(dialog)
        browser = QtWidgets.QTextBrowser()
        browser.setObjectName("helpText")
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        browser.setMarkdown(text)
        layout.addWidget(browser)
        buttons = QtWidgets.QDialogButtonBox()
        close = buttons.addButton("Закрыть", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(dialog.close)
        layout.addWidget(buttons)
        self._help_dialog = dialog
        dialog.show()

    def show_acquisition(self):
        if self.busy or self._closing:
            return
        if self._acquisition is None:
            from .acquisition import AcquisitionBridge
            from .gui_acquisition import AcquisitionDialog
            self._acquisition = AcquisitionBridge(self)
            self._acquisition.state_changed.connect(self._acquisition_state)
            self._acquisition.capture_ready.connect(self._acquisition_capture)
            self._acquisition.incomplete_ready.connect(self._acquisition_incomplete)
            self._acquisition.error.connect(lambda text: self._status(text, error=True))
            self._acquisition.finished.connect(self._acquisition_finished)
            self._acquisition_dialog = AcquisitionDialog(self, self._acquisition)
        self._acquisition_dialog.show()
        self._acquisition_dialog.raise_()
        self._acquisition_dialog.activateWindow()

    def _acquisition_state(self, state):
        self._acq_state = state
        self._acq_requested = False
        self._set_actions()
        if not self.busy:
            self._show_acquisition_status()
        capture = self._capture_waiting_for_safe_ack
        snapshot = state.snapshot
        if (capture is not None and snapshot is not None and not self._closing
                and not state.busy and not state.start_pending
                and snapshot.phase.value in ("READY", "RECOVERY")
                and not snapshot.stop_barrier and not snapshot.pending_request_id
                and not snapshot.pending_stop_id):
            # The exact immutable data are already retained; after Stop/recovery
            # this transfers ownership without RELEASE or automatic re-arming.
            if self._acquisition.acknowledge(capture):
                self._capture_waiting_for_safe_ack = None
        incomplete = self._incomplete_waiting_for_safe_ack
        if (incomplete is not None and snapshot is not None and not self._closing
                and not state.closing and not state.busy
                and snapshot.phase.value in ("READY", "RECOVERY")
                and not snapshot.stop_barrier and not snapshot.pending_request_id
                and not snapshot.pending_stop_id):
            # A queued Disconnect may reject the first delivery-time ACK.
            # Retain the exact fragments and transfer only after its barrier.
            if self._acquisition.acknowledge_incomplete(incomplete):
                self._incomplete_waiting_for_safe_ack = None

    def _show_acquisition_status(self):
        state = self._acq_state
        if state is None:
            return
        phase = state.snapshot.phase.value if state.snapshot is not None else "IDLE"
        retained = (f"Показано последнее полное окно №{self.capture.sequence}; это не новое измерение."
                    if self.capture is not None else "Полного окна пока нет.")
        if state.close_failed:
            text = ("Закрытие порта не подтверждено. Повторите закрытие основного окна. " if self._closing else "Закрытие порта не подтверждено. Повторите отключение. ") + retained
        elif phase == "RECOVERY":
            text = "Ошибка связи; автоматического перезапуска нет. " + retained
        elif state.snapshot is not None and state.snapshot.stop_barrier:
            text = "Остановка: ждём подтверждение прибора. " + retained
        elif state.start_pending:
            text = "Применяем настройки перед запуском. " + retained
        elif phase == "ARMED":
            text = "Захват активен: ждём очередное полное окно. " + retained
        elif phase in ("HELLO", "CAPS"):
            text = "Согласование соединения; захват ещё не запущен. " + retained
        elif phase == "READY" and state.connected:
            text = "Захват остановлен. Можно сохранить запись или нажать «Старт». " + retained
        else:
            text = "Источник отключён. Закрытие порта не подтверждает физическую остановку прибора. " + retained
        self._status(text, error=phase == "RECOVERY" or state.close_failed)

    def _acquisition_capture(self, capture):
        # Retain and display before granting RELEASE. A failed plot never ACKs.
        if self._closing:
            self.capture = capture
            return
        self.capture = capture
        self.current_path = None
        self.opened_from_disk = False
        try:
            self.show_capture(capture, live=capture.origin == "device")
        except Exception:
            self.curve.clear()
            self.trigger_line.hide()
            self._capture_waiting_for_safe_ack = capture
            self._acquisition.stop()
            self._status("Полученное окно сохранено в памяти; ошибка отображения. Захват останавливается.", error=True)
            return
        self._show_acquisition_status()
        if not self._acquisition.acknowledge(capture):
            self._capture_waiting_for_safe_ack = capture
            self._acquisition.stop()
            self._status("Окно сохранено в памяти; подтверждение не принято. Захват останавливается.", error=True)

    def _acquisition_incomplete(self, result):
        self.incomplete_capture = result
        self._status("Получено неполное окно. Фрагменты сохранены отдельно в памяти, график полного окна не заменён. Пропуски не заполнялись.", error=True)
        self._incomplete_waiting_for_safe_ack = result
        if self._acquisition.acknowledge_incomplete(result):
            self._incomplete_waiting_for_safe_ack = None

    def _acquisition_finished(self):
        self._acq_shutdown_complete = True
        self._acq_requested = False
        if self._closing:
            QtCore.QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        self._closing = True
        if self._acquisition is not None and not self._acq_shutdown_complete:
            event.ignore()
            self._set_actions()
            self._acquisition.shutdown()
            return
        if self._acquisition_dialog is not None:
            self._acquisition_dialog.close()
        if self._help_dialog is not None:
            self._help_dialog.close()
        super().closeEvent(event)


def screenshot(path: Path) -> int:
    """Run a real round-trip before rendering; do not reuse the source capture."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    window = MainWindow()
    with tempfile.TemporaryDirectory(prefix="motortester-gui-") as folder:
        file_path = Path(folder) / "демонстрация.mtcap"
        original = from_message(demo_message())
        recording.save(file_path, original)
        reopened = recording.load(file_path)
        if reopened != original or reopened is original:
            raise CaptureError("Повторное открытие не подтвердило независимую загрузку")
        window.show_capture(reopened, file_path, opened=True)
        window._status("Открыто с диска: демонстрация.mtcap · проверка программы на синтетических данных.")
        window.show()
        app.processEvents()
        # A short Qt timer allows plot geometry and the backing store to settle.
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(150, loop.quit)
        loop.exec()
        if not window.grab().save(str(path)):
            raise OSError(f"Не удалось сохранить снимок: {path}")
        window.close()
        app.processEvents()
    # The PNG is already saved. Optional console text must not turn a valid
    # screenshot into failure on a windowed/legacy-encoding Windows stdout.
    if sys.stdout is not None:
        try:
            print(f"Снимок Qt после повторного открытия синтетической записи: {path}")
            print("Проверка ОС Windows 10 и реального прибора не выполнялась.")
        except (UnicodeEncodeError, OSError):
            pass
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Мотортестер · просмотр файлов и явно синтетическое демо")
    parser.add_argument("--screenshot", type=Path, help="Сохранить снимок реального окна после файлового round-trip и завершиться")
    args = parser.parse_args(argv)
    if args.screenshot:
        return screenshot(args.screenshot)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("Мотортестер")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

"""Reusable isolated streaming controls; deliberately absent from app entry points."""
from PySide6 import QtCore, QtWidgets

from .gui_acquisition import _ContentSizedComboBox, _SettingsSpinBox
from .gui_stream import LiveEventIndex, LiveSnapshot, StreamViewerPanel
from .stream_controller import EVENT_CAPACITY, StreamController
from .stream_view import EventConfig

# Explicit synthetic fixture context, not electrical defaults or burn detection.
SYNTHETIC_CONFIG = EventConfig(1000, 500, "above", 8, 12, 3, 0, 256, 0, 4095)


class StreamControls(QtWidgets.QWidget):
    """Buttons and tests use the same public controller actions.

    GUI owns widgets and chooser intent only. The supplied controller owns the
    byte peer. Its display worker constructs and validates LiveSnapshot before
    calling the viewer's thread-safe latest-only ingress; no per-block Qt signal.
    """
    def __init__(self, controller: StreamController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._revision = None
        self._closed = False
        self._sent_view = None
        self._ui_error = None
        self.viewer = StreamViewerPanel(event_config=controller.snapshot().config)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.viewer)
        self._build_controls(controller.snapshot().config)
        self.viewer.open_button.clicked.disconnect()
        self.viewer.open_button.clicked.connect(self.open_dialog)
        self.viewer.view_requested.connect(self._view_requested)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self.destroyed.connect(lambda: controller.close())
        self._poll()

    def _build_controls(self, config):
        group = QtWidgets.QGroupBox("Синтетический поток · отдельный компонент")
        grid = QtWidgets.QGridLayout(group)
        self.preview_button = QtWidgets.QPushButton("Просмотр (Preview)")
        self.record_button = QtWidgets.QPushButton("Запись… (Record)")
        self.stop_button = QtWidgets.QPushButton("Стоп (Stop)")
        for column, button in enumerate((self.preview_button, self.record_button, self.stop_button)):
            grid.addWidget(button, column // 2, column % 2)
        self.preview_button.clicked.connect(self.preview)
        self.record_button.clicked.connect(self.record_dialog)
        self.stop_button.clicked.connect(self.stop)
        self.flow_label = QtWidgets.QLabel()
        self.flow_label.setWordWrap(True)
        self.flow_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self.flow_label, 2, 0, 1, 2)
        self.settings = QtWidgets.QGroupBox("Контекст пороговых событий: синтетический preset")
        form = QtWidgets.QFormLayout(self.settings)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.polarity = _ContentSizedComboBox()
        self.polarity.addItem("Выше порога (above)", "above")
        self.polarity.addItem("Ниже порога (below)", "below")
        self.polarity.setCurrentIndex(0 if config.polarity == "above" else 1)
        form.addRow("Полярность", self.polarity)
        self.fields = {}
        labels = (("threshold_code", "Порог, код"), ("release_code", "Возврат, код"),
                  ("pre_samples", "Предыстория, отсчёты"), ("post_samples", "После возврата, отсчёты"),
                  ("baseline_samples", "Базовый интервал, отсчёты"), ("holdoff_samples", "Holdoff, отсчёты"),
                  ("max_event_samples", "Предел контекста, отсчёты"),
                  ("rail_low", "Нижняя граница, код"), ("rail_high", "Верхняя граница, код"))
        for name, label in labels:
            spin = _SettingsSpinBox()
            # The reused class supplies wheel behavior, not the dialog's field
            # palette. Keep this component's numeric values/arrows legible.
            spin.setStyleSheet("""
                QSpinBox { background: #ffffff; color: #10151f; }
                QSpinBox:disabled { background: #d9e1ee; color: #465366; }
                QSpinBox:focus { background: #d7ebff; color: #10151f; }
            """)
            maximum = 65535 if "code" in name or name.startswith("rail_") else 65536
            spin.setRange(0, maximum)
            spin.setValue(getattr(config, name))
            self.fields[name] = spin
            form.addRow(label, spin)
        self.context_label = QtWidgets.QLabel()
        self.context_label.setWordWrap(True)
        form.addRow(self.context_label)
        grid.addWidget(self.settings, 3, 0, 1, 2)
        self.viewer.content.layout().insertWidget(2, group)

    def event_config(self):
        values = {name: widget.value() for name, widget in self.fields.items()}
        values["polarity"] = self.polarity.currentData()
        return EventConfig(**values)

    def preview(self):
        try:
            accepted = self.controller.preview(event_config=self.event_config())
            self._ui_error = None if accepted else "Просмотр не принят: предыдущая попытка ещё активна или требуется изоляция транспорта."
        except ValueError as exc:
            self._ui_error = str(exc)
            self._poll()
            return False
        self._poll()
        return accepted

    def record_dialog(self):
        if not self.controller.snapshot().can_record:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Новая потоковая запись", "",
            "Потоковая запись (*.mtstream);;Все файлы (*)",
            options=QtWidgets.QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.record_path(path)

    def record_path(self, path):
        try:
            accepted = self.controller.record(path, event_config=self.event_config())
            self._ui_error = None if accepted else "Record не принят: предыдущий writer/попытка не завершены."
        except ValueError as exc:
            self._ui_error = str(exc)
            self._poll()
            return False
        self._poll()
        return accepted

    def stop(self):
        accepted = self.controller.stop()
        self._poll()
        return accepted

    def open_dialog(self):
        if not self.controller.snapshot().can_open:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Открыть закрытую потоковую запись", "",
            "Потоковая запись (*.mtstream);;Все файлы (*)")
        if path:
            self.open_path(path)

    def open_path(self, path):
        if not self.controller.claim_open():
            self.flow_label.setText("Открытие недоступно до завершения источника и закрытия writer.")
            return False
        self._revision = None
        self.viewer.open_path(path)  # Strict fresh reader in the existing viewer worker.
        return True

    def _view_requested(self, selection):
        if self._revision is not None:
            # follow() emits the selection before it updates follow_tail. Read
            # the final GUI state on the next event turn, coalescing user intent.
            QtCore.QTimer.singleShot(0, self._publish_view)

    def _publish_view(self):
        selection = self.viewer.selection
        if not self._closed and self._revision is not None and selection is not None:
            key = (self._revision, selection.start, selection.stop, self.viewer.follow_tail)
            if key != self._sent_view:
                self.controller.request_view(self._revision, selection.start, selection.stop,
                                             follow=self.viewer.follow_tail)
                self._sent_view = key

    def _poll(self):
        if self._closed:
            return
        status = self.controller.snapshot()
        snap = status.bridge
        if snap is not None and snap.identity is not None and status.revision != self._revision:
            # Open of a completed source must not be replaced by its old live state.
            if not status.can_open:
                self._revision = status.revision
                handle = self.viewer.begin_live(snap.identity, event_config=status.config,
                                                event_config_revision=status.config_revision)
                viewer = self.viewer
                def deliver(result):
                    source = result.source
                    index = LiveEventIndex(handle, source.sequence, source.config_revision,
                        source.config, source.events, source.detector, EVENT_CAPACITY)
                    snapshot = LiveSnapshot(source.identity, source.sequence, source.first,
                        source.end, result.envelope, source.loss_samples, source.tail_state,
                        source.flags, source.raw_first, index)
                    viewer.post_live_snapshot(handle, snapshot)
                self.controller.bind_display(status.revision, deliver)
        self._publish_view()
        self.preview_button.setEnabled(status.can_preview)
        self.record_button.setEnabled(status.can_record)
        self.stop_button.setEnabled(not status.close_requested and bool(snap or status.command_pending))
        self.viewer.open_button.setEnabled(status.can_open)
        self.settings.setEnabled(status.can_preview)
        cfg = status.config
        fs = 50000 if snap is None or snap.identity is None else snap.identity.nominal_fs_hz
        times = ", ".join(f"{name} {getattr(cfg, name) * 1000 / fs:g} мс" for name in
                          ("pre_samples", "post_samples", "baseline_samples", "holdoff_samples", "max_event_samples"))
        self.context_label.setText(f"Номинальная Fs {fs} Гц; {times}. Параметры фиксированы на revision {status.config_revision}. "
            "Пороговый контекст не определяет горение, затухание, цилиндр или физическую полноту разряда. "
            "Live хранит последние 65 536 отсчётов и до 4 096 событий; весь сохранённый raw доступен после Stop и Open.")
        phases = {"idle": "Готово", "negotiating": "Согласование", "preview": "Просмотр",
            "starting preview": "Ожидание START_ACK", "preparing": "Подготовка файла",
            "waiting for boundary": "Ожидание следующего целого DATA", "recording": "Запись",
            "draining": "Stop: приём хвоста / завершение файла", "saved": "Запись завершена",
            "preview stopped": "Просмотр остановлен", "stopped without R": "Остановлено без R",
            "fault": "Отказ", "fault; writer unresolved": "Отказ; writer не разрешён",
            "closing": "Закрытие ожидает writer", "closed": "Закрыто", "error": "Ошибка",
            "stopped before START": "Остановлено до START"}
        text = f"{phases.get(status.phase, status.phase)}; Record → Stop: {status.path or 'путь не выбран'}. "
        if snap and snap.session:
            s = snap.session
            text += (f"R={s.selected_start_sample_index}; получено {snap.source_received_end}; "
                f"очередь до {s.queued_end_sample_index}, writer до {s.accepted_end_sample_index}, "
                f"checkpoint до {s.checkpointed_end_sample_index}, final до {s.finalized_end_sample_index}. "
                f"Stop intent={status.stop_requested or s.stop_requested}; END={s.source_end is not None}; ACK={s.stop_acknowledgement is not None}; "
                f"writer closed={bool(snap.storage and snap.storage.closed)}; clean={s.clean_completion_reported}. "
                f"Raw очередь {s.raw_items_held}/{status.raw_item_limit} блоков, {s.raw_bytes_held}/{status.raw_byte_limit} байт (включая in-flight). ")
            if snap.pending_stop is not None:
                stop = snap.pending_stop
                text += (f"Wire STOP request={stop.request_id}: accepted bytes={stop.accepted_bytes}, "
                         f"полностью передан={stop.complete}. ")
            if s.first_failure:
                f = s.first_failure
                text += f"Первый отказ: {f.code}, {f.detail}; запрос {f.operation}/{f.request_id}. "
            if snap.late_fault:
                text += f"Отдельный поздний отказ: {snap.late_fault}. "
        if status.worker_unresolved:
            text += "Writer ещё не разрешён; блокирующий OS I/O нельзя отменить, новая запись запрещена. "
        text += (f"Raw-квота файла {status.storage_raw_limit / (1024 * 1024):g} MiB "
                 f"(≤{status.storage_raw_limit / (2 * fs * 60):.1f} мин при номинальной Fs; накладные расходы отдельно). ")
        if status.error or status.display_error or self._ui_error:
            text += (status.error or status.display_error or self._ui_error)
        self.flow_label.setText(text)

    def shutdown(self):
        if not self._closed:
            self._closed = True
            self._timer.stop()
            self.controller.close()
            self.viewer.shutdown()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

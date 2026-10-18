"""Explicit acquisition controls; serial I/O belongs to AcquisitionBridge."""
from pathlib import Path
from uuid import uuid4

from PySide6 import QtCore, QtWidgets
from .session import Config, Phase


def recording_status_text(recording):
    """Describe only acknowledged host/storage facts; never infer device time."""
    if recording is None:
        return "Record сохраняет отдельные окна с неизвестными промежутками между ними."
    snapshot = recording.snapshot
    if snapshot is None:
        return "Подготовка файла восстановления; файл может ещё не существовать, захват не подтверждён."
    phases = {
        "idle": "Запись ещё не начата", "preparing": "Подготовка файла восстановления",
        "starting": "Применение настроек записи", "recording": "Запись / ожидание запуска",
        "stopping": "Stop: ожидание ответа / завершение окна",
        "persisting": "Запись сегмента на диск", "acknowledging": "Подтверждение сохранённого сегмента",
        "finalize_ready": "Завершение файла восстановления", "finished": "Запись завершена",
        "failed": "Ошибка записи; требуется восстановление",
    }
    phase = phases.get(snapshot.phase.value, "Состояние записи неизвестно")
    if recording.retired:
        phase = "Сеанс закрыт; фоновая запись могла не завершиться"
    elif recording.saving:
        phase = "Сохранение .mtrec… Отмена действует только до публикации файла"
    elif recording.error:
        phase = {"save_cancelled": "Сохранение отменено; файл восстановления оставлен",
                 "save_failed": "Ошибка сохранения .mtrec. Выберите новое имя; проверьте место и права. Существующие файлы не заменяются",
                 "prepare_failed": "Не удалось подготовить восстановление; файл мог не создаться. Проверьте место и права папки"}.get(
                     recording.error, "Ошибка записи / сохранения; созданный файл восстановления не удаляется")
        if recording.saved_path:
            phase += ". Прежнее успешное сохранение не подтверждает новую попытку"
    elif recording.saved_path:
        phase = "Архив .mtrec сохранён; файл восстановления оставлен"
    elif recording.ready_to_save:
        phase = "Сеанс готов: выберите «Сохранить сеанс .mtrec…»"
    reason = {"user_stop": "по кнопке Stop", "single_complete": "Single: одно окно",
              "capacity": "достигнут предел ёмкости / времени", "error": "из-за ошибки",
              "recovered": "после восстановления"}.get(snapshot.termination_reason)
    stop = {"stop_ack": "Получен ответ Stop (не доказательство физической остановки)",
            "single_release_ack": "Получен ответ Single (не доказательство физической остановки)",
            "unknown": "Подтверждение Stop неизвестно", "none": "Подтверждения Stop пока нет"}.get(snapshot.stop_evidence, "Подтверждение Stop неизвестно")
    text = (f"{phase}." + (f" Завершение: {reason}." if reason else "")
            + f"\nСегментов на диске: {snapshot.committed_segments}; {snapshot.committed_bytes} байт. "
            + f"Время ПК: {snapshot.elapsed_ms / 1000:.1f} с.\n{stop}."
            + "\nПокрытие: отдельные сегменты; промежутки времени прибора неизвестны. "
              "Номинальное полное окно: 2048 / 50 кГц = 40,96 мс данных; это не длительность сеанса.")
    if snapshot.finalization_invalidated:
        text += "\nЗавершение не подтверждено: обычное сохранение недоступно; сохраните spool для отдельного восстановления."
    if snapshot.source_retention_required or snapshot.writer_pending or recording.writer_busy:
        text += "\nЕсть незавершённая запись / удерживаемые данные."
    if snapshot.recovery_diagnostic:
        text += "\nПричина восстановления: " + snapshot.recovery_diagnostic
    if snapshot.fault:
        text += "\nОшибка записи: " + snapshot.fault
    if recording.error:
        text += "\nОшибка сохранения / записи: " + recording.error
    return text


def reported_device_status_text(state):
    """Render a received STATUS separately from host command/ownership phase."""
    snapshot = state.snapshot if state is not None else None
    status = getattr(snapshot, "device_status", None)
    if status is None:
        return "Состояние прибора по STATUS: ещё не сообщено в этом сеансе."
    names = {0: "остановлен", 1: "подготовка", 2: "ожидание запуска",
             3: "захват", 4: "передача", 5: "ошибка", 6: "ожидание подтверждения окна"}
    name = names.get(status.state, "неизвестный код состояния")
    text = f"Последний STATUS прибора: {name} (код {int(status.state)}); причина {status.reason}."
    if not state.connected:
        text += "\nСоединение закрыто; это прежнее сообщение."
    elif getattr(snapshot, "device_status_stale", True):
        text += "\nСообщение устарело после команды или ошибки; новое состояние ещё не сообщено."
    else:
        text += "\nСообщено в текущем сеансе; это не независимая проверка физического состояния."
    return text


def recovery_directory():
    """Persistent user-data folder; unlike TemporaryDirectory it is not deleted."""
    base = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.GenericDataLocation)
    if not base:
        raise OSError("Не найдена папка данных пользователя")
    return Path(base) / "Motortester" / "recovery"


class _ContentSizedComboBox(QtWidgets.QComboBox):
    def minimumSizeHint(self):
        # Qt can cache the unstyled native-font minimum while sizeHint already
        # reflects the stylesheet. Keep the full styled item/arrow/padding size.
        return self.sizeHint()


class AcquisitionDialog(QtWidgets.QDialog):
    def __init__(self, viewer, bridge):
        super().__init__(viewer)
        self.viewer, self.bridge = viewer, bridge
        self.state = None
        self._last_error = ""
        self.setWindowTitle("Мотортестер · подключение и захват")
        self.resize(560, 650)
        self.setStyleSheet("""
            QDialog { background: #171f2c; color: #e5edf7; }
            QWidget#acquisitionContent { background: #171f2c; }
            QComboBox, QSpinBox, QLineEdit {
                background: #10151f; color: #e5edf7;
                border: 1px solid #61718c; padding: 5px;
                selection-background-color: #365772;
            }
            QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled {
                background: #1c2533; color: #8d9aaf;
            }
            QComboBox QAbstractItemView { background: #10151f; color: #e5edf7; selection-background-color: #365772; }
            QLabel { color: #e5edf7; }
            QCheckBox:disabled { color: #8d9aaf; }
        """)
        outer = QtWidgets.QVBoxLayout(self)
        content = QtWidgets.QWidget(objectName="acquisitionContent")
        layout = QtWidgets.QVBoxLayout(content)
        layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        hide = QtWidgets.QPushButton("Скрыть панель")
        hide.setToolTip("Скрытие панели не останавливает захват.")
        hide.clicked.connect(self.hide)
        outer.addWidget(hide)
        warning = QtWidgets.QLabel("Эмулятор выдаёт синтетические данные. Реальный USB и Windows-пакет требуют отдельной проверки. Только согласованный низковольтный стенд; шкалы не калиброваны.")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        form = QtWidgets.QFormLayout()
        # Stack full-size fields below their labels rather than add both widths
        # offscreen. Vertical scrolling keeps every control reachable.
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapAllRows)
        self.source = _ContentSizedComboBox()
        self.source.addItem("Эмулятор (синтетика)", "emulator")
        self.source.addItem("Прибор (COM-порт)", "device")
        self.port = QtWidgets.QLineEdit()
        self.port.setPlaceholderText("Например, COM3; автоматического поиска нет")
        # Native multiline labels keep every word readable without imposing the
        # entire sentence's width on a small, vertically scrollable panel.
        self.reset = QtWidgets.QCheckBox("Подтверждаю\nфизический сброс")
        self.reset.setToolTip("Открытие COM-порта не подтверждает сброс прибора.")
        self.mode = _ContentSizedComboBox()
        for title, value in (("Auto", 0), ("Normal", 1), ("Single", 2)):
            self.mode.addItem(title, value)
        self.mode.setCurrentIndex(1)
        self.edge = _ContentSizedComboBox()
        self.edge.addItem("Нарастающий", 0)
        self.edge.addItem("Спадающий", 1)
        self.level = QtWidgets.QSpinBox()
        self.level.setRange(1, 4094)
        self.level.setValue(2048)
        self.pretrigger = _ContentSizedComboBox()
        self.pretrigger.setObjectName("acquisitionPretrigger")
        self.pretrigger.setAccessibleName("Предыстория: число отсчётов и доля окна")
        self.pretrigger.addItem("0 отсчётов · 0%", 0)
        self.pretrigger.setToolTip("Выбор по CAPS текущего соединения. Принятое значение видно ниже после CONFIG_ACK.")
        self.pretrigger_status = QtWidgets.QLabel("Предыстория: возможности ещё не подтверждены.")
        self.pretrigger_status.setWordWrap(True)
        self.pretrigger_status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.timeout = QtWidgets.QSpinBox()
        self.timeout.setRange(100, 60000)
        self.timeout.setValue(1000)
        self.timeout.setSuffix(" мс")
        self.simulate = QtWidgets.QCheckBox("Событие запуска\nв эмуляторе")
        self.simulate.setChecked(True)
        for title, widget in (("Источник", self.source), ("COM-порт", self.port), ("Режим", self.mode), ("Фронт", self.edge), ("Уровень · код АЦП", self.level), ("Предыстория", self.pretrigger), ("Ожидание Auto", self.timeout)):
            form.addRow(title, widget)
        layout.addLayout(form)
        layout.addWidget(self.pretrigger_status)
        layout.addWidget(self.reset)
        reset_hint = QtWidgets.QLabel("После ошибки перед новым подключением прибора требуется физический сброс / переподключение. Открытие COM-порта не доказывает сброс.")
        reset_hint.setWordWrap(True)
        layout.addWidget(reset_hint)
        layout.addWidget(self.simulate)
        profile = QtWidgets.QLabel("Профиль LV-001 · один канал · аналоговый запуск. Предыстория выбирается по объявленным возможностям соединения. Обычный «Старт» хранит последнее полное окно; Single требует нового «Старт» после каждого окна. Для архива всех принимаемых сегментов выберите Record. Закрытие панели не останавливает захват.")
        profile.setWordWrap(True)
        layout.addWidget(profile)
        self.device_details = QtWidgets.QLabel("Сведения о соединении: CAPS ещё не подтверждены.")
        self.device_details.setObjectName("acquisitionDeviceDetails")
        self.device_details.setWordWrap(True)
        self.device_details.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.device_details.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.connect_button = QtWidgets.QPushButton("Подключить ·\nбез запуска захвата")
        self.start_button = QtWidgets.QPushButton("Старт")
        self.stop_button = QtWidgets.QPushButton("Стоп")
        self.record_button = QtWidgets.QPushButton("Record ·\nзаписать сеанс")
        self.record_button.setToolTip("Auto / Normal: отдельные окна до Stop. Single: одно окно. Между окнами есть неизвестные промежутки.")
        self.save_recording_button = QtWidgets.QPushButton("Сохранить сеанс\n.mtrec…")
        self.cancel_recording_button = QtWidgets.QPushButton("Отменить сохранение\nсеанса")
        self.recording_status = QtWidgets.QLabel(recording_status_text(None))
        self.recording_status.setWordWrap(True)
        self.recording_status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.recovery_path = QtWidgets.QLineEdit()
        self.recovery_path.setReadOnly(True)
        self.recovery_path.setAccessibleName("Путь файла восстановления; можно скопировать")
        self.recovery_path.setPlaceholderText("Путь файла восстановления; можно скопировать")
        self.disconnect_button = QtWidgets.QPushButton("Отключить")
        for button in (self.connect_button, self.start_button, self.record_button, self.stop_button, self.save_recording_button, self.cancel_recording_button, self.disconnect_button):
            layout.addWidget(button)
        self.device_status = QtWidgets.QLabel(reported_device_status_text(None))
        self.device_status.setObjectName("acquisitionDeviceStatus")
        self.device_status.setWordWrap(True)
        self.device_status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.device_status)
        self.status = QtWidgets.QLabel("Не подключено")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        layout.addWidget(self.recording_status)
        recovery_label = QtWidgets.QLabel("Файл восстановления · путь можно скопировать:")
        recovery_label.setWordWrap(True)
        layout.addWidget(recovery_label)
        layout.addWidget(self.recovery_path)
        layout.addWidget(self.device_details)
        self.connect_button.clicked.connect(self._connect)
        self.start_button.clicked.connect(self._start)
        self.record_button.clicked.connect(self._record)
        # Ordinary acquisition can also be embedded in a simple viewer. Only
        # offer recording when its save/close lifecycle is available as well.
        save_recording = getattr(self.viewer, "save_recording_dialog", None)
        cancel_save = getattr(self.viewer, "cancel_recording_save", None)
        self._recording_supported = (hasattr(self.bridge, "record") and callable(save_recording)
            and callable(cancel_save) and callable(getattr(self.viewer, "confirm_recording_exit", None)))
        if self._recording_supported:
            self.save_recording_button.clicked.connect(save_recording)
            self.cancel_recording_button.clicked.connect(cancel_save)
        self.stop_button.clicked.connect(lambda: self._request(self.bridge.stop))
        self.disconnect_button.clicked.connect(self._disconnect)
        self.source.currentIndexChanged.connect(self.refresh)
        self.mode.currentIndexChanged.connect(self.refresh)
        bridge.state_changed.connect(self.update_state)
        bridge.error.connect(self.show_error)
        self.refresh()

    def _request(self, operation):
        if operation():
            self._last_error = ""
            self.viewer._acq_requested = True
            self.viewer._set_actions()
        else:
            self.show_error("Команда не принята: дождитесь завершения предыдущего действия.")
        self.refresh()

    def _connect(self):
        if self.viewer.busy:
            return
        source = self.source.currentData()
        port = self.port.text().strip()
        if source == "device" and not port:
            self.show_error("Укажите COM-порт прибора.")
            return
        self._request(lambda: self.bridge.connect_source(source, port=port, reset_confirmed=self.reset.isChecked()))
        self.reset.setChecked(False)

    def _start(self):
        if self.viewer.busy:
            return
        self._request(lambda: self.bridge.start(self._settings(), simulate_edge=self.simulate.isChecked()))

    def _settings(self):
        mode = self.mode.currentData()
        return Config(mode=mode, edge=self.edge.currentData(), level_code=self.level.value(), pretrigger_samples=self.pretrigger.currentData(), auto_timeout_ms=self.timeout.value() if mode == 0 else 0)

    def _record(self):
        if self.viewer.busy or not self.record_button.isEnabled():
            return
        try:
            folder = recovery_directory()
            path = folder / ("recording-" + uuid4().hex + ".mtspool")
            # Lexical selection only: even mkdir/stat can stall on a filesystem.
            # Parent creation and exclusive file creation belong to the bridge's
            # disk worker, before CONFIG/ARM and behind its Stop/close fences.
            if len(str(path.absolute())) > 240:
                raise OSError("Слишком длинный путь папки восстановления (предел 240 символов)")
        except OSError:
            self.show_error("Не удалось выбрать путь восстановления. Проверьте путь папки данных пользователя и предел 240 символов.")
            return
        self._request(lambda: self.bridge.record(self._settings(), str(path), simulate_edge=self.simulate.isChecked()))

    def _disconnect(self):
        if getattr(self.state, "recording", None) is None:
            self._request(self.bridge.disconnect_source)
        elif self._recording_supported:
            if self.viewer.confirm_recording_exit("Отключить источник"):
                self._request(self.bridge.disconnect_source)
        else:
            self.show_error("Для закрытия сеанса нужен просмотрщик с поддержкой предупреждения о восстановлении.")

    def update_state(self, state):
        previous_phase = self.state.snapshot.phase if self.state and self.state.snapshot else Phase.IDLE
        self.state = state
        snapshot = state.snapshot
        phase = snapshot.phase if snapshot is not None else Phase.IDLE
        if phase is Phase.HELLO and previous_phase is not Phase.HELLO:
            self._last_error = ""
        self._show_device_details(state)
        self.device_status.setText(reported_device_status_text(state))
        current_recording = getattr(state, "recording", None)
        self.recording_status.setText(recording_status_text(current_recording))
        spool = current_recording.spool_path if current_recording is not None else None
        self.recovery_path.setText(spool or "")
        self.recovery_path.setCursorPosition(0)
        self.recovery_path.setToolTip("Выбранный путь; создание файла может быть не завершено. Созданный файл не удаляется автоматически: " + spool if spool else "Путь восстановления ещё не выбран")
        labels = {Phase.IDLE: "Не подключено", Phase.HELLO: "Согласование соединения", Phase.CAPS: "Чтение возможностей", Phase.READY: "Готово · захват остановлен", Phase.ARMED: "Захват / ожидание запуска", Phase.RECOVERY: "Ошибка · требуется восстановление соединения"}
        text = labels[phase]
        if state.start_pending:
            text = "Применение настроек перед запуском"
        if snapshot is not None and snapshot.stop_barrier:
            text = "Остановка · ожидание подтверждения прибора"
        if state.reset_required:
            text += ". Для прибора подтвердите физический сброс перед повторным подключением."
        if self._last_error:
            text += "\n" + self._last_error
        self.status.setText(text)
        self.refresh()

    def _show_device_details(self, state):
        snapshot = state.snapshot
        source = {"emulator": "Эмулятор · синтетические данные", "device": "Прибор · выбранный COM-порт"}.get(state.source, "Источник не выбран")
        current = (state.connected and snapshot is not None
                   and snapshot.phase in (Phase.READY, Phase.ARMED)
                   and snapshot.capabilities is not None)
        if not current:
            self.device_details.setText(source + "\nCAPS не подтверждены для текущего соединения.\nОжидаемый формат: wire 0.1 (draft); версия прошивки и возможности пока неизвестны.")
            return
        caps = snapshot.capabilities
        modes = ", ".join(name for bit, name in ((1, "Auto"), (2, "Normal"), (4, "Single")) if caps.mode_bits & bit) or "нет"
        edges = ", ".join(name for bit, name in ((1, "нарастающий"), (2, "спадающий")) if caps.edge_bits & bit) or "нет"
        self.device_details.setText(
            source + "\n"
            + f"CAPS приняты в этом сеансе. Формат кадров: wire 0.1 (draft).\n"
            + f"Объявленная сборка прошивки: {caps.firmware_build} (0x{caps.firmware_build:08x}); это не версия SemVer и не SHA.\n"
            + "Серийный номер и версия SemVer не передаются этим контрактом.\n"
            + f"Профиль {caps.profile}; окно {caps.window_samples} отсчётов; предыстория: {', '.join(map(str, caps.pretrigger_samples))}.\n"
            + f"Режимы: {modes}; фронты: {edges}.\n"
            + f"Маски возможностей/режимов/источников/фронтов: 0x{caps.capability_bits:x}/0x{caps.mode_bits:x}/0x{caps.source_bits:x}/0x{caps.edge_bits:x}.\n"
            + f"Таймер: {caps.timer_clock_hz} Гц, PSC={caps.timer_psc}, ARR={caps.timer_arr}; АЦП: {caps.adc_clock_hz} Гц, sample_cycles_x2={caps.adc_sample_cycles_x2}.\n"
            + "Параметры объявлены устройством; физическая точность и безопасность не подтверждены.")

    def clear_error(self):
        self._last_error = ""
        if self.state is not None:
            self.update_state(self.state)

    def show_error(self, text):
        self._last_error = str(text)
        self.status.setText(self._last_error)

    def refresh(self, *_):
        state = self.state
        connected = bool(state and state.connected)
        snapshot = state.snapshot if state else None
        phase = snapshot.phase if snapshot is not None else Phase.IDLE
        pending = self.viewer._acq_requested or bool(state and (state.busy or state.start_pending or state.closing))
        idle = not connected and not pending
        ready = connected and phase is Phase.READY and not pending
        if snapshot is not None:
            ready = ready and not (snapshot.pending_request_id or snapshot.pending_stop_id or snapshot.window_barrier)
        self.connect_button.setEnabled(idle and not self.viewer.busy)
        recording = getattr(state, "recording", None)
        can_restart = (recording is None or (recording.saved_path is not None
                       and not recording.writer_busy and not recording.saving and not recording.retired))
        self.start_button.setEnabled(ready and can_restart and not self.viewer.busy)
        self.record_button.setEnabled(ready and can_restart and not self.viewer.busy and self._recording_supported)
        self.save_recording_button.setEnabled(bool(self._recording_supported and recording and recording.ready_to_save
            and not recording.retired and not pending and not self.viewer.busy))
        self.cancel_recording_button.setEnabled(bool(self._recording_supported and recording and recording.saving and not recording.retired))
        self.stop_button.setEnabled(connected and phase is not Phase.RECOVERY and not (state and state.closing))
        self.disconnect_button.setEnabled((connected or bool(state and state.close_failed)) and not (state and state.closing))
        for widget in (self.source, self.port, self.reset):
            widget.setEnabled(idle)
        for widget in (self.mode, self.edge, self.level):
            widget.setEnabled((idle or ready) and not self.viewer.busy)
        current_caps = (snapshot.capabilities if connected and snapshot is not None
                        and phase in (Phase.READY, Phase.ARMED) else None)
        choices = (0,)
        if (current_caps is not None and current_caps.capability_bits & 32
                and current_caps.pretrigger_samples == (0, 512, 1024)):
            choices = current_caps.pretrigger_samples
        if choices != tuple(self.pretrigger.itemData(i) for i in range(self.pretrigger.count())):
            selected = self.pretrigger.currentData()
            self.pretrigger.clear()
            for count in choices:
                self.pretrigger.addItem(f"{count} отсчётов · {count * 100 // 2048}%", count)
            self.pretrigger.setCurrentIndex(max(0, self.pretrigger.findData(selected)))
        self.pretrigger.setEnabled(ready and can_restart and not self.viewer.busy and len(choices) > 1)
        if current_caps is None:
            pre_text = "Предыстория: возможности ещё не подтверждены."
        elif len(choices) == 1:
            pre_text = "Соединение поддерживает только нулевую предысторию."
        else:
            pre_text = "Доступно по CAPS: 0 / 512 / 1024 отсчёта (0 / 25 / 50% окна)."
        if current_caps is not None and snapshot.config is not None:
            count = snapshot.config.pretrigger_samples
            pre_text += f" Принято по CONFIG_ACK: {count} отсчётов ({count * 100 // 2048}%)."
        self.pretrigger_status.setText(pre_text)
        self.timeout.setEnabled((idle or ready) and self.mode.currentData() == 0 and not self.viewer.busy)
        self.port.setEnabled(idle and self.source.currentData() == "device")
        self.reset.setEnabled(idle and self.source.currentData() == "device")
        self.simulate.setEnabled((idle or ready) and self.source.currentData() == "emulator" and not self.viewer.busy)

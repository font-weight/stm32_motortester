"""Explicit acquisition controls; serial I/O belongs to AcquisitionBridge."""
from PySide6 import QtCore, QtWidgets
from .session import Config, Phase


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
        self.timeout = QtWidgets.QSpinBox()
        self.timeout.setRange(100, 60000)
        self.timeout.setValue(1000)
        self.timeout.setSuffix(" мс")
        self.simulate = QtWidgets.QCheckBox("Событие запуска\nв эмуляторе")
        self.simulate.setChecked(True)
        for title, widget in (("Источник", self.source), ("COM-порт", self.port), ("Режим", self.mode), ("Фронт", self.edge), ("Уровень · код АЦП", self.level), ("Ожидание Auto", self.timeout)):
            form.addRow(title, widget)
        layout.addLayout(form)
        layout.addWidget(self.reset)
        reset_hint = QtWidgets.QLabel("После ошибки перед новым подключением прибора требуется физический сброс / переподключение. Открытие COM-порта не доказывает сброс.")
        reset_hint.setWordWrap(True)
        layout.addWidget(reset_hint)
        layout.addWidget(self.simulate)
        profile = QtWidgets.QLabel("Профиль LV-001 · один канал · аналоговый запуск · предыстория 0. Single требует нового нажатия «Старт» после каждого окна. При повторном захвате в памяти остаётся последнее полное окно; для сохранения нажмите «Стоп». Закрытие этой панели не останавливает захват.")
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
        self.disconnect_button = QtWidgets.QPushButton("Отключить")
        for button in (self.connect_button, self.start_button, self.stop_button, self.disconnect_button):
            layout.addWidget(button)
        self.status = QtWidgets.QLabel("Не подключено")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        layout.addWidget(self.device_details)
        self.connect_button.clicked.connect(self._connect)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(lambda: self._request(self.bridge.stop))
        self.disconnect_button.clicked.connect(lambda: self._request(self.bridge.disconnect_source))
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
        mode = self.mode.currentData()
        settings = Config(mode=mode, edge=self.edge.currentData(), level_code=self.level.value(), auto_timeout_ms=self.timeout.value() if mode == 0 else 0)
        self._request(lambda: self.bridge.start(settings, simulate_edge=self.simulate.isChecked()))

    def update_state(self, state):
        previous_phase = self.state.snapshot.phase if self.state and self.state.snapshot else Phase.IDLE
        self.state = state
        snapshot = state.snapshot
        phase = snapshot.phase if snapshot is not None else Phase.IDLE
        if phase is Phase.HELLO and previous_phase is not Phase.HELLO:
            self._last_error = ""
        self._show_device_details(state)
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
        self.start_button.setEnabled(ready and not self.viewer.busy)
        self.stop_button.setEnabled(connected and phase is not Phase.RECOVERY and not (state and state.closing))
        self.disconnect_button.setEnabled((connected or bool(state and state.close_failed)) and not (state and state.closing))
        for widget in (self.source, self.port, self.reset):
            widget.setEnabled(idle)
        for widget in (self.mode, self.edge, self.level):
            widget.setEnabled((idle or ready) and not self.viewer.busy)
        self.timeout.setEnabled((idle or ready) and self.mode.currentData() == 0 and not self.viewer.busy)
        self.port.setEnabled(idle and self.source.currentData() == "device")
        self.reset.setEnabled(idle and self.source.currentData() == "device")
        self.simulate.setEnabled((idle or ready) and self.source.currentData() == "emulator" and not self.viewer.busy)

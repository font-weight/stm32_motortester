# USB-005: черновая связь TinyUSB с эпохами операций

Задача: https://github.com/font-weight/stm32_motortester/issues/60.
Автор production-кода: Dot A. Независимая приёмка ещё не выполнена.

## Проверяемая область

Один foreground owner, STM32F103xB, OS_NONE, один CDC, HWFIFO, один
элемент очереди за вызов task. Исходный TinyUSB остаётся неизменным;
патч применяется к результатам USB003/USB004 в отдельном build-каталоге.

- Private queue envelope сохраняет исходную hard epoch.
- Принятые SETUP считаются ровно один раз; любой снятый SETUP уменьшает
  счётчик, включая устаревший. Потеря любого текущего события закрывает эпоху.
- BUS_RESET меняет metadata на ingress. Failed completion закрывает gate
  до обработки события foreground. ISR не меняет token активной операции.
- Выделенная функция dispatch гарантирует выход из scope даже при раннем
  возврате TinyUSB. Вложенный task не снимает события активного callback.
- Девять явных DCD shim проверяют сохранённый actor и удерживают внешний
  guard через полный вызов. Linker --wrap не используется как гарантия.
- Результат скрытого EP0 submission SET_ADDRESS передаётся отдельным hook;
  отсутствие receipt тоже считается отказом. Одна операция даёт один fault.
- Открытие config1 и успешная постановка status необходимы для configured.
  Mount сам gate не открывает. Это submission, не подтверждение ACK хостом.
- Config0 намеренно закрывает эпоху ДО teardown. Ответ/повторная конфигурация
  без нового физического reset в этой итерации не поддерживаются. Это
  ограничение кандидата, а не требование ко всем корректным USB-host.

## Обязанности будущего owner

До init USB вызвать bridge_init с проверенной парой save/restore interrupt
mask. Все metadata-доступы выполняются под этой парой. Нельзя переинициализировать
bridge, пока существуют старые события. IRQ USB HP/LP невложенные; другие ISR,
NMI, пользовательские классы, рекурсивный deinit и произвольные DCD-вызовы
не входят в область.

В runtime этой версии запрещены также обычные public API
`tud_remote_wakeup`, `tud_connect`, `tud_disconnect`, `tud_configure`,
`tud_sof_cb_enable`/`usbd_sof_enable`, `usbd_edpt_iso_alloc` и
`usbd_edpt_iso_activate`: app_enter не делает их защищёнными автоматически.
`tud_init`/`tusb_init` и `tud_deinit` допустимы только в отдельно
сериализованных lifecycle-фазах вне live actor/task, не из callback.
USB test mode выключен; приложение не добавляет class drivers, SOF либо
xfer ISR callbacks. Разрешённый runtime ограничен task, перечисленными
CDC open/read/write/flush путями и контрольными запросами pinned CDC.
Новые классы, прямые DCD-вызовы либо эти API требуют отдельного расширения
карты guard и тестов. Наличие символа в библиотеке не означает допуск.

Перед dispatch новой эпохи owner выполняет quarantine/Stop в foreground и
фиксирует cleaned(E). Это receipt попытки очистки, не доказательство успешного
аппаратного Stop. Контроль неуспешного Stop, свежести времени и допуска ADC ARM
должен быть отдельно связан перед готовностью целого прибора.

Каждая прикладная операция CDC должна быть целиком внутри app_enter/leave;
вложенные read/write наследуют actor callback. Stable/ready/DTR без этого
scope недостаточны. Положительный результат записи остаётся количеством
скопированных в FIFO байтов, даже если дальнейший submit отказал.

Adapter в этом черновике оборачивает step/write/open в сохраняемые scopes.
Hard loss теперь допускает повторную quarantine в той же физической epoch
для queue/submission fault; это отдельно проверяется регрессионным тестом.
Полного owner pump пока нет: fake-cleaned не выдаётся за аппаратный Stop.

## Что пока не доказано

Реальные PRIMASK/vector/priority/PRIGROUP, латентность под ADC/TIM,
плата/USB enumeration, физическое прекращение старых передач, PMAOVR,
полная ISR cancellation, полный ELF и его RAM/Flash/stack, Windows-связность.
Native fake lock не является доказательством поведения STM32.
Буферизованный не-F1 режим не получает обещания сохранности TX при soft pause.

# FSDEV STM32F103: сброс, сохранённый CTR и контракт сериализации

Дата: 10.10.2026. Область: предпосылка следующего USB bridge для `font-weight/stm32_motortester`, STM32F103C8T6, один CDC, OS_NONE. Уровень: **анализ точных исходников и первичной документации**. Это предварительное предложение автору Dot A, не принятый контракт, не PASS критика и не доказательство поведения кремния. Связь: MVP-F06; USB001/PR56, USB002/PR57 и подготовленный USB003 не объявляются полным bridge.

## 1. Результат для интегратора

**Нельзя считать pinned FSDEV reset достаточным cancellation barrier.** В нём остаётся конкретный источник completion после публикации нового BUS_RESET: аппаратные CTR ненулевых физических endpoint-регистров. Это отдельная проблема от семи ранее исследованных очередных контрпримеров; они здесь не повторялись.

Рекомендую узкую F103-only коррекцию `handle_bus_reset`: при выключенной USB function очистить оба CTR во всех **8 физических EP-регистрах**, очистить весь `xfer_status`, затем восстановить EP0 штатным `edpt0_open` и лишь после этого включить function. Область именно реального bus/FRES-reset; это не общий алгоритм закрытия активного endpoint. Коррекция не заменяет фильтр эпох очереди, раннюю защёлку, admission guard DCD и processed-reset receipt.

Нужен одновременный контракт: USB HP/LP никогда не исполняют DCD вложенно; foreground защищает проверку токена вместе с фактической DCD-операцией; другие ISR не вызывают TinyUSB. Без этого старый обработчик, уже сохранивший локальные данные, может продолжиться после reset другого обработчика.

**Физическое утверждение остаётся открытым:** прекращение транзакций, синхронизация APB/USB, повторный reset в узких окнах и измеренная latency требуют отдельной целевой проверки. Ни этот отчёт, ни будущая модель регистров этого не измеряют.

## 2. Точные входы и первичные основания

Проверены все 35 файлов исходного `SHA256SUMS.sources`: каждый `OK`, журнал `evidence/source-verification.log`.

- TinyUSB: `dae3f9a366bfcddbf9dcf1b48d7500286a849539`, исходный vendor-каталог родителя без изменений.
- ST CMSIS Device F1: `c8e9a4a4f16b6d2cb2a2083cbe5161025280fb22`, `stm32f103xb.h`.
- ARM CMSIS_5: `2b7495b8535bdcb306dac29b9ded4cfb679d7e5c`, `cmsis_gcc.h`, `core_cm3.h`.
- Прочитан исходный отчёт `usb-reset-barrier-analysis/REPORT_RU.md`, AGENTS и quality-gate, относящиеся к задаче требования/решения/статус/MVP/координация из предоставленного context. Отдельного `task.md` в этой копии context нет; узкие границы заданы назначением родителя.
- Реальные PDF скачаны с официального ST, преобразованы `pdftotext`; версии установлены по самим PDF, не по поисковому сниппету. Общий web-open большого RM0008 не прошёл ограничение размера, прямое получение официального PDF прошло.

### Факты документации, используемые далее

[RM0008 Rev21](https://www.st.com/resource/en/reference_manual/cd00171190.pdf): §23.5.2, стр.644–647: bus-reset/FRES обнуляет EPnR, кроме сохраняемых CTR_RX/TX; CTR очищается записью нуля, DTOG/STAT меняются записью единицы. SETUP доступен только для чтения. §23.5.1, стр.640,643: ISTR.CTR только для чтения; RESET блокирует обмен до очистки; EF=0 запрещает транзакции. §23.3, стр.625: LP(20) получает USB-события, HP(19) обслуживает CTR из ISO/double-buffer bulk, Wakeup(42) связан с suspend. §23.4.2, стр.626: восстановление EP0 после reset имеет ограничение 10 мс от конца reset.

[PM0056 Rev7](https://www.st.com/resource/en/programming_manual/cd00228163.pdf), §2.3.6, стр.36: одинаковый group/preemption priority запрещает взаимное вытеснение IRQ; subpriority задаёт порядок ожидающих IRQ. §2.1.3, стр.20: PRIMASK закрывает configurable-priority exceptions, но не NMI/HardFault. Это сведения архитектуры, не проверка нашей vector table.

[ES096 Rev15](https://www.st.com/resource/en/errata_sheet/es096-stm32f101x8b-stm32f102x8b-and-stm32f103x8b-mediumdensity-device-limitations-stmicroelectronics.pdf), §2.12: описаны ложный wakeup, ESOF после resume, CRC16 в PMA и ошибочный RX count при низкой APB1. Отмены правила сохранения CTR там не найдено. Для точного MPS=64 предлагается APB1 ≥13 МГц, а не одно лишь условие ≥10 МГц из комментария DCD. Маркировка и REV_ID конкретной платы остаются непроверенными; также проверить применимость §2.2.9 о старых ревизиях Z/B и компиляторах. Отсутствие отдельной erratum не доказывает отсутствие всех аппаратных гонок.

## 3. Что на самом деле выполняет pinned DCD

Основной источник: [dcd_stm32_fsdev.c](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c).

### Порядок одного вызова ISR

`dcd_int_handler`, строки 392–469:

1. Один раз читает `int_status = ISTR`.
2. При SOF из этого snapshot и включённом SOFM очищает SOF и вызывает `dcd_event_sof`.
3. При RESET из **того же начального snapshot** очищает RESET, вызывает `handle_bus_reset`, публикует BUS_RESET, сразу возвращается.
4. Иначе по snapshot обрабатывает WKUP, SUSP, ESOF.
5. Пока текущий ISTR содержит CTR, читает физический `ep_id`, сохраняет локальный `ep_reg`, сначала обрабатывает RX/SETUP, потом TX из того же `ep_reg`.
6. После цикла обрабатывает PMAOVR, если тот был в начальном snapshot. Это breakpoint/очистка, не полноценная ошибка transfer, переданная bridge.

Следовательно, «RESET обрабатывается первым» **неточно**: SOF раньше; RESET, появившийся после начального чтения ISTR, в этом вызове не перепроверяется. Это не утверждение о дефекте всего upstream, а ограничение применимости предлагаемого нами barrier.

### Состояние после штатного `handle_bus_reset`

| Объект | Реальное действие исходника |
|---|---|
| DADDR | 0 в начале, EF без адреса в конце |
| `ep_alloc_status[8]` | Каждый slot становится свободным, ep_num/ep_type = 0xFF |
| `ep_buf_ptr` | Сброс к началу PMA после BTABLE |
| Физический EP0 | `edpt0_open` переоткрывает обе стороны |
| Физические EP1…EP7 | Нет ни чтения, ни записи EPnR в reset-функции |
| `xfer_status` | Нет общей очистки; EP0 получает только новый MPS и ep_idx=0 |
| EP0 BTABLE | Новые адреса RX/TX, подготовка RX под SETUP |
| Остальные BTABLE и PMA payload | Не очищаются |
| `remoteWakeCountdown` | Не обнуляется |
| Старые события core queue | Не удаляются этой функцией |

Ссылки: [reset 241–263](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c#L241-L263), [EP0 553–579](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c#L553-L579).

EP0 — важное исключение: `ep_read(0) & ~U_EPREG_MASK` исключает CTR из записываемого значения; последующий `ep_write(0,...)` очищает их. Такой операции для ненулевых физических slots нет. Очистка программной allocation table сама по себе никак не затрагивает MMIO-флаги.

## 4. Конкретная трасса сохранённого completion

Это вывод из исходников и документированной модели, **не выполненный аппаратный опыт**.

1. Старая транзакция оставляет CTR_TX или CTR_RX в физическом slot 1…7.
2. Приходит USB reset. Применяем постусловие RM из §2.
3. ISR выбирает reset-ветвь, восстанавливает только EP0, вызывает BUS_RESET. Будущий bridge уже увеличил epoch до E+1.
4. Следующий ISR видит оставшийся CTR ненулевого slot. В `handle_ctr_tx/rx` номер логического endpoint берётся из EA: `ep_num = ep_reg & U_EPADDR_FIELD`, а не из physical `ep_id` и не из allocator.
5. После reset EA такого slot равен нулю. TX использует `xfer_status[0][IN]`, RX — `[0][OUT]`; событие выдаётся как EP0 и будет помечено **новой** эпохой на входе в core.

TX может опубликовать ложное EP0 completion либо продолжить передачу по сохранившимся длинам. RX до публикации completion уже читает count/данные из BTABLE физического slot и копирует их через EP0 transfer state. Это может направить старые bytes в неверный буфер; при несовместимых длинах/указателе возникает риск выхода за границы. Здесь не заявляется, что конкретный memory corruption уже воспроизведён.

Пути: [TX 266–298](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c#L266-L298), [RX 333–389](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c#L333-L389).

Обычный tail-chain скорее обработает этот CTR **до** reconfiguration. Уже этого достаточно для нарушения cancellation. Более сильное утверждение «обязательно доживёт до нового CDC open» из исходника не следует. Но если обработка отложена, `dcd_edpt_open` явно сохраняет оба CTR (`| U_EP_CTR_TX | U_EP_CTR_RX`, строки 590–625); переиспользование slot само по себе не является очисткой. В драйвере нет generation/per-transfer ID, который восстановил бы происхождение такого CTR.

Per-transfer token, прикреплённый только к новой submission, тоже недостаточен без аппаратной очистки: сохранённый бит может быть приписан уже новому transfer. Сначала требуется quiesce/clear, затем reuse.

## 5. Оценка предложенной F103-only коррекции

### Предусловия

- Вызов пришёл из reset-ветви реального FSDEV ISR либо из init после `fsdev_core_reset`/FRES. В pinned DCD именно эти два call site.
- Выполнение reset-функции эксклюзивно относительно других USB ISR и foreground DCD-операций.
- `DADDR.EF=0` на всём промежутке sweep → state reset → EP0 rebuild. Не выполняются сторонние записи EPnR/BTABLE.
- Изменение ограничено проверенной F103-конфигурацией; не переносится молча на CH32, FSDEV32, ISO или другие MCU.

### Минимальная последовательность внутри reset

1. Выключить function (`DADDR=0`), как уже делает исходник.
2. По **FSDEV_EP_COUNT**, а не по размеру logical endpoint массива, записать `ep_write(i, 0, false)` для i=0…7. В том же ограниченном цикле оставить штатную очистку allocator.
3. Очистить весь `xfer_status` до EP0 open: старые buffer/ff, total_len/queued_len, MPS, ep_idx, iso flag больше не используются.
4. Сбросить PMA allocation штатным способом; вызвать штатный `edpt0_open`, восстанавливающий действующие EP0 MPS, индексы и buffers.
5. Включить function. BUS_RESET публикуется после возврата, как и сейчас.

При указанных предусловиях нулевая запись — достаточный **регистровый** discard обоих старых CTR. Вне их она не является универсальным «reset endpoint»: например, у активного endpoint STAT_VALID останется прежним. Поэтому не копировать эту коррекцию как доказательство безопасности `close_all`/SET_CONFIGURATION(0).

Исходный clear RESET перед `handle_bus_reset` не отменяет этот вывод: документация задаёт post-reset состояние DADDR/EP, а сама функция немедленно пишет DADDR=0. Для ещё более явной новой последовательности допустимо поставить DADDR=0 перед acknowledge RESET, но это уже отдельное изменение порядка, которое нужно предъявить тестам. Не переносить acknowledge вслепую в конец: следует отдельно определить обработку второго RESET, возникшего во время cleanup. Ни безусловное `ISTR=0`, ни очистка NVIC pending не заменяют endpoint sweep.

Полная очистка PMA payload не нужна для данного узкого доказательства: после sweep старые флаги не вызывают читателя; новые endpoint открываются/вооружаются штатно. Однако это **не** доказательство очистки class FIFOs/queued data: они остаются обязанностью epoch/core reset пути. Отдельное обнуление только `xfer_status` без sweep также недостаточно: ложный CTR всё ещё будет обработан, пусть с другими значениями.

`remoteWakeCountdown`, CNTR suspend/resume flags, PMAOVR и весь disconnect/suspend lifecycle этой коррекцией не закрываются. В первом bridge не включать remote wake/STOP как скрытую поддержанную функцию.

## 6. Точный минимальный IRQ/owner-контракт bridge

### Векторы и NVIC

Pinned [stm32f103xb.h](https://github.com/STMicroelectronics/cmsis-device-f1/blob/c8e9a4a4f16b6d2cb2a2083cbe5161025280fb22/Include/stm32f103xb.h#L81-L124) задаёт:

- IRQ19: `USB_HP_CAN1_TX_IRQn`, vector handler `USB_HP_CAN1_TX_IRQHandler`.
- IRQ20: `USB_LP_CAN1_RX0_IRQn`, handler `USB_LP_CAN1_RX0_IRQHandler`.
- IRQ42: `USBWakeUp_IRQn`, отдельная EXTI18 wakeup-линия.
- 4 реализованных priority bits; имена HP/LP не назначают NVIC priority автоматически.

[fsdev_stm32.h 177–260](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/fsdev_stm32.h#L177-L260) включает/выключает **оба** IRQ19/20. Wakeup оставлен выключенным; драйвер не обслуживает EXTI18 и не реализует STOP-mode wakeup. Board startup/vector implementation в исследованных зависимостях нет: реальные маршруты и priorities ещё не подтверждены.

Рекомендованный контракт:

1. Оба handler символа маршрутизируются в один DCD entry для rhport=0; в цепочке не вызывается второй HAL/USB stack. CAN выключен, общей PMA и общих vectors одновременно не использует.
2. У HP и LP одинаковый **preemption/group priority**, практически проще задать одинаковое полное priority value. PRIGROUP фиксируется и проверяется. Числовой уровень относительно ADC/TIM пока TBD по общему latency-бюджету.
3. Ни один другой IRQ, deferred producer, NMI/HardFault или reentrant callback не входит в TinyUSB/DCD. Аварийный IRQ может безопасно остановить hardware своим путём, но не выполнять USB reset/API.
4. Один foreground owner выполняет все TinyUSB application/core вызовы и lifecycle cleanup. Task не запускается из USB ISR.
5. В выбранном CDC нет `xfer_isr` и `sof` class callbacks ([usbd.c 173–183](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c#L173-L183)). Запретить app-driver добавки, которые нарушают это условие.
6. IRQ42/EXTI18 не включать без самостоятельной реализации arm/clear/wakeup. `ISTR.WKUP` продолжает обслуживаться in-band.

Для single-buffer CDC нормальный traffic ожидается в LP. Называть «HP не нужен, можно не иметь handler» ошибочно для pinned port: enable-функция включает оба IRQ. Одинаковый priority и корректный второй vector нужны даже если практический HP traffic пока отсутствует.

### Guard foreground и вложенное маскирование

Короткая critical section сохраняет PRIMASK, запрещает IRQ, проверяет actor token и состояние допуска, выполняет **целую ограниченную DCD-операцию**, затем точно восстанавливает прежний PRIMASK. Это включает state/PMA writes, не только последнюю запись EPnR. Metadata queue/enqueue/dequeue требует такой же согласованности по своей ограниченной транзакции.

Нельзя ограничиться внешним `dcd_int_disable`: `ep_write(...,true)`, `dcd_edpt_close_all` и OS_NONE queue операции безусловно снова включают NVIC IRQ. Сам `ep_write` запрещает IRQ лишь вокруг финального store; preceding read/modify и PMA copy находятся снаружи. См. [fsdev_common.h 328–350](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/fsdev_common.h#L328-L350), [osal_none.h 205–225](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/osal/osal_none.h#L205-L225).

PRIMASK защищает от фактического ISR entry, но не восстанавливает прежнее состояние NVIC enable bits. Поэтому простейший runtime-контракт: оба USB IRQ намеренно включены между обычными операциями; отдельные init/deinit/особые masked phases не вызывают queue/DCD через этот runtime guard. Если требуется сохранение индивидуального NVIC состояния, нужен явный nest-safe port, а не обещание, что один PRIMASK автоматически всё сохраняет.

Guard не включает целый `tud_task_ext`, весь CTR loop, controller poll, ожидание или logging. Другие разрешённые ADC/TIM IRQ могут прерывать USB ISR, если не касаются USB state; все они задерживаются на PRIMASK-участках. Packet size 64 делает конкретную копию ограниченной, но не задаёт её время. Требуются generated ARM instructions и измерение худшей задержки.

## 7. SOF и reset, пришедший внутри ISR

SOF до RESET при одном ISR не создаёт самостоятельной новой эпохи. При текущем CDC без ISR class callbacks foreground не успевает исполнить очередь между ними: SOF/синтезированный RESUME получает старый token, последующий BUS_RESET закрывает эпоху, consumer обязан отбросить старый event. Поэтому narrow sweep можно исследовать без изменения SOF-порядка, **если** bridge соблюдает эти ограничения. Нельзя расширять это рассуждение на произвольный SOF callback; там код может исполняться до reset latch.

Минимальная более простая конфигурация — не включать пользовательский SOF consumer и не использовать SOF как safety-timebase. Если требуется безусловный тезис «RESET до любого event», нужен отдельный проверяемый reorder/reset-first ingress, а не новое описание старого кода.

Если physical RESET приходит после начального чтения ISTR, старый ISR может ещё использовать локальный `ep_reg` или старый transfer pointer. При невложенных USB ISR software epoch пока старый; все опубликованные им events должны остаться старыми и быть отброшены после следующего reset entry. Sweep следующего reset нужен, чтобы не оставить его промежуточные MMIO/state изменения.

Это даёт гарантию только относительно **наблюдённого и сериализованного reset barrier**, не запрет любой инструкции DCD после электрического начала reset. Ускоряющие проверки RESET перед CTR/между RX и TX можно исследовать отдельно, но они не превращают произвольную MMIO-последовательность в аппаратно атомарную.

## 8. Минимальные проверяемые условия следующей итерации

### Регистровая модель / source-level tests

Это новая проверка FSDEV cleanup, не повтор старых семи queue probes. Другой назначенный worker готовит такую модель; результатов его тестов данный отчёт не присваивает себе.

- Все 8 физических slots, отдельно CTR_RX, CTR_TX и оба; после моделируемого bus-reset EA=0. Непатченный reset должен сохранять nonzero-slot CTR как отрицательный контроль, кандидат очищать их до EF enable.
- Перехват следующего CTR handling должен показать отсутствие callbacks/PMA copies, возникших только из старых flags. Проверить и потенциальный ложный EP0 путь оригинала.
- Old `xfer_status` с разными buffer/ff/length/index значениями полностью сброшен; после EP0 open обе стороны снова MPS=64, ep_idx=0. Ненулевые logical endpoint остаются невооружёнными.
- Проверять последовательность MMIO: EF off → sweep 0…7 → transfer clear → EP0 rebuild → EF on → BUS_RESET event. До enable endpoint 1…7 disabled; нет очистки только logical 0…2.
- Отрицательный контроль семантики: zero-write в искусственный VALID/DTOG=1 endpoint без reset не должен магически обнулять toggle bits. Plain-RAM регистры без rc_w0/toggle поведения не годятся как доказательство.
- Нормальный новый SETUP/EP0 IN/OUT после cleanup, затем новая конфигурация и первый CDC arm; старые значения не мешают свежей работе.
- Совместные SOF+RESET+CTR, RESET после entry snapshot, pending второй IRQ, повторный RESET во время cleanup. Для каждой injection обозначить, моделируется ли физическое reset-постусловие или лишь software event.

### Bridge/ARM проверки

- Link map/vector table реально содержит оба IRQ handler и один DCD маршрут; source/config checks подтверждают запрет CAN, ISO/double-buffer, STOP, app ISR callbacks.
- Runtime/startup evidence для PRIGROUP, HP/LP priorities/enables; искусственное одновременное pending показывает отсутствие вложенного DCD entry. Одного native mock NVIC недостаточно для target claim.
- PRIMASK на входе 0/1, вложенные queue и DCD calls, IRQ arrival между check и MMIO, между RX и TX. Exit точно восстанавливает mask; запрещённый actor не изменяет PMA/endpoint.
- Отдельные tests epoch envelope, queue loss, processed-reset/config receipt из предыдущего отчёта остаются обязательными. Sweep не даёт права снять их.

### Плата: открыто, не запускалось

Проверить настоящий MCU/REV_ID, clocks, USBCLK/APB1, board wiring/pull-up и binary SHA. На низковольтной USB-плате создать завершённый IN/OUT, временно отложить обслуживание IRQ, затем выполнить настоящий host bus reset. Логировать ISTR/EPnR и события в bounded RAM trace, без printf из ISR: до reset cleanup, до EF enable, после повторного entry, после нового EP0/CDC обмена. Сопоставить trace с host USB capture; где важен порядок пакетов/SE0, нужен анализатор/измерение, не только Windows API-вызов.

Повторить сочетания RX/TX, RESET+CTR, SOF, masked interval, repeated reset, reset на границе EP0 status и при bulk load. Критерий: до нового arm ни один старый CTR не порождает completion/copy, после новой конфигурации данные принадлежат новой эпохе; нет duplicate completion, зависшего EP0, искажения RX count. Измерить максимальную IRQ/guard latency под реальной ADC/TIM нагрузкой и время EP0 recovery; числовые допустимые пределы задать до испытания. Unplug/suspend и config0→config1 остаются отдельными acceptance-paths.

## 9. Граница результата и передача

В этой работе прочитаны источники, проверены hashes, получены официальные PDF и разобраны пути. Не выполнялись production edits, новые GitHub записи, сборка firmware, runtime FSDEV, MMIO, устройство, Windows, ADC либо аппаратные измерения. Приёмка будущего кода требует Critic-A ≥9.5 и соответствующих обязательных доказательств; общий reconnect policy требует согласования сторон по quality-gate.

К моменту завершения отчёта родитель сообщил об авторском USB004/issue58: кандидат F103-only sweep + `xfer_status` clear создан и скомпилирован в ARM object; отдельный worker готовит регистровые tests. Эти результаты не являются выполненными мною проверками и не заменяют независимого ревью. Следующее действие автора: связать точный patch/SHA с отрицательным контролем и evidence кандидата, затем подключать его как **предпосылку**, а не замену полноценного epoch/IRQ bridge. Утверждение о silicon cancellation остаётся заблокировано аппаратными доказательствами.

`evidence/provenance.json` фиксирует источники и hashes. Загруженные ST PDF и извлечённый текст — локальный кэш для проверки, не материал для перепубликации; пользователю/в PR достаточно ссылок на оригиналы, данного авторского отчёта и собственного кода проверок.

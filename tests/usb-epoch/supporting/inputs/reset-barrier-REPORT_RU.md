# USB: защёлка reset и барьер очереди, ограниченное предложение

Дата: 10.10.2026. Задача родителя: исследование следующего шага после [USB-001 / issue 46](https://github.com/font-weight/stm32_motortester/issues/46). Это предложение для реализации основным автором, **не принятый контракт, не исправление firmware и не независимый PASS**.

## Короткий вывод

Рекомендую отдельную opt-in доработку `usbd.c`: частный элемент очереди `{ dcd_event_t event; uint32_t epoch; }`, ранняя producer-защёлка, явный результат enqueue, фильтр до dispatch и подтверждение **уже выполненного** reset. Публичный `dcd_event_t` и API DCD менять не требуется. Нужны также прямые точки допуска/результата DCD-операций: фильтр очереди не останавливает callback, который уже начал исполняться и затем был прерван reset.

Один `--wrap=dcd_event_handler` решает лишь раннее наблюдение внешнего DCD-события. Этого недостаточно для требуемого прибором восстановления. Простое расширение до `--wrap=dcd_edpt_xfer_fifo` тоже недостаточно: на настоящем core с F1/HWFIFO этот wrapper **не вызывается** из-за weak-определения функции в том же `usbd.c`.

При переполнении очереди предлагается fail-closed: закрыть прикладной допуск и подавлять dispatch/I/O повреждённой эпохи; ограниченно вынимать старые события; ждать **следующего настоящего BUS_RESET, который успешно поставлен в очередь и обработан**. Ни пустая очередь, ни `ready`, ни resume/HELLO не снимают эту блокировку. Не требуется предполагать рабочее отключение pull-up, `tud_deinit`/`tud_init` или синтезировать BUS_RESET программно.

Все upstream-файлы, adapter и исходный fake DCD в ходе исследования оставлены неизменными. Написаны только отдельные наблюдательные программы в `probes/`. Здесь не реализован новый bridge.

## 1. Что проверено лично

Основа: TinyUSB 0.21.0, commit `dae3f9a366bfcddbf9dcf1b48d7500286a849539`; четыре неизменённых TU: `tusb.c`, `tusb_fifo.c`, `usbd.c`, `cdc_device.c`; неизменённый fake DCD из supporting harness. Два существующих config: buffered и F1 dedicated-HWFIFO. В обоих очередь 16, один event за вызов task, один CDC, FIFO/EP 64, persistence и overwrite выключены.

Сборка: GCC 14.2.0, upstream GNU11, probes/fake C11 `-pedantic-errors -Werror -Wall -Wextra`, `-O1 -fno-lto`; GNU ld 2.44. Выполнены семь программных контрпримеров в каждой конфигурации:

| Опыт | Наблюдение |
|---|---|
| `wrap` | buffered: один реальный submission, xfer-wrapper=1. HWFIFO: один реальный submission, xfer-wrapper=0, fifo-wrapper=0 |
| `overflow` | 16 принятых событий, затем BUS_RESET: ingress-wrapper=1, успешный reset-hook=0. После drain `ready=1`, fake CDC endpoint закрыт |
| `stale` | queued IN-complete на 64 байта и OUT-complete на 0 байт, затем reset и TX clear: последующий dispatch делает две недопустимые попытки, ZLP на 0 байт и OUT rearm на 64 |
| `reject` | DCD отклоняет submission; write всё равно возвращает положительные 64, FIFO снова свободен, host получил 0 байт. В HWFIFO оба submission-wrapper остаются 0 |
| `synth` | полная очередь, затем SOF после suspend: внутренний RESUME не принят в очередь; hook отсутствует, но producer уже снял `suspended` |
| `lost_setup_reset` | переполнение теряет SETUP; очередь опустошается; следующий BUS_RESET успешно принят и обработан, но новый GET_DESCRIPTOR пропускается: ответа EP0 нет из-за старого `_usbd_queued_setup` |
| `same_tu_address` | SET_ADDRESS создаёт EP0 status transfer через вызов из того же fake-DCD TU; внешний xfer-wrapper видит 0 вызовов |

Это **успешное воспроизведение отрицательных примеров**, а не PASS будущего решения. Код не скрывает ошибочные submissions: существующий fake DCD их считает и отклоняет. В отрицательном опыте завершение идёт через `tud_deinit`, без `fake_finish`, который справедливо требует ноль ошибок.

Полные команды, исходники, return codes, вывод и disassembly сохранены. В этой итерации не запускались sanitizer, ARM runtime, ELF/link firmware, IRQ/MMIO или плата. Существующий ARM object только прочитан через `nm`: `dcd_edpt_xfer` — U, `dcd_edpt_xfer_fifo` — W. Это дополнительное наблюдение символов, не новая ARM-сборка.

## 2. Почему публичных callbacks и простого wrapping недостаточно

1. [`queue_event`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c#L430-L434) сначала вызывает enqueue; hook исполняется только после успеха. Вызов hook означает «поставлено», не «обработано».
2. [`dcd_event_handler`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c#L1391-L1484) не возвращает признак успеха enqueue. SOF самостоятельно ставит RESUME/SOF; [`usbd_defer_func`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c#L1527-L1535) тоже обращается напрямую к частному `queue_event`.
3. [`tud_task_ext`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c#L704-L777) не имеет публичного pre-dispatch фильтра. После извлечения старого completion core меняет endpoint status и вызывает CDC. Один вызов task при лимите 1 не является барьером reset.
4. [`cdcd_xfer_cb`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/class/cdc/cdc_device.c#L460-L523) сам повторно выставляет OUT и автоматически продолжает TX/ZLP. Закрытие только application writer или очистка TX FIFO не запрещают эти действия.
5. `dcd_edpt_xfer_fifo` имеет [weak-определение внутри usbd.c](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c#L462-L465), откуда [вызывается](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c#L1608-L1632). Наблюдательный линк показывает реальный обход `--wrap`. Кроме того, настоящий FSDEV [`dcd_set_address`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c#L223-L231) вызывает xfer из собственного TU.
6. [`_usbd_queued_setup`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c#L1445-L1448) увеличивается раньше enqueue. При потере события уменьшения не будет; [`usbd_reset`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c#L650-L664) этот отдельный счётчик не очищает. Простое ожидание нового reset без исправления учёта недостаточно.

Обход через переименование символов при компиляции, переписывание objects, свои копии CDC или подмену inline OSAL теоретически возможен, но это уже отдельная интеграционная модификация с собственным ABI/build-контрактом. Называть её доказанным «неизменённым upstream + один wrapper» нельзя. Для текущих требований явный маленький opt-in patch проще проверять и сопровождать.

Эти наблюдения относятся к нашим требованиям безопасного восстановления прибора. Они не означают, что upstream обещал такую интеграционную политику, и не являются заявкой об upstream-дефекте.

## 3. Минимальная первая реализация: границы patch

**Предлагаемая область:** один частный opt-in участок в `usbd.c`, приватный header его hooks, firmware lifecycle-owner/bridge и native tests. Исходный reference upstream хранится отдельно с прежними hashes; применяемый patch и результирующие hashes получают отдельный provenance. Опция выключена по умолчанию; при включении отсутствующий bridge/port должен давать compile/link error, не слабый «всегда разрешить». Public DCD ABI и wire protocol остаются прежними.

Первый результат ограничить программной гарантией reset/queue/submission при данном single-core, OS_NONE, one-CDC config. Production включение блокируется до реализации и проверки target guard. Не объявлять полный USB lifecycle готовым по одному ядру очереди.

### 3.1. Producer: токен операции и защёлка

В самом начале `dcd_event_handler`, **до изменения `_usbd_dev` и до enqueue**, hook выдаёт локальный `event_epoch`. BUS_RESET увеличивает ненулевой epoch, немедленно закрывает stable и сбрасывает *кандидатные* reset/config подтверждения. Это только короткая публикация metadata. Controller/Stop, FIFO clear, логирование с блокировкой и прочие foreground-действия в ISR запрещены.

UNPLUGGED тоже закрывает допуск и инвалидирует эпоху; его обработка сама не становится подтверждением нового BUS_RESET. Suspend устанавливает отдельную sticky soft-loss защёлку, не увеличивая hard epoch.

Epoch присваивается в начале исходного события/операции и передаётся во все внутренние `queue_event`, включая порождённые RESUME/SOF. У `usbd_defer_func` также должен быть явно захваченный token. Нельзя заново безусловно читать current epoch только в конце enqueue: прерванный reset старый foreground producer иначе получает ложную новую метку. Устаревший producer token на момент enqueue означает намеренное отбрасывание obsolete-события, без отравления новой эпохи.

В первом config разрешены один foreground consumer и сериализованные USB IRQ producers; `xfer_isr`/SOF class callbacks отсутствуют у выбранного CDC. Иные классы, RTOS, вторые порты и произвольные вне-owner TinyUSB вызовы не включать молча. Отложенные функции, не относящиеся к USB epoch, нельзя незаметно терять: их использование в этом scoped config запретить либо отдельно определить их политику.

### 3.2. Очередь и overflow

Частный тип очереди содержит исходный event и epoch. Публичные DCD helpers ничего об этом не знают. Queue storage при ARM `sizeof(dcd_event_t)=12` увеличится с 192 до 256 байт: **+64 байта расчётно**, плюс metadata. Размер подтвердить новым object/map; native pointer size даёт другие размеры.

Единственная функция enqueue обслуживает все источники. Короткая защищённая транзакция охватывает проверку token, помещение полного envelope, учёт SETUP и публикацию результата. При неуспехе hook фиксирует `QUEUE_LOSS` для текущей эпохи **до** существующего ASSERT/возврата. Счётчик диагностики остаётся sticky; отсутствие hook из-за ASSERT больше не является способом обнаружения ошибки.

Любая неожиданная потеря текущего event отравляет эпоху, а не только потеря BUS_RESET. Потерянный completion оставляет busy/state недостоверными; потерянный SETUP нарушает EP0. Pending fault запрещает дальнейший normal dispatch и application I/O. Возврат к работе после обычного task drain недопустим.

Новый реальный BUS_RESET создаёт новую кандидатную эпоху. Если его enqueue снова неудачен, она тоже отравлена. Если позже в этой эпохе потерян хотя бы один event, даже уже обработанный reset не снимает блокировку: нужен следующий reset. Исторический счётчик ошибок при смене эпохи не стирается.

### 3.3. Учёт SETUP: обязательная часть, а не cleanup

- Перенести increment из ingress в **успешный enqueue** под той же защитой.
- На **каждом успешном dequeue SETUP**, в том числе при дальнейшем stale/fault drop, уменьшать счётчик ровно один раз под защитой.
- Убрать старый decrement из switch; сохранить смысл upstream «если в очереди уже есть более новый SETUP, старый не исполнять».
- Не обнулять счётчик вслепую на BUS_RESET: за reset уже могут быть приняты новые SETUP. Счётчик должен равняться количеству фактически ещё находящихся в очереди SETUP.
- Несоответствие счётчика очереди — отдельный fail-closed internal fault. Обработка снятого события уже не меняет это количество.

Это одновременно закрывает потерянный SETUP и фильтрацию старого SETUP перед новым reset. Обязателен опыт `lost_setup_reset` уже как положительный regression после авторской реализации.

### 3.4. Consumer и достоверный processed-reset barrier

После dequeue и учёта SETUP, **до изменения endpoint status, driver lookup, EP0 parser и callback**, сравнить envelope epoch с текущим snapshot. Старый epoch или отравленная эпоха: событие не dispatch-ится. Drop входит в тот же бюджет `TASK_EVENTS_PER_RUN=1`; не делать неограниченный цикл до reset.

Для разрешённого dispatch установить `active_dispatch_epoch`. Отдельное подтверждение BUS_RESET публикуется **после возврата `usbd_reset`**, с повторной проверкой epoch и fault. Именно это подтверждает выполнение reset всех class drivers и сброс core state. Ни ingress, ни enqueue, ни mount не заменяют его.

После новой ошибки или ещё одного reset старое подтверждение автоматически не совпадает с current epoch. Не нужно доказывать пустую очередь: известная метка каждого события и подтверждённая граница сильнее временного наблюдения empty.

При ранних возвратах/ASSERT внутри task owner обязан снять active-dispatch context при возврате из `tud_task_ext`; нельзя полагаться только на общий tail после switch, который может быть пропущен. Все TinyUSB вызовы делает один владелец.

### 3.5. Уже начавшийся callback и результаты submissions

Фильтр dequeue необходим, но недостаточен. Reset может прервать уже начавшийся callback после проверки envelope. Поэтому перед каждым аппаратно значимым действием проверяется **токен исполняемой операции**, а не только текущее состояние ready:

- `usbd_edpt_xfer` и `usbd_edpt_xfer_fifo`, включая ZLP и OUT rearm;
- используемые в scoped config open/close/close_all, stall/clear_stall;
- прямые core→DCD SET_ADDRESS и EP0 status-complete действия;
- другие реально достижимые DCD state-changing пути либо защищаются, либо запрещены конфигурацией/owner API.

Для перечисленных точек использовать прямые opt-in hooks/guard вокруг реального DCD вызова. Не рассчитывать на `--wrap`, особенно для FIFO, status-complete и same-TU вызовов. Один guard вокруг **внешнего** `dcd_set_address` защищает его внутренний status transfer как часть той же ограниченной операции.

Guard связывает проверку epoch с защищённым вызовом DCD. После наблюдённого reset старый actor не выполняет MMIO/FIFO submission; отказ возвращается по штатному API. Уже снятые старые bytes допустимо потерять только как часть аннулированной hard epoch. Само приложение позже выполняет hard-loss cleanup.

Нормальный `dcd_edpt_xfer[_fifo] == false` при ещё действительном actor означает новый `SUBMIT_FAILED`, синхронно закрывает stable и отравляет эпоху. Это отличается от ожидаемого отказа уже устаревшему actor. Неуспешный completion result также должен закрывать текущую эпоху до class callback: CDC и EP0 сейчас игнорируют `result`.

[`tu_edpt_stream_write`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/tusb.c#L401-L435) возвращает скопированный префикс независимо от автоматического flush. Этот positive результат остаётся честным и **не заменяется нулём/−1**. При normal failure нельзя продолжить live epoch и повторить тот же prefix: буферный путь уже извлёк его из FIFO, HWFIFO DCD также может его потребить. До нового физического reset сохранять объект TX/диагностику, остановить/recover controller и ждать; не выдавать потерянные байты за доставленные и не очищать живой поток для сокрытия ошибки.

Guard обязан разрешать EP0 enumeration и CDC open/первый OUT arm в **новой, уже reset-processed эпохе до app-open**. Иначе получаем тупик: CDC не сможет открыться, пока adapter закрыт. Поэтому допуск core enumeration и допуск application bytes — разные биты, оба привязаны к одному epoch.

## 4. Взаимодействие с существующим adapter

Рекомендуемый owner-порядок одного foreground шага:

1. Снять согласованный snapshot producer metadata. При новой hard epoch вызвать `mt_usb_cdc_hard_loss` до TinyUSB dispatch; при fault без физического reset закрыть допуск и вызвать soft recovery/Stop, сохраняя TX. `hardware_running` и failed Stop не стирать.
2. Выполнить не более одного разрешённого TinyUSB queue step. Фильтр/guard действуют и если reset пришёл после начального snapshot.
3. Повторно обработать новые pending loss/fault. Не подавать RX и не делать controller I/O между обнаружением потери и этим действием.
4. После processed BUS_RESET дождаться успешной обработки SET_CONFIGURATION в той же эпохе: реальные nonpersistent CDC open, действующие IN/OUT, отсутствие pending fault. Mount callback только сообщает кандидата; owner повторно проверяет всё **после возврата task**, включая ошибки status submission.
5. В foreground вызвать `open_epoch(E)` только если `E == current == reset_processed == configured`, нет hard/fault/soft-pending, `tud_cdc_n_ready(0)` и timebase healthy. `stable` пока должен означать допустимость этой проверенной эпохи, а не только `state==OPEN`, иначе получится круговая зависимость открытия.
6. Выполнить adapter step в application-operation scope с захваченным epoch. Если reset пришёл внутри write, вернуть истинный positive count; больше старых submissions guard не разрешает; следующая foreground граница делает hard cleanup.

Защита queue не означает защиту всех команд контроллера. Текущий adapter проверяет stable перед байтом, но ISR может прийти между проверкой и разбором последнего delimiter. Если этот delimiter приводит к ARM, аппаратный backend ARM тоже обязан проверить актуальный transport/timebase permit в своей короткой commit-секции. Запрещать IRQ вокруг целого controller poll или 64-byte RX loop ради этого нельзя. Это отдельное обязательство интегратора, не доказанная функция текущего `stable` callback.

Ранние **новые** bytes после конфигурации могут уже лежать в новом RX FIFO. Они относятся к новой эпохе и могут ждать app-open; старые carry/FIFO очищены hard loss + nonpersistent open. Не добавлять без необходимости дополнительный blind `read_flush`, который повторно выставляет OUT. Fresh HELLO/nonce выполняет прежние правила контроллера; bridge не переинициализирует controller, capture link или session вместо его штатного восстановления.

## 5. Target-safe синхронизация и её предел

Для первой однопроцессорной Cortex-M3 реализации предлагается явный port critical-section с сохранением и точным восстановлением PRIMASK через CMSIS. Короткие metadata-транзакции читают/меняют epoch, fault и подтверждения как единый snapshot. `volatile` не заменяет эту синхронизацию. IRQ не вызывает controller. USB HP/LP producers должны быть сериализованы; их реальные приоритеты и vector routing ещё требуется определить/проверить.

Почему недостаточно просто вызвать `dcd_int_disable` снаружи: OS_NONE [`queue_receive`/`queue_send`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/osal/osal_none.h#L205-L225) безусловно re-enable IRQ; FSDEV [`close_all`](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c#L630-L646) делает то же. Поэтому внешняя NVIC-блокировка не является nest-safe защитой. PRIMASK предотвращает фактическое принятие IRQ во время такой короткой транзакции, но порт всё равно обязан соблюдать явно установленное рабочее NVIC-состояние. Metadata lock не оборачивает init/deinit и произвольное управление IRQ.

Guard DCD-операции не должен содержать controller, ожидание, callback пользователя, целый task или drain. Для выбранного config длина packet transfer ограничена 64 байтами. Это делает участок конечным, **но не доказывает время**. Нужны ARM disassembly/ABI/symbols и измеренный верхний предел latency перед production/ADC claims. Существующий TIM/ADC deadline нельзя объявлять выполненным по этой оценке.

Альтернатива — lock-free C11 atomics после проверки конкретных generated ARM instructions/отсутствия `libatomic` locks. Но один atomic epoch не делает согласованными несколько полей, очередь, SETUP count и check→DCD call. Для этой небольшой модели критическая секция яснее; это рекомендация, не реализованный primitive.

Строгая доказуемая граница: **после публикации producer reset** старый actor не выполняет новую DCD-операцию. Маскирование CPU IRQ не останавливает сам USB bus. RESET hardware flag может появиться до ISR или во время короткой секции; один предварительный register check не устраняет весь физический race. Поведение настоящего FSDEV/reset cancellation надо подтвердить отдельно; native fake не моделирует MMIO.

Точно так же epoch на входе не распознает «старый аппаратный completion, доставленный уже после нового reset и повторного использования endpoint». Для F1 требуется доказанный cancellation/serialization контракт DCD либо дополнительные per-transfer проверки. Метка только текущей эпохи не делает старый completion новым и корректным. FSDEV [обрабатывает RESET раньше остальных флагов и возвращается](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c#L401-L406), но это исходниковое основание, не измеренная гарантия платы.

При исчерпании счётчика epoch запретить повторное использование старого значения; fail-closed с явной диагностикой. Не применять сравнение `>` без правила переполнения. Для обычных допусков достаточно равенства ненулевых токенов.

## 6. Disconnect, suspend и deconfiguration

FSDEV прямо пишет, что без VBUS detection [unplug и suspend неразличимы](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c#L417-L427). Поэтому:

- SUSPEND закрывает новые application I/O немедленно; foreground выполняет предлагаемую soft Stop/recovery, сохраняет TX/FIFO. Sticky pending не снимается только потому, что producer уже увидел RESUME/SOF.
- Ни RESUME, ни DTR high не доказывают новый hard boundary и не снимают `QUEUE_LOSS`/`SUBMIT_FAILED`.
- UNPLUGGED — hard loss, когда источник действительно его сообщил; нельзя выдавать отсутствие такого события за доказательство подключения.
- DTR policy остаётся явной опцией существующего adapter. Нового требования к Windows здесь нет.

Первая гарантия отсутствия stale DCD I/O относится к **наблюдённому hard reset**, а не к физическому unplug, который драйвер пока видит только как SUSPEND. Нельзя ради soft pause просто возвращать false из DCD после FIFO extraction: это теряет байты живой эпохи. Сохранение/отложенная обработка completions при suspend требует отдельной проверяемой политики; данный report её не объявляет реализованной.

`SET_CONFIGURATION(0)` — отдельная граница. Нельзя полагаться лишь на umount callback: он вызывается после teardown. Для полного lifecycle потребуется pre-teardown gate и подтверждение конфигурационной границы, включая config0→config1 без BUS_RESET и queued completions. **Минимальный первый patch не обязан объявлять этот сценарий автоматически восстановимым.** Он должен закрываться на обнаруженной deconfiguration и явно оставлять её reopen за доказанным внешним bridge/последующим настоящим reset. Эта ограниченная поддержка должна быть записана в новой задаче; она не является новым требованием «Windows всегда обязан делать reset» и не закрывает MVP-F06 полностью.

Если хочется включить автоматический config0→config1 сразу, это дополнительный acceptance-path с отдельной generation/teardown receipt, а не несколько строк в mount callback. Не распознавать произвольный SETUP с `wValue=0` как состоявшийся deconfiguration: нужны корректный recipient/type/request и реально принятая операция.

## 7. Инварианты для авторской реализации

1. Reset latch опубликован до попытки enqueue, в том числе при полной очереди.
2. Каждый принятый envelope имеет epoch исходного producer; rejected/stale enqueue не увеличивает SETUP count.
3. SETUP count равен фактическому числу SETUP в очереди; every pop уменьшает его один раз.
4. Ни stale, ни poisoned envelope не меняет core endpoint status и не вызывает class/control/deferred callback.
5. Callback, начатый раньше reset, не может после latch выполнить DCD-операцию со старым actor token.
6. Normal submission/completion failure становится наблюдаемым sticky fault до следующего app I/O; positive prefix не подменяется zero/error.
7. `reset_processed(E)` означает исполненный `usbd_reset`, а не enqueue. Reopen требует того же current E и успешной новой конфигурации.
8. Lost-reset/overflow epoch не оживает от ready, empty, DTR, HELLO или resume. Восстановление — новый успешно queued+processed BUS_RESET без последующей потери.
9. Stop/controller cleanup только foreground; failed Stop/diagnostics/nonce сохраняются по существующему контракту.
10. Ни loop budget, ни queue size, ни object compilation не выдаются за аппаратный deadline/USB enumeration.

## 8. Минимальная матрица будущей проверки

Сохранить семь текущих probes как reference отрицательного поведения. На patch нужны самостоятельные положительные tests с настоящими core/FIFO/CDC и fake DCD, отдельно buffered/HWFIFO:

- Queue full + reset: latch виден; никакого stale I/O; после bounded drain gate остаётся закрытым; новый реальный reset + новая конфигурация действительно открывают новую эпоху.
- Queue full + SETUP, completion, внутренний RESUME/deferred event: ни один отказ не скрыт. После следующего valid reset enumeration работает; SETUP count не застрял.
- Старые IN64/OUT0/SETUP/EP0 completion перед reset: ни payload, ни ZLP, ни rearm, ни SET_ADDRESS от них не достигают DCD.
- Два reset; reset между dequeue filter и callback; reset внутри writer; reset при configuration/reopen; overflow после queued reset: старое подтверждение никогда не открывает новую эпоху.
- Normal false для buffered/FIFO/EP0 submissions и failed completion: stable закрывается автоматически; реальный copied prefix сохранён в accounting, host receipt не выдуман.
- Metadata lock: injection перед stamp, между stamp/enqueue, вокруг queue publication и SETUP accounting; stale producer не получает новую метку.
- Проверка сохранения IRQ mask при вложенных OS_NONE queue operations и уже замаскированном входе; отсутствие controller calls в ISR; fresh HELLO повторяет failed Stop по прежнему пути.
- Старые successful adapter/codec/session/TX tests, strict C11 для проекта, upstream GNU11 с предупреждениями, ASan+UBSan. ARM guard compilation/disassembly отдельно; аппаратные проверки остаются открытыми.

Предъявить Critic-A точный patch/result SHA, scope и evidence; ≥9.5 требуется по [quality-gate](https://github.com/font-weight/stm32_motortester/blob/06178f31be79c3e7dbc5b46281a67a5c1d1da7f0/docs/quality-gate.md). Shared suspend/reconnect policy требует также Critic-B. Этот исследовательский отчёт не заменяет ни реализации, ни независимого ревью.

## 9. Артефакты и воспроизведение

- `probes/wrap_probe.c`, `probes/wrappers.c`: семь наблюдательных сценариев; не bridge implementation.
- `probes/run.py`: runner с неизменёнными исходниками upstream/fake; повторный запуск перегенерирует локальный `evidence/`, поэтому для сохранения замороженного пакета запускать его копию.
- `evidence/commands.json`, `commands.log`: argv, полный stdout/stderr и exit status; `buffered/` и `f1/`: разные objects/executables/disassembly.
- `evidence/input-manifest.json`: SHA-256 всех использованных upstream dependency files, config, fake harness, adapter/reference report и probe sources.
- `evidence/upstream-source-verification.log`: проверка исходного `SHA256SUMS.sources`, все 35 файлов OK.
- `evidence/matrix.json`: отдельные результаты 14 запусков, config/executable hashes.
- `SHA256SUMS`: замороженные файлы отчёта и доказательств.

Команда из отдельной копии: `python3 probes/run.py`. Пути исходных input-каталогов явно заданы в runner; их содержимое не модифицируется. Команды и hashes важнее одинаковых имён файлов. GitHub writes, upstream issues, изменения firmware/host, новые облачные задачи и аппаратные действия не выполнялись.

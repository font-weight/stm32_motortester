# BOOT004: верхняя оценка SOFTWARE-стека выбранной сборки `-Os`

Статус: **SUPPORTING_ANALYSIS_COMPLETE; INDEPENDENT_ACCEPTANCE_PENDING**. Это расчёт по неизменным машинным объектам и исходникам, не независимый вердикт Critic-A и не измерение на MCU.

## Результат

Для указанной ниже композиции получены консервативные верхние оценки MSP:

| Область | Оценка, байт |
|---|---:|
| `Reset_Handler` до входа в `main` | 0 |
| Инициализация `main` до разрешения обычных IRQ, включая постоянный frame `main` | 288 |
| Foreground после разрешения IRQ, включая постоянный frame `main` | 2640 |
| Один USB IRQ19 **или** IRQ20, только программные frames | 264 |
| Аппаратный вход в исключение с запасом на выравнивание | 32 + 4 = 36 |
| Foreground + один USB IRQ + его аппаратный вход | **2940** |
| То же + terminal HardFault и затем NMI | **3012** |
| Округление последней оценки вверх до 8 байт | **3016** |

3016 байт — математически округлённое покрытие найденной оценки при описанном профиле; это **не назначение production stack**, не дополнительный инженерный запас и не подтверждение готовности образа к прошивке. Резерв 4096 в ELF тестового размещения выбран ранее произвольно и не использован для получения этих чисел.

Это верхние оценки графа с безопасными завышениями, а не доказанный достижимый точный high-water. Все tail calls в межфункциональном расчёте сохраняют полный frame вызывающего; учитываются и несовместимые между собой пути ветвления; глубина IRQ добавлена даже к foreground-точке, в которой PRIMASK мог бы запрещать IRQ. Уточнение по маскам или выбору FIFO-режима для уменьшения числа не применялось.

## Закреплённые входы

Исходная сборка: `/workspace/shared/device-main-arm/run-r2`, frozen по сообщению её ARM-аудитора. В её `RESULTS.json` закреплены команды, зависимости, компилятор и объекты; `inputs_unchanged=true`.

- main C: `f42d80a8c5c999819252d71d9cda9ce8797db3c22cd59e9dfeac7f55f8bcdc9b`
- CPU C: `4640364e1fdd44cd224552547f164e3bf4838556c6623d866cfbb02b8ae98b95`
- application `tusb_config.h`: `341fd5a09fe5594c9e6dbee0c14427c485476e58bd8efb640091a717dd657c9e`
- main Os object: `0136f3fdc5f8e51fd36e26cc0366ff2fa51a54089453b82174d3914767cf033a`
- CPU Os object: `fca6eb470b9d515f80cfa5d050be4974f8890160358ba3e1c0adf6a126d80f4e`
- ELF: `0450e7ee104e19d6a1c018c2f6d1bdcb6f4b74544063044e14c41c01b586c2ae`
- GCC: `arm-none-eabi-gcc (15:14.2.rel1-1) 14.2.1 20241119`, Cortex-M3, Thumb, soft-float, `-Os`, без LTO.

ELF называется `TEST_ONLY_INVALID_BOARD_STATIC_PLACEMENT_NOT_FLASHABLE.elf`. All-zero `mt_board` заведомо не пройдёт actual `main`. Для анализа он используется только как связанная адресная карта всех сохранённых настоящих code objects: значение zero-board **не использовано** для отсечения инициализации/рабочих ветвей. Ни production board configuration, ни работоспособность устройства этим не доказаны. При изменении конечной композиции, флагов, callback bindings или добавлении кода анализ нужно повторить.

## Метод и полнота машинного графа

1. Узлы определены адресом и диапазоном входной `.text.*` section из linker map; дополнительно сохранён объект. Одинаковые локальные имена `rd`, `start`, `valid`, `init`, `read_reg`, `fail` разных объектов не объединяются.
2. Повторный `objdump -dr` закреплённым инструментом дал побайтно тот же текст disassembly. SHA каждого связанного объекта проверен против исходного `RESULTS.json`.
3. Выделены **363** code-section узла: **361** frame из `.su`, каждый с квалификатором `static`, плюс `Reset_Handler` и `Default_Handler` с доказанным assembler-frame 0. `dynamic`, `dynamic,bounded` и unbounded `.su` в выбранных объектах нет. Значения из O0/O1/O2/O3/Og не смешивались с Os.
4. Локальный машинный CFG независимо проверяет изменение SP: каждый достижимый instruction address имеет единственное значение SP; каждый возврат/tail-call восстанавливает исходный SP; цикл с накапливанием SP не найден. Максимальное уменьшение SP во **всех 363** функциях точно совпало с `.su`/assembler 0. Учтены push/stmdb, add/sub/subw, pop/ldmia и post-index load SP. Переключения стека отсутствуют.
5. Все **9** вычисляемых switch-переходов `tbb`, `tbh`, `ldr pc,[base,index]` разобраны по байтам ELF. Проверены ограничение индекса перед переходом и все элементы таблицы. Все цели являются началами инструкций внутри той же функции. Они не являются неизвестными callbacks.
6. Прямые межфункциональные переходы разрешены адресно; переходов к неизвестной цели или в середину чужой функции нет. Все **53** оставшихся `blx/bx register` перечислены и разобраны в `indirect-resolutions.json`.
7. После указанных ниже bindings граф ацикличен: рекурсии/циклического вызова нет во всех компонентах, включая неиспользуемые публичные API. Циклы опроса, main-loop и обработки данных не накапливают stack frames.
8. Рекуррентная формула: `U(f) = frame(f) + max(U(child))`, либо просто frame для листа. Обычный и tail-вызов намеренно одинаковы. Это сумма вдоль пути, **не сумма frames всех функций**.

### Закрытие косвенных целей

- `main` задаёт единственные MMIO read/write (`mt_clock_mmio_read32/write32`) и IRQ guards (`mt_irq_guard_enter/leave`). Clock/time/runtime/boot-USB копируют именно эти bindings.
- Runtime задаёт ADC wrappers `adc_read`, `adc_write`, `adc_time`, commit guards `commit_enter/allowed/leave`, `mt_timebase_now_ms` и CDC predicate `stable`.
- `mt_adc_control` задаёт control callbacks `profile`, `start`, `stop`; `mt_control_init` сохраняет их. Все соответствующие register-call offsets сопоставлены структурам и инициализаторам.
- TX writer в actual цепочке — **`mt_usb_cdc_write_impl`**, так как macro substitution действует при передаче callback из `mt_usb_cdc_step_impl`; подтверждено ABS32 relocation. Не выдуман дополнительный вызов внешней wrapper-функции.
- TinyUSB содержит только CDC; `CFG_TUD_TEST_MODE=0`, host disabled, OS_NONE, bridge enabled. Фактический weak `usbd_app_driver_get_cb` записывает count 0 и возвращает NULL; strong override отсутствует.
- Единственная таблица class driver содержит `cdcd_init/deinit/reset/open/control_xfer_cb/xfer_cb`. Таблица и шесть function-address relocations проверены. `sof` и `xfer_isr` равны NULL; register-call sites `0x08005a82` и `0x08005aec` невозможны при этой таблице.
- Поле EP0 complete_cb принимает NULL, `cdcd_control_xfer_cb` или фактический weak `tud_vendor_control_xfer_cb`. Test-mode callback не скомпилирован. Оба ненулевых адреса оставлены в оценке.
- Queue/spin callback `interrupt_set` — `usbd_int_set`, закреплённый статическими OSAL initializer/relocations.
- Deferred dispatch `0x080064f6` в `tud_task_ext` не объявлен произвольной безопасной целью. Его единственный producer `usbd_defer_func` **не имеет ни caller, ни address-taking relocation** в композиции и не достижим из reset/main/IRQ. Другие queue producers — DCD IRQ и обработчик SOF/resume — создают фиксированные теги аппаратных событий, а не `USBD_EVENT_FUNC_CALL`. Поэтому эта ветвь недостижима из actual roots. Это исключение не действует при добавлении пользователя deferred API, debugger-инъекции или повреждении памяти.

Итого: 50 sites имеют явные конечные множества целей; 2 sites исключены NULL-driver-slot, 1 — отсутствием достижимого producer. Неразрешённых целей в заявленном профиле нет. Полный адресный список и основания доступны в JSON, а все code-pointer relocations отдельно в `code-pointer-relocations.json`.

### C library и compiler helpers

Link выполнялся напрямую `arm-none-eabi-ld` по конечному перечню объектов, без libc/libgcc. До board/linker symbols closure содержал только семь ожидаемых external names; итоговый `nm -u` пуст. Поставщики `memcpy` (8), `memmove` (8), `memset` (0), `memcmp` (12) — включённый `mt_memory.o`, их frames и вызовы разобраны. Неучтённого `__aeabi_*`, allocator, CRT или скрытого библиотечного хвоста нет. Деления в коде не требуют неразрешённого helper.

## Максимальные пути консервативного графа

Foreground (включая постоянный frame main):

`main 88 → mt_runtime_step 16 → mt_usb_cdc_step 24 → mt_usb_cdc_step_impl 40 → mt_control_rx_feed 48 → mt_control_receive 640 → mt_capture_link_stop 1696 → mt_tx_stop 64 → append 16 → memcpy 8 = 2640`.

Ранее известные 2552 относятся к тому же пути начиная с runtime; только после закрытия остальных edges и проверки maxima их можно считать верхней оценкой поддерева. Сами по себе прежние 2552 были лишь subtotal.

IRQ19 и IRQ20 имеют одинаковую оценку:

`IRQ wrapper 0 → usb_irq_dispatch 8 → dcd_int_handler 72 → dcd_event_handler 48 → queue_event_epoch 48 → tu_fifo_write_n_access_mode 64 → tu_hwfifo_read 16 → memcpy 8 = 264`.

Ветка hardware-FIFO внутри generic FIFO оставлена, хотя actual queue передаёт normal access mode; это намеренное завышение. Hardware exception frame в 264 не входит.

TinyUSB foreground-поддерево `tud_task_ext`: 408. Startup до admission: `main 88 → mt_runtime_init 104 → mt_adc_control_init_guarded 16 → init 40 → mt_control_init 16 → mt_capture_link_init 16 → mt_acq_init 8 → memset 0 = 288`.

## Исключения и профиль исполнения

Reset использует загруженный аппаратурой MSP, не резервирует frame, устанавливает CCR.STKALIGN, и вызывает `main` через BL. `main` никогда не освобождает свой frame. Проверка CPU перед admission требует privileged Thread/MSP, PRIMASK=1, BASEPRI=FAULTMASK=0, IPSR=0. PSP/RTOS/других задач в расчёте нет.

В профиле после настоящего system reset проверка перед admission оставляет разрешёнными только NVIC IRQ19/20 и проверяет одинаковый реализованный приоритет обоих. Поэтому они не вытесняют друг друга, в том числе до первого CPSID внутри wrapper; wrapper дополнительно маскирует полную DCD-цепочку через PRIMASK с восстановлением прежнего значения. Повторные pending USB IRQ выполняются последовательно и не накапливают frames. Другие NVIC IRQ отключены; SysTick/PendSV/SVC не используются. Конфигурируемые fault handlers остаются отключёнными после reset, программного включения в композиции нет; внешний debugger не меняет это состояние.

Для Cortex-M3 берётся 8 аппаратно сохранённых слов = 32 байта и ещё 4 байта запаса для выравнивания. HardFault и NMI направлены на один assembler `Default_Handler`: CPSID и бесконечная ветвь, frame=0, возврата нет. HardFault может прервать обычный USB handler, NMI — HardFault; обратного вытеснения и рекурсивного входа того же приоритета нет. Поэтому отдельно добавлены не более двух terminal exception frames, ещё 72 байта. Reset начинает новый stack lifecycle. Это следует из [ST PM0056 Rev 7, §§2.3.5–2.3.7, 2.4.2–2.4.4](https://www.st.com/resource/en/programming_manual/pm0056-stm32f10xxx20xxx21xxxl1xxxx-cortexm3-programming-manual-stmicroelectronics.pdf).

Следовательно нормальная оценка `2640+36+264=2940`; с terminal исключениями `2940+72=3012`. До admission действует меньшая оценка `288+72=360`. Запас 4 на каждом terminal входе сохранён консервативно, даже когда текущий SP уже выровнен.

## Границы и следующий шаг

- Оценка предполагает корректное выполнение этой композиции, неповреждённые code/function pointers/SP/память, штатную адресуемую RAM/Flash и отсутствие внешних изменений debugger/другим кодом. Это не доказательство memory safety, отсутствия hardware faults, возможности успешной обработки stacking fault или восстановления после fault.
- Board-zero ELF не запускался; MCU, USB host, timing, ADC и реальные high-water/canary не измерялись. Валидного production board object этим анализом не создано.
- Любое добавление драйвера/IRQ/callback, вызов deferred API, custom fault handler, смена compile/link options, startup/guard, CPU или исходников требует пересчёта. Доказательство не распространяется на будущий произвольный firmware image.
- Остаточных неизвестных call edges или unbounded frames в обозначенном software-профиле не осталось. **Приёмка** требует отдельной проверки Critic-A ≥9.5 и привязки к окончательному образу. Выбор production reserve/линкерного параметра остаётся решением следующего этапа и здесь не изменён.

## Воспроизведение

В каталоге анализа выполнить `python3 -I -B replay.py`. Он сначала сверяет `INPUTS_SHA256.json`, затем запускает `analyze.py`, `inventory.py`, `verify_machine.py`, `bound.py`, повторно проверяет входы и сохраняет `REPLAY_RESULT.json`. Файлы исходной сборки и production не изменяются.

Основные артефакты: `bounds.json`/`bounds.txt`, `graph-resolved.json`, `indirect-resolutions.json`, `computed-jumps.json`, `machine-stack-check.json`, `code-pointer-relocations.json`, `object-relocations.txt`, `INPUTS_SHA256.json`, `ARTIFACTS_SHA256.json`.

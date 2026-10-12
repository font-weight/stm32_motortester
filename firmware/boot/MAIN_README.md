# BOOT004: связывание startup, TIME, runtime и native USB

Локальный draft Dot A; [область](https://github.com/font-weight/stm32_motortester/issues/2#issuecomment-6102401031).
Новый main не заменяет и не возобновляет старый A-005. Исходники не означают
готовую прошивку для платы: обязательное определение `const mt_board_inputs mt_board`
намеренно отсутствует. Без него итоговая линковка должна завершаться ошибкой.

## Обязательные входы

После настоящего Flash system reset BOOT001 уже настроил MSP, VTOR, STKALIGN,
.data/.bss и оставил PRIMASK=1. Никакой другой startup, bootloader, ISR, callback
или writer не владеет периферией/общим состоянием. Модель STM32F103C8T6,
64 KiB Flash/20 KiB SRAM и физическая конфигурация требуют проверки экземпляра.

Интегратор отдельно предоставляет неизменяемый mt_board:

- проверенные HSE frequency/mode, явный read-count budget clock (не миллисекунды);
- явный USB priority 0…15, firmware build и политика DTR 0/1;
- разрешённые VID/PID, корректная BCD revision, реальный bus-current budget;
- читаемые manufacturer/product и стабильный уникальный физический serial;
- подтверждённая fixed pull-up topology (`verified_fixed_pullup=1`).

Поле с названием verified не является измерением или проверкой прав. Значения
нельзя выдумать ради успешной сборки. Controlled pull-up здесь не реализован;
для него нужен конкретный board hook с GPIO/polarity и новым анализом init.
Нет default HSE, USB identity, питания или serial. Тестовые входы, если они
применены для проверки линковщика, маркируются отдельно и не становятся платой.

`tusb_config.h` фиксирует программный профиль: F103/F1, OS_NONE, DEVICE/FULL_SPEED,
один CDC, EP0/CDC packets64, buffers64, queue16 и один event на task run, bridge=1,
остальные классы/host/debug отключены. Он повторяет ранее проверенный профиль,
но теперь задаётся приложением; изменение любого из этих условий требует
повторного анализа callers, памяти и USB backend. Это не USB certification.

## Последовательность main

1. Проверить privileged Thread/MSP: PRIMASK=1, BASEPRI=0, FAULTMASK=0, IPSR=0,
   CONTROL[1:0]=0. Неподходящие маски не нормализуются скрыто.
2. Проверить входы/clock plan, затем настоящий descriptor init до MMIO startup.
3. Настоящий CLK apply; BOOT002 включает/сбрасывает TIM2 и запускает TIME.
4. Один TIME002 bind к этому же TIME; настоящий runtime init с действительными
   IRQ save/restore guards. Его собственные ADC/control/RX/CDC init не дублируются.
5. BOOT003 готовит USB reset/gate и HP/LP priority, не включая global IRQ.
6. Настоящий `tusb_init(0, DEVICE/FULL_SPEED)` должен вернуть true. Один
   `tud_inited()` не заменяет результат: частичный init может уже выставить его.
   После true проверяется также `tud_inited()` для согласованности состояния.
7. Проверить сохранённые успешные CLK/BOOT002/BOOT003 receipts, затем свежие
   стабильные clock/Flash, USB gate/reset, CAN/shared/wake IRQ,
   priority, EXTI18, USB_CNTR и BTABLE; обновить настоящий TIME и проверить
   TIME002 validity. CPU admission ещё раз проверяет специальные регистры,
   затем выполняет DSB/ISB, отдельный CPSIE i и ISB.
8. Единственный foreground loop вызывает только `mt_runtime_step`. В частности,
   TIME_FAULT/RECOVERY не прекращают шаги, необходимые для настоящих Stop retries.

USB19/20 к моменту финальной проверки должны быть enabled, но не active; pending
разрешён, поскольку host может уже прислать RESET. CAN21/22 и wake42 остаются
без enabled/pending/active. Pending USB/ISTR не очищаются. DADDR/EP0 не обязаны
сохранять init-значения после bus RESET и потому не используются как запрет.
Набор enabled внешних IRQ проверяется целиком: ISER0 ровно 19/20,
реализованные IRQ32…42 выключены. Другие IRQ/users не обслуживаются.
Выключенные посторонние pending не очищаются и сами по себе не отвергаются.
SysTick/PendSV/SVC не запускались и не ставились по reset-only контракту.

В данном профиле USB_CNTR & 0xff1f = 0xdd00; BTABLE & 0xfff8 = 0. Это стабильная
конфигурация, не проверка pending bus events или аналогового состояния. RM0008
Rev21 §23.5 разрешает aligned word/half-word доступ к USB peripheral registers;
здесь read32 маскирует резервные старшие биты. Правило не переносится на PMA.
Группы read-only обращений не являются атомарными снимками, внутри OR-группы
порядок C-evaluation не закреплён. NMI/HardFault не должны менять маски/состояние.

## Отказы и память

Runtime, TIME и boot receipts находятся в статической DMA-доступной SRAM и не
освобождаются при отказе. До допуска IRQ и host-команд main не запускает захват.
Фактический bool tusb_init сохраняется отдельно. Ранний отказ записывает причину
в debugger diagnostic и при исходном privileged BOOT001-контексте остаётся
в masked loop;
это не утверждение cleanup, Stop, physical disconnect или успешного повторного
reset. Даже после failed tusb_init не выполняются retry/deinit/rebind.

Проверка неверного CPU-входа не восстанавливает привилегии или валидный стек.
Если main уже вызван unprivileged, CPSID i не может гарантированно установить
PRIMASK; при неверном SP даже вход в обычную C-функцию не гарантирован.
Такие случаи вне исходного BOOT001-контракта. Native wrong-CPU fixture проверяет
ветвление, но не доказывает masked recovery или отсутствие ранее исполненного ISR.

После допуска IRQ loop сохраняет последний runtime result и продолжает работу
при всех значениях. Он не стирает DMA buffers, не перезапускает часы и не считает
`hardware_running=0` доказательством освобождения периферии. Runtime остаётся
единственным владельцем своих Stop/recovery действий. Нарушение lifetime,
corrupt pointers или произвольные внешние writers не покрыты этим контрактом.

TIME002 numeric hook не вызывается из main; weak delay не используется. Последнее
диагностическое число при ошибке не становится временем. Все действительные
потребители используют общий TIME последовательно в foreground. Реальные
интервалы обслуживания TIME/ADC, clock accuracy, IRQ duration и общий стек ещё
должны быть подтверждены. Завершение конечного числа C-операций не измеряет их.

## Сборка и доказательства

Один main и один CPU backend, BOOT001 reset/linker, все настоящие компоненты и
layered USB005 backend, общий `-fno-short-enums`, `-Wa,--noexecstack`; no-GC flow
с обязательным аудитом исходных objects/relocations и итогового ELF. Нужны
явный stack reservation и реальные board inputs, а не заглушки отсутствующих
symbols. В выбранном профиле нельзя линковать альтернативный main/guard/descriptor.

Native orchestration с заменёнными аппаратными/компонентными endpoints, если
выполняется, проверяет только последовательность и маршруты отказа. Он не
является исполнением main на MCU или реальным USB. ARM compile/disassembly
подтверждают инструкции и интерфейсы, не физическое поведение. Комплект остаётся
draft до независимого Critic-A и обязательных публикационных/интеграционных
доказательств. Подключение, прошивка, enumeration Windows и HV здесь не выполняются.

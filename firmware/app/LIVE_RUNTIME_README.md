# Черновой stream-only runtime: настоящий USB/ADC/TIME код

mt_live_runtime — отдельный foreground-владелец для будущего потокового профиля.
Он использует настоящие TinyUSB CDC/FIFO/core и USB epoch bridge, mt_live_device,
mt_live_adc, mt_adc_dma, TIME и u64 clock wrapper. В native-тестах аппаратная шина,
DCD/IRQ guard и выдача CTR grant моделируются отдельно; это не измерение платы.
Legacy runtime, оконный acquisition/control и его TX-очередь рядом не создаются.
Startup, новая полная ARM-сборка и пользовательский комплект ещё не включены.

## Инициализация и владение

Объект размещается статически в DMA-доступной SRAM и изначально обнулён.
TIME, MMIO и guard context не перекрываются с ним и переживают все обращения.
Нельзя переинициализировать работающий объект, сбрасывать TIME или запускать
другого application writer/CDC callback, который добавляет старые TX-данные.

Init выполняется до tusb_init, после fixed-clock startup, при маскированных IRQ.
Он требует заранее зарезервированные ADC1/TIM3/DMA1 channel1, включает их gates
с сохранением остальных битов и затем проверяет off/reset. Активная чужая
периферия не выключается и не присваивается: init возвращает ADC_UNSAFE.
Включение gates не запускает преобразования. Дескрипторы и NVIC настраивает
отдельный boot-владелец, который в этом модуле не реализован.

Нужны guarded USB bridge и opt-in CTR fairness. На текущем этапе нет изменения
старого boot или default USB profile. Qualified TX transaction bound передаётся
явным параметром: runtime не выводит его из числа строк, x86-времени или среднего
времени callback. Без отдельного подтверждения это лишь заданный параметр
native-модели, а не разрешение объявить target deadline квалифицированным.

## Один foreground-шаг

- Свежая проверка u64/TIME, синхронизация hard/soft USB потерь и настоящая остановка
- Не более одной возможности обработки task-события TinyUSB
- Повторная проверка TIME/lifecycle, ready/DTR и сохранённого app actor
- Не более одного настоящего ADC BLOCK с немедленным queue offer
- Чтение не более 64 RX-байт; перед каждым байтом проверяется текущий epoch permit
- Один mt_live_device_step под save/restore IRQ guard до финального state commit
- Ограниченный explicit flush под app actor, затем новая TIME/lifecycle проверка
- CTR grant только под guard при свежем согласованном epoch, завершённом cleanup,
  отсутствии fault/pending-soft/exhaustion и пустой очереди task-событий

Один вызов task не означает возможность безусловно выдавать новый CTR-квант:
одна endpoint-итерация IRQ может создать два completion плюс lifecycle события.
Новый grant ждёт пустую очередь; foreground возвращается к ADC между попытками.
Это алгоритмическое правило, не доказательство фактической частоты обслуживания.

## Передача и смена эпохи

Write синхронно передаёт TinyUSB не более 64 байт и возвращает точное число
скопированных в FIFO байт. Оно не превращается в ноль, если после копии произошла
потеря эпохи. Ни этот count, ни flush не доказывают получение HOST или запись диска.
Isolation подтверждает только единственного синхронного application writer;
уже принятые FIFO-байты не очищаются при обычном HELLO и сохраняют порядок.

Hard loss закрывает admission, вызывает live transport_lost/настоящий quiesce,
сбрасывает carry и очищает TX FIFO как quarantine. Очистка FIFO сама не отменяет
PMA/старый completion: физический reset и существующий epoch bridge остаются
обязательными. Cleaned receipt выдаётся только после подтверждённого off и
освобождения owned ADC context. При неудаче остановки persistent storage остаётся
живым и попытки продолжаются. На плохом TIME никакой receipt/reinit не выдаётся.

Suspend/resume serial учитывается даже при быстром возврате ready. Потеря DTR
при require_dtr тоже останавливает поток и требует новый HELLO. При soft pause
старый TX FIFO не очищается; RX flush/rearm откладывается до устойчивого reopen.
Read/flush выполняются под исходным app actor; actor=0 не подставляется в DCD.
Начало ADC сохраняет исходную command epoch через ранний и timed CEN guards.

## Граница критической секции и оставшиеся доказательства

Guard вокруг mt_live_device_step защищает не только FIFO copy. Этот вызов может
сделать completion/CRC/HELLO queue reset, а при clock/deadline/counter ошибке —
quiesce с ограниченным polling ADC Stop. Поэтому нельзя заявлять короткий IRQ-off
интервал по размеру write alone. Полный masked call graph, stop poll cap, nested
callbacks, USB latency и final stores должны войти в target WCET/stack proof.
TIME должен идти независимо от IRQ; замерший timer не считается здоровым.

Ограничение CTR не ограничивает бесконечные lifecycle storms. Физическая модель
допустимого USB трафика/fault, queue high-water, максимальный интервал ADC service
(<2560 мкс), достаточная частота TX, real NVIC/PMA/clock и весь ARM link/map/stack
ещё не квалифицированы. Первые полные object-компиляции нового C модуля на
Cortex-M3/Thumb прошли со строгими предупреждениями; это не полный образ.

ADC binding пока оставляет produced_end неизвестным; END/ACK могут завершить
software-протокол с incomplete/unknown flags. Не превращать это в clean lossless
acquisition. Уточнение completed-DMA domain согласуется отдельно с HOST и должно
быть надёжно сохранено/показано до включения соответствующего known-end mapping.
Новый бинарник для прошивки из этой работы пока не выдаётся.

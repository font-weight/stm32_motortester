# Native-проверка STREAM runtime owner

Проверяется композиция `mt_live_runtime` с настоящими модулями TIME, ADC/DMA,
`mt_live_adc`, `mt_live_clock`, live-протоколом, очередью отсчётов, TinyUSB core,
CDC/FIFO и epoch/bridge. Это программная проверка на синтетических регистрах и
отсчётах. Она не доказывает работу платы, native USB на STM32, WCET,
пропускную способность, физическую частоту АЦП или пригодность для зажигания.

Проверенная версия: `cc2c938b122cc580d959740e16d11a2ac046b265`.
177 исходных файлов зафиксированы SHA-256 в `source-manifest.json`.
Пакет не содержит скопированного production-кода, прошивки или журналов сборки.
Исходный checkout передаётся отдельно и не меняется. Boot и legacy runtime
не включаются; полного образа и запуска на устройстве здесь нет.

## Запуск

Нужны Linux, Python 3.11+, GCC с ASan/UBSan, `nm` и `patch`.
Проверено: GCC 14.2.0, Python 3.12.14.

```sh
SOURCE="$(realpath /path/to/pinned/source)"
PACKAGE="$(realpath /path/to/this/package)"
OUTPUT="$(mktemp -d)/stream-runtime"
python3 -I -B "$PACKAGE/run.py" --source "$SOURCE" --output "$OUTPUT"
```

`OUTPUT` должен быть новым абсолютным путём вне исходников и пакета, с уже
существующим родительским каталогом. Пути с пробелами поддерживаются.
Runner сначала проверяет все входные хеши, затем создаёт отдельный staged
снимок и компилирует только его. Все временные файлы, объекты, исполняемые
файлы, stdout/stderr и `receipt.json` остаются в `OUTPUT`.

Обычный запуск выполняет все 47 именованных случаев в strict и ASan+UBSan
режимах. Один случай дополнительно перебирает 256 длин завершения: 0..255
реальных для модели DMA-переносов. Строгая компиляция собственных модулей:
C11, `-pedantic-errors -Werror -Wconversion -Wsign-conversion -Wshadow`.
TinyUSB собирается как GNU C11. Sanitizer-режим использует
`-fsanitize=address,undefined -fno-sanitize-recover=all`, остановку при первом
нарушении и non-PIE для воспроизводимого запуска ASan. Leak detection отключён.

Для диагностики доступны `--case NAME` (можно несколько) и `--strict-only`.
Это частичные запуски; они не заменяют полный результат. Список случаев:
`OUTPUT/strict/test-runtime --list` после сборки.

```sh
CONTROLS="$(mktemp -d)/runner-controls"
python3 -I -B "$PACKAGE/check_runner.py" --source "$SOURCE" --output "$CONTROLS"
```

Восемь проверок runner: отклонение `-O`, Python без isolated mode, существующего
output, output внутри source, source через symlink и изменённого production
файла; успешный перенос пакета/исходников в пути с пробелами; обязательное
падение sanitizer-запуска на намеренном signed overflow в отдельной копии
фикстуры. Последний тест должен падать внутри вложенного runner, а сама
проверка считается успешной только при подтверждённом fail-fast.

## Что проверено

- Настоящие wire-encoded HELLO/CAPS/START/STOP проходят через CDC OUT, FIFO,
  production parser и live core. Обратные HELLO_ACK/CAPS/START_ACK/DATA/END/
  STOP_ACK читаются из fake-DCD IN и декодируются с проверкой полей.
- Последовательность реальных для модели DMA-переносов, source indices,
  каждый 12-битный код, независимый CRC, полные и неполные хвосты.
  `produced_end` остаётся неизвестным; END имеет соответствующие flags=3.
- Reset до/после CDC read, ADC read, записи FIFO и explicit flush; reset во
  время calibration/CEN; pending TX и уже переданный неполный DATA frame;
  новое HELLO отделяет старый неполный кадр delimiter-ом.
- Сохранение первоначального actor во вложенном контексте. App scope для
  CDC read/flush. Hard reset отменяет отложенный soft RX flush; dead epoch
  не получает read_flush от runtime.
- Exact accepted count: нулевой, полный и положительный неполный при реально
  заполненном FIFO. Ни один возврат production CDC write не подменяется.
- IRQ guard охватывает весь live TX step, post-write TIME observations и
  финальный commit. Отложенный reset доставляется после снятия outer guard,
  когда exact accepted count уже опубликован. При TIME fault после записи
  реальный quiesce исполняется под тем же guard.
- Suspend/resume, включая обе ingress-сигнализации между foreground steps;
  DTR loss/reopen; retry при неостановившемся DMA; сохранение старых FIFO-байтов
  и их порядка перед новым HELLO.
- Cleanup receipt выдаётся только после проверенного off timer/ADC/DMA и
  здорового свежего TIME. TIME fault внутри hard/soft/DTR cleanup немедленно
  закрывает путь; hard RESET не dispatch-ится; последующие steps повторяют
  остановку и не публикуют receipt.
- Нормальная USB enumeration, включая descriptors и SET_ADDRESS, до app
  admission. Активные/reset ADC-периферии не захватываются при init.
- Reentry того же owner отклоняется; writer/isolation callbacks вне owner
  step отклоняются. Reservation единственного board owner остаётся контрактом
  вызывающего кода; тест не доказывает произвольную многопоточную безопасность.
- На step: не больше одного USB task opportunity, 64 RX bytes, одного live TX
  step и одного ADC service/read. События USB не вытесняют ADC service.
- CTR grant только при пустой task queue, свежем совпадающем lifecycle,
  отсутствии soft pause/fault/exhaustion и после TIME service. Для активного
  ADC обязательно предшествует ADC service. PMAOVR между последним sync и
  grant lock не даёт grant. Healthy idle/off может grant после TIME service.
- В native executable отсутствуют legacy `mt_runtime`, `mt_control`, `mt_tx`
  и `mt_capture_link` initialization symbols. Отсутствие legacy allocation в
  полном firmware-образе здесь не проверяется.

## Границы модели

Fake только MMIO, DCD, nested IRQ save/restore и CTR-rearm seam. Runtime,
parser, controller, ADC read/stop snapshots и clocks не заменяются готовыми
ответами. Link wrappers наблюдают входы/выходы и вызывают оригинальную функцию.
Аппаратные события задаются изменением регистров или настоящим TinyUSB event
ingress. Отсчёты синтетические, calibration завершает MMIO-модель.

Два класса reset injection разделены:

1. Deferred IRQ: соблюдает модель PRIMASK; это проверка программного guard.
2. Synchronous adversarial ingress: некоторые before/after wrapper probes
   вызывают ingress даже под PRIMASK. Это усиленная проверка устаревшего epoch,
   а не утверждение, что физический USB IRQ способен прервать masked участок.

DCD сборочная цепочка применяет setup/reset/bridge/opt-in CTR patches по их
закреплённым хешам. Fake DCD наследует инструментированную адресную операцию,
но реальный FSDEV ISR и `mt_usb_fsdev_ctr_rearm` на STM32 не исполняются.
CTR-проверки этого пакета относятся к owner-side порядку вызова.

`transaction_bound_ms=1` — специально заданный синтетический qualification
input. Он не измерен и не доказан; короткое количество операций не является
верхней границей времени. Модель не квалифицирует полный IRQ-off TX/cleanup
участок, DMA coherence, физический drain, интервалы обслуживания или USB.

Тесты не создают public posts, новые чаты, коммиты, PR и не обращаются к плате.

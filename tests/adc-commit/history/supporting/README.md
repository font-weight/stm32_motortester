# ADC-003: поддерживающие native-проверки допуска запуска

Проверяемая задача: [issue #62](https://github.com/font-weight/stm32_motortester/issues/62).
Автор production-кода — Dot A. Этот набор является поддерживающим тестированием,
а не независимым вердиктом Critic-A и не аппаратной приёмкой.

## Результат

Кандидат прошёл строгую C11-сборку и ASan/UBSan:

- неизменённый `tests/platform/test_adc_timer_dma.c`;
- неизменённый `tests/adc-control/test_adc_control.c`, включая реальный драйвер,
  контроллер, захват 2048 кодов, CRC, переполнение часов и прежнюю проверку
  корреляции времени;
- 65 новых сценариев `test_admission.c` с реальными `adc_timer_dma.c` и
  `mt_adc_control.c`, а также настоящими control/session/command/capture/TX/codec.

Оба старых набора отдельно прошли на исходной версии PR61 в строгом и
санитизированном режимах. Их байты не исправлялись для нового API.
Три испорченные локальные копии кандидата отклонены новым набором; старые
регрессии на этих копиях сначала прошли. Это отрицательные контрольные опыты,
а не обнаруженные дефекты production-кандидата. Все их логи и `FAIL` receipts сохранены.

Исходная версия зависимостей: PR61,
`7a721f82b98676d6fd9ab1b58a1d9b7d617b47a5`.
`REMOTE_BASE.json` содержит полученные read-only из GitHub tree идентификаторы
blob. Runner сверяет неизменённые зависимости и прежние тесты по Git blob SHA-1,
а все фактически использованные исходники — по SHA-256 до и после запуска.
`legacy/REMOTE_INPUTS.json` дополнительно фиксирует происхождение старых тестов.
`legacy/tests/adc-control/reproduce_evidence.py` сохранён только как справочный
вход из PR61; исторические override/reproducer-наборы этим новым runner не запускались.

## Что проверяют новые сценарии

Число сценариев — описание матрицы, не численная оценка качества.

- 14 отказов допуска: actor устарел до вызова, во время RSTCAL, чтения CAL,
  последней подготовительной записи, непосредственно перед захватом guard,
  при финальном `allowed`; fresh `now_us` перед commit установил sticky
  нездоровое состояние часов. Для каждой позиции предыдущая маска равна 0 или 1.
  Не допускается ни одна из трёх разрешающих DMA/ADC/TIM3 записей; Stop проверяет
  readback, а исходный actor не заменяется новым токеном.
- 6 отложенных IRQ: событие возникает на DMA.EN, ADC.EXTTRIG или TIM3.CEN.
  Оно не исполняется внутри guard. При прежней маске 0 исполняется из `leave`,
  при прежней 1 остаётся pending до снятия внешней маски моделью. Три разрешающих
  записи выполняются в одном guard. Возврат успешного commit может сопровождаться
  уже устаревшим actor после `leave`; тестовый вызывающий код замечает это и
  делает Stop до вывода. Реальный USB-owner здесь не реализован.
- 14 отказов commit: sticky clock-health после второго свежего чтения,
  потеря PLL ready между разрешением ADC и CEN, потерянная запись CEN/DMA/ADC,
  слишком широкий временной интервал и обратный ход часов. Маска восстановлена,
  cleanup выполняется после освобождения guard, периферия не объявляется
  остановленной без успешного readback.
- 4 отказа Stop: после denied admission и после неудачного commit, для обеих
  предыдущих масок. DMA.EN искусственно остаётся установленным, ожидание
  выполняется при глубине guard 0; ошибка сохраняет владение и блокирует restart.
  Повторный Stop также проверен: он не освобождает память при продолжающемся
  отказе и освобождает её после устранения модели отказа.
- 8 ошибок подготовки: замёрзшее время, зависший RSTCAL, зависший CAL,
  отсутствующий PLL ready. Guard ещё не захвачен, CEN не записан.
- 18 сценариев настоящей controller binding: HELLO → CONFIG → ARM, успешный
  запуск, отказ допуска/clock/readback, повторная остановка при recovery,
  удержание владения при Stop failure и сохранённый негардированный API.
  Перед ARM локальная структура guard затирается: binding должна пользоваться
  своей копией callbacks/context.
- 1 группа проверки аргументов: NULL и неполные guard отклоняются до MMIO.

На каждом новом сценарии подготовки RSTCAL/CAL проверяется глубина guard 0.
Любая запись TIM3.CR1=0 или ADC.CR2=0 также требует глубину 0, поэтому потенциально
ожидающая остановка не может начаться внутри guard. Каждая проверка `allowed`
следует за соответствующим свежим `now_us`.

## Воспроизведение

Требуются Python 3.9+ и обычный native C compiler. Ни ARM SDK, ни подключённая
плата, ни GitHub API во время запуска не нужны. Использовался Debian GCC 14.2.0.
`--out` всегда должен быть новым путём вне всех исходных деревьев. Проверка
пересечений выполняется до `mkdir`, включая разрешённые symlink-компоненты.
Повторное использование существующей output-папки запрещено. Лог, команды,
exit codes, hashes, snapshot и receipt сохраняются также при провале теста.

Команды финального запуска в данном исполнителе:

```sh
python /workspace/shared/adc-commit-tests/run.py \
  --base /workspace/shared/usb-bridge-publication/base-checkout \
  --candidate /workspace/shared/adc-commit-draft \
  --out /workspace/shared/adc-commit-test-runs/r2-strict

python /workspace/shared/adc-commit-tests/run.py \
  --base /workspace/shared/usb-bridge-publication/base-checkout \
  --candidate /workspace/shared/adc-commit-draft \
  --out /workspace/shared/adc-commit-test-runs/r2-sanitize --sanitize

python /workspace/shared/adc-commit-tests/run.py \
  --base /workspace/shared/usb-bridge-publication/base-checkout \
  --candidate /workspace/shared/usb-bridge-publication/base-checkout \
  --out /workspace/shared/adc-commit-test-runs/base-legacy-strict --legacy-only

python /workspace/shared/adc-commit-tests/run.py \
  --base /workspace/shared/usb-bridge-publication/base-checkout \
  --candidate /workspace/shared/usb-bridge-publication/base-checkout \
  --out /workspace/shared/adc-commit-test-runs/base-legacy-sanitize --legacy-only --sanitize

python /workspace/shared/adc-commit-tests/negative_controls.py \
  --base /workspace/shared/usb-bridge-publication/base-checkout \
  --candidate /workspace/shared/adc-commit-draft \
  --out /workspace/shared/adc-commit-test-runs/negative-controls-r1
```

При повторении заменить каждый `--out` новым путём: приведённые папки уже
содержат сохранённые свидетельства и намеренно будут отклонены runner.
Полные compiler argv записаны в `receipt.json` каждой папки. Общие flags:
`-std=c11 -pedantic-errors -Wall -Wextra -Werror -Wconversion -Wshadow -g
-fno-omit-frame-pointer`; strict использует `-O2`, sanitizer — `-O1
-fsanitize=address,undefined -fno-sanitize-recover=all -fno-pie -no-pie`.
Sanitizer environment: `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1`,
`UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`. Проверка утечек не заявляется.

Проверка защиты outputs сохранена в
`/workspace/shared/adc-commit-test-runs/output-refusal.json`: четыре попытки
отклонены с exit 2 — output внутри base, candidate, harness и уже существующая
папка. Новые родительские папки не созданы; hashes источников не изменились.

Отрицательные контроли используют только отдельные копии файлов под output:

1. Пропущен первый `allowed`: падение до первой разрешающей записи.
2. Пропущен второй `allowed`: падение на проверке обязательного повторного допуска.
3. Cleanup начинается без `leave`: падение при первой записи остановки внутри guard.

## Идентичность проверенного кандидата

SHA-256 production-файлов, совпадающий с root `PROTOTYPE_SHA256.json`:

```text
b771d00bf6fac333fac326b82f1e120a1ef4e582e8583e0974956e969f127620  firmware/app/mt_adc_control.c
a69143a2b432ac7403fe2125066bbd4e999e768d0d109c469a1739ed8b4c0200  firmware/app/mt_adc_control.h
fc0edee4a0948047c76513f598580c419353752374f91c45d79bce2b2e724ba4  firmware/platform/adc_timer_dma.c
930bb13278dfaf422fc78d453a9796977496f3e0e6393d7e8b3cf17268b0da3d  firmware/platform/adc_timer_dma.h
```

Production, исходная база, `host/**` и GitHub не изменялись этим тестовым заданием.
Воспроизводимый пакет поддерживающих файлов и hashes перечислен в
`SHA256SUMS.harness`; агрегированные результаты — `RESULTS.json`.

## Границы доказательства

Все IRQ, маски, MMIO и clock-health — детерминированные программные модели.
Это не эмуляция Cortex-M3/NVIC и не реализация/доказательство PRIMASK. Исходная
маска 1 после `leave` остаётся 1: освобождение данного guard не означает,
что глобальные прерывания разрешены внешним владельцем.

Не проверены физическая частота/первая задержка ADC, длительность critical
section, реальные USB reset/queue fault гонки, IRQ priorities, final owner
wiring, полный ELF/startup/linker map и безопасное электрическое подключение.
Ни низковольтный опыт, ни измерение зажигания не выполнялись. ARM object evidence
ведёт root отдельно; этот пакет не подменяет его native-сборками.

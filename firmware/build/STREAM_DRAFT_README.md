# Отдельная диагностическая ARM-сборка STREAM

`build_stream_draft.py` компилирует новый stream-only boot и создаёт только ELF.
Это не сборка принятого релиза и не комплект для прошивки. Старые inputs.json,
stack_reference.json, board ABI, build_objects.py и build_image.py не меняются.
Исторический inputs.json используется только как источник согласованных flags
для оставленных модулей; прежние object/image hashes не присваиваются новому ELF.

## Явные входы

- Чистый Git checkout и его полный HEAD SHA в `--source-commit`
- Закреплённый GCC14.2.1/binutils2.44 в `--toolchain`; проверяются 171 файла
- Board JSON по прежнему board_config.py; synthetic fixture не означает реальную плату
- Timing JSON по stream_timing.py с тем же source_commit
- `--stack-reserve`: явный положительный кратный 8 размер резервируемой памяти
- Новый абсолютный внешний `--out`; символические ссылки и перезапись запрещены

Запускать Python в isolated mode без bytecode и без `-O`. Пример команды с
заполненными вызывающим путями/значениями:

```sh
python3 -I -B firmware/build/build_stream_draft.py \
  --source-commit "$SOURCE_SHA" --toolchain "$TOOLCHAIN" \
  --board "$BOARD_JSON" --timing "$TIMING_JSON" \
  --stack-reserve "$STACK_RESERVE_BYTES" --out "$NEW_OUTPUT"
```

В timing JSON допустим только diagnostic_provisional, явное целое 1..499 мс,
profile=stream-only-v1 и schema_version=1. Рецепт не выбирает bound автоматически.
Stack reserve также не является верхней границей реального использования стека.
Ни факт линковки, ни отсутствие overlap, ни успех native-тестов не подтверждают
WCET, достаточность reserve или выполнение deadline.

## Что реально проверяется

Рецепт архивирует точный Git commit и компилирует отдельную копию. Все staged
исходники и compiler dependencies, инструменты, board/timing JSON и generated
patches/data/objects/ELF/map проверяются по SHA-256 до/после. Архив не допускает
symlink и специальные файлы. Компиляция содержит opt-in CTR fairness, 26 C units,
reset assembly и два data objects: всего 29 явных input objects.

Сохраняются отдельные compilation flags для TinyUSB и memory routines. Новые
модули используют строгий Cortex-M3/Thumb/soft-float freestanding профиль.
Реальная patch chain: setup/reset/USB bridge/CTR. ELF линкуется без GC и fallback
библиотек, с ошибкой на orphan sections. Generic BOOT проверки покрывают input
metadata, layout, архитектуру, vectors и Reset. Board object отдельно проверяется
прежним неизменённым data-only validator. Проверяется отсутствие undefined symbols.

Весь stdout/stderr, argv/exit статусы, .d/.su, disassembly, objects и receipt.json
остаются в output. Окружение не полностью hermetic: системные shared libraries,
Python, Git и patch не являются частью ARM toolchain; executable hashes записаны.
Проверки рассчитаны на стабильную cooperative filesystem, а не на враждебную
параллельную подмену файлов.

## Опциональная диагностическая телеметрия

Явный --diagnostics добавляет foreground DWT telemetry: 27 C units и 30 link
inputs, instrumentation_profile=foreground-dwt-v1. Имя результата:
DIAGNOSTIC_INSTRUMENTED_NEVER_FLASH.elf. По умолчанию опция выключена.
См. ../platform/STREAM_DIAG_README.md. Старый exact-image stack proof не
переносится на инструментированный ELF. Все qualification flags остаются false.

## Чего результат не утверждает

Успешный статус: DIAGNOSTIC_LINK_ONLY_NOT_QUALIFIED. Всегда false:
transaction_bound_qualified, stack_bound_qualified, runtime_profile_accepted,
hardware_verified, flash_approved. Общая приёмка остаётся BLOCKED_EVIDENCE.

Отдельно нужны новый full-image callback/ABI/stack proof, target IRQ-off и TX WCET,
aggregate USB traffic/fault/service model, ADC service <2560 мкс, совместимость
HOST и физические испытания. Число операций или partial-frame tests не заменяет
эти доказательства. Новый профиль не наследует 3064/3120-byte исторические bounds.

BIN/HEX не экспортируются; ELF с именем DIAGNOSTIC_UNQUALIFIED_NEVER_FLASH.elf
нельзя выдавать за разрешённый владельцу файл прошивки. Текущая ADC-привязка
по-прежнему сообщает unknown produced_end; known-end completed-DMA mapping
этой сборкой не включается.

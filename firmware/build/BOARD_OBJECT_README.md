# Проверка data-only board object

Локальный черновик. Независимое ревью узкой реализации выполнено;
формальная приёмка проекта остаётся BLOCKED_EVIDENCE, score:null.

`board_object.py` проверяет, что объект содержит только 48 байт `mt_board`
и три readonly ASCII-строки, соответствующие явно предоставленному JSON.
Вызов не запускает компилятор, linker, target-код или внешнюю программу.

```sh
python3 -I -B firmware/build/board_object.py \
  --object /absolute/path/board.o --input /absolute/path/board.json
```

Пути абсолютные, нормализованные, без symlink-компонентов; пробелы допустимы.
Как у генератора, символы LF, CR, `: # $ \\ " '` в путях не поддерживаются.
JSON обязан соответствовать неизменённому `board_config.py`, SHA которого
проверяется перед импортом и после него. Все значения обязательны; значения
реальной платы здесь не назначаются. Размер JSON ограничен 8192 байт,
объекта — 16384 байт. Исходные bytes проверяются повторно после анализа.
Это контракт стабильной кооперативной файловой системы, не TOCTOU sandbox.

## Узкий поддерживаемый профиль

Проверяется ELF32 little-endian ARM EABI5 ET_REL без program headers,
с точной структурой GCC 14.2.1 `-Os -ffreestanding -fno-common
-fno-short-enums -ffunction-sections -fdata-sections -Wa,--noexecstack`
для Cortex-M3 Thumb soft-float. Изменение compiler metadata или порядка
секций/символов намеренно требует отдельного анализа и версии checker.

Проверяются все 15 section headers: names/order, type, flags, link/info,
alignment, entry size, отсутствие перекрытия payload и скрытых ненулевых
file bytes. Разрешены только пустые `.text/.data/.bss/.note.GNU-stack`,
четыре readonly data sections и точный набор metadata. ARM attributes
и compiler comment сравниваются побайтно с текущим профилем.

Все 17 symbol records проверяются с bindings, visibility, типами, границами
и индексами секций. Имя FILE может различаться; оно не является доказательством
происхождения исходного файла. Единственный global symbol — readonly
`mt_board`; нет функций, COMMON, weak aliases и external undefined symbols.

Ровно три R_ARM_ABS32 relocation с нулевыми addends связывают offsets 36/40/44
с локальными SECTION symbols manufacturer/product/serial. Остальные 36 байт
сравниваются с little-endian ABI: семь 32-битных полей и четыре 16-битных.
Строки сравниваются целиком, включая ровно один завершающий NUL.

## Что означает результат

На stdout выводится JSON; exit 0 — `BOARD_OBJECT_STRUCTURE_PASS`, exit 1 —
`REJECTED`. Ошибки синтаксиса CLI обрабатывает argparse (exit 2). Checker
ничего не записывает; вызывающий рецепт должен сохранять argv, stdout,
stderr, exit code и input/tool hashes, в том числе для отказов.

Успех доказывает только рассмотренную структуру объекта и соответствие
JSON. Он не доказывает, что объект действительно скомпилирован из указанного
C, что компилятор был запущен с нужными flags, что данный объект использован
в final ELF или что плата соответствует caller assertions. Полный рецепт
должен отдельно подтвердить provenance, повторить исходные BOOT audits и
проверить final-image/stack binding. Резерв стека здесь не выбирается.
`hardware_verified` и `flash_approved` всегда false.

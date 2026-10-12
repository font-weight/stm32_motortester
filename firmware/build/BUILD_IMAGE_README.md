# Черновой static-link recipe

Исходные ADC reference и полный image-рецепт PR102 проверены отдельно
[для точного SHA 555ced8](https://github.com/font-weight/stm32_motortester/pull/102#issuecomment-6118044354).
Эта проверка не переносится автоматически на новую интеграционную
композицию: её точный состав и воспроизведение проверяются отдельно.
Приёмка MVP, общей ADC-политики и аппаратуры остаётся открытой.
После всех проверок результат — `CONDITIONAL_STATIC_LINK_CHECKS_PASS`:
условная оценка 3064 B связывается с конкретным ELF. Это локальный draft
с отдельно проверенным исходным ADC proof; проверка новой композиции
остаётся отдельной. BIN/HEX создаются только после всех gate; разрешение
прошивки не выдаётся. Старый provisional run без map binding сохранён.

```sh
python3 -I -B firmware/build/build_image.py \
  --objects /absolute/object-build \
  --board /absolute/board.json \
  --toolchain /absolute/toolchain/usr \
  --stack-bytes N --stack-margin-bytes M \
  --out /absolute/existing-parent/fresh-output
```

Все параметры обязательны. `--objects` содержит ровно необходимые 33
именованных объекта прежнего рецепта; лишние файлы каталога не включаются.
Содержимое каждого нужного объекта проверяется по frozen reference и
подаётся linker в точном порядке. `combined.o`, библиотеки, CRT и старый
board fixture не подставляются. Receipt прежнего build не является
источником доверия вместо фактических object bytes.

N кратно 8, 3064 ≤ N ≤ 8104. M задан явно, 0 ≤ M ≤ 5040;
N = align8(3064 + M). Отдельно сохраняется alignment slack. M=0 в тесте
не является выбранным инженерным запасом для платы. Реальные N/M должен
обосновать интегратор до аппаратного допуска; recipe их не назначает.

Проверяются исходные source/tool manifests, 171 файл существующего ARM
наборa, hashes импортируемых helper modules, actual GCC support-tool paths
и все исходные object hashes. Ничего не скачивается и не устанавливается.
Host shared libraries не зафиксированы полностью: это не hermetic build.
PATH ограничен системными каталогами; inherited compiler environment
не переносится. Используется собственный TMPDIR в fresh output.

Из строгого JSON генерируется только immutable board C. Actual GCC создаёт
board.o с явным Cortex-M3/Thumb/full-enum ABI; зависимости из `.d` обязаны
принадлежать pinned исходникам/toolchain или созданному C. Затем выполняются:

1. Структурная проверка data-only board.o и соответствия JSON.
2. Исходный BOOT input/vector audit всех 34 объектов сразу.
3. Прямая static link: no GC, fatal warnings, orphan errors, исходный linker
   script и явный stack symbol.
4. Исходный BOOT final ELF audit и узкий byte/layout equivalence checker.
5. Связка всех 369 непустых text input ranges с фактическими секциями
   исходных объектов, точными path/ordinal, VMA/size frozen reference,
   file bytes и PT_LOAD конечного ELF; точный LOAD order, без пропусков
   пустых executable inputs и без discarded code.
6. Пин существующего objcopy, экспорт только `.isr_vector/.text/.data`,
   сравнение BIN и независимо декодированного Intel HEX с Flash LMA bytes ELF.
7. Проверка сохранности исходных и созданных файлов до завершения.

Map parser сохраняет полный suffix пути без split/shlex: пробелы
в путях не меняют identity. Одинаковое имя секции в разных объектах
различается ordinal/path. Все input rows имеют известный источник и
согласованный размер; board rows и output text geometry сверяются отдельно.
GNU-generated пустые `.iplt/.rel.iplt/.igot.plt` и четыре linker stubs
разрешены только с точными identity/order/VMA/нулевым размером; они
не добавляют runtime bytes. Raw object bytes не сравниваются с relocated
кодом целиком: immutable final bytes уже проверены отдельным ELF gate,
а map fingerprint устанавливает прежнюю раскладку тех же pinned inputs.

Предыдущий static-link рецепт проверен независимо. Export-ревизия
имеет отдельный локальный вердикт; изменение исходников/pins требует ревью. `stack_bound_transferred=true`
означает только условный статический результат. Сохраняются предпосылки
reset-only privileged MSP/STKALIGN, прежние USB IRQ19/20 одинакового
приоритета, terminal fault handlers, отсутствие corruption/RTOS/deferred
producer. Физические timing/board предпосылки не проверены.

Output свежий, parent уже существует, путь абсолютный и нормализованный,
без symlink-компонентов и overlap исходников/toolchain/object directory/
Python. Пробелы поддерживаются; ограничения специальных символов совпадают
с object builder. Предполагается стабильная кооперативная FS; это не
sandbox против одновременной злонамеренной подмены файлов.

После создания output все команды имеют сохранённые argv, exit,
stdout/stderr. Ошибки запуска и timeout сохраняются; FAILED receipts
и частичные outputs не удаляются. До создания output ошибки preflight
выводятся процессом; caller должен сохранить его stdout/stderr/exit.
`RESULTS.json` содержит ordered objects, зависимости, input/output hashes,
статус каждого gate, reserve/margin и явные границы проверки.

Синтетические ELF носят имя TEST_ONLY_NEVER_FLASH. Caller-asserted ELF
помечены CALLER_ASSERTED_NOT_FLASH_APPROVED. Оба варианта всегда имеют
hardware_verified=false и flash_approved=false. USB identity, частота,
режим кварца и другие свойства платы не подтверждаются успешной сборкой.

## Export bytes

`image_exports.py` проверяет адреса загрузки LMA: initial `.data` хранится
во Flash, хотя её runtime VMA находится в SRAM. `.bss` и резерв стека
в образ не включаются. BIN начинается с 0x08000000, заканчивается после
последнего Flash load byte, без pad до 64 KiB; gaps, если есть, заполнены FF.
HEX сравнивается как адрес→байт, с проверкой checksum/length каждого record,
отсутствием overlaps/wrap и точным entry. Допускается узкий GNU dialect
00/01/04/05; 02/03 и неизвестные types отклоняются. Type05 обязателен один,
EOF последний; LF/CRLF и один необязательный terminal newline разрешены.

Objcopy из уже восстановленного официального Debian пакета закреплён
отдельным SHA b0f417cb958a1205aa5061b7bedcc05f16335dd718802d5265e8b7b0b5d78c88.
Object manifest связан с ADC-рецептом PR98; прежние WAIT_RELEASE/PMA
pins и оценки стека не применяются к этой композиции. В result сохранены hashes
обоих экспортов и канонического адресного набора. Синтетические BIN/HEX
сохраняют TEST_ONLY_NEVER_FLASH в имени. Наличие файла не означает допуска
к физической прошивке.

## Синтетическое воспроизведение без платы

Цель — проверить рецепт текущего интеграционного ADC-снимка, не подготовить
прошивку вашего экземпляра. Используйте только уже проверенный набор инструментов
из [object README](README.md#откуда-взять-именно-закреплённые-инструменты).
[Fixture](examples/SYNTHETIC_NEVER_FLASH.json) имеет `input_kind=synthetic_fixture`
и все шесть attestations=false. HSE 4,5 МГц, VID/PID 1/0, ток 2 мА и остальные
значения намеренно искусственные. Это не USB identity для использования,
не паспорт платы и не параметры подключения.

Из корня checkout замените три пути на канонические абсолютные пути.
`RUNS` — отдельный уже существующий каталог вне checkout и toolchain;
его дочерние `objects` и `image` должны отсутствовать. При повторе выберите
новый `RUNS`, чтобы сохранить прежние результаты, включая неудачные.

```sh
set -eu
ROOT=$(pwd -P)
TOOLCHAIN="/absolute/toolchain-root/usr"
RUNS="/absolute/existing-parent/fresh-run-parent"
# Создайте RUNS заранее; objects и image пока не создавайте.
python3 -I -B "$ROOT/firmware/build/build_objects.py" \
  --toolchain "$TOOLCHAIN" --out "$RUNS/objects"
python3 -I -B "$ROOT/firmware/build/build_image.py" \
  --objects "$RUNS/objects" \
  --board "$ROOT/firmware/build/examples/SYNTHETIC_NEVER_FLASH.json" \
  --toolchain "$TOOLCHAIN" --stack-bytes 3064 --stack-margin-bytes 0 \
  --out "$RUNS/image"
```

Ожидайте `PASS_RELOCATABLE_ONLY` в object RESULTS.json и
`CONDITIONAL_STATIC_LINK_CHECKS_PASS` в image RESULTS.json, а также
`hardware_verified=false`, `flash_approved=false`. Выходы с именами
TEST_ONLY_NEVER_FLASH нельзя исполнять на плате или прошивать. `3064/0`
проверяет условную границу ADC-редакции PR102 без инженерного запаса. Для
реального экземпляра эти числа автоматически не выбираются. Исторический
пример PR89 с `3024/0` относится только к своему неизменяемому снимку.
Полный рецепт сам
проверяет BOOT, карту, ELF и равенство BIN/HEX. Любой ненулевой exit или
FAILED требует разбора сохранённого журнала, а не обхода проверки.

Проверка этого документа и fixture не является приёмкой общего wire-контракта,
Windows-пакета, питания, USB-развязки, АЦП или временных характеристик.

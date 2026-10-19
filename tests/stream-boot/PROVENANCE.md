# Привязка native boot/parser проверки

## Источник

Byte-pinned compile/include/parser/patch snapshot:
`3caaa9106a3b6356e471c5e03ae0594e20472f7a`, 182 файла.
Все SHA-256 перечислены в `source-manifest.json`. Runtime/prerequisite inputs
взяты из того же снимка, не подставлены из другой версии.

Новые файлы введены в
`5d04613da6d3676399af1ac3a2d6e3f672a7f17f` и байтово совпадают:

- `firmware/boot/mt_stream_main.c`:
  `012ad885f00f9586858753e81a18372be85133965b547bca1d5d01126b6f604b`
- `firmware/boot/mt_stream_timing.h`:
  `de68d690626284e040cd67af2ed96ebd18f8e305d676942e5d19f9b35855c1c0`
- `firmware/build/stream_timing.py`:
  `4f9d300c1b4605693ab876697445d629f69e226619f62072e4f45ce2a69248c4`

Последующие изменения recipe/docs не входят в доказательство исполнения
этого native harness. Совпадение bytes входов проверяется независимо от
текущего HEAD и сохраняется в receipt. Runner не требует Git при replay.

## Структура проверки

`test_parser.py` — новая таблица API/CLI positive/negative controls.
`test_boot.c` — новая native fixture для настоящего boot entry; production
main компилируется отдельной translation unit с переименованием символа
`main` в `mt_stream_boot_entry`. Его static runtime/TIME/boot storage остаются
в production translation unit. Wrappers сохраняют указатели на настоящие
объекты и вызывают оригинальные функции. Return values не заменяются.

Runner использует тот же безопасный приём staged-source compilation,
fail-fast sanitizers и fresh external output, что и предыдущий STREAM runtime
native package. Production setup/bridge patch tools запускаются из pinned
snapshot. Fake DCD здесь минимальный boot-only; он не наследует claims
другого fake-DCD о CDC data/host enumeration. Его методы transfer/open намеренно
аварийно завершают тест при неожиданном вызове.

Generated timing C для нормального boot выпускается production CLI из
явного diagnostic JSON с pinned metadata source SHA и bound=1, затем строго
компилируется. Bad immutable C variants предназначены только для проверки
defense-in-depth entry validation, а не для обхода parser в продуктовой сборке.
Синтетические board input значения не являются авторизованными аппаратными
идентификаторами или результатом измерения платы.

`check_runner.py` проверяет восемь сценариев доставки/отказа, включая полный
переносимый replay в путях с пробелами и отдельную испорченную fixture для
подтверждения fatal UBSan failure. Изменения negative-control копий происходят
только внутри их OUTPUT; исходный repo/package не меняется.

## Доказательства и ограничения

Итог: 86 strict cases, 86 ASan+UBSan cases, 625 parser controls и 8 runner
controls. Exact commands, toolchain identity, source/staged hashes и результаты
сохраняются в отдельно создаваемом OUTPUT/receipt.json. Журналы, generated C,
объекты и staged production source в переносимый пакет не включаются.
`RESULTS.json` содержит только краткие результаты и SHA-256 внешних receipts.

Не выполнялись ARM/full-image/vector/link-budget проверки, target timing,
real Cortex-M IRQ admission/barriers, FSDEV register initialization, физическая
USB enumeration или ADC measurement. Оценка build recipe и соответствия
source_commit фактическому checked-out источнику принадлежит отдельной
проверке recipe. Provisional bound=1 не становится timing qualification.

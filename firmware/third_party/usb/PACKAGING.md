# Границы опубликованного dependency-пакета

**Сначала читать этот файл.** `PROVENANCE_RU.md` сохранён буквально как исходный
отчёт подготовки зависимостей. Он описывает более полный локальный архив;
его старые абсолютные пути и команды не являются готовыми путями checkout.
В частности, упомянутые там `compile_objects.py`, `summarize_objects.py`,
`commands.log`, effective-macro dump и полные compiler-recovery архивы здесь
не распространяются. Это не потерянные обязательные входы текущего runner.

Рабочая замена старых команд из отчёта, из корня checkout:

```sh
TC=/absolute/toolchain/usr python3 tests/usb-cdc/reproduce.py \
  --arm --out /tmp/usb001-reproduction-new
```

Полная инструкция: `tests/usb-cdc/README.md` от корня репозитория.
Ничего не загружается и не устанавливается. Нужны отдельно подготовленные
документированные compiler/binutils/newlib headers; их версии и hashes
приведены в сохранённых `evidence/toolchain-recovery/` metadata.

Сохранены все 35 неизменённых upstream-файлов (1 521 937 байт), лицензии,
индивидуальные notices, `source-manifest.json`, `SOURCES.list`,
`SHA256SUMS.sources` и точная TEST-ONLY конфигурация. Проверка из этого каталога:

```sh
sha256sum -c SHA256SUMS.sources
```

Сохранены свежие post-restoration команды/compile diagnostics и сводки двух
GNU11 прогонов, основного прогона size/symbol/undefined/.d/.su, отдельный C11
отрицательный результат и итоговые таблицы. Суммы 10796/29/637 относятся только
к text+rodata/data/bss отдельных ARM REL objects; семь предупреждений upstream
не подавлены. Эти числа не являются полной Flash/RAM картой программы.

`omitted-archive.json` перечисляет точные исходные имена и hashes не включённых
материалов: `.o`, дублированные подробные логи/ELF dumps, исторические pre-reset
наблюдения, старые локальные helper'ы, download HTML/логи и bootstrap metadata.
Эти файлы не следует искать в checkout или считать вновь воспроизведёнными.
Вновь получаемые свидетельства записываются только в явно заданный `--out`.
Compiler packages/binaries не включены. Отчёт восстановления
`evidence/toolchain-recovery/RECOVERY_RU.md` тоже исторический; перечисленные
там локальные download/extraction файлы могут входить в omitted-archive.

Новые reproduction/verification scripts относятся только к упаковке доказательств;
они не меняют upstream, адаптер, тестовую логику или заключение независимого критика.

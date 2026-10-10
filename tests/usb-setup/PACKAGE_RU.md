# Состав чернового пакета USB-003

Это добавочные файлы поверх draft [USB-002, PR #57](https://github.com/font-weight/stm32_motortester/pull/57).
Точная база: [bdfdde61d158c29e18e6d2abfe2d9d7548966a4d](https://github.com/font-weight/stm32_motortester/commit/bdfdde61d158c29e18e6d2abfe2d9d7548966a4d),
дерево `43101cf54e771048e81a44759938060b842add1a`; ветка
`dot-a/usb002-epoch-metadata-draft`. USB-002 добавляет отдельную переносимую
metadata-policy и её свидетельства; связи с TinyUSB/IRQ в ней ещё нет.

Фактические зависимости, исходники и supporting harness приходят из draft
[USB-001, PR #56](https://github.com/font-weight/stm32_motortester/pull/56),
commit [e24eb10758ca6d63222d2b8414cccffb4afde00a](https://github.com/font-weight/stm32_motortester/commit/e24eb10758ca6d63222d2b8414cccffb4afde00a).
Все 653 унаследованных blob и их режимы сохраняются; ни один опубликованный
upstream-файл этим пакетом не заменяется. Общая цепочка draft-зависимостей не
означает принятие USB-001/USB-002 либо готовность полного USB lifecycle.

Пакет содержит патч, проверяемое применение, авторский runner, supporting source,
отчёты, полные журналы команд с exit codes, матрицы, receipts и ARM disassembly.
Объекты, executables, копии vendor, временный изменённый usbd.c и повторные binary
captures не включены: их генерируют сохранённые команды. Полные локальные исходные
пакеты/неудачные свидетельства сохранены отдельно и не удалялись.

`supporting/historical-full-package-SHA256SUMS.txt` описывает полный исходный
supporting-пакет, включая не включённые сюда build-файлы. Это историческое
свидетельство; не запускать его как список файлов данного текстового пакета.
Пути внутри сохранённых журналов описывают реальные прежние запуски. Для нового
запуска ниже передаются пути нового checkout явно; совпадение абсолютных путей
между машинами не требуется.

## Повторение в полном checkout draft-ветки

Необходимы подготовленные зависимости и supporting harness USB-001, GCC/Python/GNU
patch и отдельный уже установленный ARM toolchain. Автоматической установки нет.
`TOOLCHAIN` должен указывать каталог с `bin/arm-none-eabi-gcc` и `include/newlib`.

```sh
ROOT="$PWD"
OUT=$(mktemp -d)
# Задать реальный путь уже имеющегося toolchain:
# TOOLCHAIN=/absolute/path/to/arm-toolchain/usr
python3 tests/usb-setup/check_patch.py \
  --dependencies "$ROOT/firmware/third_party/usb" \
  --supporting "$ROOT/tests/usb-cdc/supporting" \
  --firmware "$ROOT" --toolchain "$TOOLCHAIN" --output "$OUT/author"
python3 tests/usb-setup/supporting/run.py --expect upstream \
  --dependencies "$ROOT/firmware/third_party/usb" \
  --harness "$ROOT/tests/usb-cdc/supporting" --out "$OUT/upstream-native"
python3 tests/usb-setup/supporting/run.py --expect upstream --sanitize \
  --dependencies "$ROOT/firmware/third_party/usb" \
  --harness "$ROOT/tests/usb-cdc/supporting" --out "$OUT/upstream-sanitized"
python3 tests/usb-setup/supporting/run.py --usbd "$OUT/author/usbd.c" \
  --dependencies "$ROOT/firmware/third_party/usb" \
  --harness "$ROOT/tests/usb-cdc/supporting" --out "$OUT/fixed-native"
python3 tests/usb-setup/supporting/run.py --usbd "$OUT/author/usbd.c" --sanitize \
  --dependencies "$ROOT/firmware/third_party/usb" \
  --harness "$ROOT/tests/usb-cdc/supporting" --out "$OUT/fixed-sanitized"
```

Upstream runner exit 0 означает воспроизведение ожидаемого отрицательного профиля,
а не исправность исходного core. Исправленные матрицы требуют настоящих pass.
Проверка полного firmware link, IRQ/гонок, платы и Windows USB не выполняется.
Границы исправления и незакрытый fail-closed barrier описаны в README.md.

## Независимое ревью и дополнительные свидетельства

`critic-a-local/CRITIC_A_USB_SETUP_REVIEW.md` сохранён побайтно как исторический
отчёт локального ревью: SHA-256
`8d30e446ac77b675bd24e2725ab7d70743030e796103567da3fc9e9035ab6225`.
Его `BLOCKED_EVIDENCE`, `score: null` и отсутствие опубликованного subject/base
относятся к тому снимку. Окончательная привязка к неизменному commit USB-003
оформляется отдельным заключением Critic-A в PR; этот файл не утверждает её наличие.

`critic-a-local/original-additions-manifest.json` — исходный список 49 файлов,
SHA-256 `6dddb986a145755dc16daffe67bafc615449df96eee7ed9639d9f7bfcbc8a799`.
Из этих файлов при привязке изменён только настоящий `PACKAGE_RU.md`; патч,
инструменты, авторские и supporting-тесты, README и прежние свидетельства неизменны.
`review-manifest.json` и `package-verification.json` критика также исторические:
их пути `snapshot/` и состав 49 файлов описывают прежнюю локальную сборку, а не
текущий список опубликованных файлов. Полные временные деревья и binaries не включены.

Четыре `critic-a-local/evidence-*` сохраняют собственные команды, журналы, matrices,
input manifests и receipts критика. Они подтверждают лично повторённые upstream
negative profiles и исправленные native/sanitized матрицы; исходные абсолютные пути
в журналах не переписаны. Повторяемая последовательность основного пакета приведена выше.

Два исходных `test_*.py` критика также сохранены неизменными. Не запускать их напрямую
из checkout: они описывают историческую раскладку fixtures и пишут результаты рядом.
Для повторения использовать подготовку в новом внешнем каталоге:

```sh
python3 tests/usb-setup/critic-a-local/run_supplemental.py \
  --checkout "$ROOT" --out "$OUT/critic-supplemental"
```

Runner проверяет хеши исходных тестов и пяти входов checkout, копирует fixtures и
заменяет только четыре исторических литерала путей в отдельной копии теста
изоляции: dependencies, supporting harness, firmware root и неиспользуемый toolchain.
Логика/утверждения тестов не меняются; исходные входы повторно сверяются после запуска.
`SUPPLEMENTAL_RELOCATION_ADDENDUM.md` и `relocated-supplemental/` сохраняют отчёт
и новый результат Critic-A: 11 проверок применения и 12 проверок изоляции пройдены.
Это проверки инструментов, без запуска ARM или подтверждения IRQ/платы.

Переносимая локальная сборка пакета была проверена; исполнение будущего полного
неизменного checkout не приписывается этим историческим журналам. Независимое
заключение по точной новой базе/пакету и окончательный remote readback нужны отдельно.

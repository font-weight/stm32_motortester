# USB-002: воспроизводимый текстовый пакет

Это draft [issue 54](https://github.com/font-weight/stm32_motortester/issues/54):
сериализованная политика транспортных метаданных, написанная Dot A.
Основное описание: [USB_EPOCH_README.md](../../firmware/transport/USB_EPOCH_README.md).
Приёмка будущего USB bridge, lifecycle или прибора не заявляется.

## Состав и происхождение

- `run.sh`, `test_epoch.c`, `arm-check.sh`, `arm_probe.c` — неизменённые авторские проверки.
- `author-evidence/` — исторические авторские текстовые receipts; исполняемые
  файлы и объекты не поставляются.
- `supporting/` — точный текстовый срез отдельного независимого набора.
  `supporting/REPORT_RU.md` — сохранённый отчёт о его локальном запуске,
  а не новое заключение о публикуемом commit. Входы, модель, тесты и runner
  скопированы побайтно. `inputs/` содержит локальные snapshots исследования
  и issue; прежние абсолютные пути в логах — только исторические locators.
- `critic/` — отдельные неизменённые supplemental tests, source manifest,
  отчёт и logs Critic-A, который не писал рассматриваемую реализацию.
- `input-provenance.json` связывает скопированные тексты с исходными SHA-256;
  `package-manifest.json` связывает все поставляемые файлы, кроме самого себя.
- `verify-package.py` и `reproduce.py` — только публикационная проверка и
  запуск неизменённых наборов в новом каталоге. Production-код они не меняют.

Исходный `supporting/SHA256SUMS` сохранён как исторический manifest целого
локального пакета, включая непоставляемые binary outputs. Его нельзя
применять как `sha256sum -c` ко всему текстовому подмножеству. Точный список
включённых текстов и исключённых генерируемых binary outputs дан в
`supporting/text-selection.json`; `verify-package.py` проверяет обе группы
против исходного manifest и фактические bytes всех включённых текстов.
Исторические ELF objects, native binaries, gcda/gcno и mutant binaries
в репозиторий не добавляются. Старый архив первой версии не поставляется.

## Проверка файлов и полный повтор

Из корня checkout:

```sh
python3 tests/usb-epoch/verify-package.py
python3 tests/usb-epoch/reproduce.py --out /tmp/usb-epoch-fresh \
  --tc /path/to/toolchain/root/usr
```

Output-каталог должен быть новым и находиться вне checkout. Минимум по используемому Python API — 3.9 (`str.removeprefix`); фактически
проверен Python 3.12.14. Другие версии Python не запускались. Нужны также
GCC/gcov 14.2.0, POSIX sh/bash и официальный ARM GCC 14.2.1 20241119,
binutils 2.44, newlib headers. ARM layout сохраняет `root/usr/bin`,
`root/usr/include/newlib`, а рядом с `root/` — `packages/` с исходными
Debian archives и `SHA256SUMS.packages`. Runner сверяет их исходные hashes;
компилятор, headers и archives в USB002 не входят и автоматически не скачиваются.
Версии и исходные SHA-256 записаны в supporting ARM logs.

Wrapper сначала проверяет пакет, запускает author strict/sanitizer и
`-Os` ARM, затем переносит только supporting `tests/` и `inputs/` в output,
запускает его полный runner, critic strict/sanitizer и GCC analyzer.
Внешний путь к прежнему research report не нужен. Исторические evidence
не перезаписываются. Полный stdout/stderr и коды выходов находятся в output.

## Что подтверждается

- Авторские strict C11 и ASan/UBSan проверки.
- Supporting: 12 направленных групп; 2 628 485 ограниченных model transitions;
  86 740 537 проверок; отдельные coverage и десять обнаруженных directed
  safety mutants. Это не исчерпывающее доказательство 32-битного пространства.
- Critic-A: четыре supplemental группы / 1 839 проверок, GCC analyzer.
- ARM `-Os -fno-short-enums`: 470 B text, 0 module data/bss, 48 B state;
  object SHA-256 `2b63118f5e871c51c6675a4203f18dbba7f0d16239dbd2715623c1ef90154159`.
- Supporting ARM `-O2`: 708 B text, 48 B state; object SHA-256
  `e92ba76dbfc3fe56a5ec152275c6188ad8b112b7cced8d5f5d840001e220d4a7`.
  Разные флаги не смешиваются. В обоих случаях остаётся undefined `memset`.

LeakSanitizer отключён; его неподдерживаемый исходный запуск сохранён.
Проверок heap leaks нет. Нет полного firmware ELF, реального ARM execution,
IRQ/PRIMASK синхронизации, подключённого TinyUSB bridge, очереди событий,
SETUP accounting, реальных reset races или доказательства deadline ADC.
Прежние семь TinyUSB/fake-DCD counterexamples остаются отрицательными
свидетельствами и не считаются исправленными этим модулем.

## База и независимая приёмка

Контекст для staging — PR44 commit
`06178f31be79c3e7dbc5b46281a67a5c1d1da7f0`. Окончательная stack base —
проверенный head USB001; он фиксируется в `package-manifest.json` до публикации.
Все base blobs сохраняются по исходным Git object identities; USB002 добавляет
только собственный module, тесты и текстовые receipts. `host/` не изменяется.

Локальный независимый отчёт остаётся историческим доказательством проверенных
bytes. Применимое заключение связывается отдельно с immutable опубликованным
commit/tree после полного source readback. Draft не является приёмкой, merge,
CI run, релизом или готовностью MVP.

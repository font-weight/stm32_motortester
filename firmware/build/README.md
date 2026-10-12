# Воспроизводимая object-сборка черновика

Это интеграционный **draft**. Исходный ADC object-рецепт PR98 проверен
отдельно; проверка данной композиции и приёмка MVP остаются самостоятельными.
Рецепт собирает ровно 32 C-модуля и reset assembly, проверяет исходные ARM
objects и связывает их в `combined.o`. Он не выпускает ELF/BIN/HEX, не определяет
`mt_board`, не выбирает резерв стека и ничего не прошивает.

## Запуск

Нужны Python 3 с поддержкой `Path.is_relative_to`, стандартный `/usr/bin/patch`
и уже установленный ARM toolchain. Рецепт ничего не скачивает и не устанавливает.
Закреплён набор GCC 14.2.1/binutils 2.44 и заголовков newlib в `toolchain.json`;
проверяются bytes каждого записанного инструмента/заголовка, а не только версия.
Другая сборка инструментов отклоняется. Перенос того же дерева инструментов
допустим при сохранении относительной структуры и hashes.

Из корня checkout, задав реальные абсолютные пути:

```sh
python3 -I -B firmware/build/build_objects.py \
  --toolchain /absolute/toolchain/usr \
  --out /absolute/existing-parent/new-build
```

Каталог результата должен отсутствовать, его родитель уже существовать.
Checkout, toolchain и output должны использовать канонические пути без
symlink-компонентов. Для CLI запрещены относительные пути, `..`, `.` и лишние
разделители. Также запрещены переводы строк и символы : # $ \ " и апостроф
в путях: текущий формат dependency-файлов их не поддерживает. Пробелы
проверяются отдельно при переносе checkout. Output не может пересекаться с исходниками, инструментами,
Python installation или каталогом системного patch. Это сознательно узкий
контракт; aliases не поддерживаются. Проверки предполагают стабильную
кооперативную файловую систему и не защищают от одновременного подменяющего
процесса. Системные каталоги выбираются только для чтения.

## Откуда взять именно закреплённые инструменты

Существующий [manifest пакетов](../third_party/usb/evidence/toolchain-recovery/package-download-manifest.json)
содержит три точных имени официальных Debian `.deb`, URL и SHA-256:
`gcc-arm-none-eabi_14.2.rel1-1_amd64.deb`,
`binutils-arm-none-eabi_2.44-3+23+b1_amd64.deb`,
`libnewlib-dev_4.5.0.20241231-1_all.deb`.
Это зафиксированные входы воспроизведения, а не рекомендация заменить их
самой новой версией. Рядом сохранены результаты прежней проверки контрольных сумм;
сами архивы и исходные HTML-страницы не включены в этот checkout.
Доступность URL в будущем не гарантируется; несовпадение SHA — отказ,
а не повод снять pin.

Этот набор содержит исполняемые файлы **Linux x86-64 (amd64)**.
Метаданные обоих executable-пакетов требуют `libc6 >= 2.38`.
GCC также требует `libgcc-s1 >= 3.0`, `libgmp10 >= 2:6.3.0+dfsg`,
`libisl23 >= 0.15`, `libmpc3 >= 1.1.0`, `libmpfr6 >= 3.1.3`,
`libstdc++6 >= 5`, `zlib1g >= 1:1.2.2`; binutils требует
`libzstd1 >= 1.5.5`, `zlib1g >= 1:1.2.0`. Это версии Debian-зависимостей,
не обещание работы на любой системе с похожими номерами библиотек.
Для local unpack нужен `dpkg-deb`; для сборки — Python 3.9+ и `/usr/bin/patch`.
Windows-приложение остаётся отдельным продуктом B; эти Linux ELF tools
не запускаются напрямую в Windows/macOS. Совместимость WSL отдельно не проверена.

Если точные архивы уже получены из указанных источников, сначала сверьте
их SHA-256 с manifest (`sha256sum`), затем распакуйте только проверенные
архивы `dpkg-deb -x <package.deb> <fresh-toolchain-root>` в один новый каталог.
Не используйте `sudo` и не подменяйте системные инструменты. Корень для
`--toolchain` — `<fresh-toolchain-root>/usr`; относительная структура
`bin/`, `lib/gcc/`, `lib/arm-none-eabi/` и `include/newlib/` сохраняется.
Распаковка не устанавливает host shared libraries: их наличие проверяют
в выбранной совместимой Linux-среде отдельно. Рецепт сверяет все 171
pinned input и фактические support tools; полный image flow дополнительно
сверяет objcopy. Успешная распаковка ещё не означает успешную сборку.

Историческая последовательность object → synthetic ELF/BIN/HEX без реальной платы:
[тестовое воспроизведение](BUILD_IMAGE_README.md#синтетическое-воспроизведение-без-платы).
Для текущего wire0.2 кандидата этот image flow заблокирован старыми pins и
reference; успешная object-сборка его не разрешает.

## Что проверяет рецепт

До создания output проверяются hashes manifest-файлов, всех перечисленных
исходников, headers, patch helpers, BOOT checkers и toolchain inputs. Явный список
translation units исключает случайное включение test fixtures. Compiler support
programs `cc1` и `as` также должны соответствовать закреплённым файлам.

Затем upstream USB-код копируется через последовательность hash-pinned patches
в отдельные build-only файлы. Канонический vendor и его лицензии остаются без
изменений. Используется только `firmware/boot/tusb_config.h`.

Все C objects сравниваются с закреплёнными SHA256 предыдущей реальной ARM
композиции. Проверяются фактические compiler dependencies и отдельный reset
object. Выполняются BOOT input/vector audit, `ld -r --fatal-warnings` и проверка
точного unresolved-набора: `mt_board` плюс шесть linker symbols. Отсутствие
board definition здесь ожидаемо, поэтому build может пройти.

`RESULTS.json` содержит команды, exit codes, hashes до/после, зависимости,
размеры секций и статус `PASS_RELOCATABLE_ONLY` либо `FAILED`. stdout/stderr
сохранены рядом. Предварительный отказ оставляет сообщение в терминале и
не создаёт output. После начала работы неудачные каталоги не стираются и
никогда не используются повторно; для исправленного запуска нужен новый путь.

Проверяются `-fno-short-enums`, Cortex-M3/Thumb/soft-float, freestanding,
`-Os`, stack metadata, MEM001 no-builtin flags через закреплённый список
команд и итоговые objects. Vendor использует GNU11; известные conversion
warnings DCD/fsdev сохранены. Reset собирается через точный GCC `.S` pipeline,
чтобы совпадали в том числе metadata/alignment.

## Границы результата

Для исходного ADC-рецепта PR98 отдельно подтверждены совпадение 33 objects,
BOOT audit и строгая relocatable link. Этот результат не объявляет заранее
успешной сборку нового интеграционного снимка: его команды и точные hashes
фиксируются отдельным прогоном. Приёмка MVP и аппаратуры остаётся открытой;
это не end-to-end исполнение прибора.

Размеры `combined.o` не равны финальному размещению Flash/SRAM. Для финального
образа потребуются проверенные входы конкретной платы, явный запас стека,
конечная линковка без GC, повторный ELF audit и привязка расчёта стека к exact
image. Аппаратные timing/USB/ADC испытания остаются отдельными.

Этот flow не полностью hermetic: host Python, `/usr/bin/patch`, динамический
loader и системные библиотеки не поставляются. Фактические Python/patch hashes
записываются; toolchain hashes не доказывают неизменность всех host libraries.
Скрипт очищает унаследованные compiler/include/loader настройки, использует
собственный TMPDIR и изолированный Python для helpers. PATH helpers задан
как /usr/bin:/bin; toolchain/bin не может подменить системный patch. Логи содержат абсолютные
пути среды: перед их внешним распространением требуется согласование раскрытия.

## Issue91: объектная сборка с сообщением ошибок захвата (черновик)

По supporting full33 наблюдению новая версия inputs.json фиксирует
13 изменённых source SHA и семь object SHA относительно унаследованного
manifest: три PMA-объекта и четыре ADC/control/capture/TX. Остальные
26 объектов совпадают с исходной базой, 29 — с новым PMA snapshot.
Команды, флаги и toolchain не меняются.

Историческое ожидание combined SHA256 для указанной выше ADC/PMA итерации:
886e24f6af5e3c07ceffa6dbfe11451a6cabe2a8b0b25cbbff8ef3dc7129d8a6.
Это фиксированное ожидание, не заранее объявленный успешный запуск.
`PASS_RELOCATABLE_ONLY` подтверждает только объектную сборку. Для этих
ADC-исходников обновлённые image/reference проверки поставлены в
[PR102](https://github.com/font-weight/stm32_motortester/pull/102#issuecomment-6118044354);
их условная граница — 3064 байта. Новый интеграционный снимок требует своего
повторения полного рецепта. Семантика Issue91 остаётся предложением с
открытой общей приёмкой A/B. Прошивка не разрешена.


## 12.10.2026 — object pins native wire0.2 кандидата

Production-код соответствует native-кандидату6af7a110bfd64f4cfe1d1a945f5a9a2e6cb21a0d
(опубликован как PR144/7eea520b). Отдельное расширение обновляет ровно шесть
source hashes и четыре object hashes в inputs.json, затем INPUT_SHA в
build_objects.py. Compiler flags, компоненты, toolchain, patches и BOOT
checks не изменены. Автор заново выполнил штатный рецепт:33objects,
PASS_RELOCATABLE_ONLY. Независимый вердикт относится к exact итоговому SHA.

- Текущий combined.o SHA-256:
  7f40a02337cdabebadce6b477d298607c0ab729f0a9c163cdb27318cda8c1364.
- Предыдущая object-база3d32073:
  6e234f6396ac24d817f9e1e6e2197b2a912dcefe0d74d224b36f60be4c9af2ec.
- Изменились capture_link, control, wire, command; остальные29objects
  побайтно совпадают. Relocatable text33672 (+289), data122, bss12238байт.
- Undefined после ld-r: mt_board и прежние шесть linker symbols; новых
  compiler helpers нет. Это не финальная разметка ресурсов.

inputs.json SHA-256:
3cb2115e52ec22ac8b1246d03d63776e3f89ac1743193e4e3d0a1a574f0596f9.
build_objects.py SHA-256:
9770fd2bdda6746240797a71beb5ecd4ceeac7750fa74326640f295f9c7f8954.

build_image.py, image_equivalence.py и stack_reference.json не обновлены.
Image flow должен отказать до выпуска образа. Нельзя переносить старую
границу3064байта, code/literal ranges и layout на новые объекты. Требуется
новое применимое image/stack/IRQ доказательство, затем paired B/host/file
и физические проверки. Ни ELF/BIN/HEX, ни разрешение прошивки здесь не получены.

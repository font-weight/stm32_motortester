# ADC003: переносимое воспроизведение локального пакета

Production-автор и интегратор: Dot A. Упаковка не изменяет четыре production-файла.
Это локальный черновой пакет для root и Critic-A; публикация и приёмка не выполнены.

## Точная база и узкая область

Единственный предполагаемый parent: USB005 PR72,
`adabc2044a3a376056d6756e67bede05c0726440`, дерево
`f6750c8ccbd6dd8d296187fbc45cc3c72b03b0ca`.
Меняются только `firmware/platform/adc_timer_dma.c/.h` и
`firmware/app/mt_adc_control.c/.h`; новые файлы находятся в `tests/adc-commit/`.
Нет добавлений RUN/IRQ/USB006/CLK/MEM/TX002, host или общего wire-контракта.

`parent-inputs.json` связывает 20 необходимых файлов с parent Git blob и SHA-256.
Все 20 идентичны первоначальной базе ADC003 PR61
`7a721f82b98676d6fd9ab1b58a1d9b7d617b47a5`.
В `baseline/` сохранены только четыре заменяемых файла для сравнительных проверок.
Остальные зависимости и два прежних теста берутся из настоящего checkout.
Архив полного унаследованного firmware не добавляется.

## Одна команда из чистого checkout кандидата

Нужна POSIX/Linux-среда с `/usr/bin:/bin`, Python 3.9+, native GCC с
ASan/UBSan и ARM GNU GCC.
Wrapper проверяет GCC metadata/search/include paths до создания output.
Использован GCC 14.2.0 и ARM GCC 14.2.1. ARM root должен содержать
`bin/arm-none-eabi-gcc`, `size`, `nm`, `objdump` и `include/newlib/stdint.h`.
Например, установленный Debian toolchain может использовать `/usr`.
Компиляторы и newlib не входят в пакет. Установка, сеть, GitHub API, CI и плата
во время воспроизведения не нужны. При отсутствии toolchain полный запуск
отклоняется до создания output; отдельные сохранённые native runners остаются доступны.

```sh
python3 tests/adc-commit/reproduce.py \
  --toolchain /absolute/path/to/arm-root \
  --out /absolute/path/outside/checkout/fresh-adc003
```

Output должен быть новым и не пересекаться с checkout, toolchain или фактическими
backing directories исполняемых файлов, GCC support programs/search paths,
header trees, wrapper/script и source inputs. Защита разрешает directory/file
symlink aliases до mkdir; source aliases внутри checkout отклоняются.
Можно выбрать путь с пробелами, заключив его в кавычки. Wrapper сначала проверяет
полный package manifest, frozen production и parent input hashes, затем создаёт
необходимые копии только внутри output. Исходные тесты и production не редактируются.
`CC` позволяет явно выбрать native compiler; он закрепляется абсолютным путём.
Для дочерних процессов создаётся новый минимальный environment: PATH=/usr/bin:/bin,
CC, LANG=C, LC_ALL=C, PYTHONDONTWRITEBYTECODE=1 и TMPDIR внутри нового output.
Не наследуются CPATH/C_INCLUDE_PATH/COMPILER_PATH/GCC_EXEC_PREFIX/LIBRARY_PATH,
CFLAGS/CPPFLAGS/LDFLAGS, loader variables, PYTHONPATH или PYTHONOPTIMIZE.
Исторические Python runners вызываются с `-I -B`; их код не менялся. Все команды, коды завершения, sanitizer
настройки и hashes сохраняются в receipts; исходные checkout hashes проверяются
после выполнения. Повторный запуск требует нового output. Ошибки сохраняются.

Порядок: неизменённые legacy driver/controller тесты и 65 admission сценариев
strict и ASan/UBSan; legacy-only исходной базы в обоих режимах; три намеренно
испорченных копии; 20 boundary сценариев Critic-A в обоих режимах; четыре ARM
объекта; ARM ABI probe. Каждый mutant обязан сначала пройти прежние регрессии,
а затем упасть на admission assertions. Их `FAIL` означает ожидаемый контроль.
LSan отключён (`detect_leaks=0`) и не заявляется.

## Исторические и текущие свидетельства

- `history/` содержит неизменённые прежние root/supporting/Critic-A отчёты,
  receipts, deliberate failures и path refusals. Абсолютные пути в них описывают
  прежние запуски. Они не являются командами для нового checkout.
- `history/critic/CRITIC_A_ADC003_REVIEW.md` сохраняет исходный
  `BLOCKED_EVIDENCE`, `score: null`; он ещё не привязан к финальному commit этого пакета.
- `copy-provenance.json` перечисляет точное происхождение каждой скопированной
  части. Старые selection/manifest документы относятся к своим исходным снимкам;
  отсутствующие там snapshots заменены проверяемыми входами текущего checkout,
  а не выдаются за присутствующие архивы.
- `history/package-r1/` сохраняет прежний wrapper, manifest/tree, его receipts
  и оба Critic-A воспроизведения P2: output под backing directory aliased tools.
  В варианте с полноценным toolchain старый wrapper вернул 0 и создал 305 файлов.
  Этот дефект не скрывается успешными обычными тестами.
- `evidence/current/` хранит выбранные receipts исправленного wrapper с
  relocated checkout, реальным aliased toolchain и загрязнённым environment.
  `evidence/wrapper-r2/` содержит точный отказ на исходном fixture Critic-A
  и 13 воспроизводимых дополнительных negative checks. Повторный окончательный запуск из чистого Git checkout хранится
  отдельно от candidate, чтобы не создавать циклический self-hash.
- `package-manifest.json` перечисляет все файлы этого bounded diff, кроме самого
  manifest. Полный all-file SHA-256 и точный parent/tree-diff передаются отдельно.

Native MMIO/IRQ/clock-health являются моделями. ARM evidence — relocatable objects,
не полная ELF, не исполнение Cortex-M3 и не доказательство реального PRIMASK.
Callback/owner integration, USB/ADC гонки на плате, latency, sampling aperture,
полный RAM/Flash/stack и электрические испытания остаются открытыми.
После упаковки нужны проверка root, независимый Critic-A и отдельная immutable
commit/base/tree binding. Сам пакет не разрешает публикацию, merge или аппаратный запуск.

## Повторение исправленного boundary regression

```sh
python3 tests/adc-commit/test_reproduce_paths.py \
  --checkout . \
  --toolchain /absolute/path/to/arm-root \
  --out /absolute/fresh/fixture-root
```

Проверяются existing/ancestor/checkout aliases, advertised и actual tool parents,
целая header-directory и отдельный header alias, script alias, source alias,
native CC alias и отдельный GCC cc1 backing directory, missing toolchain.
Валидный fixture повторяет расположение Critic-A: копии официальных tool binaries,
symlink proxy и реальные relative libraries/headers. Все 13 отказов должны
завершаться с кодом 2, не создавая output или его новых родителей.
Затем выполняется весь native/sanitizer/baseline/mutant/boundary/ARM/ABI набор
из перенесённого checkout с допустимым output и намеренно загрязнёнными
compiler/Python variables. Это отдельная команда tooling regression, а не
рекурсивный вызов из основного wrapper. Окончательный frozen Git checkout
повторно проверяется основным wrapper отдельно.

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

Нужны Python 3.9+, native GCC/совместимый C compiler с ASan/UBSan и ARM GNU GCC.
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

Output должен быть новым и не пересекаться с checkout или toolchain.
Можно выбрать путь с пробелами, заключив его в кавычки. Wrapper сначала проверяет
полный package manifest, frozen production и parent input hashes, затем создаёт
необходимые копии только внутри output. Исходные тесты и production не редактируются.
`CC` позволяет выбрать native compiler. Все команды, коды завершения, sanitizer
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
- `evidence/current/` после формирования пакета хранит выбранные receipts нового
  воспроизведения. Повторный окончательный запуск из чистого Git checkout хранится
  отдельно от candidate, чтобы не создавать циклический self-hash.
- `package-manifest.json` перечисляет все файлы этого bounded diff, кроме самого
  manifest. Полный all-file SHA-256 и точный parent/tree-diff передаются отдельно.

Native MMIO/IRQ/clock-health являются моделями. ARM evidence — relocatable objects,
не полная ELF, не исполнение Cortex-M3 и не доказательство реального PRIMASK.
Callback/owner integration, USB/ADC гонки на плате, latency, sampling aperture,
полный RAM/Flash/stack и электрические испытания остаются открытыми.
После упаковки нужны проверка root, независимый Critic-A и отдельная immutable
commit/base/tree binding. Сам пакет не разрешает публикацию, merge или аппаратный запуск.

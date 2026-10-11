# ADC003: аудит точек запуска после двух tooling P2

Production и frozen inner runners не изменены. Проверены две поддерживаемые
точки запуска пакета; нельзя переносить их boundary guarantees на произвольный
прямой запуск сохранённого исторического скрипта.

## Поддерживаемые команды

1. `reproduce.py`: полное воспроизведение. В r3 к guard добавлена только отдельная
   проверка `lexical.parent.resolve()` для layered directory/file aliases.
   Текущий SHA-256 `5fb967b0d94bad1e522dd0f0951736d839921375e97040aa1a80e2d32392b362`.
   R2 сохранён побайтно в history/package-r2/reproduce.py; Critic-A лично
   закрыл исходный P2 и повторил его полный набор. Исторический checkpoint
   сохранён в `history/package-r2/PACKAGE_R2_CHECKPOINT.md`.
2. `test_reproduce_paths.py`: tooling fixtures и положительный полный запуск.
   R3 проверяет все входы до первого `mkdir`, включая каждый source/evidence
   файл, lexical/resolved пути самого script, interpreter, native/ARM binaries,
   GCC support/search/include paths, library/header roots и header aliases.
   До записи проверяются package hashes/inventory, production и parent inputs,
   обязательные файлы для последующего copying, а также metadata установки GCC.
   `cc1` может находиться вне advertised proxy: fixture использует полученную
   от compiler схему установки относительно настоящего binary directory.
   Неподдерживаемая relocation-схема отклоняется до создания output.

Обе точки используют subprocess argv без shell interpolation. Companion
проверяет основной wrapper на 15 отказах, себя на 16 отказах, допускает валидный
aliased toolchain в read-only preflight и выполняет полный relocated run.
Self-negative вызовы используют `--preflight-only`; этот режим проходит ту же
полную проверку входов и заканчивается до создания output. Отдельный обычный
запуск companion с исходным валидным alias fixture также проверен.

## Сохранённые внутренние исполнители

- `supporting/run.py`: native/sanitizer сборки после staging.
- `supporting/negative_controls.py`: ожидаемые дефекты в одноразовых копиях.
- `critic/run_extra.py`: boundary assertions поверх успешного supporting snapshot.
- `arm_check.py` и `critic/run_abi.py`: ARM objects/ABI внутри уже проверенного
  output и с предварительно проверенными реальными tool/header путями.

Их байты сохраняют прежнее ревью. Основной wrapper запускает их только после
своего полного preflight, в отдельных свежих подкаталогах, с минимальным
compiler environment и `-I -B`. Эти исторические CLI самостоятельно не получают
новую гарантию проверки всех backing-parent aliases. Прямой самостоятельный
запуск не является поддерживаемой полной командой данного portable пакета.
Старые команды в `history/` являются provenance, а не заменой текущим двум recipes.

## Сохранённые дефекты

R1 main wrapper мог создать output внутри фактического tool backing directory:
55 файлов в раннем воспроизведении и 305 при полностью валидном compiler.
R2 companion повторил эту ошибку и создал 352 файла, затем упал из-за
`cc1.relative_to(proxy)`. Все независимые reproducer/log и прежние frozen
scripts/manifests сохранены в `history/package-r1/` и `history/package-r2/`.
Дополнительно выявлено различие между lexical script parent, его resolved
   directory и конечным file-symlink target parent. Ранний supporting timeout
   сохранил 81 созданный файл; Critic-A затем лично довёл тот же вариант r2 до
   exit 0 и 305 файлов. Оба свидетельства сохранены. Исправленный guard проверяет
   все три места, включая layered tool-link parents.

Успешные обычные проверки этих версий не считаются опровержением дефектов.

R3 exact fixture refusal, обе negative matrices и положительный aliased-tool
запуск находятся в `evidence/companion-r3/`. Итоговый чистый Git checkout
дополнительно прогоняется основным wrapper. Сам аудит является supporting
проверкой упаковки; независимое принятие нового immutable tree ещё требуется.

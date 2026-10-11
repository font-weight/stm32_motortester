# Проверка byte/layout equivalence конечного ELF

Новая WAIT_RELEASE-редакция — локальный черновик, её exact review ещё открыт.
Прежняя реализация проверена отдельно; старый вердикт не переносится автоматически.
Design/reference рассмотрен отдельно; [полный static-link recipe](BUILD_IMAGE_README.md)
интегрирован отдельно от этого узкого checker.
Формальная приёмка проекта остаётся BLOCKED_EVIDENCE, score:null.

```sh
python3 -I -B firmware/build/image_equivalence.py \
  --elf /absolute/image.elf --input /absolute/board.json --stack-bytes N
```

N обязателен. Checker не выбирает production reserve и engineering margin.
Он требует N, кратное 8, в диапазоне 3024..8104: нижняя граница — условная
расчётная оценка, верхняя — свободная геометрия неизменной SRAM. Это не
аппаратная проверка и не защита от реального переполнения стека.

Проверка только читает файлы, без запуска внешних программ или target-кода.
На stdout — JSON; exit 0 для `ELF_BYTE_LAYOUT_EQUIVALENCE_PASS`, exit 1
для `REJECTED`, exit 2 для некорректного CLI. Вызывающий рецепт сохраняет
argv/exit/stdout/stderr и hashes. Файлы должны быть стабильны; bytes
повторно сравниваются после проверки. Это не adversarial TOCTOU sandbox.

## Проверяемая узкая версия

- Пин frozen reference SHA и действующего строгого board JSON parser.
- ELF32 LE ARM EABI5 soft-float, исходный entry и точные 10 sections/5 PHDR.
- Bounds всех прочитанных структур, отсутствие пересечения file payload,
  ненулевых неучтённых file bytes, дополнительных allocating sections,
  скрытых load tails, изменённых flags, alignment, entry или load mapping.
- Прежние VMA/размеры векторов, `.data/.bss`, исходные segment roles;
  пустой executable stub остаётся пустым.
- Четыре immutable region digest: векторы, code/rodata до и после единственного
  reset data literal, initialized `.data`. Проверяемое слово reset обязано
  равняться новой действительной `.data` LMA.
- Вся board-область: 36 scalar bytes, три точных string pointers,
  serial/product/manufacturer с NUL и только нулевой alignment padding.
- Полные отсортированные multisets function и immutable symbols, включая
  aliases и одинаковые local names. Единственные переменные linker symbols
  и новые board symbols проверяются отдельно. FILE metadata не считается
  доказательством происхождения compilation unit.

`stack_reference.json` сохранён с исходным обозначением draft. Его SHA
`0facf8258f391eec1b166bd464dc8ce709d04eaf0ecc7044ea6181a7257b77c3`
связан с новым invalid-zero-board ELF и независимо проверенным условным
расчётом 3024 байт. Поля нового reference и перенос на board image ещё
требуют независимого рассмотрения. Любое изменение reference
требует нового рассмотрения; checker не извлекает себе новый эталон из
проверяемого candidate.

## Непроверенные обязательства полного gate

Этот слой не проверяет ordered 33-object provenance, входной board.o,
команду linker и исходные input/final BOOT audits. Он не проверяет linker
map и биекцию 364 input ranges. Их должен проверить отдельный полный
рецепт перед заявлением о переносе условного stack proof. Ни hash объекта,
ни успех этого слоя сами по себе не подтверждают все предпосылки.

Поэтому `stack_bound_transferred`, `hardware_verified` и `flash_approved`
в результате всегда false. Caller assertions остаются утверждениями,
синтетические данные остаются тестовыми. BIN/HEX не генерируются.

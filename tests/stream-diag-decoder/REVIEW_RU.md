# Независимое ревью офлайн-декодера STREAM

**PASS 9.83/10 только для ограниченного software/offline draft.** Незакрытых P0/P1 в этой области нет. Это не приёмка прибора, HOST collector, инструкции SWD, Windows runtime, прошивки или физических измерений.

## Версия и независимость

- Проверен `stream-diag-decoder-draft-20261019/decode_stream_diag.py`, SHA-256 **37b76dc0d2722ca699646ec92121916c66bf42749d2c8ca5a3fa39637a8adc29**. Standalone draft вне git; commit ему не приписывается.
- README_RU.md SHA-256 **440f9c86fd2b2d434c2105368b949bea1ece20104f32bd5ba518902076177861**.
- ABI reference: `stream-telemetry-work-20261019`, HEAD **31b93659164dec376bea5b973b597196c6a4a0ad**. Header SHA **a2dc9c01d5a1ca9168e776e33c719ec727f0e0e301aad755d12b3280931774e5**, C SHA **8ead5517425c540a7876eaa540edca7205af5c904970e5688aec17b1718251c3**.
- Критик не автор/соавтор декодера. Production/draft implementation не редактировалась критиком. Автор самостоятельно внёс исправления после замечаний. Собственные изменения критика ограничены этой внешней папкой fixtures/harness/review.
- Рубрика: docs/quality-gate.md редакция 1.0, канонический файл в docs/quality-gate.md репозитория. Применяется отдельная firmware-A internal task; контракта wire/host или общей готовности эта оценка не вводит.

## Фиксированная область

Два сохранённых 136-byte dump, expected build/generation/nonce → JSON, с точной ABI v1, сохранением всех 34 uint32, проверкой структуры/совпадения ожиданий, null для отсутствующих наблюдений и честными границами. Проверяются endian/offsets/wrap/размеры, identity/nonce, unknown flags, frozen/quiescence/mismatch, zero counts, first fault/replacement/saturation/DWT invalid и CLI errors. Никаких устройств, сетевого взаимодействия, прошивки или timing qualification.

## Лично выполненные проверки

Linux x86_64, Python **3.12.14**, GCC **14.2.0**. Точные версии повторного запуска сохраняются в OUTPUT/RESULTS.json и *-version.stdout.

1. **384/384 assertions PASS**: OUTPUT/decoder/results.json; stdout в OUTPUT/decoder-checks.stdout. Включает независимый список ABI fields, C sizeof=136 и все 34 offsetof=i×4; little-endian и big-endian negative; уникальные маркеры во всех raw fields; uint32/uint64 границы; modulo wrap без вычисления времени; повторный dump с изменением каждого из 136 bytes; sizes 0/1/135/137/4096; build/generation/nonce mismatch; фазы, undefined flags, exact quiescence booleans/ADC STOPPED, freeze-cycle equality, queue<=16; enum/terminal/first-cause consistency и дополнительные критические predicates.
2. **12/12 file-type/race checks PASS**: OUTPUT/filetypes/results.json. Regular dump принимается; FIFO без writer, directory, final symlink и symlink→FIFO быстро отвергаются. Character/block/socket типы проверены mock lstat, без открытия какого-либо устройства. Смоделированные regular→FIFO и regular→symlink замены отвергаются. Это не adversarial-filesystem proof.
3. **6/6 records из настоящего C-модуля PASS**: OUTPUT/c-records/results.json и OUTPUT/commands.json. Отдельный strict C11 TU компилирует неизменённый mt_stream_diag.c и scripted fake MMIO. Ordinary normal, наблюдаемые poll/copy, first cause до TX, replacement с нулём observations, seeded saturation и sticky-invalid DWT. Проверены JSON acceptance/warnings/normal-shape/hash и всегда false physical/timing. MMIO scripted; это synthetic program test, не DWT hardware или clock measurement. Saturation задан около границы, а не получен миллиардным endurance run.
4. CLI: decimal/hex и min/max identity, help/missing args, invalid numbers/ranges/zero nonce/gen, отсутствующий файл/directory, incorrect sizes и mismatched dumps; точные argv/stdout/stderr/exit в OUTPUT/decoder/cli/. На semantic error exit2 с raw JSON, на argument/open error exit2 с argparse stderr; valid frozen warning record может давать exit0, что README явно объясняет.
5. Source review: нет network APIs, debugger/COM/USB/device client. Вход открывается только после проверки обычного файла; final symlink/special files исключены. Read максимум137 bytes, который отличает ровно136 от oversized. Итоговый source hash повторно проверен после всех запусков. ABI source git diff не создавался.

Попытка дополнительного syscall tracing не состоялась: среда запретила ptrace. Это сохранено текстом в HISTORY.json и не засчитано как успешное доказательство. Повторного обхода запрета не было. No-network/device вывод относится к исходному коду и выполненным safe fixtures, не к неподтверждённому strace.

## Сохранённая отрицательная история

HISTORY.json содержит исходные failed observations как raw-field JSON и SHA-256 fixtures; binary bulk не включён. Current suite заново генерирует все необходимые acceptance fixtures.

- Initial SHA **bac67fd2dde6a50e44c24ac629e3db36ff6795ed8f50302394f566b44af83f72**: **31 false-acceptance failure** из исходных374 assertions. Приняты неверные terminal/result values, inconsistent first-fault fields/flags/ranges и replacement modes. Для каждого отказа сохранены полные raw uint32, ожидаемый/полученный verdict и hash исходного synthetic dump.
- Semantic-fix SHA **3e0aef8bdf28a01cf8c6d74bc0ef87a4ace88bf04e0d3f077b24535e3140ecd2** прошёл исходные374, но source-directed проверка обнаружила пять невозможных histories: ordinary zero step, normal zero TX, saturation без saturated counter, unavailable DWT в frozen и final fault без first-fault. Каждый raw counterexample и observed acceptance/normal shape сохранён в HISTORY.json. Автор исправил predicates; текущий набор явно проверяет каждый отказ.
- До regular-file fix scratch FIFO без writer блокировал CLI: subprocess остановлен после0.5 s. Никакое устройство не использовалось. У текущей версии FIFO отказ быстрый и подтверждён новым replay.
- Ни одна неудача не исключалась из критерия ради PASS. Финальный набор добавил найденные exact-source invariants; unavailable-DWT frozen case требует rejection, поскольку init phase4 не позволяет BEGIN.

## Отличие acceptance от нормального Stop и hardware good

**Да, различие сохранено корректно.** accepted_frozen_record означает соответствие сохранённых files ожидаемым значениям и установленным structural predicates. First-fault, saturated, replacement и invalid-cycle records могут оставаться принятыми данными с предупреждениями; normal_stop_record_shape тогда false. Invalid DWT численные observations сохраняются как raw/observed evidence, но не квалифицируются как timing.

normal_stop_record_shape требует безошибочной структуры, flags0, final runtime OK и NEEDS_HELLO, с обязательными step/TX observations. Даже true не доказывает HOST delivery или что Stop был физически принят конкретной платой. final_hardware_off/ADC state/ownership — значения record, не самостоятельное измерение аппаратуры. physical_identity_verified и timing_qualified всегда false. Expected identity и два совпавших файла не удостоверяют flashed image, symbol address, отсутствие debugger halt, полный пропущенный оборот или calibrated clock. В JSON и README эти ограничения явные.

## Незакрытые границы и небольшие замечания

- Реальная Windows execution не выполнена и не заявлена. Код использует стандартную библиотеку и O_BINARY/fallback для отсутствующих POSIX flags; это только source portability review.
- Best-effort lstat/open/fstat/size/mtime не доказывает защиту от враждебной файловой системы и не гарантирует время чтения network-mounted file. README требует заранее сохранённые локальные dumps.
- Набор числовых enums/phase/flags/count limits вручную повторяет firmware ABI. Это небольшой риск сопровождения, особенно при будущей ABI v2; текущий pin и независимый offsetof/range suite его контролируют. Косметический refactor не требуется для текущей итерации.
- Для выпуска владельцу нужны другие, ранее не выполненные gates: окончательный ELF/symbol identity, безопасная методика получения dumps, HOST raw capture/collector, target/stack/timing/hardware evidence. Данная проверка не закрывает ни один из них.

## Рубрика и вердикт

- **C1 10.0 ×30%**: bounded offline contract, exact ABI/identity/raw/null/semantic predicates и честные claims подтверждены положительными и отрицательными результатами.
- **C2 9.8 ×20%**: corruption/size/mismatch/enum/cause/flags/counts, CLI bounds и special-file failures проверены; filesystem race protection осознанно best-effort с видимыми ограничениями.
- **C3 9.8 ×25%**: независимый authored oracle, C ABI offsets, rejecting regressions, неизменённый C-module replay, сохранённые false-acceptance/timeout observations и source hashes. Syscall trace не получен и не засчитан.
- **C4 9.6 ×15%**: ограниченный stdlib decoder и понятные output modes; ручное дублирование числовых ABI constants остаётся малым риском сопровождения.
- **C5 9.8 ×10%**: воспроизводимые команды, inputs/source snapshots/versions, полный JSON/stdout/stderr, русская инструкция и открытые Windows/physical ограничения. Standalone draft пока не имеет release/CI packaging, что не заявлялось частью scope.

**3.00+1.96+2.45+1.44+0.98 = 9.83**, до округления. PASS относится только к указанным decoder+README SHA. Изменение их bytes требует актуального delta-review. P0/P1 в этой области закрыты авторскими исправлениями и независимым повтором. Это не разрешение merge/flash/release всего проекта.

## Переносимая упаковка без нового балла

Исходный review scope, кандидат, исправления и9.83 сохранены. Эта папка содержит переносимый replay existing suite; новых критериев/сценариев при упаковке не добавлялось. Исторические failed observations представлены текстом и raw-field/hashes в HISTORY.json, без binary bulk. Локальные внешние receipts, упомянутые выше, были лично получены при review; для интеграции они не требуются и заново создаются run.py.

Команда после размещения в репозитории:

```
python3 -I -B tests/stream-diag-decoder/run.py --source . --output /ABS/NEW-EXTERNAL-OUTPUT
```

Все current результаты и команды создаются под выбранным output; paths к source/decoder/header/fixtures вычисляются из --source и текущей test-папки. Default decoder firmware/build/decode_stream_diag.py, optional --decoder позволяет проверить тот же pinned draft до интеграции. Подробнее README_RU.md/PROVENANCE.json.

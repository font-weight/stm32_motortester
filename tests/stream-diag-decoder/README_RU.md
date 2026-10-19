# Переносимое независимое ревью офлайн-декодера

Это существующий bounded software-only набор проверки, подготовленный для размещения в tests/stream-diag-decoder. Новая область приёмки не вводилась. Исходники firmware и декодер этот набор не меняет; fixture/log/bin создаются только при запуске во внешней output-папке.

## Запуск из репозитория

Нужны Linux, Python 3.10+ и native GCC с C11. Лично выполнено на Python 3.12.14 / GCC14.2.0 / Linux x86_64. Это не Windows execution proof.

```
python3 -I -B tests/stream-diag-decoder/run.py --source . --output /ABS/NEW-EXTERNAL-OUTPUT
```

Output должен отсутствовать и находиться вне source/test trees. Default decoder: firmware/build/decode_stream_diag.py. Для отдельного, неизменённого draft до интеграции можно передать --decoder /ABS/decode_stream_diag.py. --cc задаёт native compiler, по умолчанию gcc. Зависимостей pip, сети, устройства и production edits нет.

Runner сначала проверяет exact candidate/source SHA-256 по PROVENANCE.json. Любой mismatch останавливает replay: старый PASS не переносится на другие bytes. Затем запускаются существующие384 assertions, 12 filetype/race checks и6 C-generated scenarios. RESULTS.json содержит фактические результаты; commands.json и stdout/stderr сохраняют точные команды. First-failure evidence не удаляется.

## Состав

- run.py: orchestration/pins/commands и проверка C-generated JSON
- check_decoder.py: независимый oracle всех34 полей/offsets и ранее выполненных positive/negative/CLI cases
- check_filetypes.py: regular/FIFO/directory/symlink и mock special-file/race проверки; реальные devices не открываются
- emit_records.c: существующий reviewer-authored C fixture writer с fake MMIO; exact provenance и hash в PROVENANCE.json
- HISTORY.json: текст исходных31 false acceptances, пяти subsequent consistency counterexamples, FIFO timeout и неудавшегося strace; raw binaries не включены
- REVIEW_RU.md: тот же pinned PASS9.83 и границы; новый балл при упаковке не назначался

## Что именно доказывает replay

ABI v1: little-endian34 uint32/136 bytes, C sizeof/offsetof, raw preservation, expected identity/nonce, wrap без времени, size/mismatch/nonfrozen/quiescence/undefinedflags, cause/replacement/saturation predicates, null для отсутствующих observations и CLI failures. Все данные синтетические. C record writer использует неизменённый module как отдельный TU; seeded saturation проверяет границу, не долговечность.

accepted_frozen_record отличается от normal_stop_record_shape. Warnings/invalid DWT не скрываются; physical_identity_verified и timing_qualified всегда false. Даже normal shape не доказывает flashed image, реальное hardware_off или HOST END/STOP_ACK delivery. Безопасное получение dumps, полный ELF/symbol identity, HOST collector, Windows execution и target/physical/timing gates находятся за пределами этого PASS.

Файловые guards best-effort; недоступный network mount или враждебная файловая система не являются поддержанным способом ввода. Сначала сохраните два дампа локально. Portable suite не содержит исходных failed firmware images или бинарного bulk; HISTORY.json сохраняет их необходимые raw-field observations/hashes.

# TX002: воспроизводимый локальный пакет

Текущая правка packaging: [PATH_CHAIN_REVIEW.md](PATH_CHAIN_REVIEW.md).
Прежние acceptance/clearance и r2/r3 receipts ниже являются историей;
новая версия требует отдельного независимого ревью точного дерева.

Задача [#70](https://github.com/font-weight/stm32_motortester/issues/70). Единственный предлагаемый parent: опубликованный head [PR72](https://github.com/font-weight/stm32_motortester/pull/72) `adabc2044a3a376056d6756e67bede05c0726440`, tree `f6750c8ccbd6dd8d296187fbc45cc3c72b03b0ca`. Перед упаковкой GitHub прочитан заново: PR72 открыт как draft, head не изменился; issue70 закрепляет production за Dot A. Новая публикация и merge не выполнялись.

В production меняется только `firmware/transport/mt_tx.c`, SHA-256 `b706eefbfeedd22941980b456d1a81fb0bbb7da91b8d895b206f43137b369ddf`. Остальные изменения — новые файлы в `tests/tx-stop-stack/`. Это актуальное место пакета; первоначальный локальный README имел путь `tests/transport-stack/README.md` и сохранён без изменения байтов как вход исторического ARM runner. Заголовок TX, ABI, wire, Stop-policy, все прежние файлы и режимы parent, включая `host/**`, неизменны.

## Что доказывается

Полная локальная временная копия 544-байтового `mt_wire_message` заменена 12-байтовым snapshot payload ABORT. Все отказы проверяются до изменения очереди. После compaction восстанавливается нормализованное сообщение с исходными 12 байтами payload, текущей session, нулевыми sequence/request, type=147, length=12 и reply=0. Нет указателя на старый slot, переживающего compaction. Частично переданный head, прежние ответы, committed terminal, NULL fallback, Stop barrier и отсутствие sequence consumption при zero acceptance сохраняются.

Оригинальный TX: SHA-256 `e5b38052c5fef29cb178b15ca589a2414047b51f473046571f04ab29800fa79e`; заголовок: `68f013a8ffb6e946e9cd92f3e760bf593746afdc7097d11d837a101c5ab83dd9`. Успешные сообщения сравниваются по значимым полям и wire; private padding и хвост за payload_length не объявлены API. Отказ сравнивается побайтово со снимком всего TX.

## Одна автономная команда

Нужны Linux, `/usr/bin/python3` (3.12+), системный GCC 14.2.0 с ASan/UBSan, Bash/POSIX sh и ARM GCC 14.2.1 с точными файлами, указанными в `input-map.json`. Никакие установки, сеть, hardware, облачные задачи или CI не нужны. ARM-интерфейс содержит `bin/arm-none-eabi-{gcc,as,ld,nm,size,readelf,objdump}` и `include/newlib`. Исполняемые файлы и headers могут быть symlink на внешнее дерево: действительные helper paths запрашиваются у GCC, а не угадываются относительно этого интерфейса. Другой toolchain требует отдельного перепроверенного evidence, а не скрытой подмены версии.

Из полного checkout этого кандидата:

```sh
/usr/bin/python3 -I -B tests/tx-stop-stack/reproduce.py \
  --toolchain /absolute/path/to/arm-toolchain/usr \
  --out /absolute/fresh/path/outside-checkout
```

`--preflight-only` проверяет все входы, выбранные tools/headers и output separation без создания output. Полный запуск сначала выполняет тот же preflight. Output должен быть новым и не пересекаться с lexical/resolved checkout, tools, helpers, Python, header directories и отдельными backing parents. Для файла проверяются parent и resolved parent каждой промежуточной symlink-подстановки, lexical и final form; цепочки file/directory aliases обрабатываются по PATH_CHAIN_REVIEW.md. Symlink внутри самого checkout отвергается до записи. Для запуска с путями, допустимыми историческому ARM runner, не используйте в output кавычки, двоеточия, `#`, `$`, обратный слеш или перевод строки. Обычные пробелы поддержаны.

Python запускается явно с `-I -B`; compiler/Python окружение дочерних процессов создаётся заново. Исторические скрипты PR30/35, их tests и flags выполняются без правок. Их собственные shell/Python invocations сохраняются как часть исходного доказательства; они получают чистое окружение и одноразовые snapshots. LSan отключён и не заявляется.

Дополнительные проверки самого wrapper:

```sh
/usr/bin/python3 -I -B tests/tx-stop-stack/check_preflight.py \
  --checkout /absolute/path/to/this-checkout \
  --toolchain /absolute/path/to/arm-toolchain/usr \
  --out /absolute/fresh/path/outside-checkout
```

Они проверяют output/source aliases, отдельные backing parents script/evidence/driver/helper/header, missing/mismatched inputs, корректный внешний toolchain proxy и hostile compiler/Python environment. Это проверки инфраструктуры, а не новые предметные тесты TX. Отдельные layered directory+file fixtures проверяют основной wrapper и собственный записывающий entrypoint companion. В прежней версии было 18 infrastructure controls; текущий companion содержит 23, включая FILE→FILE script/tool/own-script/own-tool отказы и допустимый chained-tool proxy. Полный прежний пакет и прежний companion действительно создали файлы в пропущенных промежуточных каталогах: точные hashes/counts — history/layered-alias-replay.json. Исходный wrapper также воспроизводит прежнее ошибочное принятие промежуточного backing directory в одноразовой fixture; его bytes сохранены в history/. Гарантия относится к стабильным inputs/filesystem во время запуска. Это не sandbox и не защита от конкурентной подмены путей/TOCTOU.

## Состав воспроизведения

1. Неизменённые 14 TX suites из PR19 `826bef255362b258d5dcde2100c90dc7d591edb3` на old/new, strict и ASan+UBSan; исходный shell runner также выполняется.
2. 11 differential групп: 11 580 сценариев, 480 361 операция на build. Из них 11 577 создаются public transport API, включая 1024 opaque byte-copy probes; 3 private-state defensive controls отмечены отдельно. Это ограниченное детерминированное покрытие, не исчерпывающее доказательство всех состояний.
3. 10 compile-valid mutations, обнаруженных assertions; compilation failure не считается обнаруженным дефектом. Сохраняются все результаты.
4. 19 исходных runner controls, включая сохранение намеренного compiler failure и перенос всех входов.
5. Полные неизменённые app suites PR30 `a451e5a491439fcfed31f298a589643cf7050ec9` и control suites PR35 `0ddea1e5992caa6231203838c3956e19675867a8` на old/new: author/independent, strict/sanitized, Python wire oracle и два targeted reproducers. 47 точных blobs связаны с полными live Git trees и Git blob/SHA-256 provenance. Independent PR30: 24 группы / 44 окна; PR35: 39 групп / 576 994 checks, 344 frames / 20 окон / 40 960 samples. Только candidate `mt_tx.c` подменяется в этих исторических snapshots.
6. Отдельный oracle Critic-A на old/new, strict/sanitized: 12 288 API-reachable compaction cases, 4 359 168 checks. Он принудительно перезаписывает прежний ABORT slot предыдущими replies, проверяя полностью декодированные сообщения.
7. 28 исходных modeled runtime scenarios в каждом strict/sanitized build. Это исторический набор RUN001/TIME001/ADC003/USB005 с fake MMIO/DCD/IRQ/samples, со строго доказанной TX002 подстановкой. Он не является текущим полным тестом прибора или заменой named app/control regressions.
8. Исходный ARM comparison runner: old/new TX, ABI/symbol/AEABI equality, 25 отдельных объектов и relocatable aggregate. Baseline objects и `.d/.su` сохранены для точного сравнения; исходники каждого объекта доступны через hash-bound карту. Не линкуются одновременно две версии TX или aggregate с составляющими.
9. Неизменённая optional `--tx-stop` recipe и 12 сборок матрицы: old recipe/new-off/new-on при четырёх сочетаниях descriptors/memory. New-off совпадает с old recipe; new-on меняет только TX и итоговый aggregate. Все source overlays находятся в test-only snapshots. Они не устанавливаются в production этого PR и не становятся принятыми зависимостями.

Ожидаемое воспроизводимое ARM наблюдение: local `mt_tx_stop` frame 592→64 B, экономия 528 B; TX и 25-unit pre-GC text/rodata +120 B (aggregate 27903→28023), data/bss 122/986 без изменения. Новый TX object SHA-256 `e014288dc2d8ecdca6d17f9c14de74814c144ce4af7513c97befe53b4741dfb1`; aggregate `39b9b0a725eee442ca543c39b5b700ec13dc41d0f260db02485093312dbbe3fe`. В inherited full closure сохраняются семь conversion warnings FSDEV; сам TX warning-free.

Direct-BL выбранная цепь Stop имеет известный subtotal 3080→2552 B. Это не полный максимальный bound и не stack watermark; остаётся превышение 2 KiB на 504 B до main/caller, exception и IRQ. Полный ELF/startup/linker/RAM fit, WCET, физические ADC/USB, native USB/Windows-связность и hardware acceptance не проверены этим изменением.

## Происхождение и сохранённая история

`package-manifest.json` связывает каждый новый/заменённый файл по Git blob и SHA-256; собственный manifest связывается итоговым Git tree. `input-map.json` отображает исторические пути на точные repository files. Wrapper материализует только перечисленные файлы в своём output, восстанавливает executable bits и проверяет байты. Прежние файлы parent повторно используются по hash; целые inherited firmware/vendor archives не копируются в кандидат. Production header берётся из parent.

`evidence-index.json` показывает точное расположение включённых historical receipts/logs и их hashes. После materialization эти исходные пути можно читать под `OUT/materialized/`; для просмотра истории не требуется запускать старые абсолютные команды. Все новые команды и результаты сохраняются в `OUT/*.log`, отдельных receipts и верхнем `OUT/receipt.json`; input/tool/header hashes сверяются до и после.

Сохранены первоначальный `strict-r1` FAIL из-за предупреждения в самом harness, его точные `run.py`/`differential.c`, намеренный compiler failure, старые metadata до восстановления app/control и финальные source-review материалы. Неудачи не заменены успешными receipts. Исторический отчёт Critic-A `BLOCKED_EVIDENCE / score: null` сохранён как результат до immutable package binding; этот пакет сам не назначает итоговую оценку.

`local-historical-extras.json` перечисляет дополнительные большие исторические raw logs/receipts, сохранённые вне кандидата с hashes. Они не объявлены доступными из repository checkout и не нужны для этой автономной команды. Их удаление из candidate не меняет или удаляет оригиналы. Сопоставимые новые подробные logs/disassembly создаются заново при воспроизведении.

Полный source/local acceptance остаётся за независимым Critic-A после проверки точного tree/parent и личного повторения. Пакет не объявлен принятым, software-ready, готовым к merge или подтверждённым аппаратно.

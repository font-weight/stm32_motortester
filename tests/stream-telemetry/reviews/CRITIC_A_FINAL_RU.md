# Critic-A: независимое source/native ревью STREAM telemetry

**PASS, 9.85/10** для указанной ниже source/native области. P0/P1 не осталось. Ранее найденный P1 DIAG-START-001 исправлен и проверен новым rejecting regression. Исторический REWORK 9.255 сохранён в `REVIEW_ORIGINAL_RU.md`, его результат не переписан.

## Проверенная версия и независимость

- Репозиторий: `font-weight/stm32_motortester`, этап STREAM #191, opt-in diagnostic telemetry.
- Проверенный clean HEAD: `fa9daee885c976ae4e90ac6914c1503ddea2b92b`.
- Production source/manifest pin: `8c51c78024a2e1bcd1e5575ef9189737a3ddce88`.
- Выполненный package HEAD: `b4657447a1bf654e1e22ef61ce402df189c684b7`.
- База telemetry: `01824efd2571d5b2af8ab8fb4360cda8eb6c372d`; исходный отклонённый production: `28dab6824fe7ce45cbce9c910012df8adaaddbca`, evidence HEAD `e533e53dc5e1d614b9e6c9d6d3026f7fc72e9e31`.
- Между выполненным package и конечным HEAD изменились только README, PROVENANCE, RESULTS и PACKAGE в tests/stream-telemetry. Дельта лично прочитана; все compile/include/patch inputs и fixture bytes совпадают с выполненным replay. Весь актуальный PACKAGE hash/length inventory проверен.
- Критик не автор/соавтор реализации. Production edits не выполнялись. Автор сам исправил найденный дефект; другой test writer добавил portable regressions. Собственные дополнительные fixtures/mutants критика находятся только во внешнем output.
- Смена недоступного первоначального reviewer была разрешена владельцем; оценка не наследуется и не выбирается из нескольких результатов.

## Область и критерии

Рубрика: `docs/quality-gate.md`, редакция 1.0. Область установлена до проверки: исходники и native-программное поведение одного foreground RAM record, первый валидный START/session, first cause, freeze, opt-in/default-off и DWT boot gate. Это внутреннее firmware-A изменение без нового wire-контракта. Обязательны source review, strict+ASan/UBSan replay, negative scenarios, source/fixture pins и честные ограничения.

Это не PASS всему прибору, не software-ready релиз, не квалификация timing, полного instrumented target image/stack, физического ADC/USB или HOST. Указанные отдельные проверки не отменены и не объявлены выполненными этим отчётом. Прошивка/публикация/мерж данным заключением не разрешаются.

## Закрытый P1 DIAG-START-001

Исходно `MT_DIAG_BEGIN` находился в `live_start`, но настоящий core сначала валидирует START, записывает generation/want_start и вызывает preliminary quiesce. При отказе этого quiesce start callback не вызывается. Получались NEEDS_HELLO, hardware_off=0, ADC STOP fault, но запись оставалась armed с generation=0 и теряла первую валидную попытку.

В `8c51c780` BEGIN перенесён в начало `live_stop`, перед `mt_live_adc_quiesce`, с предикатом `device.state==MT_LIVE_STOPPED && device.want_start`. Проверен полный вызывающий control flow: эта комбинация создаётся валидированным START непосредственно перед preliminary quiesce. Невалидный START её не создаёт; HELLO заранее меняет state на HELLO_PENDING; обычные terminate/recovery имеют другие states; runtime reentry блокируется `in_step`. Phase gate модуля не позволяет позднейшему вызову заменить первый record. Core ABI и wire не менялись.

Личный независимый observer в strict и ASan/UBSan подтвердил: DMA_EN при STOPPED/unowned вызывает реальный отказ preliminary quiesce, сохраняет старый nonce/gen3/STOP cause, оставляет phase2 при off=false, затем после фактического освобождения DMA и настоящего HELLO quiesce замораживает старую запись. Новый HELLO/CAPS/успешный START не меняют её bytes и не читают DWT.

Portable регрессии отдельно проверяют reset-cleanup вариант этого сценария и invalid-profile START без активации/остановки с последующим допустимым gen4. Новый pre-quiesce regression **отвергает старый runtime** в strict и ASan/UBSan при успешных compile/link. Таким образом, тест проверяет исправленный дефект, а не просто повторяет успешный сценарий.

## Личные команды и результаты

Среда: Linux; Python 3.12.14; GCC (Debian 14.2.0-19) 14.2.0. ARM module/object проверки: закреплённый arm-none-eabi-gcc14.2.1 и binutils2.44. Все C production units остаются отдельными translation units. Полные argv/cwd/exit/environment/compiler hashes сохраняет runner.

Portable replay из любого независимого source checkout с точными manifest bytes:

```sh
python3 -I -B /ABS/SOURCE/tests/stream-telemetry/run.py \
  --source /ABS/SOURCE --output /ABS/FRESH-EXTERNAL-REPLAY
python3 -I -B /ABS/SOURCE/tests/stream-telemetry/check_runner.py \
  --source /ABS/SOURCE --output /ABS/FRESH-EXTERNAL-CONTROLS
```

Лично выполнены обе команды без `--case` и без `--strict-only`:

- **64 runtime +32 module +90 boot cases в каждом strict и ASan/UBSan: PASS.** Включает настоящие core/CRC/queue/TIME/ADC snapshot/TinyUSB-CDC-FIFO-epoch функции и all-256 stop-tail sweep.
- **8 runner controls: PASS.** Отказ при optimize/nonisolated/existing-output/source-overlap/symlink/changed-source; полный relocated strict/san replay с пробелами; deliberate UBSan overflow приводит к fatal failure и не продолжает sanitizer cases.
- ASan/UBSan: `-fno-sanitize-recover=all`, non-PIE, halt-on-error; `detect_leaks=0`. LeakSanitizer не проверен и не заявляется.
- Лично проверены все production source manifest pins, staged/source equality и актуальные PACKAGE hashes/lengths. Root receipts не заменяли личный replay.
- **5 временных current-source mutants отвергнуты:** overwrite first fault; freeze без actual-off; отсутствие precommit HELLO freeze; отсутствие BEGIN перед failed-start; отключение sticky-invalid. Каждый mutant успешно компилируется/линкуется и отказывает целевым assertion.
- **Default-off:** все три изменённых ARM objects ADC/runtime/main лично скомпилированы current/base при одинаковых Cortex-M3/-Os flags, `CFG_MT_STREAM_DIAGNOSTICS=0`; bytes совпали. Runtime проверен повторно после исправления BEGIN. Это объектное сравнение, не самостоятельный full-ELF replay.
- **DMB:** current `mt_stream_diag.c` отдельно скомпилирован target compiler. В обеих freeze ветках конечные record stores и freeze_cycle предшествуют `dmb sy`, затем идёт phase=3 store по offset16. Модуль не изменялся после этого replay. Это проверка object disassembly, не физической SWD coherence.

Hash индексы лично полученных receipts:

- native runtime/module: `d511784313d18c62c271e0aafc525654fd72f952be54e0c47237de342133c670`
- nested boot: `d39a236071aac38115d2c8b888be3830a52cccb8b267c1fda4efd4f2c26692e6`
- runner controls: `579c77b1551883afacfff9e81e3c23a3658b2a7f5a306de355173dd0b39f46c2`
- current mutants +ARM object/DMB checks: `da3842a93f514e18eeef13f6bff245651eb7597ae4b810442818cb721259611f`
- independent fixed-boundary observer: `18d716cf3001093a6ccfa64b38fde26ac797c256e25ea69176389a98ceccfca5`
- new regression rejects old runtime: `780c6c0afb976a1abe4ac1abfd948ca4676706fe3cb01d5cef5700fff7aa87e9`

External receipt locations и полные дополнительныe команды перечислены в `review-fixed.json`. Логи/бинарники не включены в portable package; штатный replay заново создаёт проверяемые логи. Checksums фиксируют полученное свидетельство, а не доказывают неисполненный target/hardware этап.

## Подтверждённые свойства

- ABI v1: один volatile record, 34 uint32/136 байт. Foreground-only writers, нет нового logger, locks или TIME calls. Runtime fixture проверяет DWT вне nested IRQ guard и неизменность TIME reads в hooks.
- DWT: TRCENA/NOCYCCNT/CYCCNTENA/readback/bounded movement probe, сохранение других bits, отсутствие CYCCNT reset. Unsupported/denied/stuck → BOOT_DIAGNOSTICS(stage12) до runtime/USB/IRQ admit. Runtime disable/duplicate cycles → sticky-invalid без изменения TIME/protocol.
- Первая валидная попытка START теперь привязана до fallible preliminary quiesce; calibration/preliminary failures сохраняются. Отброшенная команда не расходует единственную запись.
- Pre-TX note сохраняет первую наблюдённую причину и queue peak перед pop/HELLO commit; позднейшие faults не заменяют first cause.
- Normal Stop: freeze требует реальный off/STOPPED/unowned и NEEDS_HELLO после END/STOP_ACK acceptance в FIFO. DRAINING/TERMINAL не дают ложную заморозку; failed cleanup остаётся active.
- Replacement HELLO: старый record замораживается до commit, старые nonce/generation/cause не смешиваются с новым сеансом; partial-step flag/unknown runtime result задокументированы.
- Frozen record неизменяем, включая отсутствие DWT bus accesses. Copy-rejection counts, modulo wrap/maxima и saturation согласованы с README.
- Step body — только внутренний stamp→observe span; TX bracket включает lock/unlock и возможную preemption. Нулевой первый gap или отсутствие counts не выдаются за нулевую длительность. Это не whole-call, IRQ-off или WCET измерение.

## Ограничения и небольшое замечание

Native seams: CPU/MMIO/DWT/DCD/nested guard/CTR model, boot longjmp после трёх настоящих steps. Нет аппаратного USB/ADC/clock/SWD, доказательства HOST delivery, исторического debugger halt или полного пропущенного DWT оборота, квалификации transaction bound/WCET и полного image/ABI/stack closure. Обязательные условия no-halt и interval<2^32 cycles нельзя вывести из record; они явно остаются внешними.

Неблокирующее замечание сопровождения: magic phase/flag числа и длинный positional observe ABI повышают риск будущей перестановки полей. Сейчас ABI фиксирована static_assert и тестами; косметический refactor не требуется для этой итерации. При следующей смене record стоит использовать именованные constants/параметры. Дополнительная адресная инъекция TIME failure именно внутри нового preliminary-quiesce activation path была бы полезна; текущие общие TIME/cleanup paths и source flow уже проверены, это не скрытый обязательный gate.

## Балл по неизменной рубрике

- **C1 10 ×30%:** заявленные source/native свойства, первый valid START и все исправленные session/freeze пути подтверждены кодом и положительными/отрицательными свидетельствами.
- **C2 9.8 ×20%:** DWT, startup, early returns, rejected/failed start, cleanup retries, first cause, wrap/saturation/frozen подробно проверены; временные внешние assumptions и отдельная адресная TIME injection выше остаются явно ограниченными.
- **C3 9.8 ×25%:** полный личный strict/san и controls replay, five rejecting mutants, regression против старого кода, manifest/package проверки, target module disassembly. Native seams не выданы за target/hardware proof; один дополнительный узкий TIME stimulus указан открыто.
- **C4 9.6 ×15%:** небольшое bounded foreground изменение, не меняет core ABI/wire/TIME; числовые flags и positional ABI дают отмеченный небольшой риск сопровождения.
- **C5 10 ×10%:** source/fixture/receipt pins согласованы; final metadata delta проверена; история REWORK/исправления/исходных отказов и ограничения сохранены; build/default-off/NEVER_FLASH различены.

**Итог =3.00+1.96+2.45+1.44+1.00 =9.85**, без округления вверх. Обязательные source/native проверки пройдены, независимость соблюдена, P0/P1 закрыты. Вердикт **PASS только source/native telemetry**. Следующий этап — отдельный exact-image/stack/callback review и остальные ранее открытые target/physical gates, без автоматического переноса данного балла на них.

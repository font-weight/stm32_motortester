# Независимое Critic-A source/native ревью STREAM telemetry

Вердикт для исходного снимка: **REWORK, 9.255/10**. Незакрытое P1 DIAG-START-001 блокирует приёмку независимо от балла. Это самостоятельная оценка данного ревью; предыдущий балл не использовался.

## Привязка и область

- Репозиторий: font-weight/stm32_motortester, текущий этап STREAM #191, локальная telemetry-итерация; PR новой итерации не указан.
- Проверенный clean HEAD: e533e53dc5e1d614b9e6c9d6d3026f7fc72e9e31.
- Production snapshot и package source pin: 28dab6824fe7ce45cbce9c910012df8adaaddbca.
- База telemetry diff: 01824efd2571d5b2af8ab8fb4360cda8eb6c372d.
- Diff базы к production: восемь файлов, 319 добавленных и четыре удалённых строки. Последующий e533e53 меняет только tests/progress. Проверены все изменённые production файлы и вызывающие их core/ADC/boot пути.
- Ревьюер не автор/соавтор production. Production edits, публикации, прошивка и облачные задачи не выполнялись. Временные мутации существуют только в отдельном /tmp output.
- Критерии: docs/quality-gate.md, редакция 1.0; предварительно установленные source/native границы CRITERIA_RU.md предшествующего проверяющего сохранены. Независимая замена связана с недоступностью Astra и явным разрешением владельца сменить модель, не с выбором более удобного балла.
- Уровень: программный source/native opt-in foreground DWT record. Применимы D-21 и правила достоверности/раздельной приёмки в AGENTS.md; это не приёмка software-ready/прибора, wire0.2 или физического профиля.

## P1 DIAG-START-001: отсутствует запись первой валидной попытки START при отказе предварительной остановки

Места: firmware/protocol/mt_live_device.c:213–230; firmware/app/mt_live_runtime.c:51–56; firmware/platform/STREAM_DIAG_README.md:25.

Core уже проверил START, записал generation и зарезервировал want_start, затем вызывает предварительный quiesce. Если quiesce не подтверждает hardware_off, io.start не вызывается. Единственный MT_DIAG_BEGIN размещён внутри live_start, поэтому запись остаётся armed, generation=0, nonce=0 и не содержит причину этого принятого запроса. Следующий сеанс может стать записанным «первым».

Лично воспроизведено в strict и ASan/UBSan без production мутаций: настоящий HELLO/CAPS, затем modeled DMA_CCR.EN=1 при STOPPED/unowned driver и валидный START. Получено core_generation=3, NEEDS_HELLO, hardware_off=0, adc.first_fault=STOP(6), но record.phase=1 и record.generation=0. Это synthetic register fault, а не аппаратное наблюдение. Проверка выполняет настоящий core/runtime/ADC/TIME код, меняется только fixture stimulus.

Текущий README обещает nonce/generation после первой попытки START; такой же текст присутствовал в первом implementation commit 124c725. Предварительная карточка ревью также требует первый START/session, а план опыта требует сохранять неудачные попытки. Явной исходной договорённости исключить валидный START с отказом pre-quiesce не найдено. Поэтому сужение документации ради PASS не рекомендуется.

Требуется начать запись на границе первой валидной попытки до её fallible pre-start quiesce. Сохранить freeze только после настоящего STOPPED/unowned/hardware_off. Добавить реплицируемый regression этого случая и проверку, что позднейшие HELLO/успешная попытка не заменяют первоначальные nonce/generation/причину. Существующий calibration-failure тест покрывает более поздний отказ после входа live_start и проблему не обнаруживает.

## Лично воспроизведённые проверки

Среда: Linux, Python 3.12.14, GCC (Debian 14.2.0-19) 14.2.0 с C11; точные версии, SHA compiler, окружение, cwd/argv и exits сохранены receipts. Sanitizer использует ASan/UBSan fail-fast, detect_leaks=0; LeakSanitizer не заявляется.

Команды:

1. python3 -I -B /workspace/scratch/8004c8bb11f3/stream-telemetry-work-20261019/tests/stream-telemetry/run.py --source /workspace/scratch/8004c8bb11f3/stream-telemetry-work-20261019 --output /tmp/stream-telemetry-independent-source-20261019
2. python3 -I -B /workspace/scratch/8004c8bb11f3/stream-telemetry-work-20261019/tests/stream-telemetry/check_runner.py --source /workspace/scratch/8004c8bb11f3/stream-telemetry-work-20261019 --output /tmp/stream-telemetry-independent-controls-20261019
3. python3 -I -B /tmp/stream-telemetry-independent-extra-20261019/check_extra.py
4. python3 -I -B /tmp/stream-telemetry-independent-extra-20261019/check_activation_boundary.py

Результаты:

- Полный package replay PASS: 62 runtime +32 module +90 boot в каждом strict и ASan/UBSan, включая all-256-tail sweep; источник и staged inputs неизменны.
- Все восемь runner controls PASS, включая relocation с пробелами и принудительный fatal UBSan overflow.
- PACKAGE.json byte lengths/SHA и все source-manifest pins лично проверены.
- Пять независимых временных мутаций отвергнуты: overwrite first fault; freeze без actual-off; отсутствие precommit HELLO freeze; отсутствие failed-start binding; отсутствие sticky invalid при изменении DWT controls. Изменённый object компилируется и линкуется с прежними отдельными production TU; целевой assertion отказывает, а не compiler/linker.
- Текущий ARM mt_stream_diag.c отдельно скомпилирован arm-none-eabi-gcc теми же published Cortex-M3/-Os flags. В обеих freeze ветках stores final record и freeze_cycle предшествуют DMB SY, затем выполняется phase=3 store по offset16. Это локальная проверка module object, не полный image proof.
- Все три изменённых default-off production objects (ADC, runtime, stream main) отдельно скомпилированы для current и базы при одинаковых target flags и CFG_MT_STREAM_DIAGNOSTICS=0; bytes совпали. SHA: ADC 72002510659917740ee0c51024cba83258b69d8a330e931e70b8c597c5cde952; runtime a984a76274db2f7d3b7b59bcd5507090b35fc76c5c1984322671c4ebf056f63d; main 40096ae13049f67de08d513878d2ba639ec3243210c0bac1e7f55e8dddac338f.
- Отдельный activation-boundary observer подтверждает P1 в strict и sanitized режимах. Первый исследовательский вариант вызвал quiet(), которое само требует уже состоявшуюся остановку и закономерно отказало; это не дополнительный production defect. Этот первоначальный отказ имеется в tool transcript; промежуточный stderr был перезаписан исправленным observer rerun. Финальные boundary logs/receipt относятся только к corrected stimulus.

Полные команды/логи и hashes receipts перечислены в review.json. Native receipts root прочитаны для сопоставления, но итог выше основан на личном новом replay.

## Проверенные свойства и границы

- Запись: 136 байт /34 uint32, фиксированная ABI v1, глобальный volatile record; нет ISR writers, новых locks, printf/CDC logger. Telemetry hooks не добавляют TIME updates; fixture сверяет существующие delta и отсутствие MMIO TIME reads внутри hooks. Runtime DWT accesses проверяются вне моделируемого nested guard.
- DWT startup: сохранение посторонних DEMCR/CTRL bits, TRCENA/NOCYCCNT/CYCCNTENA readback, без CYCCNT reset, ограниченный probe. Отсутствие/отказ/stuck останавливает boot stage12 перед runtime/USB/IRQ admission. Runtime отключение/одинаковый successive cycle даёт sticky invalid и не меняет TIME/protocol поведение. Последовательность 100,100,101 консервативно считается invalid; это явно протестировано.
- First cause/session: после фактического BEGIN generation и полный nonce защищены от повторного BEGIN; первая наблюдённая причина sticky, pre-TX note предшествует DATA pop и HELLO commit. P1 выше является исключением до BEGIN.
- Normal Stop freeze: только actual-off +STOPPED +unowned и NEEDS_HELLO после END/STOP_ACK принятия в CDC FIFO. DRAINING/TERMINAL не замораживаются преждевременно. Failed cleanup повторяется; record immutable и не читает DWT после phase3.
- Replacement HELLO: freeze до commit, сохраняет старый nonce/generation/cause; partial-step flag и UNKNOWN runtime result честно указаны. Старый record не меняется при новом сеансе.
- Документация корректно ограничивает step body внутренним stamp/observe, TX enclosing bracket может включать preemption; это не whole-call timing, IRQ-off bound или WCET. Wrap/saturation, first-gap и rejected-copy семантика сохранены.

Не проверены: физические ADC/USB/точность clock, debugger halt/history или полный пропущенный DWT оборот, SWD visibility на плате, HOST delivery, full instrumented ELF/vector/ABI/stack closure и worst-case/qualified transaction bound. Эти отдельные открытые задачи не маскируются source/native PASS и не дают разрешение flash.

## Рубрика

- C1 8.5 ×0.30: остальные рассмотренные свойства подтверждены, но основная первая валидная попытка может не попасть в record.
- C2 9.5 ×0.20: обширные отказные/cleanup/DWT проверки и actual-off predicate подтверждены; pre-start failure сохранение требует исправления.
- C3 9.8 ×0.25: полные независимые strict/san replays, rejecting mutations, source/package pins, собственное воспроизведение нового случая; mock seams/target ограничения явно отделены.
- C4 9.7 ×0.15: небольшой foreground-only модуль, ограниченные hooks и понятная семантика; числовые flags/phases и длинный observe ABI создают небольшой риск сопровождения, но v1 документирован и проверен.
- C5 9.0 ×0.10: воспроизводимость сильная, ограничения/qualification честные; действующее обещание первого START не соответствует pre-start failure пути.

Итог: 2.55+1.90+2.45+1.455+0.90 = **9.255**, без округления вверх; порог 9.5 не достигнут, P1 открыт. Для повторного ревью нужны production fix, regression и новый exact SHA/pins, затем затронутые полные native проверки и отдельная актуализация target image/stack evidence владельцем соответствующей задачи.

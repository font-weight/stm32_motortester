# USB-002: независимая проверка политики epoch

Дата: 10.10.2026. Основание: [issue 54](https://github.com/font-weight/stm32_motortester/issues/54), контракт `mt_usb_epoch.h`, предыдущее исследование reset/queue barrier. Проверен самостоятельный metadata-модуль основного автора, без изменений его исходников.

## Результат

**PASS в ограниченной области сериализованной политики метаданных.** В проверенной версии не найдено расхождения с независимой моделью или направленными требованиями issue 54. Это не заключение о готовности firmware/USB lifecycle и не подтверждение устранения прежних контрпримеров TinyUSB.

Замороженные входы находятся в `inputs/`:

- `mt_usb_epoch.c`: SHA-256 `c3f70c9937359d263cc78c668bd2a9cd91f8d384d82d958d6587a777b6947159`.
- `mt_usb_epoch.h`: SHA-256 `0846781e5688642fc7ffb59910d3acc2492b3dd9afbef406a4b4671dc2d8be0b`.

При первоначальном чтении dispatch обычного события допускался во время soft pause, хотя core I/O уже запрещался. Я сообщил автору об интеграционном риске: callback мог извлечь живые bytes до отказа DCD. Автор изменил политику до замораживания: теперь обычный dispatch тоже запрещён. Все приведённые ниже успешные результаты относятся именно к исправленным hashes выше. False при soft pause требует **defer**, а не drop; реализация очереди/хранения сюда не входит.

## Самостоятельные проверки

`tests/epoch_independent.c` содержит 12 направленных групп:

1. Начальное закрытое состояние; наблюдение reset закрывает предыдущую эпоху **до** сообщения об enqueue. Обычный successful enqueue не заменяет reset receipt.
2. Настоящий reset dispatch требует отдельного foreground cleanup acknowledgement, успешного enqueue и ещё не обработанного reset.
3. Повторные cleanup/config/enqueue подтверждения не запускают reset второй раз и не обходят состояние.
4. Любой текущий lost enqueue отравляет даже уже configured эпоху. Поздний success/cleanup/resume/config её не открывают. Lost reset требует следующего reset с полным порядком открытия.
5. Reset между разрешённым старым dispatch и reset_done, а также перед config receipt: старый token не меняет/не открывает новую эпоху. Все stale mutators проверены на отсутствие изменений состояния.
6. Submission/completion/deconfiguration failures после config закрывают допуск; сочетания fault bits сохраняются. Config0 → config1 сам по себе не восстанавливает допуск.
7. Несколько soft pauses: старое подтверждение не снимает новое; повторное подтверждение не действует; одинаковый soft serial в другой hard epoch не принимается. Fault не очищается resume.
8. Disconnect создаёт новую закрытую эпоху и не подменяет успешно обработанный BUS_RESET.
9. Soft serial достигает максимума без wrap; следующая попытка закрывает эпоху; только следующий hard reset восстанавливает возможность.
10. Queue-loss и I/O diagnostics насыщаются без wrap, сохраняются при reset. Повторные текущие уведомления считаются уведомлениями, не уникальными аппаратными событиями.
11. Последний hard token UINT32_MAX допускается один раз; следующий reset/disconnect необратимо закрывает объект. Поздние mutators не снимают exhaustion.
12. NULL, нулевые/будущие/stale tokens, неверные Boolean flags, нулевой/неподходящий serial и все неразрешённые fault values 0..127 плюс UINT32_MAX возвращают отказ без изменения состояния.

Проверки независимой модели используют другую форму состояния: phase + факты cleanup/enqueue/config, а не epoch-valued receipts реализации. После каждого шага сравниваются результат вызова, все поля наблюдаемого состояния, reset/обычный dispatch и core/app permissions для current/zero/stale/future/max tokens.

- Все префиксы последовательностей длиной ≤5 из 15 операций, начиная из cold и configured состояния: 1 627 230 переходов + 5 шагов подготовки configured origin.
- 1 000 воспроизводимых случайных прогулок по 1 000 шагов с seed `0x6d6f746f72757362`, включая stale/future/zero tokens: 1 000 000 переходов + 1 250 шагов подготовки.
- Всего 2 628 485 переходов, 86 740 537 проверок. Наблюдались как разрешённые, так и запрещённые app queries: 24 098 и 13 118 332 соответственно.
- Max-boundary origins **подготовлены записью полей**, это не миллиардные естественные истории. В directed tests вручную заданы hard counter, soft serial и diagnostic counters около максимума; в random walks циклически используются cold, near-hard-limit, configured near-soft-limit и near-diagnostic-limit origins. Эти подстановки проверяют арифметические границы и последующие переходы, не доказывают достижимость каждого искусственного начального snapshot.

Сравнение с моделью не является математическим доказательством всех 32-битных состояний. Exhaustive утверждение относится только к указанному конечному алфавиту, origins и глубине; random walks воспроизводимы, но не исчерпывающи.

## Сборки и чувствительность тестов

GCC 14.2.0, C11, `-pedantic-errors -Werror -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Wstrict-prototypes -Wmissing-prototypes`:

- Native `-O2`: PASS.
- Native `-O1 -fsanitize=address,undefined -fno-sanitize-recover=all`: PASS; `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1`, `UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`.
- Первый запуск LeakSanitizer завершился ошибкой runtime «does not work under ptrace». Сохранены `evidence/leak-sanitizer-unsupported.log` и `preflight-with-lsan-commands.json`. Проверка утечек **не выполнена**. Сам модуль и тест не используют heap allocation.
- GCC gcov `-O0`: выполнены 74/74 строк, 88/88 branch edges, 14/14 calls. Coverage не заменяет проверку контрактов.
- Десять ограниченных mutants, только в собственной evidence-копии: отключение stale check, cleanup, queue poison, processed receipt, config receipt, matching soft serial, hard/diagnostic saturation, pause dispatch и enqueue receipt. Все 10 компилируются и обнаруживаются направленными тестами. Это проверка чувствительности oracle к выбранным ошибкам, не поиск всех возможных дефектов.

## ARM object / ABI

Повторно использован уже восстановленный официальный Debian `arm-none-eabi-gcc 14.2.1 20241119`, binutils 2.44. SHA-256 трёх исходных Debian-пакетов повторно проверены: все OK. Ничего не установлено в системные каталоги.

Сборка с теми же strict flags, `-mcpu=cortex-m3 -mthumb -mfloat-abi=soft -O2 -ffreestanding -fno-common -fno-lto -fstack-usage`:

- Модуль и отдельный `tests/abi_probe.c` компилируются успешно.
- ELF32 little-endian, ARM EABI5, CPU v7 Microcontroller, Thumb-2.
- Static assertions проверяют размер state 48 bytes, alignment 4 и offsets всех 12 полей.
- `.text` 708 bytes, `.data`/`.bss` 0. Это размер отдельного объекта без linker/runtime overhead.
- Единственный undefined symbol: **memset**. GCC использует его в init. Нужна штатная реализация platform runtime при будущем link. Нет `libatomic`/lock helpers, но это ожидаемо: модуль **не содержит синхронизации**.
- `.su` показывает 0..8 bytes собственного static stack на функцию; memset/caller/interrupt stack сюда не включены. Disassembly и symbols сохранены. Из этих чисел не следует latency/deadline гарантия.

Не выполнены full firmware link, ARM execution, IRQ/MMIO, плата, USB enumeration или физические race measurements.

## Обязательные границы будущей интеграции

- Все чтения/изменения должны сериализоваться caller. Token capture и guarded DCD check→action требуют действующей target critical section, которой здесь нет.
- Вызов reset должен соответствовать **реальному** наблюдённому BUS_RESET; enqueued/reset_done/configured/cleaned — честным фактам, а не желаемому состоянию. Модель не может проверить физическую достоверность этих подтверждений.
- Dispatch false требует классификации: stale/poisoned можно карантинировать, текущую soft-paused работу нужно удерживать. Producer resume metadata должны разрешать паузу без ожидания callback за удержанными событиями. Reset dispatch является отдельной веткой и не зависит от текущего soft pause.
- Новый token нельзя присваивать старому completion. Доказательство cancellation/serialization поздних аппаратных completions остаётся задачей DCD-интеграции.
- Queue envelope, SETUP accounting, guards всех DCD-путей, FIFO semantics, truthfulness copied-prefix accounting, controller Stop/ARM commit, IRQ mask restoration, physical reset races и ADC deadline не реализованы и не проверены этим тестом.
- Исходные семь TinyUSB counterexamples в двух конфигурациях остаются **отрицательными свидетельствами** до будущего bridge patch. Их не запускали как положительный regression и не объявляют исправленными.

## Воспроизведение и evidence

Команда: `python3 tests/run.py` из копии этого каталога. ARM toolchain по умолчанию берётся из восстановленного shared-каталога; можно задать `ARM_TOOLCHAIN_ROOT` на тот же набор. Runner перезаписывает собственную `evidence/`, поэтому замороженный пакет воспроизводить из копии.

- `evidence/commands.json`: полный argv, cwd, stdout/stderr, exit code и elapsed time каждого финального шага.
- `evidence/strict-run.log`, `sanitizer-run.log`, `coverage-gcov.log`: результаты.
- `evidence/mutation-results.json`, `mutants/`: точные изменения и обнаруженные assertions.
- `evidence/arm-*.log`, `mt_usb_epoch.arm.o`, `mt_usb_epoch.arm.su`: ABI, symbols, dependency, disassembly и локальные stack estimates.
- `evidence/input-output-manifest.json`: SHA-256 входов, compiler binary и ключевых outputs.
- `inputs/issue54.json`: прочитанный snapshot задачи.
- `inputs/reset-barrier-REPORT_RU.md`, `inputs/reference-provenance.json`: локальная копия исторического исследования с исходным locator/date/hash; внешний shared-путь не нужен для воспроизведения.
- `SHA256SUMS`: замороженные файлы всего пакета.

Производственные firmware/host и авторские исходники не изменялись; GitHub writes, новые чаты/Work tasks, покупки и subagents не создавались.

## Исправление воспроизводимости пакета

После замечания Critic-A runner исправлен только на уровне packaging: manifest теперь хеширует package-local snapshots исследования и issue 54, а не обязательный внешний файл `/workspace/shared/usb-reset-barrier-analysis/REPORT_RU.md`. Исторические locators остаются только provenance. Предыдущий замороженный пакет сохранён отдельно как `usb-epoch-independent-v1.tar.gz`. Production C/H, направленные tests, модель и ABI assertions не менялись.

Полный runner повторно выполнен из перенесённого чистого каталога, созданного только из `tests/` и `inputs/`, без предыдущего `evidence/`. Все native/sanitizer/model/coverage/mutation/ARM проверки проходят. Внешним остаётся только явно документированный официальный ARM toolchain; для другой расположенной копии используется `ARM_TOOLCHAIN_ROOT`.

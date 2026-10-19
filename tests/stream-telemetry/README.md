# Native-проверка STREAM diagnostic telemetry

Проверяемый source snapshot: `8c51c78024a2e1bcd1e5575ef9189737a3ddce88`.
Это **программная проверка на синтетических моделях**, не аппаратная или
временная квалификация. Итог: 64 runtime + 32 module + 90 boot cases в каждом
из strict и ASan/UBSan режимов; 8 runner controls. Количество не является
оценкой качества, независимым вердиктом или разрешением прошивки.

## Повтор

Нужны Linux, Python 3.12+, GCC-совместимый C11 compiler, ASan/UBSan и `nm`.
Source должен содержать точные bytes из `source-manifest.json`; Git/network
для replay не нужны. Пакет не содержит production source, logs или binaries.

```sh
python3 -I -B /absolute/portable/run.py \
  --source /absolute/source \
  --output /absolute/fresh-external-output
python3 -I -B /absolute/portable/check_runner.py \
  --source /absolute/source \
  --output /absolute/another-fresh-external-output
```

OUTPUT должен ещё не существовать, иметь существующего родителя, быть вне
SOURCE/package. Путь абсолютный, нормализованный, без symlinks; pinned input
files также проверяются на symlinks. Скрипты отказывают при `-O`, без `-I -B`,
при несовпадении source hashes, попытке перезаписи или вложенном OUTPUT.
`--cc /absolute/compiler` выбирает compiler; обычный `/usr/bin/gcc` разрешён.
`--strict-only` пропускает sanitizer, что явно видно в receipt.

Основной `run.py` выполняет runtime/module, затем вызывает `run_boot.py` для
отдельной boot composition. `--case NAME` ограничивает только runtime inventory,
всё равно выполняет 32 module cases и **пропускает boot**: это targeted replay,
не полный результат. Boot можно отдельно повторить той же командой с
`run_boot.py`. Все проверки полного пакета выполнены без `--case`.

Source сначала копируется в fresh OUTPUT; изменения patch generators происходят
только там. Настоящие TinyUSB setup/bridge patches применяются из pinned source.
Все production C остаются отдельными translation units. До/после проверяются
source hashes; команда, compiler identity, environment и exit status каждой
операции сохраняются в OUTPUT/receipt.json. Boot receipt расположен в
OUTPUT/boot/receipt.json. Ошибка compiler/test/sanitizer немедленно завершает
runner и оставляет доказательство отказа. Strict и sanitized результаты
сравниваются. Fresh processes изолируют одноразовые static-состояния module.

## Что проверяет пакет

### Модуль: 32 случая

- DWT TRCENA/NOCYCCNT/CYCCNTENA, enable readback, уже включённый счётчик,
  bounded probe движения и отказ при неподвижном/задержавшемся counter.
- Сохранение остальных control bits, отсутствие записей CYCCNT/reset counter.
- Zero/init/armed/active/unavailable/frozen; один валидный init attempt;
  generation=0 не запускает запись; первый generation и полный nonce сохраняются.
- Modulo-wrap body/TX/poll/step gap, maxima и отсутствие первого gap.
- Saturation всех четырёх counts. Для границы явно seed-ится одно public count
  поле в UINT32_MAX-1: это не endurance test из 2^32 вызовов.
- Sticky invalid при изменении controls или одинаковых последовательных cycles.
- Каждый first-fault trigger, неизвестный UINT32_MAX runtime result до TX,
  queue peak, replacement partial-step flag, все final поля.
- После frozen сравниваются все 136 bytes и число MMIO accesses; ни один hook,
  повторный init или cycle не меняет запись и не читает fake bus.

Модуль доверяет `freeze_eligible`, supplied caller. Отдельный negative API case
показывает это даже для несовместимых off/owned fields; реальный predicate
проверяется runtime composition ниже. Phase=4 unavailable не обещает полной
immutability: прямой cycle ещё может читать bus/добавить invalid. Последовательность
100,100,101 консервативно отвергается: duplicate уже установил sticky invalid.

### Runtime: 64 случая

47 ранее принятых native runtime regressions сохранены, включая настоящий
wire HELLO/CAPS/START/STOP, ADC sample snapshots/CRC/tail, reset/soft loss,
accepted-count, IRQ guard через commit, RX64/oneTX, cleanup/CTR/TIME semantics.
Tail sweep по 256 длинам сохраняет своё прежнее назначение; одноразовая telemetry
измеряет только первый attempted run данного процесса.

17 новых случаев проверяют:

- armed до первого принятого START; точные generation/nonce/build;
- отказ preliminary quiesce сохраняет первую попытку до hardware start;
- невалидный START не активирует запись, следующий валидный связывается отдельно;
- настоящий EMPTY poll и COPY hooks, existing TIME delta, rejected copy count;
- queue high-water до DATA pop и tail admission при Stop;
- first ADC fault до TX, его сохранение после позднего TIME failure;
- Stop остаётся active в DRAINING/TERMINAL и freezes только после END/STOP_ACK
  acceptance в CDC FIFO; HOST completion отдельно заканчивается позднее;
- заморозка ждёт реального off/STOPPED/unowned, включая повтор failed cleanup;
- replacement HELLO freezes старый record до commit, сохраняя старую причину,
  nonce/generation; новый run не меняет frozen bytes;
- failed START, испорченный DWT при healthy TIME, unavailable/uninitialized module.

DWT hooks получают отдельный scripted MMIO bus. Ни один TIME update не заменён;
wrappers сверяют delta с настоящим clock state и отсутствие добавленных TIME
reads внутри poll/copy/TX hooks. Все DWT accesses runtime происходят вне
моделируемого IRQ guard. Эти проверки не измеряют overhead или IRQ-off duration.

### Boot: 90 случаев

Настоящий `mt_stream_main.c` компилируется отдельной TU с переименованным main;
static storage остаётся production-owned. Real descriptors/clock/TIME bind/
diag init/runtime init/USB core init/final preflight выполняются с сохранёнными
return values. Порядок: D-C-T-B-G-R-U-I-A-S-S-S, где G — реальный DWT init.

86 прежних boot checks включают final MMIO/identity preflight, lifecycle failures,
повторный entry, отсутствие IRQ admission при отказе, продолжение TIME_FAULT/
RECOVERY loop. Четыре новых failure cases: NOCYCCNT, отвергнутый TRCENA,
отвергнутый CYCCNTENA и неподвижный CYCCNT. Каждый завершает boot stage12 перед
runtime/USB/admit; сама успешность init не подделывается. Реальные enabled init
и generated timing input проходят на success path.

## Ограничения

Fake seams: MMIO RCC/ADC/TIME/DWT, USB DCD, вложенный IRQ guard и CTR rearm;
для boot также Cortex-M mask/admit/halt и host longjmp после трёх реальных steps.
Boot DCD — минимальный stub установления регистров, не FSDEV hardware proof.
Runtime использует прежний real TinyUSB core/CDC/FIFO/epoch + instrumented fake DCD.
Controller/parser/ADC snapshot/TIME функции не заменены готовыми ответами.

Native пустая compiler barrier не доказывает target DMB/SWD visibility.
CYCCNT rollover меньше полного оборота, debugger-halt отсутствие, clock accuracy,
physical ADC/USB, full-image/vector/stack и WCET здесь не квалифицируются.
Синтетический bound=1 остаётся declaration, не measured/qualified bound.
Synthetic VID/serial/build inputs не разрешают их применение на устройстве.

ASan/UBSan fail fast: `-fno-sanitize-recover=all`, non-PIE,
`ASAN_OPTIONS=detect_leaks=0:halt_on_error=1`,
`UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`.
LeakSanitizer не проверен: отдельный начальный unit attempt блокировался ptrace;
исходный отказ оставлен вне portable package. ASan/UBSan результаты сохранены
отдельно; refusal не переименован в PASS.

`DEFAULT_OFF_EVIDENCE.json` сохраняет атрибутированную привязку к ранее
полученным ARM object/default-off ELF comparisons. Native runner их не выполняет
и не превращает в доказательство нового instrumented ELF. Итоговые instrumented
ARM/image проверки принадлежат отдельной задаче.

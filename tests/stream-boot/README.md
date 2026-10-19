# Native STREAM boot и строгая timing declaration

Пакет проверяет отдельный diagnostic STREAM entry и синтаксис обязательной
provisional timing declaration. Native-модель **не квалифицирует время,
аппаратуру, IRQ latency, USB, ADC или готовность к прошивке**.

Зафиксировано 182 compile/include/parser/patch input по SHA-256 в
`source-manifest.json`. Metadata snapshot:
`3caaa9106a3b6356e471c5e03ae0594e20472f7a`.
Три новых boot/parser файла идентичны commit
`5d04613da6d3676399af1ac3a2d6e3f672a7f17f`; точные bytes/hash определяет manifest.

## Запуск

Нужны Linux, Python 3.11+, GCC с AddressSanitizer/UBSan, `nm` и `patch`.

```sh
SOURCE="$(realpath /path/to/pinned/source)"
PACKAGE="$(realpath /path/to/portable/package)"
OUTPUT="$(mktemp -d)/stream-boot"
python3 -I -B "$PACKAGE/run.py" --source "$SOURCE" --output "$OUTPUT"
```

OUTPUT — новый абсолютный путь вне source/package; родитель существует.
Runner проверяет input hashes до копирования, собирает отдельный staged
snapshot, сохраняет команды/stdout/stderr и receipt в OUTPUT, повторяет hash
проверку после выполнения. Исходный репозиторий не изменяется. `--strict-only`
оставляет только strict native mode; полная проверка требует оба режима.

```sh
CONTROLS="$(mktemp -d)/stream-boot-runner-controls"
python3 -I -B "$PACKAGE/check_runner.py" --source "$SOURCE" --output "$CONTROLS"
```

Проверяются отказ от Python `-O`/неизолированного запуска, существующего output,
пересечения с source, symlink source, изменения pinned production файла;
положительный replay в каталогах с пробелами и fail-fast на намеренном UBSan
нарушении в отдельной копии fixture. Production-код и журналы в переносимый
пакет не входят.

## Проверки parser

625 API/CLI controls:

- Все integer bound 1..499 и generated C для каждого значения.
- Duplicate/missing/extra fields; дубликат с Unicode escape в имени ключа.
- Bool, float, exponent, NaN, ±Infinity, строка/null/list/object вместо числа;
  диапазоны, неверные schema/mode/profile и source SHA syntax.
- Пустой/слишком большой/не-UTF8/malformed JSON; граница 4096/4097 bytes.
- Existing file, same input/output, directory, hardlink, symlink и dangling
  symlink не перезаписываются; invalid input не создаёт output.
- CLI/API дают одинаковый C; receipt содержит input SHA и все четыре false:
  transaction_bound_qualified, runtime_profile_accepted, hardware_verified,
  flash_approved.

Standalone parser проверяет только синтаксис source_commit: 40 lowercase hex.
В том числе `000…000` синтаксически допустим. Существование Git object,
совпадение с фактическим source snapshot и canonical/drift binding принадлежат
внешнему build recipe и этим parser-тестом не доказываются. Generated C не
является source-identity или timing certificate.

## Native boot

86 случаев в каждом из strict и ASan+UBSan режимов. Нормальный entry использует
C, действительно выпущенный production `stream_timing.py`. Дополнительно
15 наборов immutable C inputs обходят parser специально, чтобы проверить
независимый отказ boot entry от неверного schema/mode/bound/board input.

Линкуются реальные:

- `mt_stream_main`, clock plan/apply, boot TIME и USB preparation
- TIME, USB time binding, STREAM runtime init/step, ADC, live clock/ADC/device
- descriptors, sample queue, wire, USB epoch/bridge, patched TinyUSB core,
  CDC и FIFO

Вызовы отслеживаются calling-through wrappers: descriptors → clock → TIME →
USB time bind → real runtime init → USB preparation → TinyUSB init → CPU admit
→ repeated runtime steps. Проверяются все ранние failures и отсутствие admit
при неуспешном этапе. Два повторных/рекурсивных вызова entry до admit
отклоняются уже установленными descriptors; после первого runtime init его
объект не переинициализируется. Это adversarial single-thread probes,
не обещание общей многопоточной безопасности.

Final checks: clock/FLASH, DMA/ADC/GPIO/TIM/USB/CAN gates/resets, timer/ADC/DMA
active bits, NVIC enable/pending/active/priority, EXTI18, USB CNTR/BTABLE,
TIME prescaler/service gap; runtime/ADC/binding identity and initialization,
writer/in_step/clock fault/session state. Положительные проверки разрешают
pending USB19/20, уже изменённые DADDR/EP0 и игнорируемые reserved bits.
Pending NVIC flags не очищаются.

Реальные TIME_FAULT и RECOVERY не прекращают main loop и не заменяют runtime
storage. RECOVERY-модель активирует чужой DMA после admit перед BUS_RESET:
драйвер отказывается захватывать его, две попытки дают RECOVERY; после
модельного выключения третья возвращает IDLE. Это проверка fail-closed loop,
не измерение нормальной DMA stop latency.

## Честные границы

Подменены MMIO bus; Cortex-M mask/admit/halt; DCD initialization и его NVIC/USB
register effects; CTR-rearm seam; выход из бесконечного main loop через
host-test longjmp. CPU instruction ordering/barriers и фактический FSDEV ISR
не исполняются. Fake DCD создаёт предусмотренные начальные register values;
это **не доказательство**, что настоящий DCD на STM32 создаёт их правильно.
Отдельные preflight probes повреждают captured object fields после настоящего
init, не подменяя его результат.

Контроллер, TIME snapshots, ADC stop/read implementation и возвращаемые
runtime results не подделываются. Board VID/HSE/power/serial в fixture
синтетические и не дают hardware/USB identity approval. Нормальная declaration
использует bound=1 ms как непроверенное значение, не измеренную границу.

Не выполнены ARM full-image/link/vector проверки, Windows/USB enumeration,
физические измерения и разрешение прошивки. Существующие runtime integration
тесты с настоящими протокольными командами не заменяются этим boot пакетом.

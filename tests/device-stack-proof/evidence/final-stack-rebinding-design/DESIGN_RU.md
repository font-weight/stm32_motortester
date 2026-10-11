# Минимальная повторная привязка условной оценки стека к новому ELF

Статус: **проект процедуры; независимое ревью требуется**. Production checker не реализован. Разрешение на прошивку, выбор production reserve и приёмка устройства отсутствуют. Все созданные файлы находятся только в `final-stack-rebinding-design`.

## Вывод

Для текущей узкой композиции достаточно доказать **тождественность машинной программы по тем же адресам**, а не заново решать CFG/SP для каждого набора данных платы. Нужно связать новый ELF с проверенным эталоном по байтам, раскладке, символам и происхождению. Единственное разрешённое изменение внутри прежнего кода/констант — установленное 4-байтное **неисполняемое** слово `Reset_Handler+0x58`, relocation `R_ARM_ABS32 __data_load`. Его новое значение обязано совпасть с отдельно проверенным новым LMA `.data`.

Одних SHA 33 входных объектов, отсутствия кода в board.o и списка известных видов relocations **недостаточно**. Линкер определяет конечные цели переходов, callback-адреса, векторы, константы и размещение; список допустимых типов relocation не доказывает правильность их значений. Нужна проверка фактического нового ELF.

Предложенный строгий режим не разрешает перемещение функций, изменение инструкции или новой цели вызова. Если равенство не выполнено, нельзя расширять маску различий: результат — `REANALYSIS_REQUIRED`. Тогда необходим новый машинный CFG/SP/callback/exception анализ либо отдельно разработанное и независимо проверенное доказательство семантического переноса. Это следующий объём работ, вне минимальной процедуры.

## 1. Проверенная основа

Использованы первичные локальные исходники, ELF и отчёты:

- `device-main-arm/run-r2`: фактические 33 исходных объекта и исходная команда ld;
- `device-stack-bound`: условное доказательство всех путей данной композиции;
- `critic-a-device-stack-review/CRITIC_A_CONDITIONAL_STACK_REVIEW.md`: независимая проверка;
- `firmware-integration-draft/candidate`: проверенные определения `main`, CPU, board ABI и descriptors;
- `board-config-draft`: генератор только данных с разделением caller/synthetic;
- `board-config-root-tests/arm-r1/board.o`: исключительно синтетический fixture.

344 входных SHA и 29 записей supporting artifact manifest проверены повторно: несовпадений нет. Число 29 совпадает с `final-input-check.json` независимого ревью.

Сохраняются 363 text-input диапазона, 410 записей `STT_FUNC` с aliases и 360 уникальных function addresses. Нельзя заменить 363 входных диапазона перечнем уникальных публичных функций: в ELF сохранены также три переопределённых weak fallback тела. Нельзя объединять одноимённые local functions.

Проверенная условная оценка: startup 288 B; foreground с постоянным frame main 2640 B; один USB IRQ software 264 B; один hardware exception allowance 36 B. Итого 2940 B обычно, 3012 B с terminal HardFault и NMI, округлённо 3016 B. Старый all-zero board не использовался для отсечения рабочих ветвей.

Исходные условия исключений, целостности памяти, reset-only/MSP исполнения и внешнего вмешательства остаются именно условиями прежнего доказательства. Этот проект не утверждает их физическое выполнение и не добавляет новых аппаратных допущений.

## 2. Замороженные входы и команда

Будущий рецепт должен проверить до запуска и после завершения:

1. Ровно 33 прежних объекта, побайтно, в прежнем порядке. Манифест `PINNED_33_OBJECTS_ORDER.json` сохраняет проверенные абсолютные пути для этого эксперимента; `DRAFT_REFERENCE_MANIFEST.json` содержит переносимые ordinal/name/SHA записи. Источник списка — argv успешной `test-only-static-link`, а не unordered список файлов.
2. Последним 34-м объектом идёт единственный проверенный board.o. Старый zero-board объект исключён, `combined.o` не используется. Нет archives, дополнительного object/library, CRT, libc/libgcc, LTO, linker plugin, response-file, нового `--defsym`, `--wrap`, GC, strip или post-link patch.
3. Тот же `arm-none-eabi-ld`, linker script, BOOT checker и его sibling helper, vector map и атрибуты ABI. Команда непосредственно вызывает ld с `--fatal-warnings --no-gc-sections --orphan-handling=error`; единственные параметрические изменения — пути вывода, последний board.o и явный `__boot_stack_bytes=R`.
4. Board C действительно получен текущим проверенным генератором из сохранённого входа: повторное parse/generate даёт тот же C; исходный JSON, вид данных, attestations, generator/schema/header/compiler/dependencies/flags/source/object SHA сохранены. Для board-компиляции фиксируются исходные ARM flags из `arm-r1/RESULTS.json`, включая Cortex-M3, Thumb, soft-float, `-Os`, `-ffreestanding`, `-fno-common`, `-fno-short-enums`, function/data sections и noexecstack. Нельзя принимать произвольный C только по похожему имени.
5. Входы копируются в неизменный snapshot либо проверяются до/после в стабильной кооперативной FS. Такая проверка не защищает от злонамеренной одновременной подмены и не должна так называться. Новый каталог вывода не перезаписывает старые результаты; неуспехи сохраняются.

Смена compiler для 33 объектов запрещена их SHA; заново компилировать их для переноса доказательства не требуется. SHA toolchain и compiler для нового data-only object также сохраняются. Отсутствие кода в нём проверяется независимо от compiler.

## 3. Строгий контракт board.o до линковки

Проверить ELF32 little-endian ARM EABI5 ET_REL, совместимые attributes; разрешены только конкретные секции данного generator/flags. Нулевые `.text/.data/.bss` допустимы. **Ненулевые** SHF_ALLOC секции — ровно четыре:

- `.rodata.mt_board`: PROGBITS, flags A без W/X, размер 48, alignment 4;
- `.rodata.board_manufacturer`, `.rodata.board_product`, `.rodata.board_serial`: PROGBITS, flags A без W/X, alignment 1, каждая 2..32 bytes, включая единственный завершающий NUL.

Нет других allocating bytes, NOBITS payload, executable bytes, COMMON, TLS, init/fini arrays, unwind tables, COMDAT/group/merge tricks или неизвестных metadata. Неаллоцируемые symbol/string tables, обычный FILE/mapping/SECTION metadata, `.comment`, `.ARM.attributes`, пустой `.note.GNU-stack` проверяются по формату и фиксированному списку; ими нельзя спрятать новые load bytes.

Ровно один внешний определённый символ: `mt_board`, GLOBAL/DEFAULT OBJECT, value 0 в своей секции, size 48. Ровно три именованных LOCAL/DEFAULT OBJECT `board_manufacturer/product/serial`, каждый покрывает всю соответствующую строковую секцию с offset 0. Нет FUNC, IFUNC, weak/global aliases, undefined external references или иных внешних символов. FILE/SECTION/`$d` local metadata не считать дополнительными объектами данных, но проверять их тип/секцию/границы.

Ровно три relocation во всём объекте, только `.rel.rodata.mt_board`:

- offset 0x24 → `R_ARM_ABS32` → LOCAL SECTION `.rodata.board_manufacturer`;
- offset 0x28 → `R_ARM_ABS32` → LOCAL SECTION `.rodata.board_product`;
- offset 0x2c → `R_ARM_ABS32` → LOCAL SECTION `.rodata.board_serial`.

REL addend во всех трёх исходных словах нулевой. Связанные SECTION symbols имеют value 0 и нужный section index. Relocation offsets не перекрываются, их назначение не code/undefined/absolute/interior pointer. У локальных OBJECT symbols те же starts. Нельзя ограничиться проверкой трёх строк вывода readelf без разбора таблиц ELF и связей sh_link/sh_info.

Остальные 36 bytes должны буквально кодировать проверенный JSON и ABI:

- uint32: HSE 0x00, poll budget 0x04, IRQ priority 0x08, build 0x0c;
- enum int32 HSE mode 0x10; unsigned require_dtr 0x14 и fixed_pullup 0x18;
- uint16 VID 0x1c, PID 0x1e, bcd_device 0x20, bus_power_ma 0x22;
- pointers 0x24/0x28/0x2c.

Требуются точные разрешённые численные значения schema/parser, little-endian представление и сравнение каждого поля. В строках 1..31 печатных ASCII до NUL; serial только буквенно-цифровой. Bytes равны результату генерации, дополнительных/внутренних NUL нет. Это предотвращает использование «данных платы» как новой таблицы callbacks или указателя на код.

`caller_board` и true attestations остаются утверждениями предоставившего данные. `synthetic_fixture` остаётся тестом с false attestations. Ни один вид автоматически не получает hardware_verified/flash_approved.

## 4. Оба исходных BOOT аудита обязательны

До ld запустить оригинальный `check_boot_image.py inputs` **на всех 34 объектах сразу**, с исходными `--reset-object`, `--vector-map`, `--readelf`. После ld запустить неизменённый `check_boot_image.py elf` для нового ELF с явным R, исходными vector map, objdump/readelf. Оба exit code должны быть нулевыми и JSON verdict `CHECKS_PASS`.

Pre-link проверка не заменяет board gate: исходный input audit в основном контролирует metadata/attributes. Final BOOT audit не заменяет stack equivalence: он контролирует layout, векторы и reset, но не весь call graph. Нужны все три слоя.

Сохранить stdout/stderr, argv, exit code и SHA инструментов. Не использовать helper default stack=4096: CLI всегда получает явный R. В новом ELF должны отсутствовать unresolved symbols и nonempty output relocations, динамический loader/dependencies и неучтённые helpers.

## 5. Минимальный новый ELF gate: равенство по тем же адресам

### 5.1. Структура и load image

Проверяются фактические ELF bytes/section headers/program headers; map не является единственным доказательством. Только исходные load sections и пустые разрешённые служебные секции. Нет новых executable segments, veneers/stubs, allocatable orphan или runtime load bytes вне объявленных секций. File/VMA/LMA offsets, flags, alignment и ranges согласованы, не перекрываются и лежат в исходной условной геометрии linker script.

В этой версии пять program headers: Flash RX LOAD; `.data` RW LOAD; пустой RX LOAD `.unsupported_stubs`; `.bss` RW zero-filesz LOAD; RW GNU_STACK. Требовать те же types/flags и roles. Нулевой stub section/segment не является новой программой, но ненулевое содержимое запрещено. Flash LOAD должен точно покрывать vector+text; `.data` file bytes и `.bss` mem range — свои секции, без спрятанного хвоста. Одного «секции попали внутрь широкого segment» недостаточно.

Неизменные размеры и VMA:

- `.isr_vector`: 0x08000000, 236 bytes, align 512;
- `.text` start: 0x080000ec, align 4;
- `mt_board`: 0x08007e68, 48 bytes;
- `.data`: 0x20000000, 128 bytes;
- `.bss`: 0x20000080, 12248 bytes, align 8;
- `__static_end`: 0x20003058; `__StackTop`: 0x20005000.

BSS NOBITS и reset zero range остаются прежними; `.data` initial bytes буквально прежние. Меняется только её Flash LMA. Это важно для сохранения начального состояния callbacks и глобальных переменных.

### 5.2. Четыре digest и единственное изменяемое слово

Переносимый reference содержит SHA256 следующих loaded-byte интервалов:

1. 0x08000000, length 236: все 59 vector words;
2. [0x080000ec, 0x08007de8): неизменный prefix code/rodata;
3. [0x08007dec, 0x08007e68): неизменный suffix code/rodata;
4. 0x20000000, length 128: начальные `.data` bytes по file mapping.

Единственное окно внутри прежних text inputs: [0x08007de8, 0x08007dec). Оно **не обнуляется и забывается**, а независимо проверяется как little-endian uint32 `__data_load`, соответствующий фактическому `.data` LMA и PT_LOAD file mapping. Его offset в исходном `.text.Reset_Handler` — 0x58, relocation ABS32 на `__data_load`. Все остальные байты вплоть до начала mt_board неизменны, включая literal pools, code pointers, MMIO constants, switch tables, padding и оставшиеся reset literals.

Почему это data, а не произвольная исключённая инструкция: Reset starts 0x08007d90; последняя исполняемая инструкция находится на +0x48 и безусловно замыкается на себя. +0x4a — padding, +0x4c..+0x68 — literal pool. Проверенный CFG не содержит переходов в этот pool. `ldr r0,[pc,#60]` на +0x1a читает слово +0x58 как источник копирования `.data`; затем r0 используется только для копирования. Новый оригинальный BOOT reset audit повторно проверяет последовательность, адреса ветвлений, эти literals и единственный вызов main. Изменённое слово не является SP/PC значением или callback.

Нельзя разрешать все ABS32, всю `.rodata` или весь Reset_Handler как изменяемые. Нельзя маскировать байты только потому, что они отличаются.

### 5.3. Новая board область

Все bytes от 0x08007e68 до конца `.text` должны быть ровно проверенным relocated board объектом, тремя строками и только нулевым terminal alignment padding до 4. Никаких дополнительных bytes. При текущем GCC порядок строк: serial, product, manufacturer; минимальный gate фиксирует именно его. Три pointer words в mt_board равны точным starts соответствующих строк.

Сумма длин строк S лежит в 6..96 bytes; конец `.text` и новый `__data_load` равны `align4(0x08007e98 + S)`. Padding 0..3 bytes. Размер original `.text` был 32172; новый размер равен `32172 + align4(S)`, прирост 8..96 bytes. `.data` LMA плюс 128 bytes должен оставаться внутри Flash. Проверить реальную раскладку, не только формулу.

Важная деталь исходного linker script: `.rodata.*` сливаются в выходную `.text`, поэтому у конечных board bytes flags AX. Требовать X=0 у board.o правильно; требовать X=0 у конечного output section означало бы изменить исходный linker contract. В новом ELF data provenance, неизменные control targets и отсутствие fallthrough доказывают, что эта область не добавляет исполняемых путей.

### 5.4. Символы, код и цели

Сохранить точную биекцию всех 363 исходных text-input диапазонов: object ordinal/name, input section name, VMA, size. Проверять map против реальных sections/bytes, а не доверять именам. Полный multiset 410 STT_FUNC records с name/value/size/type/bind/visibility/section остаётся неизменным; 360 unique addresses — дополнительный sanity check.

Строгий immutable-symbol fingerprint сохраняет все runtime symbols и mapping symbols как multiset, включая одноимённые locals. Разрешённые metadata исключения сформулированы в reference: FILE records; три отдельно проверяемых linker symbols `__boot_stack_bytes`, `__StackLimit`, `__data_load`; SECTION symbol пустого `.unsupported_stubs`. В новом ELF сверх эталона разрешены только три отдельно проверенных LOCAL board OBJECT и три `$d` records на точных starts строк. Не использовать dict по одному symbol name для duplicate locals или `$d`.

Все 59 векторов идентичны, включая MSP, Thumb/reset, terminal faults и strong USB IRQ wrappers. Entry остаётся 0x08007d91. Не появляется strong override/новый IRQ/кодовая alias.

Обязательства call-target/SP проверки выполняются переносом уже проверенного сертификата:

- все instruction encodings и addresses совпадают; сохранены SP-affecting instructions, IT blocks, маскирование IRQ и отсутствие переключения stack;
- direct branches/calls сохраняют targets, поскольку их bytes и PC те же;
- девять guarded switch tables и guards совпадают по bytes/addresses; dominance/finite targets переносятся;
- все существующие code-pointer literals/tables и начальная `.data` совпадают, поэтому 53 indirect sites сохраняют прежние закрытые множества целей;
- неизменные code/address-taking relocations исходных объектов и data-only board исключают новый producer deferred API, class table или callback override;
- полный прежний graph охватывает пути для разных board значений, включая admission и runtime. Значение старого zero-board здесь нигде не используется для pruning.

Само повторное SP longest-path solving не требуется, когда эти тождества доказаны. Но присвоить результат по object SHA без final-byte certificate нельзя. Допустимо дополнительно сохранять новый objdump для аудита; он не заменяет байтовое сопоставление и не должен декодировать ASCII-данные как новую программу.

## 6. Резерв и результат процедуры

R задаётся явно владельцем интеграции с отдельным основанием выбора engineering margin. Gate проверяет R > 0, R%8=0, R≥3016 и `__StackLimit=0x20005000-R≥0x20003058`. Для данной неизменной conditional geometry это ограничивает R сверху 8104 bytes. 3016 — округлённое математическое покрытие; оно не является выбранным production reserve. Установка R=4096 сама по себе ничего не доказывает и не заменяет эти проверки. `__StackLimit` — граница размещения, не механизм runtime stack protection.

После успеха допустима только формулировка: «условная оценка 3016 B повторно привязана к ELF SHA=... при неизменных перечисленных предпосылках». Итоговая запись содержит new ELF SHA, reference SHA/review identity, 34 ordered input SHA, board kind/input/source/object SHA, tool/script hashes, exact argv, BOOT logs, layout, symbol/range fingerprints, доказательство единственного literal delta, R, bounds, assumptions и собственные limits.

Не выставлять whole-device PASS, новый балл Critic-A, hardware_verified, flash_approved или software/physical readiness по одному этому результату. Формальная приёмка и disclosure hold принадлежат корневому интегратору и независимому критику. Для любого неподтверждённого обязательства — fail closed; не понижать gate постфактум.

## 7. Компактный переносимый reference

`DRAFT_REFERENCE_MANIFEST.json` — 9806 bytes, SHA256 `c447746787bceb3c603e3847b6e6308937d4773a14673ad0d040ac7f2e88730d`.

Он содержит 33 ordered object hashes, четыре region digests, fixed geometry/literal rule, canonical multiset digests для 363 ranges, 410 function records и 1053 immutable symbol records, прежние bounds и SHA доказательств/BOOT inputs. Для проверки новой сборки не нужен hard-coded путь старого ELF: адреса и hashes берутся из independently reviewed reference. Эталонный ELF и proof bundle всё равно сохраняются для происхождения и повторного независимого аудита.

Reference сейчас **черновой**. SHA — идентификатор, а не доказательство истинности. Перед production implementation независимый reviewer должен проверить derivation из указанного старого ELF и связать свой вердикт с reference SHA. Extraction script не должен автоматически создавать новый доверенный reference из проверяемого candidate.

Canonical records используют JSON ASCII, sorted keys, separators comma/colon без пробелов, без завершающего newline. Массивы записей сортируются лексикографически; численные адреса представлены integers; section identity — имя или SHN label, не случайный section index. Смысл каждого fingerprint описан внутри JSON.

## 8. Выполненный ограниченный эксперимент

Создан ровно один новый статически связанный ELF `SYNTHETIC_ONLY_NEVER_FLASH.elf`, без исполнения. Использованы исходные 33 объекта и прежний synthetic board.o с SHA `17fc42d2523bc28d1add1adf53787c0b1dc4d7ab2d7f5c623bbbb29f318cc761`. Явный **test-only** reserve 3016 выбран для проверки параметрического переноса, без инженерного запаса и без назначения пользователю.

Результат:

- оба исходных BOOT аудита: exit 0;
- все 363 text ranges по прежним адресам; единственный range с отличием — reset literal +0x58;
- все 410 function symbol records, 59 vectors и 128 initial data bytes идентичны;
- immutable prefix/suffix code+rodata совпали по SHA; 1053 canonical immutable symbol records совпали;
- `.text`: 32172 → 32220 bytes; `.data` 128 и `.bss` 12248 не изменились;
- `__data_load`: 0x08007e98 → 0x08007ec8; literal расположен на 0x08007de8;
- `__StackLimit`: 0x20004000 → 0x20004438 из-за другого **test-only** R;
- new ELF SHA: `48ff7846006813b13141d6a2d65fb05c0615ac51b9243bd2e5e57ba7831f982b`.

Это свидетельство фактической возможности узкого gate, не проверка всех будущих board inputs и не его production implementation. Полного adversarial/negative suite здесь нет.

Команды верхнего уровня:

```sh
python3 -I -B /workspace/shared/final-stack-rebinding-design/inspect_synthetic_link.py
python3 -I -B /workspace/shared/final-stack-rebinding-design/extract_reference_manifest.py
python3 -I -B /workspace/shared/final-stack-rebinding-design/validate_manifest_experiment.py
```

Первая программа отказывается перезаписывать существующий ELF. Для воспроизведения её можно скопировать в новый изолированный дочерний каталог: output расположен рядом со скриптом. Исходные paths нужны только этому experiment script, не будущему gate. Exact ld/audit/readelf/objdump argv, exit codes и stdout/stderr сохранены в `EXPERIMENT_COMMANDS.json` и одноимённых log files. Последние два скрипта создают только reference data/fixture fingerprint report; они прямо не являются полным gate.

В эксперименте заново не компилировались production sources, не выполнялись target code/flash/USB/hardware measurements и не изменялся чужой каталог. Проверены SHA всех экспериментальных inputs до/после.

## 9. Требуемые отрицательные проверки будущей реализации

Минимально: изменить/добавить/переставить исходный object; подложить старый combined/zero board; добавить board FUNC, weak/global alias, writable data/COMMON/extra section; заменить relocation type/offset/addend/target; недопустимый ASCII/NUL/длина/field value; изменить один SP instruction, direct target, callback word или switch entry; переместить функцию; изменить vector/alias; добавить veneer; изменить `.data` byte или `.bss` размер; подменить map без соответствующих ELF bytes; изменить reset literal на допустимый Flash адрес, который не равен `.data` LMA; добавить segment tail; R<3016, невыравненный/перекрывающий static storage R; stale reference SHA; ошибка BOOT audit; смена inputs во время run. Каждый случай обязан дать явный отказ без acceptance record.

Положительные случаи: минимальные и максимальные длины всех трёх строк, разные разрешённые scalar values и независимые явные R в допустимой геометрии. Эти тесты проверят implementation; один выполненный fixture их не заменяет.

## Ключевые SHA первичных источников

- Старый ELF: `0450e7ee104e19d6a1c018c2f6d1bdcb6f4b74544063044e14c41c01b586c2ae`
- Supporting input manifest: `a5086666280e0ea07468190a35e835733e1b07a68b49edff1d9014a11475e521`
- Supporting artifact manifest: `c85b25382a2e79a2c588d1fc10cf1092f364e902d9f298165a9fd667fed85aaf`
- Independent review: `035d31817eeccf077280fd36ae9c06e0240dbe61977e89bd80703d7c7f68afa4`
- `main` source: `f42d80a8c5c999819252d71d9cda9ce8797db3c22cd59e9dfeac7f55f8bcdc9b`
- `mt_board_inputs.h`: `45119dbca2ce9d7de62cbadb93835c86ceff3e3a44a8e65a4cc327f27a2bf5a5`
- Generator: `0b5d6ba56603e908d7f0b079e3319863f1f7733c97324644377a3e46244f9f27`
- Linker script: `54a13bae7a9edc25ca38dadaa3c31a957108099fbb193c66d3e32dcc26b7d60c`
- ld: `0e75ca3d63c5dbce6a2f1c42c1cccd93d7b8d7b0d4c6242ba8e6302c6387ca4d`
- BOOT checker: `dec58c65c6907ce592018519813fbf2e95332167505c3246d059673c0810c6d2`
- Его helper: `358a082b3058c2cb6656aa6609f34ddd5309e1afb52ddeb8191834bce917777e`

Полные object/tool/input hashes находятся в двух входных manifests; artifact manifest этой работы формируется отдельно и не включает сам себя.

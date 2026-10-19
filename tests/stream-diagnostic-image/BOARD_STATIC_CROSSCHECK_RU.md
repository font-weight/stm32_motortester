# Независимая перепроверка board-specific STREAM candidate

**CROSSCHECK PASS, 9.65/10**, только conditional exact-image static stack/ABI/callback и точная привязка caller-board данных. В этой области P0/P1 не найдено. Новый образ проверен отдельно; численная оценка r3 автоматически не переносилась. Исторический r3 proof не изменён.

## Точная версия

- ELF SHA256: `e32461ba94ce8d105ced09dd56ee82689f4fbdc4b8c8bdb15419e74a94b807f2`.
- Build/source commit: `31b93659164dec376bea5b973b597196c6a4a0ad`; production firmware-equivalent `8c51c78024a2e1bcd1e5575ef9189737a3ddce88`.
- Пакет: `stream-instrumented-stack-review-20261019/board-r1-proof`, 234 pinned files.
- Manifest SHA256: `8e502bea8ca2f403dcc432e2a35f74166297537c3701a30d0a5432d0f55cffb4`.
- Image receipt SHA256: `64cef569b388f234858efae244b7f6ef63e7d6c6eab5211204e6a54da7eecd20`.
- Board JSON SHA256: `d4b2e32978078406b1d5ce96b357cd3f5b9bdb2cf9c843c4c4a956c6ec819f95`.
- Provisional timing JSON SHA256: `7ab0aabbb13eb49132b81127cea6892cb3c86fbe62747dec4adedd11d03d07c2`.

Ревьюер не автор production или frozen proof. В frozen файлы не вносились правки; выполнялись штатный verifier и отдельные собственные проверки вне пакета. Рубрика неизменна: `docs/quality-gate.md` редакции1.0; область — новая exact-image условная модель и board-data binding, без физической/временной приёмки.

## Данные финального ELF, проверенные лично

`mt_board` декодирован непосредственно из PT_LOAD: 48 байт по адресу0x08008268. Поля совпали с JSON:

- HSE8000000 Hz, crystal enum0; clock_poll_budget1000000
- USB IRQ priority0; require_dtr1; fixed_pullup flag1
- firmware_build0x53545231 (1398034993)
- VID0x1209/PID1, bcdDevice0x0100, declared bus power100mA
- manufacturer «font-weight», product «Motortester private LV test», serial «AH20261017001»

Все три NUL-terminated строки прочитаны по фактическим ELF pointers и сверены с JSON, а не только с generated C. Адреса0x080082c2/0x080082a6/0x08008298 находятся в читаемом Flash; длины11/27/13 chars укладываются в31. По исходникам это даёт ожидаемые string descriptor lengths24/56/28 bytes и power byte50 после успешного descriptor init. Это вычисленное ожидание, не наблюдение enumeration.

`mt_stream_timing` по0x080082d0 содержит ровно(1,1,1): schema1, diagnostic_provisional1, declared bound1ms. JSON source_commit совпадает с build/source31b9365. Число1ms не стало квалифицированной временной границей.

Исторический caller_board сохранён с единственным изменением firmware_build. Все шесть true caller attestations — прежние декларации. Эта работа **не установила** MCU/SRAM/HSE/pull-up/power/USB VID authorization/уникальность serial и не получила новых аппаратных attestations. Все пять qualification flags исходной квитанции остаются false, статус DIAGNOSTIC_LINK_ONLY_NOT_QUALIFIED. Подмена этих деклараций аппаратным PASS была бы неверна.

Source-predicted HOST identity: build0x53545231; CAPS tuple(config_id=1,numeric_profile=1,channel=0,adc_bits=12,fs_num=50000,fs_den=1,max_block=128,flags=1). Это nominal source identity, не физический обмен или измеренная частота.

## Личные проверки

1. Все425 input pins,100 dependencies,253 generated pins и30 ordered object hashes новой сборки сверены: mismatch нет. Все248 regular files source.tar побайтно совпали с git source31b9365. В сравнении с synthetic r3 отличается только board.o; остальные29 objects, включая timing.o, совпали. Это дополнительное свидетельство, не основание автоматически переносить численную границу.
2. Новый расширенный board ABI probe отдельно скомпилирован теми же ARM flags/headers. Object byte-identical, SHA256 `3eeca05cf3051f4e764e0014b684c5e574b757efa212b6003821899f8b0b495d`; подтверждены board48B, identity20B и все field offsets. Probe не линковался в production.
3. Лично выполнен полный sealed replay:

```sh
python3 -I -B /ABS/board-r1-proof/verify_package.py \
  --objdump /ABS/PINNED/arm-none-eabi-objdump
```

Результат PASS, все6 analyses exit0: verify_board, callback census, SP CFG, rejecting mutations, bound derivation и interpreter-policy controls. Проверены30 objects,74source+26external/generated dependencies,344 static .su frames. 9 analysis mutation controls, recursion/reserve controls и42 реальных denied interpreter-mode tests выполнены заново самим verifier. Новые результаты обязаны совпасть с frozen hashes.
4. Лично дополнительно повторены три board-data rejecting controls на отдельных копиях: неверный declared build, неверный board ABI size и изменённая историческая attestation. Все exit1, без binding output. Read-only frozen manifest после этого неизменен. Четыре invalid-tool/integrity свидетельства автора пакета отдельно просмотрены; не выдаются за личный повтор всех четырёх.
5. Для нового ELF выполнен отдельный `objdump -d --no-show-raw-insn`. Все12036 настоящих инструкций совпали с исправленным raw parser, в том числе addcc и следующий uxth. Это независимо проверяет, что raw-hex lexer не проглотил mnemonic.
6. Свежий независимый leaf-first/topological расчёт по **новому** inventory346 functions/1062 call-tail edges заново дал Reset/foreground2856B, runtime_step2752B, оба USB software roots264B, Default0. Ожидаемые старые числа не были входом этого расчёта. Из HP/LP достигаются по39 функций, Default — одна; diagnostic bodies из этих roots недостижимы. Callback exclusions и source-root модель повторно просмотрены с учётом изменившихся board полей, DTR1 и HSE8MHz.
7. После завершения лично проверены все234 frozen hashes и исходный manifest SHA: изменений нет. Raw output полного личного replay имеет SHA256 `f3445ffb6556281a01a7b6d911f06b6df29df6e19af614a76f624892c8dfbedc`.

Полные собственные команды, stdout/stderr и итоговые JSON перечислены в companion `board-static-crosscheck.json`. Повторяемые стандартные команды находятся в frozen пакете. Root/другие отчёты не заменяли личные действия выше.

## Число и область применения

Свежая арифметика: **2856+264+3×36=3228B**, минимальный8-byte aligned reserve **3232B**. Резерв8192B превышает это число на4960B; это арифметика модели, а не measured high-water или инженерная гарантия будущего запаса.

Сохраняются все условия: настоящий reset в privileged Thread/MSP, STKALIGN, без bootloader handoff; только равноприоритетные USB HP/LP configurable IRQ с реальным outer PRIMASK guard; NMI/HardFault терминальны с0 software frame; успешные stacking/unstacking и исправная SRAM; нет arbitrary code/data/MSP corruption, ошибочного DMA за пределами массива, RTOS/foreign ISR, external debugger/trace/DebugMonitor, concurrent diagnostic writer или изменений code/priority/callback registrations. 32-byte basic exception frame и alignment semantics основаны на [ST PM0056 Rev7, §§2.3.5–2.3.7](https://www.st.com/resource/en/programming_manual/pm0056-stm32f10xxx20xxx21xxxl1xxxx-cortexm3-programming-manual-stmicroelectronics.pdf).

Не выполнены full production recompile всех30 units, MCU execution, физические clock/SRAM/ADC/USB, HOST delivery, WCET/aggregate service, qualification1ms, software-ready/low-voltage/ignition приёмка, BIN/HEX экспорт или flash. Конечное слово NEVER_FLASH и false flags остаются обязательными для текущего draft.

## Рубрика и вывод

C1=9.6(30%): точные board/string/timing/ABI/source/image bindings и новая conditional bound независимо проверены; source-backed callback closure не является общей формальной C/memory-safety верификацией.

C2=9.6(20%): активированные DTR/HSE и существующие cleanup/error/exception paths входят в актуальный граф; внешние hardware/stacking/debug условия сохраняются явно.

C3=9.8(25%): полный личный frozen replay, свой альтернативный lexer/topological расчёт, ELF data decode, source archive comparison, ABI recompile и дополнительные board-data rejects. Аппаратное исполнение не подразумевается.

C4=9.6(15%): явный image-specific checker/registration inventory; hardcoded addresses и ручные исключения требуют нового ревью при любом изменении ELF.

C5=9.6(10%): immutable234-file seal и exact hashes/команды, сохранённые отказы; hermetic full compiler-host rebuild не заявлен.

Итог **2.88+1.92+2.45+1.44+0.96=9.65**; **CROSSCHECK PASS** только указанной conditional exact-image области. P0/P1 нет в этой области. Переход к физическому опыту/kit требует остальных открытых условий и полномочий; этот отчёт их не предоставляет.

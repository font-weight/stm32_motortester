# IO-001: ARM object/ABI/stack evidence

10.10.2026. Проверены текущие C/H ADC-control с ограничением корреляции ms/us,
плюс неизменённый RX stream из кандидата CTRL-001. Точные SHA-256, Git blob SHA
и происхождение 20 файлов: [source-manifest.json](source-manifest.json).
Это проверка кросс-компиляции и частичный расчёт ресурсов, без полной firmware
или аппаратного исполнения; независимый review/score этим отчётом не заменяется.

## Итоги

- Две чистые сборки GCC 14.2.1: побитно одинаковые implementation objects,
  ABI-probe, combined relocatable object и `.su`: [reproduction.json](reproduction.json).
- Cortex-M3, Thumb, soft float, C11, `-Os -ffreestanding -fno-short-enums
  -fstack-usage`, strict warnings как errors. Все команды exit 0.
- Сумма `.text*`/`.rodata*` 11 281 B; `.data`/`.bss` модулей 0/0 B.
  Глобальные контексты ещё определит вызывающая прошивка, поэтому нулевой `.bss`
  объектов не означает отсутствие затрат RAM.
- Один owner: control 8 888 B + ADC driver 860 B + binding 24 B + RX stream
  564 B + RX message 544 B = 10 880 B. Embedded TX/session/link/acquisition
  повторно не прибавляются. Driver уже содержит DMA ring 512 B и snapshot 256 B.
  Локальный block descriptor находится в `.su` service и повторно не добавляется.
- Известная Stop-цепочка: receive 640 + link Stop 1 696 + TX Stop 592 + append
  16 = 2 944 B. Все три перехода — non-tail calls; кадры существуют одновременно.
  2 KiB для неё недостаточно ещё до runtime/caller/IRQ.
- Owner + эта цепочка = 13 824 B. Арифметический остаток 6 656 B от 20 KiB SRAM
  не включает неизвестные затраты. Условный стек 4 KiB оставил бы 5 504 B,
  но достаточность 4 KiB не доказана.
- Оставшиеся runtime symbols: memcmp, memcpy, memmove, memset.
- Combined object SHA-256:
  `ae149ec3c091cf67d7ba3a94b5b3f16d2905b8b772f721ff106cf1957a9a9e2b`.

[summary.json](summary.json) содержит расчёты; [report.json](runs/run-1/report.json)
содержит flags, версии, размеры всех модулей/типов/кадров.
[stack-chains.json](stack-chains.json) фиксирует граф, цепочки, assumptions
о bound callbacks и неизвестные затраты. [abi-symbol-sizes.txt](runs/run-1/abi-symbol-sizes.txt)
получен ARM nm; probe не входит в combined object. [live-source-check.json](live-source-check.json)
подтверждает совпадение финальных origin files с измеренным snapshot.

## Метод и воспроизведение

Библиотеки runtime не линкуются. `ld -r` создаёт только relocatable object,
без startup/vector table, адресов Flash/SRAM, link-time GC и полной карты памяти.
Стек считается по `.su` и relocation call graph; tail frames складываются
консервативно. Inlined функции уже учтены в содержащем их кадре. В известном
графе нет рекурсии. Неизвестные libc, timebase/USB callbacks и внешние callers
не считаются нулевыми и не закрываются этим расчётом.

Скрипты [build_evidence.py](build_evidence.py) и [summarize.py](summarize.py)
сохранены без изменения метода. В отдельной папке воспроизведения расположить
их вместе с source-manifest.json, затем скопировать перечисленные C/H из
проверяемого checkout в `sources/<path>`, **проверив каждый SHA-256**.
Не использовать `freeze`, которое ссылается на исходные рабочие папки автора.
Указать в TC путь к сохранённому или эквивалентному GCC/binutils/newlib headers;
версии/происхождение — [toolchain-provenance.md](toolchain-provenance.md).
Далее `python3 build_evidence.py run-1`, `python3 build_evidence.py run-2`,
`python3 summarize.py`. Запуски требуют новых несуществующих run directories.
Все сохранённые команды — [commands.log](runs/run-1/commands.log).

Публикуемый набор содержит текстовое evidence; объектные файлы, archive и
исполняемые toolchain binaries в PR не добавляются. Полный локальный пакет
дополнительно содержит frozen sources, оба набора .o/.su и дизассемблирование.

Открыты: полная ELF/BIN/HEX/linker map, startup/USB/clock-timebase glue, runtime
и callback code/stack, внешние RAM objects, IRQ nesting/hardware exception
stacking, измеренный stack watermark, WCET/service <2 560 us, работа платы.
Приёмка MVP-F01/PERF-03 и software-ready этим частичным свидетельством не закрыта.

# Единый исходный черновик прошивки

Статус: локальный draft, без выпуска или допуска к прошивке платы.
База: PR72 `adabc2044a3a376056d6756e67bede05c0726440`.

Здесь объединены ранее раздельно проверенные acquisition/protocol/control,
ADC003, TX002, runtime, IRQ guard, USB005, descriptors, freestanding memory,
CLK001, BOOT001–004, IRQ002 и TIME002. Исходники компонентов перенесены
побайтно; их независимые заключения не заменяют приёмку всей композиции.

## Реально выполненная проверка

10.10.2026 автор собрал из этого дерева 32 C translation units и reset assembly
в ARM objects. Все 33 objects побайтно совпали с предыдущей проверенной
BOOT004-композицией. Выполнены исходный BOOT input/vector audit и строгая
relocatable link с `--fatal-warnings`.

Секции combined.o: text 32329 B, data 122 B, bss 12238 B. Это размер
relocatable sections, **не итоговое размещение Flash/SRAM**. Остались ровно
`mt_board` и шесть предусмотренных linker symbols. Локальный журнал сохраняет
точные argv, SHA256, зависимости и результаты, включая две неудачные попытки
рецепта: отсутствовавший промежуточный каталог и отличавшийся способ сборки
reset. Они не скрыты и не считаются успешными проверками.

Сборка использовала явный список файлов и `firmware/boot/tusb_config.h`.
Поиск конфигурации по одному basename недопустим: в архиве зависимостей
также сохранена историческая test-only конфигурация.

## Зависимости и ограничения сборки

Неизменный upstream находится в `firmware/third_party/usb/vendor`.
Два source overlays строятся существующими patch helpers, upstream не меняется:

1. usbd.c: setup accounting patch, затем USB005 core patch.
2. dcd_stm32_fsdev.c: F103 reset cleanup patch, затем USB005 fsdev patch.

Промежуточные и конечные результаты требуют разных sibling-каталогов;
родительские каталоги создаёт вызывающий рецепт. Конечные SHA256:

- usbd.c: `f500d3477ef628c15e8ebb117570da64d34d4b6d638cfa47886422b9a04091fe`
- dcd_stm32_fsdev.c: `d46344c2dea3c7eceaa4a1042bd179ef3eda0e835efdf3ae3763f18db44961d8`

Текущая проверка использует GCC 14.2.1 для Cortex-M3/Thumb/soft-float, `-Os`,
`-fno-short-enums`, freestanding, function/data sections; C assembly metadata
создаётся с `-Wa,--noexecstack`. MEM001 дополнительно требует `-fno-builtin`
и `-fno-tree-loop-distribute-patterns`. Vendor компилируется как GNU11;
известные conversion warnings DCD/fsdev не скрываются.

Добавлен отдельный draft object-build entry point: `../build/README.md`.
Авторский запуск и локальное независимое ревью relocatable-only рецепта
завершены; package/tree приёмка остаётся открытой. Он не выпускает
production ELF. Исторические команды из dependency evidence не заменяют этот рецепт.
При финальной линковке обязательны input/vector и final ELF audits,
`--no-gc-sections`, `--orphan-handling=error` и явный резерв стека.

## Что остаётся открытым

`mt_board` намеренно не определён. Его значения требуют паспорта конкретной
платы: HSE, pull-up, питание/ток USB, разрешённые VID/PID и физический serial.
Нулевой тестовый fixture не является конфигурацией устройства.

Независимый критик подтвердил условную оценку стека 3016 B для прежней
точной Os-композиции; production reserve и инженерный запас не выбраны,
привязка к будущему final board image остаётся обязательной. Реальные IRQ/timing, enumeration, ADC и low-voltage
проверки не выполнялись. `host/**` здесь не изменён. Требуются согласование
сквозного контракта, проверка Windows-пакета и применимые независимые вердикты.

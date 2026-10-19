# Условное статическое ревью STREAM ELF

Это компактный отчёт и воспроизводимый тестовый пакет для конкретного
синтетического ELF. Три текстовых файла: этот отчёт,
tests/stream-static-proof/reference.json и replay_frozen_reference.py.
Замороженный архив tests/stream-static-proof/stream-static-proof-1480750b-r2.tar.gz
сохранён рядом, чтобы другая команда могла выполнить независимый replay.
Внутренний ELF явно NEVER_FLASH: это fixture статической проверки, не файл
для загрузки в пользовательскую плату. Board input синтетический; BIN/HEX нет.

## Область и результат

Независимый от реализации критик проверил точный ELF SHA256 1480750bf9802e90a543369f03922ff060dd61c42fde7bfaf1b8eca19bd60bb2. Исходная машинная база — commit 3caaa9106a3b6356e471c5e03ae0594e20472f7a; итоговый рецепт — 01824efd2571d5b2af8ab8fb4360cda8eb6c372d. Все 29 упорядоченных object hashes и ELF идентичны. Здесь оценивается точный образ, а не весь diff, PR или выпуск.

Условная консервативная граница: 2792 байта foreground + 264 байта software USB IRQ + 3 × 36 байт аппаратных exception frames = 3164 байта; округлённый резерв — 3168 байт. Это верхняя оценка, не измеренный либо доказанно достижимый high-water mark. Старые границы 3064/3120 байт не переносились.

Проверены 335 тел кода, 333 статических .su frames, 99 register-indirect sites, шесть switch tables и 1022 call/tail edges. Зарегистрированные callback targets и три исключения перечислены в полном отчёте; рекурсии, несовместимых SP joins, невыравненных call sites и невосстановленных return/tail frames не найдено. Единственные configurable IRQ roots — USB HP/LP одного приоритета; дополнительно учитывается терминальный HardFault → NMI.

Обязательные условия: точные code/config/registrations; настоящий reset и privileged Thread/MSP; STKALIGN; описанный набор IRQ и PRIMASK dispatcher; успешное архитектурное stacking/unstacking и исправная SRAM; отсутствие произвольного memory/DMA corruption, внешней смены приоритетов/векторов, RTOS и debugger/instrumentation. Условие не означает, что физическая плата уже проверена.

Рубрика подробного независимого ревью: C1 9.7, C2 9.6, C3 9.6, C4 9.5, C5 9.6; итог 9.615. PASS относится только к указанному условному static stack/callback/ABI результату. Отдельная независимая перепроверка инструментов и общая приёмка оформляются отдельно. Hardware, квалификация provisional 1 ms TX bound, ADC/IRQ/WCET и aggregate scheduling остаются BLOCKED_EVIDENCE. Firmware release, BIN/HEX и flashing не разрешены.

## Доказательства и replay

reference.json содержит точные SHA256 архива, manifest, verifier, полного отчёта, ELF и objdump. Архив stream-static-proof-1480750b-r2.tar.gz находится в tests/stream-static-proof/. Проверить SHA256 архива из reference.json и извлечь его вне репозитория. Затем из корня репозитория:

    python3 -I -B tests/stream-static-proof/replay_frozen_reference.py \
      --proof-dir '/полный/внешний/путь/portable-proof' \
      --objdump '/полный/путь/arm-none-eabi-objdump'

Entry point сверяет точные bytes перед исполнением внешнего pinned verifier. Replay проверяет 29 объектов и 72 source dependencies, заново дизассемблирует ELF, восстанавливает граф и стек, запускает отрицательные controls. Он пишет результаты только во внешний каталог доказательств. Требуются Python 3 и pinned ARM objdump; production build, target execution и сеть не нужны.

В первом архиве найден P1: Python -O/-OO отключал assert и мог ошибочно выдать PASS, в том числе с /usr/bin/true вместо objdump. Этот архив отозван как окончательное свидетельство verifier. Во втором архиве каждый исполняемый proof script до file I/O явно отвергает -O/-OO и отсутствие -I/-B; 32 настоящих отрицательных запуска подтверждают отказ без изменения output files. Исходные graph/math и ELF не менялись. Новый образ или изменение callbacks/config/roots требует нового анализа и независимого ревью, а не простой замены SHA в reference.json.


## Независимая перепроверка и интеграция

Второй независимый Critic-A повторил r2 replay и собственный разбор всех
335 executable sections, 99 indirect sites, 6 switches и 1022 edges.
Условная граница 3164/3168 байт подтверждена; отдельный scoped PASS 9.65/10.
SHA-256 его полного отчёта: 625d214000ad124cf358d4f15a20f110c34fc9080a101e34d8fd49232326cd12.
Root также выполнил полный replay через включённый entry point после извлечения
сохранённого r2 архива во внешний каталог: 29 objects, 333 frames, 72 source
dependencies и все пять scripts завершились успешно.
Эта перепроверка не изменяет старые build receipts и их false qualification flags.
Инструментированный диагностический профиль является другим ELF и требует
своего stack/callback анализа; текущая граница на него автоматически не переносится.

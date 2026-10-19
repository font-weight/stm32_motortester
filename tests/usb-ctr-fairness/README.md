# Native-проверки opt-in CTR fairness

Эта переносимая проверка не включает копии firmware/vendor и исторические логи.
Она читает явно указанный корень репозитория либо source export, проверяет каждый
используемый исходный файл по `source-pins.json`, затем применяет штатную цепочку
reset → USB005 → CTR fairness исключительно в новом внешнем каталоге результата.
Существующий результат не перезаписывается. Входные hashes проверяются повторно.

`source-pins.json` фиксирует входы из
`bbd10095cbeb798232c3a1f82a93795a6c053586`. Более поздний commit допускается только
при побайтном совпадении всех перечисленных входов. Поэтому добавление самих
тестов/документации не требует выдумывать циклический commit hash. Изменение
проверяемого исходника требует явного обновления pins и повторного ревью.

## Запуск из корня репозитория

Требуются Python 3.12+, GNU patch, native GCC. Для ARM-проверки нужен уже готовый
ARM GCC с newlib headers. Скрипты ничего не устанавливают и не скачивают.

    python3 tests/usb-ctr-fairness/run.py --repo . --output /tmp/ctr-model-new --toolchain /absolute/toolchain/usr
    python3 tests/usb-ctr-fairness/adversarial.py --repo . --evidence /tmp/ctr-model-new --output /tmp/ctr-adverse-new

Использовать новые output-каталоги вне исходного дерева. Путь с пробелами
допустим. Вместо `--repo .` можно передать абсолютный путь к export с теми же
`firmware/...` путями. Путь к репозиторию не выводится из расположения harness,
рабочей директории или прежних каталогов reviewer.

Без `--toolchain` ARM-проверка не выполняется, что отражено `arm_compiles: 0`.
Python -O/-OO отвергается до создания output: assertion-based проверки
свидетельств нельзя отключать. Output symlink также отвергается.
Sanitizers включены по умолчанию и завершают процесс при первой ошибке. `--no-sanitize` разрешает явно исключить их;
такой запуск не является полной матрицей.

## Что исполняется

`tools/extract.py` использует функции fail-closed extraction из `extract_base.py`,
унаследованного reset harness. Он извлекает 30 настоящих функций исправленного
DCD/FSDEV: 28 тел побайтно совпадают с исходниками; в ISR и rearm заменены только
чтения/записи ISTR на модельные access shims. В `extraction-manifest.json`
сохраняются SHA исходных файлов и raw/emitted SHA каждого тела, число access
shims и hash state declarations. ISR извлекается между уникальными границами
раздела, сохраняя обе альтернативные compile-time ветки `while` буквально.
Это не независимая реализация обработчика под видом тестируемого кода.

Среда в `src/preamble.h` моделирует EPnR rc_w0/toggle, derived ISTR.CTR/EP_ID,
ISTR rc_w0, PMA/BTABLE, FIFO-copy и event sink. Не моделируются USB/APB timing,
NVIC, sparse physical PMA spacing, настоящая TinyUSB очередь или PRIMASK.

38 включённых сценариев проверяют две pending endpoint, все 16 сочетаний RX/TX,
блокировку повторного CTR до foreground grant, lifecycle/reset при deferred,
отказы rearm и прибытие flags после sample, SETUP, FIFO/multipacket state,
запрет ISO/unsupported generic open без побочных эффектов и event burst.
Три legacy/default/explicit0 профиля проверяют прежнее полное обслуживание CTR
и доступность ISO API. Каждый профиль выполняется normal и ASan+UBSan.

Дополнительно: десять applier-integrity случаев, пять compile-time отказов
неподдержанного профиля и одиннадцать намеренно нарушенных behavioral вариантов.
Каждая мутация должна сначала успешно компилироваться, затем быть обнаружена
поведенческим assertion. `adversarial.py` проверяет pins входов и SHA исправленного
DCD в переданном evidence, не доверяя произвольному файлу с похожим именем.

## ARM и свидетельства

При `--toolchain` компилируются четыре полные ARM translation units: reset+USB005
baseline, новый default, explicit0 и enabled. Применяются Cortex-M3/Thumb/soft,
GNU11 -Os, function/data-sections и Wall/Wextra/Werror. Объекты первых трёх
вариантов должны быть побайтно одинаковыми. Сохраняются commands, hashes,
symbols, disassembly и local stack-usage. Это compile-only проверка полных TU,
не полный link, не firmware image, не исполнение ARM и не аппаратный опыт.

В новом output сохраняются `summary.json`, `native-results.json`,
`patch-integrity.json`, `arm-results.json` (если ARM выполнен), `commands.json`,
`input-hashes.json`, `harness-hashes.json`, extraction manifests и build files.
Adversarial output отдельно содержит `results.json` и команды. Репозиторий не
содержит утверждения, будто внешние reviewer-папки или старые логи поставляются
вместе с этими тестами.

## Ограничения вывода

Rearm true не является atomic lifecycle/epoch certificate: новый flag после
ISTR sample может оставить true и должен быть обработан следующим IRQ.
Вызов требует настоящего save/restore guard и отдельной admission дисциплины.

Один endpoint quantum может произвести два CTR event плюс lifecycle; модель
coexisting SOF/WKUP/SUSP даёт пять enqueue-вызовов. Это не доказательство
очереди. Нужны проверенные bounded drain/occupancy правила, ADC/TIME-first
service, обработка poison/потери enqueue и сохранение epoch admission.

Успех native/ARM compile проверок не устанавливает aggregate IRQ/WCET bound,
latency, sustained Windows throughput, физическую отмену транзакций, правильность
HP/LP/NVIC конфигурации или hardware qualification. Feature по умолчанию выключен;
данный пакет не включает runtime activation.

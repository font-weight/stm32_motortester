# Независимые native-проверки ADC/DMA stop certificate

Уровень: программа и регистровая модель. Эти проверки не доказывают аппаратную остановку DMA, дренирование всех запущенных преобразований ADC, отсутствие физических потерь, USB, WCET или пригодность нового образа для платы.

Пакет написан отдельным тестировщиком, который не изменял production. Проверяемый production-срез: `b6d127385e0a666e8ea78b7b872b9c638befb826`. SHA-256 исходников и тестов приведены в `provenance.json`. При переносе пакета в репозиторий запускать его на итоговом commit заново.

## Запуск

Требуются Python 3 и C11-компилятор с AddressSanitizer/UBSan; использован GCC 14.2.0.

Из любого каталога:

    python3 path/to/this/package/run.py /absolute/path/to/source-root --output /absolute/path/to/results

Без `--output` runner создаёт уникальный временный каталог вне пакета и печатает его путь. Явно заданный output обязан отсутствовать; оба runner отклоняют уже существующий output, сохраняя прежние свидетельства.

Корень исходников содержит `firmware/platform` и `firmware/app`; `.git` не требуется. Runner компилирует production отдельно от модели, запускает 25 групп в strict C11 `-O2` и ASan/UBSan `-O1`, затем отдельный тест production `mt_adc_control.c`. Санитайзеры работают с `-fno-sanitize-recover=all`, `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1` и `UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`. Ошибка санитайзера завершает тест с ненулевым кодом. LeakSanitizer выключен из-за ограничения ptrace; проверка утечек не заявляется. `CC` позволяет выбрать компилятор. `--only` запускает только указанные driver-группы, без legacy-теста.

Для неизменяемого source-only среза сначала экспортировать нужную ревизию:

    mkdir -p /absolute/path/to/export
    git -C /absolute/path/to/repo archive b6d127385e0a666e8ea78b7b872b9c638befb826 firmware | tar -x -C /absolute/path/to/export
    python3 path/to/this/package/run.py /absolute/path/to/export --output /absolute/path/to/results

Runner сохраняет manifest SHA-256 всех `.c`/`.h` в `firmware` до запуска и сверяет его после завершения. Изменившийся во время проверки source tree завершает runner с ошибкой. Для утверждения о конкретном commit нужен экспорт именно этого commit, а не только неизменность worktree.

## Что проверяет модель

`test_stop_certificate.c` содержит собственную минимальную 32-bit регистровую шину и прямой счётчик выполненных DMA-переносов. Код драйвера и его формулы в oracle не включаются. DMA-модель последовательно записывает кольцо, уменьшает CNDTR и выставляет события на пересечении границ; ожидаемый хвост выводится из исходной последовательности переносов.

- 1536 положительных случаев: обе фазы, хвосты 0..255, три положения по числу полных оборотов
- Все 4128 сочетаний двух фаз, восьми наборов HT/TC/TE и CNDTR 0..257: 512 допустимых покрыты положительным oracle, остальные 3616 обязаны вернуть unknown
- Копия данных в памяти вызывающего, правильный порядок после кольцевого перехода, повторный stop после очистки flags, новый START и новая нумерация
- Поздние переносы перед каждым этапом disable; изменение count/flags, потеря hardware-off до, во время и после capture/status clear/copy
- Игнорируемые TIM/ADC disable, stuck DMA EN, конечный poll cap при замершем времени, gates и reset
- Первая причина независимо от ошибки cleanup: TE, неверный/двойной HT/TC, clock, deadline, внешняя потеря достоверной истории, failed START и повторный stop
- Независимость source cursor от 32-bit границы; точный uint64 endpoint, переполнение непосредственно при stop и при read_block
- Начальное unowned/off требует реальных чтений; unowned/active не присваивается и не изменяется
- Invalid code во второй части хвоста не публикует частичный результат

Для хвоста 255 образец содержит EMPTY-проверку при 127 переносах непосредственно перед следующим переносом. Оставшиеся 128 переносов и stop попадают внутрь заданного `<2560 µs` service-budget модели. Это не измерение времени на STM32.

`test_legacy_narrowing.c` компилирует настоящий `mt_adc_control.c`, подставляя только явные dependency seam doubles. Они подают metadata блока и считают вызовы sample/recover. Сам service, проверка диапазона и сужение не подменены. Проверяются все три init с включённым stream mode, допустимые u32 границы и отклонение переполнения до первого sample. Такой тест отдельно от регистрового пути упражняет защиту, до которой legacy-лимит самого драйвера обычно не даёт дойти.

## Проверка чувствительности

    python3 path/to/this/package/mutations.py /absolute/path/to/export --output /absolute/path/to/mutations

Девять локальных мутаций должны успешно скомпилироваться и быть отвергнуты назначенным тестом: offset хвоста, exclusive end, потеря history, перезапись first cause, отсутствие stop-overflow guard, отсутствие code validation, ложный hardware_off, потеря pending-event cause, отсутствие legacy narrowing guard. Скрипт меняет только файлы в указанном output; production не меняется. Эти выбранные отрицательные контроли не заменяют полное mutation coverage.

## Границы

Здесь нет настоящего `mt_live_device` adapter, wire/queue/USB-композиции, ARM link/map/stack измерений или hardware-тестов. Известный `dma_end` означает только endpoint завершённых DMA-переносов при условиях драйвера; незавершённое ADC-преобразование может быть оборвано. Результаты не являются заявлением о lossless acquisition.

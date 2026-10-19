# Явная декларация completed-DMA: синтетический HOST-профиль

Ограниченный локальный successor базы
`e86759a011b93a12a42542cfc9ba8149c673ff86`, tree
`af1ad3f01112a4d57cf885e12abf1ae5e59ad80c`, для issue191.
Требуется независимый Critic-B ≥9,5 на точном freeze; этот текст не выдаёт PASS.
Физический firmware profile не зарегистрирован, real-port activation этим
изменением не разрешается. Старый entrypoint, serial implementation, wire,
прошивка, CI, упаковка и приостановленный demo package не изменены.

Нормативный смысл: PR200
`c2e37d63dbea83131e9512e90f3bf1812c2e30f7`, tree
`f59a80a1ba273ccd917478e4df0a76753b9a9d6c`,
`protocol/wire-v0.2-stream.md` SHA-256
`24b60472a9e1a8a7610e40a6e8d1bedd96dcbe55e89a3c4aec983bf9b1d5d9ab`.
Файл протокола не копируется и не редактируется этим HOST successor.

## Единственная декларация и её граница

`stream_provenance.SYNTHETIC_DMA_PROFILE` — frozen/slots `ProducerProfile`:

- ID `syn1`, origin `synthetic`, config 1, номинальная Fs 50000 Hz;
- domain `completed-dma-v1`, revision `0.2-draft-dma-sequence-20261019`;
- source Git `e86759a011b93a12a42542cfc9ba8149c673ff86`;
- artifact: точный **Python test fixture** `host/tests/test_stream_bridge.py`
  этого commit, SHA-256
  `c5377feac3cfbd97c5eef5fe2a82d772c0031d6a50e051ae1de24cf16ef7c0ca`;
- ожидаемый HELLO build ID `00001234`; CAPS ровно
  `(config=1, profile=1, channel=0, adc_bits=12, fs_num=50000, fs_den=1,
  max_block=128, flags=1)`.

Это явная **тестовая интерпретация** fixture counters. Fixture не становится
прошивкой, а исходный default этого старого fixture не переименовывается.
Декларация связывает выбранные артефакт/исходник/профиль/build ID; она не
доказывает, что подключённое устройство исполняет этот код. Хеш в журнале —
заявление о выбранном артефакте, не результат чтения flashed binary.
HELLO build ID также не аутентификация. Нет загрузки firmware, сетевой проверки,
runtime registration, device enrollment или mutable registry.

Вызов `StreamBridge(..., producer_profile=SYNTHETIC_DMA_PROFILE)` либо
`StreamController(..., producer_profile=SYNTHETIC_DMA_PROFILE)` выбирает эту
декларацию явно. Любой другой объект/tuple полей отвергается. До START и
подготовки файла должны совпасть build ID и CAPS. Controller сохраняет причину
ошибки допуска, ограждает попытку и не запускает автоматическое восстановление.
Для `SerialStreamPeer`, включая managed mode, этот synthetic profile всегда
отвергается до открытия/admission. Профилей физического прибора сейчас **нет**.
Для будущего артефакта A нужен отдельный точный reviewed source/image manifest
и новая разрешённая декларация; совпадение build ID не подставит её автоматически.

Отсутствие нового параметра (`None`) сохраняет прежнюю generic/synthetic
интерпретацию `0.2-draft-081721`. Это не допуск нового DMA-домена. Ни версия
wire 0.2, ни build `00001234` сами по себе не выбирают `syn1`.

## Каноническая запись в существующих полях

Schema, лимит 64 символа на каждую строку и остальные capacities не меняются:

1. `protocol_revision` (48 символов):
   `0.2-draft-dma-sequence-20261019|completed-dma-v1`.
   Оба литерала обязательны и проверяются как один точный tuple component.
2. `firmware_version` (64): lowercase hexadecimal SHA-256 выбранного artifact.
   У `syn1` это Python fixture, **не firmware image**.
3. `application_version` (63):
   `producer:syn1:e86759a011b93a12a42542cfc9ba8149c673ff86:00001234`.
   Namespace `producer:` явно переопределяет смысл этого carrier: это producer
   profile/source/expected-build, **не HOST application version**.

`StreamIdentity.__post_init__` проверяет новую tagged декларацию целиком,
включая origin/config/Fs. Неизвестный/отсутствующий компонент, другой домен,
источник, artifact, build или malformed tagged value не становится legacy.
`profile_for_identity` возвращает None только для прежней untagged области.
Ни поле, ни его размер не добавляются. Файл хранит эту identity уже в preparation,
затем в выбранной границе и терминале там, где это предусмотрено исходной schema.
Без R декларация остаётся в preparation; неизвестные endpoints остаются None.

`recover`/`recover_attempt` используют только сохранённую identity. У них нет
параметра для подмены текущим профилем. Смена активного источника не меняет файл.
Повреждённая tagged identity отбрасывается даже с пересчитанным SHA header.
Это проверка консистентности, не защита от злоумышленника, способного переписать
весь файл в другую декларацию или заменить Python-код процесса.

Старые v1/v2, untagged metadata и неизвестные старые terminal сохраняют свой
смысл и читаемость. Они автоматически не приобретают completed-DMA semantics.
Неизменённый старый reader может структурно читать новые ограниченные строки,
но **старый viewer не является domain-aware**. Для оценки новой декларации
следует использовать текущий reader/viewer и его явные ограничения.

## Что означают индекс, чистое завершение и время

В объявленном домене индекс ноль — первая сертифицированная завершённая
DMA-запись после successful driver START, включая записи до START_ACK.
ACK или Record не обнуляют этот счётчик. Record выбирает сохранённый `[R,E)`;
полный source-prefix CRC и выбранный raw-префикс остаются различными.
Синтетический тест готовит значения до ACK, передаёт DATA после ACK и отдельно
выбирает R=256. Это тест HOST-отображения индексов, не опыт с DMA.

Known `produced_end - prefix_end` считает только завершённые DMA-записи,
отсутствующие в сериализованном непрерывном source prefix. Чистое завершение
заявляется только в `completed-dma-v1`. Оно не учитывает перезапись ADC_DR до
DMA-read, запущенные, но недренированные/не завершённые преобразования,
незавершённые DMA requests, пропущенные триггеры или физические временные пробелы.
Оно не доказывает hardware quiescence, успешный all-conversion drain или полный
разряд. Любые fault/incomplete/unknown и отсутствие подходящего STOP_ACK
по-прежнему запрещают clean даже при равных известных counters.

Viewer показывает полный профиль и его scope для live, записи, пустой записи
и no-R попытки; подписи source loss не становятся total host/file/physical loss.
Шкала — индекс / номинальная Fs, не измеренное или калиброванное физическое время.
Подписи прокручиваются при маленьком окне/крупном шрифте. Reopen не восстанавливает
утерянный факт fsync-return и не превращает читаемые байты в квитанцию durable I/O.

## Воспроизводимые ограниченные проверки

Из корня checkout, Python 3.12, без установки пакетов:

```sh
PYTHONPATH=host:host/tests python -m unittest -v test_stream_provenance
PYTHONPATH=host:host/tests python -O -m unittest -v test_stream_provenance
PYTHONPATH=host:host/tests python -m unittest -v test_stream_recording.ModelTests \
  test_stream_recording.FileTests.test_independent_fixed_vector_and_partitioned_reads \
  test_stream_recording.FileTests.test_fresh_process_roundtrip_and_first_cause \
  test_stream_recording.FileTests.test_header_wrong_version_unsafe_schema_and_duplicate_fields
```

Для Qt использовать уже подготовленные зависимости `host/requirements-gui.txt`,
добавив их каталог в PYTHONPATH; выполнять normal и `-O` **последовательно**:

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH="$GUI_DEPS:host:host/tests" python -m unittest -v \
  test_gui_stream.StreamPanelTests.test_declared_dma_live_bound_empty_no_r_and_legacy_reopen \
  test_gui_stream.StreamPanelTests.test_v2_session_source_counts_and_first_cause_remain_separate \
  test_gui_stream.StreamPanelTests.test_recent_live_extended_labels_reachable_at_default_and_large_font
```

Проверки пишут реальные временные journals и читают их новым процессом. Они
покрывают clean/known loss/unknown, первый отказ, missing STOP_ACK, empty/no-R,
подготовку без START, все элементы декларации, rehashed bad header, старый default
и UI после смены источника. Это Linux synthetic/software evidence; Windows,
физический firmware/ADC/DMA/USB, физическая Fs и полный разряд не проверяются.

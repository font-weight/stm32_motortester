# Явная декларация completed-DMA и синтетическая репетиция `str1`

Ограниченный локальный successor для issue191. Принятое основание и
непосредственный parent — `db124e280ca020dd6af8356eadd2fdd45a7f9ae2`, tree
`63a88e0b15b63da601642ba87dacdb7e612d9d66`, равный полному дереву публичного
PR208 `1a5f0920acd1f0939fb33ee7d86e4f93612c4944`. Будущий публичный commit
не заявлен. Требуется независимый
Critic-B ≥9,5 на точном новом freeze; этот текст не выдаёт PASS.
Физический firmware profile не зарегистрирован и real-port activation не
разрешается. Новая декларация допускается только как явная fake-репетиция.
Wire, endpoint, journal schema, GUI, прошивка, лицензии, CI, упаковка и
приостановленный demo-package review `a214` остаются вне scope. Прежние freezes
и свидетельства сохраняются.

Нормативный смысл: PR200
`c2e37d63dbea83131e9512e90f3bf1812c2e30f7`, tree
`f59a80a1ba273ccd917478e4df0a76753b9a9d6c`,
`protocol/wire-v0.2-stream.md` SHA-256
`24b60472a9e1a8a7610e40a6e8d1bedd96dcbe55e89a3c4aec983bf9b1d5d9ab`.
Файл протокола не копируется и не редактируется этим HOST successor.

## Прежняя декларация `syn1`

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

Это прежняя явная **тестовая интерпретация** fixture counters. Fixture не
становится прошивкой, а его исходный default не переименовывается.
`admit_profile` по-прежнему принимает только точный `syn1`, включая типы полей.
`StreamBridge(..., producer_profile=SYNTHETIC_DMA_PROFILE)` либо
`StreamController(..., producer_profile=SYNTHETIC_DMA_PROFILE)` выбирает его
явно. Build/CAPS должны совпасть до START и подготовки файла.
Для `SerialStreamPeer`, включая managed mode, `syn1` всегда отвергается до
открытия/admission. Новая репетиция не ослабляет этот запрет.

Отсутствие обоих параметров выбора (`producer_profile=None` и
`fake_diagnostic_declaration=None`) сохраняет прежнюю generic/synthetic
интерпретацию `0.2-draft-081721`. Ни версия wire 0.2, ни один build ID сами по
себе не выбирают DMA-domain или декларацию.

## Декларация `str1`: только для fake-репетиции

`STR1_DIAGNOSTIC_DECLARATION` — отдельный точный frozen/slots `ProducerProfile`:

- ID `str1`, intended origin `device`, config 1, номинальная Fs 50000 Hz;
- domain `completed-dma-v1`, revision `0.2-draft-dma-sequence-20261019`;
- compiled-local source `31b93659164dec376bea5b973b597196c6a4a0ad`;
- заявленный BIN SHA-256
  `90fee8a085d1e561cc6f5c28c26db26a43c1e57145ae41314bf70e9422c7b904`;
- ожидаемый HELLO build `0x53545231`; ожидаемые CAPS
  `(1, 1, 0, 12, 50000, 1, 128, 1)` в порядке config, numeric profile,
  channel, ADC bits, Fs numerator, Fs denominator, max block, flags.
  Этот immutable tuple доступен как `STR1_DIAGNOSTIC_CAPS`.

Основание identity — [передача PR207](https://github.com/font-weight/stm32_motortester/issues/191#issuecomment-6271272908)
и [объявление CAPS](https://github.com/font-weight/stm32_motortester/issues/191#issuecomment-6271123729).
Независимый source/artifact report от 19.10.2026 (SHA-256
`08836312023e5fdede006f027231246d32b9e6deba9ae67a8b760ab2d22e54bf`)
сопоставил все 248 архивных source files, включая полный публичный firmware
subtree из 245 файлов, с публичными PR206/PR207. Compiled-local ID выше
сохранён буквально. Public-equivalent commit
`9ec9430fe83aa5e8ad9c504f9627e185f3285c36` записывается отдельно и не заменяет
его. Полный локальный Git tree `31b936` в проверке недоступен; его эквивалентность
остаётся заявленным provenance стороны A, а не независимо доказанным фактом.

Из file-backed load segments предоставленного ELF независимо получены 33628
байт с указанным BIN digest. Это вычисление derived identity:
**исходный BIN не был предоставлен или восстановлен**. ELF/source match не
доказывает исполнение образа, физическую Fs, USB enumeration, аппаратную
готовность или новую stack/ABI квалификацию. Source/ELF-derived HELLO/CAPS —
прогноз до принятого endpoint ответа. Ответ fake backend подтверждает только
репетицию. Образ остаётся **NOT_FLASH_APPROVED**.

`admit_fake_diagnostic_declaration` принимает только точную декларацию, включая
типы всех полей; совпадения лишь profile ID/build недостаточно. Выбор передаётся
через `fake_diagnostic_declaration` в `DiagnosticRun`, `StreamController` и
`StreamBridge`. Одновременный `producer_profile` запрещён.
Controller требует точный managed `SerialStreamPeer`, созданный с явным
`fake_backend_factory`; этот selector взаимоисключается с `backend_factory`.
Read-only `fake_backend_selected` показывает выбор. Plain/direct peer,
subclass, обычная injected factory и default/real backend не дают допуска:
ошибка возникает до worker creation/backend open. Прежний общий serial путь
`backend_factory` сам по себе не меняется.

Фабрика — договор с вызывающим кодом, не sandbox и не authentication boundary.
Репетиция использует прочитанные encoded fake fixtures; имя Python-функции не
доказывает отсутствие произвольного I/O. Нет runtime registry, автоматического
физического enrollment, port discovery, COM CLI, flash или telemetry.

`validate_fake_diagnostic_negotiation` вызывается bridge в существующем
`_reserve` по уже принятому endpoint `Negotiated`. Build и все восемь CAPS должны
совпасть до `plan_start`, generation reservation, session/writer и R.
Endpoint остаётся единственным владельцем parser/deadline/nonce checks;
нет вторичного разбора trace для управления или автоматического fallback.

`BridgeSnapshot.accepted_build_id` и `accepted_caps` показывают только
фактически принятые endpoint сведения, отдельно от ожидаемой декларации.
Они `None` до приёма и в новом bridge, неизменяемы в snapshot. В существующем
bridge их очищает только реально допущенный `Pending` HELLO; отклонённый HELLO
не стирает evidence текущей попытки. Сведения сохраняются в same-attempt
fault/terminal snapshot, привязанном к этой попытке; новый reset API не вводится.
Wire-валидные,
но отличающиеся поля могут быть показаны как фактическая причина несовпадения.
Успешный endpoint приём ответа не равен допуску декларации. Время snapshot —
время наблюдения, не вымышленное время каждого события. Collector сохраняет
ожидаемые/фактические сведения раздельно, а bounded metadata `rehearsal_notice`
объясняет synthetic/intended origin, NOT_FLASH_APPROVED, completed-DMA scope,
unknown end/loss, nominal-index time и отсутствие image authentication даже
при no-R результате: [diagnostic guide](stream-diagnostic.md).

## Каноническая запись в существующих полях

Schema, лимит 64 символа на каждую строку и остальные capacities не меняются.
Для обоих точных вариантов:

1. `protocol_revision` (48 символов):
   `0.2-draft-dma-sequence-20261019|completed-dma-v1`.
   Revision и domain проверяются как точный tuple component.
2. `firmware_version` (64): lowercase hexadecimal SHA-256 выбранного artifact.
   Для `syn1` это Python fixture, для `str1` — заявленный image digest;
   ни один вариант не означает чтение binary с устройства.
3. `application_version` содержит один из точных carrier:
   - `syn1` (63):
     `producer:syn1:e86759a011b93a12a42542cfc9ba8149c673ff86:00001234`;
   - fake `str1` (64):
     `rehearsal:str1:31b93659164dec376bea5b973b597196c6a4a0ad:53545231`.

Namespace переопределяет смысл этого поля: это selected declaration/source/
expected-build, **не HOST application version**. Для `str1` helper
`rehearsal_identity_metadata` создаёт exact carrier вместе с revision/domain
и artifact. Intended origin `device` остаётся у выбранной декларации;
**actual archived origin всегда `synthetic`**, run origin — `synthetic-fake`.
Это синтетическая репетиция выбранного образа, не device acquisition.

`StreamIdentity.__post_init__`/`profile_for_identity` проверяют tagged identity
целиком, включая origin/config/Fs. Для `str1` `profile_for_identity` распознаёт
только точный synthetic rehearsal tuple. Любой изменённый/смешанный/отсутствующий
компонент, malformed/unknown marker, неподдерживаемый `producer:str1` или
rehearsal с origin `device` отвергается; отмеченная новая декларация никогда
молча не становится legacy. None возвращается только для прежней untagged
области. Проверка консистентности не защищает от атакующего, способного
переписать весь файл в другую разрешённую декларацию или заменить Python-код.

Ни поле, ни его размер не добавляются. Identity сохраняется уже в preparation,
затем в выбранной границе и terminal, как предусмотрено прежней schema.
Без R декларация остаётся в preparation, sample interval не выдумывается.
`recover`/`recover_attempt` используют сохранённую identity без параметра для
подмены текущим профилем. Смена активного источника не меняет файл.
Повреждённая tagged identity отвергается и при пересчитанном header hash.

Новый reader сохраняет смысл и читаемость прежних v1/v2, generic/untagged,
`syn1` и неизвестных старых terminal. Они не получают `str1` semantics.
Обратная совместимость не симметрична: domain-aware readers до этой итерации
(`91778`/`db124`) **отвергают новый `rehearsal:str1` carrier**. Более ранний
структурный reader может сохранить строки, но это не понимание нового домена.
Для новой репетиции нужен обновлённый reader/viewer. Изменения schema version
для точной новой интерпретации не требуется.

## Что означают индекс, завершение и время

В объявленном домене индекс ноль — первая сертифицированная завершённая
DMA-запись после successful driver START, включая записи до START_ACK.
ACK или Record не обнуляют этот счётчик. Record выбирает сохранённый `[R,E)`;
полный source-prefix CRC и выбранный raw-префикс остаются различными.
Синтетические проверки с ненулевым R проверяют HOST-отображение индексов,
не физическую работу DMA.

Known `produced_end - prefix_end` считает только завершённые DMA-записи,
отсутствующие в сериализованном непрерывном source prefix. Для связанного
образа `str1` `produced_end` остаётся **неизвестным**: quiesce snapshot не
объявляет known produced end даже при стабильном completed-DMA prefix.
Выбор декларации, равенство видимых counters и matching STOP_ACK не создают
known end, zero loss или clean capture. END normalization не меняется.
Unknown END/incomplete, established empty `[R,R)` и no-established-R — разные
результаты. Для no-R нельзя создавать sample interval.

У `syn1` прежняя clean/known-loss интерпретация остаётся в `completed-dma-v1`.
Общий completed-DMA scope не включает ADC_DR overwrite до DMA-read,
запущенные, но недренированные/незавершённые преобразования, незавершённые DMA
requests, пропущенные триггеры или физические временные пробелы. Он не доказывает
hardware quiescence, all-conversion drain, trigger completeness или полный
разряд. Fault/incomplete/unknown и отсутствие подходящего STOP_ACK по-прежнему
запрещают clean даже при равных известных counters. Source-reported counters,
первый отказ и non-clean terminal semantics сохраняются раздельно.

Visible provenance для live/as-of report, записи, пустой записи и no-R попытки
явно показывает synthetic rehearsal, selected source/image/build и
NOT_FLASH_APPROVED. Source loss не становится total host/file/physical loss.
Шкала — индекс / номинальная Fs, не измеренное или калиброванное физическое время
и не доказательство равномерного physical timing. Reopen не восстанавливает
утерянный факт fsync-return; читаемые bytes не квитанция durable I/O. Runtime
receipt/export readback, RAM volatility и source hashes как export-time
observations сохраняют ограничения [diagnostic guide](stream-diagnostic.md).

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

Дополнительные `str1` controls используют только `test_stream_diagnostic`:

```sh
PYTHONPATH=host:host/tests python -m unittest -v test_stream_diagnostic
PYTHONPATH=host:host/tests python -O -m unittest -v test_stream_diagnostic
```

Проверяются каждый компонент/тип декларации и carrier, backend selection,
actual encoded build и все восемь CAPS, stale prior-attempt evidence,
unknown END/empty/no-R, finite timeout/transport failure, exact raw-byte
round-trip и fresh-process reopen с сохранением старых generic/`syn1` смыслов.
Предыдущие owned full-byte export/readback, substitution, quota,
unknown/partial-write и unresolved-close controls остаются обязательными.
Перечень команд и controls не заменяет журнал запуска на точном новом freeze
и отдельное независимое заключение.

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

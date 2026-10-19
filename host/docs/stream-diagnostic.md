# Opt-in диагностика потока: fake callbacks и сохранённое состояние HOST

Это отдельный source-only компонент для подготовки первого ограниченного опыта
issue191. Он не является готовым Windows GUI, пакетом, физическим сборщиком,
подтверждением работы USB/ADC/DMA или разрешением подключить прибор.

Принятое локальное основание и непосредственный parent этой итерации:
`db124e280ca020dd6af8356eadd2fdd45a7f9ae2`, дерево
`63a88e0b15b63da601642ba87dacdb7e612d9d66`, равное полному дереву публичного
PR208 `1a5f0920acd1f0939fb33ee7d86e4f93612c4944`. Будущий публичный successor
этим документом не заявлен.
Независимый Critic-B должен проверить точный новый freeze; наличие этого
документа не выдаёт PASS и не переносит прежний вердикт на новую реализацию.

Граница этой итерации — восемь файлов: пять модулей `stream_provenance.py`,
`stream_serial.py`, `stream_controller.py`, `stream_bridge.py`,
`stream_diagnostic.py`, `test_stream_diagnostic.py` и два guides
`stream-diagnostic.md`/`stream-provenance.md`. Добавляется только явно выбранная
fake-репетиция точной декларации `str1`. Endpoint, wire, session, journal schema,
GUI, firmware, упаковка, лицензии, CI и приостановленный demo-package review
`a214` остаются вне изменения. Предыдущие freezes и их свидетельства сохраняются.

## Что запускается

`DiagnosticRun` требует явно переданные `SerialSettings`, `EventConfig`,
`fake_backend_factory`, `fake_origin="synthetic-fake"` и `fixture_name`.
Необязательный `fake_diagnostic_declaration=None` сохраняет прежнюю generic
репетицию; для `str1` принимается только точная
`stream_provenance.STR1_DIAGNOSTIC_DECLARATION`.
Конструктор только проверяет параметры: не создаёт transport, поток или файл.
Нет CLI с COM-портом, импорта pyserial, discovery, стандартного настоящего backend,
fallback, reconnect, автоматического повторного Record или нового wire parser.

Фабрика — **договор с вызывающим кодом**. Произвольный Python не изолирован и не
аутентифицирован: имя fake не доказывает, что чужая функция не выполнит real I/O.
Разрешённый и проверяемый путь здесь использует прочитанные fixtures
`test_stream_controller.EncodedPeer` и `test_stream_serial.FakeFactory`;
тесты запрещают вызов `transport._serial_factory`. Другие фабрики не получают
физического допуска от этого API.

Основные вызовы:

- `collect(recording_path)` — один явный запуск на объект: Connect, одна заявка
  Record, ограниченное наблюдение, Stop, drain и close. Повторный collect запрещён.
- `request_stop()` — независимая reserved заявка в принятом controller.
- `close()` — немедленный публичный fence и запрос cleanup; не force-close.
- `snapshot()` — состояние controller, отдельные ошибки диагностики/супервизора
  и report-I/O owner. Это наблюдение, а не доступ к owner.
- `join(timeout)` — наблюдение settlement всех controller workers, supervisor и
  report owner; timeout от 0 до 10 секунд. False означает unresolved.
- `retry_close()` — только явный cleanup той же retained serial handle. Не открывает
  порт, не перезапускает запись, не выполняет бесконечный retry.
- `write_report(path)` — один отдельный export уже замороженного результата.
  Новый файл открывается эксклюзивно; существующий файл не перезаписывается.
- `read_report(path, expected_run_id=..., expected_source_hashes=...)` — строгая
  конечная проверка файла. Возвращает immutable verified prefix, complete/error,
  run ID и, только при полном корректном завершении, summary, file_bytes и file_sha256.

Пример только с source-tree fake, без физического прибора:

```python
from motortester.stream_diagnostic import DiagnosticRun
from motortester.stream_serial import SerialSettings
from test_stream_controller import CONFIG
from test_stream_serial import FakeFactory

run = DiagnosticRun(
    SerialSettings("FAKE-SELECTED", 230400, 0, .025),
    fake_backend_factory=FakeFactory(), fake_origin="synthetic-fake",
    fixture_name="inspected source-tree FakeFactory/EncodedPeer", event_config=CONFIG,
)
report = run.collect("new-empty-fake.mtstream")
run.write_report("new-fake-callbacks.jsonl")
settled = run.join(3)
status = run.snapshot()
```

Этот пример передаёт handshake/START/END/ACK, но сам не добавляет DATA: полученная
пустая запись не изображает измерение. Набор тестов отдельно подаёт точные
синтетические DATA и проверяет их свежим reader. Оба пути требуют source checkout
с указанными fixtures; это не инсталляция и не инструкция физического опыта.

## Явная fake-репетиция `str1`

В `DiagnosticRun` передают
`fake_diagnostic_declaration=STR1_DIAGNOSTIC_DECLARATION` вместе с теми же
обязательными fake-параметрами. Воспроизводящий backend должен выдавать реально
закодированные HELLO/CAPS для этой декларации. Обычный `FakeFactory` из примера
выше имеет другой build и не является подходящей положительной `str1` fixture:
одно добавление параметра к примеру должно привести к отказу допуска.
Положительные и отрицательные encoded fixtures находятся в
`host/tests/test_stream_diagnostic.py`.

Точная декларация образа:

- `str1`, domain `completed-dma-v1`, revision
  `0.2-draft-dma-sequence-20261019`;
- compiled-local source `31b93659164dec376bea5b973b597196c6a4a0ad`;
- заявленный BIN SHA-256
  `90fee8a085d1e561cc6f5c28c26db26a43c1e57145ae41314bf70e9422c7b904`;
- ожидаемый HELLO build `0x53545231`;
- ожидаемые CAPS `(1, 1, 0, 12, 50000, 1, 128, 1)` в порядке config,
  numeric profile, channel, ADC bits, Fs numerator, Fs denominator, max block, flags;
- intended origin `device`, config 1, номинальная Fs 50000 Hz.

Intended origin относится только к выбранной декларации образа. Фактический
run остаётся `synthetic-fake`, а origin сохранённого sample archive —
`synthetic`. Это **синтетическая репетиция, NOT_FLASH_APPROVED**; совпадение
fake HELLO/CAPS не удостоверяет исполнение образа и не подтверждает физический
съём. Источник, derived-BIN identity и границы проверки подробно разобраны в
[guide о provenance](stream-provenance.md#декларация-str1-только-для-fake-репетиции).

`SerialStreamPeer(..., fake_backend_factory=...)` — отдельный явный выбор,
взаимоисключающий с `backend_factory`. Read-only `fake_backend_selected`
сообщает этот выбор, но не анализирует и не изолирует Python-фабрику.
Обычный прежний путь `backend_factory` сохраняет своё поведение и сам по себе
не даёт допуска к репетиции. `StreamController(...,
fake_diagnostic_declaration=...)` требует точный тип managed `SerialStreamPeer`
с явно выбранным fake backend. Plain/direct peer, subclass, default/real backend,
обычная injected factory, изменённая декларация или совместная передача
`producer_profile` отвергаются до создания workers и открытия backend.
Запрет `syn1` для serial остаётся прежним.

Bridge проверяет уже принятые endpoint сведения `Negotiated` в существующем
`_reserve`, до `plan_start`, reservation поколения, создания session/writer и
установления R. Проверяется build и каждый из восьми CAPS components;
расхождение закрывает допуск без START и без fallback. Новый wire parser,
observer или альтернативный deadline path не добавляется.

В metadata `selected_declaration` и `expected_negotiation` содержат выбранный
exact tuple и ожидания; `flash_status` равен `NOT_FLASH_APPROVED`.
Ограниченный `rehearsal_notice` прямо указывает synthetic rehearsal/intended
device, completed-DMA exclusions, неизвестный produced end/loss, номинальное
некалиброванное время и отсутствие аутентификации образа. Этот текст присутствует
и до появления archive identity, например при no-R отказе допуска.
`STR1_DIAGNOSTIC_CAPS` — публичный immutable tuple ожидаемых CAPS.
Ожидаемая декларация и фактические наблюдения в report разделены.
`BridgeSnapshot.accepted_build_id` и `accepted_caps` — необязательные immutable
поля (`int | None` и `tuple | None`) уже принятых endpoint HELLO/CAPS. До приёма
сведений они недоступны (`None`). Новый bridge начинает без этих сведений;
в существующем bridge они очищаются только при реально допущенном `Pending`
для HELLO. Отклонённая заявка HELLO не стирает evidence текущей попытки.
Принятие корректного wire-ответа ещё не означает совпадение с декларацией;
при расхождении report может показывать фактически принятые отличающиеся поля.
Nonce/generation checks остаются у исходного endpoint. Если terminal snapshot
сохранил сведения, они относятся именно к завершённой попытке. Из ожиданий и
raw trace не создаются «наблюдавшиеся» CAPS или времена отдельных событий.

## Владение и границы времени

Managed `StreamController` остаётся единственным receiver owner.
Collector передаёт ему `SerialStreamPeer`; backend factory/open/read/write/close
выполняются через peer только его receiver owner.
Proxy оборачивает только backend callbacks; точный тип SerialStreamPeer не меняется.
Ни bridge, ни endpoint, ни controller.service не вызываются супервизором.

Sample journal пишет существующий disk worker. Supervisor только подаёт публичные
заявки и копирует ограниченные снимки. Отдельный report owner вычисляет хеши,
сериализует JSON, создаёт файл, пишет/flush/fsync/close и делает readback.
Нет потока на каждый chunk или lock вокруг backend, controller или file I/O.
JSON-accessors immutable report выполняют сериализацию на вызывающей стороне;
во время collect supervisor их не вызывает.

Clock — один явно указанный `clock_ns`, по умолчанию `time.monotonic_ns`.
Controller получает свежие `clock_ns() // 1_000_000`, включая наблюдения после
callbacks. В trace сохраняются begin/return в **наносекундах**. Поля Pending/
FirstFault `admitted_ms/deadline_ms` остаются **миллисекундами** принятого endpoint.
UTC label — отдельная календарная отметка. Она не участвует в дедлайнах.

Бюджеты `DiagnosticLimits`:

- Connect: 2 000 ms от публичной заявки Connect.
- Record → Stop: 1 000 ms по умолчанию, максимум 10 000 ms от успешного возврата
  публичной заявки Record. Включает handshake/preparation; это не START_ACK.
- Drain observation: 4 500 ms от заявки Stop.
- Close/join observation: 1 000 ms после запроса close. Принятый join использует
  свой monotonic clock; это elapsed observation, не переопределение wire deadline.

Можно только уменьшить соответствующие пределы, кроме run_ms с максимумом10s.
Test clocks должны оставаться monotonic и продвигаться; замороженный или неверный
пользовательский clock не является честным реальным временным ограничением.
Конечный budget прекращает обычное продолжение и подаёт Stop/close, но **не
прерывает OS callback**. Read timeout=0 и положительный write timeout≤1s — настройки
backend, не доказанный верхний предел длительности open/read/write/close.
Зависший callback или writer остаётся с прежним owner. Другой поток не закрывает
его handle. После истечения observation budget результат остаётся unresolved.

## Что именно записано

Каждая строка callback содержит collector-local `callback_id`, kind,
`begin_ns`, `return_ns` либо null, offered TX bytes в lowercase hex,
`read_maximum`, actual RX bytes/length, accepted count либо null,
`outcome` и ограниченное имя класса исключения. Произвольные exception messages,
repr объектов и credentials не сохраняются. Empty read сохраняется как отдельный
callback; скрытого объединения пустых чтений нет.

Для write:

- `known`, count=0 — известный нулевой результат;
- `known`, 0<count<len(offered) — известный префикс `offered[:count]`;
- `known`, count=len(offered) — backend сообщил приём всего offer;
- `unknown` — None/invalid count; `exception` — callback выбросил исключение.
  Оба случая имеют accepted_count=null, а не ноль или полный успех.

Proxy возвращает исходный результат/исключение неизменённым. SerialTransport и
endpoint самостоятельно применяют прежнюю строгую проверку. Принятые backend
байты могут оказаться слишком поздними для endpoint: эти два факта сохраняются
отдельно. Это не наблюдение USB на проводе и не квитанция устройства.

RX chunk не становится валидным DATA от наличия в trace. Oversized invalid RX
сохраняет не больше544 bytes и фактическую длину, явно помечает неполноту; исходный
invalid result по-прежнему отвергает SerialTransport. Nonbytes не преобразуются
через repr или bytes ради красивого лога.

`callback_id` не является endpoint WriteOffer token. Полного потока admission,
DATA-validation и causal events controller публично не экспортирует. Снимки
могут объединять изменения внутри service. Время снимка — время наблюдения
supervisor, а не время каждого изменения. Admission/causal request сохраняются
только там, где они действительно присутствуют в публичных Pending/FirstFault/
RecoverySnapshot; отсутствующие сведения не восстанавливаются из TX.

Итоговое evidence берётся из ControllerStatus/BridgeSnapshot/SessionSnapshot:
validated block/sample counts; source prefix end/CRC; валидный END; совпавший
session STOP_ACK; source flags и первый/поздний отказ; R, accepted/checkpointed/
finalized endpoints; disk returned/closed; serial call/generation/unresolved.
`produced_end=None` остаётся неизвестным. Нет R, `[R,R)` и неполный сохранённый
префикс — разные состояния. Полнота callbacks не означает успешную source запись.
Индекс / номинальная Fs не является измеренным физическим временем.

## Ёмкость и отказы

Максимумы: 16 384 callback records; 4MiB суммарных сохранённых offered+received;
544 bytes на одно направление callback; 128 sampled state transitions;
8KiB header metadata; 32KiB на JSONL record; 32MiB на весь файл. Один файл source,
чей хеш наблюдает export owner, ограничен1MiB. Независимо от raw/state capacities
зарезервирован итоговый snapshot. Sample session сохраняет принятые256 items/
256KiB; sample journal получает явные2MiB/16 384 blocks.

Перед callback резервируется место для его ограниченного результата. Ошибка
логирования или насыщение памяти навсегда устанавливает trace_error/gap;
несохранённые callbacks считаются, новые обычные действия запрещены и supervisor
подаёт Stop. Unlogged drain не скрывается. Clean END/ACK не исправляет log gap.
Ошибка логирования никогда не заменяет исходный backend result или first cause.

Trace до export находится **только в RAM**. Сбой процесса может уничтожить его
полностью. Durable/checkpointed sample journal не делает durable trace.
Замороженный report показывает состояние на свой observed_ns: pending callback,
unsettled writer и timeout не исчезнут из него после позднего возврата.
Один объект не выдаёт новый улучшенный export после cleanup; для продолжения
предназначен отдельный будущий reviewed run, а не переписывание старого evidence.

## Файл, verified prefix и runtime receipt

Формат `motortester-fake-callback-trace-1`: header → callback records → sampled
snapshots → final. Envelope содержит seq, run_id, kind, previous SHA-256, payload
и SHA-256 канонического envelope без собственного digest. Начальный previous —
64 нуля. Final связывает количество callback/snapshot records, raw bytes,
число/размер предыдущих записей и их последний hash. Сырые bytes round-trip
проверяются независимо от waveform или sample journal.

Reader ограничивает readline, суммарный файл, counts, payload sizes и hex;
проверяет duplicate JSON keys, sequence/run identity, hash chain, footer,
declared completeness и EOF. При повреждении, перестановке, пропуске или
обрезанном хвосте возвращает только уже проверенные immutable JSON bytes и
complete=false. Повторное хеширование злоумышленником не исключено: checksum
проверяет случайную порчу, не авторство или аутентичность производителя.

`ReadReport.complete` означает структурно целый экспортированный файл. Поля
`file_bytes` и `file_sha256` описывают все фактически прочитанные bytes, включая
header, snapshots, final и newline; при неполной проверке оба поля равны None.
Это не clean source, не полная trace coverage и не доказательство предыдущего fsync return.
Footer не может удостоверять, что последующие ему fsync/close уже вернулись.
`ExportStatus` отдельно сообщает fsync_returned, readback_verified, owner_settled,
complete_receipt/error. Complete receipt возможен только при полной as-of capture,
отсутствии supervisor error, успешных actual I/O/readback и завершившемся owner.
Export owner вычисляет отдельные размер и SHA-256 точных сериализованных bytes
своего immutable capture по мере успешных write returns. После flush/fsync/close
он сравнивает их с размером и SHA-256 всего свежепрочитанного файла. Совпадения
run_id/source hashes и правильной внутренней цепочки недостаточно: подмена RX или
другого содержимого с пересчитанной цепочкой оставляет readback_verified и
complete_receipt false, даже если standalone reader сообщает complete=true.
Фактически наблюдавшийся fsync_returned при этом сохраняется отдельно. Эта
проверка связывает readback с данным export, но не удостоверяет производителя
и не гарантирует неизменность файла после завершения readback.
Blocked writer остаётся running; failed write/fsync/close остаётся failed.
Существующий или частичный файл не удаляется и не перезаписывается.

Source hashes вычисляются report owner **во время export** для перечисленных
HOST modules и reference fake tests, с отдельным source_hash_observed_ns. Это
наблюдаемые файлы checkout, не доказательство того, какие произвольные функции
исполнял переданный factory. Исходный immutable report не меняется. Точный reviewed
candidate SHA и source freeze нужны отдельно; base_commit — accepted local base,
а не ошибочно заявленная версия всех новых файлов.

## Физическая граница и проверка

Без `fake_diagnostic_declaration` сохраняется прежний generic/synthetic смысл
identity. При точном `str1` фактический origin по-прежнему synthetic-fake;
`producer_profile=None`, а отдельная fake declaration хранит intended device
identity. Archive использует origin `synthetic` и точный carrier
`rehearsal:str1:31b93659164dec376bea5b973b597196c6a4a0ad:53545231`.
Это не `producer:str1` и не device acquisition. Физический registry пуст.
В report `firmware_image`, `usb_identity` и `telemetry` остаются `null`: выбранный
image digest находится в declaration, а эти реальные наблюдения отсутствуют.
Образ не читается с прибора и не исполняется collector. Нет COM/real-port CLI,
telemetry wire command, SWD reader,
flash или физического допуска по совпадению HELLO.

У связанного образа `produced_end` остаётся неизвестным: стабильный completed-DMA
prefix и совпавший STOP_ACK не создают known end, zero loss или clean physical
capture. END normalization не меняется. Completed-DMA counters не учитывают
ADC_DR overwrite, незавершённые преобразования/DMA requests, пропущенные триггеры
или физические пробелы. Время — индекс / номинальная Fs, без калибровки и без
доказательства равномерного физического шага. Сохранённые source counters,
first fault, неполнота и различие no-R/`[R,R)` остаются самостоятельными фактами.

Текущий reader открывает прежние generic и `syn1` записи без нового смысла и
принимает только точный synthetic rehearsal tuple. Старые domain-aware readers
баз `91778`/`db124` отвергают новый tagged carrier; более ранняя структурная
читаемость строк не означает понимания `str1`. Изменение archive schema не требуется.

Из корня exact source checkout, без установки пакетов, **последовательно**:

```sh
PYTHONPATH=host:host/tests python -m unittest -v test_stream_diagnostic
PYTHONPATH=host:host/tests python -O -m unittest -v test_stream_diagnostic
```

Ограниченный набор проверок использует реальные временные sample journals и
exports, независимые encoded fixtures, normal/-O, строгий свежий process readback, известные/unknown
write counts, точные deadline equality, bad prefix/ACK, unknown END, empty/no-R,
log caps, blocked sample/report/serial owners, first-cause separation, failed
export, short/zero report writes, exact full-file readback и same-run RX substitution
с пересчитанной цепочкой, semantic negative control против premature success.
Для `str1` добавлены точная декларация и независимые изменения каждого её поля
(включая неверные типы/bool-as-int), запрет неявных backend paths, mismatch build
и всех восьми CAPS, попытки со stale negotiation, timeout/transport failure,
matching Record → readiness → DATA → Stop → END/STOP_ACK и fresh-process reopen.
Сверяются raw bytes, source prefix/CRC, Stop identity, ненулевой R, отдельные
unknown END, empty и no-R результаты, expected/actual metadata и маркировка
synthetic/NOT_FLASH_APPROVED. Это перечень воспроизводимых controls, а не заявление
о PASS новой версии без её отдельного журнала и независимого ревью. Старые ошибки
авторского harness сохраняются в evidence вне source diff; они не выдаются за PASS.
Windows10, COM/USB платы, firmware, физические временные шкалы, напряжения и
зажигание этими synthetic/software checks не проверяются.

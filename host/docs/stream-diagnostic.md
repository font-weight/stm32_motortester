# Opt-in диагностика потока: fake callbacks и сохранённое состояние HOST

Это отдельный source-only компонент для подготовки первого ограниченного опыта
issue191. Он не является готовым Windows GUI, пакетом, физическим сборщиком,
подтверждением работы USB/ADC/DMA или разрешением подключить прибор.

Принятое локальное основание: `91778fcbb63f86a6bbf28567bbfbdced888b21f7`,
дерево `749ad8e771bd4522cb844ca7500b518960b40f9c`. Дерево равно публичному PR205
`060a24570686396162b8006f423e0dfc42d92604`. Непосредственный локальный parent этого
исправления — `9a0b89c738853ba9267cf2499b7ce9a9ae8c522d`, первый collector candidate;
его parent — 91778fc. Будущий публичный successor этим документом не заявлен.
Независимый Critic-B должен проверить точный freeze; наличие этого документа
не выдаёт PASS. Меняются только три новых файла: `stream_diagnostic.py`,
`test_stream_diagnostic.py` и этот guide. Принятые controller, serial, endpoint,
bridge, session, journal, provenance и GUI остаются неизменными.

## Что запускается

`DiagnosticRun` требует явно переданные `SerialSettings`, `EventConfig`,
`fake_backend_factory`, `fake_origin="synthetic-fake"` и `fixture_name`.
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

## Владение и границы времени

Неизменённый managed `StreamController` остаётся единственным receiver owner.
Он создаёт `SerialStreamPeer`, а через него backend factory/open/read/write/close.
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

origin остаётся synthetic-fake, producer_profile=None и прежний generic/synthetic
смысл identity. Единственный syn1 DMA-profile запрещает SerialStreamPeer; это
ограничение не обходится. Физический registry пуст. Firmware image, USB identity
и telemetry остаются null. Нет telemetry wire command, SWD reader, flash или
допуска нового build по совпадению HELLO. Даже future declaration не будет
аутентифицировать фактически исполняемый binary.

Из корня exact source checkout, без установки пакетов, **последовательно**:

```sh
PYTHONPATH=host:host/tests python -m unittest -v test_stream_diagnostic
PYTHONPATH=host:host/tests python -O -m unittest -v test_stream_diagnostic
```

Тесты используют реальные временные sample journals и exports, независимые
encoded fixtures, normal/-O, строгий свежий process readback, известные/unknown
write counts, точные deadline equality, bad prefix/ACK, unknown END, empty/no-R,
log caps, blocked sample/report/serial owners, first-cause separation, failed
export, short/zero report writes, exact full-file readback и same-run RX substitution
с пересчитанной цепочкой, semantic negative control против premature success. Старые ошибки
авторского harness сохраняются в evidence вне source diff; они не выдаются за PASS.
Windows10, COM/USB платы, firmware, физические временные шкалы, напряжения и
зажигание этими synthetic/software checks не проверяются.

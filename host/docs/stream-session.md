# Нормализованный потоковый сеанс ПК

`host/motortester/stream_session.py` — ограниченная модель решений одного
владельца, отдельно от транспорта, writer и GUI. Это подготовка host-кода для
просмотра и одной записи от Record до Stop. Открытие объекта ничего не запускает.
Нет файловых операций, потоков выполнения, часов, COM, Qt или команд устройства.

Зависимость — data-only типы `stream_model.py`, включая `UnboundOutcome` из
двухфазного дополнения storage; формат и реальные операции описаны в
[stream-recording.md](stream-recording.md). Историческая foundation storage:
`1292711b4c86847e096defdf0e6e49831e01d623`. Проверенный авторской pairing-пробой
двухфазный successor: `2ce983cfc74a9b10eff418d273222d93f610fb41`; его отдельный
review не наследуется этой моделью. Исходная база этой отдельной работы:
`e38a36004befe453923f3e86165b7fd6d83076d9`.
Черновик PR192 `72d400660a0af896ebb3810a8c9a22166d7fa78a` рассмотрен как контекст,
но его wire-типы, CRC, сроки и команды здесь не реализованы и не приняты.

## Ответственность и входы

Один serial owner вызывает методы в порядке событий. Это логическая атомарность
перехода, а не многопоточная блокировка. Позднейший adapter обязан:

- проверить wire, полную DATA, identity/generation/config/Fs, порядок и целостность
  всего исходного префикса, терминал и корреляцию Stop;
- передавать только законченные нормализованные `StreamBlock`, `SourceStarted`,
  `SourceEnd`, `StopAcknowledgement` и явные `FailureEvidence`;
- выполнить одно возвращённое действие/задание ровно один раз; не считать выдачу
  задания успешной операцией, не подменять callback постановкой в очередь;
- обеспечить собственные конечные сроки start/drain/Stop, единственного writer,
  обработку отключения и закрытие writer после отказа. `drain_failed(...)`
  передаёт уже принятое решение о timeout/невозможном drain; чисел wire-таймеров
  модель не выбирает;
- не переносить старый сеанс в новую generation. Для нового явного запуска нужен
  новый объект, новая проверенная identity и уникальный attempt_id. Автоматического retry/rearm нет.

Типизированный вход — заявление вызывающего кода, а не доказательство выполнения
этих проверок. `origin="device"`, quiescence, nominal Fs и успешный callback не
доказывают аутентичность, физическую остановку, калибровку или запись на носитель.
`uint16-le` принадлежит нормализованному storage; проверка конкретной разрядности
АЦП остаётся adapter. Внешний trigger/датчик цилиндра не добавляется.

`SourceStarted` связан с `attempt_id`, identity и точным `start_action_id` ранее
выданного Start. `SourceEnd` содержит отдельный bounded `terminal_id` и
`StreamTerminal` исходного received-префикса. `StopAcknowledgement` независимо
содержит attempt, identity, `stop_action_id`, `terminal_id`, endpoint,
`request_id` и строго boolean `quiescence_reported`. Adapter связывает своё
реальное request ID с выданным действием; локальный action ID не является wire ID.

## Переходы

| Исходное состояние | Вызов | Результат |
| --- | --- | --- |
| Новый объект | `preview()` | Один Start intent; storage отсутствует |
| Новый объект | `record(recording_id)` | `prepare`, без Start и без R |
| Preview, включая ожидание START | `record(...)` | `prepare` с политикой `next_block`; старый preview не записывается |
| `prepare` завершён | `storage_completed(...)` | Только готовность; direct Record выдаёт Start, preview продолжает ждать новую DATA |
| Direct Record, готовность, выданный Start | `source_started(...)` | Устанавливается реальный R из явного наблюдения; задание `bind_start` |
| Preview Record, готовность | Следующий валидный `receive(block)` | Проверить тип/диапазон/identity/порядок/ёмкость; атомарно выбрать R и поставить первый блок в очередь; `bind_first_block` |
| Ожидание R | END/fault/cancel/Stop выигрывает раньше DATA | R остаётся `None`; готовый attempt завершается через `finish_unbound` |
| Выбран R | `receive(block)` | Последовательная bounded очередь; DATA до Stop и разрешённый drain сохраняются полностью |
| Активный источник | `stop()` | Один Stop intent; существующая запись допускает сертифицированный DATA-drain до END |
| Выбран R | `cancel()`/fault | Первый отказ, fence новых DATA и один Stop intent; уже принятый в очередь префикс остаётся |
| END получен, disk отстаёт | `source_end(...)` | Сохранить END отдельно; не подменять его endpoint границей writer |
| Очередь обработана, clean END + matching ACK | `next_storage_job()` | Неизменяемый `finish` по точному accepted endpoint |
| Отказ + END либо явный невозможный drain | `next_storage_job()` | Неполный `finish` или `finish_unbound`, без требования успешного ACK |
| Выдан `finish`/`finish_unbound` | Точный storage callback | Подтверждённый возврат или unresolved отказ; никакого повторного задания |

Очередность самих вызовов определяет гонку ready/DATA/END/fault/cancel.
Диск и перерисовка графика не выбирают R. При прямом Record успешное явное
`SourceStarted` допускает пустой интервал `[R,R)` после соответствующих END/ACK.
Один Start intent, готовый файл или marker не являются успешной пустой записью.
Failed START оставляет R неизвестным. Ожидаемое wire-значение 0 здесь не
подставляется: нормализованная модель поддерживает известный целый source start.

До выдачи Start отмена удаляет только ещё не выданный intent. Если Start уже
выдан adapter, модель просит Stop; поздний `SourceStarted` может описать источник,
но не выбирает R после отмены. Пользовательский Stop до выбора R также запрещает
поздний выбор R на DATA-drain.

Повторы управляющих вызовов и входных callbacks до финального задания отклоняются
fail-closed: первый отказ фиксируется, новые DATA запрещаются, повторного Stop нет.
ACK может быть получен раньше END: хранится один bounded ACK, успеха ещё нет;
при появлении второго объекта проверяется их точное совпадение. END либо ACK
по отдельности не дают clean outcome. Повторный END/ACK до финального задания —
отказ. Clean END должен сообщать Stop и его request ID; matching ACK сверяет этот
ID, endpoint и terminal reference. Поздний ACK не стирает предыдущий отказ.

## Финальное задание и граница результата

Выдача `finish`/`finish_unbound` замораживает исходный результат. Более поздние
source callbacks, новый fault/cancel или повторные команды отклоняются как
события вне его границ, не переписывая первый отказ этой записи. Только
correlated callback именно финального storage job ещё относится к результату.
Это не обещание остановки оборудования: выданный ранее Stop остаётся обязанностью
adapter, включая его сроки и обработку незавершённого физического действия.

Реальная ошибка write/flush/fsync финального job задаёт `writer_fenced=True`,
`finalization_unresolved=True`, `finalized=False`; операция перестаёт числиться
in-flight после её точного failure callback. Читаемые terminal bytes могут уже
существовать. Модель не переписывает выданный terminal и не создаёт sidecar.
Reopen видит только проверенные байты журнала, а не memory-only сведения о том,
что прежний вызов завершился ошибкой. Нельзя обещать сохранение этих поздних
диагностических сведений через перезапуск.

Ошибочный/устаревший callback не подтверждает, что текущая операция вообще
завершилась: он ставит writer fence, но текущий pending ID сохраняется. Если затем
приходит точный callback текущего job, pending снимается, однако границы не
продвигаются и успешная работа не возобновляется. При неудаче raw остаётся в
ограниченной очереди для диагностики; automatic повторной записи нет. Закрытие
реального writer/дескриптора и судьба запущенного I/O принадлежат adapter.

После успешного финального callback поздний duplicate также отклоняется, но не
меняет уже установленный результат. Следующий запуск — отдельная попытка.

## Конечные ресурсы

До реализации объявлены и проверяются пределы:

- Raw очередь по умолчанию: 256 KiB и 256 элементов; настраиваемые hard caps:
  1 MiB и 4096 элементов. Каждый блок уже ограничен `MAX_BLOCK_SAMPLES` storage,
  сейчас 8192 отсчёта / 16 KiB.
- Весь in-flight raw блок остаётся элементом этой же очереди до точного успешного
  callback. Извлечение задания не освобождает ни bytes, ни item.
- Отдельно: один latest-only display block не более 16 KiB, один control slot,
  один END и один ACK, первый отказ, один pending job и фиксированные метаданные.
  Подмена display snapshot никогда не снимает raw из очереди записи.
- Метаданные имеют пределы shared-типов: identity/attempt/recording/reference
  строки до 128 символов, failure detail до 256, token 32 hex, SHA256 64 hex.
  Operation ID — проверяемый unsigned 64-bit без переполнения; история завершённых
  jobs не накапливается. Последовательность controls фиксирована: Start, затем Stop.
- Один checkpoint request может ждать; повтор отклоняется. Он обслуживается до
  следующего queued append, а не бесконечно откладывается свежими DATA.
  Автоматического checkpoint на каждом DATA нет.

Бюджеты относятся к данным, которыми владеет модель. Возвращённые job/snapshot
копии имеют независимые records; immutable bytes не изменяются. Adapter обязан
не накапливать старые snapshots/jobs: модель не управляет чужим хранилищем.
Вычисление хэша использует ограниченный текущим блоком временный payload.

Переполнение raw/items происходит после фиксации received endpoint валидного
блока, но до изменения queued endpoint и выбора R. Первый отказ становится
`capacity_raw`/`capacity_blocks`; Stop остаётся доступен независимо от очереди.
Отброшенный из записи блок не скрывается продолжением «непрерывной» записи.
Дальнейшие DATA запрещены. Поздний caller-validated END может сообщить больший
source prefix, но не расширяет fenced архив и не отменяет отказ.

## Значение границ и потерь

Все индексы целые; вычислений времени через float и arrival timestamp нет.
Номинальный шаг времени `1 / nominal_fs_hz` принадлежит неизменяемой identity.
Значения выше `2**53` сохраняются точно.

- `source_first_sample_index`: явный наблюдавшийся старт источника.
- `received_end_sample_index`: конец непрерывно проверенных здесь нормализованных
  DATA до fence; включает preview и валидный блок, не вошедший при переполнении.
- `selected_start_sample_index` (R): единственный выигравший переход выбора.
- `queued_end_sample_index`: конец принятого в bounded очередь интервала `[R,Q)`.
- `accepted_end_sample_index`: конец по точным успешным возвратам bind/append;
  это не disk flush и не readback.
- `checkpointed_end_sample_index`: reported endpoint storage после успешно
  вернувшегося checkpoint/его внутреннего аналога. Это не независимое readback.
- `finalized_end_sample_index`: endpoint только после точного успешного finish.
  Он не появляется от одного END, ACK, queue append или readable-файла.

До выбора R selected/queued/accepted/checkpointed/finalized равны `None`, а не 0.
После логического выбора R accepted/checkpointed могут ещё быть `None`, пока bind
не вернулся. Empty prefix endpoint R после bind означает ноль отсчётов; сам по
себе он не доказывает успешную finalization. У unbound исхода endpoints остаются
`None`, даже если `finalized=True` означает завершённый журнал этой попытки.

Source generated/transmitted/loss claims хранятся отдельно в `SourceEnd`.
Например, received=100, queued=80, accepted=60, checkpointed=40 — четыре разных
факта; ещё 20 queued samples не считаются долговечными, а незаписанный хвост не
заполняется нулями. Storage terminal использует exact accepted endpoint и сохраняет
`loss_samples` именно как отдельно сообщённый source-счётчик: известное целое,
включая 0, остаётся тем же; неизвестный source count или отсутствующий END даёт
`None`. Это не суммарная потеря записи и не оценка host omission. При локальном
первом отказе и более коротком архиве этот отказ и разные accepted/source endpoints
сохраняются независимо; даже source count=0 не делает запись полной. Дополнительный
host tail не выводится из этого счётчика и не прибавляется к нему. Поздний source
terminal остаётся доступен в snapshot со своими исходными claims.

Первый review исходника `10f42fade4e2ffe5a5fed4781a435d6b480331fb` обнаружил,
что прежняя projection ошибочно заменяла известный source loss на `None` при
любом отказе. Узкое исправление сохраняет отдельно сообщённое значение без
изменения schema/API, первой причины, очереди и границ финализации. Исторический
исходник и неуспешные проверки не подменяются.

`StorageJob.prefix_sha256` связывает именно выбранные и успешно accepted raw
bytes плюс текущий bind/append. Это SHA256 намерения выбранного диапазона, не CRC
всего device prefix и не независимая сверка сохранённого файла.

## Минимальный storage adapter

`next_storage_job()` возвращает одно из семи заданий или `None`. Пока оно pending,
следующий вызов не возвращает его повторно. Неизменяемый job включает attempt,
operation, identity, recording ID, policy, token ID, payload, ожидаемый endpoint и
prefix SHA. Его SHA256 покрывает все эти поля и SHA256 точных raw bytes.

| Job | Реальная storage операция |
| --- | --- |
| `prepare` | `PreparedStreamWriter.prepare(path, StreamPreparation(identity, recording_id, start_policy))` |
| `bind_first_block` | `prepared.bind_first_block(original_token, job.block)` |
| `bind_start` | `prepared.bind_start(original_token, job.start)` |
| `append` | `writer.append(job.block)` |
| `checkpoint` | `writer.checkpoint()` |
| `finish` | `writer.finish(job.terminal)` |
| `finish_unbound` | `prepared.finish_unbound(original_token, job.unbound)` |

`prepare` возвращает writer с `readiness_token`; adapter хранит именно исходный
объект токена. В модель передаётся только `token_id`. Копия токена или тот же ID
не восстанавливают одноразовое право bind. Для first-block policy actual storage
выбирает start metadata из этого блока; смысл границы согласован с session.

Только после успешного возврата операции adapter создаёт `JobCompletion.for_job`
из фактически выполненного job и действительных accepted/checkpointed значений.
Prepare completion требует обоих endpoints `None`; остальные token IDs должны
совпадать. Checkpoint/finish требуют равенства endpoint; append допускает прежний
checkpoint, checkpoint до текущего блока или конец текущего блока — варианты
ограниченной batching-политики storage. Регрессия, промежуточный выдуманный
endpoint и чужой/повторный operation/hash отклоняются.

Ошибка имеет отдельный `JobFailure.for_job(job, FailureEvidence(...))` и
`storage_failed(failure)`. Failed prepare не выдаёт readiness: token остаётся
`None`, endpoints вообще не входят в failure record. Возможен диагностический
частичный файл, но не ready или indexed recording. Не создавать success callback
из факта существования файла; readable recovery не доказывает, что fsync вернулся.

## Проверка и ограничения

После соединения совместимых исходников в одном checkout из корня репозитория:

```sh
PYTHONPATH=host python -m unittest discover -s host/tests -p test_stream_session.py -v
PYTHONPATH=host python -O -m unittest discover -s host/tests -p test_stream_session.py -v
```

Тесты проверяют фиксированные сценарии и обе допустимые очередности событий,
полные лимиты с in-flight raw, самостоятельный Stop reserve, delayed writer,
no-selection и действительно пустой source-start, сохранность первого отказа,
некорректные success/failure callbacks, high indices, bool/range/mutation,
END/ACK отдельно и вместе, точный raw/hash для всех 128 разбиений восьми samples.
Нормальные и оптимизированные запуски обязательны: проверки не зависят от assert.

`StoragePairingTests` в том же test-файле — шесть узких локальных journal-проб:
preview `[R,E)` с checkpoint, настоящий пустой direct start, END до выбора R и
читаемый terminal после синтетического failed fsync; отдельно — source-first
и local-first/shorter-archive с source loss=7, 0 и None после настоящего reopen. Adapter теста вызывает
реальные storage методы, берёт actual accepted/checkpointed только после их
возврата, затем независимо читает journal и сравнивает bytes/hash/метаданные.
Они выполнены со storage successor, указанным выше; эти шесть тестов проверяют
local storage pairing отдельно от 47 pure-state тестов. Для отдельного запуска
класса из корня совместимого checkout:

```sh
PYTHONPATH=host:host/tests python -m unittest test_stream_session.StoragePairingTests -v
PYTHONPATH=host:host/tests python -O -m unittest test_stream_session.StoragePairingTests -v
```

Авторские normal и `-O`: 53 теста, включая шесть pairing-проб. Это не независимый
вердикт; exact source review требуется отдельно.
Это не тест wire endpoint, очередей ОС/потоков, GUI, Windows, устройства,
непрерывной производительности, power-loss safety или физического Stop.
Event bookmarks/context/navigation относятся к отдельной view-модели и здесь
не реализованы. Любая будущая интеграция требует собственного review.

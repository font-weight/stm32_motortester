# Изолированный мост bytes → сеанс → журнал

Дополнение 19.10.2026: [явная synthetic completed-DMA декларация](stream-provenance.md)
добавляет опциональный `producer_profile`; отсутствие параметра сохраняет
описанную ниже старую synthetic identity. Новый профиль не разрешает real-port
activation и не меняет reserve/disk/owner-time/failure/lifecycle правила.

`stream_bridge.py` соединяет HOST-компоненты двумя явно разделёнными ролями:
реальные входные байты проходят `Endpoint`, его наблюдения преобразуются в
`StreamSession`, а отдельный явно вызываемый `StorageExecutor` выполняет
реальные операции `PreparedStreamWriter`/`StreamWriter`. Это ограниченный
**синтетический replay-прототип**, без production entrypoint, COM/USB/network,
Qt, создания потоков выполнения, сна, прошивки или общей CI. Импорт ничего не запускает.
Receiver owner меняет endpoint/session/clock; disk executor владеет всеми file I/O.
Короткий standard-library Lock защищает только bounded mailbox.
Старые оконные запросы, Stop/rearm и склейка окон не используются.

База: `e38a36004befe453923f3e86165b7fd6d83076d9`. Собственный delta — только
`host/motortester/stream_bridge.py`, `host/tests/test_stream_bridge.py` и этот
документ. Исходники зависимостей материализуются точно, без редактирования:

| Commit | Файлы исполнения | SHA256 |
|---|---|---|
| `2ce983cfc74a9b10eff418d273222d93f610fb41` | `stream_model.py` | `593dba740e75c8746fd8faa2d7e23a46d6a5678a9a548020fd73804aeb834c34` |
| тот же | `stream_recording.py` | `7f5a081b9d0860165a627d9a39d31ffd259113fd8d1c890ac6b0d9820561216e` |
| `79f2748bc83c1a11a79564201253730b5f1e4240` | `stream_session.py` | `9c08d8477bfbd34af2a45adeb42abd149615e7868512c9c75df01a14351bb24e` |
| `02225ad1bcb5c342fed8cb4083691cd06ab8b024` | `stream_wire.py` | `62c7dabe9a73a3f7d486f5e350131438a2ead7e126d6cd2d891ebb13485a9cce` |
| `138a7ba3693ecbc0ed44c24cc42bf243482fb2a4` | `stream_endpoint.py` | `fed7113f3c5570f888c1bb4852da80224849a89977517dd5b99ccf94e6f1d67e` |

На момент freeze этого successor независимая приёмка endpoint `138a7ba`
ещё ожидается; окончательная составная приёмка требует обоих отдельных gate.
Изменение статуса review не меняет эти pinned bodies.

Их собственные тесты и документы берутся из тех же commits; остальные tracked
файлы базы неизменны. Runtime closure: эти пять модулей, `motortester/__init__.py`
базы и standard library. Здесь нет импорта старого приложения или транспорта.
Wire-профиль остаётся временным draft PR192 `0817211`; актуальное уточнение
`770c46` не меняет эту грамматику. Общий gate остаётся `BLOCKED_EVIDENCE/null`.
Локальный HOST-тест не заменяет совместную приёмку контракта.

Предшественник `e603789bfc5f9f4ab440ed21dc5b9398fd23add5` сохранён со своим
REWORK 9.35: при двух pending request его архив терял точного виновника valid
ERROR. Endpoint `138a7ba` аддитивно отдаёт уже валидированный causal Pending;
bridge использует это свидетельство без второго parser или private override.
Старые source/логи и два отрицательных ERROR witness не заменяются новыми.

## Явный API и порядок

Создание `StreamBridge(attempt_id, session_limits=..., storage_limits=...)`
не создаёт файл и не выдаёт команд. Receiver owner последовательно вызывает:

1. `hello(nonce, now_ms)`, выдаёт только HELLO; `next_write(now_ms)` возвращает
   точный `WriteOffer`, `observe_write(token, accepted, now_ms)` сообщает
   фактически принятый префикс этого offer. Полная отправка не равна ACK.
2. `receive(bytes, now_ms)` передаёт байты реальному endpoint. После HELLO_ACK
   явный `get_caps(now_ms)` и валидный CAPS разрешают планирование. Автоматического
   GET_CAPS, START, reconnect или восстановления нет.
3. `preview(now_ms)` резервирует **точный объект** `StartPlan` и сразу передаёт
   session action START в endpoint. `record(path, recording_id, now_ms)` на новом
   мосте сначала резервирует identity и ставит prepare, но не допускает START.
   В существующем preview тот же вызов начинает одну попытку записи.
4. `dispatch_storage(now_ms)` резервирует максимум один внутренний неизменяемый
   job, возвращая только его номер. Повторный вызов сообщает тот же номер,
   не выдаёт job заново. Он не делает filesystem I/O.
5. `disk_executor.execute_one()` — единственный публичный disk entrypoint.
   Он атомарно берёт один job, снимает lock, вызывает реальный writer, затем
   публикует один внутренний результат. Он не читает clock, не вызывает receiver,
   не меняет session/endpoint и не посылает callbacks/signals.
6. `accept_storage_completion(current_now_ms)` на receiver owner сначала
   проверяет **текущее** время и deadlines, потом однократно забирает exact
   result. Только теперь успешный prepare становится usable readiness: direct
   Record допускает START, preview может выбрать следующий целый validated DATA.
   DATA между disk return и acceptance остаётся pre-ready. START_ACK устанавливает
   R=0, а отдельный bind сохраняет declaration. Начавшийся до ready фрагмент
   выбирается только после завершения и полной wire validation после acceptance.
   Нет параметра для caller-authored JobCompletion, token или результата.
7. `storage_tick(now_ms)` оставлен только для **синхронного replay** как явная
   композиция tick → execute_one → accept на одном owner. Не переносить его
   в disk thread и не использовать dispatch timestamp при реальном completion.
8. `stop`, `cancel`, `checkpoint`, `tick`, `transport_fault`, `recover` — явные
   управляющие вызовы с тем же `now_ms`. Tick не выполняет disk work. Recovery
   только сообщает необходимость изоляции; этот мост не начинает новый сеанс.

`now_ms` — строго целый монотонный миллисекундный observation в пределах endpoint.
Таймеры берутся из endpoint, следующий срок доступен как `next_deadline_ms`.
Нужно вызывать tick независимо от трафика. Реальный executor **может блокироваться
в ОС**. Receiver может в это время принимать bytes и обрабатывать свои deadlines;
приложение всё ещё должно предоставить и обслуживать обе роли. В этом модуле
нет scheduler, read loop, worker creation или доказательства wall-clock latency,
USB service, отмены системного I/O либо производительности. Receiver methods
проверяют исходный thread identity, чтобы disk-role вызов storage_tick/close/
accept не мог незаметно изменить session. Snapshot также принадлежит receiver.
Device-only 500 ms не измеряются и не выводятся из host timestamps.

Новый запуск требует новый объект, новый явный nonce/attempt_id и свободный
pathname. O_EXCL не разрешает перезаписать или возобновить старый файл.
Создание нового объекта само по себе не доказывает изоляцию старого реального
транспорта; такая интеграция и её подтверждение остаются следующей задачей.

## Identity, данные и корреляция

Identity составляется из валидированного nonce, зарезервированного generation,
фиксированного CAPS/config/Fs и firmware build. `origin` здесь всегда
`synthetic`: валидные wire bytes не доказывают физическое происхождение.
Даже будущее caller-declared `device` не будет доказательством платы или опыта.
Нет публичного входа для `SourceStarted`, DATA-моделей, END/ACK-моделей, readiness
или `JobCompletion`. Владелец моста получает лишь observations/snapshots.
Обычная Python-приватность не защищает от злонамеренного кода в том же процессе.

START/STOP actions 1/2 сохраняются в настоящих `Pending.action_id`; реальные
request ID берутся из endpoint. END reference включает nonce и transport sequence.
ACK приходит в session только из `StopAcknowledged`, уже связанным с тем же END.
Immediate identical duplicate подавляет endpoint; он не продвигает CRC, сроки,
индексы, запись или успех. Иные старые/переставленные/некорректные кадры закрывают
приём. ACK до END не становится нормализованным подтверждением.

Первая ошибка сохраняется. Request timeout связан с ordinary, Stop timeout
со Stop; неизвестный результат write связан с **реальным активным offer**,
включая STOP при ещё ожидающем START_ACK. Для неоднозначной ошибки чтения/CRC
не выбирается произвольный виноватый request. Оба pending ID остаются контекстом
bounded FailureEvidence.detail и полного endpoint FirstFault. Valid ERROR,
в отличие от CRC/read fault, получает точный `FirstFault.causal_request` даже
при двух pending lanes; текущий ERROR дополнительно сохраняется в recovery
snapshot. Local queue capacity
не приписывается команде: используется исходный receive/failure переход session.

После capacity-провала session received endpoint включает полностью проверенный
отвергнутый блок, queued endpoint — только принятые блоки. Следующий DATA после
fence уже не входит в session; endpoint продолжает проверять весь source prefix
и сохраняет его отдельные counters/CRC. Возможное различие этих границ явно
видно в snapshot и не выдаётся за неизвестную полную потерю.

END CRC покрывает весь исходный поток от нуля, включая preview. StorageJob и
`StorageStatus.selected_sha256` покрывают только admitted `[R,E)`. Это SHA256
точных raw bytes, а не hash-chain физического файла и не readback receipt.
Сообщённые generated/transmitted/loss сохраняются отдельно от более короткого
host accepted prefix. Unknown остаётся None; source loss=0 не означает отсутствие
host потерь. Из discarded bytes не вычисляется выдуманный sample count.

## Storage, границы и закрытие

Executor хранит исходный one-shot readiness object и передаёт именно его
`bind_start`, `bind_first_block` или `finish_unbound`. Копия token_id служит лишь
внутренней корреляции. Completion строится **после** реального метода, проверки
фактических accepted/checkpointed endpoints и выбранного raw SHA. No-op append
не может подтвердить ожидаемый end. Независимого readback для каждого блока нет.
Настоящий `recover_attempt` вызывается отдельно после записи в тестах/потребителем.

Очередь session ограничена по raw bytes/items (по умолчанию 256 KiB/256).
Зарезервированный in-flight raw остаётся внутри этого бюджета **до owner acceptance**,
включая промежуток после disk return. Mailbox имеет один work slot и один result
slot; новый job запрещён до consume предыдущего результата. Concurrent execute
не берёт второй job. Exact job object + attempt/generation/operation/hash/token
проверяются до изменения session. Чужой/скопированный job result или malformed
completion fencing сохраняют first failure и завершают его pending ownership.
Никакого I/O, user callback, ожидания или join под mailbox lock нет.
Endpoint дополнительно имеет собственные finite input/frame/control limits;
мост не накапливает историю events, offers или frames. Хранятся один job, один
активный offer, START/STOP identity, последний status, один result и первый late fault.
Coalescer/index/file quotas принадлежат исходному storage. Snapshot/deepcopy
временно копирует только эти уже ограниченные данные, не бесконечный поток.

Известный verified prefix сохраняется при fault. Prepare/append/checkpoint/
finalize failure запрещает дальнейшие writer jobs, без автоматического retry.
Открываемые после failed fsync bytes **не доказывают прежний успешный возврат**.
Существующий формат может сохранить clean-looking T/U до failed sync: live
`finalization_unresolved` и failure остаются, но после гибели процесса parser
не может восстановить утраченное свидетельство поздней ошибки fsync/close.
Также не обещается сохранение нового first cause в файле при самом отказе диска.
Directory durability, ОС/контроллер/питание и native Windows не доказаны.

Выдача finish/finish_unbound замораживает исход по контракту session. Поздний
валидный ACK не дописывается в уже выданный failed outcome; новый source fault
сохраняется отдельно как `late_fault`, не переписывая архив задним числом.
Собственный поздний отказ writer всё равно оставляет finalization unresolved.
Clean completion требует validated START/END, matching Stop ACK и успешного
реального финального writer-return, принятого receiver owner до close. END, ACK,
файл или опубликованный, но ещё не принятый completion отдельно
не дают успех. После dispatch уже завершённого интервала late fault относится
к состоянию соединения за этой границей; нужно показывать его отдельно.

`close(now_ms)` — receiver-only необратимый **запрос** abandon: он запрещает
admission/dispatch, отмечает текущий pending job как abandoned, но не трогает
writer и не уничтожает ещё не прочитанный result. Disk owner:

- отменяет ещё не взятый job без его исполнения;
- ждёт возврата уже взятого метода, не пытаясь forcibly cancel OS I/O;
- закрывает текущего владельца descriptor после возврата, без добавления
  checkpoint/terminal от close; после успешного finish descriptor уже закрыт;
- публикует реальный operation status, включая поздний отказ, и отдельный
  `mailbox.close_acknowledged`: None — ещё не подтверждено, True — close вернулся,
  False — попытка close завершилась ошибкой. `close_requested=True` не равно True ACK.

Если result slot уже полон, close его не перезаписывает. Owner сначала вызывает
accept (разрешён после close только для ownership/evidence), затем disk-role
execute_one обслуживает отдельный close request. Если close пришёл между
последней проверкой executor и публикацией, применяется тот же bounded порядок.
Драйвер teardown обязан продолжить эти вызовы до известного close ACK; один
close-вызов не обещает завершения. Result после close не допускает START и не
продвигает abandoned session. Новый мост имеет отдельный executor/mailbox;
старый completion физически не доставляется в него через публичный API.

Prepared после bind capability consumed и не закрывает active writer. Сам
executor пытается close не более одного раза; внутреннее закрытие исходного
writer при его собственном отказе остаётся частью принятой зависимости.
Close failure сохраняется отдельным False ACK/`storage.closed=False`, даже
если операция уже имела более ранний failure. Автоматического retry нет.
Если финальный метод реально вернулся после close, его T/U может читаться,
но live session остаётся abandoned/unresolved, а не accepted clean success.
Это не физический Stop, не доказательство durable bytes и не подтверждение
отмены блокирующего системного вызова.

## Проверки и следующие ворота

Из корня materialized exact-source checkout:

```text
PYTHONPATH=host python -m unittest discover -s host/tests -p test_stream_bridge.py -v
PYTHONPATH=host python -O -m unittest discover -s host/tests -p test_stream_bridge.py -v
PYTHONPATH=host:host/tests python -m unittest test_stream_bridge test_stream_endpoint test_stream_wire test_stream_session test_stream_recording test_recording test_session_archive -v
PYTHONPATH=host:host/tests python -O -m unittest test_stream_bridge test_stream_endpoint test_stream_wire test_stream_session test_stream_recording test_recording test_session_archive -v
```

Independent literal envelope/COBS builder формирует входные трассы без вызова
производственного encoder. Тесты проверяют реальные файлы/recover, побитовое
равенство, нулевой/nonzero R, fragmented/coalesced input, поздний ready, failed
START, END/fault до R, partial/unknown writes и их request identity, malformed/
stale DATA/END/ACK, short DATA→DATA, очереди с in-flight, known/unknown source tail,
реальные storage failures, close и fresh attempt без старой причины.
Отдельные concurrency controls используют только test-side Thread/Event с
конечными wait/join (3 s), гарантированным release в finally и cleanup. Они держат
реальный writer на границе метода, затем дают ему выполнить настоящее I/O:
receiver deadlines, DATA/overflow/Stop, cancel/close при prepare/bind/append/
finish/U, поздний prepare acceptance, заполненный result slot, stale/copy/
foreign-generation result, current clock, late operation+close failure.
Это структурная проверка независимости ролей, не wall-clock обещание. Два
исходных независимых ERROR witness запускаются неизменными на новом pairing.
Отдельный bounded replay 100×128 samples с задержанным disk dispatch проверяет
raw equality/backlog, **не** sustained rate или wall-clock performance.

Следующий UI replay должен использовать этот настоящий byte/journal путь и
существующий viewer; здесь GUI/event detector отсутствуют. Display может
coalesce последний блок. Будущий event detector обязан потреблять **каждый**
валидированный raw block перед display coalescing. Это пока не реализовано.
Независимый Critic-B оценивает только этот bounded HOST-мост; native transport,
планирование I/O, Windows, firmware/железо и совместный gate остаются открытыми.

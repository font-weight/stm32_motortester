# Изолированный synthetic streaming flow: Preview → Record → Stop → Open

Авторский кандидат, 19.10.2026. Компонент **не подключён к entry point приложения**,
COM/USB, firmware, production transport или упаковке. Независимая приёмка этой
композиции ещё требуется. Прохождение synthetic тестов не подтверждает работу
Windows, платы, реальную частоту АЦП или физическую полноту разряда. PR95 остаётся
приостановленным. Старые раздельные окна с паузами здесь не склеиваются.

## Состав и происхождение

Ровно пять новых путей: `motortester/stream_controller.py`,
`motortester/gui_stream_controls.py`, два одноимённых тестовых модуля и этот guide.
Ни один существующий transport/protocol/application/dependency/CI path не меняется.
База copy-only: `67c67c3d09a5bcc3e26d78d2d07f1d6f7be31471`, tree
`d47c417aaa48ffddbaf3f9628fd4cd8478bd730e`: 1503 inherited e38a тела и 21 точная
добавленная dependency. Exact source/delta/manifest и результаты будущего review
должны быть привязаны к финальному commit, а не к этому тексту вообще.

Использованы без правок:

- live/event + offline composition `fb00f2680cfa4885a96bf7c4884ada92b8349798`;
  отдельная независимая scoped-проверка PASS 10.00;
- bridge disk-owner split `36ef28f4d258d00f6b78bc61991c3cbc22e2b2a8`;
  отдельная независимая scoped-проверка PASS 9.90;
- endpoint `138a7ba3693ecbc0ed44c24cc42bf243482fb2a4`, отдельный scoped PASS 10.00;
- принятые отдельно storage/session/view/offline foundations.

Эти баллы не переносятся на настоящий joined flow. Позднее согласование текста
PR192 `7ab5996a53921785b3d445c3afccc543c3b07c44` / tree
`02d447fdf4734e1278966b4f933ccbe1726ea708` не изменило HOST request/liveness prose
или 20 wire frames. [Дельта-контекст](https://github.com/font-weight/stm32_motortester/issues/191#issuecomment-6265918223)
не является общим PASS: common gate остаётся BLOCKED_EVIDENCE/null. Device-only
500 ms qualified transaction budget не доказывается таймером GUI/receiver.

## Подключение как компонента

Приложение-владелец может явно создать `StreamController(peer,
event_config=...)`, затем `StreamControls(controller)` и разместить widget в
своём layout. Импорт файлов ничего не запускает. Конструктор controller создаёт
ровно три daemon worker; источника/HELLO/START до явного Preview или Record нет.
`SYNTHETIC_CONFIG` в GUI-модуле — явно помеченный контекст тестовой формы,
**не электрический default**. Отдельного runner/demo/framework не добавлено.

Переданный peer имеет ровно нужные здесь byte операции:

- `read(maximum)` возвращает `bytes` длиной 0…maximum; maximum=544;
- `write(bytes)` возвращает фактически принятый prefix count, 0 при отсутствии
  прогресса или None при неизвестном количестве. Unknown не превращается в full;
- оба callback должны быть неблокирующими/ограниченными по времени; их вызывает
  только receiver, без mailbox lock. Controller не создаёт и не доказывает
  физический FIFO, reset, serial timeout, изоляцию старых writes или USB latency.

Clock-контракт одинаков для threaded и manual исполнения: единственный
авторитетный источник — `clock()` (по умолчанию monotonic milliseconds).
`service(now_ms)` принимает лишь начальное наблюдение в той же шкале, а не
замораживает время на всю итерацию. Для deterministic manual tests необходимо
явно передать изменяемый fake clock. Receiver повторно читает его после каждого
read/write return или exception, перед дальнейшими bridge actions, приёмом
storage completion и dispatch, после detector/raw batch и bounded display copy.
Истёкший deadline, включая точное равенство, обрабатывается раньше позднего
результата. Actual disk/display progress не сдвигает receive deadlines.

Если конкретный write callback выбрасывает exception, controller передаёт
`observe_write` тот же offer token и unknown accepted count (`None`) с новым
наблюдением времени. Так сохраняется известный operation/request без догадки о
принятом prefix. Неоднозначный read exception остаётся без causal request;
уже истёкший deadline сохраняет первичную причину.

Единственный synthetic peer живёт внутри `test_stream_controller.py`: независимый
COBS/header/CRC encoder/decoder, реальные encoded bytes, разделение на части и
реальные accepted-prefix counts. Buttons не вводят normalized DATA/END, readiness
или успешный storage result. Тесты GUI используют тот же controller и peer.

## Владение и границы памяти

1. Receiver создаёт bridge и один владеет его endpoint/session/clock. За итерацию
   он принимает максимум одну ordinary UI command, reserved Stop/close, обслуживает
   текущий monotonic tick, реальный completion, одну byte write offer и одно read.
   Ordinary slot не накапливает команды даже во время обработки предыдущей.
   Stop идемпотентен и не зависит от raw/storage/display backlog.
2. Disk worker вызывает только принадлежащий bridge `execute_one()`. Единственный
   immutable work и единственный result/claim принадлежат принятому executor.
   Receiver принимает фактический result своим **текущим** временем; dispatch-time
   и GUI heartbeat не заменяют tick. `storage_tick()` здесь нигде не вызывается.
3. Каждый полностью validated raw block идёт в detector и fixed circular raw ring
   до любого display coalescing, включая валидный wire drain после storage fence.
   Это не расширяет durable selected prefix. Source range, ring availability и
   записанный `[R,E)` различны.
4. Display worker не чаще 10 Hz строит envelope и передаёт результат своему
   source-bound sink. В нём же создаются/валидируются LiveEventIndex/LiveSnapshot
   и выполняется `post_live_snapshot`. Полная validation 4096 entries не происходит
   на receiver или под его lock. Viewer имеет собственный bounded worker для
   последующего plot/offline read, его GUI poll принимает latest-only результат.
5. Ни один controller lock не охватывает I/O, callback peer, callback sink,
   envelope construction, snapshot validation, wait или join. Под lock только
   короткие slot/reference/status операции. Receiver делает bounded memcpy raw
   ring и shallow tuple copy event metadata; sample loop детектора остаётся на RX.

Бюджеты не увеличены: raw session queue 256 KiB/256 items, **включая in-flight**;
ring 65 536 samples; 4096 retained closed events плюс один pending; envelope≤2048
points; одна pending display copy плюс одна выполняемая, latest-only status.
В GUI остаётся отдельный latest-only snapshot mailbox и bounded viewer worker,
а не Qt signal на каждый raw block/threshold event.

При 128 samples/block и nominal 50 kS/s очередь ограничивается прежде всего
256 items: арифметически это около 0.655 s, не гарантированный disk-stall budget.
Raw quota 64 MiB даёт верхнюю арифметическую оценку около 11.2 min при 50 kS/s;
file metadata/index/overheads учитываются отдельно. Это не unlimited recording.
Effective limits показаны в GUI, включая явно меньшие limits supplied controller.

## Операторский путь и R

- Настроить synthetic polarity, threshold/release hysteresis, pre/post/baseline,
  holdoff, maximum context и rails **до** попытки. Форма показывает codes, samples,
  nominal milliseconds и frozen config revision. Обычный click/keyboard edit
  работает; wheel по combo/spin не меняет настройки и передаётся scrolling.
- Preview начинает новый source без файла. Direct Record сначала выбирает путь,
  затем disk exclusively создаёт и durably prepares journal; START допускается
  только после owner acceptance readiness. R=0 появляется только после реального
  matching START_ACK.
- Record из Preview выбирает следующий целый validated DATA после owner acceptance
  readiness. Frame, частично принятый до readiness, может стать первым, если
  полностью validated после него. Предыдущий preview не дописывается задним числом.
  Click time, pixels и prepare return сами по себе не устанавливают R.
- Cancel файлового chooser ничего не допускает. GUI передаёт только pathname,
  не делает exists/stat/mkdir/open и не разрешает overwrite. Existing destination
  и ошибку создания решает реальный exclusive writer; failure не допускает START.
- Stop имеет reserved slot. Уже принятый partial DATA может завершиться и пройти
  проверку целиком. Stop intent, pending wire request/accepted byte count/complete,
  END, ACK, queued/accepted/checkpointed/finalized boundaries и clean writer outcome
  показаны отдельно. END или ACK поодиночке не означают clean completion.
- Stop/terminal/fault до R оставляет явную unbound attempt. Это не `[0,0)`.
  Matching START_ACK без DATA с clean terminal даёт действительно bound empty
  `[0,0)`; это также не доказательство отсутствия физического разряда.
- Open доступен только после окончания source/drain и settled writer. Он вызывает
  fresh strict reader через существующий viewer worker; активный растущий journal
  не открывается как immutable offline source. Нет отдельной фиктивной Save.

Fresh reopen восстанавливает только сохранённый prefix, identity, индекс,
terminal, known request/first cause и counters. Если событие пересекает R,
новый offline detector видит только post-R raw и явно усечённый baseline/context;
live cache не подмешивается. Читаемый terminal после failed fsync/close не меняет
факт failed writer return. Поздний bridge fault остаётся отдельным evidence.

## Навигация, zoom и неполные контексты

Recent detector хранит global increasing ordinals и явный metadata eviction count.
Ringing/context может начаться раньше самого старого сохранённого в RAM sample.
Выбор такого события сохраняет **его исходные** `[start,end)`/flags и сообщает
unavailable; диапазон не заменяется более новым и не называется потерей архива.
Выбранный ordinal после metadata eviction остаётся stale до нового явного выбора.
Полный saved raw и whole-file event index доступны после Stop + Open. All-history
seek во время записи не реализован и не требуется этому ограниченному этапу.

Threshold context/rails/left-right truncation/pending/unknown tail не являются
классификацией горения, затухания, цилиндра или гарантией физической полноты.
Нет external trigger и датчика первого цилиндра. X/Y wheel zoom, pan, Follow,
Previous/Next и Whole после reopen используют existing viewer; vertical-only zoom
не меняет raw selection. На 420×420 и 13/22 px содержимое доступно через обе
полосы scrolling; отсутствие horizontal overflow не заявляется. Существующий
отдельный P2 большого main-window при 22 px этим компонентом не закрывается.

## Close и новая попытка

`close()` немедленно закрывает public command/display admission и ставит reserved
owner fence. Это не прерывает уже выполняемый bounded receiver callback. Receiver
на следующей итерации fences bridge; disk owner закрывает descriptor после возврата
выполняемого OS I/O. GUI не ждёт join. `snapshot().worker_unresolved` и `join(0)`
позволяют внешнему владельцу честно видеть незавершённую работу; `join()` не нужно
вызывать на GUI. Скрытие widget не отменяет fsync. Нельзя создавать цепочку новых
writer attempts, оставляя заблокированные старые в этом controller.

Повторная попытка требует явного Preview/Record, нового nonce/attempt/recording ID
и settled old descriptor/result. Preview-only clean Stop использует реальный
END + matching ACK без выдуманного recording-finalized flag. У записи дополнительно
нужен реальный successful finalized writer + disk close acknowledgement; clean
unbound cancellation без START тоже можно завершить и явно начать заново.

Любой fault/unknown TX навсегда блокирует restart **на том же controller**.
Новый nonce сам по себе не доказывает изоляцию: после fault необходима отдельная
изолированная byte boundary и settled старые workers, устанавливаемые внешним
владельцем, а не этим компонентом. Retry/reconnect/reset и automatic restart нет.
Stale display handles/results старого source не восстанавливают новый source.

## Авторские проверки и воспроизведение

Python 3.12.14; retained PySide6 6.12.0 / pyqtgraph 0.14.0 runtime, Linux offscreen.
Установка пакетов, actual COM/board I/O, реальный Windows и publication не выполнялись.
Из корня coherent source:

```sh
PYTHONPATH=host python -m unittest discover -s host/tests -p test_stream_controller.py -v
PYTHONPATH=host python -O -m unittest discover -s host/tests -p test_stream_controller.py -v
PYTHONPATH=host QT_QPA_PLATFORM=offscreen \
python -m unittest discover -s host/tests -p test_gui_stream_controls.py -v
# Повторить предыдущую команду с python -O.
```

Команды предполагают уже подготовленную Python-среду с установленными GUI
зависимостями. Если зависимости лежат отдельно, вызывающий добавляет свой каталог
в PYTHONPATH; XDG_CACHE_HOME при необходимости указывает на выбранный им cache.
Переменная STREAM_FLOW_EVIDENCE_DIR может направить PNG в заранее созданную папку;
без неё тест использует собственный temporary directory. Точные локальные команды
и retained runtime paths принадлежат отдельным авторским evidence logs.

Focused suite: 31 controller methods и 11 real-Qt methods на момент подготовки
этого guide. Проверяются actual buttons, direct/nonzero-R/empty/no-R, partitioned
bytes и accepted-prefix writes, partial readiness/tail, malformed END/missing ACK,
exact fresh raw/identity/digest/terminal/first-failure equality, crossing-R reindex,
capacity fence, current-time deadlines, fresh identities, latest/coalesced display,
4096-event eviction/unavailable original range, real append/prepare/fsync/finish/read
barriers, stale source/results, GUI heartbeat, frozen config, keyboard/wheel edits,
plot wheel, X/Y selection/follow и 13/22 px narrow scrolling. Test-side Events имеют
finite release/join cleanup; failed assertions не оставляют вечные test workers.

Большинство stdlib cases используют явно injected manual receiver clock/roles: это детерминированная
проверка ordering/bounds, не доказательство actual scheduling. Отдельные barrier tests
используют actual owner/disk/display threads с explicit clock или monotonic clock;
GUI heartbeat сам по себе не считается receiver deadline service.

Отдельный `test_finite_nominal_cadence_load...` использует **независимый Python producer
thread**, wall-clock paced на 640×128 samples при целевом интервале 2.56 ms, одновременно
с actual GUI/receiver/disk/display. Это не pull-only источник, но его synthetic FIFO
и конечные offered bytes — test assertions, не physical adapter evidence. Test печатает
`FINITE_JOINED_LOAD`: actual offered interval p50/p95/max, offered/received/queued/
accepted/checkpointed/finalized counts, maximum backlog, retained events, coalescing и
owner max service gap. Generic universal timing threshold не вводится.

Предварительное наблюдение 11:20 UTC: offered=received=queued=accepted=checkpointed=
finalized=81 920 samples; очередь в конце0, peak12 items/3072bytes;5120events,
retained4096;18 display constructions. Producer span1.637s, interval p50/p95/max
2.542/6.121/19.504ms, owner max observed gap17ms. Это конечное Linux-наблюдение с
джиттером, **не подтверждение 2.56ms deadline, sustained50kS/s, USB или Windows**.
Финальные normal/-O логи сохраняют собственные реальные измерения и могут отличаться.

Ранние failed harness логи сохраняются отдельно: неверный выбор v1 `recover` вместо
`recover_attempt`; неверные имена selected_start/device_transmitted fields; ожидаемое
имя capacity_raw вместо capacity_blocks для item cap; ошибочное ожидание fsync на
bind_start, где его нет; oversized single read вместо bounded iter_raw; ожидание fault вместо предусмотренного discard чужого nonce; stale GUI
button polling, None как empty plot, narrow field growth. Никакие dependency API,
лимиты или старые результаты ради этих проверок не менялись. Form field growth
исправлен в новом GUI-файле, чтобы каждый spin оставался узким и доступным.


## Узкая коррекция после независимого review

Предшественник `15d44121083695cc53d44f8404453c512fd1221c` сохранён вместе с
исходными failing witnesses. Его review потребовал исправить три конкретных
дефекта: stale iteration time после callback/owner batch, потерю известного
STOP request при write exception и слабый контраст девяти numeric fields.
Эта дельта ограничена теми же пятью новыми путями; принятые dependencies не менялись.

Добавлены real-journal regression cases для read DATA/empty/error и write
full/partial/zero/unknown/error при deadline и после него, elapsed completion/
detector/display-copy work, default-clock manual mode и actual receiver threads.
Отдельно проверяются persisted first failure и известный STOP request против
неоднозначного read. Исходные negative methods критиков запускаются без изменения
их bodies; ускоренный manual fixture явно получает свой clock.

Локальная numeric-field палитра сохраняет native step arrows, wheel protection и
keyboard/click edits. Проверяются все девять полей в enabled/disabled/focus состояниях
при 13 и 22 px: palette contrast и фактические PNG pixels, scroll reachability и
step click. Численный contrast diagnostic не является полной accessibility
сертификацией. Финальная независимая оценка относится только к точному successor
commit после отдельной фиксации, а не переносится с компонентов или author tests.

# Изолированный provisional HOST endpoint stream 0.2

## Статус и точная основа

Это отдельный HOST codec/state-прототип, **не подключённый к приложению**.
Он выполняет реальные проверки входных байтов и состояния HOST на синтетическом
потоке ответов. Это не реализация firmware, не COM/CDC-транспорт и не аппаратное
доказательство. Общий контракт по-прежнему **BLOCKED_EVIDENCE, score null** до
независимых оценок точных реализаций обеих сторон. Авторские тесты не заменяют
Critic-B; текущая локальная версия требует его отдельного exact-source verdict.

- База HOST: `e38a36004befe453923f3e86165b7fd6d83076d9`, дерево
  `ffab972586afff8d0a4e13a1e73d56af49190292`.
- Нормативный проект: PR192,
  `08172114403273ecfac2fb1e8e49d92f9c68756f`, дерево
  `6904eee03586eb35fe65c1e5e65fa1cb1e82797e`.
- `protocol/wire-v0.2-stream.md`: 24 746 bytes, SHA-256
  `174aed3c570a204d865c1a446c7878c628a972222d00ef8f60d33ad9c92c998a`.
- Сохранённые реальные vectors из родителя
  `72d400660a0af896ebb3810a8c9a22166d7fa78a` не изменились; SHA-256 файла
  `e2b1a0db86143f2b787d70ed93269cf64c74c5a7867684f51b10b7210e375b0b`.
  В тест скопированы фактические `framed_hex` всех 20 кадров. Тест не запускает
  генератор и не строит положительные retained bytes через проверяемый codec.
- Последующий PR192 `770c468491b4c469336db7fa9ee3ee7653013481` добавляет ссылку
  на отдельные reference deadline examples. Нормативные правила таймеров не
  изменились; эти примеры не импортируются и не подменяют тесты endpoint.

Ровно пять новых файлов: `stream_wire.py`, `stream_endpoint.py`, их два теста и
этот документ. Старые wire 0.1, архивы, UI, пакетирование, CI и shared protocol
не меняются. Нет новых зависимостей, файлового/сетевого/аппаратного I/O, потоков,
тайных повторов, ожиданий или автоматического START.

## Какие числа закреплены в проекте

Все ниже — константы **предлагаемого** wire 0.2, не принятое изменение wire 0.1:

- COBS + `00`; CRC-32/ISO-HDLC little endian, header 24 bytes, payload ≤512;
  decoded ≤540, encoded ≤543 без delimiter, wire frame ≤544 bytes.
- Один канал, config/profile 1, unsigned 12-bit codes в u16LE,
  Fs = 50000/1, максимум 128 samples. Частота номинальная, не измеренная.
- HELLO/GET_CAPS/START/STOP: абсолютные 2000 ms от admission pending slot.
- Первый и следующий DATA-or-END: 1000 ms от START_ACK / последнего полного
  валидного DATA. Nonempty partial frame: 1000 ms от первого байта.
- Recovery isolation: отдельные абсолютные 2000 ms от обнаружения fault,
  до admission нового HELLO. Recovery delimiter входит уже в бюджет HELLO.
- Ordinary counter threshold `0xffffff00`; 256 значений зарезервированы для
  ограниченного tail/control. Не более 32 reserved DATA и 4 reserved controls.
  u32 не переходит через wrap. Exclusive u64 end может быть UINT64_MAX;
  этот предел не может стать началом следующего непустого DATA.

Device drain и late-Stop response имеют свои 500 ms от событий **в устройстве**.
HOST не знает точный момент receipt STOP или acquisition-stop. Он не выдумывает
такую отметку из host write completion. Эти device deadlines требуют отдельной
реализации и тестов A. Здесь late Stop ограничен наблюдаемым HOST request budget;
принятый END остаётся неизменным при таймауте ACK.

## Граница будущего адаптера

`Endpoint` вызывается последовательно одним владельцем. Все методы получают
явный `now_ms`: целое монотонное число миллисекунд, от 0 до
`2**63 - 1`. Создание каждого нового deadline отдельно проверяет остаток
диапазона; overflow admission не занимает slot, а невозможность создать
cleanup budget требует reset. Последний представимый deadline остаётся
достижимым для tick, без wrap или продления. `bool`, float (включая NaN/inf), отрицательные, обратные и
слишком большие значения отвергаются `ValueError` без изменения состояния.
Одинаковое время нескольких dispatch допустимо.

Каждый dispatch сначала истекает применимые таймеры при `now >= deadline`.
Только полностью полученный, проверенный и сопоставленный ответ **раньше**
deadline принимается. Timestamp отражает момент выполнения всей проверки HOST,
а не предположение, когда байты попали в OS buffer. Адаптер обязан планировать
`tick(next_deadline_ms)` независимо от I/O. Поздний wake обрабатывает timeout
раньше запоздавшей completion. Этот модуль сам не создаёт scheduler/thread.

Основной интерфейс:

1. `hello(fresh_nonce, now_ms)` допустим только для нового `NO_SESSION`.
   Адаптер обязан выбирать новый ненулевой nonce, не использовавшийся для
   соединения ранее, в том числе после замены объекта Endpoint. Endpoint не
   генерирует случайность и не хранит бесконечную историю nonce; он отвергает
   немедленное повторение текущего nonce. Nonce — корреляция, не аутентификация.
2. `get_caps(now_ms, action_id=0)` проверяет отдельный CAPS. Успех HELLO не
   разрешает START без CAPS. Несоответствующие версия/capability/rate/config
   не запускают fallback, ARM или START.
3. `start(now_ms, action_id=0)` — явное Preview/Record-действие, допускаемое
   после CAPS. Generation равен admitted START request ID. START_ACK должен
   буквально подтвердить generation/config/first_index. Только событие
   `Started` подтверждает этот device report; оно не подтверждает доставку DATA.
4. `stop(now_ms, action_id=0)` — один отдельный slot. Он возможен уже при
   pending START, но не при HELLO или только planned generation. Он не
   отменяет ordinary deadline. Второй Stop для поколения отвергается локально
   без байтов; новый session требует свежего HELLO.
5. `next_write(now_ms)` выдаёт не больше одного frozen `WriteOffer(token,
   request, data)`. Пока offer не завершён, нового offer нет. Единственный
   владелец finishing suffix не перемежается со STOP.
6. `observe_write(token, accepted, now_ms)` принимает один точный целый
   accepted-prefix count в пределах текущего offer. Каждый token одноразовый.
   Ноль не сдвигает sequence/deadline; первый принятый **encoded frame** byte
   commit-ит sequence. Принятый recovery delimiter отдельно учтён и сам не
   commit-ит sequence. Partial accepted count оставляет только suffix.
   Неизвестный, отрицательный, boolean, дробный, слишком большой count либо
   неправильный/replayed token приводит к recovery, без скрытого retry.
7. `receive(bytes, now_ms)` получает куски до 544 bytes; границы chunk не
   обязаны совпадать с границами frame. Более крупный OS read адаптер разбивает
   до вызова; overflow, неверный тип или malformed active frame fail closed.
8. `transport_fault(now_ms)` начинает recovery. `adapter_fault('storage' |
   'capacity', now_ms)` сохраняет первую локальную причину и возвращает
   `StopRequired`, если Stop ещё не был admitted. Это просьба будущему
   адаптеру, не скрытое выполнение команды и не свидетельство работы диска.

Для каждого WriteOffer адаптер выполняет ровно одну неблокирующую/ограниченную
оставшимся deadline запись и передаёт точное число принятых байтов. Он не должен
отправлять копию offer повторно. Новый offer с тем же suffix после accepted=0
является продолжением того же запроса с прежним absolute deadline, не повторным
запросом. Сам факт полного accepted count не доказывает удалённое выполнение.

### Планирование прямой Record до START

`plan_start(now_ms)` после CAPS выдаёт единственный frozen `StartPlan` с
точными nonce, будущим generation/request ID, intended sequence и CAPS.
Это **не admission**: pending slot, START bytes, request timer, generation
активного потока и acquisition ещё отсутствуют. Другие ordinary requests
локально блокируются, чтобы ID не изменился, пока writer готовит storage.

После writer-ready вызывается `start(now_ms, action_id, plan=plan)` с **тем
самым объектом**, а не копией. Только теперь starts 2000 ms admission deadline.
`cancel_start_plan` не пишет байты и не означает физический Stop. Recovery/новый
source инвалидируют plan. Нельзя угадать generation=3, заменить prepared
identity после ACK или считать reservation успешным START. Preview без
предварительной записи может использовать start без plan. Никакая часть
session/storage автора здесь не импортирована и не изменена.

### Recovery и запрет предполагаемой очистки

При ошибке `RecoveryRequired` содержит fixed trigger, cleanup deadline и
immutable `RecoverySnapshot`: nonce/generation, проверенные received end/CRC,
принятый END, первую причину, фактические ordinary/Stop identities, известные
accepted counts и незавершённый offer. Даже если причина появилась раньше,
последний timed-out pending identity не теряется перед очисткой slots.

До нового HELLO нет разрешённых writes. Адаптер должен доказать, что ни один
старый write не добавит suffix **после** нового delimiter и что FIFO ordering
сохранён. Только после этого `isolation_complete(new_nonce, now_ms)` допускает
новый HELLO с предшествующим `00`; его собственные 2000 ms начинаются здесь.
Cancel/reopen CDC сами по себе не доказывают isolation и не отзывают ранее
принятые байты. Late callbacks не исправляют прежний исход.

`isolation_failed` или expiry cleanup переводят в `RESET_REQUIRED` без HELLO.
`reset_confirmed(new_nonce, now_ms)` допустим только при этом состоянии и
означает отдельное утверждение адаптера: выполнен физический reset/reconnect
**и проверены startup/isolation**. Этот модуль не умеет сам подтвердить такое
событие. Ни timeout, ни nonce, ни этот вызов не выдаются за измерение остановки
ADC. Отсутствующий HELLO_ACK остаётся неизвестным/failed handshake.

Handshake допускает только bounded старый tail: ≤4096 received bytes,
≤64 delimiters/frames, accumulator ≤543 bytes и независимые partial/request
таймеры. Это явные HOST resource limits. CRC-valid несовместимая версия,
неверное направление или неизвестный тип прекращают сессию; старый nonce не
создаёт новую. Только точный fresh-nonce HELLO_ACK (seq/request 1) завершает
handshake. Никакой DATA не публикуется в этой фазе.

## Валидированные наблюдения и evidence boundaries

Все наружные dataclass-наблюдения frozen/slots; DATA содержит immutable tuple.
Нет внутренней очереди событий или истории. Адаптер получает tuple результата
текущего вызова и сам ограничивает хранение/очередь, иначе собственная очередь
адаптера могла бы быть неограниченной.

- `Negotiated`: nonce, firmware build identifier и отдельные CAPS.
- `Started`: nonce/generation/config/проверенный first_index, admitted request
  с action_id и CAPS. Это удобная граница будущей immutable StreamIdentity.
- `ValidatedData`: nonce, transport sequence, generation/config/absolute index,
  точные sample codes и rolling full-prefix CRC32 **от индекса 0**.
- `ValidatedEnd`: один неизменяемый terminal + его sequence/nonce. Проверяются
  received exclusive end, CRC, last DATA sequence, generation/config,
  reason/flags, stop_request join. Unknown produced end остаётся `None`;
  известная потерянная часть не превращается в неизвестную и наоборот.
- `StopAcknowledged`: точный Pending/action_id, тот же END, checked StopAck и
  `quiescence_reported=True`. Это утверждение устройства по wire, не физическое
  измерение. ACK без END не завершает поток. Bit2 END запрещает ACK.
- `FirstFault`/`Fault`: первая причина неизменяема. Последующий clean END/ACK
  её не стирает. `NEEDS_HELLO` означает протокольное окончание, не clean archive.

Обычный DATA имеет ровно 128 samples. Короткий блок помечает конец DATA-части;
любой следующий непустой DATA отвергается, даже при contiguous index. Допустимы
пустой stream и immediate byte-identical duplicate последнего принятого frame.
Duplicate не меняет CRC/index/deadline/reserve counters. Любой иной старый,
конфликтующий, пропущенный или reordered frame приводит к recovery.

Failure END может остаться с stop_request_id=0, если поздний STOP пришёл после
начала его отправки. Один последующий ACK должен ссылаться на тот же END.
Новый END и rearm запрещены. При Stop timeout END сохраняется как evidence.

`received prefix`, `selected interval [R,E)` и `durable prefix` — разные вещи.
Endpoint ведёт только received prefix. Будущий session adapter атомарно выбирает
следующий целый validated DATA после writer-ready; stream_model-нормализация
возможна только **после** этих wire-проверок. Selected CRC/SHA не равен
whole-prefix CRC при R>0. Если END/fault победил до выбора, session должен
выдать no-established-R. Endpoint не создаёт архив, не устанавливает R и не
утверждает durable finalization. Storage/capacity tests здесь подтверждают
только сохранение first cause при дальнейшем валидном wire-приёме.

### Точная причина валидированного ERROR

`FirstFault.causal_request: Pending | None` — дополнительная immutable ссылка
на точный pending request, отклонённый первым валидированным ERROR.
`RecoverySnapshot.causal_request` отдельно сохраняет запрос текущего ERROR,
если первая причина возникла раньше. Поля по умолчанию равны `None`;
существующие `Fault.first`, `RecoveryRequired.snapshot`, `ResetRequired.snapshot`
и `last_recovery` открывают этот контекст без новой очереди или parser.

Причина заполняется только после штатной проверки nonce, sequence, request ID,
полного TX, ERROR rejected_type и generation. Когда одновременно pending
START3 и STOP4, соответствующий ERROR сохраняет именно START3 или STOP4,
вместе с action_id и known accepted counts. Оба lane snapshots остаются
отдельными: наличие двух pending requests не делает подтверждённую причину
неизвестной и не позволяет выбирать её по порядку slots.

`FirstFault` не переписывается поздним ERROR: например, storage fault остаётся
первой причиной с `causal_request=None`, а следующий точный STOP ERROR записывает
свой запрос только в recovery snapshot. После очистки slots, cleanup timeout,
reset и нового HELLO сохранённый snapshot не теряет эту связь. Поле не является
универсальным определителем виновного запроса: ambiguous read/CRC/transport,
неверный или просроченный ERROR не получают выдуманную causal identity.

Это узкая additive поправка к сохранённому `02225ad1bcb5c342fed8cb4083691cd06ab8b024`:
раньше валидированные START/STOP ERROR с одинаковым кодом сохраняли оба slots,
но теряли известный отклонённый request. Два неизменённых regression tests
показывают assertion failures на прежнем source и успешную точную атрибуцию
на successor в normal/-O. Wire bytes, codec, state transitions и source/C-codec
семантика этой поправкой не меняются; проверяется только bounded evidence API.

## Ограниченность

В памяти: один ordinary request, один Stop, один StartPlan, один активный
WriteOffer, один предыдущий принятый encoded frame, один END, один first fault,
один последний recovery snapshot и accumulator ≤543 bytes. Request frame
≤544 bytes, recovery HELLO с delimiter ≤545. Snapshot может удержать старый
bounded offer, но не цепочку snapshots. Непринятые хвосты не накапливаются.

Один receive input ≤544 bytes, output ≤64 immutable observations; invalid
input не создаёт историю. Offer token — монотонный u64 без wrap; exhaustion
fail closed. Sample/transport/request counters также не wrap. Reserved DATA
принимаются максимум 32 раза, controls максимум 4. Это проверка наблюдаемого
wire reserve, а не доказательство размера device queue или момента, когда
устройство сертифицировало данные; для них нужны firmware tests.

## Воспроизводимая проверка

Из корня worktree, без установки пакетов:

```sh
PYTHONPATH=host:host/tests python -m unittest -v test_stream_wire test_stream_endpoint
PYTHONPATH=host:host/tests python -O -m unittest -v test_stream_wire test_stream_endpoint
```

Тесты используют standard library и явные fake clocks, без Qt и aggregate suite.
Включены actual retained 20 frames, независимый builder отрицательных CRC-valid
кадров, envelope/direction/version/length/reserved, capabilities, полный/короткий/
пустой поток, after-short/after-END, stale/gap/reorder/duplicates, CRC от 0,
END/ACK mismatches, START/Stop races и START_FAILED, one late Stop, known/unknown
tail, storage-first-cause, exact write counts/tokens, отсутствие sequence gaps
при accepted=0, just-before/equal/after request/DATA/partial/recovery deadlines,
исходный admission-origin контрпример 0/900/1500/2700 ms, bound malformed input,
clock types/overflow и counter edges. Для крайних counters тесты явно задают
private state, затем используют public dispatch; это не длительный прогон
4 миллиардов кадров и не throughput measurement.

Не проверены: реальное устройство, hardware quiescence, DMA/ADC, sustained USB,
Windows/COM, disconnect физического кабеля, планировщик адаптера, диск/durability,
Record UI, сборка/упаковка, A endpoint и общий A/B conformance gate. Следующий
шаг — независимое Critic-B exact-source review этого изолированного HOST
прототипа; интеграция требует отдельного разрешённого этапа и общей приёмки.

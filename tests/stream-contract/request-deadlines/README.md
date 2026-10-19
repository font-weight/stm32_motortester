# Исполняемые примеры HOST request deadline wire0.2

Это ограниченная **pure-state проверка предложенного правила**, а не реализация
HOST и не проверка соответствия реального endpoint. Все события и миллисекунды
синтетические. `host/**`, firmware, USB, Windows, реальное время и аппаратура
не изменяются и не запускаются. Статус общего решения остаётся
**BLOCKED_EVIDENCE** до требуемого независимого ревью обеих сторон.

Источник: `protocol/wire-v0.2-stream.md`, раздел
`Host request admission, transmission and response deadline`, commit
`174148a550c385785c8e28ba424aef09995bff69` (локальный снимок; опубликованная
версия с тем же деревом: `08172114403273ecfac2fb1e8e49d92f9c68756f`). SHA-256 источника записан в
`source-provenance.json`. Тесты не загружают изменяемый checkout: этот snapshot
позволяет точно отличить проверяемое предложение от последующих редакций.

## Запуск и квитанция

Требуется Python 3.10+; зависимости только из стандартной библиотеки.
Сеть, установка пакетов, реальные ожидания и транспорт не нужны.

```sh
cd tests/stream-contract/request-deadlines
PYTHONDONTWRITEBYTECODE=1 python3 run.py --receipt receipt.json
```

Либо из корня репозитория, задав путь для квитанции:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/stream-contract/request-deadlines/run.py \
  --receipt tests/stream-contract/request-deadlines/receipt.json
```

Минимальный повтор без квитанции:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

`run.py` возвращает ненулевой код при любом failure/error и записывает версию
Python, число test methods/subtests, список tests, результаты и хеши всех
исходных файлов. Поле времени — фактические часы среды запуска, не источник
синтетических monotonic timestamps. `receipt.json` доказывает только запуск
этой модели; `successful: true` не является PASS по quality gate.

## Что проверяется

- HELLO, GET_CAPS, STREAM_START, STOP: deadline ровно `t_admit + 2000`;
  atomic admission предшествует даже первому нулевому write. Занятый слот
  отклоняет действие без скрытой очереди, нового identity или отправки.
- Equality `now == deadline` всегда timeout. Отдельные матрицы для полной
  validation response и последнего write: `1999 / 2000 / 2001` ms, четыре
  команды, включая большой clock epoch без потери точности float.
- Отсутствие I/O, zero acceptance, partial/trickle acceptance, завершение
  полной отправки и очередь STOP не продлевают бюджет. Scheduler получает
  `next_timer_ms` независимо от I/O; поздний wake сначала применяет expiry.
- Только полный, валидированный и сопоставленный response завершает запрос.
  Невалидированное содержимое OS buffer, partial/late/old-nonce response
  не считается своевременным ответом. Отдельная независимая literal-таблица
  проверяет четыре пары command → response и 12 неподходящих сочетаний, не
  используя mapping модели или её helper для формирования входного response.
- STOP имеет собственный admission deadline, не отменяет ordinary timer и
  не вклинивается в частично переданный ordinary frame. Более ранний timeout
  сохраняет именно identity исходного запроса, даже когда STOP уже queued.
- Первый cause и условный проверенный sample prefix сохраняются. Известное
  число принятых байтов не обнуляется; при неизвестном числе оно явно unknown.
  Не принятый хвост больше не отправляется; same-session retry запрещён.
- Recovery delimiter входит в HELLO budget; его принятие ещё не фиксирует
  transport sequence. Sequence фиксируется первым encoded byte HELLO.
- До recovery HELLO требуется доказанная изоляция старого writer и FIFO.
  Cleanup имеет отдельный absolute budget 2000 ms от recovery entry, включая
  время до первого cancel call. Equality, silence, late wake и trickle проверены.
  При неуспехе требуется physical USB reset/reconnect с validated startup;
  новая HELLO не допускается через writer с неопределённым состоянием.
- После своевременной изоляции новый nonce и admission HELLO начинают новый
  request budget. Старый cause остаётся в старом session object.
- Checked arithmetic fail-closed; последний представимый deadline;
  regression clock и float timestamps отвергаются.

Две request-матрицы имеют 36 response boundary и 64 write partition subcases;
cleanup boundary имеет 9 subcases. Это конечные перечисления, не доказательство
по всем состояниям или interleavings приложения.

## Граница модели и вопросы интеграции

1. `deadline_model.py` — тестовый oracle, его нельзя подключать в `host/**`
   как реализацию транспорта. Request payload/COBS/CRC, generation machine,
   ERROR responses, DATA/END liveness и sequence validator не реализованы.
   `validated=True` представляет уже выполненную проверку внешним endpoint.
   Неподходящий response здесь только не закрывает timer; настоящий endpoint
   может быть обязан немедленно начать recovery по другим правилам.
2. Identity содержит desired connection nonce; для HELLO это свежий nonce
   из payload/ожидаемого ACK, а не нулевой nonce заголовка исходящего HELLO.
   Размеры frames — абстрактные bounded byte counts, не byte-level fixtures.
3. Write event означает наблюдаемое принятие bytes в указанный момент.
   Проверка exact-boundary запрещает новый send/принятие результата как успеха
   после expiry. Она **не доказывает отмену уже принятых OS/device байтов**.
   Поздний реальный callback может уточнить старый accepted-byte count;
   он не оживляет запрос и должен быть изолирован до recovery delimiter.
   Реальный adapter, cancellation и FIFO требуют отдельной проверки Dot B.
4. `cancelled_suffix_bytes` означает известный ещё не принятый хвост, который
   больше нельзя выдавать в старый TX path, а не доказанную физическую отмену.
   Если accepted-byte count неизвестен, точный размер хвоста тоже `None`.
5. `next_timer_ms` проверяет чистую scheduler obligation. Реальный timer wake,
   ограничение blocking writes, OS scheduling/cancellation, precise receive
   validation timestamp и отсутствие поздних writer append здесь не доказаны.
6. При одинаковых deadlines двух слотов oracle выбирает первым ранее admitted
   запрос. Текст требует expiry до completions, но не задаёт tie-break двух
   одновременно истёкших request identities. Это локальная детерминизация,
   не дополнительное нормативное правило; тесты не требуют её от endpoint.
7. Один synthetic test подаёт успешный STOP_ACK при незавершённом ordinary
   request исключительно для проверки независимости timers. Полный stream
   validator обязан отдельно обеспечивать порядок START_ACK/END/STOP_ACK.
8. int64-range monotonic milliseconds — bounded пример арифметики модели;
   контракт не навязывает реализациям этот тип или разрешение часов.
9. Test `test_cleanup_failure_does_not_erase_original_request_fault` использует
   **два несвязанных объекта**: старую request-модель и отдельную cleanup-модель.
   Он проверяет неизменность старого объекта после synthetic cleanup failure,
   но **не доказывает перенос или сохранение first cause реальным recovery
   controller**. Интеграция состояния, diagnostics и recording metadata при
   переходе recovery требует отдельного native HOST теста.

Следующий шаг: независимое ревью точного proposal SHA и пакета, затем перенос
сценариев в native HOST transport tests владельцем Dot B с реальным scheduler,
transport adapter и validator. Успешная модель не закрывает этот шаг.

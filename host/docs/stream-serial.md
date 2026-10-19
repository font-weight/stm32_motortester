# Изолированный receiver-owned serial lifecycle

Авторский opt-in компонент, 19.10.2026. Это следующий узкий слой поверх точного
принятого stream flow `07d78972b435a3a51d7987b2375a474552c61917`, tree
`18bb66ea039111027ec2b1a85a68b016838a9340`. Независимая приёмка нового слоя требуется
отдельно. Приложение, GUI, packaging и entry point не подключаются к нему.
Реальные COM/USB, драйвер, плата и Windows здесь не запускались.

Пять путей: новые `motortester/stream_serial.py`, `tests/test_stream_serial.py`,
этот guide; узкие изменения `motortester/stream_controller.py` и его теста.
`transport.py`, endpoint, bridge, journal, session, view и GUI остаются неизменными.
Принятый `SerialTransport` используется целиком, включая его pure validator,
unknown-write semantics и сохранение handle после неудачного close.

## API и явное подключение

- `SerialSettings(port, baudrate=115200, read_timeout=0, write_timeout=0.05)` —
  immutable value. Port задаётся явно; URI, пустое имя, пробелы по краям,
  control characters, bool вместо чисел, nonfinite/out-of-range settings отвергаются.
  Read timeout ровно ноль; write timeout положительный и не больше принятого
  transport limit в одну секунду. Baud — запрошенная настройка, не доказательство
  пропускной способности или согласованной конфигурации реального прибора.
- `SerialStreamPeer(settings, backend_factory=...)` хранит intent. Конструктор,
  import и validation не создают transport/backend, не открывают и не сканируют
  порты. В тестах factory всегда fake; без него существующий transport использовал
  бы свой lazy pyserial factory лишь при явном owner open. Это не разрешение на
  реальный запуск в рамках данного synthetic gate.
- `StreamController(peer, event_config=..., managed_peer=True)` явно включает
  этот lifecycle. Он стартует disconnected. Обычный внешний byte peer сохраняет
  прежний `managed_peer=False`; controller не присваивает себе его open/close.
- `connect()` ставит одну ordinary command. Успешное открытие само по себе не
  отправляет HELLO, GET_CAPS, START, STOP, не создаёт writer и не устанавливает R.
  Далее требуется отдельный явный Preview или Record с прежними правилами.
- `disconnect()` — reserved admission fence. Он доступен при заполненном command
  slot и отменяет ещё не принятый Connect. После текущего callback receiver
  прекращает дальнейшие wire operations и сам закрывает serial handle.
- `retry_close()` — одна явная cleanup-only попытка закрыть тот же retained handle.
  Она доступна только после подтверждённой ошибки close/failed-open cleanup,
  без другого выполняющегося close. Никакой автоматической петли повторов нет.
- `close()` навсегда запрещает connect/preview/record и запрашивает cleanup.
  Неудачный serial close **не завершает receiver**: `retry_close()` остаётся
  разрешённым для cleanup. `join(timeout)` возвращает false, пока serial handle,
  disk result/close или выполняющийся display callback не settled.

`ControllerStatus.serial` — immutable snapshot с выбранным port, generation,
state, handle_settled, first_error и текущим close_error. `serial_call` и
`serial_operation_id`/`serial_operation_generation` показывают текущую owner operation
и её target generation, включая ещё не вернувшийся open; `serial_unresolved`
означает retained handle или выполняющийся callback, а не device state.
`can_connect`, `can_disconnect`, `can_retry_close` отражают доступные действия.
Отдельные bridge/session/storage поля по-прежнему различают R, raw prefix,
END, STOP_ACK, writer return, finalization uncertainty и первое нарушение.
Закрытый serial не делает незавершённый writer завершённым.

## Владение, время и поздние результаты

Всё создание `SerialTransport`, factory/backend и все open/read/write/close
выполняет один receiver. GUI/public methods только ставят bounded intent.
Disk owner по-прежнему вызывает только executor.execute_one; display строится
отдельно. Нового потока, generic runner или второго state-machine framework нет.
Ни один controller lock не охватывает I/O, clock callback, wait или join.

Перед и после каждого serial/lifecycle callback читается тот же авторитетный
monotonic clock, что в принятом controller. Manual tests явно inject свой clock;
`service(now)` не замораживает время. Read запрашивает максимум544 bytes;
empty read означает отсутствие новых данных. Короткий/нулевой write сохраняет
известный accepted prefix. Исключение или недопустимый count остаётся unknown
через тот же offer token: повторная отправка неизвестного prefix не допускается.
Неоднозначная ошибка read не получает выдуманного causal request.

Значения timeout — **запрос к OS/backend**, а не гарантированная длительность
callback. Open/close также могут зависнуть в OS. Немедленный public fence не
прерывает такой вызов и не создаёт второго owner для force-close. Snapshot
показывает pending operation, admission остаётся закрытым, join — false.
Когда callback вернулся, receiver повторно проверяет disconnect/shutdown fence
до следующей операции. Поздний successful open закрывается, а не возрождает
controller. Поздние read bytes не проходят в retired source. Фактический count
или error старого write сохраняется для старого offer, но не разрешает следующий
write или приём ACK. Истёкший deadline, включая равенство, остаётся первым.

Новый connection generation допускается только после settlement предыдущего
serial handle, disk work/result/close и display work. Late commands/bytes не
переносятся в новую source identity. Успешный handle close/open **не доказывает**
device quiescence, физический reset или отсутствие старых OS/device queued writes.
После fault/unknown TX acquisition restart в том же controller навсегда fenced,
даже если пользователь явно закрыл и снова открыл порт. Новый connected handle
в таком случае не получает HELLO/START автоматически. Никакого synthetic reset
receipt или обхода через свежий nonce нет. Реальная isolation policy вне этого слоя.

## Disconnect и durable запись

Для активного источника disconnect сообщает transport loss и даёт принятому
bridge выполнить bounded failure drain и journal finalization без дальнейшего
serial I/O. Пока это не settled, нельзя открыть растущий journal или подключить
новое поколение. Сохраняются no-R/empty/prefix и first-failure различия. Реальное
завершение already-in-flight append не превращается в потерю raw только потому,
что serial уже закрыт; оно принимается настоящим receiver completion path.

Permanent close сохраняет принятую abandonment семантику: immediate fence,
затем actual cleanup, без обещания force-cancel или успешной finalization.
Если writer закончил footer после abandonment, fresh reader может увидеть его
bytes, хотя controller не наблюдал successful completion. Эти факты различны.
Clean completed Preview без writer может disconnect/reconnect после реальных
END+matching ACK; для записи дополнительно требуется actual writer settlement.

## Fake-backed воспроизведение и ограничения

Из coherent source в уже подготовленной Python среде:

```sh
PYTHONPATH=host python -m unittest discover -s host/tests -p test_stream_serial.py -v
PYTHONPATH=host python -O -m unittest discover -s host/tests -p test_stream_serial.py -v
PYTHONPATH=host python -m unittest discover -s host/tests -p test_stream_controller.py -v
PYTHONPATH=host python -O -m unittest discover -s host/tests -p test_stream_controller.py -v
```

24 новых serial methods и32 controller methods (31 принятый плюс unmanaged
compatibility control). Suite запрещает реальный serial factory и использует
настоящий SerialTransport, fake backend, encoded-byte peer из test closure и
реальные temporary journals. Проверяются thread/arguments/counts, late returns,
unknown counts, equality deadlines, retained failed-open/close handle,
cleanup-only retry после permanent close, clean/faulted reconnect, stale bytes,
реальные held prepare/append/finish и display, no-R/prefix/first cause после reopen.
Все test Events имеют finite release/join cleanup. Qt не нужен; GUI source не меняется.

Авторские результаты не заменяют независимую оценку точного frozen commit.
Старые failures, промежуточные harness ошибки и исходные07d/15d evidence сохраняются
отдельно. Нет native timing/baud/USB/Windows guarantees, automatic retry/reconnect,
port scan или автозапуска acquisition. Общий device/protocol gate остаётся
BLOCKED_EVIDENCE/null; PR95 остаётся приостановленным.

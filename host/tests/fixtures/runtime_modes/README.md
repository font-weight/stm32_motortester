# Независимый сквозной набор аналоговых режимов

Автор набора не меняет production и не выносит оценку Critic-B. Уровень проверки:
синтетическое исполнение Python на Linux; Windows, COM/native USB, плата,
ADC/DMA, физический порог и точность времени не проверяются.

## Точная матрица

`test_runtime_modes_integration.py` проводит **120 полных окон**:

- Auto, rising: 10 при 100 мс и 10 при 60000 мс; на каждом сроке 5 событий
  signal и 5 окон auto_timeout.
- Auto, falling: тот же набор 20 окон.
- Normal: 20 rising и 20 falling, по одному явному ARM на серию.
- Single: 20 rising и 20 falling, новый явный ARM и новая generation для
  каждого окна, только после полного RELEASE_ACK предыдущего.

В матрице rising использует level=1, falling level=4094. Дополнительные тесты
проверяют level=2048, событие до срока, событие точно на сроке (политика эмулятора
выбирает timeout), начало Auto до полного ARM_ACK, отсутствие срока ожидания
Normal/Single вплоть до uint64 max и новый срок Auto после последнего байта ACK.

Каждое из 120 окон проходит действительную цепь `DeviceEmulator` с явным
`all_analog_modes=True` → внедрённый backend `SerialTransport` →
`AcquisitionRuntime` → внутренние decoder/session/WindowReceiver → Capture →
атомарный save → reopen. До ACK отдельно проверяются вычисленные по документу
исходные коды, wire BEGIN/DATA/END, битовый CRC-32, метаданные файла, SHA-256,
шкала с нулём на первом отсчёте, synthetic/nominal-unverified происхождение.
Для Auto-timeout нет события и trigger_index=None; ноль шкалы не выдаётся за
время срабатывания. SHA/CRC обнаруживают изменение байтов, но не аутентифицируют
источник. Файлы повторно проверяются после ACK и завершения всей серии.

Чтения имеют шаблон 0/1/7/541/4096, записи 0/1/3/17/4096. Учитывается именно
принятый префикс; кадры одновременно фрагментируются и объединяются. Числа
фактических операций и SHA каждого файла попадают в manifest запуска.

Дополнительные сценарии: чужая равная копия Capture, повторный ACK, отказ диска,
существующий файл, Stop до события/после полного и частичного окна, ранний ABORT
при совпадении Auto/Stop, обрыв, неизвестный принятый префикс RELEASE, потеря
ACK, тайм-аут отдельного кадра/прогресса окна, повреждённая смешанная порция,
неверные wire-метаданные/CRC окна/UTC. Неполные фрагменты не заменяются нулями,
новый start требует их передачи потребителю и явного reset boundary. Нет
неявных ARM, повторов команд, открытия новых backend или reconnect.

5000-мс срок удержания эмулятора проверяется отдельно при 4999/5000 мс как
**PROVISIONAL**: он не объявляется согласованным аппаратным контрактом.
Миграция Stop и early-RELEASE из других worktree этим набором не принимается.

## Минимальная raw-фикстура

`auto-timeout-100-host.bin` и `auto-timeout-100-device.bin` — фактически
полученные байты одного запуска (handshake, CONFIG, ARM, timeout-окно,
последующее принятие потребителем и RELEASE_ACK). Их SHA, входы, SHA кодов и
файла записаны в `auto-timeout-100.json`. Тест повторяет путь на текущей
реализации и сверяет байты. Это детерминированный golden, не реальное измерение.

## Воспроизведение

Из корня репозитория (папки вывода должны быть новыми):

```sh
PYTHONDONTWRITEBYTECODE=1 python host/tests/fixtures/runtime_modes/run_evidence.py --output /tmp/runtime-modes-normal
PYTHONDONTWRITEBYTECODE=1 python -O host/tests/fixtures/runtime_modes/run_evidence.py --output /tmp/runtime-modes-optimized
PYTHONDONTWRITEBYTECODE=1 python host/tests/fixtures/runtime_modes/run_evidence.py --baseline --output /tmp/runtime-modes-old-normal
PYTHONDONTWRITEBYTECODE=1 python -O host/tests/fixtures/runtime_modes/run_evidence.py --baseline --output /tmp/runtime-modes-old-optimized
```

Runner требует ненулевое число обнаруженных тестов и хеширует все девять
потребляемых production-модулей и исходники набора до/после. `--baseline`
копирует только эти модули во временную папку, возвращает две строки прежнего
преобразования Auto и требует точный SHA старого runtime
`902e26df36523113aa1e1e5bc9aa9ca92513945424ec65f58b16fe956686abb7`.
Затем один регрессионный тест обязан упасть: старый runtime ошибочно пытался
создать signal с index=65535 и уходил в DOWNSTREAM recovery после правильного END.
Неудачный unittest-log сохраняется; успешный exit runner означает, что ожидаемый
отказ действительно воспроизведён. Production никогда не редактируется.

Первый исследовательский запуск выявил ошибку тестового oracle: после END
разрешён частичный STATUS, поэтому audit накопленного входа должен проверять
только полностью принятые кадры. Ошибка и исходный log сохранены отдельно в
пакете свидетельств; это не скрытое исправление production.

Большие логи и manifest фактических запусков хранятся отдельно от этих небольших
исходников/фикстур. Агрегат GUI/всего host и окончательное заключение требуют
заморозки согласованной общей версии и отдельного Critic-B.

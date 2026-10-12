# USB003: независимые supporting tests учёта SETUP

Дата: 10.10.2026. Задача: [issue #55](https://github.com/font-weight/stm32_motortester/issues/55). Это независимый набор исполняемых тестов, **не заключение Critic-A и не приёмка**. Автор тестов не менял production patch, upstream, исходный fake DCD, host или GitHub.

## Результат

На авторском `usbd.c` с SHA-256 `187144971b52e77385809500bd7401b46c3c332acda5f6221a1b45e440b8ddd0`:

- 30/30 запусков прошли в обычной native-сборке;
- 30/30 запусков прошли с ASan+UBSan;
- в каждом наборе отдельно buffered и F1 dedicated-HWFIFO;
- сохранилась исходная проверка полной EP0 enumeration, CDC bulk IN/OUT и FIFO backpressure;
- неизменённый upstream в двух отдельных наборах воспроизвёл по 16 ожидаемых `EP0_RESPONSE_MISSING`; остальные 14 контрольных запусков каждого набора прошли.

Итого: 120 исполняемых запусков, включая 60 положительных проверок исправленного варианта, 32 воспроизведения отрицательного поведения upstream и 28 проходящих upstream controls. Число запусков не является оценкой качества или вероятностью надёжности.

Все четыре runner завершились с кодом 0, означающим совпадение с выбранным профилем ожиданий. Для upstream это **не означает исправность**: в `matrix.json` видны настоящие exit=1 отрицательных сценариев. Runner требует точный маркер отсутствия EP0-ответа; случайная ошибка или sanitizer failure не засчитываются ожидаемым отрицательным результатом.

## Проверенная граница

Настоящие TinyUSB `tusb.c`, `tusb_fifo.c`, `usbd.c`, `cdc_device.c`, frozen commit `dae3f9a366bfcddbf9dcf1b48d7500286a849539`, и неизменённый fake DCD из ранее проверенного supporting harness. Для исправленного варианта заменяется только TU `usbd.c` на результат root patch через явный `--usbd`.

Конфигурация: OS_NONE, очередь 16, не более одного события за `tud_task_ext`, один CDC, FIFO/EP 64, непостоянные RX/TX FIFO и выключенное overwrite. Два frozen config используются без изменений. Проверки изолированы: каждый сценарий стартует отдельным процессом, затем вызывает штатный `fake_start`. Реальные USB reset представлены `dcd_event_bus_reset` штатного fake DCD; программный epoch/recovery bridge не подменяет reset.

Тесты не получают доступ к приватному счётчику и не исправляют его из harness. Наблюдаются успешные enqueue hooks, порядок callbacks, queue-ready, фактическое dispatch reset, EP0 submissions, длина и байты дескриптора, status stage, повторная конфигурация CDC. Каждый task вызывается отдельно; до/после него проверяются эффекты и наличие остатка очереди.

## Матрица

| Сценарий | Что проверяет | Неизменённый upstream | Root patch |
|---|---|---|---|
| `rejected_1`, `_255`, `_257`, `_511`, `_513`, `_1025` | Полная очередь deferred events; отказ SETUP; точный bounded drain; следующий BUS_RESET и GET_DESCRIPTOR | EP0 ответа нет | Ответ 18 байт и восстановленная CDC-конфигурация |
| `rejected_256`, `_512` | Полные обороты uint8; защита от случайно удобного размера burst | Проходит из-за wrap | Проходит без накопления отказов |
| `supersession` | Из двух принятых SETUP обрабатывается только последний (18 вместо 8 байт) | Проходит | Проходит |
| `capacity_supersession` | Все 16 мест заняты принятыми SETUP; каждый старый pop потребляет ровно один task budget | Проходит | Проходит |
| `mixed_1`, `_257` | Два принятых SETUP между deferred; полная очередь отклоняет SETUP, deferred и RESUME; последний принятый SETUP сохраняется | EP0 ответа нет | Проходит |
| `mixed_256` | Такой же смешанный состав на границе полного оборота uint8 | Проходит | Проходит |
| `reset_queued_setups` | Два настоящих SETUP уже стоят после принятого BUS_RESET; reset не должен стереть их учёт; supersession сохраняется | Проходит | Проходит |
| `existing_core` | Исходный `test_core.c`: device/config descriptors, SET_ADDRESS, SET_CONFIGURATION, CDC line state, IN/OUT и backpressure | Проходит | Проходит |

В rejection-тестах SETUP ставится в очередь **до dispatch BUS_RESET**, что дополнительно ловит неправильное «исправление» безусловным обнулением счётчика в обработчике reset. В смешанных сценариях отклонённый SETUP просит 8 байт, а последний принятый 18: проверка длины отличает их фактическую обработку. На каждом шаге проверяется отсутствие лишнего DCD submission и по завершении `fake_finish` подтверждает нулевой `fake_invalid_submissions`.

## Сборка и воспроизведение

GCC 14.2.0, GNU ld 2.44. Реальные upstream TU: GNU11, `-Wall -Wextra`. Тест и исходный fake: C11, `-pedantic-errors -Werror -Wall -Wextra`. Общие: `-g -O1 -fno-omit-frame-pointer -fno-lto`. Sanitizers: `-fsanitize=address,undefined`, `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1`, `UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1`. Для стабильного native sanitizer layout использованы `-fno-pie -no-pie`. LSan не заявлен.

Из каталога этого набора, выбирая **новые** output paths:

```sh
python3 run.py --expect upstream --out ./rerun-upstream-native
python3 run.py --expect upstream --sanitize --out ./rerun-upstream-sanitized
python3 run.py --usbd /workspace/shared/usb-setup-draft/build/usbd.c --out ./rerun-fixed-native
python3 run.py --usbd /workspace/shared/usb-setup-draft/build/usbd.c --sanitize --out ./rerun-fixed-sanitized
```

`--dependencies PATH` указывает каталог с `vendor/tinyusb/src` и `SHA256SUMS.sources`; `--harness PATH` указывает исходный неизменённый harness. Runner отказывается использовать существующий `--out`, чтобы не затереть прежние свидетельства. Все inputs хешируются до и после; изменение входа делает результат неуспешным.

## Минимальная коррекция и оставшиеся ограничения

Root patch сохраняет прежнее увеличение SETUP до enqueue и прежний порядок успешного hook, но при отказе конкретного SETUP возвращает этот increment назад. При последовательных producer вызовах и одном невложенном consumer это возвращает счётчик к прежнему значению после каждого отказа, поэтому 1025 отказов не превращаются в 1025 «несуществующих» queued events. Успешно принятые SETUP остаются учтёнными и продолжают выполнять upstream supersession. Обнуление на reset не требуется и было бы неверно для уже стоящих за reset SETUP.

Это локальное исправление **не добавляет синхронизацию**. В исходном коде и после patch остаются:

- неатомарные read/modify/write ++/-- при пересечении producer/consumer или вложенных producers;
- промежуточное увеличение до enqueue, которое не является атомарным снимком очереди;
- OS_NONE queue helpers, безусловно включающие USB IRQ на выходе, без сохранения внешней вложенной маски;
- отсутствие reset epoch barrier, фильтра stale completion, обработки lost-reset и автоматического fail-closed recovery;
- RTOS scheduling, IRQ preemption/приоритеты, MMIO cancellation и полноценный hardware concurrency contract вне данной модели.

Сами fake `dcd_int_enable/disable` не моделируют прерывания. ASan/UBSan не доказывают отсутствие races. Простое перемещение increment после enqueue создало бы иную проблему в RTOS: consumer может увидеть event до обновления счётчика. Ни один вариант здесь не выдаётся за универсальную синхронизационную коррекцию.

Не проверены ARM runtime/disassembly, физическая enumeration, Windows 10, плата, USB electrical timing, deadline АЦП, lost event recovery и full lifecycle. Этот набор не закрывает hardware/MVP readiness и не заменяет независимый quality gate ≥9.5.

## Артефакты и целостность

- `test_setup_queue.c`, `run.py`: весь новый тестовый код и runner.
- `evidence-upstream-native/`, `evidence-upstream-sanitized/`: сохранённые отрицательные controls.
- `evidence-fixed-native/`, `evidence-fixed-sanitized/`: положительные результаты на root patch.
- Каждый каталог содержит полные `commands.json`, `commands.log`, `matrix.json`, `receipt.json`, `input-manifest.json`, objects и executables.
- `original-harness-verification.log`: все frozen hashes исходного supporting harness совпали.
- `old-negative-verification.log`: все hashes старого research пакета, включая прежние отрицательные probes, совпали.
- `root-patch-diff.txt`: единственное production source отличие, прочитанное из авторского build файла.
- `SHA256SUMS`: финальная фиксация этого тестового пакета.

Upstream `usbd.c` SHA-256: `38de54351d81878e6543f2b022d0c39f45d28c8f0888491796398dec45e727be`.

Следующий шаг: Critic-A проверяет точный опубликованный кандидат и полный набор свидетельств; данный supporting worker оценку не выдаёт.

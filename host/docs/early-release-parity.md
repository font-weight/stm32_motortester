# Узкая defensive parity: RELEASE при частичном END

12.10.2026. Авторский локальный кандидат B, уровень проверки — программа с
синтетическими отсчётами. Отдельное независимое заключение Critic-B ещё требуется.
Это не принятие общего протокола, не оценка качества автором и не готовность MVP.

## Задача, исходники и границы

Задача уточняет byte-emulator для MVP-01/MVP-U09: при преднамеренном raw RELEASE,
поступившем до завершения передачи текущего END, сохранить окно и вернуть
существующий `ERROR(RELEASE,BUSY=2)`. API, payloads, frame/session/Stop admission,
счётчики, очередь, часы, waveform и поддерживаемые режимы не расширяются.

База этой итерации — опубликованный
[`c44bae42b19ec8208c2ebee4a3e662ce1b7ff240`](https://github.com/font-weight/stm32_motortester/commit/c44bae42b19ec8208c2ebee4a3e662ce1b7ff240)
плюс точный восстановленный Stop-overlay и свежий набор Stop R2 conformance.
Это явно составное локальное состояние; опубликованного Stop commit на момент
фиксации базы ещё нет. SHA такого commit не подставляется вместо overlay.
Все 228 host-файлов базы перечислены в `evidence/baseline-tree.json` нового fixture.
SHA-256 этой записи:
`e4044f4ee7b47e02e598fdd1555a16aadb2e5f87314cbb8ac901e9f58bb80673`.
Её поле `source_manifest_sha256` привязано к финальному Stop-manifest
`d846c354ead4b77c46bf0e98f08906397e1df4148f099a0b8f549a864c644aea`.
Предыдущий Stop worktree, старые тесты, отчёты и manifests не переписываются.

Источники именно нового узкого правила:

- A [PR79](https://github.com/font-weight/stm32_motortester/pull/79), точный
  [`66fce97ba86de459e6d597deb32130e030f34594`, mt_capture_link.c:232–237](https://github.com/font-weight/stm32_motortester/blob/66fce97ba86de459e6d597deb32130e030f34594/firmware/app/mt_capture_link.c#L232-L237):
  committed, но не переданный целиком END получает BUSY после проверки tombstone/ID/CRC.
  Проверен опубликованный исходник; C-реализация не копировалась в Python.
- [Согласие A, comment6117103278](https://github.com/font-weight/stm32_motortester/pull/79#issuecomment-6117103278)
  и [ограничение области B, comment6117158061](https://github.com/font-weight/stm32_motortester/pull/79#issuecomment-6117158061)
  — согласование проекта защиты, явно **не совместная приёмка**.
- Стабильные native WAIT/Stop/mode исходники A:
  [PR82, `7382ae7f3490127d8a61733cbca948acd89047f9`](https://github.com/font-weight/stm32_motortester/pull/82).
  Их наличие не означает новый native-прогон в рамках этого host-изменения.
- Историческая пара A/B и qualified reproducer отдельно опубликованы в
  [PR87, `50ed6608df2fa8bae6799e6a96c09b7543cec596`](https://github.com/font-weight/stm32_motortester/pull/87).
  Его старое ожидание расхождения не является oracle приёмки нового эмулятора.

`SOURCE_SHA=c54bd955aaf3cb863ef3df043ea237ea62e74ad4` остаётся источником
основного emulator draft; `STOP_BARRIER_POLICY_SHA=2017576dbe104f4c43d8d9feac28436589191fd1`
сохраняет только отдельную миграцию Stop. Метка **PROVISIONAL** и толкование
5000 ms от последнего принятого END-byte остаются прежними. Ни provenance API,
ни общий контракт не заменяются целиком источником A PR79.

## Правило и границы передачи

После прежних frame/session/duplicate/order/active-Stop guards и после точного
сопоставления tombstone, generation, window и CRC проверяется текущий TX:
если в текущем epoch ещё существует незавершённый END для текущих ID, RELEASE
получает BUSY. Наличие `_tx` означает наличие непринятых байтов; `read` удаляет
этот объект до callback последнего байта. Очередной ERROR идёт после уже начатого
END и более ранних обязательных ответов.

Такой запрос не снимает held-state, не ставит release-pending, не меняет
continuation, timer, terminal/tombstone или samples и не создаёт RELEASE_ACK.
Ошибка получает существующий request_id и generation; команда потребляет RX
sequence/request как обычная допустимая команда с ошибкой состояния.

- Ноль переданных END-byte и отсутствие tombstone: прежний BAD_RELEASE=7.
- Неверные generation/window/CRC: BAD_RELEASE=7, даже во время partial END.
- Неверный размер RELEASE: прежний BAD_LENGTH=6 до проверки BUSY.
- Повреждённый frame, чужой session, duplicate, неверный порядок и Stop-barrier
  обрабатываются прежними guards раньше новой проверки.
- Последний END-byte запускает прежний PROVISIONAL hold timer. Даже 20000 ms
  задержки до этого байта не являются истечением ещё не начавшегося hold timer.
  Это не моделирование отдельного firmware TX-stall timeout.
- После полного END RELEASE снова действует обычно: новый Auto/Normal wait
  разрешён только последним байтом RELEASE_ACK. Single затем STOPPED до явного ARM.
- Старый tombstone/replay, поздний RELEASE после Stop или expiry и fresh HELLO
  сохраняют прежний смысл. Старый незаконченный END после fresh HELLO заканчивается
  перед новым HELLO_ACK, но его callback не меняет новый сеанс.

Обычный поддерживаемый host создаёт проверенный receipt только после полного
END и проверки окна. Проверка early RELEASE использует намеренно нарушающий
это правило raw-peer. До исправления зафиксированы ACK/логическое освобождение
и rearm, но преждевременное физическое переиспользование массива или повреждение
обычного host не продемонстрированы. Нельзя выводить из расхождения severity
или аппаратную неисправность. Raw storage проверяется отдельно от held-флага;
тесты только читают `_samples`, не меняют частные поля устройства.

## Новые проверки и воспроизведение

Исходники: `host/tests/test_early_release_parity.py` и
`host/tests/fixtures/early_release_parity/{raw_peer.py,run_suite.py,probe.py}`.
COBS/битовый CRC в `raw_peer.py` происходят из неизменного Stop R2 helper
SHA-256 `6d58f4074dc8c13a10a49fef3f95722baac94580c029f519972094314a75d519`;
новый `one()` читает COBS-блоки публичным byte API. Это повторное использование
независимого codec, **не независимое авторство нового набора**. Набор не содержит
побочных эффектов в bare assert. Runner inventory требует точное ненулевое число
тестов, уникальные IDs, отсутствие ошибок импорта и ноль skips/expected failures.

Матрица: 45 частичных префиксов END × 3 режима × 2 фронта = 270 отдельных тестов.
Ещё 16 именованных тестов с subcases проверяют zero/final END, Auto-timeout,
неверные tuple/length, duplicate, frame/session/request precedence, Stop/fresh
HELLO, задержанный final END, 4999/5000 ms, старый tombstone, bounded queue,
порядок прежних ответов, входную фрагментацию, host receipt и uint64 clock.
Во всех 270 строках ACK передаётся фрагментами 1/остаток-без-последнего/последний
байт; samples сравниваются побитно и storage identity сохраняется до rearm.

Из корня checkout с реальными GUI dependencies:

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=host:/tmp/motortester-deps
export QT_QPA_PLATFORM=offscreen
python host/tests/fixtures/early_release_parity/run_suite.py \
  --pattern test_early_release_parity.py --expected-count 286 --inventory /tmp/new-focused.json
python -O host/tests/fixtures/early_release_parity/run_suite.py \
  --pattern test_early_release_parity.py --expected-count 286 --inventory /tmp/new-focused-O.json
python host/tests/fixtures/early_release_parity/run_suite.py \
  --pattern 'test_*.py' --expected-count 892 --inventory /tmp/new-full.json
python -O host/tests/fixtures/early_release_parity/run_suite.py \
  --pattern 'test_*.py' --expected-count 892 --inventory /tmp/new-full-O.json
```

Путь `/tmp/motortester-deps` описывает эту Linux-среду, не обязательный путь
пользовательской установки. Реальные Qt/PySide6/numpy/pyqtgraph импортируются;
skip не засчитывается как успех. Фактические результаты, версии среды,
source-before/after и команды находятся в новом `evidence/`, отдельно от
малого frozen source-manifest. Исторические 606/606 Stop aggregate не выдаются
за финальную проверку этой версии.

Для шести сохраняемых raw traces (mode × edge) используйте новый каталог вывода:

```sh
python host/tests/fixtures/early_release_parity/probe.py \
  --emulator host/motortester/device_emulator.py --out /tmp/new-parity-probe
```

В отдельном disposable fixture сохранены точные старые варианты emulator:
`evidence/published_c44_device_emulator.py` и `evidence/baseline_device_emulator.py`.
Их SHA-256: соответственно
`e9f726bb4ed6591ae6810f351de5f7429e42a4316e8a7c109f9835d77ad1f9c6` и
`e79887e3d047feda419b3106cccc474d28fc9c232e74f6dbfd22463d910ea411`.
Тот же probe с `--emulator` каждого из этих файлов должен дать exit1:
он требует BUSY, а старый вариант выдаёт ACK; исходный fixture не исправляется.
Негативный контроль — отдельный запуск, не expectedFailure в зелёном aggregate.
`observations.json` сохраняет до/после snapshot, фактические wire bytes и frames,
CRC и hash samples, retention и результат BUSY-oracle. Оба режима Python
должны дать ту же семантику; старые файлы и текущий wire codec хешируются.

Не выполнены здесь: новый native A прогон, физические ADC/USB, Windows 10,
LV-001, приёмка общего протокола или независимый quality gate. Публикация и
привязка будущего commit принадлежат root; локальный кандидат не имеет
выдуманного published SHA. Следующий шаг — независимое Critic-B ревью точного
source/evidence tuple, затем разрешённая публикация и отдельная общая приёмка.

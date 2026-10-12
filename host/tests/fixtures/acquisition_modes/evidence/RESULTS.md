# Результаты независимого автора тестов, 10.10.2026

Уровень: синтетическая программная проверка ограниченного аналогового
Auto/Normal/Single. Это не заключение итогового Critic-B, не балл, не приёмка
общего draft и не готовность MVP/USB/Windows/аппаратуры.

## Проверенная версия

Локальная база `ef2880c0655d155284c176424a12378b3b66adf4` плюс исходный frozen
HOST008 overlay (manifest SHA256 `a4596d0f5eea1318841390600bcc763d9aa02ad66649ec4a06fece3de37a0042`),
затем семь авторских файлов из `../author-frozen-v1.json` и новые независимые
тесты/fixtures. Для overlay отдельно записана последующая публикация PR69
`45ef38792b8e824e2109e7bacc1c9dc605917d06`; координатор лично проверил145/145
host blobs против baseline (`../published-binding.json`).

Ключевые SHA256 проверенного кода:

- session.py: `0bf21924f22971d895fc1ec0b7bcc4c4d041d475a0461a650c0b785078905ca9`
- device_emulator.py: `e9f726bb4ed6591ae6810f351de5f7429e42a4316e8a7c109f9835d77ad1f9c6`
- test_acquisition_modes_conformance.py: `cf4a7aade8b62e69da273f1aedf605383e4086c1dea2213c3843ac44c66166ec`

`final-v1/source-before.json` и `source-after.json` совпадают для155 host-файлов
вне собственной изменяемой папки evidence. SHA256 обоих:
`4b7529fde80377f45a52504fb017697dd56b0b7c672900706339e46b3c8e2df9`.
`source_stable=true`, `changed_paths=[]`.

Оригинальный `/tmp/motortester-host-session-release` проверен только чтением:
все145 исходных host-хешей совпадают, пропавших/изменённых файлов нет. В новом
worktree изменились только шесть существующих файлов из области автора, плюс
его новый тест и настоящие новые независимые fixtures. Исторические manifests
сохранены. Подробности — `ownership-check.json`.

## Исполненные проверки

Среда: Linux x86_64, CPython3.12.14; реальные импорты PySide6 6.12.0,
numpy2.5.3, pyqtgraph0.14.0 из `/tmp/motortester-deps`; Qt offscreen.

- `final-v1/new-normal.log`:30 tests,0 failures,0 errors,0 skips;1.747s тестов.
- `final-v1/new-optimized.log`:30 tests с Python -O,0 failures/errors/skips;1.729s.
- `final-v1/full-host.log`:471 tests,0 failures/errors/skips;51.481s тестов.
- Все три команды завершились exit0. Полные argv, cwd и elapsed_seconds
  процесса записаны в `final-v1/manifest.json`.

SHA256 окончательного manifest:
`f0e6f8e7bb53450b64dfa7dad8c988d9a911f253c2218a5dcf3c15b9ce1e3f65`.

Сохранены20 реальных синтетических byte captures, проходивших production
StreamDecoder/SessionController/WindowReceiver/WindowReceipt.from_window
и RELEASE:10 Auto с чередованием real/timeout под одним ARM,10 Single с
новым явным ARM на каждое окно. Две пары host/device byte streams и20 записей
с generation/window/CRC — `final-v1/captures/`. Каждый BEGIN/DATA/END и каждый
из2048 кодов сравнивался с независимым oracle, CRC считался побитово отдельно.

## Неудачное ожидание и честность журнала

`expanded-focused.log` содержит27 tests и1 failure. Независимый тест ошибочно
требовал ARMED после Single RELEASE_ACK при pending Stop. Исходный контракт
допускает READY плюс отдельный stop_barrier. Исправлено ожидание теста, а не
production: ARM/CONFIG действительно отклоняются без мутации и нового TX до
STOP_ACK, который не создаёт второго terminal/rearm. Подробности, начальное
уточнение synthetic timeout i0 и отдельно сообщённый автором старый отказ —
`diagnoses.md`. Все прежние журналы сохранены.

Авторские промежуточные logs460 tests normal и20 tests -O также сохранены;
они не подменяют окончательные471/30 и явно названы author-intermediate.

## Оставшиеся границы

Новых блокеров исполнения независимых тестов не обнаружено. Порог/балл и
приёмку даёт отдельный независимый критик на точную итоговую версию. Таймы
expiry-before-input локальны;5000ms hold остаётся PROVISIONAL. PR52 StopRX
policy здесь не принята. Нет новых source pins. Windows10, firmware/USB,
физические ADC/DMA/частота/порог, предыстория и внешний запуск, LV/зажигание,
GUI/runtime интеграция и файловая запись этим узлом не подтверждаются.

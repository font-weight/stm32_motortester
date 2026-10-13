# Frozen-проверка сегментированной записи

Ограниченная программная проверка реального Qt-приложения с существующим
синтетическим байтовым эмулятором. Это не новая возможность приложения,
не физический USB, не испытание платы и не приёмка чистой Windows 10.
База реализации: PR155, commit `dbbff7812a9d811d6764dfb80ad83001c8f10bdb`,
tree `412176cadae21859d677d08d130a654cbeb19643`.

## Как запустить

В собранном Windows onedir-пакете, из PowerShell:

```powershell
.\Motortester.exe --recording-smoke C:\evidence\frozen-recording.json
```

Родительская папка должна существовать. Имя JSON и соседняя папка
`frozen-recording.json.work` должны быть новыми. Предыдущие результаты,
в том числе неудачные, не удаляются и не перезаписываются. Путь ограничен
180 символами, чтобы созданный штатной кнопкой recovery-spool укладывался
в существующий предел 240 символов. UNC, device namespaces, ADS,
зарезервированные Windows-имена, `..`, символы управления и ссылки в
родительском пути запрещены. Кириллица и пробелы поддерживаются.

Сборочный рецепт `packaging/build_windows.py` вызывает эту команду сам,
после прежних serial-import и screenshot gates, перед manifest/ZIP/итоговым
сообщением об успешном кандидате. Нужны одновременно нулевой exit code,
свежий строгий receipt, совпадение хешей EXE и артефактов и native Windows
qualification. Старый EXE без новой ветки не проходит даже при exit code 0.

Для разработки из `host/`, на Linux с установленными GUI-зависимостями:

```sh
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python packaging/launch_gui.py --recording-smoke /tmp/new-recording.json
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p test_recording_packaging.py -v
QT_QPA_PLATFORM=offscreen python -O -m unittest discover -s tests -p test_recording_packaging.py -v
```

Source/Linux получает отдельный scope `source-or-nonnative-development`.
Тестовые fake-native receipts проверяют только правила сборочного gate;
они не являются запуском Windows. Фактический frozen Windows результат
публикуется отдельно с точным build commit, EXE, receipt и SHA256SUMS.
Наличие этого рецепта или успешный Linux-прогон не закрывает этот шаг.

## Что действительно исполняется

1. Настоящий `MainWindow.show_acquisition()` создаёт панель и штатный
   `AcquisitionBridge`. Явно выбирается эмулятор. Создание SerialTransport
   запрещено защитой smoke; COM-порт не открывается и не перечисляется.
2. Нажимается настоящая кнопка Record с Normal, rising, уровнем 2048,
   нулевой предысторией и включённым синтетическим фронтом. Штатные
   adapter/controller и off-thread writer создают spool в новой work-папке.
   Остальные Auto/Normal/Single-пути приложения не изменяются.
3. После как минимум двух полных committed-сегментов нажимается Stop.
   Проверяются реальные request ID, nonce, generation и STOPPED/STOP_ACK,
   `user_stop`, `stop_ack` и отсутствие незавершённой записи. Закрытие
   потока или транспорта не подменяет это свидетельство.
4. `save_recording_path` сохраняет новый `recording.mtrec` обычным API;
   recovery-spool сохраняется. Строгий `session_archive.load` проверяет
   весь архив. Новый, второй MainWindow открывает его через `open_path`.
5. Переключатель выбирает каждый сегмент. Байты и provenance сравниваются
   с наблюдением перед реальным append; сами данные не подменяются.
   Для полных окон сравниваются все 2048 исходных ADC-кодов, MTCAP bytes,
   исходный Capture и график. Отдельная формула проверяет детерминированный
   шаблон эмулятора, включая уровень запуска и оба рельса ADC. Для частей
   сравниваются принятые фрагменты; событий без BEGIN нельзя превращать
   в полный Capture. Дополнительный terminal от Stop не отбрасывается.
6. Подтверждаются native join acquisition-потока и фактическое завершение
   disk writer; оба окна закрываются. Только после этого receipt может
   стать `passed`. В `finally` также выполняется ограниченный shutdown
   при отказе, без модальных диалогов и без уничтожения evidence.

Шкала остаётся номинальной: 50 кГц, 2048 отсчётов на полное окно.
Межсегментные интервалы устройства неизвестны. Ни хеш, ни Stop ACK
не доказывает физическую остановку или непрерывное покрытие разряда.

## Receipt v1 и ограничения ресурсов

`recording_smoke.validate_report` не импортирует Qt. При обычной валидации
требует `frozen-windows-recording`, реальные `sys.platform=win32`, `os.name=nt`,
`sys.frozen is True`, Qt QPA `windows`, CPython 3.13.16 x64 и Qt/PySide 6.12.0,
без оптимизации bytecode. Для source-проверок нужно явно передать
`require_frozen_windows=False`; это не меняет реальную платформу receipt.

Строгая схема содержит:

- `schema_version`, `status`, `scope`, `phase`, полный упорядоченный список
  десяти `phases` с UTC, basename JSON и соседней work-папки
- `runtime`: фактические OS release/version, архитектура, Python, Qt,
  PySide, QPA, frozen/optimization flags и версия приложения
- `imports`: точный перечень реально загруженных модулей GUI, acquisition,
  adapter/controller, archive, emulator, runtime, session/wire/window,
  recording и model; literal import в launcher захватывает smoke для PyInstaller
- `identity`, `stop`, `segments`: UUID записи/источника, wire nonce,
  matching Stop, порядковые номера, ADC counts, raw/sample SHA-256 и provenance
- `checks`: явная синтетика, отсутствие device I/O и физической проверки,
  неизвестные gaps и номинальная шкала, подтверждённые сравнения
- `artifacts`: только `.mtrec` и штатный `.mtspool` внутри work-папки,
  их размеры и SHA-256; `executable`: размер и SHA-256 запущенного EXE
  (для source-development это Python interpreter)
- `cleanup`: подтверждённые закрытие/завершение, отсутствие pending I/O
  и invalidated finalization, повторно измеренные после retirement; `error`:
  null при успехе или фиксированный код. Успешный Save не подменяет этот
  финальный снимок: поздний fault делает smoke неуспешным, а сохранённый
  до него архив остаётся в evidence

Максимум receipt — 64 KiB, spool/архив — по 2 MiB, executable — 128 MiB,
число сегментов — 16. JSON не принимает повторяющиеся/лишние/пропущенные
поля, bool вместо integer, NaN/Infinity, неверные hashes/counts/paths.
Validator повторно хеширует реальные файлы и сверяет каждого члена архива,
его sample bytes и provenance с receipt. Необычные файлы/ссылки запрещены.
Stop.last_window должен совпадать с последним записанным окном;
его generation не может предшествовать поколениям сегментов, а request ID
должен следовать ARM generation. Новое поколение Stop после последнего
полного окна допустимо. Validator повторно проверяет все родительские
каталоги и work-папку, включая Windows junction/reparse flags.
В receipt не попадают окружение, абсолютные приватные пути, traceback или
текст backend-исключения. Ошибка записи самого receipt также закрывает gate.

Каждое асинхронное ожидание ограничено 12 с, основной сценарий — 45 с,
cleanup — 8 с. Qt pump вызывает `processEvents`/deferred deletes и Python
`sleep`, освобождающий GIL. Внешний build child ограничен 60 с; при timeout
процесс завершается и reap проверяется ещё до 5 с. Синхронный OS file I/O
не объявляется отменяемым: зависание покрывает именно внешний child timeout.
Неподтверждённый shutdown, unresolved writer или invalidated finalization
не дают успешного receipt и не разрешают поздний экспорт.

## Версия и граница доказательства

Receipt не выдумывает embedded Git SHA. `build_windows.py` требует чистый
checkout и связывает его `source_commit`, raw source-file hashes,
EXE hash, receipt hash и результаты в `build-manifest.json`.
`SHA256SUMS.txt` дополнительно включает receipt, `.mtrec` и `.mtspool`.
Это внешняя прослеживаемость сборки, не воспроизводимая binary-to-source
аттестация и не защита от человека, намеренно подделывающего все артефакты.

Открыты отдельные проверки actual Windows frozen выбранного commit,
ручной Windows 10/DPI/диалогов, sustained throughput/длительных записей,
реального USB, временной точности, LV/зажигания и физической безопасности.
Новая команда не заменяет прежние serial/screenshot gates или независимое
ревью упаковки по [quality gate](../../docs/quality-gate.md).

# pyserial в Windows-кандидате: повторно подготовленный пакет r2

Это новая подготовка после замены рабочей среды. Исходная база:
`ec11d01a42e0cbdc06039ddf274121a4db7202ed`. Старые неопубликованные файлы,
хеши и результаты потерянного кандидата не восстановлены и не используются
как доказательства. Фактические новые журналы находятся отдельно в
`../packaging/evidence/serial-r2/`; исторические manifest не изменяются.

## Область и критерий

В рецепт добавлен только `pyserial==3.5`; прежние 13 Windows-зависимостей
и пять GUI-зависимостей сохраняют версии и хеши. Это подготовка упаковки
существующего `motortester.transport.SerialTransport`, связанная с MVP-U10,
MVP-U02 и DOC-01/DOC-02. Она не подключает транспорт к GUI и не меняет
wire-контракт, прошивку или поведение транспорта.

Критерий этой итерации: закреплённый wheel, явное включение Windows-backend
в PyInstaller, отдельная обязательная проверка импорта готового EXE,
проверяемый сбор уведомления о лицензии, отрицательные тесты и точные
команды передачи Windows-сборки. Независимый Critic-B проверяет замороженные
исходники и новые свидетельства. Автор не назначает себе оценку и не
объявляет приёмку до такого заключения.

Реальный Windows/PyInstaller build, запуск EXE, чистая Windows 10 и USB
остаются отдельными незакрытыми проверками команды A. Успешное разрешение
Windows wheels на Linux или тестирование с подставными модулями их не
заменяет; `software-ready` и аппаратная готовность здесь не заявляются.

## Закреплённые входы

- [PyPI pyserial 3.5](https://pypi.org/pypi/pyserial/3.5/json): wheel
  `pyserial-3.5-py2.py3-none-any.whl`, 90 585 байт,
  SHA-256 `c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0`.
  Размер и хеш повторно сверены по фактически загруженным байтам.
- `requirements-win-x64.lock`: 14 дистрибутивов для обычного CPython 3.13.16,
  ABI cp313, Windows x64; обязательны `--only-binary=:all:` и `--require-hashes`.
- В официальном wheel нет полного текста лицензии. Полный upstream v3.5
  сохранён без изменений, проверяется по версии и SHA-256 без сети при сборке.
  Подробности и незакрытые юридические вопросы: [LICENSE-REVIEW.md](../packaging/LICENSE-REVIEW.md).

## Обязательный импорт готового EXE

Команда `Motortester.exe --serial-import-smoke OUTPUT.json` обрабатывается
до импорта Qt/GUI. Нужны ровно флаг и один путь. Обычный запуск и прежний
`--screenshot` делегируются GUI без изменения аргументов.

Успех возможен только при одновременном выполнении условий:

1. Настоящая Windows (`sys.platform == "win32"`, `os.name == "nt"`) и
   `sys.frozen is True`.
2. Импортируются `serial`, `serial.serialutil`, `serial.serialwin32`,
   `serial.win32`, `motortester.transport`.
3. `serial.VERSION` и `serial.__version__` равны строке `3.5`.
4. `serial.Serial` является тем же классом, что и
   `serial.serialwin32.Serial`, а `SerialTransport` доступен из своего модуля.

Ни конструкторы Serial/SerialTransport, ни открытие, ни перечисление портов
не вызываются. Импорт Windows DLL-объявлений не означает взаимодействие
с прибором. Поле `device_io_performed: false` отражает этот ограниченный
сценарий, а не результат аппаратного теста.

Выход — JSON схемы 1 размером не более 4096 байт. Успешный результат имеет
строго заданные поля и значения. Валидатор отклоняет пропущенные/лишние поля,
неверные типы, повторы ключей, повреждение, слишком большой файл и любой
результат из исходников/не-Windows. Ошибки возвращают ненулевой код.

Путь должен быть обычным локальным относительным либо абсолютным Windows
путём с буквой диска и расширением `.json`, длиной до 240 символов.
UNC, пространства имён устройств, ADS, имена CON/NUL/COM/LPT и подобные,
компоненты `.`/`..`, завершающие точки/пробелы запрещены. Родительская папка
должна существовать. Файл создаётся исключительно (`xb`): существующий файл
или возникший до открытия конкурентный файл не перезаписывается. Неудачный
или неполный результат сохраняется для разбора; повтор требует нового имени.
Проверка рассчитана на собственную папку сборки, а не на защиту от процесса,
который злонамеренно подменяет родительские каталоги во время работы.

`build_windows.py` запускает этот сценарий с тайм-аутом 30 секунд,
проверяет код дочернего процесса и строго валидирует JSON перед созданием
успешного manifest/ZIP. Ошибка любого шага останавливает сборку. Прежний
frozen screenshot smoke сохранён и выполняется дополнительно. JSON и его
SHA-256 включаются в выходные свидетельства сборки.

## Передача команде A: Windows, ещё не выполнено здесь

На Windows x64 с обычным CPython ровно 3.13.16 и Git, из чистого checkout
опубликованного и независимо проверенного source commit:

```powershell
cd host
py -3.13 packaging\build_windows.py --output C:\motortester-build\serial-r2
if ($LASTEXITCODE -ne 0) { throw "Сборка кандидата завершилась ошибкой" }
```

Папка должна быть новой; при повторе используйте `serial-r2-repeat-1` и
сохраните прежний журнал. Ожидаются `build.log`, wheelhouse,
`frozen-serial-import.json`, `frozen-window.png`, ZIP пакета, `source.zip`,
`SHA256SUMS.txt`, `build-manifest.json` внутри пакета, лицензии и inventory.
Build-скрипт требует чистый commit: локальные правки нельзя незаметно
предъявить как уже опубликованный исходник.

Для отдельной повторной проверки уже собранного пакета (путь изменить на
реальный, выходное имя обязательно новое):

```powershell
$exe = "C:\motortester-build\serial-r2\dist\Motortester\Motortester.exe"
$out = "C:\motortester-build\serial-r2\serial-repeat-1.json"
$p = Start-Process -FilePath $exe -ArgumentList @("--serial-import-smoke", $out) -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Ошибка импорта; сохранить журнал и JSON" }
$env:PYTHONPATH = (Resolve-Path packaging).Path
py -3.13 -c "from pathlib import Path; from serial_import_smoke import validate_report; validate_report(Path(r'C:\motortester-build\serial-r2\serial-repeat-1.json'))"
if ($LASTEXITCODE -ne 0) { throw "JSON не прошёл строгую проверку" }
Remove-Item Env:PYTHONPATH
```

Использованный пример пути не содержит пробелов; при другом пути учитывайте
правила кавычек PowerShell/Start-Process. Для приёмки сохранить точный source
SHA, Windows edition/build, Python, SHA пакета и JSON, оба smoke-результата и
журнал ошибок. Далее проверить обычное открытие/закрытие приложения на
чистой Windows 10 без Python, DPI/диалоги/кириллические пути и лицензии.
Физический USB, отключение/повторное подключение и плата проверяются отдельно.

## Воспроизводимые проверки исходников

Из `host/`, с зависимостями из `requirements-gui.txt`:

```sh
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p test_serial_packaging.py -v
QT_QPA_PLATFORM=offscreen python -O -m unittest discover -s tests -p test_serial_packaging.py -v
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen python -O -m unittest discover -s tests -v
```

Полный заявленный прогон требует установленных настоящих GUI-зависимостей
и нуля пропущенных GUI-тестов. Подставные Windows-модули в focused-тестах
проверяют отказоустойчивость логики, а не Windows. Запуск helper из исходников
должен вернуть ошибку `requires-frozen-windows`; он не может создать
принимаемое свидетельство frozen-сборки.

Свежие журналы содержат команды, время UTC, код выхода, среду и хеши
исходников до/после. Ошибочные ранние прогоны сохраняются с объяснением;
результат последующей исправленной версии не переписывает их историю.

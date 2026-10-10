# Мотортестер: просмотрщик и программные проверки

Первый прототип пути «внутреннее событие эмулятора → исходный массив → `.mtcap` → повторное открытие → график». Это часть приложения общего MVP, а не готовый USB-мотортестер. Задача: [HOST-001, issue #5](https://github.com/font-weight/stm32_motortester/issues/5); критерии: [checklist](reviews/HOST-001-acceptance-checklist.md).

Реализованы ядро без внешних зависимостей, явно обозначенная синтетическая демонстрация, версионированный файл с проверкой целостности, CSV/SVG и тёмное окно просмотра. GUI открывает/сохраняет `.mtcap`, экспортирует CSV и PNG-иллюстрацию текущего графика, показывает предупреждения и номинальные шкалы, поддерживает масштаб/сдвиг и два курсора. Офлайн-справка: [help.md](docs/help.md).

USB-транспорт и согласованный wire-контракт отсутствуют. В GUI пока нет Auto/Normal/Single и управления устройством. Прототип не подтверждает Windows 10, точность шкал, низковольтное измерение или работу с зажиганием. Независимое ревью и полный MVP проверяются отдельно.

## Запуск из исходников

Команды выполняются из `host/`. Фактическая доступная среда разработки: Linux x64, CPython 3.12.14. Windows Server 2022 / CPython 3.13.16 x64 уже выполнил 77 тестов исходной версии `2376d325`: 76 прошли, один выявил перекрытие подписей в минимальном окне. Сборка остановилась до PyInstaller; исправление компоновки в этом кандидате пока проверено только на Linux. [Журнал Windows](https://github.com/font-weight/stm32_motortester/actions/runs/38055733142). Версии GUI закреплены в `requirements-gui.txt`, обоснование и первичные источники — в [stack.md](docs/stack.md).

Для ядра и CLI сторонние пакеты не нужны:

```text
python -m unittest discover -s tests -p test_recording.py -v
python -m motortester demo demo-output
python -m scripts.check_first_path verification-output
```

Выходные папки должны быть новыми: команды не перезаписывают предыдущие результаты. `demo-output` содержит `.mtcap`, CSV и SVG, причём график строится после чтения файла с диска. `check_first_path` проверяет 20 синтетических окон и отдельный контрольный массив; это не 20 измерений LV-001. Фиксированная дата в его fixtures задана для повторяемости и не является временем наблюдения.

GUI, Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
.\.venv\Scripts\python.exe -m motortester.gui
```

GUI, Linux:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-gui.txt
.venv/bin/python -m motortester.gui
```

В Linux необходима графическая сессия и системные библиотеки Qt. Для автоматической проверки без экрана можно использовать offscreen, который не проверяет настоящий Windows desktop:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen .venv/bin/python -m motortester.gui --screenshot gui-smoke.png
```

`--screenshot` запускает настоящее окно Qt, создаёт синтетическую запись, сохраняет и снова загружает её, сохраняет изображение и завершает приложение. Это служебный smoke-сценарий, он отделён от пользовательского PNG-экспорта графика. Окончательные результаты проверок привязываются к точному SHA в `reviews/` и checkpoint задачи; наличие команды не означает её прохождения.

## Экспериментальный байтовый путь

Из `host/`, без сторонних зависимостей:

```sh
python -m motortester.wire_demo wire-output --windows 20 --chunk-size 97
```

Настоящие COBS/CRC байты синтетического источника проходят через накопитель и
сборщик окна, затем сохраняются в MTCAP, читаются снова и превращаются в CSV/SVG.
Все исходные коды сравниваются. Папка должна быть новой. Это программная
проверка непринятого draft, без USB, handshake или физического измерения.

- [Байтовый codec и накопитель](docs/wire-adapter.md)
- [Полные и неполные окна](docs/window-receive.md)
- [Сквозной пример и его ограничения](docs/wire-demo.md)
- [Контроллер Normal/analog: команды, Stop и восстановление](docs/session-controller.md)

Контроллер пока проверяется отдельно от GUI; наличие модуля не означает
доступность управления настоящим прибором или полную поддержку режимов.

## Сборка кандидата Windows

На Windows x64 с CPython 3.13.16 x64 и Git, из чистого checkout:

```powershell
py -3.13 packaging\build_windows.py
```

Скрипт проверяет точную версию Python и архитектуру, создаёт отдельный venv, загружает закреплённые Windows wheels с проверкой SHA-256, выполняет тесты/синтетический сценарий, собирает PyInstaller onedir, проверяет запуск frozen-окна и создаёт переносимый ZIP. При ошибке сборка останавливается; предыдущая папка не удаляется. Для повтора указать новую папку:

```powershell
py -3.13 packaging\build_windows.py --output build\windows-x64-candidate-2
```

Ожидаемые выходы: `build/windows-x64-candidate/Motortester-windows-x64-candidate.zip`, `source.zip`, `SHA256SUMS.txt`, `frozen-window.png`, `build.log`, wheelhouse и manifest внутри пакета. Запускать `Motortester.exe` после распаковки всей папки; `_internal` необходим. Установка Python пользователю готового пакета не должна требоваться, но на чистой Windows 10 это ещё не проверено. Установщик, системные ассоциации и драйвер не добавляются.

Это воспроизводимая процедура с закреплёнными входами, а не обещание побитно одинаковых EXE/ZIP: время сборки и особенности платформы сохраняются в manifest. PyInstaller не делает Windows-сборку из Linux. Подробности лицензирования: [LICENSE-REVIEW.md](packaging/LICENSE-REVIEW.md).

Состав этой объединённой ветки и границы доказательств: [integration-sources.md](docs/integration-sources.md).

## Матрица проверки

| Проверка | Среда / статус |
|---|---|
| Ядро, round-trip, ошибочные записи, синтетический SVG | Linux, Python 3.12.14; фактические логи и SHA — в отчёте HOST-001 |
| Настоящее окно Qt, offscreen-тесты, изображение | Linux; не Windows и не аппаратный опыт |
| Windows resolver/wheel hashes и синтаксис build-кандидата | Подготовка сборки; не выполнение Windows build |
| PyInstaller onedir и frozen GUI | Windows x64, Python 3.13.16: не выполнено в текущей Linux-среде |
| Чистая Windows 10 x64 без Python, DPI/диалоги/кириллические пути | Не выполнено; нужен журнал по точному ZIP/SHA |
| USB, отключение/повторное подключение, реальная плата | Не реализовано и не проверено |
| Низковольтный опыт, стенд зажигания, автомобиль | Не выполнено; отдельные аппаратные критерии |

Перед Windows-приёмкой проверить запуск/закрытие, создание демо, сохранение и открытие через диалоги, CSV, отмену/подтверждение замены, повреждённый файл, read-only папку, кириллические пути, 100/150/200% DPI, масштаб/курсоры, справку при отключённом интернете. Проверить полный пакет на чистой Windows 10 1809+ x64, записать edition/build, CPU/RAM, SHA исходников/ZIP и результат каждого сценария. Отдельно проверить содержимое лицензий. Это ещё не `software-ready` общего MVP.

## Структура

- `motortester/model.py`, `recording.py`: исходные данные, валидация, файлы и CSV
- `motortester/emulator.py`: внутренний синтетический источник, не протокол устройства
- `motortester/plotting.py`: офлайн SVG; `gui.py`: просмотр Qt
- `tests/`, `scripts/check_first_path.py`: проверки и воспроизводимый сценарий
- `docs/format-v1.md`: точное описание файла; `docs/help.md`: источник офлайн-справки
- `packaging/`: рецепт и lock Windows-кандидата; общей CI владеет команда A

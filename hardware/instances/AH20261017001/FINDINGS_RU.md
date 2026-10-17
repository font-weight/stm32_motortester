# STM32Cube в VS Code: точный внешний BIN и readback

Дата исследования: 17.10.2026. Это документационное предложение для интегратора, не разрешение подключать, прошивать или запускать плату. `flash_approved=false`, `hardware_verified=false`. Компьютер владельца и установленное расширение не исследовались; аппаратных действий не было.

## Вывод

Официальный путь без пересборки — уже установленный STM32CubeProgrammer CLI: непосредственно `STM32_Programmer_CLI.exe` либо через поддерживаемый установленной версией `cube programmer`. Он принимает отдельный BIN с явным адресом, проверяет запись и экспортирует заданный участок Flash в бинарный файл. Новый установщик заранее не нужен: сначала проверяется существующий bundle или CubeCLT.

Имя «stm32cube» пока не устанавливает точный publisher, extension ID и версию. Текущий официальный пакет ST имеет ID `stmicroelectronics.stm32-vscode-extension`; в разных поколениях и составе пакета механизм поставки инструментов отличается. Существующее рабочее расширение не обновлять и не заменять ради этой инструкции.

Точный кандидат проверен локально:
- `CALLER_ASSERTED_NOT_FLASH_APPROVED.bin`
- 33556 байт = `0x8314`
- SHA-256 `bae2ef0461d26b8dd5c92496fcc621b5b84b2dbe4e6fb175cc4aa120d49fe041`
- Начало `0x08000000`; конец, не включая его, `0x08008314`; последний байт `0x08008313`

Сообщение владельца о прежней успешной записи через VS Code/ST-Link V2 принимается как история работоспособного маршрута. Оно не подтверждает нынешний набор байтов. Резервная копия старой прошивки, по уточнению владельца, не требуется; отдельное чтение старых 64 KiB не включено.

## Почему не обычная кнопка запуска проекта

ST описывает стандартный debug launch с `preBuild` и выбором binary из текущего project context. Такая команда способна пересобрать проект и выбрать иной файл. Для этого кандидата не использовать F5, Run and Debug, build-and-flash task или существующий Flash project без отдельного аудита его аргументов и зависимостей. Открытие BIN в редакторе не делает его автоматически файлом прошивки. GDB здесь не нужен.

В проверенных официальных разделах расширения не найден отдельный документированный GUI-workflow «произвольный внешний BIN → экспорт ровно 33556 байт». Это граница найденных сведений, а не утверждение, что никакая версия расширения не умеет такой экспорт. Подтверждённый путь readback — CubeProgrammer CLI.

## Минимальный поиск на Windows владельца, без платы

1. В Extensions открыть уже установленное расширение и записать publisher, ID и версию. Достаточно одного снимка этой страницы, если состав неясен; не нужна переустановка.
2. Открыть новый PowerShell-терминал внутри существующей сессии VS Code. Сначала выполнить `Get-Command cube, STM32_Programmer_CLI.exe, STM32CubeCLT_metadata* -ErrorAction SilentlyContinue | Select-Object Name,Source`.
3. Если найден `cube`: выполнить `cube --help`, `cube --list`, `cube --version`. Только если в локальной справке есть `--resolve`, выполнить `cube --resolve programmer`; он показывает разрешённый executable без его запуска. `cube --json bundle show programmer` показывает установленный bundle. Проверить, что programmer действительно установлен и в локальной справке доступна команда `programmer`.
4. Если wrapper не найден, но есть старый CubeCLT: `STM32CubeCLT_metadata -j` показывает установленные компоненты. Путь берётся оттуда, из существующей конфигурации инструмента или старого успешного журнала. При видимом STM32Cube Bundles Manager достаточно проверить установленную строку programmer и её версию. Не нажимать Install/Update/Uninstall/Lock.
5. Если по этим данным путь ещё не установлен, передать интегратору ID/версию расширения и вывод поиска. Не подставлять типичный `C:\ST\...` или `%LOCALAPPDATA%\...` как якобы фактический путь. Пустой PATH не доказывает отсутствие CLI.

После нахождения executable вызывается его `--help` без соединения с платой. В журнале нужно видеть название/версию STM32CubeProgrammer и поддержку `-c`, `-w`, `-v`, `-u`/`--upload`, `-log`. Версия на сайте не подменяет установленную версию. Если выбранный пакет не даёт нужных операций, остановиться до записи; проверить уже установленную копию CLI в bundle/CubeCLT. Если её нет, отдельная установка обсуждается позже.

## Предложенная последовательность

Готовые PowerShell-фрагменты находятся в `FLASH_SEQUENCE_RU.md`. Они запускаются по одному после заполнения фактических путей и прохождения аппаратных условий. Подготовка без платы отделена от соединения/записи.

1. Проверить SHA и длину входного файла; сохранить путь, версию и SHA executable, дату, версию расширения и tool help.
2. После отдельного допуска на точную карту питания и образ закрыть debug-сессии/других клиентов ST-Link. Сохранить список probe, выбрать его по реальному serial, затем прочитать connect report и сверить MCU с паспортом.
3. Записать только этот BIN: `-w <BIN> 0x08000000 -v`. Не применять mass erase, RDP regression, изменение option bytes или «исправление защиты».
4. Отдельно экспортировать `-u 0x08000000 33556 <NEW_READBACK.bin>`. Не использовать `-r32`: у этой команды документирован предел 32 KiB, а наш BIN больше.
5. Убедиться, что обе операции без ошибок, verify успешен, новый readback ровно 33556 байт и его SHA совпадает с SHA кандидата. Сохранить оба файла и журналы, включая неуспешные. Наличие файла/нулевой exit code по отдельности недостаточны.
6. Только после этого перейти к настоящему system reset/power cycle по утверждённой карте. Рекомендуемый уже выбранным проектом путь — обесточивание, отключение SWD и отдельное включение только native USB при BOOT0=0. Этот документ не назначает неизвестные контакты или новые источники питания. `-g`/Run по адресу не заменяет этот переход.

Если отдельно требуется программный system reset, CubeProgrammer предоставляет `-rst`; это ещё не физическое отключение питания. `-hardRst` и `mode=UR` требуют реально подключённого NRST и отдельной проверки карты. Для текущей карты с тремя SWD-проводами их не добавлять. Управление питанием через `-pwr` не предполагается для имеющегося ST-Link V2.

Успешный flash/readback подтверждает только указанные байты указанного участка Flash. Он не доказывает корректность подключения, штатный USB boot, работающий ADC, USB serial/HELLO/CAPS, LV-001 или разрешение ARM.

## Основания

- [Официальный пакет ST в Marketplace](https://marketplace.visualstudio.com/items?itemName=stmicroelectronics.stm32-vscode-extension): текущая архитектура с bundles и cube wrapper вместо обязательного монолитного CLT.
- [ST, Debug](https://dev.st.com/stm32cube-docs/stm32cubeide-vscode/latest/en/docs/markup/development/debug.html#debug-launch): default preBuild и project-context binary.
- [ST, CubeCLI command reference](https://dev.st.com/stm32cube-docs/stm32cubecli/latest/en/docs/markup/STM32CubeCLI_Command_Reference.html): help/list/resolve/version, установленный bundle и разрешение команды.
- [ST, API manual](https://dev.st.com/stm32cube-docs/stm32cubeide-vscode/latest/en/docs/markup/development/cli_toolbox.html): `cube --list` и `cube --json bundle show`.
- [ST, Bundles](https://dev.st.com/stm32cube-docs/stm32cubeide-vscode/latest/en/docs/markup/basic_concepts/bundles.html#stm32cube-bundles-manager): панель bundles через значок STM32.
- [ST, UM3088 Rev 3, §§3 и 4.3](https://www.st.com/resource/en/user_manual/um3088-stm32cube-commandline-toolset-quick-start-guide-stmicroelectronics.pdf): CubeProgrammer в CubeCLT и команда метаданных путей.
- [ST, CubeProgrammer 2.23.0 MCU commands](https://dev.st.com/stm32cube-docs/prog/2.23.0/en/docs/markup/CubeProg_Command_Lines.html): синтаксис write/verify/upload/log/reset, NORMAL/UR и предел r32. Проверить также локальную справку установленной версии.
- [ST, CubeProgrammer system requirements](https://dev.st.com/stm32cube-docs/prog/2.23.0/en/docs/markup/CubeProg_How_To_Start/CubeProg_System_Requirements.html): Windows 10 x64 перечислена среди поддерживаемых систем текущей документации.

Проверено: официальные документы и арифметика диапазона, SHA/размер существующего локального кандидата. Не проверено: Windows/PowerShell владельца, наличие и версия CLI, реальная плата, выполнение команд, readback, reset или USB. Независимый аппаратный допуск этим исследованием не выдаётся.

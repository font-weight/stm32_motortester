# PowerShell: подготовка и будущая запись точного BIN

Статус: исполняемые фрагменты для интегратора; не запускались на Windows или аппаратуре. Это не команда прошивать сейчас. До этапа B должны быть отдельно подтверждены точный образ, карта питания/SWD и разрешение конкретного сеанса. Старую прошивку сохранять не требуется по сообщению владельца.

Не копировать весь документ одним блоком. Каждый этап выполняется и проверяется отдельно. Если переменные потерялись после закрытия терминала, начать подготовку заново, не угадывать значения.

## A. Подготовка без подключения платы

Сначала найти фактический путь установленного CLI по FINDINGS_RU.md. Для современного bundle рекомендовано получить его через `cube --resolve programmer`, если эта операция есть в локальной справке. Имеющийся CLI можно запускать из терминала VS Code без build/debug. Нижние placeholders заменить действительными абсолютными путями; оставленные placeholders дают ошибку.

```powershell
$ErrorActionPreference = 'Stop'
$Cli = (Resolve-Path -LiteralPath 'C:\REPLACE_WITH_ACTUAL_PATH\STM32_Programmer_CLI.exe').Path
$Bin = (Resolve-Path -LiteralPath 'C:\REPLACE_WITH_IMAGE_FOLDER\CALLER_ASSERTED_NOT_FLASH_APPROVED.bin').Path
$Expected = 'bae2ef0461d26b8dd5c92496fcc621b5b84b2dbe4e6fb175cc4aa120d49fe041'
$ExpectedBytes = 33556
if ([IO.Path]::GetExtension($Bin) -ne '.bin') { throw 'Expected a BIN file' }
if ((Get-Item -LiteralPath $Bin).Length -ne $ExpectedBytes) { throw 'BIN length mismatch' }
if ((Get-FileHash -LiteralPath $Bin -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Expected) { throw 'BIN SHA-256 mismatch' }

# New directory prevents accidental reuse of an older successful readback.
$LogDir = Join-Path (Split-Path -Parent $Bin) ('flash-evidence-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $LogDir | Out-Null
$Readback = Join-Path $LogDir 'readback-08000000-33556.bin'
@(
    ('UTC=' + [DateTime]::UtcNow.ToString('o'))
    ('PowerShell=' + $PSVersionTable.PSVersion.ToString())
    ('OS=' + [Environment]::OSVersion.VersionString)
    ('CLI=' + $Cli)
    ('BIN=' + $Bin)
    ('BIN_BYTES=' + (Get-Item -LiteralPath $Bin).Length)
    ('CLI_SHA256=' + (Get-FileHash -LiteralPath $Cli -Algorithm SHA256).Hash)
    ('BIN_SHA256=' + (Get-FileHash -LiteralPath $Bin -Algorithm SHA256).Hash)
) | Set-Content -LiteralPath (Join-Path $LogDir 'identity.txt') -Encoding UTF8
(Get-Item -LiteralPath $Cli).VersionInfo | Format-List * | Out-File -LiteralPath (Join-Path $LogDir 'cli-file-version.txt') -Encoding UTF8
& $Cli --help 2>&1 | Tee-Object -FilePath (Join-Path $LogDir 'programmer-help.txt')
$HelpExit = $LASTEXITCODE
('help_exit=' + $HelpExit) | Add-Content -LiteralPath (Join-Path $LogDir 'identity.txt')
if ($HelpExit -ne 0) { throw 'CLI help failed; stop and inspect' }
```

Сохранить также publisher/ID/версию расширения и, если использован cube, вывод `cube --version`, `cube --list` и `cube --resolve programmer`. SHA exe отличает эту копию, но не является хешем всей установки с DLL/loaders. Не выдавать его за полный supply-chain manifest.

Открыть сохранённую справку: это должен быть STM32CubeProgrammer с нужными операциями. Если старый CLI не поддерживает какой-либо аргумент, не продолжать через удаление проверок. В выбранном installed bundle/CubeCLT искать поддерживаемый CLI; при отсутствии согласовать установку отдельно.

Если требуется именно wrapper вместо прямого exe, эквивалентный префикс — `cube programmer` вместо `& $Cli`. Использовать его только после подтверждения installed programmer и выбранной версии; никаких `cube bundle install/update/init/fix` эта процедура не требует. В журнале сохранить resolved executable и его SHA. Не смешивать выбранные версии в одном сеансе.

## B. После допуска: probe и connect report

До следующего блока оператор выполняет утверждённую физическую карту. Необходима остановленная debug-сессия; другой клиент ST-Link не должен удерживать соединение. Подготовка и проверка help не означают разрешения на питание платы.

```powershell
& $Cli -l 2>&1 | Tee-Object -FilePath (Join-Path $LogDir 'probe-list.txt')
if ($LASTEXITCODE -ne 0) { throw 'Probe enumeration failed' }
```

Из вывода выбрать serial именно нужного физического ST-Link. Если probe нет, несколько неоднозначных probe, serial пуст/не читается или команда не поддержана локальной справкой, остановиться. Не использовать «первый найденный» автоматически.

```powershell
$ProbeSN = 'REPLACE_WITH_REPORTED_STLINK_SERIAL'
if ($ProbeSN -eq 'REPLACE_WITH_REPORTED_STLINK_SERIAL' -or [string]::IsNullOrWhiteSpace($ProbeSN)) { throw 'Select the actual ST-Link serial first' }
$Connect = @('-c', 'port=SWD', ('sn=' + $ProbeSN), 'mode=NORMAL', 'reset=SWrst')
& $Cli @Connect -log (Join-Path $LogDir 'connect.trace.log') 2>&1 | Tee-Object -FilePath (Join-Path $LogDir 'connect.console.txt')
if ($LASTEXITCODE -ne 0) { throw 'Connection failed; stop before programming' }
```

NORMAL выполняет reset/halt цели. Это уже аппаратная операция. В connect report вручную сверить probe serial, MCU/device ID, размер Flash, напряжение и фактическую частоту SWD с паспортом/допуском. Показание программатора не подменяет измерение мультиметром. Не продолжать при неизвестном или несовместимом результате; не менять RDP/option bytes и не переходить автоматически к UR/HWrst.

## C. Запись и verify

Только после проверки B. Вводный BIN повторно хешируется непосредственно перед записью. Никаких команд сборки проекта нет. `-v` стоит сразу после записи и её аргументов.

```powershell
if ((Get-Item -LiteralPath $Bin).Length -ne $ExpectedBytes -or (Get-FileHash -LiteralPath $Bin -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Expected) { throw 'Input BIN changed' }
& $Cli @Connect -w $Bin '0x08000000' -v -log (Join-Path $LogDir 'flash-verify.trace.log') 2>&1 | Tee-Object -FilePath (Join-Path $LogDir 'flash-verify.console.txt')
$FlashExit = $LASTEXITCODE
('flash_verify_exit=' + $FlashExit) | Add-Content -LiteralPath (Join-Path $LogDir 'identity.txt')
if ($FlashExit -ne 0) { throw 'Flash/verify failed; preserve logs and stop' }
```

Проверить текст журнала: правильные файл/адрес/размер, успешная запись и успешный verify, отсутствие ошибок. При любой ошибке дальнейший этап не выполнять. Нулевой exit code не заменяет проверку сообщения verify. Явный mass erase не добавляется; автоматическое стирание определяется установленным backend и может затронуть больше загружаемого диапазона. Сохранение остальной Flash не обещается; старую программу владелец сохранять не требует.

## D. Readback ровно заданного диапазона и сравнение

Этот этап снова соединяется с тем же probe в NORMAL. Он читает текущую Flash, а не копирует исходный BIN. Выходного файла ещё не должно быть.

```powershell
if (Test-Path -LiteralPath $Readback) { throw 'Readback already exists; do not overwrite evidence' }
& $Cli @Connect -u '0x08000000' '33556' $Readback -log (Join-Path $LogDir 'readback.trace.log') 2>&1 | Tee-Object -FilePath (Join-Path $LogDir 'readback.console.txt')
$ReadExit = $LASTEXITCODE
('readback_exit=' + $ReadExit) | Add-Content -LiteralPath (Join-Path $LogDir 'identity.txt')
if ($ReadExit -ne 0) { throw 'Readback failed; preserve logs and stop' }
if (-not (Test-Path -LiteralPath $Readback -PathType Leaf)) { throw 'No readback file' }
$ReadBytes = (Get-Item -LiteralPath $Readback).Length
$ReadSHA = (Get-FileHash -LiteralPath $Readback -Algorithm SHA256).Hash.ToLowerInvariant()
@(
    ('READBACK_BYTES=' + $ReadBytes)
    ('READBACK_SHA256=' + $ReadSHA)
    'RANGE=[0x08000000,0x08008314)'
) | Add-Content -LiteralPath (Join-Path $LogDir 'identity.txt')
if ($ReadBytes -ne $ExpectedBytes -or $ReadSHA -ne $Expected) { throw 'READBACK MISMATCH: do not start firmware or acquisition' }
'IMAGE_BYTES_MATCH_ONLY; hardware/USB/ADC not verified' | Add-Content -LiteralPath (Join-Path $LogDir 'identity.txt')
```

Сохранить readback, input BIN, identity и все логи. Успешное сравнение не разрешает ARM. На ошибке не применять обход защиты, массовое стирание или повторный цикл «наудачу».

## E. Reset / power cycle

Предпочтительный следующий шаг для данного проекта — физический power cycle по одобренной карте с отсоединённым SWD и последующим native USB-only включением, BOOT0=0. Команды `-g`/`--start`, Run и переход по PC в этот маршрут не входят.

## Операторские сроки и прекращение команды

Эти блоки не содержат программного watchdog. Не оставлять терминал без
наблюдения; отсчёт вести внешними часами от запуска каждой native-команды.
Предлагаемые максимальные сроки действий оператора: help/список probe/connect
по 30 секунд; flash+verify и readback по 120 секунд каждый. Это сроки
прекращения ожидания человеком, не гарантированное время завершения процесса
или аппаратной операции. При ошибке ждать предельного срока не нужно.

Если срок вышел или программа зависла, не выполнять следующие блоки:
нажать Ctrl+C, сохранить доступный вывод; по утверждённой карте отсоединить
USB самой платы И USB ST-Link, затем уже трогать SWD-провода. Одно Ctrl+C
не доказывает завершения процесса или остановки MCU. Не повторять запись,
не переходить к UR/HWrst/option bytes и не открывать native COM. Если запись
прервана, считать содержимое Flash непроверенным, даже если старый лог
содержит слово success. Дальнейший сеанс требует отдельного разбора.

После штатного совпадения readback для перестановки также сначала
отсоединить оба USB, а затем все SWD-провода. Не считать снятие только USB
платы доказательством отсутствия подпитки через запитанный ST-Link. Перед
следующим штатным USB-only включением программатор и все SWD-провода отсутствуют.

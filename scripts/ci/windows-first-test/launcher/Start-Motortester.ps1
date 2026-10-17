#requires -Version 5.1
<# Draft outer launcher. No agreement/auto-start command-line switch. #>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ManifestPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-LocalFile([string]$Path) {
    # Resolve lexically before any filesystem lookup; reject UNC/provider paths.
    if ([string]::IsNullOrEmpty($Path)) { throw 'Не указан локальный путь.' }
    $full = $Path
    if ($Path -notmatch '^[A-Za-z]:[\\/]') {
        if ([IO.Path]::IsPathRooted($Path) -or $Path.Contains(':')) {
            throw 'Разрешён только путь на локальном диске.'
        }
        $full = [IO.Path]::Combine($PWD.Path, $Path)
    }
    if ($full -notmatch '^[A-Za-z]:[\\/]' -or $full.Substring(2).Contains(':')) {
        throw 'Разрешён только обычный путь на локальном диске.'
    }
    $full = [IO.Path]::GetFullPath($full)
    $driveRoot = [IO.Path]::GetPathRoot($full)
    $driveType = ([IO.DriveInfo]::new($driveRoot)).DriveType
    if ($driveType -notin @([IO.DriveType]::Fixed, [IO.DriveType]::Removable)) {
        throw 'Сетевые и неподдерживаемые диски запрещены.'
    }
    # Inspect from the local drive downward; never traverse a reparse point.
    $current = $driveRoot
    foreach ($part in (@('') + $full.Substring($driveRoot.Length).Split('\'))) {
        if ($part) {
            if (-not $item.PSIsContainer) { throw 'Родитель пути не является папкой.' }
            $current = [IO.Path]::Combine($current, $part)
        }
        $item = Get-Item -LiteralPath $current
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Ссылки и перенаправления в пути запрещены.'
        }
    }
    if ($item.PSIsContainer) { throw 'Ожидался обычный локальный файл.' }
    return $item
}

function Get-CheckedFile($Entry, [string]$Root) {
    if ($Entry.path -isnot [string] -or $Entry.sha256 -isnot [string] -or
        $Entry.sha256 -cnotmatch '^[0-9a-fA-F]{64}$') {
        throw 'Нет корректных пути и SHA256 файла; запуск с заглушками запрещён.'
    }
    $relative = $Entry.path.Replace('/', '\')
    if ([IO.Path]::IsPathRooted($relative) -or $relative -match '[:*?"<>|]' -or
        $relative -match '(^|\\)(\.|\.\.|)(\\|$)') {
        throw 'Путь должен быть непустым, относительным и находиться внутри комплекта.'
    }
    $item = Get-LocalFile ([IO.Path]::Combine($Root, $relative))
    $path = $item.FullName
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    if ($hash -ine $Entry.sha256) { throw "Не совпал SHA256: $relative" }
    return $item.FullName
}

function Show-LocalText([string]$Path) {
    Write-Host "`n--- $Path ---"
    Write-Host ([IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8))
    Write-Host '--- Конец локального документа ---'
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Для этого черновика нужен Windows PowerShell 5.1 или новее в Windows.'
}
if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
    throw 'Нужна интерактивная консоль без перенаправления ввода или вывода. Приложение не запущено.'
}
$manifestItem = Get-LocalFile $ManifestPath
$root = $manifestItem.DirectoryName
$manifest = [IO.File]::ReadAllText($manifestItem.FullName, [Text.Encoding]::UTF8) |
    ConvertFrom-Json
$exe = Get-CheckedFile $manifest.application $root
if ([IO.Path]::GetExtension($exe) -ine '.exe') { throw 'В манифесте ожидается приложение EXE.' }
$documentKeys = @('componentTerms', 'runtimeNotice', 'runtimeText', 'runtimeOriginal', 'pythonLicense')
$documents = @{}
foreach ($key in $documentKeys) {
    $documents[$key] = Get-CheckedFile $manifest.documents.$key $root
}

Show-LocalText $documents.componentTerms
Write-Host "`nПРОЧИТАЙТЕ ДО ЗАПУСКА: условия только для компонентов Microsoft."
Write-Host "Условия runtime, копия для чтения: $($documents.runtimeText)"
Write-Host "Официальный оригинал условий runtime: $($documents.runtimeOriginal)"
Write-Host 'TXT не сохраняет автоматическую нумерацию Word; определяющий оригинал — DOCX.'
Write-Host "Полный файл лицензий CPython: $($documents.pythonLicense)"
Write-Host "Перечень компонентов Microsoft: $($documents.runtimeNotice)"
Write-Host 'Если консоль неудобна, откройте указанные локальные файлы своим просмотрщиком.'
Write-Host 'Права MIT/BSD и применимые права LGPL на другие компоненты сохраняются отдельно.'
Write-Host 'До вашего явного выбора обёртка не запускает приложение.'

while ($true) {
    Write-Host "`nR = читать условия runtime; P = полный CPython LICENSE; N = перечень компонентов"
    Write-Host 'СОГЛАСЕН = принять применимые условия компонентов Microsoft и запустить приложение'
    Write-Host 'ОТКАЗ / Enter / отмена / закрытие окна = не запускать приложение'
    $choice = Read-Host -Prompt 'Ваш выбор (для согласия введите ровно СОГЛАСЕН)'
    if ($null -eq $choice -or $choice.Length -eq 0 -or $choice -ceq 'ОТКАЗ') {
        Write-Host 'Отказ или нет выбора. Motortester не запущен.'
        return
    }
    if ($choice -ceq 'R') { Show-LocalText $documents.runtimeText; continue }
    if ($choice -ceq 'P') { Show-LocalText $documents.pythonLicense; continue }
    if ($choice -ceq 'N') { Show-LocalText $documents.runtimeNotice; continue }
    if ($choice -ceq 'СОГЛАСЕН') { break }
    Write-Host 'Согласия нет. Выберите чтение, ОТКАЗ или точную фразу согласия.'
}

# Recheck the same parsed identities after reading/choice; changes fail closed.
# This is an ordinary local gate, not a signature or adversarial tamper barrier.
foreach ($key in $documentKeys) {
    $null = Get-CheckedFile $manifest.documents.$key $root
}
$exe = Get-CheckedFile $manifest.application $root
Start-Process -FilePath $exe -WorkingDirectory ([IO.Path]::GetDirectoryName($exe))
Write-Host 'Запуск процесса запрошен. Обёртка не передавала команды подключения или захвата.'

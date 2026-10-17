#requires -Version 5.1
<# Only synthetic text files and mocked choices/process creation. No legal assent. #>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Run these inert tests in an existing permitted Windows PowerShell session.'
}
$launcherPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'Start-Motortester.ps1'
$source = [IO.File]::ReadAllText($launcherPath, [Text.Encoding]::UTF8)
# Mock console state and drive classification so CI can exercise inert choices/boundaries.
# No test switch or bypass is added to the shipped script.
$guard = '[Console]::IsInputRedirected -or [Console]::IsOutputRedirected'
if ($source.Split([string[]]@($guard), [StringSplitOptions]::None).Count -ne 2) {
    throw 'Expected exactly one production console guard; update this test deliberately.'
}
$driveExpression = '([IO.DriveInfo]::new($driveRoot)).DriveType'
if ($source.Split([string[]]@($driveExpression), [StringSplitOptions]::None).Count -ne 2) {
    throw 'Expected exactly one drive-type query; update this test deliberately.'
}
$source = $source.Replace($guard, '$script:simulatedRedirectedIO')
$launcher = [ScriptBlock]::Create($source.Replace($driveExpression, '(Get-InertDriveType $driveRoot)'))
$work = Join-Path $PSScriptRoot ('.inert-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $work

# Functions shadow these cmdlets in the invoked script. Never delegate to the real ones.
function Get-InertDriveType([string]$Root) {
    if ($script:networkDrive) { return [IO.DriveType]::Network }
    return ([IO.DriveInfo]::new($Root)).DriveType
}
function Get-Item([string]$LiteralPath) {
    $script:lookupCount++
    if ($script:denyAllLookups) { throw 'TEST REFUSED ANY FILESYSTEM LOOKUP' }
    if ($LiteralPath -eq $script:pretendReparsePath) {
        return [pscustomobject]@{Attributes = [IO.FileAttributes]::ReparsePoint; PSIsContainer = $true}
    }
    # Only ordinary inert local fixture paths reach the real filesystem provider.
    return Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath
}
function Read-Host([string]$Prompt) {
    $script:prompts++
    if ($script:cancelPrompt) { throw 'SIMULATED PROMPT CLOSED' }
    if ($script:changeAfterPrompt) {
        [IO.File]::AppendAllText($script:changeAfterPrompt, 'changed while reading')
        $script:changeAfterPrompt = $null
    }
    if ($script:next -ge $script:choices.Count) { return $null }
    $value = $script:choices[$script:next]
    $script:next++
    return $value
}
function Start-Process([string]$FilePath, [string]$WorkingDirectory) {
    if ([IO.File]::ReadAllText($FilePath) -cne 'INERT FILE; NOT A WINDOWS EXECUTABLE') {
        throw 'Test refused a non-fixture process target.'
    }
    if ($WorkingDirectory -cne [IO.Path]::GetDirectoryName($FilePath)) {
        throw 'Unexpected working directory.'
    }
    $script:starts++
}
function Write-Host($Object) { $script:screen += [string]$Object + "`n" }

function New-Fixture([string]$Name) {
    $dir = Join-Path $work $Name
    $null = New-Item -ItemType Directory -Path $dir
    [IO.File]::WriteAllText((Join-Path $dir 'inert.exe'), 'INERT FILE; NOT A WINDOWS EXECUTABLE')
    $doc = [ordered]@{}
    foreach ($key in @('componentTerms', 'runtimeNotice', 'runtimeText', 'runtimeOriginal', 'pythonLicense')) {
        $file = Join-Path $dir ($key + '.txt')
        [IO.File]::WriteAllText($file, "INERT $key; TEST DATA ONLY; NOT AN AGREEMENT")
        $doc[$key] = @{path = $key + '.txt'; sha256 = (Get-FileHash $file -Algorithm SHA256).Hash}
    }
    $manifest = @{
        application = @{path = 'inert.exe'; sha256 = (Get-FileHash (Join-Path $dir 'inert.exe') -Algorithm SHA256).Hash}
        documents = $doc
    }
    return @{dir = $dir; data = $manifest; path = Join-Path $dir 'manifest.json'}
}

function Test-Case([string]$Name, [object[]]$Choices, [int]$ExpectedStarts,
                   [bool]$ExpectedError, [scriptblock]$Prepare = {}) {
    $fixture = New-Fixture $Name
    $script:choices = $Choices
    $script:starts = 0; $script:prompts = 0; $script:next = 0
    $script:networkDrive = $false; $script:pretendReparsePath = $null
    $script:denyAllLookups = $false; $script:lookupCount = 0; $script:manifestOverride = $null
    $script:simulatedRedirectedIO = $false
    $script:cancelPrompt = $false; $script:changeAfterPrompt = $null; $script:screen = ''
    & $Prepare $fixture
    $fixture.data | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $fixture.path -Encoding UTF8
    $errorOccurred = $false
    $target = $fixture.path
    if ($script:manifestOverride) { $target = $script:manifestOverride }
    try { & $launcher -ManifestPath $target } catch { $errorOccurred = $true }
    if ($script:denyAllLookups -and $script:lookupCount -ne 0) {
        throw "FAIL $Name : rejected path was looked up before rejection"
    }
    if ($script:starts -ne $ExpectedStarts -or $errorOccurred -ne $ExpectedError) {
        throw "FAIL $Name : starts=$script:starts, error=$errorOccurred"
    }
    if ($ExpectedStarts -eq 1 -and ($script:prompts -eq 0 -or
        -not $script:screen.Contains('условия только для компонентов Microsoft') -or
        -not $script:screen.Contains('Официальный оригинал условий runtime'))) {
        throw "FAIL $Name : required local prompt/reference absent"
    }
    if ($Name -eq 'all-readers-then-choice') {
        foreach ($reader in @('runtimeText', 'pythonLicense', 'runtimeNotice')) {
            if (-not $script:screen.Contains("INERT $reader; TEST DATA ONLY")) {
                throw "FAIL $Name : reader output missing for $reader"
            }
        }
    }
    if ($Name -eq 'exact-choice') {
        $script:choices = @('ОТКАЗ'); $script:next = 0; $script:starts = 0
        & $launcher -ManifestPath $fixture.path
        if ($script:starts -ne 0) { throw 'FAIL repeat same fixture: earlier choice bypassed refusal' }
    }
    Microsoft.PowerShell.Utility\Write-Host "PASS (inert only): $Name"
}

try {
    Test-Case 'unc-before-lookup' @('СОГЛАСЕН') 0 $true {
        param($f) $script:manifestOverride = '\\inert.invalid\never-contact\manifest.json'
        $script:denyAllLookups = $true
    }
    Test-Case 'mapped-drive-before-lookup' @('СОГЛАСЕН') 0 $true {
        param($f) $script:networkDrive = $true; $script:denyAllLookups = $true
    }
    Test-Case 'manifest-reparse' @('СОГЛАСЕН') 0 $true {
        param($f) $script:pretendReparsePath = $f.path
    }
    Test-Case 'parent-reparse' @('СОГЛАСЕН') 0 $true {
        param($f) $script:pretendReparsePath = $f.dir
    }
    Test-Case 'exe-reparse' @('СОГЛАСЕН') 0 $true {
        param($f) $script:pretendReparsePath = Join-Path $f.dir 'inert.exe'
    }
    Test-Case 'redirected-console' @('СОГЛАСЕН') 0 $true {
        param($f) $script:simulatedRedirectedIO = $true
    }
    Test-Case 'exact-choice' @('СОГЛАСЕН') 1 $false
    Test-Case 'all-readers-then-choice' @('R', 'P', 'N', 'СОГЛАСЕН') 1 $false
    foreach ($word in @('ОТКАЗ', '', 'YES', 'согласен', ' СОГЛАСЕН', 'СОГЛАСЕН ')) {
        Test-Case ('decline-' + [Guid]::NewGuid().ToString('N')) @($word, '') 0 $false
    }
    Test-Case 'end-of-input' @() 0 $false
    Test-Case 'closed-prompt' @() 0 $true { param($f) $script:cancelPrompt = $true }
    Test-Case 'placeholder' @('СОГЛАСЕН') 0 $true {
        param($f) $f.data.application.sha256 = 'REPLACE_FROM_FRESH_BUILD'
    }
    Test-Case 'bad-exe-hash' @('СОГЛАСЕН') 0 $true {
        param($f) $f.data.application.sha256 = ('0' * 64)
    }
    Test-Case 'missing-exe' @('СОГЛАСЕН') 0 $true {
        param($f) Remove-Item -LiteralPath (Join-Path $f.dir 'inert.exe')
    }
    foreach ($key in @('componentTerms', 'runtimeNotice', 'runtimeText', 'runtimeOriginal', 'pythonLicense')) {
        Test-Case ('missing-' + $key) @('СОГЛАСЕН') 0 $true {
            param($f) Remove-Item -LiteralPath (Join-Path $f.dir ($key + '.txt'))
        }
        Test-Case ('bad-hash-' + $key) @('СОГЛАСЕН') 0 $true {
            param($f) $f.data.documents[$key].sha256 = ('0' * 64)
        }
    }
    Test-Case 'terms-changed-during-prompt' @('СОГЛАСЕН') 0 $true {
        param($f) $script:changeAfterPrompt = Join-Path $f.dir 'runtimeText.txt'
    }
    Test-Case 'exe-changed-during-prompt' @('СОГЛАСЕН') 0 $true {
        param($f) $script:changeAfterPrompt = Join-Path $f.dir 'inert.exe'
    }
    Test-Case 'relative-path-escape' @('СОГЛАСЕН') 0 $true {
        param($f) $f.data.application.path = '../inert.exe'
    }
    Microsoft.PowerShell.Utility\Write-Host 'Only inert branch tests completed; no agreement or native app was exercised.'
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force
}

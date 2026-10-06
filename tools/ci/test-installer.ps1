<#
.SYNOPSIS
    Installs the OpenXTalk-Lite setup program silently for the current user,
    checks the installation, runs the smoke test on it and uninstalls it.

.DESCRIPTION
    1. Refuses to run when OpenXTalk-Lite is already installed for the current
       user: the test would replace that installation and then remove it.
    2. Runs the setup program with
         /CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /NOCANCEL
         /DIR=<new temporary folder> /LOG=<file> /MERGETASKS="desktopicon"
       The folder is under RUNNER_TEMP on GitHub Actions, otherwise under the
       user's temporary folder.
    3. Checks the exit code, the key files, that every file of the staged
       layout was installed with the same size (when the staged layout is
       found), .version, the uninstall registration in HKCU, the Start menu
       and desktop shortcuts, the .oxtstack and .oxtscript associations, and
       that the Users group has Modify on the folders and files the IDE
       writes to at run time.
    4. Runs tools/ci/smoke-test.ps1 -InstallDir on the installed folder.
    5. Runs the uninstaller silently and checks that the registration, the
       installed files, the shortcuts and the associations are gone. Files
       that were not installed but are left in the folder are listed as a
       warning (the program created them at run time).

    If a check fails after the installation, the uninstaller still runs, so
    the machine is left as it was. When .oxtstack or .oxtscript are already
    associated with another program for this user, the association task is
    left out (and not checked) so that the test does not remove it.

    The setup and uninstall logs are written to -LogDir. Exits with 0 when
    every check passed, 1 otherwise. Under GitHub Actions it adds a short
    report to the job summary.

    Written to run under Windows PowerShell 5.1 and PowerShell 7.

.PARAMETER Setup
    The OpenXTalk-Lite-<version>-win-x86_64-setup.exe to test.

.PARAMETER InstallDir
    Folder to install into. It must not exist or be empty. Default: a new
    folder OpenXTalk-Lite-test-<random> under RUNNER_TEMP or the temporary folder.

.PARAMETER Stage
    Staged installed layout to compare the installed files with. Default:
    stage\OpenXTalk-Lite-<version> next to the setup program, if it exists.

.PARAMETER RepoRoot
    Repository root. Default: two levels up from this script.

.PARAMETER LogDir
    Folder for the setup, uninstall and smoke test logs. Default:
    <RUNNER_TEMP>\build-logs\installer under GitHub Actions, otherwise a new
    temporary folder (printed).

.PARAMETER TimeoutSeconds
    How long to wait for the setup program and for the uninstaller.
    Default: 900.

.PARAMETER SkipSmokeTest
    Do not run the smoke test on the installed program.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Setup,
    [string]$InstallDir,
    [string]$Stage,
    [string]$RepoRoot,
    [string]$LogDir,
    [ValidateRange(60, 7200)]
    [int]$TimeoutSeconds = 900,
    [switch]$SkipSmokeTest
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# ProductName names the files; AppName is what the installer shows (its
# AppName: the Start menu folder, the shortcuts, Settings > Apps)
$ProductName = 'OpenXTalk-Lite'
$AppName = 'OpenXTalk Lite'
$ExeName = 'OpenXTalk-Lite.exe'
$Publisher = 'SethMorrowSoftware/OpenXTalk-Lite-1.15'
$RepoUrl = 'https://github.com/SethMorrowSoftware/OpenXTalk-Lite-1.15'
$UsersSid = 'S-1-5-32-545'
$utf8 = New-Object System.Text.UTF8Encoding($false)

# --- Arguments ---
if (-not $RepoRoot) { $RepoRoot = Join-Path $PSScriptRoot '..\..' }
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath.TrimEnd('\')
$Setup = (Resolve-Path -LiteralPath $Setup).ProviderPath
$Version = ([System.IO.File]::ReadAllText((Join-Path $RepoRoot 'ide\.version'))).Trim()
$expectedSetupName = "$ProductName-$Version-win-x86_64-setup.exe"
if ((Split-Path -Leaf $Setup) -ne $expectedSetupName) {
    Write-Warning "Expected a setup program named $expectedSetupName (ide\.version is $Version)."
}

# The uninstall registry key is named after the fixed AppId in the script
$issPath = Join-Path $RepoRoot 'Installer\openxtalk-lite\openxtalk-lite.iss'
$appIdMatch = [regex]::Match([System.IO.File]::ReadAllText($issPath), '(?m)^\s*AppId\s*=\s*\{\{([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\}')
if (-not $appIdMatch.Success) { throw "AppId not found in $issPath" }
$UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{' + $appIdMatch.Groups[1].Value + '}_is1'

$tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
$createdInstallDir = $false
if (-not $InstallDir) {
    $InstallDir = Join-Path $tempBase "$ProductName-test-$suffix"
    $createdInstallDir = $true
}
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
if ((Test-Path -LiteralPath $InstallDir) -and @(Get-ChildItem -LiteralPath $InstallDir -Force).Count -gt 0) {
    throw "Install folder $InstallDir exists and is not empty."
}
if (-not $LogDir) {
    $LogDir = if ($env:RUNNER_TEMP) { Join-Path $env:RUNNER_TEMP 'build-logs\installer' } else { Join-Path $tempBase "$ProductName-test-logs-$suffix" }
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogDir = (Resolve-Path -LiteralPath $LogDir).ProviderPath.TrimEnd('\')
$installLog = Join-Path $LogDir 'setup-install.log'
$uninstallLog = Join-Path $LogDir 'setup-uninstall.log'

if (-not $Stage) {
    $candidate = Join-Path (Split-Path -Parent $Setup) "stage\$ProductName-$Version"
    if (Test-Path -LiteralPath $candidate -PathType Container) { $Stage = $candidate }
}
if ($Stage) { $Stage = (Resolve-Path -LiteralPath $Stage).ProviderPath.TrimEnd('\') }

# --- Helpers ---

$checks = New-Object System.Collections.Generic.List[object]
function Add-Check([string]$Name, [bool]$Passed, [string]$Detail) {
    $checks.Add((New-Object PSObject -Property @{ Name = $Name; Passed = $Passed; Detail = $Detail }))
    $mark = if ($Passed) { 'ok  ' } else { 'FAIL' }
    $text = if ($Detail) { "$Name ($Detail)" } else { $Name }
    Write-Host "  [$mark] $text"
}

# 64-bit registry view, as used by the 64-bit install mode
$registryView = if ([Environment]::Is64BitOperatingSystem) { [Microsoft.Win32.RegistryView]::Registry64 } else { [Microsoft.Win32.RegistryView]::Default }
function Open-RegKey([Microsoft.Win32.RegistryHive]$Hive, [string]$SubKey) {
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($Hive, $registryView)
    try { return $base.OpenSubKey($SubKey) } finally { $base.Dispose() }
}
function Test-RegKey([Microsoft.Win32.RegistryHive]$Hive, [string]$SubKey) {
    $key = Open-RegKey $Hive $SubKey
    if ($null -eq $key) { return $false }
    $key.Dispose()
    return $true
}
# $null when the key or value does not exist; '' is the default value
function Get-RegValue([Microsoft.Win32.RegistryHive]$Hive, [string]$SubKey, [string]$Name) {
    $key = Open-RegKey $Hive $SubKey
    if ($null -eq $key) { return $null }
    try {
        if (@($key.GetValueNames()) -notcontains $Name) { return $null }
        return $key.GetValue($Name)
    }
    finally { $key.Dispose() }
}
$HKCU = [Microsoft.Win32.RegistryHive]::CurrentUser
$HKLM = [Microsoft.Win32.RegistryHive]::LocalMachine

function Test-SamePath([string]$A, [string]$B) {
    if ($null -eq $A -or $null -eq $B) { return $false }
    return ($A.Trim('"').TrimEnd('\') -ieq $B.Trim('"').TrimEnd('\'))
}

# Run a GUI-subsystem program and wait for it; returns the exit code
function Invoke-Program([string]$File, [string]$Arguments) {
    Write-Host "> `"$File`" $Arguments"
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru
    # Read the handle now, or ExitCode is empty after the process has exited
    $null = $process.Handle
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "$File did not finish within $TimeoutSeconds seconds"
    }
    $process.WaitForExit()
    return $process.ExitCode
}

# Relative paths of the files under a folder
function Get-RelativeFiles([string]$Dir) {
    $prefix = $Dir.TrimEnd('\').Length + 1
    foreach ($f in @(Get-ChildItem -LiteralPath $Dir -Recurse -File -Force)) {
        New-Object PSObject -Property @{ Path = $f.FullName.Substring($prefix); Length = $f.Length }
    }
}

# Whether the Users group has an Allow rule with Modify on a file or folder
# (explicit or inherited)
function Test-UsersModify([string]$Path) {
    $modify = [System.Security.AccessControl.FileSystemRights]::Modify
    foreach ($rule in @((Get-Acl -LiteralPath $Path).Access)) {
        if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) { continue }
        if (($rule.FileSystemRights -band $modify) -ne $modify) { continue }
        try { $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { continue }
        if ($sid -eq $UsersSid) { return $true }
    }
    return $false
}

function Get-ShortcutTarget([string]$Path) {
    $shell = New-Object -ComObject WScript.Shell
    try { return $shell.CreateShortcut($Path).TargetPath }
    finally { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) }
}

# --- Before installing ---
Write-Host "Setup      : $Setup"
Write-Host "Version    : $Version"
Write-Host "Install to : $InstallDir"
Write-Host "Stage      : $(if ($Stage) { $Stage } else { '(not found; staged files are not compared)' })"
Write-Host "Logs       : $LogDir"
Write-Host ''

if (Test-RegKey $HKCU $UninstallKey) {
    $existing = Get-RegValue $HKCU $UninstallKey 'InstallLocation'
    throw "$ProductName is already installed for this user ($existing). Uninstall it first: this test would replace it and then remove it."
}
if (Test-RegKey $HKLM $UninstallKey) {
    Write-Warning "$ProductName is installed for all users on this machine; the test installs a separate copy for the current user."
}

$testAssociations = $true
foreach ($pair in @(@('.oxtstack', 'OpenXTalkLite.Stack'), @('.oxtscript', 'OpenXTalkLite.Script'))) {
    $current = Get-RegValue $HKCU "Software\Classes\$($pair[0])" ''
    if ($current -and $current -ne $pair[1]) {
        Write-Warning "$($pair[0]) is associated with '$current' for this user; the file association task is left out of the test."
        $testAssociations = $false
    }
}
$programsDir = [Environment]::GetFolderPath('Programs')
$desktopDir = [Environment]::GetFolderPath('DesktopDirectory')
$groupDir = Join-Path $programsDir $AppName
$appLink = Join-Path $groupDir "$AppName.lnk"
$uninstallLink = Join-Path $groupDir "Uninstall $AppName.lnk"
$desktopLink = Join-Path $desktopDir "$AppName.lnk"
$testDesktopLink = -not (Test-Path -LiteralPath $desktopLink)
if (-not $testDesktopLink) {
    Write-Warning "$desktopLink exists already; the desktop shortcut task is left out of the test."
}
if (Test-Path -LiteralPath $groupDir) {
    throw "The Start menu folder $groupDir exists already; remove it first (the test would remove it)."
}
$taskList = @()
$taskList += $(if ($testDesktopLink) { 'desktopicon' } else { '!desktopicon' })
if (-not $testAssociations) { $taskList += '!fileassoc' }
$tasks = $taskList -join ','

$installed = $false
$uninstalled = $false
$installedFiles = @{}
try {
    # --- Install ---
    Write-Host 'Installing ...'
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $arguments = "/CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /NOCANCEL /DIR=`"$InstallDir`" /LOG=`"$installLog`" /MERGETASKS=`"$tasks`""
    $code = Invoke-Program $Setup $arguments
    $installed = $true
    Write-Host ('Setup finished in {0:N0} s with exit code {1}' -f $timer.Elapsed.TotalSeconds, $code)
    Add-Check 'Setup exit code is 0' ($code -eq 0) "$code"
    if ($code -ne 0) { throw "Setup failed with exit code $code; see $installLog" }

    Write-Host ''
    Write-Host 'Installed files:'
    $keyFiles = @(
        $ExeName, '.version', '.buildnumber', 'edition.txt', 'about.dat',
        'LICENSE',
        'revsecurity.dll', 'revpdfprinter.dll', 'unins000.exe', 'unins000.dat',
        'Toolset\home.livecodescript',
        'Externals\Externals.txt', 'Externals\revdb.dll', 'Externals\revxml.dll', 'Externals\revzip.dll',
        'Externals\Database Drivers\dbsqlite.dll',
        'Toolchain\lc-compile.exe',
        'Runtime\Windows\x86-64\Standalone',
        'Documentation\html_viewer\resources\data\api\exports\xtalk\index.txt'
    )
    $missing = @($keyFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $InstallDir $_) -PathType Leaf) })
    Add-Check "Key files present ($($keyFiles.Count))" ($missing.Count -eq 0) ($missing -join ', ')
    $emptyDirs = @('builder', 'datagrid') | ForEach-Object { "Documentation\html_viewer\resources\data\api\exports\$_\plugins" }
    $missingDirs = @($emptyDirs | Where-Object { -not (Test-Path -LiteralPath (Join-Path $InstallDir $_) -PathType Container) })
    Add-Check 'Empty dictionary plugin folders present' ($missingDirs.Count -eq 0) ($missingDirs -join ', ')

    $versionFile = Join-Path $InstallDir '.version'
    $installedVersion = if (Test-Path -LiteralPath $versionFile) { ([System.IO.File]::ReadAllText($versionFile)).Trim() } else { '' }
    Add-Check ".version is $Version" ($installedVersion -eq $Version) $installedVersion
    $buildFile = Join-Path $InstallDir '.buildnumber'
    $buildNumber = if (Test-Path -LiteralPath $buildFile) { ([System.IO.File]::ReadAllText($buildFile)).Trim() } else { '' }
    Add-Check '.buildnumber is a number' ($buildNumber -match '^\d+$') $buildNumber

    foreach ($f in @(Get-RelativeFiles $InstallDir)) { $installedFiles[$f.Path] = $f.Length }
    if ($Stage) {
        $absent = New-Object System.Collections.Generic.List[string]
        $differ = New-Object System.Collections.Generic.List[string]
        $staged = @{}
        foreach ($f in @(Get-RelativeFiles $Stage)) {
            $staged[$f.Path] = $true
            if (-not $installedFiles.ContainsKey($f.Path)) { $absent.Add($f.Path) }
            elseif ($installedFiles[$f.Path] -ne $f.Length) { $differ.Add($f.Path) }
        }
        $extra = @($installedFiles.Keys | Where-Object { -not $staged.ContainsKey($_) -and $_ -notlike 'unins000.*' })
        Add-Check "All $($staged.Count) staged files installed" ($absent.Count -eq 0) ((@($absent) | Select-Object -First 10) -join ', ')
        Add-Check 'Installed sizes match the staged files' ($differ.Count -eq 0) ((@($differ) | Select-Object -First 10) -join ', ')
        if ($extra.Count -gt 0) {
            Write-Warning ("Installed files that are not in the staged layout: " + ((@($extra) | Select-Object -First 10) -join ', '))
        }
    }

    # --- Registration ---
    Write-Host ''
    Write-Host 'Uninstall registration (HKCU):'
    $registered = Test-RegKey $HKCU $UninstallKey
    Add-Check 'Uninstall key exists' $registered "HKCU\$UninstallKey"
    if ($registered) {
        # Setup adds a suffix to DisplayName when another entry already has
        # the same name (for example an installation for all users)
        $value = Get-RegValue $HKCU $UninstallKey 'DisplayName'
        Add-Check "DisplayName is '$AppName $Version'" ("$value" -like "$AppName $Version*") "$value"
        $expect = [ordered]@{
            DisplayVersion = $Version
            Publisher      = $Publisher
            URLInfoAbout   = $RepoUrl
            HelpLink       = "$RepoUrl/issues"
            URLUpdateInfo  = "$RepoUrl/releases"
        }
        foreach ($name in $expect.Keys) {
            $value = Get-RegValue $HKCU $UninstallKey $name
            Add-Check "$name is '$($expect[$name])'" ($value -eq $expect[$name]) "$value"
        }
        $value = Get-RegValue $HKCU $UninstallKey 'InstallLocation'
        Add-Check 'InstallLocation is the install folder' (Test-SamePath $value $InstallDir) "$value"
        $value = Get-RegValue $HKCU $UninstallKey 'DisplayIcon'
        Add-Check "DisplayIcon is $ExeName" (Test-SamePath $value (Join-Path $InstallDir $ExeName)) "$value"
        $value = Get-RegValue $HKCU $UninstallKey 'UninstallString'
        Add-Check 'UninstallString is unins000.exe' (Test-SamePath $value (Join-Path $InstallDir 'unins000.exe')) "$value"
    }

    # --- Shortcuts ---
    Write-Host ''
    Write-Host 'Shortcuts:'
    $links = @($appLink)
    if ($testDesktopLink) { $links += $desktopLink }
    foreach ($link in $links) {
        $exists = Test-Path -LiteralPath $link -PathType Leaf
        $target = if ($exists) { Get-ShortcutTarget $link } else { '' }
        Add-Check "$link points to $ExeName" ($exists -and (Test-SamePath $target (Join-Path $InstallDir $ExeName))) $target
    }
    Add-Check "$uninstallLink exists" (Test-Path -LiteralPath $uninstallLink -PathType Leaf) ''

    # --- File associations ---
    Write-Host ''
    Write-Host 'File associations (HKCU\Software\Classes):'
    $command = "`"$(Join-Path $InstallDir $ExeName)`" `"%1`""
    $icon = "$(Join-Path $InstallDir $ExeName),1"
    if ($testAssociations) {
        foreach ($pair in @(@('.oxtstack', 'OpenXTalkLite.Stack'), @('.oxtscript', 'OpenXTalkLite.Script'))) {
            $ext = $pair[0]; $progId = $pair[1]
            $value = Get-RegValue $HKCU "Software\Classes\$ext" ''
            Add-Check "$ext opens as $progId" ($value -eq $progId) "$value"
            Add-Check "$ext lists $progId in OpenWithProgids" ($null -ne (Get-RegValue $HKCU "Software\Classes\$ext\OpenWithProgids" $progId)) ''
            $value = Get-RegValue $HKCU "Software\Classes\$progId\shell\open\command" ''
            Add-Check "$progId opens with $ExeName" ($value -eq $command) "$value"
            $value = Get-RegValue $HKCU "Software\Classes\$progId\DefaultIcon" ''
            Add-Check "$progId icon is $ExeName,1" (Test-SamePath $value $icon) "$value"
        }
    }
    else {
        Write-Host '  (association task left out; see the warning above)'
    }
    foreach ($ext in @('.oxtstack', '.oxtscript')) {
        $present = $null -ne (Get-RegValue $HKCU "Software\Classes\Applications\$ExeName\SupportedTypes" $ext)
        Add-Check "Applications\$ExeName supports $ext" $present ''
    }

    # --- Permissions for the IDE's run-time writes ---
    Write-Host ''
    Write-Host 'Users group has Modify on:'
    $exports = 'Documentation\html_viewer\resources\data\api\exports'
    $writable = @($exports, "$exports\xtalk\index.txt")
    foreach ($optional in @('Toolset\palettes\updates\whatsnew.txt')) {
        if (Test-Path -LiteralPath (Join-Path $InstallDir $optional)) { $writable += $optional }
    }
    $history = Join-Path $InstallDir 'Toolset\palettes\updates\updatehistory'
    if (Test-Path -LiteralPath $history -PathType Container) {
        $first = Get-ChildItem -LiteralPath $history -Filter '*.txt' -File | Select-Object -First 1
        if ($first) { $writable += "Toolset\palettes\updates\updatehistory\$($first.Name)" }
    }
    foreach ($rel in $writable) {
        $path = Join-Path $InstallDir $rel
        $ok = (Test-Path -LiteralPath $path) -and (Test-UsersModify $path)
        Add-Check $rel $ok ''
    }

    # --- Smoke test of the installed program ---
    Write-Host ''
    if ($SkipSmokeTest) {
        Write-Host 'Smoke test skipped (-SkipSmokeTest).'
    }
    else {
        Write-Host 'Smoke test of the installed program:'
        $smokeLog = Join-Path $LogDir 'smoke-test-installed.log'
        $smokePath = Join-Path $PSScriptRoot 'smoke-test.ps1'
        $failedCount = $null
        $detail = ''
        try {
            $global:LASTEXITCODE = 0
            & $smokePath -InstallDir $InstallDir -Exe $ExeName -LogFile $smokeLog
            $failedCount = $LASTEXITCODE
            $detail = "$failedCount failed"
        }
        catch {
            $detail = $_.Exception.Message
        }
        $global:LASTEXITCODE = 0
        Add-Check 'Smoke test of the installed OpenXTalk-Lite.exe' ($failedCount -eq 0) $detail
    }

    # --- Uninstall ---
    Write-Host ''
    Write-Host 'Uninstalling ...'
    $uninstaller = Join-Path $InstallDir 'unins000.exe'
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $code = Invoke-Program $uninstaller "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$uninstallLog`""
    $uninstalled = $true
    Add-Check 'Uninstaller exit code is 0' ($code -eq 0) "$code"
    # The uninstaller hands the work to a copy of itself in TEMP and may
    # return before that copy has finished
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline -and ((Test-Path -LiteralPath $uninstaller) -or (Test-RegKey $HKCU $UninstallKey))) {
        Start-Sleep -Milliseconds 500
    }
    Write-Host ('Uninstall finished in {0:N0} s' -f $timer.Elapsed.TotalSeconds)

    Add-Check 'Uninstall key removed' (-not (Test-RegKey $HKCU $UninstallKey)) ''
    $left = @()
    if (Test-Path -LiteralPath $InstallDir) { $left = @(Get-RelativeFiles $InstallDir | ForEach-Object { $_.Path }) }
    $leftInstalled = @($left | Where-Object { $installedFiles.ContainsKey($_) })
    $leftOther = @($left | Where-Object { -not $installedFiles.ContainsKey($_) })
    Add-Check 'Installed files removed' ($leftInstalled.Count -eq 0) ((@($leftInstalled) | Select-Object -First 10) -join ', ')
    if ($leftOther.Count -gt 0) {
        Write-Warning ("Files created after installation and left in ${InstallDir}: " + ((@($leftOther) | Select-Object -First 20) -join ', '))
    }
    elseif (Test-Path -LiteralPath $InstallDir) {
        Write-Host "  (the empty folder $InstallDir is left)"
    }
    $linksLeft = @(@($appLink, $uninstallLink, $groupDir) | Where-Object { Test-Path -LiteralPath $_ })
    if ($testDesktopLink -and (Test-Path -LiteralPath $desktopLink)) { $linksLeft += $desktopLink }
    Add-Check 'Shortcuts removed' ($linksLeft.Count -eq 0) ($linksLeft -join ', ')
    $assocLeft = @()
    foreach ($key in @('Software\Classes\OpenXTalkLite.Stack', 'Software\Classes\OpenXTalkLite.Script', "Software\Classes\Applications\$ExeName")) {
        if (Test-RegKey $HKCU $key) { $assocLeft += $key }
    }
    foreach ($pair in @(@('.oxtstack', 'OpenXTalkLite.Stack'), @('.oxtscript', 'OpenXTalkLite.Script'))) {
        if ((Get-RegValue $HKCU "Software\Classes\$($pair[0])" '') -eq $pair[1]) { $assocLeft += "$($pair[0]) (default)" }
        if ($null -ne (Get-RegValue $HKCU "Software\Classes\$($pair[0])\OpenWithProgids" $pair[1])) { $assocLeft += "$($pair[0])\OpenWithProgids" }
    }
    Add-Check 'File associations removed' ($assocLeft.Count -eq 0) ($assocLeft -join ', ')
}
catch {
    Add-Check 'Test ran to completion' $false $_.Exception.Message
}
finally {
    # Leave the machine as it was, whatever failed
    if ($installed -and -not $uninstalled) {
        $uninstaller = Join-Path $InstallDir 'unins000.exe'
        if (Test-Path -LiteralPath $uninstaller) {
            Write-Host 'Removing the test installation ...'
            try { $null = Invoke-Program $uninstaller "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$uninstallLog`"" }
            catch { Write-Warning "Uninstall failed: $($_.Exception.Message)" }
            Start-Sleep -Seconds 5
        }
    }
    if ($createdInstallDir -and (Test-Path -LiteralPath $InstallDir)) {
        Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# --- Result ---
$failed = @($checks | Where-Object { -not $_.Passed })
$passed = $checks.Count - $failed.Count
Write-Host ''
Write-Host "Logs: $LogDir"
if ($failed.Count -eq 0) {
    Write-Host "Installer test passed ($passed checks)."
}
else {
    Write-Host "Installer test failed: $($failed.Count) of $($checks.Count) checks failed:"
    foreach ($c in $failed) { Write-Host "  - $($c.Name)$(if ($c.Detail) { ": $($c.Detail)" })" }
}
if ($env:GITHUB_STEP_SUMMARY) {
    $md = @('### Installer test', '')
    $result = if ($failed.Count -eq 0) { "all $passed checks passed" } else { "$($failed.Count) of $($checks.Count) checks failed" }
    $md += "Per-user silent install of ``$(Split-Path -Leaf $Setup)``, checks, smoke test of the installed ``$ExeName`` and silent uninstall: $result."
    foreach ($c in $failed) { $md += "- $($c.Name)$(if ($c.Detail) { ": $($c.Detail)" })" }
    $md += ''
    [System.IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, ($md -join "`n") + "`n", $utf8)
}
if ($failed.Count -gt 0) { exit 1 }
exit 0

<#
.SYNOPSIS
    Builds the OpenXTalk Lite 1.15 Windows installer with Inno Setup 6.

.DESCRIPTION
    Compiles Installer\openxtalk-lite\openxtalk-lite.iss over a staged installed
    layout (written by tools/oxt/package.py, normally through
    tools/ci/package-windows.ps1) into

      <OutDir>\OpenXTalk-Lite-<version>-win-x86_64-setup.exe

    and then rewrites <OutDir>\SHA256SUMS over all files in OutDir
    ("<sha256>  <file name>", LF line endings, sorted by name).

    ISCC.exe is looked for in -Iscc, the usual Inno Setup 6 folders, the
    Inno Setup 6 uninstall registration, PATH, and finally the same places
    for Inno Setup 7. When it is not found, Inno Setup is installed with
    "choco install innosetup -y --no-progress" (unless -NoInstall is given).

    The wizard uses Inno Setup's built-in images. Nothing is written into
    the repository except OutDir. The full compiler output goes to -LogFile.

    Written to run under Windows PowerShell 5.1 and PowerShell 7.

.PARAMETER Stage
    The staged installed layout. Default:
    <OutDir>\stage\OpenXTalk-Lite-<version>.

.PARAMETER OutDir
    Where the setup program is written and SHA256SUMS is rewritten.
    Default: <RepoRoot>\dist.

.PARAMETER RepoRoot
    Repository root. Default: two levels up from this script.

.PARAMETER Version
    Product version. Default: the contents of <RepoRoot>\ide\.version. It
    must match the staged .version.

.PARAMETER BuildNumber
    Build number shown in the setup program's version information. Default:
    the staged .buildnumber.

.PARAMETER Iscc
    Path to ISCC.exe; skips the search.

.PARAMETER NoInstall
    Fail instead of installing Inno Setup when ISCC.exe is not found.

.PARAMETER LogFile
    Compiler output. Default: <RUNNER_TEMP>\build-logs\installer\iscc.log under
    GitHub Actions, otherwise iscc.log in a temporary folder (printed).
#>
[CmdletBinding()]
param(
    [string]$Stage,
    [string]$OutDir,
    [string]$RepoRoot,
    [string]$Version,
    [string]$BuildNumber,
    [string]$Iscc,
    [switch]$NoInstall,
    [string]$LogFile
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ProductName = 'OpenXTalk-Lite'
$utf8 = New-Object System.Text.UTF8Encoding($false)

# --- Arguments ---
if (-not $RepoRoot) { $RepoRoot = Join-Path $PSScriptRoot '..\..' }
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath.TrimEnd('\')
$script = Join-Path $RepoRoot 'Installer\openxtalk-lite\openxtalk-lite.iss'
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw "Installer script not found: $script" }

if (-not $Version) {
    $versionFile = Join-Path $RepoRoot 'ide\.version'
    if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) { throw "$versionFile not found" }
    $Version = ([System.IO.File]::ReadAllText($versionFile)).Trim()
}
# Digits first: the installer's version resource takes the numeric part
if ($Version -notmatch '^\d+(\.\d+){0,3}([-+][0-9A-Za-z][0-9A-Za-z.+-]*)?$') {
    throw "Version '$Version' is not a version number usable in a file name (for example 0.0.1 or 0.1.0-beta.1)."
}

if (-not $OutDir) { $OutDir = Join-Path $RepoRoot 'dist' }
# Resolved against the current location, like Resolve-Path, but without
# requiring the folder to exist yet
$OutDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutDir).TrimEnd('\')

if (-not $Stage) { $Stage = Join-Path $OutDir "stage\$ProductName-$Version" }
if (-not (Test-Path -LiteralPath $Stage -PathType Container)) {
    throw "Staged layout not found: $Stage (run tools/ci/package-windows.ps1 or tools/oxt/package.py first)"
}
$Stage = (Resolve-Path -LiteralPath $Stage).ProviderPath.TrimEnd('\')
if (($OutDir + '\').StartsWith($Stage + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutDir ($OutDir) must not be inside the staged layout ($Stage)."
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

foreach ($required in @("$ProductName.exe", 'LICENSE', '.version', 'Toolset\home.livecodescript')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Stage $required) -PathType Leaf)) {
        throw "The staged layout has no $required ($Stage)"
    }
}
# The compiler silently skips hidden files and folders matched by a wildcard
# (BuildFileList in Inno Setup's Compiler.SetupCompiler.pas)
$hidden = @(Get-ChildItem -LiteralPath $Stage -Recurse -Force |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::Hidden) -ne 0 } |
        ForEach-Object { $_.FullName.Substring($Stage.Length + 1) })
if ($hidden.Count -gt 0) {
    throw ("The staged layout has hidden files or folders, which the installer would leave out: " + (($hidden | Select-Object -First 10) -join ', '))
}
$stagedVersion = ([System.IO.File]::ReadAllText((Join-Path $Stage '.version'))).Trim()
if ($stagedVersion -ne $Version) {
    throw "The staged .version is '$stagedVersion' but the installer version is '$Version'."
}
if (-not $BuildNumber) {
    $buildFile = Join-Path $Stage '.buildnumber'
    if (Test-Path -LiteralPath $buildFile -PathType Leaf) {
        $BuildNumber = ([System.IO.File]::ReadAllText($buildFile)).Trim()
    }
}
if ($BuildNumber -notmatch '^\d+$') { throw "Build number '$BuildNumber' is missing or not a number." }

$runnerTemp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$workDir = Join-Path $runnerTemp ('oxtl-installer-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
if (-not $LogFile) {
    if ($env:RUNNER_TEMP) {
        $LogFile = Join-Path $env:RUNNER_TEMP 'build-logs\installer\iscc.log'
    }
    else {
        $LogFile = Join-Path $workDir 'iscc.log'
    }
}

$setupName = "$ProductName-$Version-win-x86_64-setup.exe"
$setupPath = Join-Path $OutDir $setupName

Write-Host "Repository   : $RepoRoot"
Write-Host "Script       : $script"
Write-Host "Stage        : $Stage"
Write-Host "Output       : $setupPath"
Write-Host "Version      : $Version (build $BuildNumber)"

# --- Locating ISCC.exe ---

function Get-InnoRegistryLocation([string]$Major) {
    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup $($Major)_is1",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup $($Major)_is1",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup $($Major)_is1"
    )
    foreach ($key in $keys) {
        $item = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
        if ($item -and ($item.PSObject.Properties.Name -contains 'InstallLocation') -and $item.InstallLocation) {
            Join-Path $item.InstallLocation 'ISCC.exe'
        }
    }
}

function Find-Iscc {
    $candidates = New-Object System.Collections.Generic.List[string]
    foreach ($major in @('6', '7')) {
        foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
            if ($base) { $candidates.Add((Join-Path $base "Inno Setup $major\ISCC.exe")) }
        }
        foreach ($p in @(Get-InnoRegistryLocation $major)) { $candidates.Add($p) }
        if ($major -eq '6') {
            # A shim or a folder on PATH (for example from Chocolatey)
            foreach ($c in @(Get-Command 'ISCC.exe' -CommandType Application -All -ErrorAction SilentlyContinue)) {
                $candidates.Add($c.Source)
            }
        }
    }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c -PathType Leaf)) { return (Resolve-Path -LiteralPath $c).ProviderPath }
    }
    return $null
}

if ($Iscc) {
    if (-not (Test-Path -LiteralPath $Iscc -PathType Leaf)) { throw "ISCC not found: $Iscc" }
    $Iscc = (Resolve-Path -LiteralPath $Iscc).ProviderPath
}
else {
    $Iscc = Find-Iscc
    if (-not $Iscc) {
        if ($NoInstall) { throw 'ISCC.exe (Inno Setup 6) was not found and -NoInstall was given.' }
        $choco = Get-Command 'choco' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $choco) { throw 'ISCC.exe (Inno Setup 6) was not found, and Chocolatey is not available to install it.' }
        Write-Host 'ISCC.exe not found; installing Inno Setup with Chocolatey ...'
        & $choco.Source install innosetup -y --no-progress
        $code = $LASTEXITCODE
        # 3010 and 1641: installed, a restart is pending
        if ($code -ne 0 -and $code -ne 3010 -and $code -ne 1641) { throw "choco install innosetup failed (exit code $code)" }
        $global:LASTEXITCODE = 0
        $Iscc = Find-Iscc
        if (-not $Iscc) { throw 'Inno Setup was installed, but ISCC.exe was not found afterwards.' }
    }
}
$isccVersion = (Get-Item -LiteralPath $Iscc).VersionInfo.ProductVersion
Write-Host "ISCC         : $Iscc ($isccVersion)"
if ($isccVersion -and -not "$isccVersion".StartsWith('6.')) {
    Write-Warning "The installer script targets Inno Setup 6; compiling with Inno Setup $isccVersion."
}

# --- Build ---
$started = Get-Date
try {
    New-Item -ItemType Directory -Force -Path $workDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogFile) | Out-Null

    $defines = [ordered]@{
        AppVersion  = $Version
        BuildNumber = $BuildNumber
        StageDir    = $Stage
        OutputDir   = $OutDir
        RepoRoot    = $RepoRoot
    }
    # Paths have no trailing backslash, so the quoting that PowerShell adds
    # around arguments with spaces cannot be escaped by one
    $isccArgs = @()
    foreach ($name in $defines.Keys) {
        $value = [string]$defines[$name]
        if ($value.Contains('"')) { throw "Define $name contains a double quote: $value" }
        $isccArgs += "/D$name=$value"
    }
    $isccArgs += $script

    if (Test-Path -LiteralPath $setupPath) { Remove-Item -LiteralPath $setupPath -Force }

    Write-Host ''
    Write-Host "Compiling (full output in $LogFile) ..."
    $lines = New-Object System.Collections.Generic.List[string]
    $compressed = 0
    # ISCC writes errors to stderr; Windows PowerShell turns redirected
    # stderr lines into error records, which 'Stop' would make fatal
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Iscc @isccArgs 2>&1 | ForEach-Object {
            $line = "$_"
            $lines.Add($line)
            # One line per compressed file would flood the log; count them
            if ($line -match '^\s*Compressing:') {
                $compressed++
                if ($compressed % 1000 -eq 0) { Write-Host "  ... $compressed files compressed" }
            }
            else {
                Write-Host $line
            }
        }
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedPreference
    }
    [System.IO.File]::WriteAllLines($LogFile, $lines, $utf8)
    if ($code -ne 0) { throw "ISCC failed with exit code $code (see $LogFile)" }
    $global:LASTEXITCODE = 0

    if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) { throw "ISCC succeeded but $setupPath was not written" }
    if ((Get-Item -LiteralPath $setupPath).LastWriteTime -lt $started.AddSeconds(-5)) { throw "$setupPath is older than this build" }
}
finally {
    # Keep the work folder only when it holds the log
    if (-not ($LogFile.StartsWith($workDir + '\', [System.StringComparison]::OrdinalIgnoreCase))) {
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Compiler output: $LogFile"
}

# --- Checksums over everything in OutDir ---
$sumsPath = Join-Path $OutDir 'SHA256SUMS'
$names = [string[]]@(Get-ChildItem -LiteralPath $OutDir -File | Where-Object { $_.Name -ne 'SHA256SUMS' } | ForEach-Object { $_.Name })
[Array]::Sort($names, [System.StringComparer]::Ordinal)
$sumLines = @()
$results = @()
foreach ($name in $names) {
    $f = Get-Item -LiteralPath (Join-Path $OutDir $name)
    $hash = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $sumLines += "$hash  $name"
    $results += New-Object PSObject -Property @{ Name = $name; Bytes = $f.Length; Sha256 = $hash }
}
[System.IO.File]::WriteAllText($sumsPath, (($sumLines -join "`n") + "`n"), $utf8)

$setupItem = Get-Item -LiteralPath $setupPath
$setupHash = ($results | Where-Object { $_.Name -eq $setupName } | Select-Object -First 1).Sha256
Write-Host ''
Write-Host ('Installer: {0} ({1:N1} MB, {2:N0} s)' -f $setupPath, ($setupItem.Length / 1MB), ((Get-Date) - $started).TotalSeconds)
Write-Host "SHA256SUMS in ${OutDir}:"
foreach ($line in $sumLines) { Write-Host "  $line" }

# --- GitHub Actions outputs and job summary ---
if ($env:GITHUB_OUTPUT) {
    [System.IO.File]::AppendAllText($env:GITHUB_OUTPUT, "setup-path=$setupPath`nsetup-name=$setupName`n", $utf8)
}
if ($env:GITHUB_STEP_SUMMARY) {
    $md = @('### Installer', '', '| File | Size | SHA-256 |', '| --- | --- | --- |')
    $md += ('| {0} | {1:N1} MB | `{2}` |' -f $setupName, ($setupItem.Length / 1MB), $setupHash)
    $md += ''
    $compiler = if ($isccVersion) { "Inno Setup $isccVersion" } else { $Iscc }
    $md += "Built with $compiler. SHA256SUMS now covers the $($results.Count) file(s) in ``$(Split-Path -Leaf $OutDir)``."
    $md += ''
    [System.IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, ($md -join "`n") + "`n", $utf8)
}

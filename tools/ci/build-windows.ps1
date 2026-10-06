<#
.SYNOPSIS
    Runs the Windows configure and build steps with a known tool setup.

.DESCRIPTION
    This is the configure/build part of .github/workflows/build-windows.yml,
    kept in a script so that it can also be run by hand. It does the same as
    the manual steps in BUILDING.md:

        configure:  C:\Python27\python.exe config.py --platform win-x86_64
                    (from the repository root)
        build:      ..\make.cmd [target]
                    (from build-win-x86_64)

    Before running them it arranges PATH so that:
      - python is Python 2.7 (gyp, and util/remove_matching.py during the
        build, need Python 2),
      - perl is a native Windows perl such as Strawberry Perl (gyp actions
        run "perl" through cmd.exe),
      - no folder containing cygpath.exe is on PATH (Git's usr\bin, MSYS2,
        Cygwin), and CYGPATH points at the Cygwin installation, so that
        util/invoke-unix.bat runs the Cygwin bash, flex and bison.

    Other environment variables that make.cmd reads (BUILDTYPE,
    BUILD_PLATFORM, VSINSTALLDIR, WINSDK_VERSION, MSBUILD_EXTRA_ARGS) and that
    prebuilt/fetch-libraries.sh reads (PREBUILT_*) are passed through as they
    are.

.PARAMETER Stage
    configure, build, or both (the default, in that order).

.PARAMETER Target
    msbuild target for make.cmd. Default: make.cmd's default target.

.PARAMETER RepoRoot
    Repository root. Default: two levels up from this script.

.PARAMETER Python
    Python 2.7 interpreter. Default: C:\Python27\python.exe.

.PARAMETER CygwinRoot
    Cygwin installation. Default: %CYGPATH%, else C:\cygwin64.

.PARAMETER PerlDir
    Folder containing a native perl.exe. Default: the first perl.exe on PATH
    that reports $^O = MSWin32, else C:\Strawberry\perl\bin.

.PARAMETER DryRun
    Print the tool setup and the commands without running them.
#>
[CmdletBinding()]
param(
    [ValidateSet('configure', 'build')]
    [string[]]$Stage = @('configure', 'build'),
    [string]$Target,
    [string]$RepoRoot,
    [string]$Python = 'C:\Python27\python.exe',
    [string]$CygwinRoot,
    [string]$PerlDir,
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) { $RepoRoot = Join-Path $PSScriptRoot '..\..' }
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath.TrimEnd('\')

if (-not $CygwinRoot) {
    if ($env:CYGPATH) { $CygwinRoot = $env:CYGPATH } else { $CygwinRoot = 'C:\cygwin64' }
}
$CygwinRoot = $CygwinRoot.TrimEnd('\')

if (-not $env:BUILD_PLATFORM) { $env:BUILD_PLATFORM = 'win-x86_64' }
$BuildDir = Join-Path $RepoRoot "build-$($env:BUILD_PLATFORM)"

# Run a native program; fail if it returns a non-zero exit code
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    Write-Host "> $Exe $($Arguments -join ' ')"
    if ($DryRun) { return }
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Exe exited with code $LASTEXITCODE"
    }
}

# First line of a program's stdout, or '' (for version reporting only)
function Get-FirstLine([string]$Exe, [string[]]$Arguments) {
    # Windows PowerShell turns redirected stderr into errors, which 'Stop'
    # would make fatal
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Arguments 2>$null
        $global:LASTEXITCODE = 0
        return ([string](@($out) | Select-Object -First 1)).Trim()
    }
    catch {
        return ''
    }
}

# In a dry run a missing tool is only reported
function Stop-OrWarn([string]$Message) {
    if ($DryRun) { Write-Warning $Message } else { throw $Message }
}

function Test-NativePerl([string]$PerlExe) {
    $os = Get-FirstLine $PerlExe @('-e', 'print $^O')
    return ($os -eq 'MSWin32')
}

# --- Python 2.7 ---
# (Arguments avoid embedded double quotes, which Windows PowerShell 5.1 does
# not pass to native programs intact.)
$pyVersion = ''
if (Test-Path -LiteralPath $Python) {
    $pyVersion = Get-FirstLine $Python @('-c', 'import sys; sys.stdout.write(''%d.%d.%d'' % sys.version_info[:3])')
    if (-not $pyVersion.StartsWith('2.7')) {
        Stop-OrWarn "$Python is Python '$pyVersion'; config.py and gyp need Python 2.7."
    }
}
else {
    Stop-OrWarn "Python 2.7 not found at $Python. Install Python 2.7.18 x64 to C:\Python27 or pass -Python."
}
$pythonDir = Split-Path -Parent $Python

# --- Split PATH, dropping every folder that contains cygpath.exe ---
$keptPath = New-Object System.Collections.Generic.List[string]
$droppedPath = New-Object System.Collections.Generic.List[string]
foreach ($dir in ($env:PATH -split ';')) {
    $d = $dir.Trim().Trim('"')
    if (-not $d) { continue }
    $expanded = [Environment]::ExpandEnvironmentVariables($d)
    $hasCygpath = $false
    try { $hasCygpath = [System.IO.File]::Exists([System.IO.Path]::Combine($expanded, 'cygpath.exe')) } catch { }
    if ($hasCygpath) {
        if (-not $droppedPath.Contains($d)) { $droppedPath.Add($d) }
        continue
    }
    if (-not $keptPath.Contains($d)) { $keptPath.Add($d) }
}

# --- Native perl ---
if (-not $PerlDir) {
    foreach ($d in $keptPath) {
        $candidate = $null
        try { $candidate = [System.IO.Path]::Combine([Environment]::ExpandEnvironmentVariables($d), 'perl.exe') } catch { continue }
        if ([System.IO.File]::Exists($candidate) -and (Test-NativePerl $candidate)) {
            $PerlDir = Split-Path -Parent $candidate
            break
        }
    }
}
if (-not $PerlDir) { $PerlDir = 'C:\Strawberry\perl\bin' }
$perlExe = Join-Path $PerlDir 'perl.exe'
if (-not (Test-Path -LiteralPath $perlExe)) {
    Stop-OrWarn "No native Windows perl found (looked on PATH and in $PerlDir). Install Strawberry Perl or pass -PerlDir."
}
elseif (-not (Test-NativePerl $perlExe)) {
    Stop-OrWarn "$perlExe is not a native Windows perl (`$^O is not MSWin32)."
}

# --- New PATH: Python 2.7 and native perl first ---
$front = @($pythonDir, (Join-Path $pythonDir 'Scripts'), $PerlDir)
$newPath = New-Object System.Collections.Generic.List[string]
foreach ($d in ($front + $keptPath.ToArray())) {
    if ($d -and -not $newPath.Contains($d)) { $newPath.Add($d) }
}
$env:PATH = $newPath.ToArray() -join ';'

# util/invoke-unix.bat finds Cygwin through CYGPATH
$env:CYGPATH = $CygwinRoot

# --- Report ---
$gitVersion = Get-FirstLine 'git' @('--version')
$perlVersion = Get-FirstLine $perlExe @('-e', 'printf(q(%vd %s), $^V, $^O)')
Write-Host "Repository      : $RepoRoot"
Write-Host "Stages          : $($Stage -join ', ')"
Write-Host "Python          : $Python ($pyVersion)"
Write-Host "Perl            : $perlExe $perlVersion"
Write-Host "Git             : $gitVersion"
Write-Host "CYGPATH         : $CygwinRoot"
foreach ($name in @('BUILDTYPE', 'BUILD_PLATFORM', 'VSINSTALLDIR', 'WINSDK_VERSION', 'MSBUILD_EXTRA_ARGS',
                    'PREBUILT_URL', 'PREBUILT_WIN32_SUBPLATFORMS', 'PREBUILT_WIN32_LIBS', 'PREBUILT_STRICT')) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($value) { Write-Host ("{0,-16}: {1}" -f $name, $value) }
}
if ($droppedPath.Count -gt 0) {
    Write-Host 'Removed from PATH (they contain cygpath.exe):'
    foreach ($d in $droppedPath) { Write-Host "    $d" }
}

if ($Stage -contains 'build') {
    # invoke-unix.bat needs these for the prebuilt fetch, flex/bison and tzdata steps
    foreach ($tool in @('bash.exe', 'cygpath.exe', 'flex.exe', 'bison.exe', 'm4.exe', 'gawk.exe', 'sed.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $CygwinRoot "bin\$tool"))) {
            Stop-OrWarn "$CygwinRoot\bin\$tool not found. Install Cygwin with flex bison m4 gawk sed grep curl tar bzip2, or set CYGPATH."
        }
    }
    $bisonVersion = Get-FirstLine (Join-Path $CygwinRoot 'bin\bison.exe') @('--version')
    $flexVersion = Get-FirstLine (Join-Path $CygwinRoot 'bin\flex.exe') @('--version')
    Write-Host "Cygwin bison    : $bisonVersion"
    Write-Host "Cygwin flex     : $flexVersion"
}

# --- Configure ---
if ($Stage -contains 'configure') {
    Push-Location -LiteralPath $RepoRoot
    try {
        Invoke-Native $Python @('config.py', '--platform', $env:BUILD_PLATFORM)
    }
    finally {
        Pop-Location
    }
}

# --- Build ---
if ($Stage -contains 'build') {
    if (-not $DryRun -and -not (Test-Path -LiteralPath (Join-Path $BuildDir 'livecode\livecode.sln'))) {
        throw "$BuildDir\livecode\livecode.sln not found; run the configure stage first."
    }
    $makeArgs = @('/d', '/c', '..\make.cmd')
    if ($Target) { $makeArgs += $Target }
    Write-Host "(in $BuildDir)"
    if (-not $DryRun) { Push-Location -LiteralPath $BuildDir }
    try {
        Invoke-Native 'cmd.exe' $makeArgs
    }
    finally {
        if (-not $DryRun) { Pop-Location }
    }
    if (-not $DryRun) {
        Write-Host "Build finished; msbuild log: $BuildDir\msbuild.log"
    }
}

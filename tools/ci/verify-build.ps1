<#
.SYNOPSIS
    Checks a Windows build output folder (win-x86_64-bin or win-x86-bin).

.DESCRIPTION
    Fails (exit code 1) unless:
      - the expected engine, external, database driver, toolchain and CEF
        files exist and are not empty;
      - every required .exe and .dll is a valid PE image for the build's
        architecture (rather than one of the other architecture or a
        placeholder file);
      - modules\lci and packaged_extensions are not empty;
      - dbsqlite.dll contains the SQLITE_SOURCE_ID string from
        thirdparty/libsqlite/include/sqlite3.h, which shows that it was linked
        against the vendored SQLite and not an older prebuilt library;
      - LiveCode-Community.exe contains BUILD_SHORT_VERSION from the version
        file.

    It prints the file sizes and, under GitHub Actions, adds a short report to
    the job summary and writes the step outputs "version", "sqlite-version"
    and "sqlite-source-id".

.PARAMETER RepoRoot
    Repository root. Default: two levels up from this script.

.PARAMETER BinDir
    Folder to check. Default: <RepoRoot>\win-x86_64-bin.

.PARAMETER Arch
    x86_64 or x86: the PE machine every required .exe and .dll must have
    (0x8664 or 0x14c). Default: x86 for a folder named win-x86-bin, else
    x86_64.

.PARAMETER Version
    Expected version string. Default: BUILD_SHORT_VERSION from <RepoRoot>\version.

.PARAMETER SqliteSourceId
    Expected SQLite source id. Default: SQLITE_SOURCE_ID from
    <RepoRoot>\thirdparty\libsqlite\include\sqlite3.h.

.PARAMETER RequiredFiles
    Files (relative to BinDir) that must exist.

.PARAMETER OptionalFiles
    Files (relative to BinDir) that are only reported with a warning when
    missing.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$BinDir,
    [ValidateSet('', 'x86_64', 'x86')]
    [string]$Arch = '',
    [string]$Version,
    [string]$SqliteSourceId,
    [string[]]$RequiredFiles = @(
        'LiveCode-Community.exe',
        'standalone-community.exe',
        'server-community.exe',
        'installer.exe',
        'lc-compile.exe',
        'lc-run.exe',
        'revsecurity.dll',
        'revpdfprinter.dll',
        'revbrowser.dll',
        'revdb.dll',
        'revspeech.dll',
        'revxml.dll',
        'revzip.dll',
        'dbsqlite.dll',
        'dbmysql.dll',
        'dbodbc.dll',
        'dbpostgresql.dll',
        'w32-manifest-template.xml',
        'Externals\CEF\libcef.dll',
        'Externals\CEF\libbrowser-cefprocess.exe',
        'Externals\CEF\revbrowser-cefprocess.exe'
    ),
    [string[]]$OptionalFiles = @(
        'lc-compile-ffi-java.exe',
        'revandroid.dll',
        'server-revdb.dll',
        'server-revxml.dll',
        'server-revzip.dll',
        'LiveCode-Community.pdb',
        'dbsqlite.pdb'
    )
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) { $RepoRoot = Join-Path $PSScriptRoot '..\..' }
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath.TrimEnd('\')
if (-not $BinDir) { $BinDir = Join-Path $RepoRoot 'win-x86_64-bin' }
if (-not (Test-Path -LiteralPath $BinDir -PathType Container)) {
    Write-Host "::error::Build output folder not found: $BinDir"
    exit 1
}
$BinDir = (Resolve-Path -LiteralPath $BinDir).ProviderPath.TrimEnd('\')
if (-not $Arch) {
    if ((Split-Path -Leaf $BinDir) -eq 'win-x86-bin') { $Arch = 'x86' } else { $Arch = 'x86_64' }
}
$expectedMachine = 0x8664
$machineName = 'x86-64 (0x8664)'
if ($Arch -eq 'x86') {
    $expectedMachine = 0x14c
    $machineName = 'x86 (0x14c)'
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
$latin1 = [System.Text.Encoding]::GetEncoding(28591)
$failures = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]

function Add-Failure([string]$Message) {
    $failures.Add($Message)
    Write-Host "::error::$Message"
}

function Add-Warning([string]$Message) {
    $warnings.Add($Message)
    Write-Host "::warning::$Message"
}

function Format-Size([long]$Bytes) {
    return ('{0:N1} MB' -f ($Bytes / 1MB))
}

# Where $Text occurs in a binary file: 'ASCII', 'UTF-16LE' or $null
function Find-TextInBinary([string]$Path, [string]$Text) {
    $haystack = $latin1.GetString([System.IO.File]::ReadAllBytes($Path))
    if ($haystack.IndexOf($Text, [System.StringComparison]::Ordinal) -ge 0) { return 'ASCII' }
    $wide = $latin1.GetString([System.Text.Encoding]::Unicode.GetBytes($Text))
    if ($haystack.IndexOf($wide, [System.StringComparison]::Ordinal) -ge 0) { return 'UTF-16LE' }
    return $null
}

# Read the COFF Machine field from a PE image. Returning $null means that the
# file is truncated, has no DOS/PE signature, or has an invalid PE offset.
function Get-PeMachine([string]$Path) {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
        if ($stream.Length -lt 64) { return $null }
        $reader = New-Object System.IO.BinaryReader($stream)
        try {
            if ($reader.ReadUInt16() -ne 0x5a4d) { return $null } # MZ
            $stream.Position = 0x3c
            $peOffset = $reader.ReadUInt32()
            if ($peOffset -gt ($stream.Length - 6)) { return $null }
            $stream.Position = $peOffset
            if ($reader.ReadUInt32() -ne 0x00004550) { return $null } # PE\0\0
            return $reader.ReadUInt16()
        }
        finally { $reader.Dispose() }
    }
    finally { $stream.Dispose() }
}

# --- Expected values from the source tree ---
if (-not $Version) {
    $versionFile = Join-Path $RepoRoot 'version'
    $m = Select-String -LiteralPath $versionFile -Pattern '^\s*BUILD_SHORT_VERSION\s*=\s*(\S+)\s*$' | Select-Object -First 1
    if (-not $m) { throw "BUILD_SHORT_VERSION not found in $versionFile" }
    $Version = $m.Matches[0].Groups[1].Value
}

$sqliteHeader = Join-Path $RepoRoot 'thirdparty\libsqlite\include\sqlite3.h'
$sqliteVersion = ''
if (Test-Path -LiteralPath $sqliteHeader) {
    $m = Select-String -LiteralPath $sqliteHeader -Pattern '^#define\s+SQLITE_VERSION\s+"([^"]+)"' | Select-Object -First 1
    if ($m) { $sqliteVersion = $m.Matches[0].Groups[1].Value }
}
if (-not $SqliteSourceId) {
    if (-not (Test-Path -LiteralPath $sqliteHeader)) { throw "$sqliteHeader not found; pass -SqliteSourceId" }
    $m = Select-String -LiteralPath $sqliteHeader -Pattern '^#define\s+SQLITE_SOURCE_ID\s+"([^"]+)"' | Select-Object -First 1
    if (-not $m) { throw "SQLITE_SOURCE_ID not found in $sqliteHeader" }
    $SqliteSourceId = $m.Matches[0].Groups[1].Value
}

Write-Host "Build output      : $BinDir"
Write-Host "Expected version  : $Version"
Write-Host "Expected SQLite   : $sqliteVersion ($SqliteSourceId)"
Write-Host ''

# --- Files ---
foreach ($rel in $RequiredFiles) {
    $p = Join-Path $BinDir $rel
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
        Add-Failure "Missing file: $rel"
    }
    elseif ((Get-Item -LiteralPath $p).Length -eq 0) {
        Add-Failure "Empty file: $rel"
    }
    elseif ([System.IO.Path]::GetExtension($rel) -in @('.exe', '.dll')) {
        $machine = Get-PeMachine $p
        if ($machine -ne $expectedMachine) {
            $description = if ($null -eq $machine) { 'not a valid PE image' } else { 'PE machine 0x{0:x4}' -f $machine }
            Add-Failure "$rel is $description; expected $machineName"
        }
    }
}
foreach ($rel in $OptionalFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $BinDir $rel) -PathType Leaf)) {
        Add-Warning "Optional file not found: $rel"
    }
}

$lciDir = Join-Path $BinDir 'modules\lci'
$lciCount = 0
if (Test-Path -LiteralPath $lciDir) { $lciCount = @(Get-ChildItem -LiteralPath $lciDir -Filter '*.lci' -File).Count }
if ($lciCount -eq 0) { Add-Failure 'No module interface files in modules\lci' }

$extDir = Join-Path $BinDir 'packaged_extensions'
$extCount = 0
if (Test-Path -LiteralPath $extDir) { $extCount = @(Get-ChildItem -LiteralPath $extDir -Directory).Count }
if ($extCount -eq 0) { Add-Failure 'No extensions in packaged_extensions' }

# --- SQLite source id in dbsqlite.dll ---
$sqliteFound = $null
$dbsqlite = Join-Path $BinDir 'dbsqlite.dll'
if (Test-Path -LiteralPath $dbsqlite -PathType Leaf) {
    $sqliteFound = Find-TextInBinary $dbsqlite $SqliteSourceId
    if ($sqliteFound) {
        Write-Host "OK: dbsqlite.dll contains SQLITE_SOURCE_ID ($sqliteFound)"
    }
    else {
        Add-Failure "dbsqlite.dll does not contain SQLITE_SOURCE_ID '$SqliteSourceId' from thirdparty/libsqlite (SQLite $sqliteVersion)"
        # Show which SQLite it does contain, e.g. the 3.34.0 prebuilt library
        $text = $latin1.GetString([System.IO.File]::ReadAllBytes($dbsqlite))
        $ids = @([regex]::Matches($text, '\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [0-9a-f]{40,64}') |
                 ForEach-Object { $_.Value } | Select-Object -Unique)
        if ($ids.Count -gt 0) {
            foreach ($id in $ids) { Write-Host "    dbsqlite.dll contains source id: $id" }
        }
        else {
            Write-Host '    No SQLite source id found in dbsqlite.dll'
        }
    }
}

# --- Version string in the IDE engine ---
$versionFound = $null
$engine = Join-Path $BinDir 'LiveCode-Community.exe'
if (Test-Path -LiteralPath $engine -PathType Leaf) {
    $versionFound = Find-TextInBinary $engine $Version
    if ($versionFound) {
        Write-Host "OK: LiveCode-Community.exe contains version '$Version' ($versionFound)"
    }
    else {
        Add-Failure "LiveCode-Community.exe does not contain version '$Version' (BUILD_SHORT_VERSION)"
    }
}

# --- Sizes ---
$allFiles = @(Get-ChildItem -LiteralPath $BinDir -Recurse -File -Force)
$pdbFiles = @($allFiles | Where-Object { $_.Extension -eq '.pdb' })
$totalBytes = [long]0
foreach ($f in $allFiles) { $totalBytes += $f.Length }
$pdbBytes = [long]0
foreach ($f in $pdbFiles) { $pdbBytes += $f.Length }

Write-Host ''
Write-Host 'Files in the build output folder (without *.pdb):'
Get-ChildItem -LiteralPath $BinDir -File -Force |
    Where-Object { $_.Extension -ne '.pdb' } |
    Sort-Object Name |
    ForEach-Object { Write-Host ('  {0,-38} {1,12}' -f $_.Name, (Format-Size $_.Length)) }
foreach ($sub in @(Get-ChildItem -LiteralPath $BinDir -Directory -Force | Sort-Object Name)) {
    $subFiles = @(Get-ChildItem -LiteralPath $sub.FullName -Recurse -File -Force)
    $subBytes = [long]0
    foreach ($f in $subFiles) { $subBytes += $f.Length }
    Write-Host ('  {0,-38} {1,12}  ({2} files)' -f ($sub.Name + '\'), (Format-Size $subBytes), $subFiles.Count)
}
Write-Host ('Total: {0} files, {1}; of which {2} *.pdb files, {3}' -f `
    $allFiles.Count, (Format-Size $totalBytes), $pdbFiles.Count, (Format-Size $pdbBytes))
Write-Host "Module interfaces: $lciCount; packaged extensions: $extCount"

# --- GitHub Actions outputs and job summary ---
if ($env:GITHUB_OUTPUT) {
    [System.IO.File]::AppendAllText($env:GITHUB_OUTPUT,
        "version=$Version`nsqlite-version=$sqliteVersion`nsqlite-source-id=$SqliteSourceId`n", $utf8)
}
if ($env:GITHUB_STEP_SUMMARY) {
    $engineSize = ''
    if (Test-Path -LiteralPath $engine) { $engineSize = Format-Size (Get-Item -LiteralPath $engine).Length }
    $status = if ($failures.Count -eq 0) { 'passed' } else { "FAILED ($($failures.Count) problem(s))" }
    $sqliteCell = if ($sqliteFound) { "found in dbsqlite.dll" } else { 'NOT found in dbsqlite.dll' }
    $versionCell = if ($versionFound) { 'found in LiveCode-Community.exe' } else { 'NOT found in LiveCode-Community.exe' }
    $lines = @(
        '### Build verification',
        '',
        "Result: **$status**",
        '',
        '| Check | Value | Result |',
        '| --- | --- | --- |',
        "| Version (BUILD_SHORT_VERSION) | ``$Version`` | $versionCell |",
        "| SQLite $sqliteVersion source id | ``$SqliteSourceId`` | $sqliteCell |",
        "| LiveCode-Community.exe | $engineSize | |",
        "| Output folder | $($allFiles.Count) files, $(Format-Size $totalBytes) ($(Format-Size $pdbBytes) of *.pdb) | |",
        "| modules\lci / packaged_extensions | $lciCount / $extCount | |",
        ''
    )
    foreach ($f in $failures) { $lines += "- :x: $f" }
    foreach ($w in $warnings) { $lines += "- :warning: $w" }
    $lines += ''
    [System.IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, ($lines -join "`n") + "`n", $utf8)
}

Write-Host ''
if ($failures.Count -gt 0) {
    Write-Host "Verification FAILED with $($failures.Count) problem(s):"
    foreach ($f in $failures) { Write-Host "  - $f" }
    exit 1
}
Write-Host "Verification passed ($($warnings.Count) warning(s))."
exit 0

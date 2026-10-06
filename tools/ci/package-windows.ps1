<#
.SYNOPSIS
    Packages a Windows x86-64 build (win-x86_64-bin) of OpenXTalk-Lite.

.DESCRIPTION
    1. Runs tools/oxt/package.py, which writes the installed program folder
       to <StageParent>\OpenXTalk-Lite-<ver>\ (default <OutDir>\stage\...): the
       IDE from ide\ and ide-support\, the build outputs at their installed
       paths (the development engine as OpenXTalk-Lite.exe), the external assets
       of tools/oxt/external-assets.json, the xTalk Suite extensions of
       tools/oxt/xtalk-extensions.json (fetched from their repositories at
       the pinned commits and built with this build's lc-compile by
       tools/oxt/xtalk_extensions.py) and the licence files. <ver> is the
       product version in ide\.version.

       enetxt.dll and box2dxt.dll import the Visual C++ runtime, which the
       OpenXTalk-Lite engine does not ship. The runtime DLLs are copied next to
       them from Visual Studio's redistributable folder (-VcRedist, else
       the one of VCToolsRedistDir and the Visual Studio installs vswhere
       finds that has the runtime pinned in tools/oxt/xtalk-extensions.json);
       each copy must be the pinned one (-AllowUnpinnedVcRuntime only
       warns). When no such folder is found, packaging warns and those two
       libraries cannot load on a PC without the Visual C++
       Redistributable.

    2. Writes to OutDir (default <RepoRoot>\dist):

      OpenXTalk-Lite-<ver>-win-x86_64-portable.zip
          The staged program folder under one top folder OpenXTalk-Lite-<ver>\.
          Extract it anywhere and run OpenXTalk-Lite.exe.

      OpenXTalk-Lite-<ver>-win-x86_64-binaries.zip
          win-x86_64-bin\ without *.pdb, plus the licence files. Extracting it
          into the root of a source checkout gives the same layout as a build.

      OpenXTalk-Lite-<ver>-win-x86_64-symbols.zip
          The *.pdb files, under win-x86_64-bin\ with their relative paths.

      OpenXTalk-Lite-<ver>-xtalk-sources.zip (only with -XtalkSourcesZip)
          Every file of the xTalk Suite extensions that
          tools/oxt/xtalk-extensions.json pins, in the layout of their
          download cache, with a copy of the manifest
          (tools/oxt/xtalk_extensions.py export). Tag builds attach it to
          the release, so that the release can be rebuilt with
          -XtalkCache <extracted folder> even if a member repository loses
          a pinned commit.

      SHA256SUMS
          "<sha256>  <file name>" for the zips (LF line endings).
          tools/ci/build-installer.ps1 rewrites it when it adds the installer.

    The zips are written straight from the staged folder and the build
    output; nothing else is copied. The installer (build-installer.ps1) is
    built from the same staged folder.

    Under GitHub Actions the step outputs are: version and product-version
    (ide\.version), build-number, engine-version (BUILD_SHORT_VERSION),
    package-root (OpenXTalk-Lite-<ver>), stage-dir (full path of the staged
    program folder), dist-dir and portable-zip.

    Written to run under Windows PowerShell 5.1 and PowerShell 7.

.PARAMETER RepoRoot
    Repository root. Default: two levels up from this script.

.PARAMETER BinDir
    Build output folder. Default: <RepoRoot>\win-x86_64-bin.

.PARAMETER OutDir
    Output folder for the zips. Default: <RepoRoot>\dist. Existing files with
    the same names are replaced; other files are left alone.

.PARAMETER StageParent
    Folder in which package.py creates OpenXTalk-Lite-<ver>\ (an existing folder
    of that name is replaced). Default: <OutDir>\stage.

.PARAMETER BuildNumber
    Build number written into the packaged .buildnumber. Default: the
    environment variable OXT_BUILD_NUMBER, else the current UTC time as
    yyyyMMddHHmm.

.PARAMETER AssetsCache
    Download cache for the external assets. Default: the environment variable
    OXT_ASSETS_CACHE, else <RepoRoot>\prebuilt\fetched-assets.

.PARAMETER NoExternalAssets
    Leave the external assets (other-platform runtimes) out of the package.

.PARAMETER NoXtalkExtensions
    Leave the xTalk Suite extensions out of the package. The environment
    variable NO_XTALK_EXTENSIONS set to 1 or true does the same.

.PARAMETER XtalkCache
    Download cache of the xTalk Suite extensions' files. Default: the
    environment variable OXT_XTALK_CACHE, else <AssetsCache>\xtalk. A
    folder extracted from a release's OpenXTalk-Lite-<ver>-xtalk-sources.zip
    holds every file that release pinned, so nothing is downloaded.

.PARAMETER XtalkSourcesZip
    Also write OpenXTalk-Lite-<ver>-xtalk-sources.zip (see above) to OutDir
    and list it in SHA256SUMS. CI sets it for tag builds.

.PARAMETER VcRedist
    Visual Studio's redistributable folder, ...\VC\Redist\MSVC\<version>
    (the one with x64\Microsoft.VC14x.CRT and x86\Microsoft.VC14x.CRT),
    used as given. Default: of the environment variable VCToolsRedistDir
    (set in a Visual Studio developer prompt) and the redistributable
    folders of every Visual Studio with the C++ tools that vswhere finds
    (newest first, each one's default folder first), the first that has
    the Visual C++ runtime DLLs pinned in tools/oxt/xtalk-extensions.json
    ("vc_runtime"), else the first of them at all (packaging then stops,
    unless -AllowUnpinnedVcRuntime).

.PARAMETER AllowUnpinnedVcRuntime
    Package even when the Visual C++ runtime DLLs are not the ones pinned
    in tools/oxt/xtalk-extensions.json, with a warning (for trying out
    another redistributable before pinning it with "xtalk_extensions.py
    pin-vc-runtime"). Never for a release; CI does not set it.

.PARAMETER Python
    Python 3 interpreter. Default: the first of "py -3", python3 and python
    that is Python 3.8 or later.

.PARAMETER CompressionLevel
    Optimal (default), Fastest or NoCompression.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$BinDir,
    [string]$OutDir,
    [string]$StageParent,
    [string]$BuildNumber,
    [string]$AssetsCache,
    [switch]$NoExternalAssets,
    [switch]$NoXtalkExtensions,
    [string]$XtalkCache,
    [switch]$XtalkSourcesZip,
    [string]$VcRedist,
    [switch]$AllowUnpinnedVcRuntime,
    [string]$Python,
    [ValidateSet('Optimal', 'Fastest', 'NoCompression')]
    [string]$CompressionLevel = 'Optimal'
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# ZipFileExtensions lives in System.IO.Compression.FileSystem on .NET
# Framework (Windows PowerShell) and in System.IO.Compression.ZipFile on .NET
# (PowerShell 7)
foreach ($assembly in @('System.IO.Compression', 'System.IO.Compression.FileSystem', 'System.IO.Compression.ZipFile')) {
    try { Add-Type -AssemblyName $assembly -ErrorAction Stop } catch { }
}
if (-not ('System.IO.Compression.ZipFileExtensions' -as [type])) {
    throw 'System.IO.Compression.ZipFileExtensions is not available in this PowerShell.'
}

$Platform = 'win-x86_64'
$BinName = "$Platform-bin"
$Product = 'OpenXTalk-Lite'
$ExeName = "$Product.exe"
$LicenseFiles = @('LICENSE')

$utf8 = New-Object System.Text.UTF8Encoding($false)

# --- Arguments ---
if (-not $RepoRoot) { $RepoRoot = Join-Path $PSScriptRoot '..\..' }
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath.TrimEnd('\')
if (-not $BinDir) { $BinDir = Join-Path $RepoRoot $BinName }
if (-not (Test-Path -LiteralPath (Join-Path $BinDir 'LiveCode-Community.exe'))) {
    throw "No build found: $BinDir\LiveCode-Community.exe does not exist."
}
$BinDir = (Resolve-Path -LiteralPath $BinDir).ProviderPath.TrimEnd('\')
if (-not $OutDir) { $OutDir = Join-Path $RepoRoot 'dist' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path -LiteralPath $OutDir).ProviderPath.TrimEnd('\')
if (-not $StageParent) { $StageParent = Join-Path $OutDir 'stage' }
New-Item -ItemType Directory -Force -Path $StageParent | Out-Null
$StageParent = (Resolve-Path -LiteralPath $StageParent).ProviderPath.TrimEnd('\')

if (-not $BuildNumber) { $BuildNumber = $env:OXT_BUILD_NUMBER }
if (-not $BuildNumber) { $BuildNumber = [DateTime]::UtcNow.ToString('yyyyMMddHHmm') }
if ($BuildNumber -notmatch '^[0-9]{1,20}$') { throw "Build number '$BuildNumber' must be digits only." }

# Run a native program with the given arguments, show its output (stdout and
# stderr, through Write-Host so that a caller's redirection captures it) and
# return its exit code. Windows PowerShell turns redirected stderr lines into
# error records, which 'Stop' would make fatal, so relax it for the call.
function Invoke-Native([string]$FilePath, [string[]]$Arguments) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $global:LASTEXITCODE = 0
        & $FilePath @Arguments 2>&1 | ForEach-Object { Write-Host "$_" }
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $saved
    }
}

# --- Python 3 ---
function Find-Python {
    $candidates = @()
    if ($Python) { $candidates += , @($Python) }
    else {
        if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @('py', '-3') }
        foreach ($name in @('python3', 'python')) {
            $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($cmd) { $candidates += , @($cmd.Source) }
        }
    }
    foreach ($c in $candidates) {
        $exe = $c[0]
        $pre = @($c | Select-Object -Skip 1)
        $saved = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            # No double quotes in the argument: Windows PowerShell does not
            # escape them for native programs
            $v = & $exe @pre -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
            $code = $LASTEXITCODE
        }
        catch { $code = 1 }
        finally { $ErrorActionPreference = $saved }
        if ($code -eq 0 -and "$v".Trim() -match '^3 (\d+)$' -and [int]$Matches[1] -ge 8) {
            return New-Object PSObject -Property @{ Exe = $exe; Pre = $pre; Version = "3.$($Matches[1])" }
        }
    }
    throw 'Python 3.8 or later was not found (tried py -3, python3 and python); pass -Python.'
}
$py = Find-Python

# Run a Python script (the arguments after the interpreter) and return its
# exit code. Unbuffered, so that progress lines appear as they are written;
# UTF-8, so that printing a path never fails on a legacy code page.
function Invoke-Python([string[]]$Arguments) {
    $savedUnbuffered = $env:PYTHONUNBUFFERED
    $savedEncoding = $env:PYTHONIOENCODING
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    try { return Invoke-Native $py.Exe (@($py.Pre) + @($Arguments)) }
    finally {
        $env:PYTHONUNBUFFERED = $savedUnbuffered
        $env:PYTHONIOENCODING = $savedEncoding
    }
}

# --- Visual C++ redistributable (for the xTalk extensions) ---
# A folder qualifies when it has x64\ and x86\Microsoft.VC14x.CRT
function Test-VcRedist([string]$Dir) {
    if (-not $Dir -or -not (Test-Path -LiteralPath $Dir -PathType Container)) { return $false }
    foreach ($arch in @('x64', 'x86')) {
        $crt = @(Get-ChildItem -LiteralPath (Join-Path $Dir $arch) -Directory -Filter 'Microsoft.VC14*.CRT' -ErrorAction SilentlyContinue)
        if ($crt.Count -eq 0) { return $false }
    }
    return $true
}

# The Visual C++ runtime DLLs pinned in the xTalk manifest ("vc_runtime"),
# as objects with Arch (x64 or x86), Name and Sha256
function Get-VcRuntimePins {
    $manifest = Join-Path $RepoRoot 'tools\oxt\xtalk-extensions.json'
    $pins = @()
    try { $vc = ([System.IO.File]::ReadAllText($manifest, $utf8) | ConvertFrom-Json).vc_runtime }
    catch { return $pins }
    if (-not $vc -or -not $vc.files) { return $pins }
    $arches = @{ 'x86_64-win32' = 'x64'; 'x86-win32' = 'x86' }
    foreach ($platform in $vc.files.PSObject.Properties) {
        if (-not $arches.ContainsKey($platform.Name)) { continue }
        foreach ($dll in $platform.Value.PSObject.Properties) {
            $pins += New-Object PSObject -Property @{ Arch = $arches[$platform.Name]; Name = $dll.Name; Sha256 = [string]$dll.Value.sha256 }
        }
    }
    return $pins
}

# True when every pinned DLL is in the folder's Microsoft.VC14x.CRT
# folders (the last one by name, as xtalk_extensions.py takes it) with the
# pinned SHA-256
function Test-VcRedistPinned([string]$Dir, $Pins) {
    if (@($Pins).Count -eq 0) { return $false }
    foreach ($pin in $Pins) {
        $crt = @(Get-ChildItem -LiteralPath (Join-Path $Dir $pin.Arch) -Directory -Filter 'Microsoft.VC1*.CRT' -ErrorAction SilentlyContinue |
            Sort-Object Name | Select-Object -Last 1)
        if ($crt.Count -eq 0) { return $false }
        $file = Join-Path $crt[0].FullName $pin.Name
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $false }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pin.Sha256) { return $false }
    }
    return $true
}

# The redistributable folder to bundle the runtime from. -VcRedist is used
# as given. Otherwise every candidate is collected (VCToolsRedistDir, then
# for each Visual Studio vswhere finds, newest first, its default
# redistributable and its other version folders, newest first), and the
# first that has the pinned runtime wins: a machine with several Visual
# Studio installs then packages the pinned runtime rather than the newest,
# and xtalk_extensions.py's mismatch error appears only when the pinned
# runtime is really absent. Without a match, the first valid candidate.
function Find-VcRedist {
    if ($VcRedist) {
        if (-not (Test-VcRedist $VcRedist)) { throw "-VcRedist $VcRedist has no x64\Microsoft.VC14x.CRT and x86\Microsoft.VC14x.CRT folders." }
        return (Resolve-Path -LiteralPath $VcRedist).ProviderPath.TrimEnd('\')
    }
    $candidates = @()
    if ($env:VCToolsRedistDir) { $candidates += $env:VCToolsRedistDir }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere -PathType Leaf) {
        $saved = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            # Newest first; -products * includes the Build Tools
            $installs = @(& $vswhere -all -sort -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null)
        }
        finally { $ErrorActionPreference = $saved }
        foreach ($vs in $installs) {
            $vs = "$vs".Trim()
            if (-not $vs) { continue }
            $root = Join-Path $vs 'VC\Redist\MSVC'
            # The redistributable that matches the default toolset, then any
            # other version folder, newest first
            $versionFile = Join-Path $vs 'VC\Auxiliary\Build\Microsoft.VCRedistVersion.default.txt'
            if (Test-Path -LiteralPath $versionFile -PathType Leaf) {
                $candidates += Join-Path $root ([System.IO.File]::ReadAllText($versionFile).Trim())
            }
            if (Test-Path -LiteralPath $root -PathType Container) {
                $candidates += @(Get-ChildItem -LiteralPath $root -Directory | Where-Object { $_.Name -match '^\d+\.\d+\.\d+$' } |
                    Sort-Object { [version]$_.Name } -Descending | ForEach-Object { $_.FullName })
            }
        }
    }
    $valid = @($candidates | Where-Object { Test-VcRedist $_ } | ForEach-Object { $_.TrimEnd('\') })
    $pins = Get-VcRuntimePins
    foreach ($c in $valid) {
        if (Test-VcRedistPinned $c $pins) { return $c }
    }
    if ($valid.Count -gt 0) { return $valid[0] }
    return $null
}

if ($env:NO_XTALK_EXTENSIONS -match '^(1|true|yes)$') { $NoXtalkExtensions = $true }
if ($XtalkSourcesZip -and $NoXtalkExtensions) {
    throw '-XtalkSourcesZip needs the xTalk Suite extensions; it cannot be combined with -NoXtalkExtensions or NO_XTALK_EXTENSIONS.'
}
$vcRedistDir = $null
$vcRedistPinned = $false
if (-not $NoXtalkExtensions) {
    $vcRedistDir = Find-VcRedist
    if (-not $vcRedistDir) {
        $message = 'No Visual C++ redistributable folder found (-VcRedist, VCToolsRedistDir or vswhere): enetxt and box2dxt are packaged WITHOUT the Visual C++ runtime DLLs they import and cannot load on a PC without the Visual C++ Redistributable.'
        Write-Warning $message
        if ($env:GITHUB_ACTIONS) { Write-Host "::warning title=Package::$message" }
    }
    else { $vcRedistPinned = Test-VcRedistPinned $vcRedistDir (Get-VcRuntimePins) }
}

Write-Host "Repository : $RepoRoot"
Write-Host "Build      : $BinDir"
Write-Host "Stage in   : $StageParent"
Write-Host "Output     : $OutDir"
Write-Host "Build no.  : $BuildNumber"
Write-Host "Python     : $((@($py.Exe) + @($py.Pre)) -join ' ') ($($py.Version))"
if ($NoXtalkExtensions) { Write-Host 'xTalk ext. : left out' }
elseif (-not $vcRedistDir) { Write-Host 'VC++ redist: (none found)' }
elseif ($vcRedistPinned) { Write-Host "VC++ redist: $vcRedistDir (has the pinned runtime)" }
else { Write-Host "VC++ redist: $vcRedistDir (NOT the pinned runtime$(if ($AllowUnpinnedVcRuntime) { '; -AllowUnpinnedVcRuntime' } else { '; packaging will stop' }))" }
Write-Host ''

# --- 1. Stage the installed layout ---
$summaryFile = [System.IO.Path]::GetTempFileName()
try {
    $pyArgs = @(
        (Join-Path $RepoRoot 'tools\oxt\package.py'),
        '--repo', $RepoRoot,
        '--bin', $BinDir,
        '--out', $StageParent,
        '--build-number', $BuildNumber,
        '--summary-json', $summaryFile)
    if ($AssetsCache) { $pyArgs += @('--assets-cache', $AssetsCache.TrimEnd('\')) }
    if ($NoExternalAssets) { $pyArgs += '--no-external-assets' }
    if ($NoXtalkExtensions) { $pyArgs += '--no-xtalk-extensions' }
    else {
        if ($XtalkCache) { $pyArgs += @('--xtalk-cache', $XtalkCache.TrimEnd('\')) }
        if ($vcRedistDir) { $pyArgs += @('--vc-redist', $vcRedistDir) }
        if ($AllowUnpinnedVcRuntime) { $pyArgs += '--allow-unpinned-vc-runtime' }
    }
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $code = Invoke-Python $pyArgs
    if ($code -ne 0) { throw "tools/oxt/package.py failed with exit code $code" }
    Write-Host ("Staged in {0:N0} s" -f $timer.Elapsed.TotalSeconds)
    $summary = [System.IO.File]::ReadAllText($summaryFile, $utf8) | ConvertFrom-Json
}
finally {
    Remove-Item -LiteralPath $summaryFile -Force -ErrorAction SilentlyContinue
}

$Version = [string]$summary.version
$PackageRoot = [string]$summary.package_root
$StageDir = [string]$summary.stage_dir
$EngineVersion = [string]$summary.engine_version
if ($Version -notmatch '^[A-Za-z0-9][A-Za-z0-9._+-]*$') { throw "Version '$Version' is not usable in a file name." }
if ($PackageRoot -ne "$Product-$Version") { throw "Unexpected package folder name '$PackageRoot'." }
if (-not (Test-Path -LiteralPath (Join-Path $StageDir $ExeName) -PathType Leaf)) {
    throw "The staged folder has no $ExeName ($StageDir)."
}

# Tom Perry's release has his icons and version information in its
# OpenXTalk-Lite.exe, which he wrote in after the build; the staged engine
# gets the same resources, from Installer\openxtalk-lite\from-tom-release
# (tools/oxt/win_resources.py, which checks the result)
$code = Invoke-Python @((Join-Path $RepoRoot 'tools\oxt\win_resources.py'), 'apply', (Join-Path $StageDir $ExeName))
if ($code -ne 0) { throw "tools/oxt/win_resources.py failed with exit code $code" }

$PortableZipName = "$PackageRoot-$Platform-portable.zip"
$BinZipName = "$PackageRoot-$Platform-binaries.zip"
$SymZipName = "$PackageRoot-$Platform-symbols.zip"

# --- Helpers ---

function New-Entry([string]$Source, [string]$Name) {
    return New-Object PSObject -Property @{ Source = $Source; Name = $Name }
}

# Files under a folder, as paths relative to it with '/' separators
function Get-RelativeFiles([string]$Dir) {
    $prefixLength = $Dir.TrimEnd('\').Length + 1
    foreach ($f in @(Get-ChildItem -LiteralPath $Dir -Recurse -File -Force)) {
        $f.FullName.Substring($prefixLength).Replace('\', '/')
    }
}

# Empty folders under a folder, as relative paths with '/' separators
function Get-EmptyFolders([string]$Dir) {
    $prefixLength = $Dir.TrimEnd('\').Length + 1
    foreach ($d in @(Get-ChildItem -LiteralPath $Dir -Recurse -Directory -Force)) {
        if (@(Get-ChildItem -LiteralPath $d.FullName -Force).Count -eq 0) {
            $d.FullName.Substring($prefixLength).Replace('\', '/')
        }
    }
}

# Write a zip from a list of entries (Source file -> Name inside the zip)
# and optional folder entries (names ending in '/')
function Write-Zip([string]$Path, $Entries, [string[]]$Folders) {
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force }
    $level = [System.IO.Compression.CompressionLevel]::$CompressionLevel
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew)
    try {
        $zip = New-Object System.IO.Compression.ZipArchive($stream, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($e in $Entries) {
                [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $e.Source, $e.Name, $level)
            }
            foreach ($name in @($Folders | Where-Object { $_ })) {
                [void]$zip.CreateEntry($name)
            }
        }
        finally {
            $zip.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

# --- 2. Collect the entries ---
$stageFiles = @(Get-RelativeFiles $StageDir | Sort-Object)
$portableEntries = New-Object System.Collections.Generic.List[object]
foreach ($rel in $stageFiles) {
    if ($rel -like '*.pdb') { throw "A *.pdb file was staged: $rel" }
    $portableEntries.Add((New-Entry (Join-Path $StageDir ($rel.Replace('/', '\'))) "$PackageRoot/$rel"))
}
$portableFolders = @(Get-EmptyFolders $StageDir | Sort-Object | ForEach-Object { "$PackageRoot/$_/" })
Write-Host "Staged folder: $($stageFiles.Count) files, $($portableFolders.Count) empty folders"

$binFiles = @(Get-RelativeFiles $BinDir | Sort-Object)
$binNoPdb = New-Object System.Collections.Generic.List[object]
$binPdb = New-Object System.Collections.Generic.List[object]
foreach ($rel in $binFiles) {
    $src = Join-Path $BinDir ($rel.Replace('/', '\'))
    if ($rel -like '*.pdb') { $binPdb.Add((New-Entry $src "$BinName/$rel")) }
    else { $binNoPdb.Add((New-Entry $src "$BinName/$rel")) }
}
Write-Host "Build output: $($binNoPdb.Count) files, plus $($binPdb.Count) *.pdb files"
if ($binPdb.Count -eq 0) { Write-Warning "No *.pdb files in $BinDir; the symbols zip will be empty." }

# The licence files as staged (package.py gives them CRLF line endings)
$licenseEntries = New-Object System.Collections.Generic.List[object]
foreach ($name in $LicenseFiles) {
    $p = Join-Path $StageDir $name
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "$name is missing from the staged folder." }
    $licenseEntries.Add((New-Entry $p $name))
}

# --- 3. Write the zips ---
$portablePath = Join-Path $OutDir $PortableZipName
$binPath = Join-Path $OutDir $BinZipName
$symPath = Join-Path $OutDir $SymZipName
$sumsPath = Join-Path $OutDir 'SHA256SUMS'
if (Test-Path -LiteralPath $sumsPath) { Remove-Item -LiteralPath $sumsPath -Force }

$timer = [System.Diagnostics.Stopwatch]::StartNew()
Write-Host "Writing $PortableZipName ..."
Write-Zip $portablePath $portableEntries $portableFolders

Write-Host "Writing $BinZipName ..."
$binZipEntries = New-Object System.Collections.Generic.List[object]
foreach ($e in $binNoPdb) { $binZipEntries.Add($e) }
foreach ($e in $licenseEntries) { $binZipEntries.Add($e) }
Write-Zip $binPath $binZipEntries @()

Write-Host "Writing $SymZipName ..."
Write-Zip $symPath $binPdb @()

# The pinned xTalk files, exactly as package.py fetched and verified them
# (from the same cache; --offline, so nothing is downloaded again)
$zipPaths = @($binPath, $portablePath, $symPath)
if ($XtalkSourcesZip) {
    $sourcesPath = Join-Path $OutDir "$PackageRoot-xtalk-sources.zip"
    Write-Host "Writing $(Split-Path -Leaf $sourcesPath) ..."
    $exportArgs = @((Join-Path $RepoRoot 'tools\oxt\xtalk_extensions.py'), '--repo', $RepoRoot,
        'export', '--offline', '--out', $sourcesPath)
    if ($XtalkCache) { $exportArgs += @('--cache', $XtalkCache.TrimEnd('\')) }
    elseif (-not $env:OXT_XTALK_CACHE -and $AssetsCache) { $exportArgs += @('--cache', (Join-Path $AssetsCache.TrimEnd('\') 'xtalk')) }
    $code = Invoke-Python $exportArgs
    if ($code -ne 0) { throw "tools/oxt/xtalk_extensions.py export failed with exit code $code" }
    $zipPaths += $sourcesPath
}
Write-Host ("Zips written in {0:N0} s" -f $timer.Elapsed.TotalSeconds)

# --- 4. Checksums ---
$results = @()
$sumLines = @()
foreach ($p in $zipPaths) {
    $item = Get-Item -LiteralPath $p
    $hash = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()
    $sumLines += "$hash  $($item.Name)"
    $count = 0
    $archive = [System.IO.Compression.ZipFile]::OpenRead($p)
    try { $count = $archive.Entries.Count } finally { $archive.Dispose() }
    $results += New-Object PSObject -Property @{ Name = $item.Name; Bytes = $item.Length; Entries = $count; Sha256 = $hash }
}
[System.IO.File]::WriteAllText($sumsPath, (($sumLines -join "`n") + "`n"), $utf8)

$stageBytes = [int64]$summary.bytes
$xtalkCount = @($summary.xtalk_extensions).Count
$vcRuntimeVersion = if ($summary.vc_redist_version) { [string]$summary.vc_redist_version } else { '' }
$missingRuntime = @($summary.xtalk_missing_runtime | Where-Object { $_ })
$staleRuntime = @($summary.vc_runtime_older_than_build_tools | Where-Object { $_ })
$runtimeFileVersions = @($summary.vc_runtime_files | Where-Object { $_ } | ForEach-Object { [string]$_.file_version } | Sort-Object -Unique)
if ($NoXtalkExtensions) { $xtalkText = 'left out' }
elseif ($missingRuntime.Count -gt 0) { $xtalkText = "$xtalkCount, WITHOUT the Visual C++ runtime that $($missingRuntime.Count) of their libraries need" }
elseif ($vcRuntimeVersion) { $xtalkText = "$xtalkCount, with the Visual C++ runtime $($runtimeFileVersions -join ', ') (redistributable folder $vcRuntimeVersion)" }
else { $xtalkText = "$xtalkCount" }
if ($summary.vc_runtime_pinned -eq $false) {
    $xtalkText += ", NOT the pinned runtime ($([string]$summary.vc_runtime_pinned_file_version))"
    $message = "The Visual C++ runtime DLLs bundled with the xTalk extensions are NOT the ones pinned in tools/oxt/xtalk-extensions.json (file version $([string]$summary.vc_runtime_pinned_file_version)); packaged with -AllowUnpinnedVcRuntime."
    Write-Warning $message
    if ($env:GITHUB_ACTIONS) { Write-Host "::warning title=Package::$message" }
}
# Libraries built by a newer MSVC toolset than the runtime bundled for
# them: Microsoft does not support that combination, though it loads
foreach ($s in $staleRuntime) {
    $older = @($s.runtime | ForEach-Object { "$($_.dll) $($_.file_version)" }) -join ', '
    $message = "Extensions/$($s.library) was built with MSVC $($s.linker), but the Visual C++ runtime bundled for it is older ($older); Microsoft supports only a runtime at least as new as the newest toolset used."
    Write-Warning $message
    if ($env:GITHUB_ACTIONS) { Write-Host "::warning title=Package::$message" }
}
Write-Host ''
Write-Host ("Staged folder {0}: {1:N0} files, {2:N1} MB" -f $StageDir, [int]$summary.files, ($stageBytes / 1MB))
Write-Host "xTalk Suite extensions: $xtalkText"
Write-Host "Packages in ${OutDir}:"
foreach ($r in $results) {
    Write-Host ('  {0,-52} {1,10:N1} MB  {2,6} entries' -f $r.Name, ($r.Bytes / 1MB), $r.Entries)
}
Write-Host '  SHA256SUMS'
foreach ($line in $sumLines) { Write-Host "    $line" }

# --- GitHub Actions outputs and job summary ---
if ($env:GITHUB_OUTPUT) {
    $outputs = @(
        "version=$Version",
        "product-version=$Version",
        "build-number=$([string]$summary.build_number)",
        "engine-version=$EngineVersion",
        "package-root=$PackageRoot",
        "stage-dir=$StageDir",
        "dist-dir=$OutDir",
        "portable-zip=$portablePath",
        "xtalk-extensions=$xtalkCount",
        "vc-runtime-version=$vcRuntimeVersion"
    )
    [System.IO.File]::AppendAllText($env:GITHUB_OUTPUT, (($outputs -join "`n") + "`n"), $utf8)
}
if ($env:GITHUB_STEP_SUMMARY) {
    $md = @('### Packages', '',
        ("{0} {1}, build {2} (engine {3}); staged folder: {4:N0} files, {5:N1} MB" -f $Product, $Version, $summary.build_number, $EngineVersion, [int]$summary.files, ($stageBytes / 1MB)),
        '', "xTalk Suite extensions: $xtalkText.",
        '', '| File | Size | Entries | SHA-256 |', '| --- | --- | --- | --- |')
    foreach ($r in $results) {
        $md += ('| {0} | {1:N1} MB | {2} | `{3}` |' -f $r.Name, ($r.Bytes / 1MB), $r.Entries, $r.Sha256)
    }
    $md += ''
    [System.IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, ($md -join "`n") + "`n", $utf8)
}

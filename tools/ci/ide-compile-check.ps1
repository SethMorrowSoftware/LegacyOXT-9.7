<#
.SYNOPSIS
    Compiles every IDE script of an installed LiveCode layout and compares
    the compile errors with a baseline of known errors.

.DESCRIPTION
    Runs tools/ci/ide-compile-check.livecodescript with the development
    engine, without a user interface (-ui), over an installed layout such as
    dist/stage/LegacyOXT-<ver> written by tools/oxt/package.py. The script
    compiles every script-only stack (*.livecodescript, *.oxtscript) under
    Toolset, Plugins and Extensions and every object script of the binary
    stacks (*.livecode, *.rev, *.oxtstack) under Toolset and Plugins. Stacks
    are only loaded, with messages locked; they are never opened or saved.

    Each error is one line:

        <file> | <object> | line <n> | <message>

    Errors listed in the baseline (tools/ci/ide-compile-baseline.txt: one
    record per line; blank lines and lines starting with # are ignored) are
    known, pre-existing errors. The check fails (exit code 1) when there is
    an error that is not in the baseline, or when the engine did not finish.
    Baseline entries that no longer occur are reported, as warnings, so the
    baseline can be updated; they do not fail the check.

    With -UpdateBaseline the current errors are written to the baseline file
    instead, and the script exits with 0 when the engine finished.

    Under GitHub Actions it adds annotations and a short report to the job
    summary, and writes the step outputs "scripts", "errors", "new-errors"
    and "fixed".

    Written to run under Windows PowerShell 5.1 and PowerShell 7.

.PARAMETER Root
    The installed layout to check (the folder with Toolset, Plugins and
    Extensions). Default: the single LegacyOXT-* folder in
    <RepoRoot>\dist\stage.

.PARAMETER Engine
    The development engine to run. Default: <Root>\LiveCode Community.exe, or
    <RepoRoot>\win-x86_64-bin\LiveCode-Community.exe when the layout has no
    engine (for example one written by "layout.py assemble").

.PARAMETER RepoRoot
    Repository root. Default: two levels up from this script.

.PARAMETER Baseline
    Baseline file. Default: tools\ci\ide-compile-baseline.txt next to this
    script.

.PARAMETER UpdateBaseline
    Write the errors found to the baseline file instead of comparing.

.PARAMETER BaselineSource
    With -UpdateBaseline: a short description of the checked layout for the
    baseline's header, for example "LiveCode Community 9.7.0-dp-1 IDE".

.PARAMETER LogFile
    Optional file to write the engine's output and the comparison to.

.PARAMETER TimeoutSeconds
    How long to wait for the engine. Default: 900.
#>
[CmdletBinding()]
param(
    [string]$Root,
    [string]$Engine,
    [string]$RepoRoot,
    [string]$Baseline,
    [switch]$UpdateBaseline,
    [string]$BaselineSource,
    [string]$LogFile,
    [ValidateRange(30, 7200)]
    [int]$TimeoutSeconds = 900
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$utf8 = New-Object System.Text.UTF8Encoding($false)
$checkScript = Join-Path $PSScriptRoot 'ide-compile-check.livecodescript'
if (-not $RepoRoot) { $RepoRoot = Join-Path $PSScriptRoot '..\..' }
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).ProviderPath.TrimEnd('\')
if (-not $Baseline) { $Baseline = Join-Path $PSScriptRoot 'ide-compile-baseline.txt' }

# --- Layout and engine ---
if (-not $Root) {
    $stage = Join-Path $RepoRoot 'dist\stage'
    $candidates = @()
    if (Test-Path -LiteralPath $stage -PathType Container) {
        $candidates = @(Get-ChildItem -LiteralPath $stage -Directory -Filter 'LegacyOXT-*')
    }
    if ($candidates.Count -ne 1) { throw "Pass -Root: expected one LegacyOXT-* folder in $stage, found $($candidates.Count)" }
    $Root = $candidates[0].FullName
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'Toolset') -PathType Container)) {
    throw "Not an installed layout (no Toolset folder): $Root"
}
$Root = (Resolve-Path -LiteralPath $Root).ProviderPath.TrimEnd('\')

if (-not $Engine) {
    $Engine = Join-Path $Root 'LiveCode Community.exe'
    if (-not (Test-Path -LiteralPath $Engine -PathType Leaf)) {
        $Engine = Join-Path $RepoRoot 'win-x86_64-bin\LiveCode-Community.exe'
    }
}
if (-not (Test-Path -LiteralPath $Engine -PathType Leaf)) { throw "Engine not found: $Engine" }
$Engine = (Resolve-Path -LiteralPath $Engine).ProviderPath

Write-Host "Layout   : $Root"
Write-Host "Engine   : $Engine"
Write-Host "Checker  : $checkScript"
Write-Host "Baseline : $Baseline$(if ($UpdateBaseline) { ' (will be rewritten)' })"
Write-Host ''

# Reads a text file as UTF-8 lines without line endings
function Read-Lines([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }
    $text = [System.IO.File]::ReadAllText($Path, $utf8)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    return @($text -split "`r?`n")
}

# --- Run the engine without a user interface ---
# The engine is a GUI-subsystem program, so start it with redirected output
# and wait for it explicitly; PowerShell would not wait for it otherwise.
# It runs in an empty temporary folder so that nothing is written into the
# layout or the repository.
$base = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$workDir = Join-Path $base ('oxt-compile-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
$outFile = Join-Path $workDir 'stdout.txt'
$errFile = Join-Path $workDir 'stderr.txt'
$recordFile = Join-Path $workDir 'errors.txt'

$savedRoot = $env:OXT_CHECK_ROOT
$savedOut = $env:OXT_CHECK_OUT
$env:OXT_CHECK_ROOT = $Root
$env:OXT_CHECK_OUT = $recordFile

$output = @()
$stderr = @()
$summary = $null
$exitCode = $null
$timedOut = $false
$timer = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $process = Start-Process -FilePath $Engine -ArgumentList @('-ui', ('"{0}"' -f $checkScript)) `
        -WorkingDirectory $workDir -RedirectStandardOutput $outFile -RedirectStandardError $errFile `
        -NoNewWindow -PassThru
    # Read the handle now: without it, ExitCode is empty after the process
    # has exited (a known Start-Process -PassThru quirk).
    $null = $process.Handle
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $timedOut = $true
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        $null = $process.WaitForExit(30000)
    }
    else {
        $process.WaitForExit()
        $exitCode = $process.ExitCode
    }
    $output = @(Read-Lines $outFile | Where-Object { $_ -ne '' })
    $stderr = @(Read-Lines $errFile | Where-Object { $_ -ne '' })
}
finally {
    $env:OXT_CHECK_ROOT = $savedRoot
    $env:OXT_CHECK_OUT = $savedOut
}
$elapsed = $timer.Elapsed.TotalSeconds

# The FILE lines are only useful to see where a run stopped
$fileLines = @($output | Where-Object { $_ -like 'FILE *' })
$output | Where-Object { $_ -notlike 'FILE *' } | ForEach-Object { Write-Host $_ }
if ($stderr.Count -gt 0) {
    Write-Host ''
    Write-Host 'Engine stderr:'
    $stderr | ForEach-Object { Write-Host "  $_" }
}

$summaryLine = $output | Where-Object { $_ -match '^SUMMARY ' } | Select-Object -Last 1
$counts = @{}
if ($summaryLine) {
    foreach ($m in [regex]::Matches($summaryLine, '(\w+)=([0-9.]+)')) { $counts[$m.Groups[1].Value] = $m.Groups[2].Value }
}
$completed = (-not $timedOut) -and ($exitCode -eq 0) -and $summaryLine -and (Test-Path -LiteralPath $recordFile -PathType Leaf)
$problem = $null
if ($timedOut) {
    $last = if ($fileLines.Count -gt 0) { $fileLines[-1].Substring(5) } else { '(none)' }
    $problem = "The engine did not finish within $TimeoutSeconds seconds (last file started: $last)"
}
elseif (-not $completed) {
    $problem = "The compile check did not complete (exit code $exitCode$(if (-not $summaryLine) { ', no SUMMARY line' }))"
}

$current = @()
if ($completed) { $current = @(Read-Lines $recordFile | ForEach-Object { $_.TrimEnd() } | Where-Object { $_ -ne '' }) }

# --- Compare with the baseline, or rewrite it ---
$newErrors = New-Object System.Collections.Generic.List[string]
$fixed = New-Object System.Collections.Generic.List[string]
$known = 0
if ($completed -and $UpdateBaseline) {
    $source = if ($BaselineSource) { $BaselineSource } else { 'the layout in ' + (Split-Path -Leaf $Root) }
    $engineVersion = ''
    $info = $output | Where-Object { $_ -match '^INFO version=(\S+) build=(\S+)' } | Select-Object -First 1
    if ($info -and $info -match '^INFO version=(\S+) build=(\S+)') { $engineVersion = "$($Matches[1]) (build $($Matches[2]))" }
    $header = @(
        '# Known compile errors in the IDE scripts, one per line:',
        '#   <file> | <object> | line <n> | <message>',
        '# tools/ci/ide-compile-check.ps1 fails only on errors that are not listed',
        '# here and reports listed errors that no longer occur. Regenerate with',
        '#   tools/ci/ide-compile-check.ps1 -Root <installed layout> -UpdateBaseline',
        "# Generated from $source",
        "# with engine ${engineVersion}: $($counts['files']) files, $($counts['scripts']) scripts, $($current.Count) error(s)."
    )
    [System.IO.File]::WriteAllText($Baseline, ((@($header) + @($current)) -join "`n") + "`n", $utf8)
    Write-Host ''
    Write-Host "Wrote $($current.Count) error(s) to $Baseline"
}
elseif ($completed) {
    if (-not (Test-Path -LiteralPath $Baseline -PathType Leaf)) { throw "Baseline not found: $Baseline" }
    $expected = @(Read-Lines $Baseline | ForEach-Object { $_.TrimEnd() } | Where-Object { $_ -ne '' -and -not $_.StartsWith('#') })
    # Errors that occur only on Windows are listed in
    # ide-compile-baseline-windows.txt next to the shared baseline, as
    # run_livecode_check.py reads ide-compile-baseline-<family>.txt on Linux
    # and macOS
    $supplement = [System.IO.Path]::Combine([System.IO.Path]::GetDirectoryName($Baseline),
        [System.IO.Path]::GetFileNameWithoutExtension($Baseline) + '-windows' + [System.IO.Path]::GetExtension($Baseline))
    if (Test-Path -LiteralPath $supplement -PathType Leaf) {
        $extra = @(Read-Lines $supplement | ForEach-Object { $_.TrimEnd() } | Where-Object { $_ -ne '' -and -not $_.StartsWith('#') })
        Write-Host "Platform baseline: $supplement ($($extra.Count) entries)"
        $expected = @($expected) + @($extra)
    }
    # Compare as multisets with ordinal (case-sensitive) matching
    $remaining = New-Object 'System.Collections.Generic.Dictionary[string,int]' ([System.StringComparer]::Ordinal)
    foreach ($e in $expected) {
        if ($remaining.ContainsKey($e)) { $remaining[$e]++ } else { $remaining[$e] = 1 }
    }
    foreach ($c in $current) {
        if ($remaining.ContainsKey($c) -and $remaining[$c] -gt 0) {
            $remaining[$c]--
            $known++
        }
        else {
            $newErrors.Add($c)
        }
    }
    foreach ($e in $expected) {
        if ($remaining[$e] -gt 0) {
            $remaining[$e]--
            $fixed.Add($e)
        }
    }

    Write-Host ''
    Write-Host ("Compared with the baseline: {0} known, {1} new, {2} no longer occurring" -f $known, $newErrors.Count, $fixed.Count)
    if ($newErrors.Count -gt 0) {
        Write-Host ''
        Write-Host 'New compile errors (not in the baseline):'
        $n = 0
        foreach ($e in $newErrors) {
            Write-Host "  $e"
            # Annotations are limited per step; the full list is in the log
            if ($env:GITHUB_ACTIONS -eq 'true' -and $n -lt 20) { Write-Host "::error title=IDE compile check::$e" }
            $n++
        }
    }
    if ($fixed.Count -gt 0) {
        Write-Host ''
        Write-Host 'Baseline entries that no longer occur (remove them from the baseline):'
        $n = 0
        foreach ($e in $fixed) {
            Write-Host "  $e"
            if ($env:GITHUB_ACTIONS -eq 'true' -and $n -lt 10) { Write-Host "::warning title=IDE compile check::No longer occurs: $e" }
            $n++
        }
    }
}

$passed = $completed -and ($UpdateBaseline -or $newErrors.Count -eq 0)
$result = if ($problem) { $problem }
          elseif ($UpdateBaseline) { "baseline rewritten with $($current.Count) error(s)" }
          elseif ($passed) { "passed: $($current.Count) error(s), all in the baseline" }
          else { "FAILED: $($newErrors.Count) new error(s)" }

# --- Log, step outputs and job summary ---
if ($LogFile) {
    $logDir = Split-Path -Parent $LogFile
    if ($logDir) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
    $log = @("Layout: $Root", "Engine: $Engine", "Baseline: $Baseline", '') + @($output) + @('', 'stderr:') + @($stderr) +
           @('', "Result: $result", '', 'New errors:') + @($newErrors) + @('', 'Baseline entries that no longer occur:') + @($fixed)
    [System.IO.File]::WriteAllText($LogFile, ($log -join "`r`n") + "`r`n", $utf8)
}
if ($env:GITHUB_OUTPUT -and $completed) {
    [System.IO.File]::AppendAllText($env:GITHUB_OUTPUT,
        "scripts=$($counts['scripts'])`nerrors=$($current.Count)`nnew-errors=$($newErrors.Count)`nfixed=$($fixed.Count)`n", $utf8)
}
if ($env:GITHUB_STEP_SUMMARY) {
    $md = @('### IDE compile check', '')
    if ($completed) {
        $md += ('{0} files, {1} objects, {2} scripts compiled in {3:N0} s. {4} error(s): {5} in the baseline, {6} new; {7} baseline entr{8} no longer occur{9}.' -f
            $counts['files'], $counts['objects'], $counts['scripts'], $elapsed, $current.Count, $known, $newErrors.Count, $fixed.Count,
            $(if ($fixed.Count -eq 1) { 'y' } else { 'ies' }), $(if ($fixed.Count -eq 1) { 's' } else { '' }))
    }
    $md += @('', "Result: **$result**", '')
    if ($newErrors.Count -gt 0) {
        $md += @('New errors:', '', '```text')
        $md += @($newErrors | Select-Object -First 50)
        $md += @('```', '')
    }
    if ($fixed.Count -gt 0) {
        $md += @('No longer occurring (update `tools/ci/ide-compile-baseline.txt`):', '', '```text')
        $md += @($fixed | Select-Object -First 50)
        $md += @('```', '')
    }
    [System.IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, ($md -join "`n") + "`n", $utf8)
}

Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ''
if ($problem -and $env:GITHUB_ACTIONS -eq 'true') { Write-Host "::error title=IDE compile check::$problem" }
Write-Host ("IDE compile check: {0} ({1:N1} s)." -f $result, $elapsed)
if ($passed) { exit 0 }
exit 1

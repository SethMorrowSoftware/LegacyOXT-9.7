<#
.SYNOPSIS
    Checks that every Windows DLL of the extensions in an installed
    OpenXTalk-Lite layout finds the DLLs it imports.

.DESCRIPTION
    Reads the import table and the delay-load import table of every DLL in
    Extensions\<extension>\code\<arch>-win32\ of the layout (the folders the
    IDE maps into revLibraryMapping and the standalone builder copies into
    a standalone's Externals folder). Every imported DLL must be

      - a Windows system DLL: KERNEL32, USER32, ADVAPI32, WS2_32, WINMM,
        CRYPT32, bcrypt, IPHLPAPI, MSWSOCK, msvcrt (the system C runtime
        that MinGW builds use) or api-ms-win-crt-* (the Universal CRT,
        part of Windows 10 and later), or
      - a file in the same folder. The engine loads extension libraries
        with LOAD_WITH_ALTERED_SEARCH_PATH, so Windows looks for their
        DLLs next to them first.

    Anything else, typically the Visual C++ runtime (MSVCP140.dll,
    VCRUNTIME140.dll, VCRUNTIME140_1.dll), is missing on a PC that does not
    have it installed, and the library then fails to load there. Build
    machines and CI runners always have the Visual C++ Redistributable, so
    loading the library there proves nothing; this check reads the files
    instead. It also checks that the DLLs in x86_64-win32 are x86-64 images
    and those in x86-win32 x86 images.

    Exit code 0 when every import is satisfied, 1 otherwise. With -PassThru
    it returns one object per DLL (Path relative to the layout, Machine,
    Imports, Missing, Problem) and does not exit, for smoke-test.ps1.

    Written to run under Windows PowerShell 5.1 and PowerShell 7.

.PARAMETER Root
    The installed layout (the folder with Extensions).

.PARAMETER PassThru
    Return the results instead of printing a report and exiting.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Root,
    [switch]$PassThru
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# Lower case; the same list as SYSTEM_DLLS in tools/oxt/xtalk_extensions.py
$SystemDlls = @('kernel32.dll', 'user32.dll', 'advapi32.dll', 'ws2_32.dll', 'winmm.dll',
    'crypt32.dll', 'bcrypt.dll', 'iphlpapi.dll', 'mswsock.dll', 'msvcrt.dll')
$SystemDllPrefixes = @('api-ms-win-crt-')
$Machines = @{ 'x86_64-win32' = 0x8664; 'x86-win32' = 0x14c }

function Test-SystemDll([string]$Name) {
    $n = $Name.ToLowerInvariant()
    if ($SystemDlls -contains $n) { return $true }
    foreach ($p in $SystemDllPrefixes) { if ($n.StartsWith($p)) { return $true } }
    return $false
}

# Machine and imported DLL names (import and delay-load import tables) of a
# PE image. Throws for anything that is not a valid PE image.
function Read-PeImports([string]$Path) {
    $b = [System.IO.File]::ReadAllBytes($Path)
    if ($b.Length -lt 64 -or $b[0] -ne 0x4d -or $b[1] -ne 0x5a) { throw 'no MZ header' }
    $pe = [int64][BitConverter]::ToUInt32($b, 0x3c)
    if ($pe + 24 -gt $b.Length -or [BitConverter]::ToUInt32($b, $pe) -ne 0x00004550) { throw 'no PE signature' }
    $machine = [BitConverter]::ToUInt16($b, $pe + 4)
    $sectionCount = [BitConverter]::ToUInt16($b, $pe + 6)
    $optSize = [BitConverter]::ToUInt16($b, $pe + 20)
    $opt = $pe + 24
    $magic = [BitConverter]::ToUInt16($b, $opt)
    if ($magic -eq 0x10b) { $dirs = $opt + 96; $imageBase = [int64][BitConverter]::ToUInt32($b, $opt + 28) }
    elseif ($magic -eq 0x20b) { $dirs = $opt + 112; $imageBase = [int64][BitConverter]::ToUInt64($b, $opt + 24) }
    else { throw ('unknown optional header magic 0x{0:x}' -f $magic) }
    $dirCount = [BitConverter]::ToUInt32($b, $dirs - 4)
    $sections = @()
    for ($i = 0; $i -lt $sectionCount; $i++) {
        $s = $opt + $optSize + 40 * $i
        $sections += , @([int64][BitConverter]::ToUInt32($b, $s + 12), [int64][Math]::Max([BitConverter]::ToUInt32($b, $s + 8), [BitConverter]::ToUInt32($b, $s + 16)), [int64][BitConverter]::ToUInt32($b, $s + 20))
    }
    $toOffset = {
        param([int64]$Rva)
        foreach ($sec in $sections) {
            if ($Rva -ge $sec[0] -and $Rva -lt $sec[0] + $sec[1]) { return $Rva - $sec[0] + $sec[2] }
        }
        throw ('RVA 0x{0:x} is outside every section' -f $Rva)
    }
    $readName = {
        param([int64]$Rva)
        $o = & $toOffset $Rva
        $e = [Array]::IndexOf($b, [byte]0, [int]$o)
        if ($e -lt 0) { throw 'unterminated name' }
        return [System.Text.Encoding]::ASCII.GetString($b, [int]$o, [int]($e - $o))
    }
    $imports = New-Object System.Collections.Generic.List[string]
    if ($dirCount -gt 1) {
        $rva = [int64][BitConverter]::ToUInt32($b, $dirs + 8)
        if ($rva -ne 0) {
            $o = & $toOffset $rva
            while ($true) {
                $olt = [BitConverter]::ToUInt32($b, $o)
                $name = [BitConverter]::ToUInt32($b, $o + 12)
                $first = [BitConverter]::ToUInt32($b, $o + 16)
                if ($olt -eq 0 -and $name -eq 0 -and $first -eq 0) { break }
                $imports.Add((& $readName $name))
                $o += 20
            }
        }
    }
    if ($dirCount -gt 13) {
        $rva = [int64][BitConverter]::ToUInt32($b, $dirs + 13 * 8)
        if ($rva -ne 0) {
            $o = & $toOffset $rva
            while ($true) {
                $attributes = [BitConverter]::ToUInt32($b, $o)
                $name = [int64][BitConverter]::ToUInt32($b, $o + 4)
                if ($name -eq 0) { break }
                # Old-style descriptors (attribute bit 0 clear) hold VAs
                if (($attributes -band 1) -eq 0) { $name -= $imageBase }
                $dll = & $readName $name
                if (-not ($imports | Where-Object { $_ -ieq $dll })) { $imports.Add($dll) }
                $o += 32
            }
        }
    }
    return New-Object PSObject -Property @{ Machine = [int]$machine; Imports = @($imports) }
}

if (-not (Test-Path -LiteralPath $Root -PathType Container)) { throw "Not a folder: $Root" }
$Root = (Resolve-Path -LiteralPath $Root).ProviderPath.TrimEnd('\')
$extensions = Join-Path $Root 'Extensions'

$results = New-Object System.Collections.Generic.List[object]
if (Test-Path -LiteralPath $extensions -PathType Container) {
    foreach ($ext in @(Get-ChildItem -LiteralPath $extensions -Directory | Sort-Object Name)) {
        $code = Join-Path $ext.FullName 'code'
        if (-not (Test-Path -LiteralPath $code -PathType Container)) { continue }
        foreach ($folder in @(Get-ChildItem -LiteralPath $code -Directory -Filter '*-win32' | Sort-Object Name)) {
            $present = @(Get-ChildItem -LiteralPath $folder.FullName -File | ForEach-Object { $_.Name.ToLowerInvariant() })
            foreach ($dll in @(Get-ChildItem -LiteralPath $folder.FullName -File -Filter '*.dll' | Sort-Object Name)) {
                $rel = $dll.FullName.Substring($Root.Length + 1)
                $problem = ''
                $machine = 0
                $imports = @()
                $missing = @()
                try {
                    $info = Read-PeImports $dll.FullName
                    $machine = $info.Machine
                    $imports = $info.Imports
                    $missing = @($imports | Where-Object { -not (Test-SystemDll $_) -and ($present -notcontains $_.ToLowerInvariant()) })
                    if ($Machines.ContainsKey($folder.Name) -and $machine -ne $Machines[$folder.Name]) {
                        $problem = 'PE machine 0x{0:x4}, expected 0x{1:x4} for {2}' -f $machine, $Machines[$folder.Name], $folder.Name
                    }
                }
                catch {
                    $problem = "not a valid PE image: $($_.Exception.Message)"
                }
                $results.Add((New-Object PSObject -Property @{
                    Extension = $ext.Name; Folder = $folder.Name; Path = $rel; Machine = $machine
                    Imports = @($imports); Missing = @($missing); Problem = $problem }))
            }
        }
    }
}

if ($PassThru) { return $results }

Write-Host "Layout: $Root"
Write-Host "Windows DLLs in Extensions\*\code\*-win32: $($results.Count)"
$failures = 0
foreach ($r in $results) {
    $local = @($r.Imports | Where-Object { -not (Test-SystemDll $_) })
    $note = if ($local.Count -gt 0) { " (next to it: $($local -join ', '))" } else { '' }
    if ($r.Problem) {
        $failures++
        Write-Host "::error::$($r.Path): $($r.Problem)"
    }
    elseif ($r.Missing.Count -gt 0) {
        $failures++
        Write-Host "::error::$($r.Path) imports $($r.Missing -join ', '): not a Windows system DLL and not in its folder, so it cannot load on a PC that lacks them (for the Visual C++ runtime: package with Visual Studio's redistributable folder, package-windows.ps1 -VcRedist)"
    }
    else {
        Write-Host "OK   $($r.Path)$note"
    }
}
if ($env:GITHUB_STEP_SUMMARY) {
    $status = if ($failures -eq 0) { "all $($results.Count) DLLs find their imports" } else { "$failures of $($results.Count) DLLs have missing imports" }
    Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Encoding utf8 -Value "### Extension DLL imports`n`n$status (``tools/ci/check-extension-imports.ps1``).`n"
}
if ($failures -gt 0) {
    Write-Host "Extension DLL import check FAILED: $failures DLL(s)"
    exit 1
}
Write-Host 'Extension DLL import check passed.'
exit 0

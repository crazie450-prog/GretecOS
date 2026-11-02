<#
.SYNOPSIS
    Copy each new ServerUtilities backup zip of an instance's world to the off-instance backup folder.

.DESCRIPTION
    WO-0.8 (D75, plan section 4.6). Runs as Clayton from GOS_APP via a scheduled task.
    It only READS the instance: each zip is opened with read and delete sharing (CLAUDE.md:
    never block the game's own rename/delete), copied to "<name>.partial", checked, then renamed.
    A zip that something still has open for writing is skipped until the next run.
    Copies older than -KeepDays are deleted from the destination only.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File backup_world.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File backup_world.ps1 -Instance C:\Games\PrismLauncher\instances\GTNH-Lab -Dest C:\Temp\lab-backups
#>
[CmdletBinding()]
param(
    [string]$Instance = 'C:\Games\PrismLauncher\instances\GTNH-Play',
    [string]$Dest = (Join-Path $env:USERPROFILE 'GregtecOS\backups\world'),
    [int]$KeepDays = 14,
    [int]$MinAgeSeconds = 60,
    [string]$LogFile = (Join-Path $env:USERPROFILE 'GregtecOS\logs\backup_world.log')
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Write-BackupLog([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    Write-Output $line
    if ($LogFile) {
        $dir = Split-Path -Parent $LogFile
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Add-Content -LiteralPath $LogFile -Value $line
    }
}

function Test-Zip([string]$Path) {
    try {
        $zip = [System.IO.Compression.ZipFile]::OpenRead($Path)
        try { $null = $zip.Entries.Count } finally { $zip.Dispose() }
        return $true
    } catch {
        return $false
    }
}

# Prism instances keep the game folder in minecraft\ (or .minecraft\ for some imports).
$mc = @('minecraft', '.minecraft') |
    ForEach-Object { Join-Path $Instance $_ } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
    Select-Object -First 1
if (-not $mc) { Write-BackupLog "No minecraft folder under $Instance; nothing to do."; exit 0 }
$source = Join-Path $mc 'backups'
if (-not (Test-Path -LiteralPath $source -PathType Container)) { Write-BackupLog "No backups folder at $source yet; nothing to do."; exit 0 }

if (-not (Test-Path -LiteralPath $Dest)) { New-Item -ItemType Directory -Force -Path $Dest | Out-Null }

$copied = 0
$cutoff = (Get-Date).AddSeconds(-$MinAgeSeconds)
$expiry = (Get-Date).ToUniversalTime().AddDays(-$KeepDays)
foreach ($zip in Get-ChildItem -LiteralPath $source -Filter '*.zip' -File) {
    $target = Join-Path $Dest $zip.Name
    if ((Test-Path -LiteralPath $target) -and ((Get-Item -LiteralPath $target).Length -eq $zip.Length)) { continue }
    if ($zip.LastWriteTimeUtc -lt $expiry) { continue }   # already past retention; copying it would only be pruned below
    if ($zip.LastWriteTime -gt $cutoff) { Write-BackupLog "Skip $($zip.Name): modified in the last $MinAgeSeconds s."; continue }

    # Deny writers (so a zip still being written fails to open) but allow delete (never block the game).
    $share = [System.IO.FileShare]::Read -bor [System.IO.FileShare]::Delete
    try {
        $in = [System.IO.File]::Open($zip.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
    } catch [System.IO.IOException] {
        Write-BackupLog "Skip $($zip.Name): still open for writing."
        continue
    }
    $partial = "$target.partial"
    try {
        $out = [System.IO.File]::Open($partial, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $in.CopyTo($out) } finally { $out.Dispose() }
    } finally {
        $in.Dispose()
    }
    if (-not (Test-Zip $partial)) {
        Remove-Item -LiteralPath $partial -Force
        Write-BackupLog "Skip $($zip.Name): not a complete zip yet."
        continue
    }
    (Get-Item -LiteralPath $partial).LastWriteTimeUtc = $zip.LastWriteTimeUtc
    Move-Item -LiteralPath $partial -Destination $target -Force
    Write-BackupLog "Copied $($zip.Name) ($($zip.Length) bytes)."
    $copied++
}

$removed = 0
foreach ($old in Get-ChildItem -LiteralPath $Dest -Filter '*.zip' -File | Where-Object { $_.LastWriteTimeUtc -lt $expiry }) {
    Remove-Item -LiteralPath $old.FullName -Force
    Write-BackupLog "Removed $($old.Name): older than $KeepDays days."
    $removed++
}
Write-BackupLog "Done: $copied copied, $removed removed."

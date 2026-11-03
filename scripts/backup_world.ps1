<#
.SYNOPSIS
    Copy each new ServerUtilities backup zip of an instance's world to the off-instance backup folder.

.DESCRIPTION
    WO-0.8 (D75, plan section 4.6). Runs as Clayton from GOS_APP via a scheduled task.
    It only READS the instance. Each zip is opened with read and delete sharing (CLAUDE.md: never
    block the game's own rename or delete), so one that is still being written fails to open and
    waits for the next run. Its central directory is checked on that same handle before anything
    is copied; then it is copied to "<name>.partial", checked again and renamed.

    Retention: the newest -KeepAtLeast copies always stay, so a long break from playing never
    empties the folder; older copies go after -KeepDays. A copy that would leave less than
    -MinFreeGB free on the destination drive is skipped. A failing zip is logged and skipped, and
    the newer ones are still copied.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File backup_world.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File backup_world.ps1 -Instance C:\Games\PrismLauncher\instances\GTNH-Lab -Dest C:\Temp\lab-backups
#>
[CmdletBinding()]
param(
    [string]$Instance = 'C:\Games\PrismLauncher\instances\GTNH-Play',
    [string]$Dest = (Join-Path $env:USERPROFILE 'GregtecOS\backups\world'),
    [int]$KeepDays = 14,
    [int]$KeepAtLeast = 12,   # ServerUtilities' own default backups_to_keep
    [int]$MinFreeGB = 20,
    [int]$MinAgeSeconds = 60,
    [string]$LogFile = (Join-Path $env:USERPROFILE 'GregtecOS\logs\backup_world.log')
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
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

# Reads the central directory from an open stream, then rewinds it; $false if the zip is incomplete.
function Test-ZipStream([IO.Stream]$Stream) {
    try {
        $archive = New-Object IO.Compression.ZipArchive($Stream, [IO.Compression.ZipArchiveMode]::Read, $true)
        try { $null = $archive.Entries.Count } finally { $archive.Dispose() }
        return $true
    } catch [IO.InvalidDataException] {
        return $false
    } finally {
        $Stream.Position = 0
    }
}

function Test-ZipFile([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try { Test-ZipStream $stream } finally { $stream.Dispose() }
}

function Copy-Backup([IO.FileInfo]$Zip, [string]$Target) {
    # Deny writers (so a zip still being written fails to open) but allow delete (never block the game).
    $share = [IO.FileShare]::Read -bor [IO.FileShare]::Delete
    try {
        $in = [IO.File]::Open($Zip.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, $share)
    } catch [IO.IOException] {
        return 'still open for writing'
    }
    $partial = "$Target.partial"
    try {
        if (-not (Test-ZipStream $in)) { return 'not a complete zip yet' }
        $out = [IO.File]::Open($partial, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $in.CopyTo($out) } finally { $out.Dispose() }
    } finally {
        $in.Dispose()
    }
    if (-not (Test-ZipFile $partial)) {
        Remove-Item -LiteralPath $partial -Force
        return 'the copy is not a valid zip'
    }
    (Get-Item -LiteralPath $partial).LastWriteTimeUtc = $Zip.LastWriteTimeUtc
    Move-Item -LiteralPath $partial -Destination $Target -Force
    return $null
}

try {
    # Prism instances keep the game folder in minecraft\ (or .minecraft\ for the GTNH zips).
    $mc = @('minecraft', '.minecraft') |
        ForEach-Object { Join-Path $Instance $_ } |
        Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
        Select-Object -First 1
    if (-not $mc) { Write-BackupLog "No minecraft folder under $Instance; nothing to do."; exit 0 }
    $source = Join-Path $mc 'backups'
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { Write-BackupLog "No backups folder at $source yet; nothing to do."; exit 0 }

    if (-not (Test-Path -LiteralPath $Dest)) { New-Item -ItemType Directory -Force -Path $Dest | Out-Null }
    Get-ChildItem -LiteralPath $Dest -Filter '*.partial' -File | Remove-Item -Force   # left by an interrupted run
    $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Dest)))

    $copied = 0
    $cutoff = (Get-Date).AddSeconds(-$MinAgeSeconds)
    $expiry = (Get-Date).ToUniversalTime().AddDays(-$KeepDays)
    foreach ($zip in Get-ChildItem -LiteralPath $source -Filter '*.zip' -File) {
        $target = Join-Path $Dest $zip.Name
        if ((Test-Path -LiteralPath $target) -and ((Get-Item -LiteralPath $target).Length -eq $zip.Length)) { continue }
        if ($zip.LastWriteTimeUtc -lt $expiry) { continue }   # past retention already: it would only be pruned
        if ($zip.LastWriteTime -gt $cutoff) { Write-BackupLog "Skip $($zip.Name): modified in the last $MinAgeSeconds s."; continue }
        if ($drive.AvailableFreeSpace - $zip.Length -lt [int64]$MinFreeGB * 1GB) {
            Write-BackupLog "Skip $($zip.Name): copying it would leave less than $MinFreeGB GB free on $($drive.Name)."
            continue
        }
        try {
            $reason = Copy-Backup $zip $target
        } catch {
            Remove-Item -LiteralPath "$target.partial" -Force -ErrorAction SilentlyContinue
            $reason = "failed: $($_.Exception.Message)"
        }
        if ($reason) { Write-BackupLog "Skip $($zip.Name): $reason."; continue }
        Write-BackupLog "Copied $($zip.Name) ($($zip.Length) bytes)."
        $copied++
    }

    $removed = 0
    $copies = @(Get-ChildItem -LiteralPath $Dest -Filter '*.zip' -File | Sort-Object LastWriteTimeUtc -Descending)
    foreach ($old in $copies | Select-Object -Skip $KeepAtLeast | Where-Object { $_.LastWriteTimeUtc -lt $expiry }) {
        Remove-Item -LiteralPath $old.FullName -Force
        Write-BackupLog "Removed $($old.Name): older than $KeepDays days."
        $removed++
    }
    Write-BackupLog "Done: $copied copied, $removed removed."
} catch {
    Write-BackupLog "ERROR: $($_.Exception.Message)"
    exit 1
}

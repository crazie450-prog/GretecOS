<#
.SYNOPSIS
    Set Windows' communications ducking ("When Windows detects communications activity") to
    "Do nothing", show it, or undo the change.

.DESCRIPTION
    WO-5.3 ([D§2.6], D205). By default Windows turns other sounds down by 80% while it sees a
    "communication session" (a headset's Chat endpoint or microphone in use), so the game dips
    whenever GOS listens or speaks. This sets Sound > Communications to "Do nothing" for the
    account that runs it. It changes only that one per-user setting:

        HKCU\Software\Microsoft\Multimedia\Audio  UserDuckingPreference (DWORD)
        0 = mute all other sounds, 1 = reduce by 80% (Windows' default when the value is absent),
        2 = reduce by 50%, 3 = do nothing

    It is Clayton's sound setting, so Clayton runs it himself, in his own account (a PC step:
    GOS never runs it). The value it found is printed and kept in -BackupFile, so -Undo puts
    that back; with no backup, -Undo removes the value, which is Windows' default (80%).
    Apps that are already playing pick the change up the next time they open their audio:
    restart Discord and GTNH, or sign out and in.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File set_comm_ducking.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File set_comm_ducking.ps1 -Show
    powershell -NoProfile -ExecutionPolicy Bypass -File set_comm_ducking.ps1 -Undo
#>
[CmdletBinding()]
param(
    [switch]$Undo,
    [switch]$Show,
    # Tests point these at a throwaway key and file; nothing else should change them.
    [string]$KeyPath = 'HKCU:\Software\Microsoft\Multimedia\Audio',
    [string]$BackupFile = (Join-Path $env:USERPROFILE 'GregtecOS\data\comm_ducking_before.txt')
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Name = 'UserDuckingPreference'
$DoNothing = 3
$Labels = @{
    0 = 'mute all other sounds'
    1 = 'reduce other sounds by 80%'
    2 = 'reduce other sounds by 50%'
    3 = 'do nothing'
}

function Get-Ducking {
    $item = Get-ItemProperty -LiteralPath $KeyPath -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $null }
    return [int]$item.$Name
}

function Format-Ducking($Value) {
    if ($null -eq $Value) { return "not set (Windows' default: $($Labels[1]))" }
    $label = $Labels[[int]$Value]
    if ($null -eq $label) { $label = 'unknown' }
    return "$Value ($label)"
}

$current = Get-Ducking
if ($Show) {
    Write-Output "Communications ducking: $(Format-Ducking $current)"
    exit 0
}

if ($Undo) {
    $before = $null
    if (Test-Path -LiteralPath $BackupFile) {
        $saved = (Get-Content -LiteralPath $BackupFile -TotalCount 1).Trim()
        if ($saved -match '^[0-3]$') { $before = [int]$saved }
    }
    if ($null -eq $before) {
        Remove-ItemProperty -LiteralPath $KeyPath -Name $Name -ErrorAction SilentlyContinue
    } else {
        Set-ItemProperty -LiteralPath $KeyPath -Name $Name -Value $before -Type DWord
    }
    if (Test-Path -LiteralPath $BackupFile) { Remove-Item -LiteralPath $BackupFile }
    Write-Output "Communications ducking: $(Format-Ducking (Get-Ducking)) (undone)"
    exit 0
}

if ($current -eq $DoNothing) {
    Write-Output "Communications ducking: $(Format-Ducking $current) (already; nothing changed)"
    exit 0
}
$dir = Split-Path -Parent $BackupFile
if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
if ($null -eq $current) { $saved = 'unset' } else { $saved = [string]$current }
Set-Content -LiteralPath $BackupFile -Value $saved -Encoding ASCII
if (-not (Test-Path -LiteralPath $KeyPath)) { New-Item -Path $KeyPath -Force | Out-Null }
Set-ItemProperty -LiteralPath $KeyPath -Name $Name -Value $DoNothing -Type DWord
Write-Output "Communications ducking was: $(Format-Ducking $current)"
Write-Output "Communications ducking now: $(Format-Ducking (Get-Ducking))"
Write-Output 'Restart Discord and GTNH (or sign out and in) so they pick it up. Undo: -Undo'

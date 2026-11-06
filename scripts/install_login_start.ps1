#Requires -Version 5.1
<#
.SYNOPSIS
    Start Gregtec OS at Windows login (D77, D116): a per-user Run key for gos-supervisor.

.DESCRIPTION
    WO-2.1. Clayton runs this as himself (no admin needed), from GOS_APP\scripts after a deploy.
    It writes one value under his HKCU Run key:

        GregtecOS = "<GOS_APP>\current\.venv\Scripts\gos-supervisor.exe" --login

    gos-supervisor.exe is a windowless launcher. At each login it starts the supervisor, which
    starts the core and restarts it if it dies. The supervisor exits at once if gos.toml says
    [startup] core_at_login = false, so that switch works without running this script again.
    `gos stop` stops GOS until `gos serve`; Task Manager's Startup apps tab can also turn the
    entry off.

    It refuses to run from anywhere but GOS_APP (Clayton never runs code gos-dev can change,
    D75), as gos-dev, elevated (run it in a normal window), or when the release has no
    gos-supervisor.exe. -Remove deletes the value.
    -StartNow also starts the supervisor straight away, so there is no need to log off.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\GregtecOS-app\scripts\install_login_start.ps1 -StartNow
    powershell -NoProfile -ExecutionPolicy Bypass -File C:\GregtecOS-app\scripts\install_login_start.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$AppRoot = 'C:\GregtecOS-app',
    [string]$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
    [string]$ValueName = 'GregtecOS',
    [string]$Config,          # passed on as --config; default: the supervisor's own default
    [string]$DevUser = 'gos-dev',
    [switch]$Remove,
    [switch]$StartNow
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Get-FullPath([string]$Path) { [IO.Path]::GetFullPath($Path).TrimEnd('\') }

# One plain line on stderr and exit 1 (Write-Error would wrap it in a formatted error record).
function Stop-Script([string]$Message) {
    [Console]::Error.WriteLine($Message)
    exit 1
}

function Test-Under([string]$Child, [string]$Parent) {
    $c = (Get-FullPath $Child) + '\'
    $p = (Get-FullPath $Parent) + '\'
    $c.StartsWith($p, [StringComparison]::OrdinalIgnoreCase)
}

if ($env:USERNAME -ieq $DevUser) {
    Stop-Script "Run this as Clayton, not as ${DevUser}: login start is for Clayton's own account."
}

# Elevated, -StartNow would start GOS elevated, and with it Prism, Lab and Export (gos-dev's
# mods). An elevated window may also belong to another account, whose HKCU this would write.
# GOS_ALLOW_ELEVATED: GitHub's Windows runners run elevated; never set it on a real PC.
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -and $env:GOS_ALLOW_ELEVATED -ne '1') {
    Stop-Script 'Run this in a normal PowerShell window, not as administrator: GOS would run elevated, and so would everything it starts.'
}

if ($Remove) {
    if (Test-Path -LiteralPath $RunKey) {
        $item = Get-ItemProperty -LiteralPath $RunKey -Name $ValueName -ErrorAction SilentlyContinue
        if ($item) {
            Remove-ItemProperty -LiteralPath $RunKey -Name $ValueName
            Write-Host "Removed $ValueName from $RunKey. GOS no longer starts at login."
            exit 0
        }
    }
    Write-Host "$ValueName isn't in $RunKey; nothing to remove."
    exit 0
}

$app = Get-FullPath $AppRoot
if (-not (Test-Under $PSScriptRoot $app)) {
    Stop-Script "Run this from GOS_APP ($app\scripts), not from $PSScriptRoot. Deploy first: uv run gos deploy"
}
$appItem = Get-Item -LiteralPath $app -Force
if ($appItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    Stop-Script "$app is a link or junction; GOS_APP must be a real folder."
}
$exe = Join-Path $app 'current\.venv\Scripts\gos-supervisor.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    Stop-Script "$exe is missing. Deploy a release that has it: uv run gos deploy"
}

$command = '"{0}" --login' -f $exe
if ($Config) { $command += ' --config "{0}"' -f (Get-FullPath $Config) }
if (-not (Test-Path -LiteralPath $RunKey)) { New-Item -Path $RunKey -Force | Out-Null }
New-ItemProperty -LiteralPath $RunKey -Name $ValueName -Value $command -PropertyType String -Force | Out-Null
Write-Host "GOS starts at login: $RunKey\$ValueName = $command"

if ($StartNow) {
    $arguments = @('--login')
    # Windows PowerShell's Start-Process joins arguments without quoting them.
    if ($Config) { $arguments += @('--config', ('"{0}"' -f (Get-FullPath $Config))) }
    Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $env:USERPROFILE
    Write-Host 'Started the supervisor. Check it with: gos status'
}
exit 0

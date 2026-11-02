#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    WO-0.8 setup: wall Claude Code's gos-dev account off from Play (D75, PM15, plan section 4.6).

.DESCRIPTION
    Clayton runs this in an elevated Windows PowerShell 5.1, in his own account. It is idempotent:
    run it once before WO-0.7, and again after WO-0.3 creates GTNH-Export and GTNH-Lab.

    1. Creates the standard local account gos-dev (member of Users only).
    2. Creates C:\GregtecOS-dev (gos-dev: modify), C:\GregtecOS-app (gos-dev: read) and
       C:\Games\PrismLauncher (gos-dev: none), with inheritance from C:\ broken so that
       Authenticated Users' Modify on new root folders can't leak in.
    3. Installs portable Prism Launcher (pinned, SHA256-checked) and sets instance permissions:
       instances\ and GTNH-Play read-only for gos-dev, GTNH-Export and GTNH-Lab modify.
    4. Sets Clayton's GOS_HOME: data\ readable by gos-dev, backups\world\ not at all.
    5. Writes Claude Code deny rules as a managed-settings drop-in (a second layer only).
    6. Copies scripts\ into GOS_APP\scripts (stand-in for `gos deploy` until WO-0.2).
    7. Registers the world backup task (runs as Clayton, S4U: no stored password, no window).
    8. Checks every path's ACL and exits 1 if gos-dev could write where it must not.

    It never touches an existing Prism or Minecraft install; it only reports them.
#>
[CmdletBinding()]
param(
    [string]$DevUser = 'gos-dev',
    [string]$AppRoot = 'C:\GregtecOS-app',
    [string]$DevRoot = 'C:\GregtecOS-dev',
    [string]$PrismRoot = 'C:\Games\PrismLauncher',
    [string]$GosHome = (Join-Path $env:USERPROFILE 'GregtecOS'),
    [string]$PlayInstance = 'GTNH-Play',
    [string[]]$WritableInstances = @('GTNH-Export', 'GTNH-Lab'),
    [string]$ManagedSettingsDir = 'C:\Program Files\ClaudeCode\managed-settings.d',
    [string]$TaskName = 'GregtecOS world backup',
    [securestring]$DevPassword,
    [switch]$SkipPrismDownload,
    [switch]$Yes
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# Pinned portable Prism. SHA256 computed from the release asset on 2026-11-02.
$PrismVersion = '11.1.1'
$PrismZipUrl = "https://github.com/PrismLauncher/PrismLauncher/releases/download/$PrismVersion/PrismLauncher-Windows-MSVC-Portable-$PrismVersion.zip"
$PrismZipSha256 = 'AB35A770FB06D89D2CCC098079DB5DB329FB4E68F42B72BABD8B095EFDE3D2D7'
$Marker = '.gregtec-os'   # written into folders this script created, so a re-run knows they are ours

$Full = [Security.AccessControl.FileSystemRights]::FullControl
$Modify = [Security.AccessControl.FileSystemRights]'Modify, Synchronize'
$ReadExec = [Security.AccessControl.FileSystemRights]'ReadAndExecute, Synchronize'
$System = New-Object Security.Principal.SecurityIdentifier 'S-1-5-18'
$Admins = New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544'
$UsersGroup = New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-545'

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }

function New-Rule([Security.Principal.SecurityIdentifier]$Sid, [Security.AccessControl.FileSystemRights]$Rights) {
    New-Object Security.AccessControl.FileSystemAccessRule($Sid, $Rights, 'ContainerInherit, ObjectInherit', 'None', 'Allow')
}

function New-Dir([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
}

# Replace a folder's DACL with exactly these rules and stop inheriting from its parent.
function Set-ProtectedAcl([string]$Path, [Security.AccessControl.FileSystemAccessRule[]]$Rules) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in $Rules) { $acl.AddAccessRule($rule) }
    (Get-Item -LiteralPath $Path).SetAccessControl($acl)
}

# Keep the inherited ACEs, drop any explicit ACE for $Sid, then optionally grant $Rights.
function Set-ExplicitGrant([string]$Path, [Security.Principal.SecurityIdentifier]$Sid, $Rights) {
    $item = Get-Item -LiteralPath $Path
    $acl = $item.GetAccessControl('Access')
    $acl.PurgeAccessRules($Sid)
    if ($null -ne $Rights) { $acl.AddAccessRule((New-Rule $Sid $Rights)) }
    $item.SetAccessControl($acl)
}

# C:\Games\PrismLauncher -> //c/Games/PrismLauncher (Claude Code matches Windows paths in POSIX form).
function ConvertTo-ClaudePath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    '//' + $full.Substring(0, 1).ToLowerInvariant() + ($full.Substring(2) -replace '\\', '/')
}

function Test-Under([string]$Child, [string]$Parent) {
    $c = [IO.Path]::GetFullPath($Child).TrimEnd('\') + '\'
    $p = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $c.StartsWith($p, [StringComparison]::OrdinalIgnoreCase)
}

# ---------------------------------------------------------------- preflight
$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$OwnerSid = $me.User
Write-Step "Owner (gets Full Control everywhere): $($me.Name) [$OwnerSid]"
Write-Host "    GOS_HOME: $GosHome"
if ($me.Name -match "\\$([regex]::Escape($DevUser))$") { throw "Run this as yourself, not as $DevUser." }
if (Test-Under $PSScriptRoot $DevRoot) { throw "Don't run this from ${DevRoot}: $DevUser can change files there. Run it from your own copy or from $AppRoot\scripts." }

$existing = @(
    (Join-Path $env:APPDATA 'PrismLauncher'),
    (Join-Path $env:LOCALAPPDATA 'Programs\PrismLauncher'),
    (Join-Path $env:APPDATA '.minecraft')
) | Where-Object { Test-Path -LiteralPath $_ }
foreach ($path in $existing) { Write-Warning "Existing Minecraft/Prism data at $path. This script leaves it alone (treat it as Play)." }

$prismMarker = Join-Path $PrismRoot $Marker
if ((Test-Path -LiteralPath $PrismRoot) -and -not (Test-Path -LiteralPath $prismMarker) -and
    @(Get-ChildItem -LiteralPath $PrismRoot -Force).Count -gt 0) {
    throw "$PrismRoot already exists and wasn't created by this script. Refusing to change it; pass -PrismRoot to use another folder."
}

if (-not $Yes) {
    $answer = Read-Host "Set up $DevUser and the folders above? Type YES to continue"
    if ($answer -cne 'YES') { Write-Host 'Stopped; nothing changed.'; exit 1 }
}

# ---------------------------------------------------------------- 1. account
Write-Step "Account $DevUser"
$dev = Get-LocalUser -Name $DevUser -ErrorAction SilentlyContinue
if (-not $dev) {
    if (-not $DevPassword) {
        $DevPassword = Read-Host "New password for $DevUser" -AsSecureString
        $again = Read-Host 'Same password again' -AsSecureString
        $plain = { param($s) [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)) }
        if ((& $plain $DevPassword) -cne (& $plain $again)) { throw 'The passwords did not match; nothing changed.' }
    }
    $dev = New-LocalUser -Name $DevUser -Password $DevPassword -PasswordNeverExpires `
        -Description 'GregtecOS: Claude Code development account (D75). No access to Play.'
    Write-Host "    created $DevUser"
}
$DevSid = $dev.SID

# The local groups gos-dev is in. ADSI, because Get-LocalGroupMember throws on groups with
# unresolvable or Azure AD members (often Administrators).
function Get-DevGroupSids {
    $user = [ADSI]"WinNT://$env:COMPUTERNAME/$DevUser,user"
    @($user.psbase.Invoke('Groups')) | ForEach-Object {
        $name = $_.GetType().InvokeMember('Name', 'GetProperty', $null, $_, $null)
        (Get-LocalGroup -Name $name).SID.Value
    }
}
if (@(Get-DevGroupSids) -notcontains $UsersGroup.Value) { Add-LocalGroupMember -SID $UsersGroup.Value -Member $DevUser }
$otherGroups = @(Get-DevGroupSids | Where-Object { $_ -ne $UsersGroup.Value })
if ($otherGroups.Count) { throw "$DevUser must be a member of Users only, but is also in: $($otherGroups -join ', ')." }

# ---------------------------------------------------------------- 2. roots
Write-Step 'Folders and permissions'
$ownerFull = @((New-Rule $System $Full), (New-Rule $Admins $Full), (New-Rule $OwnerSid $Full))

# A parent folder this script creates (e.g. C:\Games) would inherit Authenticated Users' Modify from C:\,
# which would let gos-dev rename it and plant its own PrismLauncher. Protect any parent we create.
$prismParent = Split-Path -Parent $PrismRoot
if (-not (Test-Path -LiteralPath $prismParent)) {
    New-Dir $prismParent
    Set-ProtectedAcl $prismParent ($ownerFull + (New-Rule $UsersGroup $ReadExec))
    New-Item -ItemType File -Force -Path (Join-Path $prismParent $Marker) | Out-Null
}

New-Dir $DevRoot;   Set-ProtectedAcl $DevRoot   ($ownerFull + (New-Rule $DevSid $Modify))
New-Dir $AppRoot;   Set-ProtectedAcl $AppRoot   ($ownerFull + (New-Rule $DevSid $ReadExec))
New-Dir $PrismRoot; Set-ProtectedAcl $PrismRoot $ownerFull
New-Item -ItemType File -Force -Path $prismMarker | Out-Null

# ---------------------------------------------------------------- 3. Prism and instances
if (-not $SkipPrismDownload -and -not (Test-Path -LiteralPath (Join-Path $PrismRoot 'prismlauncher.exe'))) {
    Write-Step "Portable Prism Launcher $PrismVersion"
    $zip = Join-Path $env:TEMP "PrismLauncher-$PrismVersion-portable.zip"
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -UseBasicParsing -Uri $PrismZipUrl -OutFile $zip
    if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $PrismZipSha256) {
        Remove-Item -LiteralPath $zip -Force
        throw 'Prism download failed its SHA256 check; nothing was extracted.'
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $PrismRoot -Force
    Remove-Item -LiteralPath $zip -Force
    if (-not (Test-Path -LiteralPath (Join-Path $PrismRoot 'portable.txt'))) { throw 'portable.txt missing after extract.' }
}

$instances = Join-Path $PrismRoot 'instances'
New-Dir $instances
Set-ExplicitGrant $instances $DevSid $ReadExec          # read-only; GTNH-Play and new instances inherit this
foreach ($inst in Get-ChildItem -LiteralPath $instances -Directory) {
    $grant = if ($WritableInstances -contains $inst.Name) { $Modify } else { $null }
    Set-ExplicitGrant $inst.FullName $DevSid $grant
    Write-Host ("    {0}: {1}" -f $inst.Name, $(if ($grant) { 'gos-dev modify' } else { 'gos-dev read-only' }))
}

# ---------------------------------------------------------------- 4. Clayton's GOS_HOME
$data = Join-Path $GosHome 'data'
$worldBackups = Join-Path $GosHome 'backups\world'
$logs = Join-Path $GosHome 'logs'
foreach ($dir in @($GosHome, $data, $worldBackups, $logs)) { New-Dir $dir }
Set-ExplicitGrant $GosHome $DevSid $null
Set-ExplicitGrant $data $DevSid $ReadExec
Set-ExplicitGrant $worldBackups $DevSid $null

# ---------------------------------------------------------------- 5. Claude Code deny rules
Write-Step 'Claude Code managed settings (deny rules, second layer)'
$play = Join-Path $instances $PlayInstance
# Only Read(...) and Edit(...) rules match file paths (Edit also covers Write). These bind Claude Code's
# own file tools and the file commands it recognises in Bash, not PowerShell cmdlets or subprocesses,
# so the ACLs above are the wall (PM15). The drop-in is machine-wide: it applies to every account.
$accounts = "$(ConvertTo-ClaudePath $PrismRoot)/accounts.json"
$deny = @(
    "Edit($(ConvertTo-ClaudePath $play)/**)",
    "Edit($(ConvertTo-ClaudePath $AppRoot)/**)",
    "Edit($(ConvertTo-ClaudePath $GosHome)/**)",
    "Read($(ConvertTo-ClaudePath $worldBackups)/**)",
    "Read($accounts)",
    "Edit($accounts)"
)
$json = @{ permissions = @{ deny = $deny } } | ConvertTo-Json -Depth 4
$null = $json | ConvertFrom-Json
New-Dir $ManagedSettingsDir
$dropIn = Join-Path $ManagedSettingsDir 'gregtec-os.json'
# No BOM, and replace atomically: Claude Code refuses to start, for every account, if a policy file can't be parsed.
[IO.File]::WriteAllText("$dropIn.tmp", $json, (New-Object Text.UTF8Encoding $false))
Move-Item -LiteralPath "$dropIn.tmp" -Destination $dropIn -Force
Write-Host "    $dropIn"

# ---------------------------------------------------------------- 6. GOS_APP scripts (until gos deploy)
$appScripts = Join-Path $AppRoot 'scripts'
if (-not (Test-Under $PSScriptRoot $appScripts)) {
    Write-Step "Copy scripts into $appScripts"
    New-Dir $appScripts
    Copy-Item -Path (Join-Path $PSScriptRoot '*.ps1') -Destination $appScripts -Force
}

# ---------------------------------------------------------------- 7. world backup task
Write-Step "Scheduled task '$TaskName'"
$backupArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Instance "{1}" -Dest "{2}" -LogFile "{3}"' -f `
    (Join-Path $appScripts 'backup_world.ps1'), $play, $worldBackups, (Join-Path $logs 'backup_world.log')
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $backupArgs
$every30 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30)
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $me.Name
$principal = New-ScheduledTaskPrincipal -UserId $me.Name -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $TaskName -TaskPath '\GregtecOS\' -Action $action -Trigger @($every30, $atLogon) `
    -Principal $principal -Settings $settings -Force | Out-Null

# ---------------------------------------------------------------- 8. check
Write-Step 'Check: what can gos-dev do?'
# SIDs whose ACEs apply to gos-dev: itself, its local groups, and the well-known groups every logon gets.
$devSids = @($DevSid.Value, 'S-1-1-0', 'S-1-2-0', 'S-1-5-4', 'S-1-5-11', 'S-1-5-113', 'S-1-5-32-545') + @(Get-DevGroupSids)
$WriteMask = 0x000D0156 -bor 0x50000000   # write/append data and attributes, delete, delete child, WRITE_DAC, WRITE_OWNER, GENERIC_WRITE/ALL
$ReadMask = 0x00000009 -bor 0x90000000    # read data, read EA, GENERIC_READ/ALL

function Get-DevAccess([string]$Path) {
    $mask = 0
    foreach ($ace in (Get-Item -LiteralPath $Path).GetAccessControl('Access').GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($ace.AccessControlType -eq 'Allow' -and $devSids -contains $ace.IdentityReference.Value) { $mask = $mask -bor [int]$ace.FileSystemRights }
    }
    [pscustomobject]@{ Write = [bool]($mask -band $WriteMask); Read = [bool]($mask -band $ReadMask) }
}

$expect = [ordered]@{}
$expect[$DevRoot] = 'write'
$expect[$AppRoot] = 'read'
$expect[$PrismRoot] = 'none'
if (Test-Path -LiteralPath (Join-Path $prismParent $Marker)) { $expect[$prismParent] = 'read' }
$expect[$instances] = 'read'
foreach ($inst in Get-ChildItem -LiteralPath $instances -Directory) {
    $expect[$inst.FullName] = $(if ($WritableInstances -contains $inst.Name) { 'write' } else { 'read' })
}
$expect[$GosHome] = 'none'
$expect[$data] = 'read'
$expect[$worldBackups] = 'none'
$expect[$ManagedSettingsDir] = 'read'

$failed = $false
foreach ($path in $expect.Keys) {
    $got = Get-DevAccess $path
    $want = $expect[$path]
    $ok = switch ($want) {
        'write' { $got.Write }
        'read'  { -not $got.Write }
        'none'  { -not $got.Write -and -not $got.Read }
    }
    if (-not $ok) { $failed = $true }
    '{0,-4}  {1,-5}  write={2,-5} read={3,-5}  {4}' -f $(if ($ok) { 'ok' } else { 'FAIL' }), $want, $got.Write, $got.Read, $path | Write-Host
}
if ($failed) { Write-Host 'gos-dev has access it must not have (see FAIL lines).' -ForegroundColor Red; exit 1 }
Write-Step "Done. Next: sign in as $DevUser once, then follow the PR's steps."

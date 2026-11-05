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
       C:\Games\PrismLauncher (gos-dev: none). Their inheritance from C:\ is broken, they are owned
       by Administrators, and every existing folder above them gets a deny ACE so gos-dev can't
       rename it and swap in a tree of its own.
    3. Installs portable Prism Launcher (pinned, SHA256-checked) and sets instance permissions:
       instances\ and GTNH-Play read-only for gos-dev, GTNH-Export and GTNH-Lab modify.
    4. Sets Clayton's GOS_HOME: data\, vault\ and logs\ readable by gos-dev (D82), everything
       else not at all.
    5. Writes Claude Code deny rules as a managed-settings drop-in (a second layer only).
    6. Copies itself and backup_world.ps1 into GOS_APP\scripts, until `gos deploy` has run
       (deploy then keeps GOS_APP\scripts in step with the deployed release).
    7. Registers the world backup task (runs as Clayton, S4U: no stored password, no window).
    8. Checks every protected file and folder, and every Minecraft or Prism install it finds on
       the fixed drives, and exits 1 if gos-dev could change any of them.

    It refuses to run from a copy gos-dev could have changed, and it never changes an existing
    Minecraft or Prism install: it only reports those gos-dev can write.
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
    [string[]]$InstallScanRoots,   # where to look for other Minecraft installs; default: every fixed drive
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
$Scripts = @('setup_gos_dev.ps1', 'backup_world.ps1')

$Full = [Security.AccessControl.FileSystemRights]::FullControl
$Modify = [Security.AccessControl.FileSystemRights]'Modify, Synchronize'
$ReadExec = [Security.AccessControl.FileSystemRights]'ReadAndExecute, Synchronize'
$NoRename = [Security.AccessControl.FileSystemRights]'Delete, ChangePermissions, TakeOwnership'
$System = New-Object Security.Principal.SecurityIdentifier 'S-1-5-18'
$Admins = New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544'
$UsersGroup = New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-545'

# Access masks. Write: write/append data and attributes, delete, delete child, WRITE_DAC,
# WRITE_OWNER, GENERIC_WRITE, GENERIC_ALL. Read: read data, read EA, GENERIC_READ, GENERIC_ALL.
$WriteMask = 0x000D0156 -bor 0x50000000
$ReadMask = 0x00000009 -bor 0x90000000
$ReplaceMask = 0x000D0000 -bor 0x10000000   # DELETE, WRITE_DAC, WRITE_OWNER, GENERIC_ALL
$DeleteChild = 0x00000040
# SIDs a gos-dev logon can carry besides its own and its local groups': Everyone, LOCAL, CONSOLE
# LOGON, OWNER RIGHTS, NETWORK, BATCH, INTERACTIVE, Authenticated Users, REMOTE INTERACTIVE, This
# Organization, Local account, NTLM, Users.
$WellKnownSids = @('S-1-1-0', 'S-1-2-0', 'S-1-2-1', 'S-1-3-4', 'S-1-5-2', 'S-1-5-3', 'S-1-5-4',
    'S-1-5-11', 'S-1-5-14', 'S-1-5-15', 'S-1-5-113', 'S-1-5-64-10', 'S-1-5-32-545')
$script:DevSids = $WellKnownSids   # gos-dev's own SID and groups are added once the account exists

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }

function New-Rule([Security.Principal.SecurityIdentifier]$Sid, [Security.AccessControl.FileSystemRights]$Rights) {
    New-Object Security.AccessControl.FileSystemAccessRule($Sid, $Rights, 'ContainerInherit, ObjectInherit', 'None', 'Allow')
}

function New-Dir([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
}

# Full path without a trailing backslash, except for a drive root, which keeps it: "C:" alone means
# the current folder on drive C, not its root.
function Get-FullPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.Length -gt 3) { $full.TrimEnd('\') } else { $full }
}

function Test-DriveRoot([string]$Path) { $Path -match '^[A-Za-z]:\\$' }

function Get-ParentPath([string]$Path) {
    $parent = Split-Path -Parent (Get-FullPath $Path)
    if ($parent) { Get-FullPath $parent } else { $null }
}

function Test-Under([string]$Child, [string]$Parent) {
    $c = (Get-FullPath $Child).TrimEnd('\') + '\'
    $p = (Get-FullPath $Parent).TrimEnd('\') + '\'
    $c.StartsWith($p, [StringComparison]::OrdinalIgnoreCase)
}

# Replace a folder's DACL with exactly these rules, stop inheriting from its parent, and give it to
# Administrators: an owner can always rewrite a DACL.
function Set-ProtectedAcl([string]$Path, [Security.AccessControl.FileSystemAccessRule[]]$Rules) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($Admins)
    foreach ($rule in $Rules) { $acl.AddAccessRule($rule) }
    (Get-Item -LiteralPath $Path -Force).SetAccessControl($acl)
}

# Keep the inherited ACEs, drop any explicit ACE for $Sid, then optionally grant $Rights.
function Set-ExplicitGrant([string]$Path, [Security.Principal.SecurityIdentifier]$Sid, $Rights) {
    $item = Get-Item -LiteralPath $Path -Force
    $acl = $item.GetAccessControl('Access')
    $acl.PurgeAccessRules($Sid)
    if ($null -ne $Rights) { $acl.AddAccessRule((New-Rule $Sid $Rights)) }
    $item.SetAccessControl($acl)
}

# What gos-dev may do to one file or folder: the allowed mask (allow ACEs for its SIDs, minus deny
# ACEs for them) and whether it owns it. Inherit-only ACEs are counted too, which errs on the safe
# side: they are what the folder's children get.
function Get-DevAccess([string]$Path) {
    $acl = (Get-Item -LiteralPath $Path -Force).GetAccessControl('Access, Owner')
    $allow = 0; $deny = 0
    foreach ($ace in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($script:DevSids -contains $ace.IdentityReference.Value) {
            if ($ace.AccessControlType -eq 'Allow') { $allow = $allow -bor [int]$ace.FileSystemRights }
            else { $deny = $deny -bor [int]$ace.FileSystemRights }
        }
    }
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    [pscustomobject]@{ Mask = ($allow -band (-bnot $deny)); Owned = ($script:DevSids -contains $owner) }
}

# Ways gos-dev could swap $Path out without writing it: rename, move or re-permission any folder
# above it (DELETE, WRITE_DAC, WRITE_OWNER or ownership), or delete entries from one (DELETE_CHILD).
function Get-ChainRisk([string]$Path) {
    $reasons = @()
    $child = Get-FullPath $Path
    while ($parent = Get-ParentPath $child) {
        if (Test-Path -LiteralPath $parent) {
            $access = Get-DevAccess $parent
            if ($access.Mask -band $DeleteChild) { $reasons += "can delete or rename entries in $parent" }
            if (-not (Test-DriveRoot $parent)) {
                if ($access.Owned) { $reasons += "owns $parent" }
                if ($access.Mask -band $ReplaceMask) { $reasons += "can rename or re-permission $parent" }
            }
        }
        if (Test-DriveRoot $parent) { break }
        $child = $parent
    }
    $reasons
}

# Ways gos-dev could change $Path itself or swap it out.
function Get-ChangeRisk([string]$Path) {
    $reasons = @()
    $access = Get-DevAccess $Path
    if ($access.Owned) { $reasons += "owns $Path" }
    if ($access.Mask -band $WriteMask) { $reasons += "can write $Path" }
    $reasons + @(Get-ChainRisk $Path)
}

# Every file and folder under $Root, skipping the subtrees in $Skip.
function Get-Tree([string]$Root, [string[]]$Skip) {
    $stack = New-Object System.Collections.Stack
    $stack.Push((Get-Item -LiteralPath $Root -Force))
    while ($stack.Count) {
        $item = $stack.Pop()
        if ($Skip | Where-Object { (Get-FullPath $_) -ieq (Get-FullPath $item.FullName) }) { continue }
        $item
        if ($item.PSIsContainer -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            foreach ($child in Get-ChildItem -LiteralPath $item.FullName -Force) { $stack.Push($child) }
        }
    }
}

# Minecraft and Prism installs on the fixed drives, a few folders deep (CLAUDE.md: every one of
# them counts as Play). Only reported; never changed.
function Find-Installs([string[]]$Roots, [string[]]$Exclude) {
    $markers = @('prismlauncher.cfg', 'multimc.cfg', 'instance.cfg', 'launcher_profiles.json', 'level.dat', '.minecraft')
    $skip = @('Windows', 'Program Files', 'Program Files (x86)', 'ProgramData', '$Recycle.Bin',
        'System Volume Information', 'Recovery', 'PerfLogs', 'Users')
    $found = foreach ($root in $Roots) {
        foreach ($top in Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction SilentlyContinue) {
            if ($skip -contains $top.Name) { continue }
            Get-ChildItem -LiteralPath $top.FullName -Recurse -Depth 3 -Force -ErrorAction SilentlyContinue |
                Where-Object { $markers -contains $_.Name } |
                ForEach-Object { if ($_.PSIsContainer) { $_.FullName } else { $_.DirectoryName } }
        }
    }
    @($found | Sort-Object -Unique | Where-Object { $p = $_; -not ($Exclude | Where-Object { Test-Under $p $_ }) })
}

# C:\Games\PrismLauncher -> //c/Games/PrismLauncher (Claude Code matches Windows paths in POSIX form).
function ConvertTo-ClaudePath([string]$Path) {
    $full = Get-FullPath $Path
    '//' + $full.Substring(0, 1).ToLowerInvariant() + ($full.Substring(2) -replace '\\', '/')
}

# ---------------------------------------------------------------- preflight (changes nothing)
$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$OwnerSid = $me.User
Write-Step "Owner (gets Full Control everywhere): $($me.Name) [$OwnerSid]"
Write-Host "    GOS_HOME: $GosHome"
if ($me.Name -match "\\$([regex]::Escape($DevUser))$") { throw "Run this as yourself, not as $DevUser." }

$existingDev = Get-LocalUser -Name $DevUser -ErrorAction SilentlyContinue
if ($existingDev) { $script:DevSids = $WellKnownSids + $existingDev.SID.Value }

# Clayton must never run code gos-dev can change: check this copy before it runs anything.
$sourceRisk = @(Get-ChangeRisk $PSScriptRoot) + @($Scripts | ForEach-Object { Get-ChangeRisk (Join-Path $PSScriptRoot $_) })
if ($sourceRisk) {
    throw "Refusing to run from $PSScriptRoot because $DevUser (or every user) $($sourceRisk -join '; '). Run it from a copy in your own profile or from $AppRoot\scripts."
}

$prismMarker = Join-Path $PrismRoot $Marker
if ((Test-Path -LiteralPath $PrismRoot) -and -not (Test-Path -LiteralPath $prismMarker) -and
    @(Get-ChildItem -LiteralPath $PrismRoot -Force).Count -gt 0) {
    throw "$PrismRoot already exists and wasn't created by this script. Refusing to change it; pass -PrismRoot to use another folder."
}

if (-not $InstallScanRoots) {
    $InstallScanRoots = [IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq 'Fixed' -and $_.IsReady } |
        ForEach-Object { $_.RootDirectory.FullName }
}
Write-Step "Looking for other Minecraft and Prism installs in $($InstallScanRoots -join ', ')"
$installs = Find-Installs $InstallScanRoots @($PrismRoot, $DevRoot, $AppRoot, $GosHome)
foreach ($path in $installs) { Write-Warning "Existing Minecraft/Prism data at $path. This script won't change it (treat it as Play)." }
foreach ($path in @((Join-Path $env:APPDATA 'PrismLauncher'), (Join-Path $env:LOCALAPPDATA 'Programs\PrismLauncher'), (Join-Path $env:APPDATA '.minecraft'))) {
    if (Test-Path -LiteralPath $path) { Write-Warning "Existing Minecraft/Prism data at $path (in your profile; $DevUser can't reach it)." }
}

if (-not $Yes) {
    $answer = Read-Host "Set up $DevUser and the folders above? Type YES to continue"
    if ($answer -cne 'YES') { Write-Host 'Stopped; nothing changed.'; exit 1 }
}

# ---------------------------------------------------------------- 1. account
Write-Step "Account $DevUser"
$dev = $existingDev
if (-not $dev) {
    if (-not $DevPassword) {
        $DevPassword = Read-Host "New password for $DevUser" -AsSecureString
        $again = Read-Host 'Same password again' -AsSecureString
        $plain = { param($s) [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)) }
        if ((& $plain $DevPassword) -cne (& $plain $again)) { throw 'The passwords did not match; nothing changed.' }
    }
    $dev = New-LocalUser -Name $DevUser -Password $DevPassword -PasswordNeverExpires `
        -Description 'GregtecOS Claude Code dev account (D75)'   # 48 characters at most
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
$script:DevSids = @($WellKnownSids + $DevSid.Value + @(Get-DevGroupSids) | Sort-Object -Unique)

# ---------------------------------------------------------------- 2. roots
Write-Step 'Folders and permissions'
# Anything gos-dev already owns in a root we are about to adopt could be re-permissioned by it later.
foreach ($root in @($AppRoot, $PrismRoot) | Where-Object { Test-Path -LiteralPath $_ }) {
    $skip = @($WritableInstances | ForEach-Object { Join-Path $PrismRoot "instances\$_" })
    $owned = @(Get-Tree $root $skip | Where-Object { (Get-DevAccess $_.FullName).Owned } | Select-Object -First 5)
    if ($owned) { throw "$DevUser owns files in $root (for example $($owned[0].FullName)); refusing to adopt it." }
}

# Every existing folder above a root: deny gos-dev rename and re-permission on that folder only, so it
# can't move the folder aside and put a look-alike in its place (a folder made under C:\ inherits
# Authenticated Users: Modify, which includes DELETE). A parent this script creates is protected below.
$ownerFull = @((New-Rule $System $Full), (New-Rule $Admins $Full), (New-Rule $OwnerSid $Full))
$prismParent = Get-ParentPath $PrismRoot
if (-not (Test-Path -LiteralPath $prismParent)) {
    New-Dir $prismParent
    Set-ProtectedAcl $prismParent ($ownerFull + (New-Rule $UsersGroup $ReadExec))
    New-Item -ItemType File -Force -Path (Join-Path $prismParent $Marker) | Out-Null
}
foreach ($root in @($AppRoot, $DevRoot, $PrismRoot, $GosHome, $ManagedSettingsDir)) {
    $folder = Get-ParentPath $root
    while ($folder -and -not (Test-DriveRoot $folder)) {
        if (Test-Path -LiteralPath $folder) {
            $access = Get-DevAccess $folder
            if ($access.Owned) { throw "$DevUser owns $folder; give it to Administrators first (icacls `"$folder`" /setowner *S-1-5-32-544)." }
            if ($access.Mask -band $ReplaceMask) {
                $item = Get-Item -LiteralPath $folder -Force
                $acl = $item.GetAccessControl('Access')
                $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($DevSid, $NoRename, 'None', 'None', 'Deny')))
                $item.SetAccessControl($acl)
                Write-Host "    ${folder}: $DevUser may no longer rename or re-permission it (deny ACE on this folder only)"
            }
        }
        $folder = Get-ParentPath $folder
    }
}

New-Dir $DevRoot;   Set-ProtectedAcl $DevRoot   ($ownerFull + (New-Rule $DevSid $Modify))
New-Dir $AppRoot;   Set-ProtectedAcl $AppRoot   ($ownerFull + (New-Rule $DevSid $ReadExec))
New-Dir $PrismRoot; Set-ProtectedAcl $PrismRoot $ownerFull
New-Item -ItemType File -Force -Path $prismMarker | Out-Null

# ---------------------------------------------------------------- 3. Prism and instances
$prismExe = Join-Path $PrismRoot 'prismlauncher.exe'
if (-not $SkipPrismDownload -and -not (Test-Path -LiteralPath $prismExe)) {
    Write-Step "Portable Prism Launcher $PrismVersion"
    $zip = Join-Path $env:TEMP "PrismLauncher-$PrismVersion-portable.zip"
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -UseBasicParsing -Uri $PrismZipUrl -OutFile $zip
    if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $PrismZipSha256) {
        Remove-Item -LiteralPath $zip -Force
        throw 'Prism download failed its SHA256 check; nothing was extracted.'
    }
    # Extract inside the protected root, check, then move into place, so an interrupted extract
    # can't leave a prismlauncher.exe without its portable.txt.
    $staging = Join-Path $PrismRoot '.gos-staging'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $staging -Force
    Remove-Item -LiteralPath $zip -Force
    foreach ($name in 'prismlauncher.exe', 'portable.txt') {
        if (-not (Test-Path -LiteralPath (Join-Path $staging $name))) { throw "$name missing from the Prism zip; nothing was installed." }
    }
    Get-ChildItem -LiteralPath $staging -Force | Move-Item -Destination $PrismRoot -Force
    Remove-Item -LiteralPath $staging -Recurse -Force
}
# Without portable.txt next to the exe, Prism silently uses %APPDATA%\PrismLauncher instead.
if ((Test-Path -LiteralPath $prismExe) -and -not (Test-Path -LiteralPath (Join-Path $PrismRoot 'portable.txt'))) {
    throw "portable.txt is missing next to $prismExe, so Prism would not run portable. Create it (an empty file) and re-run."
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
$vault = Join-Path $GosHome 'vault'
foreach ($dir in @($GosHome, $data, $worldBackups, $logs)) { New-Dir $dir }
Set-ExplicitGrant $GosHome $DevSid $null
Set-ExplicitGrant $worldBackups $DevSid $null
# Read only for gos-dev (plan 4.6, D82). `gos vault deploy` makes the vault and refuses a folder it
# didn't make, so the vault is granted once it exists: re-run this script after deploying it.
$readable = @($data, $logs) + @(@($vault) | Where-Object { Test-Path -LiteralPath $_ })
foreach ($dir in $readable) { Set-ExplicitGrant $dir $DevSid $ReadExec }
if (-not (Test-Path -LiteralPath $vault)) {
    Write-Host "    $vault doesn't exist yet: after 'gos vault deploy', re-run this script so $DevUser can read it"
}

# ---------------------------------------------------------------- 5. Claude Code deny rules
Write-Step 'Claude Code managed settings (deny rules, second layer)'
# Only Read(...) and Edit(...) rules match file paths (Edit also covers Write). These bind Claude Code's
# own file tools and the file commands it recognises in Bash, not PowerShell cmdlets or subprocesses,
# so the ACLs above are the wall (PM15). The drop-in is machine-wide: it applies to every account.
$play = Join-Path $instances $PlayInstance
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
# Once `gos deploy` has run, GOS_APP\scripts is a copy of the deployed release's scripts\ and
# deploy owns it: this script leaves it alone (D91).
$appScripts = Join-Path $AppRoot 'scripts'
if (Test-Path -LiteralPath (Join-Path $AppRoot 'current\release.json')) {
    Write-Step "$appScripts comes from gos deploy; not copying into it"
} elseif ((Get-FullPath $PSScriptRoot) -ine (Get-FullPath $appScripts)) {
    Write-Step "Copy $($Scripts -join ' and ') into $appScripts"
    New-Dir $appScripts
    foreach ($name in $Scripts) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $appScripts -Force }
}

# ---------------------------------------------------------------- 7. world backup task
Write-Step "Scheduled task '$TaskName'"
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'   # never via PATH
$backupArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Instance "{1}" -Dest "{2}" -LogFile "{3}"' -f `
    (Join-Path $appScripts 'backup_world.ps1'), $play, $worldBackups, (Join-Path $logs 'backup_world.log')
$action = New-ScheduledTaskAction -Execute $powershell -Argument $backupArgs -WorkingDirectory $appScripts
$every30 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30)
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $me.Name
$principal = New-ScheduledTaskPrincipal -UserId $me.Name -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $TaskName -TaskPath '\GregtecOS\' -Action $action -Trigger @($every30, $atLogon) `
    -Principal $principal -Settings $settings -Force | Out-Null

# ---------------------------------------------------------------- 8. check
Write-Step 'Check: what can gos-dev do?'
$failed = $false
function Write-Check([bool]$Ok, [string]$Want, [string]$Path, [string]$Detail) {
    if (-not $Ok) { $script:failed = $true }
    '{0,-4}  {1,-5}  {2}{3}' -f $(if ($Ok) { 'ok' } else { 'FAIL' }), $Want, $Path, $(if ($Detail) { "  ($Detail)" } else { '' }) | Write-Host
}

# Folders gos-dev must be able to write: the folder itself only.
$writable = @($DevRoot) + @($WritableInstances | ForEach-Object { Join-Path $instances $_ } | Where-Object { Test-Path -LiteralPath $_ })
foreach ($path in $writable) { Write-Check ([bool]((Get-DevAccess $path).Mask -band $WriteMask)) 'write' $path '' }

# Trees gos-dev must not change ('read') or not even read ('none'): every file and folder in them,
# plus every folder above them.
$trees = @(
    @{ Path = $AppRoot; Want = 'read'; Skip = @() },
    @{ Path = $PrismRoot; Want = 'none'; Skip = @($instances) },
    @{ Path = $instances; Want = 'read'; Skip = $writable },
    @{ Path = $GosHome; Want = 'none'; Skip = $readable },
    @{ Path = $ManagedSettingsDir; Want = 'read'; Skip = @() }
) + @($readable | ForEach-Object { @{ Path = $_; Want = 'read'; Skip = @() } })
foreach ($tree in $trees) {
    $bad = @(); $count = 0
    foreach ($item in Get-Tree $tree.Path $tree.Skip) {
        $count++
        $access = Get-DevAccess $item.FullName
        $mask = if ($tree.Want -eq 'read') { $WriteMask } else { $WriteMask -bor $ReadMask }
        if ($access.Owned -or ($access.Mask -band $mask)) { $bad += $item.FullName }
    }
    $risk = @(Get-ChainRisk $tree.Path)
    Write-Check (-not $bad -and -not $risk) $tree.Want $tree.Path "$count items"
    foreach ($path in $bad | Select-Object -First 20) { Write-Host "        $DevUser can $(if ($tree.Want -eq 'read') { 'change' } else { 'open' }) $path" }
    if ($bad.Count -gt 20) { Write-Host "        ... and $($bad.Count - 20) more" }
    foreach ($reason in $risk) { Write-Host "        $DevUser $reason" }
}
foreach ($path in $writable) {
    foreach ($reason in @(Get-ChainRisk $path)) { Write-Check $false 'write' $path "$DevUser $reason" }
}
foreach ($path in $installs) {
    Write-Check (-not ((Get-DevAccess $path).Mask -band $WriteMask)) 'read' $path 'existing install, not changed by this script'
}
# A Claude Code policy in HKLM outranks the policy files, and Claude Code then ignores this drop-in.
$hklmPolicy = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\ClaudeCode' -Name Settings -ErrorAction SilentlyContinue
Write-Check (-not $hklmPolicy) 'rules' $dropIn $(if ($hklmPolicy) { 'HKLM\SOFTWARE\Policies\ClaudeCode has a Settings value, so Claude Code ignores this file' } else { 'no HKLM Claude Code policy overrides it' })

if ($failed) { Write-Host 'gos-dev can change or reach something it must not (see FAIL lines).' -ForegroundColor Red; exit 1 }
Write-Step "Done. Next: sign in as $DevUser once, then follow the PR's steps."

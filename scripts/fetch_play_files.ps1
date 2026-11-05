#Requires -Version 5.1
<#
.SYNOPSIS
    WO-0.7: download the pinned GTNH 2.9.0 Prism zip and the addon jars for Play, SHA256-checked.

.DESCRIPTION
    Clayton runs this in his own account, in a normal (non-admin) Windows PowerShell 5.1. It writes
    only into -Dest, a staging folder in his profile, and refuses one inside a Prism install, an
    instance or a game folder. It never opens C:\Games\PrismLauncher or any instance: importing
    the zip and copying the jars into GTNH-Play are his steps in the PR's runbook.

    Each file is checked against the SHA256 pinned below. A file already there with that hash is
    skipped. One with another hash is deleted and downloaded again. A download that fails its check
    is deleted and reported, the rest still run, and the script exits 1.

    The "Next" steps it prints are for building Play from scratch. Play already exists, so for the
    jars D90 added (OTHTechnology-2.2.1.1.jar, plannh-0.2.0.jar) the order is: a Lab smoke test
    first (copy them into Lab's mods and delete Lab's plannh-0.1.9.jar, before the next
    sync_instances.py run, which mirrors Lab from Play), then Clayton copies them into Play's mods
    and deletes plannh-0.1.9.jar there (two PlanNH jars won't start).

.PARAMETER Build
    Java17 (the Java 17-26 build, recommended by D68) or Java8.

.PARAMETER Manifest
    A JSON array of {"name", "url", "sha256"} to fetch instead of the pinned files (for tests).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File fetch_play_files.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File fetch_play_files.ps1 -Build Java8
#>
[CmdletBinding()]
param(
    [ValidateSet('Java17', 'Java8')]
    [string]$Build = 'Java17',
    [string]$Dest = (Join-Path $env:USERPROFILE 'Downloads\GTNH-2.9.0'),
    [string]$Manifest
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is far slower with its progress bar
# Windows PowerShell 5.1 may not offer TLS 1.2 by default; GitHub requires it.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# Pins, checked 2026-11-03 (123Technology and PlanNH 0.2.0: 2026-11-05, D90).
# downloads.gtnewhorizons.com publishes no checksum, so the zip hashes are those of the files it
# serves (two full passes agreed, and the sizes matched Content-Length). The jars are GitHub release
# assets, hashed the same way; the worldedit and PlanNH 0.1.9 hashes also match GTNH's Nexus.
$Zips = @{
    Java17 = @{ name = 'GT_New_Horizons_2.9.0_Java_17-26.zip'   # 747,848,389 bytes
        url = 'https://downloads.gtnewhorizons.com/Multi_mc_downloads/GT_New_Horizons_2.9.0_Java_17-26.zip'
        sha256 = '6d0ebe7e6be18fb74b4fc920916cc5710e142a3f885072d324da05244d398b16' }
    Java8 = @{ name = 'GT_New_Horizons_2.9.0_Java_8.zip'         # 732,488,324 bytes
        url = 'https://downloads.gtnewhorizons.com/Multi_mc_downloads/GT_New_Horizons_2.9.0_Java_8.zip'
        sha256 = 'ae94a01a5367065f71ac9b5150966182a5b55762c841749fcb3d603ab5ed78af' }
}
$Addons = @(
    # Twist Space Technology 0.8.0 (2026-11-02), built for 2.9.0 stable. 0.8.0-RC2.9 is not a stand-in.
    @{ name = 'TwistSpaceTechnology-0.8.0.jar'
        url = 'https://github.com/Nxer/Twist-Space-Technology-Mod/releases/download/0.8.0/TwistSpaceTechnology-0.8.0.jar'
        sha256 = '7c2b217d2bd31123ed7badc30fc94e3ad82f1b42507b17a0ee2effdfd5a723f8' },
    # Programmable Hatches 0.2.1p0, the author's README pick for GTNH 2.9.0.
    @{ name = 'programmablehatches-0.2.1p0.jar'
        url = 'https://github.com/reobf/Programmable-Hatches-Mod/releases/download/v0.2.1p0-beta/programmablehatches-0.2.1p0.jar'
        sha256 = 'ac3fb4fb642812f8b3c79e974ec0f2cf1673ebf554918b1f30da86f5f3cf44b7' },
    # worldedit-gtnh v0.0.10, the latest (plan 5.1). Issue #30 (meta > 15) only matters when using it.
    @{ name = 'worldedit-v0.0.10.jar'
        url = 'https://github.com/GTNewHorizons/worldedit-gtnh/releases/download/v0.0.10/worldedit-v0.0.10.jar'
        sha256 = '4904a6d7e29e53afaf28d99a50ce4b946b429d174fecd0563e7787e9c8ef481f' },
    # PlanNH 0.2.0 (2026-11-03, 14,410,615 bytes), client-only planner (F8); replaces 0.1.9 (D90).
    # Rollback: the plannh-0.1.9.jar this script fetched before stays in -Dest (nothing here
    # deletes files it doesn't list). To fetch it again:
    # https://github.com/GTNewHorizons/PlanNH/releases/download/0.1.9/plannh-0.1.9.jar
    # SHA256 a9976d2f43463ccf19683eb00b21ff68e8aa81a5b0573b2108b423847ffa36cb.
    @{ name = 'plannh-0.2.0.jar'
        url = 'https://github.com/GTNewHorizons/PlanNH/releases/download/0.2.0/plannh-0.2.0.jar'
        sha256 = '7201b4b32f3dadef5160ae24b89bc9ff079c25af00eac067e1273b61940f5fc9' },
    # 123Technology 2.2.1.1 (2026-11-04, 7,800,686 bytes), built for 2.9.0 stable; fixes #277
    # (recipe IllegalAccessError with TST). The jar only, not the optional 123Technology_Musics.zip (D90).
    @{ name = 'OTHTechnology-2.2.1.1.jar'
        url = 'https://github.com/CallmeSHaobe/123Technology/releases/download/2.2.1.1/OTHTechnology-2.2.1.1.jar'
        sha256 = 'f863dacded2231cda2239ae68912dcec64a62b1ee067cc70432e3cab7034efce' }
)
# Left out on purpose:
# - GT-Not-Leisure and GTNLLib: no versioned release contains 28007498 (plan 13.1, D02). The rolling
#   "dev-build" asset is replaced on every push, so it can't be pinned.

try {
    $files = if ($Manifest) { Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json } else { @($Zips[$Build]) + $Addons }
    foreach ($f in $files) {
        if (-not $f.name -or $f.name -ne [IO.Path]::GetFileName($f.name) -or $f.name -match '^\.+$') { throw "Not a plain file name: '$($f.name)'." }
        if ($f.sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw "Not a SHA256 for $($f.name): '$($f.sha256)'." }
    }

    # Never stage inside a Prism install, an instance or a game folder (CLAUDE.md: they are all Play).
    $Dest = [IO.Path]::GetFullPath($Dest)
    $markers = @('prismlauncher.exe', 'prismlauncher.cfg', 'instance.cfg', 'mmc-pack.json', 'level.dat')
    $folder = $Dest
    while ($folder) {
        if (@('.minecraft', 'minecraft', 'instances', 'PrismLauncher') -contains (Split-Path -Leaf $folder) -or
            ($markers | Where-Object { Test-Path -LiteralPath (Join-Path $folder $_) })) {
            throw "Refusing to write under $folder (a Prism, instance or game folder). Pick a folder in your profile."
        }
        $folder = Split-Path -Parent $folder
    }
    if (-not (Test-Path -LiteralPath $Dest)) { New-Item -ItemType Directory -Force -Path $Dest | Out-Null }

    $failed = 0
    foreach ($f in $files) {
        $target = Join-Path $Dest $f.name
        if (Test-Path -LiteralPath $target) {
            if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $f.sha256) {   # -eq ignores case
                Write-Output "ok        $($f.name) (already here)"
                continue
            }
            Remove-Item -LiteralPath $target -Force
            Write-Output "deleted   $($f.name): wrong SHA256; downloading it again"
        }
        $partial = "$target.partial"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $f.url -OutFile $partial
        } catch {
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
            Write-Output "FAILED    $($f.name): download failed: $($_.Exception.Message)"
            $failed++
            continue
        }
        $got = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash
        if ($got -ne $f.sha256) {
            Remove-Item -LiteralPath $partial -Force
            Write-Output "FAILED    $($f.name): SHA256 $($got.ToLowerInvariant()), expected $($f.sha256.ToLowerInvariant()); deleted"
            $failed++
            continue
        }
        Move-Item -LiteralPath $partial -Destination $target -Force
        Write-Output "ok        $($f.name) (downloaded)"
    }
    if ($failed) { Write-Output "$failed file(s) failed; nothing bad was kept. Re-run to retry."; exit 1 }

    Write-Output "`nAll files are in $Dest. Next (the PR's WO-0.7 steps):"
    Write-Output '  (Play already built? Skip these steps. Run the Lab smoke test first (D90), and when you copy'
    Write-Output '  plannh-0.2.0.jar into Play''s mods, delete plannh-0.1.9.jar there: two PlanNH jars won''t start.)'
    foreach ($f in $files | Where-Object { $_.name -like '*.zip' }) {
        Write-Output "  Prism: Add Instance > Import > $(Join-Path $Dest $f.name), Name: GTNH-Play"
    }
    $jars = @($files | Where-Object { $_.name -like '*.jar' })
    if ($jars) {
        Write-Output '  then launch it with no addons to the main menu and quit,'
        Write-Output '  then copy into C:\Games\PrismLauncher\instances\GTNH-Play\.minecraft\mods\ :'
        foreach ($f in $jars) { Write-Output "    $(Join-Path $Dest $f.name)" }
    }
    Write-Output '  then launch to the main menu and quit. Do not create a world.'
} catch {
    Write-Output "ERROR: $($_.Exception.Message)"
    exit 1
}

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

    GT-Not-Leisure 0.2.7 and GTNLLib 1.1.8 (D365, D382) go into the LAB only, both together (GTNL won't
    start without GTNLLib): Lab's smoke test first, and Play gets them only after Clayton passes it
    and decides (D365, D02's world gate). Same order as above: copy them into Lab's mods before the
    next sync_instances.py run, and add a temporary [sync.lab] exclude entry for them (D93 (5)) so
    the sync, which mirrors Lab from Play, doesn't delete them.

    Twist Space Technology 0.8.2, PlanNH 0.2.6, 123Technology 2.2.1.3 and Programmable Hatches
    0.2.1p9 (D373, D382) are UPDATES of jars Play has, for the LAB first: Play keeps
    TwistSpaceTechnology-0.8.0.jar, plannh-0.2.0.jar, OTHTechnology-2.2.1.1.jar and
    programmablehatches-0.2.1p0.jar, which stay pinned here as the rollback, until Clayton passes the Lab test and says yes. In Lab's mods the old jar is
    deleted when the new one is copied in (two versions of one mod won't start). A sync would
    delete the new jar and put the old one back until Play has the new one, so the same temporary
    [sync.lab] exclude entries come first (D93 (5); checklist 3.4.3 lists them).

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

# Pins, checked 2026-11-03 (123Technology and PlanNH 0.2.0: 2026-11-05, D90; GTNL and GTNLLib:
# 2026-11-20, D365; Twist Space Technology 0.8.2: 2026-11-20, D373; GTNLLib 1.1.8, PlanNH 0.2.6,
# 123Technology 2.2.1.3 and Programmable Hatches 0.2.1p9: 2026-11-22, D382).
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
    # In Play (D94). It stays pinned as the rollback while 0.8.2, below, is tested in the Lab (D373).
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
    # 0.2.0 is in Play (D94) and stays pinned as the rollback while 0.2.6, below, is tested in the
    # Lab (D373, D382).
    @{ name = 'plannh-0.2.0.jar'
        url = 'https://github.com/GTNewHorizons/PlanNH/releases/download/0.2.0/plannh-0.2.0.jar'
        sha256 = '7201b4b32f3dadef5160ae24b89bc9ff079c25af00eac067e1273b61940f5fc9' },
    # 123Technology 2.2.1.1 (2026-11-04, 7,800,686 bytes), built for 2.9.0 stable; fixes #277
    # (recipe IllegalAccessError with TST). The jar only, not the optional 123Technology_Musics.zip (D90).
    @{ name = 'OTHTechnology-2.2.1.1.jar'
        url = 'https://github.com/CallmeSHaobe/123Technology/releases/download/2.2.1.1/OTHTechnology-2.2.1.1.jar'
        sha256 = 'f863dacded2231cda2239ae68912dcec64a62b1ee067cc70432e3cab7034efce' },
    # GT-Not-Leisure 0.2.7 (tag 0.2.7 = ef436cf, 2026-11-16, 21,180,104 bytes): the first versioned
    # release with 28007498 "adapt to GTNH 2.9.0 final" (#666), D02's gate (D365). LAB FIRST: Play
    # gets it only after Clayton's Lab smoke test (D365). Not the -dev, -predowngrade or -sources jar.
    @{ name = 'sciencenotleisure-0.2.7.jar'
        url = 'https://github.com/GTNotLeisure/GT-Not-Leisure/releases/download/0.2.7/sciencenotleisure-0.2.7.jar'
        sha256 = '17e7f8b28f4de9f460cd0845fdbad3b57ff820b66b6de36c928107c43f0c97ce' },
    # GTNLLib 1.1.8 (tag 1.1.8 = cd4256d, 2026-11-16, 1,253,517 bytes; D382): GTNL 0.2.7 builds
    # against 1.1.6, and 1.1.7 fixes GTNLLib's id for 123Technology ("123technology" since 2.2.1.2),
    # which 123Technology 2.2.1.3 below needs; 1.1.6..1.1.8 deletes no class. GTNL requires it.
    # LAB FIRST, together with GTNL (D365). (1.1.6, D365's first pin: SHA256
    # 83a3b0ab4487fc86bda0ee9694a3fe5aa860e4d4e3494b1894aab8bce502c9ca.)
    @{ name = 'gtnllib-1.1.8.jar'
        url = 'https://github.com/GTNotLeisure/GTNLLib/releases/download/1.1.8/gtnllib-1.1.8.jar'
        sha256 = '93011e8d8da447e138e8ea228b5937c1946c4f23c04c9136b4bd1b6f3803befa' },
    # Twist Space Technology 0.8.2 (tag 0.8.2 = 99f8ed2, released 2026-11-17, 29,683,612 bytes):
    # built with gtnhVersion "2.9.0" (GT 5.09.54.265); no machine id of 0.8.0 moved or went away.
    # LAB FIRST: it REPLACES TwistSpaceTechnology-0.8.0.jar, and Play keeps 0.8.0 until Clayton's
    # Lab test and his yes (D373). The main jar, not the -dev or -sources one.
    @{ name = 'TwistSpaceTechnology-0.8.2.jar'
        url = 'https://github.com/Nxer/Twist-Space-Technology-Mod/releases/download/0.8.2/TwistSpaceTechnology-0.8.2.jar'
        sha256 = '5d5a537af18baea5b49efeafa200bd4c2869c4b100b6b1ba52b4e6fd80f427ed' },
    # PlanNH 0.2.6 (tag 0.2.6 = 95d3e41, released 2026-11-22, 14,504,496 bytes; D382), client only:
    # built on exactly 2.9.0's GTNHLib, ModularUI2 and NEI; mixins as in 0.2.4. LAB FIRST: it
    # REPLACES plannh-0.2.0.jar, and Play keeps 0.2.0 until Clayton's Lab test and his yes. (0.2.4,
    # D373's first pin: SHA256 671e54712deb732264ef51aed1fde6134fc6257f193938df53d84c247f10cbd7.)
    @{ name = 'plannh-0.2.6.jar'
        url = 'https://github.com/GTNewHorizons/PlanNH/releases/download/0.2.6/plannh-0.2.6.jar'
        sha256 = 'b3acb053a17f3aeebb22c26577ab060e8bc0a409a9325640c2c2c497e4e0ec37' },
    # 123Technology 2.2.1.3 (tag 2.2.1.3 = b1b826d, 2026-11-15, 8,152,080 bytes; D382): built with
    # gtnhVersion "2.9.0"; its notes say "Contains WIP Content"; requires TST; mod id 123technology.
    # LAB FIRST: it REPLACES OTHTechnology-2.2.1.1.jar, and Play keeps 2.2.1.1 until his yes.
    @{ name = 'OTHTechnology-2.2.1.3.jar'
        url = 'https://github.com/CallmeSHaobe/123Technology/releases/download/2.2.1.3/OTHTechnology-2.2.1.3.jar'
        sha256 = '1e29c3bd76fe59cbc63385b3df7c00aa96c3697f5adfa1b8873ad6ba005b7a64' },
    # Programmable Hatches 0.2.1p9 (tag v0.2.1p9-beta = 0644186, 2026-11-19, 3,154,355 bytes; D382):
    # the author's "2.9.0 Daily" (currently maintained) build, against GT .264 and NEI 2.8.171.
    # LAB FIRST: it REPLACES programmablehatches-0.2.1p0.jar, and Play keeps p0 until his yes.
    @{ name = 'programmablehatches-0.2.1p9.jar'
        url = 'https://github.com/reobf/Programmable-Hatches-Mod/releases/download/v0.2.1p9-beta/programmablehatches-0.2.1p9.jar'
        sha256 = '1f6aa64911e3c611905e88df433bc8d56524e12260a52a81ebc861cfb5f0264e' }
)
# Left out on purpose:
# - GT-Not-Leisure's rolling "dev-build" asset: it is replaced on every push, so it can't be pinned.
#   0.2.7 above is the versioned release with 28007498 (D365).
# - worldedit-gtnh 1.0.1 and 1.0.2: built against newer mods than GTNH 2.9.0 or 2.9.1 ship, and no
#   gain GOS needs today (D380, D382). Play keeps v0.0.10.
# - GTNH 2.9.1's zips (D382): not yet, because TST 0.8.2 fails to load on 2.9.1 (TST #1430). Java 17-26:
#   753,304,930 bytes, SHA256 998875d413ed5d9b16fed9992844b61dcb82e819a15ea56e4eb868f68ea98327; Java 8:
#   737,944,815 bytes, SHA256 5663d779b2c0b31f7006d404fcc0e8a5bba15e3af68f59636c66b3a390c0d18d.
#   PlanNH 0.3.0 is built for 2.9.1.

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
    # D365: GT-Not-Leisure and GTNLLib go into the Lab first; Play gets them only after Clayton's yes.
    $labFirst = @('sciencenotleisure-*.jar', 'gtnllib-*.jar')
    # D373: updates that REPLACE a jar Play has (new jar = the jar it replaces). Lab first; Play
    # keeps the old jar, still pinned above as the rollback, until Clayton's Lab test and his yes.
    $replaces = @{
        'TwistSpaceTechnology-0.8.2.jar' = 'TwistSpaceTechnology-0.8.0.jar'
        'plannh-0.2.6.jar' = 'plannh-0.2.0.jar'
        'OTHTechnology-2.2.1.3.jar' = 'OTHTechnology-2.2.1.1.jar'
        'programmablehatches-0.2.1p9.jar' = 'programmablehatches-0.2.1p0.jar'
    }
    $isLabFirst = { param($n) [bool]($labFirst | Where-Object { $n -like $_ }) }
    $jars = @($files | Where-Object { $_.name -like '*.jar' -and -not (& $isLabFirst $_.name) -and -not $replaces.ContainsKey($_.name) })
    $lab = @($files | Where-Object { $_.name -like '*.jar' -and (& $isLabFirst $_.name) })
    $swap = @($files | Where-Object { $_.name -like '*.jar' -and $replaces.ContainsKey($_.name) })
    if ($jars) {
        Write-Output '  then launch it with no addons to the main menu and quit,'
        Write-Output '  then copy into C:\Games\PrismLauncher\instances\GTNH-Play\.minecraft\mods\ :'
        foreach ($f in $jars) { Write-Output "    $(Join-Path $Dest $f.name)" }
    }
    Write-Output '  then launch to the main menu and quit. Do not create a world.'
    if ($lab) {
        Write-Output '  NOT into Play yet (D365): these go into the LAB first, both together, for its smoke test'
        Write-Output '  (checklist 3.4.3); Play gets them only after that test passes and you say yes (5.2.12, 3.15.8),'
        Write-Output '  before the survival world starts (GTNL is a world gate):'
        foreach ($f in $lab) { Write-Output "    $(Join-Path $Dest $f.name)" }
    }
    if ($swap) {
        Write-Output '  NOT into Play yet (D373): these REPLACE a jar, in the LAB first (checklist 3.4.3). In the'
        Write-Output '  Lab''s mods folder delete the old jar named beside each one: two versions of one mod won''t'
        Write-Output '  start. A sync would put the old jar back, so add 3.4.3''s [sync.lab] exclude entries first.'
        Write-Output '  Play keeps the old jar until that test passes and you say yes (5.2.13, 3.15.9):'
        foreach ($f in $swap) { Write-Output "    $(Join-Path $Dest $f.name)  (replaces $($replaces[$f.name]))" }
    }
} catch {
    Write-Output "ERROR: $($_.Exception.Message)"
    exit 1
}

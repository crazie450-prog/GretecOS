<#
.SYNOPSIS
    Prism's pre-launch command for GTNH-Play (WO-1.4, D121). Prism runs it before the game starts.

.DESCRIPTION
    Runs `gos hook pre-launch` from the deployed release (..\current\.venv\Scripts\gos.exe, next to
    this script in GOS_APP\scripts, D91). That refuses the launch (exit 3) while an export holds the
    instance lock or GTNH-Export is running, and records whether recipe data is stale.

    Only exit 3 is passed to Prism, which then doesn't start the game (V25). Any other failure,
    a missing release included, is printed to Prism's console and the game still starts: GOS being
    broken never stops Clayton from playing.

    install_prism_hooks.py sets this as Play's pre-launch command; Clayton runs it himself.
#>
# No $ErrorActionPreference = 'Stop': Windows PowerShell turns a native command's stderr into
# errors when its own output is redirected, as Prism's is.
$gos = Join-Path $PSScriptRoot '..\current\.venv\Scripts\gos.exe'
if (-not (Test-Path -LiteralPath $gos -PathType Leaf)) {
    Write-Output "GOS: $gos is missing (run gos deploy); no pre-launch checks. The game starts."
    exit 0
}
try {
    & $gos hook pre-launch
    $code = $LASTEXITCODE
} catch {
    Write-Output "GOS: couldn't run $gos ($_); no pre-launch checks. The game starts."
    exit 0
}
if ($code -eq 3) { exit 3 }
if ($code -ne 0) {
    Write-Output "GOS: the pre-launch check failed (exit $code); the game starts anyway."
}
exit 0

<#
.SYNOPSIS
    Prism's post-exit command for GTNH-Play (WO-1.4, D69, D121). Prism runs it after the game quits.

.DESCRIPTION
    Runs `gos hook post-exit` from the deployed release (..\current\.venv\Scripts\gos.exe, next to
    this script in GOS_APP\scripts, D91). If Play changed since the last export, that asks
    "Mods changed. Export now?" (5 minutes, then no) and on yes starts `gos export run`; otherwise
    it does nothing. Prism runs post-exit commands only after a clean exit, not after a crash.

    Its exit code goes to Prism: anything but 0 makes Prism show the console with the error.

    install_prism_hooks.py sets this as Play's post-exit command; Clayton runs it himself.
#>
# No $ErrorActionPreference = 'Stop': Windows PowerShell turns a native command's stderr into
# errors when its own output is redirected, as Prism's is.
$gos = Join-Path $PSScriptRoot '..\current\.venv\Scripts\gos.exe'
if (-not (Test-Path -LiteralPath $gos -PathType Leaf)) {
    Write-Output "GOS: $gos is missing (run gos deploy); not checking for an export."
    exit 1
}
& $gos hook post-exit
exit $LASTEXITCODE

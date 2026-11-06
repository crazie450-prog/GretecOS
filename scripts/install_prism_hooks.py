r"""Set GOS's pre-launch and post-exit hooks on GTNH-Play (WO-1.4). Clayton runs this himself.

    GOS_APP\current\.venv\Scripts\python.exe GOS_APP\current\scripts\install_prism_hooks.py --dry-run
    ... install_prism_hooks.py [--instance play|lab] [--uninstall] [--replace]

Close Prism first: it keeps instance.cfg in memory and rewrites it. The hooks run
GOS_APP\scripts\prism_prelaunch.ps1 and prism_postexit.ps1 (copied there by gos deploy, D91).
``--instance lab`` sets the same hooks on GTNH-Lab, to test them there first. The old
instance.cfg is kept as instance.cfg.gos-backup-<time>. See gregtec.prism.
"""

from gregtec.prism import install_main

if __name__ == "__main__":
    raise SystemExit(install_main())

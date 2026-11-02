r"""Check (and with --apply, set) the plan 5.6 config tweaks on one Prism instance.

    uv run python scripts/check_tweaks.py C:\Games\PrismLauncher\instances\GTNH-Lab
    uv run python scripts/check_tweaks.py <instance> --apply

See gregtec.tweaks. Play is changed only by Clayton running this himself from GOS_APP.
"""

from gregtec.tweaks import main

if __name__ == "__main__":
    raise SystemExit(main())

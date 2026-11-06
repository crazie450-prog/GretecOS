r"""Mirror mods/, config/ and scripts/ from GTNH-Play to GTNH-Export and GTNH-Lab (plan 5.1).

    uv run python scripts/sync_instances.py --dry-run
    uv run python scripts/sync_instances.py [--only export|lab] [--config PATH]

See gregtec.sync. Play is only read; excludes come from [sync.export] and [sync.lab] in gos.toml.
Then, for Export only, it installs the GregtecLink jar that gos deploy built into GOS_APP
([paths] gos_app), checked against the SHA256 in its release.json, with the export module on (D98).
"""

from gregtec.sync import main

if __name__ == "__main__":
    raise SystemExit(main())

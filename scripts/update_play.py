r"""Update GTNH-Play from the Lab that an adopted pack update tested (WO-1.9). Clayton runs this.

    GOS_APP\current\.venv\Scripts\python.exe GOS_APP\current\scripts\update_play.py
    ... update_play.py --apply [--update ID] [--config PATH]

Close Play first. Without --apply it prints the plan and writes nothing. --apply needs the
update adopted (gos pack adopt yes) and its world backup step done, backs up every Play file it
replaces or deletes into the update's play-before\ folder, then mirrors Lab's mods\, config\ and
scripts\ into Play. See gregtec.packupdate.playupdate and docs/runbooks/pack_update.md.
"""

from gregtec.packupdate.playupdate import main

if __name__ == "__main__":
    raise SystemExit(main())

r"""Update GTNH-Play from the Lab that an adopted pack update tested (WO-1.9). Clayton runs this.

    GOS_APP\current\.venv\Scripts\python.exe GOS_APP\current\scripts\update_play.py
    ... update_play.py --apply [--update ID] [--config PATH]
    ... update_play.py --rollback [--update ID] [--config PATH]

Close Play first. Without --apply it prints the plan and writes nothing. --apply needs the
update open and adopted (gos pack adopt yes) and a world backup from the last 24 hours, backs
up every Play file it replaces or deletes into the update's play-before\ folder (with a
manifest of what it adds), then mirrors Lab's mods\, config\ and scripts\ into Play.
--rollback puts Play back from that folder. See gregtec.packupdate.playupdate and
docs/runbooks/pack_update.md.
"""

from gregtec.packupdate.playupdate import main

if __name__ == "__main__":
    raise SystemExit(main())

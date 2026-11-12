r"""Install GregtecLink with the listener and client modules into GTNH-Play (or Lab) (WO-8.1).

Clayton runs this himself from GOS_APP, with the game closed and the core running:

    GOS_APP\current\.venv\Scripts\python.exe GOS_APP\current\scripts\install_gregteclink.py
    ... install_gregteclink.py --apply [--instance play|lab] [--config PATH]

Without --apply it prints what it would do and writes nothing. See gregtec.gamelink.install
and docs/runbooks/gregteclink_client.md.
"""

from gregtec.gamelink.install import main

if __name__ == "__main__":
    raise SystemExit(main())

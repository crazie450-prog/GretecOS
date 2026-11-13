"""A real core for GregtecLink's client harness (WO-8.1, D287): ``-PharnessClientLink``.

``uv run python tests/harness/client_link_core.py --game mod/run/client --home <tmp>`` copies the
newest export run in ``<game>/gregteclink/exports`` into ``<home>`` and builds its DB, as ``gos
export collect`` and ``gos db build`` do (the harness DB: GT 5.09.54.265 and its addons from the
dev runtime), issues the ``gregteclink-lab`` token and writes it to
``<game>/gregteclink-harness/core.token`` (``mod/harness/config-client-link/gregteclink.cfg``
points the client module there), then runs ``gregtec.api.server.run`` on ``127.0.0.1:<port>``
(18765) in a child process, with fake lanes (no key, no cost). ``<home>/ready`` says it is up.

The game asks for a core restart by creating ``<game>/gregteclink-harness/restart-core``: the
child is stopped and started again on the same port and DB, so the harness can check that the
client module reconnects. Test-only: nothing here runs in a real instance, and the token is a
throwaway in a temp folder. Exits after ``--minutes`` or when stopped.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Sequence
from pathlib import Path

HERE = Path(__file__).resolve()
TOKEN_NAME = "gregteclink-lab"
LINK_DIR = "gregteclink-harness"
MARKER = ".gos-client-link-home"


def fresh_home(home: Path) -> None:
    """An empty home; only one this script made (its marker) or an empty folder is wiped."""
    if home.exists():
        if not home.is_dir() or not (
            (home / MARKER).is_file() or not any(home.iterdir())
        ):
            raise SystemExit(f"refusing to delete {home}: not a home this script made")
        shutil.rmtree(home)
    home.mkdir(parents=True)
    (home / MARKER).write_text("made by tests/harness/client_link_core.py\n", "utf-8")


def prepare(game: Path, home: Path) -> str:
    """The harness DB in ``home`` and a fresh token for the client module: the token."""
    from gregtec.api.security import Auth
    from gregtec.db import build
    from gregtec.ingest import collect
    from gregtec.store import DB_NAME, Store

    collect.collect(game, home, 1, allow_harness=True)
    result = build.build(home, allow_harness=True)
    print(f"client-link core: DB {result.db}", flush=True)
    store = Store(home / "data" / DB_NAME)
    try:
        return Auth(store, "x" * 32).issue_client_token(TOKEN_NAME)
    finally:
        store.close()


def serve(home: Path, port: int) -> int:
    """The child: the core, as ``gos serve --core-only`` runs it, with fake lanes."""
    from gregtec.agents import ask
    from gregtec.api import server
    from gregtec.config import Config
    from gregtec.store import Store

    def fake_agents(cfg: Config, store: Store) -> ask.Agents:
        return ask.setup(cfg, store=store, fake=True)

    cfg = Config(
        gos_home=home,
        core_port=port,
        supervisor_port=port + 1,
        dashboard_host="gos-harness.localhost",
    )
    return server.run(
        cfg, setup_agents=fake_agents, config_path=home / "config" / "gos.toml"
    )


def up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def start(home: Path, port: int) -> subprocess.Popen[bytes]:
    child = subprocess.Popen(
        [sys.executable, str(HERE), "--serve", "--home", str(home), "--port", str(port)]
    )
    end = time.monotonic() + 60
    while not up(port):
        if child.poll() is not None or time.monotonic() > end:
            raise SystemExit("client-link core: the core didn't start")
        time.sleep(0.2)
    return child


def stop(child: subprocess.Popen[bytes]) -> None:
    child.terminate()
    try:
        child.wait(15)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="client_link_core.py")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--game", type=Path)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    home: Path = args.home.absolute()
    if args.serve:
        return serve(home, args.port)
    if args.game is None:
        parser.error("--game is required")
    game: Path = args.game.absolute()
    fresh_home(home)
    token = prepare(game, home)
    link = game / LINK_DIR
    link.mkdir(exist_ok=True)
    request = link / "restart-core"
    request.unlink(missing_ok=True)
    (link / "core.token").write_text(token + "\n", "utf-8")
    child = start(home, args.port)
    (home / "ready").write_text(str(os.getpid()), "utf-8")
    print(f"client-link core: up on 127.0.0.1:{args.port}", flush=True)
    end = time.monotonic() + args.minutes * 60
    restarts = 0
    try:
        while time.monotonic() < end:
            if request.exists():
                stop(child)
                request.unlink(missing_ok=True)
                restarts += 1
                print(f"client-link core: restarting ({restarts})", flush=True)
                time.sleep(2)  # the game sees the link drop before the core is back
                child = start(home, args.port)
            elif child.poll() is not None:
                print(
                    "client-link core: the core exited; starting it again", flush=True
                )
                child = start(home, args.port)
            time.sleep(0.25)
    finally:
        stop(child)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

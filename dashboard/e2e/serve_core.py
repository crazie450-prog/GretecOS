"""A real core for the dashboard's end-to-end tests (WO-2.6), with fake lanes and a fake desktop.

``uv run python dashboard/e2e/serve_core.py --home <tmp> --port 8795`` builds the tool tests' own
made-up export run and quest book into ``<tmp>/data/gos.sqlite`` (tests/unit/tools/conftest.py),
then runs ``gregtec.api.server.run`` on ``gos-e2e.localhost:<port>`` with the fast lane on
:class:`gregtec.llm.fake.EchoClient` (no key, no cost) and the deep lane only recorded. It serves
``dashboard/dist`` (build it first), so Playwright tests the built dashboard with the core's
real Host/Origin checks, cookie and pairing.

What the dashboard reads from the desktop comes from files the tests write in ``<tmp>/e2e/``:
``foreground`` (the program in front, e.g. ``javaw.exe``: the core's focus signal), ``running``
(a JSON list of running GTNH roles, default none) and ``key`` (``missing``: no Anthropic key, so
"AI offline"); the core polls them every second. Test-only: production code never reads these
files. Its gos.toml marks setup done, so the shell shows.

Ores (WO-6.6, :func:`load_ores`): WO-1.6's made-up ore mixes and Lab world (tests/unit/test_ores.py)
are loaded into the same DB from a fake Lab instance in ``<tmp>/fixtures``, with Clayton standing
at x 100, z 20 in its ``level.dat`` and JourneyMap's data folder made, so the Ores view lists,
maps and proposes waypoints, and a confirmed one is written there (never a real instance).

Crops (WO-6.7, :func:`load_crops`): the crop guide's made-up world (tests/fixtures/crops) added
to the tool fixtures' export run, never as a newer run of its own: the recipe tools read only the
newest run, so a crop-only run would hide the e2e world's recipes.

``--fresh`` (WO-2.9's first run, :func:`setup_fresh`): no DB and no gos.toml; GOS_HOME is
``<tmp>/GregtecOS`` and the fake Prism tree, GOS_APP and keyring are in ``<tmp>/fixtures``.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DASHBOARD_HOST = "gos-e2e.localhost"
# Written into every home this script makes: only such a home (or an empty folder) is wiped, so a
# wrong --home or GOS_E2E_HOME can never delete a real folder.
MARKER = ".gos-e2e-home"


def _read(path: Path) -> str | None:
    try:
        return path.read_text("utf-8").strip()
    except OSError:
        return None


def fresh_home(home: Path) -> None:
    """Make ``home`` an empty e2e home, deleting the one an earlier run left there. Anything else
    already at that path (no marker, not an empty folder, a link) is refused."""
    if home.exists() or home.is_symlink():
        folder = home.is_dir() and not home.is_symlink()
        ours = folder and (home / MARKER).is_file()
        if not (ours or (folder and not any(home.iterdir()))):
            raise SystemExit(
                f"serve_core.py: refusing to delete {home}: it isn't an e2e home this script "
                f"made (no {MARKER} in it). Pick an empty or new folder."
            )
        shutil.rmtree(home)
    (home / "e2e").mkdir(parents=True)
    (home / MARKER).write_text(
        "made by dashboard/e2e/serve_core.py, which deletes it on its next run\n",
        "utf-8",
    )


def build_db(home: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "tool_fixtures", REPO / "tests" / "unit" / "tools" / "conftest.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("can't load the tool test fixtures")
    module = importlib.util.module_from_spec(spec)
    sys.modules["tool_fixtures"] = module
    spec.loader.exec_module(module)
    module.build_home(home)


def load_ores(home: Path) -> tuple[Path, str]:
    """WO-1.6's ore fixture loaded into ``home``'s DB from a fake Lab instance in
    ``home/fixtures``: (the instances folder, the Lab world's name)."""
    spec = importlib.util.spec_from_file_location(
        "ore_fixtures", REPO / "tests" / "unit" / "test_ore_intel.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("can't load the ore test fixtures")
    module = importlib.util.module_from_spec(spec)
    sys.modules["ore_fixtures"] = module
    spec.loader.exec_module(module)
    f = module.F  # tests/unit/test_ores.py

    from gregtec.db import access
    from gregtec.db import ores as ore_db
    from gregtec.ingest import ore_mixes

    instances = home / "fixtures" / "instances"
    lab = instances / "GTNH-Lab"
    game = lab / ".minecraft"
    (game / "saves").mkdir(parents=True)
    (lab / "instance.cfg").write_text("name=GTNH-Lab\n", encoding="utf-8")
    world = f.write_world(game)
    (world / "level.dat").write_bytes(module.level_dat(*module.POS))
    (game / "journeymap" / "data" / "sp" / f.WORLD).mkdir(parents=True)
    mixes = ore_mixes.parse(f.ore_mix_bytes())
    veins = ore_db.read_veins(world, game)
    conn = access.connect(access.db_path(home))
    try:
        ore_db.write(conn, "e2e-ores", mixes, veins)
    finally:
        conn.close()
    return instances, f.WORLD


def load_crops(home: Path) -> None:
    """WO-6.7's made-up crop world (tests/fixtures/crops/cropworld.py: crops, CropsNH's machines
    and a Crop Breeder recipe) added to the newest export run in ``home``'s DB (the tool
    fixtures'), so the recipe tools still answer from that run."""
    spec = importlib.util.spec_from_file_location(
        "crop_fixtures", REPO / "tests" / "fixtures" / "crops" / "cropworld.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("can't load the crop test fixtures")
    module = importlib.util.module_from_spec(spec)
    sys.modules["crop_fixtures"] = module
    spec.loader.exec_module(module)
    from gregtec.db import access

    module.write_db(access.db_path(home), into_latest=True)


def _load_fakes() -> object:
    """tests/unit/setup/fakes.py: the file keyring and the fake Prism tree (WO-2.9)."""
    spec = importlib.util.spec_from_file_location(
        "setup_fakes", REPO / "tests" / "unit" / "setup" / "fakes.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("can't load the setup test fakes")
    module = importlib.util.module_from_spec(spec)
    sys.modules["setup_fakes"] = module
    spec.loader.exec_module(module)
    return module


def setup_fresh(root: Path) -> Path:
    """``--fresh`` (WO-2.9): GOS_HOME is ``<root>/GregtecOS``, which doesn't exist yet (no
    gos.toml, no DB); ``<root>/fixtures`` holds a fake Prism tree with GTNH 2.9.0 Play, Export
    and Lab, an empty GOS_APP and the keyring the core writes to (``keyring.json``). The core's
    test call to the API gets a scripted answer. Returns GOS_HOME."""
    import keyring

    from gregtec.agents import fast_lane
    from gregtec.llm.fake import ScriptedClient, Turn

    fakes = _load_fakes()
    fixtures = root / "fixtures"
    fakes.make_prism(fixtures / "PrismLauncher")  # type: ignore[attr-defined]
    (fixtures / "GregtecOS-app").mkdir()
    keyring.set_keyring(fakes.FileKeyring(fixtures / "keyring.json"))  # type: ignore[attr-defined]
    fast_lane.client_for = lambda key: ScriptedClient(turns=[Turn(text="ok")])  # type: ignore[assignment,misc]
    return root / "GregtecOS"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="serve_core.py")
    parser.add_argument(
        "--home",
        type=Path,
        required=True,
        help="a new or empty folder, or one an earlier run made (it is wiped)",
    )
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="a first run (WO-2.9): no gos.toml and no DB; see setup_fresh",
    )
    parser.add_argument("--host-name", default=DASHBOARD_HOST)
    args = parser.parse_args(argv)
    root: Path = args.home.absolute()
    fresh_home(root)
    ores_lab: tuple[Path, str] | None = None
    if args.fresh:
        home = setup_fresh(root)
    else:
        home = root
        build_db(home)
        ores_lab = load_ores(home)
        load_crops(home)
        # The vault from the template, so the Designer's accept writes its notes (WO-3.7).
        from gregtec.vault import deploy as vault_deploy

        vault_deploy.deploy(home / "vault", REPO / "vault-template")
        # Setup is done here (the wizard's own e2e uses --fresh), so the shell shows.
        (home / "config").mkdir()
        (home / "config" / "gos.toml").write_text(
            '[setup]\ncompleted_utc = "2026-11-11T00:00:00+00:00"\n', "utf-8"
        )

    from gregtec import desktop, prism
    from gregtec.agents import ask
    from gregtec.alerts.rules import AlertRules
    from gregtec.api import app as core_app
    from gregtec.api import dashboard, server
    from gregtec.config import Config
    from gregtec.llm import keys
    from gregtec.store import Store

    controls = root / "e2e"

    def foreground() -> str | None:
        return _read(controls / "foreground") or None

    def running(cfg: Config, lines: Sequence[str] | None = None) -> list[str]:
        found = json.loads(_read(controls / "running") or "[]")
        return [str(role) for role in found]

    def has_key(name: str) -> bool:
        return _read(controls / "key") != "missing"

    desktop.foreground_program = foreground
    prism.running_clients = running
    if not args.fresh:  # --fresh: the real check, against the file keyring
        keys.has_key = has_key
    # Poll every second, so a test sees a change at once (the core waits 30 s and 5 s).
    core_app.KEY_CHECK_S = 1.0
    dashboard.GAME_EVERY_S = 1.0
    dashboard.DATA_EVERY_S = 1.0

    def fake_agents(cfg: Config, store: Store) -> ask.Agents:
        return ask.setup(cfg, store=store, fake=True)

    cfg = Config(
        gos_home=home,
        core_port=args.port,
        supervisor_port=args.port + 1,
        dashboard_host=args.host_name,
        # WO-7.5: a low store alerts after 5 s, not 30, so oc_displays.spec.ts needn't wait.
        alerts=AlertRules(power_low_s=5),
    )
    if (
        ores_lab is not None
    ):  # the Ores view's Lab (WO-6.6): the fake instance load_ores made
        cfg = dataclasses.replace(
            cfg, prism_instances=ores_lab[0], lab_world=ores_lab[1]
        )
    print(
        f"e2e core on http://{args.host_name}:{args.port}/ (GOS_HOME {home})",
        flush=True,
    )
    return server.run(
        cfg,
        setup_agents=fake_agents,
        config_path=home / "config" / "gos.toml",
    )


if __name__ == "__main__":
    raise SystemExit(main())

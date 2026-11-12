"""The quest guide's fixture pack (WO-6.4): a made-up GOS_HOME for ``gos eval quest-guide`` and
the "L0 never spoils" tests.

The recipe export run is the tool tests' own (tests/unit/tools/conftest.py: GT-like names, never
pack data); the quest book here is self-authored too, BetterQuesting-shaped, in three lines:

- **Stone Age**: 1 First Steps (done, main); 2 Steel Age and 3 Oxygen Run (available);
  4 Stainless Dreams (locked, shown: the book previews it); 5 Secret Alloy, 6 Hidden Cache
  (HIDDEN) and 7 Ashes to Ashes (locked, not shown).
- **Steam**: 10 Logs (done, main); 11 Bricks, 12 Charcoal Pile and 13 Either Way (an OR quest,
  available); 14 Blast Off (locked, shown); 15 Pressure Cooker (locked, not shown).
- **Tier 1 - LV**: 20 Welcome to LV (SECRET), 21 Macerate Everything, 22 EBF Time (locked).

Clayton has done 1 and 10. His goals (``me/goals.md``): the Electric Blast Furnace (quest 22,
five steps past Bricks), then oxygen (quest 3). One saved plan is accepted: a steel ingot line
(quest 2 asks for steel ingots). Every locked quest has a description and items no quest in
reach has, so a spoiler shows up in a string search. :func:`build` writes it all through the
real loaders, deploys the vault and writes the questline summaries.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.ingest import quests as bq
from gregtec.quests import summaries
from gregtec.vault import deploy, fts, index

REPO = Path(__file__).resolve().parents[2]
CLAYTON = "11111111-2222-4333-8444-555555555555"
GT = "gregtech:gt.metaitem.01"
MACHINES = "gregtech:gt.blockmachines"
GOALS = ("Electric Blast Furnace", "get some oxygen")
PLAN = ("pl-steel", "Steel ingots", f"{GT}@11305")  # id, name, target: an accepted plan
DONE = (1, 10)


def tools() -> ModuleType:
    """tests/unit/tools/conftest.py (its export run and BetterQuesting helpers)."""
    name = "tool_fixtures"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "tests" / "unit" / "tools" / "conftest.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("can't load the tool test fixtures")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def qid(n: int) -> str:
    return bq.encode_id(0, n)


def _quest(
    t: ModuleType,
    n: int,
    name: str,
    prereqs: tuple[int, ...] = (),
    *,
    tasks: list[dict[str, Any]] | None = None,
    rewards: list[dict[str, Any]] | None = None,
    desc: str = "",
    main: bool = False,
    visibility: str = "NORMAL",
    logic: str = "AND",
) -> dict[str, Any]:
    q: dict[str, Any] = t.quest(
        n,
        name,
        prereqs,
        tasks=tasks if tasks is not None else [t.CHECKBOX],
        rewards=rewards or [],
        desc=desc,
        main=main,
    )
    props = q["properties:10"]["betterquesting:10"]
    props["visibility:8"] = visibility
    props["questLogic:8"] = logic
    return q


def quests(t: ModuleType) -> list[dict[str, Any]]:
    r, s = t.retrieval, t.bq_stack
    oxygen = {
        **t.OXYGEN,
        "requiredFluids:9": t.typed_list([{"Amount:3": 1000, "FluidName:8": "oxygen"}]),
    }
    return [
        _quest(t, 1, "First Steps", main=True, desc="Look around."),
        _quest(t, 2, "Steel Age", (1,), tasks=[r(s(GT, 11305, 16), consume=True)],
               rewards=[t.item_reward(s(GT, 2305, 4))], desc="Make some steel.", main=True),
        _quest(t, 3, "Oxygen Run", (1,), tasks=[oxygen], desc="Bottle some air."),
        _quest(t, 4, "Stainless Dreams", (2,), tasks=[r(s(GT, 2306, 8))],
               desc="Dreams of a shinier alloy."),
        _quest(t, 5, "Secret Alloy", (4,), tasks=[r(s(GT, 11306, 8))],
               rewards=[t.item_reward(s(GT, 17306, 2))],
               desc="The secret is a chromium-rich alloy."),
        _quest(t, 6, "Hidden Cache", (5,), rewards=[t.item_reward(s(GT, 2032, 7))],
               desc="A cache only the worthy find.", visibility="HIDDEN"),
        _quest(t, 7, "Ashes to Ashes", (5,), tasks=[r(s(GT, 2815, 3))],
               desc="Burn it all down to grey powder."),
        _quest(t, 10, "Logs", (1,), main=True, desc="Punch a tree."),
        _quest(t, 11, "Bricks", (10,), tasks=[r(s(MACHINES, 140, 1))],
               desc="Build a bricked blast furnace."),
        _quest(t, 12, "Charcoal Pile", (10,), tasks=[r(s("minecraft:coal", 1, 32), consume=True)],
               desc="Smoulder some logs."),
        _quest(t, 13, "Either Way", (10, 3), desc="Any road leads here.", logic="OR"),
        _quest(t, 14, "Blast Off", (11,), tasks=[r(s(GT, 17305, 4))],
               desc="Plates for the furnace walls."),
        _quest(t, 15, "Pressure Cooker", (14,), tasks=[r(s(MACHINES, 31, 1))],
               rewards=[t.item_reward(s(MACHINES, 1000, 1))],
               desc="Pressure makes diamonds, they say."),
        _quest(t, 20, "Welcome to LV", (15,), desc="The age of electricity begins.",
               visibility="SECRET"),
        _quest(t, 21, "Macerate Everything", (20,), tasks=[r(s(MACHINES, 31, 2))],
               desc="Grind ores into dust for double output."),
        _quest(t, 22, "EBF Time", (21,), tasks=[r(s(MACHINES, 1000, 1))],
               rewards=[t.item_reward(s(GT, 11306, 16))],
               desc="The furnace that changes everything."),
    ]  # fmt: skip


LINES = [
    ("Stone Age", [(1, 0, 0), (2, 24, 0), (3, 24, 24), (4, 48, 0), (5, 72, 0), (6, 96, 0),
                   (7, 96, 24)]),
    ("Steam", [(10, 0, 0), (11, 24, 0), (12, 24, 24), (13, 24, 48), (14, 48, 0),
               (15, 72, 0)]),
    ("Tier 1 - LV", [(20, 0, 0), (21, 24, 0), (22, 48, 0)]),
]  # fmt: skip


def write_world(world: Path) -> Path:
    t = tools()
    folder = world / bq.WORLD_DIR
    folder.mkdir(parents=True)
    lines = []
    for n, (name, entries) in enumerate(LINES):
        lines.append(
            {
                "properties:10": {
                    "betterquesting:10": {
                        "desc:8": "",
                        "name:8": name,
                        "visibility:8": "NORMAL",
                    }
                },
                **t.ident(n, "questLineID"),
                "order:3": n,
                "quests:9": t.typed_list(
                    {**t.ident(q), "sizeX:3": 24, "sizeY:3": 24, "x:3": x, "y:3": y}
                    for q, x, y in entries
                ),
            }
        )
    database = {
        "build:8": "3.8.92-GTNH",
        "format:8": "3.1.0",
        "questDatabase:9": t.typed_list(quests(t)),
        "questLines:9": t.typed_list(lines),
        "questSettings:10": {
            "betterquesting:10": {
                "pack_name:8": "Testpack",
                "pack_version:3": 1,
                "editMode:1": 0,
            }
        },
    }
    (folder / bq.DATABASE).write_text(json.dumps(database), encoding="utf-8")
    names = {
        "nameCache:9": t.typed_list(
            [{"isOP:1": 0, "name:8": "Clayton", "uuid:8": CLAYTON}]
        )
    }
    (folder / bq.NAME_CACHE).write_text(json.dumps(names), encoding="utf-8")
    done = [
        {
            **t.ident(n),
            "completed:9": t.typed_list(
                [{"claimed:1": 1, "timestamp:4": 1_700_000_000_000, "uuid:8": CLAYTON}]
            ),
            "tasks:9": {},
        }
        for n in DONE
    ]
    progress = folder / bq.PROGRESS_DIR / f"{CLAYTON}.json"
    progress.parent.mkdir()
    progress.write_text(
        json.dumps({"questProgress:9": t.typed_list(done)}), encoding="utf-8"
    )
    return world


def build(home: Path, *, goals: bool = True, plan: bool = True) -> Path:
    """GOS_HOME with the export run, this quest book, a deployed vault (with ``me/goals.md``
    and the questline summaries) and an accepted plan; returns the DB's path."""
    t = tools()
    t.make_run(home)
    from gregtec.db import build as db_build

    db_build.build(home)
    db = access.db_path(home)
    loaded = quest_db.read(write_world(home / "world"), None)
    conn = access.connect(db)
    try:
        quest_db.write(conn, loaded)
        if plan:
            conn.execute(
                "INSERT INTO plan(id, name, created, kind, target, status, json_path)"
                " VALUES (?, ?, 1, 'production', ?, 'accepted', '')",
                PLAN,
            )
            conn.commit()
    finally:
        conn.close()
    vault = home / "vault"
    deploy.deploy(vault)
    (vault / "glossary.tsv").write_text(t.GLOSSARY, encoding="utf-8")
    if goals:
        (vault / "me" / "goals.md").write_text(
            "---\nid: me-goals\nt: goals\nupd: 2026-11-12\n---\n"
            + "".join(f"goal: {g}\n" for g in GOALS),
            encoding="utf-8",
        )
    summaries.refresh_home(home)
    index.build(vault, dt.date(2026, 11, 12))
    fts.mirror_all(db, vault)
    return db

"""G3's benchmark lines (plan §8 G3, D65) against NEI, on real harness runs (G3 review, D254).

For each G3 line the dev runtime has, the designer's best route is designed on the server
harness DB, and every distinct (machine, recipe, tier, OC, parallel) of it is listed. Each
recipe is then checked against the line NEI 2.8.172 dumped for it in the client harness run,
with G1's check (``test_golden_recipes``): EU/t, duration, every amount and every chance must be
equal. GT recipe keys are the same in both runs (``test_db_harness``). NEI also shows a recipe's
unconsumed special item (a Bacterial Vat's culture, a replicator's data orb) as a slot; the DB
keeps it in ``recipe_meta.special_items``, so a stack NEI shows that equals that item is left out
of the pairing.

The OC and parallel half needs no NEI: ``gtmath`` equals GT's own classes bit for bit
(``tests/unit/gtmath``, D174), and line totals are checked against gtnhplanner.com by
``dashboard/e2e/gtnhplanner-import.cjs`` (it needs the internet; G3 review). Nanocircuits and
wetware circuits are not in the dev runtime (GTNH's circuit recipes are in its core mod), so
they are a PC step, as is the Lab run that decides when references disagree [QA:D12].

Runs only with GOS_REQUIRE_HARNESS_RUN=1 after both harnesses (CI's mod job; locally
mod/README.md). On a real GOS_HOME, ``uv run python tests/integration/test_g3_lines_harness.py
report <GOS_HOME>`` checks the saved plans in ``data/plans/`` against that home's own NEI dump
and prints one TSV row per distinct tuple (the Lab sheet).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import plan
from gregtec.planner.request import DefaultBaseInputs, Request

REPO = Path(__file__).resolve().parents[2]


def _load_golden() -> ModuleType:
    """G1's check, test_golden_recipes.py (pytest imports test files by path, not by name)."""
    name = "_g3_golden_recipes"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name("test_golden_recipes.py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclasses look their module up while being made
    spec.loader.exec_module(module)
    return module


golden = _load_golden()
# Target key and tier cap per line, as in test_plan_harness.py and test_rank_harness.py.
LINES = {
    "steel (EBF)": ("gregtech:gt.metaitem.01@11305", 2),
    "polyethylene": ("fluid:molten.plastic", 2),
    "sulfuric acid": ("fluid:sulfuricacid", 2),
    "silicon": ("gregtech:gt.metaitem.03@32030", 3),
    "rubber": ("gregtech:gt.metaitem.01@17880", 2),
    "platline": ("gregtech:gt.metaitem.01@2085", 5),
    "rare earth": ("gregtech:gt.metaitem.01@2067", 6),
    "epoxid": ("fluid:molten.epoxid", 4),
    "stainless steel": ("gregtech:gt.metaitem.01@11306", 3),
    "nitric acid": ("fluid:nitricacid", 3),
    "polybenzimidazole": ("fluid:molten.polybenzimidazole", 5),
    "naquadah": ("gregtech:gt.metaitem.01@2324", 7),
    "light fuel (G4 fuel chain)": ("fluid:liquid_light_fuel", 2),
}
COLUMNS = ("machine", "recipe", "tier", "oc", "parallel", "nei", "why", "plans")

Tuple = tuple[str, str, str, int, int]


def tuples(plans: Iterable[dict[str, Any]]) -> dict[Tuple, list[str]]:
    """Every distinct (machine, recipe, tier, OC, parallel) of the plans, with their names."""
    out: dict[Tuple, list[str]] = {}
    for p in plans:
        for n in p["nodes"]:
            m = n["machine"]
            if not m:
                continue
            key = (m["name"], n["recipe"], str(m["tier"]), n["oc"], n["parallel"])
            out.setdefault(key, []).append(p.get("name") or p["id"])
    return out


def special_items(conn: sqlite3.Connection, run_id: int, ref: str) -> set[str]:
    """The ``registry@meta`` keys of a recipe's unconsumed special items (recipe_meta)."""
    out: set[str] = set()
    for (text,) in conn.execute(
        "SELECT m.value_text FROM recipe r JOIN recipe_meta m ON m.recipe_id = r.id "
        "WHERE r.run_id = ? AND r.ref = ? AND m.key = 'special_items'",
        (run_id, ref),
    ):
        for s in _json_list(text):
            if isinstance(s, dict) and "item" in s:
                out.add(f"{s['item']}@{s.get('meta', 0)}")
    return out


def _json_list(text: str | None) -> list[Any]:
    v = json.loads(text) if text else []
    return v if isinstance(v, list) else [v]


def check_recipes(
    conn: sqlite3.Connection, run_dir: Path, run_id: int, refs: Iterable[str]
) -> dict[str, golden.Verdict]:
    """Each recipe against NEI's lines of its handler (G1's check, special items aside)."""
    refs = sorted(set(refs))
    sides = {r: golden.db_side(conn, run_id, r) for r in refs}
    handlers = {s.handler for s in sides.values() if s is not None}
    lines = golden.nei_lines(run_dir, handlers)
    out: dict[str, golden.Verdict] = {}
    for r in refs:
        side = sides[r]
        shown = lines.get(side.handler, []) if side else []
        special = special_items(conn, run_id, r)
        if special:
            shown = [
                replace(n, inputs=[s for s in n.inputs if s.key not in special])
                for n in shown
            ]
        out[r] = golden.check(r, r, side, shown)
    return out


def rows(
    found: dict[Tuple, list[str]], verdicts: dict[str, golden.Verdict]
) -> Iterator[list[str]]:
    for key, names in sorted(found.items()):
        v = verdicts[key[1]]
        yield [
            *map(str, key),
            "ok" if v.ok else "DIFF",
            v.why,
            ", ".join(sorted(set(names))),
        ]


# --- on the harness runs -----------------------------------------------------------------


def _home(tmp: Path, side: str) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running both harnesses")
    game = REPO / "mod" / "run" / side
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(
            f"no {side} harness run in {game / collect.EXPORTS} (mod/README.md)"
        )
    collect.collect(game, tmp, 1, allow_harness=True)
    build.build(tmp, allow_harness=True)
    return tmp


@pytest.fixture(scope="module")
def plans(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    home = _home(tmp_path_factory.mktemp("g3-server"), "server")
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        base = DefaultBaseInputs.from_db(conn)
        out = []
        for key, tier in LINES.values():
            d = plan.design(conn, Request(key, qty=1, tier_cap=tier), base)
            assert d.plans, key
            out.append(d.plans[0])
        return out
    finally:
        conn.close()


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _home(tmp_path_factory.mktemp("g3-client"), "client")


def test_every_g3_line_recipe_matches_its_nei_line(
    plans: list[dict[str, Any]], client: Path
) -> None:
    found = tuples(plans)
    conn, run_dir, run_id = golden._open(client)
    try:
        verdicts = check_recipes(conn, run_dir, run_id, (k[1] for k in found))
    finally:
        conn.close()
    assert len(plans) == len(LINES) and all(p["route"]["all_run"] for p in plans)
    assert len(found) >= len(LINES)
    bad = [v for v in verdicts.values() if not v.ok]
    assert not bad, golden.report(bad)


def test_an_nei_slot_is_left_out_only_when_it_is_the_special_item() -> None:
    db = golden.Side(
        "h", 30, 100, [golden.Stack("fluid:uu", 40)], [golden.Stack("a@1", 1)]
    )
    nei = golden.Side(
        "h",
        30,
        100,
        [golden.Stack("orb@7", 1), golden.Stack("fluid:uu", 40)],
        db.outputs,
    )
    assert not golden.check("x", "x", db, [nei]).ok
    kept = replace(nei, inputs=[s for s in nei.inputs if s.key not in {"orb@7"}])
    assert golden.check("x", "x", db, [kept]).ok
    other = replace(nei, inputs=[s for s in nei.inputs if s.key not in {"cell@1"}])
    assert not golden.check("x", "x", db, [other]).ok


# --- on a real GOS_HOME ----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("action", choices=("report",))
    p.add_argument("home", type=Path, help="a GOS_HOME with saved plans and its run")
    a = p.parse_args(argv)
    saved = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted((a.home / "data" / "plans").glob("*.json"))
    ]
    found = tuples(x for x in saved if isinstance(x, dict) and "nodes" in x)
    conn, run_dir, run_id = golden._open(a.home)
    try:
        verdicts = check_recipes(conn, run_dir, run_id, (k[1] for k in found))
    finally:
        conn.close()
    print("\t".join(COLUMNS))
    for row in rows(found, verdicts):
        print("\t".join(row))
    return 0 if all(v.ok for v in verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

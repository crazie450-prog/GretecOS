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
and prints one TSV row per distinct tuple (the Lab sheet). It fails when the home has no saved
plans, or no saved plan for one of the lines below (a wrong GOS_HOME, or a line step 3 didn't save).
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import io
import json
import math
import os
import sqlite3
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest

from gregtec.db import access
from gregtec.ingest import collect
from gregtec.planner import plan, solve
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


def drop_special(lines: Iterable[Any], special: set[str]) -> list[Any]:
    """NEI's lines with every input stack equal to one of the recipe's special items left out."""
    return [
        replace(n, inputs=[s for s in n.inputs if s.key not in special]) for n in lines
    ]


def missing_lines(saved: Iterable[dict[str, Any]]) -> list[str]:
    """The G3 lines (``LINES``) that no saved plan has as its target."""
    targets = {(p.get("target") or {}).get("id") for p in saved}
    return [name for name, (key, _) in LINES.items() if key not in targets]


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
            shown = drop_special(shown, special)
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


def _home(tmp: Path, side: str, built_home: Callable[[Path, Path], Path]) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running both harnesses")
    game = REPO / "mod" / "run" / side
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(
            f"no {side} harness run in {game / collect.EXPORTS} (mod/README.md)"
        )
    built_home(game, tmp)
    return tmp


Stages = tuple[np.ndarray, tuple[float, float, float]]


def _lexicographic_v1(m: solve._Model, strict: bool = True) -> Stages | None:
    """The solver's stages 1-3 as WO-3.4 wrote them, before WO-4.7's EU stage (D323), frozen
    here verbatim for the differential check below."""
    gates, quantity, machines = solve._objectives(m)

    def result(x: np.ndarray) -> Stages:
        return x, (
            solve._value(gates, x),
            solve._value(quantity, x),
            solve._value(machines, x),
        )

    x = solve._solve(m, gates, [])
    if x is None:
        return None
    n1 = sum(solve._open_gates(m, x).values())
    x2 = None
    for n in range(int(n1), len(m.gate) + 1):
        x2 = solve._solve(m, quantity, [(gates, -math.inf, n + 0.5)])
        if x2 is not None:
            break
    if x2 is None:
        return None if strict else result(x)
    for k, value in solve._open_gates(m, x2).items():
        m.lb[m.gate[k]] = m.ub[m.gate[k]] = value
    x2b = solve._solve(m, quantity, [])
    if x2b is None:
        return None if strict else result(x2)
    q = solve._value(quantity, x2b)
    x3 = solve._solve(m, machines, [solve._keep(quantity, q)])
    if x3 is None:
        return None if strict else result(x2b)
    return result(x3)


# Every stage call made while the G3 lines are designed: (EU costed, same as before WO-4.7).
DIFFERENTIAL: list[tuple[bool, bool]] = []


def _differential(
    m: solve._Model, strict: bool = True
) -> tuple[np.ndarray, solve.Objectives] | None:
    """The solver's stages, and the frozen pre-WO-4.7 ones on a copy of the same model."""
    before = copy.deepcopy(m)
    new = _CURRENT(m, strict)
    old = _lexicographic_v1(before, strict)
    same = (new is None) == (old is None)
    if same and new is not None and old is not None:
        same = (
            np.array_equal(new[0], old[0])
            and new[1][:3] == old[1]
            and new[1][3] is None
            and (m.lb, m.ub) == (before.lb, before.ub)
        )
    DIFFERENTIAL.append((m.net.costs_eu, same))
    return new


_CURRENT = solve._lexicographic


@pytest.fixture(scope="module")
def plans(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> list[dict[str, Any]]:
    """The lines' best routes, designed with the differential check on every stage call
    (``test_production_lines_solve_exactly_as_before_the_eu_stage``)."""
    home = _home(tmp_path_factory.mktemp("g3-server"), "server", built_home)
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        base = DefaultBaseInputs.from_db(conn)
        out = []
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(solve, "_lexicographic", _differential)
            for key, tier in LINES.values():
                d = plan.design(conn, Request(key, qty=1, tier_cap=tier), base)
                assert d.plans, key
                out.append(d.plans[0])
        return out
    finally:
        conn.close()


def test_production_lines_solve_exactly_as_before_the_eu_stage(
    plans: list[dict[str, Any]],
) -> None:
    """WO-4.7 changed the staged MILP for power networks only (EU banned, D323): every G3
    line, its 3 routes and their replays, solves to the same vector and objectives as the
    frozen pre-WO-4.7 stages, and no production network costs EU."""
    assert len(DIFFERENTIAL) >= len(LINES)
    assert not any(eu for eu, _ in DIFFERENTIAL)
    assert all(same for _, same in DIFFERENTIAL)


@pytest.fixture(scope="module")
def client(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    return _home(tmp_path_factory.mktemp("g3-client"), "client", built_home)


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
    assert golden.check("x", "x", db, drop_special([nei], {"orb@7"})).ok
    assert not golden.check("x", "x", db, drop_special([nei], {"cell@1"})).ok
    # a special item the recipe also consumes is still paired, so it still shows as a DIFF
    consumed = replace(db, inputs=[golden.Stack("orb@7", 1), *db.inputs])
    assert not golden.check("x", "x", consumed, drop_special([nei], {"orb@7"})).ok


def test_report_fails_on_a_home_with_no_saved_plans(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "data" / "plans").mkdir(parents=True)
    assert main(["report", str(tmp_path)]) == 1
    assert "no saved plans" in capsys.readouterr().err


def test_report_names_the_lines_with_no_saved_plan() -> None:
    keys = [key for key, _ in LINES.values()]
    saved = [{"target": {"id": k}} for k in keys[1:]] + [{"nodes": []}]
    assert missing_lines(saved) == [next(iter(LINES))]
    assert missing_lines([{"target": {"id": k}} for k in keys]) == []


# --- on a real GOS_HOME ----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("action", choices=("report",))
    p.add_argument("home", type=Path, help="a GOS_HOME with saved plans and its run")
    a = p.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(
            encoding="utf-8"
        )  # plan names hold "·"; Windows redirects in ANSI
    plans_dir = a.home / "data" / "plans"
    loaded = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(plans_dir.glob("*.json"))
    ]
    saved = [x for x in loaded if isinstance(x, dict) and "nodes" in x]
    if not saved:
        print(f"no saved plans in {plans_dir}", file=sys.stderr)
        return 1
    missing = missing_lines(saved)
    for name in missing:
        print(f"no saved plan for {name} ({LINES[name][0]})", file=sys.stderr)
    found = tuples(saved)
    conn, run_dir, run_id = golden._open(a.home)
    try:
        verdicts = check_recipes(conn, run_dir, run_id, (k[1] for k in found))
    finally:
        conn.close()
    print("\t".join(COLUMNS))
    for row in rows(found, verdicts):
        print("\t".join(row))
    return 0 if not missing and all(v.ok for v in verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

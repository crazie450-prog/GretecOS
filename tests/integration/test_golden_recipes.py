"""G1's 30 golden recipes, checked against NEI's own handler dump of the same export run.

The golden set (``tests/fixtures/golden/g1_recipes.tsv``) holds DB keys only: each row is a
citation key (``r:<map>/<sig12>``), the NEI handler and the first output's registry key, never a
game number. The numbers come from the run itself on both sides: the recipe as ``gos db build``
stored it (``recipe``, ``recipe_input``, ``recipe_output``) and the line NEI 2.8.172 wrote for it
in the run's ``nei/`` files. The check reads NEI's JSON on its own (it doesn't reuse WO-1.3's
merge), finds the NEI line whose stacks pair up with the recipe's slots, and compares EU/t,
duration, every amount (items and mB) and every chance. A slot pairs with an NEI stack when the
stack shows, or lists in ``anyOf``, an item the slot accepts (its key, its ``recipe_input_alt``
stacks, or a member of its oredict name; meta 32767 is any meta). NBT is not compared: NEI's
``tag`` holds numeric IDs, and the keys' NBT hashes are WO-1.3's own tested code. What NEI's dump
doesn't carry (coil heat, cleanroom, amperage) is the Lab half, ``docs/runbooks/g1_golden_lab.md``.

Selection rule (``select``; G1 review, D222): from the run's recipes that are enabled, not hidden,
not fake, not GT recycling, and in a recipe map GT registers with NEI (or an NEI-only handler),
taken in citation-key order, each pick from a map or handler not picked yet while one is left:

- 9 GT recipes, one per voltage tier ULV to UV (0 to 8);
- 5 from maps that only multiblocks run;
- 3 with both a fluid input and a fluid output;
- 3 with a chanced output;
- 3 with an oredict input slot;
- 4 from maps owned by a mod other than GT (addon machines in the dev runtime: bartworks,
  GoodGenerator, gtnhlanth, kubatech, tectech, cropsnh), one per mod;
- 3 from NEI's own handlers: shaped crafting, shapeless crafting, furnace.

Running the check:

- in CI's mod job and locally after the client harness (mod/README.md):
  ``GOS_REQUIRE_HARNESS_RUN=1 uv run pytest tests/integration/test_golden_recipes.py``;
- on any GOS_HOME with a built DB and its collected run (gos-dev after ``gos db build``):
  ``GOS_GOLDEN_HOME=<GOS_HOME> uv run pytest tests/integration/test_golden_recipes.py``, or
  ``uv run python tests/integration/test_golden_recipes.py report <GOS_HOME>`` for the full
  report (``--reselect`` applies the rule to that DB instead of the fixture, for a full-pack DB
  whose keys differ from the dev runtime's);
- ``uv run python tests/integration/test_golden_recipes.py select <GOS_HOME>`` prints the
  fixture the rule picks from that DB (how the committed one was made);
- ``... show <GOS_HOME> [--reselect]`` prints each golden recipe as the DB has it, with the
  values NEI's dump doesn't carry, for the Lab checklist.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gregtec.ingest import collect
from gregtec.ingest.normalize import typed_int

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "golden" / "g1_recipes.tsv"
COLUMNS = ("id", "stratum", "ref", "handler", "output", "what")
FULL = 10000
WILDCARD = 32767
NEI_CRAFTING = {
    "shaped": "codechicken.nei.recipe.ShapedRecipeHandler",
    "shapeless": "codechicken.nei.recipe.ShapelessRecipeHandler",
    "furnace": "codechicken.nei.recipe.FurnaceRecipeHandler",
}


# --- the two sides -----------------------------------------------------------------------


@dataclass(frozen=True)
class Stack:
    """One slot: its shown key, what it accepts, amount (items or mB) and chance (basis points)."""

    key: str
    amount: int
    chance: int = FULL
    accepts: frozenset[str] = frozenset()


@dataclass
class Side:
    """A recipe as one source shows it."""

    handler: str
    eut: int | None
    duration: int | None
    inputs: list[Stack]
    outputs: list[Stack]
    name: str = ""
    raw: str = ""
    from_meta: list[str] = field(default_factory=list)  # slots read from recipe_meta


@dataclass
class Verdict:
    golden_id: str
    ref: str
    ok: bool
    why: str
    db: Side | None = None
    nei: Side | None = None
    diffs: list[str] = field(default_factory=list)


def _same_item(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.startswith("fluid:") or b.startswith("fluid:"):
        return False
    ra, _, ma = a.rpartition("@")
    rb, _, mb = b.rpartition("@")
    return ra == rb and str(WILDCARD) in (ma, mb)


def _compatible(db: Stack, nei: Stack) -> bool:
    return any(
        _same_item(a, b) for a in db.accepts | {db.key} for b in nei.accepts | {nei.key}
    )


def db_side(conn: sqlite3.Connection, run_id: int, ref: str) -> Side | None:
    """The recipe ``ref`` of run ``run_id`` as the DB holds it, or None."""
    row = conn.execute(
        "SELECT r.id, r.source, r.category, r.eut, r.duration_ticks, h.handler_name"
        " FROM recipe AS r LEFT JOIN nei_handler AS h ON h.id = r.nei_handler_id"
        " WHERE r.run_id = ? AND r.ref = ?",
        (run_id, ref),
    ).fetchone()
    if row is None:
        return None
    rid, source, category, eut, duration, handler = row
    key_sql = (
        "CASE WHEN s.fluid_id IS NOT NULL THEN 'fluid:' || f.name"
        " ELSE i.registry_name || '@' || i.meta END"
    )
    inputs: list[Stack] = []
    for slot, key, amount, chance, ore_id in conn.execute(
        f"SELECT s.slot, {key_sql}, s.amount, s.chance_bp, s.ore_id FROM recipe_input AS s"
        " LEFT JOIN item AS i ON i.id = s.item_id LEFT JOIN fluid AS f ON f.id = s.fluid_id"
        " WHERE s.recipe_id = ? ORDER BY s.slot",
        (rid,),
    ).fetchall():
        accepts = {
            k
            for (k,) in conn.execute(
                f"SELECT {key_sql} FROM recipe_input_alt AS s"
                " LEFT JOIN item AS i ON i.id = s.item_id LEFT JOIN fluid AS f ON f.id = s.fluid_id"
                " WHERE s.recipe_id = ? AND s.slot = ?",
                (rid, slot),
            )
        }
        if ore_id is not None:
            accepts |= {
                f"{reg}@{meta}"
                for reg, meta in conn.execute(
                    "SELECT i.registry_name, i.meta FROM ore_member AS m"
                    " JOIN item AS i ON i.id = m.item_id WHERE m.ore_id = ?",
                    (ore_id,),
                )
            }
        inputs.append(Stack(key, amount, chance, frozenset(accepts)))
    outputs = [
        Stack(key, amount, chance)
        for key, amount, chance in conn.execute(
            f"SELECT {key_sql}, s.amount, s.chance_bp FROM recipe_output AS s"
            " LEFT JOIN item AS i ON i.id = s.item_id LEFT JOIN fluid AS f ON f.id = s.fluid_id"
            " WHERE s.recipe_id = ? ORDER BY s.slot",
            (rid,),
        )
    ]
    from_meta: list[str] = []
    for key, text in conn.execute(
        "SELECT key, value_text FROM recipe_meta WHERE recipe_id = ? AND key IN (?, ?)",
        (rid, *META_INPUTS),
    ):
        extra = meta_inputs(key, json.loads(text))
        inputs.extend(extra)
        from_meta.extend(f"{s.key} ({key})" for s in extra)
    # NEI's handler id for a GT recipe is its category (D104); for the rest, the handler.
    want = category if source in ("gt", "gt+nei") else handler
    return Side(str(want), eut, duration, inputs, outputs, from_meta=from_meta)


# Inputs GT keeps in a recipe's metadata, not its item or fluid inputs, which GregtecLink exports
# as recipe_meta and NEI draws as stacks: TecTech's condensate fluids (mB) and the Beamcrafter's
# two particle beams (NEI shows each particle as a not-consumed stack, Count 0, whose meta is the
# particle id; the beam amount isn't in NEI's dump, so it is the Lab check's).
META_INPUTS = ("condensate_input", "beamcrafter_metadata")


def meta_inputs(key: str, value: object) -> list[Stack]:
    if key == "condensate_input" and isinstance(value, list):
        return [Stack(f"fluid:{v['fluid']}", int(v["mb"])) for v in value]
    if key == "beamcrafter_metadata" and isinstance(value, dict):
        f = value["fields"]
        return [
            Stack(f"gtnhlanth:particle@{f[k]}", 0)
            for k in ("particleID_A", "particleID_B")
        ]
    raise ValueError(f"recipe_meta {key} has an unknown shape: {value!r}")


def _nei_stack(s: dict[str, object]) -> Stack:
    if "neiFluidName" in s:
        key = f"fluid:{s['neiFluidName']}"
    else:
        key = f"{s['strId']}@{typed_int(s.get('Damage', 0))}"
    alts: set[str] = set()
    for a in s.get("anyOf") or []:  # type: ignore[union-attr]
        if isinstance(a, dict):
            alts.add(_nei_stack({**a, "Count": 1}).key)
    return Stack(
        key,
        typed_int(s.get("Count", 1)),
        typed_int(s.get("chance", FULL)),
        frozenset(alts),
    )


def nei_side(line: dict[str, object], raw: str = "") -> Side:
    """One line of NEI's dump."""
    gt = line.get("gt")
    eut = duration = None
    if isinstance(gt, dict):
        eut = typed_int(gt.get("eut"))
        duration = typed_int(gt.get("durationTicks"))
    ins = [_nei_stack(s) for s in line.get("ingredients") or []]  # type: ignore[union-attr]
    outs = [_nei_stack(s) for s in line.get("results") or []]  # type: ignore[union-attr]
    return Side(str(line["handler"]), eut, duration, ins, outs, raw=raw)


def nei_lines(run: Path, handlers: set[str]) -> dict[str, list[Side]]:
    """Every line of the run's ``nei/*.jsonl`` whose own ``handler`` is in ``handlers``."""
    out: dict[str, list[Side]] = {h: [] for h in handlers}
    for path in sorted((run / "nei").glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                line = json.loads(raw)
                if line.get("handler") in out:
                    out[line["handler"]].append(nei_side(line, raw))
    return out


# --- the comparison ----------------------------------------------------------------------


def _pair(
    db: Sequence[Stack], nei: Sequence[Stack]
) -> list[tuple[Stack, Stack]] | None:
    """Pair every DB slot with its own NEI stack (equal numbers first), or None."""
    if len(db) != len(nei):
        return None
    free = list(nei)
    pairs: list[tuple[Stack, Stack]] = []
    later: list[Stack] = []
    for d in db:
        hit = next(
            (
                n
                for n in free
                if _compatible(d, n) and (d.amount, d.chance) == (n.amount, n.chance)
            ),
            None,
        )
        if hit is None:
            later.append(d)
        else:
            free.remove(hit)
            pairs.append((d, hit))
    for d in later:
        hit = next((n for n in free if _compatible(d, n)), None)
        if hit is None:
            return None
        free.remove(hit)
        pairs.append((d, hit))
    return pairs


def diffs(db: Side, nei: Side) -> list[str] | None:
    """What differs between a recipe and an NEI line, or None when their stacks don't pair."""
    ins = _pair(db.inputs, nei.inputs)
    outs = _pair(db.outputs, nei.outputs)
    if ins is None or outs is None:
        return None
    out: list[str] = []
    if (db.eut, db.duration) != (nei.eut, nei.duration):
        out.append(
            f"EU/t, ticks: DB {db.eut}, {db.duration}; NEI {nei.eut}, {nei.duration}"
        )
    for part, pairs in (("in", ins), ("out", outs)):
        for d, n in pairs:
            if d.amount != n.amount:
                out.append(f"{part} {d.key} amount: DB {d.amount}; NEI {n.amount}")
            if d.chance != n.chance:
                out.append(f"{part} {d.key} chance: DB {d.chance}; NEI {n.chance}")
    return out


def check(golden_id: str, ref: str, db: Side | None, lines: Iterable[Side]) -> Verdict:
    """The recipe against every NEI line of its handler: one must match exactly."""
    if db is None:
        return Verdict(golden_id, ref, False, "not in the DB's run")
    best: tuple[Side, list[str]] | None = None
    for nei in lines:
        d = diffs(db, nei)
        if d is None:
            continue
        if not d:
            why = "match" + (
                f"; from recipe_meta: {', '.join(db.from_meta)}" if db.from_meta else ""
            )
            return Verdict(golden_id, ref, True, why, db, nei)
        if best is None or len(d) < len(best[1]):
            best = (nei, d)
    if best is None:
        return Verdict(
            golden_id, ref, False, f"no line of NEI's {db.handler} has its stacks", db
        )
    return Verdict(golden_id, ref, False, "numbers differ", db, best[0], best[1])


# --- the golden set ------------------------------------------------------------------------


def read_fixture(path: Path = FIXTURE) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cells = line.split("\t")
        if cells == list(COLUMNS):
            continue
        if len(cells) != len(COLUMNS):
            raise ValueError(
                f"{path.name}: {len(cells)} cells, want {len(COLUMNS)}: {line!r}"
            )
        rows.append(dict(zip(COLUMNS, cells, strict=True)))
    return rows


def current_run(conn: sqlite3.Connection) -> tuple[int, str]:
    row = conn.execute(
        "SELECT id, run_key FROM export_run ORDER BY created_at DESC, run_key DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise LookupError("the DB has no export run")
    return int(row[0]), str(row[1])


@dataclass(frozen=True)
class Candidate:
    ref: str
    handler: str  # the NEI handler id (a GT category, or NEI's own handler)
    group: str  # the map or handler a pick must not repeat
    mod: str
    output: str
    name: str


# Where each stratum's candidates come from (one SQL condition each, over recipe r, map m), in
# citation-key order: the key is a hash of the recipe, so the order is an unbiased shuffle that
# the same run always gives.
_BASE = """
SELECT r.ref,
  CASE WHEN r.source IN ('gt', 'gt+nei') THEN r.category ELSE h.handler_name END,
  coalesce(m.unloc_name, h.handler_name), coalesce(m.owner_mod, h.mod, ''),
  (SELECT CASE WHEN o.fluid_id IS NOT NULL THEN 'fluid:' || f.name
     ELSE i.registry_name || '@' || i.meta END
   FROM recipe_output AS o LEFT JOIN item AS i ON i.id = o.item_id
   LEFT JOIN fluid AS f ON f.id = o.fluid_id WHERE o.recipe_id = r.id ORDER BY o.slot LIMIT 1),
  (SELECT coalesce(i.name_en, f.name_en, '') FROM recipe_output AS o
   LEFT JOIN item AS i ON i.id = o.item_id LEFT JOIN fluid AS f ON f.id = o.fluid_id
   WHERE o.recipe_id = r.id ORDER BY o.slot LIMIT 1)
FROM recipe AS r
LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id
LEFT JOIN nei_handler AS h ON h.id = r.nei_handler_id
WHERE r.run_id = :run AND r.enabled = 1 AND coalesce(r.hidden, 0) = 0
  AND coalesce(r.fake, 0) = 0 AND coalesce(r.category, '') NOT LIKE '%recycling%'
  AND EXISTS (SELECT 1 FROM recipe_output AS o WHERE o.recipe_id = r.id)
  AND (r.source NOT IN ('gt', 'gt+nei')
    OR (m.nei_visible = 1 AND coalesce(m.backend_class, '') NOT LIKE '%Fuel%'))
  AND ({cond})
ORDER BY r.ref
"""
_GT = "r.source IN ('gt', 'gt+nei')"
_OWN = f"{_GT} AND m.owner_mod = 'gregtech'"
_SINGLE = (
    f"{_OWN} AND EXISTS (SELECT 1 FROM machine_map AS mm"
    " JOIN machine AS x ON x.id = mm.machine_id"
    " WHERE mm.recipe_map_id = m.id AND x.is_multiblock = 0)"
)
_MULTI_ONLY = (
    f"{_OWN} AND EXISTS (SELECT 1 FROM machine_map AS mm JOIN machine AS x ON x.id = mm.machine_id"
    " WHERE mm.recipe_map_id = m.id AND x.is_multiblock = 1)"
    " AND NOT EXISTS (SELECT 1 FROM machine_map AS mm JOIN machine AS x ON x.id = mm.machine_id"
    " WHERE mm.recipe_map_id = m.id AND x.is_multiblock = 0)"
)
_FLUID_BOTH = (
    f"{_OWN} AND EXISTS (SELECT 1 FROM recipe_input AS s WHERE s.recipe_id = r.id"
    " AND s.fluid_id IS NOT NULL AND s.amount > 0)"
    " AND EXISTS (SELECT 1 FROM recipe_output AS s WHERE s.recipe_id = r.id"
    " AND s.fluid_id IS NOT NULL)"
)
_CHANCED = (
    f"{_OWN} AND EXISTS (SELECT 1 FROM recipe_output AS s WHERE s.recipe_id = r.id"
    " AND s.chance_bp < 10000)"
)
_OREDICT = (
    f"{_OWN} AND EXISTS (SELECT 1 FROM recipe_input AS s WHERE s.recipe_id = r.id"
    " AND s.ore_id IS NOT NULL)"
)
_ADDON = f"{_GT} AND m.owner_mod NOT IN ('gregtech', 'minecraft', '')"

STRATA: tuple[tuple[str, int, str], ...] = (
    *((f"tier-{t}", 1, f"{_SINGLE} AND r.voltage_tier = {t}") for t in range(9)),
    ("multiblock", 5, _MULTI_ONLY),
    ("fluids", 3, _FLUID_BOTH),
    ("chanced", 3, _CHANCED),
    ("oredict", 3, _OREDICT),
    ("addon", 4, _ADDON),
    ("shaped", 1, f"h.handler_name = '{NEI_CRAFTING['shaped']}'"),
    ("shapeless", 1, f"h.handler_name = '{NEI_CRAFTING['shapeless']}'"),
    ("furnace", 1, f"h.handler_name = '{NEI_CRAFTING['furnace']}'"),
)
TIERS = ("ULV", "LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV")


def candidates(conn: sqlite3.Connection, run_id: int, cond: str) -> Iterator[Candidate]:
    for row in conn.execute(_BASE.format(cond=cond), {"run": run_id}):
        yield Candidate(*(str(v or "") for v in row))


def select(conn: sqlite3.Connection, run_id: int) -> list[dict[str, str]]:
    """The golden set the rule picks from run ``run_id`` (module docstring)."""
    picked: list[dict[str, str]] = []
    groups: set[str] = set()
    refs: set[str] = set()
    for stratum, n, cond in STRATA:
        pool = [c for c in candidates(conn, run_id, cond) if c.ref not in refs]
        chosen: list[Candidate] = []
        mods: set[str] = set()
        for fresh_only in (True, False):
            for c in pool:
                if len(chosen) == n:
                    break
                if c in chosen or (stratum == "addon" and c.mod in mods):
                    continue
                if fresh_only and c.group in groups:
                    continue
                chosen.append(c)
                groups.add(c.group)
                mods.add(c.mod)
        for c in chosen:
            refs.add(c.ref)
            what = f"{c.name} ({c.group}, {c.mod})"
            if stratum.startswith("tier-"):
                what += f", {TIERS[int(stratum[5:])]}"
            picked.append(
                {
                    "id": f"g{len(picked) + 1:02d}",
                    "stratum": stratum,
                    "ref": c.ref,
                    "handler": c.handler,
                    "output": c.output,
                    "what": what,
                }
            )
    return picked


def run_check(
    conn: sqlite3.Connection,
    run_dir: Path,
    run_id: int,
    golden: Sequence[dict[str, str]],
) -> list[Verdict]:
    sides = {g["id"]: db_side(conn, run_id, g["ref"]) for g in golden}
    handlers = {s.handler for s in sides.values() if s is not None}
    lines = nei_lines(run_dir, handlers)
    out: list[Verdict] = []
    for g in golden:
        side = sides[g["id"]]
        if side is not None:
            side.name = g["what"]
        out.append(
            check(g["id"], g["ref"], side, lines.get(side.handler, []) if side else [])
        )
    return out


# What NEI's JSON dump doesn't carry, which the Lab check reads off NEI's page (D222).
LAB_ONLY = ("coil_heat", "cleanroom", "low_gravity", "special_items", "additives")


def lab_lines(conn: sqlite3.Connection, run_id: int, g: dict[str, str]) -> list[str]:
    """One golden recipe as the Lab checklist shows it, every number read from the DB."""
    side = db_side(conn, run_id, g["ref"])
    if side is None:
        return [f"{g['id']}  {g['what']}  {g['ref']}: not in this DB's run"]
    amps, special = conn.execute(
        "SELECT amperage, special_value FROM recipe WHERE run_id = ? AND ref = ?",
        (run_id, g["ref"]),
    ).fetchone()
    meta = [
        f"{k}={num if text is None else text}"
        for k, num, text in conn.execute(
            "SELECT m.key, m.value_num, m.value_text FROM recipe_meta AS m"
            " JOIN recipe AS r ON r.id = m.recipe_id WHERE r.run_id = ? AND r.ref = ?"
            f" AND m.key IN ({', '.join('?' * len(LAB_ONLY))}) ORDER BY m.key",
            (run_id, g["ref"], *LAB_ONLY),
        )
    ]
    return [
        f"{g['id']}  {g['what']}",
        f"    {g['ref']}  NEI tab: {side.handler}  (look up: {g['output']})",
        f"    {_fmt_side(side)}",
        f"    amperage={amps} special_value={special}"
        + (f" | {' '.join(meta)}" if meta else ""),
    ]


def _fmt_side(s: Side) -> str:
    def stacks(xs: list[Stack]) -> str:
        return ", ".join(
            f"{x.amount}x {x.key}" + (f" @{x.chance}bp" if x.chance != FULL else "")
            for x in xs
        )

    return f"eut={s.eut} ticks={s.duration} | in: {stacks(s.inputs)} | out: {stacks(s.outputs)}"


def report(verdicts: Sequence[Verdict]) -> str:
    ok = sum(v.ok for v in verdicts)
    lines = [
        f"golden recipes: {ok} of {len(verdicts)} match NEI ({100 * ok / max(1, len(verdicts)):.0f}%)"
    ]
    for v in verdicts:
        lines.append(f"{v.golden_id}\t{'ok' if v.ok else 'MISMATCH'}\t{v.ref}\t{v.why}")
        if not v.ok:
            if v.db is not None:
                lines.append(f"    DB:  {_fmt_side(v.db)}")
            if v.nei is not None:
                lines.append(f"    NEI: {_fmt_side(v.nei)}")
            lines.extend(f"    - {d}" for d in v.diffs)
    return "\n".join(lines)


# --- unit tests (synthetic; always run) ----------------------------------------------------


def _line(
    handler: str, ins: list[dict[str, object]], outs: list[dict[str, object]], **gt: int
) -> Side:
    line: dict[str, object] = {"handler": handler, "ingredients": ins, "results": outs}
    if gt:
        line["gt"] = {"eut": gt["eut"], "durationTicks": gt["ticks"]}
    return nei_side(line)


def _db(**kw: object) -> Side:
    base: dict[str, object] = {
        "handler": "gt.recipe.blastfurnace",
        "eut": 120,
        "duration": 100,
        "inputs": [
            Stack("a:dust@5", 2, accepts=frozenset({"b:dust@0"})),
            Stack("gregtech:gt.integrated_circuit@11", 0),
            Stack("fluid:acid", 500),
        ],
        "outputs": [Stack("a:dust@6", 1), Stack("fluid:sol", 1000, 2500)],
    }
    base.update(kw)
    return Side(**base)  # type: ignore[arg-type]


NEI_INS: list[dict[str, object]] = [
    {"strId": "gregtech:gt.integrated_circuit", "Damage": 11, "Count": 0},
    {"neiFluidName": "acid", "Count": "500L"},
    {"strId": "b:dust", "Damage": 0, "Count": 2},  # an alternative of the DB's slot
]
NEI_OUTS: list[dict[str, object]] = [
    {"neiFluidName": "sol", "Count": "1000L", "chance": 2500},
    {"strId": "a:dust", "Damage": 6, "Count": 1},
]


def test_a_recipe_matches_its_nei_line_in_any_slot_order() -> None:
    nei = _line("gt.recipe.blastfurnace", NEI_INS, NEI_OUTS, eut=120, ticks=100)
    v = check("g01", "r:x", _db(), [nei])
    assert v.ok, v
    assert diffs(_db(), nei) == []


def test_every_number_that_differs_is_reported_with_both_sides() -> None:
    outs = [dict(NEI_OUTS[0], chance=5000), NEI_OUTS[1]]
    ins = [NEI_INS[0], dict(NEI_INS[1], Count="250L"), NEI_INS[2]]
    nei = _line("gt.recipe.blastfurnace", ins, outs, eut=30, ticks=100)
    v = check("g01", "r:x", _db(), [nei])
    assert not v.ok and v.why == "numbers differ"
    assert v.diffs == [
        "EU/t, ticks: DB 120, 100; NEI 30, 100",
        "in fluid:acid amount: DB 500; NEI 250",
        "out fluid:sol chance: DB 2500; NEI 5000",
    ]
    assert "MISMATCH" in report([v]) and "NEI: eut=30" in report([v])


def test_the_exact_line_wins_over_a_near_one() -> None:
    near = _line("gt.recipe.blastfurnace", NEI_INS, NEI_OUTS, eut=120, ticks=200)
    exact = _line("gt.recipe.blastfurnace", NEI_INS, NEI_OUTS, eut=120, ticks=100)
    assert check("g01", "r:x", _db(), [near, exact]).ok


def test_unpaired_stacks_or_a_missing_recipe_fail() -> None:
    other = _line("gt.recipe.blastfurnace", NEI_INS, [NEI_OUTS[0]], eut=120, ticks=100)
    v = check("g01", "r:x", _db(), [other])
    assert not v.ok and v.why.startswith("no line of NEI's gt.recipe.blastfurnace")
    assert check("g01", "r:x", None, []).why == "not in the DB's run"


def test_wildcard_meta_and_any_of_count_as_the_same_item() -> None:
    assert _same_item("a:b@32767", "a:b@3") and not _same_item("a:b@1", "a:b@3")
    assert not _same_item("fluid:water", "fluid:steam")
    s = _nei_stack(
        {"strId": "x:y", "Damage": 1, "Count": 4, "anyOf": [{"strId": "z:w"}]}
    )
    assert s == Stack("x:y@1", 4, FULL, frozenset({"z:w@0"}))
    assert _compatible(Stack("z:w@0", 4), s)


def test_the_committed_golden_set_is_30_db_keys_without_numbers() -> None:
    rows = read_fixture()
    assert [r["id"] for r in rows] == [f"g{i:02d}" for i in range(1, 31)]
    assert len({r["ref"] for r in rows}) == 30
    for r in rows:
        assert r["ref"].startswith("r:") and r["handler"] and r["output"]
        assert r["output"].startswith("fluid:") or "@" in r["output"], r
    counts: dict[str, int] = {}
    for r in rows:
        name = "tier" if r["stratum"].startswith("tier-") else r["stratum"]
        counts[name] = counts.get(name, 0) + 1
    want = {s[0] if not s[0].startswith("tier-") else "tier": 0 for s in STRATA}
    for s in STRATA:
        want["tier" if s[0].startswith("tier-") else s[0]] += s[1]
    assert counts == want


# --- the harness check -------------------------------------------------------------------


def _golden_home(tmp: Path) -> Path:
    where = os.environ.get("GOS_GOLDEN_HOME")
    if where:
        return Path(where)
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip(
            "set GOS_REQUIRE_HARNESS_RUN=1 after the client harness, or GOS_GOLDEN_HOME"
        )
    from gregtec.db import build

    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(
            f"no client harness run in {game / collect.EXPORTS} (mod/README.md)"
        )
    collect.collect(game, tmp, 1, allow_harness=True)
    build.build(tmp, allow_harness=True)
    return tmp


def _open(home: Path) -> tuple[sqlite3.Connection, Path, int]:
    db = home / "data" / "gos.sqlite"
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    run_id, run_key = current_run(conn)
    return conn, home / "data" / "exports" / run_key, run_id


def test_golden_recipes_match_nei_in_a_real_run(tmp_path: Path) -> None:
    conn, run_dir, run_id = _open(_golden_home(tmp_path))
    try:
        verdicts = run_check(conn, run_dir, run_id, read_fixture())
        shown = [lab_lines(conn, run_id, g) for g in read_fixture()]
    finally:
        conn.close()
    assert all(len(lines) == 4 for lines in shown), shown
    bad = [v for v in verdicts if not v.ok]
    assert not bad, report(verdicts)


# --- command line ------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("action", choices=("report", "select", "show"))
    p.add_argument(
        "home", type=Path, help="a GOS_HOME with data/gos.sqlite and its run"
    )
    p.add_argument("--reselect", action="store_true", help="apply the rule to this DB")
    a = p.parse_args(argv)
    conn, run_dir, run_id = _open(a.home)
    try:
        golden = (
            select(conn, run_id)
            if a.action == "select" or a.reselect
            else read_fixture()
        )
        if a.action == "select":
            print("\t".join(COLUMNS))
            for g in golden:
                print("\t".join(g[c] for c in COLUMNS))
            return 0
        if a.action == "show":
            for g in golden:
                print("\n".join(lab_lines(conn, run_id, g)))
            return 0
        verdicts = run_check(conn, run_dir, run_id, golden)
    finally:
        conn.close()
    print(report(verdicts))
    return 0 if all(v.ok for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())

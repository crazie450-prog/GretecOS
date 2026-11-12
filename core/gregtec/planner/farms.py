"""Farms as virtual recipes powered by the plant (WO-4.2, plan §7.2 "Farms" and the
``self_sustaining`` mode, D233).

A farm turns a catalyst it never uses up (a sapling, seeds, a spawner) and EU into products. The
power designer adds farms to a self-sustaining line's network as recipes like any other, their EU
taken from the line's own generator.

- **DB recipe maps first.** A farm machine with a recipe map in the export is read from it. GT's
  Tree Growth Simulator (``gt.recipe.treefarm``, NEI's page of ``MTETreeFarm``) lists per sapling
  its products in mode order (log, sapling, leaves, fruit) with the mode multiplier applied; the
  machine's tier and tool multipliers and its EU/t are GT's code (:mod:`.gtmath.treefarm`). The
  planner farms logs: the log output (slot 0) x the tier multiplier x the tool's; the sapling
  (the sapling-mode product, slot 1, which GT registers as the sapling itself) is the catalyst.
  The tier is :data:`TREE_TIER` (LV: the most logs per EU, D233) and the tool an electric saw
  (:data:`TREE_TOOL`, recharged from the plant: its 32 EU per operation is part of the farm's
  EU), so nothing wears out from outside the line.
- **Curated otherwise.** ``data/curated/farms.tsv`` holds farms with no recipe map in the export
  (crop, mob), each row tagged ``source = curated`` and read only for products no DB farm makes.
  Its numbers are a curator's, cited by file and line (``farms.tsv:<line>``), never estimated
  here; the file ships empty until one is curated (D233, a question for Clayton).

Every key is a stable key (``registry@meta`` or ``fluid:<name>``), never a numeric id.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from gregtec.planner import chains, graph, solve
from gregtec.planner.graph import EU_KEY
from gregtec.planner.gtmath import treefarm
from gregtec.planner.request import is_stable_key

FARMS = chains.REPO_ROOT / "data" / "curated" / "farms.tsv"
HEADER = [
    "id",
    "name",
    "machine",
    "seconds",
    "eut",
    "inputs",
    "outputs",
    "catalysts",
    "source",
    "note",
]
TREE_MAP = "gt.recipe.treefarm"
TREE_CLASS = (
    "MTETreeFarm"  # the 2.9 controller (GT++'s MTETreeFarmLegacy is the old one)
)
TREE_TIER = 1  # LV: most logs per EU (D233)
TREE_TOOL = "buzzsaw"  # an electric saw: x2 logs, charged from the plant


class FarmError(ValueError):
    """``farms.tsv`` is malformed; the message names the line."""


@dataclass(frozen=True)
class Farm:
    """One farm per operation: what it takes, gives and keeps (its catalysts)."""

    id: str  # farm:<r: ref> for a DB farm, farm:<id> for a curated row
    name: str
    source: str  # db | curated
    cite: str  # r:<map>/<sig12> or farms.tsv:<line>
    machine: int | None  # mte id
    seconds: float  # one operation
    eut: int  # EU/t while running
    extra_eu: int  # EU per operation besides eut x ticks (an electric tool's charge)
    inputs: tuple[tuple[str, float], ...]  # used up per operation
    outputs: tuple[tuple[str, float], ...]
    catalysts: tuple[
        tuple[str, float], ...
    ]  # kept: the bootstrap kit's seeds and saplings
    tools: tuple[str, ...] = ()  # tools the bootstrap kit needs (by GT's tool name)

    @property
    def eu_per_op(self) -> float:
        return self.eut * self.seconds * 20 + self.extra_eu

    def proc(self) -> solve.Proc:
        """The farm as a solver recipe: one craft is one operation of one machine."""
        ins = dict(self.inputs)
        ins[EU_KEY] = ins.get(EU_KEY, 0.0) + self.eu_per_op
        return solve.Proc(
            self.id,
            tuple(sorted(ins.items())),
            tuple(sorted(self.outputs)),
            self.seconds,
            tuple(sorted(k for k, _ in self.outputs)),
        )

    def ports(self) -> tuple[dict[str, float], dict[str, float]]:
        """(used up, given) per operation, EU included: what the validator checks."""
        ins = dict(self.inputs)
        ins[EU_KEY] = ins.get(EU_KEY, 0.0) + self.eu_per_op
        return ins, dict(self.outputs)


def tree_farms(
    conn: sqlite3.Connection,
    run_id: int,
    tier: int = TREE_TIER,
    tool: str = TREE_TOOL,
) -> list[Farm]:
    """GT's Tree Growth Simulator, one farm per sapling of ``gt.recipe.treefarm``."""
    mode, _, electric = treefarm.TOOLS[tool]
    if mode != "log":
        raise ValueError(f"{tool} is not a log tool")
    row = conn.execute(
        "SELECT mte_id FROM machine WHERE class_name LIKE ? ORDER BY mte_id LIMIT 1",
        (f"%.{TREE_CLASS}",),
    ).fetchone()
    machine = int(row[0]) if row else None
    rows = conn.execute(
        f"SELECT r.ref, o.slot, {graph._ITEM_KEY_SQL}, o.amount, coalesce(i.name_en, '')"
        " FROM recipe AS r JOIN recipe_map AS m ON m.id = r.recipe_map_id"
        " JOIN recipe_output AS o ON o.recipe_id = r.id JOIN item AS i ON i.id = o.item_id"
        " WHERE r.run_id = ? AND m.unloc_name = ? AND o.slot IN (0, 1)"
        " ORDER BY r.ref, o.slot",
        (run_id, TREE_MAP),
    ).fetchall()
    by_ref: dict[str, dict[int, tuple[str, int, str]]] = {}
    for ref, slot, key, amount, name in rows:
        by_ref.setdefault(str(ref), {})[int(slot)] = (str(key), int(amount), str(name))
    out = []
    for ref in sorted(by_ref):
        slots = by_ref[ref]
        if 0 not in slots or 1 not in slots:
            continue  # no log or no sapling to plant: not a log farm
        log, n, _ = slots[0]
        sapling, _, name = slots[1]
        out.append(
            Farm(
                id=f"farm:{ref}",
                name=f"Tree Growth Simulator ({name})",
                source="db",
                cite=ref,
                machine=machine,
                seconds=treefarm.TICKS / 20,
                eut=treefarm.eut(tier),
                extra_eu=treefarm.TOOL_CHARGE if electric else 0,
                inputs=(),
                outputs=((log, float(treefarm.output(n, tier, tool))),),
                catalysts=((sapling, 1.0),),
                tools=(tool,),
            )
        )
    return out


def _amounts(cell: str, where: str) -> tuple[tuple[str, float], ...]:
    out: list[tuple[str, float]] = []
    for part in filter(None, (p.strip() for p in cell.split(";"))):
        key, sep, amount = part.rpartition("*")
        if not sep or not is_stable_key(key):
            raise FarmError(f"{where}: {part!r} is not <stable key>*<amount>")
        try:
            value = float(amount)
        except ValueError:
            raise FarmError(f"{where}: {amount!r} is not a number") from None
        if value <= 0:
            raise FarmError(f"{where}: amounts are above 0")
        out.append((key, value))
    return tuple(out)


def curated(path: Path = FARMS) -> list[Farm]:
    """``farms.tsv``'s rows, checked (every row ``source = curated``)."""
    out: list[Farm] = []
    header = False
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip() or text.startswith("#"):
            continue
        cells = text.split("\t")
        where = f"{path.name}:{n}"
        if not header:
            if cells != HEADER:
                raise FarmError(f"{where}: header must be {HEADER}")
            header = True
            continue
        if len(cells) != len(HEADER):
            raise FarmError(f"{where}: {len(HEADER)} cells")
        fid, name, machine, seconds, eut, ins, outs, cats, source, _ = cells
        if source != "curated":
            raise FarmError(f"{where}: source must be curated")
        if machine and not machine.startswith("mte:"):
            raise FarmError(f"{where}: machine is mte:<id> or empty")
        try:
            secs, power = float(seconds), int(eut)
        except ValueError:
            raise FarmError(f"{where}: seconds and eut are numbers") from None
        if secs <= 0 or power < 0:
            raise FarmError(f"{where}: seconds above 0, eut 0 or more")
        outputs = _amounts(outs, where)
        if not outputs:
            raise FarmError(f"{where}: a farm gives something")
        out.append(
            Farm(
                id=f"farm:{fid}",
                name=name,
                source="curated",
                cite=where,
                machine=int(machine[4:]) if machine else None,
                seconds=secs,
                eut=power,
                extra_eu=0,
                inputs=_amounts(ins, where),
                outputs=outputs,
                catalysts=_amounts(cats, where),
            )
        )
    return out


def load(
    conn: sqlite3.Connection, run_id: int, path: Path = FARMS
) -> tuple[list[Farm], list[str]]:
    """Every farm: the DB's first, then curated rows for products no DB farm gives.
    Returns (farms, notes)."""
    farms = tree_farms(conn, run_id)
    made = {k for f in farms for k, _ in f.outputs}
    notes: list[str] = []
    for f in curated(path):
        clash = sorted(k for k, _ in f.outputs if k in made)
        if clash:
            notes.append(
                f"{f.cite}: {', '.join(clash)} comes from a DB farm map; skipped"
            )
            continue
        farms.append(f)
    return farms, notes

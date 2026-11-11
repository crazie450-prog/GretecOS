"""Curated chain templates for notorious chains (WO-3.3, plan §7.1 step 2).

A template names, for some items of a chain (the platline, rare earths, naquadah), the recipes
the graph builder expands for them instead of its heuristic's top four, and marks the items
whose choice WO-3.4 turns into a primary choice (a **branch point**). That keeps the graph of a
chain that GT's recipe web would blow up small and on the intended route.

Templates live in ``vault-template/_gos/skills/line-designer/references/chains/*.tsv`` (the
file name is the chain's name; the format is in that folder's README): ``#`` lines are comments,
then a header ``item recipe branch note`` and one row per (item, recipe):

- ``item``: the item or fluid's stable key (``registry@meta``, ``fluid:<name>``);
- ``recipe``: the recipe's citation key ``r:<map>/<sig12>``;
- ``branch``: empty, or a branch-point id: an item with two or more rows must have one, the
  same on each of its rows (the solver picks among them); one row with a branch id marks a
  single recipe WO-3.4 may still swap;
- ``note``: why, for people.

:func:`load` checks the format (ValueError names the file and line); :func:`validate` checks
every row against an export run in the DB and reports **stale keys**: a recipe that isn't in
the run, is disabled or fake, or doesn't make the item. Templates hold keys and no game numbers.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from gregtec.planner.request import RECIPE_KEY, is_stable_key

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAINS_DIR = (
    REPO_ROOT
    / "vault-template"
    / "_gos"
    / "skills"
    / "line-designer"
    / "references"
    / "chains"
)
HEADER = ["item", "recipe", "branch", "note"]
# The fake map GT's assembly-line recipes are listed in (the graph builder admits it, D189).
ASSEMBLY_LINE_MAP = "gt.recipe.fakeAssemblylineProcess"


@dataclass(frozen=True)
class Row:
    chain: str
    line: int
    item: str
    recipe: str
    branch: str
    note: str


@dataclass(frozen=True)
class Choice:
    """A template's recipes for one item, in the file's order."""

    chain: str
    item: str
    recipes: tuple[str, ...]
    branch: str | None


@dataclass(frozen=True)
class Templates:
    rows: tuple[Row, ...]
    choices: Mapping[str, Choice]  # item key -> its choice

    @property
    def chains(self) -> tuple[str, ...]:
        return tuple(sorted({r.chain for r in self.rows}))


@dataclass(frozen=True)
class Stale:
    chain: str
    line: int
    item: str
    recipe: str
    problem: str

    def text(self) -> str:
        return f"{self.chain}.tsv:{self.line}: {self.recipe} for {self.item}: {self.problem}"


EMPTY = Templates((), {})


def read_file(path: Path) -> list[Row]:
    """One template file's rows; ValueError names the line of a bad one."""
    chain = path.stem
    rows: list[Row] = []
    header: list[str] | None = None
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip() or text.startswith("#"):
            continue
        cells = text.split("\t")
        if header is None:
            if cells != HEADER:
                raise ValueError(f"{path.name}:{n}: header must be {', '.join(HEADER)}")
            header = cells
            continue
        if len(cells) != len(HEADER):
            raise ValueError(f"{path.name}:{n}: {len(HEADER)} tab-separated cells")
        item, recipe, branch, note = (c.strip() for c in cells)
        if not is_stable_key(item):
            raise ValueError(f"{path.name}:{n}: {item!r} is not an item or fluid key")
        if not RECIPE_KEY.fullmatch(recipe):
            raise ValueError(f"{path.name}:{n}: {recipe!r} is not an r: key")
        rows.append(Row(chain, n, item, recipe, branch, note))
    if header is None:
        raise ValueError(f"{path.name}: no header")
    return rows


def load(folder: Path = CHAINS_DIR) -> Templates:
    """Every ``*.tsv`` in ``folder``, checked: no row twice, an item in one template only, and
    an item with several recipes has one branch id on all of them."""
    rows: list[Row] = []
    for path in sorted(folder.glob("*.tsv")):
        rows.extend(read_file(path))
    by_item: dict[str, list[Row]] = {}
    for r in rows:
        by_item.setdefault(r.item, []).append(r)
    choices: dict[str, Choice] = {}
    for item, group in sorted(by_item.items()):
        chains = sorted({r.chain for r in group})
        if len(chains) > 1:
            raise ValueError(
                f"{item} is in more than one template: {', '.join(chains)}"
            )
        recipes = [r.recipe for r in group]
        if len(set(recipes)) != len(recipes):
            raise ValueError(f"{chains[0]}.tsv: {item} lists a recipe twice")
        branches = {r.branch for r in group}
        if len(branches) > 1:
            raise ValueError(
                f"{chains[0]}.tsv: {item}'s rows have different branch ids"
            )
        branch = branches.pop() or None
        if len(group) > 1 and branch is None:
            raise ValueError(
                f"{chains[0]}.tsv:{group[1].line}: {item} has several recipes, so it is a "
                "branch point and needs a branch id"
            )
        choices[item] = Choice(chains[0], item, tuple(recipes), branch)
    return Templates(tuple(rows), choices)


def _item_ids(
    conn: sqlite3.Connection, key: str
) -> tuple[list[int], list[int]]:  # item ids, fluid ids
    if key.startswith("fluid:"):
        rows = conn.execute(
            "SELECT id FROM fluid WHERE name = ?", (key[6:],)
        ).fetchall()
        return [], [int(r[0]) for r in rows]
    reg, _, rest = key.rpartition("@")
    meta, _, nbt = rest.partition("#")
    rows = conn.execute(
        "SELECT id FROM item WHERE registry_name = ? AND meta = ? AND"
        " (nbt_hash = ? OR (? != '' AND substr(nbt_hash, 1, length(?)) = ?))",
        (reg, int(meta), nbt, nbt, nbt, nbt),
    ).fetchall()
    return [int(r[0]) for r in rows], []


def validate(
    templates: Templates, conn: sqlite3.Connection, run_id: int
) -> list[Stale]:
    """The rows whose recipe or item no longer holds in export run ``run_id``."""
    out: list[Stale] = []
    for r in templates.rows:
        row = conn.execute(
            "SELECT r.id, r.enabled, r.fake, m.unloc_name FROM recipe AS r"
            " LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id"
            " WHERE r.run_id = ? AND r.ref = ?",
            (run_id, r.recipe),
        ).fetchone()
        if row is None:
            out.append(
                Stale(r.chain, r.line, r.item, r.recipe, "not in the export run")
            )
            continue
        rid, enabled, fake, recipe_map = row
        if not enabled:
            out.append(Stale(r.chain, r.line, r.item, r.recipe, "disabled"))
            continue
        if fake and recipe_map != ASSEMBLY_LINE_MAP:
            out.append(
                Stale(r.chain, r.line, r.item, r.recipe, "a fake (display) recipe")
            )
            continue
        items, fluids = _item_ids(conn, r.item)
        if not items and not fluids:
            out.append(Stale(r.chain, r.line, r.item, r.recipe, "item not in the DB"))
            continue
        made = conn.execute(
            "SELECT 1 FROM recipe_output WHERE recipe_id = ? AND (item_id IN"
            " (SELECT value FROM json_each(?)) OR fluid_id IN (SELECT value FROM json_each(?)))",
            (rid, str(items), str(fluids)),
        ).fetchone()
        if made is None:
            out.append(
                Stale(r.chain, r.line, r.item, r.recipe, "doesn't make the item")
            )
    return out

"""``gos db build``: load a collected export run into ``GOS_HOME/data/gos.sqlite`` (WO-1.3).

1. **Pick and check the run.** The newest collected run in ``GOS_HOME/data/exports/`` (or the
   one named), found as ``gos export collect`` finds runs (complete, ``gregteclink-export/1``,
   no harness runs outside tests), and every listed file checked again against its manifest's
   size and sha256. Steps 1 and 2 hold collect's lock, so a collect can't prune or swap the run
   while it is read. NEI's files are used only when ``parts.nei`` is ``ok``.
2. **Normalise** (:mod:`gregtec.ingest.normalize`): GT's recipe maps, recipes, items, fluids and
   machines, and NEI's recipes; merge NEI into GT (:mod:`gregtec.ingest.merge`). The machine
   facts (WO-1.7, :mod:`gregtec.ingest.machine_facts`): each multiblock's tooltip parsed, plus
   the repo's curated ``data/curated/machine_facts.tsv``, checked before anything is read.
   CropsNH's crop data (WO-1.10, :mod:`gregtec.ingest.crops`) when ``parts.crops`` is ``ok``;
   a crop file that doesn't read leaves the run without crops, not unbuilt.
3. **Collision test**: one recipe per citation key ``r:<map>/<sig12>``. A collision not on the
   allow-list fails the build before the DB is touched, and writes the curation task
   ``GOS_HOME/data/curation/collisions-<run>.tsv`` (``collisions-<run>-2.tsv`` and so on while
   that file can't be replaced, e.g. when it is open in Excel).
4. **Write** everything in one transaction, so a failed build leaves the DB as it was: the run
   (``export_run``, ``mod``), the catalog tables shared by every run (items, fluids, ores, recipe
   maps, NEI handlers, machines and their facts, latest run wins), the run's recipes and crop
   data (:mod:`gregtec.db.crops`), and the name index (``name_fts``). Loading a run that is
   already in the DB replaces its recipes and crops. Then
   runs past the newest ``[db] keep_runs`` (default 2, so WO-1.4 can diff a run with the one
   before) are deleted, except the run just loaded and any run a plan references (plan section
   5.2).
5. **Report** counts per mod, the addon check and the machine-fact coverage of the base
   multiblocks, Lab probe rows already in the DB included (:mod:`gregtec.db.report`), printed
   and saved to ``GOS_HOME/data/reports/db-<run>.txt``.

``fingerprint`` is Play's fingerprint for ``export_run`` (plan section 5.2 step 4). WO-1.4
computes it on Play before syncing and passes it in; until then it is ``""``, which never
matches Play, so the DB reads as stale.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.db import access, report
from gregtec.db import crops as crop_db
from gregtec.ingest import collect, crops, gtdump, machine_facts, machines, merge, nei
from gregtec.ingest.normalize import FLUID, ITEM, Json, OreIndex, Recipe, Ref, canonical

REPORTS = Path("data", "reports")
CURATION = Path("data", "curation")
_SUFFIX = re.compile(r"_[0-9]+$")  # NEI's _2, _3 on duplicate handler names
PASS_NOTE = (
    "GregtecLink machine pass"  # machine_fact.note of the flags this module writes
)


class BuildError(RuntimeError):
    """Refused or failed; the DB is as it was."""


@dataclass
class Loaded:
    """One collected run, normalised and keyed, ready to write."""

    run: collect.Run
    maps: list[gtdump.RecipeMap]
    items: list[gtdump.Item]
    fluids: list[gtdump.Fluid]
    machines: list[machines.Machine]
    recipes: list[Recipe]  # keyed: one per citation key, sorted by it
    recipe_errors: list[gtdump.RecipeError]
    gt_refs: gtdump.GtRefs
    nei_refs: nei.NeiRefs
    handlers: dict[str, str]  # NEI handler id -> display name
    categories: dict[str, str]  # GT category -> map
    nei_status: str
    stats: merge.MergeStats | None = None
    keys: merge.KeyResult | None = None
    # tooltip and curated machine facts (WO-1.7)
    facts: machine_facts.FactSet | None = None
    gt_recipes: int = 0  # GT lines loaded, before duplicates were dropped
    # gt_recipes.jsonl lines per map, error lines included
    map_lines: Counter[str] = field(default_factory=Counter)
    # CropsNH's crop data (WO-1.10): parts.crops, the data when it is "ok" and reads, else why not
    crops_status: str = "absent"
    crops: crops.CropData | None = None
    crops_error: str | None = None
    crop_lines: list[Recipe] = field(default_factory=list)  # NEI's CropsNH tab lines
    # The full ore dictionary (D145) when parts.oredict is "ok"; None names NEI slots from the
    # ore lists of items.jsonl, as runs made before it do (a failed part with NEI is refused).
    oredict_status: str = "absent"
    oredict: list[gtdump.OreName] | None = None


@dataclass
class Result:
    run_id: int  # export_run.id
    loaded: Loaded
    db: Path
    pruned: list[str] = field(default_factory=list)
    replaced: bool = False  # the run was in the DB already
    report_path: Path | None = None
    report_error: str | None = None  # why the report couldn't be saved


def find_run(
    gos_home: Path, run_key: str | None = None, allow_harness: bool = False
) -> collect.Run:
    root = gos_home / collect.DATA_EXPORTS
    if not root.is_dir():
        raise BuildError(f"{root} doesn't exist: run `gos export collect` first")
    runs, skipped = collect.find_runs(root, allow_harness)
    if run_key is not None:
        for r in runs:
            if r.run_id == run_key:
                return r
        why = dict(skipped).get(run_key, "no such collected run")
        raise BuildError(f"run {run_key} in {root}: {why}")
    if not runs:
        why = "".join(
            f"; skipped {collect._clean(n)}: {w}"
            for n, w in skipped
            if not n.startswith((collect.STAGING, collect.TRASH))
        )
        raise BuildError(
            f"no collected run in {root}: run `gos export collect` first{why}"
        )
    return runs[0]


def verify(run: collect.Run) -> None:
    """Every listed file matches the manifest's size and sha256 (collect's own check)."""
    try:
        listed = collect.entries(run)
    except collect.CollectError as exc:
        raise BuildError(str(exc)) from None
    problem = collect._damage(run.path, listed)
    if problem is not None:
        raise BuildError(
            f"run {run.run_id} in {run.path.parent}: {problem}; collect it again"
        )


def _nei_status(m: dict[str, Json]) -> str:
    parts = m.get("parts")
    status = parts.get("nei") if isinstance(parts, dict) else None
    return status if isinstance(status, str) else "absent"


def load(
    run: collect.Run,
    allowed: dict[tuple[str, str], tuple[str, ...]],
    curated: Sequence[machine_facts.Curated] = (),
) -> Loaded:
    """Read, normalise, merge and key ``run``; raise ``merge.CollisionError`` on a collision."""
    path = run.path
    oredict_status = gtdump.oredict_status(run.manifest)
    if oredict_status not in ("ok", "absent") and _nei_status(run.manifest) == "ok":
        # Without the full oredict, items.jsonl's ore lists name fewer NEI-only slots, so
        # thousands of NEI-only recipes would get other citation keys for this one run (D145).
        why = run.manifest.get("part_errors")
        error = why.get("oredict") if isinstance(why, dict) else None
        raise BuildError(
            f"run {run.run_id}: parts.oredict is {oredict_status!r}"
            + (f" ({collect._clean(str(error))})" if error else "")
            + ", so its NEI-only recipes would get other citation keys; run the export "
            "again, or name an older run"
        )
    maps = gtdump.read_recipe_maps(path)
    by_name = {m.name: m for m in maps}
    categories = {c: m.name for m in maps for c, _ in m.categories}
    gt_refs = gtdump.GtRefs()
    gt_recipes: list[Recipe] = []
    errors: list[gtdump.RecipeError] = []
    map_lines: Counter[str] = Counter()
    for r in gtdump.read_recipes(path, by_name, gt_refs):
        if isinstance(r, gtdump.RecipeError):
            errors.append(r)
            map_lines[r.map] += 1
        else:
            gt_recipes.append(r)
            map_lines[r.space] += 1
    items = list(gtdump.read_items(path, gt_refs.ids))
    oredict = None
    if oredict_status == "ok":
        oredict = list(gtdump.read_oredict(path, gt_refs.ids))
    fluids = list(gtdump.read_fluids(path))
    found = list(machines.read_machines(path))
    loaded = Loaded(
        run=run,
        maps=maps,
        items=items,
        fluids=fluids,
        machines=found,
        recipes=[],
        recipe_errors=errors,
        gt_refs=gt_refs,
        nei_refs=nei.NeiRefs(gt_refs.ids, {}, {}),
        handlers={},
        categories=categories,
        nei_status=_nei_status(run.manifest),
        gt_recipes=len(gt_recipes),
        map_lines=map_lines,
        oredict_status=oredict_status,
        oredict=oredict,
    )
    rows: Iterable[Recipe] = ()
    if loaded.nei_status == "ok":
        rows = _nei_rows(loaded, nei.nei_files(run.manifest))
    ores = OreIndex.build(_ore_pairs(loaded))
    combined, loaded.stats = merge.merge(gt_recipes, rows, categories, ores)
    loaded.keys = merge.assign_keys(combined, allowed)
    loaded.recipes = loaded.keys.recipes
    loaded.facts = machine_facts.assemble(found, curated)
    # Crop data isn't part of "complete" (as NEI's): a run whose crop step failed, or whose crop
    # files don't read, still builds its recipes, and the report says why it has no crops.
    loaded.crops_status = crops.status(run.manifest)
    if loaded.crops_status == "ok":
        nei_ok = loaded.nei_status == "ok"
        try:
            loaded.crops = crops.read(
                path,
                run.manifest,
                loaded.crop_lines if nei_ok else None,
                loaded.nei_refs if nei_ok else None,
            )
        except (crops.FormatError, OSError) as exc:
            loaded.crops_error = str(exc)
        except (
            ValueError,
            LookupError,
            IndexError,
        ) as exc:  # a shape the reader didn't foresee
            loaded.crops_error = f"{type(exc).__name__}: {exc}"
    return loaded


def _ore_pairs(loaded: Loaded) -> Iterator[tuple[Ref, tuple[str, ...]]]:
    """(member, its ore names) for :class:`OreIndex`: from the full ore dictionary when the run
    has one (D145), else from the ore lists of ``items.jsonl``, which hold only the items GT's
    files reference, and give an NBT variant its item's names (Forge ignores NBT there)."""
    if loaded.oredict is None:
        for i in loaded.items:
            yield i.ref, i.ore
        return
    for o in loaded.oredict:
        for m in o.members:
            yield m, (o.name,)


def _nei_rows(loaded: Loaded, rels: list[str]) -> Iterator[Recipe]:
    for rel, recipe in nei.read_nei(loaded.run.path, rels, loaded.nei_refs):
        assert recipe.handler is not None
        if recipe.handler not in loaded.handlers:
            stem = rel.removeprefix(nei.DIR + "/").removesuffix(".jsonl")
            loaded.handlers[recipe.handler] = _SUFFIX.sub("", stem)
        if crops.is_crop_line(recipe):
            loaded.crop_lines.append(
                recipe
            )  # for the crop tables; merge leaves them out
        yield recipe


# --- writing -----------------------------------------------------------------------------


def _bit(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _meta_value(value: Json) -> tuple[float | None, str | None]:
    if isinstance(value, bool):
        return float(value), None
    if isinstance(value, int | float):
        return float(value), None
    if isinstance(value, str):
        return None, value
    return None, canonical(value)


def _delete_runs(conn: sqlite3.Connection, run_ids: Sequence[int]) -> None:
    for run_id in run_ids:
        sub = "SELECT id FROM recipe WHERE run_id = ?"
        for table in (
            "recipe_input",
            "recipe_input_alt",
            "recipe_output",
            "recipe_meta",
        ):
            conn.execute(f"DELETE FROM {table} WHERE recipe_id IN ({sub})", (run_id,))
        conn.execute("DELETE FROM recipe WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM mod WHERE run_id = ?", (run_id,))
    crop_db.delete(conn, run_ids)


def _export_run(
    conn: sqlite3.Connection, loaded: Loaded, fingerprint: str
) -> tuple[int, bool]:
    m = loaded.run.manifest
    stats = loaded.stats
    sources = {
        "run_id": loaded.run.run_id,
        "created_utc": loaded.run.created_utc,
        "mode": m.get("mode"),
        "side": m.get("side"),
        "world": m.get("world"),
        "gregteclink": m.get("gregteclink"),
        "parts": m.get("parts"),
        "part_warnings": m.get("part_warnings"),
        "counts": m.get("counts"),
        "files": m.get("files"),
        "merge": None
        if stats is None
        else {
            "nei_rows": stats.nei_rows,
            "exact": stats.exact,
            "matched": stats.matched,
            "duplicates": sum(stats.duplicates.values()),
            "dropped": sum(stats.dropped.values()),
            "not_recipes": sum(stats.not_recipes.values()),
            "nei_only": stats.nei_only,
        },
    }
    row = (
        loaded.run.created_utc,
        m.get("pack_version"),
        m.get("gt_version"),
        m.get("nei_version"),
        fingerprint,
        canonical(sources),
        loaded.run.run_id,
    )
    old = conn.execute(
        "SELECT id FROM export_run WHERE run_key = ?", (loaded.run.run_id,)
    ).fetchone()
    if old is not None:
        _delete_runs(conn, [old[0]])
        # A reload with no fingerprint keeps the one the run was loaded with (WO-1.4's).
        conn.execute(
            "UPDATE export_run SET created_at=?, pack_version=?, gt_version=?, nei_version=?,"
            " fingerprint=coalesce(nullif(?, ''), fingerprint), sources_json=?, run_key=?"
            " WHERE id=?",
            (*row, old[0]),
        )
        return old[0], True
    cur = conn.execute(
        "INSERT INTO export_run(created_at, pack_version, gt_version, nei_version,"
        " fingerprint, sources_json, run_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
        row,
    )
    assert cur.lastrowid is not None
    return cur.lastrowid, False


def _refs(loaded: Loaded) -> tuple[set[Ref], set[Ref]]:
    """Every item and fluid ref the run's recipes and machines use."""
    refs: set[Ref] = set()
    for r in loaded.recipes:
        for s in (*r.inputs, *r.outputs):
            refs.add(s.ref)
            refs.update(s.alts)
            if s.returns is not None:
                refs.add(s.returns)
    refs.update(m.ref for m in loaded.machines)
    refs.update(i.ref for i in loaded.items)
    for o in loaded.oredict or ():
        refs.update(o.members)
    if loaded.crops is not None:
        refs.update(crops.item_refs(loaded.crops))
    return {r for r in refs if r.kind == ITEM}, {r for r in refs if r.kind == FLUID}


def _items(conn: sqlite3.Connection, loaded: Loaded, refs: set[Ref]) -> dict[Ref, int]:
    listed = {i.ref: i for i in loaded.items}
    nbt = {**loaded.nei_refs.nbt, **loaded.gt_refs.nbt}
    for i in loaded.items:
        if i.nbt is not None:
            nbt[i.ref] = i.nbt
    rows = []
    for ref in sorted(refs):
        item = listed.get(ref)
        tag = nbt.get(ref)
        rows.append(
            (
                ref.name,
                ref.meta,
                ref.nbt_hash,
                None if tag is None else canonical(tag),
                ref.name.split(":", 1)[0] if ":" in ref.name else None,
                (item.name if item else None) or loaded.nei_refs.names.get(ref),
                item.unlocalized if item else None,
                item.gt_prefix if item else None,
                item.gt_material if item else None,
                item.gt_amount if item else None,
                item is not None,
            )
        )
    # GT's items.jsonl is the authority for an item it lists; NEI only names items GT doesn't.
    conn.executemany(
        "INSERT INTO item(registry_name, meta, nbt_hash, nbt_json, mod_id, name_en, unlocalized,"
        " gt_prefix, gt_material, gt_material_amount) VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(registry_name, meta, nbt_hash) DO UPDATE SET"
        " nbt_json = coalesce(excluded.nbt_json, nbt_json), mod_id = excluded.mod_id,"
        " name_en = CASE WHEN ?11 THEN excluded.name_en ELSE coalesce(name_en, excluded.name_en) END,"
        " unlocalized = CASE WHEN ?11 THEN excluded.unlocalized ELSE unlocalized END,"
        " gt_prefix = CASE WHEN ?11 THEN excluded.gt_prefix ELSE gt_prefix END,"
        " gt_material = CASE WHEN ?11 THEN excluded.gt_material ELSE gt_material END,"
        " gt_material_amount = CASE WHEN ?11 THEN excluded.gt_material_amount"
        " ELSE gt_material_amount END",
        rows,
    )
    ids: dict[Ref, int] = {}
    for row in conn.execute("SELECT id, registry_name, meta, nbt_hash FROM item"):
        ref = Ref(ITEM, row[1], row[2], row[3])
        if ref in refs:
            ids[ref] = row[0]
    return ids


def _fluids(conn: sqlite3.Connection, loaded: Loaded, refs: set[Ref]) -> dict[str, int]:
    listed = {f.name: f for f in loaded.fluids}
    names = {r.name for r in refs} | set(listed)
    rows = []
    for name in sorted(names):
        f = listed.get(name)
        rows.append(
            (
                name,
                f.name_en if f else loaded.nei_refs.names.get(Ref(FLUID, name)),
                f.temperature if f else None,
                f.density if f else None,
                f.viscosity if f else None,
                _bit(f.gaseous) if f else None,
                f is not None,
            )
        )
    conn.executemany(
        "INSERT INTO fluid(name, name_en, temperature, density, viscosity, gaseous)"
        " VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET"
        " name_en = CASE WHEN ?7 THEN excluded.name_en ELSE coalesce(name_en, excluded.name_en) END,"
        " temperature = CASE WHEN ?7 THEN excluded.temperature ELSE temperature END,"
        " density = CASE WHEN ?7 THEN excluded.density ELSE density END,"
        " viscosity = CASE WHEN ?7 THEN excluded.viscosity ELSE viscosity END,"
        " gaseous = CASE WHEN ?7 THEN excluded.gaseous ELSE gaseous END",
        rows,
    )
    return {row[1]: row[0] for row in conn.execute("SELECT id, name FROM fluid")}


def _ores(
    conn: sqlite3.Connection, loaded: Loaded, items: dict[Ref, int]
) -> dict[str, int]:
    names = set(loaded.gt_refs.ores)
    for i in loaded.items:
        names.update(i.ore)
    for o in loaded.oredict or ():
        names.add(o.name)
    conn.executemany(
        "INSERT OR IGNORE INTO ore(name) VALUES (?)", [(n,) for n in sorted(names)]
    )
    ids = {row[1]: row[0] for row in conn.execute("SELECT id, name FROM ore")}
    # Membership is the latest run's, like the other catalog tables: each item's own ore list
    # (so an item covered by a wildcard member is found by its meta) plus the full ore
    # dictionary's members as registered (a wildcard member by its meta 32767 row, D145).
    conn.execute("DELETE FROM ore_member")
    pairs = {(ids[o], items[i.ref]) for i in loaded.items for o in i.ore}
    for o in loaded.oredict or ():
        pairs.update((ids[o.name], items[m]) for m in o.members)
    members = sorted(pairs)
    conn.executemany("INSERT INTO ore_member(ore_id, item_id) VALUES (?, ?)", members)
    return ids


def _maps(conn: sqlite3.Connection, loaded: Loaded) -> dict[str, int]:
    rows = [
        (
            m.name,
            loaded.handlers.get(m.name),
            m.owner_mod,
            m.amperage,
            m.backend,
            _bit(m.nei_register),
            m.max_item_in,
            m.max_item_out,
            m.max_fluid_in,
            m.max_fluid_out,
        )
        for m in loaded.maps
    ]
    conn.executemany(
        "INSERT INTO recipe_map(unloc_name, name_en, owner_mod, amperage, backend_class,"
        " nei_visible, max_item_in, max_item_out, max_fluid_in, max_fluid_out)"
        " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(unloc_name) DO UPDATE SET"
        " name_en = coalesce(excluded.name_en, name_en), owner_mod = excluded.owner_mod,"
        " amperage = excluded.amperage, backend_class = excluded.backend_class,"
        " nei_visible = excluded.nei_visible, max_item_in = excluded.max_item_in,"
        " max_item_out = excluded.max_item_out, max_fluid_in = excluded.max_fluid_in,"
        " max_fluid_out = excluded.max_fluid_out",
        rows,
    )
    return {
        row[1]: row[0] for row in conn.execute("SELECT id, unloc_name FROM recipe_map")
    }


def _handlers(
    conn: sqlite3.Connection, loaded: Loaded, maps: dict[str, int]
) -> dict[str, int]:
    owners = {c: o for m in loaded.maps for c, o in m.categories}
    rows = []
    for handler, name in sorted(loaded.handlers.items()):
        space = loaded.categories.get(handler)
        rows.append(
            (handler, name, owners.get(handler), maps.get(space) if space else None)
        )
    conn.executemany(
        "INSERT INTO nei_handler(handler_name, name_en, mod, recipe_map_id) VALUES (?,?,?,?)"
        " ON CONFLICT(handler_name) DO UPDATE SET name_en = excluded.name_en,"
        " mod = excluded.mod, recipe_map_id = excluded.recipe_map_id",
        rows,
    )
    return {
        row[1]: row[0]
        for row in conn.execute("SELECT id, handler_name FROM nei_handler")
    }


def _recipes(
    conn: sqlite3.Connection,
    loaded: Loaded,
    run_id: int,
    items: dict[Ref, int],
    fluids: dict[str, int],
    ores: dict[str, int],
    maps: dict[str, int],
    handlers: dict[str, int],
) -> None:
    def ids(ref: Ref) -> tuple[int | None, int | None]:
        return (items[ref], None) if ref.kind == ITEM else (None, fluids[ref.name])

    first = conn.execute("SELECT coalesce(max(id), 0) + 1 FROM recipe").fetchone()[0]
    recipes, inputs, alts, outputs, metas = [], [], [], [], []
    for n, r in enumerate(loaded.recipes):
        rid = first + n
        space_map = (
            loaded.categories.get(r.handler or "") if r.source == "nei" else r.space
        )
        gt = r.source in ("gt", "gt+nei")
        f = r.flags
        known = r.source in (
            "crafting",
            "furnace",
        )  # NEI: a real crafting or furnace recipe
        recipes.append(
            (
                rid,
                run_id,
                r.source,
                maps.get(space_map) if space_map else None,
                handlers.get(r.handler) if r.handler else None,
                r.category or (r.handler if r.handler in loaded.categories else None),
                r.eut,
                r.duration,
                r.amperage,
                r.voltage_tier,
                r.special_value,
                _bit(f["enabled"]) if gt else 1,
                _bit(f["hidden"]) if gt else 0,
                _bit(f["fake"]) if gt else (0 if known else None),
                _bit(f["needs_empty_output"]) if gt else None,
                _bit(f["nbt_sensitive"]) if gt else None,
                r.signature,
                r.content_hash,
                canonical(r.owners) if r.owners else None,
                r.ref,
            )
        )
        returns = []
        for slot, s in enumerate(r.inputs):
            item_id, fluid_id = ids(s.ref)
            ore_id = ores.get(s.ore) if s.ore else None
            inputs.append(
                (
                    rid,
                    slot,
                    item_id,
                    fluid_id,
                    ore_id,
                    s.amount,
                    int(s.consumed),
                    s.chance_bp,
                    s.circuit,
                )
            )
            alts.extend((rid, slot, *ids(a)) for a in s.alts)
            if s.returns is not None:
                returns.append([slot, s.returns.text()])
        for slot, s in enumerate(r.outputs):
            outputs.append((rid, slot, *ids(s.ref), s.amount, s.chance_bp))
        meta: dict[str, Json] = dict(r.meta)
        if gt:
            meta["can_be_buffered"] = f["can_be_buffered"]
        if returns:
            meta["returns"] = returns
        metas.extend((rid, k, *_meta_value(v)) for k, v in sorted(meta.items()))
    conn.executemany(
        "INSERT INTO recipe(id, run_id, source, recipe_map_id, nei_handler_id, category, eut,"
        " duration_ticks, amperage, voltage_tier, special_value, enabled, hidden, fake,"
        " needs_empty_output, nbt_sensitive, signature, content_hash, owners, ref)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        recipes,
    )
    conn.executemany(
        "INSERT INTO recipe_input(recipe_id, slot, item_id, fluid_id, ore_id, amount, consumed,"
        " chance_bp, circuit) VALUES (?,?,?,?,?,?,?,?,?)",
        inputs,
    )
    conn.executemany(
        "INSERT INTO recipe_input_alt(recipe_id, slot, item_id, fluid_id) VALUES (?,?,?,?)",
        alts,
    )
    conn.executemany(
        "INSERT INTO recipe_output(recipe_id, slot, item_id, fluid_id, amount, chance_bp)"
        " VALUES (?,?,?,?,?,?)",
        outputs,
    )
    conn.executemany(
        "INSERT INTO recipe_meta(recipe_id, key, value_num, value_text) VALUES (?,?,?,?)",
        metas,
    )


def _machines(
    conn: sqlite3.Connection,
    loaded: Loaded,
    items: dict[Ref, int],
    maps: dict[str, int],
) -> None:
    rows = [
        (
            m.mte_id,
            m.class_name,
            m.mod,
            items[m.ref],
            m.name,
            int(m.multiblock),
            m.tier,
            "\n".join(m.tooltip),
            "\n".join(m.structure_tooltip) or None,
        )
        for m in sorted(loaded.machines, key=lambda m: m.mte_id)
    ]
    conn.executemany(
        "INSERT INTO machine(mte_id, class_name, mod_id, item_id, name_en, is_multiblock, tier,"
        " tooltip, structure_tooltip) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(mte_id) DO UPDATE"
        " SET class_name = excluded.class_name, mod_id = excluded.mod_id,"
        " item_id = excluded.item_id, name_en = excluded.name_en,"
        " is_multiblock = excluded.is_multiblock, tier = excluded.tier,"
        " tooltip = excluded.tooltip, structure_tooltip = excluded.structure_tooltip",
        rows,
    )
    ids = {row[1]: row[0] for row in conn.execute("SELECT id, mte_id FROM machine")}
    # Each machine's maps and facts are the latest run's: the machine pass's flags, the
    # tooltip facts and the curated table (WO-1.7). Probe rows (Lab measurements) are kept.
    conn.execute("DELETE FROM machine_map")
    conn.execute(
        "DELETE FROM machine_fact WHERE (source = 'code' AND note = ?)"
        " OR source IN ('tooltip', 'curated')",
        (PASS_NOTE,),
    )
    mm, facts = [], []
    for m in loaded.machines:
        mid = ids[m.mte_id]
        seen: set[str] = set()
        for priority, name in enumerate(m.recipe_maps):
            if name in maps and name not in seen:
                seen.add(name)
                mm.append((mid, maps[name], priority))
        facts.extend(
            (
                mid,
                k,
                "true" if v else "false",
                None,
                "code",
                1.0,
                PASS_NOTE,
            )
            for k, v in sorted(m.flags.items())
        )
        if loaded.facts is not None:
            facts.extend(
                (mid, f.key, f.value, f.unit, f.source, f.confidence, f.note)
                for f in loaded.facts.facts.get(m.mte_id, [])
            )
    conn.executemany(
        "INSERT INTO machine_map(machine_id, recipe_map_id, priority) VALUES (?,?,?)",
        mm,
    )
    conn.executemany(
        "INSERT INTO machine_fact(machine_id, key, value, unit, source, confidence, note)"
        " VALUES (?,?,?,?,?,?,?)",
        facts,
    )
    if loaded.facts is not None:
        # The coverage report counts Lab probes too: they can clear a flag (D111).
        for mte_id, key, value, confidence, note in conn.execute(
            "SELECT m.mte_id, f.key, f.value, f.confidence, f.note FROM machine_fact AS f"
            " JOIN machine AS m ON m.id = f.machine_id WHERE f.source = 'probe'"
        ):
            try:
                machine_facts.check_value(key, value)
            except ValueError:
                continue  # not in WO-1.7's vocabulary: the report can't read it
            loaded.facts.facts.setdefault(mte_id, []).append(
                machine_facts.Fact(key, value, "probe", confidence, note or "")
            )


def _names(conn: sqlite3.Connection) -> None:
    """Rebuild ``name_fts`` from the catalog tables."""
    conn.execute("DELETE FROM name_fts")
    sources = (
        "SELECT 'item', id, name_en, name_zh, registry_name || '@' || meta FROM item",
        "SELECT 'fluid', id, name_en, NULL, name FROM fluid",
        "SELECT 'machine', m.id, m.name_en, NULL, m.class_name FROM machine AS m",
        "SELECT 'recipe_map', id, name_en, NULL, unloc_name FROM recipe_map",
        "SELECT 'nei_handler', id, name_en, NULL, handler_name FROM nei_handler",
    )
    for select in sources:
        conn.execute(
            "INSERT INTO name_fts(kind, ref_id, name_en, name_zh, registry_name) "
            + select
        )
    # Crops of the newest run that has some (WO-1.10); ref_id is the crop id. Called after
    # pruning, so it never indexes a run the build deletes.
    runs = crop_db.runs_with_crops(conn)
    if runs:
        conn.execute(
            "INSERT INTO name_fts(kind, ref_id, name_en, name_zh, registry_name)"
            " SELECT 'crop', id, name, NULL, id FROM crop WHERE run_id = ?",
            (runs[0][0],),
        )


def prune(conn: sqlite3.Connection, keep: int, current: int) -> list[str]:
    """Delete runs past the newest ``keep``, except ``current`` and runs plans reference."""
    runs = conn.execute(
        "SELECT id, run_key FROM export_run ORDER BY created_at DESC, run_key DESC"
    ).fetchall()
    pinned = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT run_id FROM plan WHERE run_id IS NOT NULL"
        )
    }
    doomed = [r for r in runs[keep:] if r[0] != current and r[0] not in pinned]
    _delete_runs(conn, [r[0] for r in doomed])
    conn.executemany("DELETE FROM export_run WHERE id = ?", [(r[0],) for r in doomed])
    return [str(r[1]) for r in doomed]


def write(
    conn: sqlite3.Connection, loaded: Loaded, *, fingerprint: str = "", keep: int = 2
) -> tuple[int, bool, list[str]]:
    """Write ``loaded`` in one transaction: (export_run id, replaced, pruned run keys)."""
    conn.execute("BEGIN IMMEDIATE")  # waits up to 30 s for another writer
    try:
        run_id, replaced = _export_run(conn, loaded, fingerprint)
        mods = loaded.run.manifest.get("mods")
        conn.executemany(
            "INSERT OR REPLACE INTO mod(run_id, modid, version, jar, jar_sha256)"
            " VALUES (?,?,?,?,?)",
            [
                (
                    run_id,
                    m.get("modid"),
                    m.get("version"),
                    m.get("file"),
                    m.get("sha256"),
                )
                for m in (mods if isinstance(mods, list) else [])
                if isinstance(m, dict) and isinstance(m.get("modid"), str)
            ],
        )
        item_refs, fluid_refs = _refs(loaded)
        items = _items(conn, loaded, item_refs)
        fluids = _fluids(conn, loaded, fluid_refs)
        ores = _ores(conn, loaded, items)
        maps = _maps(conn, loaded)
        handlers = _handlers(conn, loaded, maps)
        _recipes(conn, loaded, run_id, items, fluids, ores, maps, handlers)
        if loaded.crops is not None:
            crop_db.write(conn, run_id, loaded.crops, items)
        _machines(conn, loaded, items, maps)
        pruned = prune(conn, keep, run_id)
        _names(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return run_id, replaced, pruned


def _data_dir(gos_home: Path) -> Path:
    data = gos_home / "data"
    for path in (gos_home, data):
        if os.path.lexists(path) and (
            collect._is_link(os.lstat(path)) or not path.is_dir()
        ):
            raise BuildError(f"{path} is a link, a junction or not a folder")
    return data


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def curation_task(gos_home: Path, run_key: str, error: merge.CollisionError) -> Path:
    """Write the collisions as rows ready for the allow-list, with what differs. While the
    file can't be replaced (Windows: open in Excel, which locks it), the next free name
    ``collisions-<run>-2.tsv``, ``-3`` ... is used; OSError when none can be written."""
    text = report.collisions_tsv(run_key, error.collisions)
    folder = gos_home / CURATION
    for n in range(1, 10):
        tail = f"-{n}" if n > 1 else ""
        path = folder / f"collisions-{run_key}{tail}.tsv"
        try:
            _write_text(path, text)
        except PermissionError:
            continue  # held open by another program
        return path
    raise PermissionError(f"{folder}: no collisions-{run_key}*.tsv can be written")


def build(
    gos_home: Path,
    run_key: str | None = None,
    *,
    keep: int = 2,
    fingerprint: str = "",
    allow_harness: bool = False,
    allow_list: Path | None = None,
    curated: Path | None = None,
    db: Path | None = None,
) -> Result:
    """Load a collected run into the DB (``db``: default ``GOS_HOME/data/gos.sqlite``).

    ``allow_list`` defaults to the repo's ``core/gregtec/ingest/collisions.tsv`` and
    ``curated`` to its ``data/curated/machine_facts.tsv``; ``allow_harness`` is for tests only.
    """
    data = _data_dir(gos_home)
    allow_list = allow_list or merge.ALLOW_LIST
    try:
        allowed = merge.read_allow_list(allow_list)
    except ValueError as exc:
        raise BuildError(f"allow-list: {exc}") from None
    try:
        facts = machine_facts.read_curated(curated or machine_facts.CURATED)
    except (OSError, ValueError) as exc:
        raise BuildError(f"curated machine facts: {exc}") from None
    if not (gos_home / collect.DATA_EXPORTS).is_dir():
        raise BuildError(
            f"{gos_home / collect.DATA_EXPORTS} doesn't exist: run `gos export collect` first"
        )
    try:
        # Collect's own lock, so a collect can't prune or swap the run while it is read.
        with collect.lock(gos_home):
            run = find_run(gos_home, run_key, allow_harness)
            verify(run)
            loaded = load(run, allowed, facts)
    except collect.CollectError as exc:
        raise BuildError(str(exc)) from None
    except gtdump.FormatError as exc:
        raise BuildError(f"run {run.run_id}: {exc}") from None
    except merge.CollisionError as exc:
        try:
            where = f"The curation task is {curation_task(gos_home, run.run_id, exc)}"
        except OSError as write_error:
            where = (
                f"The curation task couldn't be written ({write_error}); the collisions are "
                + "; ".join(f"{c.space} {c.sig12}" for c in exc.collisions[:20])
            )
        raise BuildError(
            f"run {run.run_id}: {exc}. Nothing was written to the DB. {where}: check each "
            f"collision in NEI, then add the genuine ones to {allow_list.name} "
            "(core/gregtec/ingest/) and build again"
        ) from None
    path = db or access.db_path(gos_home)
    if os.path.lexists(path) and collect._is_link(os.lstat(path)):
        raise BuildError(f"{path} is a link; refusing it")
    data.mkdir(parents=True, exist_ok=True)
    conn = access.connect(path)
    try:
        run_id, replaced, pruned = write(
            conn, loaded, fingerprint=fingerprint, keep=keep
        )
    finally:
        conn.close()
    result = Result(run_id, loaded, path, pruned, replaced)
    result.report_path = gos_home / REPORTS / f"db-{run.run_id}.txt"
    try:
        _write_text(
            result.report_path, "\n".join(report.lines(result, full=True)) + "\n"
        )
    except OSError as exc:
        # The DB is built; only the saved copy of the report is missing.
        result.report_error = f"{result.report_path}: {exc}"
        result.report_path = None
    return result


def main(config_path: Path | None, run_key: str | None, out: TextIO) -> int:
    """0: built; 1: refused or failed (a collision included); 2: bad gos.toml."""
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        result = build(cfg.gos_home, run_key, keep=cfg.keep_runs)
    except (BuildError, access.SchemaError, OSError, sqlite3.Error) as exc:
        print(f"Not built: {exc}", file=out)
        return 1
    for line in report.lines(result, full=False):
        print(line, file=out)
    return 0

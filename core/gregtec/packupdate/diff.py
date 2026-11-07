"""The pack-update diff of two export runs (WO-1.9, plan section 7.13, D147).

On top of WO-1.4's manifest and file-hash diff (:mod:`gregtec.ingest.rundiff`, D122) it compares
what the runs hold:

- **Recipes**, from the DB (D104-D108): by citation key ``r:<map>/<sig12>``, a key only the new
  run has is *added*, one only the old run has is *removed*, and one whose content hash differs is
  *changed* (a rebalance keeps its key, plan 5.2 step 3). Each is put under the mod that added it:
  GT's recorded recipe owner (D96), else the recipe map's owner, else the mod of its first
  output, else its NEI handler's mod.
- **Crops** (WO-1.10, :func:`gregtec.ingest.crops.diff`) from the DB.
- **The ore dictionary** (WO-1.1c, D145) from the two collected runs' ``oredict.jsonl``: names
  added and removed, members added and removed, grouped by the member's mod.

The two runs may sit in two DBs: the playbook builds a Lab export into its own sandbox DB, so the
recipe tools never answer from a pack Clayton hasn't adopted. Everything here only reads.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.db import access
from gregtec.db import crops as crop_db
from gregtec.ingest import collect, crops, fingerprint, gtdump, rundiff
from gregtec.packupdate import gtpin

TOP_MODS = 20  # rows of the by-mod table
TOP_CHANGES = 25  # example recipes listed per kind of change
CHUNK = 500  # refs per detail query
SCALARS = (
    ("eut", "EU/t"),
    ("duration_ticks", "ticks"),
    ("amperage", "amps"),
    ("special_value", "special value"),
    ("voltage_tier", "tier"),
    ("enabled", "enabled"),
    ("hidden", "hidden"),
    ("fake", "fake"),
)


class DiffError(RuntimeError):
    """A run isn't in the DB, or the DB can't be read."""


@dataclass(frozen=True)
class Side:
    """One export run: the DB that holds it and its collected run folder."""

    db: Path
    run_id: int  # export_run.id in that DB
    run_key: str
    created_at: str | None
    pack_version: str | None
    gt_version: str | None
    nei_version: str | None
    folder: Path | None  # GOS_HOME/data/exports/<run_key>, when it is still there

    def label(self) -> str:
        return f"{self.run_key} (GT {self.gt_version}, {self.created_at})"


def connect(db: Path) -> sqlite3.Connection:
    if not db.is_file():
        raise DiffError(f"there is no DB at {db} (gos db build makes it)")
    try:
        return access.connect(db, readonly=True)
    except (sqlite3.Error, access.SchemaError) as exc:
        raise DiffError(f"can't open {db}: {exc}") from None


def side(db: Path, run_key: str | None, home: Path) -> Side:
    """The run ``run_key`` (default: the newest) in ``db``; its folder in ``home``'s exports."""
    conn = connect(db)
    try:
        if run_key is None:
            row = conn.execute(
                "SELECT id, run_key, created_at, pack_version, gt_version, nei_version"
                " FROM export_run ORDER BY created_at DESC, run_key DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, run_key, created_at, pack_version, gt_version, nei_version"
                " FROM export_run WHERE run_key = ?",
                (run_key,),
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        what = "no export run" if run_key is None else f"no run {run_key}"
        raise DiffError(f"{db} has {what}")
    folder = home / collect.DATA_EXPORTS / str(row[1])
    return Side(
        db,
        int(row[0]),
        str(row[1]),
        row[2],
        row[3],
        row[4],
        row[5],
        folder if (folder / collect.MANIFEST).is_file() else None,
    )


# --- recipes ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recipe:
    ref: str
    mod: str
    output: str  # the first output, for people
    values: tuple[Any, ...]  # SCALARS, in order


@dataclass(frozen=True)
class Changed:
    old: Recipe
    new: Recipe

    def what(self) -> str:
        parts = [
            f"{label} {a} -> {b}"
            for (_, label), a, b in zip(SCALARS, self.old.values, self.new.values)
            if a != b
        ]
        return (
            "; ".join(parts)
            if parts
            else "inputs or outputs' chances, oredict alternatives or metadata"
        )


@dataclass
class RecipeDiff:
    old_count: int
    new_count: int
    added: list[Recipe] = field(default_factory=list)
    removed: list[Recipe] = field(default_factory=list)
    changed: list[Changed] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def by_mod(self) -> list[tuple[str, int, int, int]]:
        """(mod, added, removed, changed), most changes first."""
        added = Counter(r.mod for r in self.added)
        removed = Counter(r.mod for r in self.removed)
        changed = Counter(c.new.mod for c in self.changed)
        mods = set(added) | set(removed) | set(changed)
        rows = [(m, added[m], removed[m], changed[m]) for m in mods]
        return sorted(rows, key=lambda r: (-(r[1] + r[2] + r[3]), r[0]))


def content_hashes(conn: sqlite3.Connection, run_id: int) -> dict[str, str]:
    """ref -> content hash of every recipe of ``run_id``."""
    return {
        str(ref): str(h)
        for ref, h in conn.execute(
            "SELECT ref, content_hash FROM recipe WHERE run_id = ?", (run_id,)
        )
    }


_DETAIL = (
    "SELECT r.ref, json_extract(r.owners, '$[0]'), m.owner_mod, h.mod,"
    " (SELECT coalesce(i.registry_name, 'fluid:' || f.name) || char(9) ||"
    "   coalesce(i.name_en, f.name_en, i.registry_name || '@' || i.meta, f.name)"
    "  FROM recipe_output AS o LEFT JOIN item AS i ON i.id = o.item_id"
    "  LEFT JOIN fluid AS f ON f.id = o.fluid_id"
    "  WHERE o.recipe_id = r.id ORDER BY o.slot LIMIT 1), "
    + ", ".join(f"r.{c}" for c, _ in SCALARS)
    + " FROM recipe AS r LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id"
    " LEFT JOIN nei_handler AS h ON h.id = r.nei_handler_id"
    " WHERE r.run_id = ? AND r.ref IN ({marks})"
)


def details(
    conn: sqlite3.Connection, run_id: int, refs: Iterable[str]
) -> dict[str, Recipe]:
    """The :class:`Recipe` of each ref of ``run_id`` in ``refs``."""
    wanted = sorted(set(refs))
    out: dict[str, Recipe] = {}
    for i in range(0, len(wanted), CHUNK):
        chunk = wanted[i : i + CHUNK]
        sql = _DETAIL.format(marks=",".join("?" * len(chunk)))
        for row in conn.execute(sql, (run_id, *chunk)):
            first = str(row[4] or "")
            registry, _, name = first.partition("\t")
            mod = row[1] or row[2] or _mod_of(registry) or row[3] or "unknown"
            output = f"{name} ({registry})" if registry and name else "(no output)"
            out[str(row[0])] = Recipe(str(row[0]), str(mod), output, tuple(row[5:]))
    return out


def _mod_of(registry: str) -> str | None:
    if not registry or registry.startswith("fluid:") or ":" not in registry:
        return None
    return registry.split(":", 1)[0]


def recipe_diff(
    old_conn: sqlite3.Connection,
    old_run: int,
    new_conn: sqlite3.Connection,
    new_run: int,
) -> RecipeDiff:
    a, b = content_hashes(old_conn, old_run), content_hashes(new_conn, new_run)
    added = sorted(b.keys() - a.keys())
    removed = sorted(a.keys() - b.keys())
    changed = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    old_info = details(old_conn, old_run, [*removed, *changed])
    new_info = details(new_conn, new_run, [*added, *changed])
    return RecipeDiff(
        len(a),
        len(b),
        added=[new_info[k] for k in added],
        removed=[old_info[k] for k in removed],
        changed=[Changed(old_info[k], new_info[k]) for k in changed],
    )


# --- the ore dictionary ----------------------------------------------------------------------


@dataclass
class OreDiff:
    names_added: list[str] = field(default_factory=list)
    names_removed: list[str] = field(default_factory=list)
    members_added: list[tuple[str, str]] = field(default_factory=list)  # (name, member)
    members_removed: list[tuple[str, str]] = field(default_factory=list)

    def empty(self) -> bool:
        return not (
            self.names_added
            or self.names_removed
            or self.members_added
            or self.members_removed
        )

    def by_mod(self) -> list[tuple[str, int, int]]:
        """(member's mod, members added, removed), most first."""
        added = Counter(_member_mod(m) for _, m in self.members_added)
        removed = Counter(_member_mod(m) for _, m in self.members_removed)
        rows = [(m, added[m], removed[m]) for m in set(added) | set(removed)]
        return sorted(rows, key=lambda r: (-(r[1] + r[2]), r[0]))


def _member_mod(member: str) -> str:
    return member.split(":", 1)[0] if ":" in member else "unknown"


def oredict_diff(
    old: Sequence[gtdump.OreName], new: Sequence[gtdump.OreName]
) -> OreDiff:
    """Pure: names and members added and removed (members as ``registry@meta``)."""
    a = {o.name: {m.text() for m in o.members} for o in old}
    b = {o.name: {m.text() for m in o.members} for o in new}
    d = OreDiff(sorted(b.keys() - a.keys()), sorted(a.keys() - b.keys()))
    for name in sorted(a.keys() | b.keys()):
        x, y = a.get(name, set()), b.get(name, set())
        d.members_added += [(name, m) for m in sorted(y - x)]
        d.members_removed += [(name, m) for m in sorted(x - y)]
    return d


def read_oredict(s: Side) -> tuple[list[gtdump.OreName] | None, str | None]:
    """(names, None), or (None, why they can't be compared)."""
    if s.folder is None:
        return None, f"run {s.run_key}'s collected folder is gone"
    try:
        run = rundiff.load_run(s.folder)
    except rundiff.RunError as exc:
        return None, str(exc)
    status = gtdump.oredict_status(run.manifest)
    if status != "ok":
        return None, f"run {s.run_key}'s oredict part is {status}"
    try:
        return list(gtdump.read_oredict(s.folder)), None
    except (OSError, gtdump.FormatError) as exc:
        return None, f"run {s.run_key}'s {gtdump.ORE_DICT} doesn't read: {exc}"


# --- the whole summary -----------------------------------------------------------------------


@dataclass
class Summary:
    old: Side
    new: Side
    manifest: rundiff.Diff | None
    manifest_note: str | None
    recipes: RecipeDiff
    crops: crops.CropDiff | None
    crops_note: str | None
    oredict: OreDiff | None
    oredict_note: str | None
    gt_problem: str | None
    notes: list[str] = field(default_factory=list)

    def headline(self) -> str:
        r = self.recipes
        return (
            f"{self.old.run_key} -> {self.new.run_key}: recipes {r.old_count:,} -> "
            f"{r.new_count:,} ({len(r.added):,} added, {len(r.removed):,} removed, "
            f"{len(r.changed):,} changed)"
            + ("" if self.gt_problem is None else "; GT PIN MISMATCH")
        )


def _crop_side(conn: sqlite3.Connection, run_id: int) -> crops.CropData:
    return crop_db.read(conn, run_id)


def summarise(
    old: Side,
    new: Side,
    *,
    play_old: fingerprint.Entries | None = None,
    play_new: fingerprint.Entries | None = None,
) -> Summary:
    """Compare ``old`` with ``new``. ``play_old``/``play_new`` are Play's (or Lab's) listings
    for the manifest diff's file section, when recorded."""
    manifest: rundiff.Diff | None = None
    manifest_note = None
    if old.folder is None or new.folder is None:
        manifest_note = "a collected run folder is gone: no manifest-level diff"
    else:
        try:
            manifest = rundiff.diff(
                rundiff.load_run(old.folder),
                rundiff.load_run(new.folder),
                play_old,
                play_new,
            )
        except rundiff.RunError as exc:
            manifest_note = f"no manifest-level diff: {exc}"
    old_conn, new_conn = connect(old.db), connect(new.db)
    try:
        recipes = recipe_diff(old_conn, old.run_id, new_conn, new.run_id)
        crop_old = _crop_side(old_conn, old.run_id)
        crop_new = _crop_side(new_conn, new.run_id)
    finally:
        old_conn.close()
        new_conn.close()
    crop_d: crops.CropDiff | None = None
    crops_note = None
    if not crop_old.crops or not crop_new.crops:
        crops_note = "not compared: a run has no crop data (parts.crops not ok)"
    else:
        crop_d = crops.diff(crop_old, crop_new)
    names_old, why_old = read_oredict(old)
    names_new, why_new = read_oredict(new)
    ore_d: OreDiff | None = None
    oredict_note = None
    if names_old is None or names_new is None:
        oredict_note = f"not compared: {why_old or why_new}"
    else:
        ore_d = oredict_diff(names_old, names_new)
    return Summary(
        old,
        new,
        manifest,
        manifest_note,
        recipes,
        crop_d,
        crops_note,
        ore_d,
        oredict_note,
        gtpin.problem(new.gt_version),
    )


def _cut(rows: list[str], limit: int, what: str) -> list[str]:
    if len(rows) <= limit:
        return rows
    return [*rows[:limit], f"    ... and {len(rows) - limit:,} more {what}"]


def _ranked(recipes: Sequence[Recipe], order: Mapping[str, int]) -> list[Recipe]:
    return sorted(recipes, key=lambda r: (order.get(r.mod, len(order)), r.ref))


def report(s: Summary, plan_lines: Sequence[str] = ()) -> str:
    """The summary for people: what Clayton reads before he answers "adopt?"."""
    r = s.recipes
    lines = [
        "Pack-update diff (recipes, crops, ore dictionary)",
        f"  old: {s.old.label()}  [{s.old.db}]",
        f"  new: {s.new.label()}  [{s.new.db}]",
        "",
    ]
    if s.gt_problem is not None:
        lines += [f"!!! {s.gt_problem}", ""]
    lines.append(f"Recipes: {r.old_count:,} -> {r.new_count:,}")
    lines.append(
        f"  {len(r.added):,} added, {len(r.removed):,} removed, "
        f"{len(r.changed):,} changed (same key, other content)"
    )
    mods = r.by_mod()
    order = {m[0]: i for i, m in enumerate(mods)}
    if mods:
        lines.append("  by mod (added, removed, changed):")
        lines += _cut(
            [f"    {m:<24} +{a:<7,} -{d:<7,} ~{c:,}" for m, a, d, c in mods],
            TOP_MODS,
            "mods",
        )
    for title, rows in (
        (
            "Removed recipes (plans citing them are flagged)",
            [f"    - {x.ref}  [{x.mod}] {x.output}" for x in _ranked(r.removed, order)],
        ),
        (
            "Changed recipes (plans citing them are flagged)",
            [
                f"    ~ {c.new.ref}  [{c.new.mod}] {c.new.output}: {c.what()}"
                for c in sorted(
                    r.changed,
                    key=lambda c: (order.get(c.new.mod, len(order)), c.new.ref),
                )
            ],
        ),
        (
            "Added recipes",
            [f"    + {x.ref}  [{x.mod}] {x.output}" for x in _ranked(r.added, order)],
        ),
    ):
        if rows:
            lines.append(f"  {title}:")
            lines += _cut(rows, TOP_CHANGES, "recipes")
    lines.append("")
    if s.crops is None:
        lines.append(f"Crops: {s.crops_note}")
    else:
        lines += crops.diff_lines(s.crops, limit=TOP_CHANGES)
    lines.append("")
    o = s.oredict
    if o is None:
        lines.append(f"Ore dictionary: {s.oredict_note}")
    elif o.empty():
        lines.append("Ore dictionary: no change")
    else:
        lines.append(
            f"Ore dictionary: {len(o.names_added):,} names added, "
            f"{len(o.names_removed):,} removed; members {len(o.members_added):,} added, "
            f"{len(o.members_removed):,} removed"
        )
        lines += _cut(
            [f"    {m:<24} +{a:<7,} -{d:,}" for m, a, d in o.by_mod()],
            TOP_MODS,
            "mods",
        )
        rows = [f"    + ore:{n}" for n in o.names_added]
        rows += [f"    - ore:{n}" for n in o.names_removed]
        rows += [f"    + ore:{n} {m}" for n, m in o.members_added]
        rows += [f"    - ore:{n} {m}" for n, m in o.members_removed]
        lines += _cut(rows, TOP_CHANGES, "oredict changes")
    if plan_lines:
        lines += ["", *plan_lines]
    lines.append("")
    if s.manifest is None:
        lines.append(f"Manifest: {s.manifest_note}")
    else:
        lines.append(rundiff.report(s.manifest).rstrip("\n"))
    if s.notes:
        lines += ["", "Notes:", *(f"  {n}" for n in s.notes)]
    return "\n".join(lines) + "\n"


def note_lines(s: Summary, flagged: Sequence[str]) -> list[str]:
    """The vault inbox note's body (``_gos/schema.md`` grammar: key: value lines and one TSV
    table, under 150 lines)."""
    r = s.recipes
    out = [
        f"old_run: {s.old.run_key}",
        f"new_run: {s.new.run_key}",
        f"gt_old: {s.old.gt_version}",
        f"gt_new: {s.new.gt_version}",
        f"recipes_added: {len(r.added)}",
        f"recipes_removed: {len(r.removed)}",
        f"recipes_changed: {len(r.changed)}",
        f"plans_affected: {', '.join(flagged) if flagged else 'none'}",
    ]
    if s.gt_problem is not None:
        out.append("note: GT pin mismatch; the GT pin bump comes before adopting")
    if s.crops is not None:
        c = s.crops
        out.append(
            f"crops_changed: {len(c.crops_added)} added, {len(c.crops_removed)} removed, "
            f"{len({x.key for x in c.crops_changed})} changed"
        )
    if s.oredict is not None:
        out.append(
            f"oredict_members: {len(s.oredict.members_added)} added, "
            f"{len(s.oredict.members_removed)} removed"
        )
    out += ["```tsv", "mod\tadded\tremoved\tchanged"]
    out += [f"{m}\t{a}\t{d}\t{c}" for m, a, d, c in r.by_mod()[:TOP_MODS]]
    out.append("```")
    return out

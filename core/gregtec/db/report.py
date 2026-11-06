"""The counts report of ``gos db build`` and its collision curation task (WO-1.3, D108).

The report gives counts per mod (recipe maps a mod owns, the recipes in them, the recipes it
added, its machines and items) and the addon check of plan section 8: every recipe-map addon
that loads (TST, GT-Not-Leisure, 123Technology) owns at least one recipe map. Programmable
Hatches adds its recipes to GT's own maps and owns none [A§3], so it is listed with no map
expected. An addon is matched to Export's loaded mods by modid or mod name, case ignored; the
modids are the ones in each addon's ``gradle.properties``, still to be confirmed on the first
full-pack run (Questions, WO-1.3).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gregtec.ingest.merge import ALLOW_HEADER, Collision
from gregtec.ingest.normalize import ITEM, Recipe, canonical

if TYPE_CHECKING:
    from gregtec.db.build import Result


@dataclass(frozen=True)
class Addon:
    title: str
    modids: tuple[str, ...]
    names: tuple[str, ...]
    owns_maps: bool  # whether the addon registers recipe maps of its own [A§3]


ADDONS = (
    Addon(
        "Twist Space Technology",
        ("TwistSpaceTechnology",),
        ("Twist Space Technology",),
        True,
    ),
    Addon(
        "GT-Not-Leisure",
        ("sciencenotleisure",),
        ("GTNotLeisure", "GT Not Leisure"),
        True,
    ),
    Addon("123Technology", ("othtech",), ("123Technology",), True),
    Addon(
        "Programmable Hatches",
        ("programmablehatches",),
        ("ProgrammableHatches",),
        False,
    ),
)


def _mods(manifest: dict[str, object]) -> list[dict[str, object]]:
    mods = manifest.get("mods")
    return [m for m in mods if isinstance(m, dict)] if isinstance(mods, list) else []


def _gt(r: Recipe) -> bool:
    return r.source in ("gt", "gt+nei")


def per_mod(result: Result) -> list[tuple[str, int, int, int, int, int]]:
    """(mod, maps owned, recipes in them, recipes added, machines, items), by mod, case ignored."""
    loaded = result.loaded
    names: dict[str, str] = {}

    def k(mod: str) -> str:
        names.setdefault(mod.lower(), mod)
        return mod.lower()

    owner = {m.name: m.owner_mod for m in loaded.maps}
    maps = Counter(k(m.owner_mod) for m in loaded.maps if m.owner_mod)
    in_maps: Counter[str] = Counter()
    added: Counter[str] = Counter()
    for r in loaded.recipes:
        if not _gt(r):
            continue
        if owner.get(r.space):
            in_maps[k(owner[r.space] or "")] += 1
        for o in dict.fromkeys(r.owners):
            added[k(o)] += 1
    machines = Counter(k(m.mod) for m in loaded.machines if m.mod)
    items: Counter[str] = Counter()
    seen = set()
    for r in loaded.recipes:
        for s in (*r.inputs, *r.outputs):
            if s.ref.kind == ITEM and s.ref not in seen:
                seen.add(s.ref)
    seen.update(i.ref for i in loaded.items)
    for ref in seen:
        if ":" in ref.name:
            items[k(ref.name.split(":", 1)[0])] += 1
    for m in _mods(loaded.run.manifest):
        if isinstance(m.get("modid"), str):
            k(str(m["modid"]))
    rows = []
    for key in sorted(names):
        row = (maps[key], in_maps[key], added[key], machines[key], items[key])
        if any(row):
            rows.append((names[key], *row))
    return rows


def addon_lines(result: Result) -> list[str]:
    loaded = result.loaded
    mods = _mods(loaded.run.manifest)
    owners = Counter((m.owner_mod or "").lower() for m in loaded.maps)
    lines = []
    for addon in ADDONS:
        ids = {i.lower() for i in addon.modids}
        titles = {n.lower() for n in addon.names}
        found = [
            m
            for m in mods
            if str(m.get("modid", "")).lower() in ids
            or str(m.get("name", "")).lower() in titles
        ]
        if not found:
            lines.append(f"  not loaded  {addon.title}")
            continue
        modid = str(found[0].get("modid"))
        version = found[0].get("version")
        count = owners[modid.lower()]
        added = sum(
            1
            for r in loaded.recipes
            if _gt(r) and modid.lower() in {o.lower() for o in r.owners}
        )
        what = f"{addon.title} ({modid} {version}): {count} recipe maps, {added:,} recipes added"
        if addon.owns_maps and count == 0:
            lines.append(f"  MISSING     {what}: it loads but owns no recipe map")
        elif addon.owns_maps:
            lines.append(f"  ok          {what}")
        else:
            lines.append(
                f"  ok          {what} (it adds to GT's own maps, none expected)"
            )
    return lines


def map_count_lines(result: Result) -> list[str]:
    """Maps with errors, or whose lines in gt_recipes.jsonl differ from their ``count`` in
    gt_recipe_maps.json (a recipe the mod didn't write)."""
    loaded = result.loaded
    out = []
    for m in loaded.maps:
        if m.errors:
            out.append(f"  map {m.name}: errors {'; '.join(m.errors)}")
        if m.count is not None and m.count != loaded.map_lines[m.name]:
            out.append(
                f"  map {m.name}: {loaded.map_lines[m.name]:,} recipe lines, but "
                f"gt_recipe_maps.json says {m.count:,}"
            )
    return out


def lines(result: Result, full: bool) -> list[str]:
    loaded = result.loaded
    m = loaded.run.manifest
    stats = loaded.stats
    keys = loaded.keys
    assert stats is not None and keys is not None
    by_source = Counter(r.source for r in loaded.recipes)
    counts = m.get("counts") if isinstance(m.get("counts"), dict) else {}
    assert isinstance(counts, dict)
    link = m.get("gregteclink")
    version = link.get("version") if isinstance(link, dict) else None
    nei_only = by_source["crafting"] + by_source["furnace"] + by_source["nei"]
    replacing = ", replacing its earlier load" if result.replaced else ""
    out = [
        (
            f"Built {result.db} from run {loaded.run.run_id} "
            f"(export_run {result.run_id}{replacing})"
        ),
        (
            f"  created {loaded.run.created_utc}, mode {m.get('mode')}, "
            f"GT {m.get('gt_version')}, NEI {m.get('nei_version')}, GregtecLink {version}"
        ),
        (
            f"Recipes: {len(loaded.recipes):,} citation keys: GT {by_source['gt']:,}, "
            f"GT+NEI {by_source['gt+nei']:,}, NEI only {nei_only:,} (crafting "
            f"{by_source['crafting']:,}, furnace {by_source['furnace']:,}, other handlers "
            f"{by_source['nei']:,})"
        ),
        (
            f"  GT: {loaded.gt_recipes:,} recipe lines, {len(loaded.recipe_errors):,} the mod "
            f"couldn't serialise, {keys.duplicates:,} rows dropped as copies of another recipe"
        ),
    ]
    if loaded.nei_status == "ok":
        warn = m.get("part_warnings")
        warning = warn.get("nei") if isinstance(warn, dict) else None
        out.append(
            f"  NEI: {stats.nei_rows:,} lines: {stats.exact:,} merged by identity hash, "
            f"{stats.matched:,} by slots, {sum(stats.duplicates.values()):,} duplicates, "
            f"{sum(stats.dropped.values()):,} GT recipes NEI shows in another form (dropped), "
            f"{stats.nei_only:,} NEI-only"
        )
        if warning:
            out.append(f"  NEI warning: {warning}")
    else:
        out.append(
            f"  NEI: not used (parts.nei is {loaded.nei_status!r}; GT recipes only)"
        )
    allowed = sum(len(c.recipes) for c in keys.allowed)
    out.append(
        f"Collision test: passed, every key is one recipe; {len(keys.allowed)} allow-listed "
        f"collisions ({allowed} recipes, keys -1, -2, ...)"
    )
    for space, sig12 in keys.stale:
        out.append(f"  stale allow-list entry (no collision now): {space}\t{sig12}")
    items = {i.ref for i in loaded.items}
    out.append(
        f"Catalog: {len(loaded.maps)} recipe maps, {len(loaded.handlers)} NEI handlers, "
        f"{len(loaded.machines):,} machines, {len(items):,} items listed by GT "
        f"({sum(1 for r in items if r.nbt_hash):,} with NBT), {len(loaded.fluids):,} fluids"
    )
    problems = [
        ("recipe map errors", counts.get("recipe_map_errors")),
        ("recipe errors", counts.get("recipe_errors")),
        ("machine errors", counts.get("machine_errors")),
        ("item errors", counts.get("item_errors")),
        ("unregistered refs", counts.get("unregistered")),
    ]
    out.append("Export errors: " + ", ".join(f"{n} {v}" for n, v in problems))
    out.extend(map_count_lines(result))
    out.append("Addons (recipe maps > 0 for every recipe-map addon that loads):")
    out.extend(addon_lines(result))
    out.append(
        "Per mod: recipe maps owned, recipes in them, recipes added, machines, items"
    )
    rows = per_mod(result)
    width = max((len(r[0]) for r in rows), default=4)
    for mod, *nums in rows:
        out.append(f"  {mod:<{width}}  " + "  ".join(f"{n:>7,}" for n in nums))
    if full and loaded.nei_status == "ok":
        out.append("NEI handlers: handler, name, NEI-only recipes, dropped")
        only = Counter(r.handler for r in loaded.recipes if not _gt(r))
        for handler, name in sorted(loaded.handlers.items()):
            out.append(
                f"  {handler}\t{name}\t{only[handler]}\t{stats.dropped[handler]}"
            )
    for key in result.pruned:
        out.append(f"Pruned run {key} from the DB (keeping the newest [db] keep_runs)")
    if result.report_path is not None:
        out.append(f"Report: {result.report_path}")
    return out


def _summary(r: Recipe) -> dict[str, object]:
    return {
        "category": r.category,
        "eut": r.eut,
        "duration": r.duration,
        "amperage": r.amperage,
        "special": r.special_value,
        **{f"flag {k}": v for k, v in r.flags.items()},
        **{f"meta {k}": canonical(v) for k, v in r.meta.items()},
        "input chances": sorted((s.ref.text(), s.chance_bp) for s in r.inputs),
        "input alternatives": sorted(
            (s.ref.text(), tuple(a.text() for a in s.alts), s.ore) for s in r.inputs
        ),
        "output chances": sorted((s.ref.text(), s.chance_bp) for s in r.outputs),
    }


def differences(recipes: Sequence[Recipe]) -> list[str]:
    """What differs between the recipes of one collision, e.g. ``duration 100 | 20``."""
    summaries = [_summary(r) for r in recipes]
    fields = dict.fromkeys(k for s in summaries for k in s)
    out = []
    for f in fields:
        values = [s.get(f) for s in summaries]
        if any(v != values[0] for v in values):
            shown = [
                str(v) if len(str(v)) <= 60 else str(v)[:57] + "..." for v in values
            ]
            out.append(f"{f} {' | '.join(shown)}")
    return out


def collisions_tsv(run_key: str, collisions: Iterable[Collision]) -> str:
    out = [
        f"# Identity collisions in run {run_key} (WO-1.3, D106): different recipes whose",
        "# citation keys r:<map>/<sig12> would be the same. The build failed and wrote nothing.",
        "# Check each in NEI. A genuine pair (two recipes GT really has, e.g. one registered",
        "# twice with different timings) goes into core/gregtec/ingest/collisions.tsv as its row",
        "# below, with a real reason in place of TODO; its recipes then get the keys -1, -2, ...",
        "# in content-hash order. Anything else is a normaliser bug to fix.",
        "\t".join(ALLOW_HEADER),
    ]
    for c in collisions:
        diff = differences(c.recipes)
        out.append(f"{c.space}\t{c.sig12}\t{len(c.recipes)}\tTODO: {'; '.join(diff)}")
        for i, r in enumerate(c.recipes, 1):
            ins = ", ".join(f"{s.amount}x {s.ref.text()}" for s in r.inputs)
            outs = ", ".join(f"{s.amount}x {s.ref.text()}" for s in r.outputs)
            out.append(
                f"#   -{i} content {r.content_hash[:12]}: {r.eut} EU/t, {r.duration} ticks; "
                f"{ins} -> {outs}"
            )
    return "\n".join(out) + "\n"

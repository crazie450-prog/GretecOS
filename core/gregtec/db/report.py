"""The counts report of ``gos db build`` and its collision curation task (WO-1.3, D108).

The report gives counts per mod (recipe maps a mod owns, the recipes in them, the recipes it
added, its machines and items) and the addon check of plan section 8: every recipe-map addon
that loads (TST, GT-Not-Leisure, 123Technology) owns at least one recipe map with GT recipes in
it, and each map it owns without one is listed. Programmable Hatches adds its recipes to GT's
own maps and owns none [A§3], so it is listed with no map expected. An addon is matched to
Export's loaded mods by modid or mod name, case ignored; the modids are the ones in each
addon's ``gradle.properties``, still to be confirmed on the first full-pack run (Questions,
WO-1.3). GT categories whose recipes are only in NEI (added after GT's dump, as TecTech's
Godforge maps in the dev runtime) get a warning of their own, because the planner doesn't use
NEI-only recipes of GT categories yet (``fake`` is NULL, D105). The machine-fact section (WO-1.7,
D109) counts the base and addon multiblocks whose parallels and OC rules are at confidence 0.8
or more, have no recipes, are flagged or are missing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gregtec.ingest import machine_facts
from gregtec.ingest.merge import ALLOW_HEADER, NOT_RECIPES, Collision
from gregtec.ingest.normalize import ITEM, SIG_CHARS, Recipe, canonical

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


def gt_recipes_per_map(result: Result) -> Counter[str]:
    """GT recipes (``gt`` and ``gt+nei``) per recipe map, after duplicates were dropped."""
    return Counter(r.space for r in result.loaded.recipes if _gt(r))


def nei_only_categories(result: Result) -> list[tuple[str, int]]:
    """(handler, NEI-only recipes) for each GT category whose recipes are only in NEI."""
    loaded = result.loaded
    found = Counter(
        r.handler
        for r in loaded.recipes
        if not _gt(r) and r.handler in loaded.categories
    )
    return sorted((h, n) for h, n in found.items() if h is not None)


def addon_lines(result: Result) -> list[str]:
    loaded = result.loaded
    mods = _mods(loaded.run.manifest)
    in_map = gt_recipes_per_map(result)
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
        owned = sorted(
            m.name for m in loaded.maps if (m.owner_mod or "").lower() == modid.lower()
        )
        recipes = sum(in_map[m] for m in owned)
        added = sum(
            1
            for r in loaded.recipes
            if _gt(r) and modid.lower() in {o.lower() for o in r.owners}
        )
        what = (
            f"{addon.title} ({modid} {version}): {len(owned)} recipe maps, {recipes:,} GT "
            f"recipes in them, {added:,} recipes added"
        )
        if not addon.owns_maps:
            lines.append(
                f"  ok          {what} (it adds to GT's own maps, none expected)"
            )
        elif not owned:
            lines.append(f"  MISSING     {what}: it loads but owns no recipe map")
        elif recipes == 0:
            lines.append(
                f"  MISSING     {what}: it loads but its recipe maps have no GT recipe"
            )
        else:
            lines.append(f"  ok          {what}")
        if addon.owns_maps:
            for name in (m for m in owned if in_map[m] == 0):
                lines.append(f"    no GT recipe in {name}")
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
            f"couldn't serialise, {keys.duplicates['gt']:,} rows dropped as copies of another "
            "GT recipe"
        ),
    ]
    if loaded.nei_status == "ok":
        warn = m.get("part_warnings")
        warning = warn.get("nei") if isinstance(warn, dict) else None
        out.append(
            f"  NEI: {stats.nei_rows:,} lines: {stats.exact:,} merged by identity hash, "
            f"{stats.matched:,} by slots, {sum(stats.duplicates.values()):,} duplicates, "
            f"{sum(stats.dropped.values()):,} GT recipes NEI shows in another form (dropped), "
            f"{stats.nei_only:,} NEI-only ({keys.duplicates['nei']:,} of them dropped as "
            f"copies of another NEI-only recipe), {sum(stats.not_recipes.values()):,} lines of "
            f"{len(stats.not_recipes)} handlers that aren't recipes, left out"
        )
        out.append(
            f"  NEI-only oredict slots: {stats.ore_named:,} keyed by their oredict name, "
            f"{stats.ore_sets:,} by the set of what they accept (no oredict name matches)"
        )
        if warning:
            out.append(f"  NEI warning: {warning}")
        only_nei = nei_only_categories(result)
        if only_nei:
            out.append(
                "WARNING: GT categories with recipes only in NEI (the planner doesn't use them "
                "yet): " + ", ".join(f"{h} ({n:,})" for h, n in only_nei)
            )
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
    for ref, content12 in keys.relisted:
        out.append(
            f"  {ref} has a new content hash {content12} and kept its suffix: put it in "
            "collisions.tsv"
        )
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
    if full:
        in_map = gt_recipes_per_map(result)
        empty = sorted(m.name for m in loaded.maps if in_map[m.name] == 0)
        out.append(f"Recipe maps with no GT recipe ({len(empty)}):")
        out.extend(f"  {name}" for name in empty)
    if full and loaded.nei_status == "ok":
        out.append("NEI handlers: handler, name, NEI-only recipes, dropped, left out")
        only = Counter(r.handler for r in loaded.recipes if not _gt(r))
        for handler, name in sorted(loaded.handlers.items()):
            out.append(
                f"  {handler}\t{name}\t{only[handler]}\t{stats.dropped[handler]}"
                f"\t{stats.not_recipes[handler]}"
            )
        out.append("Handlers left out (not recipes, D105):")
        out.extend(f"  {h}: {why}" for h, why in sorted(NOT_RECIPES.items()))
    out.extend(fact_lines(result, full))
    out.extend(crop_lines(result, full))
    for key in result.pruned:
        out.append(f"Pruned run {key} from the DB (keeping the newest [db] keep_runs)")
    if result.report_path is not None:
        out.append(f"Report: {result.report_path}")
    if result.report_error is not None:
        out.append(f"Report not saved: {result.report_error}")
    return out


def fact_statuses(result: Result) -> tuple[set[str], list[machine_facts.Status]]:
    """(the base pack's GT mod ids, the status of every multiblock in the run)."""
    loaded = result.loaded
    base = machine_facts.base_mods(loaded.run.manifest)
    facts = loaded.facts.facts if loaded.facts is not None else {}
    found = [
        machine_facts.status(m, facts.get(m.mte_id, []))
        for m in sorted(loaded.machines, key=lambda m: m.mte_id)
        if m.multiblock
    ]
    return base, found


def _fact(f: machine_facts.Fact | None) -> str:
    return "none" if f is None else f"{f.value} ({f.confidence:.2f} {f.source})"


def fact_lines(result: Result, full: bool) -> list[str]:
    """The machine-fact section: how many multiblocks have parallels and OC rules at
    confidence >= 0.8 (WO-1.7's acceptance), the rest flagged or missing."""
    loaded = result.loaded
    fs = loaded.facts
    if fs is None:
        return []
    base, found = fact_statuses(result)
    by_source = Counter(f.source for facts in fs.facts.values() for f in facts)
    out = [
        (
            f"Machine facts (WO-1.7): tooltip {by_source['tooltip']:,}, curated "
            f"{by_source['curated']:,} (plus the machine pass's flags); parallels and OC rules "
            f"at confidence >= {machine_facts.THRESHOLD}, or flagged:"
        )
    ]
    groups = [
        ("Base multiblocks (GT's jar)", [s for s in found if s.machine.mod in base]),
        ("Addon multiblocks", [s for s in found if s.machine.mod not in base]),
    ]
    for title, group in groups:
        n = Counter(s.state for s in group)
        # What the covered machines' formulas need besides how the machine is built: its
        # state while running (ramp, overheated, wind) or the recipe's metadata.
        kinds = Counter(
            kind
            for s in group
            if s.state == "covered"
            for kind in machine_facts.inputs(
                machine_facts.best(fs.facts.get(s.machine.mte_id, [])).values()
            )
        )
        out.append(
            f"  {title}: {len(group)}: covered {n['covered']} ({kinds['state']} of them "
            f"with a run-state input, {kinds['recipe']} with a recipe input), no recipes "
            f"(n/a) {n['n/a']}, flagged {n['flagged']}, missing {n['missing']}"
        )
    if not base:
        out.append(
            "  WARNING: GT (modid gregtech) isn't in the manifest's mods; all are addons"
        )
    missing = [s for s in groups[0][1] if s.state == "missing"]
    if missing:
        out.append(
            f"  WARNING: {len(missing)} base multiblocks have neither the facts nor a flag: "
            + ", ".join(f"{s.machine.mte_id} {s.machine.name}" for s in missing[:10])
            + (" ..." if len(missing) > 10 else "")
        )
    for c, key in fs.bad_maps:
        out.append(
            f"  WARNING: machine_facts.tsv line {c.line}: {key} names a recipe map "
            f"{c.machine} doesn't have"
        )
    pairs, modes = (
        [
            (m, t, c)
            for m in loaded.machines
            for t, c in machine_facts.disagreements(fs.facts.get(m.mte_id, []), per_map)
        ]
        for per_map in (False, True)
    )
    gone = {
        c.machine if c.meta is None else f"{c.machine}@{c.meta}" for c in fs.unmatched
    }
    out.append(
        f"  Tooltip and curated row disagree on {len(pairs)} facts, and on {len(modes)} "
        f"facts of one mode (check); curated rows for {len(gone)} classes this run doesn't "
        "have"
        + (
            " (all listed in the saved report)"
            if not full and (pairs or modes or fs.unmatched)
            else ""
        )
    )
    if not full:
        return out
    for title, group in groups:
        out.append(f"{title}: state, machine, parallel, oc, why")
        for s in group:
            m = s.machine
            out.append(
                f"  {s.state:<8} {m.mte_id} {m.name} ({m.class_name})\t{_fact(s.parallel)}"
                f"\t{_fact(s.oc)}\t{s.why}"
            )
    if pairs:
        out.append(
            "Tooltip vs curated (the curated row wins; stale after a GT update?):"
        )
        for m, t, c in pairs:
            out.append(
                f"  {m.mte_id} {m.name}: {t.key} tooltip {t.value!r} ({t.note}), curated "
                f"{c.value!r}"
            )
    if modes:
        out.append(
            "Tooltip vs curated, one mode (a tooltip mixes the modes; check the mode's row):"
        )
        for m, t, c in modes:
            out.append(
                f"  {m.mte_id} {m.name}: {c.key} curated {c.value!r}, tooltip {t.key} "
                f"{t.value!r} ({t.note})"
            )
    if fs.unmatched:
        out.append("Curated rows for classes not in this run:")
        out.extend(f"  line {c.line}: {c.machine} ({c.name})" for c in fs.unmatched)
    return out


CROP_PROBLEMS_SHOWN = 10  # in the printed report; the saved one lists them all


def crop_lines(result: Result, full: bool) -> list[str]:
    """The crop tables' counts (WO-1.10, D137) and, with NEI's data, the coverage of NEI's
    CropsNH tabs: every crop they show must be in CropsNH's crop dump (plan section 8)."""
    loaded = result.loaded
    data = loaded.crops
    if data is None:
        m = loaded.run.manifest
        errors = m.get("part_errors")
        why = errors.get("crops") if isinstance(errors, dict) else None
        if loaded.crops_error is not None:
            return [f"WARNING: crops not loaded: {loaded.crops_error}"]
        if loaded.crops_status == "absent":
            return [
                (
                    "Crops: none (parts.crops is 'absent': no CropsNH in Export, or a run from "
                    "before WO-1.10)"
                )
            ]
        if loaded.crops_status == "skipped_server":
            return [
                "Crops: none (parts.crops is 'skipped_server': CropsNH's dumpers need NEI's client)"
            ]
        tail = f": {why}" if why else ""
        return [
            f"WARNING: crops not loaded (parts.crops is {loaded.crops_status!r}{tail})"
        ]
    recipes = data.recipes
    drops = [d for c in data.crops.values() for d in c.drops]
    keyed = sum(d.item is not None for d in drops)
    machine = sum(r.machine_only for r in recipes)
    members = sum(len(p.members) for p in data.pools)
    blocks = sum(len(s.blocks) for s in data.soils)
    check = data.nei
    on_pages = (
        f" ({sum(bool(c.in_nei) for c in data.crops.values())} on NEI's crop pages)"
        if check
        else ""
    )
    in_tab = (
        f"{sum(bool(r.in_nei) for r in recipes)} in NEI's Crop Breeding tab, "
        if check
        else ""
    )
    out = [
        (
            f"Crops: {len(data.crops)} crops{on_pages}, {len(recipes)} parent-combination recipes "
            f"({in_tab}{machine} for the Crop Breeder only), {len(data.pools)} mutation pools "
            f"({members} memberships), {len(data.soils)} soil lists ({blocks} blocks), "
            f"{len(data.seeds)} alternate seeds, {len(drops)} drops ({keyed} keyed by item)"
        )
    ]
    if check is not None:
        # Every tab: the crop pages, Crop Breeding, the mutation pools and alternate seeds.
        missing = sorted(check.shown - set(data.crops))
        if not check.shown:
            verdict = "WARNING: they show no crop, so nothing was checked"
        elif not missing:
            verdict = f"all {len(check.shown)} crops they show are in the crop dump"
        else:
            names = ", ".join(missing)
            verdict = (
                f"{len(missing)} crops they show are NOT in the crop dump: {names}"
            )
        out.append(
            f"  NEI's CropsNH tabs: Crop Breeding {check.breeding} recipes with "
            f"{len(check.breeding_crops)} crops, {check.crop_pages} crop pages, "
            f"{check.pools} mutation pool pages ({check.pools_matched} match a dumped pool); "
            + verdict
        )
    else:
        out.append(
            f"  NEI's CropsNH tabs not checked (parts.nei is {loaded.nei_status!r}): drops "
            "have no item key and crops no sub-soil blocks"
        )
    other = sum(
        r.kind == "other"
        for x in (*data.crops.values(), *recipes)
        for r in x.requirements
    )
    if other:
        out.append(
            f"  {other} requirement texts not recognised (kind 'other'; not CropsNH 2.0.140's "
            "English descriptions)"
        )
    if data.problems:
        out.append(f"WARNING: crop data: {len(data.problems)} problems")
        shown = data.problems if full else data.problems[:CROP_PROBLEMS_SHOWN]
        out.extend(f"  {p}" for p in shown)
        if len(shown) < len(data.problems):
            out.append(
                f"  ... {len(data.problems) - len(shown)} more in the saved report"
            )
    if full:
        out.append("Crops: crop:<id>, name, tier, soil, in NEI")
        for c in data.crops.values():
            shown_in = "" if c.in_nei is None else ("yes" if c.in_nei else "no")
            out.append(f"  {c.ref}\t{c.name}\t{c.tier}\t{c.soil}\t{shown_in}")
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
        "# in the order of the content12 column (content-hash order for a new group). A group",
        "# the allow-list already has keeps its listed order: put each recipe's new content12",
        "# where it was. Anything else is a normaliser bug to fix.",
        "\t".join(ALLOW_HEADER),
    ]
    for c in collisions:
        diff = differences(c.recipes)
        content = ",".join(r.content_hash[:SIG_CHARS] for r in c.recipes)
        out.append(f"{c.space}\t{c.sig12}\t{content}\tTODO: {'; '.join(diff)}")
        if c.listed:
            out.append(f"#   on the allow-list as {','.join(c.listed)}")
        for i, r in enumerate(c.recipes, 1):
            ins = ", ".join(f"{s.amount}x {s.ref.text()}" for s in r.inputs)
            outs = ", ".join(f"{s.amount}x {s.ref.text()}" for s in r.outputs)
            out.append(
                f"#   -{i} content {r.content_hash[:12]}: {r.eut} EU/t, {r.duration} ticks; "
                f"{ins} -> {outs}"
            )
    return "\n".join(out) + "\n"

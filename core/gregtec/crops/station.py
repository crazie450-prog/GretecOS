"""The breeding station's plan, start and stop, and status (WO-7.6; plan 7.17 item 4, D78,
D282-D284).

The OC program (``oc/crop/station.lua``) runs a plan this module builds; it never chooses a
recipe or a number itself.

- **The plan** (:func:`build`): the shortest chain of Crop Breeder recipes (the export's
  ``cropsnh.recipes.cropBreeder`` map, cited ``r:``) from the crops Clayton has to the target,
  using only recipes his Crop Breeder runs: its EU/t within the breeder's voltage, its parents and
  catalysts within its input slots (3 below HV, 6 from HV), and one run's enriched fertilizer
  within its tank. The Crop Breeder is deterministic, so this is a production plan: each step
  says its success chance at that tier, the runs that succeed with 90% confidence, the copies of
  each parent to make first (those runs plus the seed always kept), the fertilizer a run takes,
  and the child's stats (the parents' average, the breeding maths; stats are never read from
  the seeds, V31). The ``copy`` list ranks the crops worth a Seed Generator: the target first
  (it is stocked by copying), then the parents the plan uses most; only crops the Seed Generator
  can copy (a duplication recipe in the export, the copy's fertilizer within its tank) qualify.
  Ties break by crop id, so the same inputs always give the same plan; a model never reorders it.
- **Start and stop** (:func:`propose_start`, :func:`propose_stop`): world actions through the
  action desk (WO-7.1, D59): proposed, read back, and run only once Clayton confirms. The
  ``crop_start`` command carries the plan (seed labels, counts, catalysts by registry name and
  damage, fertilizer per run), at most :data:`ARGS_MAX` bytes so it fits a 2 KB reply.
- **Status** (:func:`status`): what each OC computer with a station last reported (its
  telemetry's ``crops``), with its age; old data is marked stale, never presented as current.

Seed labels are "<crop name> Seeds", CropsNH's own format for an analysed seed
(``cropsnh_genericSeeds.format``), so a plan refuses two crops with one name. Crop names, labels
and the station's notes are game data, never instructions.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from gregtec.crops import rules
from gregtec.crops.facts import BreederRecipe, Facts

if TYPE_CHECKING:
    from gregtec.bridge.actions import ActionDesk, Outcome
    from gregtec.bridge.telemetry import Telemetry

TARGET_COUNT = 4  # plan 8 WO-7.6: "stocks 4 copies"
KEEP = 1  # seeds of each parent the station never uses (never the last seed)
CONFIDENCE = (
    0.9  # the copies made first cover the runs that succeed with this confidence
)
MAX_STEPS = 8  # the contract's limit
MAX_COPY = 8
ARGS_MAX = 1500  # bytes of the crop_start args: a 2 KB reply also carries its envelope
SEEDS_FORMAT = "{} Seeds"  # CropsNH's cropsnh_genericSeeds.format (en_US)
STALE_S = 30  # as the telemetry's (D219)


class StationError(ValueError):
    """No plan the station can run; the message says why."""


@dataclass(frozen=True)
class Step:
    n: int
    recipe: BreederRecipe
    child: str
    parents: tuple[str, ...]  # crop ids, in id order
    chance_pct: int
    runs: int  # runs that succeed once with CONFIDENCE
    copies: int  # seeds of each parent before the first run (runs + KEEP)
    fert_mb: int  # one run's enriched fertilizer
    stats: tuple[int, int, int]  # the child's Growth, Gain, Resistance (breeding maths)
    refs: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    id: str
    target: str
    count: int
    tier: int  # the Crop Breeder's tier the plan is for
    steps: tuple[Step, ...]
    starters: dict[
        str, tuple[int, int, int]
    ]  # crops he has that the plan uses, their stats
    copy: tuple[str, ...]  # crops worth a copier, best first
    copy_cat: dict[str, tuple[tuple[str, int, int, str], ...]]
    labels: dict[str, str]  # crop id -> seed label
    refs: tuple[str, ...]
    lines: list[str] = field(
        default_factory=list
    )  # the plan in words, each number cited

    def args(self) -> dict[str, Any]:
        """The ``crop_start`` command's args (the OC contract's ``crop_start_args``)."""
        return _args(self, self.id)


def _label(facts: Facts, crop: str) -> str:
    return SEEDS_FORMAT.format(facts.crop_name(crop))


def _usable(
    facts: Facts, r: BreederRecipe, tier: int
) -> tuple[bool, str]:  # (usable, why not)
    if r.eut > 8 * 4**tier:
        return False, f"its {r.eut} EU/t is over the tier's voltage ({r.ref})"
    if len(r.parents) + len(r.catalyst_keys or r.catalysts) > rules.breeder_slots(tier):
        return (
            False,
            f"its parents and catalysts don't fit {rules.breeder_slots(tier)} slots ({r.ref})",
        )
    if r.child not in facts.data.crops or any(
        p not in facts.data.crops for p in r.parents
    ):
        return False, f"a crop of it isn't in the crop data ({r.ref})"
    return True, ""


def _child_stats(
    parents: Sequence[str], stats: Mapping[str, tuple[int, int, int]]
) -> tuple[int, int, int]:
    return rules.breeder_child_stats([stats[p] for p in parents])


def _choose(
    facts: Facts,
    have: Mapping[str, tuple[int, int, int]],
    tier: int,
    banned: set[str],
) -> dict[str, tuple[int, BreederRecipe | None]]:
    """For every reachable crop, (steps to make it, the recipe that does): fewest steps, ties
    by recipe ref. A fixed point over the recipes (costs only fall, so it ends)."""
    best: dict[str, tuple[int, BreederRecipe | None]] = {c: (0, None) for c in have}
    recipes = sorted(
        (
            r
            for r in facts.breeder
            if r.ref not in banned and _usable(facts, r, tier)[0]
        ),
        key=lambda r: r.ref,
    )
    changed = True
    while changed:
        changed = False
        for r in recipes:
            if not all(p in best for p in r.parents):
                continue
            cost = 1 + sum(best[p][0] for p in r.parents)
            cur = best.get(r.child)
            if cur is None or cost < cur[0]:
                best[r.child] = (cost, r)
                changed = True
    return best


def _chain(
    best: Mapping[str, tuple[int, BreederRecipe | None]], target: str
) -> list[str]:
    """The crops to breed for ``target``, each after its parents (parents in id order)."""
    order: list[str] = []

    def visit(c: str) -> None:
        r = best[c][1]
        if r is None or c in order:
            return
        for p in sorted(r.parents):
            visit(p)
        order.append(c)

    visit(target)
    return order


def build(
    facts: Facts,
    have: Mapping[str, tuple[int, int, int]],
    target: str,
    tier: int,
    count: int = TARGET_COUNT,
) -> Plan:
    """The station's plan to stock ``count`` of ``target`` from ``have`` (crop id -> its best
    Growth, Gain, Resistance) on the Crop Breeder of ``tier`` (his best at or below it)."""
    if target not in facts.data.crops:
        raise StationError(f"no crop {target!r} in the crop data (run {facts.run_key})")
    if not 1 <= count <= 64:
        raise StationError("count must be 1 to 64")
    breeder = facts.machine("crop_breeder", tier)
    if breeder is None or breeder.tier < rules.LV:
        raise StationError("the breeding station needs a Crop Breeder (LV or higher)")
    gen = facts.machine("seed_generator", tier)
    if gen is None:
        raise StationError("the breeding station needs a Seed Generator (LV or higher)")
    btier = breeder.tier
    if target in have:
        raise StationError(
            f"you already have {facts.crop_name(target)}: stock it with a Seed Generator"
        )
    banned: set[str] = set()
    why: list[str] = []
    while True:
        best = _choose(facts, have, btier, banned)
        if target not in best:
            unusable = [
                _usable(facts, r, btier)[1]
                for r in facts.breeder
                if r.child == target and not _usable(facts, r, btier)[0]
            ]
            more = "; ".join(unusable + why)
            raise StationError(
                f"no Crop Breeder recipe chain reaches {facts.crop_name(target)} "
                f"(crop:{target}) from your crops at this tier"
                + (f": {more}" if more else "")
            )
        order = _chain(best, target)  # parents first
        stats: dict[str, tuple[int, int, int]] = dict(have)
        bad = None
        for c in order:
            r = best[c][1]
            assert r is not None
            stats[c] = _child_stats(sorted(r.parents), stats)
            fert = rules.breeder_fertilizer(facts.data.crops[c].tier, *stats[c])
            if fert > rules.breeder_tank(btier):
                bad = r
                why.append(
                    f"{facts.crop_name(c)} at {stats[c][0]}/{stats[c][1]}/{stats[c][2]} needs "
                    f"{fert} mB a run, over the tank's {rules.breeder_tank(btier)} ({r.ref})"
                )
                break
        if bad is None:
            break
        banned.add(bad.ref)
    if len(order) > MAX_STEPS:
        raise StationError(
            f"the chain to {facts.crop_name(target)} is {len(order)} Crop Breeder steps; "
            f"the station runs at most {MAX_STEPS}"
        )
    labels: dict[str, str] = {}
    seen: dict[str, str] = {}
    used = set(order)
    for c in order:
        r = best[c][1]
        assert r is not None
        used |= set(r.parents)
    for c in sorted(used):
        lab = _label(facts, c)
        if lab in seen:
            raise StationError(
                f"crop:{c} and crop:{seen[lab]} are both {lab}: the station tells seeds apart "
                "by label only"
            )
        seen[lab] = c
        labels[c] = lab
    chance = rules.breeder_chance(btier)
    runs = rules.runs_for(chance, CONFIDENCE)
    steps: list[Step] = []
    for i, c in enumerate(order, 1):
        r = best[c][1]
        assert r is not None
        fert = rules.breeder_fertilizer(facts.data.crops[c].tier, *stats[c])
        steps.append(
            Step(
                n=i,
                recipe=r,
                child=c,
                parents=tuple(sorted(r.parents)),
                chance_pct=chance,
                runs=runs,
                copies=max(2, min(64, runs + KEEP)),
                fert_mb=fert,
                stats=stats[c],
                refs=(
                    r.ref,
                    breeder.ref,
                    f"crop:{c}",
                    *(f"crop:{p}" for p in sorted(r.parents)),
                ),
            )
        )
    copy, copy_cat = _copiers(facts, steps, target, count, stats, gen.tier)
    starters = {c: stats[c] for c in sorted(used) if c in have}
    refs = tuple(
        dict.fromkeys(
            [breeder.ref, gen.ref]
            + [r for s in steps for r in s.refs]
            + [g.ref for c in copy for g in facts.generator.get(c, [])[:1]]
        )
    )
    plan = Plan(
        id="",
        target=target,
        count=count,
        tier=btier,
        steps=tuple(steps),
        starters=starters,
        copy=tuple(copy),
        copy_cat=copy_cat,
        labels=labels,
        refs=refs,
    )
    pid = hashlib.sha256(
        json.dumps(_args(plan, "00000000"), sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    plan = Plan(**{**plan.__dict__, "id": pid})
    size = len(json.dumps(plan.args(), separators=(",", ":")).encode("utf-8"))
    if size > ARGS_MAX:
        raise StationError(
            f"the plan is {size} bytes; one OC reply carries at most {ARGS_MAX} of it"
        )
    plan.lines.extend(_lines(facts, plan, breeder.name))
    return plan


def _copiers(
    facts: Facts,
    steps: Sequence[Step],
    target: str,
    count: int,
    stats: Mapping[str, tuple[int, int, int]],
    gen_tier: int,
) -> tuple[list[str], dict[str, tuple[tuple[str, int, int, str], ...]]]:
    """The crops worth a Seed Generator, best first: the target (when more than one is
    stocked), then the parents by the seeds the plan wants of them; only crops the generator
    can copy at its tier."""

    def copyable(c: str) -> bool:
        if not facts.generator.get(c):
            return False
        need = rules.seed_generator_fertilizer(*stats[c])
        return need <= rules.seed_generator_tank(gen_tier)

    want: dict[str, int] = {}
    for s in steps:
        for p in s.parents:
            want[p] = want.get(p, 0) + s.copies
    ranked = sorted(want, key=lambda c: (-want[c], c))
    order = ([target] if count > 1 else []) + ranked
    copy = [c for c in dict.fromkeys(order) if copyable(c)][:MAX_COPY]
    cats = {
        c: facts.generator[c][0].catalysts
        for c in copy
        if facts.generator[c][0].catalysts
    }
    return copy, cats


def _args(plan: Plan, pid: str) -> dict[str, Any]:
    lab = plan.labels
    out: dict[str, Any] = {
        "plan": pid,
        "target": lab[plan.target],
        "count": plan.count,
        "steps": [
            {
                "child": lab[s.child],
                "parents": [lab[p] for p in s.parents],
                "cat": [
                    {"name": reg, "damage": meta, "n": n}
                    for reg, meta, n in s.recipe.catalyst_keys
                ],
                "copies": s.copies,
                "fert": s.fert_mb,
                "ref": s.recipe.ref,
            }
            for s in plan.steps
        ],
        "copy": [lab.get(c, SEEDS_FORMAT.format(c)) for c in plan.copy],
    }
    if plan.copy_cat:
        out["copy_cat"] = {
            lab[c]: [{"name": reg, "damage": meta, "n": n} for reg, meta, n, _ in cats]
            for c, cats in plan.copy_cat.items()
        }
    return out


def _lines(facts: Facts, plan: Plan, breeder_name: str) -> list[str]:
    """The plan in words: every number with the ids it comes from (principle 2)."""
    name = facts.crop_name
    out = [
        (
            f"Breed {name(plan.target)} on the {breeder_name} in {len(plan.steps)} step(s) "
            f"and stock {plan.count} [crop:{plan.target}]"
        ),
    ]
    for s in plan.steps:
        stats = "/".join(str(v) for v in s.stats)
        out.append(
            f"Step {s.n}: {' + '.join(name(p) for p in s.parents)} -> {name(s.child)}: "
            f"{s.chance_pct}% a run, {s.runs} runs for 90%, {s.copies} of each parent first, "
            f"{s.fert_mb} mB enriched fertilizer a run, child {stats} from the parents' "
            f"average [{', '.join(s.refs)}]"
        )
        for cname, amount in s.recipe.catalysts:
            out.append(f"Step {s.n} also uses {amount} {cname} a run [{s.recipe.ref}]")
    if plan.copy:
        copied = ", ".join(name(c) for c in plan.copy)
        cites = ", ".join(f"crop:{c}" for c in plan.copy)
        out.append(
            f"Seed Generators copy, best first: {copied} (each one keeps its first seed for "
            f"good: GT lets no automation take it back) [{cites}]"
        )
    for c, st in plan.starters.items():
        out.append(f"Starter: {name(c)} at {st[0]}/{st[1]}/{st[2]} [crop:{c}]")
    return out


def as_dict(plan: Plan) -> dict[str, Any]:
    name_of = {c: lab.removesuffix(" Seeds") for c, lab in plan.labels.items()}
    return {
        "id": plan.id,
        "target": plan.target,
        "target_name": name_of.get(plan.target, plan.target),
        "count": plan.count,
        "tier": plan.tier,
        "steps": [
            {
                "n": s.n,
                "child": s.child,
                "child_name": name_of.get(s.child, s.child),
                "parents": list(s.parents),
                "chance_pct": s.chance_pct,
                "runs": s.runs,
                "copies": s.copies,
                "fert_mb": s.fert_mb,
                "stats": list(s.stats),
                "recipe": s.recipe.ref,
                "refs": list(s.refs),
            }
            for s in plan.steps
        ],
        "copy": list(plan.copy),
        "starters": {c: list(v) for c, v in plan.starters.items()},
        "lines": list(plan.lines),
        "refs": list(plan.refs),
        "args_bytes": len(
            json.dumps(plan.args(), separators=(",", ":")).encode("utf-8")
        ),
    }


# --- the read-back and the action desk -----------------------------------------------------


def readback(kind: str, args: Mapping[str, Any]) -> str:
    """The desk's read-back for ``crop_start`` / ``crop_stop`` (without "Say confirm.")."""
    if kind == "crop_stop":
        return "Stop the breeding station"
    steps = args.get("steps") or []
    n = len(steps)
    return (
        f"Start the breeding station: breed {args.get('target')} in {n} Crop Breeder "
        f"step{'s' if n != 1 else ''} and stock {args.get('count')}"
    )


def propose_start(
    desk: ActionDesk,
    plan: Plan,
    *,
    target: str = "lab",
    source: str,
    replace_any: bool = True,
) -> Outcome:
    """Propose ``crop_start`` with ``plan`` (D59: GOS reads it back, Clayton confirms).
    ``replace_any`` false (a paired browser's path, as D272's): it only replaces a pending
    breeding-station proposal, never another world action."""
    return _propose(desk, "crop_start", plan.args(), target, source, replace_any)


def propose_stop(
    desk: ActionDesk, *, target: str = "lab", source: str, replace_any: bool = True
) -> Outcome:
    return _propose(desk, "crop_stop", {}, target, source, replace_any)


def _propose(
    desk: ActionDesk,
    kind: str,
    args: dict[str, Any],
    target: str,
    source: str,
    replace_any: bool,
) -> Outcome:
    from gregtec.bridge.actions import ActionError

    pending = desk.pending()
    if (
        not replace_any
        and pending is not None
        and pending.kind not in ("crop_start", "crop_stop")
    ):
        raise StationError(
            f"another action is waiting for your confirm ({pending.readback}): confirm or "
            "cancel it first"
        )
    try:
        return desk.propose(kind, args, target=target, source=source)
    except ActionError as exc:
        raise StationError(str(exc)) from None


# --- status ----------------------------------------------------------------------------------


def status(telemetry: Telemetry, binding: str | None = None) -> list[dict[str, Any]]:
    """Each OC computer's last breeding-station report (its telemetry's ``crops``), newest
    first, with its age; ``stale`` once nothing was heard for :data:`STALE_S`."""
    now = telemetry.clock()
    out = []
    for agent in telemetry.latest.agents():
        msg = telemetry.latest.latest(agent)
        if msg is None or (binding is not None and agent.binding != binding):
            continue
        crops = msg.doc.get("crops")
        if not isinstance(crops, dict):
            continue
        age = max(0, int(now - msg.received_at))
        out.append(
            {
                "binding": agent.binding,
                "address": agent.address,
                "ref": f"tel:{agent.binding}/{agent.address}",
                "age_s": age,
                "stale": age > STALE_S,
                **{k: crops[k] for k in sorted(crops)},
            }
        )
    out.sort(key=lambda d: d["age_s"])
    return out


def expected_fertilizer(plan: Plan) -> int:
    """mB of enriched fertilizer the breeder's runs take on average (a failure pays too)."""
    return sum(math.ceil(s.fert_mb * 100 / s.chance_pct) for s in plan.steps)


# --- the station's parts, from the recipe DB (V31) -------------------------------------------

# OpenComputers' parts by registry name, and for the shared ``opencomputers:item`` the English
# name the export gives it (a report only: nothing is keyed on a name or a numeric id).
OC_PARTS: tuple[tuple[str, str, str | None], ...] = (
    ("Transposer", "opencomputers:transposer", None),
    ("Adapter", "opencomputers:adapter", None),
    ("Computer Case (Tier 1)", "opencomputers:case1", None),
    ("Screen (Tier 1)", "opencomputers:screen1", None),
    ("Keyboard", "opencomputers:keyboard", None),
    ("Cable", "opencomputers:cable", None),
    ("CPU (Tier 1)", "opencomputers:item", "Central Processing Unit (CPU) (Tier 1)"),
    ("Memory (Tier 1)", "opencomputers:item", "Memory (Tier 1)"),
    ("Graphics Card (Tier 1)", "opencomputers:item", "Graphics Card (Tier 1)"),
    (
        "Hard Disk Drive (Tier 1)",
        "opencomputers:item",
        "Hard Disk Drive (Tier 1) (1MB)",
    ),
    ("EEPROM (Lua BIOS)", "opencomputers:eeprom", None),
)
MV = 2


def parts(conn: sqlite3.Connection, facts: Facts | None = None) -> list[dict[str, Any]]:
    """Each part the station needs, the newest export run's recipes that make it (``r:``) and
    whether one is a crafting-table recipe or a GT recipe at MV or below. A part the DB doesn't
    have is reported as such (the dev harness has no OpenComputers; a full-pack export does),
    never guessed."""
    row = conn.execute(
        "SELECT id FROM export_run ORDER BY created_at DESC, run_key DESC LIMIT 1"
    ).fetchone()
    run_id = row[0] if row else None
    wanted: list[tuple[str, str, int | None, str | None]] = [
        (label, reg, None, name) for label, reg, name in OC_PARTS
    ]
    if facts is not None:
        for job, label in (
            ("crop_breeder", "Crop Breeder"),
            ("seed_generator", "Seed Generator"),
        ):
            m = facts.machine(job, MV)
            if m is not None:
                wanted.append(
                    (f"{label} ({m.name})", "gregtech:gt.blockmachines", m.mte_id, None)
                )
    out: list[dict[str, Any]] = []
    for label, reg, meta, name in wanted:
        sql = "SELECT id FROM item WHERE registry_name = ?"
        params: list[Any] = [reg]
        if meta is not None:
            sql += " AND meta = ?"
            params.append(meta)
        if name is not None:
            sql += " AND name_en = ?"
            params.append(name)
        items = [r[0] for r in conn.execute(sql, params)]
        recipes: list[dict[str, Any]] = []
        if items and run_id is not None:
            marks = ",".join("?" * len(items))
            for ref, source, vt, eut in conn.execute(
                "SELECT DISTINCT r.ref, r.source, r.voltage_tier, r.eut FROM recipe AS r"
                " JOIN recipe_output AS o ON o.recipe_id = r.id"
                f" WHERE o.item_id IN ({marks}) AND r.run_id = ?"
                " ORDER BY r.voltage_tier, r.ref",
                (*items, run_id),
            ):
                recipes.append(
                    {"ref": ref, "source": source, "voltage_tier": vt, "eut": eut}
                )
        at_mv = any(
            r["source"] in ("crafting", "nei") or (r["voltage_tier"] or 0) <= MV
            for r in recipes
        )
        out.append(
            {
                "part": label,
                "registry_name": reg,
                "meta": meta,
                "in_db": bool(items),
                "recipes": recipes[:6],
                "craftable_at_mv": at_mv if items else None,
            }
        )
    return out

"""The Power view's backend (WO-4.6, plan §7.2, §7.14 "Power", D11, D13, D67, D243).

What the dashboard's Power view, the ``power-designer`` skill's tools and ``gos power`` share.
Every number is the power designer's (:mod:`.power`, ranked by :mod:`.rank`'s ``order_power``),
the ledger's (:mod:`.ledger`, the accepted plans' own numbers) or the distribution checks'
(:mod:`.distribution`, the DB's storage tables); nothing here computes a game number, and the
options keep the order rank.py gives them.

- :func:`request_from_spec`: a :class:`power.PowerRequest` from the view's form or
  ``design_power``'s arguments (mode, tier, scale and bound, kinds, fuels, rotor, options).
- :func:`design`: solve the request (:func:`power.design`, the effort model on), save every
  option as a ``proposed`` power plan (``store.save_power``: its JSON and ``plan`` row), and
  remember the design (``data/plans/power.json``: its ids in rank order, weights line, notes
  and the rejected candidates), which the view opens with when its address names none.
- :func:`view`: what the Power view shows for a design: the mode and the weights line; per
  option its rank, score and the terms behind it, net = gross - the chain's own use, generator,
  fuel, distribution, upkeep, the effort breakdown (WO-4.3's table card), the validator's
  verdict and bootstrap kit, the nodes, the cables that carry its output (WO-4.5), and the EU
  ledger as it would be with the option accepted (its warnings before and after, and the
  transformer chains it would need); then the ledger as it is, its warnings and its storage
  and distribution checks (buffers when Clayton gives a peak window, D241: the ledger has no
  durations).
- :func:`accept`: Clayton accepts an option (``designer.accept``: its line note and its source
  in the ledger). Never a model's tool (D251, D238).
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.base.state import MyBase
from gregtec.packupdate.plans import PLAN_ID
from gregtec.planner import chains, designer, distribution, ledger, power, rank
from gregtec.planner.gtmath.tiers import VN, V
from gregtec.planner.outputs import book as book_mod
from gregtec.planner.outputs import cards, store
from gregtec.planner.request import RequestError, tier_index
from gregtec.vault.io import atomic_write

LATEST = Path("data", "plans", "power.json")  # in GOS_HOME
SPEC_KEYS = frozenset(
    {"mode", "tier", "scale", "bound", "kinds", "fuels", "rotor", "options", "free"}
)
MODE_LABELS = {
    "best": "Best at tier",
    "max_output": "Max output",
    "output_per_effort": "Output per effort",
    "self_sustaining": "Self-sustaining",
}
MODE_HOW = {  # plan 7.2's table, as the view prints it
    "best": "the weighted score: net EU/t up; effort, upkeep inputs, complexity and "
    "scarcity down",
    "max_output": "the most net EU/t at the tier cap, within the scale bound",
    "output_per_effort": "net EU/t per effort hour",
    "self_sustaining": "inputs only from the free set or the line itself, proven by the "
    "validator; then the weighted score",
}
WINDOW_MAX_S = 7 * 24 * 3600.0  # a peak window longer than a week isn't a peak


class PowerViewError(designer.DesignerError):
    """The request can't be done as asked; the message says why. Nothing was changed."""


@dataclass
class PowerDesigned:
    ids: list[str]  # rank order
    plans: list[dict[str, Any]]
    weights: str
    notes: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _strs(value: object, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PowerViewError(f"{what} is a list of names")
    return tuple(value)


def request_from_spec(spec: Mapping[str, Any]) -> power.PowerRequest:
    """``mode`` (one of the four), ``tier`` (LV..MAX, else My base's), ``scale`` (unit,
    generators, eut_cap, raw_budget) with ``bound``, ``kinds`` and ``fuels`` (keys) to consider,
    ``rotor`` ([material, size]), ``free`` (the free set's keys) and ``options`` (1-3)."""
    unknown = set(spec) - SPEC_KEYS
    if unknown:
        raise PowerViewError(f"unknown fields: {', '.join(sorted(unknown))}")
    mode = spec.get("mode", "best")
    if not isinstance(mode, str):
        raise PowerViewError(f"mode is one of {', '.join(power.MODES)}")
    try:
        tier = spec.get("tier")
        bound = spec.get("bound")
        if bound is not None and (
            isinstance(bound, bool) or not isinstance(bound, int | float)
        ):
            raise PowerViewError("bound is a number")
        rotor = spec.get("rotor") or list(power.DEFAULT_ROTOR)
        if (
            not isinstance(rotor, list | tuple)
            or len(rotor) != 2
            or not all(isinstance(x, str) for x in rotor)
        ):
            raise PowerViewError("rotor is [material, size]")
        options = spec.get("options", 3)
        if isinstance(options, bool) or not isinstance(options, int):
            raise PowerViewError("options is 1, 2 or 3")
        free = _strs(spec.get("free"), "free")
        return power.PowerRequest(
            mode=mode,
            tier_cap=tier_index(str(tier)) if tier not in (None, "") else None,
            scale=str(spec.get("scale") or "unit"),
            bound=float(bound) if bound is not None else None,
            free=free or power.DEFAULT_FREE,
            kinds=_strs(spec.get("kinds"), "kinds"),
            fuels=_strs(spec.get("fuels"), "fuels"),
            rotor=(rotor[0], rotor[1]),
            options=options,
        )
    except RequestError as exc:
        raise PowerViewError(str(exc)) from None


# --- design and save ---------------------------------------------------------------------------


def _remember(home: Path, d: PowerDesigned, mode: str) -> None:
    body = {
        "ids": d.ids,
        "mode": mode,
        "weights": d.weights,
        "notes": d.notes,
        "rejected": [list(r) for r in d.rejected],
        "ts": int(time.time()),
    }
    atomic_write(home / LATEST, json.dumps(body, sort_keys=True).encode("utf-8"))


def _latest(home: Path) -> dict[str, Any]:
    try:
        data = json.loads((home / LATEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def latest(home: Path) -> list[str]:
    """The ids of the Power view's latest design, or none."""
    ids = _latest(home).get("ids")
    if not isinstance(ids, list):
        return []
    return [i for i in ids if isinstance(i, str) and PLAN_ID.fullmatch(i)]


def design(
    home: Path,
    req: power.PowerRequest,
    *,
    farms_path: Path | None = None,
    with_effort: bool = True,
    templates: chains.Templates | None = None,
) -> PowerDesigned:
    """Solve ``req``, save every option as a proposed power plan and remember the design.
    ``farms_path`` and ``templates`` default to the repo's curated farms and chain templates."""
    extra: dict[str, Any] = {} if farms_path is None else {"farms_path": farms_path}
    prefs = power.read_prefs(home / "vault")
    with designer._LOCK, designer._conn(home, readonly=True) as conn:
        try:
            d = power.design(
                conn,
                req,
                MyBase.from_db(conn),
                prefs=prefs,
                with_effort=with_effort,
                templates=templates,
                **extra,
            )
        except power.PowerError as exc:
            raise PowerViewError(f"no power line: {exc}") from None
        except (LookupError, ValueError) as exc:
            raise PowerViewError(f"can't design it: {exc}") from None
        if not d.options:
            reasons = "; ".join(f"{g}: {w}" for g, w in d.rejected[:5])
            raise PowerViewError(
                f"no power line at {VN[d.tier_cap]} for mode {req.mode}"
                + (f" ({reasons})" if reasons else "")
            )
        with designer._conn(home, readonly=False) as rw:
            for p in d.options:
                store.save_power(home, p, rw)
    ids = [str(p["id"]) for p in d.options]
    out = PowerDesigned(
        ids,
        [store.load(home, pid) for pid in ids],
        d.weights,
        [*d.notes, *prefs.notes],
        [(str(g), str(w)) for g, w in d.rejected],
    )
    _remember(home, out, req.mode)
    return out


# --- what the view shows -----------------------------------------------------------------------


def group(home: Path, pid: str) -> list[dict[str, Any]]:
    """Power plan ``pid`` and the options solved with it that are still saved, in rank order."""
    first = designer.load(home, pid, power=True)
    if first.get("kind") != "power":
        raise PowerViewError(f"plan {pid} isn't a power line")
    plans = [first]
    for other in first.get("alternatives") or ():
        try:
            plans.append(store.load(home, other))
        except (OSError, ValueError):
            continue
    return sorted(
        plans, key=lambda p: (int((p.get("rank") or {}).get("position") or 0), p["id"])
    )


def weights_line(p: Mapping[str, Any]) -> str:
    """The weights line printed with the option (``rank.describe_power`` on its own weights)."""
    r = p.get("rank") or {}
    w = r.get("w") or {}
    prefs = rank.Prefs(
        name=str(r.get("weights") or "balanced"),
        power=tuple((m, float(w.get(m, 1.0))) for m in rank.POWER_METRICS),
    )
    return rank.describe_power(prefs, str(r.get("mode") or "best"))


def _name(b: book_mod.Book | None, key: str) -> str:
    return b.name(key) if b is not None else key


def fuel_names(home: Path, plans: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Each option's fuel key and its name from the plan's book (the key itself when the run
    it was solved on is gone), so an explanation names the fuel and cites the key (review L5)."""
    out: dict[str, str] = {}
    with designer._conn(home, readonly=True) as conn:
        for p in plans:
            key = str(p["power"]["fuel"]["id"])
            try:
                out[key] = book_mod.read(conn, p).name(key)
            except LookupError:
                out[key] = key
    return out


def _nodes(p: Mapping[str, Any], b: book_mod.Book | None) -> list[dict[str, Any]]:
    out = []
    for n in p.get("nodes") or ():
        m = n.get("machine") if isinstance(n.get("machine"), dict) else None
        kind = str(n.get("kind") or "recipe")
        made = [k for k in n.get("for") or () if k != "eu"]
        if kind in ("generator", "farm"):
            name = str(n.get("label") or n["recipe"])
        elif made:
            name = _name(b, made[0])
        else:
            name = str(n["recipe"])
        recipe = str(n["recipe"])
        out.append(
            {
                "n": n["n"],
                "kind": kind,
                "name": name,
                "recipe": recipe
                if recipe.startswith(("r:", "farm:"))
                else (n.get("cite") or None),
                "machine": m.get("name") if m else None,
                "mte": f"mte:{m['mte']}"
                if m and isinstance(m.get("mte"), int)
                else None,
                "tier": m.get("tier") if m else None,
                "count": n.get("count"),
            }
        )
    return out


def _ledger_lines(conn: sqlite3.Connection) -> list[ledger.Line]:
    try:
        return ledger.lines(conn)
    except sqlite3.Error:
        return []


def _warnings(led: ledger.Ledger, scope: str = "all") -> list[dict[str, Any]]:
    return [
        {
            "kind": w.kind,
            "tier": w.tier or None,
            "detail": w.detail,
            "plans": [f"plan:{x}" for x in w.plans],
        }
        for w in led.warnings
        if w.scope == scope
    ]


def _key(w: Mapping[str, Any]) -> tuple[str, str]:
    return (str(w["kind"]), str(w["tier"] or ""))


def _cat(conn: sqlite3.Connection) -> tuple[distribution.Catalog | None, str | None]:
    try:
        return distribution.catalog(conn), None
    except (distribution.DistributionError, sqlite3.Error) as exc:
        return None, f"no distribution data: {exc}"


def _dist(
    cat: distribution.Catalog | None,
    led: ledger.Ledger,
    window_s: float,
    why: str | None,
) -> dict[str, Any]:
    """Storage and distribution checks on the ledger's ``all`` scope (WO-4.5)."""
    if cat is None:
        return {"transformers": [], "buffers": [], "notes": [why or "no data"]}
    if not cat.transformers and not cat.buffers:
        notes = [
            "no storage data in the current export run: re-export with WO-4.5's power pass"
        ]
        return {"transformers": [], "buffers": [], "notes": notes}
    d = distribution.from_ledger(cat, led.tiers["all"], max(window_s, 0.0))
    out = d.as_dict()
    if window_s <= 0:
        # The ledger has no peak durations: buffers need Clayton's window (D241).
        out["buffers"] = []
        out["notes"] = [n for n in out["notes"] if "no buffer needed" not in n] + [
            "give a peak window (seconds) to size buffers and an LSC"
        ]
    return out


def _cables(
    cat: distribution.Catalog | None, tier: str, amps: float
) -> list[dict[str, Any]]:
    if cat is None or tier not in VN or amps <= 0:
        return []
    return [
        {
            "ref": c.ref,
            "name": c.name,
            "voltage": c.voltage,
            "amps": c.amps,
            "loss": c.loss,
        }
        for c in distribution.cables_for(cat, V[VN.index(tier)], amps)
    ]


def _effort(p: Mapping[str, Any]) -> dict[str, Any] | None:
    m = p.get("metrics") or {}
    if "effort" not in m:
        return None
    return {
        "eff_h": m.get("eff_h"),
        "build_h": m.get("build_h"),
        "unlock_h": m.get("unlock_h"),
        "uncounted": int((m.get("effort") or {}).get("uncounted") or 0),
        "card": cards.effort_card(p),
    }


def _option(
    p: Mapping[str, Any],
    status: str,
    b: book_mod.Book | None,
    lines: Sequence[ledger.Line],
    now: ledger.Ledger,
    cat: distribution.Catalog | None,
    window_s: float,
    why: str | None,
) -> dict[str, Any]:
    pw, r, m = p["power"], p.get("rank") or {}, p.get("metrics") or {}
    dist = pw.get("distribution") or {}
    counted = status in ledger.COUNTED
    after_lines = (
        list(lines) if counted else [*lines, ledger.Line(ledger.entry(p), "accepted")]
    )
    after = ledger.build(after_lines)
    before_w, after_w = _warnings(now), _warnings(after)
    before_k = {_key(w) for w in before_w}
    after_k = {_key(w) for w in after_w}
    closed = p.get("closed") or {}
    gen = pw["generator"]
    fuel = pw["fuel"]
    return {
        "id": p["id"],
        "name": p["name"],
        "status": status,
        "position": r.get("position"),
        "of": r.get("of"),
        "mode": r.get("mode"),
        "score": r.get("score"),
        "eut_per_effort_h": r.get("eut_per_effort_h"),
        "terms": r.get("terms") or {},
        "values": r.get("values") or {},
        "rank_notes": list(r.get("notes") or ()),
        "weights": weights_line(p),
        "net_eut": pw["net_eut"],
        "gross_eut": pw["gross_eut"],
        "chain_eut": pw["chain_eut"],
        "units": pw.get("units"),
        "generator": {
            "label": gen["label"],
            "kind": gen["kind"],
            "tier": gen["tier"],
            "eut": gen["eut"],
            "cites": [c for c in gen.get("cites") or () if isinstance(c, str)][:6],
        },
        "fuel": {
            "id": fuel["id"],
            "name": _name(b, str(fuel["id"])),
            "rate_s": fuel["rate_s"],
            "ref": fuel.get("ref"),
        },
        "distribution": {
            "tier": dist.get("tier"),
            "voltage": dist.get("voltage"),
            "amps": dist.get("amps"),
        },
        "upkeep_h": m.get("upkeep"),
        "machines": m.get("machines"),
        "effort": _effort(p),
        "closed": {
            "ok": bool(closed.get("ok")),
            "imports": [
                {"id": i["id"], "name": _name(b, str(i["id"])), "rate_s": i["rate_s"]}
                for i in closed.get("imports") or ()
            ],
            "surplus": [
                {"id": i["id"], "name": _name(b, str(i["id"])), "rate_s": i["rate_s"]}
                for i in closed.get("surplus") or ()
            ],
            "problems": list(closed.get("problems") or ()),
        },
        "bootstrap": p.get("bootstrap"),
        "warnings": list(p.get("warnings") or ()),
        "nodes": _nodes(p, b),
        "cables": _cables(
            cat, str(dist.get("tier") or ""), float(dist.get("amps") or 0)
        ),
        "ledger": {
            "counted": counted,
            "card": ledger.card(after),
            "warnings": after_w,
            "cleared": [w for w in before_w if _key(w) not in after_k],
            "added": [w for w in after_w if _key(w) not in before_k],
            "transformers": _dist(cat, after, 0.0, why)["transformers"],
        },
    }


def differs(first: Mapping[str, Any], o: Mapping[str, Any]) -> list[str]:
    """How view option ``o`` differs from option 1 (``first``): its generator, fuel and the
    chain's recipes (review verdict 7 (b)). Empty for option 1 itself."""
    if o["id"] == first["id"]:
        return []
    out: list[str] = []
    if o["generator"]["label"] != first["generator"]["label"]:
        out.append(f"generator {o['generator']['label']}")
    if o["fuel"]["id"] != first["fuel"]["id"]:
        out.append(f"fuel {o['fuel']['name']}")

    def recipes(v: Mapping[str, Any]) -> set[str]:
        return {
            str(n["recipe"])
            for n in v.get("nodes") or ()
            if str(n.get("recipe") or "").startswith("r:")
            and n.get("kind") not in ("generator", "farm")
        }

    mine, theirs = recipes(o), recipes(first)
    if mine - theirs:
        out.append(f"{len(mine - theirs)} recipe(s) option 1 doesn't use")
    if theirs - mine:
        out.append(f"{len(theirs - mine)} of option 1's recipe(s) not used")
    if not out:
        out.append("the same generator, fuel and recipes, run at other rates")
    return out


def _window(window_s: float) -> float:
    if not math.isfinite(window_s) or window_s < 0 or window_s > WINDOW_MAX_S:
        raise PowerViewError(f"a peak window is 0 to {WINDOW_MAX_S:g} seconds")
    return window_s


def _ledger_section(
    now: ledger.Ledger,
    cat: distribution.Catalog | None,
    why: str | None,
    window_s: float,
) -> dict[str, Any]:
    return {
        "card": ledger.card(now),
        "running": ledger.card(now, "running"),
        "warnings": _warnings(now),
        "running_warnings": _warnings(now, "running"),
        "lines": len(now.lines),
        # each counted plan's draws per tier and its source, for "click a tier to see its draw"
        "entries": now.as_dict()["lines"],
        "window_s": window_s,
        "distribution": _dist(cat, now, window_s, why),
    }


def ledger_view(home: Path, window_s: float = 0.0) -> dict[str, Any]:
    """The ledger as it is: its card (both scopes), warnings and distribution checks."""
    window_s = _window(window_s)
    with designer._conn(home, readonly=True) as conn:
        now = ledger.build(_ledger_lines(conn), ledger.reconcile(conn))
        cat, why = _cat(conn)
        return _ledger_section(now, cat, why, window_s)


def view(home: Path, pid: str, window_s: float = 0.0) -> dict[str, Any]:
    """Everything the Power view shows for plan ``pid``'s design (see the module docstring)."""
    window_s = _window(window_s)
    plans = group(home, pid)
    ids = [p["id"] for p in plans]
    statuses = designer._statuses(home, ids)
    with designer._conn(home, readonly=True) as conn:
        lines = _ledger_lines(conn)
        now = ledger.build(lines, ledger.reconcile(conn))
        cat, why = _cat(conn)
        books: dict[str, book_mod.Book | None] = {}
        for p in plans:
            try:
                books[p["id"]] = book_mod.read(conn, p)
            except LookupError:
                books[p["id"]] = None  # solved on a run that's gone: keys, not names
        options = [
            _option(p, statuses.get(p["id"], "proposed"), books[p["id"]], lines, now,
                    cat, window_s, why)
            for p in plans
        ]  # fmt: skip
        for o in options:
            o["differs"] = differs(options[0], o)
        section = _ledger_section(now, cat, why, window_s)
    first = plans[0]
    req = first.get("request") or {}
    mode = str((first.get("rank") or {}).get("mode") or req.get("mode") or "best")
    saved = _latest(home)
    same = saved.get("ids") == ids
    return {
        "id": pid,
        "ids": ids,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "mode_how": MODE_HOW.get(mode, ""),
        "tier_cap": VN[int(first["tier_cap"])]
        if isinstance(first.get("tier_cap"), int)
        else first.get("tier_cap"),
        "request": req,
        "weights": weights_line(first),
        "w": (first.get("rank") or {}).get("w") or {},
        "notes": list(saved.get("notes") or ()) if same else [],
        "rejected": [
            {"candidate": str(r[0]), "why": str(r[1])}
            for r in (saved.get("rejected") or ())[:20]
            if isinstance(r, list) and len(r) == 2
        ]
        if same
        else [],
        "options": options,
        "ledger": section,
    }


def accept(home: Path, pid: str, supersedes: str | None = None) -> designer.Accepted:
    """Clayton accepts option ``pid`` (its line note, its source in the EU ledger)."""
    p = designer.load(home, pid, power=True)
    if p.get("kind") != "power":
        raise PowerViewError(f"plan {pid} isn't a power line")
    return designer.accept(home, p["id"], supersedes)

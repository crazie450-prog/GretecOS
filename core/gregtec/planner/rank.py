"""Deterministic route ranking (WO-3.5, plan §7.1 step 8, [QA:D2], D62, D67, D198).

Route ranking is this code's: models explain routes, they never reorder them.

**Score.** For each metric of :data:`METRICS` (lower is better: ``machines``, ``new_types``
(how many), ``eut`` (average EU/t), ``effort`` (``eff_h``), ``scarcity``, ``complexity`` (its
score)), a route's value is normalised by the largest value among the routes compared (0 when
every route has 0), so each term is in [0, 1]. The cost is the weighted mean of the terms
(weights in :data:`METRICS` order, ``math.fsum``), and ``score = 1 - cost`` (higher is better,
1 for a route that has nothing of anything). Terms and scores are rounded to
:data:`DIGITS` decimals, so float noise can't decide an order.

**Order.** Higher score first; ties (equal rounded scores) break on fewer new machine types,
then the lower highest machine tier, then the plan id (a hash of the route's choices). Nothing
depends on dict or set order. The base routes are ranked among themselves; the addon route
(D12) gets its score against the base routes and itself, and keeps its own column after them.

**Weights** live in the vault's ``me/prefs.md`` (§6.2 grammar, read only here; never written):
``weights: balanced`` (D67: every weight 1) and optional ``w_<metric>: <number>`` lines that
override one weight (then the set is ``custom``); ``craft_s: <seconds>`` sets the effort model's
time per crafting-table step. Every result prints its weights (:func:`describe`).

**Power lines** (WO-4.2, plan §7.2 objective modes, D13, D67, D235): :func:`order_power` ranks a
power design's options, by mode. ``best`` (and ``self_sustaining``, among the lines that pass the
validator) maximise the weighted score of :data:`POWER_METRICS`: net EU/t (higher is better)
minus effort, upkeep inputs, complexity and scarcity (lower is better), each normalised by the
largest value among the options, ``score = (w_net x net - sum w x cost) / sum w``; ``max_output``
takes the highest sustained net EU/t within the scale bound; ``output_per_effort`` the highest net
EU/t per effort hour (an option whose effort is 0, nothing counted, after those with one,
by net EU/t among themselves; among those with one, an option whose effort leaves items out,
``effort.uncounted`` > 0, after the complete ones, WO-4.3, D236). Ties
break as above. The power weights are ``w_net_eut`` and ``w_upkeep`` in ``me/prefs.md``, with
``w_effort``, ``w_complexity`` and ``w_scarcity`` shared with production routes; balanced (1)
by default (D67) and printed with every result (:func:`describe_power`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.planner import effort, metrics

METRICS = ("machines", "new_types", "eut", "effort", "scarcity", "complexity")
LABELS = {
    "machines": "machines",
    "new_types": "new machine types",
    "eut": "EU/t",
    "effort": "effort",
    "scarcity": "scarcity",
    "complexity": "complexity",
}
POWER_METRICS = ("net_eut", "effort", "upkeep", "complexity", "scarcity")
POWER_LABELS = {
    "net_eut": "net EU/t",
    "effort": "effort",
    "upkeep": "upkeep inputs",
    "complexity": "complexity",
    "scarcity": "scarcity",
}
POWER_MODES = ("best", "max_output", "output_per_effort", "self_sustaining")
DIGITS = 9
PREFS = Path("me") / "prefs.md"
_LINE = re.compile(r"([a-z_]+):\s*(.*?)\s*")


@dataclass(frozen=True)
class Prefs:
    weights: tuple[tuple[str, float], ...] = tuple((m, 1.0) for m in METRICS)
    name: str = "balanced"
    craft_s: float = effort.CRAFT_S
    notes: tuple[str, ...] = ()
    power: tuple[tuple[str, float], ...] = tuple((m, 1.0) for m in POWER_METRICS)

    def weight(self, metric: str) -> float:
        return dict(self.weights)[metric]

    def power_weight(self, metric: str) -> float:
        return dict(self.power)[metric]

    def as_dict(self) -> dict[str, float]:
        return dict(self.weights)


BALANCED = Prefs()


def _number(text: str) -> float | None:
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) and v >= 0 else None


def parse_prefs(text: str) -> Prefs:
    """``me/prefs.md``'s ranking lines (see the module docstring); a bad value keeps its
    default and adds a note."""
    weights = {m: 1.0 for m in METRICS}
    power = {m: 1.0 for m in POWER_METRICS}
    craft_s = effort.CRAFT_S
    custom = False
    notes: list[str] = []
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[0] == "---" and "---" in lines[1:]:
        lines = lines[lines.index("---", 1) + 1 :]
    for line in lines:
        if line.startswith("```"):
            break
        m = _LINE.fullmatch(line)
        if m is None:
            continue
        key, value = m[1], m[2]
        if key == "weights":
            if value != "balanced":
                notes.append(f"weights: {value!r} unknown; balanced used (D67)")
        elif key.startswith("w_"):
            metric = key[2:]
            v = _number(value)
            if metric not in METRICS and metric not in POWER_METRICS:
                known = dict.fromkeys((*METRICS, *POWER_METRICS))
                notes.append(f"{key}: no such metric ({', '.join(known)})")
            elif v is None:
                notes.append(f"{key}: {value!r} is not a number >= 0; 1 used")
            else:
                if metric in METRICS:
                    weights[metric] = v
                if metric in POWER_METRICS:
                    power[metric] = v
                custom = custom or v != 1.0
        elif key == "craft_s":
            v = _number(value)
            if v is None or v <= 0:
                notes.append(f"craft_s: {value!r} is not a time > 0; 2 s used")
            else:
                craft_s = v
    if not any(weights.values()):
        notes.append("every weight is 0: balanced used (D67)")
        weights = {m: 1.0 for m in METRICS}
        custom = any(v != 1.0 for v in power.values())
    if not any(power.values()):
        notes.append("every power weight is 0: balanced used (D67)")
        power = {m: 1.0 for m in POWER_METRICS}
        custom = any(v != 1.0 for v in weights.values())
    return Prefs(
        tuple((m, weights[m]) for m in METRICS),
        "custom" if custom else "balanced",
        craft_s,
        tuple(notes),
        tuple((m, power[m]) for m in POWER_METRICS),
    )


def read_prefs(vault: Path | None) -> Prefs:
    """The vault's ``me/prefs.md`` (read only), or the balanced defaults without one."""
    if vault is None:
        return BALANCED
    path = vault / PREFS
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return BALANCED
    except (OSError, UnicodeDecodeError) as exc:
        return Prefs(
            notes=(f"me/prefs.md unreadable ({type(exc).__name__}); balanced",)
        )
    return parse_prefs(text)


def values(m: Mapping[str, Any]) -> dict[str, float]:
    """The ranked values of one route's metrics."""
    return {
        "machines": float(m["machines"]),
        "new_types": float(len(m["new_types"])),
        "eut": float(m["eut_avg"]),
        "effort": float(m["eff_h"]),
        "scarcity": float(m["scarcity"]),
        "complexity": float(m["complexity"]["score"]),
    }


def _r(x: float) -> float:
    out = round(x, DIGITS)
    return 0.0 if out == 0 else out


def scores(
    routes: Sequence[Mapping[str, float]], prefs: Prefs
) -> list[tuple[float, dict[str, float]]]:
    """(score, terms) per route, normalised among ``routes``."""
    top = {k: max((r[k] for r in routes), default=0.0) for k in METRICS}
    total = math.fsum(prefs.weight(k) for k in METRICS)
    out = []
    for r in routes:
        terms = {k: _r(r[k] / top[k]) if top[k] > 0 else 0.0 for k in METRICS}
        cost = math.fsum(prefs.weight(k) * terms[k] for k in METRICS) / total
        out.append((_r(1.0 - cost), terms))
    return out


def _tier(m: Mapping[str, Any]) -> int:
    t = m.get("tier")
    return metrics.TIERS[t] if t else -1


def order(
    plans: Sequence[dict[str, Any]], prefs: Prefs = BALANCED
) -> list[dict[str, Any]]:
    """The plans ranked: each gets its ``rank``; the base routes in rank order, then the addon
    column. Plans need their ``metrics``."""
    base = [p for p in plans if p["route"]["column"] != "addon"]
    addon = [p for p in plans if p["route"]["column"] == "addon"]
    vals = [values(p["metrics"]) for p in base]
    scored = scores(vals, prefs)
    keyed = sorted(
        zip(base, vals, scored, strict=True),
        key=lambda t: (
            -t[2][0],
            len(t[0]["metrics"]["new_types"]),
            _tier(t[0]["metrics"]),
            t[0]["id"],
        ),
    )
    out: list[dict[str, Any]] = []
    for pos, (p, v, (score, terms)) in enumerate(keyed, 1):
        p["rank"] = _rank(score, terms, v, prefs, "base", pos, len(base))
        out.append(p)
    for p in addon:
        v = values(p["metrics"])
        score, terms = scores([*vals, v], prefs)[-1]
        p["rank"] = _rank(score, terms, v, prefs, "addon", 1, 1)
        out.append(p)
    return out


def _rank(
    score: float,
    terms: dict[str, float],
    vals: dict[str, float],
    prefs: Prefs,
    column: str,
    position: int,
    of: int,
) -> dict[str, Any]:
    return {
        "score": score,
        "position": position,
        "of": of,
        "column": column,
        "weights": prefs.name,
        "w": prefs.as_dict(),
        "terms": terms,
        "values": vals,
        "notes": list(prefs.notes),
    }


def describe(prefs: Prefs) -> str:
    """The weights line printed with every result."""
    parts = ", ".join(f"{LABELS[m]} {w:g}" for m, w in prefs.weights)
    return f"ranked by weights ({prefs.name}): {parts}"


# --- power lines (WO-4.2) ---------------------------------------------------------------------


def power_values(m: Mapping[str, Any]) -> dict[str, float]:
    """The ranked values of one power line's metrics."""
    return {
        "net_eut": float(m["net_eut"]),
        "effort": float(m["eff_h"]),
        "upkeep": float(m["upkeep"]),
        "complexity": float(m["complexity"]["score"]),
        "scarcity": float(m["scarcity"]),
    }


def power_scores(
    lines: Sequence[Mapping[str, float]], prefs: Prefs
) -> list[tuple[float, dict[str, float]]]:
    """(score, terms) per line, normalised among ``lines`` (see the module docstring)."""
    top = {k: max((abs(r[k]) for r in lines), default=0.0) for k in POWER_METRICS}
    total = math.fsum(prefs.power_weight(k) for k in POWER_METRICS)
    out = []
    for r in lines:
        terms = {k: _r(r[k] / top[k]) if top[k] > 0 else 0.0 for k in POWER_METRICS}
        gain = prefs.power_weight("net_eut") * terms["net_eut"]
        cost = math.fsum(
            prefs.power_weight(k) * terms[k] for k in POWER_METRICS if k != "net_eut"
        )
        out.append((_r((gain - cost) / total), terms))
    return out


def _uncounted(p: Mapping[str, Any]) -> int:
    """How many items the line's effort leaves out (0 for plans without the field)."""
    return int(p["metrics"].get("effort", {}).get("uncounted", 0))


def _per_effort(v: Mapping[str, float]) -> float | None:
    return v["net_eut"] / v["effort"] if v["effort"] > 0 else None


def order_power(
    plans: Sequence[dict[str, Any]], mode: str, prefs: Prefs = BALANCED
) -> list[dict[str, Any]]:
    """Power lines ranked for ``mode`` (see the module docstring); each gets its ``rank``.
    Plans need their ``metrics``."""
    if mode not in POWER_MODES:
        raise ValueError(f"mode {mode!r}: one of {', '.join(POWER_MODES)}")
    vals = [power_values(p["metrics"]) for p in plans]
    scored = power_scores(vals, prefs)

    def key(
        t: tuple[dict[str, Any], dict[str, float], tuple[float, dict[str, float]]],
    ) -> tuple[object, ...]:
        p, v, (score, _) = t
        head: tuple[object, ...]
        if mode == "max_output":
            head = (-_r(v["net_eut"]),)
        elif mode == "output_per_effort":
            ratio = _per_effort(v)
            # Effort 0 (nothing counted: no structure or NEI data) ranks after the rest,
            # by net EU/t among itself (D235); an effort that leaves items out is a lower
            # bound, so it ranks after the complete ones (WO-4.3, D236).
            head = (
                ratio is None,
                _uncounted(p) > 0,
                -_r(ratio) if ratio is not None else -_r(v["net_eut"]),
            )
        else:
            head = (-score,)
        return (*head, len(p["metrics"]["new_types"]), _tier(p["metrics"]), p["id"])

    keyed = sorted(zip(plans, vals, scored, strict=True), key=key)
    out: list[dict[str, Any]] = []
    for pos, (p, v, (score, terms)) in enumerate(keyed, 1):
        ratio = _per_effort(v)
        p["rank"] = {
            "mode": mode,
            "score": score,
            "net_eut": _r(v["net_eut"]),
            "eut_per_effort_h": _r(ratio) if ratio is not None else None,
            "position": pos,
            "of": len(plans),
            "weights": prefs.name,
            "w": dict(prefs.power),
            "terms": terms,
            "values": v,
            "notes": list(prefs.notes)
            + (
                ["effort 0 (nothing counted): ranked after the rest, by net EU/t"]
                if mode == "output_per_effort" and ratio is None
                else []
            )
            + (
                [
                    (
                        f"effort leaves {_uncounted(p)} items out (a lower bound): ranked"
                        " after the lines whose effort is complete"
                    )
                ]
                if mode == "output_per_effort" and ratio is not None and _uncounted(p)
                else []
            ),
        }
        out.append(p)
    return out


def describe_power(prefs: Prefs, mode: str) -> str:
    """The weights line printed with every power result."""
    parts = ", ".join(f"{POWER_LABELS[m]} {w:g}" for m, w in prefs.power)
    how = {
        "best": "the weighted score",
        "max_output": "net EU/t within the scale bound",
        "output_per_effort": "net EU/t per effort hour",
        "self_sustaining": "the weighted score, closed lines only",
    }[mode]
    return f"mode {mode}, ranked by {how}; weights ({prefs.name}): {parts}"
